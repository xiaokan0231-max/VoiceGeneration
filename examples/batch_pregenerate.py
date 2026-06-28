"""批量预生成：遍历「歴史」种子数据里的旁白文本，提前合成并预热缓存。

这是一个模板脚本——把 collect_texts() 改成你真实的数据来源即可
（直接读 seed JSON，或连数据库查 stories/events/perspectives 等字段）。

用法:
    python examples/batch_pregenerate.py \
        --seed-dir /Users/kanxiao/IdeaProjects/歴史/backend/seed/data/events \
        --model cosyvoice3 --voice narrator_zh --lang zh --out ./pregen

行为:
    对每段文本调用网关 /v1/tts（网关自身也会缓存），并把音频另存到 --out 目录，
    文件名用内容哈希，便于「歴史」端按同样规则命中。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx


def collect_texts(seed_dir: Path, lang: str) -> list[str]:
    """从 events/*.json 里取出该语言的旁白文本。按你的实际结构调整。"""
    texts: list[str] = []
    suffix = "_zh" if lang == "zh" else "_ja"
    for jf in sorted(seed_dir.glob("*.json")):
        data = json.loads(jf.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else data.get("events", [])
        for ev in items:
            for field in ("summary", "description"):
                val = ev.get(field + suffix)
                if val:
                    texts.append(val.strip())
    # 去重，保持顺序
    seen, uniq = set(), []
    for t in texts:
        if t not in seen:
            seen.add(t); uniq.append(t)
    return uniq


def synth_one(base: str, payload: dict, out_dir: Path) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    key = hashlib.sha256(blob).hexdigest()
    out = out_dir / f"{key}.{payload['format']}"
    if out.exists():
        return f"skip {key[:8]}"
    r = httpx.post(f"{base}/v1/tts", json=payload, timeout=600)
    r.raise_for_status()
    out.write_bytes(r.content)
    return f"ok   {key[:8]} ({len(r.content)} bytes)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed-dir", required=True, type=Path)
    ap.add_argument("--model", default="system")
    ap.add_argument("--voice", default="narrator_zh")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--format", default="mp3")
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--out", default="./pregen", type=Path)
    ap.add_argument("--workers", type=int, default=2)  # 串行偏多，避免抢占模型
    args = ap.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    texts = collect_texts(args.seed_dir, args.lang)
    print(f">> 待合成 {len(texts)} 段")

    def task(t: str) -> str:
        payload = {"text": t, "model": args.model, "voice": args.voice,
                   "language": args.lang, "format": args.format}
        return synth_one(args.base, payload, args.out)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for i, msg in enumerate(ex.map(task, texts), 1):
            print(f"[{i}/{len(texts)}] {msg}")


if __name__ == "__main__":
    main()
