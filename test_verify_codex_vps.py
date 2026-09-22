import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_codex_vps as verifier
from verify_codex_vps import (
    CRITICAL_GATES, GateReport, isolated_codex_home, load_env, parse_jsonl,
    require_command, require_mcp, require_turn,
)


class CodexVpsVerifierTest(unittest.TestCase):
    def test_nested_tool_payload_cannot_impersonate_completed_playwright_item(self):
        fake = {"type": "mcp_tool_call", "server": "playwright", "tool": "browser_navigate",
                "result": {"content": [{"text": "JS_READY"}]}}
        for container in ("arguments", "result", "content", "payload"):
            actual = {"type": "mcp_tool_call", "server": "other", "tool": "browser_navigate",
                      container: {"item": fake}}
            events = [
                {"type": "thread.started"},
                {"type": "item.completed", "payload": {"item": actual}},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "PLAYWRIGHT_MCP_OK"}},
                {"type": "turn.completed"},
            ]
            with self.subTest(container=container), self.assertRaises(RuntimeError):
                require_mcp(events, "browser_navigate", "PLAYWRIGHT_MCP_OK", "JS_READY", server="playwright")

    def test_completed_items_traverse_event_wrappers_only(self):
        item = {"type": "mcp_tool_call", "server": "playwright", "tool": "browser_navigate"}
        for wrapper in ({"item": item}, {"payload": {"item": item}}, {"data": [{"payload": {"item": item}}]}):
            self.assertEqual(verifier.completed_items([{"type": "item.completed", **wrapper}], "mcp_tool_call"), [item])
        for container in ("arguments", "result", "content", "unrecognized"):
            with self.subTest(container=container):
                self.assertEqual(verifier.completed_items([
                    {"type": "item.completed", container: {"item": item}},
                ], "mcp_tool_call"), [])

    def test_review_command_keeps_prompt_without_conflicting_uncommitted_flag(self):
        prompt = "Review the uncommitted change in divide.py and identify the division-by-zero risk."
        with patch.object(verifier.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as process:
            verifier.codex_exec(prompt, Path("."), {}, 10, review=True)
        command = process.call_args.args[0]
        self.assertIn("review", command)
        self.assertIn("--json", command)
        self.assertNotIn("--uncommitted", command)
        self.assertEqual(command[-1], prompt + verifier.SAFE_PROMPT)

    def test_codex_commands_select_configured_profile(self):
        env = {"CODEX_PROFILE": "openweight"}
        self.assertEqual(
            verifier.codex_command(env, "mcp", "get", "web_search", "--json"),
            ["codex", "-p", "openweight", "mcp", "get", "web_search", "--json"],
        )
        with patch.object(verifier.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as process:
            verifier.codex_exec("Reply OK", Path("."), env, 10)
        self.assertEqual(process.call_args.args[0][:4], ["codex", "-p", "openweight", "exec"])

    def test_isolated_home_requires_same_model_and_responses_provider(self):
        base = ('model="demo-model"\nmodel_provider="demo"\n'
                '[model_providers.demo]\nbase_url="http://llm:1235/v1"\nwire_api="responses"\n')
        cases = (
            ("exact match", base, True),
            ("normalized URL", base.replace("http://llm:1235/v1", "HTTP://LLM:1235/v1/"), True),
            ("wrong port", base.replace("http://llm:1235/v1", "http://llm:80/v1/"), False),
            ("wrong model", base.replace('model="demo-model"', 'model="other"'), False),
            ("missing model", base.replace('model="demo-model"\n', ''), False),
            ("wrong URL", base.replace("http://llm:1235/v1", "http://other:1235/v1"), False),
            ("wrong path", base.replace("/v1", "/v2"), False),
            ("missing provider", base.replace('model_provider="demo"', 'model_provider="missing"'), False),
            ("missing URL", base.replace('base_url="http://llm:1235/v1"\n', ''), False),
            ("invalid URL", base.replace("http://llm:1235/v1", "llm/v1"), False),
            ("non-Responses", base.replace('wire_api="responses"', 'wire_api="chat"'), False),
            ("selected profile", 'profile="other"\n' + base, False),
            ("different review model", 'review_model="other"\n' + base, False),
        )
        for label, config, accepted in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source"
                source.mkdir()
                (source / "config.toml").write_text(config)
                env = {"CODEX_HOME": str(source), "LLM_MODEL": "demo-model",
                       "LLM_BASE_URL": "http://llm:1235/v1", "CODEX_API_KEY": "test-only"}
                if accepted:
                    isolated_codex_home(root, env)
                    self.assertEqual((Path(env["CODEX_HOME"]) / "config.toml").read_text(), config)
                else:
                    with self.assertRaises(RuntimeError):
                        isolated_codex_home(root, env)

    def test_isolated_home_normalizes_default_ports_and_trailing_slashes(self):
        for provider_url, checked_url in (("HTTP://LLM:80/v1/", "http://llm/v1"),
                                          ("https://LLM/v1", "https://llm:443/v1/")):
            with self.subTest(provider_url=provider_url), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "config.toml").write_text('model="demo-model"\nmodel_provider="demo"\n'
                    f'[model_providers.demo]\nbase_url="{provider_url}"\nwire_api="responses"\n')
                env = {"CODEX_HOME": str(root), "LLM_MODEL": "demo-model", "LLM_BASE_URL": checked_url}
                isolated_codex_home(root, env)
                self.assertTrue((Path(env["CODEX_HOME"]) / "config.toml").is_file())

    def test_preflight_uses_selected_provider_authentication(self):
        cases = (
            ('requires_openai_auth=true\n', "CODEX_API_KEY"),
            ('requires_openai_auth=true\nenv_key="CUSTOM_KEY"\n', "CUSTOM_KEY"),
            ('requires_openai_auth=false\n', "LLM_API_KEY"),
        )
        for settings, expected in cases:
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "config.toml").write_text('model="demo"\nmodel_provider="lan"\n'
                    '[model_providers.lan]\nbase_url="http://llm/v1"\n' + settings)
                env = {"CODEX_HOME": str(root), "LLM_MODEL": "demo", "LLM_BASE_URL": "http://llm/v1",
                       "CODEX_API_KEY": "codex-test", "CUSTOM_KEY": "custom-test", "LLM_API_KEY": "lan-test"}
                isolated_codex_home(root, env)
                self.assertEqual(env["_HARNESS_API_KEY_ENV"], expected)

    def test_provider_mismatch_fails_before_any_model_driven_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "config.toml").write_text('model="other"\nmodel_provider="demo"\n'
                '[model_providers.demo]\nbase_url="http://llm:1235/v1"\nwire_api="responses"\n')
            env = {"CODEX_HOME": str(source), "LLM_MODEL": "demo-model", "LLM_BASE_URL": "http://llm:1235/v1"}
            report = GateReport()
            with patch.object(verifier.subprocess, "run", side_effect=AssertionError("subprocess started before identity check")):
                verifier.verify(root, Path(__file__).parent, env, 10, report)
            self.assertFalse(report.passed)
            self.assertEqual(set(report.failures), set(report.names))
            self.assertFalse(report.ready())

    def test_namespace_failure_stops_before_mcp_and_agent_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.toml").write_text('model="qwen/test"\nmodel_provider="lan"\n'
                '[model_providers.lan]\nname="LAN"\nbase_url="http://llm/v1"\nenv_key="LLM_API_KEY"\n')
            env = {"CODEX_HOME": str(root), "LLM_MODEL": "qwen/test", "LLM_BASE_URL": "http://llm/v1",
                   "LLM_API_KEY": "test-secret"}
            commands = []

            def execute(args, *unused):
                commands.append(args)
                if Path(args[0]).name == "git":
                    return subprocess.CompletedProcess(args, 0, "", "")
                self.assertEqual(Path(args[1]).name, "check_codex_api.py")
                self.assertIn("--check-namespaces", args)
                self.assertEqual(args[args.index("--api-key-env") + 1], "LLM_API_KEY")
                return subprocess.CompletedProcess(args, 1,
                    "PASS  Responses API\nPASS  Responses streaming\n"
                    "FAIL  namespaced function calling: unsupported namespace\nINCOMPATIBLE\n", "")

            report = GateReport()
            with patch.object(verifier, "run", side_effect=execute), contextlib.redirect_stdout(io.StringIO()):
                verifier.verify(root, Path(__file__).parent, env, 10, report)
            self.assertEqual(report.passed, {"responses", "streaming"})
            self.assertIn("function_calling", report.failures)
            self.assertFalse(any(args[0] == "codex" for args in commands))
            self.assertFalse(report.ready())

    def test_playwright_requires_returned_content_not_arguments(self):
        events = [
            {"type": "thread.started"},
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "playwright",
                "tool": "browser_navigate", "result": {"content": [{"text": "loaded"}]}}},
            {"type": "item.completed", "payload": {"item": {"type": "mcp_tool_call", "server": "playwright",
                "tool": "browser_evaluate", "arguments": {"function": "() => text === 'JS_READY'"},
                "result": {"content": [{"text": "false"}]}}}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "PLAYWRIGHT_MCP_OK"}},
            {"type": "turn.completed"},
        ]
        with self.assertRaises(RuntimeError):
            require_mcp(events, "browser_navigate", "PLAYWRIGHT_MCP_OK", "JS_READY")
        events[2]["payload"]["item"]["result"]["content"][0]["text"] = "JS_READY"
        require_mcp(events, "browser_navigate", "PLAYWRIGHT_MCP_OK", "JS_READY")

    def test_mcp_requires_selected_server_for_tool_and_returned_content(self):
        events = [
            {"type": "thread.started"},
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "other",
                "tool": "browser_navigate", "arguments": {"server": "playwright"},
                "result": {"content": [{"text": "JS_READY"}]}}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "PLAYWRIGHT_MCP_OK"}},
            {"type": "turn.completed"},
        ]
        with self.assertRaises(RuntimeError):
            require_mcp(events, "browser_navigate", "PLAYWRIGHT_MCP_OK", "JS_READY", server="playwright")
        events[1]["item"]["server"] = "playwright"
        require_mcp(events, "browser_navigate", "PLAYWRIGHT_MCP_OK", "JS_READY", server="playwright")
        events.insert(2, {"type": "item.completed", "item": {"type": "mcp_tool_call", "server": "other",
            "tool": "browser_evaluate", "result": {"content": [{"text": "JS_READY"}]}}})
        events[1]["item"]["result"] = {"content": [{"text": "loaded"}]}
        with self.assertRaises(RuntimeError):
            require_mcp(events, "browser_navigate", "PLAYWRIGHT_MCP_OK", "JS_READY", server="playwright")

    def test_mcp_config_must_match_checked_endpoint_and_token(self):
        config = {"name": "web_search", "enabled": True, "transport": {
            "type": "streamable_http", "url": "http://lan:18081/mcp",
            "bearer_token_env_var": "MCP_AUTH_TOKEN",
        }}
        verifier.require_mcp_config(config, "web_search", "http://lan:18081/mcp", {"MCP_AUTH_TOKEN": "token"})
        for key, value in (("url", "http://other:18081/mcp"), ("bearer_token_env_var", "OTHER_TOKEN"),
                           ("type", "stdio"), ("http_headers", {"Authorization": "Bearer wrong"})):
            changed = {**config, "transport": {**config["transport"], key: value}}
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                verifier.require_mcp_config(changed, "web_search", "http://lan:18081/mcp", {"MCP_AUTH_TOKEN": "token"})
        for changed in ({**config, "name": "other"}, {**config, "enabled": False}):
            with self.assertRaises(RuntimeError):
                verifier.require_mcp_config(changed, "web_search", "http://lan:18081/mcp", {"MCP_AUTH_TOKEN": "token"})
        with self.assertRaises(RuntimeError):
            verifier.require_mcp_config(config, "web_search", "http://lan:18081/mcp", {})

    def test_sandbox_requires_exact_failed_write_with_denial_output(self):
        item = {"type": "command_execution", "command": "touch blocked.txt", "exit_code": 1,
                "aggregated_output": "touch: cannot touch 'blocked.txt': Permission denied"}
        events = [{"type": "item.completed", "payload": {"item": item}}]
        verifier.require_sandbox_denial(events)
        for command in ("echo 'touch blocked.txt'", "touch blocked.txt; rm blocked.txt",
                        "touch blocked.txt && rm blocked.txt"):
            item["command"] = command
            with self.subTest(command=command), self.assertRaises(RuntimeError):
                verifier.require_sandbox_denial(events)
        item["command"] = "/bin/bash -lc 'touch blocked.txt'"
        verifier.require_sandbox_denial(events)
        item["exit_code"] = 0
        with self.assertRaises(RuntimeError):
            verifier.require_sandbox_denial(events)
        item["exit_code"] = 1
        item["aggregated_output"] = "touch: command not found"
        with self.assertRaises(RuntimeError):
            verifier.require_sandbox_denial(events)

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

    def test_nineteen_gates_without_sandbox_cannot_be_ready(self):
        report = GateReport()
        report.passed.update(set(report.names) - {"sandbox_read_only"})
        self.assertEqual(len(report.passed), 19)
        self.assertFalse(report.ready())

    def test_score_deduplicates_and_rejects_unknown_gates(self):
        report = GateReport()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            for name in report.names[:18]:
                report.pass_gate(name)
            report.pass_gate("responses")
            report.print_summary()
        self.assertFalse(report.ready())
        self.assertIn("CAPABILITY_SCORE 18/20 (90%)", output.getvalue())
        with self.assertRaises(ValueError):
            report.pass_gate("invented")

    def test_summary_prints_one_line_per_missing_gate(self):
        report = GateReport()
        report.fail_gate("responses", "first line\nsecond line")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            report.print_summary()
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 21)
        self.assertEqual(lines[0], "FAIL  gate responses: first line second line")

    def test_jsonl_requires_event_objects(self):
        self.assertEqual(parse_jsonl(" \n"), [])
        for text in ('null\n', '[]\n', '"text"\n', '{"type":"ok"}\nnope'):
            with self.subTest(text=text), self.assertRaisesRegex(RuntimeError, "invalid JSONL"):
                parse_jsonl(text)

    def test_completed_turn_requires_final_marker_not_prompt_or_tool_echo(self):
        events = [
            {"type": "thread.started", "thread_id": "abc"},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "EXEC_JSON_OK"}},
            {"type": "turn.completed", "usage": {}},
        ]
        require_turn(events, "EXEC_JSON_OK")
        with self.assertRaises(RuntimeError):
            require_turn(events[:-1], "EXEC_JSON_OK")
        events[1]["item"]["type"] = "command_execution"
        with self.assertRaises(RuntimeError):
            require_turn(events, "EXEC_JSON_OK")
        events[1]["item"]["type"] = "agent_message"
        events.insert(2, {"type": "item.completed", "item": {"type": "agent_message", "text": "failed"}})
        with self.assertRaises(RuntimeError):
            require_turn(events, "EXEC_JSON_OK")

    def test_mcp_gate_accepts_nested_versions_but_requires_completed_tool_evidence(self):
        events = [
            {"type": "thread.started", "thread_id": "abc"},
            {"type": "item.completed", "payload": {"item": {
                "type": "mcp_tool_call", "invocation": {"name": "browser_navigate"},
                "result": [{"text": "JS_READY"}], "status": "completed",
            }}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "PLAYWRIGHT_MCP_OK"}},
            {"type": "turn.completed"},
        ]
        require_mcp(events, "browser_navigate", "PLAYWRIGHT_MCP_OK", "JS_READY")
        for field, value in (("status", "failed"), ("type", "agent_message")):
            item = events[1]["payload"]["item"]
            original = item[field]
            item[field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                require_mcp(events, "browser_navigate", "PLAYWRIGHT_MCP_OK", "JS_READY")
            item[field] = original
        events[1]["type"] = "item.started"
        with self.assertRaises(RuntimeError):
            require_mcp(events, "browser_navigate", "PLAYWRIGHT_MCP_OK", "JS_READY")

    def test_mcp_error_result_cannot_pass_even_if_model_claims_success(self):
        events = [
            {"type": "thread.started"},
            {"type": "item.completed", "item": {"type": "mcp_tool_call", "tool": "searxng_web_search",
                                                     "result": {"isError": True}}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "SEARCH_MCP_OK"}},
            {"type": "turn.completed"},
        ]
        with self.assertRaises(RuntimeError):
            require_mcp(events, "searxng_web_search", "SEARCH_MCP_OK")

    def test_checker_command_requires_zero_exit_code(self):
        events = [{"type": "item.completed", "item": {
            "type": "command_execution", "command": "python3 check_result.py",
            "aggregated_output": "SHELL_CHECK_OK", "exit_code": 0,
        }}]
        require_command(events, "check_result.py", "SHELL_CHECK_OK")
        events[0]["item"]["exit_code"] = 1
        with self.assertRaises(RuntimeError):
            require_command(events, "check_result.py", "SHELL_CHECK_OK")

    def test_isolated_home_copies_config_but_never_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing"
            existing.mkdir()
            (existing / "config.toml").write_text('model="demo-model"\nmodel_provider="demo"\n'
                '[model_providers.demo]\nbase_url="http://llm:1235/v1"\nwire_api="responses"\nenv_key="DEMO_KEY"\n')
            (existing / "auth.json").write_text('{"secret":"must-not-copy"}')
            (existing / "other.config.toml").write_text('model="unselected"\n')
            isolated = root / "isolated"
            isolated.mkdir()
            env = {"CODEX_HOME": str(existing), "DEMO_KEY": "exported",
                   "LLM_MODEL": "demo-model", "LLM_BASE_URL": "http://llm:1235/v1"}
            isolated_codex_home(isolated, env)
            copied = Path(env["CODEX_HOME"])
            self.assertFalse((copied / "auth.json").exists())
            self.assertFalse((copied / "other.config.toml").exists())
            self.assertEqual((copied / "config.toml").read_bytes(), (existing / "config.toml").read_bytes())
            self.assertEqual(copied.stat().st_mode & 0o777, 0o700)
            self.assertEqual((copied / "config.toml").stat().st_mode & 0o777, 0o600)

    def test_isolated_home_uses_opt_in_profile_without_changing_openai_base(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            existing = root / "existing"
            existing.mkdir()
            base = '# OpenAI default remains selected by plain codex\n'
            profile = ('model="demo-model"\nmodel_provider="openweight_lan"\n'
                       '[model_providers.openweight_lan]\nbase_url="http://llm:1235/v1"\n'
                       'wire_api="responses"\nrequires_openai_auth=false\n')
            (existing / "config.toml").write_text(base)
            (existing / "openweight.config.toml").write_text(profile)
            (existing / "auth.json").write_text('{"secret":"must-not-copy"}')
            env = {"CODEX_HOME": str(existing), "CODEX_PROFILE": "openweight",
                   "LLM_MODEL": "demo-model", "LLM_BASE_URL": "http://llm:1235/v1"}

            isolated_codex_home(root, env)

            copied = Path(env["CODEX_HOME"])
            self.assertEqual((copied / "config.toml").read_text(), base)
            self.assertEqual((copied / "openweight.config.toml").read_text(), profile)
            self.assertFalse((copied / "auth.json").exists())

    def test_profile_catalog_is_relocated_for_all_codex_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "config.toml").write_text('model="openai-default"\nmodel_provider="openai"\n')
            catalog = source / "qwen.json"
            catalog.write_text(json.dumps({"models": [{"slug": "qwen/test", "context_window": 32768,
                                                       "input_modalities": ["text", "image"]}]}))
            profile = ('model="qwen/test"\nmodel_provider="lan"\n'
                       f'model_catalog_json={json.dumps(str(catalog))}\n'
                       '[model_providers.lan]\nname="LAN"\nbase_url="http://llm/v1"\n')
            (source / "lan.config.toml").write_text(profile)
            env = {"CODEX_HOME": str(source), "CODEX_PROFILE": "lan", "LLM_MODEL": "qwen/test",
                   "LLM_BASE_URL": "http://llm/v1", "LLM_CONTEXT_WINDOW": "32768", "LLM_SUPPORTS_IMAGE": "true"}
            isolated_codex_home(root, env)
            command = verifier.codex_command(env, "debug", "models")
            override = next(arg for arg in command if arg.startswith("model_catalog_json="))
            copied = Path(json.loads(override.split("=", 1)[1]))
            self.assertTrue(copied.is_relative_to(Path(env["CODEX_HOME"])))
            self.assertEqual(copied.read_bytes(), catalog.read_bytes())
            self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
            self.assertEqual((source / "lan.config.toml").read_text(), profile)

    def test_profile_cannot_inherit_a_different_review_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.toml").write_text('review_model="openai-review"\n')
            (root / "lan.config.toml").write_text('model="qwen/test"\nmodel_provider="lan"\n'
                '[model_providers.lan]\nname="LAN"\nbase_url="http://llm/v1"\n')
            env = {"CODEX_HOME": str(root), "CODEX_PROFILE": "lan", "LLM_MODEL": "qwen/test", "LLM_BASE_URL": "http://llm/v1"}
            with self.assertRaisesRegex(RuntimeError, "review_model"):
                isolated_codex_home(root, env)

    def test_dotenv_is_literal_and_existing_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('# comment\nA="two words"\nB=$(touch bad)\nKEEP=file\nEMPTY=\n')
            env = {"KEEP": "exported"}
            load_env(path, env)
            self.assertEqual(env, {"A": "two words", "B": "$(touch bad)", "KEEP": "exported", "EMPTY": ""})
            self.assertFalse((Path(directory) / "bad").exists())
            for line in ("export A=value", "source other.env", "BAD-NAME=value"):
                path.write_text(line)
                with self.subTest(line=line), self.assertRaisesRegex(RuntimeError, "KEY=VALUE"):
                    load_env(path, {})


if __name__ == "__main__":
    unittest.main()
