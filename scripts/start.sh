#!/usr/bin/env bash
# 启动网关。读取 models.yaml 里的 host/port。
set -euo pipefail
cd "$(dirname "$0")/.."

ENV=vg-gateway
HOST=$(conda run -n "${ENV}" python -c "from gateway.config import load_config; print(load_config().settings.host)")
PORT=$(conda run -n "${ENV}" python -c "from gateway.config import load_config; print(load_config().settings.port)")

mkdir -p cache/_logs
conda run -n "${ENV}" alembic upgrade head

echo ">> 网关启动于 http://${HOST}:${PORT}  (Ctrl-C 退出)"
exec conda run --no-capture-output -n "${ENV}" \
  uvicorn gateway.main:app --host "${HOST}" --port "${PORT}"
