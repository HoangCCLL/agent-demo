# Phase 1 local harness plan

1. Keep direct LLM routing and verify Responses API compatibility.
2. Run SearXNG, Search MCP, Playwright MCP, its auth proxy, and a deterministic browser fixture in Docker Compose.
3. Verify both MCP servers at protocol and real-tool-call level with a dependency-free Python client.
4. Verify local shell prerequisites, model concurrency, conversation state, and background-process behavior.
5. Provide one setup command and one aggregate verification command.
6. Defer Codex CLI installation and final cross-VPS integration to the target VPS.

## Phase 1.1 status

MCP services are shared once over the LAN from the service VPS; Codex CLI
remains the client-only final integration step on each Codex VPS. See the
[Phase 1.1 design](../superpowers/specs/2026-09-21-phase11-native-parity-design.md)
and the [LAN deployment runbook](../phase11-runbook.md).
