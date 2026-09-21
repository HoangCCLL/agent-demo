#!/usr/bin/env python3
import argparse
import os
import sys

from mcp_client import McpClient, result_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.getenv("SEARCH_MCP_URL", "http://127.0.0.1:18081/mcp"))
    parser.add_argument("--token-env", default="MCP_AUTH_TOKEN")
    args = parser.parse_args()
    token = os.getenv(args.token_env)
    if not token:
        print(f"FAIL  {args.token_env} is not set")
        return 1

    try:
        unauthenticated = McpClient(args.url, timeout=10)
        unauthenticated.initialize()
        print("FAIL  unauthenticated request was accepted")
        return 1
    except RuntimeError as error:
        if "HTTP 401" not in str(error):
            print(f"FAIL  authentication gate: {error}")
            return 1
        print("PASS  authentication gate")

    client = McpClient(args.url, token)
    try:
        info = client.initialize()
        print(f"PASS  initialize: {info.get('serverInfo', {}).get('name', 'unknown')}")
        tools = {tool["name"] for tool in client.list_tools()}
        required = {"searxng_web_search", "web_url_read"}
        missing = required - tools
        if missing:
            raise RuntimeError(f"missing tools: {', '.join(sorted(missing))}")
        print(f"PASS  tools/list: {len(tools)} tools")

        search = result_text(client.call_tool("searxng_web_search", {"query": "OpenAI Codex official documentation"}))
        if "http" not in search.lower():
            raise RuntimeError("search result contains no URL")
        print("PASS  real web search")

        page = result_text(client.call_tool("web_url_read", {"url": "https://example.com"}))
        if "example domain" not in page.lower():
            raise RuntimeError("URL reader returned unexpected content")
        print("PASS  URL reader")
    except Exception as error:
        print(f"FAIL  Search MCP: {error}")
        return 1
    finally:
        client.close()

    print("\nSEARCH_MCP_READY")
    return 0


if __name__ == "__main__":
    sys.exit(main())

