#!/usr/bin/env bash
set -euo pipefail

for command_name in git grep curl python3 node docker; do
  command -v "$command_name" >/dev/null || { echo "FAIL  missing $command_name"; exit 1; }
done
echo "PASS  local shell commands"

work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
printf 'needle\n' > "$work_dir/sample.txt"
if command -v rg >/dev/null; then
  rg -q needle "$work_dir/sample.txt"
  search_tool=rg
else
  grep -q needle "$work_dir/sample.txt"
  search_tool="grep (rg optional; install it on the Codex VPS)"
fi
python3 -c "from pathlib import Path; assert Path('$work_dir/sample.txt').read_text() == 'needle\n'"
node -e "const fs=require('fs'); if(!fs.readFileSync('$work_dir/sample.txt','utf8').includes('needle')) process.exit(1)"
echo "PASS  filesystem + $search_tool + Python + Node"

git -C "$work_dir" init -q
git -C "$work_dir" add sample.txt
git -C "$work_dir" -c user.name=Verifier -c user.email=verifier@example.invalid commit -qm initial
test -z "$(git -C "$work_dir" status --porcelain)"
echo "PASS  Git workflow"

(sleep 0.1; printf done > "$work_dir/background") &
job_pid=$!
wait "$job_pid"
test "$(cat "$work_dir/background")" = done
echo "PASS  background process + wait"

docker version >/dev/null
docker compose version >/dev/null
echo "PASS  Docker CLI"

if command -v gh >/dev/null; then
  echo "PASS  GitHub CLI installed"
else
  echo "WARN  GitHub CLI not installed; add on the Codex VPS only if GitHub workflows are needed"
fi
