#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

AUTHOR_TEXT = "Creado por José María Parrón Pinto (elrincondeteexplicoTube)"


def infer_source_label(output_path) -> str:
    text = str(output_path).lower().replace("\\", "/")
    if "icon-eu" in text or "public-icon" in text:
        return "DWD Open Data"
    if "/gfs/" in text or "gfs" in Path(text).name:
        return "NOAA/NCEP NOMADS"
    if "/ecmwf/" in text or "ecmwf" in Path(text).name:
        return "ECMWF Open Data"
    return "fuente meteorológica oficial indicada en el manifiesto"


def credit_text(output_path, source_label: str | None = None) -> str:
    source = source_label or infer_source_label(output_path)
    return f"{AUTHOR_TEXT} - Datos oficiales: {source}"


def _font_for_width(width: int, text: str):
    size = max(11, min(28, width // 105))
    while size >= 10:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", size=size)
        except OSError:
            font = ImageFont.load_default()
            return font
        probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        draw = ImageDraw.Draw(probe)
        box = draw.textbbox((0, 0), text, font=font)
        if (box[2] - box[0]) <= width * 0.92:
            return font
        size -= 1
    return ImageFont.load_default()


def brand_image(image: Image.Image, output_path, source_label: str | None = None) -> Image.Image:
    """Añade la firma visual sin alterar los datos meteorológicos que originaron el mapa."""
    img = image if image.mode == "RGBA" else image.convert("RGBA")
    text = credit_text(output_path, source_label)
    width, height = img.size
    font = _font_for_width(width, text)
    draw = ImageDraw.Draw(img, "RGBA")
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    margin = max(8, width // 280)
    pad_x = max(7, width // 450)
    pad_y = max(5, height // 700)
    x1 = width - margin
    y1 = height - margin
    x0 = max(margin, x1 - tw - 2 * pad_x)
    y0 = max(margin, y1 - th - 2 * pad_y)
    radius = max(5, min(14, width // 300))
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(0, 0, 0, 155))
    draw.text((x1 - pad_x, y1 - pad_y), text, font=font, fill=(255, 255, 255, 242), anchor="rd")
    return img


def brand_figure(fig, output_path, source_label: str | None = None) -> None:
    """Añade la misma firma a una figura Matplotlib antes de guardarla."""
    if getattr(fig, "_meteorologia_branding_applied", False):
        return
    text = credit_text(output_path, source_label)
    pixel_width = max(800.0, float(fig.get_size_inches()[0] * fig.dpi))
    fontsize = max(6.5, min(10.5, pixel_width / 240.0))
    fig.text(
        0.995,
        0.006,
        text,
        ha="right",
        va="bottom",
        fontsize=fontsize,
        color="white",
        bbox={"boxstyle": "round,pad=0.28", "facecolor": "black", "edgecolor": "none", "alpha": 0.58},
        zorder=10000,
    )
    fig._meteorologia_branding_applied = True
