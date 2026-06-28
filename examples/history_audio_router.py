"""可直接放进「歴史」后端的音频路由（仿照其 images.py 的缓存代理写法）。

放置位置建议: 歴史/backend/app/routers/audio.py
注册方式 (歴史/backend/app/main.py):
    from app.routers import audio
    app.include_router(audio.router)

前端调用:
    POST /api/audio   body: {"text": "...", "lang": "zh", "voice": "narrator_zh"}
    -> 返回 audio/mpeg；命中缓存的话直接读磁盘。

环境变量:
    VG_BASE_URL   默认 http://127.0.0.1:8080  （VoiceGeneration 网关地址）
    VG_MODEL      默认 system（联调）；上线换成 cosyvoice3 或 f5_tts
    VG_API_TOKEN  若网关开了鉴权则需设置
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["audio"])

VG_BASE_URL = os.environ.get("VG_BASE_URL", "http://127.0.0.1:8080")
VG_MODEL = os.environ.get("VG_MODEL", "system")
VG_API_TOKEN = os.environ.get("VG_API_TOKEN", "")

CACHE_DIR = Path(os.environ.get("AUDIO_CACHE_DIR", ".audiocache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class AudioRequest(BaseModel):
    text: str
    lang: str | None = None
    voice: str = "narrator_zh"
    model: str | None = None
    format: str = "mp3"


def _cache_path(payload: dict) -> Path:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    key = hashlib.sha256(blob).hexdigest()
    return CACHE_DIR / key[:2] / f"{key}.{payload['format']}"


@router.post("/audio")
async def audio(req: AudioRequest):
    model = req.model or VG_MODEL
    payload = {"text": req.text, "model": model, "voice": req.voice,
               "language": req.lang, "format": req.format}
    cache_file = _cache_path(payload)
    media = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg"}.get(
        req.format, "application/octet-stream")

    if cache_file.exists():
        return Response(cache_file.read_bytes(), media_type=media,
                        headers={"X-Cache": "HIT"})

    headers = {"Authorization": f"Bearer {VG_API_TOKEN}"} if VG_API_TOKEN else {}
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            r = await client.post(f"{VG_BASE_URL}/v1/tts", json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"语音服务不可用: {e}")
    if r.status_code != 200:
        raise HTTPException(502, f"语音合成失败: {r.text}")

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(cache_file.suffix + ".tmp")
    tmp.write_bytes(r.content)
    tmp.replace(cache_file)
    return Response(r.content, media_type=media, headers={"X-Cache": "MISS"})
