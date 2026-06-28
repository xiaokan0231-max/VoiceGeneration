"""所有模型 worker 共用的接口与音频工具。

新增一个模型，只需在 workers/<name>/backend.py 里继承 TTSBackend 实现 synthesize()。
"""
from __future__ import annotations

import io
import wave
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class SynthRequest:
    text: str
    voice: str | None = None
    language: str | None = None
    speed: float = 1.0
    mode: str = "clone"
    instruct_text: str | None = None
    ref_audio_path: str | None = None   # 克隆参考音频（绝对路径）
    ref_text: str | None = None         # 参考音频对应文字


class TTSBackend(ABC):
    """一个模型后端。约定：synthesize() 返回 16-bit PCM 的 WAV 字节。"""

    def __init__(self, model_id: str, options: dict[str, Any]):
        self.model_id = model_id
        self.options = options or {}

    def list_voices(self) -> list[dict]:
        """模型内置音色列表，元素形如 {id,name,language}。无内置音色返回 []。"""
        return []

    @abstractmethod
    def synthesize(self, req: SynthRequest) -> bytes:
        """返回 WAV 字节。模型权重应在首次调用时惰性加载。"""
        raise NotImplementedError


def pcm_to_wav_bytes(samples, sample_rate: int) -> bytes:
    """把 [-1,1] 的 float 一维数组（或 int16 数组）编码成 WAV 字节。"""
    import numpy as np

    arr = np.asarray(samples)
    if arr.ndim > 1:
        arr = arr.reshape(-1)
    if arr.dtype != np.int16:
        arr = np.clip(arr, -1.0, 1.0)
        arr = (arr * 32767.0).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(arr.tobytes())
    return buf.getvalue()
