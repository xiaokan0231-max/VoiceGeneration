#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

ROOT="$PWD"
APP="$ROOT/VoiceGeneration.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed "s|__PROJECT_ROOT__|$ROOT|g" packaging/VoiceGeneration-launcher > "$APP/Contents/MacOS/VoiceGeneration"
chmod +x "$APP/Contents/MacOS/VoiceGeneration"
cp packaging/Info.plist "$APP/Contents/Info.plist"

echo ">> 已生成 $APP"
