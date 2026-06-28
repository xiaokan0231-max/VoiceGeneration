"""Locate ffmpeg/ffprobe reliably, including macOS apps with a minimal PATH."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


_COMMON_DIRS = (
    Path("/opt/homebrew/bin"),   # Apple Silicon Homebrew
    Path("/usr/local/bin"),     # Intel Homebrew / manual installs
    Path("/usr/bin"),
    Path("/bin"),
)


def media_binary(name: str) -> str:
    """Return an executable path or raise a user-facing configuration error."""
    override = os.environ.get(f"VG_{name.upper()}", "").strip()
    candidates = [override, shutil.which(name)]
    candidates.extend(str(directory / name) for directory in _COMMON_DIRS)
    for candidate in candidates:
        if candidate and Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return str(Path(candidate).resolve())
    raise RuntimeError(
        f"找不到 {name}。请用 Homebrew 安装 ffmpeg，或设置 VG_{name.upper()} 为可执行文件路径"
    )
