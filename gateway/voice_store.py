"""Safe CRUD and audio normalization for clone voices."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

import yaml

from .config import ROOT, Voice


VOICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _voices_path() -> Path:
    return ROOT / "voices.yaml"


def _load_raw() -> dict[str, Any]:
    return yaml.safe_load(_voices_path().read_text(encoding="utf-8")) or {"voices": []}


def _save_raw(raw: dict[str, Any]) -> None:
    path = _voices_path()
    backup = ROOT / "voices.yaml.bak"
    backup.write_bytes(path.read_bytes())
    fd, tmp = tempfile.mkstemp(dir=str(ROOT), prefix="voices-", suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(proc.stdout.strip())


def _normalize_audio(data: bytes, destination: Path) -> float:
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("参考音频必须小于 20MB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "upload"
        source.write_bytes(data)
        # 只修剪【开头】的静音/死区（麦克风预热、点录后停顿），保留至多 0.05s 自然留白。
        # 注意：不剪结尾——F5-TTS 需要参考音频末尾留有静音，否则结尾易被截断。
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
             "-af", "silenceremove=start_periods=1:start_silence=0.05:start_threshold=-50dB:detection=peak",
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(destination)],
            capture_output=True,
        )
        if proc.returncode != 0:
            raise ValueError("无法读取音频，请上传 WAV、MP3、M4A 或 WebM 文件")
    seconds = _duration(destination)
    if not 3 <= seconds <= 30:
        destination.unlink(missing_ok=True)
        raise ValueError("参考音频时长需在 3–30 秒之间")
    return seconds


def create_voice(
    *, name: str, language: str, ref_text: str, models: list[str],
    audio: bytes, voice_id: str | None = None,
) -> Voice:
    voice_id = voice_id or f"voice_{uuid.uuid4().hex[:8]}"
    if not VOICE_ID_RE.fullmatch(voice_id):
        raise ValueError("音色 ID 只能包含字母、数字、下划线和短横线")
    if not name.strip() or not language.strip() or not ref_text.strip():
        raise ValueError("名称、语言和参考文字不能为空")
    raw = _load_raw()
    if any(v.get("id") == voice_id for v in raw.get("voices", [])):
        raise ValueError(f"音色 ID 已存在: {voice_id}")
    relative = Path("voices") / voice_id / "ref.wav"
    destination = ROOT / relative
    _normalize_audio(audio, destination)
    item = {
        "id": voice_id,
        "name": name.strip(),
        "language": language.strip(),
        "ref_audio": str(relative),
        "ref_text": ref_text.strip(),
        "models": models,
    }
    raw.setdefault("voices", []).append(item)
    _save_raw(raw)
    return Voice(**item)


def update_voice(
    voice_id: str, *, name: str, language: str, ref_text: str,
    models: list[str], audio: bytes | None = None,
) -> Voice:
    raw = _load_raw()
    item = next((v for v in raw.get("voices", []) if v.get("id") == voice_id), None)
    if not item:
        raise KeyError(voice_id)
    if not name.strip() or not language.strip() or not ref_text.strip():
        raise ValueError("名称、语言和参考文字不能为空")
    if audio is not None:
        _normalize_audio(audio, ROOT / item["ref_audio"])
    item.update(name=name.strip(), language=language.strip(), ref_text=ref_text.strip(), models=models)
    _save_raw(raw)
    return Voice(**item)


def delete_voice(voice_id: str) -> bool:
    raw = _load_raw()
    items = raw.get("voices", [])
    item = next((v for v in items if v.get("id") == voice_id), None)
    if not item:
        return False
    raw["voices"] = [v for v in items if v.get("id") != voice_id]
    _save_raw(raw)
    folder = (ROOT / item["ref_audio"]).resolve().parent
    if folder.parent == (ROOT / "voices").resolve():
        shutil.rmtree(folder, ignore_errors=True)
    return True

