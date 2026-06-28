"""集群节点 agent：认领任务 → 本地 worker 执行 → 回传结果。

两种用法：
- 内置（协调端进程内）：`EmbeddedAgent`，直接调用 cluster.* 与协调端的 finalize，无 HTTP。
- 远程（Windows 等）：`python -m gateway.agent`，长轮询协调端 /v1/cluster/* 认领并回传。
"""
from __future__ import annotations

import asyncio
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from .config import ROOT, load_config, load_raw_models, save_raw_models
from .supervisor import Supervisor


async def run_worker(supervisor: Supervisor, model_cfg, payload: dict, timeout: float = 1800) -> bytes:
    """取一个空闲 worker 副本，POST /synthesize，返回 WAV 字节，最后归还副本。"""
    st = await supervisor.acquire(model_cfg.id)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            r = await client.post(f"{st.base_url}/synthesize", json=payload)
        if r.status_code != 200:
            detail = r.json().get("error", r.text) if r.content else "未知错误"
            raise RuntimeError(str(detail))
        return r.content
    finally:
        supervisor.release(model_cfg.id, st)


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
            max_concurrency=self.state.supervisor.total_slots(), version="1.0.0",
        )
        while not self._stop:
            c = self._cluster_cfg
            capacity = max(0, self.state.supervisor.total_slots() - self._inflight)
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


# ---- 远程 agent（独立进程 + 本地 web 控制台：python -m gateway.agent）--------

class RemoteAgent:
    def __init__(self) -> None:
        self.config = load_config()
        self.supervisor = Supervisor(self.config.settings, self.config.enabled_models())
        self.active: dict[str, dict] = {}            # job_id -> {model, text, started}
        self.enabled = self.config.settings.cluster.enabled  # 是否主动连接(网页连接/断开控制)
        self.reconnect = asyncio.Event()
        self.status = {"connected": False, "last_error": None, "counters": {"leased": 0, "completed": 0, "failed": 0}}
        self._stop = False
        # trust_env=False：忽略 HTTP_PROXY/HTTPS_PROXY 等环境变量，直连协调端，
        # 避免被本机系统代理/VPN(Clash/V2Ray)截走导致 502。
        self._client = httpx.AsyncClient(timeout=1800, trust_env=False)
        self._asset_dir = Path(tempfile.gettempdir()) / "vg-agent-assets"
        self._asset_dir.mkdir(parents=True, exist_ok=True)
        self._asset_cache: dict[str, str] = {}

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
            if not self.enabled or not c.coordinator_url:
                self.status["connected"] = False
                await self._sleep_or_wake(3600)  # 断开态：挂起等待「连接」唤醒
                continue
            try:
                await self._register()
                self.status.update(connected=True, last_error=None)
                backoff = 1.0
            except Exception as exc:  # noqa: BLE001
                self.status.update(connected=False, last_error=str(exc))
                await self._sleep_or_wake(backoff)
                backoff = min(backoff * 2, 30)
                continue
            await self._lease_loop(c.coordinator_url)

    async def _lease_loop(self, url: str) -> None:
        while (not self._stop and not self.reconnect.is_set()
               and self.enabled and self.cluster.coordinator_url == url):
            capacity = max(0, self.supervisor.total_slots() - len(self.active))
            jobs: list[dict] = []
            if capacity > 0:
                try:
                    r = await self._client.post(
                        f"{self.base}/v1/cluster/lease", headers=self.headers,
                        json={"node_id": self.cluster.node_id, "models": self._models(), "capacity": capacity},
                        timeout=40,
                    )
                    r.raise_for_status()
                    jobs = r.json().get("jobs", [])
                    self.status.update(connected=True, last_error=None)
                except httpx.HTTPError as exc:
                    self.status.update(connected=False, last_error=str(exc))
                    return
            for job in jobs:
                self.status["counters"]["leased"] += 1
                # 同步登记，避免下一轮 lease 在任务populate active 前重复计算容量→超额认领
                self.active[job["id"]] = {"model": job["model"],
                                          "text": (job.get("text") or "")[:60],
                                          "started": time.monotonic()}
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
        started = time.perf_counter()
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
            }
            wav = await run_worker(self.supervisor, model, payload)
            await self._client.post(
                f"{self.base}/v1/cluster/jobs/{jid}/result", headers=self.headers,
                files={"audio": ("out.wav", wav, "audio/wav")},
                data={"node_id": self.cluster.node_id, "elapsed": str(time.perf_counter() - started)},
            )
            self.status["counters"]["completed"] += 1
        except Exception as exc:  # noqa: BLE001
            self.status["counters"]["failed"] += 1
            try:
                await self._client.post(f"{self.base}/v1/cluster/jobs/{jid}/fail",
                                        headers=self.headers,
                                        json={"node_id": self.cluster.node_id, "error": str(exc)})
            except httpx.HTTPError:
                pass
        finally:
            self.active.pop(jid, None)

    async def stop(self) -> None:
        self._stop = True
        self.reconnect.set()
        await self.supervisor.shutdown()
        await self._client.aclose()

    # ---- 给 web 控制台用 -----------------------------------------------
    def snapshot(self) -> dict:
        c, sup = self.cluster, self.supervisor
        return {
            "node_id": c.node_id, "node_name": c.node_name, "role": "agent",
            "coordinator_url": c.coordinator_url, "token_set": bool(c.token),
            "connected": self.status["connected"], "last_error": self.status["last_error"],
            "enabled": self.enabled, "total_slots": sup.total_slots(),
            "available": sup.available_capacity(), "inflight": len(self.active),
            "counters": self.status["counters"],
            "models": [
                {"id": m.id, "enabled": m.enabled, "replicas": m.replicas,
                 "device": m.options.get("device", "auto"), "loaded": sup.is_loaded(m.id)}
                for m in self.config.enabled_models()
            ],
        }

    def jobs(self) -> list[dict]:
        now = time.monotonic()
        return [{"id": jid, "model": j["model"], "text": j["text"], "elapsed": round(now - j["started"], 1)}
                for jid, j in list(self.active.items())]

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
