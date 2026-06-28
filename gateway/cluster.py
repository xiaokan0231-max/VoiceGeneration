"""集群任务队列 + 节点注册（MySQL 行锁实现，无需额外中间件）。

任务即 generation_history 行：queued → leased → completed|failed。
认领用 SELECT ... FOR UPDATE SKIP LOCKED 保证多节点不抢同一任务。
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update

from .database import ClusterNode, GenerationHistory, _utcnow, db_session


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
        return [node_dict(r) for r in rows]


def node_name_map() -> dict[str, str]:
    with db_session() as db:
        return {r.node_id: r.name for r in db.scalars(select(ClusterNode))}


def queue_depth() -> int:
    with db_session() as db:
        return int(db.scalar(
            select(func.count()).select_from(GenerationHistory)
            .where(GenerationHistory.status == "queued")
        ) or 0)
