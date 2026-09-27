"""Optional Pillow-powered local photo editor for the Work Status controller.

All image processing takes place on the laptop; small paletted PNG frames
are transmitted to a paired badge on the home LAN.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
import struct

WIDTH, HEIGHT = 160, 120
ENCODE_WIDTH, ENCODE_HEIGHT = 80, 60
MAX_PNG_BYTES = 98304
MAX_ANIMATION_BYTES = 524288
MAX_ANIMATION_FRAMES = 120
VIDEO_SUFFIXES = {
    ".avi", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm",
}


def pillow():
    try:
        from PIL import Image, ImageOps, ImageTk
    except ImportError as exc:
        raise RuntimeError(
            "Photo Frame requires Pillow. Install it using: python -m pip install Pillow"
        ) from exc
    return Image, ImageOps, ImageTk


def open_photo(path):
    """Open a still image or the first frame of an animated image."""
    Image, ImageOps, _ = pillow()
    with Image.open(path) as source:
        source = ImageOps.exif_transpose(source)
        try:
            source.seek(0)
        except EOFError:
            pass
        source.load()
        return source.convert("RGB")


def open_media(path):
    """Return ``(image, kind)`` for a still image, GIF, or video.

    The returned image is the crop preview. Animated GIFs are identified so
    the controller can preserve their frames; videos still use one frame.
    """
    suffix = Path(path).suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        kind = "GIF frame" if suffix == ".gif" else "image"
        if suffix == ".gif":
            Image, _, _ = pillow()
            with Image.open(path) as source:
                if getattr(source, "is_animated", False):
                    kind = "GIF animation"
        return open_photo(path), kind

    Image, _, _ = pillow()
    try:
        import imageio.v2 as imageio
    except ImportError as exc:
        raise RuntimeError(
            "Video files require imageio and imageio-ffmpeg. Install them using: "
            "python -m pip install imageio imageio-ffmpeg"
        ) from exc

    reader = None
    try:
        reader = imageio.get_reader(str(path))
        frame = reader.get_data(0)
        return Image.fromarray(frame).convert("RGB"), "video frame"
    except Exception as exc:
        raise RuntimeError(
            "This video could not be decoded. Install imageio-ffmpeg or choose "
            "a PNG, JPEG, GIF, MP4, MOV, or WebM file."
        ) from exc
    finally:
        if reader is not None:
            reader.close()


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
    """Encode an 80x60, 8-bit paletted PNG for the badge decoder.

    The badge has too little free heap for a full-size image while serving
    Wi-Fi requests. This keeps the decoded pixel buffer to 4,800 bytes; the
    badge scales the same 4:3 crop to its 160x120 screen.
    """
    Image, _, _ = pillow()
    if image.size != (WIDTH, HEIGHT):
        raise ValueError("The photo must be cropped to 160 by 120 pixels.")
    buffer = BytesIO()
    encoded = image.convert("RGB").resize(
        (ENCODE_WIDTH, ENCODE_HEIGHT), Image.Resampling.LANCZOS,
    ).quantize(
        colors=256, method=Image.Quantize.MEDIANCUT,
    )
    encoded.info.clear()
    encoded.save(
        buffer,
        format="PNG",
        bits=8,
        optimize=False,
        compress_level=9,
    )
    payload = buffer.getvalue()
    if len(payload) > MAX_PNG_BYTES:
        raise ValueError("This picture exceeds the badge's 96 KiB image limit.")
    return payload


def encode_badge_animation(path, zoom=1.0, offset_x=0.0, offset_y=0.0):
    """Package composited GIF frames as timed, independently decodable PNGs."""
    Image, _, _ = pillow()
    with Image.open(path) as source:
        count = getattr(source, "n_frames", 1)
        if count < 2:
            return None
        if count > MAX_ANIMATION_FRAMES:
            raise ValueError("Choose a GIF with at most 120 frames.")
        repeat = source.info.get("loop")
        plays = 1 if repeat is None else (0 if repeat == 0 else min(65535, repeat + 1))
        output = BytesIO()
        output.write(b"WSA1" + struct.pack(">HH", count, plays))
        for index in range(count):
            source.seek(index)
            duration = max(20, min(60000, int(source.info.get("duration", 100) or 100)))
            frame = crop_photo(source.convert("RGB"), zoom, offset_x, offset_y)
            payload = encode_badge_png(frame)
            output.write(struct.pack(">HI", duration, len(payload)))
            output.write(payload)
            if output.tell() > MAX_ANIMATION_BYTES:
                raise ValueError("This GIF exceeds the badge's 512 KiB animation limit.")
        return output.getvalue()
