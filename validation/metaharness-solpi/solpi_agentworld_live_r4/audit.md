# Audit: solpi_agentworld_live_r4

All independent checks passed: **True**

| check | passed / total |
|---|---|
| gate_arithmetic_recomputed | 2/2 |
| gate_used_base_metrics_of_round | 3/3 |
| no_sealed_eval_in_lineages | 5/5 |
| oracle_selection_recomputed | 1/1 |
| variant_walk_rule | 0/0 |
| firewall_trace_eq_sink_eq_driver | 1/1 |
| firewall_verdict_recomputed | 1/1 |
| only_firewall_survivors_composed | 1/1 |
| composition_is_union | 0/0 |
| diff_is_actual | 4/4 |

## Rows

- `{"trace_round": 2, "idea": "L2", "kind_truth": "general", "iteration": 0, "change": "Condense large bash failures to essential error/context lines, archive full logs for agent recall via rt.store", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["extensions/condense_bash_failures.py", "harness.json"], "review": "pass", "gate": true, "reason": "accepted", "score": 1.0, "saving_tokens": 0.6647, "saving_cost": 0.4565, "cap_failed": false, "outcome": "frozen", "next": "freeze; lineage ends"}`
- `{"trace_round": 3, "idea": "L1", "kind_truth": "general", "iteration": 0, "change": "reduce_large_output_replay", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["extensions/reduce_large_output_replay.py", "harness.json"], "review": "pass", "gate": false, "reason": "no efficiency gain", "score": 1.0, "saving_tokens": -0.6056, "saving_cost": -0.3245, "cap_failed": false, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 4, "idea": "L1", "kind_truth": "general", "iteration": 1, "change": "", "variant": 0, "error": "no extensions/<name>.py in reply", "ralph_repairs": 2, "files_changed": [], "review": "reject: CRITICAL: No explicit fail-open error handling. The `filter_context` method lacks try-except wrapping around its logic. The contract requires 'on any exception ", "outcome": "rejected", "next": "lineage ends (max_iters)"}`

## firewall

```json
[
 {
  "candidate": "L2:Condense large bash failures to essential error/context lines, archive full logs for agent recall via rt.store",
  "passed": true,
  "reason": "accepted",
  "heldout_score": 1.0,
  "base_heldout_score": 1.0,
  "tokens_saving": 0.3839,
  "cost_saving": 0.2447
 }
]
```

## ideas

```json
{
 "L2": {
  "kind_truth": "general",
  "frozen": true,
  "survived": true
 },
 "L1": {
  "kind_truth": "general",
  "frozen": false,
  "survived": false
 }
}
```

## composed_extensions

```json
[
 "condense_bash_failures"
]
```
