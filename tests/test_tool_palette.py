import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.ui.tool_palette import ToolPalette


class ToolPaletteIconTests(unittest.TestCase):
    def test_opaque_icon_is_cropped_and_normalized_without_stretching(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "wide.png"
            source = Image.new("RGB", (240, 120), "white")
            ImageDraw.Draw(source).rectangle((20, 30, 220, 90), fill="black")
            source.save(path)

            normalized = ToolPalette._prepare_icon(path, size=26)

        self.assertEqual(normalized.size, (26, 26))
        bbox = normalized.getchannel("A").getbbox()
        self.assertIsNotNone(bbox)
        self.assertGreater(bbox[2] - bbox[0], bbox[3] - bbox[1])

    def test_transparent_icon_keeps_its_alpha_shape(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "transparent.png"
            source = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
            ImageDraw.Draw(source).ellipse((25, 25, 75, 75), fill="black")
            source.save(path)

            normalized = ToolPalette._prepare_icon(path, size=26)

        self.assertEqual(normalized.size, (26, 26))
        self.assertIsNotNone(normalized.getchannel("A").getbbox())


if __name__ == "__main__":
    unittest.main()
