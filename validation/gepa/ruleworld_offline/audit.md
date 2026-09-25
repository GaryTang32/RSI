# Audit of `ruleworld_offline`

Ground truth: exact analytic expected score (RuleWorld.expected).

Independent checks re-derived from `trace.jsonl`, the ledger and the artifact store:

- gate_arithmetic: 8/8
- diff_matches_store: 8/8
- decision_matches_ledger: 8/8
- val_eval_matches_ledger: 8/8
- pareto_weights_recomputed: 8/8
- round_robin_recomputed: 8/8
- minibatch_epoch_disjoint: 8/8
- val_eval_covers_D_pareto: 9/9
- returned_best_is_argmax_val: 1/1
- child_minibatch_same_ids_as_parent: 8/8
- rollouts charged in eval events = 318, engine counter = 318
- gate vs truth: `{"n": 8, "accepted": 8, "rejected": 0, "false_accepts (accepted, true gain <= 0)": 1, "false_rejects (rejected, true gain > 0)": 0, "split": "test"}`

Incumbent (argmax mean D_pareto) changes against the truth:

- it 0: c0 -> c1  {"val_before": 0.0, "val_after": 0.0767, "true_test_before": 0.0, "true_test_after": 0.0509, "true_gain": 0.0509}
- it 1: c1 -> c2  {"val_before": 0.0767, "val_after": 0.0917, "true_test_before": 0.0509, "true_test_after": 0.1068, "true_gain": 0.0559}
- it 2: c2 -> c3  {"val_before": 0.0917, "val_after": 0.1183, "true_test_before": 0.1068, "true_test_after": 0.1681, "true_gain": 0.0613}
- it 3: c3 -> c4  {"val_before": 0.1183, "val_after": 0.2583, "true_test_before": 0.1681, "true_test_after": 0.2773, "true_gain": 0.1092}
- it 6: c4 -> c7  {"val_before": 0.2583, "val_after": 0.2811, "true_test_before": 0.2773, "true_test_after": 0.2654, "true_gain": -0.0119}
- it 7: c7 -> c8  {"val_before": 0.2811, "val_after": 0.3222, "true_test_before": 0.2654, "true_test_after": 0.3141, "true_gain": 0.0487}

| it | parent (p) | component | minibatch sum before -> after | gate | decision | new val | incumbent | true gain | sealed (monitor) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | c0 (1.00) | triage.md | 0 -> 0.75 (expected 0 -> 0.713) | True | accepted c1 | 0.077 | c0->c1 | +0.051 | c1: test 0.0502 |
| 1 | c1 (1.00) | reply.md | 0 -> 1 (expected 0.317 -> 0.95) | True | accepted c2 | 0.092 | c1->c2 | +0.056 | c2: test 0.1059 |
| 2 | c2 (0.52) | triage.md | 0 -> 0.5 (expected 0 -> 0.475) | True | accepted c3 | 0.118 | c2->c3 | +0.061 | c3: test 0.1669 |
| 3 | c3 (0.53) | reply.md | 0.333 -> 1 (expected 0.317 -> 0.95) | True | accepted c4 | 0.258 | c3->c4 | +0.109 | c4: test 0.2789 |
| 4 | c3 (0.26) | triage.md | 0.583 -> 1.5 (expected 0.554 -> 1.43) | True | accepted c5 | 0.176 | c4->c4 | +0.052 | - |
| 5 | c5 (0.27) | reply.md | 1.2 -> 1.95 (expected 1.14 -> 1.85) | True | accepted c6 | 0.187 | c4->c4 | +0.000 | - |
| 6 | c6 (0.33) | triage.md | 0.65 -> 1.22 (expected 0.618 -> 1.16) | True | accepted c7 | 0.281 | c4->c7 | +0.046 | c7: test 0.2621 |
| 7 | c7 (0.43) | reply.md | 0.25 -> 1.75 (expected 0.237 -> 1.66) | True | accepted c8 | 0.322 | c7->c8 | +0.049 | c8: test 0.3181 |
