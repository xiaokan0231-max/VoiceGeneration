#!/usr/bin/env python3
"""生成应用/托盘图标：暗色圆角方块 + 铜色麦克风/声波。

产物（写到 packaging/）：
  AppIcon.icns  (macOS .app；需系统 iconutil)
  AppIcon.ico   (Windows / PyInstaller)
  tray.png / tray_off.png  (托盘图标：在线=铜色 / 离线=灰)
跨平台：Pillow 绘制；.icns 仅在 macOS 上生成。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging"
COPPER = (217, 141, 82, 255)
GRAY = (140, 150, 156, 255)
BG = (17, 23, 28, 255)


def _draw_glyph(d: ImageDraw.ImageDraw, color, grille) -> None:
    """在 1024×1024 坐标系里画 麦克风 + 两侧声波。"""
    cx, cy = 512, 430
    # 声波（两侧各两道弧，画在麦克风后面）
    for r in (150, 205):
        d.arc([cx - r, cy - r, cx + r, cy + r], 118, 242, fill=color, width=20)
        d.arc([cx - r, cy - r, cx + r, cy + r], -62, 62, fill=color, width=20)
    # 麦克风胶囊
    d.rounded_rectangle([452, 300, 572, 560], radius=60, fill=color)
    # 网格（拾音孔）
    for y in (362, 412, 462):
        d.rounded_rectangle([474, y, 550, y + 12], radius=6, fill=grille)
    # 支架（U 形托）+ 立柱 + 底座
    d.arc([402, 360, 622, 600], 22, 158, fill=color, width=26)
    d.rounded_rectangle([499, 585, 525, 655], radius=13, fill=color)
    d.rounded_rectangle([452, 648, 572, 674], radius=13, fill=color)


def _render(px: int, with_bg: bool, color=COPPER, grille=BG) -> Image.Image:
    base = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    if with_bg:
        d.rounded_rectangle([88, 88, 936, 936], radius=205, fill=BG)
    _draw_glyph(d, color, grille if with_bg else (90, 55, 30, 255))
    return base.resize((px, px), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = _render(1024, with_bg=True)

    # Windows .ico
    base.save(OUT / "AppIcon.ico",
              sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

    # 托盘图标（透明背景，无圆角方块）
    _render(128, with_bg=False, color=COPPER).save(OUT / "tray.png")
    _render(128, with_bg=False, color=GRAY).save(OUT / "tray_off.png")

    # macOS .icns
    if sys.platform == "darwin":
        iconset = OUT / "AppIcon.iconset"
        iconset.mkdir(exist_ok=True)
        specs = [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
                 (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
                 (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x")]
        for size, name in specs:
            base.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{name}.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "AppIcon.icns")],
                       check=True)
        for f in iconset.iterdir():
            f.unlink()
        iconset.rmdir()
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
