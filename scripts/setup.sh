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
  gateway_key="sk-$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  sed \
    -e "s/replace-with-a-random-token/$mcp_token/" \
    -e "s/replace-with-a-random-secret/$searxng_secret/" \
    -e "s/sk-replace-with-a-random-gateway-key/$gateway_key/g" \
    .env.example > .env
  echo "PASS  generated .env with mode 600"
else
  echo "PASS  using existing .env"
fi

# Existing private env files are operator-owned; do not silently rewrite them.
set -a
source .env
set +a
for variable in LITELLM_MASTER_KEY LMSTUDIO_BASE_URL LLM_BASE_URL LLM_API_KEY LLM_CONTINUATION_MODE; do
  if [[ -z "${!variable:-}" || "${!variable}" == *replace-with* ]]; then
    echo "FAIL  set $variable in .env; merge gateway settings from .env.example (see docs/phase11-runbook.md)"
    exit 1
  fi
done

docker compose config --quiet
docker compose pull
docker compose up -d --wait --wait-timeout 180
echo "PASS  Docker MCP + model gateway stack is running"

exec scripts/verify_all.sh
