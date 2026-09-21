import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from verify_codex_vps import (
    CRITICAL_GATES, GateReport, isolated_codex_home, load_env, parse_jsonl,
    require_command, require_mcp, require_turn,
)


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
            (existing / "config.toml").write_text('model_provider="demo"\n[model_providers.demo]\nenv_key="DEMO_KEY"\n')
            (existing / "auth.json").write_text('{"secret":"must-not-copy"}')
            isolated = root / "isolated"
            isolated.mkdir()
            env = {"CODEX_HOME": str(existing), "DEMO_KEY": "exported"}
            isolated_codex_home(isolated, env)
            copied = Path(env["CODEX_HOME"])
            self.assertFalse((copied / "auth.json").exists())
            self.assertEqual((copied / "config.toml").read_bytes(), (existing / "config.toml").read_bytes())
            self.assertEqual(copied.stat().st_mode & 0o777, 0o700)
            self.assertEqual((copied / "config.toml").stat().st_mode & 0o777, 0o600)

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
