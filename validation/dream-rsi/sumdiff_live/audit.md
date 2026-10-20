# Audit of `sumdiff_live`

Every check below is re-derived from the files on disk (trace.jsonl, trace_pool/, snapshots/, history/) with code independent of the loop where possible.

## Run-level checks

| check | result | detail |
|---|---|---|
| seed program re-graded independently | PASS | trace S=0.910491, independent=0.910491 (ok) |
| run starts from the untouched seed artifact | PASS | ed8962e6b1 |
| agent calls: sum of attempts over worlds = cost meter = trajectory | PASS | 36 attempts, meter 36 |
| call budget respected | PASS | 36 <= 40 |
| returned best = best of every recorded successful attempt and the seed | PASS | 1.030417 |

## Cycle 1

Deployed policy `r0000` (initial, default beta 0.6); plan 3 x 4 (parallel refine: fixed fallback grid (width x depth never adapts)); N = 12 attempts in k = 4 rounds, batches [3, 3, 3, 3]; 0 planned cells left unprobed at stop; outcomes {'compile_other': 6, 'ok': 6}; best 0.910491 -> 1.023749.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 3) used (3, 3): parallel refine: fixed fallback grid (width x depth never adapts) |
| live root = best program so far (root='best') | PASS | root 0.910491 vs best so far 0.910491 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 4 rounds, batch sizes [3, 3, 3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 12/12 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 12/12 match |
| failed attempts: genuine failures (not reply-parsing artifacts) | FAIL | 6 failed; 6 were a trailing '===' / fence line left by the reply parser - re-graded without it: b1.a0=0.9552, b0.a1=1.0286, b2.a1=1.0196, b0.a2=1.0142, b2.a2=1.0163, b2.a3=1.0000 |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 0.910491 -> 1.023749 (trace: 0.910491 -> 1.023749, kept t1/b1.a3) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3, 3] vs replay [3, 3, 3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=1: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 1 worlds |
| dream t=1: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0000=0.8950, r0001=0.8950, r0002=0.8950, r0003=0.8950 |
| dream t=1: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.895, 0.895, 0.895, 0.895], selected [0], expected 0, next cycle deploys r0000 |
| dream t=1: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=1: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0000 clean |

Dreaming after cycle 1: V = [0.895, 0.895, 0.895, 0.895], selected index 0 (r0000); argmax replay value (incumbent included); V = [0.895, 0.895, 0.895, 0.895]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0001_t1m1 | Implement adaptive trajectory-guided policy with smart batching, beta-controlled thresholds, and adaptive grid planning | 96 | [True, True] | 0.895 |
| r0002_t1m2 | Fix indentation error on line 170: `batch.append(cell)` under Priority 3 repair loop was not indented, causing syntax error. | 47 | [False, True, True] | 0.895 |
| r0003_t1m3 | Prioritize recovery over exploration and fix soft-closure to spare branches with successful anchors | 49 | [True, True] | 0.895 |

## Cycle 2

Deployed policy `r0000` (initial, default beta 0.6); plan 3 x 4 (parallel refine: fixed fallback grid (width x depth never adapts)); N = 12 attempts in k = 4 rounds, batches [3, 3, 3, 3]; 0 planned cells left unprobed at stop; outcomes {'compile_other': 5, 'ok': 7}; best 1.023749 -> 1.030417.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 3) used (3, 3): parallel refine: fixed fallback grid (width x depth never adapts) |
| live root = best program so far (root='best') | PASS | root 1.023749 vs best so far 1.023749 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 4 rounds, batch sizes [3, 3, 3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 12/12 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 12/12 match |
| failed attempts: genuine failures (not reply-parsing artifacts) | FAIL | 5 failed; 5 were a trailing '===' / fence line left by the reply parser - re-graded without it: b1.a0=1.0000, b2.a0=1.0000, b0.a1=1.0000, b0.a2=1.0000, b0.a3=1.0210 |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.023749 -> 1.030417 (trace: 1.023749 -> 1.030417, kept t2/b2.a2) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3, 3] vs replay [3, 3, 3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=2: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 2 worlds |
| dream t=2: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0000=0.8950, r0004=0.8950, r0005=0.8950, r0006=0.8950 |
| dream t=2: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.895, 0.895, 0.895, 0.895], selected [0], expected 0, next cycle deploys r0000 |
| dream t=2: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=2: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0000 clean |

Dreaming after cycle 2: V = [0.895, 0.895, 0.895, 0.895], selected index 0 (r0000); argmax replay value (incumbent included); V = [0.895, 0.895, 0.895, 0.895]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0004_t2m1 | Implement success-rate-prioritized portfolio exploration with adaptive beta schedule and history-responsive grid planning | 97 | [True, True] | 0.895 |
| r0005_t2m2 | Fix syntax error at line 230 by ensuring proper indentation and file structure throughout solve() method | 74 | [False, True, True] | 0.895 |
| r0006_t2m3 | Fix syntax error in solve() method: correct recovery_candidates sorting to use proper key function indexing | 58 | [False, True, True] | 0.895 |

## Cycle 3

Deployed policy `r0000` (initial, default beta 0.6); plan 3 x 4 (parallel refine: fixed fallback grid (width x depth never adapts)); N = 12 attempts in k = 4 rounds, batches [3, 3, 3, 3]; 0 planned cells left unprobed at stop; outcomes {'compile_other': 1, 'ok': 11}; best 1.030417 -> 1.030417.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 3) used (3, 3): parallel refine: fixed fallback grid (width x depth never adapts) |
| live root = best program so far (root='best') | PASS | root 1.030417 vs best so far 1.030417 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 4 rounds, batch sizes [3, 3, 3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 12/12 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 12/12 match |
| failed attempts: genuine failures (not reply-parsing artifacts) | FAIL | 1 failed; 1 were a trailing '===' / fence line left by the reply parser - re-graded without it: b2.a3=1.0148 |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.030417 -> 1.030417 (trace: 1.030417 -> 1.030417, kept None) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3, 3] vs replay [3, 3, 3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |

## Report

```json
{
 "seed_score": 0.9104910395109694,
 "best_score": 1.0304168748055738,
 "calls_per_cycle": [
  12,
  12,
  12
 ],
 "spend": {
  "loop_usd": 2.906,
  "shadow_monitor_usd": 0.0,
  "report_usd": 0.0
 }
}
```
