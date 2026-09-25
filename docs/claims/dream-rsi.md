# Dream-RSI: claim-by-claim audit of `rsi.dream`

This page checks our implementation against every claim made about Dream-RSI. The claims come from the paper "Dream-RSI: Recursive Self-Improvement through Evolving Worlds" (arXiv 2609.14858, 36 pages, including Appendices A–C) and from the user's overview page. It covers mechanisms, every table and figure number, the qualitative findings and the caveats. It is an audit only: no code was changed.

**Sources used.**
- The paper, full text: `scratchpad/papers/dream-rsi.txt`. Page numbers below are the PDF's printed page numbers. `L1:n` and `L2:n` are line *n* of the prompt Listings 1 and 2 (App. B).
- Our verified spec, `docs/methods/dream-rsi.md`, and the user's overview, `scratchpad/doc.txt` (Dream-RSI section).
- Our code: `rsi/dream/` and `rsi/domains/discovery/`.
- Our evidence:
  - `docs/methods/dream-rsi-impl.md`;
  - `results/dream-rsi/*.json`;
  - `validation/dream-rsi/{AUDIT,RUNS}.md` and the four run directories.
- The official code is **not released**. The repo holds only the README, CITATION.cff, the assets and the PDF (spec §8.16). So "fidelity" below always means fidelity to the paper text, never to reference code.

**New checks run for this audit.** All ran offline with no LLM calls, each in under 5 min of CPU. The scripts are in `scratchpad/claims/`, outside the repo.
- `prompt_cov.py` compares Listing 1 and Listing 2, sentence by sentence, with our `EXPLORATION_PROMPT` and `DEVELOPER_PROMPT`. A targeted phrase check then confirmed each omission reported in §3.
- `arith.py` recomputes every derived number in the paper and the overview: the table averages, the ratios, the win counts, the Fig. 6 totals.
- `raw_vs_norm.py` recomputes every dreaming decision of the four validation runs, using the paper's raw Eq. 1 in place of our per-world normalized Eq. 1.
- `memo_cheater.py` runs a new cheating policy through the static check, the guard and both runners, on the 13 recorded validation worlds.
- `w_check.py` re-runs E3's synthetic arm (20 seeds, both arms) with W = 6 in place of W = 4, which matches the paper's "all workspaces in parallel".
- Extra queries on `results/dream-rsi/e3_dream_vs_fixed.json`: per-round budgets, round-1 identity, and the equal-rounds view.
- `tests/test_dream-rsi_*.py`: 70 passed in 32 s.

## 1. Summary

| verdict | mechanism | quantitative | qualitative | caveat | **total** |
|---|---|---|---|---|---|
| REPRODUCED | 18 | 3 | 1 | 7 | **29** |
| PARTIAL | 11 | 5 | 9 | 0 | **25** |
| NOT REPRODUCED | 0 | 2 | 0 | 0 | **2** |
| NOT TESTABLE HERE | 0 | 21 | 3 | 0 | **24** |
| CONTRADICTED | 0 | 2 | 0 | 1 | **3** |
| **total** | 29 | 33 | 13 | 8 | **83** |

**Mechanisms.** The core replay machinery is faithful to §3 word for word, and it has been re-derived independently:
- tree model, reveal rule, round counting and termination;
- Eq. 1;
- the mean over all worlds;
- argmax selection with the incumbent included.

The 11 PARTIAL mechanisms fall into two groups:
- Documented deviations: history caps, the developer's base version, the objective, the support rule and the guidance mock.
- **Four new mismatches** (§3): a no-peeking hole through module-level memory, a heavily condensed Listing-2 prompt, a per-round budget that Dream may exceed, and W set below the grid width in E3/E6.

**Quantitative.** The paper's numbers need Gemini-3.1-Pro/3.7-Flash, 110–640 agent calls per round, real Lasso datasets and GPUs. None of them can be re-measured here, so 21 of 33 quantitative claims are NOT TESTABLE HERE.

Our CPU analogues support one half of the headline:
- **Reproduced: fewer calls.** At equal rounds Dream uses 1.39–1.73× fewer calls (paper: 1.74×).
- **Not reproduced: better quality at equal rounds.** Dream is never better at equal rounds. On synthetic worlds it is significantly worse (−0.039 [−0.059, −0.021]).
- **Equal budget.** Dream's win on synthetic worlds is matched by a fixed grid tuned in hindsight. On the real CPU tasks the two arms are comparable.

Two claims are CONTRADICTED:
- Fig. 3b's "consistently superior … while requiring substantially lower cumulative compute" (our equal-rounds data and the paper's own Pro curve both disagree).
- The introduction's math claim "matches or surpasses strong baselines". The paper's own Table 1 contradicts it on autocorrelation.

**Qualitative.** Most findings are PARTIAL:
- replay beats advice (synthetic yes, circle packing no);
- pacing (conserving yes; re-expanding only on sum-difference);
- "almost free": replay is free, but in the live runs the LLM developer cost 13–30% of total spend.

**Caveats.** All but one hold. The overview's "RRSI and Dream-RSI both have open code" is contradicted by the official README.

## 2. Claim table

Columns: **claim** | **source** | **type** | **fidelity and code reference** | **our evidence** (at our scale) | **verdict**.
Abbreviations: `q.py` = `rsi/dream/question.py`, `obj.py` = `rsi/dream/objectives.py`, `sel.py` = `rsi/dream/selection.py`, `loop.py` = `rsi/dream/loop.py`, `dev.py` = `rsi/dream/developer.py`, `ev.py` = `rsi/dream/evaluator.py`, `guard.py` = `rsi/dream/guard.py`, `agent.py` = `rsi/dream/agent.py`. "E*n*" = `results/dream-rsi/e<n>_*.json`.

### 2.1 Mechanisms

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| M1 | Discovery tree rooted at r. Each non-root node has one primary parent and records its snapshot, artifact, diagnostics and score; larger is better. | §3 p.5 | mechanism | Faithful. `DiscoveryNode`/`DiscoveryTree` `tree.py:43-186`; `SnapshotStore` `tree.py:384`; nodes are appended by `OnlineQuestion` `q.py:321-`. Minimization tasks are negated (the spec's convention). | E1: 64/64 replays identical to their live rollouts. AUDIT: 129/129 parent → child diffs rebuilt from snapshots. | REPRODUCED |
| M2 | Eligible set A(T) = {r} ∪ leaves; action = batch C with \|C\| ≤ W. | §3 p.5 | mechanism | Faithful: legal roots + one frontier per opened branch, `q.py:93-111`; ≤ W, `q.py:153`. Several root cells per batch are allowed. The paper's set formalism `C ⊆ A(T)` allows only one r (spec §3.1 gap), but L2:104 allows several, and ours follows L2:104. | `test_batch_legality`; AUDIT: 60/60 online rounds legal. | REPRODUCED |
| M3 | Online transition: every selected node goes to a worker, which resumes its saved workspace and produces one evaluated child. Earlier nodes stay unchanged. Stochastic. Ends on an empty batch or K1 rounds. | §3 p.5 | mechanism | Faithful: thread pool, parent workspace restored, new node appended, `q.py:321-`; K1 = `Config.K1`, `loop.py:59`; empty batch = stop (templates). | E1; AUDIT M3: the child resumes the parent (129/129), and a failed node's child resumes the broken workspace. | REPRODUCED |
| M4 | H_t = H_{t−1} ∪ {T_t}. H_{t−1} "provides context for exploration but remains separate from the new tree". | §3 p.5 | mechanism | The replay pool is complete (`loop.py:293`). The agent's context is capped: last 8 worlds (`loop.py:218`), ≤ 30 records per section (`agent.py:271-276`), proposals clipped to 600 chars (`agent.py:67-72`). These are documented deviations (impl §4.16, AUDIT F6). | AUDIT: 53/53 live agent prompts rebuilt from disk; the history seen was 0 / 12 / 24 records. | PARTIAL |
| M5 | Replay reveal rule: start from {r}. A root pick reveals the earliest-created unrevealed child of r; a leaf pick reveals its unique recorded child; nothing is revealed when no continuation exists. Deterministic; no new outcomes are generated. | §3 p.5–6 | mechanism | Faithful word for word: `ReplayQuestion._transition` `q.py:295-313` (earliest mode maps each requested root to the next unrevealed recorded branch in creation order, `q.py:297-310`); a missing continuation returns `None`, `q.py:192-195`. A `root_mode="addressable"` variant follows the L2 API. | E1 64/64; a second, independent replay written from §3 reproduced 36/36 evaluations (AUDIT §4a M4); tests (e) and (f). | REPRODUCED |
| M6 | Replay ends on an empty batch, k = K2 or T = T_i. Each non-empty batch counts as one round, even when nothing is revealed. N = revealed non-root nodes. | §3 p.6 | mechanism | Faithful: `k += 1` for every non-empty batch, `q.py:188`; done on K, on complete or with no legal cell, `q.py:205`; `N = len(revealed)`, `q.py:145`. Deviation (impl §4.3): an exhausted leaf is closed after one empty probe, while the paper keeps it a selectable leaf. That only stops a policy wasting further rounds on it. | invariant (f) tested; demo exact. | REPRODUCED |
| M7 | "Replay resets the policy's per-rollout state" before every policy × world pair. | §3 p.5 | mechanism | A fresh policy *instance* is created per episode (`guard.py:232`; subprocess `guard.py:300`). **New:** the module and its class attributes are loaded once per evaluator *session*, and `ReplayEvaluator.evaluate` uses one session for every world *and* every sweep beta (`ev.py:195-203`). So module-level state survives between episodes on the same world (§3, N1). | `memo_cheater.py` (§3 N1). | PARTIAL |
| M8 | No peeking: decisions use only the revealed prefix; never unrevealed scores, optima, hard-coded ids, `best_so_far` or `budget_spent`. | L2:9-10, 53-55, 133-139; doc | mechanism | A strong firewall: `PrefixGuard` + subprocess sandbox + AST `static_check` (`guard.py:1-167`), and a reset after probing is a violation. **New hole:** a policy that keeps a module-level dict keyed by `baseline_score` passes `static_check`, is not disqualified in either runner, and walks straight to the recorded best on repeat visits (N1). | E8: both earlier cheaters blocked in 8/8 worlds. New: the memo cheater's `pareto.reward` is +0.2815, against +0.0987 for honest adaptive and +0.0237 for parallel refine (13 worlds, both runners). Eq. 1 is unaffected: +0.5412 against +0.5391. | PARTIAL |
| M9 | Eq. 1: V_i = max s_v − β1·N + β2·N / max(1, k*); the max includes the root. | §3 p.6 | mechanism | Faithful: `Eq1Objective.score_episode` `obj.py:126-129`; the max includes the root (`q.py:214-216`); only successful nodes count, which is the safe reading. Default: per-world normalization (documented deviation); `normalize=False` gives the paper's raw form. The paper reports no β1/β2; ours are 0.01/0.005, from the overview demo. | Demo exact: 0.575 / 0.611 / 0.475. `raw_vs_norm.py`: in 9/9 dreaming phases of the validation runs, raw Eq. 1 selects the same version as the normalized Eq. 1. | REPRODUCED |
| M10 | V^m = (1/t) Σ_i V_i^m over all t worlds of H_t. | §3 p.6 | mechanism | Faithful: `ev.py:219-221`; every version is evaluated on `self.worlds`, `loop.py:359, 377`. | AUDIT M1: each eval covers len(worlds) = t. | REPRODUCED |
| M11 | m* = argmax over versions that include π_t^0, so V^{m*} ≥ V^0 on H_t. | §3 p.6 | mechanism | Faithful: `Selector.select` `sel.py:51-62`; ties keep the incumbent (our choice; the paper is silent). | E4: V*−V0 ≥ 0 in 150/150 phases; without the incumbent 4/75 and 9/75 phases regress (worst −0.32). | REPRODUCED |
| M12 | Dreaming loop: M versions π^0…π^{M−1}. The developer examines the trajectories and scores of π^m plus earlier feedback and revises π^m into π^{m+1}. Each version is evaluated on the same t worlds before the next one is written. | §3 p.5–6; Fig.1–2 | mechanism | `_dream` `loop.py:353-415`. The sequential evaluate-then-revise order is faithful. Deviations: each revision starts from the *strongest* version so far (`dev.py:87-89`; this follows L2:247, not §3's π^m → π^{m+1}); the off-by-one is a flag (`loop.py:60-61`); the developer sees ≤ 6 earlier versions and ≤ 12 trace rows of the base version (`dev.py:372, 380-395`); M = 4–6 against the paper's "massive pool" / "thousands". | AUDIT M16: 27/27 revisions start from the strongest version. | PARTIAL |
| M13 | Only the exploration-policy code changes; models, evaluator and interfaces stay fixed. | §3 p.4; doc | mechanism | Faithful: the agent and task are built once per run; only `method.py` is swapped (`loop.py:262-276`). | the validation runs' traces | REPRODUCED |
| M14 | Policy API: `reset`, `observed`, `legal_actions`, `legal_roots`, `opened_branches`, `meta`, `probe_batch`, `baseline_score`, `max_parallelism`; the Observation fields; the four helper signals; `LLMDesignedMethod`, `SimResult`, `_budget_done`, `_record_curve`, `finalize_result`, `GridPlan`, `GridPlanningContext`. | L2:28-45, 110-127, 194-197 | mechanism | Faithful. `rsi/dream/policy_api.py` implements all of them; the module is named `policy_api`, not `see.policy.api`. | all experiments | REPRODUCED |
| M15 | Batch legality: distinct cells, legal before the call, several roots and/or one frontier per opened branch, never a parent with its child, ≤ max_parallelism cells. | L2:103-105, 142-143 | mechanism | Faithful: `validate_batch` `q.py:149-167`; an illegal batch disqualifies the episode. | `test_batch_legality` | REPRODUCED |
| M16 | Success semantics: `error is None` and `fail_class=="ok"` is success even when `valid` is false. | L2:47-51 | mechanism | Faithful in the API (`policy_api.py:65`, `is_repairable` `:204`). The prompt drops "or n_valid/n_total are unavailable" (N2). | AUDIT: success semantics hand-checked | REPRODUCED |
| M17 | beta: one scalar read in `__init__`, routed through `_schedule(beta)`, fixed within an episode; high beta = more width and patience. | L2:145-159 | mechanism | Faithful in the `adaptive` template and the prompt. | E7: ρ(beta, probes) = +0.96, ρ(beta, attainment) = +0.92. | REPRODUCED |
| M18 | Offline beta sweep ranked by `pareto.reward = pareto.auc − λ·parallel_penalty`; parallel_penalty = mean of effective_sequential_rounds / total_probes, where a batch costs ceil(k/W). | L2:12-22, 160-162 | mechanism | `ParetoSweepObjective` `obj.py:141-188`; `eff_rounds` `obj.py:80-81`. The AUC definition, λ = 0.1 and the grid {0.2…1.0} are inferred (the paper gives none). All experiments select with Eq. 1; the paper never says which objective produced its results (spec §8.15). New: the memo cheater games this objective (N1). | E7: Eq. 1 and the Pareto reward rank policies almost independently (ρ = −0.06). | PARTIAL |
| M19 | Cross-cycle default-beta rule over the last 2–3 live manifests and the sweeps: improving → keep; plateau and higher beta helps → +0.1–0.2 (clamped); high default through a plateau → lower; otherwise about 0.6. | L2:163-188 | mechanism | Coded as described: `choose_default_beta` `dev.py:112-143` (step +0.15 / −0.1, clamp, 0.6). Minor: every unmatched case falls through to 0.6. | AUDIT M11 hand-checked; it fires in only 2/20 synthetic, 3/10 sum-diff, 4/5 circle-packing and 0/4 Lasso E3 runs. | REPRODUCED |
| M20 | `plan_grid`: deterministic, runs before the live grid, reads only earlier manifests, returns a GridPlan on every path, width/depth rules, caps validated; out-of-support replay plans "cannot earn replay reward". | L2:190-242 | mechanism | `adaptive.py:87-112`; `ev.py:144-161`; support intersection `q.py:255-266`. Deviations: the default `support="clip"`, not `"no_reward"` (impl §4.2); a single manifest is labelled "still improving" (AUDIT open issue 3). | AUDIT M10: 13/13 plans re-executed. E2: clipping over-estimates out-of-support plans by +0.14; `no_reward` restores the ranking (0.91 → 0.93). | PARTIAL |
| M21 | "Dream-RSI maintains identical per-round budgets" (110 or 640). | §4 p.7 | mechanism | **New mismatch (N3):** Dream's `plan_grid` may widen or deepen up to `hard_max` 12 × 12 (`loop.py:56-57`), far above the fixed grid (5 × 4 or 6 × 5 cells). Only the total call budget is shared (`max_calls`). | E3 raw: 182 of 541 Dream round plans exceed Fixed's per-round grid (synthetic 110/380, sum-diff 44/84, circle packing 24/47, Lasso 4/30). 3 synthetic rounds actually spent 34 calls against Fixed's 30. | PARTIAL |
| M22 | π₁ = "parallel refining": several workspaces launched in parallel, each refining its own trajectory. | §4 p.7 | mechanism | `parallel_refine.py` (all roots, then deepen all, ≤ W per batch). **New (N4):** E3/E6 use W = 4 on 5–6-branch grids, so π₁ cannot run all workspaces in parallel. Also the Listing-1 agent reads all siblings, not only its own workspace (a paper tension, spec §6.1). | `w_check.py`: with W = 6 the E3 synthetic result is unchanged (Fixed 0.857 → Dream 0.912 at W = 4, and 0.857 → 0.912 at W = 6; 20/20 wins both). The mismatch is harmless for this verdict. | PARTIAL |
| M23 | Both arms behave identically in round 1. | §4 p.7, §4.1 p.8 | mechanism | Faithful: same π₁, seed, agent and grid. | E3 raw: round-1 calls and best identical in 35/35 seeds on synthetic, sum-diff and circle packing. Lasso: 4/4 same calls, best differs by CPU-timing noise only. | REPRODUCED |
| M24 | Cost = cumulative discovery-agent calls. | §4 p.7 | mechanism | Faithful: `CostMeter` `cost.py:18-70`; replay episodes are metered separately. | AUDIT: meter = tree sizes in 4/4 runs; replay adds 0 agent calls. | REPRODUCED |
| M25 | Listing 1 exploration prompt. | App.B.1 L1:1-28 | mechanism | `EXPLORATION_PROMPT` `agent.py:220-263`, adapted to a single completion. Steps 2–4 are verbatim apart from small cuts. Omissions and inconsistencies (N5): the "pkill/kill" safety line; "not just recent cycles or the current branch"; "Look at the shape of what's been tried"; "(not just guessed from the proposal)"; the baseline is shown as a score only (L1 reads `$baseline_dir` proposals); the prompt says "in full, not a sample" while the code caps and clips the history. | AUDIT: 53/53 prompts rebuilt; 17/17 compile after the parser fix. | PARTIAL |
| M26 | Listing 2 policy-development prompt. | App.B.2 L2:1-273 | mechanism | `DEVELOPER_PROMPT` `dev.py:258-350` + `objective_text` `dev.py:353-362`. **New (N2):** it is a condensed paraphrase. About 20 substantive rules of L2 are missing, including the anti-over-pruning constraints, the portfolio-level stop, the recovery-slot rules, three beta-rule safeguards, half the `plan_grid` rules and the final self-verification checklist. The earlier stage-B audit called it "faithful" (AUDIT §4a M14). | `prompt_cov.py` + phrase check (§3 N2). Two small live runs: all six haiku policies tied π₁ in stage A; one haiku policy was deployed in `sumdiff_live_b`. | PARTIAL |
| M27 | Guidance ablation: past trajectories are abstracted into "high-level directional insights" injected into the prompt. | §5.1 p.11 | mechanism | `LLMGuidanceSummarizer` / `MockGuidanceSummarizer` `baselines.py:53-105` → `$direction_guidance`. Offline, the mock agent ignores the text, so the effect is built in: advised directions get root assignments with probability 0.8 (`agent.py:407-422`). | E5 is a mechanism check (impl §4.15). | PARTIAL |
| M28 | Replay reads only really-measured evaluator outcomes; no LLM judge. | §3; doc "The grader decides everything" | mechanism | Faithful: `ReplayQuestion` only reveals recorded nodes; the objectives are numeric. | – | REPRODUCED |
| M29 | Online and replay share one decision interface and differ only in the transition. | §3 p.5; doc table | mechanism | Faithful: `GridQuestion` base with `ReplayQuestion` / `OnlineQuestion` subclasses (`q.py:37-313`). | E1: outcome "random vs fixed", 0 calls in replay. | REPRODUCED |

### 2.2 Quantitative claims

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| Q1 | sklearn row: 11275.2 / 252881.7 / 93.8 / 227.2 / 229.8 / 374.0; avg 44180.3 ms | Fig.3a p.8 | quantitative | Our held-out re-measurement includes a `lasso_path` reference row (`LassoPathDomain.reference_artifact`). | Our 2 synthetic held-out instances only: sklearn 3.3 ms (geometric mean). The six real datasets are not available offline. | NOT TESTABLE HERE |
| Q2 | glmnet row (avg 13767.5 ms) | Fig.3a | quantitative | no glmnet (R/Fortran) | – | NOT TESTABLE HERE |
| Q3 | SimpleTES and SimpleTES† rows (gpt-oss-120b, 51,200 generations; avg 3804.8 / 8318.4) | Fig.3a | quantitative | no SimpleTES | – | NOT TESTABLE HERE |
| Q4 | Recursive Fixed, Gemini-3.1-Pro: 550 calls, avg 3587.1 ms | Fig.3a; §4.1 | quantitative | the arm is `Config(dream=False)` | Scale and model unavailable. Our analogue: Fixed ≈ 16.0 ms geometric mean on our instances. | NOT TESTABLE HERE |
| Q5 | Recursive Fixed, Gemini-3.7-Flash: 3200 calls, avg 2516.7 ms | Fig.3a | quantitative | same | – | NOT TESTABLE HERE |
| Q6 | Dream-RSI, 3.1-Pro: **317 calls**, avg **2931.0 ms** | Fig.3a; §4.1 | quantitative | – | – | NOT TESTABLE HERE |
| Q7 | Dream-RSI, 3.7-Flash: **1879 calls**, avg **2350.6 ms** | Fig.3a | quantitative | – | – | NOT TESTABLE HERE |
| Q8 | The Avg. column is the mean of the six datasets. | Fig.3a | quantitative (internal) | – | `arith.py`: all 8 rows recompute to within ±0.03 ms. | REPRODUCED |
| Q9 | 3587.1 → 2931.0 ms, i.e. **1.22×** faster (README) | §4.1; README | quantitative | – | 3587.1/2931.0 = 1.224 ✓. Our analogue (Lasso, equal rounds): search score +4.8 [−2.2, +15.1] (≈1.05×, n.s.); held-out −2.0 [−5.7, +2.2] 1/s. | NOT TESTABLE HERE |
| Q10 | **317 vs 550** calls: "1.7×" (§1) / **1.74×** (README) fewer; −42% | §1 p.3; §4.1; doc headline | quantitative | Our loop and meter reproduce the measurement (calls at equal rounds). | 550/317 = 1.735 ✓. Our equal-rounds (5) call ratios Fixed/Dream: synthetic **1.73** [1.60, 1.87], sum-diff 1.57, circle packing 1.65, Lasso 1.39. Quality is *not* better at those rounds (Q15). | PARTIAL |
| Q11 | Flash: "**41% fewer calls**" (1879 vs 3200), better on 5/6 datasets | §4.1; doc | quantitative | – | 1 − 1879/3200 = 41.3% ✓. Ours at equal rounds: −40% synthetic, −34% sum-diff, −39% circle packing, −25% Lasso. Win analogue: Dream faster than Fixed on 4/8 (seed, instance) held-out pairs. | PARTIAL |
| Q12 | "up to **162×** fewer agent calls than SimpleTES" | §1 p.3; README | quantitative | – | 51200/317 = 161.5 ✓ (arithmetic). Needs SimpleTES + gpt-oss. | NOT TESTABLE HERE |
| Q13 | The discovered solvers "outperform the standard sklearn and glmnet implementations on all six held-out datasets" | §4.1 p.8; doc | quantitative | The Lasso domain has the App. A correctness gate (`F_k ≤ F_k(sklearn) + 1e-6` on fresh instances), `lasso.py`. | The table supports it: 6/6 for all four rows (`arith.py`). Ours: **0/16** final programs beat sklearn's `lasso_path` (the mock agent writes numpy solvers; sklearn is compiled). This is agent-capability-bound, not a method test. | NOT REPRODUCED |
| Q14 | Dream achieves lower average runtime than SimpleTES with ~2 orders of magnitude fewer calls | §4.1 p.8 | quantitative | – | True on the mean; per dataset SimpleTES is faster on 4/6 (all biological), `arith.py`. | NOT TESTABLE HERE |
| Q15 | Fig. 3b: the two trajectories "diverge markedly: Dream-RSI consistently achieves superior downstream performance while requiring substantially lower cumulative compute" | §4.1 p.8 | quantitative | Our E3 reports the paper-style equal-rounds view. | The paper's own Fig. 3b (spec §7.1, digitized): Pro is *worse* than Fixed in rounds 2–4. Ours at equal rounds: synthetic −0.039 [−0.059, −0.021] (Dream worse, 20 seeds); sum-diff −0.0013 [−0.0048, +0.0013]; circle packing −0.0006 [−0.0014, 0.0]; Lasso +4.8 [−2.2, +15.1]. Never significantly better. | CONTRADICTED |
| Q16 | Sum–Difference: Dream **1.145427** > Fixed 1.144047 > SimpleTES 1.143975 | Table 1 p.9 | quantitative | `sumdiff.py`: Γ exactly as App. A Problem 2 | Our sets reach Γ ≈ 1.02–1.07 (small mock construction); the absolute value is not testable. | NOT TESTABLE HERE |
| Q17 | Circle packing: **2.635983** (ties the best reported) | Table 1 | quantitative | `circlepack.py`: n = 26 only (App. A also lists n = 32) | Ours ≈ 2.631 (SLSQP). | NOT TESTABLE HERE |
| Q18 | Autocorrelation: **1.456375** (competitive) | Table 1 | quantitative | **Not implemented.** No autocorrelation domain exists; App. A Problems 4a–c are CPU-cheap. | – | NOT REPRODUCED |
| Q19 | Rows for other systems (AlphaEvolve, AlphaEvolveV2, OpenEvolve, CodeEvolve, ShinkaEvolve, TTS-Discovery, ThetaEvolve, EvoX) | Table 1 | quantitative | literature numbers | – | NOT TESTABLE HERE |
| Q20 | Math, direction of the result: Dream beats Fixed on sum-diff and ties on circle packing (autocorrelation slightly worse) | Table 1; §4.2 | quantitative | same agent, evaluator and budget in both arms | E3 at equal budget: sum-diff +0.0002 [−0.0033, +0.0028] (non-inferior, not better); circle packing −0.0006 [−0.0014, 0.0] (tie, as in the paper); autocorrelation absent. | PARTIAL |
| Q21 | Math within "1k generations", ">50× budget savings" vs SimpleTES | §1 p.3; §4.2 p.10 | quantitative | – | 51200/1000 = 51.2 ✓. Dream's math call counts are not reported. Note: Fixed's stated budget, 10 rounds × 110, is 1100 > 1k. | NOT TESTABLE HERE |
| Q22 | Math "matches or surpasses strong baselines within 1k generations" | §1 p.3 | quantitative | – | Table 1 contradicts it on autocorrelation (lower is better): Dream 1.456375 > SimpleTES 1.453675 and > AlphaEvolve 1.455700 (`arith.py`). | CONTRADICTED |
| Q23 | VGG16: comparable performance with **2.43×** fewer generations | Fig.4; §4.3 | quantitative | no GPU / KernelBench | 990/408 = 2.43 (spec digitization: Dream ends slightly below Fixed). | NOT TESTABLE HERE |
| Q24 | LayerNorm: **1.79×** fewer generations | Fig.4 | quantitative | no GPU | 990/553 = 1.79 | NOT TESTABLE HERE |
| Q25 | ConvDiv: **2.09×** higher performance at a similar budget | Fig.4 | quantitative | no GPU | 1.898/0.909 = 2.09 | NOT TESTABLE HERE |
| Q26 | ConvMax: **1.44×** higher | Fig.4 | quantitative | no GPU | 0.431/0.299 = 1.44 | NOT TESTABLE HERE |
| Q27 | Fig. 6 ConvDiv numbers: attempts 110, 110, 87, 80, 50, 92, 80, 91, 86; round best 0.427 → 1.898 | Fig.6 p.11 | quantitative | – | Sum = 786, which matches Fig. 4's last Dream point; the round best rises every round (`arith.py`). The qualitative analogue is L7. | NOT TESTABLE HERE |
| Q28 | Fig. 5 guidance-ablation curves | Fig.5 | quantitative | – | the qualitative analogue is L5 | NOT TESTABLE HERE |
| Q29 | Budget accounting: 10 × 11 = 110 and 32 × 20 = 640 calls per round; the fixed arm spends its full grid every round (5 rounds → 550 / 3200) | §4 p.7 | quantitative | `parallel_refine.py` always uses the fallback grid; the meter counts agent calls | E3: Fixed spends exactly its grid in every round of every seed (20 or 30 calls). AUDIT: meter = tree sizes. | REPRODUCED |
| Q30 | "8 scientific discovery tasks spanning three domains" | §1 p.3; doc | quantitative | domains: Lasso, sum-diff, circle packing n = 26 (+ synthetic worlds, AgentQA, FunctionDomain) | 3 of the 8 tasks have a CPU analogue; no autocorrelation, no circle packing n = 32, no kernels. | PARTIAL |
| Q31 | Lasso setup: the 17 SimpleTES synthetic instances, 6 held-out datasets, 5 rounds | §4.1 p.7 | quantitative | `lasso.py`: own CPU-timed synthetic instances, 2 held-out instances, 5 rounds, correctness gate on fresh instances (App. A) | E3 Lasso, 4 seeds | PARTIAL |
| Q32 | The App. C solver (strong rules + Cauchy–Schwarz KKT pruning, lazy Gram, C++/Eigen/OpenMP) is correct and fast | §4.1 p.9; App.C | quantitative | not compiled here | Eigen headers are not installed (checked `/usr/include/eigen3`); no network install was attempted. | NOT TESTABLE HERE |
| Q33 | Overview reading of the table: with Pro, Dream is slower on 5/6 and its better average comes "almost entirely from RCV1"; Flash wins 5/6; Dream-Pro's geometric mean is worse than Fixed-Pro's | doc; spec §7.1 | quantitative (reading) | – | `arith.py`: Dream-Pro faster on 1/6, Flash on 5/6; mean without RCV1 is 594.0 (Dream-Pro) vs 394.5 (Fixed-Pro); geometric mean 179.4 vs 159.1. | REPRODUCED |

### 2.3 Qualitative findings

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| L1 | A finished discovery history is a replay simulator that evaluates alternative policies cheaply and usefully (off-policy). | §1–2, Fig.2 | qualitative | faithful machinery (M5–M10) | E2: Spearman(replay, true online value) = 0.92 in support; out of support it is mis-estimated (+0.14 with clipping); context coupling lowers ρ to 0.80. AUDIT: at t = 1 (one world) replay preferred a frugal policy that found significantly less online (−0.0028 [−0.0046, −0.0012], 40 searches). | PARTIAL |
| L2 | "zero-execution-cost off-policy evaluations"; "negligible execution cost"; overview: "almost for free between real runs" | Fig.2; §7; doc | qualitative | `CostMeter` separates agent, replay and developer spend | Replay: 0 agent calls, 2.7 ms per episode in-process (E1). **But** the LLM developer, which the paper leaves unquantified, cost 13% ($0.093/$0.71, live smoke), 30% ($0.876/$2.906, stage A) and 27% ($0.340/$1.278, `sumdiff_live_b`) of the total spend. | PARTIAL |
| L3 | "Thousands of candidate policies can then be tested"; "dreams up a massive pool of alternative policies" | Fig.1–2 captions | qualitative | the throughput exists; our M is small | E3 synthetic: ≈2,200 replay episodes (≈4 s of CPU) per run; per phase we use M = 4–6 versions (E9 goes up to 32). No run tests thousands of LLM-written versions. | PARTIAL |
| L4 | Dream "achieves competitive or improved discovery quality while substantially reducing discovery cost in several settings"; adapting the policy across rounds improves efficiency and effectiveness | Abstract; §4.3; §7 | qualitative | – | E3: fewer calls everywhere. At equal budget it is better only on synthetic worlds, where a hindsight-tuned 3 × 3 fixed grid ties it (+0.0006 [−0.0095, +0.0107]). Sum-diff and circle packing: comparable. Lasso: inconclusive. | PARTIAL |
| L5 | "Replay beats advice": written guidance underperforms no guidance, for both Fixed and Dream | §5.1, Fig.5; doc | qualitative | M27 (the mock's guidance bias is built in) | E5 synthetic: −0.056 (Fixed) and −0.058 (Dream), both CIs below 0. Circle packing (n = 3): guidance is marginally *better*, +0.0003 / +0.0022. | PARTIAL |
| L6 | Guidance "over-constrain[s] the search space and impede[s] diverse exploration" | §5.1 | qualitative | – | E5: the diversity of directions per round falls from 1.00 to 0.47–0.58 in both domains. In the mock this happens by construction (the advised directions get 80% of the root assignments). | PARTIAL |
| L7 | Pacing: effort falls while performance improves (110 → 50) and rises again after a plateau; "the best kernel kept improving each round" | §5.2, Fig.6; doc | qualitative | effort comes from `plan_grid` and PARAMS edits, logged in the manifests | E6: conserving is reproduced (17 vs 30 calls on synthetic, 12 vs 20 on sum-diff). Re-expansion after a plateau: sum-diff +1.4 [+0.5, +2.4] (7 seeds); synthetic +1.3 [−0.2, +2.7], not significant. The default-beta rule rarely drives it. | PARTIAL |
| L8 | Fig. 6 reading: the round-best kernel improved every round | doc | qualitative | – | True from the paper's own labels (`arith.py`); kernels cannot be run here. | NOT TESTABLE HERE |
| L9 | "Works across domains": numerical code, math constructions, GPU kernels | §1, §4.3; doc | qualitative | domain-agnostic plug-ins: `DomainTask`, `ProgramDomain` | Runs on synthetic worlds, sum-diff, circle packing, Lasso, AgentQA and two new `FunctionDomain`s (tests). No GPU domain. | PARTIAL |
| L10 | "puts quality, spending and parallelism into one explicit objective, with a dial the strategy tunes across cycles" | doc | qualitative | Eq. 1 + policy beta + cross-cycle rule | E7: β1 → probes ρ = −0.74; β2 changes the argmax only from β2 ≥ 0.1 (the doc's 0.005 changes nothing); the beta dial is monotone (ρ = +0.96). The cross-cycle rule fires rarely (2/20). | PARTIAL |
| L11 | History is an executable simulator, not notes or training data; it improves *how* the system searches; model weights never change | §1, §6; doc | qualitative | faithful (M13, M28, M29) | the validation traces | REPRODUCED |
| L12 | The Pro-discovered solver suits large matrices (RCV1); Flash's is general-purpose | §4.1 p.8 | qualitative | – | – | NOT TESTABLE HERE |
| L13 | "Dream-RSI generalize[s] well on mathematics optimization" | §4.2 p.10 | qualitative | – | CPU analogue: non-inferior, not better (Q20). | NOT TESTABLE HERE |

### 2.4 Caveats

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| C1 | Replay can only test choices that were tried; out-of-support plans cannot earn reward | doc; L2:210-212; §3 "no outcomes beyond T_i" | caveat | support fields, `support="clip"/"no_reward"` (`q.py:255-266`, `obj.py:116-118`) | E10: the go-deeper policy's error is −0.013 out of support and 0.000 in support. E2: +0.14 with clipping. | REPRODUCED |
| C2 | Picking the best score on past searches can overfit; RRSI-style guards (held-out worlds, a noise margin) would help | doc | caveat | `GuardedSelector` `sel.py:65-119` (an extension) | E9: the dev − fresh gap grows with M, +0.070 → +0.175 (t = 1). The guard narrows it (t = 6: +0.063 vs +0.094) but gives no significant fresh-world gain. | REPRODUCED |
| C3 | The "162×" headline compares different models; the fair comparison is against Fixed (1.7×) | doc | caveat | – | gpt-oss-120b vs Gemini (Fig. 3a) ✓ | REPRODUCED |
| C4 | Single runs without error bars; some gains are small | doc | caveat | our harness reports multi-seed CIs | The paper shows no CIs. Math gaps against Fixed: +0.00138 (sum-diff) and −0.00037 (autocorrelation). Our small-gain arms mostly span 0 (Lasso, sum-diff). | REPRODUCED |
| C5 | The selection guarantee V^{m*} ≥ V^0 holds "in average replay score on the fixed history" only | §3 p.6 | caveat | – | AUDIT: the replay-selected r0001 did worse online (−0.0028 [−0.0046, −0.0012]). E4: the online effect is negligible. | REPRODUCED |
| C6 | Autocorrelation is slightly worse than Fixed and SimpleTES; "in several settings" is a hedge | doc; Abstract | caveat | – | 1.456375 > 1.456001 and > 1.453675 (lower is better) ✓ | REPRODUCED |
| C7 | "RRSI and Dream-RSI both have open code" | doc | caveat | – | The official README lists "Full codebase ⏳ Being prepared" (spec §8.16, checked on 2026-09-24; not re-checked here, network blocked). | CONTRADICTED |
| C8 | "Held-out datasets in one domain; selection can still overfit" | doc | caveat | Lasso `holdout` split only | Same in ours: held-out re-measurement exists only for Lasso. | REPRODUCED |

## 3. Newly found mismatches (not in the spec, the impl notes or AUDIT.md)

**N1. The no-peeking guard can be bypassed with module-level memory across episodes (M7, M8, M18).** This is a real bug.
- **Root cause.**
  - `ReplayEvaluator.evaluate` opens one runner session per policy (`ev.py:195`).
  - It replays every world with the default beta, and then replays *the same worlds again* for every sweep beta, all in that one session (`ev.py:197-203`).
  - Each episode gets a fresh instance (`guard.py:232`, `guard.py:300`), but the module and its class objects persist.
- **The cheater.** `scratchpad/claims/memo_cheater.py` defines a policy that uses only the public API. On its first visit to a world it runs parallel refine and stores the best cell's (branch, attempt) in a module-level `_MEMO` keyed by `question.baseline_score`. On later visits it opens only the roots it needs and deepens only the winning branch.
- **Result.**
  - It passes `static_check` and is never disqualified, in-process or in the subprocess sandbox.
  - On the 13 recorded validation worlds its Pareto sweep reads attainment 0.608 at a probe fraction of 0.445 for every beta. Its `pareto.reward` is **+0.2815**, against +0.0987 for the honest adaptive policy and +0.0237 for parallel refine. It would win selection under `objective="pareto"`.
  - Under Eq. 1 (the default and all experiments) its value is not inflated: +0.5412, against +0.5391 for parallel refine. The main pass visits each world once, before the sweep. But `beta_sweep.json`, and hence the default-beta rule and the developer's feedback, are corrupted.
- **Paper text violated.** "replay resets the policy's per-rollout state" (§3 p.5).
- **Possible fixes.**
  - Fresh session per (world, beta) episode.
  - Or reload the module per episode.
  - Or have `static_check` reject module-level mutable containers, `global`/`nonlocal`, class-attribute mutation and `functools.lru_cache`/`cache` decorators.
- **Scope.** The earlier reviews caught the `reset()` variant of this attack (`peek_reset`) but not this one.

**N2. The Listing-2 developer prompt is a condensed paraphrase, not the paper's prompt (M26).**
- **Method.** A sentence coverage check (`prompt_cov.py`), then targeted phrase checks.
- **L2 rules with no counterpart in `DEVELOPER_PROMPT` (`dev.py:258-350`):**
  - *Anti-over-pruning* (L2:136-139): "Every prune, widen, deepen, batch, and stop decision must be explainable from the current prefix. Shallow weak scores are not enough to discard a branch: deeper attempts can recover. A repairable latest failure must not erase its historical successful anchor or by itself cause permanent starvation." The Deliverable's docstring list still names "permanent starvation" safeguards, but the constraint itself is gone.
  - *Failure interpretation* (L2:65-72): "Output/correctness mismatch, shared-memory/resource limits, and variable/code, mask/layout/shape errors are normally repairable. Do not infer algorithmic failure from one such error." Also: "`n_valid == 0` and `branch_failed_hard(obs)` are signals, not unconditional closure" and "`compile_other` alone is not permanently hard". (Ours only lists `compile_other` among the repairable classes.)
  - *Queues* (L2:87-88): "A repairable failure retains eligibility unless cumulative evidence lowers its relative priority."
  - *Portfolio* (L2:92-97): "give exploration and justified recovery representation before filling remaining slots by priority; adapt this to prefix evidence rather than fixed quotas. Recovery must not displace normal successful refinements or leave workers idle."
  - *Portfolio-level stop* (L2:99-101): "Do not stop while an eligible high-priority recovery or underexplored candidate remains; every remaining action needs an evidence-based decision to continue, reserve, or close." Ours keeps only "Stop … only after considering the whole revealed portfolio".
  - "adapt batch composition after every revealed prefix" (L2:106).
  - *Beta* (L2:151-188):
    - the three-roles framing;
    - "Route recovery eligibility, reserve threshold, and waiting through the same schedule";
    - "Scores alone do not establish that beta caused a change, so always use both sources";
    - "clamped to [0, 1]";
    - "rather than pretending the replay ceiling is a live stopping signal";
    - "The beta sweep is non-degenerate only if … It also reveals whether the policy batches";
    - "**Do not select the default simply as the smallest beta that reaches a frozen trace's known ceiling**". This is the paper's own guard against the frugality bias that AUDIT observed.
  - *plan_grid* (L2:199-242):
    - "must never inspect a current episode's outcomes";
    - "do not inherit the template stub and do not delegate grid choice to the runner's fallback";
    - the GridPlan semantics ("creates branches 0..W−1 and attempts 0..R; R is the number of refinements");
    - "Do not read raw trace outcomes or a current cycle result inside plan_grid";
    - "strongly redundant directions";
    - "state that evidence is insufficient";
    - the division of labour between plan_grid, the direction provider and solve;
    - "**Do not choose roots merely because their branch id is small**";
    - "The runtime grid is the hard bound";
    - "verify that the edited method.py contains an override of plan_grid".
  - *History* (L2:249-263): the `{trace_pool}` rule ("only outside solve(); prefer the manifests").
  - *Deliverable* (L2:271-273): "Before finishing, verify trajectory-based ranking, the stated success semantics, non-automatic zero-valid closure, deterministic recovery competition, and portfolio-level stop."
  - The minimal `solve` skeleton (L2:108-127). The developer gets the API source and the base policy instead.
- **Additions in ours that are not in L2** (harmless): the import allow-list, the reset rule, the explicit fail-class lists and the Eq. 1 objective text.
- **Impact.** Offline experiments are unaffected, because the mock developer edits `PARAMS`. Live LLM-written policies were developed under a weaker prompt. That may be part of why all six stage-A haiku policies probed the full grid, and why one live-b policy closed a branch after two flat results.

**N3. Per-round budgets are not held identical (M21).**
- **Paper:** "Dream-RSI maintains identical per-round budgets" (§4 p.7).
- **Ours:** Dream's `plan_grid` may grow the grid up to `hard_max` 12 × 12 (`loop.py:56-57`), while Fixed runs 5 × 4 or 6 × 5.
- **E3:** 182 of 541 Dream round plans exceed Fixed's per-round grid, and 3 synthetic rounds spent 34 calls against Fixed's 30.
- **Effect:** small, because equal *total* budgets are enforced. But it is a protocol difference from the paper, and it could favour Dream once plateaus trigger widening.
- **Fix:** set `hard_max` to the fallback grid in the Dream arm.

**N4. E3 and E6 set W (4) below the fixed grid's width (5–6) (M22).** The paper's π₁ runs all workspaces in parallel (10 workspaces with 10 workers, or 32 with 32). Ours staggers the roots over two rounds. `w_check.py` re-ran E3's synthetic arm with W = 6: Fixed 0.857 → Dream 0.912 (20/20 wins), identical to W = 4. So the verdicts stand; the mismatch should still be documented or fixed.

**N5. Listing-1 omissions and a self-contradiction (M25).** `EXPLORATION_PROMPT` (`agent.py:220-263`):
- It drops the "Never execute pkill, kill, killall" line. This matters when `EditorAgent` wraps a shell-capable `AgentEditor`.
- It drops "not just recent cycles or the current branch", "Look at the shape of what's been tried" and "(not just guessed from the proposal)".
- It shows the baseline only as a score, where L1:9 reads the `$baseline_dir` proposals.
- It tells the agent it sees the history "in full, not a sample", while the code shows at most 30 records per section, the last 8 cycles and 600-char proposals. The caps themselves are documented; the false assurance in the prompt is not.

**N6. Paper-internal overclaims not recorded in the spec.**
- (a) The introduction's "In mathematical optimization … it matches or surpasses strong baselines within 1k generations" (§1 p.3) is contradicted by Table 1's autocorrelation column: Dream is worse than SimpleTES and AlphaEvolve.
- (b) "fewer than 1,000 generations" for math, while Fixed's stated budget for 10 rounds is 10 × 110 = 1,100. So the "<1k" can describe only Dream's arm, and Dream's math call counts are never reported.
- (c) The §1 abstract-level "outperforms … strong baselines" on Lasso holds on the arithmetic mean only. SimpleTES is faster on 4/6 datasets for every Dream and Fixed row.

**N7. "Almost for free" leaves out the developer's cost (L2).** The paper and the overview price dreaming at "zero execution cost". In all three of our live runs, the dreaming stage's LLM calls were 13–30% of total spend. That is cheap relative to the agent, but not free, and it grows with M.

**Checked and not a mismatch.**
- Per-world normalization of Eq. 1 did not change any of the 9 recorded dreaming decisions (`raw_vs_norm.py`).
- `CellMeta` never exposes the `seq` or existence of unrevealed cells (`tree.py:182-186`), so no leak there.
- Round 1 is identical across arms (M23).

## 4. What would be needed to fully reproduce

1. **Paper-scale live runs.** This means Gemini-3.1-Pro and Gemini-3.7-Flash (or equivalent frontier agents) through a coding-agent CLI, at 10 × 11 = 110 and 32 × 20 = 640 calls per round, for 5 rounds (Lasso) and 10 rounds (math), with ≥ 3 seeds per arm for CIs. Order of cost: 550–3,200 agent calls per arm per seed, i.e. thousands of dollars and days of wall time. The official code (`see.policy.*`, direction provider, trace pool) is unreleased, so every number would still come from a reimplementation.
2. **GPU kernel track.** KernelBench VGG16, LayerNorm, ConvDiv and ConvMax need a CUDA GPU, the reference implementations and a correctness checker. That covers Q23–Q28 and L7/L8.
3. **Missing CPU tasks** (cheap; could be done here):
   - autocorrelation inequalities Φ1–Φ3 (App. A Problem 4);
   - circle packing n = 32;
   - the 17 SimpleTES Lasso search instances plus the six real held-out datasets (Gisette, RCV1, DNA, Leukemia, Colon, Duke Breast; download needed);
   - glmnet as a second reference;
   - compiling and verifying the App. C solver (needs Eigen headers).
4. **Fix N1 and N3, then re-run E3, E7 and E8 under both objectives.** Also run the main E3 comparison with `objective="pareto"`: the paper's prompt describes that objective, and it has never been used for a headline experiment here.
5. **Use the verbatim Listing-2 prompt (and Listing 1 with the pkill line)** for the LLM developer, then repeat the live runs with enough cycles (T ≥ 5) and a live ground truth for at least one dreaming decision. Only then can "an LLM developer improves the policy" be separated from "the incumbent is kept on ties".
6. **Test the headline's form directly.** Compare at *equal rounds* (the paper's form: better quality *and* fewer calls) and at equal budgets, against a hindsight-tuned fixed grid, on a real task with a real agent. At our scale, equal rounds gives fewer calls with lower or equal quality.
7. **Validate replay against online value at scale.** Run several recorded policies online on fresh searches and measure the replay-vs-online rank correlation, with and without sibling-context coupling (spec §8.3). The paper never reports this, and our only real-task check (t = 1) went the wrong way.
8. **Settle the unreported hyperparameters.** These are β1, β2, λ, the beta grid, M, K1, K2 and the developer model. They are only available from the authors or the code once it is released.
