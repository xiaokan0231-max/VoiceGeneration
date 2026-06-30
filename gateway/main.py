"""VoiceGeneration gateway and same-origin Web workbench."""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import platform
import signal
import socket
import subprocess
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import (
    Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text as sql_text

from .audio import convert, media_type
from .media_tools import media_binary
from .cache import AudioCache
from .config import (
    ROOT, AppConfig, Voice, load_config, load_raw_models, save_raw_models,
)
from . import cluster
from .agent import EmbeddedAgent, run_worker
from .database import (
    active_generations, audio_file, create_project, db_session, delete_generation,
    delete_project, finish_generation, get_generation, get_project, history_dict,
    init_database, list_generations, list_projects, new_generation, project_name_map,
    set_generation_project, update_project,
)
from .schemas import (
    ClusterLease, ClusterRegister, HistoryProjectUpdate, JobFail, ModelConfigUpdate,
    ModelInfo, ProjectCreate, ProjectUpdate, SettingsUpdate, TTSRequest, VoiceInfo,
)
from .supervisor import Supervisor
from .voice_store import create_voice, delete_voice, update_voice


logger = logging.getLogger(__name__)


class App:
    def __init__(self) -> None:
        self.config: AppConfig = load_config()
        settings = self.config.settings
        self.cache = AudioCache(settings.cache_path, int(settings.cache_max_gb * 1024**3))
        self.supervisor = Supervisor(settings, self.config.enabled_models())
        self.embedded: EmbeddedAgent | None = None
        self.cluster_reaper = None

    @property
    def node_id(self) -> str:
        return self.config.settings.cluster.node_id

    async def reload_config(self) -> None:
        new_config = load_config()
        await self.supervisor.reconfigure(new_config.enabled_models())
        self.config = new_config
        self.supervisor.settings = new_config.settings
        self.cache.max_bytes = int(new_config.settings.cache_max_gb * 1024**3)
        if self.embedded:
            await self.embedded.sync_registration(force=True)
            self.embedded.publish_runtime()

    def reload_voices(self) -> None:
        self.config = load_config()

    def resolve_ref(self, model, voice_id: str) -> tuple[str | None, str | None, bool]:
        """返回 (ref_audio_path, ref_text, is_clone)；不合法抛 ValueError。"""
        clone = self.config.clone_voice(voice_id)
        if clone:
            if not model.supports_cloning or not clone.usable_by(model.id):
                raise ValueError(f"音色 {clone.id} 不允许用于模型 {model.id}")
            if not clone.ref_audio_path.exists():
                raise ValueError(f"参考音频不存在: {clone.ref_audio}")
            return str(clone.ref_audio_path), clone.ref_text, True
        if model.supports_cloning:
            raise ValueError(f"模型 {model.id} 需要一个克隆音色")
        return None, None, False

    def build_payload(self, job: dict) -> dict:
        model = self.config.model(job["model"])
        ref_audio_path, ref_text, _ = self.resolve_ref(model, job["voice"])
        return {
            "text": job["text"], "voice": job["voice"], "language": job.get("language"),
            "speed": job.get("speed", 1.0), "mode": job.get("mode", "clone"),
            "instruct_text": job.get("instruct_text"),
            "ref_audio_path": ref_audio_path, "ref_text": ref_text,
        }

    async def finalize_job(
        self, job_id: str, wav_bytes: bytes, elapsed: float, node_id: str,
        *, worker_id: str | None = None, inference_seconds: float | None = None,
    ) -> None:
        """协调端落地结果：转码→入缓存→更新历史→去重（阻塞操作放线程池）。"""
        row = await asyncio.to_thread(get_generation, job_id)
        if not row:
            return
        fmt = row.format
        cache_key = row.cache_key
        data = await asyncio.to_thread(convert, wav_bytes, fmt)
        output = await asyncio.to_thread(self.cache.put, cache_key, fmt, data)
        rel = _relative(output)
        dur = await asyncio.to_thread(_duration_from_file, output)
        mt = media_type(fmt)
        await asyncio.to_thread(
            finish_generation, job_id, status="completed", assigned_node=node_id,
            worker_id=worker_id, inference_seconds=inference_seconds,
            audio_path=rel, mime_type=mt, byte_size=len(data),
            duration_seconds=dur, cache_hit=False, elapsed_seconds=elapsed,
            lease_expires_at=None,
        )
        await asyncio.to_thread(cluster.dedup_completed, cache_key, rel, mt, len(data), dur, job_id)


async def _cluster_reaper(state: App) -> None:
    """协调端：回收过期租约 + 标记离线节点。"""
    while True:
        await asyncio.sleep(10)
        c = state.config.settings.cluster
        try:
            await asyncio.to_thread(cluster.requeue_expired, c.max_attempts)
            await asyncio.to_thread(cluster.mark_nodes_offline, c.node_timeout)
        except Exception:
            pass


async def _wait_for_job(job_id: str, timeout: float):
    """轮询等待任务到终态。返回行或 None(超时)。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = await asyncio.to_thread(get_generation, job_id)
        if row and row.status in ("completed", "failed", "cancelled"):
            return row
        await asyncio.sleep(0.25)
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    state = App()
    app.state.app = state
    state.supervisor.start_reaper()
    # 协调端：启动残留租约重入队 + 回收器；按需启动内置本地 agent。
    await asyncio.to_thread(cluster.requeue_all_leased)
    state.cluster_reaper = asyncio.create_task(_cluster_reaper(state))
    if state.config.settings.cluster.coordinator_runs_jobs:
        state.embedded = EmbeddedAgent(state)
        state.embedded.start()
    yield
    if state.embedded:
        await state.embedded.stop()
    if state.cluster_reaper:
        state.cluster_reaper.cancel()
    await state.supervisor.shutdown()


app = FastAPI(title="VoiceGeneration", version="1.0.0", lifespan=lifespan)


def get_state(request: Request) -> App:
    return request.app.state.app


def check_auth(authorization: str | None = Header(None)):
    return authorization


def _require_token(state: App, authorization: str | None) -> None:
    token = state.config.settings.api_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="无效或缺失的 API token")


def _voice_name(config: AppConfig, voice_id: str) -> str:
    clone = config.clone_voice(voice_id)
    if clone:
        return clone.name
    try:
        from workers.system.backend import VOICES
        builtin = next((v for v in VOICES if v["id"] == voice_id), None)
        if builtin:
            return builtin["name"]
    except Exception:
        pass
    return voice_id


def _duration_from_wav(data: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            return wav.getnframes() / float(wav.getframerate())
    except (wave.Error, EOFError, ZeroDivisionError):
        return None


def _duration_from_file(path: Path) -> float | None:
    try:
        proc = subprocess.run(
            [media_binary("ffprobe"), "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return float(proc.stdout.strip())
    except Exception:
        return None


def _relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


async def _submit_generation(state: App, body: TTSRequest):
    """Validate and persist one generation, returning ``(row, cached_bytes)``."""
    config = state.config
    model = config.model(body.model)
    if not model or not model.enabled:
        raise HTTPException(404, f"未找到已启用的模型: {body.model}")
    if body.mode != "clone" and model.id != "cosyvoice3":
        raise HTTPException(400, f"模型 {model.id} 不支持 {body.mode} 模式")
    if body.mode == "instruct" and not (body.instruct_text or "").strip():
        raise HTTPException(400, "指令控制模式必须填写风格指令")

    fmt = body.format or config.settings.default_format
    if fmt not in {"wav", "mp3", "opus"}:
        raise HTTPException(400, f"不支持的输出格式: {fmt}")

    try:
        ref_audio_path, ref_text, _ = state.resolve_ref(model, body.voice)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    cache_key = state.cache.key({
        "model": model.id,
        "voice": body.voice,
        "language": body.language,
        "text": body.text,
        "speed": body.speed,
        "format": fmt,
        "mode": body.mode,
        "instruct_text": body.instruct_text or "",
        "model_options": model.options,
        "ref": ref_audio_path or "",
        "ref_text": ref_text or "",
    })
    project_id = body.project_id or None
    if project_id and not get_project(project_id):
        raise HTTPException(404, f"未找到项目: {project_id}")

    common = {
        "text": body.text, "model_id": model.id, "voice_id": body.voice,
        "voice_name": _voice_name(config, body.voice), "project_id": project_id,
        "mode": body.mode, "language": body.language, "speed": body.speed,
        "format": fmt, "instruct_text": body.instruct_text, "cache_key": cache_key,
    }
    cached = state.cache.get(cache_key, fmt)
    if cached:
        data = await asyncio.to_thread(cached.read_bytes)
        row = new_generation(
            **common, status="completed", assigned_node=state.node_id,
            audio_path=_relative(cached), mime_type=media_type(fmt), byte_size=len(data),
            duration_seconds=await asyncio.to_thread(_duration_from_file, cached),
            cache_hit=True, elapsed_seconds=0.0,
        )
        await asyncio.to_thread(finish_generation, row.id)
        return get_generation(row.id), data

    return new_generation(**common, status="queued", priority=0), None


def _generation_payload(row) -> dict:
    payload = history_dict(
        row, project_name_map().get(row.project_id),
        cluster.node_name_map().get(row.assigned_node),
    )
    payload["audio_url"] = f"/v1/history/{row.id}/audio" if payload["audio_available"] else None
    return payload


def _parse_models(value: str | None) -> list[str]:
    if not value:
        return ["cosyvoice3", "f5_tts"]
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    except json.JSONDecodeError:
        pass
    return [item.strip() for item in value.split(",") if item.strip()]


def _public_model(model, state: App) -> dict:
    return {
        "id": model.id,
        "description": model.description,
        "enabled": model.enabled,
        "python": model.python,
        "host": model.host,
        "port": model.port,
        "replicas": model.replicas,
        "languages": model.languages,
        "supports_cloning": model.supports_cloning,
        "options": model.options,
        "loaded": state.supervisor.is_loaded(model.id),
    }


@app.get("/health")
async def health():
    return {"ok": True, "version": app.version}


@app.get("/v1/models", response_model=list[ModelInfo])
async def list_models(request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    return [
        ModelInfo(
            id=model.id,
            description=model.description,
            enabled=model.enabled,
            languages=model.languages,
            supports_cloning=model.supports_cloning,
            loaded=state.supervisor.is_loaded(model.id),
        )
        for model in state.config.enabled_models()
    ]


@app.get("/v1/voices", response_model=list[VoiceInfo])
async def list_voices(
    request: Request, model: str | None = None,
    authorization: str | None = Depends(check_auth),
):
    """List voices from config only; never wakes a heavyweight model worker."""
    state = get_state(request)
    _require_token(state, authorization)
    models = state.config.enabled_models()
    if model:
        models = [item for item in models if item.id == model]
        if not models:
            raise HTTPException(404, f"未找到已启用的模型: {model}")

    result: list[VoiceInfo] = []
    for model_cfg in models:
        if model_cfg.supports_cloning:
            for voice in state.config.voices:
                if voice.usable_by(model_cfg.id):
                    result.append(VoiceInfo(
                        id=voice.id, name=voice.name, language=voice.language,
                        kind="clone", model=model_cfg.id,
                    ))
        # 非克隆模型（如 Style-Bert-VITS2）的内置音色在 models.yaml 的 options.voices 里
        # 静态声明，gateway 直接据此列出，无需唤醒重型 worker。
        for voice in (model_cfg.options.get("voices") or []):
            result.append(VoiceInfo(
                id=voice["id"], name=voice.get("name", voice["id"]),
                language=voice.get("language", ""), kind="builtin", model=model_cfg.id,
            ))
        if model_cfg.id == "system":
            from workers.system.backend import VOICES
            for voice in VOICES:
                result.append(VoiceInfo(
                    id=voice["id"], name=voice["name"], language=voice["language"],
                    kind="builtin", model=model_cfg.id,
                ))
    return result


@app.get("/v1/active-jobs")
async def active_jobs(request: Request, authorization: str | None = Depends(check_auth)):
    """全集群当前未完成(排队/生成中)的任务，供右上角任务徽标与抽屉显示。"""
    state = get_state(request)
    _require_token(state, authorization)
    rows, total = await asyncio.to_thread(active_generations, 50)
    return {"total": total, "items": [_generation_payload(row) for row in rows]}


@app.get("/v1/voice-library")
async def voice_library(request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    return [
        {
            "id": voice.id,
            "name": voice.name,
            "language": voice.language,
            "ref_text": voice.ref_text,
            "models": voice.models,
            "audio_url": f"/v1/voices/{voice.id}/audio",
        }
        for voice in state.config.voices
    ]


# 各语言的试听示例句（用于在音色库里快速听到某个模型/音色的声音）
_PREVIEW_SAMPLES = {
    "ja": "こんにちは。これは音声のサンプルです。どうぞよろしくお願いします。",
    "zh": "你好，这是一段声音示例，希望你会喜欢。",
    "en": "Hello, this is a voice sample. Nice to meet you.",
}


def _preview_language(config: AppConfig, model, voice_id: str) -> str:
    clone = config.clone_voice(voice_id)
    if clone:
        return clone.language
    for v in (model.options.get("voices") or []):
        if v.get("id") == voice_id:
            return v.get("language", "ja")
    if model.id == "system":
        from workers.system.backend import VOICES
        for v in VOICES:
            if v["id"] == voice_id:
                return v["language"]
    return (model.languages or ["ja"])[0]


@app.get("/v1/voices/{model_id}/{voice_id}/preview")
async def voice_preview(model_id: str, voice_id: str, request: Request,
                        authorization: str | None = Depends(check_auth)):
    """合成一句固定示例供试听：走缓存、不写历史，仅本机可运行任务时可用。"""
    state = get_state(request)
    _require_token(state, authorization)
    model = state.config.model(model_id)
    if not model or not model.enabled:
        raise HTTPException(404, f"未找到已启用的模型: {model_id}")
    lang = _preview_language(state.config, model, voice_id)
    text = _PREVIEW_SAMPLES.get(lang, _PREVIEW_SAMPLES["ja"])
    try:
        ref_audio_path, ref_text, _ = state.resolve_ref(model, voice_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    cache_key = state.cache.key({
        "preview": 1, "model": model.id, "voice": voice_id, "text": text,
        "language": lang, "model_options": model.options,
        "ref": ref_audio_path or "", "ref_text": ref_text or "",
    })
    cached = state.cache.get(cache_key, "wav")
    if cached is not None:
        data = await asyncio.to_thread(cached.read_bytes)
        return Response(content=data, media_type="audio/wav", headers={"X-Cache": "HIT"})
    if not state.config.settings.cluster.coordinator_runs_jobs:
        raise HTTPException(503, "本机未启用本地合成（coordinator_runs_jobs=false），无法试听")
    payload = {
        "text": text, "voice": voice_id, "language": lang, "speed": 1.0,
        "mode": "clone", "instruct_text": None,
        "ref_audio_path": ref_audio_path, "ref_text": ref_text,
    }
    try:
        result = await run_worker(state.supervisor, model, payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"试听合成失败：{exc}") from exc
    await asyncio.to_thread(state.cache.put, cache_key, "wav", result.wav)
    return Response(content=result.wav, media_type="audio/wav", headers={"X-Cache": "MISS"})


@app.post("/v1/tts")
async def tts(
    request: Request, body: TTSRequest,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    record, cached_data = await _submit_generation(state, body)
    base_headers = {"Content-Disposition": f'attachment; filename="voice.{record.format}"'}
    if cached_data is not None:
        return Response(content=cached_data, media_type=media_type(record.format), headers={
            **base_headers, "X-Generation-Id": record.id, "X-Cache": "HIT",
            "X-Node": state.node_id,
        })

    # 未命中：入队，等任意工作节点（Mac 内置 agent / 远程 agent）完成。
    row = await _wait_for_job(
        record.id, state.config.settings.worker_start_timeout + 900,
    )
    if row is None:
        raise HTTPException(504, "生成超时：没有可用工作节点或排队过久")
    if row.status == "failed":
        raise HTTPException(502, f"语音生成失败：{row.error_message}")
    if row.status == "cancelled":
        raise HTTPException(409, "生成任务已取消")
    path = audio_file(row)
    if not path:
        raise HTTPException(502, "生成完成但音频缺失")
    data = await asyncio.to_thread(path.read_bytes)
    return Response(content=data, media_type=media_type(row.format), headers={
        **base_headers, "X-Generation-Id": record.id, "X-Cache": "MISS",
        "X-Node": row.assigned_node or "",
    })


@app.post("/v1/generations")
async def generation_create(
    request: Request, body: TTSRequest,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    row, cached_data = await _submit_generation(state, body)
    return JSONResponse(
        status_code=200 if cached_data is not None else 202,
        content=_generation_payload(row),
    )


@app.get("/v1/generations/{generation_id}")
async def generation_get(
    generation_id: str, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    row = await asyncio.to_thread(get_generation, generation_id)
    if not row:
        raise HTTPException(404, "未找到生成任务")
    return _generation_payload(row)


@app.delete("/v1/generations/{generation_id}")
async def generation_cancel(
    generation_id: str, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    outcome = await asyncio.to_thread(cluster.cancel_queued_job, generation_id)
    if outcome == "missing":
        raise HTTPException(404, "未找到生成任务")
    if outcome == "conflict":
        raise HTTPException(409, "任务已被 Worker 领取，无法安全取消")
    return _generation_payload(get_generation(generation_id))


@app.get("/v1/history")
async def history_list(
    request: Request,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    model: str | None = None,
    status: str | None = None,
    q: str | None = None,
    project: str | None = None,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    rows, total = list_generations(
        page=page, page_size=page_size, model=model, status=status, query=q,
        project=project,
    )
    pnames = project_name_map()
    nnames = cluster.node_name_map()
    return {
        "items": [history_dict(row, pnames.get(row.project_id), nnames.get(row.assigned_node)) for row in rows],
        "total": total, "page": page, "page_size": page_size,
    }


@app.patch("/v1/history/{generation_id}")
async def history_set_project(
    generation_id: str, body: HistoryProjectUpdate, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    if body.project_id and not get_project(body.project_id):
        raise HTTPException(404, f"未找到项目: {body.project_id}")
    if not set_generation_project(generation_id, body.project_id):
        raise HTTPException(404, "未找到生成记录")
    row = get_generation(generation_id)
    return history_dict(row, project_name_map().get(row.project_id),
                        cluster.node_name_map().get(row.assigned_node))


@app.get("/v1/history/{generation_id}/audio")
async def history_audio(
    generation_id: str, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    row = get_generation(generation_id)
    if not row:
        raise HTTPException(404, "未找到生成记录")
    path = audio_file(row)
    if not path:
        raise HTTPException(410, "该音频已从磁盘缓存清理")
    return FileResponse(path, media_type=row.mime_type or media_type(row.format), filename=path.name)


@app.delete("/v1/history/{generation_id}")
async def history_delete(
    generation_id: str, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    if not delete_generation(generation_id):
        raise HTTPException(404, "未找到生成记录")
    return {"ok": True}


@app.post("/v1/voices")
async def voice_create(
    request: Request,
    name: str = Form(...),
    language: str = Form(...),
    ref_text: str = Form(...),
    models: str | None = Form(None),
    voice_id: str | None = Form(None),
    audio: UploadFile = File(...),
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    try:
        voice = create_voice(
            name=name, language=language, ref_text=ref_text,
            models=_parse_models(models), audio=await audio.read(), voice_id=voice_id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    state.reload_voices()
    return {
        "id": voice.id, "name": voice.name, "language": voice.language,
        "ref_text": voice.ref_text, "models": voice.models,
        "audio_url": f"/v1/voices/{voice.id}/audio",
    }


@app.put("/v1/voices/{voice_id}")
async def voice_update(
    voice_id: str, request: Request,
    name: str = Form(...),
    language: str = Form(...),
    ref_text: str = Form(...),
    models: str | None = Form(None),
    audio: UploadFile | None = File(None),
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    try:
        voice = update_voice(
            voice_id, name=name, language=language, ref_text=ref_text,
            models=_parse_models(models), audio=await audio.read() if audio else None,
        )
    except KeyError as exc:
        raise HTTPException(404, "未找到音色") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    state.reload_voices()
    return {
        "id": voice.id, "name": voice.name, "language": voice.language,
        "ref_text": voice.ref_text, "models": voice.models,
        "audio_url": f"/v1/voices/{voice.id}/audio",
    }


@app.get("/v1/voices/{voice_id}/audio")
async def voice_audio(
    voice_id: str, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    voice = state.config.clone_voice(voice_id)
    if not voice or not voice.ref_audio_path.is_file():
        raise HTTPException(404, "未找到参考音频")
    return FileResponse(voice.ref_audio_path, media_type="audio/wav", filename=f"{voice.id}.wav")


@app.delete("/v1/voices/{voice_id}")
async def voice_delete(
    voice_id: str, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    if not delete_voice(voice_id):
        raise HTTPException(404, "未找到音色")
    state.reload_voices()
    return {"ok": True}


@app.get("/v1/system")
async def system_info(request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    database_ok = False
    try:
        with db_session() as db:
            database_ok = bool(db.scalar(sql_text("SELECT 1")))
    except Exception:
        pass
    cache_bytes = sum(
        path.stat().st_size for path in state.config.settings.cache_path.rglob("*")
        if path.is_file() and path.suffix in {".wav", ".mp3", ".opus", ".ogg"}
    )
    return {
        "service": "online",
        "version": app.version,
        "platform": platform.platform(),
        "apple_silicon": platform.system() == "Darwin" and platform.machine() == "arm64",
        "mps": platform.system() == "Darwin" and platform.machine() == "arm64",
        "database": "online" if database_ok else "offline",
        "cache_bytes": cache_bytes,
        "cache_limit_bytes": state.cache.max_bytes,
        "models": [_public_model(model, state) for model in state.config.models],
    }


@app.get("/v1/settings")
async def settings_get(request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    settings = state.config.settings
    return {
        "default_model": settings.default_model,
        "default_format": settings.default_format,
        "worker_idle_timeout": settings.worker_idle_timeout,
        "worker_start_timeout": settings.worker_start_timeout,
        "cache_max_gb": settings.cache_max_gb,
        "models": [_public_model(model, state) for model in state.config.models],
    }


@app.put("/v1/settings")
async def settings_update(
    body: SettingsUpdate, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    raw = load_raw_models()
    values = body.model_dump(exclude_none=True)
    if "default_model" in values:
        model_ids = {model.get("id") for model in raw.get("models", []) if model.get("enabled", True)}
        if values["default_model"] not in model_ids:
            raise HTTPException(400, "默认模型必须处于启用状态")
    raw.setdefault("settings", {}).update(values)
    save_raw_models(raw)
    await state.reload_config()
    return await settings_get(request, authorization)


@app.put("/v1/models/{model_id}/config")
async def model_config_update(
    model_id: str, body: ModelConfigUpdate, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    raw = load_raw_models()
    model = next((item for item in raw.get("models", []) if item.get("id") == model_id), None)
    if not model:
        raise HTTPException(404, "未找到模型")
    values = body.model_dump(exclude_none=True)
    options = values.pop("options", None)
    python_path = values.get("python")
    if python_path and not Path(python_path).is_file():
        raise HTTPException(400, "Python 路径不存在")
    port = int(values.get("port", model.get("port", 0)))
    replicas = int(values.get("replicas", model.get("replicas", 1)) or 1)
    if port + replicas - 1 > 65535:
        raise HTTPException(400, "端口范围超出 65535")
    for item in raw.get("models", []):
        if item.get("id") == model_id:
            continue
        other_port = int(item.get("port", 0))
        other_replicas = int(item.get("replicas", 1) or 1)
        if max(port, other_port) < min(port + replicas, other_port + other_replicas):
            raise HTTPException(400, f"端口范围与模型 {item.get('id')} 冲突")
    if options is not None:
        device = options.get("device", model.get("options", {}).get("device"))
        if device not in {None, "auto", "mps", "cpu"}:
            raise HTTPException(400, "设备只能是 auto、mps 或 cpu")
        for key in ("repo_dir", "model_dir"):
            value = options.get(key)
            if value and not (ROOT / value).resolve().exists() and not Path(value).expanduser().exists():
                raise HTTPException(400, f"{key} 路径不存在")
        model.setdefault("options", {}).update(options)
    model.update(values)
    save_raw_models(raw)
    await state.reload_config()
    return _public_model(state.config.model(model_id), state)


@app.post("/v1/models/{model_id}/start")
async def model_start(model_id: str, request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    model = state.config.model(model_id)
    if not model or not model.enabled:
        raise HTTPException(404, "未找到已启用的模型")
    try:
        await state.supervisor.ensure_running(model_id)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    if state.embedded:
        state.embedded.publish_runtime()
    return {"ok": True, "loaded": True}


@app.post("/v1/models/{model_id}/stop")
async def model_stop(model_id: str, request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    try:
        await state.supervisor.stop(model_id)
    except KeyError as exc:
        raise HTTPException(404, "未找到模型") from exc
    if state.embedded:
        state.embedded.publish_runtime()
    return {"ok": True, "loaded": False}


@app.post("/v1/models/{model_id}/restart")
async def model_restart(model_id: str, request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    try:
        await state.supervisor.restart(model_id)
    except KeyError as exc:
        raise HTTPException(404, "未找到模型") from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    if state.embedded:
        state.embedded.publish_runtime()
    return {"ok": True, "loaded": True}


@app.post("/v1/service/shutdown")
async def service_shutdown(request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)

    async def stop_later():
        await asyncio.sleep(0.25)
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(stop_later())
    return {"ok": True, "message": "VoiceGeneration 正在停止"}


# --- 集群(Cluster) -----------------------------------------------------------
def _require_cluster(state: App, authorization: str | None) -> None:
    token = state.config.settings.cluster.token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="集群 token 无效")


def _enrich_job(state: App, job: dict) -> dict:
    """为远程节点补上 ref_text / is_clone（参考音频另经 /asset 下载）。"""
    model = state.config.model(job["model"])
    try:
        _, ref_text, is_clone = state.resolve_ref(model, job["voice"]) if model else (None, None, False)
    except ValueError:
        ref_text, is_clone = None, False
    job["ref_text"] = ref_text
    job["is_clone"] = is_clone
    return job


@app.post("/v1/cluster/register")
async def cluster_register(body: ClusterRegister, request: Request,
                           authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_cluster(state, authorization)
    await asyncio.to_thread(
        cluster.register_node, node_id=body.node_id, name=body.name, role=body.role,
        models=body.models, max_concurrency=body.max_concurrency, version=body.version,
    )
    return {"ok": True}


@app.post("/v1/cluster/lease")
async def cluster_lease(body: ClusterLease, request: Request,
                        authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_cluster(state, authorization)
    cfg = state.config
    c = cfg.settings.cluster
    allowed = [
        m for m in body.models
        if (mc := cfg.model(m)) and mc.enabled and mc.allows(body.node_id)
    ]
    await asyncio.to_thread(cluster.touch_node, body.node_id)
    cluster.update_node_runtime(body.node_id, body.metrics)
    capacities: dict[str, int] = {}
    remaining = max(0, min(int(body.capacity), 64))
    if body.capacities:
        for model_id in allowed:
            requested = max(0, min(int(body.capacities.get(model_id, 0)), 64))
            capacities[model_id] = min(requested, remaining)
            remaining -= capacities[model_id]
    if body.capacity <= 0 or (body.capacities and not any(capacities.values())):
        return {"jobs": []}
    deadline = time.monotonic() + 25  # 长轮询
    while True:
        if body.capacities:
            jobs = await asyncio.to_thread(
                cluster.lease_jobs_by_model, body.node_id, capacities, c.lease_ttl
            )
        else:
            # Backward compatibility for older single-model agents.
            jobs = await asyncio.to_thread(
                cluster.lease_jobs, body.node_id, allowed, body.capacity, c.lease_ttl
            )
        if jobs or time.monotonic() > deadline:
            break
        await asyncio.sleep(0.5)
    return {"jobs": [_enrich_job(state, j) for j in jobs]}


@app.get("/v1/cluster/asset/{voice_id}")
async def cluster_asset(voice_id: str, request: Request,
                        authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_cluster(state, authorization)
    voice = state.config.clone_voice(voice_id)
    if not voice or not voice.ref_audio_path.is_file():
        raise HTTPException(404, "未找到参考音频")
    return FileResponse(voice.ref_audio_path, media_type="audio/wav", filename=f"{voice.id}.wav")


@app.post("/v1/cluster/jobs/{job_id}/result")
async def cluster_job_result(job_id: str, request: Request,
                             node_id: str = Form(...), elapsed: float = Form(0.0),
                             worker_id: str | None = Form(None),
                             inference_seconds: float | None = Form(None),
                             audio: UploadFile = File(...),
                             authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_cluster(state, authorization)
    wav = await audio.read()
    if not wav:
        raise HTTPException(400, "副节点上传的音频为空")
    try:
        await state.finalize_job(
            job_id, wav, elapsed, node_id,
            worker_id=worker_id, inference_seconds=inference_seconds,
        )
    except Exception as exc:
        logger.exception("处理副节点结果失败 job=%s node=%s", job_id, node_id)
        raise HTTPException(500, f"主节点处理音频结果失败：{exc}") from exc
    return {"ok": True}


@app.post("/v1/cluster/jobs/{job_id}/fail")
async def cluster_job_fail(job_id: str, body: JobFail, request: Request,
                           authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_cluster(state, authorization)
    result = await asyncio.to_thread(
        cluster.fail_or_requeue, job_id, body.error, state.config.settings.cluster.max_attempts
    )
    return {"status": result}


@app.post("/v1/cluster/jobs/{job_id}/heartbeat")
async def cluster_job_heartbeat(job_id: str, request: Request,
                                authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_cluster(state, authorization)
    ok = await asyncio.to_thread(cluster.extend_lease, job_id, state.config.settings.cluster.lease_ttl)
    return {"ok": ok}


@app.get("/v1/cluster/nodes")
async def cluster_nodes(request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    return {
        "self": {
            "node_id": state.config.settings.cluster.node_id,
            "node_name": state.config.settings.cluster.node_name,
            "role": state.config.settings.cluster.role,
            "coordinator_runs_jobs": state.config.settings.cluster.coordinator_runs_jobs,
        },
        "nodes": cluster.list_nodes(),
        "queue_depth": cluster.queue_depth(),
    }


@app.get("/v1/jobs/{job_id}")
async def job_get(job_id: str, request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    row = get_generation(job_id)
    if not row:
        raise HTTPException(404, "未找到任务")
    return history_dict(row, project_name_map().get(row.project_id),
                        cluster.node_name_map().get(row.assigned_node))


def _candidate_coordinator_urls(port: int) -> list[str]:
    """探测本机非回环 IPv4，拼成副节点可填的协调端地址（Tailscale 段优先）。"""
    ips: list[str] = []
    seen: set[str] = set()

    def add(ip: str) -> None:
        if ip and ip not in seen and ":" not in ip and not ip.startswith("127."):
            seen.add(ip)
            ips.append(ip)

    try:  # 主网卡出口 IP（UDP connect 不实际发包）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:  # 多网卡 / Tailscale
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            add(info[4][0])
    except Exception:
        pass

    def is_tailscale(ip: str) -> bool:
        parts = ip.split(".")
        return len(parts) == 4 and parts[0] == "100" and parts[1].isdigit() and 64 <= int(parts[1]) <= 127

    ips.sort(key=lambda ip: (not is_tailscale(ip), ip))
    return [f"http://{ip}:{port}" for ip in ips]


@app.get("/v1/cluster/connect-info")
async def cluster_connect_info(request: Request, authorization: str | None = Depends(check_auth)):
    """给「服务设置」展示：副节点该怎么连本协调端（地址候选 / 端口 / 令牌）。"""
    state = get_state(request)
    _require_token(state, authorization)
    s = state.config.settings
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = ""
    urls = _candidate_coordinator_urls(s.port)
    if hostname:
        mdns = hostname if hostname.endswith(".local") else f"{hostname}.local"
        urls.append(f"http://{mdns}:{s.port}")
    return {
        "host": s.host,
        "port": s.port,
        "reachable": s.host not in ("127.0.0.1", "localhost"),
        "token": s.cluster.token,
        "hostname": hostname,
        "candidate_urls": urls,
    }


# --- 项目(Project) CRUD ------------------------------------------------------
@app.get("/v1/projects")
async def projects_list(request: Request, authorization: str | None = Depends(check_auth)):
    state = get_state(request)
    _require_token(state, authorization)
    return list_projects()


@app.post("/v1/projects")
async def project_create(
    body: ProjectCreate, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    row = create_project(name=body.name, description=body.description, color=body.color)
    return {"id": row.id, "name": row.name, "description": row.description,
            "color": row.color, "generation_count": 0,
            "created_at": row.created_at.isoformat() + "Z"}


@app.put("/v1/projects/{project_id}")
async def project_update(
    project_id: str, body: ProjectUpdate, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    row = update_project(project_id, **body.model_dump(exclude_none=True))
    if not row:
        raise HTTPException(404, "未找到项目")
    return {"id": row.id, "name": row.name, "description": row.description,
            "color": row.color, "updated_at": row.updated_at.isoformat() + "Z"}


@app.delete("/v1/projects/{project_id}")
async def project_delete(
    project_id: str, request: Request,
    authorization: str | None = Depends(check_auth),
):
    state = get_state(request)
    _require_token(state, authorization)
    if not delete_project(project_id):
        raise HTTPException(404, "未找到项目")
    return {"ok": True}


# Serve the production Vite bundle without shadowing API and docs routes.
WEB_DIST = ROOT / "web" / "dist"
if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")


@app.get("/", include_in_schema=False)
async def web_index():
    index = WEB_DIST / "index.html"
    if not index.is_file():
        return HTMLResponse(
            "<h1>VoiceGeneration</h1><p>Web 前端尚未构建，请运行 npm run build。</p>",
            status_code=503,
        )
    return FileResponse(index)


@app.get("/{path:path}", include_in_schema=False)
async def web_fallback(path: str):
    candidate = (WEB_DIST / path).resolve()
    try:
        candidate.relative_to(WEB_DIST.resolve())
    except ValueError:
        raise HTTPException(404)
    if candidate.is_file():
        return FileResponse(candidate)
    index = WEB_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(404)
