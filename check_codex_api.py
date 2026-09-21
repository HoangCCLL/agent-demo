#!/usr/bin/env python3
"""Check whether an OpenAI-compatible server can drive Codex directly."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


TOOL = {
    "type": "function",
    "name": "add_numbers",
    "description": "Add two integers.",
    "parameters": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
    "strict": True,
}


def request(base_url, path, timeout, api_key, payload=None):
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        body = error.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {error.code}: {body[:500]}") from error


def request_json(base_url, path, timeout, api_key, payload=None):
    status, headers, body = request(base_url, path, timeout, api_key, payload)
    try:
        return status, headers, json.loads(body)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid JSON: {body[:200]!r}") from error


def output_text(response):
    return "".join(
        part.get("text", "")
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Example: http://host:1235/v1")
    parser.add_argument("--model", help="Model ID; defaults to the first model returned")
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    api_key = os.environ.get(args.api_key_env)
    required_ok = True

    def check(label, action, required=True):
        nonlocal required_ok
        try:
            value = action()
            print(f"PASS  {label}")
            return value
        except Exception as error:
            level = "FAIL" if required else "WARN"
            print(f"{level}  {label}: {error}")
            if required:
                required_ok = False
            return None

    def models_check():
        _, _, body = request_json(base_url, "/models", args.timeout, api_key)
        ids = [item.get("id") for item in body.get("data", []) if item.get("id")]
        if body.get("object") != "list" or not ids:
            raise RuntimeError("expected a non-empty OpenAI model list")
        if args.model and args.model not in ids:
            raise RuntimeError(f"model {args.model!r} not found")
        return args.model or ids[0]

    model = check("model discovery", models_check)
    if not model:
        print("\nINCOMPATIBLE")
        return 1
    print(f"INFO  model: {model}")

    def post(payload):
        _, _, body = request_json(base_url, "/responses", args.timeout, api_key, payload)
        if body.get("object") != "response" or body.get("status") != "completed":
            raise RuntimeError("response is not completed")
        return body

    def basic_check():
        body = post({
            "model": model,
            "instructions": "Follow the user's exact output format.",
            "input": "Reply with exactly OK",
            "max_output_tokens": 128,
        })
        if output_text(body).strip() != "OK":
            raise RuntimeError(f"expected output_text 'OK', got {output_text(body)!r}")
        return body

    check("Responses API", basic_check)

    def stream_check():
        status, headers, body = request(base_url, "/responses", args.timeout, api_key, {
            "model": model,
            "input": "Reply with exactly STREAM_OK",
            "stream": True,
            "max_output_tokens": 128,
        })
        text = body.decode(errors="replace")
        if status != 200 or "text/event-stream" not in headers.get("Content-Type", ""):
            raise RuntimeError("expected HTTP 200 text/event-stream")
        if "response.output_text.delta" not in text or "response.completed" not in text:
            raise RuntimeError("missing required Responses SSE events")
        return text

    check("Responses streaming", stream_check)

    tool_payload = {
        "model": model,
        "input": "Call add_numbers with a=2 and b=3. Do not answer directly.",
        "tools": [TOOL],
        "tool_choice": "required",
        "parallel_tool_calls": True,
        "max_output_tokens": 256,
    }

    def tool_check():
        body = post(tool_payload)
        calls = [item for item in body.get("output", []) if item.get("type") == "function_call"]
        if not calls:
            raise RuntimeError("no function_call in response output")
        call = calls[0]
        if call.get("name") != "add_numbers" or json.loads(call.get("arguments", "{}")) != {"a": 2, "b": 3}:
            raise RuntimeError(f"invalid function call: {call}")
        if not body.get("id") or not call.get("call_id"):
            raise RuntimeError("missing response id or call_id")
        return body["id"], call["call_id"]

    tool_result = check("function calling", tool_check)

    def continuation_check():
        response_id, call_id = tool_result
        body = post({
            "model": model,
            "previous_response_id": response_id,
            "input": [{"type": "function_call_output", "call_id": call_id, "output": "5"}],
            "tools": [TOOL],
            "tool_choice": "auto",
            "max_output_tokens": 256,
        })
        if not output_text(body).strip():
            raise RuntimeError("no assistant output after function_call_output")
        return body

    if tool_result:
        check("tool continuation", continuation_check)
    else:
        required_ok = False
        print("FAIL  tool continuation: function calling failed")

    def named_choice_check():
        payload = dict(tool_payload)
        payload["tool_choice"] = {"type": "function", "name": "add_numbers"}
        return post(payload)

    check("named tool choice", named_choice_check, required=False)

    verdict = "DIRECT_READY" if required_ok else "INCOMPATIBLE"
    print(f"\n{verdict}")
    return 0 if required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
