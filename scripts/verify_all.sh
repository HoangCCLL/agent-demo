#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
set -a
source .env
set +a

python3 -m unittest -v
docker compose config --quiet
scripts/verify_local.sh
python3 check_codex_api.py --base-url "$LLM_BASE_URL" --model "$LLM_MODEL"
python3 verify_model_runtime.py --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" --concurrency "${LLM_CONCURRENCY:-3}"
python3 verify_search_mcp.py --url "http://127.0.0.1:${SEARCH_MCP_PORT}/mcp"
python3 verify_playwright_mcp.py --url "http://127.0.0.1:${PLAYWRIGHT_MCP_PORT}/mcp"

if [[ -n "${RAG_CORPUS_PATH:-}" ]]; then
  [[ -d "$RAG_CORPUS_PATH" ]] || { echo "FAIL  RAG_CORPUS_PATH is not a directory"; exit 1; }
  echo "WARN  RAG corpus configured; retrieval quality needs a corpus-specific evaluation set"
else
  echo "SKIP  RAG: no external corpus configured; Codex will use filesystem/rg"
fi

echo
echo "PHASE1_LOCAL_READY"
