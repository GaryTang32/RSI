# RRSI: implementation notes (`rsi.rrsi`, `rsi.domains.harnessworld`)

This page documents how the RRSI spec (`docs/methods/rrsi.md`) is implemented on the shared `rsi.core` API. It covers the module map, the public API, how each capability-checklist item maps to code, an experiment and a result, deviations from the released code (`google-research/rrsi@be50316`), and limitations. All numbers below come from `results/rrsi/*.json`, produced by `experiments/rrsi/*.py` with the default offline settings. CIs are 95% bootstrap CIs over seeds (`rsi.core.stats.summarize_runs` / `paired_diff_ci`).

## 1. Module map

The layout mirrors `google-research/rrsi/rrsi/*.py`. Git worktrees and branches are replaced by content-addressed `rsi.core.ArtifactStore` artifacts plus an `rsi.core.Ledger` tree.

| module | role (paper symbol) | built on |
|---|---|---|
| `rsi/rrsi/config.py` | `Config`: every hyperparameter with the paper/code default, plus `PRESETS` `coding` / `workspace` / `eng` / `overview` taken from `domains/*/rrsi.json` | – |
| `rsi/rrsi/switches.py` | `RegularizerSwitches`: ablation arms (`full`, `no_proposal`, `no_acceptance`, `none` = unregularized, `budget_only`, `selector_only`, `.but(...)`) | – |
| `rsi/rrsi/schedule.py` | `edit_budget`: annealed L0 budget b_t (cosine + ceil; `floor_at_bmin_last` option) | – |
| `rsi/rrsi/components.py` | K, K_str, `Taxonomy.from_domain`, diff-based `normalize` / `classify` (with a `trust_code_tags` fallback for taxonomy-less domains), novelty ν | `Domain.components`, `Domain.structural_components` |
| `rsi/rrsi/history.py` | per-edit JSONL `History` (L_t), `tried` (T_t), `yield_g` (g_t), `prune_set` (B_t), `render`, `stall_flag` (σ_t), `exploration` (E_t, U_t) | – |
| `rsi/rrsi/evaluate.py` | `Measurement` (the code's `eval.json`), `aggregate` (Ŝ, Ĉ, missing = 0 with the full denominator, weights), `Measurer` (per-job trial seeds) | `rsi.core.Evaluator` |
| `rsi/rrsi/calibrate.py` | δ from a within-task bootstrap (reps 2000, seed 7) or from R repeated base evaluations; z = 2 | `rsi.core.stats.noise_from_trials` / `noise_from_repeats` |
| `rsi/rrsi/analyst.py` | `build_traces` (worst trial of the n_fail lowest tasks, best trial of the n_success highest), `Analyst` (LLM digesters + aggregator with the code's prompts and schemas, or a deterministic heuristic), producing the three-lens report F_t | `rsi.core.LLM` |
| `rsi/rrsi/propose.py` | `Proposer`: code's SYSTEM_TMPL and context sections, done() contract (≤ b_t edits, required fields, K, reserved slot, no abort, zero-change bounce), repair mode; `RRSIRewriteEditor`; `parse_sections` helper for scripted mocks | `rsi.core.Editor` (`RewriteEditor`, `AgentEditor`) |
| `rsi/rrsi/critic.py` | `RRSICritic`: denylist/regex precheck over added lines, then an LLM review with the code's six REJECT rules (3 parse attempts, fails closed) | `rsi.core.LeakageCritic` |
| `rsi/rrsi/selection.py` | Algorithm 2 (`build_gates`, `judge`, `select_round`, `cost_rule`), `Candidate`, `Decision`, `IncumbentFloor`, `RRSICostRule` | `rsi.core.gates` (`NoiseFloor`, `CostRule`, `MetricGuard`, `StrictImprovement`, `select`) |
| `rsi/rrsi/attribution.py` | `Scoreboard`: per-edit predicted hits and unpredicted regressions | – |
| `rsi/rrsi/frontier.py` | frontier state (`incumbent`, `S_star`, `trajectory`, frozen config), atomic JSON writes | – |
| `rsi/rrsi/loop.py` | `RRSIRun`: `baseline`, `calibrate`, `round(t)` (Algorithm 1 then 2), `_draft` (propose → critic + repair → tag → smoke), `_evaluate` (invalid → one retry), `truncate`, `readjudicate`, `reevaluate`, held-out monitor | `rsi.core.Ledger`, `ArtifactStore` |
| `rsi/rrsi/driver.py` | `drive` (resume by settled rounds, STOP file, `MAX_CONSECUTIVE_INFRA = 3`, `rsi.core.Budget`), `run(...) -> ImprovementResult`, per-role usage | `rsi.core.run` |
| `rsi/rrsi/transfer.py` | `paired_transfer`: H_0 vs final on evolve / holdout / OOD (paired CI, within-task win/tie/loss, analytic ground truth when available, non-regression flag) | `rsi.core.transfer_report` |
| `rsi/rrsi/constitution.py` | domain-agnostic SKILL.md / PATTERNS.md, with the run's numbers filled in; the reward section follows the active `RegularizerSwitches` (`default_constitution`) | – |
| `rsi/rrsi/mocks.py` | `AgentQAMockLLM`: scripted proposer + critic for `rsi.domains.agentqa` | `rsi.core.MockLLM` |
| `rsi/rrsi/toy.py` | the overview's "overfitting trap" simulator, bit-exact port (mulberry32 + Box-Muller) | – |
| `rsi/domains/harnessworld/world.py` | `World` / `WorldConfig` / `Mechanism` / `Policy`: synthetic tasks and a mechanism catalog with ground truth | – |
| `rsi/domains/harnessworld/domain.py` | `HarnessWorldDomain(Domain)`: execute (simulated frozen policy), locked grader, `expected()` ground truth, `with_policy()` | `rsi.core.Domain` |
| `rsi/domains/harnessworld/mocks.py` | `HarnessWorldMockLLM`: parametric proposer, critic with a catch rate, and digester/analyst responders | `rsi.core.MockLLM` |

Every file is at most 600 lines (the largest, `loop.py`, has 600). Each run directory follows the code's `runs/<domain>/` layout: `frontier.json`, `calibration.json`, `history.jsonl`, `attribution.jsonl`, `global_analysis.json`, `ledger.jsonl`, `artifacts/`, `evals/<job>.json`, and per round `r<t>/{directives,analysis_report,digests,decisions,summary}.json` plus per variant `r<t>/<V>/{proposal*.json, critic_a<n>.json, critic.json, diff.patch, smoke.json, prep.json, eval.json}`.

## 2. Public API and applying RRSI to a new problem

```python
from rsi.rrsi import Config, RegularizerSwitches, run, paired_transfer
res = run(domain, seed_artifact, llm_task=..., llm_propose=..., config=Config(...), out_dir="runs/x",
          switches=RegularizerSwitches.full(), guards=(), budget=None)   # -> rsi.core.ImprovementResult
```

* `res.best` is the final incumbent H_T. As in the code, this is the incumbent, not the best measured artifact.
* `res.trajectory` has one row per settled round: S, C, S*, b_t, σ_t, winner, and each candidate's decision.
* `res.usage` is keyed by role: `proposer`, `critic`, `digester`, `analyst`, `task`, plus `*:cached` for cache hits.
* `res.meta` holds δ, the calibration, switches, config, the rollout count and critic statistics.
* Calling `run(...)` again on the same `out_dir` resumes the run.

**A new problem, end to end.** The example below is a customer-reply harness: a system prompt plus a formatting helper, graded by exact match. It is copy-pasteable, with a scripted proposer standing in for an LLM:

```python
import json
from rsi.core import Artifact, Task, TaskSuite, FunctionDomain, MockLLM, ClaudeCLI, CachedLLM
from rsi.rrsi import Config, run, paired_transfer

tasks = [Task(f"t{i}", {"amount": i * 7, "currency": "EUR"}, f"EUR {i * 7:.2f}", "money") for i in range(40)]
suite = TaskSuite(tasks, {"evolve": [f"t{i}" for i in range(20)], "holdout": [f"t{i}" for i in range(20, 40)]})

def execute(artifact, task, seed, llm):        # the harness: a tiny formatting "program" read from the artifact
    fmt = artifact["format.txt"].strip()        # e.g. "{currency} {amount}"  or  "{currency} {amount:.2f}"
    return fmt.format(**task.input)

dom = FunctionDomain(suite, execute, lambda task, out: float(out == task.target), name="money-format",
                     description="format.txt is a Python format string applied to {amount, currency}.")
dom.components = {"prompt": ["format.txt"], "control_flow": ["*.py"]}   # optional taxonomy (K); K_str = ()

def proposer(prompt, system, seed, i):          # replace with CachedLLM(ClaudeCLI("haiku"), ".rsi_cache/x")
    if "strict reviewer" in (system or ""):
        return json.dumps({"verdict": "accept", "reasons": [], "risk_notes": []})
    edit = {"id": "C1", "component": "prompt", "hypothesis": "always print two decimals",
            "targets_mode": "wrong formatting", "predicted_affected": ["t1"],
            "retroactive_check": "(corrective) fixes t1; (preservative) none; (transfer) generic"}
    return ("```json\n" + json.dumps({"action": "done", "summary": "2 decimals", "edits": [edit]}) +
            "\n```\n=== FILE: format.txt ===\n{currency} {amount:.2f}\n")

res = run(dom, Artifact({"format.txt": "{currency} {amount}\n"}), llm_propose=MockLLM(proposer),
          config=Config(T=3, k=1, delta=0.05, workers=1), out_dir="runs/money")
print(res.best["format.txt"], res.trajectory[-1]["S"])            # -> {currency} {amount:.2f}  1.0
print(paired_transfer(dom, None, {"H0": res.baseline, "final": res.best})["splits"]["holdout"])
```

Checklist for a real problem:
* Subclass `rsi.core.Domain`, or use `FunctionDomain`, and give it splits `evolve` (plus `holdout` / `ood` for the report).
* Set `components` (path globs, or `re:` regexes, per component) and `structural_components`. This is what makes diff-based tagging faithful. A domain with no taxonomy at all (e.g. a bare `FunctionDomain`) still works: declared non-structural tags are kept for code changes, while structural tags still need diff evidence (§5, item 15). Without that fallback, every stall would burn the reserved exploration slot.
* Optionally set `critic_patterns = [(regex, why)]` and `leakage_terms()`. The default leakage terms are task ids, answers and entities.
* Optionally define `rrsi_guards` (e.g. `MetricGuard("valid_rate", 0.03)`) and `regression_threshold(k)`.
* Pick `Config.preset(...)` or tune β and w, and set `delta` or let the loop calibrate it. With k = 1, or a harness that scores 0 or 1 everywhere, the within-task bootstrap gives δ = 0. `calibration.json` then carries a `warning` and a `RuntimeWarning` is raised: set `delta`, or use `calibration_repeats ≥ 2`.
* For live runs, pass `llm_propose=CachedLLM(ClaudeCLI("haiku"), cache_dir)`. For a coding-agent proposer, use `editor=rsi.core.AgentEditor(ClaudeCLI(...))`; the agent writes the done() object to `_rrsi_done.json`.

## 3. Capability checklist → code → experiment → result

"E*" refers to `experiments/rrsi/e*.py` → `results/rrsi/e*.json` (+ `.png`). All experiments ran with the default offline settings: HarnessWorld uses the parametric mock proposer, and AgentQA uses SimModel with the scripted mock.

### 3.1 Experiment results (offline, default settings)

All numbers below come from the **second review rerun** (2026-09-25, §6.2). Its fixes leave the full-RRSI prompts byte-identical, so every full-RRSI number (and E0, E10, E13) is unchanged. The ablation arms now receive switch-specific prompt text, and the mocks seed their RNG on the prompt, so every ablated arm is a new realization. Where a verdict changed between realizations the row says so; treat those effects as borderline. E5 and E6 were raised from 20/30 to 50 seeds for this reason.

| # | spec claim | arms / N | result (95% CI) | verdict |
|---|---|---|---|---|
| E0 | overview toy: the plain loop's practice score rises while its unseen score stays flat; the guarded loop practises less and transfers better | page defaults, 200 runs × 60 rounds | Exact port: it matches the page's JS under Node to 1e-15, with identical accept counts. Plain loop: practice **+65.4**, unseen **+2.8** pts, 35.2 changes kept. Guarded loop: **+19.6** / **+9.3**, 14.5 kept. Guarded − plain on unseen = +6.5 [+5.6, +7.4]. The critic alone gives +8.8 and the noise margin alone +4.3. With RRSI's z = 2 band the toy keeps only 2.2 changes (unseen +2.4). | reproduced (exact) |
| E1 | unregularized evolution overfits; RRSI transfers and is cheaper | HarnessWorld, 50 seeds, T = 20, m = 2 | **Unregularized:** measured evolve +46.2, true evolve +46.0, held-out +9.6, OOD **−3.4 [−6.3, −0.3]**, tokens **93×**. **RRSI:** +28.7 / +28.2 / +26.7 / **+19.2 [+17.2, +21.2]**, tokens **3.5×**. **Paired RRSI − unregularized:** held-out +17.1 [+14.4, +19.7], OOD +22.6 [+19.3, +25.8], tokens −90× [−123, −59]. **Context dilution off (20 seeds):** the unregularized arm's OOD gain becomes positive (+9.3), but RRSI still beats it on OOD by +12.8 [+7.4, +18.6]. | 5 of 6 checks. The miss is the pre-declared "unregularized OOD gain near 0" (\|mean\| < 3 pts). In this realization the unregularized arm is significantly **below** H_0 (−3.4; the previous realization gave −2.5 [−4.9, +0.1]), which is more overfitting than "near zero", not transfer. |
| E1b | the same on AgentQA | SimModel + scripted proposer, 5 suites, T = 8 | Unregularized kept the leaky answer table in **4/5** runs (previous realization 5/5), RRSI in **0/5**. Unseen gains are the same (+44.8 vs +43.3 pts; both find the Python tool): paired OOD −1.2 [−19.4, +17.5]. Tokens: 1.04× [0.22, 2.63] vs 1.19× [0.56, 1.76] (previous realization 2.01× vs 1.19×). | leak blocking reproduced. Transfer difference and token difference are **not significant** (5 suites); on AgentQA RRSI is **not** reliably cheaper. |
| E2 | removing either guard group raises evolve and lowers OOD; no guards is worst and most expensive | 8 arms × 50 seeds, plus 2 token-matched arms | Differences vs full (Δmeasured evolve, ΔOOD, Δtokens ×H_0): **no guards** +17.5, **−22.6 [−25.8, −19.3]**, +90×. **−acceptance** +15.1, **−20.0 [−23.2, −16.9]**, +20×. **−proposal** −0.9 [−2.9, +1.2], **−3.0 [−5.5, −0.4]**, +1.0× [+0.1, +1.8]. The previous realization gave −proposal +0.8 [−1.6, +3.1], −2.3 [−5.0, +0.3], +0.8× [−0.1, +1.7]. With pruning moved into the proposal group, −proposal is +2.3 / +1.9 [−0.9, +4.8] / +3.6×. **E2b.** All arms are matched on candidate count and evaluation rollouts (≈ 8,200 each; `compute` in the JSON). Budget-only gives OOD +2.7 [−0.3, +5.6], about the same as no guards. Selector-only − budget-only on OOD = +13.5 [+10.1, +16.8]. Token matching gives the critic-free arms 1.6–1.7× more rounds (T = 32 / 33) to equal full RRSI's search-LLM tokens. At that match, budget-only OOD is −2.4 and selector-only − budget-only = +18.6 [+15.1, +21.9]. | Acceptance group and no guards reproduced (8/11 checks). **−proposal:** OOD and tokens are now significantly worse than full, but only just (CI bounds −0.4 and +0.1). Measured evolve is **not** raised in either realization. Treat the proposal-group effect as borderline. E2b: under both matchings the budget alone explains none of the OOD gain. |
| E3 | the critic blocks benchmark-specific fitting before evaluation | leak share {5, 15, 30%} × (critic off, or on with obfuscated-leak catch rate 0 / .5 / .8 / 1), 15 seeds | At 15% leaks, critic on (catch 0.8) − off: leaks kept −8.5 [−9.9, −7.1], evaluations on leaky candidates −13.0, OOD **+18.0 [+13.1, +22.7]**, measured evolve −9.7. At 30%: OOD +21.9 [+16.7, +27.1]. At **5%**: OOD +5.5 [**−0.7**, +12.0], not significant. With catch 1.0: 0 leaks kept and 0 evaluations spent at every share. Regex-only (catch 0) still keeps obfuscated leaks (1.3–3.7 per run). | reproduced at 15% and 30% leak share. At 5% fewer leaks are kept, but the OOD gain is only a trend (both realizations). |
| E4 | the floor stops downhill walks; nulls rarely register as gains | drift pool (−0.5 pt, −10% tokens), 40 rounds, 20 seeds; null pool, 20 seeds | **True evolve change:** S* floor **−2.7 [−2.9, −2.4]** (≈ 1.7 δ, δ = 1.56 pts); S_t floor −10.8; no floor **−30.1**; keep-if-better −0.8. **Nulls with dS > δ over a whole run:** 1.0% (bootstrap δ), 0.4% (R = 5 δ), 2.6% (coding rule), all at or below the z = 2 level. This is **not** evidence that δ is calibrated. The nominal rate at the bootstrap δ is 8.2% (see E10). The run-level rate is pushed down by winner's-curse inflation of the incumbent: in round 0, against H_0's unselected measurement, 5 of 40 nulls (12.5%) cleared δ. The spec asks for "within about δ of its best"; the script's check allows 2δ, and the result is 1.7δ. | floor reproduced (1.7 δ, not ≈ δ). The pooled null rate is below 2.3% only because of winner's-curse inflation; calibration accuracy is tested in E10. |
| E5 | the cost rule earns its tokens: a lighter harness with equal or higher OOD | β1 sweep {0 … 160}, cost rule off, whole complexity term off, **50 seeds** | Default β1 = 40: 3.5× tokens vs 9.2× with the complexity term off (paired **−5.7× [−9.0, −3.2]**). OOD **+1.9 [−1.2, +5.0]** pts, which is **not** non-inferior at the script's pre-declared 1-pt margin (the lower bound misses by 0.2). Tokens vs β1: 1.28× (0), 1.86× (5), 1.92× (10), 2.19× (20), 3.50× (40), 3.38× (80), 3.16× (160), so they rise non-monotonically. β1 = 0 is too tight (OOD +7.1 vs +19.2). | **partial:** lighter harness reproduced. "Equal or higher OOD" is **not certified**: the check passed in 1 of 3 realizations (20 seeds: +3.1 [−0.3, +6.6] and +1.7 [−2.0, +5.3]; 50 seeds: the result shown). |
| E6 | pruning removes machinery that stopped helping | prune directives on / off, decaying-rich pool, T = 30, **50 seeds** | Dead (superseded) mechanisms **−0.8 [−1.4, −0.2]**; prune edits accepted +3.7; mechanisms −4.5 [−6.6, −2.3]; tokens **−4.4× [−6.5, −2.1]**. Pruning also costs a little true evolve score (−1.3 [−2.5, −0.1]); OOD −1.3 [−3.5, +1.1], n.s. | reproduced. At 30 seeds, the dead-mechanism difference was significant in the first realization and not in the second (−0.6 [−1.3, +0.1]). |
| E7 | the annealed budget improves attribution | anneal (paper values: ceil gives 4 → 2) / const 4 / const 1, 50 seeds | **Hitchhiker rate:** 0.16 vs 0.24 with const 4 (**−8.5 pts [−13.8, −3.0]**); const 1: 0.02. **Spearman(recorded dS, true marginal):** 0.55 vs 0.51 (const 1: 0.86). **Early progress** (5 rounds; b_t = 4 in both arms there): vs const 4 −0.8 [−3.0, +1.6]; vs const 1 **+4.7 [+2.8, +6.6]**. **Final unseen:** +1.8 [−0.2, +3.8] vs const 4. | reproduced (both checks, in both review realizations) |
| E9 | stall exploration escapes prompt-only collapse | strict collapse (the spec's premise) and mild collapse, 30 seeds | **Strict:** coverage **1.00 vs 0.60**, accepted structural mechanisms 1.03 vs 0.13, true evolve +2.4 [+1.3, +3.5], OOD +2.5 [+1.6, +3.5]. **Mild:** coverage 1.00 vs 0.87; the other differences are not significant. | reproduced under the spec's premise; weak under mild collapse |
| E10 | δ calibration: ~97.5% clearance at z = 2 | pools of 300 base evaluations; 5 worlds + 2 AgentQA suites | The code-default bootstrap (1 evaluation, k = 2) underestimates sd_null (×0.69–0.70), giving clearance **91.6% (HW) / 90.7% (AQ)**. The small-k-corrected bootstrap gives **97.5% / 96.8%**. Repeated evaluations give 84% (R = 2), 91% (R = 3), 94% (R = 5). At k = 4 the plain bootstrap gives 95.6%. | **not reproduced with the code's estimator at k = 2**; reproduced with the √(k/(k−1)) correction |
| E11 | domain guards are non-compensatory (new in the second review) | a third domain built only through the public API: a `FunctionDomain` design world with a validity flag and a pass/fail verifier, plus `MetricGuard("valid", 0.03)` (the eng `max_valid_rate_drop`). Aggressive mechanisms raise the practice score but break validity; on a hardened split their gain vanishes. 30 seeds, T = 12 | Guards on: **0** accepted candidates whose measured valid rate dropped more than 0.03 (12.4 such candidates were evaluated per run). Guards off: 1.3 per run accepted. Aggressive mechanisms in the final harness 1.4 vs 2.7. On − off: measured practice gain −2.8 [−5.3, −0.3], true held-out valid rate **+6.9 [+4.8, +9.1]** pts, hardened-split score **+3.1 [+0.2, +6.0]**. On the held-out split, which uses the same exploitable verifier, the unguarded harness scores higher (+16.2 vs +13.2). The guard trades that score for validity and for the hardened split. The guard is per step and relative to the current incumbent, as in the eng adapter, so the true valid rate still drifts **−3.6 [−4.7, −2.7]** pts over a guarded run through sub-tolerance steps (off: −10.6). | reproduced (5/5 checks). Guards bound each step, not the cumulative drift. |
| E12 | the harness transfers to a weaker policy never used in the search | HarnessWorld WEAK policy (50 seeds); AgentQA SimModel(skill 0.6) (5 suites) | Weak-policy unseen gain: RRSI **+16.6 [+14.7, +18.5]** vs unregularized +1.6 [−0.7, +3.9] (paired **+15.1 [+12.8, +17.3]**). Weak OOD: +12.0 vs −3.0. AgentQA: +36.3 [+28.3, +41.9] vs +34.4 [+26.7, +39.7], no difference. | reproduced on HarnessWorld; AgentQA shows positive transfer for both arms (no RRSI advantage) |
| E13 | auditable and resumable; readjudicate changes only Algorithm 2 | 5 HW + 2 AQ seeds; kills after the baseline, draft, evaluation, between candidates' history records, and after settle | Every resume ran in a **separate OS process with a different `PYTHONHASHSEED`**. All resumed runs have byte-identical history, attribution, decisions and frontier, and identical ledger nodes. Readjudicating with δ × 0.5 / × 2 spends **0** rollouts and leaves (S, C, dS, dC) unchanged. Counterfactual replay flips 0–3 of 20 decisions per run. | reproduced. Before the first review's fixes, AgentQA seed 0 diverged after a cross-process resume (§6.1). |
| live | real LLM end-to-end | AgentQA; haiku as proposer, critic and analyst; SimModel as task model; T = 2 | 17 live calls, **$0.68** across the first (timed-out) attempt and its cached resume. Every haiku done() submission was valid on the first try (0 bounces). The critic accepted 4/4 with detailed reasoning over its six rules. Round 0 accepted a Python-tool pipeline (dS +0.46, dC −0.83). Round 1: both candidates were rejected by the within-band cost rule. Held-out 0.42 → 0.92, OOD 0.46 → 0.96. The run itself used pre-review code. **Replay on the current code** (`live_smoke.py --llm claude:haiku --cache-only`: the backend behind the cache refuses every call, so the replay costs $0): 17/17 cache hits, 0 misses. The trajectory, winners, transfer and final files are identical to the recorded live run (`results/rrsi/live_smoke_replay.json`). The current code therefore sends haiku exactly the prompts it answered, and handles its answers identically. No new live calls were made. | works end to end (`live_smoke.json`, `live_smoke_replay.json`) |

Not run: E8 (history conditioning with a real LLM; the mock proposers' history use is mechanical).

### 3.2 Capability checklist (spec §10)

| # | claim | code | evidence |
|---|---|---|---|
| 1 | improves the harness around a frozen model | `loop.RRSIRun`, `propose.Proposer`, `components.Taxonomy` (K); the policy is frozen inside `Domain.execute` | E1, live |
| 2 | every part editable; limits how the search moves | `Config.editable=None` (all files); regularizers only in `schedule`, `history`, `selection`, `critic` | tests, E2 |
| 3 | three failure modes named and targeted | leakage → `critic`; noise chasing → `calibrate` + `NoiseFloor` / within-band rule; complexity → `RRSICostRule` + `History.prune_set` | E3, E4, E5, E6 |
| 4 | keep rule: floor, critic, earned cost | `selection.build_gates` / `judge` / `select_round` + `critic.RRSICritic` | E1–E5 |
| 5 | shrinking edit budget | `schedule.edit_budget`; enforced in `Proposer.validate` | tests, E7 |
| 6 | memory of what failed | `History` (per-edit JSONL) + `render(mode=full)` with the negative-evidence header + `Scoreboard` | tests; mocks avoid rejected ideas |
| 7 | forced variety when stuck | `history.stall_flag` / `exploration`; reserved slot checked in `done()` and on the diff after the critic (also when the critic is ablated) | E9, `test_reserved_slot_checked_on_the_diff_even_without_critic` |
| 8 | leakage critic, generic edits pass | `RRSICritic` (core denylist + regex + LLM review), repair loop `repair_rounds` | E3, live |
| 9 | noise floor from repeated runs of the unchanged agent | `calibrate.calibrate` (repeats / bootstrap) + frontier `S_star` + `NoiseFloor` | E4, E10 |
| 10 | cost must be earned | `Measurement.C` + `RRSICostRule` | E5 |
| 11 | pruning flagged for deletion (selection side) | `History.prune_set` → proposer context; prune edits pass critic + Algorithm 2; ablation switch in the acceptance group | E6, E2 |
| 12 | regularization analogies | §4.6 mapping; `schedule` (L0), prune (L1), cost rule (L2), exploration (entropy), floor + history (holdout reuse) | this page |
| 13 | b_t formula, illustrative 1 / 4 / 10 | `edit_budget`, `Config.preset("overview")` | `test_schedule_tables_from_spec` |
| 14 | score ≥ best − δ | `NoiseFloor` with `GateContext.best_score = S*` | E4 |
| 15 | ΔC ≤ β0 + β1·gain, frozen for transfer | `RRSICostRule`; config frozen in `frontier.json`; `paired_transfer` never mutates it | E5 |
| 16 | evolve on one set, run unchanged on unseen; same budget for every arm | sealed `holdout` / `ood` splits, `paired_transfer`, `RegularizerSwitches` arms sharing T, m, k | E1, E2 |
| 17 | several domains / graders | any `rsi.core.Domain`: HarnessWorld (rubric simulator), AgentQA (exact match), the E11 design world (a `FunctionDomain` with validity + pass/fail verifier, built only through the public API), the `FunctionDomain` example (§2), and two new taxonomy-less `FunctionDomain`s in `tests/test_rrsi_generic.py` (text normalization) and `tests/test_rrsi_review.py` (unit conversion in code) | E1, E11, E12, tests |
| 18 | compared with prior methods | unregularized and ablation arms here; Meta-Harness / GEPA etc. live in their own packages and share `rsi.core.transfer_report` | E1, E2 |
| 19 | the ranking flips | E1 / E2: highest evolve ↔ lowest OOD | E1, E2 |
| 20 | removing guards: evolve up, unseen down, more tokens | `RegularizerSwitches.none/no_acceptance/no_proposal` | E2 (acceptance and none: yes; proposal: borderline, OOD −3.0 [−5.5, −0.4] in one realization and a trend in the other; evolve never raised) |
| 21 | model independence / weak-policy transfer | `HarnessWorldDomain.with_policy(WEAK)`, `SimModel(skill=0.6)` | E12 |
| 22 | no held-out split got worse; guards for simulator-graded domains | `paired_transfer(...)["non_regression"]` (point estimates) and `["significant_regression"]` (paired CI below 0); non-compensatory guards via `run(..., guards=(MetricGuard("valid", 0.03),))` | E1, E11, live |
| 23 | steps per task | `Measurement.extra["steps"]`, `transfer_report` steps | results JSON |
| 24 | statistics, not search | `calibrate`, floor, falsified-idea ledger, split discipline | E4, E10 |
| 25 | guards on proposing and keeping | proposal (`schedule`, `history`, `exploration`) + selection (`critic`, `selection`) | E2 |
| 26 | lighter harness | cost rule + within-band shaped rule + prune | E1 (3.5× vs 93×), E5 (−5.7×), E6 (−4.4×) |
| 27 | simulator-graded transfer checks | HarnessWorld analytic ground truth (`expected`) | all HW experiments |
| 28 | caveats (error bars, same model family, knobs) | every table has seed CIs; the critic LLM is configurable (`llm_critic`); `Config` presets and sweeps | E5 β1 sweep, E10 |
| 29 | several filtered candidates per round; a record of what failed; human-set rules | `Config.m`, `History`, `constitution.py` (generic SKILL / PATTERNS) | – |
| 30 | measure noise first; keep a set the loop never sees; ban shortcuts; one change at a time; log as a tree; never let the loop grade itself | `calibration_repeats`, `heldout_monitor` (extension, logged only), critic, budget, `rsi.core.Ledger` tree, grader inside `Domain.grade` + critic precheck against harness code importing `rsi`, touching `sys.modules`, or walking the call stack / object graph (`sys._getframe`, `f_back`, `gc.get_objects`, ...) | E10, E13, `test_rrsi_generic.py` (the loop reads only `evolve`; tampering blocked), `test_rrsi_review.py::test_stack_walking_grader_tampering_is_real_and_blocked_before_evaluation` (the exploit works in-process and is rejected before evaluation) |
| 31 | cross-method reuse | `build_gates` / `select_round` / `calibrate` are pure and reusable; ledgers are `rsi.core.Ledger` trees (Dream-RSI replay) | E13 counterfactual replay |
| 32 | a critic that Meta-Harness lacks | `RRSICritic` | E3 |
| 33 | full scale is costly; CPU tiers | Tier 0 (`toy`), Tier 1 (HarnessWorld), Tier 2 (AgentQA + SimModel), live haiku | all |
| 34 | regularizes what may be proposed and kept | proposal-side and selection-side modules, each switchable | E2 |


## 4. HarnessWorld (the Tier-1 domain)

HarnessWorld follows spec §9.2 Tier 1 [inferred design]. Default `WorldConfig`:
* **Tasks.** 100 evolve, 100 ID held-out and 100 OOD tasks. Evolve and held-out use the practice families {extract, arith, plan, format}; OOD uses shifted families {units, code, search, dates}, has more tool-surface tasks and is +0.4 logit harder.
* **Rewards.** Each task has 16 rubric criteria, so a trial's reward is Binomial(16, p)/16, and the noise band is about 1.6 pts (bootstrap δ, k = 2).
* **Policy.** p = σ(a_π − d_x + Σ_m e_m(x) − 0.15·log(cost factor)). The last term is a documented *context-dilution* assumption; E1 includes a robustness arm with it set to 0.
* **Catalog.** 110 mechanisms, one file each, in 9 kinds: generic, structural, narrow (practice-only, −0.04 OOD), leak (a literal `ANSWERS = {"hw-e-017": "ans-…"}` line), obfuscated_leak (a hash-keyed table), null, costly (×1.3–1.8 tokens), harmful and decaying (superseded).
* **Trick effect.** A leak's +1.5-logit effect on the listed evolve tasks applies only while its payload line is present, so a repair that deletes the payload removes the trick.
* **Mock proposer shares** (per proposal, by kind): generic .15, structural .07, narrow .05, leak .10, obfuscated .05, null .18, costly .12, harmful .20, decaying .08. The mock proposer never sees true effects. In a repair round it removes only the edits that the objections name, i.e. the mechanism id or one of the evolve ids / answers its payload hard-codes. A leak the critic missed stays in the candidate. After an objection, its replacement draw excludes literal answer tables only.
* **Catalog text.** Mechanism descriptions name practice families only ("aimed at extract, plan tasks"). Which mechanisms help the sealed OOD families is ground truth that a live proposer must not read.

## 5. Deviations from the released code (deliberate, documented)

1. **No git.** Candidates are content-addressed artifacts, and the artifact id stands in for the git tree hash. "Fast-forward" becomes an update of the frontier's incumbent, and branches/worktrees become artifact ids in `prep.json`. Diff paths are stored relative to the run directory.
2. **Proposer protocol.** The code runs a multi-turn JSON action agent (list/read/edit/write/done, 40 turns). Here one `rsi.core.Editor` call returns whole files plus the done() JSON, or an `AgentEditor` edits in place. The done() validation is the code's: required fields, K, ≤ b_t, reserved slot, zero-change bounce and abort bounce. Bounces are capped by `max_done_bounces = 3` instead of the 40-turn limit. The "zero file changes" check compares against the incumbent (the whole candidate) rather than counting this call's edit actions.
3. **Analyst and digesters.** The code gives them tool-using multi-turn loops (digest_many, grep/read). Here there is one digester call per trace with the rendered trace inline, and one aggregation call, using the same prompts, schemas and rules. `Config.analyst="heuristic"`, the offline default when no analyst LLM is given, is a deterministic clusterer over grader feedback. `max_digests = 8` per round.
4. **Critic precheck.** It uses `rsi.core.LeakageCritic` over *added* diff lines. The code greps the whole diff, which would block an edit that *deletes* leaked lines. Domain denylist terms (task ids, answers, entities) apply automatically. The LLM review prompt is the code's.
   * Extension: the generic patterns also reject harness code that imports `rsi` or touches `sys.modules`. Artifacts run in the evaluator's process and could otherwise rewrite the grader.
   * An LLM verdict must be a JSON object whose `verdict` is `accept` or `reject`. The code returns the first `{...}` span without checking it. Anything else counts as unparseable (3 attempts, then fail closed).
5. **Component signals.** These come from `Domain.components` path globs, checked structural-first with `prompt` last, followed by the paper's generic structural regexes. The code uses hand-written, per-domain, very broad regex lists. `normalize` still receives the whole candidate diff, as in the code. `ALIASES` maps `client_tool`↔`tool`.
6. **Driver.** On an exception the driver retries the same round (resume-safe) and stops after 3 consecutive failures. The code re-launches the next round, which then fails its trajectory precondition, so the effect is similar.
7. **Missing trials** are `Trial.error` values starting with `infra:` (the `rsi.core` convention). They score 0 with the full denominator. `invalid_missing_frac` triggers one retry, then `eval_invalid`.
8. **Extensions**, all off by default unless noted:
   * `budget_rounding="floor_at_bmin_last"`, which lets b_t reach b_min.
   * `bootstrap_small_k_correction`, which rescales the bootstrap se by √(k/(k−1)); see E10.
   * `calibration_repeats` (R base evaluations).
   * `heldout_monitor`, which scores each new incumbent on holdout and only logs it.
   * `RegularizerSwitches` for every mechanism, including `floor="S_t"` and `within_band` ∈ {shaped, strict, reject, admit}.
   * `Config.record_timestamps=False`, for byte-identical ledgers.
9. **Unregularized baseline** (not defined by any source; the spec's proposal): constant b_max, accepted-only history, no exploration, no critic (smoke only), and keep the argmax iff S′ > S_t.
10. **Ablation groups.** Proposal = {budget anneal, negative-evidence history, stall exploration}. Acceptance = {critic, floor, cost rule, within-band shaped rule, domain guards, prune directives}. E2 also reports pruning moved to the proposal group. The E2b "selector-only" arm uses exactly the same switches as "−proposal", so their numbers coincide.
11. **Resume fix (differs from the code).** Algorithm 2 uses `incumbent_component_counts(before_t=t)`, i.e. accepted edits from rounds < t only, so a round resumed after a partial history write reproduces the same novelty ν. Ledger nodes are upserted so their order survives a resume. A candidate's per-edit history and scoreboard rows are written in one append, and a torn last JSONL line is skipped on read.
12. **Reserved-slot check without a critic.** In the code, the diff-based reserved-slot check sits inside the critic loop. Here it also runs when the critic is ablated, with a pass-through reviewer and the same bounded repair loop, because it belongs to stall exploration (proposal side).
13. **Traces precondition** (as in the code): a round raises (an infra failure for the driver) if fewer than `0.5·min(|D|, n_fail + n_success)` incumbent traces are available.
14. **`paired_transfer`** runs each (arm, task, seed) rollout once. The win/tie/loss pass replays `transfer_report`'s trials from a shared trial cache, so a live task model is not paid twice. Besides the point-estimate `non_regression` flag, it reports `significant_regression`: the unseen splits whose paired CI lies entirely below the reference.
15. **Taxonomy-less domains** (`Taxonomy.trust_code_tags`, extension). The released code keeps a declared tag only if a domain or generic regex evidences it. On a domain that declares neither `components` nor `component_signals`, nothing can evidence `control_flow` / `config` / `output_plumbing` / `context_mgmt`. Every code edit was therefore re-tagged `prompt`, T_t collapsed to {prompt}, and once σ_t = 1 every reserved variant burned its 1 + `repair_rounds` proposer calls and ended as `critic_reject` (reproduced in `test_rrsi_review.py`). In that case only, a declared non-structural tag is kept for a code (non-text-only) diff, and an unverifiable code edit defaults to `control_flow`. Structural tags still need the generic structural evidence, so novelty ν and reserved slots on K_str cannot be faked. Domains with a taxonomy (HarnessWorld, AgentQA) are unaffected.
16. **Ablation arms see only the rules in force.** The released code has no ablation arms. Here, `default_constitution(cfg, taxonomy, switches)` writes the "How your work is judged" section from the active switches. For example, the unregularized arm is told "kept only if measured higher", and nothing about a floor, a cost rule, exploration or pruning. With `history_conditioning="accepted_only"` / `"none"`, the attribution scoreboard (itself evidence about rejected edits) is filtered to accepted candidates or hidden, and the history header drops "negative evidence". Before this fix, a live proposer in a baseline arm was steered by regularizers that were switched off. The full-RRSI prompt text is byte-identical to before (verified: identical ledgers, and the recorded live run replays from cache with 0 misses). The mocks ignore these texts but seed their RNG on the prompt, so ablation arms drew a new realization, and E1–E7, E9 and E12 were rerun.
17. **Float ties.** The ablation gates (`IncumbentFloor`, the `RRSICostRule` band test, the "strict" band rule) use the core's `TIE_EPS`, like `NoiseFloor` / `CostRule`.
18. **Degenerate δ.** If calibration yields δ = 0 (k = 1 bootstrap, or a harness whose trials never vary), `calibration.json` gets a `warning` and `RRSIRun.calibrate` raises a `RuntimeWarning`. The released code silently runs with a zero band.
19. **`m` is validated** (1 ≤ m ≤ 8 variant labels, 0 ≤ m_draft ≤ m).

## 6. Limitations

* **HarnessWorld is a stylised simulation.**
  * Magnitudes are not calibrated to the paper (unregularized tokens 93× H_0 here vs 2.4× in the paper).
  * The conclusions depend on world assumptions that are now explicit in `WorldConfig`: a random proposal is roughly neutral off the practice set, leaks and practice-tuned instructions are slightly harmful off-target, and a larger harness dilutes context (`context_penalty`; E1 shows the main result holds with it off).
* **Mock proposers are shallow.** They avoid rejected ideas but never "refine what has credit" and do not learn from the scoreboard. So the proposal-side regularizers are tested only mechanically. This is the likely reason "−proposal lowers OOD" is only borderline (E2: −3.0 [−5.5, −0.4] in the second review realization, −2.3 [−5.0, +0.3] in the first) and never comes with a higher evolve score: the selection side already filters the larger bundles well.
* **Results depend on the realization.** Several borderline effects changed significance across the three realizations (pre-review, first review, second review):
  * E7 hitchhiking: n.s. → significant → significant.
  * E3 at 5% leaks: significant → n.s. → n.s.
  * E5 OOD non-inferiority: failed → passed → failed, including at 50 seeds.
  * E6 dead mechanisms: significant → n.s. (30 seeds) → significant (50 seeds).
  * E2 −proposal OOD: n.s. → n.s. → significant.
  * E1 "unregularized OOD near 0": met → met → missed, because it fell below H_0.

  Treat effects whose CI bound sits near 0 as trends. The verdict column above reports the current realization and names the flips.
* **E8 was not run.** Re-proposal of falsified ideas needs a real LLM at scale.
* **AgentQA with SimModel is dominated by one mechanism** (routing computation through the Python tool), which every arm finds. There, the regularizers show up as leak blocking (0/5 vs 4–5/5 leaky finals). The token ratios (RRSI 1.19× vs unregularized 2.01× in one realization, 1.04× in the other) and any transfer gap are not significant with 5 suites.
* **Proposer protocol.** It is single-shot rewrite (or `AgentEditor`) instead of the code's 40-turn JSON-action agent. Traces are inlined and capped (8 traces × 1,200 chars) rather than browsed with `read_trace`. The analyst is likewise single-shot per digest.
* **Live evidence is one small run** (T = 2, haiku, $0.68), made with pre-review code. It replays from the cache on the current code with 0 misses and identical outcomes (`live_smoke_replay.json`). That shows the prompts and decisions are unchanged. It is not a new sample of the model. Headless `claude -p` calls took roughly 1–3 minutes each in this environment. Every experiment script accepts `--llm claude:haiku`. For HarnessWorld, the domain description shows the mechanism catalog (practice-family targets only) so that a real LLM can pick mechanisms. The mock-only knobs (shares, collapse bias, catch rate) have no effect on a live proposer.
* **The within-task bootstrap δ** (the faithful default) under-covers at k = 2 (E10). `bootstrap_small_k_correction=True` or `calibration_repeats ≥ 5` are the recommended settings for new problems.
* **E13 bugs, found and fixed by the implementer.** Two resume bugs appeared at the kill point between history records within a round.
  * Novelty counts included the round's own partial records. The released code has the same pattern (`incumbent_component_counts()` is computed after partial records may exist); we now count rounds < t only.
  * Ledger nodes were re-added with a reset `seq`.
* **Paper-level claims not reproducible here:** absolute benchmark numbers (Terminal-Bench, Harvey LAB, EngDesign) and cross-provider policies (Gemini). Only their CPU analogues are tested.

### 6.1 Adversarial review (2026-09-25): bugs found and fixed

These were fixed in place. Every experiment except E0 (unaffected) was then rerun at full settings.
1. **Offline runs were not deterministic across processes.**
   * The HarnessWorld mock consumed one random draw per element while iterating a `set` of rejected mechanism ids, so its choices depended on `PYTHONHASHSEED`: 4 hash seeds gave 3 different ledgers.
   * The AgentQA mock's self-consistency harness broke majority-vote ties with `max(set(finals), …)`, so the evaluated program itself was hash-seed dependent.
   * The earlier E13 "fresh process" resumes ran in the same interpreter and could not see this. E13 now resumes in a subprocess with a different hash seed, and `test_offline_run_is_identical_across_processes_and_hash_seeds` guards both domains.
2. **The mock repair was omniscient.** In any repair round, the HarnessWorld mock deleted every leaky edit (`bool(m.payload)`), including leaks the critic had missed. That flattered the critic in E1–E3 and E12. It now removes only the edits the objections name.
3. **Sealed-split information in the proposer's context.** HarnessWorld mechanism descriptions (in `describe()` and in the harness files) listed the OOD families each mechanism helps. They now name practice families only.
4. **Held-out monitor de-duplication bug.** Rows were written with `t + 1` but the check looked for `t`, so the second of two consecutive accepted rounds was never logged.
5. **Fidelity gaps.**
   * The reserved-slot diff check was skipped whenever the critic was ablated.
   * The code's traces precondition was missing.
   * `paired_transfer` evaluated every arm twice and ignored its `seed_offset` argument, which has been removed.
   * History appends were not atomic per candidate, and a torn line crashed every later round.
6. **Over-claiming in the notes.**
   * E2b was called "compute-matched" without saying on what. The arms are now matched on candidate count and rollouts, plus a new search-token-matched pass.
   * E4 labelled a pooled null rate of ≤ 2.3% as "near the calibrated level". Winner's-curse inflation produces that rate; round-0 nulls clear δ at 12.5% (nominal 8.2%).
   * E7's arm label said "4 → 1"; the released ceil schedule gives 4 → 2.
   * E10's docstring claimed held-out pairs.
7. **Genericity.** `tests/test_rrsi_generic.py` runs RRSI unchanged on a new `FunctionDomain` (text normalization with a flaky executor and its own leak). It checks that the loop reads only the `evolve` split, that δ is calibrated, that the leak is caught by the precheck and repaired, that transfer is positive, and that the unregularized arm keeps the leak.

* **Domain guards bound each step, not the drift** (E11). As in the eng adapter, a guard compares the candidate with the current incumbent. A run can still lose validity through a series of sub-tolerance steps: −3.6 pts over 12 rounds with guards on, −10.6 off. A floor-style guard relative to the best validity seen would close this gap. That would be an extension, not the released rule.
* **Ablation prompts.** The switch-aware constitution (§5, item 16) is our choice for the baselines. The paper does not say what prompt its "unregularized" arm used. The mocks ignore prompt text, so this matters only for live runs.

### 6.2 Second adversarial review (2026-09-25): bugs found and fixed

The previous review's findings (§6.1) were re-checked and hold. This pass found and fixed the following. E1–E7, E9, E11 and E12 were rerun at full settings (E5 and E6 at 50 seeds). E10 and E13 were rerun unchanged.

1. **Reserved exploration slots burned on any taxonomy-less domain.** On a `FunctionDomain` without `components`, every declared code tag was re-tagged `prompt`, so T_t = {prompt}. Once σ_t = 1, every reserved variant spent 1 + `repair_rounds` proposer calls and ended as `critic_reject`. It was reproduced on a new unit-conversion domain: all 4 stalled rounds lost variant B. Fixed with `trust_code_tags` (§5, item 15), and regression-tested in `test_taxonomy_less_domain_keeps_code_tags_and_reserved_slots_work`, which fails with the fix disabled.
2. **Ablation arms were told the full RRSI reward and shown negative evidence.** The constitution always described the floor, cost rule, exploration and pruning, even for the unregularized arm. The attribution scoreboard (hits and unpredicted regressions of *rejected* edits) was shown even with `history_conditioning="accepted_only"` / `"none"`. For a live proposer, this leaked the regularizers into the baselines. Fixed with switch-aware text and a switch-aware scoreboard view (§5, item 16). The full-arm prompts are byte-identical (checked on ledger fingerprints and by the 0-miss live replay).
3. **Grader tampering by stack walking.** Harness code that walks `sys._getframe().f_back` to the `FunctionDomain` and replaces `grade_fn` scores 1.0 and poisons every later grade in the process. The test demonstrates this against the core `Evaluator`. The critic precheck now rejects stack/object-graph walking (`sys._getframe`, `inspect.currentframe/stack`, `f_back`, `f_globals`, `gc.get_objects/referrers`, `__globals__`) before any evaluation.
4. **Silent zero noise band.** With k = 1, or a harness whose trials never vary (e.g. H_0 fails every task), the bootstrap gives δ = 0 and the floor and band silently degenerate. This now yields a `warning` in `calibration.json` and a `RuntimeWarning`.
5. **Experiments could average in unfinished runs.** `drive` turns 3 consecutive round exceptions into `stop_reason="infra_failures"`, and `run_hw` / `run_aq` never checked it. Offline runs now raise if a run did not reach `max_rounds`. Live runs record `stop_reason` / `rounds_settled` per row. Every row of the rerun reached `max_rounds`.
6. **`run_aq` transfer used SimModel even when the task model was live.** Fixed: transfer uses the run's task model. Offline numbers are unchanged, because SimModel is stateless.
7. **Smaller issues.**
   * The LLM analyst crashed the round (as an "infra failure") on `"n_tasks": "many"` or non-list sections; it now falls back to the heuristic.
   * The ablation gates now use the core's float-tie tolerance.
   * `m` / `m_draft` are validated.
   * `paired_transfer` reports `significant_regression`.
   * `default_constitution` is exported.
8. **Missing experiment.** E11 (domain guards) had never been run. It is now `experiments/rrsi/e11_guards.py`, a third domain built through the public API with a user-supplied `MetricGuard`, and it reproduces.
9. **Over-claiming corrected.** The notes said E5 was "reproduced (OOD non-inferior)". The check fails in 2 of 3 realizations, including at 50 seeds, so it is now marked partial. The E1 and E2 rows now report the realization flips.

## 7. Running the tests and experiments

```bash
python -m pytest -q tests/test_rrsi_core.py tests/test_rrsi_toy.py tests/test_rrsi_loop.py tests/test_rrsi_harnessworld.py tests/test_rrsi_generic.py tests/test_rrsi_review.py
python experiments/rrsi/e0_overfitting_trap.py            # 5 s, LLM-free
python experiments/rrsi/e1_overfitting.py                 # 50 seeds HW + 5 AgentQA suites
python experiments/rrsi/e2_ablations.py                   # 8 arms x 50 seeds + 2 token-matched arms (~5 min on 3 cores)
python experiments/rrsi/e3_critic.py   ... e13_resume_readjudicate.py
python experiments/rrsi/e11_guards.py                     # domain guards, 30 seeds (~10 s)
python experiments/rrsi/live_smoke.py --llm claude:haiku  # one small live run, cached in .rsi_cache/rrsi
python experiments/rrsi/live_smoke.py --llm claude:haiku --cache-only   # $0 replay of the recorded run on the current code
python experiments/rrsi/e1_overfitting.py --llm claude:haiku --seeds 1 --quick   # live showcase (costs money)
```

The tests (53) take about 7-10 s. Every script accepts `--llm sim|claude:<model>`, `--seeds N`, `--quick` and `--workers`, and writes `results/rrsi/<name>.json` (config, per-seed rows, mean + 95% bootstrap CI, paired differences, checks, verdict) plus a PNG. E0 and E10 are LLM-free and ignore `--llm`.
