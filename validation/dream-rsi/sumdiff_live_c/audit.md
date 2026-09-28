# Audit of `sumdiff_live_c`

Every check below is re-derived from the files on disk (trace.jsonl, trace_pool/, snapshots/, history/) with code independent of the loop where possible.

## Run-level checks

| check | result | detail |
|---|---|---|
| seed program re-graded independently | PASS | trace S=0.910491, independent=0.910491 (ok) |
| run starts from the untouched seed artifact | PASS | ed8962e6b1 |
| agent calls: sum of attempts over worlds = cost meter = trajectory | PASS | 18 attempts, meter 18 |
| call budget respected | PASS | 18 <= 18 |
| returned best = best of every recorded successful attempt and the seed | PASS | 1.046752 |

## Cycle 1

Deployed policy `r0000` (initial, default beta 0.6); plan 3 x 3 (parallel refine: fixed fallback grid (width x depth never adapts)); N = 9 attempts in k = 3 rounds, batches [3, 3, 3]; 0 planned cells left unprobed at stop; outcomes {'ok': 9}; best 0.910491 -> 1.028061.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 2) used (3, 2): parallel refine: fixed fallback grid (width x depth never adapts) |
| live root = best program so far (root='best') | PASS | root 0.910491 vs best so far 0.910491 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 3 rounds, batch sizes [3, 3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 9/9 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 9/9 match |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 0.910491 -> 1.028061 (trace: 0.910491 -> 1.028061, kept t1/b2.a2) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3] vs replay [3, 3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=1: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 1 worlds |
| dream t=1: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0000=0.9250, r0001=0.9250, r0002=0.9250, r0003=0.7163 |
| dream t=1: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.925, 0.925, 0.925, 0.716323], selected [0], expected 0, next cycle deploys r0000 |
| dream t=1: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=1: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0000 clean |

Dreaming after cycle 1: V = [0.925, 0.925, 0.925, 0.716323], selected index 0 (r0000); argmax replay value (incumbent included); V = [0.925, 0.925, 0.925, 0.716323]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0001_t1m1 | Implement adaptive prefix-only policy: trajectory-based ranking with beta-scheduled thresholds, portfolio batch selection (exploit/explore/recover), adaptive gr | 292 | [True, True] | 0.925 |
| r0002_t1m2 | Implement recovery tier for repairable failures and restructure batch selection with adaptive tier-based portfolio building instead of fixed quotas | 293 | [True, True] | 0.925 |
| r0003_t1m3 | Improve portfolio balance, recovery scoring, early stopping, and grid planning: prioritize all three tiers (exploit, recovery, explore) in batches; refine recov | 329 | [True, True] | 0.7163226629691685 |

## Cycle 2

Deployed policy `r0000` (initial, default beta 0.6); plan 3 x 3 (parallel refine: fixed fallback grid (width x depth never adapts)); N = 9 attempts in k = 3 rounds, batches [3, 3, 3]; 0 planned cells left unprobed at stop; outcomes {'ok': 9}; best 1.028061 -> 1.046752.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 2) used (3, 2): parallel refine: fixed fallback grid (width x depth never adapts) |
| live root = best program so far (root='best') | PASS | root 1.028061 vs best so far 1.028061 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 3 rounds, batch sizes [3, 3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 9/9 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 9/9 match |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.028061 -> 1.046752 (trace: 1.028061 -> 1.046752, kept t2/b0.a1) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3] vs replay [3, 3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |

## Report

```json
{
 "seed_score": 0.9104910395109694,
 "best_score": 1.0467524040140475,
 "calls_per_cycle": [
  9,
  9
 ],
 "spend": {
  "loop_usd": 1.4573,
  "shadow_monitor_usd": 0.0,
  "report_usd": 0.0
 }
}
```
