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

## 1. Summary of step verdicts

"Automatic" rows come from `stepcheck.json`. "Manual" rows are the judgement calls in §2 that the checker cannot make, such as whether a correct-by-rule decision was good.

| run | steps audited | correct | questionable | wrong | unverifiable |
|---|---|---|---|---|---|
| `offline_agentqa` (re-run after fixes), automatic | 241 | 232 | 8 | 1 (domain: evolve/holdout duplicate question) | 0 |
| `offline_harnessworld`, automatic | 321 | 311 | 10 | 0 | 0 |
| `live_agentqa`, automatic | 64 | 63 | 1 | 0 | 0 |
| manual checks, all runs (§2) | 14 | 6 | 7 | 0 | 1 |
| **total, current runs** | **640** | **612** | **26** | **1** | **1** |
| `offline_agentqa_superseded` (pre-fix), automatic | 241 | 222 | 9 | **10** (9 RRSI steps, fixed; 1 domain) | 0 |

**Checks that passed in every run:**
- Ŝ and Ĉ recomputed from raw trials match the trace and `evals/*.json` exactly.
- Every Algorithm 2 verdict matches: floor on S*, the cost rule above the band, the shaped rule inside it, ν, and dS and dC measured against S_t.
- Every argmax, S* update, b_t, σ_t, T_t, U_t, reserved variant, B_t (with g_t), and δ matches. δ agrees with the closed-form plug-in within 2%.
- Every history record's S, dS and outcome matches.
- Every critic-rejected candidate was never evaluated, and every evaluated candidate had a final critic accept.
- No holdout or OOD task id or question reached a proposer, analyst or critic input.
- No evolve answer or task id is in any evaluated diff.
- Each proposer saw the b_t, U_t, reserved flag, B_t, S*, δ and the full evidence-aware L_t of its round.

**The recurring questionable rows** are faithful to the reference code but questionable as science:
- The within-task bootstrap δ under-covers at k = 2 (spec §4.4; E10). An unbiased estimate would give 0.212 instead of 0.147 for AgentQA and 0.022 instead of 0.016 for HarnessWorld.
- B_t lists components that have no accepted machinery to prune (spec §8.3). This happens in 8 AgentQA rounds and 9 HarnessWorld rounds.
- The live δ comes from R = 3 evaluations at k = 1.

## 2. Per-round step audit (compact; evidence = trace `seq`)

### 2.1 `offline_agentqa` (re-run; the pre-fix run is discussed in §5)

δ = 0.147 (seq 3). b_t = 4, 4, 4, 4, 3, 2, 2, 2.

| round | what happened | verdict |
|---|---|---|
| r0 | r0A: python_skill card. S = 0 < floor 0.228, rejected (seq 18). r0B: null reword + persona + self-consistency (sc3) + step-by-step. dS = +0.175 > δ and dC = +2.07 ≤ 7.10, so admissible and kept (seq 19–20). | correct. Questionable in effect: the costly persona and the null edit hitchhiked on the sc3 gain; a later round pruned the persona. k = 10 truth: +0.215. |
| r1 | Both drafts first carried the leaky answer table. The precheck blocked them (seq 26, 32) and the repair removed it. r1A (answer format): dS = +0.200, kept. r1B (checker): shaped +5.22, admissible, LOST. | correct by rule. Winner's curse: the measured 0.75 is really about 0.61 at k = 10, and every later round is judged against it. |
| r2 | Both variants bundled the Python tool with the checker. S = 0.575 < floor 0.603, and S = 0.625 with shaped −10.8. Rejected (seq 60–62). | correct: the checker overrides the tool answer. Questionable in effect: shared bundle credit makes the Python tool "rejected", and the mock never retries it alone (spec §8.4). |
| r3 | Both variants drafted the leak and the precheck rejected them (seq 67, 71). The repair shipped nothing, so both became `critic_reject` and were not evaluated. | correct |
| r4 | Two "verify" edits. dS = 0 with shaped −0.30, and dS = −0.075 with shaped −7.8. Rejected. | correct |
| r5 | σ = 1 (S_5 − S_2 = 0 ≤ δ), U = {tool, memory}, reserved slot on B (seq 94). r5A pruned sc3: 0.475 < floor. r5B could only fill the slot with the leak: `critic_reject`. | correct (the mock cannot produce a `tool` idea) |
| r6 | r6A pruned the persona. dS = −0.10 is inside the band and dC = −0.73, so shaped = +0.92: admissible and kept (seq 126–128). S_t = 0.65 while S* stays 0.75. | **correct and good.** At k = 10, r6A scores 0.605 / 0.655 / 0.617 against r1A's 0.610 / 0.635 / 0.621 (evolve / holdout / OOD), at 27% of the tokens. |
| r7 | r7A re-added the persona: dS = 0, dC = +2.68, shaped −40.2, rejected. r7B: leak, `critic_reject`. | correct |

### 2.2 `offline_harnessworld`

δ = 0.0159 (seq 3). b_t = 4, 4, 4, 4, 3, 3, 3, 2, 2, 2.

| round | what happened | verdict |
|---|---|---|
| r0–r3 | 7 evaluated candidates, all truly worse (true dE[S] on evolve between −0.006 and −0.036). 4 were rejected by the floor and 3 by the shaped rule (seq 18–74). The precheck blocked literal `ans-…` tables before evaluation (seq 7, 29, 43, 65). σ = 1 at r3 and r4 with B reserved. r3B spent 5 proposer calls: after the repair removed the leak, the done() contract bounced it 4 times. | correct. The 5 wasted calls are mock-limited; the loop is correct. |
| r4 | r4A: dS = +0.031, dC = +0.53 ≤ 1.34. r4B (the reserved structured tool): dS = +0.029, dC = +0.025. Both admissible, and argmax S' keeps r4A by 0.0016 (seq 90–92). | correct by rule (spec §4.3: argmax S', not the shaped score). **Questionable in effect:** truth is r4A +0.027 evolve / +0.016 OOD at ×1.55 tokens, and r4B +0.021 / **+0.041 OOD** at ×1.06. |
| r5 | Floor at S* = 0.4963 rejected r5A. r5B shaped −2.70. | correct |
| r6 | r6B (narrow mechanism `nar_02`): dS = +0.027, dC = +0.035, kept (seq 126–127). | correct by rule. **Questionable in effect:** true OOD −0.010. Nothing that reads only the evolve set can see this. |
| r7–r9 | All rejected, 5 by the floor and 3 by the shaped rule. r8B is a null edit (true +0.0002) at +1.8% tokens; shaped −0.58. r9A: the LLM critic caught the obfuscated leak `obf_00` (seq 167); the repaired prune of `dec_00` fell below the floor (truly −0.020). | correct |

Against the analytic ground truth, all 17 rejected candidates were truly worse or null on evolve, and both keeps were truly better on evolve.

### 2.3 `live_agentqa` (Haiku in every role)

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
| §4.2 Eq. anneal | b_t = ceil(cosine) | Schedules 4,4,4,4,3,2,2,2 (T = 8), 4,4,4,4,3,3,3,2,2,2 (T = 10) and 4,4,3,2 (T = 4; only r0–r1 ran). b_t never reaches 1. Edits over b_t are bounced in done(). | faithful to code; documented discrepancy with the paper's "ends at one" |
| §4.2 L_t | one record per edit; the bundle shares ΔS | history.jsonl; the proposer sees all measured edits (stepcheck) | faithful. The phantom credit (mock) is fixed. |
| §4.2 T_t, U_t, σ_t | measured-only T_t; σ = 1[S_t − S_{t−w} ≤ δ] | AgentQA σ = 1 in r5–r7; HarnessWorld σ = 1 in r3–r4 | faithful (T_t "measured only" follows the code, not the paper docstring) |
| §4.2 reserved slot | last m_draft variants; checked in done() and on the diff | AgentQA seq 94, 111, 131; HarnessWorld r3B bounced, r4B honoured with `client_tool` | faithful |
| §4.2 B_t | {l ∈ T_t : g_t ≤ 0}, advisory | lists include empty-machinery components; prune edits proposed (AgentQA r5A, r6A, r7A superseded; HarnessWorld r9A) | faithful (spec §8.3) |
| §6.2 critic | regex/denylist precheck, then 6-rule LLM review, then bounded repair, all before evaluation | 21 / 27 / 5 reviews; no rejected candidate evaluated | faithful, with documented deviation 4 (added lines only; strict verdict parsing). Limitation: literal answers under 4 characters are not denylisted. |
| §4.3 floor | S' ≥ S* − δ with S* the running max | AgentQA r7: S_t = 0.65, floor from S* = 0.75 → 0.603 | faithful |
| §4.3 cost rule | dC ≤ β0 + β1·dS when dS > δ | AgentQA r0B, r1A; HarnessWorld r4A, r4B, r6B | faithful |
| §4.3 shaped rule | w_s·dS − w_c·dC + w_n·ν > 0 inside the band, including negative dS | AgentQA r6A kept at dS = −0.10; live r0A kept, r0B rejected | faithful |
| §4.3 argmax | argmax S' among admissible candidates, not the shaped score | HarnessWorld r4 | faithful |
| §4.2 ν | structural components with no accepted edit | recomputed per candidate; resume fix counts rounds < t | documented deviation 11 |
| §4.4 δ | bootstrap within tasks (R = 1) or stdev·√2 (R ≥ 2) | offline bootstrap; live R = 3 | faithful; E10 under-coverage documented |
| §3 analyst | worst trials of the n_fail lowest tasks plus the best of the wins | trace selection matches; the heuristic labelling of solved tasks is **fixed** | documented deviation 3; bug fixed |
| §6.1 prompts | system, constitution, headers, reserved-slot and repair text | verbatim in `run_start` and the proposal prompts | faithful |
| §8.12 | no per-round sealed monitoring in the code | the shadow monitor is an extension, write-only (§2.3) | documented extension |

### 3.2 Behaviour level

| paper behaviour | observed | why |
|---|---|---|
| annealed budget: early bundles, late single edits | AgentQA bundle sizes A/B are 1/4, 1/1 and 3/3 in r0–r2, then 1 from r3 on (one 2-edit bundle in r6B). HarnessWorld goes from 1–4 to 1–2. | matches |
| failure memory steers the proposer | Mocks skip ideas that were rejected, and re-draw them only when nothing else is left (AgentQA pre-fix r4/r5; HarnessWorld compliance 0.9 by design). Live r1 did not redraw r0B's rejected idea. | matches mechanically; the mocks are shallow |
| stall → reserved exploration slot | fires in all offline runs. HarnessWorld: the reserved `client_tool` became admissible (r4B). AgentQA: unsatisfiable, because the mock has no `tool` idea and its only `memory` idea is the leak. | matches; the AgentQA outcome is mock-limited |
| critic before evaluation, bounded repair | leaks blocked: 7 literal + 1 obfuscated (HarnessWorld); 10 leaky AgentQA drafts, of which 5 were repaired and 5 dropped; 1 real Haiku leak (live). None evaluated. | matches. The live precheck missed short answers and the repair saved it. |
| floor on S*, not S_t | AgentQA r7 | matches |
| cost rule above the band, shaped rule inside it; harness stays lighter | AgentQA final is **lighter than H_0** (626 vs 736 tokens) after the within-band persona prune; HarnessWorld E[C] ×1.61 (paper: RRSI ×1.55 vs H_0); live ×3.6 (k = 1 noise, 2 rounds) | matches the paper's magnitude on HarnessWorld; the AgentQA prune is the "pruning removes growth that stopped paying" dynamic |
| lower practice gain, better transfer than unregularized | **unverifiable here**: the validation runs have no unregularized arm (see E1 in `docs/methods/rrsi-impl.md`). Transfer gaps observed: HarnessWorld truth +0.055 evolve vs +0.007 OOD, the residual evolve-only overfit being r6B; AgentQA (k = 10) +0.26 evolve / +0.27 holdout / +0.18 OOD. | CPU scale, one realization |
| winner's curse / ratcheting S* (spec §8.1) | AgentQA r1A 0.75 vs true 0.61; superseded r0A 1.00 vs 0.945; live 0.667 vs transfer 0.75 | faithful to code; a real weakness |

## 4. Inconsistency register

| item | status | evidence |
|---|---|---|
| Algorithm 2 (floor, cost, shaped, argmax, S*) | faithful | stepcheck: 0 mismatches over the 34 evaluated candidates of the current runs (46 including the superseded run) |
| b_t schedule never reaches b_min | documented-deviation (paper vs code; code followed) | spec §4.2; `run_start.b_t_schedule` |
| T_t counts measured edits only | documented-deviation | spec §4.2 |
| ν counts accepted edits in rounds < t | documented-deviation (impl §5.11) | recomputed |
| Precheck on added lines; strict verdict parse | documented-deviation (impl §5.4) | – |
| Single-shot proposer and digesters | documented-deviation (impl §5.2–3) | – |
| Shadow monitor | documented-deviation (extension), verified write-only | tests + code |
| Bundle credit shared, advisory B_t with empty lists | faithful (spec §8.3–8.4) | AgentQA r2; B_t rows |
| Winner's curse / S* never re-measured | faithful (spec §8.1) | k = 10 references |
| Bootstrap δ under-covers at k = 2 | faithful (E10 documented) | plug-in vs unbiased |
| Mock phantom credit (declared edit overwritten) | **inconsistent-fixed** | superseded r0A; regression test |
| Heuristic analyst ranks solved tasks as a failure mode | **inconsistent-fixed** | superseded r0–r7, new r3–r7 before the fix; regression test |
| audit.py "dropped as no_proposal" message | **inconsistent-fixed** | now reports the gate failure |
| Component tag for a harness that calls `tools.python` (live r1B → control_flow) | faithful (code `normalize`), questionable attribution | live seq 36 |
| Live dependent edits declared as separate edits; undeclared persona text accepted by the critic | inconsistent-open (LLM proposer/critic behaviour; no code change possible) | live seq 8–9 |
| Precheck misses literal answers under 4 characters | inconsistent-open (core `LeakageCritic` / AgentQA denylist) | live seq 6–7 |
| AgentQA generator puts the same question in evolve and holdout | inconsistent-open (domain, not RRSI) | `evolve-numeric-017` = `holdout-numeric-012` |
| Resumed run's USD budget ignores pre-kill spend | inconsistent-open (core) | $0.88 not metered |
| TRACE.md shows superseded partial round 0 | inconsistent-open (core trace) | live segment 1 |
| Missing trial = 0 with full denominator; eval_invalid retry; smoke_fail; domain guards; driver infra retries | unverifiable (never exercised in these runs; covered by unit tests) | 0 missing trials |
| Paper-level numbers (Terminal-Bench, Harvey, EngDesign) | unverifiable (paper blocked; CPU scale) | spec §0 |

## 5. Fixes and re-runs

1. **`rsi/rrsi/mocks.py`: phantom credit.** The AgentQA mock no longer bundles ideas that set the same `harness.py` field (self-consistency vs the Python tool). Test `test_agentqa_mock_never_declares_an_edit_a_later_edit_overwrote` fails on the unfixed mock.
2. **`rsi/rrsi/analyst.py`: solved tasks as a failure mode.** A trace that reached reward 1.0 is success evidence. In heuristic mode it gets the success lens. In LLM mode it is digested with the success lens after the genuine wins, which leaves the live prompts byte-identical. Test `test_heuristic_analyst_never_reports_solved_tasks_as_a_failure_mode` fails without the fix.
3. **`rsi/rrsi/audit.py`: message fix.** It now reports the recorded gate failure.

**Re-runs:**
- `offline_agentqa`: from scratch, into a fresh directory. The old run was moved to `offline_agentqa_superseded/`.
- `offline_harnessworld`: re-run into scratch on the fixed code. Identical history, so it was kept.
- `live_agentqa`: $0 cache replay on the fixed code. Identical, so it was kept (`live_agentqa/replay_current_code.json`).
- Tests: all 62 rrsi tests pass, including the 2 new regressions.

**New tools:** `experiments/rrsi/validate_rrsi_stepcheck.py` (the independent checker) and `experiments/rrsi/validate_rrsi_replay.py` ($0 live replay).

**Not re-run:** the AgentQA parts of E1b, E12 and E13 in `results/rrsi/`, which are outside this stage's scope. Their AgentQA numbers come from the pre-fix mock and analyst.

## 6. Remaining open issues

- **Winner's curse.** The paper's selector never re-measures the incumbent. With k ≤ 2 this repeatedly inflates S*: AgentQA 0.75 vs 0.61, and 1.00 vs 0.945. That blocks later real gains, as in the superseded run, where 10 candidates were judged against an unreachable 1.00. Faithful to the paper's code; a re-measure option would be an extension.
- **Transfer-blind keeps.** Correct-by-rule keeps can transfer worse: HarnessWorld r4A over r4B, and the narrow r6B. Nothing in RRSI's evolve-only selection can see this.
- **Live evidence is thin.** 2 rounds, k = 1, 12 tasks, δ = 0.27. Most decisions fall inside the band, where the cost term dominates and noisy token counts decide.
- **Core and domain requests:**
  - meter pre-kill spend into a resumed Budget;
  - group trace segments;
  - a per-domain minimum term length (or numeric-answer terms) for the AgentQA denylist;
  - de-duplicate questions across AgentQA splits.
