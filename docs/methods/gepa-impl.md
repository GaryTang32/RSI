# GEPA: implementation notes (`rsi.gepa`, `rsi.domains.ruleworld`)

This page explains how the GEPA spec (`docs/methods/gepa.md`) is built on the shared `rsi.core` API. It covers:

- the module map;
- the public API, with a copy-pasteable example for a new problem;
- how each capability-checklist item maps to code, an experiment and a result;
- the experiment results;
- deviations from the paper and from `gepa-ai/gepa@d771eb21`;
- limitations.

**Where the numbers come from.**
- Every number is taken from `results/gepa/*.json`.
- Those files are written by `experiments/gepa/*.py`, run at their default (full) offline settings.
- CIs are 95% bootstrap CIs over seeds (`rsi.core.stats.summarize_runs` / `paired_diff_ci`).
- "True test" is the exact analytic expected score on RuleWorld's sealed 300-example test split. The optimizer never sees that split.

## 1. Module map

The layout mirrors `gepa/src/gepa/{core,proposer,strategies}`.

| module | role (paper / reference symbol) | built on |
|---|---|---|
| `rsi/gepa/config.py` | `Config`: every knob, with the `gepa.optimize` / paper default (b = 3, Pareto, `instance` frontier, round-robin, strict acceptance, merge off / 5 invocations / 5-id subsample / overlap floor 5, perfect-score skip, seed 0) | – |
| `rsi/gepa/adapter.py` | `DomainAdapter` = `GEPAAdapter` over any `rsi.core.Domain`. `evaluate(ids, candidate, capture_traces, seeds) -> EvalBatch`. `make_reflective_dataset` builds `{Inputs, Generated Outputs, Feedback}` from `Trial.trace` and `Trial.feedback`, and uses the optional `domain.reflective_record(task, trial, component)` hook for module-specific μ_f. `feedback="score_only"` gives the ScoreOnlyReflection ablation. Sealed tasks are refused. | `rsi.core.Evaluator`, `Trial` |
| `rsi/gepa/reflection.py` | Verbatim `DEFAULT_TEMPLATE` (paper App. C) and `OPTIMIZE_ANYTHING_TEMPLATE`. `render_samples` (reference `format_samples` markdown). `parse_fenced` (reference `ProposalAdapter.parse`, including the known-truncation rejection). `ReflectionProposer`: one call per selected component, role `reflection`. | `rsi.core.LLM` |
| `rsi/gepa/frontier.py` | `FrontierTracker` (instance / objective / hybrid / cartesian keys; `>` replaces, `==` joins). `remove_dominated_programs` (set-cover pruning), `find_dominator_programs`, `select_from_pareto_front` (Algorithm 2). Generic, so any loop can reuse them as a diversity-preserving parent selector. | – |
| `rsi/gepa/strategies.py` | Selectors: `ParetoSelector`, `CurrentBestSelector`, `EpsilonGreedySelector(0.1)`, `TopKParetoSelector(5)`, `BeamSearchSelector(4)`. Component selectors: `RoundRobinComponents` (per-candidate pointer, inherited) and `AllComponents`. `EpochShuffledBatchSampler` (exact port, including least-frequent padding). Acceptance: `StrictImprovement`, `ImprovementOrEqual`, `NoiseMargin`. | – |
| `rsi/gepa/merge.py` | `MergeProposer`: Algorithms 3–4 as the reference implements them (common-ancestor triplet, `DESIRABLE`, module-wise "take the changed side", both-changed → higher aggregate; the departures from Alg. 4 as printed are listed in §5.16). 5-id subsample with 3 buckets. Cap modes: `reference_soft` (default, the reference code), `hard` (the paper's "invoked a maximum of 5 times": merges built and scored ≤ cap) and `accepted` (caps accepted merges only; the pre-audit `hard`). `n_invocations` counts merges built and scored. Zero-weight fallback. | – |
| `rsi/gepa/state.py` | `SearchState` (= `GEPAState`: candidates, parents DAG, per-id validation scores, frontier, round-robin pointers, discovery rollouts, trace). `RolloutCounter` by phase. JSON persistence. | `rsi.core.ArtifactStore` |
| `rsi/gepa/stoppers.py` | `MaxMetricCalls`, `MaxIterations`, `Timeout`, `FileStopper` (`gepa.stop`), `ScoreThreshold`, `NoImprovement`, `MaxCandidateProposals`, `MaxReflectionCost`, `BudgetStopper` (`rsi.core.Budget`), `Composite` | `rsi.core.Budget` |
| `rsi/gepa/engine.py` | `GEPAEngine`: the loop (paper Alg. 1 with reference semantics). Merge scheduling, skip rules, `FULL_EVAL_AND_ADD`, ledger nodes, callbacks, save and resume, and the hard/soft budget modes. | `rsi.core.Ledger`, `Node` |
| `rsi/gepa/api.py` | `run(...) -> ImprovementResult`. `optimize(seed_dict, trainset, valset, metric=...)` quick start through `MetricDomain`. `report(...)` for sealed-split reporting. `default_components`. | `rsi.core.run.transfer_report` |
| `rsi/gepa/rl.py` | `ScalarRLBaseline` / `run_scalar_rl`: a GRPO-style Bernoulli prompt policy trained on scalar reward only (group 12, 4 instances/step, β pull, validation every 20 steps, best-validation checkpoint). The vocabulary is `domain.rl_vocabulary`, or brainstormed once by the LLM. | `rsi.core.Evaluator` |
| `rsi/gepa/baselines.py` | `run_score_only` (ScoreOnlyReflection), `run_best_of_n` (BestOfN), `FewShotDemoOptimizer` / `run_fewshot` (MIPRO-lite: bootstrapped and labelled demos, grounded instruction proposals, TPE-like categorical search on validation minibatches). | – |
| `rsi/gepa/analysis.py` | `tree_metrics`, `gepa_curve` / `trajectory_curve` (true score of the returned candidate against rollouts), `curve_at`, `rollouts_to_target`, `gate_errors` | – |
| `rsi/gepa/mocks.py` | `GenericReflectionLM` (any domain), `AgentQAReflectionLM` (AgentQA + SimModel), `two_module_harness()` (AgentQA seed: solver prompt → Python tool → reporter prompt; `harness.py` stays frozen) | `rsi.core.MockLLM` |
| `rsi/gepa/tracing.py` | `GEPATracer`: the per-iteration audit trace (`<out_dir>/trace.jsonl`, uniform `rsi.trace` event kinds; §7). `ShadowLLM`: routes the shadow monitor's model calls under `shadow:*` roles, which the engine keeps out of its own spend and stoppers | `rsi.trace.RunTracer`, `ShadowMonitor` |
| `rsi/domains/ruleworld/world.py` | `RuleWorld` / `WorldConfig`: modules, aspects, customer families, conflicting aspects, rule parsing (general, conditioned, contradiction, ticket facts, demos), the simulated task model, dilution, slip, optional interference, and the analytic `expected()` | – |
| `rsi/domains/ruleworld/domain.py` | `RuleWorldDomain(Domain)`: `execute`; a locked grader with μ_f ∈ {rich, symptom, score_only} and optional first-error-only; `reflective_record` (the reply module sees upstream notes); `rl_vocabulary`, `gold_text`, `demo_text`, `expected`; `make_domain(seed, feedback, **cfg)` | `rsi.core.Domain` |
| `rsi/domains/ruleworld/mocks.py` | `RuleWorldReflectionLM` + `ReflectionProfile`: a mock reflection LM that uses only the information in its prompt (§9.2 Tier-1 mock). It also answers the few-shot baseline's grounded-proposal prompts. | `rsi.core.MockLLM` |

The largest file, `rsi/gepa/engine.py`, has 611 lines.

**Tests.** Run them with `python -m pytest -q tests/test_gepa_*.py`. There are 79 tests, all offline and deterministic, taking about 25 s: `test_gepa_core.py` (14), `test_gepa_engine.py` (12), `test_gepa_ruleworld.py` (6), `test_gepa_baselines.py` (3), `test_gepa_review.py` (21), `test_gepa_validation.py` (7), `test_gepa_validation_stageb.py` (4) and `test_gepa_claims_fixes.py` (12, the claim-audit regression tests: merge invocation cap, truncation check on cache hits, monitor wall-time credit, reference tie-breaking, E9 records, and checks on the regenerated E1 / E4 / E9 result files). `tests/test_gepa_validation.py` (7 tests) covers the audit trace: it is write-only (identical ledgers, run logs and results with the trace and shadow monitor on, off, or without a run directory; a USD stopper never sees the monitor's spend), complete (every event kind, one `round_start` / `decision` / `state` per iteration) and consistent with the ledger and artifact store (gate arithmetic, actual diffs, rollouts charged = rollouts counted, one monitor event per change of incumbent).

`tests/test_gepa_review.py` holds the adversarial-review tests:
- A new unit-conversion domain built inline with `rsi.core.FunctionDomain`, with no GEPA-specific hooks. It covers:
  - a split named `train`;
  - a missing val split;
  - objective scores from `Trial.meta["objectives"]`;
  - a 0–10 score range;
  - every selector, frontier type, acceptance rule and budget mode;
  - merge, with and without caching;
  - all four baselines.
- The failure-surfacing warnings.
- The `selector=` hook, including resume.

The method needed no changes to run on this domain.

**Run directory** (`out_dir`):
- `state.json` is written atomically at the top of every iteration. It holds the pool, lineage, validation scores, frontier, rollout counters, and the RNG, sampler, merge and selector state.
- `run_log.jsonl` has one entry per iteration, in the reference `full_program_trace` fields (`selected_program_candidate`, `subsample_ids`, `subsample_scores`, `new_subsample_scores`, `new_program_idx`, `merged_entities`, …).
- `ledger.jsonl` is an `rsi.core.Ledger`:
  - one node `c<k>` per pool candidate (kind `baseline`, `reflective` or `merge`; merges carry both parents in `meta.parents`);
  - one node `x<i>` per rejected proposal;
  - one node `m<i>` per rejected merge.
- `artifacts/` is an `ArtifactStore` holding every candidate, including rejected ones.
- `config.json`.
- `gepa.stop`: create this file to stop at the next iteration boundary.
- Also written: `best_artifact/`, `trajectory.json` and `summary.json`.
- Calling `run()` again on the same `out_dir` resumes the run. A larger budget extends it.

## 2. Public API and applying GEPA to a new problem

```python
from rsi.gepa import Config, run, optimize, report
res = run(domain, seed_artifact, llm_task=task_llm, llm_propose=reflection_llm, config=Config(max_metric_calls=800),
          out_dir="runs/x", components=None, budget=None, report_splits=("test",), critic=None)
res.best                         # argmax mean D_pareto score (ties: coverage, then lowest index)
res.meta["rollouts_by_phase"]    # seed_val / minibatch_parent / minibatch_child / val_reflective / merge_*
res.meta["candidates"], res.meta["frontier"], res.meta["report"]   # pool table, frontier, sealed-split report
res.state                        # SearchState: parents DAG, per-instance scores, trace
res.usage                        # per role: reflection, task, brainstorm, proposer, *:cached
```

- **Splits.** D_train is `config.train_split` (default `evolve`). D_pareto is `config.val_split` (default `val`). If there is no val split, D_pareto = D_train, which is multi-task / inference-time-search mode.
- **Components.** `components` defaults to `domain.component_paths()`, or else to the artifact's text files (never `*.py`, because GEPA changes prompts, not control flow). Pass `.py` files explicitly to evolve code as text.
- **Baselines** take the same inputs: `run_scalar_rl`, `run_score_only`, `run_best_of_n`, `run_fewshot`.

**A new problem, end to end.** The example below is a support-ticket router whose system is one prompt. It is copy-pasteable. The system and grader are yours; the reflection LM is a mock offline, or `claude -p` live:

```python
from rsi.core import Artifact, Domain, Execution, Task, TaskSuite, CachedLLM, ClaudeCLI
from rsi.gepa import Config, GenericReflectionLM, run

TEAMS = {"refund": "billing", "invoice": "billing", "password": "accounts", "login": "accounts",
         "crash": "engineering", "bug": "engineering"}
tickets = [Task(f"t{i}", f"Customer writes about a {kw} problem (#{i})", team)
           for i, (kw, team) in enumerate(list(TEAMS.items()) * 6)]
ids = [t.id for t in tickets]
suite = TaskSuite(tickets, {"evolve": ids[:12], "val": ids[12:24], "test": ids[24:]})   # test is sealed

class Routing(Domain):
    name = "routing"
    def execute(self, artifact, task, *, seed, llm):
        prompt = artifact["support/triage.md"]
        if llm is not None:                    # the real system: a frozen model reading the prompt
            return Execution(output=llm.complete(prompt + "\n\n" + task.input, seed=seed, role="task").text)
        kw = next(k for k in TEAMS if k in task.input)          # offline stand-in: follows stated routing rules
        rule = next((l for l in prompt.lower().splitlines() if kw in l), "")
        return Execution(output=next((t for t in set(TEAMS.values()) if t in rule), "general"),
                         trace=f"matched rule: {rule!r}")
    def grade(self, task, ex):                 # locked grader; the feedback text is GEPA's learning signal
        ok = str(ex.output).strip().lower().endswith(task.target)
        kw = next(k for k in TEAMS if k in task.input)
        return float(ok), ("Correct." if ok else f"Tickets about {kw} must go to the {task.target} team.")

res = run(Routing(suite), Artifact({"support/triage.md": "Route the ticket to one team. Answer with the team name only.\n"}),
          llm_propose=GenericReflectionLM(),   # live: CachedLLM(ClaudeCLI("haiku"), ".rsi_cache/me"); llm_task=ClaudeCLI("haiku")
          config=Config(max_metric_calls=200), report_splits=("test",))
print(res.best["support/triage.md"]); print(res.meta["report"]["splits"]["test"]["best"]["S"])   # -> 6 routing lessons, 1.0
```

- **Without a Domain class.** `optimize({"formatter.md": "..."}, trainset, valset, metric=fn, llm_propose=..., testset=...)` wraps a `metric(candidate_dict, example) -> score | (score, feedback) | (score, feedback, output)`.
  - Worked example: `experiments/gepa/example_new_problem.py`, a record formatter. Its sealed test went from 0.00 to 1.00 in 300 rollouts offline.
  - `--task-llm claude:haiku` makes the formatter a real model.
- **Multi-module systems.** Each prompt is its own file. RuleWorld has `prompts/triage.md` and `prompts/reply.md`; the AgentQA harness has `prompts/solver.md` and `prompts/reporter.md`. Module-specific feedback comes through `domain.reflective_record`.
- **Guards.** `critic=rsi.core.LeakageCritic(domain.leakage_terms())` screens each rewrite before the child's minibatch rollouts are spent (the parent's minibatch was already run). `Config(acceptance="noise_margin", noise_margin=δ)` adds an RRSI-style floor. Both are off by default.
- **Custom parent selection.** `run(..., selector=obj)` takes any object with `select(state) -> index`, as `gepa.optimize` accepts a `CandidateSelector` instance. It can also be a factory `f(rng)` that receives the engine's shared RNG. An optional `get_state` / `set_state` pair makes the selector resumable. E3 uses this hook for its tie-breaking control arm.
- **Failures are never silent.**
  - `meta` reports `n_exec_errors`, `seed_val_error_rate`, `n_infra_errors`, `n_reflection_failed` and `n_reflection_unparsed`.
  - A `RuntimeWarning` fires when the seed raises on every D_pareto example, for example a forgotten `llm_task`.
  - A second `RuntimeWarning` fires when every reflection-LM call failed.
  - Without these warnings, both cases used to end as an ordinary "no improvement found" run.

## 3. Capability checklist → code → experiment → result

| # | Overview claim (spec §10) | Code | Experiment → result |
|---|---|---|---|
| 1 | Tunes the several prompts inside an AI system | `Artifact` components + `DomainAdapter` + `RoundRobinComponents` / `AllComponents` | Two-module RuleWorld and two-module AgentQA (`tests/test_gepa_engine.py::test_agentqa_two_module_harness_improves`, E1). **Implemented, demonstrated** |
| 2 | Runs on a handful of examples and keeps full traces | `EpochShuffledBatchSampler` (b = 3); `evaluate(capture_traces=True)` → `Trial.trace` → records | Reference padding/epoch semantics unit-tested (`test_epoch_shuffled_sampler_padding_and_epochs`). **Implemented** |
| 3 | Reflects in plain language and proposes better prompts | `ReflectionProposer` + verbatim meta-prompt (tested byte-for-byte against the spec) + `render_samples` + `parse_fenced` | Live smoke with `claude:haiku`: 8/8 fenced rewrites parsed and accepted; RuleWorld true test 0.00 → 0.73. **Implemented, live-demonstrated** |
| 4 | "A score tells you that something failed; the trace tells you why" | Grader `feedback` + `reflective_record` (μ_f); RuleWorld μ_f ∈ {rich, symptom, score_only} | E2: rich 0.900 vs text-free 0.721, paired +0.179 [+0.146, +0.211]. **Reproduced** |
| 5 | Test the new prompts | Strict minibatch gate, then full D_pareto evaluation (`full_eval_and_add`) | E5 (gate error rates, budget split); budget identity unit-tested. **Implemented** |
| 6 | Keep every candidate best on at least one example (Pareto frontier) | `FrontierTracker` + `ParetoSelector` (set-cover pruning; reference probe vectors pass) | E3: large gains over the reference SelectBestCandidate with coarse (binary) rewards (+0.60 / +0.75). The gain disappears against a greedy selector that breaks ties toward the newest candidate (not significant in all 8 cells), so it is a tie-breaking effect, not frontier diversity. Not a general advantage with partial rewards. **Implemented; claim partially reproduced, mechanism not reproduced** |
| 7 | Combine complementary lessons | `MergeProposer` (Alg. 3–4 as in the reference; reference soft cap, the paper's hard invocation cap, or an accepted-merge cap) | E4: complementary merges beat both parents 65–95% of the time (+0.027 to +0.049); overlapping ones 19–31%. Net gain only with a rationed merge: the paper's hard cap of 5 invocations +0.014 [+0.001, +0.027] and soft cap 1 +0.017 [+0.006, +0.029] at B = 4000. **Reproduced qualitatively** |
| 8 | Genetic-Pareto | Lineage DAG (`SearchState.parents`, ledger) + Pareto selection | – |
| 9 | Beats GRPO with far fewer rollouts | `RolloutCounter` + `ScalarRLBaseline` + curves | E1: 5.7× / 6.1× fewer rollouts to reach 80% / 90% of the oracle; the GEPA curve dominates at every matched budget. This is inside the paper's 4–35× range but below the spec's ≥10× bar. At the paper's own 1:4 budget ratio (GEPA 6000 vs RL 24000) it is a tie: +0.015 [−0.004, +0.035], and RL reaches the oracle in 7/20 seeds, GEPA in 0/20. **Partially reproduced** |
| 10 | Beats MIPROv2 | `FewShotDemoOptimizer` (MIPRO-lite) | E7: +0.179 / +0.211 test, prompts 1.5–1.6× shorter (paper ≈ 3–9×). **Score reproduced; length only partly** |
| 11 | Promise for optimizing code | Multi-task mode (`val_split=None` → D_pareto = D_train); components may be `.py` files; `optimize()` | Not run (E10 was not prioritised). **Mechanism implemented, not demonstrated** |
| 12 | Changes prompts only | `default_components` excludes `*.py`; control flow (`harness.py`) stays frozen (tested) | **Implemented (documented boundary; `components=` relaxes it)** |
| 13 | Needs a good metric; text feedback works best | μ_f contract; E2; E12 | E12: a train-saturated seed is returned untouched (the gotcha). **Reproduced** |
| 14 | Few examples overfit; keep a separate validation set | Separate D_train / D_pareto; sealed `report(...)` | E6: at \|train\| = 6 the gap is +0.424 when D_pareto = D_train and +0.031 with a separate val set; test 0.576 vs 0.644. **Reproduced** |
| 15 | Plain-language reflection on full traces | as 3 (traces and upstream notes go into the records); module-specific records through `domain.reflective_record` | E9: module-specific records beat whole-system records by +0.047 [+0.025, +0.068] (round-robin) and +0.169 [+0.148, +0.190] (all), with 0 against 5.3 misplaced rule lines. **Reproduced as a mechanism** (mock reflection LM) |
| 16 | Keeps candidates that each win somewhere | as 6, plus the sealed test | – |
| 17 | Quality from few trial runs | `RolloutCounter`, `MaxMetricCalls`, curves | E1 |
| 18 | Built into DSPy; easy start on an existing pipeline | `optimize(...)` quick start + `MetricDomain`; any `Domain` | `example_new_problem.py`. **Implemented** (no DSPy adapter) |
| 19 | Rollout = one run of the system on a task | `RolloutCounter` (one (candidate, example) evaluation = one metric call, every phase) | Budget identity test. **Implemented** |
| 20 | Pareto frontier = candidates each best on some example | `FrontierTracker.mapping()` | **Implemented** |
| 21 | A diverse set as the anti-overfitting strategy; RRSI guards as options | Pareto selector; `NoiseMargin` acceptance; `critic=` pre-screen | E5 (noise margin arm); `test_optional_leakage_critic_blocks_ticket_copies_before_evaluation`. **Implemented** |
| 22 | Skills tuned with GEPA | Any text files as components (skills/*.md) | **Implemented** (not a separate experiment) |
| 23 | Held-out set, tree log, locked grader, noise | `report()` (sealed splits); `Ledger` tree; grading only in `Domain.grade`; `NoiseMargin`; E13 | E13: reruns byte-identical and resumes exact. **Implemented, demonstrated** |

## 4. Experiments and results

**How to run.**
- Every script runs as `python experiments/gepa/<name>.py [--llm sim|claude:haiku] [--seeds N] [--quick] [--workers W]`.
- Each writes `results/gepa/<name>.json` (config, per-seed raw rows, summaries with CIs, verdict) and a PNG where useful.
- `--llm claude:haiku` swaps the mock reflection LM for cached `claude -p`. The task models stay simulated.
  - Live mode runs 1 seed with smaller budgets and fewer arms.
  - The review ran every script's live code path end to end at zero cost, with `ClaudeCLI` stubbed and outputs sent to a scratch directory.
  - Live runs of the experiments themselves were not made (see "Live smoke" below).
- Runtimes: the full offline suite took about 13 min of wall-clock time on 3 workers; the largest script was E3 at 231 s.

**The testbed (RuleWorld, Tier 1).**
- The system has 2 modules: triage, then reply.
- There are 16 aspects (8 per module), 4 customer families and 2 conflicting aspects. On a conflicting aspect, one half of the families needs protocol A and the other half needs B; conditioned rules resolve it.
- Per-module slip is 5%.
- Train / val / test sizes are 30 / 30 / 300.
- Scores are partial (the fraction of properties right) unless stated.
- Oracle true test = 0.95.

The mock reflection LM (`ReflectionProfile` defaults):
- adds at most 2 rules per call;
- copies a ticket-specific fact instead of a rule with q = 0.15;
- drops a line with r = 0.05;
- conditions on the ticket's family when it sees a contradiction (0.4), else replaces (0.6) or appends;
- scopes spontaneously with probability 0.1;
- diagnoses side effects with probability 0.25.

The mock sees only its prompt.

### E1: sample efficiency against scalar-reward RL (`e1_sample_efficiency`)

**Setup.**
- 20 seeds.
- GEPA and ScoreOnlyReflection at B = 6000.
- ScalarRL at B = 24000 for lr ∈ {0.5, 2, 8}. lr = 2 is selected by validation score, not test.
- The figure is `e1_sample_efficiency.png`.

**Rollouts to reach a share of the oracle score.**

| arm | to 80% of oracle | to 90% of oracle | test at 800 | test at 1600 | test at 6000 | final |
|---|---|---|---|---|---|---|
| GEPA | 851 (100% of seeds) | 1685 (100%) | 0.745 | 0.860 | 0.917 | 0.917 [0.910, 0.923] |
| ScoreOnlyReflection | 3599 (60%) | 4133 (10%) | 0.476 | 0.606 | 0.763 | 0.766 |
| ScalarRL lr = 2 | 5326 (100%) | 8824 (85%) | 0.000 | 0.069 | 0.824 | 0.902 [0.881, 0.922] at 24000 |

**Ratio to GEPA.**
- Median per-seed RL/GEPA rollout ratio: **5.7×** (80% target) and **6.1×** (90% target).
- The GEPA curve is at or above the RL curve at 7/7 budgets ≤ 6000.
- GEPA − ScoreOnly final test: +0.151 [+0.120, +0.181].

**The paper's own comparison: GEPA at its budget against RL at 4× more** (`matched_budget_ratio` in the JSON; added after the claim audit, finding 5). The paper compares GEPA at about 2–7k rollouts with GRPO at 24k. Here, GEPA at B = 6000 against ScalarRL (lr = 2) at B = 24000, paired over 20 seeds:
- GEPA − RL final test = **+0.015 [−0.004, +0.035], not significant**. GEPA ends higher in 11 seeds, RL in 9.
- RL reaches the oracle test score (0.95) in **7/20** seeds; GEPA in **0/20** (it plateaus at 0.917).
- GEPA reaches RL's final (24k) score in 11/20 seeds, after 5.0–35.6× fewer rollouts (median 11.5×); in the other 9 seeds RL ends higher.
- At equal budgets GEPA is far ahead: paired GEPA − RL +0.745 [+0.712, +0.776] at 800 rollouts, +0.791 [+0.758, +0.822] at 1600, and still +0.093 [+0.061, +0.126] at 6000.

**Verdict.** Qualitatively reproduced for rollout efficiency: GEPA dominates at every matched budget, and the 6× ratio sits in the paper's "4–35×" range. The spec's "≥ 10×" is **not** met in this world. "Outperforms RL" is **not** reproduced at the paper's 1:4 budget ratio: there it is a statistical tie, and only RL ever reaches the oracle prompt.

- RL eventually gets close: 0.902 at 24000 rollouts, against GEPA's 0.917 plateau. The reward here is dense (partial credit per property), which helps policy gradients.
- RL's curve moves in steps of about 1000 rollouts, because its returned prompt changes only at validation checkpoints (every 20 steps). That is the paper's GRPO protocol, but it adds up to about 1000 rollouts (about 500 on average) to each RL rollouts-to-target value. That inflates the 80% ratio by about 10% on average (5.7× could be about 5.2×), not by a factor.
- Why GEPA plateaus below the oracle (0.917 against 0.95). The review attributed the residual per-aspect test loss of the returned prompts on seeds 0–3 at B = 6000; these runs are not in the results JSON.
  - Every aspect already had a correct rule in all four seeds.
  - The loss came almost entirely from conflicting aspects that were only partly resolved: a general rule plus conditioned rules for some families only.
  - A smaller part came from rules that the mock scoped to the one family it saw fail (`p_scope`).
  - Memorised ticket facts (3–9 lines per prompt) cost only prompt capacity, through dilution.
  - An earlier version of this page blamed memorised facts and rare aspects that the minibatches never show. That diagnosis was wrong.

**Tier-2 analogue (AgentQA).** Setup:
- two-module harness with SimModel;
- 5 seeds, B = 300;
- ScalarRL is scaled down to fit B = 300: group 6, 2 instances per step, validation every 3 steps, lr = 2, and 12 brainstormed lines per component;
- measured holdout accuracy over up to 20 questions and OOD accuracy over 20 questions from 4 unseen families. The shared AgentQA generator now drops duplicate questions, so the holdout split has 18–20 questions and the val split 13–15 depending on the seed (a core change, not a GEPA one).

Re-run after the claim audit with the fixed AgentQA mock (`results/gepa/e1_sample_efficiency.json`, `agentqa`); mean [95% CI] over 5 seeds:

| arm | holdout | OOD |
|---|---|---|
| seed | 0.445 [0.361, 0.519] | 0.390 [0.310, 0.500] |
| GEPA | 0.928 [0.877, 0.970] | 0.920 [0.850, 0.990] |
| ScoreOnly | 0.948 [0.946, 0.949] | 0.960 [0.900, 1.000] |
| ScalarRL (brainstormed vocabulary) | 0.906 [0.872, 0.938] | 0.890 [0.850, 0.950] |

Paired over seeds: GEPA − ScoreOnly = −0.020 [−0.072, +0.023] (holdout) and **−0.040 [−0.070, −0.010]** (OOD); GEPA − ScalarRL = +0.022 [0.000, +0.067] and +0.030 [−0.020, +0.090]. With 5 seeds these are weak.

- Every method finds the generic skills: "write a Python program" in the solver, plus answer format.
- Text feedback gives no edge here, and the mock fix does not change that. The grader only says "expected X, got Y", which never says how to fix the failure.
- The stateless AgentQA mock also keeps proposing the Python skill to the *reporter* module. The gate usually rejects it, but in 2 of the 5 returned GEPA harnesses the reporter carries it. Under score-only the mock chooses skills at random, which avoids that loop (see §6).
- **History.** The first version of this table was produced with a mock bug: `AgentQAReflectionLM` split the reflection prompt on every triple backtick, so once the solver wrote Python, the ```` ```python ```` block inside the execution trace cut the `<side_info>` short and the "full" arm fell back to its score-only branch. The parser was fixed during validation (`rsi/gepa/mocks.py:_split`), and this table is the re-run with the fix (it used to read GEPA 0.90 / 0.90, ScoreOnly 0.95 / 0.99, ScalarRL 0.90 / 0.89, seed 0.46 / 0.39). The confound is gone and the conclusion stands: on AgentQA's "expected X, got Y" feedback, reflection with text is not better than score-only reflection. The RuleWorld rows of E1 reproduced bit for bit.

### E2: text-feedback ablation (`e2_feedback`)

**Setup.** 30 seeds, B = 3000.

| μ_f | final true test | test at 800 rollouts |
|---|---|---|
| rich (names the violated rule) | 0.900 [0.887, 0.912] | 0.758 |
| rich, first error only | 0.916 [0.904, 0.927] | – |
| symptom (names the failing property) | 0.753 [0.725, 0.781] | 0.502 |
| score_only | 0.721 [0.690, 0.752] | 0.492 |
| ScoreOnlyReflection (engine strips the text) | 0.721 (identical records → identical runs) | – |

**Paired differences.**
- rich − symptom: +0.148 [+0.117, +0.179].
- symptom − score_only: +0.031 [−0.014, +0.075], not significant.

**Verdict.** Reproduced for the main claim: text that names the fix is by far the strongest signal. The ordering rich > symptom > score_only holds in the means, but symptom versus score-only is not significant.

- Symptom-level feedback tells the reflection *which* property failed but not the protocol. Its guesses then thrash on rules that already exist.
- The primary paper never ran this ablation (spec §7.1). This is the first measured version in this repo.

### E3: candidate selection (`e3_selection`)

**Setup.**
- 30 seeds per cell, 4 world variants, 8 arms, B ∈ {1500, 4000}. The figure is `e3_selection.png`.
- The 8th arm, CurrentBest with ties broken toward the *newest* candidate, was added in review. It is a control that separates frontier diversity from tie-breaking.
- The other 7 arms reproduced their earlier numbers exactly on rerun.

| world @ B | Pareto | CurrentBest | CurrentBest, newest tie | BeamSearch(4) | ε-greedy | TopK-Pareto(5) | Pareto+all | CurrentBest+all |
|---|---|---|---|---|---|---|---|---|
| partial @1500 | 0.863 | 0.890 | 0.879 | 0.827 | 0.889 | 0.884 | **0.919** | 0.891 |
| binary @1500 | 0.614 | 0.011 | 0.653 | 0.112 | 0.037 | 0.488 | **0.777** | 0.512 |
| interference @1500 | 0.767 | 0.818 | 0.818 | 0.720 | 0.804 | 0.788 | **0.858** | 0.853 |
| interference, undiagnosable @1500 | 0.722 | 0.724 | 0.727 | 0.674 | 0.742 | 0.742 | **0.765** | 0.731 |
| partial @4000 | 0.906 | 0.903 | 0.896 | 0.910 | 0.909 | 0.906 | **0.927** | 0.897 |
| binary @4000 | 0.770 | 0.025 | 0.768 | 0.507 | 0.160 | 0.690 | **0.801** | 0.659 |
| interference @4000 | 0.872 | 0.868 | 0.863 | 0.871 | 0.874 | 0.871 | **0.897** | 0.870 |
| interference, undiagnosable @4000 | 0.788 | 0.768 | 0.772 | 0.765 | 0.782 | 0.791 | **0.793** | 0.759 |

**Paired Pareto − CurrentBest (reference `idxmax`, ties go to the oldest candidate).**
- Binary rewards: +0.603 [+0.547, +0.664] at B = 1500 and +0.745 [+0.679, +0.799] at B = 4000.
- Undiagnosable interference at B = 4000: +0.021 [+0.003, +0.037].
- Partial rewards at B = 1500: −0.027 [−0.046, −0.007].
- Interference at B = 1500: −0.051 [−0.086, −0.015].
- Every other cell is not significant.

**Paired Pareto − CurrentBest with newest-tie breaking (the control).**
- Binary: −0.039 [−0.115, +0.040] at B = 1500 and +0.002 [−0.060, +0.069] at B = 4000.
- Undiagnosable interference at B = 4000: +0.016 [−0.003, +0.035].
- Interference at B = 1500: −0.051 [−0.088, −0.011].
- **Pareto significantly beats this greedy selector in none of the 8 cells.**

**Mechanism behind the binary result: tie-breaking on a validation plateau.**
- With all-or-nothing rewards, a one-module rewrite can fix a minibatch example, so the strict gate accepts children.
- It rarely makes a whole *validation* example right, though, so the accepted children tie their parent at validation mean 0.
- The reference `CurrentBest` (`idxmax`) breaks ties toward the oldest candidate. It therefore re-expands the seed forever: tree depth about 1.5, stalled for 476 of 484 iterations. Every rewrite restarts from the seed text.
- Set-cover pruning of fully tied candidates leaves the newest one. So Pareto, like the newest-tie control, keeps building on the latest accepted child and climbs out of the plateau: depth about 40 for Pareto, 60 for the control.
- The control shows that the whole gain comes from this tie rule, not from keeping per-instance winners alive.
- **The baseline's tie rule is the reference's** (checked after the claim audit). `CurrentBestSelector` is `gepa.strategies.candidate_selector.CurrentBestCandidateSelector` exactly: `idxmax` (`list.index(max(...))`) over the mean D_pareto score, which is summed in the same (validation-id) order as `GEPAState.get_program_average_val_subset`, so float ties resolve identically and go to the lowest index, i.e. the oldest candidate. `EpsilonGreedySelector` and `TopKParetoSelector` use the same `idxmax`. Pinned by `tests/test_gepa_claims_fixes.py::test_current_best_ties_go_to_oldest_like_reference_idxmax`, which also pins the other half of the mechanism (set-cover pruning keeps the newest of fully tied candidates). So the E3 comparison against SelectBestCandidate is against the reference's own greedy rule, and the finding stands: Pareto's advantage over it comes from tie-breaking. E3 was re-run after the fixes and reproduced bit for bit.
- The paper's Fig. 4 ("SelectBestCandidate led to a local optimum after one iteration") is *consistent* with this mechanism, but neither the paper nor this experiment establishes it as the paper's cause.

**Mechanism behind the partial-credit result.**
- With partial credit, greedy hill-climbing on a decomposable reward is efficient.
- Pareto spends budget validating accepted children of weaker frontier parents. It expands the current best in only 14–36% of iterations.

**Tree shape at B = 4000, partial rewards.**
- Pareto: depth 33, 35 leaves.
- CurrentBest: depth 18, 75 leaves ("broom" trees), stalled for 135 of 273 iterations.

**Other findings.**
- Updating every module per reflection (`module_selector="all"`) had the highest mean in every cell. That is consistent with the FAQ's "large boost in rollout efficiency" (spec E9). Not every pairwise difference is significant.
- BeamSearch(4) was the weakest arm at B = 1500 in 3 of the 4 worlds (partial and both interference worlds). In the binary world, the reference CurrentBest and ε-greedy were weaker. The paper's Table 3 also ranks BeamSearch last in aggregate.

**Verdict.**
- **Partly reproduced.**
  - Against the reference SelectBestCandidate (`idxmax`), Pareto wins big where rewards are coarse (plateaus), which is the paper's AIME / exact-match regime.
  - It also wins slightly in one interference cell.
  - It loses at small budgets with fine-grained partial rewards.
- **Not reproduced: the claimed mechanism.** Once greedy breaks ties toward the newest candidate, Pareto is never significantly better, so "different strengths survive" is not what produces the gain in these worlds.
- In short, the gain depends on how the greedy baseline breaks ties.

### E4: merge (`e4_merge`)

**Setup.** 30 seeds, B ∈ {1500, 4000}. Re-run after the claim audit: the paper-faithful invocation cap (`merge_cap_mode="hard"`, §5.4) replaced the old accepted-merge cap, which is kept as the `accepted` arm. The previous E4 JSON was also stale: its merge arms predated the review change that gave the merge subsample fresh rollout seeds instead of the child's D_pareto draw (§5.6), and none of its merge rows reproduced with the current code (the GEPA arm did).

Terms (§5.4): a *check* is an iteration where a merge was due and the proposer looked for a triplet; an *invocation* is a merge that was built and scored on its subsample (accepted + rejected).

| arm | test @1500 (paired vs GEPA) | test @4000 (paired vs GEPA) | invocations / run @1500, @4000 (max) | accepted / run @4000 | merge share of rollouts @1500, @4000 | runs over the cap @4000 (accepted; invocations) |
|---|---|---|---|---|---|---|
| GEPA | 0.863 | 0.906 | – | – | – | – |
| +Merge, soft cap 5 (reference, default) | 0.868 (+0.005 [−0.017, +0.027]) | 0.914 (+0.008 [−0.006, +0.021]) | 11.8, 20.3 (35) | 12.6 | 19%, 12% | 90%; 100% |
| +Merge, hard cap 5 (paper: invocations ≤ 5) | 0.877 (+0.014 [−0.014, +0.039]) | **0.921 (+0.014 [+0.001, +0.027])** | 5.0, 5.0 (5) | 3.2 | 8%, 3% | 0%; 0% |
| +Merge, accepted cap 5 (pre-audit "hard") | 0.874 (+0.011 [−0.013, +0.033]) | 0.914 (+0.007 [−0.008, +0.022]) | 7.4, 7.5 (13) | 4.9 | 12%, 5% | 0%; 90% |
| +Merge, soft cap 1 | 0.879 (+0.016 [−0.013, +0.041]) | **0.923 (+0.017 [+0.006, +0.029])** | 8.1, 9.4 (24) | 6.0 | 13%, 6% | 100%; 100% |
| +Merge, soft cap 20 | 0.866 (+0.003 [−0.020, +0.025]) | 0.911 (+0.005 [−0.008, +0.019]) | 12.3, 26.6 (55) | 16.2 | 20%, 16% | 37%; 57% |

Checks per run: 28.9 and 66.5 under soft cap 5, 14.2 under the hard cap (every hard-cap run used its 5 invocations before B = 1500, so its B = 1500 and B = 4000 merges are the same).

**Merged child against its parents.**
- Merges whose parents changed *different* modules (complementary): under soft cap 5 the child beats both parents 86% of the time at B = 1500 (mean true-test gain over the better parent +0.040, n = 28) and 65% at B = 4000 (+0.027, n = 43); under the hard cap 95% (+0.049, n = 21).
- Overlapping merges: 19–31% beat both parents, with gains of +0.002 to +0.008 (n = 75–336).

**Verdict.** Qualitatively reproduced: merge combines complementary module lessons, and its net effect depends on budget and rationing.
- At B = 1500 no merge arm differs significantly from GEPA.
- At B = 4000 only the rationed arms gain, by a small but significant amount: the paper's hard invocation cap 5 at +0.014 [+0.001, +0.027] and soft cap 1 at +0.017 [+0.006, +0.029]. The hard cap does it with 5 invocations and 3% of the rollouts. The unrationed soft caps (20–27 invocations and 12–16% of rollouts per run at B = 4000) and the accepted cap (7.5 invocations, 5%) show no significant gain.
- The reference soft cap lets accepted merges exceed `max_merge_invocations` in 73% (B = 1500) and 90% (B = 4000) of runs with cap 5, and in 100% with cap 1; invocations exceed 5 in every soft-cap-5 run. This reproduces [run:merge-cap-probe] and is consistent with the paper's own budget-allocation diagnosis of the Qwen degradation.
- Only the hard cap makes merge "sparse" in the paper's sense: at most 5 invocations per run.
- **Not** reproduced: the spec's expectation that *early* merges are worse. Early merges (< 33% of the budget) gained more than late ones: +0.022 against +0.005 at B = 1500 and +0.011 against −0.002 at B = 4000 (soft cap 5), because the complementary merges happen early.

### E5: the minibatch gate (`e5_minibatch`)

**Setup.** 30 seeds, B = 3000.

| arm | test | validation share of rollouts | false-accept rate | false-reject rate | iterations |
|---|---|---|---|---|---|
| b = 1 | 0.902 | 0.84 | 0.50 | 0.10 | 342 |
| b = 3 | 0.900 | **0.73** | 0.48 | 0.20 | 164 |
| b = 5 | **0.910** | 0.63 | 0.53 | 0.21 | 129 |
| b = 10 | 0.907 | 0.50 | 0.40 | 0.30 | 79 |
| b = 3, ≥ | 0.899 | 0.78 | 0.52 | 0.34 | 127 |
| b = 3, margin 0.1 | 0.909 | 0.63 | 0.52 | 0.31 | 229 |

**Definitions.** A false accept is an accepted child whose true test score is not higher than its parent's. That includes neutral ticket-fact copies and noise.

**Verdict.**
- **Reproduced:** most of the budget goes to validation (73% at b = 3).
- Larger b costs more per iteration.
- False accepts fall only at b = 10 and are not monotone in b. False rejects rise with b.
- Final test is flat in b (0.90–0.91). b = 5 is marginally best at this budget.

### E6: overfitting with few examples (`e6_overfitting`)

**Setup.** 30 seeds, B = 2000.

| \|train\| | gap, D_pareto = D_train | gap, separate val (\|V\| = 30) | test, D_pareto = D_train | test, separate val |
|---|---|---|---|---|
| 6 | +0.424 | +0.031 | 0.576 | **0.644** |
| 15 | +0.140 | +0.049 | 0.852 | 0.872 |
| 30 | +0.063 | +0.040 | 0.892 | 0.882 |
| 60 | +0.043 | +0.050 | 0.809 | 0.885 |

**Higher copy rate (q = 0.5).**
- At \|train\| = 6 the gap is +0.614 with D_pareto = D_train and +0.028 with a separate val set.
- The final prompts carry 8.6–15 ticket-specific lines, against about 3 at q = 0.15.

**Verdict. Reproduced.**
- With few examples, D_pareto = D_train reports near-perfect validation scores (1.0 at \|train\| = 6) that do not hold on test.
- A separate validation set removes most of the gap and improves test at small \|train\|.
- The large \|train\| = 60 test drop when D_pareto = D_train is a budget effect: each accepted child then costs 60 validation rollouts.
- **Confound.** As spec E6 prescribes, the separate-val arm also has 30 extra examples, which it reaches only through their scores.
  - Its gap reduction is mainly about honest measurement: the val score is no longer the training score.
  - Part of its test gain (+0.068 [+0.040, +0.097] at \|train\| = 6) may come from the extra selection data, not from the separation itself.
  - A split of the same total data was not run.

### E7: instruction-only against few-shot optimization (`e7_fewshot`)

**Setup.** 30 seeds, B ∈ {1500, 4000}.

**B = 1500.**
- GEPA 0.863 [0.844, 0.882] against MIPRO-lite 0.684 [0.666, 0.704]: paired +0.179 [+0.153, +0.204].
- MIPRO-lite-no-demos 0.606.

**B = 4000.**
- GEPA 0.906 against MIPRO-lite 0.695: paired +0.211 [+0.189, +0.232].

**Prompt length and generalization gap.**
- Prompt tokens: GEPA 409–451, MIPRO-lite 642–653 (median ratio 1.5–1.6×).
- Validation-to-test gap: GEPA +0.04–0.05, MIPRO-lite +0.07–0.08.

**Verdict.**
- Score part **reproduced**.
- "Markedly shorter" is only **partly** reproduced: 1.6× shorter, where the paper reports about 3× and up to 9.2×. MIPRO-lite has a fixed 4-demo cap in one-line demos.

### E9: module-level credit assignment (`e9_credit_assignment`, added after the claim audit)

**Setup.** 30 seeds, B = 1500, RuleWorld default world. Two feedback records crossed with two module selectors:
- *module-specific* (GEPA's default): `RuleWorldDomain.reflective_record` gives each module only its own properties' outputs and feedback (the reply module also gets upstream notes);
- *system-level*: `RuleWorldDomain.system_reflective_record` gives every module the whole system's outputs, decisions and feedback, in the same format.

The mock reflection LM sees only its prompt and does not know which module it is rewriting, so a failure it reads for another module's property becomes a rule in the wrong prompt. `misplaced_rule_lines` counts such rules in the returned prompts (the owning module never reads them; they only dilute).

| records / selector | final true test | test at 400 | misplaced rule lines | to 80% of oracle (share of seeds) | to 90% of oracle (share) | accept rate |
|---|---|---|---|---|---|---|
| module-specific / round-robin (GEPA default) | 0.863 [0.844, 0.882] | 0.533 | 0.0 | 813 (100%) | 989 (50%) | 0.70 |
| system-level / round-robin | 0.816 [0.798, 0.834] | 0.372 | 5.3 | 1099 (90%) | 1260 (30%) | 0.67 |
| module-specific / all | **0.919 [0.910, 0.928]** | 0.781 | 0.0 | 400 (100%) | 577 (97%) | 0.83 |
| system-level / all | 0.750 [0.732, 0.768] | 0.529 | 14.7 | 869 (47%) | 1437 (3%) | 0.75 |

The module-specific rows use the same configuration as the E3 "Pareto" and "Pareto+all" cells for partial rewards at B = 1500 and give identical numbers.

**Paired differences.**
- Module-specific − system-level: **+0.047 [+0.025, +0.068]** with round-robin, **+0.169 [+0.148, +0.190]** with all; with round-robin +0.161 [+0.117, +0.204] at 400 rollouts and +0.145 [+0.101, +0.188] at 800.
- All − round-robin: +0.056 [+0.040, +0.072] with module-specific records, but −0.065 [−0.092, −0.038] with system-level records (every module then receives every failure).

**Verdict. Reproduced as a mechanism, in the analogue.** Module-specific μ_f lets the reflection LM fix the module that is at fault (zero misplaced rules, faster and higher), and `all` is more rollout-efficient only when the feedback is module-specific. The size of the effect depends on the mock being module-blind; a real LLM can partly infer module ownership from the prompt text, so this does not measure how much GEPA's credit assignment helps a real model.

### E12: saturation (`e12_saturation`)

**Setup.** 10 seeds.

**Oracle seed with noise-free evaluation.** 0 accepted children, and the seed was returned in 100% of runs, both with and without the perfect-score skip.

**Oracle seed with 5% noise.**
- 12.2 children were accepted per run, and the seed came back in only 60% of runs. True test was unchanged at 0.950.
- The strict gate has no noise margin, so noise-driven children get in.

**A seed that memorises every training ticket.** It is perfect on D_train and has true test 0.00.
- GEPA accepts 0 children and returns it in all runs.
- With D_pareto = D_train it reports validation 1.00.
- With a separate val set it still returns it (GEPA never reflects on D_pareto), but reports validation 0.00, which exposes the problem.

**Verdict. Reproduced** under noise-free evaluation.

### E13: determinism and resume (`e13_determinism`)

**Setup.** 5 RuleWorld seeds (GEPA+Merge) and 2 AgentQA two-module seeds.

**Results.**
- Reruns are byte-identical: the same `run_log.jsonl`, candidate ids and ledger, with timestamps excluded.
- 19 kill-and-resume cycles, killed at iterations 2–25, all ended with the identical final `state.json`, `run_log.jsonl` and candidate sequence.
- Resuming *without* the persisted RNG and sampler state, which is the reference behaviour (weakness 15), diverged in 7/7 cases.

**Verdict. Reproduced** (a design goal of this implementation).

### Live smoke (`live_smoke`, `claude:haiku` as the reflection LM, one run)

**Totals.** $0.203 and 374 s, of which RuleWorld took $0.114 and 8 calls. Every call is cached under `.rsi_cache/gepa`.

**RuleWorld (B = 300).**
- All 8 haiku rewrites parsed from the fenced output and were accepted.
- True test went from 0.000 to 0.727 (oracle 0.95). The offline mock reached only 0.314 at the same budget.
- Haiku wrote compact rule lists ("fragile → periwinkle") that the simulated task model recognises.
- It copied no ticket facts.

**AgentQA two-module (B = 80, \|V\| = 8).**
- Haiku rewrote the solver into a long, arithmetic-specific instruction.
- Validation rose from 0.375 to 0.5, but it did **not** transfer: holdout 0.7 → 0.6, OOD 0.58 → 0.50. Tiny validation sets reward noise.
- `experiments/gepa/live_smoke.py --llm claude:haiku` re-runs it; it is free from the cache.

## 5. Deviations from the paper and the reference code

1. **Candidates are `Artifact`s.** Components are file names, and every other file is frozen (for example `harness.py`). The current text is inserted into `<curr_param>` with trailing newlines stripped, and a trailing newline is restored after parsing.
2. **Deterministic iteration order.** Front members, common ancestors, modules and merge-subsample ids are visited in sorted order; the reference iterates Python sets. Reference semantics are otherwise unchanged. The §4.7 probe vectors are unit tests.
3. **Merge edge case.** When every eligible ancestor has aggregate 0, the reference raises `ValueError`. Here the default is `merge_zero_weight="uniform"`; `"raise"` reproduces the crash.
4. **Merge cap** (rewritten after the claim audit). Terms: a merge *check* is an iteration in which a merge was due and the proposer looked for a valid (i, j, a) triplet (`invoked_merge` in the run log; no triplet → no rollout, fall through to reflection). A merge *invocation* is a check that found a triplet, built the child and scored it on its 5-id subsample (5 rollouts, plus \|D_pareto\| if accepted), accepted or rejected — the paper's "invoking merge when identified" [App. D.1]. `MergeProposer.n_invocations` counts them (`meta["n_merge_invocations"]`).
   - `merge_cap_mode="reference_soft"` (the default) is `gepa-ai/gepa@d771eb21` exactly. The reference's `total_merges_tested` counts **accepted** merges only (`core/engine.py:1038-1039`), and `max_merge_invocations` is checked only when `merges_due` is incremented (`core/engine.py:702`), never at attempt time. Due merges pile up while no triplet exists, so accepted merges can exceed the cap; rejected merges consume neither counter, so invocations are unbounded. This is what `gepa.optimize(use_merge=True, max_merge_invocations=5)` does, despite its docstring ("The maximum number of merge invocations to perform").
   - `merge_cap_mode="hard"` is the paper's "merge is invoked a maximum of 5 times" [App. G.4]: once `n_invocations` reaches the cap, no further merge is checked or attempted, so accepted + rejected ≤ cap and merge spends at most cap × (5 + \|D_pareto\|) rollouts. Checks that find no triplet are free and do not count. Scheduling (`merges_due`, the "right after an accepted reflective child" rule) is unchanged.
   - `merge_cap_mode="accepted"` caps **accepted** merges at attempt time. It is what this page called `"hard"` before the claim audit: it is neither the paper's nor the reference's semantics (rejected merges stay unlimited: seed 1 at B = 1500 built 13 merges, 5 accepted), and it is kept only so the earlier E4 arm can be reproduced.
   - The default stays on the reference, like every other place where the reference and the paper's text disagree (pair pool, set-cover domination, §5.16). Regression tests: `tests/test_gepa_claims_fixes.py::test_merge_proposer_hard_cap_counts_invocations_not_acceptances`, `::test_hard_merge_cap_bounds_accepted_plus_rejected_end_to_end`, `::test_hard_merge_cap_one_invocation_and_soft_cap_unbounded`.
5. **Resume state.** Iteration ids are seed-derived. The RNG, sampler, merge and selector state are persisted, so resume is exact. The reference uses unseeded uuid4 ids and persists no RNG state.
6. **Rollout seeds.**
   - Every D_pareto evaluation of a candidate uses `val_seed` (one draw per candidate, N = 1, as in the reference).
   - Minibatch evaluations of parent and child use fresh, independent, iteration-derived seeds, so they are never served from any cache (reference semantics).
   - With `cache_evaluation=False` (the default), merge-subsample and full-validation evaluations are charged in full, as in the reference. The rsi trial cache still returns the same draw for the same seed.
7. **BeamSearch(N = 4)** is absent from the reference. It is implemented as round-robin expansion of the top-N by validation aggregate, APO-style [inferred].
8. **Not implemented:** parallel proposals (`SameParent` / `PxN` sampling), ComBEE, the refiner, seedless mode, multimodal side_info and the DSPy adapter. **Implemented** from the stopper and strategy families: every stopper listed in §1, the `objective` / `hybrid` / `cartesian` frontiers (unit-tested), and `optimize_anything`'s hard eval cap (`budget_mode="hard"`).
9. **Run-directory format.** `run_log.jsonl` is append-only and truncated on resume, instead of a rewritten `run_log.json`. The `iterations/<id>/` agent directories and `candidate_tree.html` are replaced by `ledger.jsonl` plus `artifacts/`.
10. **Parallel rollouts.** `workers > 1` runs one evaluation's rollouts in threads. The reference is sequential unless the adapter batches.
11. **Baselines are CPU analogues.**
    - ScalarRL is not LoRA-GRPO on an LLM. It is a GRPO-style group-relative REINFORCE on Bernoulli line-inclusion logits, using SGD and an L2 pull toward the initial logits as the KL proxy.
    - MIPRO-lite replaces Optuna TPE with softmax-over-mean categorical sampling. Its grounded-proposal prompt is ours.
12. **Frontier fallbacks.**
    - Without objective scores, `hybrid` and `cartesian` fall back to instance keys. For `hybrid` this matches `optimize_anything`, whose adapter returns empty objective dicts.
    - In `gepa.optimize` the reference instead raises for all three objective-based types. `objective` raises here too.
13. **Additions from the adversarial review.** None of these changes the search trajectory, so earlier results reproduce bit-for-bit.
    - Execution-error and reflection-failure counters, plus the two warnings above.
    - The `selector=` hook.
    - `optimize()` without a `valset` now selects multi-task mode explicitly (`val_split=None`, so no warning), and it ignores `train_split` / `val_split` from the passed config, because it builds its own suite.
14. **RuleWorld is a simulator.** The rule semantics, the interference option and the mock reflection LM's behaviours (`ReflectionProfile`) are design choices [inferred from spec §9.2]. The interference world and the `p_scope` / `p_diagnose` behaviours were added during this work to test the local-optimum claim in more than one world. All four variants are reported.
15. **Outage stopper (stage-B audit fix).** `Config.max_consecutive_infra_failures = 3` (default) adds the stopper `ConsecutiveInfraFailures`. It ends the run with `stop_reason="infra_outage"` after 3 consecutive iterations lost to the backend: `skip_infra_error`, or `no_proposal` where every reflection call failed with an LLM error. It is a safety net, not a stop condition, so a config with no other stopper still raises. The reference has no such stopper. There, an exception aborts the run and a swallowed failure is scored 0. Before this fix, the usage-limit-interrupted live run kept charging 3 rollouts per dead iteration. On the offline validation runs the fix changes nothing: re-run from scratch, their ledgers (apart from timestamps), trajectories, run logs and audits are byte-identical. Regression tests: `tests/test_gepa_validation_stageb.py`. Set it to `None` for the old behaviour.
16. **Merge versus Algorithm 4 as printed** (documented after the claim audit; every item is reference behaviour, `gepa/proposer/merge.py` and `gepa/core/engine.py`, and this implementation follows the reference in all of them):
    - **Pair pool.** Alg. 4 line 2 samples i, j from all of P. The reference samples them from the Pareto survivors, `find_dominator_programs` of the frontier (`rsi/gepa/merge.py:250`), which matches the App. D.1 prose ("are pareto-optimal") but not the printed algorithm.
    - **Retries.** Up to 10 outer attempts, each with up to 10 pair draws (`merge.py:104`, `merge.py:146`); Alg. 4 shows a single draw followed by `continue`.
    - **Ancestor choice.** Alg. 4 builds the child from the first eligible common ancestor in iteration order. The reference collects every eligible ancestor (not tried as (i, j, a), S[a] ≤ min(S[i], S[j]), `DESIRABLE`) and draws one with weights ∝ S[a] (`merge.py:117`); the all-zero-weight case is deviation 3.
    - **"Both changed" branch.** The reference tests `π_a ≠ π_i and π_a ≠ π_j` (`merge.py:130`); Alg. 4 line 26 writes `π_i ≠ π_j ≠ π_a`. The merged text is the same either way, but when π_i = π_j ≠ π_a and S[i] = S[j] the reference draws `rng.choice` where Alg. 4 falls through to the default π_i, so the RNG stream can differ.
    - **Duplicate check.** Besides a repeated (i, j, a), the reference skips a repeated (i, j, module-source tuple) (`merge.py:155`).
    - **Overlap floor.** The pair must share at least `merge_val_overlap_floor = 5` scored D_pareto ids (`merge.py:252-253`). It is not in the paper; with full evaluation and \|D_pareto\| < 5 merge never fires.
    - **Acceptance test.** Alg. 4 returns the child with no test, and Alg. 1 has no merge branch; Fig. 3 says a new candidate is "first evaluated on a minibatch, and if improved, evaluated on a larger dataset". The reference scores the child on a 5-id subsample of **D_pareto** (up to ⌈5/3⌉ ids from each of the i-better, j-better and tie buckets, `merge.py:164`) and accepts iff Σ child ≥ max(Σ S_i, Σ S_j) on those ids (`rsi/gepa/engine.py:477`): ≥ rather than >, validation ids rather than a D_train minibatch, and against the better parent.
    - **Schedule and cap.** A merge is attempted only in the iteration right after an accepted reflective child and only while `merges_due > 0` (`engine.py:457`, `engine.py:589`); the paper says GEPA "routinely checks if the pool has 2 such candidates". The cap is deviation 4.
17. **Shadow-monitor wall time is credited back** (validation register fix). The write-only shadow monitor runs inside the loop's wall clock. After every monitor observation the engine now calls `credit_wall_time(ShadowMonitor.last_elapsed_s)` (`rsi/gepa/engine.py:200`, called from `rsi/gepa/tracing.py:311`), which forwards the seconds to every stopper with a `credit` method: `Timeout.credit` and `BudgetStopper.credit` → `rsi.core.Budget.credit` (`rsi/gepa/stoppers.py`). So `Config(timeout_s=…)` and `Budget(max_wall_s=…)` measure the loop's own time only. The credited total is written to the trace's `run_end` event (`monitor_wall_s_credited`), not to `state.json`, which stays deterministic. The reference has no shadow monitor. Regression test: `tests/test_gepa_claims_fixes.py::test_monitor_wall_time_does_not_count_toward_wall_clock_stoppers` (a fake clock: the monitor takes 100 s per observation, the loop 10 s per iteration; before the fix the seed's observation stopped the run at iteration 0).
18. **Provider stop reason for the truncation check.** `ReflectionProposer` reads the reason through `response_finish_reason` (`rsi/gepa/reflection.py:124`): `raw["stop_reason"]` (`claude -p` JSON, now also kept by `rsi.core.CachedLLM` on cache hits) or `raw["finish_reason"]` (OpenAI / LiteLLM style), then a `finish_reason` attribute, strings only, as the reference `ProposalAdapter._finish_reason`. The reason is recorded per component (`ReflectionResult.finish`, trace `proposal.finish_reasons`). Semantics are the reference's: a known-truncated reply (`length` / `max_tokens`, or an unterminated `<think>`) is rejected only when it has no complete fence pair. Cache entries written before the core fix carry no reason, so on them only the `<think>` heuristic applies; no recorded GEPA live reply lacked a fence pair, so no earlier decision changes. `rsi.core.AnthropicAPI` returns no `raw`, so the check cannot see its stop reason (a core-owned gap). Regression tests: `tests/test_gepa_claims_fixes.py::test_truncated_reflection_is_rejected_on_cache_miss_and_hit`, `::test_finish_reason_key_and_complete_fence_semantics`; live evidence `results/gepa/live_stop_reason_check.json`.

## 6. Limitations

- **What the numbers can show.** The offline numbers come from a simulated task model and a parametric mock reflection LM. They test *mechanisms and orderings*, not the paper's absolute gains. Most qualitative outcomes depend on the world design, as in E3, where the Pareto effect appears with coarse rewards only. The results JSONs keep every per-seed row so these trade-offs can be re-analysed.
- **The AgentQA mock.** It is stateless and ignores what a module is for. It repeatedly proposes the "write Python" skill to the reporter module, where the gate usually rejects it. Together with AgentQA's "expected X, got Y" feedback, this makes text feedback no better than score-only on AgentQA, also after the mock's parser fix (E1 re-run). The live haiku run is the better evidence there, but it is tiny: B = 80 with 8 validation questions, and the gain did not transfer.
- **What was not run.** E8 (cross-model transfer), E10 (code search) and E11 (adversarial) were not run as repo experiments (the claim audit ran E8 and E10 analogues in its scratch space; see `docs/claims/gepa.md` L10, L11). The mechanism for E10 exists; E8 and E11 need only different domains or rewards. E9 was run after the claim audit (§4 E9).
- **Winner's curse.** The returned candidate is the argmax over many D_pareto scores, each from a single draw. E6 and E7 report the resulting validation-to-test gaps. `NoiseMargin` and the leakage `critic=` are available but off by default, to stay faithful.
- **Live usage.** Only one small live smoke run was made. Real-LLM behaviour at larger budgets, such as prompt bloat, task drift and reward hacking, is untested here.

## 7. From-scratch validation (audit trace)

**How to run.**
- Script: `experiments/gepa/validate_gepa.py <run>`.
- Outputs go to `validation/gepa/<run>/`. The narrative is in `validation/gepa/RUNS.md`.
- Every run starts from the untouched seed artifact with a fresh run directory and, for the live run, a fresh LLM cache.

**What gets recorded.**
- With `out_dir` set, the engine writes `trace.jsonl` through `GEPATracer` (`rsi/gepa/tracing.py`).
- Each iteration records parent selection with the Pareto weights, every evaluation (per task and raw trials), the reflective records, the reflection prompt and reply, the actual diff, the gate arithmetic, the decision and the loop state.
- Tracing is on by default. `Config(trace=False)` disables it.
- A `ShadowMonitor` on the sealed splits scores every new incumbent. `Config(shadow_monitor=False)` disables it. Its spend is metered under `shadow:*` and never reaches the loop's USD stopper.

**The audit.** It re-derives every iteration independently from the trace, ledger and artifact store:
- gate arithmetic;
- the diff against the store;
- Pareto weights recomputed from Alg. 2;
- the round-robin choice;
- epoch-disjoint minibatches;
- rollout conservation.

Where a ground truth exists, it also scores each accept or reject against it.

**Results.**

| run | seed → final D_pareto | seed → final sealed | gate vs truth |
|---|---|---|---|
| RuleWorld, B = 300 | 0.000 → 0.322 | exact test 0.000 → 0.314 | 8 accepted, 1 false accept |
| RuleWorld + merge, B = 1500 | 0.000 → 0.898 | exact test 0.000 → 0.847 | 39 accepted / 17 rejected; 6 false accepts, 8 false rejects |
| AgentQA SimModel, B = 120 | 0.375 → 1.000 | holdout 0.667 → 1.000, ood 0.5 → 1.0 | 2 false accepts, 1 false reject |
| AgentQA live haiku | 0.500 → 0.500 (seed returned) | unchanged | unverifiable |

The live haiku run was stopped by its $1.20 USD guard after 51 of 100 rollouts. Haiku task replies run about 2,200 output tokens, about $0.012 per call.

**Findings.** All mechanics verified. The wrong steps are statistical, inherent to a strict gate on 3 single-draw examples and a 1-draw D_pareto argmax. None is an implementation error. Observations for follow-up:
1. `skip_infra_error` iterations are charged against B, and no stopper ends a run on a sustained backend outage. The usage-limit-interrupted live attempt kept looping.
2. Haiku's reflective rewrites overfit to the 3 minibatch items ("You are solving modular arithmetic problems…"). They were accepted on the minibatch and then scored below the seed on D_pareto. GEPA keeps them in the pool by design.

**Stage-B independent audit** (`experiments/gepa/validate_gepa_stageb.py`, report `validation/gepa/AUDIT.md`). It re-derives every iteration of every run without using `rsi.gepa`:
- it replays the shared RNG with the reference `EpochShuffledBatchSampler` imported from gepa-ai/gepa, together with an independent Alg. 2 and a merge proposer transcribed from `gepa/proposer/merge.py`;
- it rebuilds each offline reflection prompt with the reference `prompt_renderer` from a re-execution of the parent minibatch;
- it parses each raw reply with the reference `parse_proposal`;
- it recomputes gates, frontier, incumbent, round-robin pointers, merge schedule and the budget identity from the raw scores.

All 125 audited steps across the 6 run directories reproduce exactly: every parent draw, minibatch, merge triplet and subsample, and every prompt byte for byte. Verdicts: 89 correct, 26 questionable (statistical false accepts and rejects of the 3-example gate, plus 5 outage iterations that charged budget, now fixed), 0 wrong, 10 unverifiable (live, no ground truth).

**Claim-audit fix round.** The two open register items (monitor wall time; the E1 AgentQA table) are fixed (§5.17, §4 E1). The three offline runs were re-run from scratch with the fixed code into a scratch directory and re-audited by stage B: trajectories, run logs and audits are byte-identical, and every check and verdict count matches (`validation/gepa/AUDIT.md` §5).

