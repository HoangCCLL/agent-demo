import os
import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


class InstallCodexProfileTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("codex"), "Codex config-only probe runs where the CLI is installed")
    def test_installed_codex_loads_profile_and_catalog_without_changing_default(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_dir = root / "codex"
            codex_dir.mkdir()
            (codex_dir / "config.toml").write_text('# OpenAI default\n')
            env_file = root / "client.env"
            env_file.write_text("CODEX_PROFILE=local-probe\nLLM_MODEL=qwen/test\n"
                "LLM_CONTEXT_WINDOW=32768\nLLM_SUPPORTS_IMAGE=false\n"
                "LLM_BASE_URL=http://127.0.0.1:1/v1\n"
                "SEARCH_MCP_URL=http://127.0.0.1:1/search\nPLAYWRIGHT_MCP_URL=http://127.0.0.1:1/browser\n")
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(("LLM_", "CODEX_", "SEARCH_MCP_", "PLAYWRIGHT_MCP_"))}
            env.update(CODEX_HOME=str(codex_dir), MCP_AUTH_TOKEN="probe-token", LLM_API_KEY="probe-secret")
            scripts = Path(__file__).parent / "scripts"
            for script in ("install_codex_profile.py", "verify_codex_profile.py"):
                result = subprocess.run([sys.executable, scripts / script, "--env-file", env_file],
                                        env=env, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("CODEX_PROFILE_READY", result.stdout)
            self.assertEqual((codex_dir / "config.toml").read_text(), '# OpenAI default\n')
            profile = (codex_dir / "local-probe.config.toml").read_text()
            self.assertEqual(tomllib.loads(profile)["model_providers"]["openweight_lan"]["env_key"], "LLM_API_KEY")
            self.assertNotIn("probe-secret", profile)

    def test_installs_lan_profile_without_touching_default_or_storing_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            codex_home = root / "codex"
            codex_home.mkdir()
            default = '# OpenAI default\n'
            (codex_home / "config.toml").write_text(default)
            env_file = root / "client.env"
            env_file.write_text(
                "CODEX_PROFILE=lan-qwen\n"
                "LLM_MODEL=qwen/test\n"
                "LLM_CONTEXT_WINDOW=32768\n"
                "LLM_SUPPORTS_IMAGE=true\n"
                "LLM_BASE_URL=http://llm.lan:1235/v1\n"
                "SEARCH_MCP_URL=http://mcp.lan:18081/mcp\n"
                "PLAYWRIGHT_MCP_URL=http://mcp.lan:18082/mcp\n"
                "MCP_AUTH_TOKEN=must-not-be-written\n"
            )
            script = Path(__file__).parent / "scripts" / "install_codex_profile.py"
            clean_env = os.environ.copy()
            for key in ("CODEX_PROFILE", "LLM_MODEL", "LLM_BASE_URL", "SEARCH_MCP_URL",
                        "PLAYWRIGHT_MCP_URL", "MCP_AUTH_TOKEN", "LLM_CONTEXT_WINDOW",
                        "LLM_SUPPORTS_IMAGE", "LLM_API_KEY"):
                clean_env.pop(key, None)

            result = subprocess.run(
                [sys.executable, script, "--env-file", env_file, "--codex-home", codex_home],
                capture_output=True,
                text=True,
                env={**clean_env, "PYTHONDONTWRITEBYTECODE": "1"},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((codex_home / "config.toml").read_text(), default)
            profile_path = codex_home / "lan-qwen.config.toml"
            profile = tomllib.loads(profile_path.read_text())
            self.assertEqual(profile["model"], "qwen/test")
            self.assertEqual(profile["model_provider"], "openweight_lan")
            self.assertEqual(profile["model_providers"]["openweight_lan"]["base_url"], "http://llm.lan:1235/v1")
            self.assertEqual(profile["mcp_servers"]["web_search"]["url"], "http://mcp.lan:18081/mcp")
            self.assertEqual(profile["mcp_servers"]["playwright"]["url"], "http://mcp.lan:18082/mcp")
            self.assertNotIn("must-not-be-written", profile_path.read_text())
            self.assertEqual(profile_path.stat().st_mode & 0o777, 0o600)
            self.assertIn("codex -p lan-qwen", result.stdout)
            catalog_path = Path(profile["model_catalog_json"])
            self.assertEqual(catalog_path, codex_home / "catalogs/lan-qwen.models.json")
            catalog = json.loads(catalog_path.read_text())
            self.assertEqual([m["slug"] for m in catalog["models"]], ["qwen/test"])
            self.assertEqual(catalog["models"][0]["context_window"], 32768)
            self.assertEqual(catalog["models"][0]["input_modalities"], ["text", "image"])
            self.assertFalse(catalog["models"][0]["supports_search_tool"])
            self.assertEqual(profile["web_search"], "disabled")
            self.assertNotIn("must-not-be-written", catalog_path.read_text())
            self.assertEqual(catalog_path.stat().st_mode & 0o777, 0o600)

            # A rerun must be harmless, and may not overwrite operator edits.
            command = [sys.executable, script, "--env-file", env_file, "--codex-home", codex_home]
            rerun = subprocess.run(command, capture_output=True, text=True, env=clean_env)
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            profile_path.write_text("# operator customization\n")
            before = catalog_path.read_bytes()
            conflict = subprocess.run(command, capture_output=True, text=True, env=clean_env)
            self.assertNotEqual(conflict.returncode, 0)
            self.assertEqual(profile_path.read_text(), "# operator customization\n")
            self.assertEqual(catalog_path.read_bytes(), before)

    def test_invalid_capabilities_or_default_route_fail_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.toml").write_text('# OpenAI default\n')
            env_file = root / "client.env"
            valid = ("LLM_MODEL=qwen/test\nLLM_CONTEXT_WINDOW=32768\nLLM_SUPPORTS_IMAGE=false\n"
                     "LLM_BASE_URL=http://llm.lan/v1\nSEARCH_MCP_URL=http://mcp.lan/search\n"
                     "PLAYWRIGHT_MCP_URL=http://mcp.lan/browser\n")
            script = Path(__file__).parent / "scripts/install_codex_profile.py"
            clean_env = {key: value for key, value in os.environ.items()
                         if not key.startswith(("LLM_", "CODEX_", "SEARCH_MCP_", "PLAYWRIGHT_MCP_"))}
            for change in ("LLM_CONTEXT_WINDOW=0", "LLM_CONTEXT_WINDOW=oops", "LLM_SUPPORTS_IMAGE=maybe"):
                key = change.split("=")[0]
                env_file.write_text("\n".join(line for line in valid.splitlines()
                                               if not line.startswith(key + "=")) + "\n" + change + "\n")
                result = subprocess.run([sys.executable, script, "--env-file", env_file,
                                         "--codex-home", root], env=clean_env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, change)
                self.assertFalse((root / "openweight.config.toml").exists())
                self.assertFalse((root / "catalogs").exists())
            env_file.write_text(valid)
            (root / "config.toml").write_text('model_provider="old_lan"\n')
            result = subprocess.run([sys.executable, script, "--env-file", env_file,
                                     "--codex-home", root], env=clean_env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "openweight.config.toml").exists())


if __name__ == "__main__":
    unittest.main()
