# RRSI: implementation notes (`rsi.rrsi`, `rsi.domains.harnessworld`)

This page documents how the RRSI spec (`docs/methods/rrsi.md`) is implemented on the shared `rsi.core` API. It covers the module map, the public API, how each capability-checklist item maps to code, an experiment and a result, deviations from the released code (`google-research/rrsi@be50316`), and limitations. All numbers below come from `results/rrsi/*.json`, produced by `experiments/rrsi/*.py` with the default offline settings. CIs are 95% bootstrap CIs over seeds (`rsi.core.stats.summarize_runs` / `paired_diff_ci`).

## 1. Module map

The layout mirrors `google-research/rrsi/rrsi/*.py`. Git worktrees and branches are replaced by content-addressed `rsi.core.ArtifactStore` artifacts plus an `rsi.core.Ledger` tree.

| module | role (paper symbol) | built on |
|---|---|---|
| `rsi/rrsi/config.py` | `Config`: every hyperparameter with the paper/code default, plus `PRESETS` `coding` / `workspace` / `eng` / `overview` taken from `domains/*/rrsi.json` | – |
| `rsi/rrsi/switches.py` | `RegularizerSwitches`: ablation arms (`full`, `no_proposal`, `no_acceptance`, `none` = unregularized, `budget_only`, `selector_only`, `.but(...)`) | – |
| `rsi/rrsi/schedule.py` | `edit_budget`: annealed L0 budget b_t (cosine + ceil; `floor_at_bmin_last` option) | – |
| `rsi/rrsi/components.py` | K, K_str, `Taxonomy.from_domain`, diff-based `normalize` / `classify`, novelty ν | `Domain.components`, `Domain.structural_components` |
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
| `rsi/rrsi/constitution.py` | domain-agnostic SKILL.md / PATTERNS.md, with the run's numbers filled in | – |
| `rsi/rrsi/mocks.py` | `AgentQAMockLLM`: scripted proposer + critic for `rsi.domains.agentqa` | `rsi.core.MockLLM` |
| `rsi/rrsi/toy.py` | the overview's "overfitting trap" simulator, bit-exact port (mulberry32 + Box-Muller) | – |
| `rsi/domains/harnessworld/world.py` | `World` / `WorldConfig` / `Mechanism` / `Policy`: synthetic tasks and a mechanism catalog with ground truth | – |
| `rsi/domains/harnessworld/domain.py` | `HarnessWorldDomain(Domain)`: execute (simulated frozen policy), locked grader, `expected()` ground truth, `with_policy()` | `rsi.core.Domain` |
| `rsi/domains/harnessworld/mocks.py` | `HarnessWorldMockLLM`: parametric proposer, critic with a catch rate, and digester/analyst responders | `rsi.core.MockLLM` |

Every file is under 600 lines (the largest, `loop.py`, has 581). Each run directory follows the code's `runs/<domain>/` layout: `frontier.json`, `calibration.json`, `history.jsonl`, `attribution.jsonl`, `global_analysis.json`, `ledger.jsonl`, `artifacts/`, `evals/<job>.json`, and per round `r<t>/{directives,analysis_report,digests,decisions,summary}.json` plus per variant `r<t>/<V>/{proposal*.json, critic_a<n>.json, critic.json, diff.patch, smoke.json, prep.json, eval.json}`.

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
* Optionally set `components` (path globs, or `re:` regexes, per component) and `structural_components`.
* Optionally set `critic_patterns = [(regex, why)]` and `leakage_terms()`. The default leakage terms are task ids, answers and entities.
* Optionally define `rrsi_guards` (e.g. `MetricGuard("valid_rate", 0.03)`) and `regression_threshold(k)`.
* Pick `Config.preset(...)` or tune β and w, and set `delta` or let the loop calibrate it.
* For live runs, pass `llm_propose=CachedLLM(ClaudeCLI("haiku"), cache_dir)`. For a coding-agent proposer, use `editor=rsi.core.AgentEditor(ClaudeCLI(...))`; the agent writes the done() object to `_rrsi_done.json`.

## 3. Capability checklist → code → experiment → result

"E*" refers to `experiments/rrsi/e*.py` → `results/rrsi/e*.json` (+ `.png`). All experiments ran with the default offline settings: HarnessWorld uses the parametric mock proposer, and AgentQA uses SimModel with the scripted mock.

### 3.1 Experiment results (offline, default settings)

All HarnessWorld and AgentQA numbers below come from the **review rerun** of 2026-09-25, done after the determinism and mock-fairness fixes in §6.1. Earlier numbers depended on `PYTHONHASHSEED` and could not be reproduced exactly. Where the rerun changed a conclusion, the row says so.

| # | spec claim | arms / N | result (95% CI) | verdict |
|---|---|---|---|---|
| E0 | overview toy: the plain loop's practice score rises while its unseen score stays flat; the guarded loop practises less and transfers better | page defaults, 200 runs × 60 rounds | Exact port: it matches the page's JS under Node to 1e-15, with identical accept counts. Plain loop: practice **+65.4**, unseen **+2.8** pts, 35.2 changes kept. Guarded loop: **+19.6** / **+9.3**, 14.5 kept. Guarded − plain on unseen = +6.5 [+5.6, +7.4]. The critic alone gives +8.8 and the noise margin alone +4.3. With RRSI's z = 2 band the toy keeps only 2.2 changes (unseen +2.4). | reproduced (exact) |
| E1 | unregularized evolution overfits; RRSI transfers and is cheaper | HarnessWorld, 50 seeds, T = 20, m = 2 | **Unregularized:** measured evolve +45.5, true evolve +45.2, held-out +9.7, OOD **−2.5 [−4.9, +0.1]**, tokens **63×**. **RRSI:** +28.7 / +28.2 / +26.7 / **+19.2 [+17.2, +21.2]**, tokens **3.5×**. **Paired RRSI − unregularized:** held-out +17.0 [+14.3, +19.6], OOD +21.7 [+18.9, +24.6]. **Context dilution off (20 seeds):** the unregularized arm's OOD gain becomes positive (+12.3), but RRSI still beats it on OOD by +9.8 [+4.8, +14.4]. | reproduced (all 6 checks) |
| E1b | the same on AgentQA | SimModel + scripted proposer, 5 suites, T = 8 | Unregularized kept the leaky answer table in **5/5** runs, RRSI in **0/5**. Unseen gains are the same (+44.8 vs +43.3 pts; both find the Python tool): paired OOD −2.3 [−18.1, +16.3]. Tokens: 2.01× [1.77, 2.45] vs 1.19× [0.56, 1.76]. | leak blocking reproduced; transfer difference and token saving **not significant** (5 suites) |
| E2 | removing either guard group raises evolve and lowers OOD; no guards is worst and most expensive | 8 arms × 50 seeds, plus 2 token-matched arms | Differences vs full (Δmeasured evolve, ΔOOD, Δtokens ×H_0): **no guards** +16.8, **−21.7 [−24.6, −18.9]**, +60×. **−acceptance** +13.3, **−18.9 [−22.4, −15.5]**, +19.5×. **−proposal** +0.8 [−1.6, +3.1], −2.3 [−5.0, +0.3], +0.8× [−0.1, +1.7]. Moving pruning into the proposal group: −proposal is then +2.3 / +0.2 / +3.0×. **E2b.** All arms are matched on candidate count and evaluation rollouts (≈ 8,200 each; `compute` in the JSON). Budget-only gives OOD +0.9 [−1.7, +3.5], about the same as no guards. Selector-only − budget-only on OOD = +16.0 [+12.6, +19.4]. Token matching gives the critic-free arms 1.6× more rounds (T = 32) to equal full RRSI's search-LLM tokens. At that match, budget-only OOD is −0.0 and selector-only − budget-only = +16.9 [+13.4, +20.3]. | acceptance group and no guards reproduced. Removing the proposal group gives **no significant effect** on evolve, OOD or tokens here (OOD trends down). E2b: under both matchings the budget alone explains none of the OOD gain. |
| E3 | the critic blocks benchmark-specific fitting before evaluation | leak share {5, 15, 30%} × (critic off, or on with obfuscated-leak catch rate 0 / .5 / .8 / 1), 15 seeds | At 15% leaks, critic on (catch 0.8) − off: leaks kept −9.2 [−10.5, −7.9], evaluations on leaky candidates −14.0, OOD **+19.6 [+14.3, +24.7]**, measured evolve −11.1. At 30%: OOD +23.1 [+18.1, +28.0]. At **5%**: OOD +4.5 [**−1.3**, +12.1], not significant. With catch 1.0: 0 leaks kept and 0 evaluations spent at every share. Regex-only (catch 0) still keeps obfuscated leaks (1.3–3.7 per run). | reproduced at 15% and 30% leak share; at 5% fewer leaks are kept but the OOD gain is only a trend |
| E4 | the floor stops downhill walks; nulls rarely register as gains | drift pool (−0.5 pt, −10% tokens), 40 rounds, 20 seeds; null pool, 20 seeds | **True evolve change:** S* floor **−2.7 [−2.9, −2.4]** (≈ 1.7 δ, δ = 1.56 pts); S_t floor −11.7; no floor **−30.2**; keep-if-better −0.8. **Nulls with dS > δ over a whole run:** 1.0% (bootstrap δ), 0.4% (R = 5 δ), 2.6% (coding rule), all at or below the z = 2 level. This is **not** evidence that δ is calibrated. The nominal rate at the bootstrap δ is 8.2% (see E10). The run-level rate is pushed down by winner's-curse inflation of the incumbent: in round 0, against H_0's unselected measurement, 5 of 40 nulls (12.5%) cleared δ. | floor reproduced. The pooled null rate is below 2.3% only because of winner's-curse inflation; calibration accuracy is tested in E10. |
| E5 | the cost rule earns its tokens: a lighter harness with equal or higher OOD | β1 sweep {0 … 160}, cost rule off, whole complexity term off, 20 seeds | Default β1 = 40: 3.2× tokens, vs 8.7× with the complexity term off (paired −5.5× [−9.8, −2.1]). OOD +3.1 [−0.3, +6.6] pts, which is non-inferior at the script's 1-pt margin. Tokens rise with β1 from 1.25× (β1 = 0) to ≈ 3.2× (β1 ≥ 40), non-monotonically in between. β1 = 0 is too tight (OOD +7.3 vs +19.3). | reproduced: lighter harness, and OOD non-inferior within 1 pt (not superior) |
| E6 | pruning removes machinery that stopped helping | prune directives on / off, decaying-rich pool, T = 30, 30 seeds | Dead (superseded) mechanisms −1.5 [−2.2, −0.8]; prune edits accepted +4.1; mechanisms −7.0; tokens −3.8× [−6.7, −0.6]. OOD −2 pts [−5, +1], not significant. | reproduced |
| E7 | the annealed budget improves attribution | anneal (paper values: ceil gives 4 → 2) / const 4 / const 1, 50 seeds | **Hitchhiker rate:** 0.16 vs 0.24 with const 4 (−8 pts [−11, −4]); const 1: 0.02. **Spearman(recorded dS, true marginal):** 0.55 vs 0.50 (const 1: 0.85). **Early progress** (5 rounds): same as const 4, +5 pts [+3, +7] over const 1. **Final unseen:** +1 [−1, +2] vs const 4. The pre-fix realization gave −3.4 [−6.9, +0.3] for the hitchhiker difference, so the direction is stable but the size is moderate. | reproduced (both checks in this rerun) |
| E9 | stall exploration escapes prompt-only collapse | strict collapse (the spec's premise) and mild collapse, 30 seeds | **Strict:** coverage **1.00 vs 0.61**, accepted structural mechanisms 1.03 vs 0.10, true evolve +3 pts [+2, +4], OOD +3 [+2, +4]. **Mild:** coverage 1.00 vs 0.88; the other differences are not significant. | reproduced under the spec's premise; weak under mild collapse |
| E10 | δ calibration: ~97.5% clearance at z = 2 | pools of 300 base evaluations; 5 worlds + 2 AgentQA suites | The code-default bootstrap (1 evaluation, k = 2) underestimates sd_null (×0.69–0.70), giving clearance **91.6% (HW) / 90.7% (AQ)**. The small-k-corrected bootstrap gives **97.5% / 96.8%**. Repeated evaluations give 84% (R = 2), 91% (R = 3), 94% (R = 5). At k = 4 the plain bootstrap gives 95.6%. | **not reproduced with the code's estimator at k = 2**; reproduced with the √(k/(k−1)) correction |
| E12 | the harness transfers to a weaker policy never used in the search | HarnessWorld WEAK policy (50 seeds); AgentQA SimModel(skill 0.6) (5 suites) | Weak-policy unseen gain: RRSI **+16.6 [+14.7, +18.5]** vs unregularized +2.3 [+0.5, +4.2] (paired **+14.4 [+12.3, +16.4]**). Weak OOD: +12.0 vs −2.2. AgentQA: +36.3 [+28.3, +41.9] vs +35.8 [+27.3, +43.4], no difference. | reproduced on HarnessWorld; AgentQA shows positive transfer for both arms (no RRSI advantage) |
| E13 | auditable and resumable; readjudicate changes only Algorithm 2 | 5 HW + 2 AQ seeds; kills after the baseline, draft, evaluation, between candidates' history records, and after settle | Every resume ran in a **separate OS process with a different `PYTHONHASHSEED`**. All resumed runs have byte-identical history, attribution, decisions and frontier, and identical ledger nodes. Readjudicating with δ × 0.5 / × 2 spends **0** rollouts and leaves (S, C, dS, dC) unchanged. Counterfactual replay flips 0–3 of 20 decisions per run. | reproduced. Before the review fixes, AgentQA seed 0 diverged after a cross-process resume (§6.1). |
| live | real LLM end-to-end | AgentQA; haiku as proposer, critic and analyst; SimModel as task model; T = 2 | 17 live calls, **$0.68** across the first (timed-out) attempt and its cached resume. Every haiku done() submission was valid on the first try (0 bounces). The critic accepted 4/4 with detailed reasoning over its six rules. Round 0 accepted a Python-tool pipeline (dS +0.46, dC −0.83). Round 1: both candidates were rejected by the within-band cost rule. Held-out 0.42 → 0.92, OOD 0.46 → 0.96. Run before the review, with pre-review code (the fixes do not touch this path); not repeated, to respect the one-live-run limit. | works end to end (`results/rrsi/live_smoke.json`) |

Not run: E8 (history conditioning with a real LLM) and E11 (domain guards; unit-tested in `tests/test_rrsi_core.py::test_selection_floor_argmax_and_sstar`).

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
| 17 | several domains / graders | any `rsi.core.Domain`: HarnessWorld (rubric simulator), AgentQA (exact match), `FunctionDomain` example (§2) and a new text-normalization `FunctionDomain` in `tests/test_rrsi_generic.py` | E1, E12, tests |
| 18 | compared with prior methods | unregularized and ablation arms here; Meta-Harness / GEPA etc. live in their own packages and share `rsi.core.transfer_report` | E1, E2 |
| 19 | the ranking flips | E1 / E2: highest evolve ↔ lowest OOD | E1, E2 |
| 20 | removing guards: evolve up, unseen down, more tokens | `RegularizerSwitches.none/no_acceptance/no_proposal` | E2 (acceptance and none: yes; proposal: no significant effect) |
| 21 | model independence / weak-policy transfer | `HarnessWorldDomain.with_policy(WEAK)`, `SimModel(skill=0.6)` | E12 |
| 22 | no held-out split got worse | `paired_transfer(...)["non_regression"]` | E1, live |
| 23 | steps per task | `Measurement.extra["steps"]`, `transfer_report` steps | results JSON |
| 24 | statistics, not search | `calibrate`, floor, falsified-idea ledger, split discipline | E4, E10 |
| 25 | guards on proposing and keeping | proposal (`schedule`, `history`, `exploration`) + selection (`critic`, `selection`) | E2 |
| 26 | lighter harness | cost rule + within-band shaped rule + prune | E1 (3.5× vs 63×), E5, E6 |
| 27 | simulator-graded transfer checks | HarnessWorld analytic ground truth (`expected`) | all HW experiments |
| 28 | caveats (error bars, same model family, knobs) | every table has seed CIs; the critic LLM is configurable (`llm_critic`); `Config` presets and sweeps | E5 β1 sweep, E10 |
| 29 | several filtered candidates per round; a record of what failed; human-set rules | `Config.m`, `History`, `constitution.py` (generic SKILL / PATTERNS) | – |
| 30 | measure noise first; keep a set the loop never sees; ban shortcuts; one change at a time; log as a tree; never let the loop grade itself | `calibration_repeats`, `heldout_monitor` (extension, logged only), critic, budget, `rsi.core.Ledger` tree, grader inside `Domain.grade` + critic precheck against harness code importing `rsi` / touching `sys.modules` | E10, E13, `test_rrsi_generic.py` (the loop reads only `evolve`; tampering blocked) |
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
14. **`paired_transfer`** runs each (arm, task, seed) rollout once. The win/tie/loss pass replays `transfer_report`'s trials from a shared trial cache, so a live task model is not paid twice.

## 6. Limitations

* **HarnessWorld is a stylised simulation.**
  * Magnitudes are not calibrated to the paper (unregularized tokens 63× H_0 here vs 2.4× in the paper).
  * The conclusions depend on world assumptions that are now explicit in `WorldConfig`: a random proposal is roughly neutral off the practice set, leaks and practice-tuned instructions are slightly harmful off-target, and a larger harness dilutes context (`context_penalty`; E1 shows the main result holds with it off).
* **Mock proposers are shallow.** They avoid rejected ideas but never "refine what has credit" and do not learn from the scoreboard. So the proposal-side regularizers are tested only mechanically. This is the likely reason "−proposal lowers OOD" did not reproduce (E2: −2.3 [−5.0, +0.3], a trend only): the selection side already filters the larger bundles well.
* **Results depend on the realization.** Several borderline effects changed significance between the pre-review run and the rerun (E7 hitchhiking: n.s. → significant; E3 at 5% leaks: significant → n.s.; E5 OOD non-inferiority: failed → passed). Treat effects whose CI bound sits near 0 as trends.
* **E8 was not run.** Re-proposal of falsified ideas needs a real LLM at scale.
* **AgentQA with SimModel is dominated by one mechanism** (routing computation through the Python tool), which every arm finds. There, the regularizers show up as leak blocking. The token saving (1.19× vs 2.01×) and any transfer gap are not significant with 5 suites.
* **Proposer protocol.** It is single-shot rewrite (or `AgentEditor`) instead of the code's 40-turn JSON-action agent. Traces are inlined and capped (8 traces × 1,200 chars) rather than browsed with `read_trace`. The analyst is likewise single-shot per digest.
* **Live evidence is one small run** (T = 2, haiku, $0.68), made before the review with pre-review code. Headless `claude -p` calls took roughly 1–3 minutes each in this environment. Every experiment script accepts `--llm claude:haiku`. For HarnessWorld, the domain description shows the mechanism catalog (practice-family targets only) so that a real LLM can pick mechanisms. The mock-only knobs (shares, collapse bias, catch rate) have no effect on a live proposer.
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

## 7. Running the tests and experiments

```bash
python -m pytest -q tests/test_rrsi_core.py tests/test_rrsi_toy.py tests/test_rrsi_loop.py tests/test_rrsi_harnessworld.py tests/test_rrsi_generic.py
python experiments/rrsi/e0_overfitting_trap.py            # 5 s, LLM-free
python experiments/rrsi/e1_overfitting.py                 # 50 seeds HW + 5 AgentQA suites
python experiments/rrsi/e2_ablations.py                   # 8 arms x 50 seeds + 2 token-matched arms (~5 min on 3 cores)
python experiments/rrsi/e3_critic.py   ... e13_resume_readjudicate.py
python experiments/rrsi/live_smoke.py --llm claude:haiku  # one small live run, cached in .rsi_cache/rrsi
python experiments/rrsi/e1_overfitting.py --llm claude:haiku --seeds 1 --quick   # live showcase (costs money)
```

The tests (44) take about 6-10 s. Every script accepts `--llm sim|claude:<model>`, `--seeds N`, `--quick` and `--workers`, and writes `results/rrsi/<name>.json` (config, per-seed rows, mean + 95% bootstrap CI, paired differences, checks, verdict) plus a PNG. E0 and E10 are LLM-free and ignore `--llm`.
