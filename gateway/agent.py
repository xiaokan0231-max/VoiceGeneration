"""集群节点 agent：认领任务 → 本地 worker 执行 → 回传结果。

两种用法：
- 内置（协调端进程内）：`EmbeddedAgent`，直接调用 cluster.* 与协调端的 finalize，无 HTTP。
- 远程（Windows 等）：`python -m gateway.agent`，长轮询协调端 /v1/cluster/* 认领并回传。
"""
from __future__ import annotations

import asyncio
import io
import tempfile
import time
import wave
from collections import Counter, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import httpx
from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from .config import ROOT, load_config, load_raw_models, save_raw_models
from .supervisor import Supervisor


@dataclass
class WorkerResult:
    wav: bytes
    worker_id: str
    inference_seconds: float
    audio_seconds: float | None


def connection_state(*, enabled: bool, coordinator_url: str, connected: bool,
                     last_error: str | None) -> str:
    """Return the UI-facing connection state without conflating idle states."""
    if not enabled:
        return "disconnected"
    if not coordinator_url:
        return "unconfigured"
    if connected:
        return "connected"
    if last_error:
        return "error"
    return "connecting"


async def run_worker(supervisor: Supervisor, model_cfg, payload: dict,
                     timeout: float = 1800,
                     on_acquired: Callable[[], None] | None = None) -> WorkerResult:
    """取一个空闲 worker 副本，POST /synthesize，返回 WAV 字节，最后归还副本。"""
    st = await supervisor.acquire(model_cfg.id)
    if on_acquired:
        on_acquired()
    started = time.perf_counter()
    supervisor.begin_work(st, payload.get("job_id"), payload.get("text") or "")
    try:
        worker_payload = {key: value for key, value in payload.items() if key != "job_id"}
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            r = await client.post(f"{st.base_url}/synthesize", json=worker_payload)
        if r.status_code != 200:
            detail = r.json().get("error", r.text) if r.content else "未知错误"
            raise RuntimeError(str(detail))
        elapsed = time.perf_counter() - started
        audio_seconds = _wav_duration(r.content)
        supervisor.finish_work(st, audio_seconds=audio_seconds, elapsed_seconds=elapsed)
        return WorkerResult(
            wav=r.content, worker_id=f"{model_cfg.id}#{st.index + 1}",
            inference_seconds=elapsed, audio_seconds=audio_seconds,
        )
    except Exception as exc:
        supervisor.finish_work(st, error=str(exc))
        raise
    finally:
        supervisor.release(model_cfg.id, st)


def _wav_duration(data: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            width = wav.getsampwidth() * wav.getnchannels()
            frames = len(wav.readframes(wav.getnframes())) // width
            return frames / float(wav.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


# ---- 内置 agent（协调端进程内，无 HTTP）-------------------------------------

class EmbeddedAgent:
    def __init__(self, state) -> None:
        self.state = state              # gateway.main.App
        self._task: asyncio.Task | None = None
        self._inflight_by_model: Counter[str] = Counter()
        self._stop = False
        self._jobs: set[asyncio.Task] = set()

    @property
    def _cluster_cfg(self):
        return self.state.config.settings.cluster

    def _allowed_models(self) -> list[str]:
        cfg = self.state.config
        nid = cfg.settings.cluster.node_id
        return [m.id for m in cfg.enabled_models() if m.allows(nid)]

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()
        for task in self._jobs:
            task.cancel()
        if self._jobs:
            await asyncio.gather(*self._jobs, return_exceptions=True)

    async def _loop(self) -> None:
        from . import cluster
        c = self._cluster_cfg
        await asyncio.to_thread(
            cluster.register_node, node_id=c.node_id, name=c.node_name,
            role="coordinator", models=self._allowed_models(),
            max_concurrency=self.state.supervisor.total_slots(), version="1.0.0",
        )
        while not self._stop:
            c = self._cluster_cfg
            capacities = {
                model_id: max(0, slots - self._inflight_by_model[model_id])
                for model_id, slots in self.state.supervisor.slots_by_model().items()
                if model_id in self._allowed_models()
            }
            jobs = []
            if any(capacities.values()):
                jobs = await asyncio.to_thread(
                    cluster.lease_jobs_by_model, c.node_id, capacities, c.lease_ttl
                )
            await asyncio.to_thread(cluster.touch_node, c.node_id)
            cluster.update_node_runtime(c.node_id, self.state.supervisor.runtime_metrics())
            for job in jobs:
                self._inflight_by_model[job["model"]] += 1
                task = asyncio.create_task(self._run(job))
                self._jobs.add(task)
                task.add_done_callback(self._jobs.discard)
            await asyncio.sleep(0.05 if jobs else max(0.25, c.poll_interval))

    async def _run(self, job: dict) -> None:
        from . import cluster
        state = self.state
        c = self._cluster_cfg
        started = time.perf_counter()
        heartbeat = asyncio.create_task(self._job_heartbeat(job["id"]))
        try:
            model = state.config.model(job["model"])
            if not model or not model.enabled:
                raise RuntimeError(f"模型未启用: {job['model']}")
            payload = state.build_payload(job)
            payload["job_id"] = job["id"]
            result = await run_worker(state.supervisor, model, payload)
            await state.finalize_job(
                job["id"], result.wav, time.perf_counter() - started, c.node_id,
                worker_id=result.worker_id, inference_seconds=result.inference_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            await asyncio.to_thread(cluster.fail_or_requeue, job["id"], str(exc), c.max_attempts)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            model_id = job["model"]
            self._inflight_by_model[model_id] -= 1
            if self._inflight_by_model[model_id] <= 0:
                del self._inflight_by_model[model_id]

    async def _job_heartbeat(self, job_id: str) -> None:
        """Keep slow local MPS jobs leased just like remote-agent jobs."""
        from . import cluster
        interval = max(5.0, min(30.0, self._cluster_cfg.lease_ttl / 3))
        while not self._stop:
            await asyncio.sleep(interval)
            if self._stop:
                return
            ok = await asyncio.to_thread(
                cluster.extend_lease, job_id, self._cluster_cfg.lease_ttl,
            )
            if not ok:
                return


# ---- 远程 agent（独立进程 + 本地 web 控制台：python -m gateway.agent）--------

class RemoteAgent:
    def __init__(self) -> None:
        self.config = load_config()
        self.supervisor = Supervisor(self.config.settings, self.config.enabled_models())
        self.active: dict[str, dict] = {}            # job_id -> runtime state (phase/lease/etc.)
        self.enabled = self.config.settings.cluster.enabled  # 是否主动连接(网页连接/断开控制)
        self.reconnect = asyncio.Event()
        self.status = {"connected": False, "last_error": None, "counters": {
            "leased": 0, "completed": 0, "failed": 0, "upload_retries": 0,
        }}
        self._logs: deque[dict] = deque(maxlen=300)
        self._last_log: tuple[str, str] | None = None
        self._stop = False
        # trust_env=False：忽略 HTTP_PROXY/HTTPS_PROXY 等环境变量，直连协调端，
        # 避免被本机系统代理/VPN(Clash/V2Ray)截走导致 502。
        self._client = httpx.AsyncClient(timeout=1800, trust_env=False)
        self._asset_dir = Path(tempfile.gettempdir()) / "vg-agent-assets"
        self._asset_dir.mkdir(parents=True, exist_ok=True)
        self._asset_cache: dict[str, str] = {}
        self._log("info", f"副节点已启动：{self.cluster.node_id}，可用模型：{', '.join(self._models()) or '无'}")

    def _log(self, level: str, message: str, *, dedupe: bool = False) -> None:
        key = (level, message)
        if dedupe and self._last_log == key:
            return
        self._logs.append({
            "time": datetime.now().astimezone().isoformat(timespec="seconds"),
            "level": level,
            "message": message,
        })
        self._last_log = key

    @staticmethod
    def _http_error(exc: Exception) -> str:
        if isinstance(exc, httpx.HTTPStatusError):
            response = exc.response
            detail = response.text.strip().replace("\n", " ")[:300]
            suffix = f"：{detail}" if detail else ""
            return f"HTTP {response.status_code} {response.request.url}{suffix}"
        return str(exc)

    # ---- 便捷属性 -------------------------------------------------------
    @property
    def cluster(self):
        return self.config.settings.cluster

    @property
    def base(self) -> str:
        return self.cluster.coordinator_url.rstrip("/")

    @property
    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cluster.token}"} if self.cluster.token else {}

    def _models(self) -> list[str]:
        nid = self.cluster.node_id
        return [m.id for m in self.config.enabled_models() if m.allows(nid)]

    # ---- 主循环 --------------------------------------------------------
    async def run(self) -> None:
        self.supervisor.start_reaper()
        backoff = 1.0
        while not self._stop:
            self.reconnect.clear()
            c = self.cluster
            if not self.enabled:
                self.status.update(connected=False, last_error=None)
                self._log("info", "已暂停连接协调端", dedupe=True)
                await self._sleep_or_wake(3600)
                continue
            if not c.coordinator_url:
                message = "未配置主节点地址"
                self.status.update(connected=False, last_error=message)
                self._log("warning", message, dedupe=True)
                await self._sleep_or_wake(3600)  # 断开态：挂起等待「连接」唤醒
                continue
            try:
                await self._register()
                was_connected = self.status["connected"]
                self.status.update(connected=True, last_error=None)
                if not was_connected:
                    self._log("info", f"已连接协调端：{c.coordinator_url}")
                backoff = 1.0
            except Exception as exc:  # noqa: BLE001
                message = self._http_error(exc)
                self.status.update(connected=False, last_error=message)
                self._log("error", f"连接协调端失败：{message}", dedupe=True)
                await self._sleep_or_wake(backoff)
                backoff = min(backoff * 2, 30)
                continue
            await self._lease_loop(c.coordinator_url)

    async def _lease_loop(self, url: str) -> None:
        while (not self._stop and not self.reconnect.is_set()
               and self.enabled and self.cluster.coordinator_url == url):
            # A preparing job has not consumed its worker token yet. Active
            # synthesis is already reflected by Supervisor.available_by_model;
            # uploads have released their worker and may overlap new inference.
            reservations = Counter(
                str(job.get("model"))
                for job in self.active.values()
                if job.get("model")
                and job.get("phase") in {"preparing", "waiting_worker"}
            )
            capacities = {
                model_id: max(0, slots - reservations[model_id])
                for model_id, slots in self.supervisor.available_by_model().items()
                if model_id in self._models()
            }
            # Defensive compatibility for an in-memory job created by an
            # older agent version without a model field.
            unknown_reservations = sum(
                1 for job in self.active.values()
                if not job.get("model")
                and job.get("phase") in {"preparing", "waiting_worker"}
            )
            for model_id in sorted(capacities):
                reduction = min(unknown_reservations, capacities[model_id])
                capacities[model_id] -= reduction
                unknown_reservations -= reduction
                if unknown_reservations <= 0:
                    break
            # Keep workers useful during a short upload outage, but bound the
            # number of buffered WAV results so a long coordinator failure
            # cannot grow memory usage without limit.
            backlog_remaining = max(
                0, self.supervisor.total_slots() * 2 - len(self.active)
            )
            for model_id in sorted(capacities):
                capacities[model_id] = min(capacities[model_id], backlog_remaining)
                backlog_remaining -= capacities[model_id]
            capacity = sum(capacities.values())
            jobs: list[dict] = []
            # capacity=0 也必须继续长轮询：协调端会在 lease 入口更新
            # node.last_seen。否则节点满载执行长任务时会被 node_timeout
            # 错误标记为离线。
            try:
                r = await self._client.post(
                    f"{self.base}/v1/cluster/lease", headers=self.headers,
                    json={"node_id": self.cluster.node_id, "models": self._models(),
                          "capacity": capacity, "capacities": capacities,
                          "metrics": self.supervisor.runtime_metrics()},
                    timeout=40,
                )
                r.raise_for_status()
                jobs = r.json().get("jobs", [])
                self.status.update(connected=True, last_error=None)
            except httpx.HTTPError as exc:
                message = self._http_error(exc)
                self.status.update(connected=False, last_error=message)
                self._log("error", f"认领任务失败：{message}", dedupe=True)
                return
            for job in jobs:
                self.status["counters"]["leased"] += 1
                current = self.active.get(job["id"])
                if current is not None:
                    # The coordinator may requeue a lease after a restart or
                    # temporary disconnect. Reuse the in-progress synthesis or
                    # buffered WAV instead of starting the same GPU work again.
                    current["lease_valid"] = True
                    current["lease_count"] = current.get("lease_count", 1) + 1
                    self._log("warning", f"任务 {job['id']} 重新认领，复用现有结果")
                    continue
                # 同步登记，避免下一轮 lease 在任务populate active 前重复计算容量→超额认领
                self.active[job["id"]] = {"model": job["model"],
                                          "text": (job.get("text") or "")[:60],
                                          "started": time.monotonic(),
                                          "phase": "preparing", "lease_valid": True,
                                          "lease_count": 1}
                self._log("info", f"认领任务 {job['id']}（{job['model']}）")
                asyncio.create_task(self._run_job(job))
            if not jobs:
                await self._sleep_or_wake(self.cluster.poll_interval)

    async def _sleep_or_wake(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self.reconnect.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def _register(self) -> None:
        c = self.cluster
        r = await self._client.post(f"{self.base}/v1/cluster/register", headers=self.headers, json={
            "node_id": c.node_id, "name": c.node_name, "role": "agent",
            "models": self._models(), "max_concurrency": self.supervisor.total_slots(), "version": "1.0.0",
        })
        r.raise_for_status()  # 非 2xx（含被代理拦截/令牌错误）视为未连接

    async def _fetch_asset(self, voice: str) -> str:
        if voice in self._asset_cache and Path(self._asset_cache[voice]).exists():
            return self._asset_cache[voice]
        r = await self._client.get(f"{self.base}/v1/cluster/asset/{voice}", headers=self.headers)
        r.raise_for_status()
        path = self._asset_dir / f"{voice}.wav"
        path.write_bytes(r.content)
        self._asset_cache[voice] = str(path)
        return str(path)

    async def _run_job(self, job: dict) -> None:
        jid = job["id"]  # 已在 _lease_loop 登记进 self.active
        state = self.active[jid]
        started = time.perf_counter()
        heartbeat = asyncio.create_task(self._job_heartbeat(jid))
        try:
            model = self.config.model(job["model"])
            if not model or not model.enabled:
                raise RuntimeError(f"模型未启用: {job['model']}")
            ref_audio_path = await self._fetch_asset(job["voice"]) if job.get("is_clone") else None
            payload = {
                "text": job["text"], "voice": job["voice"], "language": job.get("language"),
                "speed": job.get("speed", 1.0), "mode": job.get("mode", "clone"),
                "instruct_text": job.get("instruct_text"),
                "ref_audio_path": ref_audio_path, "ref_text": job.get("ref_text"),
                "job_id": jid,
            }
            state["phase"] = "waiting_worker"
            worker_result = await run_worker(
                self.supervisor, model, payload,
                on_acquired=lambda: state.update(phase="synthesizing"),
            )
            state["phase"] = "uploading"
            await self._upload_result(
                jid, worker_result.wav, time.perf_counter() - started, state,
                worker_id=worker_result.worker_id,
                inference_seconds=worker_result.inference_seconds,
            )
            self.status["counters"]["completed"] += 1
            self._log("info", f"任务 {jid} 完成，耗时 {time.perf_counter() - started:.1f}s")
        except Exception as exc:  # noqa: BLE001
            self.status["counters"]["failed"] += 1
            level = "error" if state.get("lease_valid", True) else "warning"
            self._log(level, f"任务 {jid} 失败：{self._http_error(exc)}")
            # Never fail a lease that the coordinator has already requeued or
            # assigned elsewhere; doing so could corrupt the new attempt.
            if state.get("lease_valid", True):
                try:
                    await self._client.post(f"{self.base}/v1/cluster/jobs/{jid}/fail",
                                            headers=self.headers,
                                            json={"node_id": self.cluster.node_id, "error": str(exc)},
                                            timeout=10)
                except httpx.HTTPError:
                    pass
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            if self.active.get(jid) is state:
                self.active.pop(jid, None)

    async def _upload_result(self, job_id: str, wav: bytes, elapsed: float,
                             state: dict, attempts: int = 8,
                             worker_id: str | None = None,
                             inference_seconds: float | None = None) -> None:
        """Retry only the buffered WAV; never rerun synthesis for a short outage."""
        delay = 1.0
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            if not state.get("lease_valid", True):
                state["phase"] = "waiting_lease"
                last_error = RuntimeError("协调端租约已失效，等待重新认领")
            else:
                state["phase"] = "uploading" if attempt == 1 else "upload_retry"
                try:
                    data = {"node_id": self.cluster.node_id, "elapsed": str(elapsed)}
                    if worker_id:
                        data["worker_id"] = worker_id
                    if inference_seconds is not None:
                        data["inference_seconds"] = str(inference_seconds)
                    result = await self._client.post(
                        f"{self.base}/v1/cluster/jobs/{job_id}/result", headers=self.headers,
                        files={"audio": ("out.wav", wav, "audio/wav")},
                        data=data,
                        timeout=120,
                    )
                    result.raise_for_status()
                    if attempt > 1:
                        self._log("info", f"任务 {job_id} 结果重传成功（第 {attempt} 次）")
                    return
                except (httpx.HTTPError, RuntimeError) as exc:
                    last_error = exc
                    status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    retryable = status_code is None or status_code >= 500 or status_code in {408, 429}
                    if not retryable:
                        raise
            if attempt >= attempts:
                break
            self.status["counters"]["upload_retries"] += 1
            self._log(
                "warning",
                f"任务 {job_id} 结果上传失败，{delay:.0f}s 后重试（{attempt}/{attempts}）："
                f"{self._http_error(last_error)}",
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)
        raise last_error or RuntimeError("结果上传失败")

    async def _job_heartbeat(self, job_id: str) -> None:
        """Extend an active job lease until synthesis/result upload finishes."""
        interval = max(5.0, min(30.0, self.cluster.lease_ttl / 3))
        while not self._stop and job_id in self.active:
            await asyncio.sleep(interval)
            if self._stop or job_id not in self.active:
                return
            try:
                response = await self._client.post(
                    f"{self.base}/v1/cluster/jobs/{job_id}/heartbeat",
                    headers=self.headers,
                    timeout=10,
                )
                response.raise_for_status()
                state = self.active.get(job_id)
                if state is None:
                    return
                ok = bool(response.json().get("ok"))
                if not ok:
                    state["lease_valid"] = False
                    self._log("warning", f"任务 {job_id} 租约已失效，等待协调端重新认领", dedupe=True)
            except Exception as exc:  # noqa: BLE001
                self._log(
                    "warning",
                    f"任务 {job_id} 续租失败：{self._http_error(exc)}",
                    dedupe=True,
                )

    async def stop(self) -> None:
        self._stop = True
        self.reconnect.set()
        await self.supervisor.shutdown()
        await self._client.aclose()

    # ---- 给 web 控制台用 -----------------------------------------------
    def snapshot(self) -> dict:
        c, sup = self.cluster, self.supervisor
        phases = [state.get("phase") for state in self.active.values()]
        return {
            "node_id": c.node_id, "node_name": c.node_name, "role": "agent",
            "coordinator_url": c.coordinator_url, "token_set": bool(c.token),
            "connected": self.status["connected"], "last_error": self.status["last_error"],
            "connection_state": connection_state(
                enabled=self.enabled, coordinator_url=c.coordinator_url,
                connected=self.status["connected"], last_error=self.status["last_error"],
            ),
            "enabled": self.enabled, "total_slots": sup.total_slots(),
            "available": sup.available_capacity(), "inflight": len(self.active),
            "synthesizing": phases.count("synthesizing"),
            "uploading": sum(p in {"uploading", "upload_retry", "waiting_lease"} for p in phases),
            "waiting": sum(p in {"preparing", "waiting_worker"} for p in phases),
            "counters": self.status["counters"],
            "models": [
                {"id": m.id, "enabled": m.enabled, "replicas": m.replicas,
                 "device": m.options.get("device", "auto"), "loaded": sup.is_loaded(m.id)}
                for m in self.config.enabled_models()
            ],
        }

    def jobs(self) -> list[dict]:
        now = time.monotonic()
        return [{"id": jid, "model": j["model"], "text": j["text"],
                 "phase": j.get("phase", "preparing"),
                 "elapsed": round(now - j["started"], 1)}
                for jid, j in list(self.active.items())]

    def logs(self) -> list[dict]:
        return list(self._logs)

    def config_view(self) -> dict:
        c = self.cluster
        return {
            "coordinator_url": c.coordinator_url, "token_set": bool(c.token),
            "node_id": c.node_id, "node_name": c.node_name,
            "agent_host": c.agent_host, "agent_port": c.agent_port,
            "models": [
                {"id": m.id, "enabled": m.enabled, "replicas": m.replicas,
                 "device": m.options.get("device", "auto"), "supports_cloning": m.supports_cloning}
                for m in self.config.models
            ],
        }

    async def coordinator_nodes(self) -> dict:
        if not self.cluster.coordinator_url:
            return {}
        try:
            r = await self._client.get(f"{self.base}/v1/cluster/nodes", headers=self.headers, timeout=10)
            return r.json() if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001
            return {}

    def set_enabled(self, value: bool) -> None:
        """连接/断开：持久化到 models.yaml 并立即唤醒循环生效。"""
        self.enabled = value
        raw = load_raw_models()
        raw.setdefault("settings", {}).setdefault("cluster", {})["enabled"] = value
        save_raw_models(raw)
        self.config.settings.cluster.enabled = value
        self._log("info", "已启用连接协调端" if value else "已断开协调端")
        self.reconnect.set()

    async def apply_config(self, updates: dict) -> None:
        """把网页改动落盘到 models.yaml 并热生效。"""
        raw = load_raw_models()
        cl = raw.setdefault("settings", {}).setdefault("cluster", {})
        for key in ("coordinator_url", "node_id", "node_name", "token"):
            if key in updates and updates[key] is not None:
                cl[key] = updates[key]
        for mu in updates.get("models", []) or []:
            entry = next((m for m in raw.get("models", []) if m.get("id") == mu.get("id")), None)
            if not entry:
                continue
            if "enabled" in mu:
                entry["enabled"] = bool(mu["enabled"])
            if "replicas" in mu:
                entry["replicas"] = max(1, int(mu["replicas"]))
            if "device" in mu and mu["device"]:
                entry.setdefault("options", {})["device"] = mu["device"]
        save_raw_models(raw)
        self.config = load_config()
        # 显式回写本次改动，避免环境变量在 reload 时盖掉网页配置
        for key in ("coordinator_url", "node_id", "node_name", "token"):
            if key in updates and updates[key] is not None:
                setattr(self.config.settings.cluster, key, updates[key])
        await self.supervisor.reconfigure(self.config.enabled_models())
        enabled_models = ", ".join(
            f"{m.id}×{m.replicas}" for m in self.config.enabled_models()
        ) or "无"
        target = self.config.settings.cluster.coordinator_url or "未配置"
        self._log("info", f"配置已应用：协调端={target}，模型={enabled_models}")
        self.reconnect.set()


def build_agent_app() -> FastAPI:
    agent = RemoteAgent()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop_task = asyncio.create_task(agent.run())
        yield
        await agent.stop()
        loop_task.cancel()

    app = FastAPI(title="vg-agent", lifespan=lifespan)
    web_index = ROOT / "gateway" / "agent_web" / "index.html"

    @app.get("/", include_in_schema=False)
    async def index():
        if web_index.is_file():
            return FileResponse(web_index)
        return HTMLResponse("<h1>VoiceGeneration 副节点</h1><p>缺少 agent_web/index.html</p>", status_code=503)

    @app.get("/api/status")
    async def api_status():
        return agent.snapshot()

    @app.get("/api/jobs")
    async def api_jobs():
        return agent.jobs()

    @app.get("/api/logs")
    async def api_logs():
        return agent.logs()

    @app.get("/api/config")
    async def api_config():
        return agent.config_view()

    @app.put("/api/config")
    async def api_config_update(body: dict = Body(...)):
        await agent.apply_config(body)
        return {"ok": True}

    @app.post("/api/connect")
    async def api_connect():
        agent.set_enabled(True)
        return {"ok": True, "enabled": True}

    @app.post("/api/disconnect")
    async def api_disconnect():
        agent.set_enabled(False)
        return {"ok": True, "enabled": False}

    @app.get("/api/coordinator")
    async def api_coordinator():
        return await agent.coordinator_nodes()

    return app


if __name__ == "__main__":
    import uvicorn

    _cfg = load_config().settings.cluster
    print(f">> 副节点控制台 http://{_cfg.agent_host}:{_cfg.agent_port}  (node={_cfg.node_id})")
    uvicorn.run(build_agent_app(), host=_cfg.agent_host, port=_cfg.agent_port, log_level="info")
