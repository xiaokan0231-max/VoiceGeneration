#!/usr/bin/env bash
# 打包 VoiceGeneration.app —— 双击即起跨平台托盘程序(scripts/tray.py)。
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT="$PWD"
APP="$ROOT/VoiceGeneration.app"

# 1) 解析 vg-gateway 的 python（托盘要用它跑 pystray/PIL/gateway）
source "$HOME/miniconda3/etc/profile.d/conda.sh" 2>/dev/null || true
GATEWAY_PYTHON="$(conda run -n vg-gateway python -c 'import sys;print(sys.executable)' 2>/dev/null || true)"
[ -z "$GATEWAY_PYTHON" ] && GATEWAY_PYTHON="$HOME/miniconda3/envs/vg-gateway/bin/python"
if [ ! -x "$GATEWAY_PYTHON" ]; then
  echo "!! 找不到 vg-gateway 的 python，请先运行 scripts/setup_gateway.sh" >&2
  exit 1
fi

# 2) 生成图标（AppIcon.icns / tray*.png）
"$GATEWAY_PYTHON" scripts/make_icons.py

# 3) 组装 .app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed -e "s|__PROJECT_ROOT__|$ROOT|g" -e "s|__GATEWAY_PYTHON__|$GATEWAY_PYTHON|g" \
  packaging/VoiceGeneration-launcher > "$APP/Contents/MacOS/VoiceGeneration"
chmod +x "$APP/Contents/MacOS/VoiceGeneration"
cp packaging/Info.plist "$APP/Contents/Info.plist"
cp packaging/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
cp packaging/tray.png packaging/tray_off.png "$APP/Contents/Resources/"

# 4) 刷新 Finder 图标缓存（忽略失败）
touch "$APP"
echo ">> 已生成 $APP  (python=$GATEWAY_PYTHON)"
