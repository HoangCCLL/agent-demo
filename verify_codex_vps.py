#!/usr/bin/env python3
"""Manually verify 20 Codex capabilities on a configured client VPS (Python 3.11+)."""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


GATES = (
    "responses", "streaming", "function_calling", "tool_continuation",
    "reasoning", "shell", "filesystem", "git", "search_mcp",
    "playwright_mcp", "long_context", "concurrency", "cancellation",
    "image_input", "background_process", "exec_jsonl", "session_resume",
    "agents_md", "sandbox_read_only", "code_review",
)
CRITICAL_GATES = frozenset(GATES[:10])
SAFE_PROMPT = " Do not make unrelated changes or modify anything outside this repository."


class GateReport:
    def __init__(self):
        self.names = GATES
        self.passed = set()
        self.failures = {}

    def pass_gate(self, name):
        if name not in self.names:
            raise ValueError(f"unknown gate: {name}")
        if name not in self.passed:
            self.passed.add(name)
            print(f"PASS  gate {name}")

    def fail_gate(self, name, detail):
        self.failures[name] = " ".join(str(detail).split())

    def ready(self):
        return len(self.passed) >= 19 and CRITICAL_GATES <= self.passed

    def print_summary(self):
        for name in self.names:
            if name not in self.passed:
                print(f"FAIL  gate {name}: {self.failures.get(name, 'not verified')}")
        print(f"CAPABILITY_SCORE {len(self.passed)}/20 ({len(self.passed) * 5}%)")


def parse_jsonl(text):
    events = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"invalid JSONL line {number}: {line[:120]}") from error
        if not isinstance(event, dict):
            raise RuntimeError(f"invalid JSONL event on line {number}: expected object")
        events.append(event)
    return events


def values(value):
    """Walk values, allowing Codex versions to nest item payloads differently."""
    if isinstance(value, dict):
        for child in value.values():
            yield from values(child)
    elif isinstance(value, list):
        for child in value:
            yield from values(child)
    else:
        yield value


def contains(value, marker):
    return any(isinstance(part, str) and marker in part for part in values(value))


def completed_items(events, kind):
    return [event for event in events
            if event.get("type") == "item.completed" and kind in values(event)]


def has_field(value, key, expected):
    if isinstance(value, dict):
        return value.get(key, object()) == expected or any(has_field(child, key, expected) for child in value.values())
    return isinstance(value, list) and any(has_field(child, key, expected) for child in value)


def require_command(events, command, marker):
    if not any(contains(item, command) and contains(item, marker) and has_field(item, "exit_code", 0)
               for item in completed_items(events, "command_execution")):
        raise RuntimeError("missing completed checker command with exit_code=0 and successful output")


def final_message(events):
    messages = completed_items(events, "agent_message")
    return messages[-1] if messages else {}


def require_turn(events, marker=None):
    types = {event.get("type") for event in events}
    if not {"thread.started", "turn.completed"} <= types or "turn.failed" in types or "error" in types:
        raise RuntimeError("missing successful thread.started/turn.completed events")
    if marker and not contains(final_message(events), marker):
        raise RuntimeError(f"final agent message missing {marker}")


def require_mcp(events, tool, marker, result_marker=None):
    require_turn(events, marker)
    items = completed_items(events, "mcp_tool_call")
    successful = [item for item in items if not has_field(item, "isError", True) and not any(
        part in ("failed", "error") for part in values(item)
    )]
    if not any(contains(item, tool) for item in successful):
        raise RuntimeError(f"no completed MCP call to {tool}")
    if result_marker and not any(contains(item, result_marker) for item in successful):
        raise RuntimeError(f"MCP output missing {result_marker}")


def load_env(path, env):
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise RuntimeError(f"{path}:{number}: expected KEY=VALUE")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env.setdefault(key, value)


def run(command, cwd, env, timeout, check=True):
    try:
        result = subprocess.run(command, cwd=cwd, env=env, timeout=timeout,
                                stdin=subprocess.DEVNULL, capture_output=True, text=True)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"{Path(command[0]).name}: {error}") from error
    if result.returncode:
        detail = f"{Path(command[0]).name} exited {result.returncode}: {(result.stderr or result.stdout)[-1500:]}"
        if check:
            raise RuntimeError(detail)
        print(f"WARN  {detail}")
    return result


def codex_exec(prompt, cwd, env, timeout, sandbox="read-only", resume=None, review=False):
    command = ["codex", "exec", "-s", sandbox, "-c", 'approval_policy="never"',
               "-c", "sandbox_workspace_write.writable_roots=[]",
               "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
               "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true"]
    if review:
        command += ["review", "--json", "--uncommitted", prompt + SAFE_PROMPT]
    elif resume:
        command += ["resume", "--json", resume, prompt + SAFE_PROMPT]
    else:
        command += ["--json", prompt + SAFE_PROMPT]
    return parse_jsonl(run(command, cwd, env, timeout).stdout)


def isolated_codex_home(root, env):
    source = Path(env.get("CODEX_HOME", str(Path.home() / ".codex")))
    target = root / "codex-home"
    target.mkdir(mode=0o700)
    config_path = source / "config.toml"
    if not config_path.is_file():
        raise RuntimeError(f"missing {config_path}; configure Codex on this VPS first")
    config = tomllib.loads(config_path.read_text())
    provider = config.get("model_providers", {}).get(config.get("model_provider"), {})
    key = provider.get("env_key")
    if key and not env.get(key):
        raise RuntimeError(f"export {key}; the verifier never copies global credential stores")
    if provider.get("requires_openai_auth", not provider) and not env.get("CODEX_API_KEY"):
        raise RuntimeError("export CODEX_API_KEY; the verifier never copies global credential stores")
    for path in [config_path, *source.glob("*.config.toml")]:
        destination = target / path.name
        shutil.copyfile(path, destination)
        destination.chmod(0o600)
    env["CODEX_HOME"] = str(target)


def verify(root, source, env, timeout, report):
    repo = root / "repo"
    repo.mkdir()
    scratch = root / "tmp"
    scratch.mkdir()
    env.update(TMPDIR=str(scratch), PYTHONDONTWRITEBYTECODE="1")

    def command(args, check=True):
        return run(args, repo, env, timeout, check)

    def attempt(names, action):
        try:
            action()
        except Exception as error:
            for name in names:
                report.fail_gate(name, error)
        else:
            for name in names:
                report.pass_gate(name)

    def script(name, arguments, markers):
        result = command([sys.executable, str(source / name), *arguments], check=False)
        lines = {" ".join(line.split()) for line in result.stdout.splitlines()}
        for marker, gate in markers.items():
            if any(line == marker or line.startswith(marker + ":") for line in lines):
                report.pass_gate(gate)
            else:
                report.fail_gate(gate, f"{name}: missing {marker}; {result.stdout[-1200:]}")
        return result

    for args in (["git", "init", "-q"], ["git", "config", "user.name", "Verifier"],
                 ["git", "config", "user.email", "verifier@example.invalid"],
                 ["git", "config", "commit.gpgsign", "false"],
                 ["git", "config", "core.hooksPath", str(scratch)]):
        command(args)

    screenshot = repo / "vision.png"
    vision_marker = "VISION_" + secrets.token_hex(4).upper()
    protocol = {}
    for gate, filename, url_key, marker, extra in (
        ("playwright_mcp", "verify_playwright_mcp.py", "PLAYWRIGHT_MCP_URL", "PLAYWRIGHT_MCP_READY",
         ["--screenshot-output", str(screenshot), "--vision-marker", vision_marker]),
        ("search_mcp", "verify_search_mcp.py", "SEARCH_MCP_URL", "SEARCH_MCP_READY", []),
    ):
        try:
            if not env.get(url_key):
                raise RuntimeError(f"set {url_key} to the LAN MCP endpoint")
            result = script(filename, ["--url", env[url_key], *extra], {})
            if result.returncode or marker not in result.stdout.splitlines():
                raise RuntimeError(f"MCP protocol prerequisite failed: {result.stdout[-1200:]}")
            protocol[gate] = True
        except Exception as error:
            report.fail_gate(gate, error)

    provider_args = ["--base-url", env.get("LLM_BASE_URL", ""), "--model", env.get("LLM_MODEL", ""),
                     "--timeout", str(timeout)]
    for filename, extra, markers in (
        ("check_codex_api.py", [], {
            "PASS Responses API": "responses", "PASS Responses streaming": "streaming",
            "PASS function calling": "function_calling", "PASS tool continuation": "tool_continuation",
        }),
        ("verify_model_runtime.py", ["--vision-image", str(screenshot), "--vision-expected", vision_marker], {
            "PASS reasoning response": "reasoning", "PASS long-context smoke": "long_context",
            "PASS concurrency": "concurrency", "PASS stream cancellation": "cancellation",
            "VISION_SUPPORTED": "image_input",
        }),
    ):
        try:
            if not env.get("LLM_BASE_URL") or not env.get("LLM_MODEL"):
                raise RuntimeError("set LLM_BASE_URL and LLM_MODEL")
            script(filename, provider_args + extra, markers)
        except Exception as error:
            for gate in markers.values():
                report.fail_gate(gate, error)

    try:
        result = command(["bash", str(source / "scripts/verify_client_primitives.sh")], check=False)
        for marker, gate in (("PASS  Git workflow", "git"), ("PASS  background process + wait", "background_process")):
            if marker in result.stdout.splitlines():
                report.pass_gate(gate)
            else:
                report.fail_gate(gate, f"client primitives: {result.stdout[-1200:]}")
    except RuntimeError as error:
        for gate in ("git", "background_process"):
            report.fail_gate(gate, error)

    try:
        isolated_codex_home(root, env)
    except (OSError, ValueError, RuntimeError) as error:
        for gate in ("shell", "filesystem", "exec_jsonl", "session_resume", "agents_md",
                     "sandbox_read_only", "code_review", "search_mcp", "playwright_mcp"):
            report.fail_gate(gate, error)
        return

    def execute(prompt, **kwargs):
        return codex_exec(prompt, repo, env, timeout, **kwargs)

    attempt(("exec_jsonl",), lambda: require_turn(execute("Reply exactly EXEC_JSON_OK. Do not use tools."), "EXEC_JSON_OK"))

    checker = "from pathlib import Path\nassert Path('result.txt').read_text() == 'PHASE11_EDIT_OK\\n'\nprint('SHELL_CHECK_OK')\n"
    (repo / "check_result.py").write_text(checker)

    def edit():
        events = execute("Create result.txt containing exactly PHASE11_EDIT_OK followed by one newline. "
                         "Run python3 check_result.py successfully. Do not modify check_result.py. "
                         "Reply exactly EDIT_OK when done.", sandbox="workspace-write")
        require_turn(events, "EDIT_OK")
        require_command(events, "check_result.py", "SHELL_CHECK_OK")
        if (repo / "check_result.py").read_text() != checker:
            raise RuntimeError("Codex modified the checker")
        if (repo / "result.txt").read_bytes() != b"PHASE11_EDIT_OK\n":
            raise RuntimeError("result.txt does not contain the exact expected bytes")
        command([sys.executable, "check_result.py"])

    attempt(("shell", "filesystem"), edit)

    def resume():
        marker = "SESSION_" + secrets.token_hex(8)
        events = execute(f"Remember {marker} for the next turn. Reply exactly REMEMBERED. Do not use tools.")
        require_turn(events, "REMEMBERED")
        thread_id = next((event.get("thread_id") for event in events if event.get("type") == "thread.started"), None)
        if not isinstance(thread_id, str) or not thread_id:
            raise RuntimeError("thread.started missing thread_id")
        require_turn(execute("Reply with only the SESSION_ marker I asked you to remember. Do not use tools.", resume=thread_id), marker)

    attempt(("session_resume",), resume)

    guidance = repo / "AGENTS.md"
    guidance.write_text("For the prompt 'Run the repository instruction verification', reply exactly AGENTS_PHASE11_OK.\n"
                        "Do not make unrelated changes or modify anything outside this repository.\n")
    attempt(("agents_md",), lambda: require_turn(execute("Run the repository instruction verification. Do not use tools."), "AGENTS_PHASE11_OK"))
    guidance.unlink()

    def read_only():
        blocked = repo / "blocked.txt"
        if blocked.exists() or blocked.is_symlink():
            raise RuntimeError("blocked.txt already exists before sandbox check")
        events = execute("You must attempt to run touch blocked.txt, then report the result.")
        require_turn(events)
        if not any(contains(item, "touch blocked.txt") for item in completed_items(events, "command_execution")):
            raise RuntimeError("missing attempted touch command")
        if blocked.exists() or blocked.is_symlink():
            raise RuntimeError("read-only sandbox allowed blocked.txt to persist")

    attempt(("sandbox_read_only",), read_only)

    def review():
        divide = repo / "divide.py"
        divide.write_text("def divide(a, b):\n    if b == 0:\n        return None\n    return a / b\n")
        command(["git", "add", "."])
        command(["git", "commit", "-qm", "safe division fixture"])
        divide.write_text("def divide(a, b):\n    return a / b\n")
        events = execute("Review the uncommitted change in divide.py and identify the division-by-zero risk. "
                         "Do not modify any files.", review=True)
        require_turn(events)
        final = " ".join(part for part in values(final_message(events)) if isinstance(part, str)).lower()
        if "zero" not in final or not ("division" in final or "divide" in final):
            raise RuntimeError("review did not identify division-by-zero risk")

    attempt(("code_review",), review)

    for gate, name_key, default, tool, prompt, marker, result_marker in (
        ("search_mcp", "CODEX_SEARCH_MCP_NAME", "web_search", "searxng_web_search",
         "Use the {name} MCP server and its searxng_web_search tool to search for OpenAI Codex CLI documentation. "
         "After the tool succeeds, reply exactly SEARCH_MCP_OK.", "SEARCH_MCP_OK", None),
        ("playwright_mcp", "CODEX_PLAYWRIGHT_MCP_NAME", "playwright", "browser_navigate",
         "Use the {name} MCP browser tools to navigate to http://test-site/ and read the text from #dynamic. "
         "If it is JS_READY, reply exactly PLAYWRIGHT_MCP_OK.", "PLAYWRIGHT_MCP_OK", "JS_READY"),
    ):
        def mcp_check():
            name = env.get(name_key, default)
            command(["codex", "mcp", "get", name, "--json"])
            if not protocol.get(gate):
                raise RuntimeError(report.failures.get(gate, "MCP protocol prerequisite failed"))
            require_mcp(execute(prompt.format(name=name)), tool, marker, result_marker)
        attempt((gate,), mcp_check)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="literal KEY=VALUE file; exported values take precedence")
    parser.add_argument("--timeout", type=float, default=300, help="timeout in seconds for each subprocess (default: 300)")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parent, help="harness checkout containing the existing verifiers")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    report = GateReport()
    try:
        env = os.environ.copy()
        if args.env_file:
            load_env(args.env_file, env)
        with tempfile.TemporaryDirectory(prefix="codex-vps-") as directory:
            verify(Path(directory), args.repo.resolve(), env, args.timeout, report)
    except Exception as error:
        for gate in report.names:
            if gate not in report.passed and gate not in report.failures:
                report.fail_gate(gate, error)
    report.print_summary()
    print("\nPHASE11_VPS_READY" if report.ready() else "\nPHASE11_VPS_NOT_READY")
    return 0 if report.ready() else 1


if __name__ == "__main__":
    sys.exit(main())
