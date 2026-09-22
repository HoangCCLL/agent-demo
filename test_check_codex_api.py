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
        self.server.requests.append(request)
        namespaced = any(tool.get("type") == "namespace" for tool in request.get("tools", []))
        if namespaced and self.server.namespace_mode == "reject":
            self.send_json(400, {"error": {"message": "unsupported namespace tool"}})
            return
        if namespaced and isinstance(request.get("input"), list) and self.server.namespace_mode == "reject_continuation":
            self.send_json(400, {"error": {"message": "namespaced call_id not found"}})
            return

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
            if namespaced:
                response_id = "resp_namespaced"
                output[0]["call_id"] = "call_namespaced"
                if self.server.namespace_mode != "drop":
                    output[0]["namespace"] = "mcp__probe"
        else:
            output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "OK"}]}]
            response_id = "resp_basic"

        self.send_json(200, {"id": response_id, "object": "response", "status": "completed", "output": output})


class CompatibilityCheckerTest(unittest.TestCase):
    def run_checker(self, *flags, namespace_mode="supported"):
        server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        server.requests = []
        server.namespace_mode = namespace_mode
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            script = Path(__file__).with_name("check_codex_api.py")
            result = subprocess.run(
                [sys.executable, str(script), "--base-url", f"http://127.0.0.1:{server.server_port}/v1", "--model", "test-model", *flags],
                capture_output=True,
                text=True,
                timeout=10,
            )
        finally:
            server.shutdown()
            server.server_close()
        return result, server.requests

    def test_direct_ready_with_named_tool_choice_warning(self):
        result, _ = self.run_checker(namespace_mode="reject")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS  tool continuation", result.stdout)
        self.assertIn("WARN  named tool choice", result.stdout)
        self.assertIn("DIRECT_READY", result.stdout)

    def test_namespaced_function_and_continuation(self):
        result, requests = self.run_checker("--check-namespaces")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS  namespaced function calling", result.stdout)
        self.assertIn("PASS  namespaced tool continuation", result.stdout)
        namespaced = [request for request in requests if any(tool.get("type") == "namespace" for tool in request.get("tools", []))]
        self.assertEqual(len(namespaced), 2)
        call, continuation = namespaced
        self.assertEqual(call["tool_choice"], "required")
        self.assertEqual(call["tools"][0]["name"], "mcp__probe")
        self.assertEqual(continuation["tools"], call["tools"])
        self.assertEqual(continuation["previous_response_id"], "resp_namespaced")
        self.assertEqual(continuation["input"], [{"type": "function_call_output", "call_id": "call_namespaced", "output": "5"}])

    def test_namespace_rejection_or_loss_is_incompatible(self):
        for mode in ("reject", "drop"):
            with self.subTest(mode=mode):
                result, _ = self.run_checker("--check-namespaces", namespace_mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FAIL  namespaced function calling", result.stdout)
                self.assertIn("FAIL  namespaced tool continuation", result.stdout)
                self.assertIn("INCOMPATIBLE", result.stdout)
                self.assertNotIn("DIRECT_READY", result.stdout)

    def test_namespaced_continuation_rejection_is_incompatible(self):
        result, _ = self.run_checker("--check-namespaces", namespace_mode="reject_continuation")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PASS  namespaced function calling", result.stdout)
        self.assertIn("FAIL  namespaced tool continuation", result.stdout)
        self.assertIn("INCOMPATIBLE", result.stdout)


if __name__ == "__main__":
    unittest.main()
