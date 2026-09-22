# Phase 1.1: local checks, VPS integration, and LAN clients

Run LiteLLM, Search, and Playwright Docker services once on a shared LAN VPS.
Codex sends Responses requests to LiteLLM, which bridges them to LM Studio
`0.4.25` at `/v1/chat/completions`. Search and Playwright MCP connections go
directly to their authenticated endpoints on that VPS. Clients need no Docker
or direct access to LM Studio. Plain `codex` uses the existing OpenAI
configuration; `codex -p openweight` selects the opt-in LAN model and MCP tools.

Choose the files for the machine you are preparing:

| Machine | Template / private file | Workflow |
|---|---|---|
| Local demo/test host | [`.env.example`](../.env.example) → `.env` | Section 1: setup and local verification |
| Shared service VPS | [`.env.vps.example`](../.env.vps.example) → `.env.vps` | Section 2: Docker services only |
| Codex workstation or client VPS | [`.env.client.example`](../.env.client.example) → `.env.client` | Sections 3–5: opt-in profile, acceptance, use |

One [Compose file](../compose.yaml) serves local testing and the shared VPS.
Setup does not imply acceptance: the VPS deployment below does not invoke the
test suite, LLM, or Codex. Run the separate verification commands when ready.

The final `19/20` gate measures this harness's functional coverage, not equal
model quality or 95% coverage of every OpenAI product feature. Local protocol
checks do not establish that Codex integration passed; run the VPS gates too.

Compatibility finding (2026-09-22): Codex CLI `0.155.1` sends MCP functions as
Responses `namespace` tools, which the direct Qwen/LM Studio endpoint rejected
with HTTP `400`. The Compose stack now includes LiteLLM `v1.98.0`, pinned as
`ghcr.io/berriai/litellm:v1.98.0`, with the namespace bridge fix. A model catalog
alone cannot fix this mismatch.
Deployment is not acceptance: run the authenticated model/namespace preflight
and the full client integration below before LAN rollout.

Historical verification on 2026-09-22, before adding the bridge: all 38 tests
passed, the old local pipeline returned `PHASE11_LOCAL_READY` and
`VISION_SUPPORTED`, and the separate namespace preflight failed with HTTP 400.
Those results do not accept the bridge, its authentication, or its image
support. Full client-VPS integration and multi-client rollout remain
unaccepted until the new checks pass.

## 1. Verify the service stack locally

On the local development machine, install Docker with Compose, Python 3.11+,
Bash, Git, curl, and Node.js. Run from the harness checkout:

```bash
bash scripts/setup.sh
```

This generates `.env` if absent (or fills only the exact secret placeholders in
an existing unedited copied template), starts containers, then runs the unit tests,
client primitives, authenticated Responses API and namespace preflight, real
Search/Playwright calls, and model runtime checks. Keep both
`MCP_BIND_ADDRESS=127.0.0.1` and `LITELLM_BIND_ADDRESS=127.0.0.1` for this
local-only stage. Set `LLM_BASE_URL=http://127.0.0.1:4000/v1` and make the local
`LLM_API_KEY` equal `LITELLM_MASTER_KEY`; `LMSTUDIO_API_KEY` is the separate
upstream credential. Use `LLM_CONTINUATION_MODE=input-history` for the bridge.
Existing non-placeholder `.env` values are never rewritten. An older or incomplete
`.env` still needs the new bridge settings described below before rerunning setup. To repeat verification
without pulling/restarting containers:

```bash
bash scripts/verify_all.sh
```

Require `PHASE11_LOCAL_READY`. Record `VISION_SUPPORTED` or `VISION_GAP`
separately: the final local marker alone does not promise image support.
Set the client image declaration to `true` only after the real screenshot
input gate passes against the same model server. Do not install Codex locally
just to run this stage; Codex integration follows on the client VPS.

## 2. Deploy the shared service VPS

Use a Linux VPS with Docker Engine and its Compose plugin, and OpenSSL for
generating secrets. Copy/clone the same harness revision, including `docker/`
(Compose bind-mounts those configuration and fixture files). Run all commands
below from that checkout. The operator must have permission to use Docker.
Python, Node.js, Codex, a GPU, and a host LiteLLM installation are not required
on this service host. LiteLLM, browser, and search dependencies run inside
containers. The VPS needs network access to the separate LM Studio server at
`192.168.110.16:1235`; that access is not required on Codex clients.

The existing stack contains:

| Compose service | Purpose | Published on the VPS |
|---|---|---|
| `litellm` | Authenticated Responses-to-Chat-Completions model bridge | LAN IP, TCP `4000` |
| `searxng` | Search backend | No |
| `search-mcp` | Authenticated Search MCP and URL reader | LAN IP, TCP `18081` |
| `playwright` | Headless browser MCP | No |
| `playwright-proxy` | Caddy bearer-auth proxy for Playwright | LAN IP, TCP `18082` |
| `test-site` | Nginx fixture for later browser acceptance | No |

Caddy is already included; do not install a second host proxy. Its current
configuration serves HTTP, not HTTPS. `/health` checks the proxy itself and
is intentionally public; MCP requests require the bearer token. Search MCP
enforces its own authentication. Model requests pass through LiteLLM separately;
MCP calls do not pass through LiteLLM. The model bridge uses the image pinned in
`compose.yaml`; do not replace it with a floating tag or a release that predates
the namespace fix.

### Prepare the private VPS environment

```bash
umask 077
test -e .env.vps || cp .env.vps.example .env.vps
chmod 600 .env.vps

# First output: MCP_AUTH_TOKEN. Second output: SEARXNG_SECRET.
openssl rand -hex 32
openssl rand -hex 32
# Third output: prepend sk- and use it as LITELLM_MASTER_KEY.
openssl rand -hex 32
```

Edit `.env.vps` before starting anything:

- Replace `192.0.2.20` in both bind addresses, allowed hosts, and allowed origins
  with the VPS's actual LAN interface IP. Do not bind `0.0.0.0` for this demo.
- Replace the MCP, SearXNG, and LiteLLM placeholder secrets with independent
  values. `LITELLM_MASTER_KEY` must start with `sk-`.
- If changing port `18081`, update both Search host/origin values too.
- Set `LITELLM_BIND_ADDRESS` to the VPS LAN IP and `LITELLM_PORT=4000`.
- Set `LMSTUDIO_BASE_URL=http://192.168.110.16:1235/v1`,
  `LMSTUDIO_API_KEY=not-needed` if upstream authentication is disabled (otherwise
  its real API key), and `LLM_MODEL=qwen/qwen3.8-27b` to match the served model.

The service file holds the bridge's upstream URL/key and gateway master key;
Codex profile settings belong on clients. Keep assignments literal: the commands
below source this trusted file. Exported shell values override Compose env files,
so the subshell loads this file explicitly and keeps these credentials out of
your parent shell. Give approved client operators the MCP URLs/token, gateway
URL, and gateway master key as their `LLM_API_KEY`. Never distribute
`SEARXNG_SECRET`, `LMSTUDIO_API_KEY`, or the private `.env.vps` file. The shared
gateway master key has admin access; this is a trusted-client demo, not
per-user isolation.

### Upgrade an existing deployment

Template changes do not update private `.env` or `.env.vps` files. Add
`LITELLM_BIND_ADDRESS`, `LITELLM_PORT`, `LITELLM_MASTER_KEY`,
`LMSTUDIO_BASE_URL`, `LMSTUDIO_API_KEY`, and `LLM_MODEL` using the values above;
for local `.env`, use loopback and set `LLM_BASE_URL`/`LLM_API_KEY` as in section 1.
Then run the deployment commands below. On every client, change
`LLM_BASE_URL` to `http://192.0.2.20:4000/v1` (with the real VPS LAN IP), set
`LLM_API_KEY` to the gateway master key and `LLM_CONTINUATION_MODE=input-history`,
and regenerate its opt-in profile as described in section 3. Updating the env
file alone does not replace the endpoint stored in an existing profile.

### Start services without running tests

Apply the firewall policy below before starting the LAN-bound stack. Then run:

```bash
(
  set -e
  set -a
  source .env.vps
  set +a
  docker compose --env-file .env.vps -f compose.yaml config --quiet
  docker compose --env-file .env.vps -f compose.yaml pull
  docker compose --env-file .env.vps -f compose.yaml up -d --wait --wait-timeout 180
  docker compose --env-file .env.vps -f compose.yaml ps
)
```

Use `--env-file .env.vps -f compose.yaml` for subsequent Compose commands too.
Do not use `scripts/setup.sh` on this service host: it is the local demo entry
point and automatically runs the full verification suite using `.env`.

`up --wait` only establishes Compose startup/health status, not working MCP
tools or model compatibility. Caddy health does not prove the browser backend
works; LiteLLM `/health/liveliness` does not prove LM Studio connectivity or
namespace support. No `PHASE11_LOCAL_READY` marker is expected here; run the
authenticated model and MCP checks from the client when ready (section 3).
The three published ports must use the explicit LAN IP; all other services
remain Docker-internal. Compose has a fixed project name, `codex-phase1`:
do not run the local and VPS configurations as two stacks on the same host.

### Network acceptance

Use an upstream firewall on the actual path to the service VPS: router,
hypervisor firewall, enforced VLAN policy, or security appliance. Allow TCP
`4000`, `18081`, and `18082` to the service IP only from approved client IPs; deny
those destination ports from every other source. Review existing broad
allows, and preserve/test SSH management access from a second session.

The enforcement point must also filter unapproved clients on the same LAN.
A router policy does not filter traffic that reaches the VPS directly on
the same Layer 2 network. Use an enforced VLAN boundary or hypervisor
filtering in that case. If no such boundary exists, configure and verify
Docker-aware host filtering, such as `DOCKER-USER` on the iptables backend,
before deployment; ordinary UFW `INPUT` rules are insufficient for
[Docker-published ports](https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw).

Allow the LM Studio port (`1235` in this example) from the service VPS; clients
need access only to the three published service ports.

From each approved client, run the authenticated model and remote MCP checks
in section 3. While services remain running, test from an
unapproved LAN client with `nc` installed:

```bash
nc -vz -w 5 192.0.2.20 4000
nc -vz -w 5 192.0.2.20 18081
nc -vz -w 5 192.0.2.20 18082
```

All three connections must fail or time out with nonzero exit status. HTTP `401`
after a successful TCP connection fails this network check; command-not-found
does not count as a pass. Record approved and unapproved source IPs/results.
Plain HTTP with bearer tokens is suitable only for the isolated demo LAN;
use TLS or a trusted encrypted overlay for untrusted network paths.

## 3. Prepare a client machine (workstation or client VPS)

Use a Bash environment with Python 3.11+, Git, curl, and Node.js. Ripgrep (`rg`)
is recommended; the primitive verifier also accepts grep. Install `gh` only
if GitHub workflows need it. Install Codex using the
[official CLI setup guide](https://learn.chatgpt.com/docs/codex/cli), or keep
the existing installation. This harness was checked with CLI `0.155.1`;
record your version and repeat acceptance after upgrades.

Copy/clone the same harness revision to each client. Do not run `setup.sh` or
install Docker, Caddy, SearXNG, Playwright, or Chromium here. Each client needs
network access to the gateway URL and both MCP URLs on the service VPS.
Only that VPS connects to the separate LM Studio endpoint. Use sections 3 and 4
from the harness checkout; day-to-day Codex use can start in any project directory.

### Keep OpenAI as the default

Configure/authenticate plain `codex` with OpenAI first using its normal login
flow. If OpenAI already works, keep the existing login/configuration.
The installer requires a base config file to exist. If the default setup has
not created one, create it without replacing any existing contents:

```bash
mkdir -p "${CODEX_HOME:-$HOME/.codex}"
touch "${CODEX_HOME:-$HOME/.codex}/config.toml"
```

An empty base config uses Codex defaults. Paths in this guide assume the
default `CODEX_HOME=~/.codex`; keep your own `CODEX_HOME` consistent if customized.
Preserve its authentication and unrelated plugins/MCP.
If an earlier demo made Qwen the default or placed
`web_search`/`playwright` LAN entries in this base file, first restore its
OpenAI model/provider and remove only those LAN server entries after backing
up the file. Installing a profile does not repair a previously changed
default. Do not leave `profile = "openweight"` as the base default.

Create a new client environment file from the template, then edit its values:

```bash
umask 077
test -e .env.client || cp .env.client.example .env.client
chmod 600 .env.client
```

Set `LLM_BASE_URL=http://192.0.2.20:4000/v1` with the actual service VPS LAN IP,
the MCP URLs/token, `LLM_MODEL=qwen/qwen3.8-27b` matching the gateway route, and
these explicit capability declarations:

- `LLM_CONTEXT_WINDOW`: the context limit configured on this server, in tokens.
  The template's `32768` is an example, not a claim about the model's maximum.
- `LLM_SUPPORTS_IMAGE`: `true` only after the real image gate passed; otherwise
  use `false`. Text-only operation is allowed, but affects the final score.
- `LLM_API_KEY`: required gateway authentication, equal to the service operator's
  `LITELLM_MASTER_KEY`. This is not `LMSTUDIO_API_KEY`; clients do not receive the
  backend secret. It is referenced through the environment, never serialized
  into profile/catalog.
- `LLM_CONTINUATION_MODE=input-history`: the checker replays the prior response
  output and function result in the next request, matching Codex's explicit
  conversation input. This bridge deployment has no database or Redis and
  does not promise hosted `previous_response_id`/conversation storage. The
  runtime check explicitly skips invalid hosted-conversation-state testing in
  this mode. Native endpoints can still be checked with `previous-response-id`.
- `CODEX_PROFILE`: `openweight` by default; change it for another named choice.

Environment files are trusted local input: shell commands below source them.
Keep them as literal `KEY=VALUE` assignments. Use a subshell for each profile
so values from one choice cannot persist into the next.

### Install the opt-in configuration (no runtime tests)

```bash
(
  set -e
  unset LLM_API_KEY
  set -a
  source .env.client
  set +a
  python3 scripts/install_codex_profile.py --env-file .env.client
)
```

The installer writes config/catalog only; it does not contact the model or
MCP services, install Codex, or change the base OpenAI configuration.
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
the separate config-only check below validates this with the installed binary
before any model calls. The profile file layout follows the
[official profile configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#profiles).

For an existing direct-to-LM-Studio profile, the simplest upgrade is to set
`CODEX_PROFILE=openweight-bridge` in `.env.client` and install a new choice:

```bash
(
  set -e
  unset LLM_API_KEY
  set -a
  source .env.client
  set +a
  python3 scripts/install_codex_profile.py --env-file .env.client --profile openweight-bridge
)
```

Alternatively, back up and move aside both the old `.config.toml` and matching
`catalogs/<profile>.models.json` before rerunning the installer with the old
name. Do not remove or replace the base `config.toml`; plain `codex` stays on
OpenAI. A new profile name must also be used for verification and launch.

### Verify explicitly when ready

These are separate checks, not part of installing the profile. The remote
check really searches the web and operates the browser on the MCP VPS.

First check gateway authentication and Responses namespace compatibility without
running Codex (Python 3.11+, Bash, and network access are sufficient):

```bash
bash scripts/verify_model_api.sh .env.client
```

This rejects missing/wrong gateway credentials and checks authenticated model
discovery, flat and namespaced function calls, streaming, and input-history
continuation through LiteLLM to LM Studio. Require `MODEL_API_READY` before
starting Codex acceptance. The nested checker's `DIRECT_READY` refers to the
selected API endpoint; it does not mean Codex connects directly to LM Studio.

```bash
(
  set -e
  unset LLM_API_KEY
  set -a
  source .env.client
  set +a
  python3 --version
  codex --version
  bash scripts/verify_client_primitives.sh
  python3 scripts/verify_codex_profile.py --env-file .env.client
  bash scripts/verify_remote_mcp.sh .env.client
)
```

Require `MODEL_API_READY`, `CODEX_PROFILE_READY`, and `REMOTE_MCP_READY` before
model-driven testing.
The profile marker verifies configuration only, not end-to-end capability.
Record the CLI version alongside integration results.

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
  bash scripts/verify_model_api.sh .env.client
  python3 -u verify_codex_vps.py --env-file .env.client --profile "$CODEX_PROFILE"
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

Launch the LAN choice explicitly from the project you want Codex to work on.
Replace `/absolute/path/to/harness/.env.client` below with your private client
env file's absolute path; do not copy the secrets into every project. The
subshell keeps credentials and provider selection out of your parent shell:

```bash
(
  set -e
  unset LLM_API_KEY
  set -a
  source /absolute/path/to/harness/.env.client
  set +a
  codex -p "$CODEX_PROFILE"
)
```

`CODEX_PROFILE` is read by the harness scripts; native Codex is selected with
`-p`. The last command is the environment-selected form, without a wrapper.
Setting `CODEX_PROFILE` alone does not switch plain `codex`. For a fixed
choice, `codex -p openweight` in the same loaded subshell is equivalent when
that is the installed profile name. Exiting it does not change the OpenAI
default; no repeated installation is needed when switching modes.

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

This shared MCP token and gateway master-key setup is a trusted-client demo,
not a per-user authorization boundary. Codex sessions and workspace
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

On the service VPS, from the harness checkout, stop containers while
preserving the cache volume:

```bash
docker compose --env-file .env.vps -f compose.yaml down
```

Do not use `docker compose down -v` unless deliberate cache deletion is wanted.
