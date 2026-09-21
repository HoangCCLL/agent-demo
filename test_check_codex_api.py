import json
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def send_json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_json(200, {"object": "list", "data": [{"id": "test-model", "object": "model"}]})

    def do_POST(self):
        size = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(size))

        if request.get("stream"):
            data = (
                'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"STREAM_OK"}\n\n'
                'event: response.completed\ndata: {"type":"response.completed","response":{"status":"completed"}}\n\n'
                "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if isinstance(request.get("tool_choice"), dict):
            self.send_json(400, {"error": {"message": "named tool choice unsupported"}})
            return

        if isinstance(request.get("input"), list):
            output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "5"}]}]
            response_id = "resp_continue"
        elif request.get("tools"):
            output = [{"type": "function_call", "call_id": "call_1", "name": "add_numbers", "arguments": '{"a":2,"b":3}'}]
            response_id = "resp_tool"
        else:
            output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "OK"}]}]
            response_id = "resp_basic"

        self.send_json(200, {"id": response_id, "object": "response", "status": "completed", "output": output})


class CompatibilityCheckerTest(unittest.TestCase):
    def test_direct_ready_with_named_tool_choice_warning(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            script = Path(__file__).with_name("check_codex_api.py")
            result = subprocess.run(
                [sys.executable, str(script), "--base-url", f"http://127.0.0.1:{server.server_port}/v1", "--model", "test-model"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS  tool continuation", result.stdout)
        self.assertIn("WARN  named tool choice", result.stdout)
        self.assertIn("DIRECT_READY", result.stdout)


if __name__ == "__main__":
    unittest.main()
