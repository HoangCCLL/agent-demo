#!/usr/bin/env python3
"""Check an OpenAI-compatible endpoint (native or bridge) for Codex API support."""

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
    parser.add_argument("--check-auth", action="store_true", help="Require a configured key and reject missing/incorrect bearer keys")
    parser.add_argument("--continuation-mode", choices=("previous-response-id", "input-history"), default="previous-response-id", help="Use stored response IDs (default) or resend input/output history")
    parser.add_argument("--check-namespaces", action="store_true", help="Require namespaced function tools used by current Codex MCP clients")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    api_key = os.environ.get(args.api_key_env)
    if args.check_auth and not api_key:
        parser.error(f"--check-auth requires a key in {args.api_key_env}")
    required_ok = True

    def check(label, action, required=True):
        nonlocal required_ok
        try:
            value = action()
            print(f"PASS  {label}")
            return value
        except Exception as error:
            level = "FAIL" if required else "WARN"
            detail = str(error).replace(api_key, "[REDACTED]") if api_key else str(error)
            print(f"{level}  {label}: {detail}")
            if required:
                required_ok = False
            return None

    def auth_check(key):
        try:
            request(base_url, "/models", args.timeout, key)
        except RuntimeError as error:
            # LiteLLM without Prisma rejects an invalid virtual key with its
            # no_db_connection HTTP 400 rather than 401/403. It is still a
            # rejected credential; other HTTP 400 errors are not auth proof.
            if isinstance(error.__cause__, urllib.error.HTTPError) and (
                error.__cause__.code in (401, 403)
                or (error.__cause__.code == 400 and "no_db_connection" in str(error))
            ):
                return
            raise
        raise RuntimeError("expected HTTP 401/403, or LiteLLM no_db_connection HTTP 400, for an unauthorized request")

    if args.check_auth:
        wrong_key = "codex-probe-invalid-api-key"
        if wrong_key == api_key:
            wrong_key += "-invalid"
        check("authentication without key", lambda: auth_check(None))
        check("authentication with wrong key", lambda: auth_check(wrong_key))

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

    def validate_call(call, namespace=None, arguments=True):
        if call.get("name") != "add_numbers" or (arguments and json.loads(call.get("arguments", "{}")) != {"a": 2, "b": 3}):
            raise RuntimeError(f"invalid function call: {call}")
        if namespace is not None and call.get("namespace") != namespace:
            raise RuntimeError(f"expected function_call namespace {namespace!r}, got {call.get('namespace')!r}")
        if not call.get("call_id"):
            raise RuntimeError("missing call_id")

    def tool_check(payload=tool_payload, namespace=None):
        body = post(payload)
        calls = [item for item in body.get("output", []) if item.get("type") == "function_call"]
        if not calls:
            raise RuntimeError("no function_call in response output")
        call = calls[0]
        validate_call(call, namespace)
        if not body.get("id") or not call.get("call_id"):
            raise RuntimeError("missing response id or call_id")
        return body, call["call_id"]

    tool_result = check("function calling", tool_check)

    def continuation_check(result=tool_result, original=tool_payload):
        response, call_id = result
        payload = {
            "model": model,
            "input": [{"type": "function_call_output", "call_id": call_id, "output": "5"}],
            "instructions": "After receiving the tool result, reply with exactly 5.",
            "tools": original["tools"],
            "tool_choice": "auto",
            "max_output_tokens": 256,
        }
        if args.continuation_mode == "input-history":
            payload["input"] = [{"role": "user", "content": original["input"]}] + response["output"] + payload["input"]
        else:
            payload["previous_response_id"] = response["id"]
        body = post(payload)
        if not output_text(body).strip():
            raise RuntimeError("no assistant output after function_call_output")
        if original is not tool_payload and output_text(body).strip() != "5":
            raise RuntimeError(f"expected namespaced tool result '5', got {output_text(body)!r}")
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

    def namespaced_stream_check(payload, namespace):
        status, headers, body = request(base_url, "/responses", args.timeout, api_key, dict(payload, stream=True))
        if status != 200 or "text/event-stream" not in headers.get("Content-Type", ""):
            raise RuntimeError("expected HTTP 200 text/event-stream")
        events, data = [], []
        for line in body.decode().splitlines() + [""]:
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
            elif not line and data:
                value = "\n".join(data)
                if value != "[DONE]":
                    events.append(json.loads(value))
                data = []
        if any(event.get("type") in ("error", "response.failed", "response.incomplete") for event in events):
            raise RuntimeError("stream reported an error or incomplete response")
        completed = [event["response"] for event in events if event.get("type") == "response.completed"]
        if len(completed) != 1 or completed[0].get("status") != "completed":
            raise RuntimeError("missing completed streaming response")
        emitted = [event for event in events if event.get("type") == "response.output_item.added" and event.get("item", {}).get("type") == "function_call"]
        done = [event for event in events if event.get("type") == "response.output_item.done" and event.get("item", {}).get("type") == "function_call"]
        calls = [item for item in completed[0].get("output", []) if item.get("type") == "function_call"]
        if len(emitted) != 1 or len(done) != 1 or len(calls) != 1:
            raise RuntimeError("expected one emitted, done, and completed streaming function call")
        added, finished, call = emitted[0]["item"], done[0]["item"], calls[0]
        validate_call(added, namespace, arguments=False)
        validate_call(finished, namespace)
        validate_call(call, namespace)
        if not call.get("id") or any(added.get(key) != call.get(key) or finished.get(key) != call.get(key) for key in ("id", "name", "namespace", "call_id")):
            raise RuntimeError("streaming function call identity changed")
        arguments = [event for event in events if event.get("type") in ("response.function_call_arguments.delta", "response.function_call_arguments.done")]
        if done[0].get("output_index") != emitted[0].get("output_index") or any(event.get("item_id") != call["id"] or event.get("output_index") != emitted[0].get("output_index") for event in arguments):
            raise RuntimeError("streaming arguments refer to a different function call")
        deltas = [event["delta"] for event in arguments if event["type"] == "response.function_call_arguments.delta"]
        args_done = [event["arguments"] for event in arguments if event["type"] == "response.function_call_arguments.done"]
        if not deltas or len(args_done) != 1 or json.loads(added.get("arguments", "") + "".join(deltas)) != {"a": 2, "b": 3} or json.loads(args_done[0]) != {"a": 2, "b": 3}:
            raise RuntimeError("streaming function arguments are missing or inconsistent")

    if args.check_namespaces:
        namespace = "mcp__probe"
        tools = [{"type": "namespace", "name": namespace, "description": "MCP compatibility probe", "tools": [TOOL]}]
        payload = dict(tool_payload, tools=tools, input="Call mcp__probe.add_numbers with a=2 and b=3. After receiving the tool result, reply with exactly that result.")
        namespaced_result = check("namespaced function calling", lambda: tool_check(payload, namespace))
        if namespaced_result:
            check("namespaced tool continuation", lambda: continuation_check(namespaced_result, payload))
        else:
            required_ok = False
            print("FAIL  namespaced tool continuation: namespaced function calling failed")
        check("namespaced tool streaming", lambda: namespaced_stream_check(payload, namespace))

    verdict = "DIRECT_READY" if required_ok else "INCOMPATIBLE"
    print(f"\n{verdict}")
    return 0 if required_ok else 1


if __name__ == "__main__":
    sys.exit(main())
