# Audit of `sumdiff_offline`

Every check below is re-derived from the files on disk (trace.jsonl, trace_pool/, snapshots/, history/) with code independent of the loop where possible.

## Run-level checks

| check | result | detail |
|---|---|---|
| seed program re-graded independently | PASS | trace S=0.910491, independent=0.910491 (ok) |
| run starts from the untouched seed artifact | PASS | ed8962e6b1 |
| agent calls: sum of attempts over worlds = cost meter = trajectory | PASS | 43 attempts, meter 43 |
| returned best = best of every recorded successful attempt and the seed | PASS | 1.019011 |

## Cycle 1

Deployed policy `r0000` (initial, default beta 0.6); plan 3 x 5 (parallel refine: fixed fallback grid (width x depth never adapts)); N = 15 attempts in k = 5 rounds, batches [3, 3, 3, 3, 3]; 0 planned cells left unprobed at stop; outcomes {'ok': 15}; best 0.910491 -> 1.000000.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 4) used (3, 4): parallel refine: fixed fallback grid (width x depth never adapts) |
| live root = best program so far (root='best') | PASS | root 0.910491 vs best so far 0.910491 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 5 rounds, batch sizes [3, 3, 3, 3, 3] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 15/15 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 15/15 match |
| mock agent's claimed change = the stage its diff actually appends | PASS | 15/15 match; 4 attempts switched away from the branch direction (documented 25% mock behaviour) |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 0.910491 -> 1.000000 (trace: 0.910491 -> 1.0, kept t1/b1.a3) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3, 3, 3] vs replay [3, 3, 3, 3, 3] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=1: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 1 worlds |
| dream t=1: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0000=0.8650, r0001=0.9100, r0002=0.9100, r0003=0.9100 |
| dream t=1: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.865, 0.91, 0.91, 0.91], selected [1], expected 1, next cycle deploys r0001 |
| dream t=1: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=1: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0001 clean |

Dreaming after cycle 1: V = [0.865, 0.91, 0.91, 0.91], selected index 1 (r0001); argmax replay value (incumbent included); V = [0.865, 0.91, 0.91, 0.91]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0001_t1m1 | rewrite: adaptive portfolio policy (prefix trajectories, dynamic batches, beta schedule, plan_grid) replaces the fixed widen/deepen schedule; ceiling reached wi | 252 | [True] | 0.91 |
| r0002_t1m2 | ceiling reached with many probes: try a cheaper search (patience); perturb w_trend -> 0.8054; perturb plan_depth_step -> 3 | 8 | [True] | 0.91 |
| r0003_t1m3 | ceiling reached with many probes: try a cheaper search (patience); perturb max_width_lo -> 0.8235; perturb explore_on_stall -> 0.8285 | 6 | [True] | 0.91 |

Ground truth (fresh online searches, 8 seeds per version): versions [0, 1, 2, 3], replay V [0.865, 0.91, 0.91, 0.91], online V [0.6833, 0.4043, 0.4043, 0.4043], online mean gain [0.01377, 0.00778, 0.00778, 0.00778], online mean calls [15.0, 7.12, 7.12, 7.12]; Spearman(replay, online) = -1.0; selected r0001, best online r0000; selected - incumbent online V = -0.2790 [-0.6020, +0.0332]; selected - incumbent online gain = -0.00599 [-0.01153, -0.00062]

## Cycle 2

Deployed policy `r0001` (t1m1, default beta 0.6); plan 3 x 5 (one live manifest: evidence insufficient for a live-best trend and gains balanced: conservative bootstrap from the fallback grid); N = 12 attempts in k = 5 rounds, batches [3, 3, 3, 2, 1]; 2 planned cells left unprobed at stop; outcomes {'ok': 12}; best 1.000000 -> 1.017204.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 4) used (3, 4): one live manifest: evidence insufficient for a live-best trend and gains balanced: conservative bootstrap from the fallb |
| live root = best program so far (root='best') | PASS | root 1.000000 vs best so far 1.000000 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 5 rounds, batch sizes [3, 3, 3, 2, 1] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 12/12 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 12/12 match |
| mock agent's claimed change = the stage its diff actually appends | PASS | 12/12 match; 0 attempts switched away from the branch direction (documented 25% mock behaviour) |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.000000 -> 1.017204 (trace: 1.0 -> 1.017204, kept t2/b0.a3) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 3, 2, 1] vs replay [3, 3, 3, 2, 1] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=2: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 2 worlds |
| dream t=2: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0001=0.9010, r0004=0.9010, r0005=0.9010, r0006=0.8996 |
| dream t=2: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.901, 0.901, 0.901, 0.899571], selected [0], expected 0, next cycle deploys r0001 |
| dream t=2: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=2: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0001 clean |

Dreaming after cycle 2: V = [0.901, 0.901, 0.901, 0.899571], selected index 0 (r0001); argmax replay value (incumbent included); V = [0.901, 0.901, 0.901, 0.899571]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0004_t2m1 | ceiling reached with many probes: try a cheaper search (patience); perturb repairs_hi -> 2.3691; perturb prune_hi -> 0.7204 | 8 | [True] | 0.901 |
| r0005_t2m2 | ceiling reached with many probes: try a cheaper search (width); perturb repairs_hi -> 4.0; perturb max_width_lo -> 0.6155 | 10 | [True] | 0.901 |
| r0006_t2m3 | ceiling reached with many probes: try a cheaper search (width); perturb w_depth -> 0.0963; perturb max_width_lo -> 0.6014 | 10 | [True] | 0.8995714285714286 |

Ground truth (fresh online searches, 8 seeds per version): versions [1, 4, 5, 6], replay V [0.901, 0.901, 0.901, 0.8996], online V [0.5932, 0.5932, 0.5932, 0.5914], online mean gain [0.00285, 0.00285, 0.00285, 0.00285], online mean calls [9.0, 9.0, 9.0, 9.0]; Spearman(replay, online) = 1.0; selected r0001, best online r0001; incumbent kept

## Cycle 3

Deployed policy `r0001` (t1m1, default beta 0.6); plan 3 x 5 (live best still improving with balanced gains: hold the grid); N = 7 attempts in k = 3 rounds, batches [3, 3, 1]; 3 planned cells left unprobed at stop; outcomes {'ok': 7}; best 1.017204 -> 1.017725.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (3, 4) used (3, 4): live best still improving with balanced gains: hold the grid |
| live root = best program so far (root='best') | PASS | root 1.017204 vs best so far 1.017204 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 3 rounds, batch sizes [3, 3, 1] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 7/7 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 7/7 match |
| mock agent's claimed change = the stage its diff actually appends | PASS | 7/7 match; 2 attempts switched away from the branch direction (documented 25% mock behaviour) |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.017204 -> 1.017725 (trace: 1.017204 -> 1.017725, kept t3/b1.a0) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 1] vs replay [3, 3, 1] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |
| dream t=3: Eq.1 V_i and V recomputed from revealed cells (scores looked up in tree.json) | PASS | 4 versions x 3 worlds |
| dream t=3: every version re-replayed from history/ gives the recorded V (deterministic replay) | PASS | r0001=0.9146, r0007=0.9146, r0008=0.9126, r0009=0.9146 |
| dream t=3: selected = argmax V (incumbent kept on ties) and deployed next cycle | PASS | V=[0.914556, 0.914556, 0.912631, 0.914556], selected [0], expected 0, next cycle deploys r0001 |
| dream t=3: each revision's trace diff = history code diff (base -> revision) | PASS | 3 revisions |
| dream t=3: deployed policy passes the static check and copies no trace-specific id/score | PASS | r0001 clean |

Dreaming after cycle 3: V = [0.914556, 0.914556, 0.912631, 0.914556], selected index 0 (r0001); argmax replay value (incumbent included); V = [0.914556, 0.914556, 0.912631, 0.914556]

| version | change | diff lines | screens | re-replay V |
|---|---|---|---|---|
| r0007_t3m1 | ceiling reached with many probes: try a cheaper search (width); perturb max_width_lo -> 0.9123; perturb explore_on_stall -> 0.983 | 10 | [True] | 0.9145555555555557 |
| r0008_t3m2 | ceiling reached with many probes: try a cheaper search (width); perturb prune_hi -> 1.1492; perturb repairs_lo -> 0.4732 | 12 | [True] | 0.9126309523809524 |
| r0009_t3m3 | ceiling reached with many probes: try a cheaper search (patience); perturb w_depth -> 0.0168; perturb plan_width_step: unchanged at 1 (clamp/round) | 6 | [True] | 0.9145555555555557 |

Ground truth (fresh online searches, 8 seeds per version): versions [1, 7, 8, 9], replay V [0.9146, 0.9146, 0.9126, 0.9146], online V [0.4246, 0.4208, 0.4208, 0.4208], online mean gain [0.00186, 0.00186, 0.00186, 0.00186], online mean calls [9.0, 9.0, 9.0, 9.0]; Spearman(replay, online) = 0.3333333333333333; selected r0001, best online r0001; incumbent kept

## Cycle 4

Deployed policy `r0001` (t1m1, default beta 0.6); plan 4 x 3 (roots improved early while depth stalled: widen, trim depth); N = 9 attempts in k = 4 rounds, batches [3, 3, 2, 1]; 3 planned cells left unprobed at stop; outcomes {'ok': 9}; best 1.017725 -> 1.019011.

| check | result | detail |
|---|---|---|
| plan used within hard caps (and = requested clamped) | PASS | requested (4, 2) used (4, 2): roots improved early while depth stalled: widen, trim depth |
| live root = best program so far (root='best') | PASS | root 1.017725 vs best so far 1.017725 |
| online batches legal (<= W, one cell per branch, inside plan, parent first) and = recorded tree | PASS | 4 rounds, batch sizes [3, 3, 2, 1] |
| every attempt's trace diff = parent -> child program diff from the snapshot store | PASS | 9/9 match |
| every attempt re-graded independently (own grader, fresh process) = recorded score | PASS | 9/9 match |
| mock agent's claimed change = the stage its diff actually appends | PASS | 9/9 match; 1 attempts switched away from the branch direction (documented 25% mock behaviour) |
| best program = max(best so far, best successful attempt), strict improvement | PASS | 1.017725 -> 1.019011 (trace: 1.017725 -> 1.019011, kept t4/b3.a2) |
| replay fidelity: the recording policy replayed on its own world reveals exactly the online batches | PASS | online [3, 3, 2, 1] vs replay [3, 3, 2, 1] |
| manifest beta = baked-in default beta of the deployed code | PASS | beta 0.6 |

## Report

```json
{
 "seed_score": 0.9104910395109694,
 "best_score": 1.0190107355476399,
 "calls_per_cycle": [
  15,
  12,
  7,
  9
 ],
 "spend": {
  "loop_usd": 0.0,
  "shadow_monitor_usd": 0.0,
  "report_usd": 0.0
 }
}
```
