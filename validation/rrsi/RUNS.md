# RRSI: from-scratch validation runs

Three runs, each started from the **untouched seed artifact** of its domain in a fresh run
directory. The live run used a fresh LLM cache. Every step is in `<run>/trace.jsonl`
and rendered in `<run>/TRACE.md` (`rsi.trace.inspect`). `<run>/audit.json` is the
independent step audit (`rsi.rrsi.audit`). It re-derives every step from the trace with
the paper's formulas, not with the loop's code. `<run>/summary.json` holds the numbers
below, including the round table, transfer report and spend.

Reproduce (each command refuses to reuse an existing directory or cache):

```bash
python experiments/rrsi/validate_rrsi.py offline_agentqa
python experiments/rrsi/validate_rrsi.py offline_harnessworld
python experiments/rrsi/validate_rrsi.py live_agentqa --max-usd 2.0 --max-wall-min 32
# (the recorded live run was interrupted once and continued with --resume --max-usd 1.5 --max-wall-min 25)
python experiments/rrsi/validate_rrsi_stepcheck.py     # independent step check -> <run>/stepcheck.json
python experiments/rrsi/validate_rrsi_replay.py        # $0 offline-cache replay of live_agentqa on the current code
# (after the claims-audit fixes the replay misses every proposer call by design: the prompts changed)
```

The stage-B adversarial audit of all three runs (step verdicts, paper alignment, inconsistency register, fixes) is in `AUDIT.md`.

How to read the tables:
- **S** is the mean exact-match reward (AgentQA) or the fraction of rubric criteria passed (HarnessWorld).
- **C** is mean policy tokens per trial.
- "loop" numbers come from the loop's own measurements.
- "transfer" numbers come from `rsi.core.transfer_report`, which runs H_0 and the final harness unchanged on each split.
- "shadow" numbers come from the write-only `ShadowMonitor`, which scores each new incumbent on holdout and OOD. The loop never sees them.

## 1. `offline_agentqa`: AgentQA, SimModel task model, scripted proposer and critic

> **Regenerated after the claims-audit fixes** (`docs/claims/rrsi.md`, "Fix log"). The fixes change the proposer's prompt: no δ, S*, S_t or T in the directives, symbolic β/w in SKILL.md, and the BASELINE row in L_t. They also change the precheck, which now holds task ids and patterns only, not the practice answers. The mock seeds its RNG on the prompt, so this is a new realization. The stage-B run described in earlier versions of this file is in git history at `02b830e`; the stage-A run is kept as `offline_agentqa_superseded/`.

**Setup.**
- **Domain.** `make_suite(seed=0)`: 20 evolve and 19 holdout questions from the practice family `numeric`, plus 24 OOD questions (6 each from the never-seen families `dates`, `numbertheory`, `strings`, `lists`). The suite is de-duplicated across splits; the stage-B overlap `evolve-numeric-017` = `holdout-numeric-012` is gone.
- **Seed.** `AgentQADomain.seed_artifact()`: `harness.py` makes one direct model call and returns the last line. `system.md` is "You are a helpful assistant." and `task.md` is `{question}`.
- **Models.** The frozen task model is `SimModel`. `AgentQAMockLLM` (scripted) is the proposer and the critic. The analyst is the deterministic heuristic.
- **Config.** T = 8, m = 2, k = 2; paper defaults otherwise (b_min..b_max = 1..4, w = 3, m_draft = 1, β0 = 0.10, β1 = 40, w_s = 100, w_c = 15, w_n = 0.5, n_prune = 4, repair_rounds = 5). δ is calibrated.

**Spend.** $0 (all mocks), 7 s. 762 loop LLM calls (712 task, 31 proposer, 19 critic), 360 rollouts. The shadow monitor's task calls are metered separately.

**Seed vs final** (`transfer_report`, k = 2) and the k = 10 reference re-evaluation (`reference_k10.json`):

| split | H0 | final (r2A) | paired diff [95% CI] | k = 10: H0 → r0A → r2A |
|---|---|---|---|---|
| evolve | 0.350 | 0.925 | +0.575 [+0.375, +0.750] | 0.350 → 0.980 → 0.955 |
| holdout | 0.395 | 0.974 | +0.579 [+0.474, +0.684] | 0.389 → 0.932 → 0.932 |
| ood | 0.375 | 0.979 | +0.604 [+0.438, +0.771] | 0.442 → 0.946 → 0.938 |

Tokens per trial: 736 (H0) → 1,235 (r0A) → 1,293 (r2A). Shadow monitor (holdout / OOD, k = 1): H0 0.42 / 0.42, r0A 0.95 / 0.88, r2A 1.00 / 1.00.

**What the loop did, round by round** (every number is re-derived from raw trials by `experiments/rrsi/validate_rrsi_stepcheck.py`).

- **Setup.** Baseline S = 0.375 (15/40). δ = 0.147 (within-task bootstrap, sd_null = 0.0736).
- **r0** (b_t = 4). r0A = Python tool + verbose persona: S = 0.950, dS = +0.575 > δ, dC = +0.68 ≤ 0.10 + 40·0.575 = 23.1, so **kept** (S* = 0.950). r0B = persona alone: S = 0.575, admissible, LOST. The costly persona hitchhiked on the tool's gain (spec §8.4).
- **r1.** Both first drafts carried the leaky answer table (`memory/answers.json`, keyed on practice questions). The precheck holds no answers, as in the code; the LLM review rejected both drafts, and the repair removed the table. r1A (step-by-step + null reword + verify): dS = 0, dC = +0.05, shaped −0.68, rejected. r1B (checker sub-call + reword): S = 0.625 < floor 0.803, rejected.
- **r2.** r2A and r2B (r2B after a leak rejection and a repair) = the "write Python" skill card + the answer format: S = 0.975, dS = +0.025 inside the band, dC = +0.05, ν = 1 (skill, never accepted before): shaped = +2.30, so both are admissible. The exact tie goes to the first, and **r2A is kept** (S* = 0.975). k = 10 shows r2A is really a touch *worse* than r0A (0.955 vs 0.980 evolve, equal holdout, 0.938 vs 0.946 OOD) at +4.7% tokens: the novelty bonus admitted a neutral structural addition (spec §8.5–8.6), and the measured 0.975 is a winner's-curse draw.
- **r3–r5.** The mock's clean ideas are used up and every draft is the leak. The LLM review rejected all six; the repair had nothing clean left to ship, so all were dropped as `critic_reject` without an evaluation. σ = 1 from r4 (S_4 − S_1 = 0.025 ≤ δ); U_t = {tool, memory}, and B holds the reserved slot, which the mock cannot fill (no `tool` idea; its only `memory` idea is the leak).
- **r6.** r6A (verify): S = 0.925, dS = −0.05, shaped −5.36, rejected. r6B: leak, `critic_reject`.
- **r7.** r7A (null reword): dS = −0.025, shaped −2.57, rejected. r7B: the done() contract bounced its reserved slot and then 4 zero-change submissions, and the leak draft that followed was rejected (`critic_reject`).
- **Net.** Two acceptances. The final harness (Python tool + persona + skill card + answer format) is a large real gain (k = 10: +0.61 evolve, +0.54 holdout, +0.50 OOD) at 1.76× H_0's tokens. No within-band prune happened in this realization, so, unlike the stage-B realization, it is not lighter than H_0. The leak was blocked before evaluation in all 11 of its drafts.

**Audit.** `rsi.rrsi.audit`: 230 pass, 0 fail, 0 unverifiable, 12 info (7 repairs that shipped nothing, 5 done() bounces). Independent step check (`stepcheck.json`): 237 correct, 7 questionable (the k = 2 bootstrap under-covers; B_t lists `subagent` with no machinery in r2–r7), 0 wrong, 0 unverifiable.

## 2. `offline_harnessworld`: HarnessWorld, simulated policy, parametric mock LLM

> **Regenerated after the claims-audit fixes** (same reason as §1; the stage-B run is in git history at `02b830e`).

**Setup.**
- **Domain.** `make_domain(seed=0)`: 100 evolve, 100 holdout and 100 OOD tasks (OOD uses shifted families), 16 rubric criteria each.
- **Seed.** `HarnessWorldDomain.seed_artifact()`: `harness.md` plus two generic mechanisms.
- **Policy.** Simulated STRONG policy.
- **LLM.** `HarnessWorldMockLLM` plays proposer, critic (catch rate 0.8 on obfuscated leaks), digesters and analyst, so the *LLM analyst path* is exercised.
- **Config.** T = 10, m = 2, k = 2, defaults otherwise.
- **Ground truth.** Analytic E[S] and E[C] for every evaluated candidate are recorded in the trace as report-only data.

**Spend.** $0. 139 search-LLM calls (80 digester, 10 analyst, 27 proposer, 22 critic), 4,200 rollouts, 2 s.

**Seed vs final.** Transfer at k = 2; the last column is the analytic ground truth.

| split | H0 | final (r9B) | paired diff [95% CI] | analytic E[S], H0 → final |
|---|---|---|---|---|
| evolve | 0.472 | 0.647 | +0.175 [+0.151, +0.200] | 0.464 → 0.638 |
| holdout | 0.527 | 0.683 | +0.156 [+0.132, +0.181] | 0.528 → 0.691 |
| ood | 0.402 | 0.506 | +0.104 [+0.079, +0.129] | 0.403 → 0.503 |

E[C] per trial: 2,145 → 9,023 (×4.21), 16 active mechanisms (H0: 2).

**What the loop did.** δ = 0.0159 (bootstrap, k = 2, 100 tasks). The round-by-round table with the ground truth of every candidate is in `AUDIT.md` §2.2. In short:
- **Keeps (7):** r0A (context pin), r3A (structured search tool + costly prompt + the narrow `nar_04`), r5B, r6A (planner sub-call), r7A (critic sub-call), r8A (verification skill) and r9B (code-runner tool + the harmful `har_05`, kept inside the band on ν = 1). All seven are truly better on evolve (+0.0025 to +0.051). r9B lowers OOD by 0.015.
- **Rejections (11):** all truly worse or null on evolve. 8 by the floor, 3 by the shaped rule (r6B is a null at +47% tokens).
- **Admissible but LOST (2):** r3B (the reserved code-runner tool, truly +0.019) lost to r3A (+0.030). r8B is a prune of the structured search tool, cheaper and inside the band: shaped +1.57, truth −0.016. It lost to r8A.
- **Leaks:** the precheck (evolve task ids + `hw-e-\d{3}`) rejected 5 literal-leak drafts (r0B, r1A, r1B, r3A, r8A). They are keyed by task ids, so the fix that removed the answer key changes nothing here. The LLM review rejected 2 obfuscated hash-keyed drafts (r0B's repair, r6A). All were repaired; none was evaluated.
- σ = 1 only at r3 (U_t = {config, client_tool}); the reserved slot was honoured by r3B.

**Audit.** 360 pass, 0 fail, 0 unverifiable, 7 info (the ground truth of the 7 keeps). Step check: 335 correct, 10 questionable (the k = 2 bootstrap δ under-covers; B_t lists components with no machinery to prune in r1–r9), 0 wrong.

## 3. `live_agentqa`: AgentQA, Claude Haiku everywhere (pre-fix code)

> **Recorded on the pre-fix code.** This run predates the claims-audit fixes. Its proposer saw δ, S*, S_t, T and the numeric β/w (N2); its L_t lacked the BASELINE row (N5); its precheck held the evolve answers (N3), which is why it caught `570186` below. It was **not** re-run: a faithful rerun costs about $2.8, the whole live cap. A $0 replay on the fixed code misses every proposer call by design (`live_agentqa/replay_current_code.json`). On the fixed code, the r0A leak below passes the faithful precheck (as it would pass the code's), and the Haiku critic rejects it under REJECT rule 1, naming all three copied answers (fresh cache, 1 call, $0.025; `AUDIT.md` §2.3). A new live run on the fixed code is `results/rrsi/live_smoke.json`.

**Setup.**
- **Domain.** `make_suite(n_evolve=12, n_holdout=12, n_ood_per_family=3, seed=0)`: 12 evolve and 12 holdout `numeric` questions, 12 OOD questions (3 each from `dates`, `numbertheory`, `strings`, `lists`).
- **Seed.** `AgentQADomain.seed_artifact()`, untouched ("You are a helpful assistant.", one direct call).
- **Models.** `CachedLLM(ClaudeCLI("haiku"))` is the frozen task model and, as a separate wrapper on the same fresh cache `validation/rrsi/.cache_live_agentqa`, the proposer, critic, digesters and analyst (LLM analyst path).
- **Config.** T = 4, m = 2, k = 1, calibration_repeats = 3, max_digests = 4, n_fail_traces = 6, n_success_traces = 3, repair_rounds = 2, max_done_bounces = 2; paper defaults otherwise. Shadow monitor k = 1.
- **Budget.** The first attempt (07:38) was killed by an account usage limit in the middle of round 0, after the baseline, calibration, the analysis, r0A's draft (with its critic repair) and r0B's draft. It was resumed with `--resume --max-usd 1.5 --max-wall-min 25` (10:28). Resume reused the settled baseline, δ and F_0, reused r0A's draft from `prep.json`, and replayed r0B's proposer reply from the cache (same artifact `4b316504d2`). `trace.jsonl` therefore holds two `run_start` segments; the first segment's round-0 events are the partial, superseded attempt.

**Outcome.** Stopped by the wall-clock budget (`max_wall_s`) after **2 of 4 rounds** (the budget is checked between rounds). T = 4 was not reached.

**Spend (honest total).** **$2.80** in live Haiku calls, summed over every entry of the fresh cache (178 calls, each paid exactly once):
- first, interrupted attempt: $0.88;
- resumed loop: $1.16 (task $0.66, proposer $0.20, digesters $0.13, critic $0.10, analyst $0.07); the meter reports cached replays at $0;
- shadow monitor: $0.33; transfer report: $0.44.
- Wall time: about 15 min (first attempt) + 29 min (resumed run incl. transfer) ≈ 44 min, slightly over the 40-minute target. Haiku through the CLI takes 1 to 4 minutes per proposer / analyst call.

**Seed vs final** (`transfer_report`, k = 1, paired over 12 tasks per split):

| split | H0 | final (r0A) | paired diff [95% CI] | tokens/trial H0 → final |
|---|---|---|---|---|
| evolve | 0.500 | 0.750 | +0.250 [−0.083, +0.583] | 1,954 → 7,106 |
| holdout | 0.500 | 0.750 | +0.250 [0.000, +0.500] | 1,552 → 4,139 |
| ood | 0.333 | 0.667 | +0.333 [+0.083, +0.583] | 2,344 → 2,952 |

Shadow monitor (write-only, same k = 1): H0 holdout 0.50 / OOD 0.33 → r0A holdout 0.75 / OOD 0.67. Only OOD's CI excludes zero; at 12 tasks and k = 1 the evolve and holdout gains are not statistically established.

**What the loop did, round by round.**

- **Setup.**
  - Baseline S = 0.500 (6/12), C = 2,075 tokens.
  - δ = 0.272 (repeated base evaluations, 3 × k = 1: sd_null = 0.136, z = 2). This is large: with 12 tasks one task is 0.083, so any candidate within ±3 tasks of the incumbent is "inside the band" and is decided by the shaped score, where cost weighs heavily.
- **r0** (b_t = 4, σ = 0, U_t = all six components).
  - **F_0 (LLM analyst, 4 digests).** Two failure modes: the answer is buried in explanatory text so extraction fails (3 tasks), and arithmetic errors.
  - **Leak caught.** r0A's first draft put worked examples in `system.md` containing `570186`, which is the target of `evolve-numeric-008` (copied from a trace). The deterministic precheck rejected it ("hard-coded evaluation data '570186'"); the repair replaced the values with `<numeric value>` placeholders and the Haiku critic accepted it.
  - **Candidates.**
    - r0A: system prompt (step-by-step, verify, `ANSWER: <value>` final line) + `harness.py` extraction of the `ANSWER:` marker with a last-line fallback. S = 0.667, dS = +0.167, dC = +0.848.
    - r0B: similar prompt/format edits plus `task.md` and extraction changes. S = 0.750, dS = +0.250, dC = +1.998 (about 3× tokens).
  - **Gates (re-derived by hand from the trace).** Floor S* − δ = 0.228: both pass. Both dS ≤ δ, so both take the within-band shaped rule `w_s·dS − w_c·dC + w_n·ν`:
    - r0A: 100·0.1667 − 15·0.848 + 0 = **+3.95 > 0** → admissible.
    - r0B: 100·0.25 − 15·1.998 + 0 = **−4.97 ≤ 0** → rejected.
  - **Decision.** argmax S' over the admissible set {r0A} → **r0A kept**, S* 0.500 → 0.667. The higher-scoring r0B lost on cost alone; this is what Algorithm 2 prescribes (within band, the shaped score decides admissibility), and it is correct as a step. Whether r0B was really better cannot be told at k = 1 (a 1-task difference).
- **r1** (b_t = 4, σ = 0, U_t = {tool, skill, memory, subagent}).
  - **F_1.** One failure mode: wrong numeric values from arithmetic errors (4 tasks).
  - r1A (prompt only: "use the Python tool, mandatory") scored S = 0.333, below the floor 0.667 − 0.272 = 0.395 → rejected. The incumbent r0A harness makes one plain `llm(...)` call and never runs `tools`, so the prompt asks the model to "execute it using available tools" that it does not have; that is plausibly why it hurt (my reading, not verified).
  - r1B (prompt + `harness.py` "auto-execute Python code" + magnitude checks) scored S = 0.500, dS = −0.167 (inside the band), dC = −0.465: shaped = −16.67 + 6.97 = **−9.69** → rejected.
  - **Decision.** No admissible candidate, H_2 = H_1 = r0A.
  - **Tagging looks wrong for r1B.** Its `harness.py` diff extracts ```` ```python ```` blocks from the reply and runs them with `tools.python(...)`, i.e. it adds tool use, but it was declared and normalized as `control_flow` (components [prompt, control_flow, prompt]). So `tool` stays in U_t as "untried" and the attribution scoreboard credits `control_flow`. This follows the reference code's `normalize` exactly (a declared tag is kept whenever the *whole* candidate diff evidences it, and `harness.py` changes evidence `control_flow`), so it is faithful, not an implementation bug; but as a step it is an incorrect component attribution. It also set ν = 0 instead of 1 (`tool` is structural and untried); +0.5 would not have changed the shaped score of −9.69.
- **Stop.** Wall budget reached before r2.

**Things to note.**
- **Resume is correct but visible in the trace.** The superseded round-0 events of the killed attempt stay in `trace.jsonl` (segment 1). The audit and the round table read them together; r0A's "2 proposer calls" come from segment 1, and segment 2 logs `draft reused from prep.json (resume)` for it.
- **Loop-measured vs re-measured cost.** The loop measured r0A at dC = +0.85 (C 2,075 → 3,835); the transfer pass measured evolve tokens ×3.6. At k = 1 per-trial token counts are noisy, and the shaped rule's decision (+3.95 vs 0) was sensitive to it: dC above ≈ 1.11 would have rejected r0A as well.
- **Winner's curse** as in run 1: S* = 0.667 is a single k = 1 sample; the transfer pass measured the same harness at 0.750 on evolve.

**Audit.** 76 pass, 0 fail, 0 unverifiable, 0 info. Stage-B step check: 61 correct, 1 questionable (δ from 3 evaluations at k = 1), 0 wrong.

**Replay on the current (stage-B fixed) code.** `validate_rrsi_replay.py` re-ran this configuration from scratch with every LLM behind the run's own cache in offline mode (a miss would be an error, never a paid call): 107/107 cache hits (19 search, 88 task), $0, and the history, decisions, trajectory, δ and final incumbent are identical to the record (`live_agentqa/replay_current_code.json`). This also shows that the kill-and-resume did not change any decision compared with an uninterrupted run.


