# core-qa: quality of the shared core (`rsi/core`, `rsi/domains/agentqa`)

Owner slug `core-qa`. Scope: offline pytest suites for every `rsi.core` module and for the
shared AgentQA domain, backward-compatible bug fixes in both packages, live smoke tests of the
real `claude` CLI, and one calibration experiment that checks the core's statistical claims end
to end. There have been two rounds: the implementation (bugs 1-20) and an adversarial review
(bugs 21-33, a genericity audit, and a corrected claims table).

## Module map (tests)

| Test file | Covers | Tests |
|---|---|---|
| `tests/conftest.py` | puts the repo root on `sys.path` and re-registers the `live`/`slow` markers. It has no fixtures, hooks or options, so it cannot affect other test files | - |
| `tests/test_core_llm.py` | `Usage`, `UsageMeter` (incl. threads), `MockLLM`, `CachedLLM` (hit/miss, key parts, errors not cached, corrupt entries, concurrent writers, real-spend totals), `get_llm`, `ClaudeCLI` against a **fake `claude` executable** (usage/cost parsing, argv, list output, `is_error`, garbage, retry, timeout, `run_agent`), `extract_code(_blocks)`, `extract_json`, `extract_tag` | 22 |
| `tests/test_core_artifact.py` | identity/hash/eq, `with_files` deletes, `changed_files`, `diff`/`diff_size` (incl. no trailing newline and `--`/`++` content), render and `parse_file_blocks`, `to_dir`/`from_dir` (binary skip, excludes, globs, `clean`), path-escape refusal, pickle/deepcopy/json | 22 |
| `tests/test_core_tasks.py` | `Task` json/frozen, split roles, sealing, `from_tasks` (partition, rounding, determinism, `by_family`, zero fractions), jsonl round trip re-seals | 10 |
| `tests/test_core_evaluate.py` | `Domain.run` error handling, `FunctionDomain`, `leakage_terms`, `smoke`, `Trial.to_json`; `Evaluator` aggregation **exactly as RRSI `aggregate`** (missing trial = failure score with full denominator k, C = mean tokens over trials that report tokens), caching, `n_rollouts`, infra retries, `seed_offset`, sealed-split refusal, `on_trial` | 24 |
| `tests/test_core_stats.py` | `noise_from_repeats` (sqrt(2)), `noise_from_trials` (analytic within-task SE, within-task-only resampling), CIs, paired diffs, `prob_better_bootstrap`, `beta_lcb` (= scipy), `spearman` (= scipy with ties), `gini`, `summarize_runs` | 14 |
| `tests/test_core_gates.py` | `Scored.from_eval`, every gate against the `rrsi/selection.py` formulas (floor on S*, cost-rule boundaries, shaped rule, novelty, guards, ablations), `BootstrapRigor`, `DualGate`, `AllOf`, `select`, float ties, numpy scores | 17 |
| `tests/test_core_ledger.py` | add/seq/queries, lineage (foreign parents, cycles), update merge/persistence, resume after a torn write, `results.tsv`, concurrent adds, `ArtifactStore` | 14 |
| `tests/test_core_editors_critic.py` | `parse_proposal`, scope guard, `RewriteEditor`, `AgentEditor` via an **offline fake agent CLI**; `LeakageCritic` (whole-token numbers, added lines only, newline-less last lines, hunk-aware parsing, patterns, LLM review, **fails closed**) | 32 |
| `tests/test_core_sandbox_run.py` | `run_cmd` (env scrub, **timeout kills the process group**, memory and CPU rlimits), `run_python`, `call_function`, `Budget`, `usd_of`, `ImprovementResult`, `transfer_report` | 16 |
| `tests/test_core_regressions.py` | round-2 fixes: non-finite scores, `failure_score`, NaN in `DualGate`/`select`, disk-cache identity (model, domain, task content, legacy entries), replay usage, `extra_files` escape, interrupted `run_cmd`, thread safety + limits, `AgentEditor` context names, numpy in `save`; **fidelity**: `noise_from_trials` vs a line-by-line port of RRSI `calibrate.bootstrap_se` | 20 |
| `tests/test_core_generic.py` | **genericity audit**: a complete propose → run → score → keep loop written only against the public core API, run unchanged on two new domains defined in the test (see below) | 2 |
| `tests/test_agentqa_generators.py` | every family's answer **re-derived from the question text** (150 draws × 7 families), split structure, determinism, independent RNG streams, `PYTHONHASHSEED` stability, overlap + `decontaminate` | 14 |
| `tests/test_agentqa_domain.py` | answer extraction/normalization/grading, harness failures, output coercion, LLM seeding/metering/trace, **LLM and tool budgets**, infra = missing trial (incl. swallowed), SimModel behaviours, the domain property (a tool harness transfers, a lookup table only inflates evolve) | 67 |
| `tests/test_agentqa_integrity.py` | round-2 fixes: **non-terminating harnesses are stopped** (plain, exception-swallowing, import-time loops), time inside model/tool calls is not counted, a timed-out harness cannot call the model, the harness frame holds no `Task`, **grader tampering is caught and undone** (module functions, `AgentQADomain.grade`, builtins, `EvalResult`), `decontaminate` keeps custom seals | 13 |
| `tests/test_core_live.py` (`-m live`) | real `ClaudeCLI.complete`, `CachedLLM` over it, `AgentEditor` on a two-file artifact with a locked file; writes `results/core-qa/live_smoke.json` | 3 |

Run: `pytest tests/test_core_*.py tests/test_agentqa*.py` gives **287 passed in about 15 s**
(offline, deterministic). `pytest tests/test_core_live.py -m live` runs the 3 live tests.

## Capability checklist → code → evidence

The spec for this slug is `docs/ARCHITECTURE.md`: its component list and its five contracts.

| Item | Code | Tests | Experiment / result |
|---|---|---|---|
| LLM backends, per-role metering, cache | `llm.py` (`MockLLM`, `ClaudeCLI`, `AnthropicAPI`, `CachedLLM`, `artifact_usage`, `get_llm`) | `test_core_llm`, `test_core_regressions`, live tests | live: 96 Haiku calls through `CachedLLM`; a replay spends $0 and still reports the trials' real $0.0935 cost |
| Artifact (immutable, content-addressed, diff, dirs) | `artifact.py` | `test_core_artifact` | - |
| Split discipline (evolve/val decide; holdout/ood/test sealed) | `tasks.py`, `Evaluator(allow_sealed=False)`, `transfer_report` | `test_core_tasks`, `test_core_evaluate`, `test_core_generic` (the loop never unseals) | - |
| Domain = execute + locked grade | `domain.py` (`Domain.run`, `failure_score`, `FunctionDomain`) | `test_core_evaluate`, `test_core_regressions`, `test_core_generic` | - |
| Evaluator (parallel, cached, k trials, missing = 0) | `evaluate.py` | `test_core_evaluate` (hand-computed RRSI aggregates), `test_core_regressions` | used by every calibration job |
| Noise band, CIs, paired diffs, Beta LCB, Spearman, Gini | `stats.py` | `test_core_stats`, RRSI-port fidelity test | `core_calibration` C1/C2 |
| Gates + `select` | `gates.py` | `test_core_gates`, `test_core_regressions` | `core_calibration` C1/C3/C4 |
| Ledger tree, results.tsv, ArtifactStore | `ledger.py` | `test_core_ledger`, `test_core_generic` (lineage, resume) | - |
| Editors + scope guard | `editors.py` | `test_core_editors_critic`, live `AgentEditor` | - |
| Leakage critic (denylist on added lines + LLM, fails closed) | `critic.py` | `test_core_editors_critic`, `test_core_generic` (a leaked task id is rejected) | - |
| Sandbox (timeouts, rlimits) | `sandbox.py` | `test_core_sandbox_run`, `test_core_regressions` | - |
| Budget / ImprovementResult / transfer_report | `run.py` | `test_core_sandbox_run`, `test_core_generic` | - |
| AgentQA domain + SimModel | `domains/agentqa` | `test_agentqa_*` | `core_calibration` (AgentQA arm, sim and live) |
| Contract 1: `Config` + `run(...) -> ImprovementResult` | `run.py` | `test_core_generic.mini_loop` is a 40-line method in this pattern | - |
| Contract 2: frozen things stay frozen | sealing, grading only in `Domain.grade`, AgentQA tamper tripwire | `test_agentqa_integrity` | - |
| Contract 3: everything is a tree | `Ledger` | `test_core_ledger`, `test_core_generic` | - |
| Contract 4: offline first, deterministic | `MockLLM`, `SimModel` | all offline tests | two full runs of `core_calibration` gave identical JSON |
| Contract 5: honest measurement | `stats.summarize_runs`, pooled bootstrap CIs | - | `core_calibration`: 30 seeds, 2 domains, verdicts test claim vs alternative |

## Genericity audit (`tests/test_core_generic.py`)

`mini_loop(domain, seed_artifact, llm_propose=..., rounds=4)` uses only the public API:
`RewriteEditor`, `LeakageCritic(domain.leakage_terms("evolve"))`, `Evaluator`, `noise_from_trials`,
`RRSIGate` + `select`, `Ledger`, `Budget`, `ImprovementResult` and `transfer_report`. It runs unchanged on two
domains that exist only in the test:

* **curve**: a `FunctionDomain`. The artifact is `model.json` holding polynomial coefficients, and
  scores are seeded noisy closeness to a hidden curve. The loop improves holdout S, keeps a lineage
  back to the seed, and the critic rejects the round that leaks an evolve task id.
* **sorter**: a `Domain` subclass whose artifact is a Python program run in the sandbox
  (`call_function`). Its score is `-loss` with `score_range = (-5, 0)`. A crashing proposal scores -5
  (the new `failure_score`), is discarded, and the correct program wins.

No core change was needed except bug 24 below: before it, the crashing sorter scored 0.0 (the
best possible `-loss`), so it would have been kept.

## Bugs found and fixed (all backward compatible; no public signature or default changed)

### Round 1 (implementation)

| # | Where | Bug | Fix |
|---|---|---|---|
| 1 | `artifact.diff` | files without a trailing newline glued `-old`/`+new` lines together, so **`LeakageCritic` accepted a newline-less leaked line** | git's `\ No newline at end of file` marker |
| 2 | `diff_size`, `critic.added_lines` | content starting with `--`/`++` was taken for headers | hunk-aware parsing |
| 3 | `Artifact.to_dir`, `apply_scope` | `=== FILE: ../x ===` wrote outside the directory | `is_safe_relpath`; refuse |
| 4 | `CachedLLM` | shared temp file: concurrent misses on one key crashed | unique temp names, atomic replace |
| 5 | `CachedLLM` metering | cache hits counted as real calls and dollars | `:cached` roles carry tokens only; `saved`, `hits`, `misses` |
| 6 | `gates` | float summation order made ties pass "strictly better" | `TIE_EPS = 1e-9` |
| 7 | `Verdict` | numpy scores gave `numpy.bool_`; `bool(verdict)` raised | coerce |
| 8 | `Domain.run` | `SystemExit` escaped; non-numeric grader scores and bare outputs crashed | contained |
| 9 | `Trial.to_json` | non-JSON meta crashed the disk cache | JSON-safe copy |
| 10 | `Evaluator` | duplicate seeds halved scores; non-atomic, crash-prone disk cache | dedupe; atomic; re-run corrupt entries |
| 11 | `Ledger` | torn last line broke resume; seq reset; lineage loops; lost fields; broken TSV rows | fixed |
| 12 | `sandbox` | scratch-dir leak (48,613 dirs); `func` code injection; relative scripts | fixed |
| 13 | `AgentEditor` | bad `_proposal.json` crashed; `"components": "prompt"` split into characters | `_header_fields` |
| 14 | `LeakageCritic` | int terms crashed; string objections split; `12` fired on `12.5`; nondeterministic order | fixed |
| 15 | `extract_json` | a fenced scalar shadowed a later object | prefer objects |
| 16 | `ClaudeCLI._run` | list-shaped output crashed | handled |
| 17 | AgentQA grader | bold/backtick/`\boxed`/unicode-minus answers graded wrong | normalization |
| 18 | AgentQA execute | a swallowed backend error became a cached wrong answer; `sys.exit` escaped | infra remembered; contained |
| 19 | AgentQA `units` | banker's rounding on exact halves | exact half-up |
| 20 | docs | stale `SplitGuard` reference; zero fractions divided by zero | fixed |

### Round 2 (adversarial review)

Every fix has a test that fails on the round-1 code: 25 of the 27 new tests in
`test_core_regressions.py` and `test_agentqa_integrity.py` failed when run against it. The other
two cover a limit that did not exist before and the absence of a deadlock.

| # | Where | Bug (how found) | Impact | Fix | Test |
|---|---|---|---|---|---|
| 21 | AgentQA `execute` | **The harness runs in-process, so it could monkeypatch the grader** (probe: `d.is_correct = lambda *a: True`) | the tampering harness scored 1.0, and **every later trial in the process scored 1.0 too, including the seed harness** | the grading path (`is_correct`/`extract_answer`/`normalize`, `AgentQADomain.grade/execute/run`, `Domain.run`, `Evaluator.run_one/evaluate`, `EvalResult.task_scores/score`, the `re` functions and builtins the grader uses) is snapshotted at import. A harness that changes any of it is graded `tamper: ...` and the originals are restored; `grade` also restores before grading | `test_grader_tampering_is_caught_and_undone` (4 attack variants), `test_grade_restores_a_concurrently_patched_grader` |
| 22 | AgentQA `execute` | **A non-terminating harness hung the evaluator forever** (probe: `while True: pass` never returned) | one bad LLM-written harness froze a whole experiment | the harness runs in its own thread. A watchdog stops it after `harness_timeout_s = 60` s of *its own* time (model and tool calls excluded) by raising `HarnessTimeout`, a `BaseException`, and keeps raising it until the thread ends. A timed-out harness can no longer call the model or tools. The harness's calling frame no longer holds the `Task` | `test_non_terminating_harness_is_stopped_and_graded` (3 variants), `test_time_inside_model_and_tool_calls_is_not_counted`, `test_timed_out_harness_can_no_longer_call_the_model`, `test_harness_frame_does_not_hold_the_task` |
| 23 | `Domain.run` | a non-finite grader score (an artifact that prints `"nan"` under `-abs(float(out) - y)`) went straight into S | S = nan; `select` returned the NaN candidate; `DualGate` accepted it | non-finite scores are grader errors | `test_non_finite_grader_score_is_a_grader_error` |
| 24 | `Domain.run`, `Evaluator` | failed and missing trials always scored 0.0 | in any domain with negative scores (`-loss`), **a crash beat every working artifact** | `Domain.failure_score = score_range[0]` (0.0 for the default range, so no existing domain changes) | `test_failure_score_follows_score_range`, `test_core_generic[sorter]` |
| 25 | `gates` | `DualGate` used `c < b - tol`, which is False for NaN; `select`'s `max` returns a NaN key that comes first | NaN candidates admitted and selected | `not c >= ...`; `select` marks NaN keys inadmissible (tuple keys still work) | `test_dual_gate_rejects_nan_capability`, `test_select_never_returns_a_nan_candidate` |
| 26 | `Evaluator` disk cache | the key was (artifact, task id, seed) only (probe: model B read model A's trials). `make_suite(seed=s)` also reuses task ids across seeds | a shared `cache_dir` served the wrong model's or the wrong suite's trials without any error | each entry records `{llm (minus cached: wrappers), domain, sha of task input/target/family}`; a mismatch is a miss. Entries from older caches are still read | `test_disk_cache_is_keyed_on_task_model/_content`, `..._legacy_entries_...` |
| 27 | `CachedLLM` + AgentQA | a cache hit returns cost 0, so a replayed trial's `cost_usd` was $0 while the live run's was not (probe: $0.02 → $0.00) | `EvalResult.dollars` and SoL-Pi's `cost`/`eta` metrics changed between a run and its replay | hits carry `raw={"cached": True, "usage": original}`; new `rsi.core.artifact_usage(resp)`; AgentQA uses it. Spend metering is unchanged | `test_cached_hit_carries_original_usage`, `test_agentqa_trial_cost_is_the_same_on_a_replay`; live check below |
| 28 | `call_function` | `extra_files={"../x": ...}` wrote outside the scratch dir (probe wrote `/tmp/escape_probe.txt`). The discovery domain passes artifact file names | writes outside the sandbox | `ValueError` before anything is written | `test_call_function_refuses_escaping_extra_files` |
| 29 | `run_cmd` | `preexec_fn`, which Python documents as unsafe with threads (the Evaluator's workers reach it through `Tools.python`); an exception while waiting (Ctrl-C, the new watchdog) left the child running | possible fork deadlock; orphan processes | `start_new_session=True` + `resource.prlimit` on the child; the group is killed on any exception | `test_run_cmd_kills_its_child_when_interrupted`, `test_run_cmd_is_thread_safe_and_keeps_limits` |
| 30 | `AgentEditor` | a context name `"../harness.py"` overwrote the artifact before the agent ran | the context text became part of the proposal | unsafe names are skipped and listed in `meta["skipped_context"]` | `test_agent_editor_skips_escaping_context_names` |
| 31 | `ImprovementResult.save` | `json.dumps(default=float)` crashed on a numpy array in the trajectory | the run crashed at the very end and lost its results | tolerant JSON default | `test_improvement_result_save_accepts_numpy_values` |
| 32 | `decontaminate` | custom splits sealed with `suite.seal(...)` came back unsealed | sealed data readable | re-seal | `test_decontaminate_keeps_custom_sealed_splits_sealed` |
| 33 | SimModel | a comment said the fuzzy match picks "the longest" question; the code picks the first | misleading | comment fixed (behaviour unchanged) | - |

**Behaviour changes other engineers may notice.** Round 1: `role:cached` usage has 0 calls and
$0; diffs carry the `\ No newline` marker; gates treat differences below 1e-9 as ties;
`Ledger.update(unknown=...)` goes to `meta`; `to_dir` raises on unsafe names; `run_python()`
deletes its scratch dir. Round 2:
* AgentQA harnesses run in a worker thread and are stopped after 60 s of their own time. Set
  `domain.harness_timeout_s` to change the limit.
* A harness that replaces a grading-path attribute is graded `tamper: ...`.
* A non-finite grader score is a grader error.
* Failures score `score_range[0]`. This is 0.0 for every domain in the repo today.
* Disk-cache entries written for another model, domain or task are re-run.
* `CachedLLM` hits have `raw` set.
* `call_function` raises on escaping `extra_files`.

None of these changes any number for a terminating, non-tampering harness. The round-1 and round-2
AgentQA code produce identical trials (output, score, tokens, cost, steps and error for 200 trials of
the seed harness and a tool-using harness on evolve, holdout and ood). Two full runs of
`core_calibration` give identical JSON, and the whole test tree still collects (598/602, 4 live
tests deselected).

## Experiment: `experiments/core-qa/core_calibration.py`

`python experiments/core-qa/core_calibration.py [--llm sim|claude:haiku] [--seeds N] [--quick] [--replot JSON]`
writes `results/core-qa/core_calibration.{json,png}`. It uses the shared core end to end
(`Evaluator` → `noise_from_trials`/`noise_from_repeats` → `NoiseFloor`/`StrictImprovement`/`MinGain`)
on **two domains**: AgentQA (seed harness + SimModel) and a Bernoulli toy `FunctionDomain`. The null
is two evaluations of an unchanged artifact. The run uses 30 seeds × k ∈ {2, 3, 5} × 80 evaluations
per seed, which gives **1,200 independent null pairs per cell and 2,400 per k** with both domains
pooled. It takes 170-180 s of wall time on 2 workers and is deterministic: two runs produced identical JSON.

**Review change.** Round 1 used 200 pairs per cell and called a claim "reproduced" whenever 0.975 lay
inside the CI. At k=3 and k=5 that CI also contained the small-k bias prediction, so the verdict
said nothing. Verdicts now test **both** hypotheses (claim vs `Phi(z·sqrt((k-1)/k))`). A claim is
reproduced only if the CI contains the claim and excludes the alternative. With the extra power, the
round-1 conclusion "reproduced for k ≥ 3" turned out to be **wrong at k=3**.

| Claim | Result (pooled 95% bootstrap CI) | Verdict |
|---|---|---|
| C1 (RRSI `calibrate.py`): with delta from the within-task bootstrap of one k-trial evaluation, an unchanged artifact clears `S* - delta` "about 97.5% of the time" | both domains pooled: **k=2 0.926 [0.915, 0.936]**, **k=3 0.952 [0.943, 0.960]**, k=5 0.971 [0.965, 0.978]. Per cell: AgentQA 0.918 / 0.947 / 0.973; toy 0.933 / 0.957 / 0.969. The small-k predictions are 0.921 / 0.949 / 0.963 | **NOT reproduced at k=2 and k=3**: the values match the small-k bias prediction and exclude 0.975. **Reproduced at k=5** pooled, only barely: the prediction 0.963 is just below the CI. The toy cell at k=5 cannot tell the two apart |
| Diagnosis | resampling k trials within a task estimates the per-task variance with the plug-in (ddof=0) estimator, which is (k-1)/k too small. `noise_from_trials` equals a line-by-line port of RRSI's `bootstrap_se` (fidelity test), so **RRSI's own estimator has this bias**. Scaling delta by sqrt(k/(k-1)) gives 0.976 [0.970, 0.982] (k=2) and 0.978 [0.973, 0.984] (k=3); at k=5 it gives 0.982 [0.977, 0.987], slightly conservative | explained |
| C2: bootstrap delta = repeated-evaluation delta | delta_boot / delta_repeat: AgentQA 0.699 / 0.806 / 0.901, toy 0.726 / 0.844 / 0.919. sqrt((k-1)/k) = 0.707 / 0.816 / 0.894 | **NOT reproduced**: biased low as predicted (the toy at k=3 is 3% above the prediction, a per-seed ratio of noisy estimates) |
| C3 (autoresearch-mlx): "strictly better" chases noise | strict keeps 45-52% of null re-evaluations. `MinGain(delta)` keeps 3.7-5.8% at k ≥ 3 and 8.2-8.8% at k=2. With the corrected delta it keeps 2.3-3.8% (the one-sided z=2 target is 2.3%) | **reproduced** for "strict chases noise". A noise band stops most of it, but the uncorrected band lets 2-4× the nominal rate through at small k |
| C4 (bug 6): float ties | 123 of 452 exact ties differed in float; the pre-fix strict rule accepted 62 of them, the fixed rule 0 | fix confirmed |

**Practical consequence.** At RRSI's default k=2, a floor built from one evaluation's bootstrap is
**stricter** than documented, not looser: an unchanged harness falls below `S* - delta` about 8% of
the time instead of 2.5%. A noise band built from it (`MinGain`) lets about 9% of pure-noise gains
through instead of about 2.3%. Use repeated base evaluations (`noise_from_repeats`, which RRSI
prefers when R ≥ 2) or scale by sqrt(k/(k-1)).

## Live smoke

* **Round 1** (`tests/test_core_live.py`, about $0.017 per run, recorded in
  `results/core-qa/live_smoke.json`): `ClaudeCLI.complete` returned "pong"; the `CachedLLM` replay
  used 0 calls and $0; `AgentEditor` edited only the editable file.
* **Round 2** (this review, one run): `python experiments/core-qa/core_calibration.py --llm claude:haiku`.
  This is the first live run of the experiment script, and it exercises the new threaded AgentQA
  harness, `ClaudeCLI`, `CachedLLM` and the `Evaluator`. It made 96 Haiku calls with 0 infra
  failures in 394 s and cost **$0.70**; Haiku's answers averaged about 1,300 output tokens. The seed
  harness scored S = 0.50 and 0.15 on the two numeric suites. Verdicts are "inconclusive (too few
  pairs)" by design (8 pairs). Output: `results/core-qa/core_calibration_claude-haiku.{json,png}`.
  A replay of one evaluation through a no-spend inner model gave 12 hits, 0 misses and $0 spend,
  and the trials still report their original $0.0935 inference cost (bug 27).

## Applying the core to a new problem (checked by running it)

```python
from rsi.core import (Artifact, Evaluator, FunctionDomain, GateContext, NoiseFloor, RRSIGate, Scored, Task,
                      TaskSuite, noise_from_trials, select, transfer_report)

tasks = [Task(f"t{i}", {"x": i}, i * i, family="squares") for i in range(40)]
suite = TaskSuite.from_tasks(tasks, fractions={"evolve": 0.5, "holdout": 0.5}, seed=0)

def execute(art, task, seed, llm):              # run the artifact (here: a tiny Python program)
    ns = {}; exec(art["solver.py"], ns); return ns["solve"](**task.input)

dom = FunctionDomain(suite, execute, lambda t, out: float(out == t.target), name="squares")
ev = Evaluator(dom, llm=None, workers=4)
seed = Artifact({"solver.py": "def solve(x):\n    return x + x\n"})
cand = seed.with_files({"solver.py": "def solve(x):\n    return x * x\n"})
base = ev.evaluate(seed, "evolve", k=3)
delta = noise_from_trials(base.trial_matrix()).delta      # 0 here (deterministic); biased low for small k
winner, verdicts = select([("cand", Scored.from_eval(ev.evaluate(cand, "evolve", k=3)))],
                          Scored.from_eval(base), RRSIGate(), GateContext(best_score=base.score, delta=delta))
report = transfer_report(dom, None, {"seed": seed, "cand": cand}, splits=("evolve", "holdout"))
```

For a full loop (proposer, critic, ledger, budget, result object) on a new problem, copy
`mini_loop` from `tests/test_core_generic.py`. If your metric is "lower is better", return `-loss`
from `grade` and set `score_range = (worst, best)` so failures score `worst`.

## Deviations from the RRSI reference (`rrsi/calibrate.py`)

* `noise_from_trials` uses `np.std(ddof=1)` over 2000 bootstrap replicates with `seed=0`. RRSI uses
  `statistics.pstdev` with `random.Random(7)`. The difference is below Monte-Carlo error, and the
  fidelity test checks agreement within 6%.
* RRSI's pooled-evaluation rescale `sqrt(k_pooled / k_single)` has no core counterpart. With several
  base evaluations use `noise_from_repeats`, which is what RRSI prefers anyway.
* The core `LeakageCritic` is simpler than RRSI's reviewer: it has no credential patterns, no
  empty-diff rejection and a shorter prompt. `rsi.rrsi` ships the faithful version.

## Limitations and open requests

* `noise_from_trials` is biased low by sqrt((k-1)/k), faithfully to RRSI. Request: an opt-in
  `small_k_correction=False` argument, which would change a public signature. The experiment shows
  that at k=2 and k=3 it matters.
* **The AgentQA tamper tripwire is not a security boundary.** A harness runs in-process: it can still
  read caller objects (`gc`, `sys._current_frames()`), SimModel's answer table through the
  `llm` closure, or defeat the tripwire deliberately. It catches the realistic failure (an LLM
  "fixing" the grader) and stops cross-trial contamination. Real isolation needs a subprocess harness
  runner, which would change the domain's cost profile. That is the integrator's call.
* The watchdog cannot interrupt a harness blocked inside one long C call (for example `time.sleep(1e9)`).
  Such a harness is abandoned at the budget (the trial returns) and stopped when it next runs bytecode.
* Other domains that record `resp.usage.cost_usd` as the artifact's cost (instead of
  `rsi.core.artifact_usage(resp)`) still report $0 on cache replays. Request to domain owners:
  switch to `artifact_usage`.
* SimModel instances of different `skill` share the name `sim-model`. The disk-cache identity uses the
  model name, so give them distinct `name=` values before sharing a `cache_dir`.
* `make_suite` still has one evolve/holdout repeat (seed 0). The opt-in `decontaminate` removes it;
  making it the default would change every AgentQA number, so it is the integrator's call.
* `TaskSuite.split("typo")` returns `[]` silently. Raising would break methods that probe an optional
  `val` split.
* `AgentEditor` needs a bare `ClaudeCLI`: agent runs are not cached. Autoresearch already unwraps
  `CachedLLM.inner`.
* The 48,613 scratch dirs leaked before round 1's fix are still in `/tmp`. Other engineers' runs
  created them, so they were not deleted.
