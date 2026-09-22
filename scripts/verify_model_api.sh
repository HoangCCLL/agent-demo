#!/usr/bin/env bash
# Live gateway preflight only. Does not install/run Codex or execute MCP tools.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
env_file="${1:-.env}"
[[ -f "$env_file" ]] || { echo "FAIL  missing env file: $env_file"; exit 1; }
set -a
source "$env_file"
set +a

: "${LLM_BASE_URL:?LLM_BASE_URL is required}"
: "${LLM_MODEL:?LLM_MODEL is required}"
: "${LLM_API_KEY:?Set LLM_API_KEY to the service gateway key}"
if [[ "$LLM_API_KEY" == *replace-with* ]]; then
  echo "FAIL  replace the LLM_API_KEY placeholder with the gateway key"
  exit 1
fi

python3 -u check_codex_api.py \
  --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" \
  --api-key-env LLM_API_KEY --check-auth --check-namespaces \
  --continuation-mode "${LLM_CONTINUATION_MODE:-input-history}"
echo
echo "MODEL_API_READY"
