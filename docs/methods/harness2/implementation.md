# Harness²: implementation notes (`rsi.harness2`)

This page explains how the Harness² spec (`docs/methods/harness2/paper-spec.md`, cited below as [spec §n]) is built on
the shared `rsi.core` API. It covers:

- the module map;
- the public API, with an example;
- the choices made where the spec is silent;
- deviations from the paper and from `google-research/harness2`;
- the offline experiment;
- limitations, including what a live run still needs.

Everything here is offline: the tests and the experiment use the AgentQA simulated model (`SimModel`) as the
executor and the deterministic `rsi.harness2.MockImprover` as editor, composer, selector and reflector.

## 1. Module map

| module | role (spec / reference symbol) |
|---|---|
| `rsi/harness2/config.py` | `Config`: every knob, defaulting to the reference CLI (`--mode parallel --k 1`). Validates the prompt-pack slots: no slot may mention benchmark/evaluation/grader/rubric/score [spec §5, code `spec.BenchPrompts`]. |
| `rsi/harness2/api.py` | `run(...) -> ImprovementResult`: the task stream, the open-world guard, the per-domain library update after each task, the spend cap, `posthoc_report` (grading only after the loop). Also `TaskStream`, `stream_order` [code `schedule.stream_order`], `eval_split`, `domain_of`, `editable_surface`. |
| `rsi/harness2/loop.py` | `TaskRunner`: one open task. `bases` (k references), `run_parallel` [code `recursion.run_parallel`], `run_sequential` [code `recursion.run_sequential`], `solve` (selection), `reflect`. `TaskBudget` enforces R = 4k rollouts and P = 3k sessions [code `ledger.assert_identity`]. |
| `rsi/harness2/roles.py` | Improver sessions: `Session.ask` (the single choke point: every prompt is screened, transport retried twice), `run_stage` (retry once with the validator message, then inherit [code `recursion._run_stage`]), `parse_edit` (`<notes>` + `ADOPT:`/`REPRODUCE:` + `=== FILE:` blocks), `select` (anonymised, deliverable-only, trivial at k = 1 [code `selection.py`]), `reflect` + `parse_experience` [code `selection._distil`], `anon_ids` [code `selection.assign_ids`]. |
| `rsi/harness2/prompts.py` | The adapt-pack prompts of [spec §6]: INV/DOM dimension blocks, GENERALIZE, HOW TO DECIDE, ANSWER_FREE, EVIDENCE_BOUNDARY, the surface block, `aggregate`, `compose_round`, `select`, `experience`, the mission pointer. `VERSION = "harness2-1+rsi-text"`. |
| `rsi/harness2/context.py` | Evidence as files [spec §2]: `build` lays out `context/{task.md, source/, rollouts/, deliverables/, harness/, pool/<label>/{harness/, harness.diff, rollout.md, produced.md}, decisions/, library/}`; `selector_context` (`task.md`, `source/`, `candidates/<id>.md` only); `reflector_context` (adds `base_harness/` and `adoption.json`, no library). `render`/`parse` turn the tree into `=== context/<path> ===` text blocks. |
| `rsi/harness2/tree.py` | Harness trees (`Artifact` = `dict[path, text]`): `validate` before any rollout, `off_surface`, `diff_summary`, `census`, `diversity` (pairwise line-Jaccard of the finals). |
| `rsi/harness2/guard.py` | `OpenWorld` (the domain's `grade`/`run`/`grade_execution`/`score` raise `GraderAccessError` inside the loop), grader-verdict markers (the spec's seven + rsi's graded-record keys + `domain.grader_verdict_markers()`), `GraderLeak`, `open_task` (no target, no answer-like meta keys). Both errors derive from `BaseException`, so no `except Exception` swallows them. |
| `rsi/harness2/library.py` | `Library`: `<root>/<mode>/<domain>/INDEX.md` + `harnesses/<task>/{harness/, EXPERIENCE.md}` [code `library.py`]; `add` stores only a valid experience and screens everything first; `context(domain)` is empty until the domain has an entry. |
| `rsi/harness2/records.py` | `Rollout` (never carries a score), `Stage`, `TaskRecord` (`counts()`, `submitted`, `adopted_tree`, `kept_base()`). |
| `rsi/harness2/baselines.py` | `best_of_n`: H0 rolled n times + the same selector [paper App. C "Best-of-k"]. |
| `rsi/harness2/mocks.py` | `MockImprover` routes by the prompt's first line to `MockEditor` (one evidence-backed lever per candidate; DOM also copies library lines; `invalid=` emits template braces to exercise retry/fallback), `MockComposer` (merges the added lines of delivering candidates; `ADOPT` when one equals the merge), `MockSelector` (DELIVERY/FINISH: a labelled final line, then the lowest id), `MockReflector` (edit rows with verdicts from deliverable contrasts; `NO_ENTRY` unless an edit helped). |
| hooks outside the package | `Domain.rollout` (execute without grading) and `Domain.grade_execution` in `rsi/core/domain.py` (`Domain.run` = both). AgentQA: `harness_brief()` (the harness interface without the grading sentence), `validate_artifact()` (harness.py parses and defines `solve`), `grader_verdict_markers()` (the grader's exact feedback phrases). |

Registration: `rsi.api.METHODS["harness2"]` (aliases `harness-squared`, `harness²`), `recommend(no_grader=True)`
(CLI `rsi recommend --no-grader`) returns only Harness², and `rsi improve --method harness2 --llm sim` runs the
offline mock improver and prints the post-hoc stream accuracy instead of a transfer report.

**Tests**: `python -m pytest -q tests/harness2` (44 tests, offline, deterministic, about 4 s):
`test_harness2_stream.py` (stream protocol, open tasks, grader only after the loop, no future tasks, leak
tripwires, stream order), `test_harness2_task.py` (Propose-Probe-Compose per task in both modes and k = 1..3,
evidence visibility, KEEP_BASE, retry-then-inherit, transport retries, validation rules, selector anonymisation and
fallback, base-failed tasks), `test_harness2_library.py` (update after each task, per-domain visibility, entry
content, reuse, NO_ENTRY, the w/o-library ablation, empty start and per-mode separation), `test_harness2_api.py`
(budget identity, global budget and spend cap, the trace, determinism, config validation, `rsi.improve` on
AgentQA, CLI, Best-of-n, the experiment's quick mode and the stored result file).

## 2. Public API

```python
import rsi
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.harness2 import Config, MockImprover

suite = make_suite()
dom = AgentQADomain(suite)
res = rsi.improve(dom, method="harness2", llm_task=SimModel(suite), llm_propose=MockImprover(),
                  config=Config(k=1, mode="parallel", split="holdout+ood", order="shuffle", seed=0),
                  out_dir="runs/h2")
res.meta["deliverables"]          # {task_id: the submitted output}: what the method produces
res.meta["report"]                # evaluation only: base vs submitted accuracy, paired bootstrap CI, per family
res.meta["records"]               # live TaskRecord objects (stages, rollouts, trees, selection, reflection)
res.meta["library_dir"]           # runs/h2/library/agg/<domain>/INDEX.md, harnesses/<task>/...
```

`run(domain, seed_artifact, *, llm_task, llm_propose=None, config=None, out_dir=None, llm_select=None,
budget=None, tasks=None)`:

- `llm_task`: the frozen executor (required). `llm_propose`: editors and composers; default `llm_task`, as in the
  reference code (`HARNESS2_IMPROVER_MODEL` defaults to the solver model). `llm_select`: selector and reflector
  (the reference's selector channel); default `llm_propose`.
- `tasks`: the stream in order (overrides `config.split`/`order`/`max_tasks`).
- `budget`: an `rsi.core.Budget` (`max_rounds` = tasks, `max_rollouts`, `max_usd`, `max_wall_s`, STOP file), checked
  between tasks only.
- `res.best` is H0 (`res.best == res.baseline`): every task starts from H0, the adopted harnesses live in the
  records and the library. `rsi.transfer_report(res.best)` is therefore meaningless for this method; evaluate the
  deliverables with `posthoc_report` (done by `run` when `config.report`).

Out-dir layout: `trace.jsonl` (+ `TRACE.md` via `rsi inspect`), `ledger.jsonl` (one node per stage tree, no
scores), `tasks/<task>.json` (full `TaskRecord`), `library/<agg|seq>/<domain>/...`, `summary.json`,
`trajectory.json`, `best_artifact/` (= H0).

`Config` defaults: `mode="parallel"`, `k=1`, `split=None` (the sealed evaluation split: holdout, else test, else
ood; `"a+b"` joins splits), `max_tasks=None`, `max_usd=None`, `order="sorted"` (grouped by domain; `"shuffle"` uses
the reference's seeded shuffles; `"split"` keeps the listed order), `retry_invalid=1` (STAGE_ATTEMPTS = 2),
`selector_attempts=2`, `seed=0`, `library=True`, `library_dir=None` (`<out_dir>/library`, emptied at the start),
`editable=None` (the seed's files plus the domain's `components` globs), `strip_targets=True`, `obs_trunc=50000`,
`max_text_chars=24000`, `prompts={}` (slots: `work`, `domain_word`, `deliverable_word`, `inv_levers`, `dom_levers`,
`yardstick`, `domain_guidance`, `selection_checks`, `selection_lead`; `work` defaults to `domain.harness_brief()`),
`report=True`, `trace=True`.

**Trace events** (`trace.jsonl`, write-only): `run_start`, `noise` (none: there is no score), per task
`round_start` (task, domain, library entries visible), `rollout` (label, tree, seed, ok, delivered, steps, tokens,
output head), `proposal` (one per editor/composer stage: prompt, reply, notes, actual diff, components, kept_base,
fell_back, attempts, adopted_from), `selection` (k > 1: prompt, reply, anonymous ids, choice), `decision` (adopted
label and tree, why), `reflection` (prompt, reply, experience or NO_ENTRY), `library` (added or not, and why),
`state`; then `run_end` and, after the loop, `report` (the only event with scores). A test checks that no score,
`S`, `per_task`, `submitted` or `target` key appears before `report`.

## 3. Choices where the spec is silent

| point | spec | rsi choice |
|---|---|---|
| improver sessions | agentic file-tool sessions in a scratch workspace [spec §2] | one text completion per attempt; the `context/` tree is rendered as `=== context/<path> ===` blocks and the reply carries `=== FILE: <path> ===` blocks (or `ADOPT: <label>` / `REPRODUCE: <label>`, or nothing = KEEP_BASE). The mission and surface text say so ("you have no file tools"). |
| editable surface | 8 substrate components (OpenCode/Codex files) with a path whitelist [spec §1] | any `rsi` harness: the seed's files plus `domain.components` globs; code files (`*.py`) are control flow/tools/checks, other files are text the code loads. The surface block maps the levers (skill -> on-demand prompt file, script -> deterministic helper, subagent -> separate model call, plugin -> check in the control flow). |
| validation | skills/subagents/scripts/hooks/Jinja rules [spec §5] | generic: every `*.py` compiles; no `{{`, `{%`, `{#` in text; keep every `{placeholder}` the base text has and add none; every new file must be named in some code file (the "trigger line" analogue, rejected, not auto-repaired); non-code text <= `max_text_chars`; plus `domain.validate_artifact` (AgentQA: `solve` exists). |
| library key | `adapter.domain_of(task)` [spec §4.1] | `domain.domain_of(task)` if defined, else `task.family`. |
| stream | sorted per domain, or seeded shuffles [spec §4.1] | same; domains run one after another (the reference may run them concurrently; libraries are per domain, so the information flow is identical). |
| task text and source | benchmark adapter [spec §9] | `domain.task_prompt(task)` / `domain.task_source(task)` if defined, else `task.input` and no source. |
| rollout seeds | idempotent `run_id` per label [spec §2.2] | `sha256(seed, task, label) mod 10^6`, so `base1` of Harness² and of Best-of-n are the same draw (paired comparisons). |
| global budget | not specified | `Budget` and `config.max_usd` are checked between tasks: a started task always completes its 4k/3k. `max_usd` also stops when spent + mean cost per task so far would cross the cap (`max_usd_projected`). Stopped tasks are listed in `res.meta["unprocessed"]` and have no deliverable. |
| base-failed task | stops, no deliverable [spec §2.1] | same; `posthoc_report` scores it at `domain.failure_score`. |
| selector fallback | lexicographically first final label [spec §3.3] | the first *delivering* final label (a non-delivering final cannot be submitted usefully). |
| `res.best` | n/a | H0 (see §2). |
| evaluation | judge after selection [spec §5] | `posthoc_report`: grades `base1` (one H0 draw) and the submitted rollout of every processed task after the guard is lifted. |

## 4. Deviations from the paper and the reference code

1. **Single-shot text sessions, not agentic sessions** (above). The evidence is complete (nothing is truncated to
   fit, except `obs_trunc` per rendered file, with an explicit renderer marker); a live improver must read a long
   prompt instead of opening files. The reference clips per *observation* (50,000, minimum 10,000); we clip per
   rendered trajectory/deliverable at `obs_trunc`.
2. **Surface and validator** are generic (§3), not the OpenCode/Codex surfaces; no `autotrigger` repair.
3. **Serial execution**: the reference runs INV/DOM sessions and their rollouts two at a time (`PHASE_WIDTH = 2`);
   here everything is sequential. This changes latency, not results.
4. **No session cache inside the method**: caching is the LLM's (`rsi.improve(..., cache_dir=...)` wraps models in
   `CachedLLM`, keyed by prompt and seed), not a content hash of the context directory.
5. **Transport retries** have no back-off sleep (the reference waits 10 s x (n-1)).
6. **Rollout caps** (200 turns, 3,600 s [paper App. D.1]) are the domain's: AgentQA caps 12 model calls, 8 tool
   calls and 60 s of the harness's own time.
7. **Guard**: in addition to the spec's markers and answer-key globs, the loop refuses the domain's grading methods
   at run time and strips targets and answer-like meta keys (`answer|target|expect|solution|gold|truth|label|rubric|
   grad|score|verdict|reference`) from the task. There are no `leak_globs` (rsi tasks are not directories).
8. **Selector criteria** are the adapt pack's DELIVERY .. FINISH; the `tts` pack (JobBench) is not ported.
9. **Ablations**: only "w/o library" (`library=False`). "w/o scoping" and "w/o composition" are not in the reference
   core either [spec §10.6] and are not implemented.
10. **Best-of-k** baseline: implemented as `best_of_n` with the Harness² selector, which the paper describes as
    "base harness k times + the same selector" [paper App. C]; ReasoningBank, Harness Scaling and Meta-Harness
    baselines are not ported (`rsi.metaharness` is a separate method in this package).

## 5. Offline experiment (`experiments/harness2/compare_sim.py`)

Setting: AgentQA `make_suite(seed=0)`, stream = holdout + ood (43 tasks: 19 numeric, 6 each of dates, lists,
numbertheory, strings; 5 library domains), 3 seeds (each a shuffled stream order per [spec §4.1] and its own
rollout seeds), executor `SimModel`, improver `MockImprover`, parallel mode. Accuracy is graded against the
evaluation-only labels after each loop. CIs: 95% percentile bootstrap over the 43 tasks (per-task accuracy averaged
over seeds first), paired by task. Result: `results/harness2/compare_sim.json` (gitignored on main; force-added on
`wip/harness2`). Runtime about 9 s.

| arm | rollouts / task | accuracy [95% CI] | vs seed, paired [95% CI] |
|---|---|---|---|
| seed (H0, the `base1` draw) | 1 | 0.488 [0.372, 0.605] | |
| Harness² k = 1 | 4 | 0.597 [0.488, 0.698] | +0.109 [+0.008, +0.209] |
| Harness² k = 3 | 12 | 0.643 [0.535, 0.752] | +0.155 [+0.054, +0.256] |
| Harness² k = 1, w/o library | 4 | 0.597 [0.488, 0.698] | +0.109 [+0.008, +0.209] |
| Best-of-4, same selector | 4 | 0.481 [0.357, 0.605] | -0.008 [-0.070, +0.054] |
| Best-of-12, same selector | 12 | 0.403 [0.287, 0.527] | -0.085 [-0.202, +0.023] |
| GEPA, matched agent calls, labelled evolve split | 3.9 | 0.457 [0.357, 0.558] | -0.031 [-0.132, +0.070] |

Paired: Harness² k = 1 - Best-of-4 = +0.116 [+0.016, +0.217]; k = 3 - Best-of-12 = +0.240 [+0.132, +0.349];
k = 3 - k = 1 = +0.047 [-0.039, +0.140]; k = 1 - w/o library = 0.000 [0, 0]; k = 1 - GEPA = +0.140 [+0.039, +0.240].

How to read it:
- **What is simulated.** `SimModel` answers better when the prompt asks to work step by step (+0.12) or to verify
  (+0.05), and adds an `ANSWER:` line when asked; `MockImprover` finds exactly those edits when the trajectories
  show their absence. The gains measure that the loop wires evidence -> edit -> probe -> compose -> submission
  correctly, with no label in the loop; they are not evidence about live models.
- **Best-of-n does not help** because the selector has no correctness signal here: the simulated deliverables
  differ only in the value, and the mock selector's FINISH rule (a labelled final line, then the lowest id) is close
  to a random pick among the base draws. Best-of-12 vs seed is within noise (CI includes 0).
- **k = 3 vs k = 1** is +0.047 with a CI that includes 0: the extra width finds the "verify" lever (inv2) that k = 1
  never tries, and the selector's choice among the 3 composed re-runs adds no signal.
- **The library has no measurable effect offline**: the mock DOM editor copies library lines, but the editors find
  the same two edits on every task anyway, so H* is the same with or without the library. Library update and reuse
  are verified mechanically in `tests/harness2/test_harness2_library.py`, not by this experiment.
- **GEPA** is matched on agent calls (4N - N training rollouts on the labelled numeric evolve split, then one
  rollout per stream task) but uses labels and a different split, so it is a different information regime. Its
  offline proposer adds only the `ANSWER:` line within that budget, which the simulator does not reward.

## 6. Limitations

- **Small live evidence.** One live comparison (`experiments/harness2/compare_live.py` + `merge_live.py`, Claude Haiku
  as agent, improver and selector, 20 AgentQA holdout+ood tasks, 26 Oct 2026; results and LLM cache on the data branch
  under `results/harness2/`): seed 0.50, k = 1 0.70 (+0.20 [0.00, +0.45]), k = 3 0.95 (+0.45 [+0.25, +0.65]), k = 3 − k = 1
  +0.25 [+0.10, +0.45]; $13.83 in all. One domain and 20 tasks; best-of-n was not run live. Offline, the mock improver
  knows the simulator's levers; a real selector may prefer weaker deliverables (the paper's own failure analysis
  [spec §7.4]).
- **Long prompts.** Because sessions are single completions, every editor/composer prompt holds the whole evidence
  (k base trajectories, 2k candidates with diffs, the domain library). With a real model, prompt size grows with k
  and with the library (no retrieval, no consolidation, as in the reference [spec §10.13]).
- **Cost**: per task 4k rollouts + 3k sessions + (k > 1) one selector + one reflector, i.e. 4 executor runs and 4
  improver calls at k = 1, 12 and 11 at k = 3, with the improver calls carrying the long prompts above.
- **Markers are strings.** The leak tripwire raises on any grader phrase in evidence; a model that prints
  `"score": ` in JSON, or one of AgentQA's grader phrases, stops the run with `GraderLeak` (by design: "a leak, not a
  false positive to widen around").
- **No resume**: a killed run restarts the stream (a `CachedLLM` makes the replay cheap).

### Before a live run (k = 1 and k = 3 with Claude Haiku, $25 cap)

- Add `experiments/harness2/live_smoke.py`: `rsi.improve(..., method="harness2", llm_task="claude:haiku",
  llm_propose="claude:haiku", cache_dir=".rsi_cache/harness2", config={"k": k, "max_usd": ...})` on a small
  multi-family stream, writing `results/harness2/live_*.json`, plus a spend-attribution check like the other
  methods' live smokes.
- Check the text-session format with a real model on one task first: that replies use `=== FILE: ... ===` blocks
  with complete file contents, that `ADOPT:` lines are recognised, that the selector ends with a ```` ```choice ````
  block and the reflector with `<experience>`/`NO_ENTRY`; count fallen-back stages in the trace.
- Estimate cost from that task: improver prompts are the large term. Use `max_usd` so that k = 1 and k = 3 together
  stay under $25 (`max_usd_projected` stops before the next task would cross it); check `res.usage` per role.
- Decide the stream size from the estimate, and run the seed (one H0 rollout per task) on the same stream for the
  paired comparison.
