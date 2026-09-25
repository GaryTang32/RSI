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

Every file is under 600 lines (the largest, `loop.py`, has 569). Each run directory follows the code's `runs/<domain>/` layout: `frontier.json`, `calibration.json`, `history.jsonl`, `attribution.jsonl`, `global_analysis.json`, `ledger.jsonl`, `artifacts/`, `evals/<job>.json`, and per round `r<t>/{directives,analysis_report,digests,decisions,summary}.json` plus per variant `r<t>/<V>/{proposal*.json, critic_a<n>.json, critic.json, diff.patch, smoke.json, prep.json, eval.json}`.

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

| # | spec claim | arms / N | result (95% CI) | verdict |
|---|---|---|---|---|
| E0 | overview toy: plain loop practises up, unseen flat; guarded loop practises less, transfers better | page defaults, 200 runs × 60 rounds | Exact port; matches the page's JS under Node to 1e-15 with identical accept counts. Plain: practice **+65.4**, unseen **+2.8** pts, 35.2 kept. Guarded: **+19.6** / **+9.3**, 14.5 kept. Guarded − plain unseen = +6.5 [+5.6, +7.4]. The critic alone gives +8.8, the noise margin alone +4.3. With RRSI's z = 2 band the toy keeps only 2.2 changes (unseen +2.4). | reproduced (exact) |
| E1 | unregularized evolution overfits; RRSI transfers; RRSI cheaper | HarnessWorld, 50 seeds, T = 20, m = 2 | Unregularized: measured evolve +44.9, true evolve +44.8, held-out +10.9, OOD **−1.9 [−4.7, +0.9]**, tokens **56×**. RRSI: +26.8 / +26.5 / +24.0 / **+16.5 [+14.3, +18.6]**, tokens **3.2×**. Paired RRSI − unreg: held-out +13.1 [+9.8, +16.3], OOD +18.4 [+14.7, +21.9]. With context dilution off: OOD +13.9 [+9.7, +17.8]. | reproduced |
| E1b | the same on AgentQA | SimModel + scripted proposer, 5 suites, T = 8 | Unregularized kept the leaky answer table in **5/5** runs, RRSI in **0/5**. Unseen gains are similar (+46 vs +44 pts; both find the Python tool), and RRSI uses fewer tokens (0.88× vs 2.0×). | leak blocking reproduced; transfer difference not significant |
| E2 | removing either guard group raises evolve and lowers OOD; no guards is worst and most expensive | 8 arms × 50 seeds | vs full, (Δmeasured evolve, ΔOOD, Δtokens×): no guards **+18.8, −19.2, +52.8**; −acceptance **+16.7, −17.0, +21.8**; −proposal **+3.0 [+0.9, +5.1], +0.8 [−1.8, +3.3], +0.8 [−0.9, +2.2]**. E2b: budget-only OOD −0.9 (≈ no guards); selector-only equals −proposal. Selector-only − budget-only OOD = +19.0 [+15.9, +22.0]. Moving pruning into the proposal group does not change the picture. | acceptance group reproduced; proposal group raises evolve (as in the paper) but **does not lower OOD** here; the budget alone explains none of the OOD gain |
| E3 | the critic blocks benchmark-specific fitting before evaluation | leak share {5, 15, 30%} × critic off / on (obfuscated-leak catch rate 0, .5, .8, 1), 15 seeds | At 15% leaks, on (0.8) − off: leaks kept −7.1 [−8.5, −5.6], evaluations on leaky candidates −12.3, OOD **+11.3 [+6.1, +16.2]**, measured evolve −12.0. With catch 1.0: 0 leaks kept and 0 evaluations spent. Regex-only (catch 0) still keeps the obfuscated leaks (2.3). | reproduced |
| E4 | the floor stops downhill walks; the null false-accept rate is near the calibrated level | drift pool (−0.5 pt, −10% tokens), 40 rounds, 20 seeds; null pool | True evolve change: S* floor **−2.5 [−2.7, −2.3]** (≈ 1.6 δ); S_t floor −11.9; no floor **−30.4**; keep-if-better −1.0. Nulls: the fraction with dS > δ is 0.2% (bootstrap δ), 1.0% (R = 5 δ), 2.4% (coding rule). The nominal rate at the calibrated δ is 8%; winner's-curse inflation of the incumbent pushes the observed rate lower. | reproduced |
| E5 | the cost rule earns its tokens; lighter harness, equal or higher OOD | β1 sweep {0 … 160}, cost rule off, whole complexity term off, 20 seeds | Tokens rise monotonically with β1 (1.3× at β1 = 0 → 3.8× at 80). Default β1 = 40: 2.8× vs 9.8× with the complexity term off (paired −7.0× [−12.5, −3.5]). OOD −0.4 [−5.0, +4.6] pts: no detectable loss, but the CI is too wide to certify "equal". β1 ≤ 5 is too tight (OOD +7.9 at β1 = 0). | lighter harness reproduced; OOD equality not significant |
| E6 | pruning removes machinery that stopped helping | prune directives on / off, decaying-rich pool, T = 30, 30 seeds | Dead (superseded) mechanisms −1.6 [−2.4, −0.7], prune edits accepted +4.2, tokens −3.8× [−5.8, −1.5], OOD unchanged (±0.0) | reproduced |
| E7 | the annealed budget improves attribution | anneal / const 4 / const 1, 50 seeds | Hitchhiker rate 0.17 vs 0.20 (−3.4 pts [−6.9, +0.3]). Spearman(recorded dS, true marginal): 0.54 vs 0.50 (const 1: 0.85). Early progress is identical to const 4 (b_t = 4 for the first 8 rounds) and +4.8 pts [+3.1, +6.4] over const 1. Final unseen: −1.7 pts [−3.3, −0.1] vs const 4. | faster than b = 1 reproduced; lower hitchhiking only as a trend (not significant) |
| E9 | stall exploration escapes prompt-only collapse | strict collapse (the spec's premise) and mild collapse, 30 seeds | Strict: coverage **1.00 vs 0.59**, accepted structural mechanisms 1.2 vs 0.07, true evolve +3.2 pts [+2.2, +4.3], OOD +2.8 [+1.8, +3.8]. Mild: coverage 1.00 vs 0.90; other gains not significant. | reproduced under the spec's premise |
| E10 | δ calibration: ~97.5% clearance at z = 2 | pools of 300 base evaluations; 5 worlds + 2 AgentQA suites | Code-default bootstrap (1 eval, k = 2) underestimates sd_null (×0.69–0.71) → clearance **91.8% (HW) / 90.7% (AQ)**. The small-k-corrected bootstrap gives **97.5% / 96.8%**; repeated evaluations give 84% (R = 2), 91% (R = 3), 94% (R = 5). At k = 4 the bootstrap gives 95.3%. | **not reproduced with the code's estimator at k = 2**; reproduced with the √(k/(k−1)) correction |
| E12 | the harness transfers to a weaker policy never used in the search | HarnessWorld WEAK policy (50 seeds); AgentQA SimModel(skill 0.6) (5 suites) | Weak-policy unseen gain: RRSI **+15.7 [+13.9, +17.5]** vs unregularized +2.5 [+0.5, +4.4] (paired +13.2 [+10.9, +15.6]); weak OOD: +11.6 vs −1.8. AgentQA: +36.2 vs +30.6. | reproduced |
| E13 | auditable and resumable; readjudicate changes only Algorithm 2 | 5 HW + 2 AQ seeds; kills after the baseline, draft, evaluation, between history records, and after settle | All resumed runs have byte-identical history, attribution, decisions and frontier and identical ledger nodes. Readjudicating with δ × 0.5 / × 2 spends **0** rollouts and leaves (S, C, dS, dC) unchanged; counterfactual replay flips 0–3 of 20 decisions per run. | reproduced (after fixing two resume bugs; see §5.11) |
| live | real LLM end-to-end | AgentQA, haiku as proposer, critic and analyst; SimModel as task model; T = 2 | 17 calls, **$0.68**. Every haiku done() submission was valid on the first try (0 bounces). The critic accepted 4/4 with detailed reasoning over its six rules. Round 0 accepted a Python-tool pipeline (dS +0.46, dC −0.83). Round 1: both candidates rejected by the within-band cost rule. Held-out 0.42→0.92, OOD 0.46→0.96. | works end to end (`results/rrsi/live_smoke.json`) |

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
| 7 | forced variety when stuck | `history.stall_flag` / `exploration`; reserved slot checked in `done()` and on the diff after the critic | E9 |
| 8 | leakage critic, generic edits pass | `RRSICritic` (core denylist + regex + LLM review), repair loop `repair_rounds` | E3, live |
| 9 | noise floor from repeated runs of the unchanged agent | `calibrate.calibrate` (repeats / bootstrap) + frontier `S_star` + `NoiseFloor` | E4, E10 |
| 10 | cost must be earned | `Measurement.C` + `RRSICostRule` | E5 |
| 11 | pruning flagged for deletion (selection side) | `History.prune_set` → proposer context; prune edits pass critic + Algorithm 2; ablation switch in the acceptance group | E6, E2 |
| 12 | regularization analogies | §4.6 mapping; `schedule` (L0), prune (L1), cost rule (L2), exploration (entropy), floor + history (holdout reuse) | this page |
| 13 | b_t formula, illustrative 1 / 4 / 10 | `edit_budget`, `Config.preset("overview")` | `test_schedule_tables_from_spec` |
| 14 | score ≥ best − δ | `NoiseFloor` with `GateContext.best_score = S*` | E4 |
| 15 | ΔC ≤ β0 + β1·gain, frozen for transfer | `RRSICostRule`; config frozen in `frontier.json`; `paired_transfer` never mutates it | E5 |
| 16 | evolve on one set, run unchanged on unseen; same budget for every arm | sealed `holdout` / `ood` splits, `paired_transfer`, `RegularizerSwitches` arms sharing T, m, k | E1, E2 |
| 17 | several domains / graders | any `rsi.core.Domain`: HarnessWorld (rubric simulator), AgentQA (exact match), `FunctionDomain` example (§2) | E1, E12 |
| 18 | compared with prior methods | unregularized and ablation arms here; Meta-Harness / GEPA etc. live in their own packages and share `rsi.core.transfer_report` | E1, E2 |
| 19 | the ranking flips | E1 / E2: highest evolve ↔ lowest OOD | E1, E2 |
| 20 | removing guards: evolve up, unseen down, more tokens | `RegularizerSwitches.none/no_acceptance/no_proposal` | E2 (acceptance and none: yes; proposal: evolve up only) |
| 21 | model independence / weak-policy transfer | `HarnessWorldDomain.with_policy(WEAK)`, `SimModel(skill=0.6)` | E12 |
| 22 | no held-out split got worse | `paired_transfer(...)["non_regression"]` | E1, live |
| 23 | steps per task | `Measurement.extra["steps"]`, `transfer_report` steps | results JSON |
| 24 | statistics, not search | `calibrate`, floor, falsified-idea ledger, split discipline | E4, E10 |
| 25 | guards on proposing and keeping | proposal (`schedule`, `history`, `exploration`) + selection (`critic`, `selection`) | E2 |
| 26 | lighter harness | cost rule + within-band shaped rule + prune | E1 (3.2× vs 56×), E5, E6 |
| 27 | simulator-graded transfer checks | HarnessWorld analytic ground truth (`expected`) | all HW experiments |
| 28 | caveats (error bars, same model family, knobs) | every table has seed CIs; the critic LLM is configurable (`llm_critic`); `Config` presets and sweeps | E5 β1 sweep, E10 |
| 29 | several filtered candidates per round; a record of what failed; human-set rules | `Config.m`, `History`, `constitution.py` (generic SKILL / PATTERNS) | – |
| 30 | measure noise first; keep a set the loop never sees; ban shortcuts; one change at a time; log as a tree; never let the loop grade itself | `calibration_repeats`, `heldout_monitor` (extension, logged only), critic, budget, `rsi.core.Ledger` tree, grader inside `Domain.grade` | E10, E13 |
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
* **Mock proposer shares** (per proposal, by kind): generic .15, structural .07, narrow .05, leak .10, obfuscated .05, null .18, costly .12, harmful .20, decaying .08. The mock proposer never sees true effects.

## 5. Deviations from the released code (deliberate, documented)

1. **No git.** Candidates are content-addressed artifacts, and the artifact id stands in for the git tree hash. "Fast-forward" becomes an update of the frontier's incumbent, and branches/worktrees become artifact ids in `prep.json`. Diff paths are stored relative to the run directory.
2. **Proposer protocol.** The code runs a multi-turn JSON action agent (list/read/edit/write/done, 40 turns). Here one `rsi.core.Editor` call returns whole files plus the done() JSON, or an `AgentEditor` edits in place. The done() validation is the code's: required fields, K, ≤ b_t, reserved slot, zero-change bounce and abort bounce. Bounces are capped by `max_done_bounces = 3` instead of the 40-turn limit. The "zero file changes" check compares against the incumbent (the whole candidate) rather than counting this call's edit actions.
3. **Analyst and digesters.** The code gives them tool-using multi-turn loops (digest_many, grep/read). Here there is one digester call per trace with the rendered trace inline, and one aggregation call, using the same prompts, schemas and rules. `Config.analyst="heuristic"`, the offline default when no analyst LLM is given, is a deterministic clusterer over grader feedback. `max_digests = 8` per round.
4. **Critic precheck.** It uses `rsi.core.LeakageCritic` over *added* diff lines. The code greps the whole diff, which would block an edit that *deletes* leaked lines. Domain denylist terms (task ids, answers, entities) apply automatically. The LLM review prompt is the code's.
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
11. **Resume fix (differs from the code).** Algorithm 2 uses `incumbent_component_counts(before_t=t)`, i.e. accepted edits from rounds < t only, so a round resumed after a partial history write reproduces the same novelty ν. Ledger nodes are upserted so their order survives a resume.

## 6. Limitations

* **HarnessWorld is a stylised simulation.**
  * Magnitudes are not calibrated to the paper (unregularized tokens 56× H_0 here vs 2.4× in the paper).
  * The conclusions depend on world assumptions that are now explicit in `WorldConfig`: a random proposal is roughly neutral off the practice set, leaks and practice-tuned instructions are slightly harmful off-target, and a larger harness dilutes context (`context_penalty`; E1 shows the main result holds with it off).
* **Mock proposers are shallow.** They avoid rejected ideas but never "refine what has credit" and do not learn from the scoreboard. So the proposal-side regularizers are tested only mechanically. This is the likely reason "−proposal lowers OOD" did not reproduce (E2): larger bundles, filtered by the selection side, accumulate good mechanisms faster within T = 20.
* **E8 was not run.** Re-proposal of falsified ideas needs a real LLM at scale.
* **AgentQA with SimModel is dominated by one mechanism** (routing computation through the Python tool), which every arm finds. There, the regularizers show up as leak blocking and a cheaper harness, not as a transfer gap.
* **Proposer protocol.** It is single-shot rewrite (or `AgentEditor`) instead of the code's 40-turn JSON-action agent. Traces are inlined and capped (8 traces × 1,200 chars) rather than browsed with `read_trace`. The analyst is likewise single-shot per digest.
* **Live evidence is one small run** (T = 2, haiku, $0.68). Headless `claude -p` calls took roughly 1–3 minutes each in this environment. Every experiment script accepts `--llm claude:haiku`; for HarnessWorld, the domain description shows the mechanism catalog so that a real LLM can pick mechanisms.
* **The within-task bootstrap δ** (the faithful default) under-covers at k = 2 (E10). `bootstrap_small_k_correction=True` or `calibration_repeats ≥ 5` are the recommended settings for new problems.
* **E13 bugs, found and fixed.** Two resume bugs appeared at the kill point between history records within a round.
  * Novelty counts included the round's own partial records. The released code has the same pattern (`incumbent_component_counts()` is computed after partial records may exist); we now count rounds < t only.
  * Ledger nodes were re-added with a reset `seq`.
* **Paper-level claims not reproducible here:** absolute benchmark numbers (Terminal-Bench, Harvey LAB, EngDesign) and cross-provider policies (Gemini). Only their CPU analogues are tested.

## 7. Running the tests and experiments

```bash
python -m pytest -q tests/test_rrsi_core.py tests/test_rrsi_toy.py tests/test_rrsi_loop.py tests/test_rrsi_harnessworld.py
python experiments/rrsi/e0_overfitting_trap.py            # 5 s, LLM-free
python experiments/rrsi/e1_overfitting.py                 # 50 seeds HW + 5 AgentQA suites
python experiments/rrsi/e2_ablations.py                   # 8 arms x 50 seeds (the heaviest, ~25 min on 3 busy cores)
python experiments/rrsi/e3_critic.py   ... e13_resume_readjudicate.py
python experiments/rrsi/live_smoke.py --llm claude:haiku  # one small live run, cached in .rsi_cache/rrsi
python experiments/rrsi/e1_overfitting.py --llm claude:haiku --seeds 1 --quick   # live showcase (costs money)
```

The tests (35) take about 4-6 s. Every script accepts `--llm sim|claude:<model>`, `--seeds N`, `--quick` and `--workers`, and writes `results/rrsi/<name>.json` (config, per-seed rows, mean + 95% bootstrap CI, paired differences, checks, verdict) plus a PNG. E0 and E10 are LLM-free and ignore `--llm`.
