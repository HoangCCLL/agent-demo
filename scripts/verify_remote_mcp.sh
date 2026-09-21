#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
env_file="${1:-.env}"
[[ -f "$env_file" ]] || { echo "FAIL  missing env file: $env_file"; exit 1; }

set -a
source "$env_file"
set +a

: "${MCP_AUTH_TOKEN:?MCP_AUTH_TOKEN is required}"
: "${SEARCH_MCP_URL:?SEARCH_MCP_URL is required}"
: "${PLAYWRIGHT_MCP_URL:?PLAYWRIGHT_MCP_URL is required}"

python3 verify_search_mcp.py --url "$SEARCH_MCP_URL"
python3 verify_playwright_mcp.py --url "$PLAYWRIGHT_MCP_URL"
echo
echo "REMOTE_MCP_READY"
