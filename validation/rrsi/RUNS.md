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
python experiments/rrsi/validate_rrsi_stepcheck.py     # stage-B independent step check -> <run>/stepcheck.json
python experiments/rrsi/validate_rrsi_replay.py        # $0 offline-cache replay of live_agentqa on the current code
```

The stage-B adversarial audit of all three runs (step verdicts, paper alignment, inconsistency register, fixes) is in `AUDIT.md`.

How to read the tables:
- **S** is the mean exact-match reward (AgentQA) or the fraction of rubric criteria passed (HarnessWorld).
- **C** is mean policy tokens per trial.
- "loop" numbers come from the loop's own measurements.
- "transfer" numbers come from `rsi.core.transfer_report`, which runs H_0 and the final harness unchanged on each split.
- "shadow" numbers come from the write-only `ShadowMonitor`, which scores each new incumbent on holdout and OOD. The loop never sees them.

## 1. `offline_agentqa`: AgentQA, SimModel task model, scripted proposer and critic

> **Re-run in the stage-B audit (see `AUDIT.md`).** The stage-B audit found two bugs that changed what this
> run did: the scripted proposer declared a self-consistency edit that its own tool edit had overwritten
> (phantom credit in L_t), and the heuristic analyst ranked *solved* tasks as the top "failure mode" from
> round 3 on. Both were fixed and this run was redone from scratch (same seed artifact, fresh directory; the
> mocks are deterministic, so no LLM cache is involved). The first run is kept, unchanged, as
> `offline_agentqa_superseded/` for reference. Because the mock seeds its RNG on the prompt text, the fix
> gives a different realization, not a corrected copy of the old one.

**Setup.**
- **Domain.** `make_suite(seed=0)`: 20 evolve and 20 holdout questions from the practice family `numeric`, plus 24 OOD questions (6 each from the never-seen families `dates`, `numbertheory`, `strings`, `lists`). One holdout question (`holdout-numeric-012`, "sum of the decimal digits of 44!") is the same question as `evolve-numeric-017`: a small evolve/holdout overlap in the domain generator (reported as a core change request; it is not an RRSI step).
- **Seed.** `AgentQADomain.seed_artifact()`: `harness.py` makes one direct model call and returns the last line. `system.md` is "You are a helpful assistant." and `task.md` is `{question}`.
- **Models.** The frozen task model is `SimModel`. `AgentQAMockLLM` (scripted) is the proposer and the critic. The analyst is the deterministic heuristic.
- **Config.** T = 8, m = 2, k = 2; paper defaults otherwise (b_min..b_max = 1..4, w = 3, m_draft = 1, β0 = 0.10, β1 = 40, w_s = 100, w_c = 15, w_n = 0.5, n_prune = 4, repair_rounds = 5). δ is calibrated.

**Spend.** $0 (all mocks), 1.7 s. 1,317 loop LLM calls (1,270 task, 35 proposer, 12 critic), 480 rollouts; the shadow monitor made 440 more task calls, metered separately.

**Seed vs final** (`transfer_report`, k = 2) and a k = 10 reference re-evaluation (`reference_k10.json`, stage B):

| split | H0 | final (r6A) | paired diff [95% CI] | k = 10: H0 → r0B → r1A → r6A (tokens of r6A) |
|---|---|---|---|---|
| evolve | 0.350 | 0.675 | +0.325 [+0.075, +0.575] | 0.350 → 0.565 → 0.610 → 0.605 (626 vs H0 736) |
| holdout | 0.425 | 0.575 | +0.150 [−0.025, +0.325] | 0.390 → 0.550 → 0.635 → 0.655 |
| ood | 0.375 | 0.604 | +0.229 [+0.104, +0.375] | 0.442 → 0.600 → 0.621 → 0.617 |

**What the loop did, round by round** (every number below is re-derived from raw trials by `experiments/rrsi/validate_rrsi_stepcheck.py`).

- **Setup.** Baseline S = 0.375 (15/40). δ = 0.147 (within-task bootstrap, sd_null = 0.0736; the closed-form plug-in gives 0.0750).
- **r0** (b_t = 4). r0A = the harmful "use Python" skill card (the harness cannot run code): S = 0.000 < floor 0.228, rejected. r0B = 4 edits (null reword, verbose persona, 3-sample self-consistency, step-by-step): S = 0.550, dS = +0.175 > δ, dC = +2.07 ≤ 0.10 + 40·0.175 = 7.10 → admissible, **kept** (S* = 0.550). True gain (k = 10) +0.215; the costly persona and the null reword hitchhiked on the self-consistency gain, which the cost rule allows at this dS (spec §8.4).
- **r1** (b_t = 4). r1A = answer-format line: S = 0.750, dS = +0.200 > δ, dC = +0.02 → **kept** (S* = 0.750). r1B = checker sub-call: S = 0.650, within band, shaped = +5.22 → admissible but LOST (argmax S'). True value of r1A ≈ 0.61: the 0.75 is a lucky draw (winner's curse, spec §8.1), and every later round is judged against it.
- **r2.** Both variants bundled the Python tool with the checker sub-call (and verify); the checker re-answers without the tool, S = 0.575 (below floor 0.603) and 0.625 (shaped −10.8): rejected. Correct, but the Python tool, the best single mechanism in this domain, is now "rejected" in L_t through shared bundle credit and the mock never tries it alone again (spec §8.4).
- **r3.** Both variants drew the leaky answer table (`memory/answers.json`, keyed on practice questions). The precheck rejected both on the 7-digit answers (`2119981`, `3886910`, ...); the repair removed the table and shipped nothing, so both were dropped as `critic_reject`. Never evaluated.
- **r4** (b_t = 3). Two "verify" prompt edits: S = 0.750 (dS 0, shaped −0.30) and 0.675 (shaped −7.8): rejected.
- **r5** (b_t = 2, σ = 1: S_5 − S_2 = 0 ≤ δ; U_t = {tool, memory}, B holds the reserved slot). r5A = prune of self-consistency: S = 0.475 < floor, rejected. r5B could only fill the slot with the leak: critic_reject.
- **r6** (σ = 1). r6A = **prune of the verbose persona**: S = 0.650, dS = −0.100 (inside the band), dC = −0.73 → shaped = −10.0 + 10.9 = +0.92 > 0 → admissible and **kept**; S_t drops to 0.650 while S* stays 0.750 (floor 0.603 still binds on S*). The k = 10 reference shows this was right: r6A = 0.605 / 0.655 / 0.617 (evolve / holdout / OOD) vs r1A 0.610 / 0.635 / 0.621, at 27% of the tokens. The measured −0.10 was noise on top of r1A's lucky 0.75. r6B: leak again, critic_reject.
- **r7** (σ = 1). r7A re-adds the persona: S = 0.650, dS 0, dC +2.68 → shaped −40.2, rejected. r7B: leak, critic_reject.
- **Net.** Three acceptances. The final harness (self-consistency + step-by-step + answer format, persona pruned) is a real gain (k = 10: +0.26 evolve, +0.27 holdout, +0.18 OOD) and is *lighter* than H_0 (626 vs 736 tokens/trial), because the within-band cost rule let the loop prune the persona the r0 bundle had smuggled in. The leak was blocked in 4 variants. It never found the Python tool alone (see r2).

**Audit.** `rsi.rrsi.audit`: 253 pass, 0 fail, 0 unverifiable, 14 info. Independent stage-B step check (`stepcheck.json`): 224 correct, 8 questionable, 1 wrong (the domain's evolve/holdout question overlap), 0 unverifiable.

## 2. `offline_harnessworld`: HarnessWorld, simulated policy, parametric mock LLM

**Setup.**
- **Domain.** `make_domain(seed=0)`: 100 evolve, 100 holdout and 100 OOD tasks (OOD uses shifted families), 16 rubric criteria each.
- **Seed.** `HarnessWorldDomain.seed_artifact()`: `harness.md` plus two generic mechanisms (`gen_09`, `gen_11`).
- **Policy.** Simulated STRONG policy.
- **LLM.** `HarnessWorldMockLLM` plays proposer, critic (catch rate 0.8 on obfuscated leaks), digesters and analyst, so the *LLM analyst path* is exercised.
- **Config.** T = 10, m = 2, k = 2, defaults otherwise.
- **Ground truth.** Analytic E[S] and E[C] for every evaluated candidate are recorded in the trace as report-only data.

**Spend.** $0. 141 search-LLM calls, 4,000 rollouts, 1.2 s.

**Seed vs final.** Transfer at k = 2; the last column is the analytic ground truth.

| split | H0 | final | paired diff [95% CI] | analytic E[S], H0 → final |
|---|---|---|---|---|
| evolve | 0.472 | 0.528 | +0.057 [+0.036, +0.078] | 0.464 → 0.519 |
| holdout | 0.527 | 0.587 | +0.060 [+0.037, +0.083] | 0.528 → 0.581 |
| ood | 0.402 | 0.424 | +0.022 [−0.003, +0.046] | 0.403 → 0.410 |

E[C] per trial: 2,145 → 3,452 (×1.61).

**What the loop did.** δ = 0.0159 (bootstrap, k = 2, 100 tasks).

- **r0 to r3.**
  - All 7 evaluated candidates were *truly* worse, with true dE[S] on evolve between −0.006 and −0.036. All 7 were rejected: 4 by the floor, 3 by the shaped rule.
  - Leaks: over the whole run the precheck rejected 7 drafts with literal answer tables (r0A, r1B, r2A, r3B, r4B, r6A, r8B) and the scripted LLM critic rejected 1 obfuscated hash-keyed table (r9A, `obf_00`). Every one was removed by a repair or dropped, and none was ever evaluated.
  - B_t grows with every tried component that has not produced a gain. By r3 it lists 7 of 9 components, most with empty machinery lists (spec §8.3).
  - **r3B wasted 5 proposer calls:**
    1. It filled its reserved slot (U_t = {client_tool, skill}) with a *leaky* skill.
    2. The precheck rejected it.
    3. The repair removed the leak, and with it the only U_t edit.
    4. The done() contract then bounced the draft 4 times. The mock repair does not re-read the bounce log.
    5. The variant was dropped as `critic_reject`.
- **r4** (σ = 1, B reserved).
  - r4A = costly prompt + generic context + decaying context. S = 0.4963, dS = +0.031 > δ, dC = +0.53 ≤ 1.34, so admissible.
  - r4B = structured search tool (the reserved slot, honoured). S = 0.4947, dS = +0.029, dC = +0.025, so admissible.
  - argmax S' keeps **r4A**, by 0.0016 (0.1·δ).
  - Ground truth: r4A is +0.027 on evolve and **+0.016 on OOD at ×1.55 tokens**. r4B was +0.021 on evolve and **+0.041 on OOD at ×1.06 tokens**. The step is correct under the rule ("argmax S' among admissible, not the shaped score", spec §4.3), but on noise the rule chose the dearer candidate that transfers worse.
- **r6.** r6B, the *narrow* mechanism `nar_02` (practice-only by construction), was **kept**: dS = +0.027, dC = +0.035.
  - Truth: evolve +0.028, holdout +0.028, **OOD −0.010**.
  - Nothing in RRSI can see this. The floor and the cost rule read evolve only, and the critic has no leak to find.
- **r5, r7, r8, r9.** All rejections were truly worse candidates:
  - r8B is a null mechanism (true +0.0002) that costs +1.8% tokens; the shaped rule rejected it.
  - r9A is a prune of `dec_00` (true −0.020); the floor rejected it (0.5056 < 0.5069).

**Decision quality against ground truth.**
- 19 evaluated candidates.
- All 17 rejected candidates were truly worse or null on evolve.
- Both kept candidates were truly better on evolve; one of them is worse on OOD.

**Audit.** 350 pass, 0 fail, 0 unverifiable, 6 info (4 done() bounces and 2 ground-truth notes on the keeps). Stage-B step check: 301 correct, 10 questionable (the k = 2 bootstrap δ under-covers; B_t lists 1-8 components with no machinery to prune in r1-r9), 0 wrong. The stage-B fixes do not touch this run: a from-scratch re-run on the fixed code gives an identical history.

## 3. `live_agentqa`: AgentQA, Claude Haiku everywhere

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


