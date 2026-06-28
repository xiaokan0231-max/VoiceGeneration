#!/usr/bin/env bash
# 下载模型权重。
# 用法: bash scripts/download_weights.sh cosyvoice3
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${1:-}"
case "${MODEL}" in
  cosyvoice3)
    ENV=vg-cosyvoice
    mkdir -p models
    echo ">> 通过 ModelScope 下载 Fun-CosyVoice3-0.5B-2512"
    conda run -n "${ENV}" pip install -q modelscope
    conda run --no-capture-output -n "${ENV}" python -c \
      "from modelscope import snapshot_download; snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', local_dir='models/Fun-CosyVoice3-0.5B-2512', ignore_file_pattern=['llm.rl.pt', 'flow.decoder.estimator.*.onnx', 'speech_tokenizer_v3.batch.onnx']); print('done')"
    echo ">> CosyVoice3 权重下载完成。"
    ;;
  f5_tts)
    echo ">> F5-TTS 权重在首次合成时由 huggingface 自动下载，无需手动操作。"
    ;;
  *)
    echo "用法: bash scripts/download_weights.sh <cosyvoice3|f5_tts>"; exit 1;;
esac
