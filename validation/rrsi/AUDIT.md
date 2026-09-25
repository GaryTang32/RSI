# RRSI: stage-B independent step audit of the validation runs

**Scope.** The audit covers the three from-scratch runs under `validation/rrsi/`: `offline_agentqa`, `offline_harnessworld` and `live_agentqa`. It also covers the first `offline_agentqa` run, which is kept as `offline_agentqa_superseded/` because this audit found bugs in it. I did not write the code or the runs, and I audited them adversarially.

**Method.** Every step was re-derived from raw data by a new checker, `experiments/rrsi/validate_rrsi_stepcheck.py`:
- **Inputs:** the per-trial rewards and tokens in `trace.jsonl`, `evals/*.json` and the per-edit `history.jsonl`.
- **Formulas:** the spec formulas (`docs/methods/rrsi.md` §4), written out from scratch.
- **Isolation:** it imports nothing from `rsi.rrsi`, so a bug shared by the loop and its own `audit.py` cannot hide.
- **Output:** one row per check, written to `<run>/stepcheck.json`.

Manual review added:
- the claimed changes compared with the diffs;
- the prompts the proposer and critic actually saw;
- the monitor wiring in the code;
- the cache spend;
- k = 10 ground-truth re-evaluations (`offline_agentqa/reference_k10.json`, plus the analytic truth for HarnessWorld);
- a $0 replay of the live run on the current code.

**Live spend of this stage: $0.** No new paid call was made. The live replay used the run's own cache in offline mode, 107/107 hits. The stage-A live run had already spent $2.80 of the ~$3 cap.

**Claims-audit fix stage (2026-09-25, after stage B).** The claim-by-claim paper audit (`docs/claims/rrsi.md`) found nine mismatches with the released code (N1–N9) plus this register's open items. They were fixed (see the claims audit's "Fix log" and §5.2 below), and the two offline runs were **regenerated from scratch** on the fixed code, because the fixes change the proposer's prompt: no δ, S*, S_t or T in the directives, symbolic β/w in SKILL.md, and the BASELINE row in L_t. The mocks seed their RNG on the prompt, so this is a new realization, not a corrected copy. The stage-B offline runs are in git history (commit `02b830e`). `live_agentqa` was **not** re-run (a faithful rerun would cost about $2.8, near the whole $3 cap). It stays as the record of the *pre-fix* code, and its step check now flags the pre-fix prompt content as questionable. Live evidence on the fixed code:
- `results/rrsi/live_smoke.json`, a new Haiku run with a fresh cache;
- a Haiku critic check of the recorded leak (§2.3), $0.025.

§§1–4 below describe the regenerated offline runs and the pre-fix live run.

## 1. Summary of step verdicts

"Automatic" rows come from `stepcheck.json`. "Manual" rows are the judgement calls in §2 that the checker cannot make, such as whether a correct-by-rule decision was good.

| run | steps audited | correct | questionable | wrong | unverifiable |
|---|---|---|---|---|---|
| `offline_agentqa` (regenerated on the fixed code), automatic | 244 | 237 | 7 | 0 | 0 |
| `offline_harnessworld` (regenerated on the fixed code), automatic | 345 | 335 | 10 | 0 | 0 |
| `live_agentqa` (pre-fix code), automatic | 68 | 59 | 9 | 0 | 0 |
| manual checks, regenerated offline runs (§2.1–2.2) | 6 | 3 | 3 | 0 | 0 |
| manual checks, live run (§2.3) | 8 | 4 | 3 | 0 | 1 |
| **total** | **671** | **638** | **32** | **0** | **1** |
| stage-B `offline_agentqa` (superseded), automatic | 241 | 232 | 8 | 1 (domain duplicate question, since fixed in core) | 0 |
| stage-A `offline_agentqa_superseded` (pre-fix), automatic | 241 | 222 | 9 | **10** (9 RRSI steps, fixed; 1 domain) | 0 |

The live run's 9 questionable rows are its δ from R = 3 evaluations at k = 1, plus 8 rows the updated check adds for the pre-fix prompt: each of the four proposals saw δ, S*, S_t and T (claims N2), and its L_t lacked the BASELINE row (N5). The stage-B count of the regenerated runs' predecessors was 232/8/1 and 311/10/0. The single "wrong" row there (an AgentQA question shared by evolve and holdout) is gone: `make_suite` now de-duplicates, and the check "sealed splits are disjoint from evolve" passes.

**Checks that passed in every run:**
- Ŝ and Ĉ recomputed from raw trials match the trace and `evals/*.json` exactly.
- Every Algorithm 2 verdict matches: floor on S*, the cost rule above the band, the shaped rule inside it, ν, and dS and dC measured against S_t.
- Every argmax, S* update, b_t, σ_t, T_t, U_t, reserved variant, B_t (with g_t), and δ matches. δ agrees with the closed-form plug-in within 2%.
- Every history record's S, dS and outcome matches.
- Every critic-rejected candidate was never evaluated, and every evaluated candidate had a final critic accept.
- No holdout or OOD task id or question reached a proposer, analyst or critic input.
- No evolve answer or task id is in any evaluated diff.
- Each proposer saw the b_t, U_t, reserved flag and B_t of its round and the full evidence-aware L_t, BASELINE row included. In the regenerated runs, no proposer prompt contained δ, S*, S_t or T, as in the code.

**The recurring questionable rows** are faithful to the reference code but questionable as science:
- The within-task bootstrap δ under-covers at k = 2 (spec §4.4; E10). An unbiased estimate would give 0.212 instead of 0.147 for AgentQA and 0.022 instead of 0.016 for HarnessWorld.
- B_t lists components that have no accepted machinery to prune (spec §8.3). This happens in 6 AgentQA rounds and 9 HarnessWorld rounds.
- The live δ comes from R = 3 evaluations at k = 1.
- In the pre-fix live run only: the proposer was shown δ, S*, S_t and T, and L_t omitted the BASELINE row. Both are fixed; see the stage note above.

## 2. Per-round step audit (compact; evidence = trace `seq`)

### 2.1 `offline_agentqa` (regenerated on the fixed code)

δ = 0.147 (seq 3, bootstrap, k = 2). b_t = 4, 4, 4, 4, 3, 2, 2, 2. No proposer prompt holds δ, S*, S_t or T. The precheck holds evolve task ids only (no answer key), so every leaky draft reached the scripted LLM critic, which rejected it (catch rate 1.0; 11 LLM rejections, 0 precheck rejections).

| round | what happened | verdict |
|---|---|---|
| r0 | r0A: Python tool + verbose persona. S = 0.950, dS = +0.575 > δ, dC = +0.68 ≤ 23.1: admissible, kept. r0B (persona alone): S = 0.575, admissible, LOST. | correct. Questionable in effect: the costly persona hitchhiked on the tool's gain (spec §8.4). k = 10 truth: r0A 0.980 / 0.932 / 0.946 (evolve / holdout / OOD) vs H0 0.350 / 0.389 / 0.442. |
| r1 | Both first drafts carried the answer table and the LLM review rejected them. The repairs shipped r1A (step-by-step + null reword + verify: dS 0, shaped −0.68, rejected) and r1B (checker sub-call + reword: S = 0.625 < floor 0.803, rejected). | correct |
| r2 | r2A and r2B both = the Python skill card + answer format (r2B after an LLM rejection of a leaky first draft and a repair). S = 0.975, dS = +0.025 inside the band, dC = +0.05, ν = 1 (skill never accepted): shaped = +2.30, both admissible. The first on the tie, r2A, is kept. | correct by rule (argmax, first on ties). **Questionable in effect:** the novelty bonus admitted a neutral structural addition (spec §8.5–8.6). k = 10: r2A 0.955 / 0.932 / 0.938 vs r0A 0.980 / 0.932 / 0.946, at +4.7% tokens. Winner's curse: the measured 0.975 is really 0.955. |
| r3–r5 | Every draft was the leak (the mock's idea pool is nearly exhausted). The LLM review rejected them, and the repair had no clean idea left and shipped nothing, so all six ended as `critic_reject`. σ = 1 from r4 (S_4 − S_1 = 0.025 ≤ δ); U_t = {tool, memory}; B holds the reserved slot. | correct (mock-limited, as in stage B) |
| r6 | r6A (verify): S = 0.925, dS = −0.05, shaped −5.36, rejected. r6B: leak, `critic_reject`. | correct |
| r7 | r7A (null reword): dS −0.025, shaped −2.57, rejected. r7B: the done() contract bounced its reserved slot, then 4 zero-change done() calls, then the leak, `critic_reject`. | correct |

Net: two acceptances. The final harness (Python tool + persona + skill card + answer format) is a large real gain: transfer k = 2 +0.575 evolve, +0.579 holdout, +0.604 OOD, all CIs above 0. It costs 1.76× H_0's tokens (1,293 vs 736). The leak was blocked in every one of its 11 drafts before evaluation.

### 2.2 `offline_harnessworld` (regenerated on the fixed code)

δ = 0.0159 (seq 3). b_t = 4, 4, 4, 4, 3, 3, 3, 2, 2, 2. LLM analyst path (mock digesters + aggregator). The precheck (evolve task ids + the `hw-e-\d{3}` pattern) rejected all 5 literal-leak drafts, as it did with the answer key: 600/600 catalog leaks over 50 worlds are keyed by task ids. The LLM review rejected 2 obfuscated drafts (r0B's repair, r6A); all 7 were repaired, and none was evaluated.

| round | what happened | verdict |
|---|---|---|
| r0 | r0A (generic context pin): dS = +0.013 inside the band, shaped +0.65, kept. r0B (harmful + costly): below the floor. | correct; truth +0.012 evolve / +0.001 OOD |
| r1–r2 | 4 evaluated candidates, all truly worse (true dE[S] evolve −0.005 to −0.067). 3 were rejected by the floor and 1 by the shaped rule. Literal leaks were blocked by the precheck and repaired (r0B, r1A, r1B). | correct |
| r3 | σ = 1, U_t = {config, client_tool}, B reserved. r3A (structured search tool + a costly prompt + the narrow `nar_04`): dS = +0.041, dC = +0.53 ≤ 1.75, kept. r3B (the reserved slot: code-runner tool + null config): dS = +0.025, admissible, LOST. | correct by rule. Truth: r3A +0.030 evolve / +0.014 OOD; r3B +0.019 / +0.006. **Questionable in effect:** r3A carries the practice-only `nar_04`. |
| r4 | Both below the floor (truth −0.010, −0.005). | correct |
| r5 | r5B (config + procedure skill + control flow): dS = +0.041, kept (truth +0.051 / +0.042 OOD). r5A below the floor (truth −0.051). | correct |
| r6 | r6A (planner sub-call + null config): dS = +0.029, kept (truth +0.040 / +0.033 OOD). r6B: shaped −6.28 (truth +0.0003: null at +47% tokens). | correct |
| r7 | r7A (critic sub-call + decaying prompt): dS = +0.030, dC = +0.83 ≤ 1.29, kept (truth +0.016 / +0.014 OOD). r7B below the floor. | correct |
| r8 | r8A (verification skill card, after a precheck repair): dS = +0.017, kept (truth +0.022). r8B (prune of `str_10`, the structured search tool): dS = −0.004 inside the band, dC = −0.13, shaped +1.57, admissible, LOST. Its truth is −0.016. | correct |
| r9 | r9B (code-runner tool + harmful semantic memory `har_05`): dS = +0.011 inside the band, ν = 1, shaped +1.20, kept. Truth: +0.0025 evolve, **−0.015 OOD**. r9A: shaped −1.82. | correct by rule. **Questionable in effect:** the novelty bonus admitted a bundle whose harmful hitchhiker costs OOD (spec §8.4–8.6). |

Against the analytic ground truth:
- All 11 rejected candidates were truly worse or null on evolve.
- All 7 keeps were truly better on evolve. One keep (r9B) lowered OOD; r3A carries a narrow mechanism.
- Final E[S], H0 → final: evolve 0.464 → 0.638, holdout 0.528 → 0.691, OOD 0.403 → 0.503. E[C] ×4.21 (2,145 → 9,023 tokens).

### 2.3 `live_agentqa` (Haiku in every role; **pre-fix code**)

This run was recorded before the claims-audit fixes. Its proposer prompts show δ, S*, S_t, T and the numeric β/w (claims N2), its L_t omits the BASELINE row (N5), and its precheck held the evolve answers (N3). It can no longer be replayed on the current code: every proposer call is a cache miss, while baseline, calibration and the round-0 analysis replay identically (`live_agentqa/replay_current_code.json`). **Live check of the fixed precheck:** the r0A draft below, which copied the answers of `evolve-numeric-001` (781), `-005` (99) and `-008` (570186), passes the faithful precheck, as it would pass the code's. It was sent to the Haiku critic (fresh cache, 1 call, $0.025). The critic **rejected** it under REJECT rule 1 and named all three answers, The pre-fix answer-key precheck had caught only one of them. The record is in the claims audit's Fix log.

δ = 0.272 from 3 base evaluations at k = 1 (seq 3). Resumed once; segment 1 is the superseded partial round 0.

| round | what happened | verdict |
|---|---|---|
| r0 analysis | F_0 reports two modes: the answer is buried in explanatory text, and arithmetic errors. Both are grounded in the grader feedback. | correct |
| r0A draft | The worked examples copied `evolve-numeric-001` ("19^763 mod 823 → 781"), `-005` (→ 99) and `-008` (→ 570186). The precheck caught only `570186` (seq 7): 3-digit and 2-digit answers are below `LeakageCritic`'s minimum term length. The repair removed all three, and the Haiku critic accepted. | outcome correct. **Questionable:** partial detection. The edits are also declared as E1 "format" + E2 "parser" even though they depend on each other ("Dependent parts are ONE edit"), and the persona wording is undeclared. |
| r0 gate | r0A: dS = +0.167 < δ, shaped = 16.67 − 12.72 = +3.95, admissible. r0B: dS = +0.25 < δ, dC = +2.00, shaped −4.97, rejected. r0A kept (seq 23–25). | correct by rule. **Questionable:** a 1-task difference at k = 1 decided by noisy per-trial tokens. The loop measured dC = +0.85 while transfer measured ×3.6, and above dC ≈ 1.11 r0A would also have been rejected. Whether r0B was better is **unverifiable** at k = 1. |
| r1 analysis | "Tool invocation fails silently." This is **grounded**: the policy writes fake `<function_calls>` blocks that the harness never executes (checked in `evals/r0A.json`). | correct |
| r1 | r1A (prompt only: "use Python") scored 0.333 < floor 0.395. r1B (runs ```` ```python ```` blocks via `tools.python`) had shaped −9.69. Nothing kept. | correct. **Questionable:** r1B was tagged `control_flow`, not `tool`, which follows the code's `normalize`. |
| accounting | The cache sum is $2.804: $0.88 before the kill, $1.16 in the loop, $0.33 shadow monitor and $0.44 transfer. The loop's USD budget did not include the $0.88. | correct (open core issue, §6) |
| replay | A from-scratch offline-cache replay on the current code gives identical history, decisions, trajectory, δ and final incumbent, with 0 misses and $0. So the kill-and-resume changed nothing. | correct |

**Monitor never feeds back (code and tests).**
- `RRSITrace.attach_monitor` evaluates through `sealed_view(domain)` with `isolated_llm`.
- The loop's `Measurer` has `allow_sealed=False`, and nothing in `rsi/rrsi` except `audit.py` and `tracing.py` reads `trace.jsonl`, monitor rows or `audit_truth`.
- `tests/test_rrsi_validation.py` shows identical ledger, history, decisions and usage with the monitor on, off, or with a tracer that raises.
- Verdict: correct.

## 3. Paper alignment

### 3.1 Mechanism level (spec section → observed in trace)

| spec | mechanism | observed | verdict |
|---|---|---|---|
| §4.2 Eq. anneal | b_t = ceil(cosine) | Schedules 4,4,4,4,3,2,2,2 (T = 8), 4,4,4,4,3,3,3,2,2,2 (T = 10) and 4,4,3,2 (T = 4; only r0–r1 ran). b_t never reaches 1. Edits over b_t are bounced in done(). The proposer is told b_t, never δ, S* or T (regenerated runs). | faithful to code; documented discrepancy with the paper's "ends at one" |
| §4.2 L_t | one record per edit; the bundle shares ΔS | history.jsonl; the proposer sees all measured edits and the BASELINE row, as the code renders it (stepcheck) | faithful. The phantom credit (mock) and the dropped BASELINE row (claims N5) are fixed. |
| §4.2 T_t, U_t, σ_t | measured-only T_t; σ = 1[S_t − S_{t−w} ≤ δ] | AgentQA σ = 1 in r5–r7; HarnessWorld σ = 1 in r3–r4 | faithful (T_t "measured only" follows the code, not the paper docstring) |
| §4.2 reserved slot | last m_draft variants; checked in done() and on the diff | AgentQA r4–r7 (B reserved; r7B bounced in done()); HarnessWorld r3B honoured with the code-runner `client_tool` | faithful |
| §4.2 B_t | {l ∈ T_t : g_t ≤ 0}, advisory | lists include empty-machinery components; prune edits proposed (HarnessWorld r8B, admissible, LOST) | faithful (spec §8.3) |
| §6.2 critic | regex/denylist precheck, then 6-rule LLM review, then bounded repair, all before evaluation | 19 AgentQA reviews (0 precheck / 11 LLM rejections), 27 HarnessWorld (5 precheck / 2 LLM); no rejected candidate evaluated | faithful since the fix: the precheck holds the evolve task ids and the domain patterns, as the code's, and literal answers are the LLM review's (claims N3). Documented deviation 4: added lines only (the code's whole-diff grep is `precheck_scope="diff"`; the two never disagree on the 53 diffs of the three runs); strict verdict parsing. |
| §4.3 floor | S' ≥ S* − δ with S* the running max, raw float comparisons as in the code | AgentQA r1B: 0.625 < 0.803; HarnessWorld r1A: 0.4619 < 0.4622 | faithful (the 1e-9 tie tolerance is gone, claims N1) |
| §4.3 cost rule | dC ≤ β0 + β1·dS when dS > δ | AgentQA r0A, r0B; HarnessWorld r3A, r3B, r5B, r6A, r7A, r8A | faithful |
| §4.3 shaped rule | w_s·dS − w_c·dC + w_n·ν > 0 inside the band, including negative dS | AgentQA r2A kept via ν = 1; HarnessWorld r8B (dS −0.004, cheaper) admissible, r9B kept via ν; live r0A kept, r0B rejected | faithful |
| §4.3 argmax | argmax S' among admissible candidates, not the shaped score (first on ties) | HarnessWorld r3, r8; AgentQA r2 (exact tie, first wins) | faithful |
| §4.2 ν | structural components with no accepted edit | recomputed per candidate; resume fix counts rounds < t | documented deviation 11 |
| §4.4 δ | bootstrap within tasks (R = 1) or stdev·√2 (R ≥ 2) | offline bootstrap; live R = 3 | faithful; E10 under-coverage documented |
| §3 analyst | worst trials of the n_fail lowest tasks plus the best of the wins | trace selection matches; the heuristic labelling of solved tasks is **fixed** | documented deviation 3; bug fixed |
| §6.1 prompts | system, constitution, headers, reserved-slot and repair text | verbatim in `run_start` and the proposal prompts | faithful |
| §8.12 | no per-round sealed monitoring in the code | the shadow monitor is an extension, write-only (§2.3) | documented extension |

### 3.2 Behaviour level

| paper behaviour | observed | why |
|---|---|---|
| annealed budget: early bundles, late single edits | AgentQA bundle sizes A/B (as shipped) are 2/1, 3/2 and 2/2 in r0–r2, then 1 from r3 on, apart from one 2-edit leaky draft (r7B). HarnessWorld goes from 1–4 to 1–2. | matches |
| failure memory steers the proposer | Mocks skip ideas that were rejected and redraw them only when nothing else is left (AgentQA r3–r7 keep drawing the leak once the clean ideas are used up; HarnessWorld compliance is 0.9 by design). In the live run, r1 did not redraw r0B's rejected idea. | matches mechanically; the mocks are shallow |
| stall → reserved exploration slot | Fires in both offline runs. HarnessWorld: the reserved slot produced an admissible `client_tool` (r3B; LOST to r3A by argmax). AgentQA: unsatisfiable, because the mock has no `tool` idea and its only `memory` idea is the leak. | matches; the AgentQA outcome is mock-limited |
| critic before evaluation, bounded repair | Leaks blocked: 5 literal drafts by the id precheck and 2 obfuscated drafts by the LLM review (HarnessWorld); 11 leaky AgentQA drafts, all by the LLM review, since the faithful precheck has no answer key; 1 real Haiku leak (live, pre-fix: precheck plus repair). None was evaluated. | matches. On the fixed code, the recorded Haiku leak is caught by the Haiku review alone (§2.3). |
| floor on S*, not S_t | the floor binds on S* in every rejection above (AgentQA r1B: 0.625 < 0.803 = S* − δ) | matches |
| cost rule above the band, shaped rule inside it; harness stays lighter | AgentQA final ×1.76 H_0 tokens (costly persona hitchhiked on the tool in r0 and was never pruned in this realization; B_t never listed `prompt` before r7). HarnessWorld E[C] ×4.21 over 7 keeps (paper: RRSI ×1.55 vs H_0). Live (pre-fix) ×3.6 (k = 1 noise, 2 rounds). | cost rule and shaped rule fire as specified. Unlike the stage-B realization, no within-band prune happened, so the harness is not lighter than H_0. |
| lower practice gain, better transfer than unregularized | **unverifiable here**: the validation runs have no unregularized arm (see E1 in `docs/methods/rrsi-impl.md`). Transfer gaps observed: HarnessWorld truth +0.174 evolve / +0.163 holdout / +0.100 OOD (the residual evolve-only share comes from `nar_04` and r9B's `har_05`); AgentQA (k = 10) +0.61 evolve / +0.54 holdout / +0.50 OOD. | CPU scale, one realization |
| winner's curse / ratcheting S* (spec §8.1) | AgentQA r2A measured 0.975 vs true 0.955 (k = 10); HarnessWorld r3A measured dS +0.041 vs true +0.030; live 0.667 vs transfer 0.75 | faithful to code; a real weakness |

## 4. Inconsistency register

| item | status | evidence |
|---|---|---|
| Algorithm 2 (floor, cost, shaped, argmax, S*) | faithful (raw float comparisons since the fix, claims N1) | stepcheck: 0 mismatches over the 32 evaluated candidates of the current runs (8 AgentQA, 20 HarnessWorld, 4 live) |
| b_t schedule never reaches b_min | documented-deviation (paper vs code; code followed) | spec §4.2; `run_start.b_t_schedule` |
| T_t counts measured edits only | documented-deviation | spec §4.2 |
| ν counts accepted edits in rounds < t | documented-deviation (impl §5.11) | recomputed |
| Precheck on added lines; strict verdict parse | documented-deviation (impl §5.4: the code's whole-diff grep also rejects removals of flagged content; `precheck_scope="diff"` reproduces it) | 0 disagreements between the two scopes on the 53 diffs of the three runs |
| Single-shot proposer and digesters | documented-deviation (impl §5.2–3) | – |
| Proposer shown δ, S*, S_t, T and numeric β/w (the code shows none) | **inconsistent-fixed** (claims N2; `Config.proposer_numbers` keeps the old view as an opt-in) | regenerated runs: stepcheck "proposer was NOT shown delta, S*, S_t or T" correct for every proposal; live (pre-fix) flagged questionable |
| L_t render drops the BASELINE row | **inconsistent-fixed** (claims N5) | stepcheck history rows include BASELINE |
| Precheck denylist holds the evolve answers (an oracle the code lacks) | **inconsistent-fixed** (claims N3; `Config.precheck_answers` opt-in) | AgentQA: all 11 leak drafts now stopped by the LLM review; HarnessWorld: unchanged (literal leaks carry task ids) |
| Smoke = 1 task, errors only (code: 2–4 tasks) | **inconsistent-fixed** (claims N9) | `smoke.json` lists 2 ids per candidate |
| Shadow monitor | documented-deviation (extension), verified write-only | tests + code |
| Bundle credit shared, advisory B_t with empty lists | faithful (spec §8.3–8.4) | AgentQA r2; B_t rows |
| Winner's curse / S* never re-measured | faithful (spec §8.1) | k = 10 references |
| Bootstrap δ under-covers at k = 2 | faithful (E10 documented) | plug-in vs unbiased |
| Mock phantom credit (declared edit overwritten) | **inconsistent-fixed** | superseded r0A; regression test |
| Heuristic analyst ranks solved tasks as a failure mode | **inconsistent-fixed** | superseded r0–r7, new r3–r7 before the fix; regression test |
| audit.py "dropped as no_proposal" message | **inconsistent-fixed** | now reports the gate failure |
| Component tag for a harness that calls `tools.python` (live r1B → control_flow) | faithful (code `normalize`), questionable attribution | live seq 36 |
| Live dependent edits declared as separate edits; undeclared persona text accepted by the critic | documented (LLM proposer/critic behaviour on the pre-fix live run; judging bundling is the LLM critic's rule 4, as in the paper, so no code change applies) | live seq 8–9. In the fixed-code critic check, Haiku listed an undeclared markdown-stripping change as a risk note (§2.3). |
| Precheck misses literal answers under 4 characters | **inconsistent-fixed** (by making the precheck faithful: like the code's, it holds no answers at all, so no answer length can slip past it; answers are the LLM review's REJECT rule 1) | Haiku, fixed code, fresh cache: the recorded r0A leak (781, 99, 570186) rejected with all three answers named ($0.025) |
| AgentQA generator puts the same question in evolve and holdout | **inconsistent-fixed** (in core: `make_suite(dedupe=True)` by default) | verified: no question shared by evolve/holdout/ood in 20 suite seeds; the regenerated `offline_agentqa` stepcheck row "sealed splits are disjoint" is correct |
| Resumed run's USD budget ignores pre-kill spend | **inconsistent-fixed** (in `rsi.rrsi.spend`: `spend.json` + `CachedLLM.saved`) | `test_resumed_run_budget_includes_the_killed_process_spend`: killed and resumed runs now stop where the uninterrupted run does |
| TRACE.md shows superseded partial round 0 | documented (rendering is `rsi.trace`, a core module; core change request filed). The segments are marked: each `run_start` carries `resumed`, and the step checkers read only the last segment | live segment 1 |
| Missing trial = 0 with full denominator; eval_invalid retry; smoke_fail; domain guards; driver infra retries | unverifiable in these runs (never exercised). Unit tests cover them, now including a missing trial's task weight (claims N4) and the retry refilling only the missing trials (claims N7: already true, pinned by a test) | 0 missing trials |
| Paper-level numbers (Terminal-Bench, Harvey, EngDesign) | unverifiable (paper blocked; CPU scale) | spec §0 |

## 5. Fixes and re-runs

### 5.1 Stage B

1. **`rsi/rrsi/mocks.py`: phantom credit.** The AgentQA mock no longer bundles ideas that set the same `harness.py` field (self-consistency vs the Python tool). Test `test_agentqa_mock_never_declares_an_edit_a_later_edit_overwrote` fails on the unfixed mock.
2. **`rsi/rrsi/analyst.py`: solved tasks as a failure mode.** A trace that reached reward 1.0 is success evidence. In heuristic mode it gets the success lens. In LLM mode it is digested with the success lens after the genuine wins, which leaves the live prompts byte-identical. Test `test_heuristic_analyst_never_reports_solved_tasks_as_a_failure_mode` fails without the fix.
3. **`rsi/rrsi/audit.py`: message fix.** It now reports the recorded gate failure.

Stage-B re-runs: `offline_agentqa` from scratch (the stage-A run moved to `offline_agentqa_superseded/`); `offline_harnessworld` re-run, with an identical history; `live_agentqa` replayed from its cache for $0, identical.

### 5.2 Claims-audit fix stage

The claim-by-claim audit (`docs/claims/rrsi.md` §3, "Fix log" §5) compared the code with `google-research/rrsi@be50316` and found mismatches that neither review nor stage B had flagged. What changed, in brief:
- Algorithm 2 compares raw floats (N1).
- The proposer never sees δ, S*, S_t or T, and the rules are stated symbolically (N2).
- The precheck holds task ids and patterns, not answers (N3).
- A missing trial carries its task's weight (N4).
- L_t keeps the BASELINE row (N5).
- A declared `tool` is bounced, not aliased (N6).
- The smoke runs 2 (eng: 4) tasks (N9).
- A resumed run's USD budget counts the killed process's spend (this register).

Every old behaviour that is a deliberate extension stays reachable behind an explicit `Config` option: `tie_eps`, `proposer_numbers`, `precheck_answers`, `precheck_scope`, `component_aliases`. `tests/test_rrsi_fidelity.py` holds one regression test per fix; each fails on the pre-fix code, except N7's, which pins behaviour that was already correct.

**Re-runs on the fixed code:**
- `offline_agentqa` and `offline_harnessworld` were regenerated from scratch into fresh directories (§2.1–2.2). The stage-B versions are in git history at `02b830e`.
- `offline_agentqa/reference_k10.json` was recomputed for the new incumbents.
- The step check was re-run on all three runs, with updated expectations: raw floats; no numbers in the prompt; the BASELINE row. On the pre-fix live run the new rows come out questionable, not wrong.
- `live_agentqa` was not re-run (cost); see §2.3 for the $0.025 live critic check. `live_agentqa/replay_current_code.json` records that a $0 replay on the fixed code now misses every proposer call, by design.

## 6. Remaining open issues

- **Winner's curse.** The paper's selector never re-measures the incumbent. With k ≤ 2 this inflates S*: AgentQA r2A 0.975 vs a true 0.955 (stage B: 0.75 vs 0.61, and 1.00 vs 0.945). Faithful to the paper's code; a re-measure option would be an extension.
- **Transfer-blind keeps.** Correct-by-rule keeps can transfer worse. In HarnessWorld, r9B (kept inside the band on ν) lowers OOD, and r3A carries the practice-only `nar_04`. In AgentQA, r2A is a neutral structural addition kept on ν. Nothing in RRSI's evolve-only selection can see this.
- **Live evidence on the fixed code is small.** One `live_smoke` run with Haiku in the search roles and SimModel as the task model, plus the critic check above. The audited Haiku-everywhere run (`live_agentqa`: 2 rounds, k = 1, 12 tasks, δ = 0.27) predates the fixes.
- **Core requests:** group or collapse superseded trace segments in `TRACE.md` (`rsi.trace`).
