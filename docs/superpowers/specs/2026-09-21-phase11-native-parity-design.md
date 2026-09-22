# Phase 1.1 Native Codex CLI Parity Design

## Goal

Demonstrate at least 95% of the stable, default Codex CLI experience with an
OpenAI-compatible open-weight model endpoint, while keeping the runtime small
and avoiding services that duplicate capabilities already provided by Codex
CLI and the client host.

Phase 1.1 extends verification coverage. The approved 2026-09-22 update adds
LiteLLM to translate Codex Responses namespace tools for LM Studio. It does
not add GitHub MCP, RAG, memory, hosted sandbox, desktop automation, or an
observability stack.

## Scope

Included:

- OpenAI-compatible Responses API model traffic through a LiteLLM bridge;
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
Codex CLI VPS A ─┬──────── Responses API ───────► LiteLLM on LAN service VPS
                 │
                 ├──────── Search MCP ─────────► LAN MCP service VPS
                 └──────── Playwright MCP ─────► LAN MCP service VPS

Codex CLI VPS B ─┬──────── Responses API ───────► LiteLLM on LAN service VPS
                 │
                 ├──────── Search MCP ─────────► LAN MCP service VPS
                 └──────── Playwright MCP ─────► LAN MCP service VPS

LAN service VPS
└── Docker Compose
    ├── LiteLLM (LAN port, bearer protected) ──► LM Studio /v1/chat/completions
    ├── SearXNG (internal only)
    ├── Search MCP (LAN port, bearer protected)
    ├── Playwright MCP (internal only)
    ├── Caddy auth proxy (LAN port, bearer protected)
    └── deterministic test site (internal only)
```

The same `compose.yaml` runs the whole stack once on the LAN service VPS.
Clients need the gateway and MCP URLs and their bearer credentials, with no
Docker or direct LM Studio access. Only the service VPS needs a route to the
LM Studio host. Python is not required on the service host; LiteLLM runs inside
its container.

### Model bridge contract

Pin the bridge image to `ghcr.io/berriai/litellm:v1.98.0`, which contains the
Responses namespace fix. Route `LLM_MODEL=qwen/qwen3.8-27b` through the OpenAI
Chat Completions provider, with `use_chat_completions_api: true`, to LM Studio
`0.4.25` at `LMSTUDIO_BASE_URL=http://192.168.110.16:1235/v1`. A floating or older
image is not an accepted substitute. MCP remains direct to Search/Playwright;
LiteLLM handles only model traffic.

The bridge listens on `LITELLM_PORT=4000`, with `LITELLM_BIND_ADDRESS=127.0.0.1`
for local tests or the explicit VPS LAN IP for shared use. The service owns
`LITELLM_MASTER_KEY=sk-...` and `LMSTUDIO_API_KEY` (`not-needed` if upstream auth
is disabled). Clients use `LLM_BASE_URL=http://192.0.2.20:4000/v1` with the real
VPS address and `LLM_API_KEY` equal to the gateway master key. They do not
receive the LM Studio key. The combined local `.env` must also set its
`LLM_API_KEY` equal to `LITELLM_MASTER_KEY`.

No database or Redis is added. `LLM_CONTINUATION_MODE=input-history` makes the
checks replay prior response output plus tool results, as Codex does with
explicit conversation input. Hosted `previous_response_id` or Conversations
API state is not part of this bridge contract; invalid hosted-state runtime
testing is explicitly skipped in this mode. The native direct checker keeps
`previous-response-id` as a selectable default for other endpoints. These
limits must be reflected in output; no hosted state-support claim follows
from input-history continuation passing.

Plain `codex` keeps the user's normal OpenAI configuration. The open-weight
model and LAN MCP tools live in an opt-in Codex profile selected with
`codex -p <profile>`. Multiple profile files may select different LAN model
endpoints while sharing the MCP service.

## LAN Deployment Contract

The service host binds the LiteLLM and MCP ports to one explicit LAN interface
address. It must not bind them to a public interface or publish SearXNG,
Playwright, or the test site directly.

Example client endpoints:

```text
http://192.0.2.20:4000/v1    # LiteLLM Responses gateway
http://192.0.2.20:18081/mcp  # Search MCP
http://192.0.2.20:18082/mcp  # Playwright MCP auth proxy
```

The actual address is configuration, not a hard-coded repository value.

Required LAN controls:

- bind to the service VPS LAN IP rather than `0.0.0.0` when possible;
- allow inbound ports `4000`, `18081`, and `18082` only from approved Codex clients;
- require bearer authentication on the gateway and both MCP endpoints;
- keep tokens in environment variables and out of Git;
- reject missing/wrong gateway authentication and unauthenticated MCP initialization;
- keep Docker-internal backends unexposed.

Plain HTTP with a bearer token is accepted only for an isolated demo LAN. Use
TLS or a trusted encrypted overlay such as WireGuard/Tailscale before the same
service crosses an untrusted network. A shared MCP token and shared LiteLLM
master key are accepted for trusted demo clients only; the master key is not
per-user isolation. Keep the SearXNG secret and LM Studio backend key on the
service host. Per-client credentials and audit attribution are production
follow-up work. Firewall policy must cover Docker-published ports and same-LAN
traffic, and only the service VPS needs access to the LM Studio port.

## Codex Client Contract

Update (2026-09-22): `scripts/install_codex_profile.py` generates the opt-in
profile plus `catalogs/<profile>.models.json`. It requires explicit serving
context (`LLM_CONTEXT_WINDOW`) and image capability (`LLM_SUPPORTS_IMAGE`).
The catalog uses the real `LLM_MODEL` slug, direct tools, and medium reasoning;
file editing stays available through shell commands. The installer preserves
the OpenAI base config and refuses differing existing output files.

The observed Codex 0.155.1 wire format nests MCP functions in Responses
`namespace` tools. The direct LM Studio endpoint rejected this format with
HTTP 400; the LiteLLM bridge addresses that mismatch. The model catalog does
not itself provide API compatibility. Run
`bash scripts/verify_model_api.sh .env.client` before CLI integration and
require `MODEL_API_READY`. This check needs no Codex and verifies missing/wrong
credential rejection, model discovery, flat and namespaced functions,
streaming, and input-history continuation. The checker's `DIRECT_READY` means
the selected API endpoint passed; it does not mean direct LM Studio traffic.
The current operational steps are in `docs/phase11-runbook.md`.

Existing `.env`/`.env.vps` files are not automatically migrated. Operators must
add the service bridge variables, update each client endpoint/key and
continuation mode, then regenerate the opt-in profile. The installer refuses
differing existing profiles/catalogs: back up and move both aside, or select a
new name such as `--profile openweight-bridge` and use it consistently. Plain
`codex` continues to use the base OpenAI configuration.

Each Codex VPS configures its open-weight profile with the model provider and
remote Streamable HTTP servers:

```toml
[mcp_servers.web_search]
url = "http://192.0.2.20:18081/mcp"
bearer_token_env_var = "MCP_AUTH_TOKEN"
required = true

[mcp_servers.playwright]
url = "http://192.0.2.20:18082/mcp"
bearer_token_env_var = "MCP_AUTH_TOKEN"
required = true
startup_timeout_sec = 30
tool_timeout_sec = 120
```

The client exports `MCP_AUTH_TOKEN` before starting `codex -p <profile>`. MCP
entries are absent from the base OpenAI config, so plain `codex` does not
connect to the LAN MCP services. No Docker dependency is required on a client
VPS for MCP use.

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
19. read-only sandbox boundary (also mandatory/critical);
20. code review workflow.

An unsupported image input is reported as a real gap. Phase 1.1 does not hide
it behind a lower-quality OCR or vision MCP workaround.
The read-only sandbox gate is mandatory regardless of the numeric score.

## Verification Flow

### Service-host local verification

`scripts/setup.sh` continues to start the Docker stack and run the local
verification suite. The aggregate verification includes gateway authentication,
Responses namespaces, explicit input-history continuation, image input, and
a capability summary.

Expected terminal condition:

```text
PHASE11_LOCAL_READY
```

This proves model/API compatibility, local primitives, MCP protocol behavior,
real search, and real browser automation. It does not claim that a remote Codex
client is configured correctly.

Historical local passes from before this bridge was added do not accept the
new gateway, authentication, namespace handling, or image path. Rerun the
relevant checks against the deployed bridge and record its versions.

### Service VPS deployment

Deployment consists of `docker compose --env-file .env.vps -f compose.yaml`
with `config --quiet`, `pull`, and `up -d --wait --wait-timeout 180`. It does not
automatically run local tests, contact the model, or start Codex. Do not use
the local `setup.sh` entry point on this host. Compose health and LiteLLM
`/health/liveliness` establish process readiness, not upstream LM Studio
reachability, tool compatibility, or acceptance.

### Remote network verification

From each Codex client VPS, a dependency-free verifier calls the LAN MCP URLs
using the configured bearer token. It verifies authentication rejection,
initialization, tool discovery, search, URL reading, browser navigation, form
interaction, screenshot, and download.

Run `bash scripts/verify_model_api.sh .env.client` separately against the model
gateway and require `MODEL_API_READY`. Run the MCP verifier and require
`REMOTE_MCP_READY`. Neither test needs Codex to be installed. An unapproved
LAN source must fail TCP connection attempts to all three published ports.

The browser target remains `http://test-site/` because that name is resolved by
the Docker network from inside the Playwright container. A host-side failure to
resolve `test-site` is expected and is not a deployment failure.

### Codex CLI integration verification

A separate script is run manually on the client VPS after Codex CLI and an
opt-in profile are installed. It must not install Codex or mutate the base
OpenAI configuration automatically.

The script uses an isolated temporary Git repository and checks:

- Codex diagnostics and configured MCP visibility;
- JSONL event output from `codex exec`;
- a deterministic file edit followed by a local test;
- capture and resume of a thread ID;
- repository guidance from `AGENTS.md`;
- failure to persist a requested write in read-only sandbox mode;
- code review execution;
- model-driven Search MCP and Playwright MCP tool calls.

It first validates the selected profile and relocated catalog with the installed
Codex binary, then checks namespaced function calls and continuation. A namespace
failure stops before agent turns. This gate is additional compatibility evidence,
not a new scored capability. The catalog is copied to the temporary Codex home
and selected using a per-command path override; originals stay unchanged.

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
- multi-model routing or a separate vision model when required;
- OpenSandbox when untrusted execution must be isolated from the Codex host;
- GitHub MCP when typed permissions provide value beyond `gh`;
- per-client MCP tokens, TLS automation, rate limiting, and centralized audit;
- production scaling for concurrent Playwright sessions;
- OpenTelemetry dashboards after operational demand exists.

## Acceptance Criteria

Phase 1.1 is accepted when:

1. the service-host local suite ends with `PHASE11_LOCAL_READY`;
2. the client VPS passes `MODEL_API_READY` and reaches both bearer-protected
   LAN MCP endpoints;
3. the manual Codex CLI suite ends with `PHASE11_VPS_READY`;
4. at least 19 of 20 capability gates pass;
5. all critical gates pass;
6. no MCP backend other than the authenticated proxy endpoints is reachable
   from a Codex client or a public interface;
7. no additional optional service is required for the passing baseline.
