import base64
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_model_runtime as runtime
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

    def test_input_history_mode_does_not_claim_server_stored_conversations(self):
        requests = []

        def respond(base_url, path, timeout, api_key, payload):
            requests.append(payload)
            self.assertNotIn("previous_response_id", payload)
            prompt = payload["input"]
            reply = prompt.rsplit("Reply with exactly ", 1)[-1].rstrip(".")
            output = [{"type": "reasoning"}, {"type": "message", "content": [
                {"type": "output_text", "text": reply}]}]
            if payload.get("tools"):
                output = [{"type": "function_call", "arguments": '{"value":11}'},
                          {"type": "function_call", "arguments": '{"value":22}'}]
            return 200, {}, {"object": "response", "status": "completed", "output": output}

        text = io.StringIO()
        with patch("sys.argv", ["verify_model_runtime.py", "--base-url", "http://unused.invalid/v1",
                                "--model", "test", "--continuation-mode", "input-history"]), \
                patch.object(runtime, "request_json", side_effect=respond), \
                patch.object(runtime.urllib.request, "urlopen") as stream, contextlib.redirect_stdout(text):
            stream.return_value.read.return_value = b"data: {}\n\n"
            result = runtime.main()
        self.assertEqual(result, 0, text.getvalue())
        self.assertIn("SKIP  server-stored conversation state", text.getvalue())
        self.assertNotIn("PASS  invalid conversation state", text.getvalue())
        self.assertIn("MODEL_RUNTIME_READY", text.getvalue())


if __name__ == "__main__":
    unittest.main()
