"""Worker 进程生命周期管理：每个模型一个【副本池】，按需拉起、空闲回收、健康检查。

关键：单个 worker 进程内部是串行的（GIL + 单 Metal/CUDA 队列），所以真正的并行
来自「同一模型开多个 worker 进程」。每个模型可配 replicas=N，本类为其维护 N 个
进程（端口 base..base+N-1），并发任务经 acquire()/release() 分发到空闲副本。
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass

import httpx

from .config import ROOT, ModelConfig, Settings


def _replicas(cfg: ModelConfig) -> int:
    return max(1, int(getattr(cfg, "replicas", 1) or 1))


@dataclass
class WorkerState:
    config: ModelConfig
    index: int
    port: int
    process: subprocess.Popen | None = None
    last_used: float = 0.0
    current_job_id: str | None = None
    current_text: str = ""
    work_started_at: float | None = None
    last_speed: float | None = None
    last_audio_seconds: float | None = None
    last_elapsed_seconds: float | None = None
    last_finished_at: float | None = None
    last_error: str | None = None

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://{self.config.host}:{self.port}"


class Supervisor:
    def __init__(self, settings: Settings, models: list[ModelConfig]):
        self.settings = settings
        self.pools: dict[str, list[WorkerState]] = {}
        self.free: dict[str, asyncio.Queue] = {}
        self._busy: dict[str, set[int]] = {}
        self._locks: dict[str, dict[int, asyncio.Lock]] = {}
        self._reaper_task: asyncio.Task | None = None
        for m in models:
            self._init_pool(m)

    def _init_pool(self, cfg: ModelConfig) -> None:
        n = _replicas(cfg)
        self.pools[cfg.id] = [
            WorkerState(config=cfg, index=i, port=cfg.port + i) for i in range(n)
        ]
        q: asyncio.Queue = asyncio.Queue()
        for i in range(n):
            q.put_nowait(i)
        self.free[cfg.id] = q
        self._busy[cfg.id] = set()
        self._locks[cfg.id] = {i: asyncio.Lock() for i in range(n)}

    # ---- 容量 -----------------------------------------------------------
    def total_slots(self) -> int:
        return sum(len(pool) for pool in self.pools.values())

    def slots_by_model(self) -> dict[str, int]:
        """Configured concurrency per model.

        Cluster scheduling must not treat an idle system/F5 slot as capacity for
        a CosyVoice job.  A worker process can only serve its configured model.
        """
        return {model_id: len(pool) for model_id, pool in self.pools.items()}

    def available_by_model(self) -> dict[str, int]:
        """Currently free worker slots per model."""
        return {
            model_id: max(0, len(pool) - len(self._busy.get(model_id, set())))
            for model_id, pool in self.pools.items()
        }

    def available_capacity(self) -> int:
        # Derive capacity from the authoritative busy set instead of Queue.qsize().
        # A worker released after a hot reconfigure belongs to an old pool and
        # must never inflate the new pool's capacity.
        return sum(self.available_by_model().values())

    def begin_work(self, st: WorkerState, job_id: str | None, text: str = "") -> None:
        st.current_job_id = job_id
        st.current_text = text[:80]
        st.work_started_at = time.time()
        st.last_error = None

    def finish_work(self, st: WorkerState, *, audio_seconds: float | None = None,
                    elapsed_seconds: float | None = None, error: str | None = None) -> None:
        if audio_seconds is not None and elapsed_seconds and elapsed_seconds > 0:
            st.last_speed = audio_seconds / elapsed_seconds
            st.last_audio_seconds = audio_seconds
            st.last_elapsed_seconds = elapsed_seconds
        st.last_finished_at = time.time()
        st.last_error = error
        st.current_job_id = None
        st.current_text = ""
        st.work_started_at = None

    def runtime_metrics(self) -> dict:
        """Return serializable per-worker state for cluster reporting."""
        now = time.time()
        workers = []
        for model_id, pool in self.pools.items():
            for st in pool:
                active = st.current_job_id is not None
                workers.append({
                    "id": f"{model_id}#{st.index + 1}", "model": model_id,
                    "index": st.index + 1, "port": st.port,
                    "started": st.running or active, "active": active,
                    "job_id": st.current_job_id, "text": st.current_text,
                    "elapsed_seconds": round(now - st.work_started_at, 1)
                    if active and st.work_started_at else None,
                    "speed": round(st.last_speed, 4) if st.last_speed is not None else None,
                    "audio_seconds": st.last_audio_seconds,
                    "inference_seconds": st.last_elapsed_seconds,
                    "last_finished_at": st.last_finished_at, "error": st.last_error,
                })
        active = [worker for worker in workers if worker["active"]]
        speeds = [worker["speed"] for worker in active if worker["speed"] is not None]
        return {
            "started_workers": sum(1 for worker in workers if worker["started"]),
            "working_workers": len(active),
            "total_speed": round(sum(speeds), 4) if speeds else None,
            "workers": workers, "updated_at": now,
        }

    # ---- 执行路径：取/还一个空闲副本 -----------------------------------
    async def acquire(self, model_id: str) -> WorkerState:
        q = self.free[model_id]
        while True:
            idx = await q.get()
            pool = self.pools.get(model_id)
            busy = self._busy.get(model_id)
            if pool is None or busy is None or idx >= len(pool) or idx in busy:
                continue
            st = pool[idx]
            busy.add(idx)
            break
        try:
            async with self._locks[model_id][idx]:
                if not st.running:
                    self._spawn(st)
                    await self._wait_healthy(st)
        except Exception:
            self.release(model_id, st)  # 起不来也要把槽位还回去
            raise
        st.last_used = time.time()
        return st

    def release(self, model_id: str, st: WorkerState) -> None:
        pool = self.pools.get(model_id)
        busy = self._busy.get(model_id)
        q = self.free.get(model_id)
        # Ignore stale releases from a pool that was replaced by hot config,
        # and make release idempotent to prevent duplicate queue tokens.
        if (pool is None or busy is None or q is None or st.index >= len(pool)
                or pool[st.index] is not st or st.index not in busy):
            return
        st.last_used = time.time()
        busy.remove(st.index)
        q.put_nowait(st.index)

    # ---- 生命周期 --------------------------------------------------------
    def start_reaper(self) -> None:
        self._reaper_task = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        if self._reaper_task:
            self._reaper_task.cancel()
        for pool in self.pools.values():
            for st in pool:
                self._stop(st)

    async def ensure_running(self, model_id: str) -> None:
        """管理用：预热整个副本池（启动/重启按钮）。"""
        if model_id not in self.pools:
            raise KeyError(model_id)
        for st in self.pools[model_id]:
            async with self._locks[model_id][st.index]:
                if not st.running:
                    self._spawn(st)
                    await self._wait_healthy(st)
                st.last_used = time.time()

    async def stop(self, model_id: str) -> None:
        if model_id not in self.pools:
            raise KeyError(model_id)
        for st in self.pools[model_id]:
            self._stop(st)
        q = self.free[model_id]
        while not q.empty():
            q.get_nowait()
        self._busy[model_id].clear()
        for st in self.pools[model_id]:
            q.put_nowait(st.index)

    async def restart(self, model_id: str) -> None:
        await self.stop(model_id)
        await self.ensure_running(model_id)

    async def reconfigure(self, models: list[ModelConfig]) -> None:
        """热应用新的模型注册表（含 replicas/端口变化），无需重启网关。"""
        incoming = {m.id: m for m in models}
        # 停掉被删除或配置变化的池
        for model_id, pool in list(self.pools.items()):
            new_cfg = incoming.get(model_id)
            if new_cfg is None or new_cfg != pool[0].config:
                for st in pool:
                    self._stop(st)
                del self.pools[model_id]
                self.free.pop(model_id, None)
                self._busy.pop(model_id, None)
                self._locks.pop(model_id, None)
        # 新建/重建变化的池
        for model_id, cfg in incoming.items():
            if model_id not in self.pools:
                self._init_pool(cfg)

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            now = time.time()
            timeout = self.settings.worker_idle_timeout
            for pool in self.pools.values():
                for st in pool:
                    if st.running and now - st.last_used > timeout:
                        self._stop(st)

    # ---- 进程操作 --------------------------------------------------------
    def _spawn(self, st: WorkerState) -> None:
        cfg = st.config
        env = os.environ.copy()
        env.update({
            "VG_BACKEND": cfg.backend,
            "VG_MODEL_ID": cfg.id,
            "VG_HOST": cfg.host,
            "VG_PORT": str(st.port),
            "VG_OPTIONS": json.dumps(cfg.options, ensure_ascii=False),
            "PYTHONPATH": str(ROOT),
            "PYTHONUNBUFFERED": "1",
        })
        log_dir = ROOT / "cache" / "_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / f"worker-{cfg.id}-{st.index}.log", "ab")
        st.process = subprocess.Popen(
            [cfg.python_exe, "-m", "worker_runtime.server"],
            cwd=str(ROOT), env=env, stdout=log, stderr=log, start_new_session=True,
        )
        st.last_used = time.time()  # 立刻打时间戳，避免回收器在启动期误杀

    def _stop(self, st: WorkerState) -> None:
        process = st.process
        if process and process.poll() is None:
            try:
                if os.name == "nt":
                    process.terminate()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            except (AttributeError, ProcessLookupError, PermissionError):
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    process.wait(timeout=2)
                except (AttributeError, ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                    pass
        st.process = None
        if st.current_job_id is not None:
            self.finish_work(st, error="worker 已停止")

    async def _wait_healthy(self, st: WorkerState) -> None:
        url = f"{st.base_url}/health"
        deadline = time.time() + self.settings.worker_start_timeout
        # Worker endpoints are always local. Ignore HTTP_PROXY/HTTPS_PROXY so
        # VPN/TUN adapters (Clash, V2Ray, FastLink, etc.) cannot intercept
        # 127.0.0.1 health checks and cause a false startup timeout.
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            while time.time() < deadline:
                if not st.running:
                    raise RuntimeError(
                        f"worker '{st.config.id}#{st.index}' 启动即退出，"
                        f"详见 cache/_logs/worker-{st.config.id}-{st.index}.log"
                    )
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        self._stop(st)
        raise TimeoutError(f"worker '{st.config.id}#{st.index}' 在 {self.settings.worker_start_timeout}s 内未就绪")

    def is_loaded(self, model_id: str) -> bool:
        pool = self.pools.get(model_id)
        return bool(pool and any(st.running for st in pool))
