"""Style-Bert-VITS2（JP-Extra）后端 —— 日语高自然度 TTS（内置音色，非克隆）。

【启用步骤】
  1. bash scripts/setup_worker.sh style_bert_vits2     # 建 conda 环境 vg-sbv2 + pip install style-bert-vits2
  2. bash scripts/download_weights.sh style_bert_vits2 # 下载 JP DeBERTa + JVNV JP-Extra 模型到 models/style_bert_vits2
  3. 在 models.yaml 把 style_bert_vits2 的 enabled 改成 true
  4. 重启 gateway

实现说明：
  Style-Bert-VITS2 不是零样本克隆模型，而是用「训练好的模型」推理。每个“音色”对应
  model_dir 下的一个子目录，里面有 *.safetensors + config.json + style_vectors.npy。
  音色清单写在 models.yaml 的 options.voices 里（gateway 据此列出，无需唤醒 worker）。
  JP-Extra 版本仅支持日语；语速通过 length=1/speed 控制；infer() 直接返回 16-bit PCM。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# 个别 torch 算子在 MPS 上尚未实现时自动回退 CPU（必须在首次导入 torch 前设置）。
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

from worker_runtime.base import SynthRequest, TTSBackend, pcm_to_wav_bytes

# Style-Bert-VITS2 各语言默认使用的 BERT（与官方一致）。JP-Extra 只会用到 JP。
_BERT_NAMES = {
    "ja": "ku-nlp/deberta-v2-large-japanese-char-wwm",
    "en": "microsoft/deberta-v3-large",
    "zh": "hfl/chinese-roberta-wwm-ext-large",
}

# 把「风格指令」里的中/日/英情绪词映射到 JVNV 训练好的情感风格名。
_STYLE_KEYWORDS = {
    "Sad": ["sad", "sorrow", "悲", "哀", "难过", "伤感", "沉重", "かなし", "悲し", "つら"],
    "Angry": ["angry", "anger", "怒", "愤", "气愤", "おこ", "いか", "怒り"],
    "Happy": ["happy", "joy", "喜", "开心", "高兴", "快乐", "愉悦", "楽し", "うれし", "幸せ"],
    "Fear": ["fear", "scared", "恐", "害怕", "惧", "怖", "こわ", "不安"],
    "Disgust": ["disgust", "厌恶", "嫌", "反感", "嫌悪"],
    "Surprise": ["surprise", "惊", "驚", "意外", "びっくり", "驚き"],
    "Neutral": ["neutral", "中性", "平静", "平淡", "普通", "冷静"],
}


class StyleBertVITS2Backend(TTSBackend):
    def __init__(self, model_id, options):
        super().__init__(model_id, options)
        self._models: dict[str, object] = {}   # voice_id -> TTSModel（按需惰性加载并缓存）
        self._styles: dict[str, dict] = {}      # voice_id -> {风格名: id}（从 config.json 读）
        self._bert_loaded: set = set()          # 已加载 BERT 的语言枚举
        self._device: str | None = None
        self._voices = list(self.options.get("voices") or [])
        self._by_id = {v["id"]: v for v in self._voices}
        self._root = (Path(__file__).resolve().parents[2]
                      / self.options.get("model_dir", "models/style_bert_vits2"))

    def list_voices(self) -> list[dict]:
        return [{"id": v["id"], "name": v.get("name", v["id"]),
                 "language": v.get("language", "ja")} for v in self._voices]

    # ---- 惰性初始化 ----
    def _resolve_device(self) -> str:
        if self._device:
            return self._device
        import torch
        requested = str(self.options.get("device", "cpu")).lower()
        if requested == "auto":
            device = "mps" if torch.backends.mps.is_available() else "cpu"
        elif requested == "cuda":
            if not torch.cuda.is_available():
                raise RuntimeError("配置要求 CUDA，但未检测到 NVIDIA GPU")
            device = "cuda"
        elif requested == "mps":
            if not torch.backends.mps.is_available():
                raise RuntimeError("配置要求 MPS，但当前 PyTorch 检测不到 Apple GPU")
            device = "mps"
        else:
            device = "cpu"
        self._device = device
        return device

    def _lang_enum(self, code: str | None):
        from style_bert_vits2.constants import Languages
        return {"ja": Languages.JP, "en": Languages.EN, "zh": Languages.ZH}.get(
            (code or "ja").lower(), Languages.JP)

    def _ensure_bert(self, code: str | None) -> None:
        from style_bert_vits2.nlp import bert_models
        lang = self._lang_enum(code)
        if lang in self._bert_loaded:
            return
        name = _BERT_NAMES.get((code or "ja").lower(), _BERT_NAMES["ja"])
        model = bert_models.load_model(lang, name)
        bert_models.load_tokenizer(lang, name)
        # transformers 5.x 会按 checkpoint 的 fp16 加载 BERT，而 SBV2 的声学模型是 fp32，
        # 直接推理会报 "Input type (Half) and bias type (float)"。强制 BERT 用 fp32。
        # load_model 返回并缓存同一对象，原地 .float() 即对后续推理生效。
        try:
            model.float()
        except Exception:
            pass
        self._bert_loaded.add(lang)

    def _find(self, folder: Path, pattern: str) -> Path:
        # safetensors 文件名各模型不同（如 jvnv-F2_e166_s20000.safetensors），用通配匹配。
        matches = sorted(folder.glob(pattern))
        if not matches:
            raise FileNotFoundError(f"{folder} 下找不到 {pattern}，请先 download_weights.sh style_bert_vits2")
        return matches[0]

    def _get_model(self, voice: dict):
        vid = voice["id"]
        if vid in self._models:
            return self._models[vid]
        from style_bert_vits2.tts_model import TTSModel
        folder = (self._root / voice.get("dir", vid)).resolve()
        if not folder.is_dir():
            raise FileNotFoundError(f"音色目录不存在: {folder}")
        config_path = folder / "config.json"
        model = TTSModel(
            model_path=self._find(folder, "*.safetensors"),
            config_path=config_path,
            style_vec_path=folder / "style_vectors.npy",
            device=self._resolve_device(),
        )
        self._models[vid] = model
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
            self._styles[vid] = dict((cfg.get("data") or {}).get("style2id") or {})
        except Exception:
            self._styles[vid] = {}
        return model

    def _resolve_style(self, hint, styles: dict) -> tuple[str | None, float | None]:
        """把「风格指令」文字解析成 (风格名, 强度)。支持英文风格名 / 中日英情绪词 /
        末尾 ':8' 指定强度（越大情感越强）。无法识别则用 Neutral。"""
        names = {s.lower(): s for s in styles}
        default = "Neutral" if "Neutral" in styles else next(iter(styles), None)
        if not hint or not str(hint).strip():
            return default, None
        text, weight = str(hint).strip(), None
        match = re.match(r"^\s*(.+?)\s*[:：]\s*([0-9]+(?:\.[0-9]+)?)\s*$", text)
        if match:
            text, weight = match.group(1).strip(), float(match.group(2))
        low = text.lower()
        if low in names:
            return names[low], weight
        for canon, words in _STYLE_KEYWORDS.items():
            if canon in styles and any(word in low for word in words):
                return canon, weight
        return default, weight

    def synthesize(self, req: SynthRequest) -> bytes:
        if req.mode != "clone":
            raise ValueError("Style-Bert-VITS2 只支持基础生成模式（不支持指令/跨语言）")
        if not self._voices:
            raise ValueError("Style-Bert-VITS2 未配置任何内置音色（models.yaml 的 options.voices）")
        voice = self._by_id.get(req.voice) or self._voices[0]
        lang_code = voice.get("language") or req.language or "ja"
        self._ensure_bert(lang_code)
        model = self._get_model(voice)

        kwargs: dict = {
            "text": req.text,
            "language": self._lang_enum(lang_code),
            "length": 1.0 / max(0.1, req.speed or 1.0),   # length 越大语速越慢
        }
        # 情感风格：优先用本次「风格指令」(instruct_text)，其次音色默认 style。
        style, weight = self._resolve_style(
            req.instruct_text or voice.get("style"), self._styles.get(voice["id"], {}))
        if style:
            kwargs["style"] = style
        if weight is not None:
            kwargs["style_weight"] = weight
        if voice.get("speaker_id") is not None:
            kwargs["speaker_id"] = int(voice["speaker_id"])
        sr, audio = model.infer(**kwargs)
        return pcm_to_wav_bytes(audio, sr)
