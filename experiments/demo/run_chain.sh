#!/usr/bin/env bash
# Run the live demo jobs one after another (one at a time keeps the account under its usage cap).
# A job is either live_demo.py arguments ("metaharness --suite hard") or another demo script with its arguments
# ("rescore.py metaharness_hard", "leak_ablation.py").
#
# The chain stops at the first job that exits non-zero or whose log shows lost model calls (usage cap, infrastructure
# errors, missing trials). To continue after the cause is fixed (e.g. the cap has reset), start a new chain from the
# job that stopped, with --resume added to it, and leave out the jobs that already finished (a job whose run
# directory holds a finished run is refused, which stops the chain): live_demo.py moves the unfinished attempt to
# demo/runs/_archive/ and restarts it, replaying every call its LLM cache already holds for $0.
#
# With --check first, the jobs are checks of committed results: their logs, chain log and any archived log go to a
# new directory under ${TMPDIR:-/tmp}, and nothing under demo/runs is archived or appended to.
#
# Usage: experiments/demo/run_chain.sh "metaharness --suite hard" "rescore.py metaharness_hard" ...
#        experiments/demo/run_chain.sh --check "metaharness --replay-check" "rescore.py" "spend.py"   # $0 checks
cd "$(dirname "$0")/../.."
logs=demo/runs
archive=demo/runs/_archive
if [ "$1" = "--check" ]; then
  shift
  base="${TMPDIR:-/tmp}"
  mkdir -p "$base" && logs="$(mktemp -d "$base/rsi_checks_$(date -u +%Y%m%dT%H%M%SZ)_XXXX")" \
    || { echo "cannot create a log directory under $base" >&2; exit 2; }
  archive="$logs/_archive"
fi
chain="$logs/chain.log"
for job in "$@"; do
  log="$logs/$(echo "$job" | tr ' /-' '___' | tr -s '_' | sed 's/\.py//').log"
  if [ -e "$log" ]; then   # never overwrite an earlier attempt's log
    mkdir -p "$archive"
    mv "$log" "$archive/$(basename "$log" .log)_$(date -u +%Y%m%dT%H%M%SZ)_$$_$RANDOM.log"
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
