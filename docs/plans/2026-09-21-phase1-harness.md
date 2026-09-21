# Phase 1 local harness plan

1. Keep direct LLM routing and verify Responses API compatibility.
2. Run SearXNG, Search MCP, Playwright MCP, its auth proxy, and a deterministic browser fixture in Docker Compose.
3. Verify both MCP servers at protocol and real-tool-call level with a dependency-free Python client.
4. Verify local shell prerequisites, model concurrency, conversation state, and background-process behavior.
5. Provide one setup command and one aggregate verification command.
6. Defer Codex CLI installation and final cross-VPS integration to the target VPS.

