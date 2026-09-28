# Audit: solpi_agentworld_live_r5

All independent checks passed: **True**

| check | passed / total |
|---|---|
| gate_arithmetic_recomputed | 4/4 |
| gate_used_base_metrics_of_round | 4/4 |
| no_sealed_eval_in_lineages | 8/8 |
| oracle_selection_recomputed | 1/1 |
| variant_walk_rule | 0/0 |
| firewall_trace_eq_sink_eq_driver | 1/1 |
| firewall_verdict_recomputed | 1/1 |
| only_firewall_survivors_composed | 1/1 |
| composition_is_union | 0/0 |
| diff_is_actual | 6/6 |

## Rows

- `{"trace_round": 2, "idea": "L2", "kind_truth": "general", "iteration": 0, "change": "condense_failing_logs", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["extensions/condense_failing_logs.py", "harness.json"], "review": "pass", "gate": true, "reason": "accepted", "score": 1.0, "saving_tokens": 0.6665, "saving_cost": 0.4579, "cap_failed": false, "outcome": "frozen", "next": "passing variant recorded; sweep continues"}`
- `{"trace_round": 3, "idea": "L2", "kind_truth": "general", "iteration": 1, "change": "Intercept bash tool errors; condense large outputs to failure-carrying lines while storing full logs in /.solpi/ for recall", "variant": 1, "error": null, "ralph_repairs": 0, "files_changed": ["extensions/condense_failing_logs.py", "harness.json"], "review": "pass", "gate": true, "reason": "accepted", "score": 1.0, "saving_tokens": 0.6656, "saving_cost": 0.4572, "cap_failed": false, "outcome": "frozen", "next": "passing variant recorded; sweep continues"}`
- `{"trace_round": 4, "idea": "L1", "kind_truth": "general", "iteration": 0, "change": "recallable_large_outputs", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["extensions/recallable_large_outputs.py", "harness.json"], "review": "pass", "gate": false, "reason": "no efficiency gain", "score": 1.0, "saving_tokens": -0.6696, "saving_cost": -0.3968, "cap_failed": false, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 5, "idea": "L1", "kind_truth": "general", "iteration": 1, "change": "Cache large tool outputs and deduplicate them in context to reduce repeated large outputs and context growth.", "variant": 1, "error": null, "ralph_repairs": 0, "files_changed": ["extensions/sparse_outputs.py", "harness.json"], "review": "pass", "gate": false, "reason": "no efficiency gain", "score": 1.0, "saving_tokens": 0.0, "saving_cost": 0.0, "cap_failed": false, "outcome": "gate_failed", "next": "lineage ends (max_iters)"}`

## firewall

```json
[
 {
  "candidate": "L2:condense_failing_logs",
  "passed": true,
  "reason": "accepted",
  "heldout_score": 1.0,
  "base_heldout_score": 1.0,
  "tokens_saving": 0.3845,
  "cost_saving": 0.2451
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
 "condense_failing_logs"
]
```
