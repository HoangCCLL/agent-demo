#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

for command_name in docker python3; do
  command -v "$command_name" >/dev/null || { echo "FAIL  missing $command_name"; exit 1; }
done
docker compose version >/dev/null

if [[ ! -f .env ]]; then
  umask 077
  mcp_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  searxng_secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sed \
    -e "s/replace-with-a-random-token/$mcp_token/" \
    -e "s/replace-with-a-random-secret/$searxng_secret/" \
    .env.example > .env
  echo "PASS  generated .env with mode 600"
else
  echo "PASS  using existing .env"
fi

docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 180
echo "PASS  Docker MCP stack is running"

exec scripts/verify_all.sh

