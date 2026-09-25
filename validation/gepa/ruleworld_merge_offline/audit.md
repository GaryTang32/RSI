# Audit of `ruleworld_merge_offline`

Ground truth: exact analytic expected score (RuleWorld.expected).

Independent checks re-derived from `trace.jsonl`, the ledger and the artifact store:

- gate_arithmetic: 56/56
- diff_matches_store: 45/45
- decision_matches_ledger: 56/56
- val_eval_matches_ledger: 39/39
- pareto_weights_recomputed: 48/48
- round_robin_recomputed: 45/45
- minibatch_epoch_disjoint: 48/48
- val_eval_covers_D_pareto: 40/40
- returned_best_is_argmax_val: 1/1
- child_minibatch_same_ids_as_parent: 45/45
- rollouts charged in eval events = 1534, engine counter = 1534
- gate vs truth: `{"n": 56, "accepted": 39, "rejected": 17, "false_accepts (accepted, true gain <= 0)": 6, "false_rejects (rejected, true gain > 0)": 8, "split": "test"}`

Incumbent (argmax mean D_pareto) changes against the truth:

- it 0: c0 -> c1  {"val_before": 0.0, "val_after": 0.0767, "true_test_before": 0.0, "true_test_after": 0.0509, "true_gain": 0.0509}
- it 1: c1 -> c2  {"val_before": 0.0767, "val_after": 0.0917, "true_test_before": 0.0509, "true_test_after": 0.1068, "true_gain": 0.0559}
- it 2: c2 -> c3  {"val_before": 0.0917, "val_after": 0.1056, "true_test_before": 0.1068, "true_test_after": 0.1122, "true_gain": 0.0054}
- it 4: c3 -> c4  {"val_before": 0.1056, "val_after": 0.1511, "true_test_before": 0.1122, "true_test_after": 0.1609, "true_gain": 0.0487}
- it 6: c4 -> c5  {"val_before": 0.1511, "val_after": 0.1633, "true_test_before": 0.1609, "true_test_after": 0.1578, "true_gain": -0.0031}
- it 7: c5 -> c6  {"val_before": 0.1633, "val_after": 0.2594, "true_test_before": 0.1578, "true_test_after": 0.2573, "true_gain": 0.0995}
- it 9: c6 -> c8  {"val_before": 0.2594, "val_after": 0.2639, "true_test_before": 0.2573, "true_test_after": 0.318, "true_gain": 0.0607}
- it 10: c8 -> c9  {"val_before": 0.2639, "val_after": 0.3478, "true_test_before": 0.318, "true_test_after": 0.374, "true_gain": 0.056}
- it 12: c9 -> c10  {"val_before": 0.3478, "val_after": 0.3772, "true_test_before": 0.374, "true_test_after": 0.3994, "true_gain": 0.0254}
- it 13: c10 -> c11  {"val_before": 0.3772, "val_after": 0.4428, "true_test_before": 0.3994, "true_test_after": 0.4554, "true_gain": 0.056}
- it 15: c11 -> c12  {"val_before": 0.4428, "val_after": 0.4561, "true_test_before": 0.4554, "true_test_after": 0.5068, "true_gain": 0.0514}
- it 16: c12 -> c13  {"val_before": 0.4561, "val_after": 0.5028, "true_test_before": 0.5068, "true_test_after": 0.5645, "true_gain": 0.0577}
- it 17: c13 -> c14  {"val_before": 0.5028, "val_after": 0.5317, "true_test_before": 0.5645, "true_test_after": 0.5388, "true_gain": -0.0257}
- it 18: c14 -> c15  {"val_before": 0.5317, "val_after": 0.5756, "true_test_before": 0.5388, "true_test_after": 0.5964, "true_gain": 0.0577}
- it 25: c15 -> c19  {"val_before": 0.5756, "val_after": 0.6333, "true_test_before": 0.5964, "true_test_after": 0.648, "true_gain": 0.0515}
- it 26: c19 -> c20  {"val_before": 0.6333, "val_after": 0.6628, "true_test_before": 0.648, "true_test_after": 0.6582, "true_gain": 0.0103}
- it 30: c20 -> c22  {"val_before": 0.6628, "val_after": 0.6994, "true_test_before": 0.6582, "true_test_after": 0.7439, "true_gain": 0.0857}
- it 31: c22 -> c23  {"val_before": 0.6994, "val_after": 0.7228, "true_test_before": 0.7439, "true_test_after": 0.7542, "true_gain": 0.0103}
- it 32: c23 -> c24  {"val_before": 0.7228, "val_after": 0.7483, "true_test_before": 0.7542, "true_test_after": 0.7801, "true_gain": 0.0259}
- it 38: c24 -> c28  {"val_before": 0.7483, "val_after": 0.805, "true_test_before": 0.7801, "true_test_after": 0.8008, "true_gain": 0.0207}
- it 39: c28 -> c29  {"val_before": 0.805, "val_after": 0.8489, "true_test_before": 0.8008, "true_test_after": 0.825, "true_gain": 0.0242}
- it 50: c29 -> c33  {"val_before": 0.8489, "val_after": 0.8978, "true_test_before": 0.825, "true_test_after": 0.8475, "true_gain": 0.0225}

| it | parent (p) | component | minibatch sum before -> after | gate | decision | new val | incumbent | true gain | sealed (monitor) |
|---|---|---|---|---|---|---|---|---|---|
| 0 | c0 (1.00) | triage.md | 0 -> 0.75 (expected 0 -> 0.713) | True | accepted c1 | 0.077 | c0->c1 | +0.051 | c1: test 0.0502 |
| 1 | c1 (1.00) | reply.md | 0 -> 1 (expected 0.317 -> 0.95) | True | accepted c2 | 0.092 | c1->c2 | +0.056 | c2: test 0.1059 |
| 2 | c1 (0.48) | triage.md | 0 -> 0.5 (expected 0 -> 0.475) | True | accepted c3 | 0.106 | c2->c3 | +0.061 | c3: test 0.1093 |
| 3 | - | reply.md,triage.md | merge 0.45 vs 0.533 | False | merge_rejected  | - | c3->c3 | +0.056 | - |
| 4 | c3 (0.35) | reply.md | 0.583 -> 0.833 (expected 0.554 -> 0.792) | True | accepted c4 | 0.151 | c3->c4 | +0.049 | c4: test 0.162 |
| 5 | - | reply.md,triage.md | merge 0.45 vs 0.533 | False | merge_rejected  | - | c4->c4 | +0.000 | - |
| 6 | c2 (0.32) | triage.md | 0.817 -> 1.22 (expected 0.776 -> 1.16) | True | accepted c5 | 0.163 | c4->c5 | +0.051 | c5: test 0.1593 |
| 7 | c4 (0.45) | triage.md | 0.5 -> 0.75 (expected 0.475 -> 0.95) | True | accepted c6 | 0.259 | c5->c6 | +0.096 | c6: test 0.2612 |
| 8 | c5 (0.50) | reply.md | 0.667 -> 1.2 (expected 0.633 -> 1.14) | True | accepted c7 | 0.253 | c6->c6 | +0.096 | - |
| 9 | c6 (0.46) | reply.md | 1.83 -> 2.23 (expected 1.74 -> 2.12) | True | accepted c8 | 0.264 | c6->c8 | +0.061 | c8: test 0.3198 |
| 10 | c8 (0.35) | triage.md | 0.833 -> 1.5 (expected 0.792 -> 1.43) | True | accepted c9 | 0.348 | c8->c9 | +0.056 | c9: test 0.3837 |
| 11 | c9 (0.41) | reply.md | 1.6 -> 1.43 (expected 1.52 -> 1.66) | False | rejected  | - | c9->c9 | +0.050 | - |
| 12 | c8 (0.27) | reply.md | 1.03 -> 1.43 (expected 0.982 -> 1.36) | True | accepted c10 | 0.377 | c9->c10 | +0.081 | c10: test 0.4021 |
| 13 | - | reply.md,triage.md | merge 2 vs 1.83 | True | merge_accepted c11 | 0.443 | c10->c11 | +0.056 | c11: test 0.4521 |
| 14 | c7 (0.37) | triage.md | 2.15 -> 2.15 (expected 2.04 -> 2.04) | False | rejected  | - | c11->c11 | +0.000 | - |
| 15 | c11 (0.63) | triage.md | 1.45 -> 1.7 (expected 1.38 -> 1.61) | True | accepted c12 | 0.456 | c11->c12 | +0.051 | c12: test 0.5092 |
| 16 | c12 (0.41) | reply.md | 1.83 -> 2.42 (expected 1.74 -> 2.3) | True | accepted c13 | 0.503 | c12->c13 | +0.058 | c13: test 0.5574 |
| 17 | c12 (0.38) | triage.md | 1.57 -> 1.85 (expected 1.49 -> 1.76) | True | accepted c14 | 0.532 | c13->c14 | +0.032 | c14: test 0.5412 |
| 18 | - | reply.md,triage.md | merge 3.35 vs 3.2 | True | merge_accepted c15 | 0.576 | c14->c15 | +0.032 | c15: test 0.6057 |
| 19 | c13 (0.24) | triage.md | 2.75 -> 2.75 (expected 2.61 -> 2.61) | False | rejected  | - | c15->c15 | +0.000 | - |
| 20 | c14 (0.31) | reply.md | 1.08 -> 2.5 (expected 1.27 -> 2.38) | True | accepted c16 | 0.544 | c15->c15 | +0.004 | - |
| 21 | - | reply.md,triage.md | merge 2.87 vs 3.47 | False | merge_rejected  | - | c15->c15 | -0.021 | - |
| 22 | c15 (0.44) | reply.md | 2 -> 1.75 (expected 1.9 -> 2.61) | False | rejected  | - | c15->c15 | +0.098 | - |
| 23 | c15 (0.44) | triage.md | 1.67 -> 2 (expected 1.58 -> 1.9) | True | accepted c17 | 0.569 | c15->c15 | +0.010 | - |
| 24 | - | reply.md,triage.md | merge 3.8 vs 3.2 | True | merge_accepted c18 | 0.569 | c15->c15 | +0.000 | - |
| 25 | c15 (0.40) | reply.md | 2.33 -> 2.58 (expected 2.22 -> 2.45) | True | accepted c19 | 0.633 | c15->c19 | +0.051 | c19: test 0.642 |
| 26 | - | reply.md,triage.md | merge 3.5 vs 3.5 | True | merge_accepted c20 | 0.663 | c19->c20 | +0.010 | c20: test 0.6685 |
| 27 | c20 (0.50) | - | - | - | skip_perfect  | - | c20->c20 | - | - |
| 28 | c19 (0.50) | triage.md | 1.58 -> 1.83 (expected 1.5 -> 2.02) | True | accepted c21 | 0.619 | c20->c20 | +0.008 | - |
| 29 | c20 (0.50) | reply.md | 2.67 -> 2.67 (expected 2.53 -> 2.85) | False | rejected  | - | c20->c20 | +0.001 | - |
| 30 | c19 (0.50) | reply.md | 1.87 -> 2.27 (expected 1.77 -> 2.15) | True | accepted c22 | 0.699 | c20->c22 | +0.096 | c22: test 0.7486 |
| 31 | - | reply.md,triage.md | merge 4.3 vs 4.02 | True | merge_accepted c23 | 0.723 | c22->c23 | +0.010 | c23: test 0.7495 |
| 32 | c23 (0.36) | triage.md | 2.4 -> 2.6 (expected 2.28 -> 2.47) | True | accepted c24 | 0.748 | c23->c24 | +0.026 | c24: test 0.7804 |
| 33 | c22 (0.45) | triage.md | 2.47 -> 1.3 (expected 2.34 -> 2.03) | False | rejected  | - | c24->c24 | +0.006 | - |
| 34 | c22 (0.45) | reply.md | 2.42 -> 2.75 (expected 2.3 -> 2.61) | True | accepted c25 | 0.711 | c24->c24 | +0.001 | - |
| 35 | c22 (0.45) | triage.md | 2.42 -> 2.75 (expected 2.45 -> 2.61) | True | accepted c26 | 0.727 | c24->c24 | +0.000 | - |
| 36 | c26 (0.47) | reply.md | 2.67 -> 3 (expected 2.53 -> 2.85) | True | accepted c27 | 0.712 | c24->c24 | +0.024 | - |
| 37 | - | reply.md,triage.md | merge 3.58 vs 3.72 | False | merge_rejected  | - | c24->c24 | +0.024 | - |
| 38 | c26 (0.27) | triage.md | 2.17 -> 2.5 (expected 2.06 -> 2.38) | True | accepted c28 | 0.805 | c24->c28 | +0.057 | c28: test 0.8121 |
| 39 | - | reply.md,triage.md | merge 4.6 vs 3.93 | True | merge_accepted c29 | 0.849 | c28->c29 | +0.024 | c29: test 0.8167 |
| 40 | c24 (0.42) | reply.md | 2.8 -> 2.8 (expected 2.66 -> 2.66) | False | rejected  | - | c29->c29 | +0.000 | - |
| 41 | c29 (0.58) | reply.md | 1.85 -> 1.55 (expected 1.76 -> 2.42) | False | rejected  | - | c29->c29 | +0.027 | - |
| 42 | c24 (0.42) | - | - | - | skip_perfect  | - | c29->c29 | - | - |
| 43 | c29 (0.58) | triage.md | 2.25 -> 2.25 (expected 2.14 -> 2.14) | False | rejected  | - | c29->c29 | +0.000 | - |
| 44 | c29 (0.58) | - | - | - | skip_perfect  | - | c29->c29 | - | - |
| 45 | c24 (0.42) | triage.md | 2.25 -> 2.25 (expected 2.61 -> 2.61) | False | rejected  | - | c29->c29 | +0.000 | - |
| 46 | c29 (0.58) | reply.md | 2.75 -> 2.58 (expected 2.61 -> 2.65) | False | rejected  | - | c29->c29 | +0.011 | - |
| 47 | c29 (0.58) | triage.md | 1.7 -> 2.3 (expected 2.19 -> 2.19) | True | accepted c30 | 0.849 | c29->c29 | +0.000 | - |
| 48 | - | reply.md,triage.md | merge 4.1 vs 4.1 | True | merge_accepted c31 | 0.849 | c29->c29 | +0.000 | - |
| 49 | c24 (0.42) | reply.md | 2.5 -> 3 (expected 2.38 -> 2.85) | True | accepted c32 | 0.747 | c29->c29 | +0.047 | - |
| 50 | c31 (0.43) | reply.md | 1.55 -> 1.6 (expected 2.23 -> 2.23) | True | accepted c33 | 0.898 | c29->c33 | +0.022 | c33: test 0.8568 |
| 51 | c24 (0.30) | triage.md | 2 -> 2 (expected 1.9 -> 1.9) | False | rejected  | - | c33->c33 | +0.000 | - |
| 52 | c31 (0.43) | triage.md | 1.67 -> 3 (expected 2.53 -> 2.85) | True | accepted c34 | 0.878 | c33->c33 | +0.027 | - |
| 53 | c34 (0.44) | reply.md | 2 -> 3 (expected 2.38 -> 2.85) | True | accepted c35 | 0.851 | c33->c33 | +0.000 | - |
| 54 | c32 (0.27) | triage.md | 2.8 -> 3 (expected 2.66 -> 2.85) | True | accepted c36 | 0.881 | c33->c33 | +0.057 | - |
| 55 | c36 (0.49) | reply.md | 2.17 -> 2.67 (expected 2.06 -> 2.53) | True | accepted c37 | 0.893 | c33->c33 | -0.022 | - |
| 56 | c37 (0.33) | triage.md | 2.75 -> 2.75 (expected 2.61 -> 2.61) | False | rejected  | - | c33->c33 | +0.000 | - |
| 57 | c37 (0.33) | reply.md | 2.38 -> 2.8 (expected 2.26 -> 2.66) | True | accepted c38 | 0.858 | c33->c33 | +0.022 | - |
| 58 | c33 (0.32) | triage.md | 1.8 -> 2.05 (expected 2.42 -> 2.52) | True | accepted c39 | 0.788 | c33->c33 | +0.005 | - |
