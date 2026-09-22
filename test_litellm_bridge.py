"""Real LiteLLM bridge integration with a loopback chat-completions upstream."""

import contextlib
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parent
IMAGE = "ghcr.io/berriai/litellm:v1.98.0"


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class ChatCompletionsHandler(BaseHTTPRequestHandler):
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

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            self.send_json(404, {"error": {"message": "wrong upstream endpoint"}})
            return
        if self.headers.get("Authorization") != "Bearer upstream-secret":
            self.send_json(401, {"error": {"message": "wrong upstream key"}})
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        has_tool_result = any(message.get("role") == "tool" for message in body.get("messages", []))
        tools = body.get("tools", [])
        tool_name = tools[0]["function"]["name"] if tools else None
        if body.get("stream"):
            if tool_name:
                chunks = [
                    {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                        {"index": 0, "id": "chat_call_1", "type": "function",
                         "function": {"name": tool_name, "arguments": ""}}]}}]},
                    {"choices": [{"index": 0, "delta": {"tool_calls": [
                        {"index": 0, "function": {"arguments": '{"a":2,'}}]}}]},
                    {"choices": [{
                        "index": 0,
                        "delta": {"tool_calls": [
                            {"index": 0, "function": {"arguments": '"b":3}'}}
                        ]},
                        "finish_reason": "tool_calls",
                    }]},
                ]
            else:
                chunks = [
                    {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "STREAM_OK"},
                                  "finish_reason": "stop"}]},
                ]
            data = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            encoded = data.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        if has_tool_result:
            message, finish_reason = {"role": "assistant", "content": "5"}, "stop"
        elif tool_name:
            message, finish_reason = {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "chat_call_1", "type": "function",
                                "function": {"name": tool_name, "arguments": '{"a":2,"b":3}'}}],
            }, "tool_calls"
        else:
            message, finish_reason = {"role": "assistant", "content": "OK"}, "stop"
        self.send_json(200, {
            "id": "chatcmpl_test", "object": "chat.completion", "created": 1,
            "model": body.get("model", "qwen/test"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })


class LiteLLMBridgeIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("docker"):
            raise unittest.SkipTest("Docker is required for LiteLLM bridge integration")
        if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
            raise unittest.SkipTest(f"pull {IMAGE} before LiteLLM bridge integration")
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), ChatCompletionsHandler)
        cls.upstream.requests = []
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()
        cls.gateway_port = unused_port()
        cls.name = f"harness-litellm-{os.getpid()}-{cls.gateway_port}"
        command = [
            "docker", "run", "-d", "--rm", "--name", cls.name, "--network", "host",
            "-v", f"{ROOT / 'docker/litellm/config.yaml'}:/app/config.yaml:ro",
            "-e", "LITELLM_MASTER_KEY=sk-gateway-test",
            "-e", "LLM_MODEL=qwen/test",
            "-e", "LITELLM_UPSTREAM_MODEL=openai/qwen/test",
            "-e", f"LMSTUDIO_BASE_URL=http://127.0.0.1:{cls.upstream.server_port}/v1",
            "-e", "LMSTUDIO_API_KEY=upstream-secret",
            "-e", "LITELLM_LOG=WARNING", "-e", "DO_NOT_TRACK=true",
            IMAGE, "--config", "/app/config.yaml", "--host", "127.0.0.1", "--port", str(cls.gateway_port),
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        cls.wait_for_gateway()

    @classmethod
    def wait_for_gateway(cls):
        deadline = time.monotonic() + 45
        url = f"http://127.0.0.1:{cls.gateway_port}/health/liveliness"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except urllib.error.URLError:
                time.sleep(0.2)
        logs = subprocess.run(["docker", "logs", cls.name], capture_output=True, text=True).stdout
        raise RuntimeError(f"LiteLLM did not become live: {logs[-2000:]}")

    @classmethod
    def tearDownClass(cls):
        with contextlib.suppress(Exception):
            subprocess.run(["docker", "rm", "-f", cls.name], capture_output=True, timeout=20)
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def test_responses_namespace_is_bridged_to_chat_completions(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "check_codex_api.py"), "--base-url",
             f"http://127.0.0.1:{self.gateway_port}/v1", "--model", "qwen/test",
             "--api-key-env", "LITELLM_BRIDGE_TEST_KEY", "--check-auth",
             "--check-namespaces", "--continuation-mode", "input-history", "--timeout", "20"],
            env={**os.environ, "LITELLM_BRIDGE_TEST_KEY": "sk-gateway-test"},
            capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS  namespaced function calling", result.stdout)
        self.assertIn("PASS  namespaced tool continuation", result.stdout)
        self.assertIn("PASS  namespaced tool streaming", result.stdout)
        self.assertIn("DIRECT_READY", result.stdout)

        requests = self.upstream.requests
        namespaced = [request for request in requests if request.get("tools") and any(
            tool["function"]["name"].startswith("mcp__probe__") for tool in request["tools"])]
        self.assertGreaterEqual(len(namespaced), 3)
        for request in namespaced:
            self.assertNotIn("namespace", json.dumps(request))
            self.assertEqual(request["tools"][0]["function"]["name"], "mcp__probe__add_numbers")
        continuations = [request for request in namespaced if any(
            message.get("role") == "tool" for message in request.get("messages", []))]
        self.assertEqual(len(continuations), 1)
        self.assertEqual(continuations[0]["messages"][-1]["content"], "5")


if __name__ == "__main__":
    unittest.main()
