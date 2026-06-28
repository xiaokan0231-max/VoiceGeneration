#!/usr/bin/env bash
# 把网关装成 macOS launchd 常驻服务（开机自启 + 崩溃自动拉起，独立于终端/Claude）。
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd -P)"
LABEL=local.voicegeneration.gateway
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

mkdir -p "$HOME/Library/LaunchAgents" cache/_logs
sed "s#__PROJECT_ROOT__#${ROOT}#g" packaging/${LABEL}.plist > "$PLIST"

launchctl unload "$PLIST" 2>/dev/null || true
# 清掉任何手动/会话启动的网关，避免占用 8080 让 launchd 实例起不来
pkill -f "uvicorn gateway.main:app" 2>/dev/null || true
sleep 1
launchctl load "$PLIST"

echo ">> 已安装常驻服务：$LABEL"
echo "   日志:   cache/_logs/gateway.out.log / gateway.err.log"
echo "   停止:   launchctl unload \"$PLIST\""
echo "   重启:   launchctl unload \"$PLIST\" && launchctl load \"$PLIST\""
echo "   卸载:   launchctl unload \"$PLIST\" && rm \"$PLIST\""
