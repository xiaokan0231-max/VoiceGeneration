"""CosyVoice 3 后端（零样本多语种声音克隆）。

【启用步骤】
  1. bash scripts/setup_worker.sh cosyvoice3      # 建 conda 环境 vg-cosyvoice + 装依赖
  2. bash scripts/download_weights.sh cosyvoice3  # 下载权重到 models/
  3. 在 models.yaml 把 cosyvoice3 的 enabled 改成 true，确认 options.repo_dir / model_dir
  4. 重启 gateway

实现说明：
  CosyVoice 的推理类来自其官方仓库（git clone）。这里在首次合成时惰性加载，
  通过 inference_zero_shot(text, ref_text, ref_audio_path) 完成克隆合成。
  （当前版本 prompt 传文件路径，由其 frontend 内部 load_wav 自行重采样到 16k/24k。）
  使用官方 AutoModel 根据权重目录自动识别模型类型；当前配置固定指向
  Fun-CosyVoice3-0.5B-2512。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from worker_runtime.base import SynthRequest, TTSBackend, pcm_to_wav_bytes


class CosyVoice3Backend(TTSBackend):
    def __init__(self, model_id, options):
        super().__init__(model_id, options)
        self._model = None
        self._sr = 24000
        self._device = "cpu"

    def _ensure_loaded(self):
        if self._model is not None:
            return
        repo_dir = str(Path(self.options["repo_dir"]).resolve())
        # 把官方仓库及其第三方依赖目录加入 import 路径
        for p in (repo_dir, str(Path(repo_dir) / "third_party" / "Matcha-TTS")):
            if p not in sys.path:
                sys.path.insert(0, p)

        # 让 PyTorch 在个别 MPS 尚未实现的算子上自动回退到 CPU。该变量必须在
        # 首次导入 torch 前设置。
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        import torch
        from cosyvoice.cli.cosyvoice import AutoModel

        model_dir = str(Path(self.options["model_dir"]).resolve())
        # 官方加载器会根据 cosyvoice3.yaml 选择 CosyVoice3。上游目前只自动识别
        # CUDA/CPU，因此先按其默认方式加载，再显式迁移到 Apple MPS。
        self._model = AutoModel(
            model_dir=model_dir,
            load_trt=False,
            load_vllm=False,
            fp16=False,
        )
        requested = str(self.options.get("device", "auto")).lower()
        if requested == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("配置要求 CUDA，但未检测到 NVIDIA GPU")
        use_cuda = requested in {"auto", "cuda"} and torch.cuda.is_available()
        use_mps = (not use_cuda) and requested in {"auto", "mps"} and torch.backends.mps.is_available()
        if requested == "mps" and not use_mps:
            raise RuntimeError("配置要求使用 MPS，但当前 PyTorch 检测不到 Apple GPU")
        if use_cuda:
            self._device = "cuda"  # 官方 AutoModel 已默认把模型加载到 CUDA
        if use_mps:
            device = torch.device("mps")
            runtime = self._model.model
            runtime.llm.to(device).eval()
            runtime.flow.to(device).eval()
            # HiFT 的 F0 predictor 强制使用 float64，而 MPS 不支持 float64。
            # 让计算量最大的 LLM/Flow 使用 GPU，HiFT 声码器保留在 CPU，并在
            # 两者边界自动搬运 mel 张量。
            runtime.hift.to("cpu").eval()
            original_hift_inference = runtime.hift.inference

            def cpu_hift_inference(*args, **kwargs):
                if "speech_feat" in kwargs:
                    kwargs["speech_feat"] = kwargs["speech_feat"].to("cpu")
                elif args:
                    args = (args[0].to("cpu"), *args[1:])
                return original_hift_inference(*args, **kwargs)

            runtime.hift.inference = cpu_hift_inference
            runtime.device = device
            self._model.frontend.device = device
            self._device = "mps"
        self._sr = getattr(self._model, "sample_rate", 24000)

    def synthesize(self, req: SynthRequest) -> bytes:
        if not req.ref_audio_path:
            raise ValueError("CosyVoice 需要参考音频(ref_audio)")
        self._ensure_loaded()

        # 注意：当前 CosyVoice 版本的 frontend_zero_shot 内部会自行调用
        # load_wav(prompt_wav, ...) 读取并按需重采样（16k 用于 speech token，
        # 24k 用于 speech feat），因此 prompt_wav 必须传【文件路径】（或类文件对象），
        # 不能传预先加载好的张量，否则 torchaudio.load 会抛 TypeError。
        prompt_wav = str(Path(req.ref_audio_path).resolve())
        model_name = type(self._model).__name__
        mode = req.mode or "clone"

        if mode == "clone":
            if not req.ref_text:
                raise ValueError("声音克隆模式需要参考文字(ref_text)")
            prompt_text = req.ref_text
            if model_name == "CosyVoice3" and "<|endofprompt|>" not in prompt_text:
                prompt_text = f"You are a helpful assistant.<|endofprompt|>{prompt_text}"
            iterator = self._model.inference_zero_shot(
                req.text, prompt_text, prompt_wav, stream=False, speed=req.speed
            )
        elif mode == "instruct":
            if not req.instruct_text:
                raise ValueError("指令控制模式需要风格指令(instruct_text)")
            instruct = req.instruct_text
            if model_name == "CosyVoice3" and "<|endofprompt|>" not in instruct:
                instruct = f"You are a helpful assistant. {instruct}<|endofprompt|>"
            iterator = self._model.inference_instruct2(
                req.text, instruct, prompt_wav, stream=False, speed=req.speed
            )
        elif mode == "cross_lingual":
            text = req.text
            if model_name == "CosyVoice3" and "<|endofprompt|>" not in text:
                text = f"You are a helpful assistant.<|endofprompt|>{text}"
            iterator = self._model.inference_cross_lingual(
                text, prompt_wav, stream=False, speed=req.speed
            )
        else:
            raise ValueError(f"不支持的生成模式: {mode}")

        chunks = []
        for out in iterator:
            chunks.append(out["tts_speech"])
        import torch
        wav = torch.cat(chunks, dim=1).squeeze(0).cpu().numpy()
        if not torch.isfinite(torch.from_numpy(wav)).all():
            raise RuntimeError(
                f"{type(self._model).__name__} 在 {self._device} 上生成了 NaN/Inf，已拒绝写出静音文件"
            )
        return pcm_to_wav_bytes(wav, self._sr)
