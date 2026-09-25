# Dream-RSI: stage-B independent step audit

Auditor: stage B (did not write the method code or the stage-A runs). Date: 2026-09-25.
Scope: every run under `validation/dream-rsi/`. That is the three stage-A runs (`sumdiff_offline`, `agentqa_offline`, `sumdiff_live`) and one live re-run I made after the fixes (`sumdiff_live_b`, from scratch with a fresh cache).

## How the audit was done

**1. Independent re-derivation.** `experiments/dream-rsi/validate_stepaudit.py <run>` writes `<run>/stepaudit.json`. It re-derives every step from the files on disk only: `trace.jsonl`, `trace_pool/iter*/tree.json` and `live_cycle_manifest.json`, `snapshots/`, `history/r####_*/method.py`, `dream_prompts/` and the fresh LLM cache. It uses its own code:
- **Re-grade.**
  - Sum-difference: `construct.py` runs in a fresh interpreter. The checks are my own (list of distinct ints, 2 ≤ |A| ≤ 400, span ≤ 20000), and Γ is computed from pure-Python sets.
  - AgentQA: a fresh `Evaluator` with `SimModel`.
  - The mean of the raw trials must equal S, and `delta_vs_parent` is recomputed.
- **Diffs.** My own `difflib` diff between the parent snapshot and the child snapshot, compared against the diff in the trace.
- **Claims against diffs.**
  - Mock sum-difference: the claimed stage must be exactly the appended `STAGES` entry.
  - AgentQA: the claimed knob against the knob diff.
  - LLM: the mechanisms named in the claim against `STAGES`, plus a check for exact repeats of a program that was already scored successfully.
- **Context the agent saw.** Lineage, siblings from earlier rounds only, H_(t−1) (checked per attempt).
- **Leakage (AgentQA).** Every task id, question prefix and target of every split is grepped for in every program.
- **Online rounds.** My own grid simulator checks legality: ≤ W cells, at most one cell per branch, legal before the call, `legal_n` correct. The `prefix_seen` the policy got is rebuilt, and the reveals must equal `tree.json`.
- **plan_grid.** Re-executed on the manifests of the cycles before t. It must equal the requested plan, and the used plan must equal the requested plan clamped to the hard caps.
- **Live root.** Must equal the best program so far.
- **Best-program gate.** Recomputed with a strict `>` and the earliest-created cell winning ties.
- **Manifest.** Every field re-derived from `tree.json`.
- **Replay.** A **second, independent replay implementation** written from paper §3: a root pick reveals the earliest-created unrevealed branch, a leaf reveals its recorded child, a missing continuation exhausts the branch, and the plan is intersected with the support. It drives every archived policy version through the public Listing-2 API.
  - Its batches, reveals, N, k and V_i must equal the trace.
  - Eq. 1 is recomputed from the traced reveal batches, with scores looked up in `tree.json`.
- **Selection.** Argmax with the incumbent kept on ties. The version the next cycle deploys must be the one selected.
- **Revisions.**
  - The revision diff must equal the diff between the files in `history/`.
  - The base must be the strongest version so far (value, then recency).
  - The mutator's claimed moves and `params_changed` must equal the real PARAMS diff.
  - A grep for leakage and for forbidden APIs (`best_so_far`, `budget_spent`, `open(`, `exec(` …).
- **Beta sweep.** AUC, parallel penalty and the degenerate flag are recomputed from the traced points.
- **Prompts (live runs).**
  - Every Listing-1 agent prompt is **rebuilt from disk state** (problem, parent workspace, lineage, siblings, H_(t−1), baseline, direction). Its CachedLLM key (sha256 of model, system, prompt, seed) must exist in the fresh cache. That proves what the agent saw, and that it saw nothing else.
  - Every saved Listing-2 developer prompt must hash to a cache entry, and it must contain no manifest from a later cycle.
- **Accounting.** Calls per cycle, per-attempt usage against the meter, and the sum of the cache files against the loop spend.

**2. Reading every trace by hand.** I read the traces round by round (`TRACE.md` / `trace.jsonl`). Steps that pass every mechanical check but do not make sense, or were misled, are downgraded in the `JUDGEMENT` table of the script. Each downgrade carries its reason in `stepaudit.json`.

**3. Mechanical checks outside the script.** Monitor isolation, replay cost, the default-beta rule, and a new online ground truth, all described below.

## 1. Summary of step verdicts

| run | steps | correct | questionable | wrong | unverifiable |
|---|---|---|---|---|---|
| `sumdiff_offline` (mock agent + mutator, T=4) | 109 | 107 | 2 | 0 | 0 |
| `agentqa_offline` (AgentQA via DomainTask, sealed splits, T=4) | 95 | 94 | 1 | 0 | 0 |
| `sumdiff_live` (haiku agent + developer, T=3; stage A) | 85 | 69 | 1 | **15** | 0 |
| `sumdiff_live_b` (haiku, T=2; stage-B re-run after the fixes) | 46 | 45 | 0 | 0 | 1 |
| **total** | **335** | **315** | **4** | **15** | **1** |

Per kind (all runs):
- **All correct.** baseline 4/4, plan 11/13 (2 questionable), live root 13/13, online rounds 60/60, best-program gates 13/13, manifests 13/13, replay evaluations 36/36 (every one reproduced by the independent replay), selections 7/9 (1 questionable, 1 unverifiable), beta sweeps 9/9, prompt reconstructions 4/4 (36/36 + 9/9 + 17/17 + 3/3 prompts), cost 4/4, monitor 1/1.
- **Attempts.** 116/129 correct. `sumdiff_live` has 12 wrong and 1 questionable.
- **Revisions.** 24/27 correct. 3 `sumdiff_live` revisions are wrong.

**All 15 wrong steps come from one harness bug, the reply parser, which was fixed. None is a Dream-RSI method error.** After the fixes, the live re-run has 0 wrong steps: 17/17 attempts compile, and there are 0/3 developer repair rounds.

## 2. Per-round step audit

### `sumdiff_offline`
Setup: untouched `SumDiffDomain` seed, Γ = 0.910491; the independent re-grade gives 0.910491, and the seed file is byte-identical to `SumDiffDomain().seed_artifact()`. The rest of the setup is the mock agent, the ParametricMutator, a 3×5 grid, W=3, T=4 and M=4.

| cycle | step | verdict | evidence |
|---|---|---|---|
| 1 | plan π₁ | correct | fallback 3×(refine 4), seq 3 |
| 1 | online rounds 1–5 (seq 4–8) | correct | batches of 3, all roots then deepen all; `prefix_seen` = my reconstruction; 15 cells = tree |
| 1 | 15 attempts | correct | 15/15 own Γ = recorded; each claim `# apply <mech> <params>` = the appended STAGES entry; lineage/siblings/history = a / (cells of earlier rounds) / 0 |
| 1 | gate + decision (seq 40–41) | correct | round best 1.0 at b1.a3 (tie with b1.a4, b2.a4, earliest wins) > 0.910491 |
| 1 | manifest (seq 42) | correct | every field re-derived (gain_early 0.665, N 15, k 5, beta 0.6) |
| 1 | replay r0000–r0003 | correct | own replay: V = 0.865 (N 15, k 5, 1 − 0.15 + 0.015) and 0.910 (N 10: b1 closed after 2 flat cells, b0 after 3, b2 deepened to a4 = 1.0) |
| 1 | revisions r0001–r0003 | correct | rewrite to the adaptive template + moves; own PARAMS diff = claimed moves; base = strongest (value, recency) |
| 1 | selection → r0001 (seq 54–58) | **questionable** | the argmax is right, but it is a replay artefact (one world; the ceiling value 1.0 also sits in b2.a4). I re-ran the online ground truth: over **40** fresh one-cycle searches from the cycle-2 root (stage A's 8 seeds plus 32 new ones), r0001 never found more than π₁ and found less on 18/40. Mean gain −0.0028 (95% bootstrap CI [−0.0046, −0.0012]) at 8.2 vs 15 calls. Stage A's 8 seeds alone gave −0.0060, so stage A overstated the size but got the direction right. |
| 1 | beta sweep (seq 59) | correct | own AUC 0.3333, penalty 0.5067; feedback only |
| 2 | plan (r0001) | **questionable (label)** | the plan (3, 4) is right, but the template reports "live best still improving" from ONE manifest (`improving = len(hist) < 2 or …`). Spec §4.5 says insufficient history → bootstrap. The outcome is identical (hold = fallback). |
| 2 | online rounds 1–5 | correct | batches [3,3,3,2,1]: b2 closed after 3 flat results (1.0071 ×3), b1 after one flat refinement, the last singleton on the only improving branch b0 |
| 2 | 12 attempts, gate, manifest | correct | best 1.0 → 1.017204 |
| 2 | dream: replay ×4, revisions ×3, selection | correct | V = [0.901, 0.901, 0.901, 0.899571] → incumbent kept (tie rule). The default-beta rule was checked by hand: improving (1.0172 > 1.0), no nearby β with V_eq1 more than 0.02 better → keep 0.6. |
| 3 | plan (r0001) | correct | 2 manifests, improving: hold |
| 3 | online, 7 attempts, gate, manifest | correct | batches [3,3,1], 7 calls; best 1.017725 |
| 3 | dream | correct | V = [0.914556, 0.914556, 0.912631, 0.914556] → keep; one declared no-op move (`plan_width_step: unchanged at 1`) |
| 4 | plan (r0001) | correct | 4×(refine 2): "roots improved early while depth stalled" (gain_early 1.0 in cycle 3, max depth 2 < 4): the L2 width rule |
| 4 | online, 9 attempts, gate | correct | the new branch b3 gives the best, 1.019011 |

### `agentqa_offline`
Setup: untouched `AgentQADomain.seed_artifact()` (byte-identical), with evolve S 0.25. My fresh re-grade gives 0.25, and the mean of the 8 raw trials is 0.25.

| cycle | step | verdict | evidence |
|---|---|---|---|
| 1 | online, 9 attempts | correct | 9/9 re-graded; each knob claim is consistent with the knob diff; **no task id, question or target string of any split appears in any program** (33/33 attempts) |
| 1 | gate | correct | 0.25 → 1.0 at b0.a0 (`tool`) |
| 1 | monitor (seq 3, 29) | correct | I re-evaluated seed and best on holdout/ood with a fresh sealed-allowed Evaluator: 0.375/0.5 and 0.75/0.875 = trace |
| 1 | dream | correct | V = [0.925, 0.933, 0.933, 0.942] → r0003 (reaches the ceiling with fewer probes) |
| 2 | plan (r0003) | **questionable (label)** | same one-manifest "still improving" label; the grid (hold) is identical |
| 2 | online, 6 attempts, gate | correct | the root is already at 1.0, nothing is strictly better, the best is unchanged |
| 2 | dream → r0005 | correct (rule) | V = [0.448, 0.448, 0.455, 0.455]. World 2 is flat (root = ceiling), so quality is 0 for every policy and only cost is ranked. The stage-A ground truth says the switch is neutral online (8 vs 8 calls, gain 0). |
| 3 | plan | correct | final_best 1, 1: plateau → widen to 4 (the L2 rule) |
| 3 | dream | correct | V = [0.281, 0.277, 0.277, 0.277] → keep |
| 4 | plan, online (10 calls), gate | correct | widen to 5; no gain possible (saturated) |
| – | default-beta rule | correct | plateau and no attainment gain at higher β, prior 0.6 < 0.7 → 0.6 ("conflicting") |

### `sumdiff_live` (stage A; haiku; recorded before the parser fixes)

| cycle | step | verdict | evidence |
|---|---|---|---|
| all | agent prompts | correct | **36/36 prompts rebuilt from disk hash to the fresh cache** (using the pre-fix head-only error rendering the run had) |
| all | developer prompts | correct | 9/9 `dream_prompts/*` hash to the cache; no later-cycle manifest in any prompt |
| 1 | online rounds 1–4, plan, root, gate, manifest | correct | 4×3 batches; best 0.910491 → 1.023749 at b1.a3 |
| 1 | 6 failed attempts (b1.a0, b0.a1, b2.a1, b0.a2, b2.a2, b2.a3) | **wrong (harness)** | own run reproduces the `SyntaxError`, but the offending line is a trailing `===` / a closing fence left by `rsi.core.parse_file_blocks`. With it stripped they score 0.9552, **1.0286**, 1.0196, 1.0142, 1.0163, 1.0000; b0.a1 would have been the round's best. The fix (applied to all 45 cached replies) makes all 15 broken files (12 agent + 3 developer) compile. |
| 1 | b0.a2 | **wrong (harness)** | also a parser failure, and it abandons its idea on a false diagnosis ("avoids the fringe-related compilation issue") because the agent saw only the head of the traceback |
| 1 | b0.a3 | **questionable** | misled the same way: "reversed … order, causing compilation failure" |
| 1 | b1.a1, b2.a2 | correct | ignored the failure and changed mechanism (legitimate under Listing 1) |
| 1 | replay r0000–r0003 | correct | own replay V = 0.895 for all (every version probes all 12 cells; the ceiling b1.a3 is in the last round and 6 of 12 cells are repairable-looking failures, so no version stops early) |
| 1 | revisions r0001, r0003 | correct | real rewrites; diffs = history; no leakage |
| 1 | revision r0002 | **wrong (harness)** | first attempt rejected by "invalid syntax (line 188)" = a trailing fence; the repair round "fixed an indentation error on line 170" (an invented cause); the change text reports only the repair. I re-replayed the fence-stripped first attempt myself: V 0.895, so the selection would not change. |
| 1, 2 | selections | correct | all ties → incumbent π₁ kept |
| 2 | 5 failed attempts (b1.a0, b2.a0, b0.a1, b0.a2, b0.a3) | **wrong (harness)** | parser leftovers; re-graded without them: 1.0, 1.0, 1.0, 1.0, 1.0210 |
| 2 | b1.a1, b2.a1, b0.a3 diagnoses | correct | from the parent's source, the agent correctly located the "stray markdown" line (the Listing-1 "specific fix in hand") |
| 2 | revisions r0005, r0006 | **wrong (harness)** | same pattern as r0002 (invented causes: "indentation … line 230", "recovery_candidates sorting"); fence-stripped first attempts replay to V 0.895 (checked) |
| 2 | gate | correct | 1.023749 → 1.030417 at b2.a2 |
| 3 | 11 attempts correct, b2.a3 **wrong (harness)** | – | re-graded without the leftover: 1.0148; best unchanged (1.030253 < 1.030417, strict) |
| all | cost | correct | 36 calls = the sizes of the 3 trees = the meter; per-attempt usage sums to $2.0304 = meter; the 45 fresh cache files sum to **$2.9060** = loop spend |

### `sumdiff_live_b` (stage-B re-run; details in §5)
- **Correct, 45/46 steps.** 17/17 attempts compile and re-grade exactly, and 17/17 agent prompts plus 3/3 developer prompts hash to the fresh cache.
- **Replay.** Every version is reproduced by the independent replay: V = [0.925, 0.9333, 0.925, 0.92125].
- **Unverifiable, 1 step: the cycle-1 selection of r0001.** The rule is followed, and the choice is justified from the prefix: r0001 closes b2 after two flat results (1.0, 1.0); the unrevealed b2.a2 = 1.016 was not the ceiling. Its online value is not measured.
- **Cycle-2 plan.** "Early gains strong, late plateau → widen and reduce depth" gives 4 × 2. It is right from the manifest (gain_early = 1.0) and matches the L2 width rule.

## 3. Prefix-only and sealed-split checks, what the proposers saw, cost

- **What the proposers saw.**
  - Listing-1 agent: every live prompt was rebuilt bit-exactly from on-disk state (53/53). It contains the problem, the parent workspace (program, `proposal.md`, `eval/score.json`, `error.txt`), the branch lineage, sibling attempts **of earlier rounds only**, H_(t−1) (the attempts of all earlier cycles: 0 / 12 / 24 records), the seed baseline and the branch direction. There is no sealed data (sum-difference has none) and nothing from the current round's concurrent attempts.
  - Listing-2 developer: 12/12 saved prompts are what the model received. They hold only the history of earlier versions and manifests of completed cycles.
- **Sealed splits never flow back.**
  - I grepped `rsi/dream/*.py` for `holdout|ood|allow_sealed|unseal|.monitor|trace.jsonl|load_trace`. The only hits are `DomainTask._decision_split` (which refuses sealed splits), the `GuardedSelector`'s *replay-world* holdout (unrelated), and the monitor's construction.
  - `RunTracer.kept` → `ShadowMonitor.observe` returns `None` and writes only to the trace. The monitor has its own `Evaluator(allow_sealed=True)`. Its LLM goes through `ShadowLLM` (roles `shadow:*`, which are excluded from the loop usage).
  - `tests/test_dream-rsi_validation.py` shows identical ledgers, trajectories, best program and final policy with and without the monitor and the trace (7/7 pass).
- **No peeking in replay.**
  - The policy only ever holds `QuestionProxy`.
  - In `earliest` mode, root tags are hidden until revealed.
  - The independent replay (which exposes only the public API) reproduced all 36 replay evaluations, i.e. no version depended on anything but the prefix.
  - No policy code contains a best-cell score of any world or a forbidden API.
- **Replay costs zero agent calls.** In every run, `state.cost.agent_calls` after dreaming equals the cumulative tree sizes (15/27/34/43, 9/15/23/33, 12/24/36, 9/17), while only the replay-episode counter grows during dreaming (10 / 30 / 60 in the offline runs).
- **Spend.**
  - Stage A: $2.906, verified against the cache files.
  - Stage B: re-run $1.278 (agent $0.939 for 17 calls, developer $0.340 for 3 calls), also verified against the cache files. It took 19.5 min wall time.
  - The offline runs cost $0.

## 4. Paper alignment

### (a) Mechanism level (spec section → evidence)

| # | Paper element | Traced behaviour | Status |
|---|---|---|---|
| M1 | Outer loop §3.2: plan → online → append world + manifest → dream π^0 + revisions, each on ALL worlds → argmax incl. incumbent → deploy | Every cycle has this order, e.g. `sumdiff_offline` seq 3 round_start → 4–8 online → 41 decision → 42 manifest → 43 incumbent re-replay on H_1 → 45–53 revisions → 54–58 selection → 61 cycle 2 deploys r0001. Each version's eval covers `len(worlds) = t`. | faithful |
| M2 | A(T) = {r} ∪ leaves; batches ≤ W; one cell per branch; several roots allowed (§3.1, L2:103–105) | 60/60 online rounds and all replay rounds are legal (own simulator) | faithful; the multi-root reading resolves the §3.1 formalism gap (documented) |
| M3 | Online transition resumes the parent workspace (§3.3) | parent → child diffs rebuilt from snapshots (129/129); a failed node's child resumes the broken workspace | faithful |
| M4 | Replay: root → earliest-created unrevealed branch; leaf → recorded child; nothing generated (§3 p.6) | the independent replay reproduces 36/36 evaluations (64 world episodes) | faithful (`root_mode="earliest"`) |
| M5 | Eq. 1 V_i = max s − β1·N + β2·N/max(1,k); V = mean over worlds (§4.1–4.2) | recomputed for every version and world | faithful, plus a documented deviation: per-world normalization (spec §9.3). β1 = 0.01 and β2 = 0.005 are from the doc demo; the paper reports none. |
| M6 | m* = argmax incl. π^0 ⇒ V^{m*} ≥ V^0 (§4.2) | 9/9 phases; the incumbent is kept on ties | faithful (the tie rule is a framework choice; the paper is silent) |
| M7 | M versions / off-by-one (§3.2) | offline M=4 versions; live `m_semantics="revisions"` | documented deviation (ambiguity resolved as a flag) |
| M8 | π₁ = parallel refine (§3.5) | batches: all roots, then deepen all, up to W | faithful |
| M9 | Cost = cumulative agent calls (§4) | meter = tree sizes; replay adds 0 | faithful |
| M10 | plan_grid before the live grid, only earlier manifests, deterministic (L2:190–242) | re-executed on manifests < t: 13/13 equal | faithful. Label issue: a single manifest is called "improving" (open, minor). |
| M11 | Default beta ≈ 0.6 when evidence is insufficient; cross-cycle rule (L2:169–188) | hand-checked for all offline phases; LLM policies bake in 0.6 | faithful |
| M12 | Beta sweep / Pareto objective (L2:12–22, 145–188) | runs use Eq. 1; the sweep is feedback only (degenerate for π₁, flagged) | documented deviation (spec §8.15: the paper never says which objective it used) |
| M13 | Listing 1 prompt (App. B.1) | at the time: the text was followed in condensed form; single-completion adaptation; history capped at 8 cycles / 30 records per section; **proposals clipped to 600 chars**; errors head-only during stage A (fixed). After the claims audit (§8): verbatim Listing 1 (incl. the pkill line), full history by default | inconsistent-fixed |
| M14 | Listing 2 prompt + prefix-only rules (App. B.2) | at the time: a condensed paraphrase (the claims audit found ≈20 dropped rules; this audit had called it faithful). After the fix (§8): verbatim Listing 2 + framework notes; static check + leakage screen + 1 repair round (framework additions) | inconsistent-fixed |
| M15 | Out-of-support plans "cannot earn replay reward" | at the time: default `support="clip"`; 0 out-of-support episodes in these runs. Now the default is `no_reward` (§8) | faithful (after the fix) |
| M16 | Developer "start from a strong recent policy" (L2:247) | 27/27 revisions start from the strongest (value, recency) | faithful |
| M17 | Dream after the last cycle | skipped (`dream_last=False`) | documented deviation |
| M18 | Root of round t (unspecified in the paper) | best program so far (13/13 roots verified) | documented choice |
| M19 | Direction provider (unspecified in the paper) | per-round shuffled cycle of the task's mechanism classes | documented choice |

### (b) Behaviour level

- **Replay costs 0 agent calls.** Reproduced: replay costs only CPU, 2.8–4.2 ms per episode.
- **Selection that includes the incumbent never lowers the replay score.** Reproduced in 9/9 phases.
- **Dreaming changes the policy and saves calls.**
  - Reproduced offline: 43 calls vs 60 for fixed π₁ in `sumdiff_offline`, and 33 vs 36 in `agentqa`.
  - Reproduced live only after the fix. In `sumdiff_live_b`, haiku's r0001 is deployed and cycle 2 uses 8 calls.
  - In stage-A `sumdiff_live`, every LLM policy tied with π₁ (V 0.895). On those worlds no prefix signal lets a policy stop early without missing a ceiling: world 1's ceiling (b1.a3) is in the last round, and half of all cells were (parser) failures that the Listing-2 rules treat as repairable. The run was effectively Recursive Fixed Exploration.
- **"Better quality at lower cost" is not reproduced.**
  - The one offline policy switch that could be checked online (`sumdiff_offline` t=1) bought fewer calls (8.2 vs 15) with significantly less gain: −0.0028, CI [−0.0046, −0.0012], over 40 searches.
  - This is the spec's §8.2 frugality bias (the replay ceiling rewards reaching the *recorded* best cheaply) and §8.4 (one world at t=1). The paper never validates replay against online value; here Spearman(replay, online) = −1 at t=1 (stage A).
  - This does not refute the paper. Its gains appear at 110–640 calls per round over 5–10 rounds; here grids are 9–15 calls and T ≤ 4.
- **Pacing (Fig. 6: conserve while improving, then spend more after a plateau).**
  - `agentqa_offline`: calls 9 → 6 → 8 → 10, widening on "plateaued". That is qualitatively the paper's story, but the task is already saturated, so the extra calls are wasted.
  - `sumdiff_offline`: 15 → 12 → 7 → 9, with widening driven by within-cycle early gains, not by a plateau.
  - `sumdiff_live_b`: 9 → 8, widen and shallow.
  - Suggestive only: single short runs.
- **Replay independence (spec §8.3).** Live agents copy sibling recipes. In `sumdiff_live_b` cycle 1, b1.a2 and b2.a2 adopt b0.a1's "multi-start ensemble". So recorded outcomes depend on which siblings were revealed, which replay assumes they do not. The size of this bias is unverifiable: the counterfactual outcomes were never generated.

## 5. Fixes and re-runs

| # | Bug (found in) | Fix (file) | Regression test | Re-run |
|---|---|---|---|---|
| F1 | Trailing `===` / closing fence left by `parse_file_blocks` made 12/36 agent programs and 3/3 developer first attempts `SyntaxError`s (stage A) | `strip_reply_terminators` in `EditorAgent` and `LLMPolicyDeveloper` (`rsi/dream/agent.py`, `developer.py`; stage A) | `test_strip_reply_terminators_repairs_fenced_file_ends`, `test_llm_developer_strips_a_trailing_fence…` (stage A); `test_every_observed_reply_shape_yields_a_scoring_program` (stage B, end to end through `EditorAgent` → grader) | `sumdiff_live_b` |
| F2 | The agent saw only the head of a traceback and blamed ideas for slips (stage A) | `AttemptRecord.render` shows the tail (stage A) | `test_attempt_record_shows_the_cause_of_a_failure` | `sumdiff_live_b` |
| F3 | A repaired revision's claim held only the fix (stage A) | `<claim> [repaired: <fix>]` (stage A) | **new**: `test_repaired_revision_keeps_its_first_claim` | (no repair happened in the re-run) |
| F4 | **Residual of F1, found by stage B.** A *leading* ```` ```python ```` fence stays when the closing fence is followed by `===` or is missing. It compiled nowhere, and F1 did not handle it. | `strip_reply_terminators` also drops a leading opening fence (`rsi/dream/agent.py`) | **new**: `test_every_observed_reply_shape…`, `test_leading_fence_is_dropped_only_at_the_file_start` | the fix landed while `sumdiff_live_b` was running (the process had already imported the old module); none of its 20 replies had that shape |
| F5 | **Stage B.** The developer repair prompt said only "invalid syntax (line 188)", which led the model to invent causes | `static_check` quotes the offending line (`rsi/dream/guard.py`) | **new**: `test_static_check_quotes_the_offending_line` | same caveat as F4 (not in the re-run process) |
| F6 | **Stage B, docs.** The 600-char clip of proposals shown to the agent was undocumented | `docs/methods/dream-rsi-impl.md` §9 | – | – |

- **Re-run `sumdiff_live_b`.** It started from scratch: an untouched seed, a fresh run dir, and a fresh `.cache_sumdiff_live_b` with 20 misses and 0 hits.
  - **Setup.** haiku as agent and developer; 3×3 grid, W=3, T=2, 3 revisions.
  - **Result.** 17/17 attempts ran without errors (the stage-A run had 12/36 parser failures). 3/3 developer revisions passed without a repair round (vs 3/3 repaired). Γ went 0.9105 → 1.0183 → **1.0361**, which is higher than stage A's 1.0304 with 36 calls, on 17 calls.
  - **Dreaming.** It deployed a haiku-written policy (V 0.9333 vs 0.925). The next cycle's `plan_grid` widened the grid to 4 branches of depth 2 (8 calls).
  - **Audit.** Stage-A audit: 0 FAIL. My step audit: 45 correct, 1 unverifiable.
- **The offline runs were not re-run.** F1–F5 touch only the LLM paths, and the mock decisions are unchanged: `tests/test_dream-rsi_*.py`, 70 tests, all pass.
- **The 3-FAIL line in `sumdiff_live.log` was stale.** It listed diff-check FAILs from the first stage-A audit pass. Those came from the trace clipping text at 6000 chars; stage A's `same_text` handles clipping. My own diff check passes on all 129 attempts and 27 revisions.

## 6. Inconsistency register

| item | status | evidence |
|---|---|---|
| Outer loop order, replay semantics, Eq. 1, argmax incl. incumbent, π₁, cost unit, prefix-only | faithful | §4(a) M1–M6, M8, M9, M14, M16; independent replay 36/36 |
| Per-world normalization of Eq. 1 | documented-deviation | impl §8.4; raw ranking would be identical on flat worlds |
| β1 = 0.01, β2 = 0.005, M, K1, K2 | documented-deviation | the paper reports none (spec §4.8) |
| M semantics (versions vs revisions) | documented-deviation | spec §3.2 off-by-one |
| `dream_last=False` | documented-deviation | impl §8.7 |
| Root of round t = best program | documented-deviation | paper unspecified (spec §3.3) |
| Direction provider | documented-deviation | paper unspecified |
| Out-of-support plans | faithful (was documented-deviation "clip") | since the claims-audit fix the default is Listing 2's "cannot earn replay reward" (`support="no_reward"`, Eq. 1 and the Pareto sweep); "clip" is a non-default option. See §8 |
| Eq. 1 used, Pareto sweep feedback only | documented-deviation | spec §8.15 |
| Listing-1 history caps (8 cycles, 30 records) | faithful (was documented-deviation) | claims audit N5/M4: every earlier search, every record, full `proposal.md` by default; caps are non-default and announced in the prompt (§8) |
| Proposal text clipped at 600 chars in the agent's history | faithful (was documented-deviation) | same fix: `AttemptRecord.render_dir` shows the full proposal by default (§8) |
| Reply-parser trailing `===` / fence | inconsistent-fixed | F1; re-run clean |
| Traceback head-only in the agent prompt | inconsistent-fixed | F2 |
| Repaired revision claim = fix only | inconsistent-fixed | F3 |
| Mutator claims of no-op moves | inconsistent-fixed | stage A |
| Leading ```` ```python ```` fence left in a file | inconsistent-fixed | F4 (stage B) |
| Repair prompt without the offending line | inconsistent-fixed | F5 (stage B) |
| Root cause in `rsi.core.parse_file_blocks` | inconsistent-fixed (core) | fixed in `rsi.core` after this audit: lone / trailing fences and `===` terminators are stripped (checked: all three observed reply shapes now parse to clean code); the local workaround stays as a second line of defence |
| Adaptive template labels one manifest "live best still improving" | inconsistent-fixed | §8: with one manifest the template no longer claims a trend; balanced gains → "evidence insufficient … conservative bootstrap"; both offline runs re-recorded, their cycle-2 plan steps are now correct |
| Replay ranks against online value (frugality bias, one world at t=1) | faithful (paper-level weakness, documented) | our selection rule IS the paper's argmax; the bias is the method's (spec §8.2/§8.4). Re-measured on the re-recorded `sumdiff_offline` (§8). Mitigations: the restored Listing-2 prompt carries the paper's own guard ("do not select the default simply as the smallest beta that reaches a frozen trace's known ceiling"); `selector="guarded"` (non-default, not the paper's rule; E9) |
| No-peeking through state kept across replay episodes (claims audit N1) | inconsistent-fixed | §8: fresh policy namespace per episode in both runners + static-check rules; the auditor's memo cheater now scores exactly like parallel refine |
| Listing-2 developer prompt condensed (≈20 rules dropped; claims audit N2) | inconsistent-fixed | §8: verbatim Listing 2 + a separate "framework notes" block; every version's history and traces in the context |
| Listing-1 prompt: pkill line and phrases missing, false "in full" claim (claims audit N5) | inconsistent-fixed | §8: verbatim Listing 1; directories rendered inline; `baseline/proposal.md` shown |
| Dream's per-round budget above Fixed's (claims audit N3) | inconsistent-fixed | §8: `Config.round_budget="fallback"` (default) = Fixed's per-round calls; checked per cycle by the step audit |
| W below the fixed grid's width in E3/E6 (claims audit N4) | inconsistent-fixed | E3/E5/E6 now run W = grid width (every workspace in parallel) |
| Policy developer's cost outside the cost story (claims audit N7) | inconsistent-fixed | `CostMeter.developer_revisions`, `llm_calls_total`, `developer_usd_share`; E3 reports LLM calls incl. developer requests |
| Replay independence of sibling context (§8.3) | unverifiable | sibling copying observed; counterfactuals not generated |
| Online value of the live dreaming decision (`sumdiff_live_b` r0001) | unverifiable | no live ground truth within the budget |
| Paper headline results (8 tasks, Gemini, 110/640 calls per round, 5–10 rounds) | unverifiable | scale; official code unreleased |

## 7. Remaining open issues

1. **Core change request.** `rsi.core.parse_file_blocks` should drop trailing `===` / `=== END … ===` / closing-fence lines and an unmatched leading fence inside a `=== FILE:` block. Every method that uses `RewriteEditor` is exposed to the stage-A failure mode. `rsi/dream` works around it locally.
2. **Core change request (audit convenience).** `rsi.trace` clips text at 6000 chars, so agent prompts and replies in `trace.jsonl` are truncated. The Dream developer saves full prompts in `dream_prompts/`, but agent prompts had to be rebuilt from disk and matched to the cache (they were, 53/53).
3. **One-manifest label.** The adaptive template's "still improving" label with a single manifest is left as is. Fixing it changes every adaptive-policy artefact id and needs a re-run of both offline runs, for a label-only effect.
4. **Replay vs online.** Replay can prefer frugal policies that lose online gain (§8.2). With `selector="argmax"` (the paper's rule), nothing guards against it. `selector="guarded"` exists but is not the paper's rule.
5. **Scale.** No run approaches paper scale, so the paper's quality claims are unverifiable here.
