#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

for command_name in docker python3; do
  command -v "$command_name" >/dev/null || { echo "FAIL  missing $command_name"; exit 1; }
done
docker compose version >/dev/null

render_template_secrets() {
  local source_file="$1"
  local target_file="$2"
  local mcp_token
  local searxng_secret
  local gateway_key
  mcp_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  searxng_secret="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  gateway_key="sk-replace-with-a-random-gateway-key"
  if grep -qx 'LITELLM_MASTER_KEY=sk-replace-with-a-random-gateway-key' "$source_file" \
    && grep -qx 'LLM_API_KEY=sk-replace-with-a-random-gateway-key' "$source_file"; then
    gateway_key="sk-$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  fi
  sed \
    -e "s|^MCP_AUTH_TOKEN=replace-with-a-random-token$|MCP_AUTH_TOKEN=$mcp_token|" \
    -e "s|^SEARXNG_SECRET=replace-with-a-random-secret$|SEARXNG_SECRET=$searxng_secret|" \
    -e "s|^LITELLM_MASTER_KEY=sk-replace-with-a-random-gateway-key$|LITELLM_MASTER_KEY=$gateway_key|" \
    -e "s|^LLM_API_KEY=sk-replace-with-a-random-gateway-key$|LLM_API_KEY=$gateway_key|" \
    "$source_file" > "$target_file"
}

has_template_secrets() {
  local source_file="$1"
  grep -qx 'MCP_AUTH_TOKEN=replace-with-a-random-token' "$source_file" \
    || grep -qx 'SEARXNG_SECRET=replace-with-a-random-secret' "$source_file" \
    || { grep -qx 'LITELLM_MASTER_KEY=sk-replace-with-a-random-gateway-key' "$source_file" \
      && grep -qx 'LLM_API_KEY=sk-replace-with-a-random-gateway-key' "$source_file"; }
}

if [[ ! -f .env ]]; then
  umask 077
  render_template_secrets .env.example .env
  echo "PASS  generated .env with mode 600"
elif has_template_secrets .env; then
  setup_temp_env="$(mktemp .env.setup.XXXXXX)"
  trap 'rm -f "$setup_temp_env"' EXIT
  render_template_secrets .env "$setup_temp_env"
  mv "$setup_temp_env" .env
  chmod 600 .env
  trap - EXIT
  echo "PASS  filled template secrets in existing .env"
else
  echo "PASS  using existing .env"
fi

# Existing private env files are operator-owned; only exact template placeholders are filled.
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
