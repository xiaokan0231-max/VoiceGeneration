"""F5-TTS 后端（Flow-matching 零样本声音克隆）。

【启用步骤】
  1. bash scripts/setup_worker.sh f5_tts      # 建 conda 环境 vg-f5 + pip install f5-tts
  2. 首次合成会自动从 HuggingFace 拉取权重（也可提前 download_weights.sh f5_tts）
  3. 在 models.yaml 把 f5_tts 的 enabled 改成 true
  4. 重启 gateway

实现说明：
  使用 f5_tts 官方 Python API（F5TTS.infer）。M 系列芯片自动用 MPS。
  F5-TTS 是纯克隆模型，必须提供参考音频 + 参考文字。
"""
from __future__ import annotations

from worker_runtime.base import SynthRequest, TTSBackend, pcm_to_wav_bytes


class F5TTSBackend(TTSBackend):
    def __init__(self, model_id, options):
        super().__init__(model_id, options)
        self._api = None
        self._sr = 24000

    def _ensure_loaded(self):
        if self._api is not None:
            return
        import torch
        from f5_tts.api import F5TTS

        requested = str(self.options.get("device", "auto")).lower()
        if requested == "auto":
            device = ("cuda" if torch.cuda.is_available()
                      else "mps" if torch.backends.mps.is_available() else "cpu")
        else:
            device = requested
        model_name = self.options.get("model", "F5TTS_v1_Base")
        self._api = F5TTS(model=model_name, device=device)
        self._sr = getattr(self._api, "target_sample_rate", 24000)

    def synthesize(self, req: SynthRequest) -> bytes:
        if req.mode != "clone":
            raise ValueError("F5-TTS 当前只支持声音克隆模式")
        if not req.ref_audio_path or not req.ref_text:
            raise ValueError("F5-TTS 需要参考音频(ref_audio)和参考文字(ref_text)进行克隆")
        self._ensure_loaded()
        wav, sr, _ = self._api.infer(
            ref_file=req.ref_audio_path,
            ref_text=req.ref_text,
            gen_text=req.text,
            speed=req.speed,
            remove_silence=True,
        )
        return pcm_to_wav_bytes(wav, sr or self._sr)
