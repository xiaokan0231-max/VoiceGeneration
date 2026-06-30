#!/usr/bin/env python3
"""Generate application and tray icons from packaging/AppIcon-source.png.

The source is the approved square artwork. The application icon keeps its
rounded dark tile, while tray icons isolate the bright central waveform so it
remains legible at Windows notification-area sizes.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "packaging"
SOURCE = OUT / "AppIcon-source.png"

# Public constants retained for scripts/tray.py's emergency fallback call.
COPPER = (80, 210, 255, 255)
GRAY = (145, 151, 166, 255)
BG = (3, 7, 26, 255)


def _source_square() -> Image.Image:
    if not SOURCE.is_file():
        raise FileNotFoundError(f"Missing approved icon source: {SOURCE}")
    source = Image.open(SOURCE).convert("RGB")
    side = min(source.size)
    left = (source.width - side) // 2
    top = (source.height - side) // 2
    return source.crop((left, top, left + side, top + side))


def _app_icon(size: int = 1024) -> Image.Image:
    source = _source_square()
    # Crop the generated black presentation margin while retaining the full tile.
    inset = round(source.width * .083)
    source = source.crop((inset, inset, source.width - inset, source.height - inset))
    source = source.resize((size, size), Image.Resampling.LANCZOS).convert("RGBA")

    # Replace the black presentation corners with real transparency.
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (size * .026, size * .018, size * .974, size * .982),
        radius=size * .225,
        fill=255,
    )
    # Preserve the artwork's antialiased edge instead of introducing a hard cut.
    mask = mask.filter(ImageFilter.GaussianBlur(max(1, size * .002)))
    source.putalpha(mask)
    return source


def _neon_mark(size: int = 128) -> Image.Image:
    artwork = _app_icon(1024)
    rgb = artwork.convert("RGB")
    channels = rgb.split()
    brightest = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])

    # Dark navy is the tile; cyan/violet luminance becomes transparency.
    alpha = brightest.point(lambda value: max(0, min(255, (value - 17) * 7)))
    radial = Image.new("L", artwork.size, 0)
    ImageDraw.Draw(radial).ellipse((96, 96, 928, 928), fill=255)
    radial = radial.filter(ImageFilter.GaussianBlur(5))
    alpha = ImageChops.multiply(alpha, radial)

    mark = artwork.copy()
    mark.putalpha(alpha)
    bbox = alpha.point(lambda value: 255 if value > 8 else 0).getbbox()
    if not bbox:
        raise ValueError("Unable to isolate the neon waveform from AppIcon-source.png")
    mark = mark.crop(bbox)
    mark.thumbnail((round(size * .91), round(size * .91)), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
    return canvas


def _tray_icon(size: int = 128, offline: bool = False) -> Image.Image:
    icon = _neon_mark(size)
    if not offline:
        return icon
    alpha = icon.getchannel("A")
    gray = ImageOps.grayscale(icon.convert("RGB"))
    gray = ImageEnhance.Contrast(gray).enhance(.82)
    gray = ImageOps.colorize(gray, black=(67, 72, 86), white=(190, 196, 210)).convert("RGBA")
    gray.putalpha(alpha)
    return gray


def _render(px: int, with_bg: bool, color=COPPER, grille=BG) -> Image.Image:
    """Compatibility renderer used only if exported tray PNGs are missing."""
    del color, grille
    image = _app_icon(1024) if with_bg else _tray_icon(128)
    return image.resize((px, px), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = _app_icon(1024)
    base.save(OUT / "AppIcon.png")
    base.save(
        OUT / "AppIcon.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    _tray_icon(128).save(OUT / "tray.png")
    _tray_icon(128, offline=True).save(OUT / "tray_off.png")

    if sys.platform == "darwin":
        iconset = OUT / "AppIcon.iconset"
        iconset.mkdir(exist_ok=True)
        specs = [
            (16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
            (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
            (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x"),
        ]
        for icon_size, name in specs:
            base.resize((icon_size, icon_size), Image.Resampling.LANCZOS).save(
                iconset / f"icon_{name}.png"
            )
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(OUT / "AppIcon.icns")],
            check=True,
        )
        for item in iconset.iterdir():
            item.unlink()
        iconset.rmdir()
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
