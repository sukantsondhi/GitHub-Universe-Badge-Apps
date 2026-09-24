"""Optional Pillow-powered local photo editor for the Work Status controller.

All image processing takes place on the laptop; only the 160x120 PNG bytes
are transmitted to a paired badge on the home LAN.
"""
from __future__ import annotations

from io import BytesIO

WIDTH, HEIGHT = 160, 120
MAX_PNG_BYTES = 98304


def pillow():
    try:
        from PIL import Image, ImageOps, ImageTk
    except ImportError as exc:
        raise RuntimeError(
            "Photo Frame requires Pillow. Install it using: python -m pip install Pillow"
        ) from exc
    return Image, ImageOps, ImageTk


def open_photo(path):
    Image, ImageOps, _ = pillow()
    with Image.open(path) as source:
        source = ImageOps.exif_transpose(source)
        source.load()
        return source.convert("RGB")


def crop_photo(photo, zoom=1.0, offset_x=0.0, offset_y=0.0):
    """Crop to 4:3 with a centre-origin pan expressed in output pixels."""
    Image, _, _ = pillow()
    zoom = max(1.0, min(3.0, float(zoom)))
    factor = max(WIDTH / photo.width, HEIGHT / photo.height) * zoom
    target_w = max(WIDTH, round(photo.width * factor))
    target_h = max(HEIGHT, round(photo.height * factor))
    scaled = photo.resize((target_w, target_h), Image.Resampling.LANCZOS)
    limit_x = (target_w - WIDTH) / 2
    limit_y = (target_h - HEIGHT) / 2
    pan_x = max(-limit_x, min(limit_x, float(offset_x)))
    pan_y = max(-limit_y, min(limit_y, float(offset_y)))
    left = round((target_w - WIDTH) / 2 - pan_x)
    top = round((target_h - HEIGHT) / 2 - pan_y)
    left = max(0, min(target_w - WIDTH, left))
    top = max(0, min(target_h - HEIGHT, top))
    return scaled.crop((left, top, left + WIDTH, top + HEIGHT))


def encode_badge_png(image):
    """Use a 128-colour indexed PNG to minimise badge RAM and transfer size."""
    Image, _, _ = pillow()
    if image.size != (WIDTH, HEIGHT):
        raise ValueError("The photo must be cropped to 160 by 120 pixels.")
    indexed = image.convert("RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT)
    buffer = BytesIO()
    indexed.save(buffer, format="PNG", optimize=True)
    payload = buffer.getvalue()
    if len(payload) > MAX_PNG_BYTES:
        raise ValueError("This picture exceeds the badge's 96 KiB image limit.")
    return payload
