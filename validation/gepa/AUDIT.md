# GEPA: Stage-B independent step audit

**Scope.** This audit covers every run directory under `validation/gepa/`:

- `ruleworld_offline` (8 iterations);
- `ruleworld_merge_offline` (59);
- `agentqa_offline` (20);
- `agentqa_live` (5, claude-haiku);
- the two kept earlier attempts, `agentqa_offline_mockbug` (20) and `agentqa_live_interrupted` (13).

That is 125 steps in total. Every run starts from the untouched seed artifact:

- RuleWorld uses `dom.seed_artifact()`. The check `seed_artifact_untouched` confirms that the store's c0 equals `run_start.seed_artifact_id`.
- AgentQA uses `rsi.gepa.two_module_harness()`. Its solver prompt is "You are a helpful assistant. Solve the question." and its reporter prompt is "Give the final answer to the question.". GEPA needs at least one named prompt module, and module-wise merge needs two. This is a documented deviation from `AgentQADomain.seed_artifact()`, which makes a single call.

**Method.** The audit tool is `experiments/gepa/validate_gepa_stageb.py` → `<run>/stageb.json`. It was written independently of `rsi.gepa`; it does not use the engine, frontier, strategies, merge or reflection modules. It re-derives each iteration from the raw per-task scores in `trace.jsonl`, the artifact store and the ledger. Where possible it uses the **reference implementation itself** (gepa-ai/gepa@d771eb21, vendored in the scratchpad):

| check | how it is re-derived |
|---|---|
| parent choice, minibatch, merge triplet, module sources, merge subsample | Replays **one shared `random.Random(seed)`** in reference order. The selector is an independent Alg. 2 (per-instance fronts, set-cover pruning, frequency sampling). The minibatches come from the **reference `EpochShuffledBatchSampler`**, imported from gepa-ai/gepa. The merge is transcribed from `gepa/proposer/merge.py`. The reference `select_program_candidate_from_pareto_front` is also run on a cloned RNG, to measure where python-set order would change a draw. |
| reflection prompt | For the offline runs, the parent's minibatch is **re-executed** with the traced seeds (fresh evaluator, no cache). The records are rebuilt, and the prompt is rendered with the **reference `InstructionProposalSignature.prompt_renderer`**; it must equal the traced prompt byte for byte. For the live runs, the prompt must match the verbatim template skeleton around the parent's text, with exactly b records, all from the minibatch. |
| child text | The **reference `parse_proposal`** is applied to the raw reply, restoring the trailing newline (documented deviation 1). The result must equal the stored child component; every other file must be unchanged; the traced diff must equal `parent.diff(child)`. |
| gate | Recomputed from the parent and child `eval` events, not from the gate's own numbers. A float-tie check uses exact fractions. |
| state updates | Recomputed at every `round_start`: rollouts, incumbent (argmax mean; ties → coverage → lowest index), frontier members, Pareto weights, round-robin pointers (children inherit the max of the parents' already-advanced pointers), and the merge schedule (`merges_due`, `last_iter_found_new_program`, soft cap). |
| split discipline | No D_pareto or sealed task input may appear in any reflection prompt. Rejected children must never be scored on D_pareto. |
| budget | The per-phase counts must equal the spec §4.5 identity (`|V| + b·parents + b·children + |V|·accepted + 5·merges + |V|·accepted merges`). The stop must fall at the right time. Spend is re-summed from the fresh LLM cache. |
| truth | RuleWorld: the exact analytic expected test score of parent and child (for merges, the better parent). AgentQA offline: stage A's 6-seed SimModel estimate on holdout. Live runs: none, so they are **unverifiable**. |
| content | Task-specific content in each diff: RuleWorld ticket facts; AgentQA literal task answers and task ids. Also re-proposals of a rewrite that was rejected earlier. |

**Verdict rule.** A step is **wrong** if any mechanical check fails. It is **questionable** if the mechanics are right but the step is wrong against the ground truth (a false accept or false reject), or if it spent budget uselessly (outage iterations). It is **unverifiable** if there is no ground truth. Otherwise it is **correct**.

## 1. Summary of step verdicts

| run | steps | correct | questionable | wrong | unverifiable | mechanical checks |
|---|---|---|---|---|---|---|
| ruleworld_offline | 8 | 7 | 1 (FA it 5) | 0 | 0 | 248/248 |
| ruleworld_merge_offline | 59 | 45 | 14 (6 FA, 8 FR) | 0 | 0 | 1647/1647 |
| agentqa_offline | 20 | 17 | 3 (FR it 2; FA it 6, 11) | 0 | 0 | 328/328 |
| agentqa_live | 5 | 1 (skip_perfect) | 0 | 0 | 4 | 121/121 |
| agentqa_offline_mockbug (kept) | 20 | 17 | 3 | 0 | 0 | 328/328 |
| agentqa_live_interrupted (kept) | 13 | 2 | 5 (skip_infra_error ×5; bug, fixed) | 0 | 6 | 211/211 |
| **total** | **125** | **89** | **26** | **0** | **10** | **2883/2883** |

**Headline.** Every step's mechanics reproduce exactly:
- The **RNG replay reproduces all 114 parent draws and minibatches**, all 11 merge triplets, module sources and 5-id subsamples, and all 20 "merge due but no triplet" fall-throughs.
- **All 67 offline reflection prompts equal the reference renderer's output byte for byte**, and all 76 children equal the reference parser's output (the 9 live prompts match the verbatim template skeleton).
- Every gate, pool update, frontier, incumbent, round-robin pointer, merge-schedule counter, per-phase rollout count and budget identity matches.

No step is *wrong*. The *questionable* steps are statistical, except for the outage iterations:
- The 3-example, one-draw minibatch gate is an **in-sample** test. The child is scored on the same 3 examples it was written from, with fresh draws. Of the 5 reflective false accepts in RuleWorld, 4 had a positive *expected* minibatch gain (+0.16 to +0.71, from ticket facts about the minibatch's own tickets) and zero or negative test gain. Only it 47 of the merge run (expected minibatch Δ = 0) is pure sampling noise.
- Two accepted merges are exact ties in truth. The merge gate uses `>=`, as in the reference.
- The 5 outage iterations of the interrupted live run charged 3 rollouts each against a dead backend. This is a real robustness bug; it is fixed (§4).

## 2. Per-round step audit

The column `p` is the Pareto sampling probability of the chosen parent. "truth Δ" is the child minus the (better) parent:
- RuleWorld: exact expected test score;
- AgentQA offline: stage A's 6-seed SimModel holdout estimate;
- live: none.

The Pareto weights, rollouts, incumbent, frontier and pointers at each `round_start` were recomputed and matched in every row, so those checks are not repeated per row. RuleWorld proposals are also checked for grounding: 73 of 73 added or edited rule lines name an aspect whose rule appears in the prompt's feedback, and every copied ticket id belongs to a minibatch record.

**Notes on specific rounds.**
- `ruleworld_offline` it 4: the sampler picked the non-incumbent c3 (p = 0.26), as Alg. 2 prescribes. The child re-added "oversized" with the other protocol of a conflicting aspect.
- `ruleworld_offline` it 5 (FA): c6 added 2 ticket facts. Expected minibatch Δ = +0.71, but test Δ = 0.000.
- `ruleworld_offline` it 6 is correct per step (+0.046). The incumbent change c4 → c7 in that round is −0.012 in truth: winner's curse of the D_pareto argmax, not a gate error. RUNS.md had placed the false accept at it 6; this is corrected in the RUNS.md errata.
- `ruleworld_merge_offline`:
  - Merges at it 3 and 5 were correctly rejected (0.45 < 0.533).
  - Merges at it 24 and 48 are ties with the better parent in truth (Δ = 0.000); accepting ties is reference `>=` semantics.
  - The merge at it 26 was accepted on an exact floating-point tie (3.4999999999999996 on both sides).
  - At it 10 and 25, the reference code with python-set iteration order would have drawn a different parent from the same RNG state. That is documented deviation 2 (sorted order); the sampling distribution is unchanged.
  - it 47 accepted exactly the triage rewrite that it 43 had rejected from the same parent c29 (1.7 → 2.3 vs 2.25 → 2.25; truth 0.000). The reflection LM gets no history, as in the paper, so a rejected idea can come back and pass on noise.
  - it 51 re-proposed the rewrite rejected at it 45 and was rejected again.
- `agentqa_offline` it 6: the reporter "write Python" rewrite that was rejected at it 0 (on parent c0) was proposed again on parent c1, whose solver already writes Python. It was accepted with truth 0.000. After it 3, every candidate scores 1.0 on all of D_pareto. Set-cover pruning keeps only the newest of the exact-tie candidates as parent (p = 1.0), while the returned best is the oldest (lowest index). Both rules are the reference's.
- `agentqa_live` (claude-haiku as task model and reflection LM; the outcome of every gate is unverifiable):
  - **it 0**, reporter rewrite rejected (2 → 2). The rewrite diagnosed the failure correctly ("385 vs 282", answer mixed with text). It also copied the training answer "364" as an example.
  - **it 1**, solver "return ONLY the bare number" accepted (0 → 1). It copied the training labels 2695810 and 465110 into the prompt. D_pareto 0.25 < seed 0.50.
  - **it 2**, parent c1 (p = 0.42). The reporter was rewritten as a "modular arithmetic / CRT / Fermat" specialist and told to "show all computational verification using code execution", although the reporter has no tools. This is task drift toward the 3 minibatch items (all modular exponentiation), weakness 5 in the spec. It also copied the training answer 234. Accepted 2 → 3, but D_pareto 0.25.
  - **it 3**, rejected (1 → 1). The minibatch was [006, 006, 000]: the reference sampler pads 10 ids to 12 with the least-frequent id, which here duplicates the last shuffled id. Replaying the reference sampler reproduces this exactly.
  - **it 4**, skip_perfect.
  - All copied answers are from D_train. The paper gives the optimizer full train access, so this is not a leak. No D_pareto or sealed content reached any prompt.


#### `ruleworld_offline`: {'correct': 7, 'questionable': 1}; all 248 mechanical checks pass

| it | event | parent (p) | module / merge | gate: before → after (Δ) | truth Δ | verdict | evidence |
|---|---|---|---|---|---|---|---|
| 0 | accepted | c0 (1.00) | triage.md | 0 → 0.75 (+0.75) | +0.051 | correct | task-specific content added (facts 1, answers [], ids []) |
| 1 | accepted | c1 (1.00) | reply.md | 0 → 1 (+1) | +0.056 | correct | task-specific content added (facts 1, answers [], ids []) |
| 2 | accepted | c2 (0.52) | triage.md | 0 → 0.5 (+0.5) | +0.061 | correct | replay + recomputation agree |
| 3 | accepted | c3 (0.53) | reply.md | 0.333 → 1 (+0.667) | +0.109 | correct | replay + recomputation agree |
| 4 | accepted | c3 (0.26) | triage.md | 0.583 → 1.5 (+0.917) | +0.052 | correct | task-specific content added (facts 1, answers [], ids []) |
| 5 | accepted | c5 (0.27) | reply.md | 1.2 → 1.95 (+0.75) | +0.000 | questionable | false accept vs truth (+0.000); task-specific content added (facts 2, answers [], ids []) |
| 6 | accepted | c6 (0.33) | triage.md | 0.65 → 1.22 (+0.567) | +0.046 | correct | replay + recomputation agree |
| 7 | accepted | c7 (0.43) | reply.md | 0.25 → 1.75 (+1.5) | +0.049 | correct | task-specific content added (facts 1, answers [], ids []) |

#### `ruleworld_merge_offline`: {'correct': 45, 'questionable': 14}; all 1647 mechanical checks pass

| it | event | parent (p) | module / merge | gate: before → after (Δ) | truth Δ | verdict | evidence |
|---|---|---|---|---|---|---|---|
| 0 | accepted | c0 (1.00) | triage.md | 0 → 0.75 (+0.75) | +0.051 | correct | task-specific content added (facts 1, answers [], ids []) |
| 1 | accepted | c1 (1.00) | reply.md | 0 → 1 (+1) | +0.056 | correct | task-specific content added (facts 1, answers [], ids []) |
| 2 | accepted | c1 (0.48) | triage.md | 0 → 0.5 (+0.5) | +0.061 | correct | replay + recomputation agree |
| 3 | merge_rejected | c2+c3 (anc c1) | merge | sub 0.533 → 0.45 (≥ ⇒ False) | +0.056 | questionable | false reject vs truth (+0.056) |
| 4 | accepted | c3 (0.35) | reply.md | 0.583 → 0.833 (+0.25) | +0.049 | correct | replay + recomputation agree |
| 5 | merge_rejected | c2+c4 (anc c1) | merge | sub 0.533 → 0.45 (≥ ⇒ False) | +0.000 | correct | replay + recomputation agree |
| 6 | accepted | c2 (0.32) | triage.md | 0.817 → 1.22 (+0.4) | +0.051 | correct | task-specific content added (facts 1, answers [], ids []) |
| 7 | accepted | c4 (0.45) | triage.md | 0.5 → 0.75 (+0.25) | +0.096 | correct | replay + recomputation agree |
| 8 | accepted | c5 (0.50) | reply.md | 0.667 → 1.2 (+0.533) | +0.096 | correct | replay + recomputation agree |
| 9 | accepted | c6 (0.46) | reply.md | 1.83 → 2.23 (+0.4) | +0.061 | correct | replay + recomputation agree |
| 10 | accepted | c8 (0.35) | triage.md | 0.833 → 1.5 (+0.667) | +0.056 | correct | task-specific content added (facts 1, answers [], ids []) |
| 11 | rejected | c9 (0.41) | reply.md | 1.6 → 1.43 (-0.167) | +0.050 | questionable | false reject vs truth (+0.050) |
| 12 | accepted | c8 (0.27) | reply.md | 1.03 → 1.43 (+0.4) | +0.081 | correct | replay + recomputation agree |
| 13 | merge_accepted | c9+c10 (anc c8) | merge | sub 1.83 → 2 (≥ ⇒ True) | +0.056 | correct | replay + recomputation agree |
| 14 | rejected | c7 (0.37) | triage.md | 2.15 → 2.15 (+0) | +0.000 | correct | replay + recomputation agree |
| 15 | accepted | c11 (0.63) | triage.md | 1.45 → 1.7 (+0.25) | +0.051 | correct | replay + recomputation agree |
| 16 | accepted | c12 (0.41) | reply.md | 1.83 → 2.42 (+0.583) | +0.058 | correct | task-specific content added (facts 1, answers [], ids []) |
| 17 | accepted | c12 (0.38) | triage.md | 1.57 → 1.85 (+0.283) | +0.032 | correct | replay + recomputation agree |
| 18 | merge_accepted | c13+c14 (anc c11) | merge | sub 3.2 → 3.35 (≥ ⇒ True) | +0.032 | correct | replay + recomputation agree |
| 19 | rejected | c13 (0.24) | triage.md | 2.75 → 2.75 (+0) | +0.000 | correct | replay + recomputation agree |
| 20 | accepted | c14 (0.31) | reply.md | 1.08 → 2.5 (+1.42) | +0.004 | correct | task-specific content added (facts 1, answers [], ids []) |
| 21 | merge_rejected | c13+c16 (anc c12) | merge | sub 3.47 → 2.87 (≥ ⇒ False) | -0.021 | correct | replay + recomputation agree |
| 22 | rejected | c15 (0.44) | reply.md | 2 → 1.75 (-0.25) | +0.098 | questionable | false reject vs truth (+0.098) |
| 23 | accepted | c15 (0.44) | triage.md | 1.67 → 2 (+0.333) | +0.010 | correct | replay + recomputation agree |
| 24 | merge_accepted | c16+c17 (anc c14) | merge | sub 3.2 → 3.8 (≥ ⇒ True) | +0.000 | questionable | false accept vs truth (+0.000) |
| 25 | accepted | c15 (0.40) | reply.md | 2.33 → 2.58 (+0.25) | +0.051 | correct | replay + recomputation agree |
| 26 | merge_accepted | c18+c19 (anc c14) | merge | sub 3.5 → 3.5 (≥ ⇒ True) | +0.010 | correct | replay + recomputation agree |
| 27 | skip_perfect | c20 (0.50) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 28 | accepted | c19 (0.50) | triage.md | 1.58 → 1.83 (+0.25) | +0.008 | correct | replay + recomputation agree |
| 29 | rejected | c20 (0.50) | reply.md | 2.67 → 2.67 (+0) | +0.001 | questionable | false reject vs truth (+0.001) |
| 30 | accepted | c19 (0.50) | reply.md | 1.87 → 2.27 (+0.4) | +0.096 | correct | replay + recomputation agree |
| 31 | merge_accepted | c20+c22 (anc c15) | merge | sub 4.02 → 4.3 (≥ ⇒ True) | +0.010 | correct | replay + recomputation agree |
| 32 | accepted | c23 (0.36) | triage.md | 2.4 → 2.6 (+0.2) | +0.026 | correct | replay + recomputation agree |
| 33 | rejected | c22 (0.45) | triage.md | 2.47 → 1.3 (-1.17) | +0.006 | questionable | false reject vs truth (+0.006) |
| 34 | accepted | c22 (0.45) | reply.md | 2.42 → 2.75 (+0.333) | +0.001 | correct | replay + recomputation agree |
| 35 | accepted | c22 (0.45) | triage.md | 2.42 → 2.75 (+0.333) | +0.000 | questionable | false accept vs truth (+0.000); task-specific content added (facts 1, answers [], ids []) |
| 36 | accepted | c26 (0.47) | reply.md | 2.67 → 3 (+0.333) | +0.024 | correct | replay + recomputation agree |
| 37 | merge_rejected | c24+c27 (anc c22) | merge | sub 3.72 → 3.58 (≥ ⇒ False) | +0.024 | questionable | false reject vs truth (+0.024) |
| 38 | accepted | c26 (0.27) | triage.md | 2.17 → 2.5 (+0.333) | +0.057 | correct | replay + recomputation agree |
| 39 | merge_accepted | c27+c28 (anc c22) | merge | sub 3.93 → 4.6 (≥ ⇒ True) | +0.024 | correct | replay + recomputation agree |
| 40 | rejected | c24 (0.42) | reply.md | 2.8 → 2.8 (+0) | +0.000 | correct | replay + recomputation agree |
| 41 | rejected | c29 (0.58) | reply.md | 1.85 → 1.55 (-0.3) | +0.027 | questionable | false reject vs truth (+0.027) |
| 42 | skip_perfect | c24 (0.42) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 43 | rejected | c29 (0.58) | triage.md | 2.25 → 2.25 (+0) | +0.000 | correct | replay + recomputation agree |
| 44 | skip_perfect | c29 (0.58) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 45 | rejected | c24 (0.42) | triage.md | 2.25 → 2.25 (+0) | +0.000 | correct | replay + recomputation agree |
| 46 | rejected | c29 (0.58) | reply.md | 2.75 → 2.58 (-0.167) | +0.011 | questionable | false reject vs truth (+0.011) |
| 47 | accepted | c29 (0.58) | triage.md | 1.7 → 2.3 (+0.6) | +0.000 | questionable | false accept vs truth (+0.000); re-proposes the rewrite rejected at it 43 |
| 48 | merge_accepted | c24+c30 (anc c22) | merge | sub 4.1 → 4.1 (≥ ⇒ True) | +0.000 | questionable | false accept vs truth (+0.000) |
| 49 | accepted | c24 (0.42) | reply.md | 2.5 → 3 (+0.5) | +0.047 | correct | replay + recomputation agree |
| 50 | accepted | c31 (0.43) | reply.md | 1.55 → 1.6 (+0.05) | +0.022 | correct | replay + recomputation agree |
| 51 | rejected | c24 (0.30) | triage.md | 2 → 2 (+0) | +0.000 | correct | re-proposes the rewrite rejected at it 45 |
| 52 | accepted | c31 (0.43) | triage.md | 1.67 → 3 (+1.33) | +0.027 | correct | replay + recomputation agree |
| 53 | accepted | c34 (0.44) | reply.md | 2 → 3 (+1) | +0.000 | questionable | false accept vs truth (+0.000); task-specific content added (facts 1, answers [], ids []) |
| 54 | accepted | c32 (0.27) | triage.md | 2.8 → 3 (+0.2) | +0.057 | correct | replay + recomputation agree |
| 55 | accepted | c36 (0.49) | reply.md | 2.17 → 2.67 (+0.5) | -0.022 | questionable | false accept vs truth (-0.022); task-specific content added (facts 1, answers [], ids []) |
| 56 | rejected | c37 (0.33) | triage.md | 2.75 → 2.75 (+0) | +0.000 | correct | replay + recomputation agree |
| 57 | accepted | c37 (0.33) | reply.md | 2.38 → 2.8 (+0.417) | +0.022 | correct | replay + recomputation agree |
| 58 | accepted | c33 (0.32) | triage.md | 1.8 → 2.05 (+0.25) | +0.005 | correct | replay + recomputation agree |

#### `agentqa_offline`: {'correct': 17, 'questionable': 3}; all 328 mechanical checks pass

| it | event | parent (p) | module / merge | gate: before → after (Δ) | truth Δ | verdict | evidence |
|---|---|---|---|---|---|---|---|
| 0 | rejected | c0 (1.00) | reporter.md | 1 → 0 (-1) | -0.389 | correct | replay + recomputation agree |
| 1 | rejected | c0 (1.00) | solver.md | 1 → 1 (+0) | -0.111 | correct | replay + recomputation agree |
| 2 | rejected | c0 (1.00) | reporter.md | 2 → 2 (+0) | +0.056 | questionable | false reject vs truth (+0.056) |
| 3 | accepted | c0 (1.00) | solver.md | 2 → 3 (+1) | +0.472 | correct | replay + recomputation agree |
| 4 | skip_perfect | c1 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 5 | skip_perfect | c1 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 6 | accepted | c1 (1.00) | reporter.md | 2 → 3 (+1) | +0.000 | questionable | false accept vs truth (+0.000); re-proposes the rewrite rejected at it 0 |
| 7 | accepted | c2 (1.00) | solver.md | 2 → 3 (+1) | +0.056 | correct | replay + recomputation agree |
| 8 | skip_perfect | c2 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 9 | skip_perfect | c2 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 10 | skip_perfect | c2 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 11 | accepted | c2 (1.00) | reporter.md | 2 → 3 (+1) | +0.000 | questionable | false accept vs truth (+0.000) |
| 12 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 13 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 14 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 15 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 16 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 17 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 18 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 19 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |

#### `agentqa_live`: {'unverifiable': 4, 'correct': 1}; all 121 mechanical checks pass

| it | event | parent (p) | module / merge | gate: before → after (Δ) | truth Δ | verdict | evidence |
|---|---|---|---|---|---|---|---|
| 0 | rejected | c0 (1.00) | reporter.md | 2 → 2 (+0) | - | unverifiable | no ground truth (live); task-specific content added (facts 0, answers [('364', 'evolve')], ids []) |
| 1 | accepted | c0 (1.00) | solver.md | 0 → 1 (+1) | - | unverifiable | no ground truth (live); task-specific content added (facts 0, answers [('2695810', 'evolve'), ('465110', 'evolve')], ids []) |
| 2 | accepted | c1 (0.42) | reporter.md | 2 → 3 (+1) | - | unverifiable | no ground truth (live); task-specific content added (facts 0, answers [('234', 'evolve')], ids []) |
| 3 | rejected | c0 (0.58) | reporter.md | 1 → 1 (+0) | - | unverifiable | no ground truth (live); task-specific content added (facts 0, answers [('421', 'evolve'), ('421', 'smoke')], ids []) |
| 4 | skip_perfect | c0 (0.58) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |

#### `agentqa_offline_mockbug`: {'correct': 17, 'questionable': 3}; all 328 mechanical checks pass

| it | event | parent (p) | module / merge | gate: before → after (Δ) | truth Δ | verdict | evidence |
|---|---|---|---|---|---|---|---|
| 0 | rejected | c0 (1.00) | reporter.md | 1 → 0 (-1) | -0.389 | correct | replay + recomputation agree |
| 1 | rejected | c0 (1.00) | solver.md | 1 → 1 (+0) | -0.111 | correct | replay + recomputation agree |
| 2 | rejected | c0 (1.00) | reporter.md | 2 → 2 (+0) | +0.056 | questionable | false reject vs truth (+0.056) |
| 3 | accepted | c0 (1.00) | solver.md | 2 → 3 (+1) | +0.472 | correct | replay + recomputation agree |
| 4 | skip_perfect | c1 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 5 | skip_perfect | c1 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 6 | accepted | c1 (1.00) | reporter.md | 2 → 3 (+1) | +0.000 | questionable | false accept vs truth (+0.000) |
| 7 | accepted | c2 (1.00) | solver.md | 2 → 3 (+1) | +0.111 | correct | replay + recomputation agree |
| 8 | skip_perfect | c2 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 9 | skip_perfect | c2 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 10 | skip_perfect | c2 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 11 | accepted | c2 (1.00) | reporter.md | 2 → 3 (+1) | +0.000 | questionable | false accept vs truth (+0.000) |
| 12 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 13 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 14 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 15 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 16 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 17 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 18 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 19 | skip_perfect | c4 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |

#### `agentqa_live_interrupted`: {'unverifiable': 6, 'correct': 2, 'questionable': 5}; all 211 mechanical checks pass

| it | event | parent (p) | module / merge | gate: before → after (Δ) | truth Δ | verdict | evidence |
|---|---|---|---|---|---|---|---|
| 0 | rejected | c0 (1.00) | reporter.md | 2 → 2 (+0) | - | unverifiable | no ground truth (live) |
| 1 | rejected | c0 (1.00) | solver.md | 2 → 2 (+0) | - | unverifiable | no ground truth (live) |
| 2 | skip_perfect | c0 (1.00) | - | parent [1.0, 1.0, 1.0] | - | correct | skip_perfect: parent perfect on the minibatch (reference rule) |
| 3 | accepted | c0 (1.00) | reporter.md | 0 → 1 (+1) | - | unverifiable | no ground truth (live); task-specific content added (facts 0, answers [('421', 'evolve'), ('421', 'smoke')], ids []) |
| 4 | rejected | c0 (1.00) | solver.md | 2 → 2 (+0) | - | unverifiable | no ground truth (live) |
| 5 | accepted | c0 (1.00) | reporter.md | 0 → 1 (+1) | - | unverifiable | no ground truth (live) |
| 6 | no_proposal | c0 (1.00) | solver.md | parent [0.0, 0.0, 1.0] | - | correct | replay + recomputation agree |
| 7 | skip_infra_error | c0 (1.00) | - | parent [0.0, 0.0, 0.0] | - | questionable | infra failure: rollouts charged, no proposal |
| 8 | skip_infra_error | c0 (1.00) | - | parent [0.0, 0.0, 0.0] | - | questionable | infra failure: rollouts charged, no proposal |
| 9 | skip_infra_error | c0 (1.00) | - | parent [0.0, 0.0, 0.0] | - | questionable | infra failure: rollouts charged, no proposal |
| 10 | skip_infra_error | c0 (1.00) | - | parent [0.0, 0.0, 0.0] | - | questionable | infra failure: rollouts charged, no proposal |
| 11 | skip_infra_error | c0 (1.00) | - | parent [0.0, 0.0, 0.0] | - | questionable | infra failure: rollouts charged, no proposal |
| 12 | incomplete | c0 (1.00) | - | - | - | unverifiable | incomplete round (run killed before the parent evaluation was recorded) |

## 3. Paper alignment

### 3a. Mechanism level

Each item cites the spec section and the trace evidence. "Replay" means the independent re-derivation above.

| # | spec item | traced behaviour | status |
|---|---|---|---|
| M1 | §3 init: seed scored on all of D_pareto, charged to B | `baseline` event, seq 2 of every trace; `seed_val` = \|V\| | faithful |
| M2 | §3 / Alg. 2 Pareto selection: per-instance fronts with ties, set-cover pruning in ascending aggregate order, P ∝ frequency | `note` "parent selection" weights = independent recomputation (114/114); draws = replay (114/114) | faithful |
| M3 | §4.4 epoch-shuffled minibatch, b = 3, padded with the least-frequent ids, slot (i·b) mod L | reference sampler replay 114/114, including the duplicate padded id ([006, 006, 000], live it 3) | faithful |
| M4 | §3 skip rule: all parent scores ≥ perfect ⇒ skip, b rollouts still charged | 17 skip_perfect steps across the four current runs (3 + 13 + 1), 31 across all six directories; `skip_perfect_rule` holds and 3 rollouts are charged each time | faithful |
| M5 | §4.4 round-robin per candidate; a child inherits max(parent pointers) after the advance | `analysis` component = replay 77/77 (incl. the pointer advance before a failed reflection, as in the reference); `round_start.pool.next_component` recomputed every round | faithful |
| M6 | §6.1 verbatim meta-prompt and `format_samples` rendering | prompt == reference `prompt_renderer` output, byte for byte (67/67 offline); skeleton verbatim (76/76) | faithful. Deviation 1: the trailing newline of `<curr_param>` is stripped and restored (documented) |
| M7 | §6.1 parser: first to last fence, language tag dropped | child == reference `parse_proposal` (76/76) | faithful |
| M8 | §4.2 strict minibatch acceptance, sum(after) > sum(before), same ids, fresh rollouts | `gate` recomputed from the `eval` events (76/76); the child's ids equal the parent's and its seeds are disjoint; no float-tie flips | faithful |
| M9 | §3 an accepted child is scored on all of D_pareto and joins the pool whatever its score; a rejected child is never scored | `val_covers_pareto`, `no_val_eval_for_rejected`, ledger `x<i>` nodes | faithful |
| M10 | §3 FULL_EVAL_AND_ADD frontier update (> replaces, == joins) | frontier members and weights recomputed at every `round_start` | faithful |
| M11 | §3 merge scheduling: due only after an accepted *reflective* child; a None result falls through to reflection; a rejected merge consumes nothing | replay: 11 triplets and 20 "none" fall-throughs, all at the traced iterations; `round_start.merge` counters match | faithful to the code |
| M12 | §3 merge rules: dominator candidates, no direct ancestry, agg[a] ≤ both, desirable predictor, module-wise sources, 5-id bucketed subsample, accept iff Σ ≥ max(parent sums) | `merge_sources`, `merge_child_text`, `merge_subsample_ids` and `merge_gate` all 11/11 | faithful |
| M13 | §4.4 merge cap "maximum of 5" (paper) vs a soft cap (code) | the merge run accepted **7** merges with cap 5 (`total_merges_tested` reaches 7; `merges_due` accumulated to 14) | documented deviation: the paper says 5; the reference code's soft cap reproduces 7. `merge_cap_mode="hard"` is available |
| M14 | §4.5 budget identity; overshoot ≤ 2b + \|V\| because the stop check sits at the top of an iteration | identity holds in all 6 runs (318, 1534, 121, 51, 121, 75); overshoot 18/300, 34/1500, 1/120 | faithful |
| M15 | §3 return argmax mean D_pareto (ties → coverage → lowest index) | `returned_argmax` true in all runs | faithful |
| M16 | §2 / §4 split discipline: the reflection sees only D_train minibatch records; D_pareto gives scores only; test sealed | no D_pareto or sealed input in any of the 76 prompts; offline prompts are *exactly* the minibatch records (byte-exact rebuild); the monitor is write-only (code: `observe` returns None; `ShadowLLM` spend is filed under `shadow:*` and excluded from `usage_snapshot`; the `test_gepa_validation.py` on/off identity tests) | faithful. Open (low): monitor **wall time** counts toward `Budget.max_wall_s` / `Timeout` (see §5) |
| M17 | §8 weakness 8: N = 1 per evaluation, no noise band | `noise` event: mode none; `val_seed` fixed at 0 for every D_pareto evaluation (salted per artifact in RuleWorld) | faithful (documented deviation 6: the seeds are explicit) |
| M18 | §2 only the prompts change | `other_files_unchanged` 76/76 (harness.py frozen); merge children take non-component files from the ancestor | faithful |
| M19 | reflection LM = task LM (paper, inferred) | live: haiku in both roles | consistent with the paper's likely setup |

### 3b. Behaviour level

Where the observed dynamics match the paper:
- **The frontier keeps diverse winners and samples non-incumbent parents.**
  - Merge run: 28 of 48 selections chose a parent other than the incumbent, spread over 28 distinct parents. The pool ends with 39 members, 31 of them on the frontier.
  - `ruleworld_offline` it 4 chose c3 (p = 0.26).
  - This matches "balanced search tree" [paper Fig. 4].
- **Reflection uses the textual feedback.**
  - RuleWorld: 73 of 73 added rule lines are grounded in the "Rule: …" feedback of the minibatch. This holds by construction of the mock.
  - Live haiku: its rewrites quote the feedback (inconsistent answers, extraction failures, expected values).
- **Most of the rollout budget goes to validation** [paper Obs. 1]:
  - RuleWorld: 85% (B = 300) and 78% (82% including merge subsamples).
  - AgentQA: 33%, because 13 of 20 iterations were skip_perfect.
  - Live: 47%.
- **Prompts collect "niche, domain-specific facts"** [§6.6, weakness 4]:
  - RuleWorld: the returned artifacts carry 6 and 4 ticket facts.
  - Haiku copied 5 training answers into prompts across 4 rewrites.
- **Overfitting and winner's curse** [weakness 3]:
  - Merge run: the returned c33 has D_pareto 0.898 but exact test 0.847, and ranks 7th of 40 by truth. The true best is c36: test 0.884, D_pareto 0.881.
  - `ruleworld_offline`: the returned c8 is the true best (0.314).
- **The minibatch gate is coarse and noisy** [weakness 8]: 6 false accepts and 8 false rejects over 56 gated steps (merge run).

Where the dynamics do not match the paper, or cannot be compared:
- **Size of the gain.** The paper reports +10 to +20 pp with real LLMs over 1.8k–7k rollouts.
  - RuleWorld reaches 0 → 0.85 exact test at 1.5k rollouts, but it is a simulator with a mock reflection LM. It tests mechanisms and orderings, not absolute gains.
  - The live haiku run was stopped by the USD guard after 51 rollouts and returned the seed.
  - Cause: CPU scale and the $3 cap, not a bug.
- **Merge is not "sparse" here.** 11 attempts and 7 accepts in 59 iterations, because the soft cap lets `merges_due` accumulate (M13).
- **Live D_pareto evaluations share upstream outputs.** The fresh `CachedLLM` keys on (prompt, system, seed), and every D_pareto rollout uses seed 0. So c2, which changes only the reporter, reused c1's 8 cached solver replies. That is 8 of 102 task calls: 94 metered and 8 served from the cache.
  - The effect is common random numbers across candidates that share a module. It resembles DSPy's default LM cache in the paper's setting, but not fresh sampling.
  - It is noted in deviation 6; it is not a bug.

## 4. Inconsistency register

| item | status | evidence |
|---|---|---|
| Alg. 2 selection and set-cover pruning | faithful | M2; reference probe vectors are unit-tested (spec §4.7) |
| Sorted iteration order instead of python-set order | documented-deviation (impl §5.2) | changes 2 of 114 draws (merge run, it 10 and 25) against the reference code on the same RNG state; the distribution is identical |
| Epoch sampler, padding and duplicate padded ids | faithful | M3 |
| Meta-prompt and rendering | faithful | M6 (byte-exact) |
| `<curr_param>` trailing newline stripped and restored | documented-deviation (impl §5.1) | M6 |
| Parser | faithful | M7 |
| Strict minibatch gate | faithful | M8 |
| Skip-perfect rule | faithful | M4 |
| Round-robin inheritance | faithful | M5 |
| Merge rules and schedule | faithful to the code | M11, M12 |
| Merge cap: paper "max 5", code soft cap (7 accepted) | documented-deviation (impl §5.4, spec §3) | M13 |
| Merge zero-weight ancestor fallback | documented-deviation (impl §5.3) | not triggered in these runs |
| Budget identity and overshoot | faithful | M14 |
| Argmax return | faithful | M15 |
| Split discipline and write-only monitor (numbers) | faithful | M16 |
| Monitor wall time counts toward the wall-clock stoppers | inconsistent-open (low) | the monitor runs inside the loop's wall clock. `agentqa_live` stopped on USD at 990 s < 1800 s, so there was no effect here. Needs a core `Budget` change (see core_change_requests) |
| Seeded rollouts, N = 1, fixed `val_seed` | documented-deviation (impl §5.6) | M17 |
| Live cache sharing of identical upstream calls across candidates | documented-deviation (impl §5.6) | §3b |
| AgentQA seed = `two_module_harness`, not `AgentQADomain.seed_artifact()` | documented-deviation | GEPA needs named prompt modules; its solver text is "You are a helpful assistant. Solve the question." |
| Sustained outage burns b rollouts per iteration and never stops | **inconsistent-fixed** | `agentqa_live_interrupted` it 7–11. Fixed with `ConsecutiveInfraFailures` (§5) |
| RUNS.md placed `ruleworld_offline`'s false accept at it 6 | **inconsistent-fixed** (report) | per-step truth: it 5 = 0.000, it 6 = +0.046. Erratum added to RUNS.md |
| E1 table in gepa-impl.md still carries the pre-fix AgentQA-mock numbers | inconsistent-open (doc, flagged by stage A) | impl §4 E1 note. Not re-run here: an experiment table, not a validation run |
| Paper's gain magnitude | unverifiable | CPU mock and a 51-rollout live run |
| Interrupted run's spend ($0.926) | unverifiable | its cache was deleted by the from-scratch rerun; the trace shows loop $0.791 at it 12 |
| Live step outcomes (4 gated steps) | unverifiable | no ground truth; D_pareto (8 tasks, one draw) is against both accepted children (0.25 vs 0.50) |

## 5. Fixes and re-runs

**Fix: outage stopper.** Files: `rsi/gepa/stoppers.py:ConsecutiveInfraFailures`, `rsi/gepa/config.py:max_consecutive_infra_failures = 3`, `rsi/gepa/engine.py`, exported from `rsi.gepa`.
- The run now stops with `stop_reason="infra_outage"` after 3 consecutive iterations lost to the backend: `skip_infra_error`, or `no_proposal` where every reflection call failed with an LLM error.
- It is appended after the "no stop condition" check, so it is a safety net only.
- Regression tests, in `tests/test_gepa_validation_stageb.py` (4 tests, all pass; all 67 GEPA tests pass):
  - an outage after 60 task calls now stops with `infra_outage`, well under B; with `None`, the old behaviour charges more than 10 dead iterations up to B;
  - identical trajectories on healthy runs;
  - the reflection-LLM-error case;
  - the stage-B replay reproduces a fresh RuleWorld+merge run.
- Documented in `docs/methods/gepa-impl.md` §5.15.

**Re-runs from scratch** (fresh run directories; $0).
- `ruleworld_offline`, `agentqa_offline` and `ruleworld_merge_offline` were re-run with the fix.
- Their `trajectory.json`, `run_log.jsonl` and `audit.md` are byte-identical to the pre-fix runs. `ledger.jsonl` differs only in timestamps. `run_start.stoppers` now lists `ConsecutiveInfraFailures`.
- Stage B was then re-run on all 6 directories: all checks pass.
- The live run was not repeated. The fix cannot change a run without an outage, and a repeat would exceed the stage's $3 cap.

**Live spend for the stage** (from the meters and the cache):

| item | spend |
|---|---|
| `agentqa_live`: loop $1.2168 (task $1.1161 for 94 calls + reflection $0.1007 for 4) + monitor $0.1641 (20 `shadow:task` calls) | $1.3809 (118 cache entries) |
| `agentqa_live_interrupted` (stage A's figure; unverifiable, see §4) | $0.926 |
| connectivity check | $0.001 |
| this audit | $0 |
| **total** | **about $2.31** |

## 6. Remaining open issues

1. The monitor's wall time counts toward wall-clock stoppers (`Budget.max_wall_s`, `Timeout`). This is low severity and core-owned.
2. The 3-example, one-draw strict gate is an in-sample test. It admits ticket facts and task drift, which account for most false accepts. This is faithful to GEPA; `acceptance="noise_margin"` and the `critic=` leakage screen exist but are off by default.
3. Winner's curse of the one-draw D_pareto argmax (merge run: the 7th-best candidate was returned). This is faithful to GEPA.
4. The reflection gets no memory of rejected rewrites, so rejected ideas return and can pass on noise (merge run: it 43 → 47; AgentQA offline: it 0 → 6). This is faithful to GEPA.
5. The live evidence is thin: 51 rollouts, and every step is unverifiable.
6. The E1 table in the implementation doc predates the AgentQA mock fix.
