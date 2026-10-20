# Audit of `agentqa_offline`

Every check below is re-derived from the files on disk (trace.jsonl, trace_pool/, snapshots/, history/) with code independent of the loop where possible.

## Run-level checks

| check | result | detail |
|---|---|---|
| seed program re-graded independently | PASS | trace S=0.250000, independent=0.250000 (ok) |
| run starts from the untouched seed artifact | PASS | 498c3a8834 |
| agent calls: sum of attempts over worlds = cost meter = trajectory | PASS | 32 attempts, meter 32 |
| returned best = best of every recorded successful attempt and the seed | PASS | 1.000000 |

## Cycle 1

Deployed policy `r0000` (initial, default beta 0.6); plan 3 x 3 (parallel refine: fixed fallback grid (width x depth never adapts)); N = 9 attempts in k = 3 rounds, batches [3, 3, 3]; 0 planned cells left unprobed at stop; outcomes {'ok': 9}; best 0.250000 -> 1.000000.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 2) used (3, 2): parallel refine: fixed fallback grid (width x depth never adapts) |
| live root = best program so far (root='best') | PASS | root 0.250000 vs best so far 0.250000 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 3 rounds, batch sizes [3, 3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 9/9 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 9/9 match |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 0.250000 -> 1.000000 (trace: 0.25 -> 1.0, kept t1/b0.a0) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3] vs replay [3, 3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=1: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 1 worlds |
| dream t=1: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0000=0.9250, r0001=0.9333, r0002=0.9333, r0003=0.9417 |
| dream t=1: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.925, 0.933333, 0.933333, 0.941667], selected [3], expected 3, next cycle deploys r0003 |
| dream t=1: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=1: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0003 clean |

Dreaming after cycle 1: V = [0.925, 0.933333, 0.933333, 0.941667], selected index 3 (r0003); argmax replay value (incumbent included); V = [0.925, 0.933333, 0.933333, 0.941667]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0001_t1m1 | rewrite: adaptive portfolio policy (prefix trajectories, dynamic batches, beta schedule, plan_grid) replaces the fixed widen/deepen schedule; wasted probes afte | 252 | [True] | 0.9333333333333333 |
| r0002_t1m2 | wasted probes after the final best: stop stagnating branches earlier; perturb w_trend -> 0.8054; perturb plan_depth_step -> 2 | 14 | [True] | 0.9333333333333333 |
| r0003_t1m3 | wasted probes after the final best: stop stagnating branches earlier; perturb max_width_lo -> 0.8235; perturb explore_on_stall -> 0.8285 | 12 | [True] | 0.9416666666666667 |

Ground truth (fresh online searches, 8 seeds per version): versions [0, 1, 2, 3], replay V [0.925, 0.9333, 0.9333, 0.9417], online V [-0.075, -0.045, -0.045, -0.045], online mean gain [0.0, 0.0, 0.0, 0.0], online mean calls [9.0, 6.0, 6.0, 6.0]; Spearman(replay, online) = 0.8164965809277261; selected r0003, best online r0001; selected - incumbent online V = +0.0300 [+0.0300, +0.0300]; selected - incumbent online gain = +0.00000 [+0.00000, +0.00000]

## Cycle 2

Deployed policy `r0003` (t1m3, default beta 0.6); plan 3 x 3 (one live manifest: evidence insufficient for a live-best trend and gains balanced: conservative bootstrap from the fallback grid); N = 6 attempts in k = 2 rounds, batches [3, 3]; 3 planned cells left unprobed at stop; outcomes {'ok': 6}; best 1.000000 -> 1.000000.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 2) used (3, 2): one live manifest: evidence insufficient for a live-best trend and gains balanced: conservative bootstrap from the fallb |
| live root = best program so far (root='best') | PASS | root 1.000000 vs best so far 1.000000 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 2 rounds, batch sizes [3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 6/6 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 6/6 match |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.000000 -> 1.000000 (trace: 1.0 -> 1.0, kept None) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3] vs replay [3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=2: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 2 worlds |
| dream t=2: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0003=0.4483, r0004=0.4483, r0005=0.4550, r0006=0.4550 |
| dream t=2: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.448333, 0.448333, 0.455, 0.455], selected [2], expected 2, next cycle deploys r0005 |
| dream t=2: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=2: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0005 clean |

Dreaming after cycle 2: V = [0.448333, 0.448333, 0.455, 0.455], selected index 2 (r0005); argmax replay value (incumbent included); V = [0.448333, 0.448333, 0.455, 0.455]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0004_t2m1 | wasted probes after the final best: stop stagnating branches earlier; perturb repairs_hi -> 2.3691; perturb prune_hi -> 0.2704 | 8 | [True] | 0.4483333333333333 |
| r0005_t2m2 | wasted probes after the final best: stop stagnating branches earlier; perturb evidence_lo: unchanged at 1.0 (clamp/round); perturb repairs_hi -> 2.0427 | 8 | [True] | 0.45499999999999996 |
| r0006_t2m3 | wasted probes after the final best: stop stagnating branches earlier; perturb explore_on_stall -> 0.8804; perturb w_depth -> 0.1098 | 10 | [True] | 0.45499999999999996 |

Ground truth (fresh online searches, 8 seeds per version): versions [3, 4, 5, 6], replay V [0.4483, 0.4483, 0.455, 0.455], online V [-0.0667, -0.0667, -0.0667, -0.0667], online mean gain [0.0, 0.0, 0.0, 0.0], online mean calls [8.0, 8.0, 8.0, 8.0]; Spearman(replay, online) = nan; selected r0005, best online r0003; selected - incumbent online V = +0.0000 [+0.0000, +0.0000]; selected - incumbent online gain = +0.00000 [+0.00000, +0.00000]

## Cycle 3

Deployed policy `r0005` (t2m2, default beta 0.6); plan 4 x 3 (live best plateaued: widen to cover new directions); N = 8 attempts in k = 3 rounds, batches [3, 3, 2]; 4 planned cells left unprobed at stop; outcomes {'ok': 8}; best 1.000000 -> 1.000000.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (4, 2) used (4, 2): live best plateaued: widen to cover new directions |
| live root = best program so far (root='best') | PASS | root 1.000000 vs best so far 1.000000 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 3 rounds, batch sizes [3, 3, 2] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 8/8 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 8/8 match |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.000000 -> 1.000000 (trace: 1.0 -> 1.0, kept None) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 2] vs replay [3, 3, 2] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=3: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 3 worlds |
| dream t=3: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0005=0.2811, r0007=0.2767, r0008=0.2767, r0009=0.2767 |
| dream t=3: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.281111, 0.276667, 0.276667, 0.276667], selected [0], expected 0, next cycle deploys r0005 |
| dream t=3: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=3: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0005 clean |

Dreaming after cycle 3: V = [0.281111, 0.276667, 0.276667, 0.276667], selected index 0 (r0005); argmax replay value (incumbent included); V = [0.281111, 0.276667, 0.276667, 0.276667]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0007_t3m1 | premature stops (ceiling missed): more patience, weaker pruning, wider; perturb open_hi: unchanged at 1.0 (clamp/round); perturb max_width_lo -> 1.0 | 10 | [True] | 0.27666666666666667 |
| r0008_t3m2 | premature stops (ceiling missed): more patience, weaker pruning, wider; perturb max_width_lo -> 0.5173; perturb prune_hi -> 0.2555 | 10 | [True] | 0.27666666666666667 |
| r0009_t3m3 | premature stops (ceiling missed): more patience, weaker pruning, wider; perturb w_depth -> 0.0; perturb w_trend -> 1.1794 | 14 | [True] | 0.27666666666666667 |

Ground truth (fresh online searches, 8 seeds per version): versions [5, 7, 8, 9], replay V [0.2811, 0.2767, 0.2767, 0.2767], online V [-0.075, -0.075, -0.075, -0.075], online mean gain [0.0, 0.0, 0.0, 0.0], online mean calls [9.0, 9.0, 9.0, 9.0]; Spearman(replay, online) = nan; selected r0005, best online r0005; incumbent kept

## Cycle 4

Deployed policy `r0005` (t2m2, default beta 0.6); plan 5 x 3 (live best plateaued: widen to cover new directions); N = 9 attempts in k = 3 rounds, batches [3, 3, 3]; 5 planned cells left unprobed at stop; outcomes {'ok': 9}; best 1.000000 -> 1.000000.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (5, 2) used (5, 2): live best plateaued: widen to cover new directions |
| live root = best program so far (root='best') | PASS | root 1.000000 vs best so far 1.000000 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 3 rounds, batch sizes [3, 3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 9/9 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 9/9 match |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.000000 -> 1.000000 (trace: 1.0 -> 1.0, kept None) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3] vs replay [3, 3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |

## Shadow monitor (sealed splits; never shown to the loop)

| cycle | version | decision score | sealed |
|---|---|---|---|
| 0 | seed | 0.25 | holdout: S=0.375; ood: S=0.500 |
| 1 | t1/b0.a0 | 1.0 | holdout: S=0.750; ood: S=0.875 |

## Report

```json
{
 "seed_score": 0.25,
 "best_score": 1.0,
 "calls_per_cycle": [
  9,
  6,
  8,
  9
 ],
 "spend": {
  "loop_usd": 0.0,
  "shadow_monitor_usd": 0.0,
  "report_usd": 0.0
 }
}
```
