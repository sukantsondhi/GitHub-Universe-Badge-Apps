"""Photo Frame crop, RGB conversion and low-memory PNG tests."""
import io
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
                    self.assertEqual(decoded.size, (160, 120))
                    self.assertEqual(decoded.mode, "P")

    def test_prevents_invalid_canvas(self):
        with self.assertRaises(ValueError):
            photo_tools.encode_badge_png(Image.new("RGB", (3, 3)))


if __name__ == "__main__":
    unittest.main()
