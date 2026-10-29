| arm | status | n | accuracy [95% CI] | holdout | ood | vs seed | vs cot3 | eval $/ex | search $ | calls (fresh+cached) | leak |
|---|---|---|---|---|---|---|---|---|---|---|---|
| seed | ok | 120 | 0.417 [0.342, 0.492] | 0.4667 | 0.3667 | - | - | 0.224 | 0.000 | 240+0 | - |
| rrsi | ok | 120 | 0.438 [0.362, 0.517] | 0.5 | 0.375 | +0.021 [+0.004, +0.042] (n=120) | - | 0.228 | 38.098 | 103+525 | pass |
| gepa | ok | 120 | 0.417 [0.342, 0.492] | 0.4667 | 0.3667 | +0.000 [+0.000, +0.000] (n=120) | - | 0.228 | 11.800 | 1+287 | pass |
| metaharness | ok | 120 | 0.417 [0.342, 0.492] | 0.4667 | 0.3667 | +0.000 [+0.000, +0.000] (n=120) | - | 0.228 | 16.292 | 83+334 | pass |
| harness2 | ok | 36 | 0.444 [0.278, 0.611] | - | - | -0.028 [-0.194, +0.125] (n=36) | - | 0.000 | 21.917 | 4+272 | - |
| bestofn | ok | 36 | 0.556 [0.389, 0.722] | - | - | +0.083 [-0.069, +0.236] (n=36) | - | 0.000 | 29.741 | 19+521 | - |

harness2_minus_bestofn: -0.111 [-0.250, +0.028] (n=36)

harness2_submitted_minus_H0_base1: +0.028 [-0.139, +0.194] (n=36)

| family | seed | rrsi | gepa | metaharness | harness2 | bestofn |
|---|---|---|---|---|---|---|
| boolean_expressions | 0.6 | 0.6 | 0.6 | 0.6 | 0.6667 | 0.6667 |
| buggy_tables | 0.5 | 0.5 | 0.5 | 0.5 | 0.3333 | 0.3333 |
| causal_understanding | 0.25 | 0.25 | 0.25 | 0.25 | 0.0 | 0.0 |
| geometric_shapes | 0.25 | 0.3 | 0.25 | 0.25 | 0.3333 | 0.3333 |
| linguini | 0.15 | 0.15 | 0.15 | 0.15 | 0.6667 | 0.3333 |
| nycc | 0.2 | 0.2 | 0.2 | 0.2 | 0.6667 | 0.6667 |
| object_properties | 0.4 | 0.4 | 0.4 | 0.4 | 0.3333 | 0.6667 |
| sarc_triples | 0.45 | 0.45 | 0.45 | 0.45 | 0.6667 | 0.6667 |
| shuffled_objects | 0.55 | 0.75 | 0.55 | 0.55 | 1.0 | 0.6667 |
| sportqa | 0.4 | 0.4 | 0.4 | 0.4 | 0.3333 | 0.3333 |
| temporal_sequence | 0.4 | 0.4 | 0.4 | 0.4 | 0.0 | 1.0 |
| web_of_lies | 0.85 | 0.85 | 0.85 | 0.85 | 0.3333 | 1.0 |

Harness² and best-of-n rows use the 36-example subsample; their vs-seed/vs-cot3 columns compare on those examples only.
eval $/ex and search $ price every call an arm used, fresh or replayed from the shared cache (its compute cost); the money actually spent is the sum over the cache, as the shared cache bills each call once.
