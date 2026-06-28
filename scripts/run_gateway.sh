#!/usr/bin/env bash
# 供 launchd 常驻运行网关：自带 conda 初始化，读 models.yaml 的 host/port。
# 与会话/终端无关；崩溃由 launchd 自动拉起。
set -uo pipefail
cd "$(dirname "$0")/.."
source "$HOME/miniconda3/etc/profile.d/conda.sh"
mkdir -p cache/_logs

HOST=$(conda run -n vg-gateway python -c "from gateway.config import load_config; print(load_config().settings.host)" 2>/dev/null)
PORT=$(conda run -n vg-gateway python -c "from gateway.config import load_config; print(load_config().settings.port)" 2>/dev/null)
conda run -n vg-gateway alembic upgrade head >>cache/_logs/migration.log 2>&1 || true

exec conda run --no-capture-output -n vg-gateway \
  uvicorn gateway.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8080}"
