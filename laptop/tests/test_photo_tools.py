"""Photo Frame crop, media conversion and low-memory PNG tests."""
import io
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import photo_tools

try:
    from PIL import Image
except ImportError:
    Image = None


@unittest.skipIf(Image is None, "Install Pillow to enable photo editor")
class PhotoEditorTests(unittest.TestCase):
    def test_wide_and_portrait_images_crop_to_badge_dimensions(self):
        for size in ((1600, 900), (800, 1600), (160, 120)):
            with self.subTest(size=size):
                source = Image.new("RGB", size, (100, 150, 230))
                cropped = photo_tools.crop_photo(source, 1.5, 13, -17)
                self.assertEqual(cropped.size, (160, 120))
                payload = photo_tools.encode_badge_png(cropped)
                self.assertLess(len(payload), 98304)
                self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
                with Image.open(io.BytesIO(payload)) as decoded:
                    self.assertEqual(decoded.size, (80, 60))
                    self.assertEqual(decoded.mode, "P")
                    self.assertEqual(decoded.convert("RGB").getpixel((40, 30)),
                                     (100, 150, 230))

    def test_png_uses_low_memory_eight_bit_palette_encoding(self):
        source = Image.effect_noise((160, 120), 100).convert("RGB")
        payload = photo_tools.encode_badge_png(source)
        self.assertLess(len(payload), 98304)
        with Image.open(io.BytesIO(payload)) as decoded:
            decoded.load()
            self.assertEqual(decoded.size, (80, 60))
            self.assertEqual(decoded.mode, "P")
            self.assertEqual(len(decoded.tobytes()), 4800)
        self.assertEqual(payload[24:29], b"\x08\x03\x00\x00\x00")

    def test_jpeg_png_and_gif_crop_and_encode_for_badges(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            for suffix in ("jpg", "png", "gif"):
                with self.subTest(suffix=suffix):
                    path = Path(folder) / ("photo." + suffix)
                    Image.new("RGB", (320, 480), (30, 150, 210)).save(path)
                    photo, _ = photo_tools.open_media(path)
                    cropped = photo_tools.crop_photo(photo, 1.5, 12, -15)
                    payload = photo_tools.encode_badge_png(cropped)
                    with Image.open(io.BytesIO(payload)) as decoded:
                        decoded.load()
                        self.assertEqual(decoded.size, (80, 60))
                        self.assertEqual(decoded.mode, "P")

    def test_source_metadata_is_not_sent_to_the_badge(self):
        source = Image.new("RGB", (160, 120), (30, 150, 210))
        source.info.update(transparency=(30, 150, 210), icc_profile=b"profile")
        payload = photo_tools.encode_badge_png(source)
        with Image.open(io.BytesIO(payload)) as decoded:
            decoded.load()
            self.assertEqual(decoded.info, {})
            self.assertEqual(decoded.convert("RGBA").getpixel((40, 30))[3], 255)

    def test_prevents_invalid_canvas(self):
        with self.assertRaises(ValueError):
            photo_tools.encode_badge_png(Image.new("RGB", (3, 3)))

    def test_png_compression_reduces_transfer_without_changing_pixels(self):
        payload = photo_tools.encode_badge_png(Image.new("RGB", (160, 120), (30, 150, 210)))
        with Image.open(io.BytesIO(payload)) as decoded:
            original_pixels = decoded.convert("RGB").tobytes()
            baseline = io.BytesIO()
            decoded.save(baseline, format="PNG", bits=8, optimize=False, compress_level=1)
        self.assertLess(len(payload), len(baseline.getvalue()))
        with Image.open(io.BytesIO(baseline.getvalue())) as decoded:
            self.assertEqual(decoded.convert("RGB").tobytes(), original_pixels)

    def test_gif_is_accepted_as_a_static_badge_frame(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "animation.gif"
            Image.new("RGB", (320, 240), "#3184cf").save(path, format="GIF")
            frame, kind = photo_tools.open_media(path)
        self.assertEqual(frame.size, (320, 240))
        self.assertEqual(frame.mode, "RGB")
        self.assertEqual(kind, "GIF frame")

    def test_still_image_kind_is_reported(self):
        from unittest.mock import patch

        with patch.object(photo_tools, "open_photo", return_value="frame"):
            frame, kind = photo_tools.open_media("example.jpg")
        self.assertEqual(frame, "frame")
        self.assertEqual(kind, "image")

    def test_animation_keeps_distinct_frames_timing_and_loop_count(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "moving.gif"
            first = Image.new("RGB", (320, 240), "red")
            second = Image.new("RGB", (320, 240), "blue")
            first.save(path, save_all=True, append_images=[second],
                       duration=[100, 250], loop=2, disposal=2)
            payload = photo_tools.encode_badge_animation(path, 1.5, 12, -8)
        stream = io.BytesIO(payload)
        self.assertEqual(stream.read(8), b"WSA1" + struct.pack(">HH", 2, 3))
        for duration, color in ((100, (255, 0, 0)), (250, (0, 0, 255))):
            delay, size = struct.unpack(">HI", stream.read(6))
            self.assertEqual(delay, duration)
            with Image.open(io.BytesIO(stream.read(size))) as decoded:
                self.assertEqual(decoded.size, (80, 60))
                self.assertEqual(decoded.mode, "P")
                self.assertEqual(decoded.convert("RGB").getpixel((40, 30)), color)
        self.assertEqual(stream.read(), b"")

    def test_single_frame_gif_remains_a_still_photo(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "still.gif"
            Image.new("RGB", (160, 120), "red").save(path)
            self.assertIsNone(photo_tools.encode_badge_animation(path))

    def test_video_import_decodes_only_the_first_frame(self):
        import tempfile
        import imageio.v2 as imageio
        import numpy as np

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.mp4"
            frames = [np.full((32, 32, 3), color, dtype=np.uint8)
                      for color in ((240, 20, 20), (20, 20, 240))]
            imageio.mimsave(path, frames, fps=2)
            image, kind = photo_tools.open_media(path)
            self.assertEqual(kind, "video frame")
            red, green, blue = image.getpixel((16, 16))
            self.assertGreater(red, 200)
            self.assertLess(green, 50)
            self.assertLess(blue, 50)
            self.assertEqual(photo_tools.crop_photo(image).size, (160, 120))


if __name__ == "__main__":
    unittest.main()
