# GEPA: from-scratch validation runs

Each run starts from its domain's **untouched seed artifact**. It uses a fresh run directory `validation/gepa/<run>/`, which is deleted first. The live run also uses a fresh LLM cache, `validation/gepa/.cache_agentqa_live/`. Nothing is reused from `.rsi_cache` or earlier results.

Every run was produced by `python experiments/gepa/validate_gepa.py <run>`. The engine writes `trace.jsonl`, the `rsi.trace` format, one iteration per round:
- parent selection, with the Pareto sampling weights;
- every evaluation, with per-task and raw trial scores;
- the reflective records;
- the reflection prompt and reply;
- the **actual** diff;
- the gate arithmetic;
- the decision;
- the loop state before and after.

The shadow monitor scores every new incumbent on the sealed splits. The loop never sees those numbers.

Files in each run directory:

| file | contents |
|---|---|
| `TRACE.md` | the rendered trace (`rsi.trace.inspect`) |
| `report.json` | seed vs final on every split, via `rsi.core.transfer_report` (report-only unsealing); spend from the meters; the rollout breakdown |
| `audit.md` / `audit.json` | one row per iteration, re-derived **independently** from the trace, the ledger and the artifact store |
| `ledger.jsonl`, `artifacts/`, `state.json`, `run_log.jsonl` | the engine's own records |

**Independent checks in `audit.md`.**
- Gate arithmetic.
- The recorded diff equals `parent.diff(child)` from the artifact store.
- Every decision agrees with its ledger node.
- The Pareto sampling weights are recomputed with a separate implementation of paper Alg. 2 / spec §3.
- The round-robin component choice is re-simulated.
- Every epoch's minibatches are disjoint apart from padding.
- The child is evaluated on the same minibatch ids as its parent.
- Every accepted child is scored on all of D_pareto.
- The returned candidate is argmax of the mean D_pareto score.
- The rollouts charged in the trace sum to the engine's counter.

**Ground truth, where one exists.**
- RuleWorld: the exact analytic expected score.
- AgentQA + SimModel: an estimate from 6 fresh seeds per task.

The audit uses it to ask whether each accepted or rejected step was *really* better or worse. A step with no ground truth (the live run) is marked **unverifiable**, not correct.

## Summary

| run | setup | rollouts used / budget | iterations | proposals: accepted / rejected / skipped | seed → final, D_pareto | seed → final, sealed | spend |
|---|---|---|---|---|---|---|---|
| `ruleworld_offline` | RuleWorld, rich feedback, mock reflection LM, GEPA defaults | 318 / 300 | 8 | 8 / 0 / 0 | 0.000 → 0.322 | test (300): 0.000 → 0.318 sampled; **exact 0.000 → 0.314** (oracle 0.950) | $0 |
| `ruleworld_merge_offline` (extra) | same, GEPA+Merge | 1534 / 1500 | 59 | reflective 32 / 13 / 3 skip; merges 7 accepted / 4 rejected | 0.000 → 0.898 | test: 0.000 → 0.857 sampled; **exact 0.000 → 0.847** | $0 |
| `agentqa_offline` | AgentQA two-module harness, SimModel + mock reflection LM | 121 / 120 | 20 | 4 / 3 / 13 skip_perfect | 0.375 → 1.000 | holdout (6) 0.667 → 1.000; ood (4) 0.500 → 1.000 | $0 |
| `agentqa_live` | AgentQA two-module harness, **claude haiku as task model and reflection LM** | see below | | | | | see below |

`agentqa_offline_mockbug/` keeps the first `agentqa_offline` run. It exposed a bug in the offline AgentQA mock reflection LM, described under run 3.
