"""音频格式转换（用系统 ffmpeg；worker 统一返回 wav，这里按需转码）。"""
from __future__ import annotations

import subprocess

from .media_tools import media_binary

MEDIA_TYPES = {
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "opus": "audio/ogg",
}

_FFMPEG_ARGS = {
    "mp3": ["-f", "mp3", "-codec:a", "libmp3lame", "-q:a", "2"],
    "opus": ["-f", "ogg", "-codec:a", "libopus", "-b:a", "48k"],
}


def media_type(fmt: str) -> str:
    return MEDIA_TYPES.get(fmt, "application/octet-stream")


def convert(wav_bytes: bytes, fmt: str) -> bytes:
    """把 wav 字节转成目标格式；fmt == 'wav' 直接返回。"""
    if fmt == "wav":
        return wav_bytes
    if fmt not in _FFMPEG_ARGS:
        raise ValueError(f"不支持的格式: {fmt}")
    cmd = [media_binary("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
           *_FFMPEG_ARGS[fmt], "pipe:1"]
    proc = subprocess.run(cmd, input=wav_bytes, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 转码失败: {proc.stderr.decode(errors='ignore')}")
    return proc.stdout
