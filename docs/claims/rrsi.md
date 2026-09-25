# RRSI: claim-by-claim audit

This page checks our implementation (`rsi/rrsi/`, `rsi/domains/harnessworld/`) against **every** claim attributed to "RRSI: Regularized Recursive Self-Improvement of Agent Harnesses" (Xia et al., arXiv 2609.24972) in our spec and in the user's overview. It covers mechanisms, table numbers, qualitative findings and caveats. It is an audit only: no code was changed.

**Sources used.**
- Our verified spec, `docs/methods/rrsi.md` (§§2–8), and the user's overview, `scratchpad/doc.txt` (the RRSI section plus the side-by-side and "lessons" sections).
- The reference code, `google-research/rrsi@be50316`, in `scratchpad/src/google-research__rrsi`.
- Secondary paper sources, in `scratchpad/papers/rrsi_secondary/`. **The paper PDF could not be retrieved**, so every "paper" claim below comes to us through the code README, the overview or a secondary read-through (the spec tags each one).
- Our evidence:
  - `docs/methods/rrsi-impl.md`;
  - `results/rrsi/*.json`, experiments E0–E13;
  - `validation/rrsi/{AUDIT,RUNS}.md` and the run directories.

**New checks run for this audit.** All were offline, took under 4 min of CPU in total, and made no LLM calls. The scripts are in `scratchpad/claims_rrsi/` (outside the repo).
- `diff_test.py` (12 s) is a randomized differential test of our pure functions against the reference code, which it imports directly. It covers:
  - Algorithm 2 (`select_round`), 6,000 random rounds over the default, coding, workspace and eng presets;
  - `edit_budget`, every (T ≤ 59, b_min ≤ 3, b_max ≤ 6, t) combination;
  - `stall_flag`, 3,000 cases;
  - the `History` summaries (T_t, g_t, B_t, accepted counts, render), 300 random ledgers;
  - `normalize`, `classify_diff` and `novelty`, 3,000 synthetic diffs;
  - `aggregate`, 500 weighted cases;
  - `calibrate`: repeated evaluations exactly, and the bootstrap within Monte-Carlo error.
- `boot_conv.py` repeats the bootstrap comparison at 20,000 resamples.
- `policy_swap.py` (3 min) is a HarnessWorld analogue of paper Tables 3–4, run for 50 seeds × {full, unregularized} × {STRONG, WEAK} search policy. It evaluates each final harness under a still weaker policy (VWEAK, strength −2) that was never used in any search. Its STRONG rows reproduce E1's stored numbers exactly, a determinism check.
- A re-analysis of the stored E1 and E2 rows (`results/rrsi/e1_overfitting.json`, `e2_ablations.json`):
  - per-seed held-out regressions;
  - harness size, which gives a steps proxy, since HarnessWorld steps = 1 + active mechanisms (`rsi/domains/harnessworld/domain.py:114`);
  - token ratios.
- `tests/test_rrsi_*.py`: 62 passed in 14 s.

## 1. Summary

| verdict | mechanism | quantitative | qualitative | caveat | **total** |
|---|---|---|---|---|---|
| REPRODUCED | 25 | 0 | 14 | 11 | **50** |
| PARTIAL | 11 | 0 | 11 | 1 | **23** |
| NOT REPRODUCED | 1 | 0 | 3 | 0 | **4** |
| NOT TESTABLE HERE | 1 | 27 | 3 | 7 | **38** |
| CONTRADICTED | 0 | 0 | 0 | 1 | **1** |
| **total** | 38 | 27 | 31 | 20 | **116** |

**Mechanisms.** Our pure mechanism functions agree with the reference code. The differential test found:
- 0 mismatches in `edit_budget`, `stall_flag`, T_t, g_t, B_t, ν, `aggregate` and the repeated-evaluation δ;
- a bootstrap δ equal to the reference's within Monte-Carlo error (≤ 0.6% relative at 20,000 resamples).

The mismatches it did find are all small, and all are listed in §3:
- Algorithm 2 decided differently in **18 of 6,000** random rounds (15 of them with a different winner). **All 18 are exact floating-point ties** at dS = δ or S' = S* − δ. Our gates use `TIE_EPS = 1e-9`; the reference compares raw floats.
- `render` omits the BASELINE row.
- A declared `tool` tag is aliased to `client_tool`.

The PARTIAL mechanisms are plumbing substitutions that are documented, plus a few newly found gaps (§3):
- a single-shot proposer instead of the 40-turn JSON agent;
- single-shot digesters, and a heuristic analyst in every E-experiment;
- an artifact store instead of git;
- a generic smoke check;
- a leakage precheck that holds the evolve answers.

**Quantitative.** Every table number in the paper (Terminal-Bench 2.1, SWE-bench Verified, Harvey LAB, JobBench, GDPval, APEX-Agents, EngDesign, Frontier-Eng, the Gemini rows, 2.42M tokens and 26.3 steps) is **NOT TESTABLE HERE**. Each one needs frontier LLM policies (Claude Opus 4.8, Gemini 3.5 Flash and 3.1 Flash Lite), the real benchmarks with their graders, and many thousands of agent trials. Our CPU runs test only **directional analogues**, and those are scored under the qualitative claims. We make no claim about the absolute numbers.

**Qualitative.** The core story reproduces in the synthetic HarnessWorld:
- unregularized evolution overfits (highest practised score, lowest unseen score, 27× the tokens of full RRSI);
- RRSI practises less, transfers better and is cheaper;
- no seed of full RRSI regressed on any unseen split (0/50);
- the harness transfers to weaker policies never used in the search, including the new policy-swap check.

Four qualitative claims are NOT REPRODUCED:
- "removing the proposal guards raises the practised score" (measured evolve −0.9 [−2.9, +1.2] in E2);
- "rejected ideas stay rejected", which needs a real LLM (E8 was not run);
- "a weaker search policy gains more on the evolve set", an implication of Table 3/4 (policy swap: −1.7 [−3.9, +0.6]);
- "δ at z = 2 clears an unchanged harness ~97.5% of the time" with the code's estimator at k = 2 (91.6%).

Magnitudes are not calibrated: unregularized tokens are 93× H_0 here against 2.4× in the paper.

**Caveats.** Most of the paper's and the spec's caveats show up in our runs:
- results depend on the realization;
- the critic misses obfuscated leaks;
- the gap between evolve and OOD gains narrows but does not close;
- the winner's curse inflates S*;
- the budget never reaches b_min;
- B_t lists components with no machinery to prune.

One caveat is CONTRADICTED at our scale: "every evolved harness costs more than H_0". The offline AgentQA run's final harness is lighter than H_0 (626 vs 736 tokens per trial).

## 2. Claim table

Sources: `[spec §x]` is `docs/methods/rrsi.md`; `[doc]` is the user's overview; `[code]` is the reference repository, whose README maps paper to code. "Ref" line numbers refer to `scratchpad/src/google-research__rrsi/`. HW is HarnessWorld and AQ is AgentQA. Scores are in percentage points unless stated otherwise; the bracketed ranges are 95% bootstrap CIs over seeds.

### 2.1 Mechanisms

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| M1 | The harness is the only thing improved. Every component stays editable: K = {prompt, control_flow, config, output_plumbing, context_mgmt, client_tool, skill, memory, subagent}, K_str = {client_tool, skill, memory, subagent}. The rules limit *how* the search moves, not *what* it may touch. | spec §2, §1; doc | mechanism | matches: `rsi/rrsi/components.py:36-38`; `Config.editable=None` = every file (`config.py:106`). Domains may define their own K (`Taxonomy.from_domain`, `components.py:97-113`) | `tests/test_rrsi_core.py`; the HW catalog uses all 9 components; E9 coverage 1.00 | REPRODUCED |
| M2 | The policy π is frozen (weights and model id). The tool environment, grader and task set are frozen. Model, temperature and timeouts are injected externally. | spec §2; doc | mechanism | matches: the policy sits inside `Domain.execute` (`rsi/domains/harnessworld/domain.py:97-114`); the grader is `Domain.grade`, outside the artifact; the critic precheck adds grader-tampering patterns (`critic.py:29-37`, an extension) | `test_rrsi_review.py::test_stack_walking_grader_tampering_is_real_and_blocked_before_evaluation`; validation audit "no sealed id reached a proposer" | REPRODUCED |
| M3 | One round drafts m candidates from the SAME incumbent, each in isolation, evaluates each on the FULL evolve set with k trials, and moves the incumbent to the winner. | spec §3; code README | mechanism | matches the logic (`loop.py:279-319`). Git worktrees and branches are replaced by content-addressed artifacts (impl §5.1, documented) | validation stepcheck: every argmax and incumbent chain re-derived (`validation/rrsi/*/stepcheck.json`) | REPRODUCED |
| M4 | F_t = Analyze(H_t, D_evolve) on the incumbent's OWN stored evaluation: the worst trial of the n_fail lowest tasks plus the best trial of the n_success highest; three-lens digesters (failure / capability_gap / success) and an aggregator. | spec §3, §6.3–6.4 | mechanism | deviates: `build_traces` matches (`analyst.py:107-120`), but the digesters are single-shot (one call per trace with the trace inline), with no grep or read tools and `max_digests = 8` (impl §5.3). **Every E-experiment ran the heuristic analyst**: `_common.run_hw` passes no analyst LLM, so `Config.analyst="auto"` falls back to the heuristic (`loop.py:90-94`). The LLM path runs only in `validation/rrsi/offline_harnessworld` (mock) and `live_agentqa` (Haiku) | live r0/r1 F_t grounded in grader feedback (AUDIT §2.3). The stage-B fix of "solved tasks reported as a failure mode" is regression-tested | PARTIAL |
| M5 | Traces precondition: the round fails if there are fewer than 0.5·min(\|D\|, n_fail + n_success) traces. | spec §3 | mechanism | matches: `loop.py:257-260` (ref `loop.py` round step 1) | review fix §6.1.5; test | REPRODUCED |
| M6 | Estimator: Ŝ = weighted mean reward over k·\|D\| trials, Ĉ = mean policy tokens over trials with tokens > 0; **a missing trial scores 0 with the full denominator**. | spec §4.1 (Eq. estimate) | mechanism | matches: `evaluate.py:118-138`. Differential test: 0/500 mismatches against ref `aggregate`. Small deviation: missing trials are padded with weight 1.0, not the task's weight (`evaluate.py:175-179`; §3 N4) | `diff_test.py`; stepcheck "Ŝ, Ĉ recomputed from raw trials match exactly" | REPRODUCED |
| M7 | eval_invalid: if missing > invalid_missing_frac·n_expected, retry once, then drop the candidate. The baseline aborts under the same condition. | spec §4.1 | mechanism | deviates slightly: `loop.py:148-159`. The retry re-measures the whole job with the same job seeds; the ref refills only the missing trials (§3 N7) | unit tests only (AUDIT §4: "never exercised in these runs") | PARTIAL |
| M8 | Annealed L0 edit budget, Eq. (anneal): b_t = ceil(b_min + (b_max − b_min)·½(1 + cos(πt/T))), with ‖z_t‖₀ ≤ b_t enforced in done(). | spec §4.2; doc | mechanism | matches: `schedule.py:20-29` (same `round(v, 9)` guard); enforced at `propose.py:248-249` and bounced at `propose.py:358-366`. Differential test: **0 mismatches** | schedules coding 4×8/3×5/2×7, workspace 3×10/2×10, eng 4×16/3×9/2×15, overview 4,4,4,4,3,3,3,2,2,2 (= spec table); AUDIT §3.1 | REPRODUCED |
| M9 | The budget "begins at three or four … and ends at one" (paper snippet); overview: "later rounds allow only one or two". | spec §4.2 [sec:search]; doc | mechanism | code-faithful: under ceil with t ≤ T−1 the last round gets 2, never 1. Ours follows the code; `budget_rounding="floor_at_bmin_last"` is an opt-in (`schedule.py:25-26`) that yields …,2,2,1 | `diff_test.py` schedules; E7 ran the code schedule (4 → 2) | PARTIAL (paper text vs code; we follow the code) |
| M10 | Edit history L_t: one record per EDIT; a bundle shares one measurement; a candidate dropped before measurement is recorded with ΔS = None and does not enter T_t or g_t. | spec §4.2 (Eq. history) | mechanism | matches: `history.py:61-83,104-109`. Candidates are appended atomically (extension, `history.py:50-59`) | `diff_test.py`: tried 0/300 mismatches; stepcheck: every history record's S, dS and outcome matches | REPRODUCED |
| M11 | T_t (measured components), g_t(l) = max ΔS in the window n_prune, U_t = K∖T_t, B_t = {l ∈ T_t : g_t(l) ≤ 0}. | spec §4.2 (Eq. yield/prune) | mechanism | matches: `history.py:107-151`. Differential test: 0/300 mismatches on yield_g, prune_set and accepted counts | stepcheck: every T_t, U_t, B_t and g_t matches | REPRODUCED |
| M12 | Stall flag σ_t = 1[S_t − S_{t−w} ≤ δ] (0 while t < w), with w = 3. | spec §4.2 (Eq. explore); doc | mechanism | matches: `history.py:179-183`. 0/3,000 mismatches | stepcheck; σ = 1 fired in AQ r5–r7 and HW r3–r4 | REPRODUCED |
| M13 | When stalled and U_t ≠ ∅, the last m_draft variants hold a reserved slot and must ship ≥ 1 edit on an untried component, checked on the declared tags in done() **and** on the diff-normalized tags after the critic. | spec §4.2; doc | mechanism | matches: `loop.py:283`, `propose.py:260-264`, `loop.py:137-145`. Extension: the diff check also runs when the critic is ablated (impl §5.12) | HW r4B reserved `client_tool` admitted; test `test_reserved_slot_checked_on_the_diff_even_without_critic` | REPRODUCED |
| M14 | Pruning is a directive: B_t and the accepted machinery are handed to the proposer; a prune edit passes the same critic and Algorithm 2. | spec §4.2, §8.3; doc ("flagged for deletion") | mechanism | matches: `loop.py:249`, `propose.py:118-119,232-233`; `history.py:142-151` (includes empty-machinery components, as the ref does) | E6: prune edits accepted +3.7/run; AQ r6A persona prune kept | REPRODUCED |
| M15 | The proposer sees the evidence-aware history: last 40 records, at most 4 unmeasured, with the "rejected mechanism is negative evidence" header. | spec §4.2, §6.1 | mechanism | matches, except that `render` drops the BASELINE row (`history.py:163`; the ref keeps it). 23/300 random ledgers differ, **only** in that row (§3 N5) | `diff_test.py` render_excluding_baseline: 0 mismatches | REPRODUCED |
| M16 | Component tags are validated against the WHOLE candidate diff (`normalize`, `has_evidence`, `classify_diff` with domain signals first, then generic structural regexes, default prompt; text-only diffs are prompt edits). | spec §4.2 | mechanism | matches for regex signals (`components.py:128-173`): 0/3,000 `classify` mismatches. Deviations: domains may use path globs (impl §5.5); the `tool` alias gives 20/3,000 `normalize` mismatches, all from a declared "tool" (§3 N6); `trust_code_tags` for taxonomy-less domains (impl §5.15) | live r1B tagged `control_flow` although it added tool use, which is faithful to the code (RUNS §3) | REPRODUCED |
| M17 | Novelty ν = number of structural components with no accepted edit; it only tie-breaks inside the band. | spec §4.2 | mechanism | matches: `components.py:175-178`; counts come from rounds < t (resume fix, impl §5.11, documented; identical on a fresh run) | `diff_test.py` novelty 0/3,000; E13 | REPRODUCED |
| M18 | Algorithm 2: ΔS vs the current incumbent; ΔC relative (0 if a token count is missing); admissible ⇔ S' ≥ S* − δ ∧ cost rule ∧ guards; check order floor → cost → guard; H_{t+1} = argmax S' over the admissible set (first on ties), else H_t; H_t is never itself a candidate. | spec §4.3 (Eq. tokenbudget, floor); doc | mechanism | matches up to float ties: `selection.py:117-189` over `core/gates.py:142-185`. Differential test: 6,000 rounds, 18 admissibility and 15 winner mismatches, **every one an exact tie** (`TIE_EPS = 1e-9`, `core/gates.py:36`; §3 N1) | `diff_test.py`; stepcheck: 0 mismatches over 34 evaluated candidates | REPRODUCED |
| M19 | Cost rule for a gain above noise: ΔC ≤ β0 + β1·ΔS ("β0 = 0.10 is 10% more tokens for free; β1 = 40: each +1 pp buys +40%"; "a gain that also saves tokens always passes"). | spec §4.3; doc | mechanism | matches: `core/gates.py:177-180` | spot checks: dS = 0.06 > δ, dC = 2.50 passes, 2.51 fails; stepcheck on AQ r0B/r1A and HW r4A/r4B/r6B | REPRODUCED |
| M20 | Within-band shaped rule w_s·ΔS − w_c·ΔC + w_n·ν > 0. It also admits a **negative** ΔS above the floor if the candidate is cheaper or novel. | spec §4.3 | mechanism | matches: `core/gates.py:181-185` | AQ r6A kept at dS = −0.10 (shaped +0.92), which was right at k = 10 (RUNS §1); live r0B rejected at shaped −4.97 | REPRODUCED |
| M21 | Per-instance worked implications: coding (w_s = 0) admits in band iff ΔC < ν/30; eng: one extra pass offsets ΔC = 0.5; workspace: 10 criteria offset 6.7%. | spec §4.3 [inferred] | mechanism | matches (presets `config.py:37-48`) | new spot check: coding ν = 0 needs dC < 0 (−0.001 passes, 0 fails); ν = 1, dC 0.033 passes, 0.034 fails; eng dC 0.49 passes, 0.51 fails; workspace 6.6% passes, 6.8% fails | REPRODUCED |
| M22 | S* is the running max of incumbent scores; the floor uses S*, while ΔS uses the current S_t. | spec §4.3 | mechanism | matches: `loop.py:311`; readjudicate recomputes S* = max(traj[:t+1]) (`loop.py:264`) | AQ r7: floor from S* = 0.75 while S_t = 0.65 | REPRODUCED |
| M23 | Domain guards are non-compensatory (eng: valid-rate drop ≤ 0.03, no-payload rise ≤ 0.02), per step and relative to the incumbent. | spec §4.3 | mechanism | matches: `MetricGuard` (`core/gates.py:188-201`), user-supplied via `run(..., guards=...)` (`loop.py:113-115`) | E11 (below) | REPRODUCED |
| M24 | δ = z·sd(null ΔS), z = 2. With R ≥ 2 evaluations, sd = stdev·√2; with R = 1, a within-task bootstrap (2,000 resamples, seed 7) gives sd = √2·se·√(k_pooled/k_single). | spec §4.4 | mechanism | matches: `calibrate.py:69-100` (numpy RNG, not Python `random`). Repeated-evaluation δ has 0 mismatches; bootstrap δ matches within MC error (mean 2.8% at 2,000 resamples, ≤ 0.6% at 20,000). Extension: a warning for degenerate δ = 0 | `diff_test.py`, `boot_conv.py`; stepcheck "δ within 2% of closed form" | REPRODUCED |
| M25 | The paper runs used fixed δ per instance (coding 0.017 "3 passes of 178", workspace 0.004, eng 0.020) and the per-instance hyperparameters of Table 5 / rrsi.json. | spec §4.4–4.5; [sec:inkeast] | mechanism | matches: `Config.preset` (`config.py:34-51`) equals the three rrsi.json files field by field; `RRSIConfig` defaults equal `Config` (`config.py:59-86`) | arithmetic: 3/178 = 0.0169, 5/244 = 0.0205, β1 = 0.25·178 = 44.5, 0.1·244 = 24.4 | REPRODUCED |
| M26 | Leakage critic before any evaluation: a regex/denylist precheck, then an LLM review against six REJECT rules (leakage, degenerate, grader gaming, undeclared bundling, runtime memory/skill leakage, unbounded work), 3 parse attempts failing closed, and a bounded repair loop (repair_rounds = 5). | spec §6.2; doc | mechanism | deviates: the prompt is the code's verbatim (`critic.py:40-88`). But the precheck scans **added lines only** and parses verdicts strictly (impl §5.4); terms under 4 characters are skipped; and **the denylist holds the evolve answers** (`core/domain.py:166-175`), an oracle the paper's critic lacks (§3 N3) | no critic-rejected candidate was ever evaluated (stepcheck); E3 | PARTIAL |
| M27 | The critic's STATE FILES section is never filled, so runtime-memory leakage can be judged only from code. | spec §6.2, §8.8 | mechanism | matches: `_draft` never passes `state_files` (`loop.py:133-134`) | – | REPRODUCED |
| M28 | Proposer: the code's SYSTEM_TMPL and context sections; the done() contract (≤ b_t edits; required fields id, component, hypothesis, targets_mode, predicted_affected, retroactive_check; component ∈ K; reserved slot); "there is no abort action" (3 bounces); a zero-change done() is bounced; repair mode keeps the edits. | spec §3 PROPOSE, §6.1 | mechanism | deviates: the contract is enforced exactly (`propose.py:244-265,338-366`), but it is **single-shot rewrite** (or `AgentEditor`) instead of the 40-turn JSON agent with read_trace; 8 traces × 1,200 characters are inlined; "zero changes" is measured against the incumbent, not per call (impl §5.2). **New:** the proposer is also shown δ, S*, S_incumbent and the numeric β/w values (§3 N2) | live: every Haiku done() valid first try (impl §3.1); HW r3B bounced 4× | PARTIAL |
| M29 | Attribution scoreboard: per edit, predicted hits and unpredicted regressions (threshold 1/k; workspace 0.05); the last 20 rows are shown to the proposer. | spec §3 ATTRIBUTE | mechanism | matches: `attribution.py:37-52`, `loop.py:349-350`; HW threshold 0.25 (`domain.py:94`) | scoreboard rows in validation runs | REPRODUCED |
| M30 | Liveness smoke (not a selection rule): coding 2 named tasks; workspace 2 ids; eng 4 tasks with no-payload = 0 and mean combined_score > 0. | spec §4.5 | mechanism | deviates: generic `Domain.smoke` runs 1 evolve task at seed 0 and fails only on an execution error (`core/domain.py:177-184`; `loop.py:173-183`; §3 N9) | smoke rows in traces; no smoke_fail observed | PARTIAL |
| M31 | Driver: resume by settled rounds, a STOP file, stop after 3 consecutive infrastructure failures. | spec §3 DRIVE | mechanism | matches in effect: `driver.py:32,39-80`. It retries the same round instead of relaunching (impl §5.6) | E13: kills at 5 points, resumed in a fresh process → byte-identical ledgers | REPRODUCED |
| M32 | `readjudicate` re-runs Algorithm 2 on stored measurements (no new evaluation); `reevaluate` re-measures, then readjudicates; `heldout` evaluates any ref. | spec §3 maintenance | mechanism | matches: `loop.py:254-316` (plus `truncate`); held-out evaluation via `paired_transfer` (`transfer.py:26-60`) | E13: readjudicate with δ×0.5/×2 spends 0 rollouts and flips only decisions | REPRODUCED |
| M33 | β0/β1 are "set once and then frozen for all transfer tests"; hyperparameters are chosen on the evolve set and frozen. | doc; spec §4.5 [sec:shibing] | mechanism | matches: config frozen into `frontier.json` (`loop.py:181`); `paired_transfer` never touches it | – | REPRODUCED |
| M34 | Proposal-side group = {annealed budget, evidence-aware history, structured exploration}; selection-side group = {leakage critic, noise floor, complexity-aware acceptance, pruning}. Pruning belongs to the selection side in the figure, abstract and README, although the code delivers it through the proposer. | spec §4.6, §7.4 | mechanism | our choice, documented: `switches.py:45-67` puts pruning in the acceptance arm, with a sensitivity arm. The paper's exact Table 2 membership is unknown | E2 reports both groupings | PARTIAL (membership inferred) |
| M35 | Unregularized ("standard") loop: run H_t, summarize traces, unconstrained proposer, score on the same set, promote the best. | spec §4.1; doc | mechanism | ours is the spec's explicit proposal (`switches.py:62-67`: constant b_max, accepted-only history, no exploration, no critic, keep argmax iff S' > S_t). The paper's definition is not accessible | E1/E2 arm | PARTIAL (definition inferred) |
| M36 | "Evolve on one benchmark, then run unchanged on others; same base harness, model, tasks and budget for every method." | doc; spec §7.1 | mechanism | matches: sealed holdout/ood splits (`Measurer(allow_sealed=False)`, `evaluate.py:207`); all arms share T, m, k; E2b also matches rollouts and search tokens | `test_rrsi_generic.py`: the loop reads only `evolve`; AUDIT: no sealed id reached the proposer, analyst or critic | REPRODUCED |
| M37 | δ at z = 2 means "an unchanged harness clears the floor S* − δ about 97.5% of the time". | spec §4.4 (code docstring) | mechanism | the estimator matches the code (M24), but the claim is statistical | E10: code-default bootstrap at k = 2 gives **91.6% (HW) / 90.7% (AQ)** (sd under-estimated ×0.69–0.70); R = 5 repeats 94%; the √(k/(k−1)) correction gives 97.5% / 96.8%; k = 4 gives 95.6% | NOT REPRODUCED (with the code's estimator at k = 2) |
| M38 | Regularization analogies: L0 edit count, L1/lasso deletion, L2/ridge token growth, entropy exploration, Dwork holdout reuse; "analogies, not literal penalties". The sources disagree on the labels (L1 vs L2 for the cost rule). | doc; spec §4.6 | mechanism | documentation only; no code depends on the labels | – | NOT TESTABLE HERE (a naming claim, nothing to measure) |

### 2.2 Quantitative claims (paper tables and headline numbers)

None of these can be reproduced on CPU. They need the named frontier policies (Claude Opus 4.8, Gemini 3.5 Flash, Gemini 3.1 Flash Lite), the benchmarks with their own graders (hidden unit tests in Docker, a Gemini-judged rubric, frozen simulators), and roughly 2 × 178 to 2 × 244 trials per round for 20–40 rounds per run. Where a CPU analogue exists, the "our evidence" column names it; its verdict is recorded once, under §2.3.

| # | claim (H_0 → RRSI) | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| Q1 | Terminal-Bench 2.1 (evolve), Opus 4.8: 74.2 → 80.2 (+6.0) | spec §7.2; doc; README | quantitative | loop faithful (M1–M36); no Terminal-Bench adapter or Docker harness | HW analogue: measured evolve +28.7 (E1) | NOT TESTABLE HERE (frontier policy + benchmark) |
| Q2 | SWE-bench Verified (OOD): 82.0 → 83.8 (+1.8) | same | quantitative | – | HW OOD +19.2 [+17.2, +21.2] (E1) | NOT TESTABLE HERE |
| Q3 | Harvey LAB evolve: 89.4 → 90.5 (+1.1) | same | quantitative | Harvey-style criterion weights are supported (`evaluate.py:166`) but no judge | – | NOT TESTABLE HERE (LLM-judged legal benchmark) |
| Q4 | Harvey LAB ID held-out: 86.9 → 89.2 (+2.3) | same | quantitative | – | HW holdout +26.7 (E1) | NOT TESTABLE HERE |
| Q5 | JobBench: 36.0 → 40.7 (+4.7) | same | quantitative | – | – | NOT TESTABLE HERE |
| Q6 | GDPval: 48.8 → 52.3 (+3.5) | same | quantitative | – | – | NOT TESTABLE HERE |
| Q7 | APEX-Agents: 34.2 → 37.9 (+3.7) | same | quantitative | – | – | NOT TESTABLE HERE |
| Q8 | EngDesign evolve: 50.0 → 54.9 (+4.9) | same; doc | quantitative | guards available (M23); no simulator | E11 design world (below) | NOT TESTABLE HERE (simulators) |
| Q9 | Frontier-Eng OOD: 17.7 → 22.0 (+4.3 medal points, +24% relative) | same; doc | quantitative | the medal computation is not in the ref repo either | E11 hardened split +3.1 [+0.2, +6.0] | NOT TESTABLE HERE |
| Q10 | Table 1, Meta-Harness: 93.0 / 89.2 / 37.1 / 49.1 / 35.7 | spec §7.3; doc | quantitative | no Meta-Harness run on any RRSI domain (`rsi/metaharness` exists but was never compared) | – | NOT TESTABLE HERE |
| Q11 | Table 1, AHE: 90.7 / 88.7 / 37.2 / 47.2 / 33.1 | same | quantitative | AHE not implemented | – | NOT TESTABLE HERE |
| Q12 | Table 1, TTHE: 91.1 / 88.5 / 35.2 / 47.0 / 31.7 | same | quantitative | TTHE not implemented | – | NOT TESTABLE HERE |
| Q13 | Table 1, HarnessX: 91.8 / 89.1 / 36.3 / 48.5 / 34.3 | same | quantitative | HarnessX not implemented | – | NOT TESTABLE HERE |
| Q14 | RRSI's OOD average is 43.6 vs 39.7 for H_0, the only method > +1 pt; Meta-Harness +0.9, AHE −0.5, TTHE −1.7 | spec §7.3; doc | quantitative | arithmetic re-checked: mean-of-3 gives 43.63 / 39.67 / 40.63 / 39.17 / 37.97 / 39.70 | – | NOT TESTABLE HERE |
| Q15 | Table 2, H_0: 89.4 / 86.9 / 39.7 / 1.56M tokens | spec §7.4; doc | quantitative | – | – | NOT TESTABLE HERE |
| Q16 | Table 2, unregularized: 92.8 / 88.9 / 40.3 / 3.80M | same | quantitative | arm = M35 | E2: +46.2 / +9.6 / −3.4 / 93× H_0 | NOT TESTABLE HERE |
| Q17 | Table 2, −proposal: 90.7 / 88.8 / 41.9 / 2.69M | same | quantitative | arm = M34 | E2: +27.8 / +25.7 / +16.2 / 4.46× | NOT TESTABLE HERE |
| Q18 | Table 2, −acceptance: 91.5 / 88.7 / 41.0 / 3.59M | same | quantitative | arm = M34 | E2: +43.8 / +10.9 / −0.8 / 23.9× | NOT TESTABLE HERE |
| Q19 | Table 2, full RRSI: 90.5 / 89.2 / 43.6 / 2.42M | same | quantitative | – | E2: +28.7 / +26.7 / +19.2 / 3.50× | NOT TESTABLE HERE |
| Q20 | Removing the acceptance regularizers adds ~48% tokens (3.59/2.42) and costs 2.6 OOD | spec §7.4 [sec:harry] | quantitative | – | direction only: E2 −acceptance 6.8× full's tokens, OOD −20.0 [−23.2, −16.9] | NOT TESTABLE HERE (magnitude) |
| Q21 | Gemini 3.5 Flash (evolved separately), Terminal-Bench: 64.6 → 78.7 (+14.1) | spec §7.5; doc | quantitative | – | policy-swap analogue, see L10/L12 | NOT TESTABLE HERE |
| Q22 | Gemini 3.5 Flash, SWE-bench: 76.8 → 79.0 (+2.2) | same | quantitative | – | policy swap: WEAK-search OOD +12.7 [+10.8, +14.9] | NOT TESTABLE HERE |
| Q23 | Gemini 3.1 Flash Lite (never used in search), Terminal-Bench: 11.2 → 14.6 (+3.4, +30%) | same | quantitative | – | E12 / policy swap, see L11 | NOT TESTABLE HERE |
| Q24 | Final harness 2.42M tokens/trial vs 3.80M unregularized and 3.82M AHE ("58% more for 4.4 points less OOD") | spec §7.6; doc | quantitative | – | E1: 3.50× vs 93× H_0 | NOT TESTABLE HERE |
| Q25 | 26.3 steps per trial vs 27.3–34.6 for rivals and 21.2 for H_0 | spec §7.6; doc | quantitative | HW steps = 1 + active mechanisms (`domain.py:114`), a size proxy only | L8 | NOT TESTABLE HERE |
| Q26 | Abstract: "30% fewer policy tokens than the unregularized evolution" (the ablation implies 36%) | spec §7.6 [sec:abstract]; doc | quantitative | – | HW: 1 − 3.50/93.0 = 96% fewer; direction only | NOT TESTABLE HERE |
| Q27 | Meta-Harness practises best (93.0 vs 89.4) but adds under a point OOD; AHE and TTHE end below H_0 | spec §7.3; doc | quantitative | as Q10–Q13 | – | NOT TESTABLE HERE |

### 2.3 Qualitative findings

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| L1 | Unregularized evolution overfits: evolve ↑ while transfer ≈ H_0 ("gains shrink or vanish on unseen benchmarks") | spec §1, §9.2 E1; doc | qualitative | arms M35 vs full | E1 (HW, 50 seeds): unregularized measured +46.2, true evolve +46.0, holdout +9.6, **OOD −3.4 [−6.3, −0.3]**. That is *worse* than "≈ H_0"; with context dilution off, OOD is +9.3. `results/rrsi/e1_overfitting.json` | PARTIAL (overfits more than the paper, which has OOD ≈ +0.6) |
| L2 | RRSI trades a smaller evolve gain for a larger OOD gain and a cheaper final harness | spec §1 [sec:abstract]; doc | qualitative | full arm | E1 HW paired full − unregularized: evolve −17.9 [−19.6, −16.3], holdout +17.1, OOD **+22.6 [+19.3, +25.8]**, tokens −90× [−123, −59]. E1b AQ: leak kept 0/5 vs 4/5, but OOD −1.2 [−19.4, +17.5] and tokens not significant (pre-stage-B mock) | REPRODUCED (HW); AQ shows leak blocking only |
| L3 | "Removing guards **always** raises the practised score and lowers the unseen one: the signature of overfitting" | doc; spec §7.4 | qualitative | E2 arms | E2: no guards +17.5 evolve / −22.6 OOD; −acceptance +15.1 / −20.0; **−proposal −0.9 [−2.9, +1.2] evolve** / −3.0 [−5.5, −0.4] OOD | PARTIAL (holds for 2 of 3 arms) |
| L4 | Removing the proposal regularizers *raises* evolve by only 0.2 but lowers OOD by 1.7 ("steering where the search looks matters even when nothing is rejected") | spec §7.4 [sec:awesome] | qualitative | M34 grouping | E2 −proposal: evolve −0.9 (not raised, both realizations); OOD −3.0 [−5.5, −0.4], borderline (previous realization −2.3 [−5.0, +0.3]); pruning moved to proposal: +1.9 [−0.9, +4.8] | NOT REPRODUCED (evolve rise); OOD drop borderline |
| L5 | With no guards the agent uses "over half as many tokens again" (3.80M vs 2.42M); no-guards is the most expensive arm | doc | qualitative | – | E2: no guards 93× H_0 vs full 3.5× (+90× [+59, +123]); ordering none > −acc > −prop > full > H_0 **matches the paper's token ordering** | REPRODUCED (direction and ordering; magnitude uncalibrated) |
| L6 | The ranking flips: the method that practises best transfers worst; RRSI practises least well and transfers best | doc; spec §7.3 | qualitative | only our own arms exist | E2: unregularized has the highest evolve (+46.2) and the lowest OOD (−3.4); full has the highest OOD. But full is not the lowest evolve (−proposal +27.8 < full +28.7). Prior methods were not run | PARTIAL (vs ablations only) |
| L7 | "No held-out split got worse in any domain" | doc; spec §7.2 | qualitative | `paired_transfer` non_regression / significant_regression flags (`transfer.py:56-60`) | **New re-analysis:** E1 full, 0/50 seeds with a true holdout or OOD regression (unregularized 30/50 regress on OOD). E11 guarded hardened split +3.1. Validation: HW OOD +0.007 true, AQ k = 10 holdout +0.27 / OOD +0.18, live OOD +0.33 | REPRODUCED (at our scale) |
| L8 | RRSI's final harness takes fewer steps than rivals (26.3 vs 27.3–34.6) but more than H_0 (21.2) | doc; spec §7.6 | qualitative | proxy only: HW steps = 1 + mechanisms | **New re-analysis of E2 rows:** mean mechanisms full 22.3 (steps ≈ 23.3), −proposal 24.8, −acceptance 46.8, unregularized 59.0, H_0 2 | PARTIAL (the proxy is harness size, not an agent's step count; rivals = ablations only) |
| L9 | RRSI ends with a lighter harness than every rival, but still more tokens than H_0 ("transfer gain partly bought with extra inference compute") | doc; spec §7.6 [sec:harry] | qualitative | cost rule, shaped rule, prune | HW: full 3.5× H_0, lighter than every ablation arm (E2). AQ offline validation: the final harness is **lighter than H_0** (626 vs 736 tokens) after the within-band persona prune; live ×3.6 | PARTIAL (vs ablations; "more than H_0" holds on HW only, see C20) |
| L10 | Model independence: RRSI works when the search itself runs with another policy (Gemini 3.5 Flash evolved separately; OOD +2.2) | doc; spec §7.5 | qualitative | `make_domain(policy=WEAK)` | **New policy swap (50 seeds):** WEAK-policy search, full vs unregularized: evolve +26.5 vs +58.0, holdout +22.6 vs +5.5, OOD **+12.7 [+10.8, +14.9] vs −2.4**; paired OOD +15.1 [+12.7, +17.5] | REPRODUCED (HW analogue) |
| L11 | The evolved harness transfers to a weaker policy never used in the search (Flash Lite +3.4, +30%) | doc; spec §7.5 | qualitative | `with_policy` | E12: strong-evolved → WEAK, unseen **+16.6 [+14.7, +18.5]** vs unregularized +1.6 (paired +15.1). Policy swap: WEAK-evolved → VWEAK unseen +12.8 [+11.3, +14.4] vs +1.7. AQ SimModel(0.6): +36.3 vs +34.4, no RRSI advantage (pre-stage-B mock) | REPRODUCED (HW); no difference on AQ |
| L12 | A weaker search policy gets a larger evolve gain than a strong one (Table 3/4: +14.1 Flash vs +6.0 Opus) | spec §7.5 (table implication) | qualitative | – | Policy swap, full: WEAK − STRONG evolve gain **−1.7 [−3.9, +0.6]** | NOT REPRODUCED (HW analogue; world-dependent) |
| L13 | The leakage critic rejects hard-coded task names, entities, values and answers before any evaluation; generic improvements pass | doc; spec §6.2, E3 | qualitative | M26 | E3 (15 seeds): at 15% leaks, on − off: leaks kept −8.5 [−9.9, −7.1], evaluations on leaky candidates −13.0, OOD +18.0 [+13.1, +22.7]; at 30%, OOD +21.9; at **5%, OOD +5.5 [−0.7, +12.0] (n.s.)**. Catch 1.0 → 0 leaks kept. Caveat: literal leaks are caught by an answer-key denylist the paper lacks (§3 N3). Live: Haiku copied an evolve answer; the precheck caught 1 of 3 values and the repair removed all three | PARTIAL (not significant at 5% leaks; oracle denylist) |
| L14 | Noise floor: no change may fall more than δ below the best so far, which stops a slow slide made of small losses | doc; spec §4.3, E4 | qualitative | M18, M22 | E4 drift pool (20 seeds): true change with the S* floor −2.7 [−2.9, −2.4] (≈ 1.7 δ), S_t floor −10.8, no floor −30.1 | REPRODUCED (slide bounded, at 1.7 δ rather than δ) |
| L15 | Noise chasing is controlled: candidates that win by luck are not kept | doc; spec §1 | qualitative | M18, M24 | E4 null pool: whole-run false-gain rate 1.0% (bootstrap δ), 0.4% (R = 5); but **12.5% in round 0**, nominal 8.2%, so the low pooled rate comes from winner's-curse inflation. HW validation: all 17 rejected candidates were truly worse or null; both keeps truly better on evolve | PARTIAL |
| L16 | Cost must be earned: an expensive harness is kept only if the gain pays; the result is a lighter harness with ≥ OOD | doc; spec E5 | qualitative | M19–M21 | E5 (50 seeds): complexity term on vs off, tokens −5.7× [−9.0, −3.2]; OOD +1.9 [−1.2, +5.0], which **misses the 1-pt non-inferiority margin** (passed in 1 of 3 realizations); tokens vs β1 non-monotone | PARTIAL |
| L17 | Pruning deletes parts that stopped helping (L1-like) | doc; spec E6 | qualitative | M14 | E6 (50 seeds): dead mechanisms −0.8 [−1.4, −0.2], prune edits accepted +3.7, mechanisms −4.5, tokens −4.4× [−6.5, −2.1]; costs true evolve −1.3 [−2.5, −0.1] | REPRODUCED |
| L18 | Shrinking edit budget: early bundles, later single changes, "so every gain can be traced to a single change" | doc; spec E7 | qualitative | M8 | E7 (50 seeds): hitchhiker rate 0.16 vs 0.24 for const-4 (−8.5 pts [−13.8, −3.0]); early progress vs const-1 +4.7 [+2.8, +6.6]; credit Spearman 0.55 vs 0.51 (const-1 0.86); budget ends at 2, not 1 | REPRODUCED |
| L19 | Memory of what failed: rejected ideas stay rejected instead of being retried | doc; spec E8 | qualitative | M10, M15 | E8 **not run** (needs a real LLM at scale). The mocks skip rejected ideas by construction; live r1 did not redraw r0B (one anecdote). AQ r2 shows a failure mode: bundle credit marked the best mechanism (Python tool) "rejected", and it was never retried alone | NOT REPRODUCED (untested) |
| L20 | Forced variety when stuck: a stall inside the band sends budget to untouched components | doc; spec E9 | qualitative | M12, M13 | E9 (30 seeds): strict prompt collapse gives coverage 1.00 vs 0.60, structural accepted +0.9, OOD +2.5 [+1.6, +3.5]; mild collapse gives coverage +0.13 only, with the other effects n.s. | PARTIAL |
| L21 | Domain guards stop a candidate lifting the average by pushing designs into invalid territory | spec §4.3; eng SKILL.md | qualitative | M23 | E11 (30 seeds): 0 accepted guard violators on vs 1.3/run off; holdout valid rate +6.9 [+4.8, +9.1]; hardened split +3.1 [+0.2, +6.0]; the true valid rate still drifts −3.6 through sub-tolerance steps | REPRODUCED |
| L22 | Regularization narrows but does not close the evolve-to-OOD gap | spec §8 [sec:awesome] | qualitative | – | E1 full: true evolve +28.2, holdout +26.7, OOD +19.2 (gap 9 pts vs 49 unregularized) | REPRODUCED |
| L23 | RRSI treats self-improvement as a statistics problem (test-set reuse), not pure search | doc | qualitative | M18, M24, M36 | E4, E10, E13; the winner's curse is still present (C14) | REPRODUCED (design present) |
| L24 | Guards act on both proposing and keeping while leaving the edit space open | doc | qualitative | `switches.py` groups; M1 | E2 (both groups switchable) | REPRODUCED |
| L25 | Cost is part of the keep decision → lighter harness | doc | qualitative | M19–M21 | E1, E5, E6 | REPRODUCED (HW) |
| L26 | Transfer is checked on simulator-graded engineering tasks, "closing the loophole" of pleasing an AI judge | doc | qualitative | all our graders are deterministic or analytic; no LLM-judged domain to contrast | – | NOT TESTABLE HERE (needs an LLM-judge domain beside a simulator domain) |
| L27 | Table 6 case studies: a candidate rejected by the cost rule, another by the noise floor, a small reusable fix retained | spec §7.7 [sec:awesome] | qualitative | – | validation runs: floor rejections (HW r5A, AQ r0A/r5A); cost/shaped rejections (AQ r4, live r0B); small fixes retained (AQ r1A answer format, r6A prune) | REPRODUCED (case types occur) |
| L28 | The overview's toy "overfitting trap": the plain loop's practice rises while unseen stays flat; the guarded loop practises less and transfers better | doc (toy, "not the paper's method") | qualitative | `toy.py` exact port (mulberry32) | E0: matches the page's JS to 1e-15; plain +65.4 / +2.8, guarded +19.6 / +9.3; guarded − plain unseen +6.5 [+5.6, +7.4] | REPRODUCED (toy only) |
| L29 | "Keep a set the loop never sees; score every round on it; drift = memorizing" (lesson attributed to RRSI) | doc | qualitative | the released code scores held-out only at the end; ours adds a write-only shadow monitor and `heldout_monitor` (extensions) | `test_rrsi_validation.py`: identical ledgers with the monitor on or off | REPRODUCED (as an extension) |
| L30 | Evidence is auditable and resumable: resumed runs give the same decisions; readjudicate changes only Algorithm 2 outcomes | spec E13 (code design) | qualitative | M31, M32 | E13: byte-identical ledgers across cross-process resumes with a different PYTHONHASHSEED | REPRODUCED |
| L31 | RRSI's leakage critic addresses Meta-Harness's lack of any check for test-specific edits | doc | qualitative | – | E3 shows the critic's effect vs no critic; no Meta-Harness comparison | NOT TESTABLE HERE (no head-to-head) |

### 2.4 Caveats

| # | caveat | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| C1 | Results are single numbers, with no error bars or repeated runs | doc; spec §8 | caveat | we report 95% CIs over 15–50 seeds | our own borderline effects flipped across realizations (E1 "near zero", E3 5%, E5 non-inferiority, E6 at 30 seeds, E2 −proposal; impl §6), so single numbers are indeed fragile | REPRODUCED (caveat confirmed) |
| C2 | Component credit rests on noisy empirical deltas; δ may fail if the noise is non-stationary | spec §8 [sec:harry] | caveat | – | E7 credit Spearman only 0.55; E10 under-coverage | REPRODUCED |
| C3 | Agent, proposer, analyst and critic are the same model family, which may share blind spots | doc; spec §8 | caveat | separate LLM per role is supported (`loop.py:71-91`) | offline mocks; live used Haiku in every role; not tested | NOT TESTABLE HERE |
| C4 | Many tuning knobs (b_min/b_max, δ, β0/β1, pruning window, stall window, T), chosen on the evolve set | doc; spec §8 | caveat | all exposed in `Config` | E5 β1 sweep: tokens 1.28× (β1 = 0) → 3.50× (40) → 3.16× (160), non-monotone; β1 = 0 gives OOD +7.1 vs +19.2; E10 z sensitivity | REPRODUCED (sensitivity confirmed) |
| C5 | Abstract 30% token saving vs the ablation's 36% | doc; spec §7.6 | caveat | – | paper arithmetic: 1 − 2.42/3.80 = 36.3% (re-checked) | NOT TESTABLE HERE |
| C6 | Frozen weights only; longer horizons ("hundreds of rounds", "second-order overfit") and other architectures untested | doc; spec §8 | caveat | – | our runs are T ≤ 40 | NOT TESTABLE HERE |
| C7 | Third-party take: the annealed budget alone may explain most of the OOD gain | spec §8 [sec:jjak-take] (not a paper claim) | caveat | `RegularizerSwitches.budget_only/selector_only` | E2b: budget-only OOD +2.7 [−0.3, +5.6] vs selector-only +16.2; selector − budget +13.5 [+10.1, +16.8] (candidate-matched), +18.6 (token-matched) | CONTRADICTED (the hypothesis, at our scale) |
| C8 | The critic is not a guarantee: it may miss subtle leaks or reject useful mechanisms | spec §8 | caveat | M26 | E3: regex-only keeps 1.3–3.7 obfuscated leaks per run; catch 0.8 still keeps some; live precheck missed 2- and 3-digit answers (min term length 4) | REPRODUCED |
| C9 | Workspace grading relies on an LLM judge; token count is only a cost proxy (no latency or tool cost); adaptive "just above δ" poisoning is unexplored | spec §8 | caveat | our proposer is shown δ and S* numerically (§3 N2), which would *enable* such targeting with a live LLM | – | NOT TESTABLE HERE |
| C10 | Each domain needs a hand-built adapter and per-domain constants | spec §8 [sec:awesome] | caveat | – | confirmed: a taxonomy-less domain burned every reserved slot until `trust_code_tags` was added (impl §6.2.1) | REPRODUCED |
| C11 | Winner's curse and a ratcheting S*: the incumbent is never re-measured | spec §8.1 | caveat | faithful to code (never re-measured) | AQ r1A measured 0.75 vs true 0.61 (k = 10); superseded 1.00 vs 0.945; E4 round-0 null rate 12.5% | REPRODUCED |
| C12 | Budget never reaches b_min in-run | spec §8.2 | caveat | M9 | schedules above | REPRODUCED |
| C13 | Pruning is advisory; B_t lists components with empty machinery lists and those older than the window | spec §8.3 | caveat | `history.py:142-151` = ref | stage-B: 8 AQ and 9 HW rounds list components with nothing to prune | REPRODUCED |
| C14 | Credit is shared across a bundle | spec §8.4 | caveat | M10 | AQ r2: the Python tool became "rejected" through a bundle with the checker; AQ r0B persona hitchhiked | REPRODUCED |
| C15 | Negative ΔS can be admitted in-band; the novelty bonus rewards neutral structural additions | spec §8.5–8.6 | caveat | M20 | AQ r6A (dS −0.10) kept; HW r4 argmax kept the dearer candidate that transfers worse (true OOD +1.6 vs +4.1) | REPRODUCED |
| C16 | Component tags are regex heuristics; mislabels feed T_t, U_t and ν | spec §8.7 | caveat | M16 | live r1B tool use tagged `control_flow` (ν 0 instead of 1) | REPRODUCED |
| C17 | The critic sees only the diff; STATE FILES never filled | spec §8.8 | caveat | M27 | – | REPRODUCED (by code inspection) |
| C18 | Evaluation is expensive (m·\|D\|·k trials per round); only the workspace domain has an ID held-out split; no per-round held-out monitoring in the released code | spec §8.9, §8.12 | caveat | ours adds the shadow monitor (write-only) | HW validation 4,000 rollouts for T = 10 | NOT TESTABLE HERE (a cost statement about frontier runs) |
| C19 | The bootstrap δ assumes exchangeable trials; the 2-sample stdev is noisy | spec §8.10 | caveat | M24 | E10: bootstrap sd ×0.69–0.70 at k = 2; R = 2 repeats clear 84% | PARTIAL (the under-coverage comes from the plug-in (k−1)/k bias rather than exchangeability) |
| C20 | "Every evolved harness costs more than H_0" | spec §7.6 [sec:harry] | caveat | – | HW: every arm > H_0. **AQ offline validation: the RRSI final is lighter than H_0** (626 vs 736 tokens/trial, k = 10) | NOT TESTABLE HERE for the paper's domains; one counter-example at our scale (see §1) |

Verdict counts in §1 come from these four tables. C20 counts as NOT TESTABLE HERE, and C7 as CONTRADICTED.

## 3. Mismatches the earlier reviews missed

The impl notes (§5), two adversarial reviews (impl §6.1–6.2) and the stage-B audit already list the documented deviations: the proposer protocol, the digesters, no git, the added-line precheck, path-glob taxonomies, `trust_code_tags`, the resume counts for ν, the switch-aware constitution and the shadow monitor. The items below are **not** flagged as deviations from the reference anywhere in `docs/methods/rrsi-impl.md` or `validation/rrsi/AUDIT.md`. They were found by the side-by-side reading and the differential test for this audit.

- **N1: Algorithm 2 tie semantics differ.**
  - Ours: `CostRule`/`NoiseFloor` use `dS > δ + 1e-9`, `S' ≥ floor − 1e-9` and `shaped > 1e-9` (`core/gates.py:151,177,184`).
  - Ref: raw `delta_S > delta`, `S' < floor` and `shaped > 0` (ref `rrsi/selection.py:84-91,107-108`).
  - When dS equals δ exactly in exact arithmetic, the ref's branch depends on float rounding: 0.55 − 0.50 = 0.05000000000000004 > 0.05 takes the cost-rule branch, while ours takes the within-band shaped rule.
  - Impact: 18/6,000 random rounds changed admissibility (15 winners), all at exact ties. This matters only when δ is an exact multiple of the score quantum. That is unlikely with a calibrated δ, but possible with a hand-set δ (e.g. δ = 0.05 on 20 tasks at k = 1).
  - Ours is the mathematically consistent reading. Impl §5.17 mentions `TIE_EPS` only for the ablation gates.
- **N2: the proposer sees numeric thresholds the reference proposer never sees.**
  - `round_directives` (`loop.py:420-424`) carries δ, S*, S_incumbent, T and the trace ids.
  - `default_constitution` fills in β0, β1, w_s, w_c and w_n (`constitution.py:172`).
  - The ref's SKILL.md states the rules symbolically ("beta0 + beta1 x (gain)"), and its context has no δ or S* (ref `propose.py:246-283`).
  - The mocks ignore the text, so the offline results are unaffected. With a live LLM this could let the proposer aim for gains just above δ, the "adaptive poisoning" concern (C9).
- **N3: the leakage precheck is an oracle.**
  - `Domain.leakage_terms` returns every evolve task id **and every target answer of 3 or more characters** (`core/domain.py:166-175`), and `RRSICritic` puts them in its denylist (`critic.py:112-117`).
  - The ref's prechecks hold task names (coding), id patterns (workspace, eng) and grader artefacts; answers and entities are left to the LLM review.
  - As a result, in HW, literal answer-table leaks are caught deterministically, and the E3 "catch 0" arm is regex-only in name only. This flatters the critic in E1, E3 and E12 compared with the paper's setting.
  - Impl §5.4 lists "answers" as a feature, but no verdict discusses the advantage. A faithful arm would restrict the denylist to ids and patterns.
- **N4: missing trials are padded with weight 1.0.** `from_core` pads with weight 1.0 (`evaluate.py:175-179`), whereas the ref's workspace adapter gives a missing trial the task's criteria count. This affects only weighted (Harvey-style) domains; no experiment uses weights.
- **N5: `History.render` drops the BASELINE record** (`history.py:163`). The ref renders it, and it counts toward the ≤ 4 unmeasured rows. Cosmetic: in 23/300 random ledgers, this row is the only difference.
- **N6: the `tool` alias.** Ours canonicalises a declared `tool` to `client_tool` (`components.py:49,161-165`). The ref treats `tool` as outside K, bounces it in done(), and re-tags it from the diff. This gave 20/3,000 `normalize` differences. It is an extension and harmless for tagging, but a live proposer is not bounced for a vocabulary slip the ref would bounce.
- **N7: the eval_invalid retry measures the whole job again with the same job seeds** (`loop.py:151-157`, `evaluate.py:192-195`). The ref's retry refills only the missing trials of a resume-safe run. In a deterministic domain our retry is a no-op. With a live task model it re-pays for every trial.
- **N8: every E-experiment used the heuristic analyst, not the three-lens LLM analyst.** `Config.analyst="auto"` needs an explicit `llm_analyst` (`loop.py:90-91`), and `_common.run_hw`/`run_aq` pass only `llm_propose`. F_t in E1–E12 is therefore a deterministic clusterer over grader feedback. The LLM digesters and aggregator appear only in the validation runs. Impl §5.3 calls the heuristic "the offline default", but no E-row says it applies to all of them.
- **N9: the smoke check is generic** (`core/domain.py:177-184`): 1 task, seed 0, error-only. The ref's per-domain smoke checks need 2–4 tasks, and eng also requires no-payload = 0 and combined_score > 0. A candidate that runs but produces empty payloads would pass our smoke check and fail the ref's. That affects cost (an evaluation is spent on it), not selection.
- **Re-confirmed, already known:**
  - the budget never reaches b_min;
  - the E10 bootstrap under-covers at k = 2;
  - the AQ parts of E1b, E12 and E13 were **not** re-run after the stage-B mock and analyst fixes (AUDIT §5), so the AQ numbers quoted in L2 and L11 come from pre-fix code.

## 4. What would be needed to fully reproduce

1. **Frontier policies and the real benchmarks.** Q1–Q27 need:
   - Claude Opus 4.8 as policy, proposer, analyst and critic, plus Gemini 3.5 Flash and 3.1 Flash Lite;
   - Terminal-Bench 2.1 (89 tasks, Docker via harbor) and SWE-bench Verified (500);
   - Harvey LAB pinned at `1da4750`, with the Gemini-3.5-Flash rubric judge;
   - JobBench, GDPval and APEX-Agents through the archipelago runner;
   - EngDesign-Open (61 tasks), EngDesign v1 hardened and Frontier-Eng with their simulators and the (unpublished) medal computation.

   At the paper's settings a run costs m·|D|·k = 356–488 agent trials per round for T = 20–40 rounds per domain, plus baselines and 3 ablation arms. Our package would also need adapters (`Domain` subclasses) for each benchmark.
2. **The baselines.** Meta-Harness, AHE, TTHE and HarnessX all need to run under the same budget and on the same start harness (Table 1, Q10–Q14, L6, L31). `rsi/metaharness` exists but has never been run on an RRSI domain. AHE, TTHE and HarnessX are not implemented.
3. **Faithful proposer and analyst plumbing.** The ref's 40-turn JSON action agent (list/read/edit/write, read_trace over all traces) and the tool-using digesters (`digest_many`, grep/read/bash allowlist) are needed to test the proposal-side claims (L4, L19/E8, L20) with a real LLM; the shallow mocks cannot "refine what has credit". Fixing N2 (hide numeric δ and S*) and N3 (id/pattern-only denylist) would put the critic and the proposer on the paper's footing.
4. **The paper's exact definitions.**
   - the unregularized loop's keep rule (M35);
   - which mechanisms belong to each Table 2 ablation group (M34);
   - whether the paper's b_t reaches 1 (M9).

   All three need the paper text, which the egress proxy blocked.
5. **Statistics the paper omits.** To measure the noise itself:
   - repeated runs (≥ 3 seeds per arm on real benchmarks), so the Table 1/2 differences (0.2–4 pts) can be compared with run-to-run noise;
   - a re-measured incumbent, or a held-out-validated S*, to separate the method from the winner's curse (C11);
   - a δ estimator that covers at k = 2 (E10's √(k/(k−1)) correction, or R ≥ 5 repeats).
6. **Calibration of the synthetic world.** HarnessWorld magnitudes (93× tokens unregularized vs 2.4× in the paper, unregularized OOD −3.4 vs +0.6) come from world assumptions (`context_penalty`, the leak and narrow-mechanism shares). Before any magnitude comparison, HW would need fitting to at least one real run's per-mechanism effects and costs.
