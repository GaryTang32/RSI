# Dream-RSI: implementation notes (`rsi.dream`, `rsi.domains.discovery`)

This page documents how the Dream-RSI spec (`docs/methods/dream-rsi.md`) is implemented on the shared `rsi.core` API: the module map, the public API with a copy-pasteable example, how each capability-checklist item maps to code, experiment and result, deviations from the paper, and limitations. The official code is unreleased (spec §8.16), so everything here is a reimplementation from the paper text, the Listing 1/2 prompts and the overview's demo script.

All numbers come from `results/dream-rsi/*.json`, produced by `experiments/dream-rsi/*.py` at the default offline settings (`--llm sim`). CIs are 95% bootstrap CIs over seeds (`rsi.core.stats.summarize_runs` / `paired_diff_ci`).

## 0. Results at a glance

| # | Claim | Verdict | Key numbers |
|---|---|---|---|
| demo | Overview demo: 3 branches × 5 attempts, W=3, β1=0.010, β2=0.005 | **Reproduced exactly** | refine everything 0.575, drop the flat branch **0.611**, stop early 0.475; identical reveal-round maps; replayed in the subprocess sandbox |
| E1 | Replay is an exact, near-free simulator | **Reproduced** | 64/64 replays identical to the live rollout (in-process, sandboxed, and after a Ledger round trip); 0 agent calls; 2.0 ms/episode in-process, 62 ms in the subprocess sandbox (process start included); a real autoresearch ledger converts to a 24-attempt, 18-branch world |
| E2 | Off-policy replay ranks policies usefully | **Reproduced for in-support policies, with measured caveats** | Spearman(replay, true online value) = 0.92 in support; out-of-support plans are mis-estimated (+0.14 with clipping); the Listing-2 "no reward" rule restores the ranking (0.91 → 0.93); context coupling drops ρ to 0.80 |
| E3 | Dream improves quality vs discovery calls (vs Recursive Fixed Exploration) | **Reproduced on synthetic worlds; on the three real CPU tasks "comparable quality with fewer calls" (not "better")** | synthetic: +0.059 [+0.047, +0.072] at equal budget, 20/20 wins, 1.58× fewer calls to Fixed's final best; after 5 rounds the real tasks use 34–41% fewer calls (Lasso 66 vs 100, sum-diff 65 vs 100, circle packing 59 vs 100) at similar quality; the Lasso search score favours Dream (+1.7 [+0.3, +3.7]) but the held-out re-measurement does not (−2.4 [−6.0, +0.2], n.s.) |
| E4 | Safe selection: incumbent is a candidate | **Reproduced (replay)** | V\*−V0 ≥ 0 in 150/150 phases; without the incumbent 5/75 and 10/75 phases regress (worst −0.32); online effect negligible |
| E5 | Replay beats written advice | **Reproduced on synthetic worlds (a mechanism check under the generator's depletion assumption); not reproduced on circle packing; "helps early" not reproduced** | synthetic: guidance −0.056 on Fixed and −0.070 on Dream at equal budget, both CIs < 0; direction diversity 1.00 → 0.47/0.63; circle packing: +0.0003 (guidance marginally better, saturated task, n=3) |
| E6 | Learned pacing | **Conserving compute: reproduced. Re-expanding after plateaus: reproduced on sum-difference, weak on synthetic** | effort after round 1: 17 vs 30 (synthetic), 12 vs 20 (sum-diff); sum-diff: calls change +0.7 after a plateau vs −0.9 after an improving round (ρ = −0.57) |
| E7 | One objective, explicit dials | **β1 and policy beta reproduced; β2 only at large values** | ρ(β1, probes) = −0.74; policy beta: ρ(beta, probes) = +0.96, ρ(beta, attainment) = +0.92; β2 changes the argmax only from β2 ≥ 0.1 (the doc's 0.005 changes nothing) |
| E8 | No-peeking constraint matters | **Reproduced (mis-estimation), no online collapse** | unguarded cheater: replay +0.392 vs honest +0.218 (inflated by +0.119 over its online value; honest −0.024); guard disqualifies it in 8/8 worlds; static check rejects it |
| E9 | Selection can overfit past worlds | **Reproduced; the guarded selector helps partly** | argmax gap (dev − fresh V) grows with M: t=1 +0.070 → +0.175; t=3 +0.013 → +0.119; the guarded selector narrows it (t=3 at M=32: +0.088; t=6: +0.063 vs +0.094) |
| E10 | Replay can only test what was tried | **Reproduced** | go-deeper policy: replay error −0.013 on a shallow record (out of support), exactly 0.000 when the record covers its depth; all in-support errors are 0.000 |
| live | One live end-to-end run (Claude Haiku as agent and developer) | **Pipeline works end to end** | sum-difference Γ 0.9105 → 1.0320 in 12 real agent calls (9 ok, 3 repairable failures); Haiku's rewritten policy passed the static check and tied the incumbent on replay (0.955 vs 0.955), so the incumbent was kept; $0.71, 10.4 min |

## 1. Module map

| module | role (paper symbol) | built on |
|---|---|---|
| `rsi/dream/policy_api.py` | the policy-facing API, i.e. the paper's `see.policy.api` + `observation_signal`: `Observation`, `CellMeta`, `GridPlan`, `GridPlanningContext`, `LLMDesignedMethod`, `SimResult`, `_budget_done`, `_record_curve`, `finalize_result`, helper signals (`branch_promising`, `branch_failed_hard`, `probe_improved_vs_parent/baseline`, `is_repairable`, `branch_trajectories`) and `QuestionProxy`. Standard library only: it is copied verbatim into the policy sandbox | – |
| `rsi/dream/tree.py` | `DiscoveryNode` / `DiscoveryTree` (root + chains; cell ids `b{branch}.a{attempt}`; JSON; support fields; `render_grid`), `to_ledger` / `from_ledger` (any method's ledger → replay world; exact inverse for Dream ledgers), `SnapshotStore` | `rsi.core.Ledger`, `ArtifactStore` |
| `rsi/dream/question.py` | `GridQuestion` (legal roots/frontiers inside the plan, batch legality, N, k, batch sizes, per-round log), `ReplayQuestion` (deterministic reveal; `root_mode` earliest/addressable; missing continuation; plan ∩ support), `OnlineQuestion` (W-thread pool of agent + evaluator, workspace snapshots, call budget), `workspace_of` / `program_only` | – |
| `rsi/dream/guard.py` | `PrefixGuard` (parent-side firewall), `static_check` (AST lint), `InProcessRunner`, `SubprocessRunner` (JSON-RPC sandbox with rlimits and an fd firewall) | `rsi.core.sandbox` env rules |
| `rsi/dream/policy.py`, `policy_templates/` | policies as code (`method.py` artifacts), `PARAMS` read/rewrite; templates `parallel_refine` (π₁), `adaptive` (Listing-2 decision loop), `rules` (demo + zoo), `oracle` (cheater for E8) | `rsi.core.Artifact` |
| `rsi/dream/objectives.py` | `EpisodeResult`, `Eq1Objective` (paper Eq. 1, raw or per-world normalized, clip / no-reward support), `ParetoSweepObjective` (Listing-2 `pareto.auc − λ·parallel_penalty`) | – |
| `rsi/dream/evaluator.py` | `ReplayEvaluator` (plan_grid per world with prefix-safe manifests → replay → report; optional beta sweep), `PolicyReport` (value, per-world, sweep = `beta_sweep.json`, traces = `policy_execution_traces.jsonl`, diagnostics) | – |
| `rsi/dream/developer.py` | `LLMPolicyDeveloper` (Listing-2 prompt, context files in the paper's layout, static check + leakage screen + one repair round), `ParametricMutator` (feedback-directed mock developer), `choose_default_beta` (cross-cycle rule), `mock_developer_llm` | `rsi.core.RewriteEditor` / `AgentEditor`, `LeakageCritic` |
| `rsi/dream/selection.py` | `Selector` (argmax incl. incumbent; `include_incumbent=False` ablation), `GuardedSelector` (held-out worlds + noise margin) | `rsi.core.gates.MinGain`, `select` |
| `rsi/dream/agent.py` | `EvalOutcome`, `DiscoveryTask`, `DomainTask` (any `rsi.core.Domain`), `AttemptContext` / `AttemptRecord`, `EditorAgent` (Listing-1 prompt), `ParametricAgent` (mock), `DirectionProvider` | `rsi.core.Evaluator`, `Editor` |
| `rsi/dream/baselines.py` | Recursive Fixed Exploration (`fixed_config`), guidance summarizers (`MockGuidanceSummarizer`, `LLMGuidanceSummarizer`) | – |
| `rsi/dream/cost.py` | `CostMeter`: agent calls / tokens / $ kept separate from replay episodes and CPU, and from developer calls | `rsi.core.llm.Usage` |
| `rsi/dream/loop.py` | `Config`, `DreamRSILoop` (§3.2 outer loop, manifests, archives, ledgers), `run(...)`, `as_task` | `rsi.core.run.ImprovementResult` |
| `rsi/dream/demo.py` | the overview demo (tree, three strategies as code, render) | – |
| `rsi/domains/discovery/base.py` | `ProgramDomain`: program artifact + locked evaluator with typed `fail_class`, optional subprocess sandbox | `rsi.core.Domain`, `call_function` |
| `rsi/domains/discovery/lasso.py` | Lasso path: CPU-timed search instances, correctness gate `F_k ≤ F_k(sklearn ref) + 1e-6` on fresh instances, `holdout` split, mock agent over solver mechanisms | numpy, scikit-learn |
| `rsi/domains/discovery/sumdiff.py` | sum-difference Γ(A), staged construction recipe, mock agent (hill / anneal / window / fringe / grow) | numpy |
| `rsi/domains/discovery/circlepack.py` | circle packing n=26, SLSQP + exact LP radii with an exact feasibility repair, mock agent that resumes from the parent's measured packing | scipy |
| `rsi/domains/discovery/synthetic.py` | synthetic discovery worlds (typed branches, depleting directions, typed failures, CRN), ground truth (`ground_truth`, `truth_for_tree`), optional context coupling | – |
| `rsi/domains/discovery/agentqa_agent.py` | an offline discovery agent for the shared `agentqa` harness domain (shows Dream-RSI on a generic `rsi.core.Domain`) | `rsi.domains.agentqa` |

The largest file is `guard.py` (≈485 lines); all files are under 500 lines.

**Run directory** (`out_dir`): `discovery.jsonl` (every live attempt as a `rsi.core.Ledger` tree, prefixed `t<iter>/`), `policies.jsonl` (policy lineage with replay values; status deployed/candidate/discard/rejected), `trace_pool/iter<t>/{tree.json, live_cycle_manifest.json, guidance.json}`, `history/baseline/method.py`, `history/r####_<label>/{method.py, report.json, proposal_results/{beta_sweep.json, policy_execution_traces.jsonl}}`, `snapshots/` (every workspace), `final_policy/method.py`, `trajectory.json`, `summary.json`, `best_artifact/`.

## 2. Public API and applying Dream-RSI to a new problem

```python
from rsi.dream import Config, run
res = run(domain, seed_artifact=None, *, llm_task=None, llm_propose=None, llm_develop=None,
          config=Config(...), out_dir=None, agent=None, developer=None, initial_policy=None, summarizer=None)
# -> rsi.core.ImprovementResult
```

* `domain` can be any `rsi.core.Domain` (wrapped by `DomainTask`: score = mean evolve-split score from the locked grader), a discovery domain from `rsi.domains.discovery`, or a `DiscoveryTask`.
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

With a real model, replace the agent with `EditorAgent(ClaudeCLI("haiku"), editable=["approx.py"])` and pass `developer=LLMPolicyDeveloper(ClaudeCLI("haiku"))`, or just `run(domain, seed, llm_propose=CachedLLM(ClaudeCLI("haiku"), cache_dir))`. Offline, the example improves the score 0.0046 → 0.0113 while the deployed policy cuts its calls per search from 16 to 6.

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
| 7 | only revealed information; no peeking | `PrefixGuard`, `QuestionProxy` (violations reported and disqualifying), `SubprocessRunner` (hidden world never enters the process; open-file limit), `static_check`, fresh policy instance per episode | E8; `test_proxy_blocks...`, `test_sandbox_cannot_open_files` |
| 8 | replay score = best − β1·attempts + β2·attempts/round | `Eq1Objective` (+ `ParetoSweepObjective`) | demo (exact), `test_eq1_hand_computed_toy`, E7 |
| 9 | policy developer rewrites code many times; every version scored on every past search | `LLMPolicyDeveloper`, `ParametricMutator`, `DreamRSILoop._dream`, `history/r####_*/`, `policy_execution_traces.jsonl` | `test_llm_developer_path_offline`, live smoke |
| 10 | deploy the best version incl. the current one | `Selector` | E4 |
| 11 | models, agent, evaluator fixed | only `method.py` changes; agent/evaluator objects are constructed once per run | – |
| 12 | best average replay score over every past search | `ReplayEvaluator.evaluate` mean over all worlds | E4/E9 |
| 13 | online vs replay (runs agent vs reveals; random vs fixed; costly vs free; new ideas vs only recorded) | `OnlineQuestion` vs `ReplayQuestion` behind one API; `CostMeter` | E1 (0 agent calls, ms per episode), E10 (support) |
| 14 | Recursive Fixed Exploration baseline, same budgets | `Config(dream=False)`, `parallel_refine` template, `max_calls` | E3, E5 |
| 15 | Lasso/math/kernel results (fewer calls) | discovery domains + E3 | E3 (see §5) |
| 16 | replay beats written advice | `guidance=True`, summarizers, `DirectionProvider(guidance)` | E5 |
| 17 | pacing | policy `beta` + `_schedule`, `choose_default_beta`, `plan_grid` width/depth rules, per-round effort in manifests | E6 |
| 18 | history as executable simulator, not notes | replay vs guidance | E5 |
| 19 | improves the search almost for free | `CostMeter` separates agent calls from replay episodes/CPU | E1, E3 (e.g. synthetic: ≈2,200 replay episodes costing ≈4 s CPU per run, vs 240 agent calls) |
| 20 | one objective with dials | Eq.1 β1/β2, policy beta, beta sweep → `beta_sweep.json` | E7 |
| 21 | domain-agnostic | `DomainTask`, `ProgramDomain`, `DiscoveryTask` | synthetic, sum-diff, circle packing, Lasso, AgentQA (`test_dream_on_generic_rsi_core_domain_agentqa`), `FunctionDomain` example |
| 22 | replay only tests choices tried | support fields, `support="clip"/"no_reward"`, missing continuations | E2, E10 |
| 23 | selection can overfit | `GuardedSelector` | E9 |
| 24 | single runs / different models | multi-seed harness with CIs; only same-agent Fixed comparisons | all |
| 25 | RRSI guards; replaying other methods' evaluations | `GuardedSelector` (built on `rsi.core.gates.MinGain`); `DiscoveryTree.from_ledger` | E9, E1 (`--ledger`) |
| 26 | log experiments as a tree | `discovery.jsonl`, `policies.jsonl` (`rsi.core.Ledger`) | E1 ledger round trip 64/64 |
| 27 | glossary: discovery tree, replay world, strategy | `DiscoveryTree`, world pool, `method.py` | – |
| 28 | the strategy as code, improved cheaply by replay | `policy_templates`, developer, loop | E3 |
| 29 | tree of parallel branches; cost in the objective; scale | `OnlineQuestion` worker pool, `GridPlan`, Eq.1 | – |
| 30 | interactive demo | `rsi.dream.demo`, `experiments/dream-rsi/demo_replay.py` | exact (0.575/0.611/0.475, maps), plus a β1×β2 winner map (`demo_replay.png`) |
| 31 | Lasso, math, kernels; CLI agent | Lasso, sum-diff, circle packing (+ synthetic); `EditorAgent` over `ClaudeCLI` (stands in for Gemini CLI); kernels not reproduced (GPU) | E3, live smoke |
| 32 | an AI improves the strategy automatically | `LLMPolicyDeveloper` in the loop, no approval step, `r####_*/` audit trail | live smoke |
| 33 | replays really-measured results only | `ReplayQuestion` reveals recorded evaluator outcomes; no LLM judge | – |
| 34 | held-out evaluation (Lasso) | `LassoPathDomain` `holdout` split + `transfer_report` / `evaluate_split` | E3 (Lasso held-out re-measurement) |
| 35 | read the rows | per-instance held-out runtimes recorded (`e3_dream_vs_fixed.json` → `results.lasso.holdout.per_instance_ms`) | E3 |
| 36 | keep rule = best replay score over past searches | `Selector.select` | E4 |
| 37 | controlled comparison | same agent, evaluator, seed, grid cap and call budget in both arms | E3 |
| 38 | "open code" | the official code is unreleased; this is a reimplementation | – |
| 39 | per-round best and attempts logged | `live_cycle_manifest.json`, trajectory rows | E6 |

Spec §9.1 invariants: (a) recording policy reproduces its rollout: tested and E1, 64/64. (b) deterministic replay: tested. (c) V\* ≥ V0: tested and E4, 150/150. (d) guard raises on unrevealed data: tested. (e) earliest-created branch rule: tested. (f) exhausted leaf reveals nothing but counts k: tested. (g) demo: exact.

## 4. Deviations and design decisions (all flagged [inferred] in the spec unless noted)

1. **Roots.** The paper's §3 formalism picks a single `r`, but Listing 2 has addressable root cells. Both are implemented: `root_mode="earliest"` (default) maps any requested root cell to the earliest-created unrevealed recorded branch; `"addressable"` reveals the named branch. A batch may hold several root cells (the §3.1 formalism gap resolved as the spec suggests).
2. **Support.** The support of a live recording is its *planned* grid, including roots the recording policy never opened. Unrecorded cells inside the plan are missing continuations that reveal nothing. This is what makes invariant (a) hold for policies that do not open every root. Out-of-support plans are clipped by default; `support="no_reward"` implements Listing 2's "cannot earn replay reward" as "no quality credit, costs still count". E2 shows why this matters: clipping hides the probes such a policy would spend.
3. **Missing continuations.** Following the paper formalism (`hide_missing=False`), an exhausted leaf stays legal until probed once. That probe reveals nothing, counts a round, and then the branch is closed, which keeps episodes finite. `hide_missing=True` gives the API's "irregular grid" semantics.
4. **Eq. 1 normalization.** By default scores are normalized per world, `(s − s_r)/(ceiling_i − s_r)` (spec §9.3). The paper averages raw V_i; `normalize=False` does the same, and the demo, E2, E8 and E10 use it. Disqualified episodes score −1.
5. **Pareto objective.** `pareto.auc` is the area under the best-attainment-vs-probe-fraction step frontier traced by the beta sweep, with attainment normalized by each trace's ceiling. The paper does not define it exactly. λ = 0.1 and grid {0.2, …, 1.0} follow spec §9.3.
6. **M semantics.** M versions including the incumbent means M−1 revisions (`m_semantics="versions"`, the default); `"revisions"` gives M revisions and M+1 candidates. The developer starts each revision from the strongest version so far ("start from a strong recent policy").
7. **Dreaming after the last round** is skipped by default (`dream_last=False`), because it cannot affect any live search. Set it to True to return a dreamed final policy.
8. **Beta sweep** is computed for the *deployed* version only under Eq. 1 (it feeds `beta_sweep.json` and the default-beta rule), and for every version under the Pareto objective.
9. **Mock developer.** `ParametricMutator` edits the `PARAMS` block of the adaptive template, a real code edit. It uses feedback-directed moves (premature stops → more patience/width; wasted probes → earlier stops; ceiling always reached → try cheaper; under-filled batches → open more) plus random perturbations and the cross-cycle default-beta rule. It is a stand-in for the LLM developer, not a claim about what an LLM would write.
10. **Root of round t** is the best program found so far (`root="best"`); the paper leaves this unspecified. `root="seed"` restarts from the seed program.
11. **Direction provider.** Each new root gets a direction class, cycled in a per-round shuffled order. Guidance biases this assignment toward the advised classes (strength 0.8) and adds its text to `$direction_guidance`.
12. **Guard.** In-process runs are for trusted templates only: Python cannot fully hide objects in-process, although the static check forbids the introspection routes. The subprocess runner is the real firewall. The world never enters the child process, and after import the child cannot open any file descriptor (`RLIMIT_NOFILE` lowered to the lowest free fd). This goes beyond the paper, which only states the rules in the prompt.
13. **Leakage screen.** The LLM developer's code is screened by `rsi.core.LeakageCritic` against trace-specific terms (best cell ids, scores), implementing "never copy a trace-specific branch, cell id, score, or target into policy logic".
14. **Mock-agent calibration.** For sum-difference, the seed set and per-attempt search effort were chosen so progress spans several rounds. The first seed set was a subset of a strong local optimum, which made every run finish in round 1. Circle packing uses SLSQP, which converges within a few iterations, so that domain is near-saturated after round 1 (≈2.63 vs the best known 2.635983), much like the paper's tie. Lasso timing uses CPU time (`process_time`, best of 3) because wall time on the shared 4-CPU box was too noisy; an earlier 2-seed wall-clock run gave the opposite search-score ranking.
15. **Synthetic world model** (depleting mechanisms, typed branches, CRN) is my own assumption; the paper has none. E5's synthetic arm in particular *assumes* that re-exploiting a mechanism yields less, so it is a mechanism check, not independent evidence.

## 5. Experiments (commands, settings, results)

All scripts accept `--llm sim|claude:haiku --seeds N --quick --workers W --out PATH` and write `results/dream-rsi/<name>.json` (config, per-seed raw numbers, summaries with CIs, verdict) and a PNG where useful.

* **demo** `demo_replay.py`: exact reproduction. Over the demo's slider ranges (273 settings) "drop the flat branch" wins 168, "stop early" 81 and "refine everything" 24.
* **E1** `e1_replay_fidelity.py` (synthetic ×4 seeds, sum-diff ×2, circle packing, Lasso; parallel refine and adaptive; both root modes): 64/64 identical. Online seconds per world vs replay ms per episode: synthetic 0.017 s vs 1.9 ms; sum-diff 0.09 s vs 2.2 ms; circle packing 2.5 s vs 2.1 ms (≈1300×); Lasso 7.5 s vs 1.6 ms (≈4600×). With a real LLM agent at 10–60 s per call the ratio is 10⁴–10⁶. `--ledger` converts any foreign ledger.
* **E2** `e2_offpolicy_validity.py` (20 policies; 5 recorded worlds on a 6×5 grid; true value from 60 fresh worlds): see §0. The replay-optimal policy loses 0.019 against the true best. With K = 1–10 recorded worlds ρ stays ≈0.81–0.85; the zoo contains many near-ties.
* **E3** `e3_dream_vs_fixed.py` (equal agent-call budgets; synthetic 20 seeds / 240 calls; sum-diff 10 / 100; circle packing 5 / 100; Lasso 4 / 100):

  | domain | best at equal budget: Fixed → Dream (Δ, 95% CI) | wins/losses | calls to Fixed's final best (ratio) | after 5 rounds: calls Fixed vs Dream; best |
  |---|---|---|---|---|
  | synthetic | 0.857 → 0.916 (+0.059 [+0.047, +0.072]) | 20/0 | reached 20/20, 1.58× | 150 vs 90; 0.777 vs 0.737 |
  | sum-difference | 1.0174 → 1.0177 (+0.0003 [−0.003, +0.003]) | 5/1 | 9/10, 1.28× | 100 vs 65; 1.0174 vs 1.0161 |
  | circle packing | 2.6313 → 2.6307 (−0.0006 [−0.0014, 0.0000]) | 0/2 | 3/5, 1.40× | 100 vs 59; 2.6313 vs 2.6307 |
  | Lasso (search score, 1/s) | 102.7 → 104.4 (+1.7 [+0.3, +3.7]) | 3/1 | 3/4, 1.34× | 100 vs 66; 102.7 vs 95.3 |
  | Lasso held-out re-measurement (1/s) | 60.7 → 58.3 (−2.4 [−6.0, +0.2]); seed program 5.3 | – | – | – |

  Reading: at equal *rounds*, Dream spends 34–41% fewer calls, matching the paper's 317 vs 550 (Lasso-Pro: −42%). Its quality is lower on synthetic worlds (it prunes and needs more rounds) and about the same elsewhere. At equal *calls*, Dream is clearly better on synthetic worlds, slightly better on the Lasso search score, and ties on the two saturating math tasks, mirroring the paper's circle-packing tie. On Lasso the held-out re-measurement leans the other way (n.s., n = 4), so the search-score gain does not transfer. This is the same shape as the paper's Lasso-Pro result, where the average hides a mixed per-dataset picture. An earlier 2-seed Lasso run with wall-clock timing ranked the arms the other way on the search score; after switching to CPU timing and 4 seeds the search-score ranking flipped, and the held-out ranking flipped with it. Treat Lasso as noise-limited at this budget.
* **E4** `e4_safe_selection.py` (15 seeds × 5 phases × 2 developer noise levels): see §0. Deploying a regressed version did not measurably change the next live search or the final best in this world model (0.7729 vs 0.7728).
* **E5** `e5_replay_vs_advice.py` (Fixed / Fixed+G / Dream / Dream+G; synthetic 15 seeds / 240 calls; circle packing 3 seeds / 100 calls):

  | domain | arm | best @25% budget | best @100% | distinct directions per round |
  |---|---|---|---|---|
  | synthetic | Fixed | 0.554 | 0.853 [0.825, 0.878] | 1.00 |
  | synthetic | Fixed + guidance | 0.545 | 0.798 [0.764, 0.828] | 0.47 |
  | synthetic | Dream | 0.593 | **0.916** [0.893, 0.935] | 1.00 |
  | synthetic | Dream + guidance | 0.579 | 0.846 [0.811, 0.877] | 0.63 |
  | circle packing | Fixed / F+G / Dream / D+G | 2.6285 (all) | 2.6306 / 2.6308 / 2.6299 / 2.6308 | 1.00 / 0.53 / 0.99 / 0.57 |

  Guidance effect at the final budget on synthetic worlds: Fixed −0.056 [−0.081, −0.031] and Dream −0.070 [−0.094, −0.048] (reproduced: "worse than no guidance at all"). Early effect: −0.009 and −0.014, CIs spanning 0, so the paper's "helps early" nuance is **not** reproduced. Circle packing (3 seeds, saturated): guidance is marginally *better* (+0.0003 [0.0000, +0.0008] for Fixed; +0.0009 n.s. for Dream), so the claim is **not** reproduced there. In the synthetic world the harm comes from the generator's depletion assumption (§4.15), so read that arm as a mechanism check. In both domains guidance halves the diversity of opened directions.
* **E6** `e6_pacing.py` (Dream vs Fixed, 12 rounds synthetic × 20 seeds, 10 rounds sum-diff × 10): mean Dream effort by round is 30 17 15 14 14 14 13 13 14 13 12 12 on synthetic (Fixed: 30 every round) and 20 12 11 11 11 12 12 13 13 14 on sum-diff, where effort creeps back up as progress stalls. Round-controlled, the change in calls after a plateau vs after an improving round is +0.7 [+0.2, +1.1] vs −0.9 [−1.5, −0.4] on sum-diff, and +1.2 [−1.0, +3.3] vs −0.5 on synthetic (not significant). The paper's pacing story rests on one ConvDiv run.
* **E7** `e7_objective_dials.py` (8 worlds, 61 candidate policies): see §0. Eq. 1 and the Pareto sweep reward rank the random adaptive variants almost independently (ρ = −0.06). The two objectives in the paper genuinely disagree about which policy is best.
* **E8** `e8_no_peeking.py`: see §0.
* **E9** `e9_overfitting.py` (12 repetitions × t ∈ {1, 3, 6} past worlds × M up to 32; 20 fresh worlds): see §0. The fresh-world value of the argmax pick peaks at M ≈ 4–8 and then declines (t=1: 0.745 at M=4 → 0.717 at M=32): the winner's curse. The guarded selector cannot help with one past world.
* **E10** `e10_support.py`: see §0.
* **live** `live_smoke.py --llm claude:haiku` (sum-difference; 2 live searches on a 3×2 grid, W=3, M=2; programs and policies sandboxed; cache `.rsi_cache/dream-rsi`): 12 agent calls (Listing-1 prompt; 9 evaluated ok, 3 `compile_other`) raised Γ from 0.9105 to 1.0311 in round 1 and 1.0320 in round 2. The developer call (Listing-2 prompt, 24k tokens) returned a complete adaptive policy ("trajectory-aware branch pruning, priority-ranked batch selection, beta-dependent thresholds, history-responsive grid planning"). It passed the static check and leakage screen and replayed to V = 0.955, equal to the incumbent, so the incumbent was kept by the tie rule. Cost: $0.619 agent + $0.093 developer = **$0.71**, 621 s; replay of both versions took 8 episodes and 0.02 s of CPU.

## 6. Limitations

* No GPU kernels. The math tasks are small CPU stand-ins: sum-difference optima found here are ≈1.02–1.07 vs the paper's 1.145 on huge sets; circle packing reaches ≈2.631 vs 2.635983. The offline agents are parametric mocks. Only the live smoke uses a real LLM, at a tiny budget, so no claim is made about LLM-written policies beyond "the pipeline works".
* Track-A conclusions depend on the synthetic generator (common random numbers, depleting mechanisms, branch types). E2, E9 and E10 are *validations of the replay machinery* against that ground truth, not of the paper's worlds.
* Replay is only as good as its independence assumption. E2's context-coupling arm shows the bias when outcomes depend on sibling history (critique §8.3); the live `EditorAgent` does read all siblings, as Listing 1 requires.
* The in-process runner is not a security boundary. Use `sandbox="subprocess"` (the default) for LLM-written policies. The subprocess sandbox is an accident-level isolation (rlimits, scrubbed env, fd firewall), not a container.
* The adaptive template is one reasonable reading of Listing 2's decision loop. Its `PARAMS` space bounds what the mock developer can discover.
* Timing-based scores (Lasso) remain noisy on shared machines even with CPU time; always re-measure finalists on held-out instances.

## 7. Tests

`python -m pytest tests/test_dream-rsi_core.py tests/test_dream-rsi_loop.py tests/test_dream-rsi_domains.py` runs 36 tests offline and deterministically in ≈11 s. They cover invariants (a)–(g), guard and sandbox, the static check, both objectives, ledger conversion, loop logging, both developer paths (incl. the leakage screen), the selectors, budgets, the guidance arm, all four discovery domains, `DomainTask` failure classes and AgentQA.
