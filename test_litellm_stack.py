"""Offline deployment checks: Compose rendering and local env generation only."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent


class LiteLLMStackTest(unittest.TestCase):
    def compose(self, **overrides):
        if not shutil.which("docker"):
            self.skipTest("Docker Compose is required for config rendering (no daemon used)")
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("LLM_", "LMSTUDIO_", "LITELLM_", "MCP_", "SEARCH_MCP_",
                                      "PLAYWRIGHT_", "SEARXNG_", "COMPOSE_"))}
        values = {"MCP_AUTH_TOKEN": "test-mcp", "SEARXNG_SECRET": "test-search",
                  "LITELLM_MASTER_KEY": "sk-test-gateway", "LLM_MODEL": "qwen/test",
                  "LMSTUDIO_BASE_URL": "http://model.example.invalid:1235/v1",
                  "LMSTUDIO_API_KEY": "test-upstream"}
        values.update(overrides)
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory, "config.env")
            env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
            return subprocess.run(
                ["docker", "compose", "--env-file", str(env_file), "-f", str(ROOT / "compose.yaml"),
                 "config", "--format", "json"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
            )

    def test_gateway_routes_configured_model_without_exposing_upstream_credentials_to_mcp(self):
        result = self.compose()
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        self.assertIn("litellm", services)
        gateway = services["litellm"]
        self.assertEqual(gateway["ports"][0]["host_ip"], "127.0.0.1")
        self.assertEqual(str(gateway["ports"][0]["published"]), "4000")
        self.assertEqual(gateway["environment"]["LITELLM_UPSTREAM_MODEL"], "openai/qwen/test")
        self.assertEqual(gateway["environment"]["LMSTUDIO_BASE_URL"], "http://model.example.invalid:1235/v1")
        self.assertEqual(gateway["environment"]["LMSTUDIO_API_KEY"], "test-upstream")
        self.assertEqual(gateway["environment"]["LITELLM_MASTER_KEY"], "sk-test-gateway")
        for name, service in services.items():
            if name != "litellm":
                self.assertNotIn("test-upstream", json.dumps(service))
                self.assertNotIn("sk-test-gateway", json.dumps(service))

    def test_gateway_requires_key_and_honors_explicit_lan_binding(self):
        result = self.compose(LITELLM_MASTER_KEY="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("LITELLM_MASTER_KEY", result.stderr)
        result = self.compose(LITELLM_BIND_ADDRESS="192.0.2.20", LITELLM_PORT="14000")
        self.assertEqual(result.returncode, 0, result.stderr)
        port = json.loads(result.stdout)["services"]["litellm"]["ports"][0]
        self.assertEqual((port["host_ip"], str(port["published"]), port["target"]),
                         ("192.0.2.20", "14000", 4000))

    def run_setup(self, directory, existing=None):
        root = Path(directory)
        (root / "scripts").mkdir()
        shutil.copyfile(ROOT / "scripts/setup.sh", root / "scripts/setup.sh")
        shutil.copyfile(ROOT / ".env.example", root / ".env.example")
        # Replace only the external Docker and live-verification boundaries.
        (root / "bin").mkdir()
        docker = root / "bin/docker"
        docker.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_DOCKER_LOG"\n')
        docker.chmod(0o700)
        verify = root / "scripts/verify_all.sh"
        verify.write_text("#!/bin/sh\nexit 0\n")
        verify.chmod(0o700)
        if existing is not None:
            (root / ".env").write_text(existing)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("LLM_", "LITELLM_", "LMSTUDIO_"))}
        env.update(PATH=str(root / "bin") + os.pathsep + env["PATH"],
                   TEST_DOCKER_LOG=str(root / "docker.log"))
        return subprocess.run(["bash", str(root / "scripts/setup.sh")], env=env,
                              capture_output=True, text=True, timeout=10)

    def test_setup_generates_matching_gateway_client_keys_without_touching_existing_env(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_setup(directory)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            path = Path(directory, ".env")
            values = dict(line.split("=", 1) for line in path.read_text().splitlines()
                          if line and not line.startswith("#"))
            self.assertIn("LITELLM_MASTER_KEY", values)
            self.assertEqual(values["LLM_API_KEY"], values["LITELLM_MASTER_KEY"])
            self.assertTrue(values["LLM_API_KEY"].startswith("sk-"))
            self.assertNotIn("replace", values["LLM_API_KEY"])
            self.assertNotEqual(values["LLM_API_KEY"], values["MCP_AUTH_TOKEN"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

        with tempfile.TemporaryDirectory() as directory:
            original = "MCP_AUTH_TOKEN=old-token\nSEARXNG_SECRET=old-secret\n"
            result = self.run_setup(directory, original)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("LITELLM_MASTER_KEY", result.stdout + result.stderr)
            self.assertEqual(Path(directory, ".env").read_text(), original)
            self.assertNotIn("up -d", Path(directory, "docker.log").read_text())


if __name__ == "__main__":
    unittest.main()
