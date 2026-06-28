"""开箱即用的本地后端：调用 macOS 的 `say` 命令。

用途：在不下载任何大模型权重的情况下，端到端验证 gateway / 缓存 / 接入链路。
不支持声音克隆。生产用请改用 cosyvoice3 / f5_tts。
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from worker_runtime.base import SynthRequest, TTSBackend

# 内置音色（macOS 系统自带语音；可用 `say -v '?'` 查看更多）
VOICES = [
    {"id": "Tingting", "name": "婷婷（普通话）", "language": "zh"},
    {"id": "Kyoko", "name": "Kyoko（日本語）", "language": "ja"},
    {"id": "Samantha", "name": "Samantha (English)", "language": "en"},
]
_BY_LANG = {v["language"]: v["id"] for v in VOICES}
_IDS = {v["id"] for v in VOICES}
_BASE_WPM = 175


class SystemBackend(TTSBackend):
    def list_voices(self) -> list[dict]:
        return VOICES

    def _pick_voice(self, req: SynthRequest) -> str:
        if req.voice in _IDS:
            return req.voice
        if req.language and req.language in _BY_LANG:
            return _BY_LANG[req.language]
        return "Samantha"

    def synthesize(self, req: SynthRequest) -> bytes:
        if req.mode != "clone":
            raise ValueError("macOS system 引擎只支持基础生成模式")
        voice = self._pick_voice(req)
        rate = max(80, int(_BASE_WPM * req.speed))
        with tempfile.TemporaryDirectory() as d:
            aiff = Path(d) / "out.aiff"
            subprocess.run(
                ["say", "-v", voice, "-r", str(rate), "-o", str(aiff), req.text],
                check=True, capture_output=True,
            )
            # 转成 16k 单声道 wav 字节
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(aiff), "-ar", "16000", "-ac", "1", "-f", "wav", "pipe:1"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode(errors="ignore"))
            return proc.stdout
