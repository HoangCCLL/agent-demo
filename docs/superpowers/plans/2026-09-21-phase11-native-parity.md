# Phase 1.1 Native Codex CLI Parity Implementation Plan

> Operator handoff: use [the current VPS/LAN runbook](../../phase11-runbook.md).
> The 2026-09-22 checks add opt-in profiles, explicit model catalogs, mandatory
> sandbox verification, and a namespace preflight. The tested Qwen backend
> currently rejects namespace tools; the original flat-function gate below
> alone does not establish compatibility with Codex CLI 0.155.1.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing harness to prove at least 19 of 20 stable/default Codex CLI capabilities while hosting Search and Playwright MCP once on a shared LAN service VPS.

**Architecture:** Keep the current Docker stack on one LAN service host and make its authenticated MCP URLs configurable for remote Codex clients. Reuse the existing dependency-free Python checks, add a real screenshot-to-vision round trip, and add a separate Codex CLI verifier that runs only when the operator invokes it on the client VPS.

**Tech Stack:** Docker Compose, Bash, Python 3 standard library, Codex CLI, Streamable HTTP MCP, SearXNG, Playwright MCP, Caddy.

**Spec:** `docs/superpowers/specs/2026-09-21-phase11-native-parity-design.md`

## Global Constraints

- Do not add a new container, Python dependency, package manager, or MCP server.
- Keep model traffic independent from MCP traffic.
- Bind MCP ports to an explicit LAN IP for shared deployment; never expose Docker-internal backends.
- Require bearer authentication on both MCP endpoints.
- Do not install Codex CLI or mutate its global config from verification scripts.
- Keep all Codex-generated test changes inside a temporary Git repository.
- A final parity claim requires at least 19 of 20 gates and every critical gate.
- Unsupported vision is a visible gap, not a hidden OCR/MCP fallback.

---

## File Map

- Modify `.env.example`: document local defaults and LAN client endpoint variables.
- Modify `compose.yaml`: make the Search MCP allowed origin configurable.
- Create `scripts/verify_client_primitives.sh`: host primitives without a Docker requirement.
- Modify `scripts/verify_local.sh`: reuse client primitive checks, then verify Docker separately.
- Create `scripts/verify_remote_mcp.sh`: run existing MCP checks against LAN URLs without Docker.
- Modify `verify_playwright_mcp.py`: optionally save the real browser screenshot used by the vision gate.
- Modify `verify_model_runtime.py`: send an `input_image` Responses request and report vision support.
- Create `test_verify_model_runtime.py`: validate the multimodal payload and image encoding path.
- Create `verify_codex_vps.py`: execute and score all 20 gates from a configured Codex client VPS.
- Create `test_verify_codex_vps.py`: test JSONL parsing and readiness rules without an LLM.
- Modify `scripts/verify_all.sh`: use configured MCP URLs and produce `PHASE11_LOCAL_READY`.
- Create `docs/phase11-runbook.md`: exact LAN service-host and Codex client commands.

---

### Task 1: Make the existing MCP stack LAN-addressable

**Files:**

- Modify: `.env.example`
- Modify: `compose.yaml`
- Create: `scripts/verify_client_primitives.sh`
- Modify: `scripts/verify_local.sh`
- Create: `scripts/verify_remote_mcp.sh`

**Interfaces:**

- Consumes: `MCP_AUTH_TOKEN`, `SEARCH_MCP_URL`, and `PLAYWRIGHT_MCP_URL` from an operator-supplied env file.
- Produces: `REMOTE_MCP_READY` after both existing MCP verifiers pass from a non-Docker client.

- [ ] **Step 1: Add a failing LAN configuration check**

Run this before editing `compose.yaml`:

```bash
SEARCH_MCP_ALLOWED_ORIGINS=http://192.0.2.20:18081 \
  docker compose config | rg '192\.0\.2\.20:18081'
```

Expected: FAIL because `MCP_HTTP_ALLOWED_ORIGINS` is currently hard-coded to loopback.

- [ ] **Step 2: Make Search MCP origin and verifier URLs configurable**

Change the Search MCP environment entry in `compose.yaml` to:

```yaml
MCP_HTTP_ALLOWED_ORIGINS: ${SEARCH_MCP_ALLOWED_ORIGINS:-http://127.0.0.1:18081}
```

Add these local defaults to `.env.example` immediately after the MCP ports:

```dotenv
# Bind an explicit LAN IP on the shared MCP VPS; keep loopback for local-only use.
MCP_BIND_ADDRESS=127.0.0.1
SEARCH_MCP_URL=http://127.0.0.1:18081/mcp
PLAYWRIGHT_MCP_URL=http://127.0.0.1:18082/mcp
SEARCH_MCP_ALLOWED_HOSTS=127.0.0.1:18081,localhost:18081
SEARCH_MCP_ALLOWED_ORIGINS=http://127.0.0.1:18081
```

Remove the duplicate existing `MCP_BIND_ADDRESS` and `SEARCH_MCP_ALLOWED_HOSTS` entries rather than keeping two definitions.

- [ ] **Step 3: Extract client primitives from the Docker check**

Create `scripts/verify_client_primitives.sh` with the shell, filesystem, Git, Python, Node, background process, and optional `gh` checks currently in `scripts/verify_local.sh`. Use this exact boundary:

```bash
#!/usr/bin/env bash
set -euo pipefail

for command_name in git grep curl python3 node; do
  command -v "$command_name" >/dev/null || { echo "FAIL  missing $command_name"; exit 1; }
done
echo "PASS  client shell commands"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
printf 'needle\n' > "$work_dir/sample.txt"

if command -v rg >/dev/null; then
  rg -q needle "$work_dir/sample.txt"
  search_tool=rg
else
  grep -q needle "$work_dir/sample.txt"
  search_tool="grep (rg optional)"
fi

python3 -c "from pathlib import Path; assert Path('$work_dir/sample.txt').read_text() == 'needle\\n'"
node -e "const fs=require('fs'); if(!fs.readFileSync('$work_dir/sample.txt','utf8').includes('needle')) process.exit(1)"
echo "PASS  filesystem + $search_tool + Python + Node"

git -C "$work_dir" init -q
git -C "$work_dir" add sample.txt
git -C "$work_dir" -c user.name=Verifier -c user.email=verifier@example.invalid commit -qm initial
test -z "$(git -C "$work_dir" status --porcelain)"
echo "PASS  Git workflow"

(sleep 0.1; printf done > "$work_dir/background") &
job_pid=$!
wait "$job_pid"
test "$(cat "$work_dir/background")" = done
echo "PASS  background process + wait"

if command -v gh >/dev/null; then
  echo "PASS  GitHub CLI installed"
else
  echo "WARN  GitHub CLI not installed; add it only when GitHub workflows need it"
fi
```

Replace the duplicated block in `scripts/verify_local.sh` with:

```bash
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$repo_dir/scripts/verify_client_primitives.sh"

command -v docker >/dev/null || { echo "FAIL  missing docker"; exit 1; }
docker version >/dev/null
docker compose version >/dev/null
echo "PASS  Docker CLI"
```

- [ ] **Step 4: Add the non-Docker remote MCP verifier**

Create `scripts/verify_remote_mcp.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
env_file="${1:-.env}"
[[ -f "$env_file" ]] || { echo "FAIL  missing env file: $env_file"; exit 1; }

set -a
source "$env_file"
set +a

: "${MCP_AUTH_TOKEN:?MCP_AUTH_TOKEN is required}"
: "${SEARCH_MCP_URL:?SEARCH_MCP_URL is required}"
: "${PLAYWRIGHT_MCP_URL:?PLAYWRIGHT_MCP_URL is required}"

python3 verify_search_mcp.py --url "$SEARCH_MCP_URL"
python3 verify_playwright_mcp.py --url "$PLAYWRIGHT_MCP_URL"
echo
echo "REMOTE_MCP_READY"
```

Mark both new scripts executable.

- [ ] **Step 5: Verify Task 1**

Run:

```bash
bash -n scripts/verify_client_primitives.sh scripts/verify_local.sh scripts/verify_remote_mcp.sh
SEARCH_MCP_ALLOWED_ORIGINS=http://192.0.2.20:18081 \
  docker compose config | rg '192\.0\.2\.20:18081'
python3 -m unittest -v
```

Expected: shell syntax passes, rendered Compose contains the LAN origin, and existing unit tests pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add .env.example compose.yaml scripts/verify_client_primitives.sh scripts/verify_local.sh scripts/verify_remote_mcp.sh
git commit -m "feat: support shared LAN MCP services"
```

---

### Task 2: Add a real browser-screenshot vision gate

**Files:**

- Modify: `verify_playwright_mcp.py`
- Modify: `verify_model_runtime.py`
- Create: `test_verify_model_runtime.py`
- Modify: `scripts/verify_all.sh`

**Interfaces:**

- Consumes: PNG screenshot written by `verify_playwright_mcp.py --screenshot-output PATH`.
- Produces: `PASS image input` or `WARN image input`, plus `VISION_SUPPORTED` or `VISION_GAP`.

- [ ] **Step 1: Write failing multimodal payload tests**

Create `test_verify_model_runtime.py`:

```python
import base64
import tempfile
import unittest
from pathlib import Path

from verify_model_runtime import image_input


class VisionPayloadTest(unittest.TestCase):
    def test_builds_png_data_url_without_leaking_expected_text(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory, "fixture.png")
            image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            content = image_input(image)

        self.assertEqual(content[0], {
            "type": "input_text",
            "text": "Transcribe the main heading in this image. Reply with only that heading.",
        })
        self.assertEqual(content[1]["type"], "input_image")
        prefix, encoded = content[1]["image_url"].split(",", 1)
        self.assertEqual(prefix, "data:image/png;base64")
        self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG"))
        self.assertNotIn("VISION_7F31", content[0]["text"])


if __name__ == "__main__":
    unittest.main()
```

Run:

```bash
python3 -m unittest -v test_verify_model_runtime.py
```

Expected: FAIL because `image_input` does not exist.

- [ ] **Step 2: Let the Playwright verifier save its real screenshot**

Add `base64` and `pathlib.Path` imports to `verify_playwright_mcp.py`, add:

```python
parser.add_argument("--screenshot-output")
parser.add_argument("--vision-marker", default="VISION_7F31")
```

Immediately before the screenshot call, set the visible heading without exposing the marker to the model prompt:

```python
result_text(client.call_tool("browser_evaluate", {
    "function": f"() => document.querySelector('h1').textContent = {args.vision_marker!r}",
}))
screenshot = client.call_tool("browser_take_screenshot", {
    "type": "png", "fullPage": True, "scale": "css",
})
images = [item for item in screenshot.get("content", []) if item.get("type") == "image"]
if not images:
    raise RuntimeError("screenshot returned no image content")
if args.screenshot_output:
    Path(args.screenshot_output).write_bytes(base64.b64decode(images[0]["data"]))
```

Keep the existing `PASS screenshot` output and browser close behavior.

- [ ] **Step 3: Add the Responses image request**

At module level in `verify_model_runtime.py`, add:

```python
import base64
from pathlib import Path


def image_input(path):
    encoded = base64.b64encode(Path(path).read_bytes()).decode()
    return [
        {
            "type": "input_text",
            "text": "Transcribe the main heading in this image. Reply with only that heading.",
        },
        {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"},
    ]
```

Add arguments:

```python
parser.add_argument("--vision-image")
parser.add_argument("--vision-expected", default="VISION_7F31")
```

After the reasoning check, add:

```python
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
```

Before the final runtime verdict, print:

```python
print("VISION_SUPPORTED" if vision_supported else "VISION_GAP")
```

The runtime script must still exit successfully when vision is the only gap.

- [ ] **Step 4: Reorder aggregate verification around the screenshot artifact**

In `scripts/verify_all.sh`, create a temporary directory and use configured URLs:

```bash
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
search_url="${SEARCH_MCP_URL:-http://127.0.0.1:${SEARCH_MCP_PORT}/mcp}"
playwright_url="${PLAYWRIGHT_MCP_URL:-http://127.0.0.1:${PLAYWRIGHT_MCP_PORT}/mcp}"
```

Run checks in this order:

```bash
python3 verify_search_mcp.py --url "$search_url"
python3 verify_playwright_mcp.py \
  --url "$playwright_url" \
  --screenshot-output "$work_dir/vision.png" \
  --vision-marker VISION_7F31
python3 verify_model_runtime.py \
  --base-url "$LLM_BASE_URL" \
  --model "$LLM_MODEL" \
  --concurrency "${LLM_CONCURRENCY:-3}" \
  --vision-image "$work_dir/vision.png" \
  --vision-expected VISION_7F31
```

Delete the old RAG placeholder block because RAG is explicitly outside Phase 1.1. Change the final marker to:

```bash
echo "PHASE11_LOCAL_READY"
```

- [ ] **Step 5: Verify Task 2**

Run:

```bash
python3 -m unittest -v
bash scripts/verify_all.sh
```

Expected: all unit tests pass; Playwright writes a valid screenshot; runtime prints either `VISION_SUPPORTED` or the explicit `VISION_GAP`; aggregate ends with `PHASE11_LOCAL_READY` when all required local checks pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add verify_playwright_mcp.py verify_model_runtime.py test_verify_model_runtime.py scripts/verify_all.sh
git commit -m "feat: verify Codex image input capability"
```

---

### Task 3: Build the manual Codex client VPS verifier and 20-gate report

**Files:**

- Create: `verify_codex_vps.py`
- Create: `test_verify_codex_vps.py`

**Interfaces:**

- Consumes: existing Codex base config, selected `CODEX_PROFILE`, `LLM_BASE_URL`, `LLM_MODEL`, `MCP_AUTH_TOKEN`, `SEARCH_MCP_URL`, and `PLAYWRIGHT_MCP_URL`.
- Produces: one line per gate, a numeric `CAPABILITY_SCORE`, and `PHASE11_VPS_READY` only when the spec threshold passes.

- [ ] **Step 1: Write failing report and JSONL parser tests**

Create `test_verify_codex_vps.py`:

```python
import unittest

from verify_codex_vps import CRITICAL_GATES, GateReport, parse_jsonl


class CodexVpsVerifierTest(unittest.TestCase):
    def test_parses_jsonl_and_rejects_invalid_lines(self):
        events = parse_jsonl('{"type":"thread.started","thread_id":"abc"}\n')
        self.assertEqual(events[0]["thread_id"], "abc")
        with self.assertRaisesRegex(RuntimeError, "invalid JSONL"):
            parse_jsonl("not-json\n")

    def test_requires_nineteen_gates_and_every_critical_gate(self):
        report = GateReport()
        for name in report.names[:19]:
            report.pass_gate(name)
        self.assertTrue(report.ready())

        report = GateReport()
        for name in report.names:
            if name != next(iter(CRITICAL_GATES)):
                report.pass_gate(name)
        self.assertFalse(report.ready())


if __name__ == "__main__":
    unittest.main()
```

Run:

```bash
python3 -m unittest -v test_verify_codex_vps.py
```

Expected: FAIL because the verifier module does not exist.

- [ ] **Step 2: Implement the gate model and subprocess helpers**

In `verify_codex_vps.py`, define the exact gate order from the spec:

```python
GATES = (
    "responses", "streaming", "function_calling", "tool_continuation",
    "reasoning", "shell", "filesystem", "git", "search_mcp",
    "playwright_mcp", "long_context", "concurrency", "cancellation",
    "image_input", "background_process", "exec_jsonl", "session_resume",
    "agents_md", "sandbox_read_only", "code_review",
)
CRITICAL_GATES = frozenset(GATES[:10])


class GateReport:
    def __init__(self):
        self.names = GATES
        self.passed = set()

    def pass_gate(self, name):
        self.passed.add(name)
        print(f"PASS  gate {name}")

    def fail_gate(self, name, detail):
        print(f"FAIL  gate {name}: {detail}")

    def ready(self):
        return len(self.passed) >= 19 and CRITICAL_GATES <= self.passed

    def print_summary(self):
        print(f"CAPABILITY_SCORE {len(self.passed)}/20 ({len(self.passed) * 5}%)")


def parse_jsonl(text):
    events = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"invalid JSONL line {number}: {line[:120]}") from error
    return events
```

Add `run()` and `codex_exec()` helpers using `subprocess.run`, a configurable timeout, captured stdout/stderr, and non-zero exit reporting. Never use `shell=True`.

- [ ] **Step 3: Implement deterministic provider and host gates**

From the VPS verifier, run the existing scripts and map their output markers:

```text
check_codex_api.py:
  PASS Responses API       -> responses
  PASS Responses streaming -> streaming
  PASS function calling    -> function_calling
  PASS tool continuation   -> tool_continuation

verify_model_runtime.py:
  PASS reasoning response  -> reasoning
  PASS long-context smoke  -> long_context
  PASS concurrency         -> concurrency
  PASS stream cancellation -> cancellation
  VISION_SUPPORTED         -> image_input

scripts/verify_client_primitives.sh:
  PASS Git workflow                -> git
  PASS background process + wait   -> background_process
```

Generate the vision screenshot first by running `verify_playwright_mcp.py` with a temporary output path. Run both MCP protocol verifiers against `SEARCH_MCP_URL` and `PLAYWRIGHT_MCP_URL`; protocol success is a prerequisite, while the `search_mcp` and `playwright_mcp` gates are awarded only after Codex itself uses those tools in Step 5.

- [ ] **Step 4: Implement safe Codex CLI workflow gates**

Create one `TemporaryDirectory`, initialize Git, and set repository-local test identity. Implement these checks:

1. `codex -p "$CODEX_PROFILE" mcp get web_search --json` and the equivalent Playwright lookup must succeed. Allow names to be overridden by `CODEX_SEARCH_MCP_NAME` and `CODEX_PLAYWRIGHT_MCP_NAME`.
2. Run `codex exec --json -s read-only` with `Reply exactly EXEC_JSON_OK. Do not use tools.`; require `thread.started`, `turn.completed`, and the marker. Award `exec_jsonl`.
3. Add `check_result.py` that asserts `result.txt == "PHASE11_EDIT_OK\n"`. Run Codex with `-s workspace-write` and instruct it to create the file and run the checker. Require a `command_execution` item, the exact file content, and successful checker execution. Award `shell` and `filesystem`.
4. Capture the thread ID from a JSONL turn that remembers a random `SESSION_<hex>` marker. Resume with `codex exec resume --json <thread-id>` and require the marker. Award `session_resume`.
5. Add `AGENTS.md` instructing Codex to reply `AGENTS_PHASE11_OK` for the verification prompt. Require the marker. Award `agents_md`.
6. Run read-only Codex with `You must attempt to run touch blocked.txt, then report the result.` Require a `command_execution` item and require that `blocked.txt` does not exist. Award `sandbox_read_only`.
7. Commit a clean `divide.py`, then change it to perform unchecked division. Run `codex exec review --json --uncommitted` with instructions to identify the division-by-zero risk. Require a completed turn and output containing both `zero` and `division`/`divide`. Award `code_review`.

Every prompt must prohibit unrelated changes. Do not use `--dangerously-bypass-approvals-and-sandbox`.

- [ ] **Step 5: Implement model-driven MCP gates**

Run these Codex prompts in read-only mode:

```text
Use the web_search MCP server and its searxng_web_search tool to search for
OpenAI Codex CLI documentation. After the tool succeeds, reply exactly
SEARCH_MCP_OK.
```

Require the JSONL stream to contain an MCP item, `searxng_web_search`, and the final marker before awarding `search_mcp`.

```text
Use the playwright MCP browser tools to navigate to http://test-site/ and read
the text from #dynamic. If it is JS_READY, reply exactly PLAYWRIGHT_MCP_OK.
```

Require the JSONL stream to contain an MCP item, `browser_navigate`, `JS_READY`, and the final marker before awarding `playwright_mcp`.

- [ ] **Step 6: Finish readiness reporting**

Always print every missing gate, then call `GateReport.print_summary()`. End with:

```python
if report.ready():
    print("\nPHASE11_VPS_READY")
    return 0
print("\nPHASE11_VPS_NOT_READY")
return 1
```

Add CLI arguments for `--env-file`, `--timeout`, and repository path discovery. Source dotenv values with a small parser that accepts only non-comment `KEY=VALUE` lines and never executes the file as shell code.

- [ ] **Step 7: Verify Task 3 without invoking an LLM**

Run:

```bash
python3 -m unittest -v test_verify_codex_vps.py
python3 -m unittest -v
python3 verify_codex_vps.py --help
```

Expected: unit tests pass and help exits zero. Do not run the full VPS verifier on the development machine.

- [ ] **Step 8: Commit Task 3**

```bash
git add verify_codex_vps.py test_verify_codex_vps.py
git commit -m "feat: add Codex VPS parity verifier"
```

---

### Task 4: Document and validate the service-host-to-client workflow

**Files:**

- Create: `docs/phase11-runbook.md`
- Modify: `docs/plans/2026-09-21-phase1-harness.md`

**Interfaces:**

- Consumes: the completed local and VPS verifier commands.
- Produces: one copy/paste runbook for the shared LAN service host and each Codex client VPS.

- [ ] **Step 1: Write the runbook**

Document these exact stages in `docs/phase11-runbook.md`:

```text
1. Service VPS: choose an explicit LAN IP and configure .env.
2. Service VPS: run scripts/setup.sh and confirm PHASE11_LOCAL_READY.
3. Service VPS firewall: allow TCP 18081/18082 only from Codex client IPs.
4. Client VPS: create a client env file containing MCP URLs and the shared demo token.
5. Client VPS: run scripts/verify_remote_mcp.sh <client-env-file>.
6. Client VPS: install an opt-in profile containing the open-weight provider and both remote MCP servers; leave `~/.codex/config.toml` on OpenAI.
7. Client VPS: export MCP_AUTH_TOKEN before starting a new Codex session.
8. Client VPS: run python3 verify_codex_vps.py --env-file <client-env-file>.
9. Accept only PHASE11_VPS_READY and CAPABILITY_SCORE >= 19/20.
```

Include a complete service `.env` example using documentation-only address `192.0.2.20`, a complete client env example, the two Codex TOML blocks, firewall examples for UFW, and rollback using `docker compose down` without `-v`.

State explicitly that `http://test-site/` must fail from the client host and succeed only through Playwright MCP.
State that plain HTTP is permitted only on the isolated demo LAN; use TLS or a trusted WireGuard/Tailscale overlay before crossing an untrusted network.

- [ ] **Step 2: Update the original plan status**

Append a Phase 1.1 note to `docs/plans/2026-09-21-phase1-harness.md` stating that MCP services are shared over the LAN and Codex CLI remains a client-only final integration step. Link the design and runbook with relative Markdown links.

- [ ] **Step 3: Run complete local verification**

Run:

```bash
python3 -m unittest -v
docker compose config --quiet
bash -n scripts/*.sh
bash scripts/setup.sh
git diff --check
```

Expected: all tests pass, Compose becomes healthy, the full local suite ends with `PHASE11_LOCAL_READY`, and the diff has no whitespace errors.

- [ ] **Step 4: Review security invariants**

Run:

```bash
docker compose config | rg -n 'ports:|127\.0\.0\.1|18081|18082|8080|8931'
git grep -nE 'MCP_AUTH_TOKEN=.+|ghp_|sk-[A-Za-z0-9]'
```

Expected: only Search MCP and the Playwright auth proxy publish ports; no real token is committed. Review the rendered bind address against the operator's `.env` rather than assuming loopback.

- [ ] **Step 5: Commit Task 4**

```bash
git add docs/phase11-runbook.md docs/plans/2026-09-21-phase1-harness.md
git commit -m "docs: add phase 1.1 LAN deployment runbook"
```

---

## Final VPS Acceptance

The operator performs this after copying the repository to the configured Codex client VPS:

```bash
bash scripts/verify_remote_mcp.sh .env.client
python3 verify_codex_vps.py --env-file .env.client
```

Required final markers, with a score of either 19/20 or 20/20:

```text
REMOTE_MCP_READY
CAPABILITY_SCORE 19/20 (95%)
PHASE11_VPS_READY
```

Any critical gate failure must produce `PHASE11_VPS_NOT_READY`, regardless of the numeric count.
