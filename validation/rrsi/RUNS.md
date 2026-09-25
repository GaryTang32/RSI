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
```

How to read the tables:
- **S** is the mean exact-match reward (AgentQA) or the fraction of rubric criteria passed (HarnessWorld).
- **C** is mean policy tokens per trial.
- "loop" numbers come from the loop's own measurements.
- "transfer" numbers come from `rsi.core.transfer_report`, which runs H_0 and the final harness unchanged on each split.
- "shadow" numbers come from the write-only `ShadowMonitor`, which scores each new incumbent on holdout and OOD. The loop never sees them.

## 1. `offline_agentqa`: AgentQA, SimModel task model, scripted proposer and critic

**Setup.**
- **Domain.** `make_suite(seed=0)`: 20 evolve and 20 holdout questions from the practice family `numeric`, plus 24 OOD questions (6 each from the never-seen families `dates`, `numbertheory`, `strings`, `lists`).
- **Seed.** `AgentQADomain.seed_artifact()`: `harness.py` makes one direct model call and returns the last line. `system.md` is "You are a helpful assistant." and `task.md` is `{question}`.
- **Models.** The frozen task model is `SimModel`. `AgentQAMockLLM` (scripted) is the proposer and the critic. The analyst is the deterministic heuristic.
- **Config.** T = 8, m = 2, k = 2. Everything else is the paper default: b_min..b_max = 1..4, w = 3, m_draft = 1, β0 = 0.10, β1 = 40, w_s = 100, w_c = 15, w_n = 0.5, n_prune = 4. δ is calibrated.
- **LLM cache.** None. The mocks are deterministic, so there is nothing to cache.

**Spend.** $0 (all mocks), 4.7 s wall time.
- The loop made 1,102 LLM calls (1,065 task, 23 proposer, 14 critic) and 520 rollouts.
- The shadow monitor made 132 more task calls, metered separately.

**Seed vs final.**
- Transfer and paired CI: `rsi.core.transfer_report`, k = 2.
- "Reference" is a k = 10 re-evaluation I ran afterwards to get less noisy numbers.

| split | H0 | final | paired diff [95% CI] | reference, k = 10 (H0 → final) |
|---|---|---|---|---|
| evolve | 0.350 | 0.850 | +0.500 [+0.275, +0.700] | 0.350 → 0.945 |
| holdout | 0.425 | 1.000 | +0.575 [+0.450, +0.700] | 0.390 → 0.940 |
| ood | 0.375 | 0.917 | +0.542 [+0.354, +0.708] | 0.442 → 0.908 |

Tokens per trial: 736 → 135 (−82%). The loop's own measurement is H0 = 0.375 → final incumbent `r0A` = 1.000. This is the same harness that the transfer pass measures at 0.850 on evolve. The δ below is roughly the size of that gap between two samples of one harness.

**What the loop did, round by round.**

- **Setup.**
  - Baseline S = 0.375 (15 of 40 trials correct).
  - δ = 0.147 (bootstrap within tasks, z = 2, sd_null = 0.0736). The audit's plug-in formula gives the same sd_null.
  - Shadow monitor on H0: holdout 0.45, OOD 0.42.
- **r0** (b_t = 4, σ = 0).
  - **Leak blocked.** Both first drafts bundled the *leaky* answer table (`memory/answers.json`, keyed on practice questions, with the answers copied from grader feedback).
    - The denylist precheck missed it: AgentQA answers are 3-digit numbers, below `LeakageCritic`'s 4-character minimum term length.
    - The (scripted) LLM critic rejected both drafts, and one repair per variant removed the table.
  - **Candidates.** r0A = self-consistency + step-by-step + Python tool. r0B = answer format + null reword.
  - **Evaluation.**
    - r0A: S = 1.000, dS = +0.625 > δ, dC = −0.82 ≤ β0 + β1·dS = 25.1, so admissible.
    - r0B: S = 0.500, dS = +0.125 is *inside* the band; shaped score = 100·0.125 − 15·0.024 = +12.1 > 0, so also admissible.
  - **Decision.** argmax S' → **r0A kept**. S* = 1.000. Shadow monitor: holdout 1.00, OOD 0.92.
  - **Two steps that look wrong:**
    - **(a) Phantom credit (mock bug).** The declared self-consistency edit is overwritten by the Python-tool edit, because both rewrite `solve()`. The kept harness has no majority vote, yet L_t records "[aq:self_consistency] … ACCEPTED ΔS +0.625". The scripted critic does not compare the declared edits with the diff; the real critic prompt's rule 2 ("no-op while claiming a mechanism") exists for this.
    - **(b) Winner's curse.** 40/40 is a lucky draw: the harness's true success rate is about 0.945 (k = 10). The loop never re-measures the incumbent (faithful to the code), so S_t = S* = 1.000 for the rest of the run.
- **r1** (b_t = 4). r1A (verify + format + persona + checker sub-call) scored 0.475 with tokens ×15.6. r1B (verify + checker + reword) scored 0.625. Both are below the floor S* − δ = 0.853. That is the right call: the checker sub-call re-answers without the tool and overrides the correct tool answer.
- **r2.**
  - B_t = {subagent}: tried, never improved, and holding no accepted machinery, so its list is empty.
  - Both variants proposed the same harmful Python skill card. S = 0.900, dS = −0.10 (inside the band). Shaped = −10 − 4.6 + 0.5·ν(=1) = −14.1, so rejected.
  - The two evaluations are independent samples: different seeds, different per-task trials, same S by coincidence.
- **r3.** Two single prompt edits (verify, answer format) scored 0.925 and 0.975. Measured against the lucky 1.000, dS < 0. Shaped scores −11.0 and −4.3, both rejected.
- **r4** (b_t = 3).
  - σ_4 = 1 because S_4 − S_1 = 0 ≤ δ. U_t = {tool, memory}, so variant B holds the reserved slot.
  - r4A (answer format) scored 0.95 and was rejected.
  - r4B shipped nothing: the mock's only `memory` idea is the leak, and it has no `tool` idea. The candidate was dropped as `no_proposal`.
- **r5 to r7** (b_t = 2). The same pattern repeats.
  - A's prompt edits (verify, persona, a prune of step-by-step that saved 14% tokens) all measured 0.95 < 1.000, and the shaped rule rejected them (the prune scored −2.9).
  - B lost its slot to the unsatisfiable reservation every round. In r5 that took 4 proposer calls, all bounced by the done() contract.
- **Net.**
  - One acceptance in 8 rounds.
  - The final harness is a real improvement: at k = 10, holdout goes 0.39 → 0.94 and OOD 0.44 → 0.91, and it is about 5× cheaper.
  - After round 0, all 10 evaluated candidates were compared with a ceiling measurement the incumbent does not truly have (2 fell below the floor, 8 failed the shaped rule on dS < 0). None could win on score. This is spec §8.1 ("winner's curse and ratcheting S*") observed directly.

**Audit.** 244 pass, 0 fail, 0 unverifiable, 9 info.
- The 9 info rows are the done() bounces and the empty reserved-slot drafts.
- Re-derived every round: b_t, σ_t, T_t, U_t, the reserved variants, S and S* bookkeeping, Ŝ from raw trials, dS, dC, Algorithm 2, argmax, and the δ formulas.
- No holdout or OOD id or question appears in any proposer or critic input.

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

**Audit.** 350 pass, 0 fail, 0 unverifiable, 6 info (4 done() bounces and 2 ground-truth notes on the keeps).

LIVE_PLACEHOLDER

