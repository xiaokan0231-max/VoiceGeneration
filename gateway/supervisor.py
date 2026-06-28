"""Worker 进程生命周期管理：按需拉起、空闲回收、健康检查。

每个模型在自己的 conda 环境里以独立子进程运行 worker_runtime.server，
gateway 通过本地 HTTP 与之通信。这样不同模型的依赖（torch 版本等）互不冲突，
且同一时刻可只保留一个模型常驻内存（适合 24G 内存的机器）。
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field

import httpx

from .config import ROOT, ModelConfig, Settings


@dataclass
class WorkerState:
    config: ModelConfig
    process: subprocess.Popen | None = None
    last_used: float = field(default_factory=lambda: 0.0)

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None


class Supervisor:
    def __init__(self, settings: Settings, models: list[ModelConfig]):
        self.settings = settings
        self.workers: dict[str, WorkerState] = {
            m.id: WorkerState(config=m) for m in models
        }
        self._locks: dict[str, asyncio.Lock] = {m.id: asyncio.Lock() for m in models}
        self._reaper_task: asyncio.Task | None = None

    # ---- 生命周期 --------------------------------------------------------
    def start_reaper(self) -> None:
        self._reaper_task = asyncio.create_task(self._reap_loop())

    async def shutdown(self) -> None:
        if self._reaper_task:
            self._reaper_task.cancel()
        for st in self.workers.values():
            self._stop(st)

    async def stop(self, model_id: str) -> None:
        if model_id not in self.workers:
            raise KeyError(model_id)
        async with self._locks[model_id]:
            self._stop(self.workers[model_id])

    async def restart(self, model_id: str) -> WorkerState:
        await self.stop(model_id)
        return await self.ensure_running(model_id)

    async def reconfigure(self, models: list[ModelConfig]) -> None:
        """Apply a newly persisted model registry without restarting the gateway."""
        incoming = {m.id: m for m in models}
        for model_id, state in list(self.workers.items()):
            new_cfg = incoming.get(model_id)
            if new_cfg is None or new_cfg != state.config:
                self._stop(state)
        self.workers = {
            model_id: (
                self.workers[model_id]
                if model_id in self.workers and self.workers[model_id].config == cfg
                else WorkerState(config=cfg)
            )
            for model_id, cfg in incoming.items()
        }
        self._locks = {model_id: self._locks.get(model_id, asyncio.Lock()) for model_id in incoming}

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(15)
            now = time.time()
            timeout = self.settings.worker_idle_timeout
            for st in self.workers.values():
                if st.running and now - st.last_used > timeout:
                    self._stop(st)

    # ---- 拉起 / 关闭 -----------------------------------------------------
    async def ensure_running(self, model_id: str) -> WorkerState:
        st = self.workers[model_id]
        async with self._locks[model_id]:
            if st.running:
                st.last_used = time.time()
                return st
            self._spawn(st)
            await self._wait_healthy(st)
            st.last_used = time.time()
            return st

    def _spawn(self, st: WorkerState) -> None:
        cfg = st.config
        env = os.environ.copy()
        env.update(
            {
                "VG_BACKEND": cfg.backend,
                "VG_MODEL_ID": cfg.id,
                "VG_HOST": cfg.host,
                "VG_PORT": str(cfg.port),
                "VG_OPTIONS": json.dumps(cfg.options, ensure_ascii=False),
                "PYTHONPATH": str(ROOT),
                "PYTHONUNBUFFERED": "1",
            }
        )
        cmd = [cfg.python_exe, "-m", "worker_runtime.server"]
        log_dir = ROOT / "cache" / "_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log = open(log_dir / f"worker-{cfg.id}.log", "ab")
        st.process = subprocess.Popen(
            cmd, cwd=str(ROOT), env=env, stdout=log, stderr=log,
            start_new_session=True,  # 自成进程组，便于整体回收
        )
        # 立刻打上时间戳，避免空闲回收器在启动期间（last_used 仍为 0）误杀
        st.last_used = time.time()

    def _stop(self, st: WorkerState) -> None:
        if st.process and st.process.poll() is None:
            try:
                os.killpg(os.getpgid(st.process.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                st.process.terminate()
        st.process = None

    async def _wait_healthy(self, st: WorkerState) -> None:
        url = f"{st.config.base_url}/health"
        deadline = time.time() + self.settings.worker_start_timeout
        async with httpx.AsyncClient(timeout=5) as client:
            while time.time() < deadline:
                if not st.running:
                    raise RuntimeError(
                        f"worker '{st.config.id}' 启动即退出，详见 cache/_logs/worker-{st.config.id}.log"
                    )
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(1)
        self._stop(st)
        raise TimeoutError(f"worker '{st.config.id}' 在 {self.settings.worker_start_timeout}s 内未就绪")

    def is_loaded(self, model_id: str) -> bool:
        state = self.workers.get(model_id)
        return bool(state and state.running)
