#!/usr/bin/env bash
# 为某个模型创建独立 conda 环境并安装依赖。
# 用法: bash scripts/setup_worker.sh cosyvoice3 | f5_tts
set -euo pipefail
cd "$(dirname "$0")/.."

MODEL="${1:-}"
if [[ -z "${MODEL}" ]]; then
  echo "用法: bash scripts/setup_worker.sh <cosyvoice3|f5_tts>"; exit 1
fi

case "${MODEL}" in
  cosyvoice3)
    ENV=vg-cosyvoice; PY=3.10
    if ! conda env list | grep -qE "^${ENV}\s"; then
      conda create -y -n "${ENV}" "python=${PY}"
    fi
    mkdir -p third_party
    if [[ ! -d third_party/CosyVoice ]]; then
      echo ">> 克隆 CosyVoice 官方仓库"
      git clone --recursive https://github.com/FunAudioLLM/CosyVoice.git third_party/CosyVoice
    else
      (cd third_party/CosyVoice && git submodule update --init --recursive)
    fi
    # --- macOS(arm64) 适配（已实测可用）---------------------------------------
    # 1) 去掉官方 requirements 里的 CUDA index（--extra-index-url），让 torch /
    #    torchaudio 装成 arm64 CPU 轮子；linux-only 的 deepspeed/onnxruntime-gpu/
    #    tensorrt 已被上游用 sys_platform=='linux' 网关掉，darwin 上自动跳过。
    # 2) openai-whisper==20231117 的旧 setup.py 在 build 时 import pkg_resources，
    #    需先把 setuptools 降到 <81，并用 --no-build-isolation 单独安装。
    # 3) 本版本用 wetext 做文本前端（首次合成时从 modelscope 下载），无需 pynini。
    echo ">> 安装 CosyVoice 依赖（已做 macOS 适配）"
    conda run -n "${ENV}" pip install wheel
    grep -v -- '--extra-index-url' third_party/CosyVoice/requirements.txt \
      | grep -v 'openai-whisper' > /tmp/cosy_req_darwin.txt
    conda run -n "${ENV}" pip install -r /tmp/cosy_req_darwin.txt
    conda run -n "${ENV}" pip install "setuptools<81"
    conda run -n "${ENV}" pip install --no-build-isolation "openai-whisper==20231117"
    conda run -n "${ENV}" pip install -r workers/cosyvoice3/requirements.txt
    echo ">> 下一步: bash scripts/download_weights.sh cosyvoice3"
    ;;

  f5_tts)
    ENV=vg-f5; PY=3.10
    if ! conda env list | grep -qE "^${ENV}\s"; then
      conda create -y -n "${ENV}" "python=${PY}"
    fi
    echo ">> 安装 F5-TTS"
    conda run -n "${ENV}" pip install -r workers/f5_tts/requirements.txt
    echo ">> 权重将在首次合成时自动下载"
    ;;

  *)
    echo "未知模型: ${MODEL}"; exit 1;;
esac

echo ">> 完成。记得在 models.yaml 把 ${MODEL} 的 enabled 改为 true，然后重启网关。"
