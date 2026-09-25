# GEPA: from-scratch validation runs

Each run starts from its domain's **untouched seed artifact**. It uses a fresh run directory `validation/gepa/<run>/`, which is deleted first. The live run also uses a fresh LLM cache, `validation/gepa/.cache_agentqa_live/`, which is also deleted first. Nothing is reused from `.rsi_cache` or earlier results.

Every run was produced by `python experiments/gepa/validate_gepa.py <run>`. The engine writes `trace.jsonl` in the `rsi.trace` format, one iteration per round. Each iteration records:
- parent selection, with the Pareto sampling weights;
- every evaluation, with per-task and raw trial scores;
- the reflective records;
- the reflection prompt and reply;
- the **actual** diff;
- the gate arithmetic;
- the decision;
- the loop state before and after.

The shadow monitor (`rsi.trace.ShadowMonitor`) scores every new incumbent on the sealed splits. The loop never sees those numbers, and the test `tests/test_gepa_validation.py` proves the monitor is write-only.

Files in each run directory:

| file | contents |
|---|---|
| `TRACE.md` | the rendered trace (`rsi.trace.inspect`) |
| `report.json` | seed vs final on every split via `rsi.core.transfer_report` (report-only unsealing); spend from the meters; rollouts by phase |
| `audit.md` / `audit.json` | one row per iteration, re-derived **independently** from the trace, the ledger and the artifact store |
| `ledger.jsonl`, `artifacts/`, `state.json`, `run_log.jsonl` | the engine's own records |

**Independent checks in `audit.md`.** Each run passes all of them.
- Gate arithmetic.
- The recorded diff equals `parent.diff(child)` from the artifact store.
- Every decision agrees with its ledger node.
- The Pareto sampling weights, recomputed with a separate implementation of paper Alg. 2 (spec §3).
- The round-robin component choice, re-simulated.
- Every epoch's minibatches are disjoint apart from the reference padding.
- The child is evaluated on the same minibatch ids as its parent.
- Every accepted child is scored on all of D_pareto.
- The returned candidate is the argmax of mean D_pareto.
- The rollouts charged in the trace sum to the engine's counter.

**Ground truth, where one exists.**
- RuleWorld: the exact analytic expected score.
- AgentQA + SimModel: a 6-fresh-seed estimate per task.

The audit uses the ground truth to ask whether each accepted or rejected step was *really* better. For merges, the reference is the better parent, the same rule as the merge gate. Steps with no ground truth (the live run) are **unverifiable**, not correct.

## Summary

| run | setup | rollouts used / budget | iterations | proposals: accepted / rejected / skipped | seed → final, D_pareto | seed → final, sealed | spend |
|---|---|---|---|---|---|---|---|
| `ruleworld_offline` | RuleWorld, rich feedback, mock reflection LM, GEPA defaults | 318 / 300 | 8 | 8 / 0 / 0 | 0.000 → 0.322 | test (300): 0.000 → 0.318 sampled; **exact 0.000 → 0.314** (oracle 0.950) | $0 |
| `ruleworld_merge_offline` (extra) | same, GEPA+Merge, B = 1500 | 1534 / 1500 | 59 | reflective 32 / 13 / 3 skip; merges 7 accepted / 4 rejected | 0.000 → 0.898 | test: 0.000 → 0.857 sampled; **exact 0.000 → 0.847** | $0 |
| `agentqa_offline` | AgentQA two-module harness, SimModel + mock reflection LM | 121 / 120 | 20 | 4 / 3 / 13 skip_perfect | 0.375 → 1.000 | holdout (6) 0.667 → 1.000; ood (4) 0.500 → 1.000 | $0 |
| `agentqa_live` | AgentQA two-module harness, **claude haiku as task model and reflection LM** | 51 / 100 (stopped by the $1.20 USD stopper) | 5 | 2 / 2 / 1 skip_perfect | 0.500 → 0.500 (returned = seed) | holdout 0.667 → 0.667; ood 0.000 → 0.000 (same artifact) | $1.217 loop + $0.164 monitor + $0 report = **$1.381** |

The three offline runs were re-run in this session with the current code. `ruleworld_offline` and `agentqa_offline` reproduced the earlier attempt's audits byte-for-byte. `ruleworld_merge_offline` also reproduced them, except the merge rows of its "true gain" column, which now use the better parent (see run 2).

**Kept folders.**
- `agentqa_offline_mockbug/` keeps an earlier `agentqa_offline` run that exposed a bug in the offline AgentQA mock (see run 3).
- `agentqa_live_interrupted/` keeps the earlier live attempt. The account usage limit cut it off, and from iteration 7 every rollout failed.

**Live spend for this stage.**
- Interrupted attempt: $0.926, summed from its cache entries.
- This attempt: $1.381.
- Connectivity check: $0.001.
- **Total: about $2.31.**

---

## Run 1: `ruleworld_offline`

**Setup.**
- RuleWorld seed 0: two modules (`triage.md` → `reply.md`), 16 aspects, 4 customer families, 2 conflicting aspects, slip 5%, rich feedback.
- Splits: |D_train| = 30, |D_pareto| = 30, sealed test = 300.
- The task model is RuleWorld's simulated rule follower. The reflection LM is `RuleWorldReflectionLM`.
- GEPA defaults: b = 3, Pareto selection, round-robin, strict acceptance, no merge, B = 300.

**Narrative.** The seed scores 0 everywhere: it has no rules.

- **it 0.** Parent c0; component triage (round-robin starts at the first component).
  - The mock added the rule "oversized → mahogany". It also copied a verbatim *ticket fact* line ("Ticket T-3164: …"), which is the mock's modelled overfitting behaviour.
  - Minibatch 0 → 0.75, so the child was accepted as c1. c1 scores 0.077 on D_pareto; the truth gain is +0.051.
- **it 1.** Parent c1, reply: added "luxury → saffron". Minibatch 0 → 1, accepted as c2 (+0.056).
- **it 2.** Parent c2, sampled with p = 0.52 because c1 still wins some D_pareto keys.
  - The triage rewrite *dropped* "oversized → mahogany" and added "perishable → hematite". Accepted as c3 (+0.061).
- **it 3.** Reply gained express and bulk rules. Minibatch 0.33 → 1, accepted as c4: D_pareto 0.258, truth +0.109.
- **it 4.** The Pareto sampler picked c3 (p = 0.26) instead of the incumbent c4. That is paper Alg. 2 behaviour: c3 still owns frontier keys.
  - The child c5 re-added oversized, but as "anise", the other side of a conflicting aspect. It was accepted on the minibatch.
  - D_pareto 0.176 < 0.258, so c5 was added to the pool without becoming the incumbent.
- **it 5.** Parent c5, reply rewrite. Accepted into the pool (c6); no incumbent change.
- **it 6.** Parent c6, triage: deleted "oversized → anise". The child c7 reached D_pareto 0.281 and became the incumbent.
  - The truth says c7 is slightly *worse* on test than c4 (0.265 vs 0.277). This is the one false incumbent change: 30 D_pareto tasks, one draw each (winner's curse).
- **it 7.** c8: reply gained more rules. D_pareto 0.322; truth 0.314 on test.
- The budget check runs between iterations, so the run stopped at 318 of 300 (reference overshoot, spec §4.5).

**Verdict.**
- Every step's arithmetic, diff, selection weights and round-robin choice verify.
- Gate vs truth: 8 accepted, 0 rejected, 1 false accept.
- Accepted children are kept in the pool whatever their D_pareto score (reference behaviour).
- The returned c8 is the true best of the pool on test.

## Run 2: `ruleworld_merge_offline` (extra, to exercise rejections, skips and merge)

**Setup.** As run 1, but with `use_merge=True` (reference soft cap 5, 5-id subsample, overlap floor 5) and B = 1500.

**Narrative.** 59 iterations and 40 candidates.

- **Merges.**
  - Merges are due after new reflective finds. They were attempted at it 3, 5, 13, 18, 21, 24, 26, 31, 37, 39 and 48.
  - Early merges (it 3, 5) were rejected on the 5-id subsample: 0.45 vs 0.533 for the better parent.
  - From it 13 on, merges combining a strong triage from one line with a strong reply from another were accepted and often became the incumbent: c11, c15, c20, c23, c29.
  - Two accepted merges (it 24, it 48) were *ties* with the better parent in truth (gain 0.000). The merge gate uses `>=`, so ties are accepted.
- **Reflective rejections: 13.**
  - Many are "sum unchanged": the rewrite changed nothing that the 3-example minibatch could see.
  - The truth shows **8 false rejects**. Example: it 22 had minibatch 2 → 1.75 but a truth gain of +0.098.
  - It also shows **6 false accepts**. Example: it 55 had minibatch 2.17 → 2.67 but a truth gain of −0.022.
  - This is the expected price of a 3-example, one-draw gate (spec §8 critique).
- **skip_perfect: 3.** At it 27, 42 and 44 the parent scored 1.0 on all three minibatch items. Reflection was skipped (reference `skip_perfect_score=True`), but the 3 parent rollouts were still charged.
- **Incumbent changes.**
  - 22 changes; 20 have positive truth gain.
  - The changes at it 6 (−0.003) and it 17 (−0.026) are D_pareto noise.
- **Result.** Final c33: exact test 0.847, oracle 0.950.
- **Budget.** Rollouts by phase: seed 30, minibatch parent 144, minibatch child 135, reflective val 960, merge subsample 55, merge val 210.
  - Full D_pareto evaluation of every accepted child dominates the budget: 63% for reflective children, 77% including merges. This matches the reference accounting (spec §4.5).

**Verdict.**
- All independent checks pass: gate 56/56, diff 45/45, weights 48/48.
- The merge gate numbers (subsample sum vs the better parent) verify.
- Wrong steps exist only in the statistical sense above. They are faithful consequences of GEPA's small, noisy gate, not implementation errors.

## Run 3: `agentqa_offline`

**Setup.**
- The AgentQA two-module harness (`rsi.gepa.two_module_harness`): solver prompt → optional Python tool → reporter prompt. `harness.py` is frozen; the components are `prompts/solver.md` and `prompts/reporter.md`.
- Suite seed 0: |D_train| = 10, |D_pareto| = 8 (numeric), sealed holdout 6 and ood 4.
- Task model SimModel; reflection LM `AgentQAReflectionLM` (mock); B = 120.

**Narrative.**

- **it 0.** Reporter got "write a Python program". Minibatch 1 → 0, rejected.
  - The truth agrees: holdout −0.389. The reporter runs after the tool, so code there is wasted.
- **it 1.** Solver got "End with ANSWER:". Minibatch 1 → 1, rejected (truth −0.111).
- **it 2.** Reporter got the same ANSWER line. Minibatch 2 → 2, rejected.
  - Truth +0.056: a small false reject.
- **it 3.** Solver got "Write a short Python program…". Minibatch 2 → 3, accepted as c1.
  - D_pareto 0.375 → 1.000. Truth holdout 0.389 → 0.861. The monitor gave holdout 1.0 and ood 1.0.
  - This is the one real improvement, and it is the capability the domain rewards.
- **it 4–19.** Parent minibatches were mostly perfect, so 13 skip_perfect iterations followed.
  - Three more children were accepted into the pool on minibatch +1: c2 (reporter code), c3 (solver "think step by step") and c4.
  - Their truth gains were 0.000, +0.056 and 0.000, and none beat c1 on D_pareto. With a noisy task model, a +1 on 3 examples is easy to get by chance.
- The returned candidate is c1.

**Mock bug found by the earlier from-scratch attempt** (kept in `agentqa_offline_mockbug/`).
- `AgentQAReflectionLM` split the reflection prompt on every triple backtick. Once the solver wrote Python, the ```` ```python ```` block in the trace truncated `<side_info>`.
- The mock then fell back to its score-only branch and proposed irrelevant edits ("Use British spelling", "Keep the reply under 200 words"). The minibatch gate *accepted* two of these on noise.
- The fix is in `rsi/gepa/mocks.py:_split`. The E1 table in `docs/methods/gepa-impl.md` still carries the pre-fix numbers and is flagged there.

**Verdict.**
- All checks pass: gate 7/7, weights 20/20.
- Gate vs holdout truth: 2 false accepts (true gain 0.000 at it 6 and 11) and 1 false reject (it 2).

## Run 4: `agentqa_live`

**Setup.**
- As run 3, but claude haiku (`ClaudeCLI("haiku")` behind a fresh `CachedLLM`) is both the task model and the reflection LM.
- `max_metric_calls = 100`, workers 4.
- Safety budget: `Budget(max_usd=1.2, max_wall_s=1800)` on loop spend only. The monitor's spend is metered separately under `shadow:*`.

**Seed.**
- D_pareto 0.500 (4/8): val-002, 003, 005 and 007 correct.
- Sealed: holdout 0.667 and ood 0.000.
- The seed's task replies average about 4,900 tokens. Haiku writes long pseudo-tool-call transcripts (`<function_calls><invoke name="bash">…`) as plain text.

**Narrative.**

- **it 0.** Parent c0; minibatch evolve-008/009/001 → [1, 1, 0].
  - Haiku's reflection diagnosed the failure correctly: the answer was mixed with text and inconsistent (385 vs 282).
  - It rewrote the reporter into a 31-line guideline ending "**Final Answer: [number]**", with a task-specific example ("364").
  - Child [1, 1, 0]: sum 2 → 2, **rejected** by the strict rule.
  - Unverifiable whether that was right.
- **it 1.** Parent c0; minibatch 002/005/003 → [0, 0, 0].
  - The solver was rewritten to "return ONLY the bare number … no explanation". Child [1, 0, 0]: sum 0 → 1, accepted as c1.
  - c1 scored **0.250 on D_pareto**, below the seed's 0.500. It was kept in the pool as GEPA prescribes. The incumbent stayed c0.
- **it 2.** Pareto weights were c0 7 / c1 5. c1 wins val-005/006 and ties the all-zero keys; the weights were independently recomputed. Parent c1 (p = 0.42).
  - The reporter was rewritten to "You are solving **modular arithmetic** problems … use CRT … Fermat" (**task-specific overfitting** to the 3 minibatch items).
  - Minibatch 2 → 3, accepted as c2, but D_pareto 0.250. A likely false accept, but unverifiable.
- **it 3.** Parent c0; minibatch 006/006/000. The duplicate is the reference epoch padding, because 10 ids do not divide evenly into minibatches of 3.
  - The reporter was rewritten again (pow / bin-count / "verify with multiple methods").
  - Sum 1 → 1, rejected.
- **it 4.** Parent c0 scored [1, 1, 1] on its minibatch, so **skip_perfect**. Three rollouts were spent for nothing.
- **Stop.** The USD stopper tripped after it 4, at $1.217 against the $1.20 limit. Stoppers are checked between iterations (reference semantics).
  - Only 51 of the 100 rollouts were used. The task model alone cost $1.116 for 94 calls, about $0.012 per call, because of about 2,200 output tokens per call. Reflection cost $0.101 for 4 calls.
- The returned candidate is the seed c0. The transfer report therefore compares c0 with itself (all deltas 0), served from the cache, so it cost $0.

**Verdict.**
- The mechanics verify: gate 4/4, diff 4/4, weights 5/5, round-robin 4/4, rollouts 51 = 51.
- Whether each accept or reject was *right* is **unverifiable**, because there is no ground truth.
- The observable evidence (D_pareto) says both accepted children were worse than the seed on D_pareto. That is consistent with a noisy haiku task model: seed parent minibatches went [0, 0, 0] and later [1, 1, 1], and the seed's D_pareto was 0.375 in the interrupted attempt and 0.500 here.
- A strict gate on 3 single draws cannot separate signal from that noise.

**Earlier interrupted attempt** (`agentqa_live_interrupted/`).
- 7 useful iterations:
  - rejected, rejected, skip_perfect;
  - accepted (c1), rejected, accepted (c2);
  - no_proposal at it 6: the reflection call itself returned the CLI error "You've hit your session limit". This was the first symptom of the usage limit.
- Then the usage limit hit the task model too. From it 7 onwards every parent rollout failed, and the engine logged `skip_infra_error`.
  - It still **charged 3 rollouts per failed iteration** toward B (63 → 75 …).
  - The loop did not stop, because no stopper fires on consecutive infra failures. See the observations in the stage report.
