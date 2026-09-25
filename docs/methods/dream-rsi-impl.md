# Dream-RSI: implementation notes (`rsi.dream`, `rsi.domains.discovery`)

This page documents how the Dream-RSI spec (`docs/methods/dream-rsi.md`) is implemented on the shared `rsi.core` API: the module map, the public API with a copy-pasteable example, how each capability-checklist item maps to code, experiment and result, deviations from the paper, and limitations. The official code is unreleased (spec §8.16), so everything here is a reimplementation from the paper text, the Listing 1/2 prompts and the overview's demo script.

All numbers come from `results/dream-rsi/*.json`, produced by `experiments/dream-rsi/*.py` at the default offline settings (`--llm sim`). CIs are 95% bootstrap CIs over seeds (`rsi.core.stats.summarize_runs` / `paired_diff_ci`).

## 0. Results at a glance

| # | Claim | Verdict | Key numbers |
|---|---|---|---|
| demo | Overview demo: 3 branches × 5 attempts, W=3, β1=0.010, β2=0.005 | **Reproduced exactly** | refine everything 0.575, drop the flat branch **0.611**, stop early 0.475; identical reveal-round maps; replayed in the subprocess sandbox |
| E1 | Replay is an exact, near-free simulator | **Reproduced** | 64/64 replays identical to the live rollout (in-process, sandboxed, and after a Ledger round trip); 0 agent calls during replay, counted on the agent object; 3.7 ms/episode in-process, 92 ms in the subprocess sandbox (process start included; both measured on a shared, loaded 4-CPU box); a real autoresearch ledger (stored in `results/dream-rsi/inputs/`) converts to a 24-attempt, 18-branch world |
| E2 | Off-policy replay ranks policies usefully | **Reproduced for in-support policies, with measured caveats** | Spearman(replay, true online value) = 0.92 in support; out-of-support plans are mis-estimated (+0.14 with clipping); the Listing-2 "no reward" rule restores the ranking (0.91 → 0.93); context coupling drops ρ to 0.80 |
| E3 | Dream improves quality vs discovery calls (vs Recursive Fixed Exploration) | **Fewer calls per round: reproduced everywhere. Better quality at equal budget: only on synthetic worlds, and there a hand-tuned smaller fixed grid does as well. Real CPU tasks: comparable (sum-diff, circle packing, autocorrelation: non-inferior within 5% of Fixed's gain over the seed) or inconclusive (Lasso)** | Re-run after the claims-audit fixes (W = grid width, identical per-round budgets, new autocorrelation task). Synthetic: +0.053 [+0.040, +0.067] vs the paper's fixed grid at equal budget, 19/20 wins, 1.57× fewer calls to Fixed's final best. **Control**: the best fixed grid chosen in hindsight (3×3) ties Dream (−0.0017 [−0.0145, +0.0099]), i.e. the gain is "spend fewer calls per round", which Dream finds without tuning. After 5 rounds the real tasks use 31–39% fewer agent calls (sum-diff 66, circle packing 61, Lasso 69, autocorr 66 vs 100) at similar or lower quality; counting the policy developer's requests, 19–27% fewer. Lasso search score +11.0 [−6.3, +39.5], held-out −1.2 [−11.0, +11.7] (n = 4). No discovered numpy solver beats scikit-learn on any held-out instance (0/16). Selecting by the Pareto sweep reward instead of Eq. 1 gives the same verdicts |
| E4 | Safe selection: incumbent is a candidate | **Reproduced (replay)** | V\*−V0 ≥ 0 in 150/150 phases; without the incumbent 4/75 and 9/75 phases regress (worst −0.32); online effect negligible |
| E5 | Replay beats written advice | **Reproduced on synthetic worlds (a mechanism check under the generator's depletion assumption); not reproduced on circle packing; "helps early" not reproduced** | Re-run with W = grid width. Synthetic: guidance −0.056 on Fixed and −0.061 on Dream at equal budget, both CIs < 0; direction diversity 1.00 → 0.47/0.55. Circle packing: guidance marginally *better* (+0.0003 Fixed, +0.0010 Dream; saturated task, n = 3) |
| E6 | Learned pacing | **Conserving compute: reproduced. Re-expanding after plateaus: reproduced on sum-difference, weak (n.s.) on synthetic** | Re-run with W = grid width and identical per-round budgets. Effort after round 1: 17 vs 30 (synthetic), 13 vs 20 (sum-diff). Sum-diff: round-controlled change in calls after a plateau minus after an improving round, clustered by seed, +1.8 [+0.6, +3.0] (7 seeds); synthetic +1.9 [−0.3, +4.0] (6 seeds) |
| E7 | One objective, explicit dials | **β1 and policy beta reproduced; β2 only at large values** | ρ(β1, probes) = −0.74; policy beta: ρ(beta, probes) = +0.96, ρ(beta, attainment) = +0.92; β2 changes the argmax only from β2 ≥ 0.1 (the doc's 0.005 changes nothing) |
| E8 | No-peeking constraint matters | **Reproduced** | Two cheaters win selection without the guard (replay +0.392 vs honest +0.218). `oracle` reads the hidden tree (inflated +0.119 over its online value; its online fallback happens to be competitive). `peek_reset` uses only the public API and passes the static check, but explores, remembers, `reset()`s and walks to the best (inflated +0.237; online 0.155 vs honest 0.242). The guard disqualifies both in 8/8 worlds (−1.10, below every honest episode). **Cross-episode memory** (`memo_module`, claims audit N1): with one policy namespace for all episodes it inflates its sweep reward by +0.372 over its own honest behaviour; with a fresh namespace per episode (the fix) it equals parallel refine in both runners, and the static check rejects it |
| E9 | Selection can overfit past worlds | **Reproduced; the guarded selector helps partly** | argmax gap (dev − fresh V) grows with M: t=1 +0.070 → +0.175; t=3 +0.013 → +0.119; the guarded selector narrows it (t=3 at M=32: +0.088; t=6: +0.063 vs +0.094) and halves how often the pick is worse than the incumbent on fresh worlds (t=6, M=8: 17% → 8%), but its fresh-world value is within the CIs of the argmax pick |
| E10 | Replay can only test what was tried | **Reproduced** | go-deeper policy: replay error −0.013 on a shallow record (out of support), exactly 0.000 when the record covers its depth; all in-support errors are 0.000 |
| live | Live end-to-end runs (Claude Haiku as agent and developer) | **Pipeline works end to end** with the verbatim Listing-1/Listing-2 prompts | Live smoke (re-run after the claims-audit fixes, fresh cache): sum-difference Γ 0.9105 → 1.0194 in 12 real agent calls (12 ok); Haiku's rewritten policy passed the static check and tied the incumbent on replay (0.955 vs 0.955), so the incumbent was kept; $0.72, of which the developer is 14%; 10 min. Validation run `sumdiff_live_c`: Γ 0.9105 → 1.0468 in 18 calls, $1.46 (developer 21%), step audit 49/49 correct |

## 1. Module map

| module | role (paper symbol) | built on |
|---|---|---|
| `rsi/dream/policy_api.py` | the policy-facing API, i.e. the paper's `see.policy.api` + `observation_signal`: `Observation`, `CellMeta`, `GridPlan`, `GridPlanningContext`, `LLMDesignedMethod`, `SimResult`, `_budget_done`, `_record_curve`, `finalize_result`, helper signals (`branch_promising`, `branch_failed_hard`, `probe_improved_vs_parent/baseline`, `is_repairable`, `branch_trajectories`) and `QuestionProxy`. Standard library only: it is copied verbatim into the policy sandbox | – |
| `rsi/dream/tree.py` | `DiscoveryNode` / `DiscoveryTree` (root + chains; cell ids `b{branch}.a{attempt}`; JSON; support fields; `render_grid`), `to_ledger` / `from_ledger` (any method's ledger → replay world; exact inverse for Dream ledgers), `SnapshotStore` | `rsi.core.Ledger`, `ArtifactStore` |
| `rsi/dream/question.py` | `GridQuestion` (legal roots/frontiers inside the plan, batch legality, N, k, batch sizes, per-round log), `ReplayQuestion` (deterministic reveal; `root_mode` earliest/addressable; missing continuation; plan ∩ support), `OnlineQuestion` (W-thread pool of agent + evaluator, workspace snapshots, call budget), `workspace_of` / `program_only` | – |
| `rsi/dream/guard.py` | `PrefixGuard` (parent-side firewall), `InProcessRunner` (module re-executed per episode), `SubprocessRunner` (JSON-RPC sandbox with rlimits, an fd firewall, a fixed hash seed and a fresh `fork()` per episode) | `rsi.core.sandbox` env rules |
| `rsi/dream/lint.py` | `static_check`: the AST lint run before a policy ever executes (structure, imports, forbidden names / attributes, state that could outlive an episode) | – |
| `rsi/dream/policy.py`, `policy_templates/` | policies as code (`method.py` artifacts), `PARAMS` read/rewrite; templates `parallel_refine` (π₁), `adaptive` (Listing-2 decision loop), `rules` (demo + zoo), `oracle` (cheater for E8) | `rsi.core.Artifact` |
| `rsi/dream/objectives.py` | `EpisodeResult`, `Eq1Objective` (paper Eq. 1, raw or per-world normalized, clip / no-reward support), `ParetoSweepObjective` (Listing-2 `pareto.auc − λ·parallel_penalty`) | – |
| `rsi/dream/evaluator.py` | `ReplayEvaluator` (plan_grid per world with prefix-safe manifests → replay → report; optional beta sweep), `PolicyReport` (value, per-world, sweep = `beta_sweep.json`, traces = `policy_execution_traces.jsonl`, diagnostics) | – |
| `rsi/dream/developer.py` | `LLMPolicyDeveloper` (Listing-2 prompt, context files in the paper's layout: every earlier version with its traces, static check + leakage screen + one repair round), `ParametricMutator` (feedback-directed mock developer), `choose_default_beta` (cross-cycle rule), `mock_developer_llm` | `rsi.core.RewriteEditor` / `AgentEditor`, `LeakageCritic` |
| `rsi/dream/prompts.py` | Listing 2 verbatim (`DEVELOPER_PROMPT`), the runner's additions kept apart (`FRAMEWORK_NOTES`), the objective paragraph (`objective_text`), `developer_prompt` | – |
| `rsi/dream/selection.py` | `Selector` (argmax incl. incumbent; `include_incumbent=False` ablation), `GuardedSelector` (held-out worlds + noise margin) | `rsi.core.gates.MinGain`, `select` |
| `rsi/dream/agent.py` | `EvalOutcome`, `DiscoveryTask`, `DomainTask` (any `rsi.core.Domain`), `AttemptContext` / `AttemptRecord`, `EditorAgent` (Listing-1 prompt), `ParametricAgent` (mock), `DirectionProvider` | `rsi.core.Evaluator`, `Editor` |
| `rsi/dream/baselines.py` | Recursive Fixed Exploration (`fixed_config`), guidance summarizers (`MockGuidanceSummarizer`, `LLMGuidanceSummarizer`) | – |
| `rsi/dream/cost.py` | `CostMeter`: agent calls / tokens / $ kept separate from replay episodes and CPU, and from developer calls | `rsi.core.llm.Usage` |
| `rsi/dream/loop.py` | `Config`, `DreamRSILoop` (§3.2 outer loop, manifests, archives, ledgers), `run(...)`, `as_task` | `rsi.core.run.ImprovementResult` |
| `rsi/dream/demo.py` | the overview demo (tree, three strategies as code, render) | – |
| `rsi/dream/tracing.py` | `DreamTracer`: the per-iteration audit trace (`rsi.trace` format, `trace.jsonl`); `ShadowLLM` (the shadow monitor's spend, metered apart) | `rsi.trace.RunTracer`, `ShadowMonitor` |
| `rsi/domains/discovery/base.py` | `ProgramDomain`: program artifact + locked evaluator with typed `fail_class`, optional subprocess sandbox | `rsi.core.Domain`, `call_function` |
| `rsi/domains/discovery/lasso.py` | Lasso path: CPU-timed search instances, correctness gate `F_k ≤ F_k(sklearn ref) + 1e-6` on fresh instances, `holdout` split, mock agent over solver mechanisms | numpy, scikit-learn |
| `rsi/domains/discovery/sumdiff.py` | sum-difference Γ(A), staged construction recipe, mock agent (hill / anneal / window / fringe / grow) | numpy |
| `rsi/domains/discovery/circlepack.py` | circle packing n=26, SLSQP + exact LP radii with an exact feasibility repair, mock agent that resumes from the parent's measured packing | scipy |
| `rsi/domains/discovery/autocorr.py` | autocorrelation inequalities Φ1/Φ2/Φ3 (App. A Problem 4): step functions on [−1/4, 1/4], exact objectives from the discrete autoconvolution, mock agent (coord / gradient / smooth / upsample / shake) that resumes the parent's measured function; default Φ3 (Table 1's column) | numpy |
| `rsi/domains/discovery/synthetic.py` | synthetic discovery worlds (typed branches, depleting directions, typed failures, CRN), ground truth (`ground_truth`, `truth_for_tree`), optional context coupling | – |
| `rsi/domains/discovery/agentqa_agent.py` | an offline discovery agent for the shared `agentqa` harness domain (shows Dream-RSI on a generic `rsi.core.Domain`) | `rsi.domains.agentqa` |

The largest files are `loop.py` (≈580 lines), `guard.py` (≈570 lines) and `question.py` (≈525 lines); all files are under 700 lines.

**Run directory** (`out_dir`): `discovery.jsonl` (every live attempt as a `rsi.core.Ledger` tree, prefixed `t<iter>/`), `policies.jsonl` (policy lineage with replay values; status deployed/candidate/discard/rejected), `trace_pool/iter<t>/{tree.json, live_cycle_manifest.json, guidance.json}`, `history/baseline/method.py`, `history/r####_<label>/{method.py, report.json, proposal_results/{beta_sweep.json, policy_execution_traces.jsonl}}`, `snapshots/` (every workspace), `final_policy/method.py`, `trajectory.json`, `summary.json`, `best_artifact/`.

## 2. Public API and applying Dream-RSI to a new problem

```python
from rsi.dream import Config, run
res = run(domain, seed_artifact=None, *, llm_task=None, llm_propose=None, llm_develop=None,
          config=Config(...), out_dir=None, agent=None, developer=None, initial_policy=None, summarizer=None)
# -> rsi.core.ImprovementResult
```

* `domain` can be any `rsi.core.Domain` (wrapped by `DomainTask`: score = mean score from the locked grader on the `evolve` split, or on `train` / `val` when the domain has no `evolve`; a sealed or empty split raises instead of scoring everything 0), a discovery domain from `rsi.domains.discovery`, or a `DiscoveryTask`. `task_kwargs` (e.g. `{"directions": [...], "k": 3}`) configure the `DomainTask` wrapper; they raise for discovery domains, which are configured directly.
* `llm_propose` becomes the discovery agent (`EditorAgent`, Listing-1 prompt). `llm_develop` (default `llm_propose`) becomes the policy developer (`LLMPolicyDeveloper`, Listing-2 prompt). Without LLMs, `run` uses `domain.mock_agent()` and `ParametricMutator`.
* `res.best` is the best discovered program and `res.baseline` the seed. `res.trajectory` has one row per live search: calls, cumulative calls, round best, best, plan, N, k, batch sizes, beta, and the dreaming phase (values of all versions, selection, reason, changes).
* `res.meta` holds `policy` (final deployed `method.py`), `policy_ledger`, `worlds` (replay worlds), `manifests`, `cost` and `config`. `res.usage` holds per-role LLM usage plus `_cost` (the `CostMeter`).
* `Config(dream=False)` is Recursive Fixed Exploration and `Config(guidance=True)` is the written-advice ablation.

Key `Config` fields and defaults: `rounds=5, W=4, branch_count=4, refine_count=4, M=4` (versions incl. incumbent; `m_semantics="revisions"` for the other reading of the paper's off-by-one), `objective="eq1"` (or `"pareto"`), `beta1=0.01, beta2=0.005, normalize=True, support="clip"`, `root_mode="earliest"`, `selector="argmax"` (or `"guarded"`), `include_incumbent=True`, `sandbox="subprocess"`, `root="best"`, `max_calls=None`, `K1=K2=None`.

**A new problem, end to end** (`experiments/dream-rsi/example_new_problem.py`, runnable offline or with `--llm claude:haiku`):

```python
import ast
import numpy as np
from rsi.core import Artifact, FunctionDomain, Task, TaskSuite
from rsi.dream import Config, DomainTask, ParametricAgent, run

GRID = np.linspace(0.0, 1.0, 201)

def execute(artifact, task, seed, llm):            # run the candidate program
    ns = {}
    exec(artifact["approx.py"], ns)                 # use rsi.core.sandbox.call_function for untrusted code
    return [float(c) for c in ns["coeffs"]()]

def grade(task, coeffs):                            # the locked grader (larger is better)
    err = np.max(np.abs(np.polyval(coeffs[::-1], GRID) - np.exp(GRID)))
    return 1.0 / (1.0 + 1000.0 * err), f"max error {err:.2e}"

def mutate(parent, rng, ctx):                       # offline stand-in for a coding agent
    c = ast.literal_eval(parent["approx.py"].split("C = ", 1)[1].split("\n", 1)[0])
    k = int(ctx.direction["direction"][1:]) if ctx.direction else rng.randrange(len(c))
    c[k] += rng.gauss(0.0, 0.05 / (1 + k))
    return Artifact({"approx.py": f"C = {c!r}\n\ndef coeffs():\n    return C\n"}), f"# nudge c{k}"

domain = FunctionDomain(TaskSuite([Task("exp01", None)], {"evolve": ["exp01"]}), execute, grade,
                        description="approx.py: coeffs() -> polynomial approximating exp on [0, 1]")
seed = Artifact({"approx.py": "C = [1.0, 1.0, 0.5, 0.0]\n\ndef coeffs():\n    return C\n"})
task = DomainTask(domain, seed, directions=["c0", "c1", "c2", "c3"])
res = run(task, agent=ParametricAgent(mutate), config=Config(rounds=6, W=4, branch_count=4, refine_count=3))
print(res.meta["seed_score"], "->", res.meta["best_score"], [r["calls"] for r in res.trajectory])
```

With a real model, replace the agent with `EditorAgent(ClaudeCLI("haiku"), editable=["approx.py"])` and pass `developer=LLMPolicyDeveloper(ClaudeCLI("haiku"))`, or just `run(domain, seed, llm_propose=CachedLLM(ClaudeCLI("haiku"), cache_dir))`. Offline, the example improves the score 0.0046 → 0.0113 while the deployed policy cuts its calls per search from 16 to 8–11. `tests/test_dream-rsi_review.py::test_generic_new_function_domain_through_llm_paths` does the same for another new `FunctionDomain` through the *LLM* paths (Listing-1 `EditorAgent` + Listing-2 `LLMPolicyDeveloper`, both on deterministic mock LLMs), with the policy in the subprocess sandbox and a sealed holdout split that the loop never touches. `tests/test_dream-rsi_review2.py::test_generic_train_split_domain_pareto_guarded_subprocess` adds a third new problem (fit the coefficients of a quadratic; JSON artifact; only a `train` split for decisions) passed to `run(domain, seed, ...)` as a plain `rsi.core.Domain`, with the Pareto objective, the guarded selector and the subprocess sandbox; it improves the sealed holdout and is deterministic.

Other entry points:

* `ReplayEvaluator(objective, W=..., runner="subprocess").evaluate(code, worlds, manifests=...)` scores any policy code on any worlds.
* `DiscoveryTree.from_ledger(Ledger(path))` turns an RRSI, autoresearch, GEPA or other ledger into a replay world. E1 ran it on a real autoresearch ledger: 24 attempts became 18 branches, because every discarded candidate forks from the incumbent.
* `rsi.dream.run_demo()` reproduces the overview demo.

## 3. Capability checklist → code → experiment → result

| # | Capability (spec §10) | Code | Evidence |
|---|---|---|---|
| 1 | attempts resume a saved attempt; code, diagnostics, score stored as tree nodes | `OnlineQuestion._one` (restores the parent workspace), `workspace_of` (program + `proposal.md` + `eval/score.json` + `error.txt`), `SnapshotStore`, `DiscoveryNode` | E1; the circle-packing agent literally resumes from the parent's measured packing |
| 2 | record every attempt, replay strategies over the map | `ReplayQuestion`, `ReplayEvaluator` | E1 (64/64 identical), E2 |
| 3 | finished tree added to the pool of replay worlds | `DreamRSILoop.run` (`self.worlds`), `trace_pool/iter<t>/tree.json`, `live_cycle_manifest.json` | tests `test_dream_run_logs_everything...` |
| 4 | strategy written as code; sees tree so far, picks ≤ W starting points | `policy_templates/*.py`, `QuestionProxy`, `max_parallelism` | all experiments |
| 5 | pick opens a branch or extends the latest attempt; empty batch = stop | `GridQuestion.legal_roots/legal_frontiers/validate_batch`, templates stop on empty batch | `test_batch_legality` |
| 6 | in replay picking a node reveals the recorded child | `ReplayQuestion._transition` (root → earliest-created unrevealed branch; leaf → recorded child; missing → nothing, branch exhausted, k counts) | `test_root_opens_earliest...`, `test_exhausted_leaf...` |
| 7 | only revealed information; no peeking | `PrefixGuard`, `QuestionProxy` (violations reported and disqualifying; `reset()` allowed only before the first probe), `SubprocessRunner` (hidden world never enters the process; open-file limit; fixed hash seed), `static_check` (incl. cross-episode state), a fresh policy **namespace** per episode (module re-executed in-process, a fresh `fork()` in the sandbox); disqualified episodes score below every honest one (`Eq1Objective.disqualified_value`) | E8 (three cheaters); `test_proxy_blocks...`, `test_sandbox_cannot_open_files`, `test_reset_after_probing_is_a_guard_violation`, `test_memo_cheater_gains_nothing_across_replay_episodes` |
| 8 | replay score = best − β1·attempts + β2·attempts/round | `Eq1Objective` (+ `ParetoSweepObjective`) | demo (exact), `test_eq1_hand_computed_toy`, E7 |
| 9 | policy developer rewrites code many times; every version scored on every past search | `LLMPolicyDeveloper`, `ParametricMutator`, `DreamRSILoop._dream`, `history/r####_*/`, `policy_execution_traces.jsonl` | `test_llm_developer_path_offline`, live smoke |
| 10 | deploy the best version incl. the current one | `Selector` | E4 |
| 11 | models, agent, evaluator fixed | only `method.py` changes; agent/evaluator objects are constructed once per run | – |
| 12 | best average replay score over every past search | `ReplayEvaluator.evaluate` mean over all worlds | E4/E9 |
| 13 | online vs replay (runs agent vs reveals; random vs fixed; costly vs free; new ideas vs only recorded) | `OnlineQuestion` vs `ReplayQuestion` behind one API; `CostMeter` | E1 (0 agent calls counted on the agent object, ms per episode), E10 (support) |
| 14 | Recursive Fixed Exploration baseline, same budgets | `Config(dream=False)`, `parallel_refine` template, `max_calls`, and identical per-round budgets (`Config.round_budget="fallback"`, the default) | E3, E5 (per-round budget check in E3's `per_round_budget_check`) |
| 15 | Lasso/math/kernel results (fewer calls) | discovery domains + E3 | E3 (see §5) |
| 16 | replay beats written advice | `guidance=True`, summarizers, `DirectionProvider(guidance)` | E5 |
| 17 | pacing | policy `beta` + `_schedule`, `choose_default_beta`, `plan_grid` width/depth rules, per-round effort in manifests | E6 (the pacing observed offline comes mostly from `plan_grid` / PARAMS edits: the cross-cycle default-beta rule moved the default in only 3/20 synthetic, 2/10 sum-diff, 4/5 circle-packing, 2/4 Lasso and 2/10 autocorrelation E3 runs) |
| 18 | history as executable simulator, not notes | replay vs guidance | E5 |
| 19 | improves the search almost for free | `CostMeter` separates agent calls from replay episodes/CPU and counts the developer (`developer_revisions`, `llm_calls_total`, `developer_usd_share`) | E1, E3 (e.g. synthetic: ≈2,460 replay episodes costing ≈7 s CPU per run, vs 240 agent calls; but ≈92 developer requests on top); live runs: developer 14–30% of spend |
| 20 | one objective with dials | Eq.1 β1/β2, policy beta, beta sweep → `beta_sweep.json` | E7 |
| 21 | domain-agnostic | `DomainTask`, `ProgramDomain`, `DiscoveryTask` | synthetic, sum-diff, circle packing, Lasso, autocorrelation, AgentQA (`test_dream_on_generic_rsi_core_domain_agentqa`), `FunctionDomain` example |
| 22 | replay only tests choices tried | support fields, `support="clip"/"no_reward"`, missing continuations | E2, E10 |
| 23 | selection can overfit | `GuardedSelector` | E9 |
| 24 | single runs / different models | multi-seed harness with CIs; only same-agent Fixed comparisons | all |
| 25 | RRSI guards; replaying other methods' evaluations | `GuardedSelector` (built on `rsi.core.gates.MinGain`); `DiscoveryTree.from_ledger` | E9, E1 (`--ledger`) |
| 26 | log experiments as a tree | `discovery.jsonl`, `policies.jsonl` (`rsi.core.Ledger`) | E1 ledger round trip 64/64 |
| 27 | glossary: discovery tree, replay world, strategy | `DiscoveryTree`, world pool, `method.py` | – |
| 28 | the strategy as code, improved cheaply by replay | `policy_templates`, developer, loop | E3 |
| 29 | tree of parallel branches; cost in the objective; scale | `OnlineQuestion` worker pool, `GridPlan`, Eq.1 | – |
| 30 | interactive demo | `rsi.dream.demo`, `experiments/dream-rsi/demo_replay.py` | exact (0.575/0.611/0.475, maps), plus a β1×β2 winner map (`demo_replay.png`) |
| 31 | Lasso, math, kernels; CLI agent | Lasso, sum-diff, circle packing, autocorrelation Φ1–Φ3 (+ synthetic); `EditorAgent` over `ClaudeCLI` (stands in for Gemini CLI); kernels not reproduced (GPU) | E3, live smoke |
| 32 | an AI improves the strategy automatically | `LLMPolicyDeveloper` in the loop, no approval step, `r####_*/` audit trail | live smoke |
| 33 | replays really-measured results only | `ReplayQuestion` reveals recorded evaluator outcomes; no LLM judge | – |
| 34 | held-out evaluation (Lasso) | `LassoPathDomain` `holdout` split + `transfer_report` / `evaluate_split`; the sandboxed grader is hardened so a candidate cannot rewrite its own timing (§4.23) | E3 (Lasso held-out re-measurement); `test_lasso_sandboxed_grader_cannot_be_influenced_by_the_candidate` |
| 35 | read the rows | per-instance held-out table with arithmetic and geometric means and win/loss counts vs Fixed and vs scikit-learn's `lasso_path` (`LassoPathDomain.reference_artifact()`) (`e3_dream_vs_fixed.json` → `results.lasso.holdout.rows`) | E3: seed 185 ms, sklearn 3.3 ms, Fixed 16.0 ms, Dream 17.1 ms (geometric means); Dream faster than Fixed on 4/8 (seed, instance) pairs; 0/16 final programs beat sklearn |
| 36 | keep rule = best replay score over past searches | `Selector.select` | E4 |
| 37 | controlled comparison | same agent, evaluator, seed, grid cap and call budget in both arms | E3 |
| 38 | "open code" | the official code is unreleased; this is a reimplementation | – |
| 39 | per-round best and attempts logged | `live_cycle_manifest.json`, trajectory rows | E6 |

Spec §9.1 invariants: (a) recording policy reproduces its rollout: tested and E1, 64/64. (b) deterministic replay: tested. (c) V\* ≥ V0: tested and E4, 150/150. (d) guard raises on unrevealed data: tested. (e) earliest-created branch rule: tested. (f) exhausted leaf reveals nothing but counts k: tested. (g) demo: exact.

## 4. Deviations and design decisions (all flagged [inferred] in the spec unless noted)

1. **Roots.** The paper's §3 formalism picks a single `r`, but Listing 2 has addressable root cells. Both are implemented: `root_mode="earliest"` (default) maps any requested root cell to the earliest-created unrevealed recorded branch; `"addressable"` reveals the named branch. A batch may hold several root cells (the §3.1 formalism gap resolved as the spec suggests).
2. **Support.** The support of a live recording is its *planned* grid, including roots the recording policy never opened. Unrecorded cells inside the plan are missing continuations that reveal nothing. This is what makes invariant (a) hold for policies that do not open every root. Out-of-support plans get no replay reward by default (`support="no_reward"`, Listing 2's "cannot earn replay reward": no quality credit, costs still count; also in the Pareto sweep, where such an episode attains 0). `support="clip"` (intersect the plan with the support and score it as if it had asked for less) is a non-default option; it was the default until the claims audit (§10). E2 shows why this matters: clipping hides the probes such a policy would spend.
3. **Missing continuations.** Following the paper formalism (`hide_missing=False`), an exhausted leaf stays legal until probed once. That probe reveals nothing, counts a round, and then the branch is closed, which keeps episodes finite. `hide_missing=True` gives the API's "irregular grid" semantics.
4. **Eq. 1 normalization.** By default scores are normalized per world, `(s − s_r)/(ceiling_i − s_r)` (spec §9.3). The paper averages raw V_i; `normalize=False` does the same, and the demo, E2, E8 and E10 use it. Disqualified episodes score below every honest episode (§4.21).
5. **Pareto objective.** `pareto.auc` is the area under the best-attainment-vs-probe-fraction step frontier traced by the beta sweep, with attainment normalized by each trace's ceiling. The paper does not define it exactly. λ = 0.1 and grid {0.2, …, 1.0} follow spec §9.3.
6. **M semantics.** M versions including the incumbent means M−1 revisions (`m_semantics="versions"`, the default); `"revisions"` gives M revisions and M+1 candidates. The developer starts each revision from the strongest version so far ("start from a strong recent policy").
7. **Dreaming after the last round** is skipped by default (`dream_last=False`), because it cannot affect any live search. Set it to True to return a dreamed final policy.
8. **Beta sweep** is computed for the *deployed* version only under Eq. 1 (it feeds `beta_sweep.json` and the default-beta rule), and for every version under the Pareto objective, where the sweep *is* the selection value (before the review, the loop skipped it and scored every version −∞; see §8).
9. **Mock developer.** `ParametricMutator` edits the `PARAMS` block of the adaptive template, a real code edit. It uses feedback-directed moves (premature stops → more patience/width; wasted probes → earlier stops; ceiling always reached → try cheaper; under-filled batches → open more) plus random perturbations and the cross-cycle default-beta rule. It is a stand-in for the LLM developer, not a claim about what an LLM would write.
10. **Root of round t** is the best program found so far (`root="best"`); the paper leaves this unspecified. `root="seed"` restarts from the seed program.
11. **Direction provider.** Each new root gets a direction class, cycled in a per-round shuffled order. Guidance biases this assignment toward the advised classes (strength 0.8) and adds its text to `$direction_guidance`.
12. **Guard.** In-process runs are for trusted templates only: Python cannot fully hide objects in-process, although the static check forbids the introspection routes. The subprocess runner is the real firewall. The world never enters the child process, and after import the child cannot open any file descriptor (`RLIMIT_NOFILE` lowered to the lowest free fd). This goes beyond the paper, which only states the rules in the prompt. **Every episode starts from a fresh policy namespace** ("replay resets the policy's per-rollout state"): in-process the module is re-executed per episode; in the sandbox each episode runs in a fresh `fork()` of a process that has only imported the module (Python also reseeds `random` in a fork). The static check additionally rejects `global`/`nonlocal`, mutation of module-level or imported objects and of shared class attributes from inside a function, `random.seed/setstate/getstate`, and `lru_cache`/`cache` (claims audit N1, §10).
13. **Leakage screen.** The LLM developer's code is screened by `rsi.core.LeakageCritic` against trace-specific terms (best cell ids, scores), implementing "never copy a trace-specific branch, cell id, score, or target into policy logic".
14. **Mock-agent calibration.** For sum-difference, the seed set and per-attempt search effort were chosen so progress spans several rounds. The first seed set was a subset of a strong local optimum, which made every run finish in round 1. Circle packing uses SLSQP, which converges within a few iterations, so that domain is near-saturated after round 1 (≈2.63 vs the best known 2.635983), much like the paper's tie. Lasso timing uses CPU time (`process_time`, best of 3) because wall time on the shared 4-CPU box was too noisy; an earlier 2-seed wall-clock run gave the opposite search-score ranking.
15. **Synthetic world model** (depleting mechanisms, typed branches, CRN) is my own assumption; the paper has none. E5's synthetic arm in particular *assumes* that re-exploiting a mechanism yields less, so it is a mechanism check, not independent evidence.
16. **History shown to the discovery agent.** Listing 1 asks the agent to read *every* historical proposal "in full -- not a sample, not just recent cycles". By default the loop passes the attempts of **every** earlier live search as `H_{t-1}` (`Config.agent_history_cycles=None`), and `EditorAgent` renders every record with its full `proposal.md`, `eval/score.json` and `error.txt`, plus the baseline's `proposal.md` (`$baseline_dir`). Caps are non-default options for long runs (`agent_history_cycles=N`, `EditorAgent(max_history=..., max_proposal_chars=...)`), and when one is active the prompt says what it omits. Until the claims audit (§10) the defaults were the last 8 searches, 30 records per section and 600-character proposals, while the prompt claimed "in full, not a sample".
17. **Typed infrastructure failures.** An exception raised by the discovery agent or by a user evaluator during an online probe becomes a failed attempt with `fail_class="env_error"` (counted as one agent call). It is never attributed to the policy, and it never aborts the run.
18. **Policy timeout** (`policy_timeout_s`, subprocess sandbox) counts only the time the parent waits on the policy process, not the agent/evaluator work that an online probe triggers in the parent.
19. **Call-budget truncation.** When `max_calls` cuts a batch, only the first requests run; the round's batch size and `k` record what ran, and the tree's `meta["truncated_batch"]` records what was requested (so that one round cannot be replayed exactly).
20. **Reset only at episode start.** "Replay resets the policy's per-rollout state" is read as a reset *before* the first probe. The guard treats `question.reset()` after any probe as a violation: otherwise a policy can explore every cell, remember the scores in its own memory, reset the episode's counters and walk straight to the best recorded cell (the `peek_reset` template; it passes the static check because it uses only the public API). The Listing-2 prompt states the rule.
21. **Disqualified episodes.** A violation, an illegal batch or a policy crash scores `floor − β1·|world| − 1` (floor = 0 normalized, `s_r` raw), strictly below every honest episode on that world. A constant (the first version used −1) is not enough: on a paper-scale world (32 × 20 = 640 cells, β1 = 0.01) an honest refine-everything policy scores ≈ −5.2, and with raw negative scores (a negated loss) even small worlds invert.
22. **Decision split of a generic Domain.** `DomainTask` scores on `evolve`, else `train`, else `val`; sealed or empty splits raise. (Before, a domain without `evolve` scored every program 0 as a *successful* evaluation.)
23. **Lasso grader hardening.** The candidate runs in the same sandbox process as its timing harness, so the harness binds its clock and reads a result nonce before any candidate code runs, loads a fresh copy of the module and a freshly perturbed copy of the data for every timing repeat (memoising across repeats does not pay), returns the raw coefficient paths so the parent computes the correctness objectives, and exits with `os._exit` right after writing the nonce-tagged result. Before, patching `time.process_time` raised the score 10⁵× and memoisation 10³×. This is accident-level isolation, not a boundary against a determined adversary (the candidate can still introspect the harness process); the in-process path (`sandboxed=False`) is for the trusted mock agent only.
24. **Pareto objective with the guarded selector.** Per-world values under `objective="pareto"` are each world's own sweep reward, so a selector that works on world subsets (dev / held-out) ranks by the selection objective; the developer's view recomputes the sweep on the development worlds only (it used to include held-out worlds).
25. **Baked-in default beta of LLM-written policies.** `default_beta_of` reads `PARAMS["default_beta"]` and otherwise the literal in `self.config.get("beta", x)` (the Listing-2 idiom), so live manifests record the actual beta (the live smoke's Haiku policy bakes in 0.65; it used to be reported as 0.6). The default-beta rule can now also lower a default that sits at the top of the grid (it used to jump back to 0.6).
26. **Tags in `root_mode="earliest"`.** A legal root cell is only a slot there (the reveal maps it to the earliest unrevealed branch), so its tags stay hidden; once revealed, a root cell shows its branch's tags, as online.

## 5. Experiments (commands, settings, results)

All scripts accept `--llm sim|claude:haiku --seeds N --quick --workers W --out PATH` and write `results/dream-rsi/<name>.json` (config, per-seed raw numbers, summaries with CIs, verdict) and a PNG where useful. A live run writes `<name>__claude-haiku.json/.png` instead, so a small showcase never overwrites the offline result cited here (the live smoke keeps its own name). In live mode the loop experiments (E1, E3–E6, E9) use Claude as discovery agent and/or policy developer; the replay-only ones use it as the policy developer: the demo gets an LLM-written fourth strategy, E2/E7/E10 add LLM-written policies to their pools (E7 also checks that each has a non-degenerate beta dial), and E8 checks an LLM-written policy against the guard. LLM-written policies and programs always run in the subprocess sandboxes (`_common.runner_for`, `sandbox_of`, `agent_of`). These live paths were dry-run with a mock developer LLM; none was run live in this review.

* **demo** `demo_replay.py`: exact reproduction. Over the demo's slider ranges (273 settings) "drop the flat branch" wins 168, "stop early" 81 and "refine everything" 24.
* **E1** `e1_replay_fidelity.py` (synthetic ×4 seeds, sum-diff ×2, circle packing, Lasso; parallel refine and adaptive; both root modes): 64/64 identical. Zero agent calls during replay is *measured*: the agent object counts its calls across every replay (the first version compared a frozen usage snapshot with itself and hard-coded 0). Online seconds per world vs replay ms per episode (rerun on a loaded shared box): synthetic 0.016 s vs 2.7 ms; sum-diff 0.09 s vs 3.0 ms; circle packing 3.5 s vs 2.6 ms (≈1400×); Lasso 8.7 s vs 2.5 ms (≈3500×). With a real LLM agent at 10–60 s per call the ratio is 10⁴–10⁶. `--ledger` converts any foreign ledger; the saved result was run with `--ledger results/dream-rsi/inputs/autoresearch_landscape_ledger.jsonl --lower-is-better` (an offline `rsi.autoresearch` run on its `LandscapeTask`, 24 experiments): 24 attempts become 18 branches of depth ≤ 4. With `--llm claude:haiku` it records one sum-difference world pair on a 3×1 grid (24 real agent calls).
* **E2** `e2_offpolicy_validity.py` (20 policies; 5 recorded worlds on a 6×5 grid; true value from 60 fresh worlds): see §0. The replay-optimal policy loses 0.019 against the true best. With K = 1–10 recorded worlds ρ stays ≈0.81–0.85; the zoo contains many near-ties.
* **E3** `e3_dream_vs_fixed.py` (equal agent-call budgets; synthetic 20 seeds / 240 calls; sum-diff 10 / 100; circle packing 5 / 100; Lasso 4 / 100; autocorrelation Φ3 10 / 200 (10 rounds, as the paper's math tasks); W = the grid's width so that π₁ runs every workspace in parallel; identical per-round budgets (`round_budget="fallback"`); `--objective pareto` selects by the sweep reward; `--reverdict` recomputes the verdicts from the saved JSON). Re-run after the claims-audit fixes (§10):

  | domain | best at equal budget: Fixed → Dream (Δ, 95% CI) | wins/losses | calls to Fixed's final best (ratio) | after 5 rounds: agent calls Fixed vs Dream (incl. developer requests); best |
  |---|---|---|---|---|
  | synthetic | 0.857 → 0.910 (+0.053 [+0.040, +0.067]) | 19/0 | reached 20/20, 1.57× | 150 vs 90 (110); 0.777 vs 0.737 |
  | sum-difference | 1.0174 → 1.0175 (+0.0002 [−0.0034, +0.0028]) | 4/2 | 8/10, 1.29× | 100 vs 66 (78); 1.0174 vs 1.0162 |
  | circle packing | 2.6313 → 2.6307 (−0.0006 [−0.0014, 0.0000]) | 0/2 | 3/5, 1.38× | 100 vs 61 (73); 2.6313 vs 2.6307 |
  | Lasso (search score, 1/s) | 82.4 → 93.4 (+11.0 [−6.3, +39.5]) | 1/3 | 1/4 | 100 vs 69 (81); 82.4 vs 68.9 |
  | Lasso held-out re-measurement (1/s) | 45.8 → 44.5 (−1.2 [−11.0, +11.7]); seed program 5.1 | – | – | – |
  | autocorrelation Φ3 (score = −Φ3) | −1.5913 → −1.5933 (−0.0020 [−0.0109, +0.0064]) | 5/5 | 5/10, 2.17× | 100 vs 66 (78); −1.5978 vs −1.6060 |

  Per-round budgets are identical: in every domain the largest Dream round spends exactly Fixed's per-round calls (30 / 20). Dream plans larger than that budget were 98/389, 47/83, 22/45, 15/32 and 106/164, and the budget cut 15, 4, 4, 2 and 5 rounds. Before the fix Dream could reach 34 calls in a round. Counting the policy developer's requests (one per revision), Dream's LLM bill at equal *agent* budget is 332 vs 240 calls on synthetic worlds and 121–246 vs 100–200 on the real tasks.

  Verdict rule: "comparable" (non-inferior) means the CI lower bound of Dream − Fixed at equal budget is above −5% of Fixed's mean gain over the seed program: −3.1% on sum-difference, −0.6% on circle packing, −2.7% on autocorrelation. Lasso stays **inconclusive**: its search-score CI is wide (lower bound −8.8% of Fixed's gain), and the held-out re-measurement cannot exclude a larger loss either. Before the claims-audit fixes (W = 4 on 5–6-wide grids, no per-round cap) the synthetic gain was +0.055 [+0.043, +0.069], with the same verdicts. With `--objective pareto` (`e3_dream_vs_fixed__pareto.json`: synthetic, sum-diff, circle packing, autocorr) the verdicts are also the same: synthetic +0.062 [+0.047, +0.077]; sum-diff, circle packing and autocorr comparable.

  **Control (synthetic, not in the paper).** Recursive Fixed Exploration on smaller fixed grids at the same 240-call budget reaches a final best of 0.875 (6×1), 0.872 (6×2), 0.897 (4×2), 0.897 (4×4) and **0.911 (3×3)**. Dream − the hindsight-best grid (3×3) = −0.0017 [−0.0145, +0.0099], 11/20 wins. The best of five grids chosen on the same seeds is optimistic for Fixed. So on these worlds the whole equal-budget gain over the paper's baseline comes from spending fewer calls per round (more, cheaper rounds from a better root), which Dream finds without hindsight tuning. There is no evidence of better within-round decisions.

  **Lasso rows** (held-out instances, CPU ms, geometric means over the 2 instances; measured on a shared, loaded box): seed 207, scikit-learn `lasso_path` 4.2, Fixed 25.8 [23.0, 30.5], Dream 22.5 [18.3, 25.8]. Dream is faster than Fixed on 5 of 8 (seed, instance) pairs. None of the 16 final programs beats scikit-learn's compiled solver, so the paper's "every discovered solver beats scikit-learn and glmnet on all six datasets" does **not** reproduce with the mock agent's numpy solvers.

  Reading: at equal *rounds*, Dream spends 31–40% fewer agent calls (19–27% fewer LLM calls if the developer's requests are counted), consistent with the paper's 317 vs 550 (Lasso-Pro: −42%). Its quality at equal rounds is lower on synthetic worlds and Lasso and about the same elsewhere. At equal *calls*, Dream is clearly better than the paper's fixed baseline on synthetic worlds (but no better than a hand-tuned fixed grid), and it ties on the three saturating math tasks. That mirrors the paper's circle-packing tie and its slightly worse autocorrelation (1.456375 vs 1.456001). Lasso is inconclusive: timing noise dominates, and the search-score ranking has flipped between reruns (wall clock vs CPU time, after the review fixes, and again on this loaded box).
* **E4** `e4_safe_selection.py` (15 seeds × 5 phases × 2 developer noise levels): see §0. Without the incumbent the mean V\*−V0 stays positive (+0.024 / +0.028) but 4/75 and 9/75 phases regress, worst −0.32. Deploying a regressed version did not measurably change the next live search or the final best in this world model (0.7696 vs 0.7705 at σ = 0.2).
* **E5** `e5_replay_vs_advice.py` (Fixed / Fixed+G / Dream / Dream+G; synthetic 15 seeds / 240 calls; circle packing 3 seeds / 100 calls; W = the grid's width since the claims-audit fix):

  | domain | arm | best @25% budget | best @100% | distinct directions per round |
  |---|---|---|---|---|
  | synthetic | Fixed | 0.554 | 0.853 [0.825, 0.878] | 1.00 |
  | synthetic | Fixed + guidance | 0.545 | 0.798 [0.764, 0.828] | 0.47 |
  | synthetic | Dream | 0.585 | **0.911** [0.885, 0.931] | 1.00 |
  | synthetic | Dream + guidance | 0.579 | 0.851 [0.823, 0.875] | 0.55 |
  | circle packing | Fixed / F+G / Dream / D+G | 2.6285 (all) | 2.6306 / 2.6308 / 2.6299 / 2.6309 | 1.00 / 0.53 / 0.95 / 0.55 |

  Guidance effect at the final budget on synthetic worlds: Fixed −0.056 [−0.081, −0.031] and Dream −0.061 [−0.078, −0.044] (reproduced: "worse than no guidance at all"). Early effect: −0.009 and −0.006, CIs spanning 0, so the paper's "helps early" nuance is **not** reproduced. Circle packing (3 seeds, saturated): guidance is marginally *better* (+0.0003 [0.0000, +0.0008] for Fixed; +0.0010 [0.0000, +0.0028] for Dream), so the claim is **not** reproduced there. In the synthetic world the harm comes from the generator's depletion assumption (§4.15), so read that arm as a mechanism check. In both domains guidance roughly halves the diversity of opened directions.
* **E6** `e6_pacing.py` (Dream vs Fixed, 12 rounds synthetic × 20 seeds, 10 rounds sum-diff × 10; W = the grid's width and identical per-round budgets since the claims-audit fix). The effort changes come from the dreamed `plan_grid` and PARAMS edits; the cross-cycle default-beta rule rarely moves the default in these runs (see checklist #17): mean Dream effort by round is 30 17 15 14 14 14 14 14 14 14 13 14 on synthetic (Fixed: 30 every round) and 20 13 11 11 11 12 12 13 14 15 on sum-diff, where effort creeps back up as progress stalls. The round-controlled comparison (change in calls after a plateau minus after an improving round) is clustered by seed, because (seed, round) pairs from one run are correlated: sum-diff +1.8 [+0.6, +3.0] (7 seeds with both kinds of round), synthetic +1.9 [−0.3, +4.0] (6 seeds, not significant). Before the fix (W = 4): +1.4 [+0.5, +2.4] and +1.3 [−0.2, +2.7]. The paper's pacing story rests on one ConvDiv run.
* **E7** `e7_objective_dials.py` (8 worlds, 61 candidate policies): see §0. Eq. 1 and the Pareto sweep reward rank the random adaptive variants almost independently (ρ = −0.06). The two objectives in the paper genuinely disagree about which policy is best.
* **E8** `e8_no_peeking.py`: see §0. Since the claims-audit fix it also replays the `memo_module` cheater (cross-episode memory) under the Pareto sweep in both runners, with one namespace for all episodes (the pre-fix evaluator, `fresh_episodes=False`) and with the fix.
* **E9** `e9_overfitting.py` (12 repetitions × t ∈ {1, 3, 6} past worlds × M up to 32; 20 fresh worlds): see §0. The fresh-world value of the argmax pick peaks at M ≈ 4–8 and then declines (t=1: 0.745 at M=4 → 0.717 at M=32): the winner's curse. The guarded selector cannot help with one past world. With more worlds it narrows the gap and deploys a fresh-world regression less often, but its fresh-world value is not significantly better than the argmax pick's (e.g. t=3, M=32: 0.733 [0.696, 0.766] vs 0.725 [0.686, 0.759]; t=6, M=32: 0.739 vs 0.743). `--llm claude:haiku` swaps in the LLM developer (M ∈ {2, 4} to bound cost; policies run in the subprocess sandbox).
* **E10** `e10_support.py`: see §0.
* **live** `live_smoke.py --llm claude:haiku --cache <fresh dir>` (sum-difference; 2 live searches on a 3×2 grid, W=3, M=2; programs and policies sandboxed). Re-run after the claims-audit fixes with the verbatim Listing-1/Listing-2 prompts and a fresh cache (`.rsi_cache/dream-rsi-live-smoke-fix`). 12 agent calls, all evaluated ok, raised Γ from 0.9105 to 1.0194. The developer call returned a complete adaptive policy ("trajectory-aware policy: rank branches by successful anchor and trend; build portfolio batches (exploit, explore, recover)"). It passed the static check and the leakage screen and replayed to V = 0.955, equal to the incumbent, so the incumbent was kept by the tie rule. Cost: $0.622 agent + $0.099 developer = **$0.72** (the developer is 14%), 598 s. The pre-review run (condensed prompts) had given 1.0320 for $0.71.

## 6. Limitations

* No GPU kernels. The math tasks are small CPU stand-ins: sum-difference optima found here are ≈1.02–1.07 vs the paper's 1.145 on huge sets; circle packing reaches ≈2.631 vs 2.635983; autocorrelation Φ3 ≈1.59 vs 1.4557. The offline agents are parametric mocks. Only the live smoke and the live validation runs use a real LLM, at a tiny budget, so no claim is made about LLM-written policies beyond "the pipeline works". In both post-fix live runs the haiku policies tied π₁ or lost on replay.
* E3's synthetic equal-budget win over the paper's fixed grid is matched by a hand-tuned smaller fixed grid. The evidence therefore supports "Dream learns to spend less per round without tuning", not "Dream makes better within-round choices".
* Track-A conclusions depend on the synthetic generator (common random numbers, depleting mechanisms, branch types). E2, E9 and E10 are *validations of the replay machinery* against that ground truth, not of the paper's worlds.
* Replay is only as good as its independence assumption. E2's context-coupling arm shows the bias when outcomes depend on sibling history (critique §8.3); the live `EditorAgent` does read all siblings, as Listing 1 requires.
* The in-process runner is not a security boundary. Use `sandbox="subprocess"` (the default) for LLM-written policies. It re-executes the policy module per episode, but state kept in *other* modules (`policy_api`, the standard library, the `random` generator) is shared in-process; the static check rejects the obvious routes, and the sandbox forks a fresh process per episode. The subprocess sandbox is an accident-level isolation (rlimits, scrubbed env with a fixed hash seed, fd firewall, a fresh fork per episode), not a container.
* The adaptive template is one reasonable reading of Listing 2's decision loop. Its `PARAMS` space bounds what the mock developer can discover.
* Timing-based scores (Lasso) remain noisy on shared machines even with CPU time; always re-measure finalists on held-out instances. The sandboxed Lasso harness resists the obvious ways a program can rewrite its own grade (patched clock, memoisation across repeats, forged output), but the candidate still shares a process with its timer; for adversarial agents time each run in a separate process.
* The cross-cycle default-beta rule is implemented as Listing 2 describes, but in the offline runs it rarely fires (3/20 synthetic E3 runs ever moved the default). The "dial the strategy tunes across cycles" is therefore demonstrated as a working dial (E7) more than as a driver of the observed pacing (E6).

## 7. Tests

`python -m pytest tests/test_dream-rsi_*.py` runs 92 tests offline and deterministically in ≈45 s (`tests/test_dream-rsi_fixes.py`: the 22 regressions of the claims-audit fixes, §10) (`test_dream-rsi_validation.py`: the audit trace is write-only and complete, the monitor's spend is separate, the two live-run fixes of §9). They cover invariants (a)–(g), guard and sandbox, the static check, both objectives, ledger conversion, loop logging, both developer paths (incl. the leakage screen), the selectors, budgets, the guidance arm, all five discovery domains, `DomainTask` failure classes and AgentQA. `test_dream-rsi_review.py` and `test_dream-rsi_review2.py` hold the regressions of the two reviews (§8) and two genericity tests on new `FunctionDomain`s (through the LLM paths; and with a `train`-only split, the Pareto objective, the guarded selector and the sandbox).

## 8. Adversarial review (2026-09-25)

A second engineer audited the implementation against the spec (loop, formulas, defaults, data structures, checklist), wrote a new-domain genericity test, and re-checked every experiment claim. Bugs found and fixed (each has a regression test in `tests/test_dream-rsi_review.py`):

1. **`objective="pareto"` never worked in the loop.** `DreamRSILoop._eval` asked the evaluator not to sweep, so every version's Pareto value was −∞ and the incumbent was always kept. The evaluator now always runs the sweep when it is the objective.
2. **The live manifest reported the plan, not what was opened.** `opened_width` / `max_depth` echoed the support fields (which include the plan's unopened roots and unexplored depth). The adaptive `plan_grid` rule "roots improved early while depth stalled: widen, trim depth" could therefore never fire. Both fields now count the cells actually recorded.
3. **The guard dropped structural meta of revealed cells.** `PrefixGuard.state()` sent meta only for legal cells, so after a root was revealed its direction tag disappeared. The adaptive policy's "prefer directions not opened yet" rule was dead behind the guard (it only worked unguarded). Meta of revealed cells is now sent too; unrevealed, illegal cells stay hidden.
4. **The subprocess policy timeout included agent time.** During an online search every probe runs agent calls in the parent. Once they exceeded `policy_timeout_s` (30 s), each later wait on the policy got a 10 ms timeout, so live runs survived only by a race (the pre-review live smoke happened to win it). The timeout now counts only the time spent waiting on the policy.
5. **Agent or evaluator exceptions during an online probe** were attributed to the policy (in-process) or crashed the whole run (subprocess sandbox). They are now typed failed attempts (`env_error`).
6. **Call-budget truncation** recorded the requested batch size instead of the executed one. The round now records what ran, and the tree meta records what was requested.
7. **LLM usage double counting** when one LLM object serves two roles (agent + developer) is fixed.
8. **Experiments with `--llm claude:haiku`** ran LLM-written programs (`exec` in-process) and LLM-written policies in-process. Live mode now switches the domain evaluator to its subprocess sandbox and policies to the subprocess `PrefixGuard` sandbox (`_common.sandbox_of`). E9 now uses the LLM developer live, and E1's live run is bounded to 24 agent calls.

Claims re-checked. E1, E3, E4, E5, E6, E8 and E9 were rerun at full settings after the fixes. E2, E7, E10 and the demo were rerun as a check and gave identical verdicts. Corrections to the pre-review write-up:

* E3 synthetic: added the hindsight-tuned fixed-grid control. It ties Dream, so the synthetic "better at equal budget" is a pacing effect, not better within-round decisions.
* E3 Lasso: the old JSON verdict said "REPRODUCED: better at equal budget" although the held-out re-measurement disagreed. The rerun's search-score CI spans zero (+6.7 [−1.2, +17.5]), so the verdict is now "INCONCLUSIVE". The verdict code now demotes a search-score win without held-out support.
* E3 Lasso: added the per-instance "read the rows" table (checklist #35), including a scikit-learn reference row. No discovered solver beats scikit-learn.
* E6: the pooled-pair CIs were anti-conservative. A seed-clustered estimate now decides the verdict: still reproduced on sum-difference, still not significant on synthetic worlds.

The live smoke (`results/dream-rsi/live_smoke.json`) was run before these fixes and was not repeated: it used `parallel_refine` online and the eq1 objective, so bugs 1–3 did not affect it; bug 4 was a latent race it survived.

### Second adversarial review (2026-09-25)

A second reviewer re-audited the code against the spec and the first review's fixes, hunting in particular for ways a policy or a program can influence its own score. Bugs found and fixed (regressions in `tests/test_dream-rsi_review2.py`):

1. **Peeking through the policy's own memory.** `question.reset()` wiped the episode's revealed set, `N` and `k` at any time. A policy could probe every cell, remember the scores, reset, and walk straight to the best recorded cell. It passed the static check (public API only) and the guard in both runners, and on a 4 × 5 world it scored V = 0.955 against 0.82 for honest parallel refine. The guard now forbids a reset after the first probe (§4.20); E8 includes this cheater (`peek_reset`).
2. **A disqualified policy could outrank honest ones.** The disqualified value was a constant −1. An honest full-grid episode on the paper's 32 × 20 grid scores ≈ −5.2, so a crashing or cheating policy would have won selection there, and with raw negative scores the inversion happens on small worlds too (§4.21).
3. **Fake success on generic domains.** `DomainTask` scored on `evolve` unconditionally; a domain whose decision split is `train` produced score 0, `fail_class="ok"`, for every program (§4.22).
4. **The Lasso grader trusted the candidate's process.** Patching `time.process_time` raised the score from 12.7 to 10⁶, and memoising results across timing repeats to 1.3·10⁴, both passing the correctness gate (§4.23).
5. **Pareto objective + guarded selector.** The guarded selector ranked by Eq. 1 per-world values even under `objective="pareto"`, and the developer's feedback included a beta sweep computed on held-out worlds (§4.24).
6. **Smaller fidelity gaps.** `default_beta_of` ignored the Listing-2 idiom of LLM-written policies, so live manifests recorded the wrong beta; the default-beta rule could not lower a default at the top of the grid; revealed root cells lost their tags in `root_mode="earliest"`; `run()` did not meter the LLMs inside an explicitly passed agent/developer/summarizer; `task_kwargs` were silently ignored for discovery domains (§4.25–26, §2).

Claims re-checked:

* **E1** asserted "0 agent calls during replay" by comparing a frozen usage snapshot with itself and hard-coded the per-row value to 0. It now counts calls on the agent object (still 0), and the "real autoresearch ledger → 24 attempts / 18 branches" claim, which had no result behind it (the saved JSON had no `--ledger` run), is now backed by a stored ledger and the rerun JSON.
* **E3**'s "comparable" margin was 1% of the raw score (§5); now 5% of Fixed's gain. Verdict labels unchanged (`--reverdict`).
* **E8** rerun with both cheaters and the new disqualification value.
* **E10**'s verdict text said "underestimated" regardless of the error's sign; fixed (the saved result was negative, so the claim stands).
* **E1, E4, E5, E6, E8, E9** and **demo, E2, E7, E10** were rerun at full settings after the changes. Apart from the intended E1/E8 changes, every number and verdict is identical to the previous run (E5 differs only below 1e-12, floating-point noise in the circle-packing arm). E3 was not rerun: its offline path is unchanged (its dream runs never reached a default beta of 1.0, the only case the default-beta change affects), so only its verdicts were recomputed from the saved raw results (`--reverdict`).
* All experiment scripts now give `--llm claude:haiku` a real role, including the replay-only ones, and write live results under a separate name. No live LLM call was made in this review.


## 9. Per-iteration trace and from-scratch validation (2026-09-25)

**Trace.** When `out_dir` is given, `DreamRSILoop` writes `trace.jsonl` in the uniform `rsi.trace` format through `rsi/dream/tracing.py` (`Config.trace=True` by default; `trace=False` disables it). One trace round is one live cycle. The trace records:
- the loop state at `round_start`;
- every online decision round (`note: online_round`: the prefix the policy saw, the legal-set size, the batch, the reveals);
- every agent attempt as `proposal` (Listing-1 prompt, raw reply, claimed change, **actual** program diff vs the parent workspace) plus `eval` (per task + raw trials);
- the best-program `gate` and `decision`;
- the manifest;
- every policy version as `proposal` (Listing-2 prompt + reply, full text in `dream_prompts/`, or the mutator's moves; the actual `method.py` diff), `critic` (static check + leakage screen, repair rounds) and `eval` (V, V_i, per-world Eq.-1 terms and reveal batches);
- the selection `gate` and `decision`;
- the beta sweep, `state` and `run_end`.

When the task's domain has sealed holdout/ood splits, `rsi.trace.ShadowMonitor` scores the seed and every new best program on them (`Config.shadow_monitor`). Its model calls are metered under `shadow:*` roles, which `llm_usage` excludes. Everything is write-only. `tests/test_dream-rsi_validation.py` proves identical decisions with the monitor, without it, without a trace and without a run directory.

**Validation runs** (`experiments/dream-rsi/validate_dream.py`, results and narratives in `validation/dream-rsi/RUNS.md`): `sumdiff_offline`, `agentqa_offline` and `sumdiff_live` (claude haiku as agent and developer). Each run directory holds an `audit.md` that re-derives every step from the files on disk. It re-grades every attempt with an independent Γ, rebuilds diffs from the snapshot store, checks batch legality and replay fidelity, recomputes Eq. 1, re-replays every version, and checks the argmax, the diffs, leakage and the call budget. Offline, it also checks each dreaming decision against fresh online searches.

**Fixes made from what the traces showed:**
1. **Reply-format lines kept by the parser.** `EditorAgent` and `LLMPolicyDeveloper` now drop a trailing reply-format line (a closing fence, or a bare `===` / `=== END ===`) that `rsi.core.parse_file_blocks` leaves at the end of a file block (`strip_reply_terminators`). In the live run this turned 12 of 36 candidates into `SyntaxError`s, 6 of them in round 1. Re-graded without the stray line, the round-1 candidates score 0.955–1.029, and one of them would have been the round's best. The same artifact caused all 3 developer syntax errors, whose repair rounds then rewrote the policy on an invented cause.
2. **Failure text shown to the agent.** `AttemptRecord.render` now shows the *end* of an error, where a traceback states its cause. The agent used to see only the head of the traceback, so it blamed the failed ideas instead of the slip.
3. **No-op mutator moves.** `ParametricMutator` no longer claims perturbations that clamping or rounding turned into no-ops.
4. **Claims of repaired revisions.** A repaired developer revision keeps its first attempt's claim, as `<claim> [repaired: <fix>]`. Before, only the fix was reported as the change.

Decisions of offline runs are unchanged by all four.

**Stage-B independent audit** (`validation/dream-rsi/AUDIT.md`, `experiments/dream-rsi/validate_stepaudit.py`).

What the audit re-derived:
- It re-derived all 335 steps of the four validation runs from disk with independent code.
- A second replay implementation, written from the paper's §3 semantics, reproduces all 36 replay evaluations (64 world episodes).
- All 53 agent prompts and 12 developer prompts of the live runs were rebuilt from disk, and each one hashes to its fresh-cache entry. So the proposers saw exactly the recorded prefix and history.

Verdicts: 315 correct, 4 questionable, 15 wrong and 1 unverifiable. All 15 wrong steps come from the reply-parser bug.

Further fixes:
5. **Leading fence.** `strip_reply_terminators` also drops a leading ```` ```python ```` fence. `parse_file_blocks` leaves it whenever the closing fence is not the last line of the file block.
6. **Offending line in the static check.** `static_check` quotes the offending source line of a syntax error. "invalid syntax (line 188)" alone led the live developer to invent causes in its repair round.
7. **Proposal clipping (documentation).** In the agent's history, each earlier `proposal.md` was clipped to 600 characters (`AttemptRecord.render(max_chars=600)`), in addition to deviation 16. Superseded by the claims-audit fix (§10): the default is now the full proposal.

Post-fix live re-run (`sumdiff_live_b`, haiku, T = 2, 3 × 3 grid, $1.28):
- 17/17 attempts compiled.
- No developer revision needed a repair round.
- Γ went 0.9105 → 1.0361.
- A haiku-written policy was deployed by replay: V 0.933 vs 0.925.

**What the validation says about the method.** The offline ground truth confirms spec §8.2 and §8.4 on a concrete step:
- At t = 1 (one world), replay preferred a frugal adaptive policy (V 0.910 vs 0.865), only because the recorded tree held the ceiling value in a second branch.
- On fresh online searches that policy found significantly less: gain −0.0060 [−0.0115, −0.0006] at 7.1 vs 15 calls.
- The paper's guarantee V^{m*} ≥ V^0 holds on replay only, as stated.
- Later phases of the mock developer produce ties (PARAMS perturbations that change no decision), so the incumbent is kept.
- In the live run (T = 3, 3 × 4 grids), all six haiku-written policies probe the full grid in replay and tie with π₁ (V = 0.895). Dreaming never changed the deployed policy, so the run is effectively Recursive Fixed Exploration.


## 10. Claims-audit fixes (2026-09-25)

The claim-by-claim audit (`docs/claims/dream-rsi.md`) found seven new mismatches, N1–N7. Every open finding was reproduced first, then fixed or classified. Each fix has a regression test in `tests/test_dream-rsi_fixes.py` that fails on the pre-fix code. The claims file's §5 has the full log (finding → fix → file:line → test → evidence).

**Code fixes.**
1. **No peeking across episodes (N1, a real bug).** A policy could keep module-level state between replay episodes, because one session served every world and every sweep beta. The claims auditor's memo cheater remembered each world's best cell and walked straight to it on its next visit: +0.28 Pareto reward against +0.10 for the honest adaptive policy.
   - Every episode now starts from a fresh policy namespace (§4.12).
   - The static check (`rsi/dream/lint.py`) rejects the usual ways to carry state: `global`/`nonlocal`, mutating module-level, imported or shared class objects from a function, `random.seed/setstate/getstate`, and `lru_cache`/`cache`.
   - The cheater now scores exactly like parallel refine. E8 shows the attack with and without the fix (`memo_module`).
2. **Sandbox determinism (found by the live fix run).** An LLM policy that iterates a `set` of cell ids chose a hash-seed dependent batch order. The sandbox now runs with `PYTHONHASHSEED=0` and a scrubbed environment.
3. **Listing 2 verbatim (N2).** `DEVELOPER_PROMPT` (`rsi/dream/prompts.py`) is the paper's prompt word for word. The only changes are runtime paths, the API module name, and the objective paragraph when selecting by Eq. 1. Our runner's additions are in a separate `FRAMEWORK_NOTES` block. The developer sees every earlier version with its traces. The old caps (≤ 6 versions, ≤ 12 trace rows) are now options.
4. **Listing 1 verbatim, full history (N5; deviation 16).** The prompt now includes the pkill/kill line and every dropped phrase. It shows `baseline/proposal.md`, and every earlier record in full by default. Caps are options, and the prompt announces them when set.
5. **Identical per-round budgets (N3).** `Config.round_budget="fallback"` (default) caps a Dream round at Fixed's per-round calls. `plan_grid` still reshapes width × depth, and live `solve(question, budget)` receives the budget.
6. **All workspaces in parallel (N4).** E3, E5 and E6 run with W = the grid's width.
7. **The developer in the cost story (N7).** `CostMeter.developer_revisions`, `llm_calls_total` and `developer_usd_share` count it. E3 reports LLM calls including the developer's requests. The live smoke reports the developer's share of spend.
8. **Faithful defaults.** Out-of-support plans "cannot earn replay reward" (`support="no_reward"`), also in the Pareto sweep. The adaptive template no longer calls a single manifest "still improving".
9. **New task: autocorrelation inequalities.** `AutocorrelationDomain` covers App. A Problem 4, with Φ1/Φ2/Φ3 computed exactly from the discrete autoconvolution of a step function. The default is Φ3, whose values match Table 1 [inferred]. It is added to E3.

**Documented, not code.**
- N6: the paper's overclaims.
- The replay-vs-online frugality bias. The argmax is the paper's rule. With the fixed code, on the re-recorded run, the replay-selected frugal policy finds less online: −0.0037 [−0.0058, −0.0018] over 40 searches.
- Scale-bound claims.

**Re-runs.**
- **Offline.** Every offline experiment was re-run at full settings: demo, E1–E10, and E3 also with `--objective pareto`. E1, E2, E4, E7, E9 and E10 give the same numbers as before. E3, E5 and E6 changed only through W and the per-round budget, and all their verdicts are the same.
- **Validation runs.** The two offline validation runs were re-recorded, and their step audits give 112/113 and 97/97 correct. The one questionable step is the frugality selection.
- **Live.** One new live validation run, `sumdiff_live_c` (haiku, restored prompts; 49/49 steps correct; $1.46), and a re-run live smoke ($0.72). Total live spend: $2.18.
