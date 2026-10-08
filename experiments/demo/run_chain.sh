#!/usr/bin/env bash
# Run the live demos one after another (one at a time keeps the account under its usage cap).
# Stops the chain if a run hits the cap: wait for the reset and run this script again. The LLM caches stay, and
# since a run's prompts are deterministic, a rerun replays every finished step for $0 and continues live.
# Usage: experiments/demo/run_chain.sh "metaharness" "metaharness --replay-check" "metaharness --suite hard" ...
cd "$(dirname "$0")/../.."
for job in "$@"; do
  log="demo/runs/$(echo "$job" | tr ' -' '__' | tr -s '_').log"
  echo "== $(date -u +%FT%TZ) $job -> $log"
  python experiments/demo/live_demo.py $job > "$log" 2>&1
  code=$?
  echo "EXIT $code" >> "$log"
  if grep -q "session limit" "$log" || grep -q "INFRA_ERRORS" "$log"; then
    echo "== stopping: usage cap or infrastructure errors in $log"
    exit 3
  fi
done
echo "== chain done"
