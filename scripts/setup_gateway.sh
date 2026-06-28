#!/usr/bin/env bash
# 创建网关 conda 环境并安装轻量依赖。
set -euo pipefail
cd "$(dirname "$0")/.."

ENV=vg-gateway
PY=3.11

if ! conda env list | grep -qE "^${ENV}\s"; then
  echo ">> 创建 conda 环境 ${ENV} (python ${PY})"
  conda create -y -n "${ENV}" "python=${PY}"
fi

echo ">> 准备 models.yaml（不入 git；首次从模板复制后按本机修改）"
[ -f models.yaml ] || cp models.example.yaml models.yaml

echo ">> 安装网关依赖"
conda run -n "${ENV}" pip install -r requirements-gateway.txt

echo ">> 构建 Web 工作台"
(cd web && npm install && npm run build)

echo ">> 初始化 MySQL 历史数据库"
conda run -n "${ENV}" alembic upgrade head

echo ">> 构建 macOS 双击启动应用"
bash scripts/build_macos_app.sh

echo ">> 完成。双击 VoiceGeneration.app，或运行 bash scripts/start.sh"
