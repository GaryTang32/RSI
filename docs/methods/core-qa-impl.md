# core-qa: quality of the shared core (`rsi/core`, `rsi/domains/agentqa`)

Owner slug `core-qa`. Scope: offline pytest suites for every `rsi.core` module and for the
shared AgentQA domain, backward-compatible bug fixes in both packages, a live smoke test of
the real `claude` CLI, and one calibration experiment that checks the core's statistical
claims end to end.

## Module map (tests)

| Test file | Covers | Tests |
|---|---|---|
| `tests/conftest.py` | puts the repo root on `sys.path`, re-registers the `live`/`slow` markers. No fixtures, hooks or options, so it cannot affect other test files | - |
| `tests/test_core_llm.py` | `Usage`, `UsageMeter` (incl. threads), `MockLLM`, `CachedLLM` (hit/miss, key parts, errors not cached, corrupt entries, concurrent writers, real-spend totals), `get_llm`, `ClaudeCLI` against a **fake `claude` executable** (usage/cost parsing, argv, list output, `is_error`, garbage, retry, timeout, `run_agent`), `extract_code(_blocks)`, `extract_json`, `extract_tag` | 22 |
| `tests/test_core_artifact.py` | identity/hash/eq, `with_files` deletes, `changed_files`, `diff`/`diff_size` (incl. no trailing newline and `--`/`++` content), render and `parse_file_blocks` (fences, prose, CRLF, delete marker), `to_dir`/`from_dir` (binary skip, excludes, globs, `clean`), path-escape refusal, pickle/deepcopy/json | 22 |
| `tests/test_core_tasks.py` | `Task` json/frozen, split roles, sealing (`split`/`seal`/`unseal`/`is_sealed`), `from_tasks` (partition, rounding remainder, determinism, `by_family`, zero fractions), jsonl round trip re-seals | 10 |
| `tests/test_core_evaluate.py` | `Domain.run` error handling (execute/grader exceptions, `SystemExit`, bare outputs, non-numeric scores), `FunctionDomain`, `leakage_terms`, `smoke`, `Trial.to_json`; `Evaluator` aggregation **exactly as RRSI `aggregate`** (missing trial = 0 with full denominator k, C = mean tokens over trials that report tokens), `n_missing`, `trial_matrix`, families, worst/best, parallel = serial, memory and disk cache, `n_rollouts`, infra failures not cached, `seed_offset`, explicit/duplicate seeds, sealed-split refusal, custom task lists, `on_trial` | 24 |
| `tests/test_core_stats.py` | `noise_from_repeats` (sqrt(2) factor), `noise_from_trials` (matches the analytic within-task SE, resamples within tasks only), `fixed_noise`, `bootstrap_ci` (width is about 2·1.96·SE), `paired_diff_ci`, `prob_better_bootstrap`, `beta_lcb` (= scipy; normal fallback without scipy), `spearman` (= scipy with ties), `gini` (= mean-abs-difference definition), `mean_se`, `summarize_runs` | 14 |
| `tests/test_core_gates.py` | `Scored.from_eval`, `StrictImprovement` (ties, lower-is-better, min_gain), `ImprovementOrEqual`, `MinGain`, `NoiseFloor` (S* vs incumbent, boundary, slow-slide blocking), `CostRule` (budget boundary, `dS == delta` is within band, shaped rule incl. novelty, zero costs, ablation), `MetricGuard`, `RRSIGate` order/reasons/ablations, `BootstrapRigor`, `DualGate`, `AllOf`/`&`, `select` (argmax over admissible, key, ties, empty), float ties, numpy scores | 17 |
| `tests/test_core_ledger.py` | add/seq/queries, lineage (incl. foreign parents and cycles), update merge and persistence, unknown update fields, resume, torn-write recovery, re-add keeps seq, `Node` json, non-JSON meta, render, `results.tsv` (autoresearch 5 columns, crash rows, multi-line changes), concurrent adds, `ArtifactStore` | 14 |
| `tests/test_core_editors_critic.py` | `parse_proposal` (header, string components, truncation, deletes, scope guard, blocked-only means no change, unsafe paths, no blocks, header only before the first block), `apply_scope`, `RewriteEditor` (prompt layout, context budget, system/seed, LLM error), `AgentEditor` via an **offline fake agent CLI** (scoped edits, `_proposal.json`, `_context/`, cleanup, `keep_dirs`, agent errors, malformed proposal files); `LeakageCritic` whole-token numbers, case/length, only added lines, newline-less last line, hunk-aware `added_lines`, regex patterns, counters, LLM accept/reject, **fail-closed** (unparseable, list replies, backend down), denylist short-circuits the LLM, non-string terms | 32 |
| `tests/test_core_sandbox_run.py` | `run_cmd` (stdin/cwd/exit codes, env scrub, **timeout kills the whole process group**, memory rlimit, CPU rlimit), `run_python` (code/script/relative script, cwd, timeout, scratch cleanup), `call_function` (results, exceptions, timeout, dotted names, extra files, marker spoofing, name validation, cleanup), `Budget` (every limit, STOP file, wall clock), `usd_of`, `ImprovementResult.summary/save`, `transfer_report` (report-only unsealing, paired diffs, families, `unseen_mean`, reference) | 16 |
| `tests/test_agentqa_generators.py` | every family's answer **re-derived independently from the question text** (150 draws × 7 families), units half-up rounding, default split structure, options, determinism, independent RNG streams per split, stability across `PYTHONHASHSEED`, evolve/holdout overlap and `decontaminate` | 14 |
| `tests/test_agentqa_domain.py` | `extract_answer` / `normalize` / `is_correct` (commas, `$`, signs, unicode minus, bold labels and values, backticks, `\boxed{}`, string answers, suffix rule, last-number rule), grade feedback; harness failures (missing file, no `solve`, syntax error, exceptions, `sys.exit`, missing LLM), output coercion, `files` access, LLM seeding/metering/trace, **LLM and tool budgets** (incl. a harness that swallows the budget exception), backend failure = missing trial (incl. swallowed); SimModel accuracy per family, step-by-step/verify bonuses, code path p≈0.93, tool-output echo, answer format, verbosity cost, determinism, skill scaling, fuzzy match; the domain property: a tool harness transfers to unseen families at lower token cost, a lookup table only inflates evolve | 67 |
| `tests/test_core_live.py` (`-m live`) | real `ClaudeCLI.complete`, `CachedLLM` over it, `AgentEditor` on a tiny two-file artifact with a locked file; writes `results/core-qa/live_smoke.json` | 3 |

Run: `pytest tests/test_core_*.py tests/test_agentqa*.py` gives **252 passed in about 9 s**
(offline, deterministic). `pytest tests/test_core_live.py -m live` runs the 3 live tests
(about 15 s, about $0.017).

## Bugs found and fixed (all backward compatible; no public signature or default changed)

| # | Where | Bug (how found) | Impact | Fix | Test |
|---|---|---|---|---|---|
| 1 | `artifact.diff` | Files without a trailing newline made difflib glue `-old` and `+new` (and the next file's `---` header) into one line | **Leak-critic bypass**: `LeakageCritic` accepted an edit adding `'SECRET_ANSWER'` as a newline-less last line. `diff_size` undercounted. RRSI/EvoMap/autoresearch diff parsers saw corrupted lines. The live agent produced exactly such a file | every diff line ends in `\n`; git's `\ No newline at end of file` marker | `test_diff_without_trailing_newline_*`, `test_denylist_catches_last_line_without_newline` |
| 2 | `artifact.diff_size`, `critic.added_lines` | content lines starting with `--`/`++` were mistaken for file headers | undercount; added `++x` lines never screened | count hunk bodies / hunk-aware parser (header-less diffs still screened) | `test_diff_size_counts_content_that_looks_like_headers`, `test_added_lines_hunk_aware_and_headerless` |
| 3 | `Artifact.to_dir`, `editors.apply_scope` | `=== FILE: ../x ===` or `/abs/path` was accepted and written **outside** the directory | an LLM proposal could overwrite arbitrary files (`AgentEditor`, `ImprovementResult.save`) | `is_safe_relpath`; `to_dir` raises before writing; scope guard always blocks such names | `test_to_dir_refuses_paths_outside_root`, `test_parse_proposal_blocks_paths_outside_the_artifact` |
| 4 | `CachedLLM` | shared temp file `<key>.tmp`: concurrent misses on one key raised `FileNotFoundError` (7 errors in a 1600-call probe) | spurious failed trials inside harnesses under parallel evaluation | unique temp names + atomic replace; corrupt entries are misses | `test_cached_llm_concurrent_writers_same_key`, `test_cached_llm_corrupt_entry_is_a_miss` |
| 5 | `CachedLLM` metering | cache hits were metered under `role:cached` **with their original calls and dollars**, contrary to the docstring | `meter.total()`/`usd_of`/`Budget.max_usd` and every role-summing report (GEPA, RRSI, autoresearch) counted replayed calls as real spend (EvoMap had worked around it) | `:cached` entries carry tokens only; original usage kept in `CachedLLM.saved`; `hits`/`misses` counters | `test_cached_llm_miss_then_hit` |
| 6 | `gates` | float summation order: mathematically tied scores differ in the last bit (39% of shuffled ties in a probe; `0.55-0.5 > 0.05`) | `StrictImprovement` accepted ties (the calibration experiment measured 10 accepted ties among 1,200 null pairs), `MinGain` accepted gains equal to delta, etc. | `TIE_EPS = 1e-9` tolerance in every threshold comparison | `test_float_ties_are_ties`, experiment C4 |
| 7 | `gates.Verdict` | numpy scores made `accept` a `numpy.bool_`; `bool(verdict)` raised `TypeError` (found by the experiment) | `if gate.check(...)` crashed for numpy inputs | coerce `accept` to `bool` | `test_numpy_scores_give_plain_bool_verdicts` |
| 8 | `Domain.run` | `SystemExit` from artifact code escaped (killed the loop / the process with `workers=1`); a grader returning a non-number crashed; an `execute` returning a bare value crashed | loop crash instead of graded failure | catch `SystemExit`, coerce inside the guard, wrap bare outputs | `test_run_system_exit_*`, `test_run_grader_exception_and_bad_scores`, `test_run_accepts_bare_output_from_execute` |
| 9 | `Trial.to_json` | non-JSON values in `meta` raised `TypeError` in the Evaluator's disk cache | whole evaluation crashed when `cache_dir` was set | recursive JSON-safe copy (reprs for leaves) | `test_trial_to_json_non_serializable_output_and_meta` |
| 10 | `Evaluator` | `seeds=[0, 0]` left an empty slot → score halved; disk cache writes not atomic; a torn/foreign cache file crashed every later run | wrong scores / permanent crash after an interrupted run | dedupe seeds (and task ids); atomic writes; unreadable entries re-run | `test_duplicate_seeds_*`, `test_disk_cache_corrupt_or_foreign_entries_rerun` |
| 11 | `Ledger` | a torn last line (crash mid-write) made the JSONL unreadable; re-adding an id reset `seq`; `lineage` raised on foreign parents and looped forever on cycles; unknown `update` fields were silently lost on resume; multi-line `change` broke `results.tsv` rows; `memory_gb=None` crashed the TSV | "safe to resume after a crash" was false | skip corrupt lines (`n_corrupt_lines`), start the next record on a fresh line; keep seq; guarded lineage; unknown fields → `meta`; one-line descriptions | `test_resume_after_torn_write`, `test_readd_*`, `test_lineage_tolerates_*`, `test_update_unknown_fields_*`, `test_results_tsv_*` |
| 12 | `sandbox.run_python` / `call_function` | every call leaked a scratch dir (**48,613 `/tmp/rsi_sbx_*` dirs** on this box); `call_function` interpolated `func` into code; a relative `script=` resolved against the scratch dir | disk/inode leak; code injection via `func` | remove scratch dirs; validate dotted identifiers; resolve relative scripts first | `test_run_python_cleans_its_scratch_dir`, `test_call_function_*` |
| 13 | `editors.AgentEditor` | `_proposal.json` holding a list/string crashed `edit`; `"components": "prompt"` became `['p','r',...]` | proposer crash / garbage component tags (RRSI novelty) | shared `_header_fields` | `test_agent_editor_tolerates_bad_proposal_files` |
| 14 | `critic.LeakageCritic` | integer terms crashed the constructor; `"objections": "text"` became a list of characters; `12` fired on `12.5` (lookbehind excluded `3.12` but lookahead allowed `12.5`); hit order depended on set iteration order | crash / noisy feedback / false rejections | stringify terms, deterministic order, symmetric decimal-aware token boundary | `test_denylist_whole_token_numbers`, `test_denylist_accepts_non_string_terms`, `test_llm_review_accept_and_reject` |
| 15 | `llm.extract_json` | a fenced scalar (e.g. a fenced number) shadowed a later JSON object | header/verdict objects lost | prefer objects/arrays; a scalar is returned only if nothing else parses | `test_extract_json_variants` |
| 16 | `llm.ClaudeCLI._run` | list-shaped (verbose) CLI output crashed with `AttributeError` | backend crash instead of retry | take the `result` message; non-dict output is retried | `test_claude_cli_list_output_and_errors` |
| 17 | `agentqa` grader | `ANSWER: **Monday**.` → `monday**`; `**ANSWER**: 42` label not recognised; `The answer is **Monday**`; unicode minus; `\boxed{}` | correct answers from real models graded wrong (SimModel never emits bold, so offline results are unchanged) | normalize unwraps bold/backticks/`\boxed`, strips repeatedly, maps U+2212; label regex allows bold | `test_extract_answer`, `test_normalize`, `test_is_correct` |
| 18 | `agentqa` execute | a harness catching the backend exception turned an infra failure into a cached wrong answer; `sys.exit()` in a harness escaped | missing trials miscounted and never retried; loop crash | remember infra errors per trial; catch `SystemExit` | `test_swallowed_backend_failure_is_still_missing`, `test_harness_failures_are_graded_zero` |
| 19 | `agentqa` `units` generator | `round((ft*12+in)*2.54)` used banker's rounding on exact halves (75 in → 190.5 → 190) | wrong targets (legacy family; not in the default suite) | exact integer half-up | `test_units_rounding_is_half_up`, per-family re-derivation |
| 20 | docs | `tasks.py` referred to a non-existent `SplitGuard`; `from_tasks` with zero fractions raised ZeroDivisionError | - | docstring; `ValueError` | `test_from_tasks_unnormalized_fractions_and_tiny_pools` |

**Found but deliberately not changed (it would alter everyone's numbers):** `make_suite(seed=0)`
repeats one evolve question in holdout (`sum of the decimal digits of 44!`), and seed 1 repeats a
question inside evolve (factorial digit sums have only 46 possible questions). An **opt-in**
`rsi.domains.agentqa.decontaminate(suite)` removes such repeats. Making it the default is a
request to the integrator.

Behaviour changes other engineers may notice: `role:cached` usage now has 0 calls/0 USD
(`CachedLLM.saved` keeps the originals); diffs of newline-less files carry the `\ No newline`
marker; gates treat differences below 1e-9 as ties; `Ledger.update(unknown=...)` stores into
`meta`; `Artifact.to_dir` raises on unsafe names; `run_python()` without `cwd` deletes its
scratch dir afterwards.

## Experiment: `experiments/core-qa/core_calibration.py`

`python experiments/core-qa/core_calibration.py [--llm sim|claude:haiku] [--seeds N] [--quick]`
writes `results/core-qa/core_calibration.{json,png}`. It uses the shared core end to end
(`Evaluator` → `noise_from_trials`/`noise_from_repeats` → `NoiseFloor`/`StrictImprovement`/`MinGain`)
on **two domains**: AgentQA (seed harness + SimModel) and a Bernoulli toy `FunctionDomain`.
Settings: 10 seeds × k ∈ {2,3,5} × 40 re-evaluations of an unchanged artifact (200 independent
null pairs per cell); 17 s CPU.

| Claim | Result (pooled 95% bootstrap CI) | Verdict |
|---|---|---|
| C1 (RRSI calibrate.py): an unchanged artifact clears `S* - delta` about 97.5% of the time, with delta from the within-task bootstrap of one k-trial evaluation | k=2: 0.895 [0.855, 0.935] AgentQA, 0.930 [0.895, 0.965] toy. k=3: 0.955 [0.925, 0.980] both. k=5: 0.965 [0.935-0.940, 0.990] | **Reproduced for k ≥ 3; NOT at k=2** |
| Diagnosis | resampling k trials underestimates per-task variance by (k-1)/k, so bootstrap delta / repeat delta = 0.706 / 0.813 / 0.889 (AgentQA) against sqrt((k-1)/k) = 0.707 / 0.816 / 0.894. Scaling delta by sqrt(k/(k-1)) restores 0.970-0.990 clearance | explained |
| C2: bootstrap delta agrees with the repeated-evaluation delta | agrees up to the factor above | partly (biased low for small k) |
| C3 (autoresearch-mlx): "strictly better" chases noise | strict keeps 47-51% of null re-evaluations (pre-fix rule up to 52%); `MinGain(delta)` keeps 3.5-5.5% at k ≥ 3 (7.5-12% at k=2 because delta is biased low) | reproduced (k ≥ 3) |
| C4 (bug #6): float ties | 17 of 76 exact ties (of 1,200 null pairs) differed in float; the pre-fix strict rule accepted 10 of them, the fixed rule 0 | fix confirmed |

Honest note: the core estimator is **faithful to RRSI's code**, which uses the uncorrected
bootstrap. At RRSI's default k=2 its floor is therefore looser than the paper states (about 92%
clearance, not 97.5%). A `small_k_correction` option is requested below.

## Live smoke (`tests/test_core_live.py`, run twice; about $0.017 per run)

Recorded in `results/core-qa/live_smoke.json`. `ClaudeCLI("haiku").complete` returned "pong"
(822 in / 49 out tokens, $0.00107). `CachedLLM` replay: 2 hits, 0 calls, $0. `AgentEditor` edited
`greeting.txt` to "hello world" in 11 s (18.8k in / 0.8k out tokens, $0.0158); the locked file was
untouched, `_proposal.json` was parsed, `_context/` was stripped. The agent wrote the file
without a trailing newline, and the diff now renders it correctly (bug #1).

## Applying the core to a new problem (checked by the tests above)

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

## Limitations and open requests

* `noise_from_trials` is biased low by sqrt((k-1)/k) (faithful to RRSI). Request: an opt-in
  `small_k_correction=False` argument.
* `make_suite` default still contains one evolve/holdout repeat (seed 0). Request: dedupe by
  default (changes every AgentQA number, so the integrator must decide).
* `run_cmd` uses `preexec_fn`, which Python documents as not thread-safe. The Evaluator's threads
  call it through `Tools.python`. No deadlock was seen, but `start_new_session=True` plus an exec
  trampoline for rlimits would be safer.
* The Evaluator's disk cache key has no LLM identity, so sharing a `cache_dir` between task models
  returns the wrong model's trials.
* `AgentEditor` needs a bare `ClaudeCLI`: agent runs cannot go through `CachedLLM`.
* `TaskSuite.split("typo")` silently returns `[]`, so a misspelt split evaluates to S=0.
* The 48,613 scratch dirs leaked before the fix are still in `/tmp` (other engineers' runs; not deleted).
