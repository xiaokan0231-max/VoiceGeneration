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
  style_bert_vits2)
    ENV=vg-sbv2
    mkdir -p models/style_bert_vits2
    echo ">> 下载 JVNV JP-Extra 模型（litagin/style_bert_vits2_jvnv 的 *-jp 目录）"
    conda run --no-capture-output -n "${ENV}" python -c \
      "from huggingface_hub import snapshot_download; snapshot_download('litagin/style_bert_vits2_jvnv', allow_patterns=['jvnv-*-jp/*'], local_dir='models/style_bert_vits2'); print('jvnv done')"
    echo ">> 预下载 JP DeBERTa（ku-nlp/deberta-v2-large-japanese-char-wwm）到 HuggingFace 缓存"
    conda run --no-capture-output -n "${ENV}" python -c \
      "from style_bert_vits2.nlp import bert_models; from style_bert_vits2.constants import Languages; n='ku-nlp/deberta-v2-large-japanese-char-wwm'; bert_models.load_model(Languages.JP, n); bert_models.load_tokenizer(Languages.JP, n); print('bert done')"
    echo ">> Style-Bert-VITS2 权重下载完成。"
    ;;
  *)
    echo "用法: bash scripts/download_weights.sh <cosyvoice3|f5_tts|style_bert_vits2>"; exit 1;;
esac
