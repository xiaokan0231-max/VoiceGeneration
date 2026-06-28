"""集群任务队列 + 节点注册（MySQL 行锁实现，无需额外中间件）。

任务即 generation_history 行：queued → leased → completed|failed。
认领用 SELECT ... FOR UPDATE SKIP LOCKED 保证多节点不抢同一任务。
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update

from .database import ClusterNode, GenerationHistory, _utcnow, db_session


_runtime_lock = threading.Lock()
_node_runtime: dict[str, dict[str, Any]] = {}


def update_node_runtime(node_id: str, metrics: dict[str, Any] | None) -> None:
    """Store transient worker metrics reported by a node (never persisted as history)."""
    if not metrics:
        return
    workers = []
    for raw in list(metrics.get("workers") or [])[:64]:
        if not isinstance(raw, dict):
            continue
        speed = raw.get("speed")
        try:
            speed = float(speed) if speed is not None and math.isfinite(float(speed)) else None
        except (TypeError, ValueError):
            speed = None
        workers.append({
            "id": str(raw.get("id") or "worker")[:128],
            "model": str(raw.get("model") or "")[:64],
            "index": int(raw.get("index") or 0),
            "port": int(raw.get("port") or 0),
            "started": bool(raw.get("started")),
            "active": bool(raw.get("active")),
            "job_id": str(raw.get("job_id") or "")[:36] or None,
            "text": str(raw.get("text") or "")[:80],
            "elapsed_seconds": raw.get("elapsed_seconds"),
            "speed": round(max(0.0, min(speed, 100.0)), 4) if speed is not None else None,
            "audio_seconds": raw.get("audio_seconds"),
            "inference_seconds": raw.get("inference_seconds"),
            "error": str(raw.get("error") or "")[:300] or None,
        })
    active_speeds = [worker["speed"] for worker in workers if worker["active"] and worker["speed"] is not None]
    snapshot = {
        "started_workers": sum(1 for worker in workers if worker["started"]),
        "working_workers": sum(1 for worker in workers if worker["active"]),
        "total_speed": round(sum(active_speeds), 4) if active_speeds else None,
        "workers": workers,
        "metrics_updated_at": time.time(),
    }
    with _runtime_lock:
        _node_runtime[node_id] = snapshot


def _runtime_snapshot(node_id: str) -> dict[str, Any]:
    with _runtime_lock:
        return dict(_node_runtime.get(node_id, {}))


# ---- 任务队列 ---------------------------------------------------------------

def job_spec(row: GenerationHistory) -> dict[str, Any]:
    """传给工作节点执行所需的字段（参考音频由节点按 voice 另行获取）。"""
    return {
        "id": row.id,
        "text": row.text,
        "model": row.model_id,
        "voice": row.voice_id,
        "mode": row.mode,
        "instruct_text": row.instruct_text,
        "language": row.language,
        "speed": row.speed,
        "format": row.format,
    }


def lease_jobs(node_id: str, models: list[str], capacity: int,
               lease_ttl: int) -> list[dict[str, Any]]:
    """原子认领最多 capacity 条 queued 且 model 命中的任务。"""
    if capacity <= 0 or not models:
        return []
    now = _utcnow()
    with db_session() as db:
        rows = list(
            db.scalars(
                select(GenerationHistory)
                .where(
                    GenerationHistory.status == "queued",
                    GenerationHistory.model_id.in_(models),
                )
                .order_by(
                    GenerationHistory.priority.desc(),
                    GenerationHistory.created_at.asc(),
                )
                .limit(capacity)
                .with_for_update(skip_locked=True)
            )
        )
        leased = []
        for row in rows:
            row.status = "leased"
            row.assigned_node = node_id
            row.lease_expires_at = now + timedelta(seconds=lease_ttl)
            row.attempts = (row.attempts or 0) + 1
            leased.append(job_spec(row))
        db.commit()
        return leased


def lease_jobs_by_model(node_id: str, capacities: dict[str, int],
                        lease_ttl: int) -> list[dict[str, Any]]:
    """Atomically lease jobs without borrowing capacity across model pools.

    ``{"cosyvoice3": 2, "f5_tts": 1}`` can lease at most two CosyVoice jobs
    and one F5 job.  This prevents unrelated idle workers from making a node
    over-lease one busy model and hold jobs that it cannot execute yet.
    """
    clean = {
        str(model_id): max(0, min(int(capacity), 64))
        for model_id, capacity in capacities.items()
        if model_id and int(capacity) > 0
    }
    if not clean:
        return []
    now = _utcnow()
    leased: list[dict[str, Any]] = []
    with db_session() as db:
        # Stable model order avoids lock-order inversions when several nodes poll.
        for model_id in sorted(clean):
            rows = list(
                db.scalars(
                    select(GenerationHistory)
                    .where(
                        GenerationHistory.status == "queued",
                        GenerationHistory.model_id == model_id,
                    )
                    .order_by(
                        GenerationHistory.priority.desc(),
                        GenerationHistory.created_at.asc(),
                    )
                    .limit(clean[model_id])
                    .with_for_update(skip_locked=True)
                )
            )
            for row in rows:
                row.status = "leased"
                row.assigned_node = node_id
                row.lease_expires_at = now + timedelta(seconds=lease_ttl)
                row.attempts = (row.attempts or 0) + 1
                leased.append(job_spec(row))
        db.commit()
    return leased


def extend_lease(job_id: str, lease_ttl: int) -> bool:
    with db_session() as db:
        row = db.get(GenerationHistory, job_id)
        if not row or row.status != "leased":
            return False
        row.lease_expires_at = _utcnow() + timedelta(seconds=lease_ttl)
        db.commit()
        return True


def fail_or_requeue(job_id: str, error: str, max_attempts: int) -> str:
    """返回最终状态：'queued'（还能重试）或 'failed'。"""
    with db_session() as db:
        row = db.get(GenerationHistory, job_id)
        if not row:
            return "missing"
        if (row.attempts or 0) < max_attempts:
            row.status = "queued"
            row.assigned_node = None
            row.lease_expires_at = None
            result = "queued"
        else:
            row.status = "failed"
            row.error_message = error
            row.lease_expires_at = None
            row.completed_at = _utcnow()
            result = "failed"
        db.commit()
        return result


def requeue_expired(max_attempts: int) -> int:
    """回收租约到期的任务：能重试则回队列，否则置失败。返回处理条数。"""
    now = _utcnow()
    with db_session() as db:
        rows = list(
            db.scalars(
                select(GenerationHistory).where(
                    GenerationHistory.status == "leased",
                    GenerationHistory.lease_expires_at.is_not(None),
                    GenerationHistory.lease_expires_at < now,
                )
            )
        )
        for row in rows:
            if (row.attempts or 0) < max_attempts:
                row.status = "queued"
                row.assigned_node = None
                row.lease_expires_at = None
            else:
                row.status = "failed"
                row.error_message = "超过最大重试次数（节点未在租约内返回结果）"
                row.lease_expires_at = None
                row.completed_at = now
        db.commit()
        return len(rows)


def requeue_all_leased() -> int:
    """协调端启动时把残留的 leased 任务重新入队。"""
    with db_session() as db:
        result = db.execute(
            update(GenerationHistory)
            .where(GenerationHistory.status == "leased")
            .values(status="queued", assigned_node=None, lease_expires_at=None)
        )
        db.commit()
        return int(result.rowcount or 0)


def dedup_completed(cache_key: str, audio_path: str, mime_type: str,
                    byte_size: int, duration_seconds: float | None,
                    keep_id: str) -> int:
    """同 cache_key 的其它 queued 任务直接指向同一音频，避免重复算。"""
    if not cache_key:
        return 0
    now = _utcnow()
    with db_session() as db:
        rows = list(
            db.scalars(
                select(GenerationHistory).where(
                    GenerationHistory.cache_key == cache_key,
                    GenerationHistory.status == "queued",
                    GenerationHistory.id != keep_id,
                )
            )
        )
        for row in rows:
            row.status = "completed"
            row.audio_path = audio_path
            row.mime_type = mime_type
            row.byte_size = byte_size
            row.duration_seconds = duration_seconds
            row.cache_hit = True
            row.completed_at = now
        db.commit()
        return len(rows)


# ---- 节点注册 ---------------------------------------------------------------

def register_node(*, node_id: str, name: str, role: str, models: list[str],
                  max_concurrency: int, version: str | None = None) -> None:
    now = _utcnow()
    with db_session() as db:
        row = db.get(ClusterNode, node_id)
        if row is None:
            row = ClusterNode(node_id=node_id, created_at=now)
            db.add(row)
        row.name = name or node_id
        row.role = role
        row.models = json.dumps(models, ensure_ascii=False)
        row.max_concurrency = max_concurrency
        row.version = version
        row.status = "online"
        row.last_seen = now
        db.commit()


def touch_node(node_id: str) -> None:
    with db_session() as db:
        row = db.get(ClusterNode, node_id)
        if row:
            row.last_seen = _utcnow()
            row.status = "online"
            db.commit()


def mark_nodes_offline(node_timeout: int) -> int:
    cutoff = _utcnow() - timedelta(seconds=node_timeout)
    with db_session() as db:
        result = db.execute(
            update(ClusterNode)
            .where(ClusterNode.status == "online", ClusterNode.last_seen < cutoff)
            .values(status="offline")
        )
        db.commit()
        return int(result.rowcount or 0)


def node_dict(row: ClusterNode) -> dict[str, Any]:
    try:
        models = json.loads(row.models)
    except (json.JSONDecodeError, TypeError):
        models = []
    return {
        "node_id": row.node_id,
        "name": row.name,
        "role": row.role,
        "models": models,
        "max_concurrency": row.max_concurrency,
        "status": row.status,
        "version": row.version,
        "last_seen": row.last_seen.isoformat() + "Z" if row.last_seen else None,
    }


def list_nodes() -> list[dict[str, Any]]:
    with db_session() as db:
        rows = list(db.scalars(select(ClusterNode).order_by(ClusterNode.created_at.asc())))
        active_counts = {
            node_id: int(count)
            for node_id, count in db.execute(
                select(GenerationHistory.assigned_node, func.count())
                .where(
                    GenerationHistory.status == "leased",
                    GenerationHistory.assigned_node.is_not(None),
                )
                .group_by(GenerationHistory.assigned_node)
            )
        }
        # Weighted 30-minute performance: sum(audio seconds) / sum(inference seconds).
        # New records are grouped by exact worker_id; legacy rows still provide a node average.
        elapsed = func.coalesce(
            GenerationHistory.inference_seconds, GenerationHistory.elapsed_seconds,
        )
        performance: dict[str, dict[str, Any]] = {}
        for node_id, worker_id, audio_sum, elapsed_sum, samples in db.execute(
            select(
                GenerationHistory.assigned_node, GenerationHistory.worker_id,
                func.sum(GenerationHistory.duration_seconds), func.sum(elapsed), func.count(),
            )
            .where(
                GenerationHistory.status == "completed",
                GenerationHistory.assigned_node.is_not(None),
                GenerationHistory.completed_at >= _utcnow() - timedelta(minutes=30),
                GenerationHistory.duration_seconds.is_not(None),
                elapsed.is_not(None), elapsed > 0,
            )
            .group_by(GenerationHistory.assigned_node, GenerationHistory.worker_id)
        ):
            if not node_id or not elapsed_sum:
                continue
            entry = performance.setdefault(node_id, {"workers": {}, "legacy": None, "samples": 0})
            value = float(audio_sum or 0) / float(elapsed_sum)
            entry["samples"] += int(samples or 0)
            if worker_id:
                entry["workers"][worker_id] = {"speed": value, "samples": int(samples or 0)}
            else:
                entry["legacy"] = value
        result = []
        for row in rows:
            item = node_dict(row)
            runtime = _runtime_snapshot(row.node_id)
            leased = active_counts.get(row.node_id, 0)
            has_runtime = "working_workers" in runtime
            perf = performance.get(row.node_id, {"workers": {}, "legacy": None, "samples": 0})
            workers = runtime.get("workers", [])
            if not workers and row.max_concurrency == 1 and perf["legacy"] is not None:
                model_id = item["models"][0] if item["models"] else "worker"
                workers = [{
                    "id": f"{model_id}#1", "model": model_id, "index": 1, "port": 0,
                    "started": None, "active": leased > 0, "job_id": None, "text": "",
                    "elapsed_seconds": None, "speed": None, "audio_seconds": None,
                    "inference_seconds": None, "error": None,
                    "speed_30m": round(perf["legacy"], 4),
                    "samples_30m": perf["samples"],
                }]
            else:
                for worker in workers:
                    stats = perf["workers"].get(worker["id"])
                    worker["speed_30m"] = round(stats["speed"], 4) if stats else None
                    worker["samples_30m"] = stats["samples"] if stats else 0
            named_speeds = [stats["speed"] for stats in perf["workers"].values()]
            average_speed = sum(named_speeds) if named_speeds else perf["legacy"]
            average_samples = (
                sum(stats["samples"] for stats in perf["workers"].values())
                if named_speeds else perf["samples"]
            )
            item.update({
                "started_workers": runtime.get("started_workers"),
                "working_workers": int(runtime.get("working_workers") or 0)
                if has_runtime else min(leased, row.max_concurrency),
                "total_speed": runtime.get("total_speed"),
                "workers": workers,
                "metrics_updated_at": runtime.get("metrics_updated_at"),
                "average_speed_30m": round(average_speed, 4) if average_speed is not None else None,
                "samples_30m": average_samples,
            })
            result.append(item)
        return result


def node_name_map() -> dict[str, str]:
    with db_session() as db:
        return {r.node_id: r.name for r in db.scalars(select(ClusterNode))}


def queue_depth() -> int:
    with db_session() as db:
        return int(db.scalar(
            select(func.count()).select_from(GenerationHistory)
            .where(GenerationHistory.status == "queued")
        ) or 0)
