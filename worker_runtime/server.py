"""通用 worker 服务：按环境变量加载某个 TTSBackend 并对内提供 HTTP 接口。

由 gateway 的 supervisor 以子进程方式拉起，约定的环境变量：
    VG_BACKEND   "module.path:ClassName"，如 workers.system.backend:SystemBackend
    VG_MODEL_ID  模型 id
    VG_HOST      监听地址
    VG_PORT      监听端口
    VG_OPTIONS   JSON 字符串，传给后端的 options
"""
from __future__ import annotations

import importlib
import json
import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response

from worker_runtime.base import SynthRequest, TTSBackend


def load_backend() -> TTSBackend:
    spec = os.environ["VG_BACKEND"]
    module_path, _, class_name = spec.partition(":")
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    model_id = os.environ.get("VG_MODEL_ID", "unknown")
    options = json.loads(os.environ.get("VG_OPTIONS", "{}"))
    return cls(model_id, options)


def build_app() -> FastAPI:
    backend = load_backend()
    app = FastAPI(title=f"vg-worker:{backend.model_id}")

    @app.get("/health")
    async def health():
        return {"ok": True, "model": backend.model_id}

    @app.get("/info")
    async def info():
        return {"model": backend.model_id, "voices": backend.list_voices()}

    @app.post("/synthesize")
    async def synthesize(req: SynthRequest):
        try:
            wav = backend.synthesize(req)
        except Exception as e:  # 把后端异常透传给 gateway
            return JSONResponse(status_code=500, content={"error": str(e)})
        return Response(content=wav, media_type="audio/wav")

    return app


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("VG_HOST", "127.0.0.1")
    port = int(os.environ.get("VG_PORT", "8101"))
    uvicorn.run(build_app(), host=host, port=port, log_level="info")
