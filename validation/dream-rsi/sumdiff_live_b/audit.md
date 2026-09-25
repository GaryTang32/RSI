# Audit of `sumdiff_live_b`

Every check below is re-derived from the files on disk (trace.jsonl, trace_pool/, snapshots/, history/) with code independent of the loop where possible.

## Run-level checks

| check | result | detail |
|---|---|---|
| seed program re-graded independently | PASS | trace S=0.910491, independent=0.910491 (ok) |
| run starts from the untouched seed artifact | PASS | ed8962e6b1 |
| agent calls: sum of attempts over worlds = cost meter = trajectory | PASS | 17 attempts, meter 17 |
| call budget respected | PASS | 17 <= 18 |
| returned best = best of every recorded successful attempt and the seed | PASS | 1.036138 |

## Cycle 1

Deployed policy `r0000` (initial, default beta 0.6); plan 3 x 3 (parallel refine: fixed fallback grid (width x depth never adapts)); N = 9 attempts in k = 3 rounds, batches [3, 3, 3]; 0 planned cells left unprobed at stop; outcomes {'ok': 9}; best 0.910491 -> 1.018324.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 2) used (3, 2): parallel refine: fixed fallback grid (width x depth never adapts) |
| live root = best program so far (root='best') | PASS | root 0.910491 vs best so far 0.910491 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 3 rounds, batch sizes [3, 3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 9/9 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 9/9 match |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 0.910491 -> 1.018324 (trace: 0.910491 -> 1.018324, kept t1/b0.a1) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3] vs replay [3, 3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=1: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 1 worlds |
| dream t=1: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0000=0.9250, r0001=0.9333, r0002=0.9250, r0003=0.9213 |
| dream t=1: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.925, 0.933333, 0.925, 0.92125], selected [1], expected 1, next cycle deploys r0001 |
| dream t=1: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=1: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0001 clean |

Dreaming after cycle 1: V = [0.925, 0.933333, 0.925, 0.92125], selected index 1 (r0001); argmax replay value (incumbent included); V = [0.925, 0.933333, 0.925, 0.92125]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0001_t1m1 | Replace fixed parallel refine with adaptive trajectory-based exploration: rank branches by successful anchors and improvement trends, prioritize roots and first | 106 | [True, True] | 0.9333333333333333 |
| r0002_t1m2 | Relax Priority 3 batching to include all non-hard-failed branches, not just promising ones, ensuring full parallelism utilization and thorough exploration while | 41 | [True, True] | 0.925 |
| r0003_t1m3 | Add parallelism-aware inclusion of weak-but-underexplored branches in Priority 3 (deeper refinements) when sufficient capacity remains (>= 2 slots), improving b | 76 | [True, True] | 0.92125 |

## Cycle 2

Deployed policy `r0001` (t1m1, default beta 0.6); plan 4 x 2 (early gains strong, late plateau -> widen and reduce depth); N = 8 attempts in k = 3 rounds, batches [3, 3, 2]; 0 planned cells left unprobed at stop; outcomes {'ok': 8}; best 1.018324 -> 1.036138.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (4, 1) used (4, 1): early gains strong, late plateau -> widen and reduce depth |
| live root = best program so far (root='best') | PASS | root 1.018324 vs best so far 1.018324 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 3 rounds, batch sizes [3, 3, 2] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 8/8 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 8/8 match |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.018324 -> 1.036138 (trace: 1.018324 -> 1.036138, kept t2/b1.a1) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 2] vs replay [3, 3, 2] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |

## Report

```json
{
 "seed_score": 0.9104910395109694,
 "best_score": 1.0361377782299992,
 "calls_per_cycle": [
  9,
  8
 ],
 "spend": {
  "loop_usd": 1.2782,
  "shadow_monitor_usd": 0.0,
  "report_usd": 0.0
 }
}
```
