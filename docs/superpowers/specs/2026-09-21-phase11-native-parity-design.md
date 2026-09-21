# Phase 1.1 Native Codex CLI Parity Design

## Goal

Demonstrate at least 95% of the stable, default Codex CLI experience with an
OpenAI-compatible open-weight model endpoint, while keeping the runtime small
and avoiding services that duplicate capabilities already provided by Codex
CLI and the client host.

Phase 1.1 extends verification coverage. It does not add GitHub MCP, RAG,
memory, hosted sandbox, LiteLLM, desktop automation, or an observability stack.

## Scope

Included:

- direct OpenAI-compatible Responses API model traffic;
- local shell, filesystem, Git, code execution, and background processes;
- Search MCP backed by SearXNG;
- browser automation through Playwright MCP;
- image input compatibility;
- Codex CLI non-interactive execution and JSONL events;
- session persistence and resume;
- repository instructions through `AGENTS.md`;
- sandbox enforcement;
- code review;
- MCP authentication, discovery, and end-to-end tool use;
- local service-host verification and final VPS integration verification.

Excluded from the 95% denominator:

- Codex Cloud tasks;
- ChatGPT desktop Computer Use;
- hosted Code Interpreter and hosted shell;
- Responses Conversations API as a service;
- experimental Codex CLI features;
- visual previews and annotations available only in desktop/web surfaces.

Codex CLI local sessions, shell tools, and filesystem operations are the
equivalents used for the excluded hosted API primitives.

## Architecture

Model traffic and tool traffic remain independent.

```text
Codex CLI VPS A ─┬──────── Responses API ───────► Open-weight LLM server
                 │
                 ├──────── Search MCP ─────────► LAN MCP service VPS
                 └──────── Playwright MCP ─────► LAN MCP service VPS

Codex CLI VPS B ─┬──────── Responses API ───────► Open-weight LLM server
                 │
                 ├──────── Search MCP ─────────► LAN MCP service VPS
                 └──────── Playwright MCP ─────► LAN MCP service VPS

LAN MCP service VPS
└── Docker Compose
    ├── SearXNG (internal only)
    ├── Search MCP (LAN port, bearer protected)
    ├── Playwright MCP (internal only)
    ├── Caddy auth proxy (LAN port, bearer protected)
    └── deterministic test site (internal only)
```

The Docker stack is installed once on the LAN MCP service VPS. A Codex client
must not install or run copies of these containers. It only needs network
access, MCP URLs, and the bearer token.

## LAN Deployment Contract

The service host binds MCP ports to one explicit LAN interface address. It must
not bind MCP ports to a public interface or publish SearXNG, Playwright, or the
test site directly.

Example client endpoints:

```text
http://192.168.110.20:18081/mcp  # Search MCP
http://192.168.110.20:18082/mcp  # Playwright MCP auth proxy
```

The actual address is configuration, not a hard-coded repository value.

Required LAN controls:

- bind to the service VPS LAN IP rather than `0.0.0.0` when possible;
- allow inbound MCP ports only from approved Codex client IPs/subnets;
- require bearer authentication on both MCP endpoints;
- keep tokens in environment variables and out of Git;
- reject unauthenticated MCP initialization;
- keep Docker-internal backends unexposed.

Plain HTTP with a bearer token is accepted only for an isolated demo LAN. Use
TLS or a trusted encrypted overlay such as WireGuard/Tailscale before the same
service crosses an untrusted network. A single shared demo token is acceptable
for Phase 1.1; per-client credentials and audit attribution are production
follow-up work.

## Codex Client Contract

Each Codex VPS configures remote Streamable HTTP servers:

```toml
[mcp_servers.web_search]
url = "http://192.168.110.20:18081/mcp"
bearer_token_env_var = "MCP_AUTH_TOKEN"
required = true

[mcp_servers.playwright]
url = "http://192.168.110.20:18082/mcp"
bearer_token_env_var = "MCP_AUTH_TOKEN"
required = true
startup_timeout_sec = 30
tool_timeout_sec = 120
```

The client exports `MCP_AUTH_TOKEN` before starting a new Codex process. No
Docker dependency is required on a client VPS for MCP use.

## Capability Measurement

The score uses 20 user-visible stable/default Codex CLI gates. Each gate is
worth one point. Phase 1.1 may claim at least 95% only when 19 or more gates
pass and no critical gate fails.

Critical gates:

1. Responses API basic response;
2. streaming;
3. function calling;
4. tool-result continuation;
5. reasoning response;
6. shell execution;
7. filesystem read/write;
8. Git workflow;
9. Search MCP end-to-end use;
10. Playwright MCP end-to-end use.

Additional gates:

11. long-context smoke test;
12. concurrent model requests;
13. stream cancellation;
14. image input;
15. background process and wait;
16. `codex exec --json` event stream;
17. session resume;
18. `AGENTS.md` instruction application;
19. read-only sandbox boundary;
20. code review workflow.

An unsupported image input is reported as a real gap. Phase 1.1 does not hide
it behind a lower-quality OCR or vision MCP workaround.

## Verification Flow

### Service-host local verification

`scripts/setup.sh` continues to start the Docker stack and run deterministic
checks. Phase 1.1 extends the aggregate verification with image input and a
capability summary.

Expected terminal condition:

```text
PHASE11_LOCAL_READY
```

This proves model/API compatibility, local primitives, MCP protocol behavior,
real search, and real browser automation. It does not claim that a remote Codex
client is configured correctly.

### Remote network verification

From each Codex client VPS, a dependency-free verifier calls the LAN MCP URLs
using the configured bearer token. It verifies authentication rejection,
initialization, tool discovery, search, URL reading, browser navigation, form
interaction, screenshot, and download.

The browser target remains `http://test-site/` because that name is resolved by
the Docker network from inside the Playwright container. A host-side failure to
resolve `test-site` is expected and is not a deployment failure.

### Codex CLI integration verification

A separate script is run manually on the client VPS after Codex CLI and its
configuration are installed. It must not install Codex or mutate global
configuration automatically.

The script uses an isolated temporary Git repository and checks:

- Codex diagnostics and configured MCP visibility;
- JSONL event output from `codex exec`;
- a deterministic file edit followed by a local test;
- capture and resume of a thread ID;
- repository guidance from `AGENTS.md`;
- failure to persist a requested write in read-only sandbox mode;
- code review execution;
- model-driven Search MCP and Playwright MCP tool calls.

Expected terminal condition:

```text
PHASE11_VPS_READY
```

The script does not perform GitHub mutations, package installation, privileged
host changes, or public network exposure.

## Error Handling and Reporting

Required capability failures stop the corresponding verifier and produce a
non-zero exit code. Optional API extensions such as named tool choice and
Responses background mode remain warnings because Codex CLI does not require
them for the agreed denominator.

Every check prints `PASS`, `WARN`, `SKIP`, or `FAIL`. The final report lists the
passed gate count and refuses the 95% claim when fewer than 19 gates pass or a
critical gate fails.

Remote failures are classified so the operator can distinguish:

- service unavailable or firewall rejection;
- bearer token missing or rejected;
- MCP initialization/tool discovery failure;
- Docker-internal browser DNS failure;
- model failure to select or continue a tool call;
- Codex client configuration/session failure.

## Resource and Concurrency Boundaries

The existing Docker limits remain the Phase 1.1 baseline. The shared
Playwright service is intended for demo-scale concurrency. Multiple clients may
connect, but high parallel browser load, per-tenant quotas, and horizontal
scaling are outside this phase.

Search and browser sessions must not depend on client-local Docker DNS or
filesystem paths. Browser artifacts remain ephemeral inside the service stack.

## Deferred Work

- RAG after a real corpus and retrieval evaluation set exist;
- LiteLLM when multi-model routing or a separate vision model is required;
- OpenSandbox when untrusted execution must be isolated from the Codex host;
- GitHub MCP when typed permissions provide value beyond `gh`;
- per-client MCP tokens, TLS automation, rate limiting, and centralized audit;
- production scaling for concurrent Playwright sessions;
- OpenTelemetry dashboards after operational demand exists.

## Acceptance Criteria

Phase 1.1 is accepted when:

1. the service-host local suite ends with `PHASE11_LOCAL_READY`;
2. the client VPS can reach both bearer-protected LAN MCP endpoints;
3. the manual Codex CLI suite ends with `PHASE11_VPS_READY`;
4. at least 19 of 20 capability gates pass;
5. all critical gates pass;
6. no MCP backend other than the authenticated proxy endpoints is reachable
   from a Codex client or a public interface;
7. no additional optional service is required for the passing baseline.
