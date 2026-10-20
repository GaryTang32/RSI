# Audit of `agentqa_offline`

Ground truth: SimModel estimate: mean over 6 fresh seeds (offset 10000) per task on each split.

Independent checks re-derived from `trace.jsonl`, the ledger and the artifact store:

- gate_arithmetic: 7/7
- diff_matches_store: 7/7
- decision_matches_ledger: 7/7
- val_eval_matches_ledger: 4/4
- pareto_weights_recomputed: 20/20
- round_robin_recomputed: 7/7
- minibatch_epoch_disjoint: 20/20
- val_eval_covers_D_pareto: 5/5
- returned_best_is_argmax_val: 1/1
- child_minibatch_same_ids_as_parent: 7/7
- rollouts charged in eval events = 121, engine counter = 121
- gate vs truth: `{"n": 7, "accepted": 4, "rejected": 3, "false_accepts (accepted, true gain <= 0)": 2, "false_rejects (rejected, true gain > 0)": 1, "split": "holdout"}`

Incumbent (argmax mean D_pareto) changes against the truth:

- it 3: c0 -> c1  {"val_before": 0.375, "val_after": 1.0, "true_holdout_before": 0.3889, "true_holdout_after": 0.8611, "true_gain": 0.4722}

| it | parent (p) | component | minibatch sum before -> after | gate | decision | new val | incumbent | true gain | sealed (monitor) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | c0 (1.00) | reporter.md | 1 -> 0 | False | rejected  | - | c0->c0 | -0.389 | - |
| 1 | c0 (1.00) | solver.md | 1 -> 1 | False | rejected  | - | c0->c0 | -0.111 | - |
| 2 | c0 (1.00) | reporter.md | 2 -> 2 | False | rejected  | - | c0->c0 | +0.056 | - |
| 3 | c0 (1.00) | solver.md | 2 -> 3 | True | accepted c1 | 1.000 | c0->c1 | +0.472 | c1: holdout 1.0, ood 1.0 |
| 4 | c1 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 5 | c1 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 6 | c1 (1.00) | reporter.md | 2 -> 3 | True | accepted c2 | 1.000 | c1->c1 | +0.000 | - |
| 7 | c2 (1.00) | solver.md | 2 -> 3 | True | accepted c3 | 0.875 | c1->c1 | +0.111 | - |
| 8 | c2 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 9 | c2 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 10 | c2 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 11 | c2 (1.00) | reporter.md | 2 -> 3 | True | accepted c4 | 1.000 | c1->c1 | +0.000 | - |
| 12 | c4 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 13 | c4 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 14 | c4 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 15 | c4 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 16 | c4 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 17 | c4 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 18 | c4 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
| 19 | c4 (1.00) | - | - | - | skip_perfect  | - | c1->c1 | - | - |
