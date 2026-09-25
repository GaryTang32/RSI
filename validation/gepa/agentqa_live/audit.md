# Audit of `agentqa_live`

Ground truth: none (live model: no ground truth; see the shadow monitor and the transfer report).

Independent checks re-derived from `trace.jsonl`, the ledger and the artifact store:

- gate_arithmetic: 4/4
- diff_matches_store: 4/4
- decision_matches_ledger: 4/4
- val_eval_matches_ledger: 2/2
- pareto_weights_recomputed: 5/5
- round_robin_recomputed: 4/4
- minibatch_epoch_disjoint: 5/5
- val_eval_covers_D_pareto: 3/3
- returned_best_is_argmax_val: 1/1
- child_minibatch_same_ids_as_parent: 4/4
- rollouts charged in eval events = 51, engine counter = 51

| it | parent (p) | component | minibatch sum before -> after | gate | decision | new val | incumbent | true gain | sealed (monitor) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | c0 (1.00) | reporter.md | 2 -> 2 | False | rejected  | - | c0->c0 | - | - |
| 1 | c0 (1.00) | solver.md | 0 -> 1 | True | accepted c1 | 0.250 | c0->c0 | - | - |
| 2 | c1 (0.42) | reporter.md | 2 -> 3 | True | accepted c2 | 0.250 | c0->c0 | - | - |
| 3 | c0 (0.58) | reporter.md | 1 -> 1 | False | rejected  | - | c0->c0 | - | - |
| 4 | c0 (0.58) | - | - | - | skip_perfect  | - | c0->c0 | - | - |
