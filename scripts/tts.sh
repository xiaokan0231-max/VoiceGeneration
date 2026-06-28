#!/usr/bin/env bash
# 快速合成一段语音并保存。
# 用法: bash scripts/tts.sh "你好，世界" [model] [voice] [out.mp3]
set -euo pipefail
cd "$(dirname "$0")/.."

TEXT="${1:?需要文字}"
MODEL="${2:-cosyvoice3}"
VOICE="${3:-narrator_zh}"
OUT="${4:-out.wav}"
EXT="${OUT##*.}"
BASE="http://127.0.0.1:8080"

curl -s -X POST "${BASE}/v1/tts" \
  -H 'content-type: application/json' \
  -D - \
  -d "$(printf '{"text":%s,"model":"%s","voice":"%s","format":"%s"}' \
        "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$TEXT")" \
        "$MODEL" "$VOICE" "$EXT")" \
  -o "$OUT"

echo ">> 已写出 ${OUT}"
