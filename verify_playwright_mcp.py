#!/usr/bin/env python3
import argparse
import os
import sys

from mcp_client import McpClient, result_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("PLAYWRIGHT_MCP_URL", "http://127.0.0.1:18082/mcp"))
    parser.add_argument("--target", default="http://test-site/")
    parser.add_argument("--token-env", default="MCP_AUTH_TOKEN")
    args = parser.parse_args()
    token = os.getenv(args.token_env)
    if not token:
        print(f"FAIL  {args.token_env} is not set")
        return 1

    try:
        McpClient(args.url, timeout=10).initialize()
        print("FAIL  unauthenticated request was accepted")
        return 1
    except RuntimeError as error:
        if "HTTP 401" not in str(error):
            print(f"FAIL  authentication gate: {error}")
            return 1
        print("PASS  authentication gate")

    client = McpClient(args.url, token, timeout=90)
    try:
        info = client.initialize()
        print(f"PASS  initialize: {info.get('serverInfo', {}).get('name', 'unknown')}")
        tools = {tool["name"] for tool in client.list_tools()}
        required = {
            "browser_navigate", "browser_snapshot", "browser_type", "browser_click",
            "browser_evaluate", "browser_take_screenshot", "browser_close",
        }
        missing = required - tools
        if missing:
            raise RuntimeError(f"missing tools: {', '.join(sorted(missing))}")
        print(f"PASS  tools/list: {len(tools)} tools")

        result_text(client.call_tool("browser_navigate", {"url": args.target}))
        snapshot = result_text(client.call_tool("browser_snapshot"))
        if "MCP Demo" not in snapshot:
            raise RuntimeError("fixture page missing from accessibility snapshot")
        print("PASS  navigate + accessibility snapshot")

        dynamic = result_text(client.call_tool("browser_evaluate", {
            "function": "() => document.querySelector('#dynamic').textContent",
        }))
        if "JS_READY" not in dynamic:
            raise RuntimeError("JavaScript-rendered content missing")
        print("PASS  JavaScript-rendered page")

        result_text(client.call_tool("browser_type", {"target": "#name", "text": "Codex"}))
        result_text(client.call_tool("browser_click", {"target": "#submit"}))
        submitted = result_text(client.call_tool("browser_evaluate", {
            "function": "() => document.querySelector('#result').textContent",
        }))
        if "Hello Codex" not in submitted:
            raise RuntimeError("form interaction did not update the page")
        print("PASS  form input + click")

        result_text(client.call_tool("browser_take_screenshot", {"type": "png", "fullPage": True, "scale": "css"}))
        print("PASS  screenshot")
        download = result_text(client.call_tool("browser_click", {"target": "#download"}))
        if "download" not in download.lower():
            raise RuntimeError("download was not reported")
        print("PASS  download")
        result_text(client.call_tool("browser_close"))
    except Exception as error:
        print(f"FAIL  Playwright MCP: {error}")
        return 1
    finally:
        client.close()

    print("\nPLAYWRIGHT_MCP_READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())
