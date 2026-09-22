import json
import os
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
        authorization = self.headers.get("Authorization")
        self.server.auth_requests.append(authorization)
        if self.server.auth_mode != "open" and authorization != "Bearer checker-test-key":
            if self.server.auth_mode != "allow_wrong" or not authorization:
                status = (500 if self.server.auth_mode == "broken" else 400
                          if self.server.auth_mode == "bad_request" or self.server.auth_mode == "no_db" and authorization
                          else (403 if authorization else 401))
                self.send_json(status, {"error": {"message": "authentication rejected",
                                        "code": "no_db_connection" if self.server.auth_mode == "no_db" and status == 400 else None}})
                return
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
            if namespaced:
                call = {"id": "fc_stream", "type": "function_call", "call_id": "call_stream", "name": "add_numbers", "namespace": "mcp__probe", "arguments": '{"a":2,"b":3}'}
                added = dict(call, arguments="")
                if self.server.namespace_mode == "stream_drop":
                    added.pop("namespace")
                done = dict(call)
                if self.server.namespace_mode == "stream_wrong_call_id":
                    done["call_id"] = "other_call"
                events = [
                    {"type": "response.output_item.added", "output_index": 0, "item": added},
                    {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_stream", "delta": '{"a":2,'},
                    {"type": "response.function_call_arguments.delta", "output_index": 0, "item_id": "fc_stream", "delta": '"b":3}'},
                    {"type": "response.function_call_arguments.done", "output_index": 0, "item_id": "fc_stream", "arguments": call["arguments"]},
                    {"type": "response.output_item.done", "output_index": 0, "item": done},
                    {"type": "response.completed", "response": {"id": "resp_stream", "object": "response", "status": "completed", "output": [call]}},
                ]
                if self.server.namespace_mode == "stream_wrong_arguments":
                    events[1]["delta"] = '{"a":9,'
                if self.server.namespace_mode == "stream_wrong_item":
                    events[1]["item_id"] = "other_item"
                if self.server.namespace_mode == "stream_incomplete":
                    events.pop()
                if self.server.namespace_mode == "stream_failed":
                    events[-1] = {"type": "response.failed", "response": {"status": "failed"}}
                if self.server.namespace_mode == "stream_incomplete_status":
                    events[-1]["response"]["status"] = "incomplete"
                data = ("".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n").encode()
            else:
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
            if self.server.namespace_mode == "require_history" and "previous_response_id" in request:
                self.send_json(400, {"error": {"message": "response history storage disabled"}})
                return
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
            if self.server.namespace_mode == "require_history":
                output.insert(0, {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "Use the tool."}]})
                output.insert(1, {"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": "Calling the tool."}]})
        else:
            output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "OK"}]}]
            response_id = "resp_basic"

        self.send_json(200, {"id": response_id, "object": "response", "status": "completed", "output": output})


class CompatibilityCheckerTest(unittest.TestCase):
    def run_checker(self, *flags, namespace_mode="supported", auth_mode="open", api_key="checker-test-key"):
        server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
        server.requests = []
        server.namespace_mode = namespace_mode
        server.auth_mode = auth_mode
        server.auth_requests = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            script = Path(__file__).with_name("check_codex_api.py")
            result = subprocess.run(
                [sys.executable, str(script), "--base-url", f"http://127.0.0.1:{server.server_port}/v1", "--model", "test-model", "--api-key-env", "CHECKER_TEST_API_KEY", *flags],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "CHECKER_TEST_API_KEY": api_key},
            )
        finally:
            server.shutdown()
            server.server_close()
        self.auth_requests = server.auth_requests
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
        self.assertEqual(len(namespaced), 3)
        call, continuation = [request for request in namespaced if not request.get("stream")]
        self.assertEqual(call["tool_choice"], "required")
        self.assertEqual(call["tools"][0]["name"], "mcp__probe")
        self.assertEqual(continuation["tools"], call["tools"])
        self.assertEqual(continuation["previous_response_id"], "resp_namespaced")
        self.assertEqual(continuation["input"], [{"type": "function_call_output", "call_id": "call_namespaced", "output": "5"}])
        self.assertIn("PASS  namespaced tool streaming", result.stdout)

    def test_input_history_preserves_full_output_and_tool_namespace(self):
        result, requests = self.run_checker("--check-namespaces", "--continuation-mode", "input-history", namespace_mode="require_history")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for namespaced, call_id in ((False, "call_1"), (True, "call_namespaced")):
            with self.subTest(namespaced=namespaced):
                calls = [request for request in requests if request.get("tools") and not request.get("stream") and not isinstance(request.get("tool_choice"), dict) and any(tool.get("type") == "namespace" for tool in request["tools"]) == namespaced]
                first, continuation = calls
                self.assertNotIn("previous_response_id", continuation)
                self.assertEqual(continuation["tools"], first["tools"])
                self.assertEqual(continuation["input"][0], {"role": "user", "content": first["input"]})
                self.assertEqual(continuation["input"][1], {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "Use the tool."}]})
                self.assertEqual(continuation["input"][2], {"type": "message", "id": "msg_1", "role": "assistant", "content": [{"type": "output_text", "text": "Calling the tool."}]})
                expected_call = {"type": "function_call", "call_id": call_id, "name": "add_numbers", "arguments": '{"a":2,"b":3}'}
                if namespaced:
                    expected_call["namespace"] = "mcp__probe"
                self.assertEqual(continuation["input"][3], expected_call)
                self.assertEqual(continuation["input"][4], {"type": "function_call_output", "call_id": call_id, "output": "5"})

    def test_authentication_requires_missing_and_wrong_key_rejection(self):
        result, _ = self.run_checker("--check-auth", auth_mode="protected")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.auth_requests), 3)
        self.assertIn(None, self.auth_requests)
        self.assertIn("Bearer checker-test-key", self.auth_requests)
        self.assertEqual(sum(bool(value) and value != "Bearer checker-test-key" for value in self.auth_requests), 1)
        self.assertNotIn("checker-test-key", result.stdout + result.stderr)

    def test_authentication_accepts_litellm_no_database_wrong_key_rejection(self):
        result, _ = self.run_checker("--check-auth", auth_mode="no_db")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_authentication_probe_fails_open_or_broken_endpoints(self):
        for mode in ("open", "allow_wrong", "broken"):
            with self.subTest(mode=mode):
                result, _ = self.run_checker("--check-auth", auth_mode=mode)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("FAIL  authentication", result.stdout)
                self.assertIn("INCOMPATIBLE", result.stdout)

    def test_authentication_probe_rejects_unrelated_bad_request(self):
        result, _ = self.run_checker("--check-auth", auth_mode="bad_request")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("FAIL  authentication", result.stdout)

    def test_authentication_probe_requires_a_key_before_requests(self):
        result, _ = self.run_checker("--check-auth", api_key="")
        self.assertEqual(result.returncode, 2)
        self.assertIn("requires", result.stderr)
        self.assertEqual(self.auth_requests, [])

    def test_namespaced_stream_rejects_loss_mismatch_and_incomplete_response(self):
        for mode in ("stream_drop", "stream_wrong_call_id", "stream_wrong_arguments", "stream_wrong_item", "stream_incomplete", "stream_failed", "stream_incomplete_status"):
            with self.subTest(mode=mode):
                result, _ = self.run_checker("--check-namespaces", namespace_mode=mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FAIL  namespaced tool streaming", result.stdout)
                self.assertIn("INCOMPATIBLE", result.stdout)

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
