# Audit: solpi_agentworld_offline

All independent checks passed: **True**

| check | passed / total |
|---|---|
| gate_arithmetic_recomputed | 15/15 |
| gate_used_base_metrics_of_round | 19/19 |
| no_sealed_eval_in_lineages | 34/34 |
| oracle_selection_recomputed | 1/1 |
| variant_walk_rule | 19/19 |
| firewall_trace_eq_sink_eq_driver | 1/1 |
| firewall_verdict_recomputed | 6/6 |
| only_firewall_survivors_composed | 1/1 |
| composition_is_union | 1/1 |
| diff_is_actual | 15/15 |

## Rows

- `{"trace_round": 2, "idea": "D1", "kind_truth": "general", "iteration": 0, "change": "evidence_preserving_reducer{\"reducer\": \"deterministic\"}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": true, "reason": "accepted", "score": 1.0, "saving_tokens": 0.486, "saving_cost": 0.3347, "cap_failed": false, "outcome": "frozen", "next": "freeze; lineage ends"}`
- `{"trace_round": 3, "idea": "T11", "kind_truth": "trick", "iteration": 0, "change": "pytest_quiet{}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": true, "reason": "accepted", "score": 1.0, "saving_tokens": 0.5865, "saving_cost": 0.3862, "cap_failed": false, "outcome": "frozen", "next": "freeze; lineage ends"}`
- `{"trace_round": 4, "idea": "T3", "kind_truth": "trick", "iteration": 0, "change": "tail_trim{\"lines\": 20}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": false, "reason": "capability below floor", "score": 0.8285, "saving_tokens": 0.5738, "saving_cost": 0.3962, "cap_failed": true, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 5, "idea": "T3", "kind_truth": "trick", "iteration": 1, "change": "tail_trim{\"lines\": 40}", "variant": 1, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": false, "reason": "capability below floor", "score": 0.8285, "saving_tokens": 0.5434, "saving_cost": 0.3727, "cap_failed": true, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 6, "idea": "T3", "kind_truth": "trick", "iteration": 2, "change": "tail_trim{\"lines\": 120}", "variant": 2, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": false, "reason": "capability below floor", "score": 0.9167, "saving_tokens": 0.4632, "saving_cost": 0.3189, "cap_failed": true, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 7, "idea": "T3", "kind_truth": "trick", "iteration": 3, "change": "", "variant": 3, "error": "cannot repair: cannot repair: cannot repair: variant grid exhausted", "ralph_repairs": 3, "files_changed": [], "outcome": "abandoned", "next": "lineage ends (proposer exhausted)"}`
- `{"trace_round": 8, "idea": "T7", "kind_truth": "trick", "iteration": 0, "change": "head_trim{\"lines\": 40}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": false, "reason": "capability below floor", "score": 0.8285, "saving_tokens": 0.5223, "saving_cost": 0.3596, "cap_failed": true, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 9, "idea": "T7", "kind_truth": "trick", "iteration": 1, "change": "head_trim{\"lines\": 120}", "variant": 1, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": false, "reason": "capability below floor", "score": 0.8285, "saving_tokens": 0.4202, "saving_cost": 0.2753, "cap_failed": true, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 10, "idea": "T7", "kind_truth": "trick", "iteration": 2, "change": "", "variant": 2, "error": "cannot repair: cannot repair: cannot repair: variant grid exhausted", "ralph_repairs": 3, "files_changed": [], "outcome": "abandoned", "next": "lineage ends (proposer exhausted)"}`
- `{"trace_round": 11, "idea": "C6", "kind_truth": "general", "iteration": 0, "change": "online_context_compact{\"cache_write_read_ratio\": 12.5}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": true, "reason": "accepted", "score": 1.0, "saving_tokens": 0.1794, "saving_cost": 0.0441, "cap_failed": false, "outcome": "frozen", "next": "freeze; lineage ends"}`
- `{"trace_round": 12, "idea": "P14", "kind_truth": "do_less", "iteration": 0, "change": "no_verify{}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": false, "reason": "capability below floor", "score": 0.902, "saving_tokens": 0.344, "saving_cost": 0.2444, "cap_failed": true, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 13, "idea": "P14", "kind_truth": "do_less", "iteration": 1, "change": "", "variant": 1, "error": "cannot repair: cannot repair: cannot repair: variant grid exhausted", "ralph_repairs": 3, "files_changed": [], "outcome": "abandoned", "next": "lineage ends (proposer exhausted)"}`
- `{"trace_round": 14, "idea": "P8", "kind_truth": "general", "iteration": 0, "change": "action_fusion{}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": true, "reason": "accepted", "score": 1.0, "saving_tokens": 0.2975, "saving_cost": 0.1781, "cap_failed": false, "outcome": "frozen", "next": "freeze; lineage ends"}`
- `{"trace_round": 15, "idea": "C23", "kind_truth": "general", "iteration": 0, "change": "observation_pack{\"excerpt_bytes\": 0, \"full_sends\": 1}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": true, "reason": "accepted", "score": 1.0, "saving_tokens": 0.0835, "saving_cost": -0.0542, "cap_failed": false, "outcome": "frozen", "next": "freeze; lineage ends"}`
- `{"trace_round": 16, "idea": "P20", "kind_truth": "do_less", "iteration": 0, "change": "turn_cap{\"max_turns\": 10}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": false, "reason": "capability below floor", "score": 0.7547, "saving_tokens": 0.6778, "saving_cost": 0.4855, "cap_failed": true, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 17, "idea": "P20", "kind_truth": "do_less", "iteration": 1, "change": "turn_cap{\"max_turns\": 16}", "variant": 1, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": false, "reason": "capability below floor", "score": 0.914, "saving_tokens": 0.3848, "saving_cost": 0.2345, "cap_failed": true, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 18, "idea": "P20", "kind_truth": "do_less", "iteration": 2, "change": "turn_cap{\"max_turns\": 24}", "variant": 2, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": true, "reason": "accepted", "score": 0.9836, "saving_tokens": 0.1235, "saving_cost": 0.064, "cap_failed": false, "outcome": "frozen", "next": "freeze; lineage ends"}`
- `{"trace_round": 19, "idea": "R5", "kind_truth": "dud", "iteration": 0, "change": "prompt_slim{}", "variant": 0, "error": null, "ralph_repairs": 0, "files_changed": ["harness.json"], "review": "pass", "gate": false, "reason": "no efficiency gain", "score": 1.0, "saving_tokens": 0.0007, "saving_cost": 0.0005, "cap_failed": false, "outcome": "gate_failed", "next": "route back to 01 with this candidate's rollouts"}`
- `{"trace_round": 20, "idea": "R5", "kind_truth": "dud", "iteration": 1, "change": "", "variant": -1, "error": "cannot repair: cannot repair: cannot repair: variant grid exhausted", "ralph_repairs": 3, "files_changed": [], "outcome": "abandoned", "next": "lineage ends (proposer exhausted)"}`

## firewall

```json
[
 {
  "candidate": "D1:evidence_preserving_reducer{\"reducer\": \"deterministic\"}",
  "passed": true,
  "reason": "accepted",
  "heldout_score": 1.0,
  "base_heldout_score": 1.0,
  "tokens_saving": 0.3078,
  "cost_saving": 0.237
 },
 {
  "candidate": "T11:pytest_quiet{}",
  "passed": false,
  "reason": "no efficiency gain",
  "heldout_score": 1.0,
  "base_heldout_score": 1.0,
  "tokens_saving": 0.0136,
  "cost_saving": 0.0135
 },
 {
  "candidate": "C6:online_context_compact{\"cache_write_read_ratio\": 12.5}",
  "passed": false,
  "reason": "no efficiency gain",
  "heldout_score": 0.9861,
  "base_heldout_score": 1.0,
  "tokens_saving": -0.0764,
  "cost_saving": -0.1272
 },
 {
  "candidate": "P8:action_fusion{}",
  "passed": true,
  "reason": "accepted",
  "heldout_score": 1.0,
  "base_heldout_score": 1.0,
  "tokens_saving": 0.2911,
  "cost_saving": 0.1618
 },
 {
  "candidate": "C23:observation_pack{\"excerpt_bytes\": 0, \"full_sends\": 1}",
  "passed": true,
  "reason": "accepted",
  "heldout_score": 1.0,
  "base_heldout_score": 1.0,
  "tokens_saving": 0.3185,
  "cost_saving": 0.0816
 },
 {
  "candidate": "P20:turn_cap{\"max_turns\": 24}",
  "passed": false,
  "reason": "no efficiency gain",
  "heldout_score": 1.0,
  "base_heldout_score": 1.0,
  "tokens_saving": 0.0,
  "cost_saving": 0.0
 }
]
```

## ideas

```json
{
 "D1": {
  "kind_truth": "general",
  "frozen": true,
  "survived": true
 },
 "T11": {
  "kind_truth": "trick",
  "frozen": true,
  "survived": false
 },
 "T3": {
  "kind_truth": "trick",
  "frozen": false,
  "survived": false
 },
 "T7": {
  "kind_truth": "trick",
  "frozen": false,
  "survived": false
 },
 "C6": {
  "kind_truth": "general",
  "frozen": true,
  "survived": false
 },
 "P14": {
  "kind_truth": "do_less",
  "frozen": false,
  "survived": false
 },
 "P8": {
  "kind_truth": "general",
  "frozen": true,
  "survived": true
 },
 "C23": {
  "kind_truth": "general",
  "frozen": true,
  "survived": true
 },
 "P20": {
  "kind_truth": "do_less",
  "frozen": true,
  "survived": false
 },
 "R5": {
  "kind_truth": "dud",
  "frozen": false,
  "survived": false
 }
}
```

## composed_extensions

```json
[
 "action_fusion",
 "evidence_preserving_reducer",
 "observation_pack"
]
```
