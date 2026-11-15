"""Рисует assets/md2pdf.ico для переносимого .exe.

Запуск: python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
SIZE = 512


def load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = (
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/DejaVuSans-Bold.ttf",
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((10, 10, SIZE - 10, SIZE - 10), radius=112, fill=(18, 56, 92, 255))

    font = load_font(200)
    text = "MD"
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((SIZE - (right - left)) / 2 - left, 232 - (bottom - top) / 2 - top),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )

    for width, y in ((224, 336), (168, 392), (112, 448)):
        draw.rounded_rectangle(
            ((SIZE - width) / 2, y, (SIZE + width) / 2, y + 24),
            radius=12,
            fill=(255, 255, 255, 190),
        )

    image.save(ASSETS / "md2pdf-icon.png")
    image.save(
        ASSETS / "md2pdf.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print("готово:", ASSETS / "md2pdf.ico")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
