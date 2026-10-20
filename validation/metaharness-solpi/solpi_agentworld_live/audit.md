# Audit: solpi_agentworld_live

All independent checks passed: **True**

| check | passed / total |
|---|---|
| gate_arithmetic_recomputed | 1/1 |
| gate_used_base_metrics_of_round | 4/4 |
| no_sealed_eval_in_lineages | 5/5 |
| oracle_selection_recomputed | 1/1 |
| variant_walk_rule | 0/0 |
| firewall_trace_eq_sink_eq_driver | 1/1 |
| firewall_verdict_recomputed | 0/0 |
| only_firewall_survivors_composed | 1/1 |
| composition_is_union | 0/0 |
| diff_is_actual | 4/4 |

## Rows

- `{"trace_round": 2, "idea": "L2", "kind_truth": "general", "iteration": 0, "change": "condense_failing_logs", "variant": 0, "error": "exit check failed: ImportError: cannot import name 'ToolResult' from 'anthropic.types' (/usr/local/lib/python3.11/dist-packages/anthropic/types/__init__.py)", "ralph_repairs": 2, "files_changed": ["extensions/condense_failing_logs.py", "harness.json"], "outcome": "abandoned", "next": "next iteration (01 new rollouts)"}`
- `{"trace_round": 3, "idea": "L2", "kind_truth": "general", "iteration": 1, "change": "condense_failing_logs", "variant": 1, "error": null, "ralph_repairs": 1, "files_changed": ["extensions/condense_failing_logs.py", "harness.json"], "review": "reject: Criterion 3 violation: Full logs are stored in rt.store with an opaque key (bash_log_{id(event)}), but the agent has no discoverable way to retrieve them. The a", "outcome": "rejected", "next": "lineage ends (max_iters)"}`
- `{"trace_round": 4, "idea": "L1", "kind_truth": "general", "iteration": 0, "change": "large_output_cache", "variant": 0, "error": null, "ralph_repairs": 1, "files_changed": ["extensions/large_output_cache.py", "harness.json"], "review": "pass", "gate": false, "reason": "no efficiency gain", "score": 1.0, "saving_tokens": 0.0, "saving_cost": 0.0, "cap_failed": false, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 5, "idea": "L1", "kind_truth": "general", "iteration": 1, "change": "Cache large tool outputs separately and project summaries in context to eliminate token replay", "variant": 1, "error": null, "ralph_repairs": 0, "files_changed": ["extensions/output_reference_deduplication.py", "harness.json"], "review": "reject: Requirement 3 violation: No retrieval mechanism demonstrated. The mechanism stores outputs with reference keys (e.g., 'output_call_id') in rt.store and mentions", "outcome": "rejected", "next": "lineage ends (max_iters)"}`

## firewall

```json
[]
```

## ideas

```json
{
 "L2": {
  "kind_truth": "general",
  "frozen": false,
  "survived": false
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
[]
```
