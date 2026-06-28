"""内容寻址的音频缓存 + LRU 淘汰（参考「歴史」项目 images.py 的磁盘缓存做法）。"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


class AudioCache:
    def __init__(self, root: Path, max_bytes: int):
        self.root = root
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(payload: dict) -> str:
        """对“决定输出的所有参数”做规范化哈希。"""
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    def path_for(self, key: str, ext: str) -> Path:
        # 两级目录，避免单目录文件过多
        return self.root / key[:2] / f"{key}.{ext}"

    def get(self, key: str, ext: str) -> Path | None:
        p = self.path_for(key, ext)
        if p.exists():
            os.utime(p, None)  # 刷新访问时间，供 LRU 使用
            return p
        return None

    def put(self, key: str, ext: str, data: bytes) -> Path:
        p = self.path_for(key, ext)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 原子写：先写临时文件再 rename
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self._evict_if_needed()
        return p

    def _evict_if_needed(self) -> None:
        files = [
            f for f in self.root.rglob("*")
            if f.is_file()
            and f.suffix in {".wav", ".mp3", ".opus", ".ogg"}
            and "_logs" not in f.parts
        ]
        total = sum(f.stat().st_size for f in files)
        if total <= self.max_bytes:
            return
        # 按访问时间从旧到新淘汰
        files.sort(key=lambda f: f.stat().st_atime)
        for f in files:
            if total <= self.max_bytes:
                break
            sz = f.stat().st_size
            try:
                f.unlink()
                total -= sz
            except OSError:
                pass
