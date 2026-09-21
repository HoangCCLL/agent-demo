import base64
import tempfile
import unittest
from pathlib import Path

from verify_model_runtime import image_input


class VisionPayloadTest(unittest.TestCase):
    def test_builds_png_data_url_without_leaking_expected_text(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory, "fixture.png")
            image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            content = image_input(image)

        self.assertEqual(content[0], {
            "type": "input_text",
            "text": "Transcribe the main heading in this image. Reply with only that heading.",
        })
        self.assertEqual(content[1]["type"], "input_image")
        prefix, encoded = content[1]["image_url"].split(",", 1)
        self.assertEqual(prefix, "data:image/png;base64")
        self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG"))
        self.assertNotIn("VISION_7F31", content[0]["text"])


if __name__ == "__main__":
    unittest.main()
