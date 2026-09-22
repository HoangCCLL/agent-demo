# Phase 1.1: local checks, VPS integration, and LAN clients

Run Search and Playwright Docker services once on a shared LAN VPS. Each Codex
client connects to those endpoints and the model server; clients need no
Docker. Plain `codex` uses the existing OpenAI configuration. The explicit
`codex -p openweight` profile selects the LAN model and MCP tools.

The final `19/20` gate measures this harness's functional coverage, not equal
model quality or 95% coverage of every OpenAI product feature. Local protocol
checks do not establish that Codex integration passed; run the VPS gates too.

Current compatibility finding: tested Codex CLI `0.155.1` sends MCP tools as
Responses `namespace` tools. The current Qwen endpoint accepts flat function
tools but rejects the namespace request with HTTP `400`. The scripts and
configuration are ready to test; this backend is not ready for LAN rollout
until it passes the namespace preflight below. A custom model catalog does
not fix that protocol mismatch. Use a backend with namespace support or a
separately verified compatible Codex version; no older version is assumed to
work, and these scripts do not automatically downgrade or insert a proxy.

Local verification on 2026-09-22: all 38 tests passed, including the installed
Codex profile/catalog parser check; the live service pipeline returned
`PHASE11_LOCAL_READY` and `VISION_SUPPORTED`. The separate namespace preflight
returned `INCOMPATIBLE` (exit 1, HTTP 400). Full client-VPS integration and
multi-client rollout remain unaccepted.

## 1. Verify the service stack locally

On the local development machine, install Docker with Compose, Python 3.11+,
Bash, Git, curl, and Node.js. Run from the harness checkout:

```bash
bash scripts/setup.sh
```

This generates `.env` if absent, starts containers, then runs the unit tests,
client primitives, Responses API, real Search/Playwright calls, and model
runtime checks. Keep `MCP_BIND_ADDRESS=127.0.0.1` for this local-only stage.
After changing an existing `.env`, rerun setup. To repeat verification without
pulling/restarting containers:

```bash
bash scripts/verify_all.sh
```

Require `PHASE11_LOCAL_READY`. Record `VISION_SUPPORTED` or `VISION_GAP`
separately: the final local marker alone does not promise image support.
Set the client image declaration to `true` only after the real screenshot
input gate passes against the same model server. Do not install Codex locally
just to run this stage; Codex integration follows on the client VPS.

## 2. Deploy the shared service VPS

Copy the same harness revision to the service VPS and install the local-stage
prerequisites. Copy `.env.example` to `.env` only if `.env` does not exist,
set mode `600`, and replace the endpoint and secret fields below.
`192.0.2.20` is documentation-only: use the VPS's explicit LAN address.

```dotenv
SEARCH_MCP_PORT=18081
PLAYWRIGHT_MCP_PORT=18082
MCP_BIND_ADDRESS=192.0.2.20
SEARCH_MCP_URL=http://192.0.2.20:18081/mcp
PLAYWRIGHT_MCP_URL=http://192.0.2.20:18082/mcp
SEARCH_MCP_ALLOWED_HOSTS=192.0.2.20:18081
SEARCH_MCP_ALLOWED_ORIGINS=http://192.0.2.20:18081
MCP_AUTH_TOKEN=replace-with-a-random-token
SEARXNG_SECRET=replace-with-a-random-secret

LLM_BASE_URL=http://192.168.110.16:1235/v1
LLM_MODEL=qwen/qwen3.8-27b
LLM_CONCURRENCY=3
RAG_CORPUS_PATH=
```

Generate two independent secrets with `python3 -c 'import secrets;
print(secrets.token_hex(32))'`. Distribute only the MCP token to approved
client operators. Never commit environment files containing secrets.

Apply the firewall policy below before starting the LAN-bound stack, then run:

```bash
chmod 600 .env
bash scripts/setup.sh
```

Require `PHASE11_LOCAL_READY` on this host too. Compose must publish only
Search MCP on `18081` and the Playwright authentication proxy on `18082`,
bound to the explicit LAN IP. SearXNG, Playwright itself, and `test-site`
remain Docker-internal.

### Network acceptance

Use an upstream firewall on the actual path to the service VPS: router,
hypervisor firewall, enforced VLAN policy, or security appliance. Allow TCP
`18081` and `18082` to the service IP only from approved client IPs; deny
those destination ports from every other source. Review existing broad
allows, and preserve/test SSH management access from a second session.

The enforcement point must also filter unapproved clients on the same LAN.
A router policy does not filter traffic that reaches the VPS directly on
the same Layer 2 network. Use an enforced VLAN boundary or hypervisor
filtering in that case. If no such boundary exists, configure and verify
Docker-aware host filtering, such as `DOCKER-USER` on the iptables backend,
before deployment; ordinary UFW `INPUT` rules are insufficient for
[Docker-published ports](https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw).

From each approved client, run the authenticated remote verifier in section 3
and require `REMOTE_MCP_READY`. While services remain running, test from an
unapproved LAN client with `nc` installed:

```bash
nc -vz -w 5 192.0.2.20 18081
nc -vz -w 5 192.0.2.20 18082
```

Both connections must fail or time out with nonzero exit status. HTTP `401`
after a successful TCP connection fails this network check; command-not-found
does not count as a pass. Record approved and unapproved source IPs/results.
Plain HTTP with bearer tokens is suitable only for the isolated demo LAN;
use TLS or a trusted encrypted overlay for untrusted network paths.

## 3. Prepare a client VPS

Install Python 3.11+, Bash, Git, ripgrep (`rg`), curl, Node.js, and Codex CLI.
Install `gh` only if GitHub workflows need it. Use the same harness revision;
this client does not run the service containers. Check shell prerequisites:

```bash
python3 --version
codex --version
bash scripts/verify_client_primitives.sh
```

Configure and authenticate plain `codex` with OpenAI first. The installer
requires `~/.codex/config.toml` to exist. Preserve its current authentication
and unrelated plugins/MCP. If an earlier demo made Qwen the default or placed
`web_search`/`playwright` LAN entries in this base file, first restore its
OpenAI model/provider and remove only those LAN server entries after backing
up the file. Installing a profile does not repair a previously changed
default. Do not leave `profile = "openweight"` as the base default.

Create a new client environment file from the template, then edit its values:

```bash
test -e .env.client || cp .env.client.example .env.client
chmod 600 .env.client
```

Set the LAN URLs/token, exact `LLM_MODEL` returned by `/v1/models`, and these
explicit capability declarations:

- `LLM_CONTEXT_WINDOW`: the context limit configured on this server, in tokens.
  The template's `32768` is an example, not a claim about the model's maximum.
- `LLM_SUPPORTS_IMAGE`: `true` only after the real image gate passed; otherwise
  use `false`. Text-only operation is allowed, but affects the final score.
- `LLM_API_KEY`: optional authentication for the LAN model endpoint. It is
  referenced through the environment, never serialized into profile/catalog.
- `CODEX_PROFILE`: `openweight` by default; change it for another named choice.

Environment files are trusted local input: shell commands below source them.
Keep them as literal `KEY=VALUE` assignments. Use a subshell for each profile
so values from one choice cannot persist into the next.

### Install and check the opt-in configuration

```bash
(
  set -e
  unset LLM_API_KEY
  set -a
  source .env.client
  set +a
  bash scripts/verify_remote_mcp.sh .env.client
  python3 scripts/install_codex_profile.py --env-file .env.client
  python3 scripts/verify_codex_profile.py --env-file .env.client
)
```

Require `REMOTE_MCP_READY` and `CODEX_PROFILE_READY` before model-driven testing.
The profile marker verifies configuration only, not end-to-end capability.
The installer supports `--profile NAME` and `--codex-home DIRECTORY` and writes:

```text
~/.codex/openweight.config.toml
~/.codex/catalogs/openweight.models.json
```

The catalog binds metadata to the exact model slug, including its declared
context/image capabilities and normal function tools. The profile selects
Responses API, disables native OpenAI web search, and configures Search and
Playwright MCP. Direct tool mode is declared; file edits use shell commands
because the catalog does not declare freeform `apply_patch` support. This does
not add capabilities to the model itself. Only the verified `medium` reasoning
setting is declared; low/high support is not assumed. Neither generated file
contains API keys or the MCP bearer token.

The installer preserves the base `config.toml`. Identical reruns are safe;
any different existing profile or catalog is refused, including one generated
by an older installer. Back up and deliberately relocate both files before
updating that choice, or use a new `--profile` name. The installed Codex
must support separate `<name>.config.toml` profiles and `model_catalog_json`;
the config-only check validates this with the installed binary before any
model calls. Record the CLI version alongside integration results.

`http://test-site/` is a Docker-internal fixture. Browser MCP can visit it
inside the service stack; the client host does not need a DNS entry, local
browser, or `/etc/hosts` change for it.

## 4. Run Codex integration on the client VPS

The operator runs this step on the target VPS, after section 3 passes:

```bash
(
  set -e
  unset LLM_API_KEY
  set -a
  source .env.client
  set +a
  python3 check_codex_api.py --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" \
    --api-key-env LLM_API_KEY --check-namespaces
  python3 -u verify_codex_vps.py --env-file .env.client --profile openweight
)
```

The namespace preflight must pass before full Codex integration. The VPS
verifier also runs it automatically and stops early if unsupported. A flat
function-call pass or `DIRECT_READY` without `--check-namespaces` is not enough
for this Codex version.

Use the matching `--profile` name for a nondefault choice, or omit the option
to use `CODEX_PROFILE`. The verifier uses a temporary Git repository and Codex
home, copies config/profile/catalog, and points each Codex subprocess at the
relocated catalog using a command-line override. Originals remain unchanged.
It checks that the selected model/provider/Responses endpoint match the
environment. It does not copy `auth.json`; LAN authentication comes from
environment values.

Accept only `PHASE11_VPS_READY`, at least `CAPABILITY_SCORE 19/20`, and all
critical gates passing. `PHASE11_VPS_NOT_READY` blocks rollout even if direct
MCP checks pass. Save the output and CLI/model versions. A loaded catalog or
missing-metadata warning disappearing alone does not prove MCP integration:
the Search and Playwright gates must contain real Codex tool-call evidence.
The read-only sandbox gate must pass on the target client OS too.

## 5. Use and roll out to LAN clients

OpenAI remains the ordinary command:

```bash
codex
```

Launch the LAN choice explicitly, keeping credentials out of shell arguments:

```bash
(
  set -e
  unset LLM_API_KEY
  set -a
  source .env.client
  set +a
  codex -p "$CODEX_PROFILE"
)
```

`CODEX_PROFILE` is read by the harness scripts; native Codex is selected with
`-p`. The last command is the environment-selected form, without a wrapper.

For every additional client, allow its IP at the firewall and repeat sections
3 and 4 with its own environment file. Require the remote, profile, and full
VPS gates for that client before use. Keep the service containers on the one
shared VPS.

For multiple choices on one client, use separate files such as `.env.qwen-a`
and `.env.qwen-b`, distinct `CODEX_PROFILE` values, and the correct model URL,
slug, context, and image declarations in each. Run the same installation and
verification commands in a fresh subshell sourcing the selected file. The
profiles may share the LAN MCP endpoints. Applying a Qwen catalog to another
model family requires that model's own capability checks; it is not a promise
of compatibility.

This shared-token setup is a trusted-client demo. Codex sessions and workspace
files stay on each client, while browser downloads/screenshots may live on
the MCP host; a path returned by remote Playwright is not a client-local file.
Browser sessions, output storage, and finite container resources do not give
an audited multi-user isolation guarantee. Start with sequential acceptance
tests, then test concurrent workloads before expanding use. Avoid shared
browser logins or sensitive cross-client data in this demo.

## Rollback

Choose plain `codex` to return to the base OpenAI setup. Retain generated
profiles/catalogs until any needed session review is complete; remove only
the named profile/catalog if deliberately uninstalling that choice.

On the service VPS, stop containers while preserving the cache volume:

```bash
docker compose down
```

Do not use `docker compose down -v` unless deliberate cache deletion is wanted.
