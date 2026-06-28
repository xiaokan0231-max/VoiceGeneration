"""集群节点 agent：认领任务 → 本地 worker 执行 → 回传结果。

两种用法：
- 内置（协调端进程内）：`EmbeddedAgent`，直接调用 cluster.* 与协调端的 finalize，无 HTTP。
- 远程（Windows 等）：`python -m gateway.agent`，长轮询协调端 /v1/cluster/* 认领并回传。
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import httpx

from .config import load_config
from .supervisor import Supervisor


async def run_worker(supervisor: Supervisor, model_cfg, payload: dict, timeout: float = 1800) -> bytes:
    """确保本地 worker 在跑，POST /synthesize，返回 WAV 字节。"""
    await supervisor.ensure_running(model_cfg.id)
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{model_cfg.base_url}/synthesize", json=payload)
    if r.status_code != 200:
        detail = r.json().get("error", r.text) if r.content else "未知错误"
        raise RuntimeError(str(detail))
    return r.content


# ---- 内置 agent（协调端进程内，无 HTTP）-------------------------------------

class EmbeddedAgent:
    def __init__(self, state) -> None:
        self.state = state              # gateway.main.App
        self._task: asyncio.Task | None = None
        self._inflight = 0
        self._stop = False

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

    async def _loop(self) -> None:
        from . import cluster
        c = self._cluster_cfg
        await asyncio.to_thread(
            cluster.register_node, node_id=c.node_id, name=c.node_name,
            role="coordinator", models=self._allowed_models(),
            max_concurrency=c.max_concurrency, version="1.0.0",
        )
        while not self._stop:
            c = self._cluster_cfg
            capacity = max(0, c.max_concurrency - self._inflight)
            jobs = []
            if capacity > 0:
                jobs = await asyncio.to_thread(
                    cluster.lease_jobs, c.node_id, self._allowed_models(), capacity, c.lease_ttl
                )
            await asyncio.to_thread(cluster.touch_node, c.node_id)
            for job in jobs:
                self._inflight += 1
                asyncio.create_task(self._run(job))
            await asyncio.sleep(0.05 if jobs else max(0.25, c.poll_interval))

    async def _run(self, job: dict) -> None:
        from . import cluster
        state = self.state
        c = self._cluster_cfg
        started = time.perf_counter()
        try:
            model = state.config.model(job["model"])
            if not model or not model.enabled:
                raise RuntimeError(f"模型未启用: {job['model']}")
            payload = state.build_payload(job)
            wav = await run_worker(state.supervisor, model, payload)
            await state.finalize_job(job["id"], wav, time.perf_counter() - started, c.node_id)
        except Exception as exc:  # noqa: BLE001
            await asyncio.to_thread(cluster.fail_or_requeue, job["id"], str(exc), c.max_attempts)
        finally:
            self._inflight -= 1


# ---- 远程 agent（独立进程：python -m gateway.agent）-------------------------

async def _remote_main() -> None:
    config = load_config()
    c = config.settings.cluster
    if not c.coordinator_url:
        print("错误：agent 模式需要在 cluster.coordinator_url 配置协调端地址", file=sys.stderr)
        sys.exit(1)
    base = c.coordinator_url.rstrip("/")
    headers = {"Authorization": f"Bearer {c.token}"} if c.token else {}
    supervisor = Supervisor(config.settings, config.enabled_models())
    supervisor.start_reaper()
    models = [m.id for m in config.enabled_models()]
    asset_dir = Path(tempfile.gettempdir()) / "vg-agent-assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    asset_cache: dict[str, str] = {}
    inflight = 0
    backoff = 1.0
    print(f">> agent {c.node_id} → {base}，可跑模型: {models}")

    async def register(client: httpx.AsyncClient) -> None:
        await client.post(f"{base}/v1/cluster/register", headers=headers, json={
            "node_id": c.node_id, "name": c.node_name, "role": "agent",
            "models": models, "max_concurrency": c.max_concurrency, "version": "1.0.0",
        })

    async def fetch_asset(client: httpx.AsyncClient, voice: str) -> str:
        if voice in asset_cache and Path(asset_cache[voice]).exists():
            return asset_cache[voice]
        r = await client.get(f"{base}/v1/cluster/asset/{voice}", headers=headers)
        r.raise_for_status()
        path = asset_dir / f"{voice}.wav"
        path.write_bytes(r.content)
        asset_cache[voice] = str(path)
        return str(path)

    async def run_job(client: httpx.AsyncClient, job: dict) -> None:
        nonlocal inflight
        started = time.perf_counter()
        try:
            model = config.model(job["model"])
            if not model or not model.enabled:
                raise RuntimeError(f"模型未启用: {job['model']}")
            ref_audio_path = None
            if job.get("is_clone"):
                ref_audio_path = await fetch_asset(client, job["voice"])
            payload = {
                "text": job["text"], "voice": job["voice"], "language": job.get("language"),
                "speed": job.get("speed", 1.0), "mode": job.get("mode", "clone"),
                "instruct_text": job.get("instruct_text"),
                "ref_audio_path": ref_audio_path, "ref_text": job.get("ref_text"),
            }
            wav = await run_worker(supervisor, model, payload)
            await client.post(
                f"{base}/v1/cluster/jobs/{job['id']}/result", headers=headers,
                files={"audio": ("out.wav", wav, "audio/wav")},
                data={"node_id": c.node_id, "elapsed": str(time.perf_counter() - started)},
            )
        except Exception as exc:  # noqa: BLE001
            try:
                await client.post(f"{base}/v1/cluster/jobs/{job['id']}/fail",
                                  headers=headers, json={"node_id": c.node_id, "error": str(exc)})
            except httpx.HTTPError:
                pass
        finally:
            inflight -= 1

    async with httpx.AsyncClient(timeout=1800) as client:
        while True:
            try:
                await register(client)
                backoff = 1.0
            except httpx.HTTPError:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
                continue
            while True:
                capacity = max(0, c.max_concurrency - inflight)
                jobs = []
                if capacity > 0:
                    try:
                        r = await client.post(
                            f"{base}/v1/cluster/lease", headers=headers,
                            json={"node_id": c.node_id, "models": models, "capacity": capacity},
                            timeout=40,
                        )
                        jobs = r.json().get("jobs", []) if r.status_code == 200 else []
                    except httpx.HTTPError:
                        break  # 协调端不可达 → 退回外层重连
                for job in jobs:
                    inflight += 1
                    asyncio.create_task(run_job(client, job))
                if not jobs:
                    await asyncio.sleep(c.poll_interval)


if __name__ == "__main__":
    asyncio.run(_remote_main())
