#!/usr/bin/env python3
"""Small Streamable HTTP MCP client used by the Phase 1 smoke tests."""

import json
import urllib.error
import urllib.request


PROTOCOL_VERSION = "2025-06-18"


def decode_response(headers, body):
    if not body:
        return None
    content_type = headers.get("Content-Type", "")
    if "text/event-stream" not in content_type:
        return json.loads(body)
    messages = []
    for line in body.decode(errors="replace").splitlines():
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            messages.append(json.loads(line[5:].strip()))
    if not messages:
        raise RuntimeError("empty MCP event stream")
    return messages[-1]


def result_text(result):
    text = "\n".join(item.get("text", "") for item in result.get("content", []) if item.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(text or "MCP tool returned isError=true")
    return text


class McpClient:
    def __init__(self, url, token=None, timeout=60):
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session_id = None
        self.next_id = 1

    def _send(self, payload, expect_response=True):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.url, json.dumps(payload).encode(), headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if response.headers.get("Mcp-Session-Id"):
                    self.session_id = response.headers["Mcp-Session-Id"]
                body = response.read()
                return decode_response(response.headers, body) if expect_response else None
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"HTTP {error.code}: {detail[:500]}") from error

    def request(self, method, params=None):
        request_id = self.next_id
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        response = self._send(message)
        if not response or response.get("id") != request_id:
            raise RuntimeError(f"invalid MCP response: {response}")
        if response.get("error"):
            raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
        return response.get("result", {})

    def initialize(self):
        result = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "phase1-verifier", "version": "1.0"},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        return result

    def list_tools(self):
        return self.request("tools/list").get("tools", [])

    def call_tool(self, name, arguments=None):
        return self.request("tools/call", {"name": name, "arguments": arguments or {}})

    def close(self):
        if not self.session_id:
            return
        headers = {"Mcp-Session-Id": self.session_id, "MCP-Protocol-Version": PROTOCOL_VERSION}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.url, headers=headers, method="DELETE")
        try:
            urllib.request.urlopen(request, timeout=self.timeout).close()
        except urllib.error.HTTPError as error:
            if error.code not in (404, 405):
                raise

