#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
set -a
source .env
set +a
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
search_url="${SEARCH_MCP_URL:-http://127.0.0.1:${SEARCH_MCP_PORT}/mcp}"
playwright_url="${PLAYWRIGHT_MCP_URL:-http://127.0.0.1:${PLAYWRIGHT_MCP_PORT}/mcp}"

python3 -m unittest -v
docker compose config --quiet
scripts/verify_local.sh
bash scripts/verify_model_api.sh .env
python3 verify_search_mcp.py --url "$search_url"
python3 verify_playwright_mcp.py \
  --url "$playwright_url" \
  --screenshot-output "$work_dir/vision.png" \
  --vision-marker VISION_7F31
python3 verify_model_runtime.py \
  --base-url "$LLM_BASE_URL" \
  --model "$LLM_MODEL" \
  --api-key-env LLM_API_KEY \
  --continuation-mode "${LLM_CONTINUATION_MODE:-input-history}" \
  --concurrency "${LLM_CONCURRENCY:-3}" \
  --vision-image "$work_dir/vision.png" \
  --vision-expected VISION_7F31

echo
echo "PHASE11_LOCAL_READY"
