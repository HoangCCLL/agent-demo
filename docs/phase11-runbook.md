# Phase 1.1 LAN MCP runbook

Run Docker only on one shared LAN service VPS. Each Codex VPS is a client: it
uses the two authenticated HTTP MCP endpoints and does not run this stack.
`192.0.2.20` below is documentation-only; replace it with the service VPS's
explicit LAN address.

## 1. Configure and start the service VPS

Choose the service VPS LAN IP, then create `.env` in the repository. Keep this
file mode `600` and do not commit it.

```dotenv
SEARCH_MCP_PORT=18081
PLAYWRIGHT_MCP_PORT=18082
MCP_BIND_ADDRESS=192.0.2.20
SEARCH_MCP_URL=http://192.0.2.20:18081/mcp
PLAYWRIGHT_MCP_URL=http://192.0.2.20:18082/mcp
SEARCH_MCP_ALLOWED_HOSTS=192.0.2.20:18081
SEARCH_MCP_ALLOWED_ORIGINS=http://192.0.2.20:18081
MCP_AUTH_TOKEN=obtain-shared-demo-token-from-service-operator
SEARXNG_SECRET=generate-a-random-secret

LLM_BASE_URL=http://192.168.110.16:1235/v1
LLM_MODEL=qwen/qwen3.8-27b
LLM_CONCURRENCY=3
RAG_CORPUS_PATH=
```

Replace both placeholder secrets with random values; distribute the MCP token
only to approved client operators. Then run:

```bash
bash scripts/setup.sh
```

Accept the service host only when its final marker is `PHASE11_LOCAL_READY`.

## 2. Restrict the service VPS firewall

Allow the published authenticated proxy ports only from each Codex client IP.
For a client at `192.0.2.31`:

```bash
sudo ufw allow from 192.0.2.31 to 192.0.2.20 port 18081 proto tcp
sudo ufw allow from 192.0.2.31 to 192.0.2.20 port 18082 proto tcp
```

Repeat those two rules for every approved client IP; do not add a broad
subnet/public allow rule. The rendered Compose configuration must publish only
Search MCP on `18081` and the Playwright authentication proxy on `18082`.
SearXNG, Playwright itself, and `test-site` remain Docker-internal.

Plain HTTP with a bearer token is permitted only on an isolated demo LAN. Use
TLS or a trusted encrypted WireGuard/Tailscale overlay before crossing an
untrusted network.

## 3. Configure and verify each Codex client VPS

Create a mode-`600` client environment file, for example `.env.client`. It
contains no Docker settings:

```dotenv
MCP_AUTH_TOKEN=copy-the-shared-demo-token-from-the-service-vps
SEARCH_MCP_URL=http://192.0.2.20:18081/mcp
PLAYWRIGHT_MCP_URL=http://192.0.2.20:18082/mcp
LLM_BASE_URL=http://192.168.110.16:1235/v1
LLM_MODEL=qwen/qwen3.8-27b
LLM_CONCURRENCY=3
```

First verify both remote MCP endpoints from the client VPS:

```bash
bash scripts/verify_remote_mcp.sh .env.client
```

Require `REMOTE_MCP_READY`. `http://test-site/` must fail when requested from
the client host: it is Docker-internal. It succeeds only when Playwright MCP
requests it from inside the service stack.

Add these two remote servers to `~/.codex/config.toml` (merge with existing
configuration; do not replace its model-provider settings):

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

Before starting a new Codex session, export the same token:

```bash
set -a
source .env.client
set +a
export MCP_AUTH_TOKEN
```

Run the final client-only integration check from the harness checkout:

```bash
python3 verify_codex_vps.py --env-file .env.client
```

Accept only `PHASE11_VPS_READY` with `CAPABILITY_SCORE` of at least `19/20`.
A critical-gate failure must yield `PHASE11_VPS_NOT_READY`, even if the numeric
score is otherwise high.

## Rollback

On the service VPS, stop the stack without deleting named volumes:

```bash
docker compose down
```

Do not use `docker compose down -v`; retain the cache volume unless deliberate
data removal is required.
