#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$repo_dir/scripts/verify_client_primitives.sh"

command -v docker >/dev/null || { echo "FAIL  missing docker"; exit 1; }
docker version >/dev/null
docker compose version >/dev/null
echo "PASS  Docker CLI"
