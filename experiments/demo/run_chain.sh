#!/usr/bin/env bash
# Run the live demo jobs one after another (one at a time keeps the account under its usage cap).
# A job is either live_demo.py arguments ("metaharness --suite hard") or another demo script with its arguments
# ("rescore.py metaharness_hard", "leak_ablation.py").
#
# The chain stops at the first job that exits non-zero or whose log shows lost model calls (usage cap, infrastructure
# errors, missing trials). To continue after the cause is fixed (e.g. the cap has reset), run the same chain again
# with --resume as a live_demo argument for the job that stopped: live_demo.py moves the unfinished attempt to
# demo/runs/_archive/ and restarts it, replaying every call its LLM cache already holds for $0. A job whose run
# directory holds a clean finished run is refused, which also stops the chain.
#
# Usage: experiments/demo/run_chain.sh "metaharness" "metaharness --replay-check" "rescore.py metaharness" ...
cd "$(dirname "$0")/../.."
chain=demo/runs/chain.log
for job in "$@"; do
  log="demo/runs/$(echo "$job" | tr ' /-' '___' | tr -s '_' | sed 's/\.py//').log"
  if [ -e "$log" ]; then   # never overwrite an earlier attempt's log
    mkdir -p demo/runs/_archive
    mv "$log" "demo/runs/_archive/$(basename "$log" .log)_$(date -u +%Y%m%dT%H%M%SZ).log"
  fi
  echo "== $(date -u +%FT%TZ) $job -> $log" | tee -a "$chain"
  first=${job%% *}
  if [[ "$first" == *.py ]]; then
    python "experiments/demo/$first" ${job#"$first"} > "$log" 2>&1
  else
    python experiments/demo/live_demo.py $job > "$log" 2>&1
  fi
  code=$?
  echo "EXIT $code" >> "$log"
  if [ $code -ne 0 ] || grep -qE "session limit|INFRA_ERRORS|trials missing|refusing to overwrite" "$log"; then
    echo "== $(date -u +%FT%TZ) stopping (exit $code): see $log" | tee -a "$chain"
    exit 3
  fi
done
echo "== $(date -u +%FT%TZ) chain done" | tee -a "$chain"
