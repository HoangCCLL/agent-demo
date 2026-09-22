#!/usr/bin/env python3
"""Runtime checks beyond the minimum Codex Responses API contract."""

import argparse
import base64
import concurrent.futures
import json
import os
import sys
import urllib.request
from pathlib import Path

from check_codex_api import output_text, request_json


def image_input(path):
    encoded = base64.b64encode(Path(path).read_bytes()).decode()
    return [
        {
            "type": "input_text",
            "text": "Transcribe the main heading in this image. Reply with only that heading.",
        },
        {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"},
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--continuation-mode", choices=("previous-response-id", "input-history"),
                        default="previous-response-id",
                        help="input-history does not require hosted Responses state")
    parser.add_argument("--vision-image")
    parser.add_argument("--vision-expected", default="VISION_7F31")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    api_key = os.getenv(args.api_key_env)
    failed = False

    def post(payload):
        _, _, body = request_json(base_url, "/responses", args.timeout, api_key, payload)
        return body

    def required(label, action):
        nonlocal failed
        try:
            detail = action()
            print(f"PASS  {label}" + (f": {detail}" if detail else ""))
        except Exception as error:
            failed = True
            print(f"FAIL  {label}: {error}")

    def optional(label, action):
        try:
            detail = action()
            print(f"PASS  {label}" + (f": {detail}" if detail else ""))
        except Exception as error:
            print(f"WARN  {label}: {error}")

    def reasoning_check():
        body = post({"model": args.model, "input": "Reply with exactly REASON_OK", "max_output_tokens": 192})
        kinds = {item.get("type") for item in body.get("output", [])}
        if "reasoning" not in kinds or output_text(body).strip() != "REASON_OK":
            raise RuntimeError(f"unexpected output types/text: {kinds}, {output_text(body)!r}")
        return f"{body.get('usage', {}).get('total_tokens', '?')} tokens"

    required("reasoning response", reasoning_check)

    vision_supported = False

    def vision_check():
        nonlocal vision_supported
        if not args.vision_image:
            raise RuntimeError("no --vision-image supplied")
        body = post({
            "model": args.model,
            "input": [{"role": "user", "content": image_input(args.vision_image)}],
            "max_output_tokens": 128,
        })
        actual = output_text(body).strip()
        if actual != args.vision_expected:
            raise RuntimeError(f"expected {args.vision_expected!r}, got {actual!r}")
        vision_supported = True
        return args.vision_expected

    optional("image input", vision_check)

    def context_check():
        marker = "CONTEXT_MARKER_7F31"
        prompt = ("irrelevant-context " * 1500) + f"\nRemember {marker}. Reply with exactly {marker}."
        body = post({"model": args.model, "input": prompt, "max_output_tokens": 192})
        if output_text(body).strip() != marker:
            raise RuntimeError(f"marker not retained: {output_text(body)!r}")
        return f"{len(prompt)} input characters"

    required("long-context smoke", context_check)

    def concurrent_check():
        count = max(1, min(args.concurrency, 8))

        def one(index):
            expected = f"CONCURRENT_{index}"
            body = post({"model": args.model, "input": f"Reply with exactly {expected}", "max_output_tokens": 128})
            actual = output_text(body).strip()
            if actual != expected:
                raise RuntimeError(f"request {index}: expected {expected}, got {actual!r}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            list(pool.map(one, range(count)))
        return f"{count} simultaneous requests"

    required("concurrency", concurrent_check)

    def invalid_previous_id_check():
        try:
            post({
                "model": args.model,
                "previous_response_id": "resp_phase1_does_not_exist",
                "input": "hello",
                "max_output_tokens": 32,
            })
        except RuntimeError as error:
            if "HTTP 4" in str(error):
                return "rejected cleanly"
            raise
        raise RuntimeError("unknown previous_response_id was accepted")

    if args.continuation_mode == "previous-response-id":
        required("invalid conversation state", invalid_previous_id_check)
    else:
        print("SKIP  server-stored conversation state: input-history mode; no hosted state claimed")

    tool = {
        "type": "function",
        "name": "record_value",
        "description": "Record one integer value. Call once for each requested value.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "strict": True,
    }

    def parallel_tools_check():
        body = post({
            "model": args.model,
            "input": "Call record_value once with 11 and once with 22. Make both calls now.",
            "tools": [tool],
            "tool_choice": "required",
            "parallel_tool_calls": True,
            "max_output_tokens": 512,
        })
        calls = [item for item in body.get("output", []) if item.get("type") == "function_call"]
        values = sorted(json.loads(item["arguments"])["value"] for item in calls)
        if values != [11, 22]:
            raise RuntimeError(f"expected parallel values [11, 22], got {values}")
        return "two function calls"

    optional("parallel tool-call quality", parallel_tools_check)

    def background_check():
        body = post({
            "model": args.model,
            "input": "Reply with exactly BACKGROUND_OK",
            "background": True,
            "max_output_tokens": 128,
        })
        if body.get("object") != "response" or body.get("status") not in {"queued", "in_progress", "completed"}:
            raise RuntimeError(f"unexpected background response: {body.get('status')}")
        return body["status"]

    optional("Responses background mode", background_check)

    def cancellation_check():
        payload = json.dumps({
            "model": args.model,
            "input": "Write a very long numbered list.",
            "stream": True,
            "max_output_tokens": 2048,
        }).encode()
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(f"{base_url}/responses", payload, headers)
        response = urllib.request.urlopen(request, timeout=args.timeout)
        try:
            if not response.read(512):
                raise RuntimeError("stream produced no bytes")
        finally:
            response.close()
        return "client closed active stream"

    required("stream cancellation", cancellation_check)

    print("VISION_SUPPORTED" if vision_supported else "VISION_GAP")
    print("\nMODEL_RUNTIME_READY" if not failed else "\nMODEL_RUNTIME_FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
