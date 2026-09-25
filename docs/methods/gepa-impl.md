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
| `rsi/gepa/merge.py` | `MergeProposer`: Algorithms 3–4 (common-ancestor triplet, `DESIRABLE`, module-wise "take the changed side", both-changed → higher aggregate). 5-id subsample with 3 buckets; reference soft cap or hard cap; zero-weight fallback. | – |
| `rsi/gepa/state.py` | `SearchState` (= `GEPAState`: candidates, parents DAG, per-id validation scores, frontier, round-robin pointers, discovery rollouts, trace). `RolloutCounter` by phase. JSON persistence. | `rsi.core.ArtifactStore` |
| `rsi/gepa/stoppers.py` | `MaxMetricCalls`, `MaxIterations`, `Timeout`, `FileStopper` (`gepa.stop`), `ScoreThreshold`, `NoImprovement`, `MaxCandidateProposals`, `MaxReflectionCost`, `BudgetStopper` (`rsi.core.Budget`), `Composite` | `rsi.core.Budget` |
| `rsi/gepa/engine.py` | `GEPAEngine`: the loop (paper Alg. 1 with reference semantics). Merge scheduling, skip rules, `FULL_EVAL_AND_ADD`, ledger nodes, callbacks, save and resume, and the hard/soft budget modes. | `rsi.core.Ledger`, `Node` |
| `rsi/gepa/api.py` | `run(...) -> ImprovementResult`. `optimize(seed_dict, trainset, valset, metric=...)` quick start through `MetricDomain`. `report(...)` for sealed-split reporting. `default_components`. | `rsi.core.run.transfer_report` |
| `rsi/gepa/rl.py` | `ScalarRLBaseline` / `run_scalar_rl`: a GRPO-style Bernoulli prompt policy trained on scalar reward only (group 12, 4 instances/step, β pull, validation every 20 steps, best-validation checkpoint). The vocabulary is `domain.rl_vocabulary`, or brainstormed once by the LLM. | `rsi.core.Evaluator` |
| `rsi/gepa/baselines.py` | `run_score_only` (ScoreOnlyReflection), `run_best_of_n` (BestOfN), `FewShotDemoOptimizer` / `run_fewshot` (MIPRO-lite: bootstrapped and labelled demos, grounded instruction proposals, TPE-like categorical search on validation minibatches). | – |
| `rsi/gepa/analysis.py` | `tree_metrics`, `gepa_curve` / `trajectory_curve` (true score of the returned candidate against rollouts), `curve_at`, `rollouts_to_target`, `gate_errors` | – |
| `rsi/gepa/mocks.py` | `GenericReflectionLM` (any domain), `AgentQAReflectionLM` (AgentQA + SimModel), `two_module_harness()` (AgentQA seed: solver prompt → Python tool → reporter prompt; `harness.py` stays frozen) | `rsi.core.MockLLM` |
| `rsi/domains/ruleworld/world.py` | `RuleWorld` / `WorldConfig`: modules, aspects, customer families, conflicting aspects, rule parsing (general, conditioned, contradiction, ticket facts, demos), the simulated task model, dilution, slip, optional interference, and the analytic `expected()` | – |
| `rsi/domains/ruleworld/domain.py` | `RuleWorldDomain(Domain)`: `execute`; a locked grader with μ_f ∈ {rich, symptom, score_only} and optional first-error-only; `reflective_record` (the reply module sees upstream notes); `rl_vocabulary`, `gold_text`, `demo_text`, `expected`; `make_domain(seed, feedback, **cfg)` | `rsi.core.Domain` |
| `rsi/domains/ruleworld/mocks.py` | `RuleWorldReflectionLM` + `ReflectionProfile`: a mock reflection LM that uses only the information in its prompt (§9.2 Tier-1 mock). It also answers the few-shot baseline's grounded-proposal prompts. | `rsi.core.MockLLM` |

The largest file, `rsi/gepa/engine.py`, has 492 lines.

**Tests.** Run them with `python -m pytest -q tests/test_gepa_core.py tests/test_gepa_engine.py tests/test_gepa_ruleworld.py tests/test_gepa_baselines.py tests/test_gepa_review.py`. There are 56 tests, all offline and deterministic, taking about 8 s.

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
| 7 | Combine complementary lessons | `MergeProposer` (Alg. 3–4, soft / hard cap) | E4: complementary merges beat both parents 94% of the time (+0.048); overlapping ones 19–26%. Net gain only with a rationed merge (hard cap: +0.014 [+0.003, +0.026] at B = 4000). **Reproduced qualitatively** |
| 8 | Genetic-Pareto | Lineage DAG (`SearchState.parents`, ledger) + Pareto selection | – |
| 9 | Beats GRPO with far fewer rollouts | `RolloutCounter` + `ScalarRLBaseline` + curves | E1: 5.7× / 6.1× fewer rollouts to reach 80% / 90% of the oracle; the GEPA curve dominates at every budget. This is inside the paper's 4–35× range but below the spec's ≥10× bar. **Partially reproduced** |
| 10 | Beats MIPROv2 | `FewShotDemoOptimizer` (MIPRO-lite) | E7: +0.179 / +0.211 test, prompts 1.5–1.6× shorter (paper ≈ 3–9×). **Score reproduced; length only partly** |
| 11 | Promise for optimizing code | Multi-task mode (`val_split=None` → D_pareto = D_train); components may be `.py` files; `optimize()` | Not run (E10 was not prioritised). **Mechanism implemented, not demonstrated** |
| 12 | Changes prompts only | `default_components` excludes `*.py`; control flow (`harness.py`) stays frozen (tested) | **Implemented (documented boundary; `components=` relaxes it)** |
| 13 | Needs a good metric; text feedback works best | μ_f contract; E2; E12 | E12: a train-saturated seed is returned untouched (the gotcha). **Reproduced** |
| 14 | Few examples overfit; keep a separate validation set | Separate D_train / D_pareto; sealed `report(...)` | E6: at \|train\| = 6 the gap is +0.424 when D_pareto = D_train and +0.031 with a separate val set; test 0.576 vs 0.644. **Reproduced** |
| 15 | Plain-language reflection on full traces | as 3 (traces and upstream notes go into the records) | – |
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

**Verdict.** Qualitatively reproduced: GEPA dominates, and the 6× ratio sits in the paper's "4–35×" range. The spec's "≥ 10×" is **not** met in this world.

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
- measured holdout accuracy over 20 questions and OOD accuracy over 20 questions from 4 unseen families.

| arm | holdout | OOD |
|---|---|---|
| seed | 0.46 | 0.39 |
| GEPA | 0.90 | 0.90 |
| ScoreOnly | 0.95 | 0.99 |
| ScalarRL (brainstormed vocabulary) | 0.90 | 0.89 |

- Every method finds the generic skills: "write a Python program" in the solver, plus answer format.
- Text feedback gives no edge here. The grader only says "expected X, got Y", which never says how to fix the failure.
- The stateless AgentQA mock also keeps proposing the Python skill to the *reporter* module, and the gate rejects it. Under score-only the mock chooses skills at random, which avoids that loop (see §6).

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

**Setup.** 30 seeds, B ∈ {1500, 4000}.

| arm | test @1500 (paired vs GEPA) | test @4000 (paired vs GEPA) | merges accepted per run @4000 | runs exceeding the cap @4000 |
|---|---|---|---|---|
| GEPA | 0.863 | 0.906 | – | – |
| +Merge, soft cap 5 (reference) | 0.835 (−0.028 [−0.063, +0.004]) | 0.910 (+0.004 [−0.009, +0.018]) | 11.2 | 87% |
| +Merge, hard cap 5 | 0.850 (−0.013) | **0.921 (+0.014 [+0.003, +0.026])** | 4.8 | 0% |
| +Merge, soft cap 1 | 0.868 (+0.005) | **0.922 (+0.016 [+0.004, +0.028])** | 5.7 | 100% |
| +Merge, soft cap 20 | 0.835 (−0.028) | 0.909 (+0.003) | 13.9 | 13% |

**Merged child against its parents.**
- Merges whose parents changed *different* modules (complementary): the child beats both parents 94% of the time, with a mean true-test gain of +0.048 over the better parent (n = 32).
- Overlapping merges: 19–26% beat both parents, with gains of +0.003 to +0.008 (n = 180–304).
- Merges consume 21–32% of the rollouts under the soft cap.

**Verdict.** Qualitatively reproduced: merge combines complementary module lessons, and its net effect depends on budget and timing.
- At B = 1500 no merge arm differs significantly from GEPA. The reference soft cap 5 trends negative: −0.028 [−0.063, +0.004].
- At B = 4000 only the rationed arms show a gain, and it is small but significant: hard cap 5 at +0.014 and soft cap 1 at +0.016. These arms accept about 5 merges and spend about 10% of rollouts on merge.
- The unrationed soft caps (11–14 merges, 21–26% of rollouts) show no significant gain.
- At B = 1500, soft cap 5 and soft cap 20 give identical runs. Merges pile up in `merges_due` before any valid triplet exists, so a cap of 5 never binds.
- The reference soft cap lets accepted merges exceed `max_merge_invocations` in 63–87% of runs (cap 5) and in 100% of runs (cap 1). This reproduces [run:merge-cap-probe] and the paper's own budget-allocation diagnosis of the Qwen degradation.
- **Not** reproduced: the spec's expectation that *early* merges are worse. Early merges gained more (+0.031 against +0.009 at B = 1500), because the complementary merges happen early.

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
4. **Merge cap.** `merge_cap_mode="reference_soft"` (the default) reproduces the soft cap. `"hard"` implements the paper's "maximum of 5 times".
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

## 6. Limitations

- **What the numbers can show.** The offline numbers come from a simulated task model and a parametric mock reflection LM. They test *mechanisms and orderings*, not the paper's absolute gains. Most qualitative outcomes depend on the world design, as in E3, where the Pareto effect appears with coarse rewards only. The results JSONs keep every per-seed row so these trade-offs can be re-analysed.
- **The AgentQA mock.** It is stateless and ignores what a module is for. It repeatedly proposes the "write Python" skill to the reporter module, where the gate rejects it. This makes text feedback look no better than score-only on AgentQA. The live haiku run is the better evidence there, but it is tiny: B = 80 with 8 validation questions, and the gain did not transfer.
- **What was not run.** E8 (cross-model transfer), E9 as its own experiment (the `+all` arms of E3 partly cover it), E10 (code search) and E11 (adversarial) were not run. The mechanisms for E9 and E10 exist; E8 and E11 need only different domains or rewards.
- **Winner's curse.** The returned candidate is the argmax over many D_pareto scores, each from a single draw. E6 and E7 report the resulting validation-to-test gaps. `NoiseMargin` and the leakage `critic=` are available but off by default, to stay faithful.
- **Live usage.** Only one small live smoke run was made. Real-LLM behaviour at larger budgets, such as prompt bloat, task drift and reward hacking, is untested here.
