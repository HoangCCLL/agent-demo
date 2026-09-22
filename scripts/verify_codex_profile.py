#!/usr/bin/env python3
"""Check installed Codex profile/catalog configuration without any model requests."""

import argparse
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from verify_codex_vps import isolated_codex_home, load_env, verify_profile_config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--profile", help="override CODEX_PROFILE (default: openweight)")
    args = parser.parse_args()
    env = os.environ.copy()
    load_env(args.env_file, env)
    env["CODEX_PROFILE"] = args.profile or env.get("CODEX_PROFILE", "openweight")
    with tempfile.TemporaryDirectory(prefix="codex-profile-") as directory:
        root = Path(directory)
        isolated_codex_home(root, env)
        verify_profile_config(root, env, 30)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as error:
        sys.exit(f"FAIL  {error}")
