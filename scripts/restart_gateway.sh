#!/usr/bin/env bash
# 重启网关（gateway / coordinator）：停掉现有 uvicorn，跑数据库迁移，再后台拉起一个新实例。
# 用途：改了 gateway/ 下的代码后让其生效（worker 不受影响，无需重启）。
# 用法：bash scripts/restart_gateway.sh
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"

# vg-gateway 环境的 python；可用环境变量 GATEWAY_PYTHON 覆盖。
PYBIN="${GATEWAY_PYTHON:-$HOME/miniconda3/envs/vg-gateway/bin/python}"
if [ ! -x "$PYBIN" ]; then
  echo "✗ 找不到 vg-gateway 的 python：$PYBIN（可设 GATEWAY_PYTHON 指向它）" >&2
  exit 1
fi

LOG_DIR="$ROOT/cache/_logs"
PID_FILE="$LOG_DIR/gateway.pid"
mkdir -p "$LOG_DIR"

# 从 models.yaml 读取 host/port（读不到则用默认）。
HOST="$("$PYBIN" -c "from gateway.config import load_config; print(load_config().settings.host)" 2>/dev/null || echo 0.0.0.0)"
PORT="$("$PYBIN" -c "from gateway.config import load_config; print(load_config().settings.port)" 2>/dev/null || echo 8080)"
HEALTH="http://127.0.0.1:${PORT}/v1/system"

echo ">> 停止现有网关 ..."
pkill -f "uvicorn gateway.main:app" 2>/dev/null || true
[ -f "$PID_FILE" ] && kill "$(cat "$PID_FILE")" 2>/dev/null || true
# 同时停掉模型 worker（独立进程，否则旧代码会继续服务）——下次请求由新 gateway 以最新代码重启。
pkill -f "worker_runtime.server" 2>/dev/null && echo "   已停止模型 worker（将按需以最新代码重启）" || true

# 等端口释放（最多 ~10s），必要时强杀。
for _ in $(seq 1 20); do
  lsof -ti "tcp:${PORT}" >/dev/null 2>&1 || break
  sleep 0.5
done
if lsof -ti "tcp:${PORT}" >/dev/null 2>&1; then
  echo "   端口 ${PORT} 仍被占用，强制结束 ..."
  lsof -ti "tcp:${PORT}" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

echo ">> 数据库迁移 (alembic upgrade head) ..."
"$PYBIN" -m alembic upgrade head >>"$LOG_DIR/migration.log" 2>&1 || echo "   (迁移跳过/失败，详见 migration.log)"

echo ">> 启动新网关 host=${HOST} port=${PORT} ..."
nohup "$PYBIN" -m uvicorn gateway.main:app --host "$HOST" --port "$PORT" \
  >>"$LOG_DIR/gateway.log" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" >"$PID_FILE"

# 健康检查（最多 ~60s）。
for _ in $(seq 1 120); do
  if curl -fsS "$HEALTH" >/dev/null 2>&1; then
    echo "✓ 网关已就绪 pid=${NEW_PID}  ${HEALTH%/v1/system}"
    exit 0
  fi
  if ! kill -0 "$NEW_PID" 2>/dev/null; then
    echo "✗ 网关进程已退出，详见 $LOG_DIR/gateway.log" >&2
    exit 1
  fi
  sleep 0.5
done
echo "✗ 健康检查超时（60s），详见 $LOG_DIR/gateway.log" >&2
exit 1
