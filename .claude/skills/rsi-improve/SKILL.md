---
name: rsi-improve
description: Apply a recursive self-improvement loop (RRSI, GEPA, autoresearch, Meta-Harness, SoL-Pi, Dream-RSI, EvoMap from the `rsi` package in this repo) to a user's own problem - improving an agent harness, prompts, a training/solver script, a search strategy, or a shared library of strategies - with a locked grader, held-out evaluation and a per-iteration audit trail. Use when the user wants to "self-improve", "evolve", "optimize prompts/harness automatically", "run autoresearch", or apply any of these techniques to new tasks.
---

# Applying `rsi` to a new problem

The package lives at the repo root (`rsi/`). Every technique runs the same loop
(propose -> run -> score -> keep/discard) through one API:
`rsi.improve(domain, seed_artifact, method=..., llm_task=..., llm_propose=..., config=..., out_dir=...)`.

Follow these steps in order. Do not skip step 3: a loop without a measured noise
band and a sealed held-out split will happily "improve" by luck or memorization.

## 1. Pin down the problem with the user

Ask (or infer from the code) and write down:
- **Artifact** - what may change: prompt files, a harness (`harness.py` + prompts), a script, a policy module. Everything else is frozen.
- **Tasks** - at least ~20 decision tasks, plus tasks the loop must never see: `holdout` (same kind) and ideally `ood` (different kind).
- **Grader** - deterministic if at all possible (exact match, unit tests, simulator, metric). It must live outside the artifact.
- **Cost unit** - tokens, dollars, seconds; whether cost matters.

## 2. Build the Domain

```python
from rsi.core import Artifact, Domain, Execution, Task, TaskSuite

class MyDomain(Domain):
    name = "my-problem"
    components = {"prompt": ["prompts/*"], "control_flow": ["harness.py"], "tool": ["tools/*"]}   # optional (RRSI)
    structural_components = ("tool",)

    def execute(self, artifact, task, *, seed, llm):
        ...  # run the artifact on task.input with the frozen llm; return Execution(output=..., trace=..., tokens=..., steps=...)
    def grade(self, task, execution):
        ok = execution.output == task.target
        return float(ok), ("correct" if ok else f"expected {task.target!r}, got {execution.output!r}")  # feedback text helps GEPA/RRSI
    def describe(self):
        return "What the artifact is, how it is graded, and that improvements must generalize."

tasks = [Task(f"t{i}", inp, tgt, family=fam) for i, (inp, tgt, fam) in enumerate(rows)]
suite = TaskSuite.from_tasks(tasks, fractions={"evolve": 0.5, "holdout": 0.5}, by_family={"ood": ["other-kind"]})
dom = MyDomain(suite)
seed = Artifact.from_dir("my_harness/")
```
Or wrap two functions: `rsi.core.FunctionDomain(suite, execute_fn, grade_fn)`. `holdout`/`ood`/`test` are
sealed automatically: the loop's evaluator refuses them.

## 3. Measure before improving

```python
from rsi.core import Evaluator, get_llm, noise_from_trials
llm = get_llm("claude:haiku", cache_dir=".rsi_cache/myproblem")      # or "api:sonnet", or a MockLLM offline
ev = Evaluator(dom, llm, workers=8)
base = ev.evaluate(seed, "evolve", k=2)
print(base.summary(), noise_from_trials(base.trial_matrix()))        # delta = the smallest gain worth believing
```
If the seed already scores ~1.0, or delta is larger than any plausible gain, stop and fix the task set.

## 4. Choose a method

`rsi.recommend(what_changes=..., must_transfer=..., cost_is_bottleneck=..., prompts_only=..., single_metric_script=...)`
or `rsi methods`. Rules of thumb (from the overview):
- prompts only, few rollouts -> **gepa**; harness edits that must transfer -> **rrsi** (safe default);
- one script + one metric + fixed budget -> **autoresearch**; tokens are the bottleneck -> **solpi**;
- long many-attempt searches -> **dream**; many agents sharing lessons -> **evomap** (SafeHub only).

## 5. Run with a trace

```python
import rsi
res = rsi.improve(dom, seed, method="rrsi", llm_task=llm, llm_propose="claude:haiku",
                  config={"T": 8}, out_dir="runs/my-rrsi")
res.save()
report = rsi.transfer_report(dom, llm, {"seed": res.baseline, "rrsi": res.best}, k=2)   # the ONLY place sealed splits are read
```
CLI equivalent: `python -m rsi.cli improve --method rrsi --problem my_problem.py:make --llm claude:haiku --out runs/x --set T=8`
(`make()` returns `(domain, seed_artifact)`).

## 6. Audit every step

`python -m rsi.cli inspect runs/x` renders `trace.jsonl` into `TRACE.md`: for each round the analysis,
the proposer prompt/reply, claimed change vs actual diff, critic verdict, per-task scores, gate arithmetic,
decision, state, and shadow held-out scores (never shown to the loop). Check that:
- each kept change is generic (no task ids, entities, literal answers - the leakage critic should have caught these);
- the gate arithmetic is right (recompute from the per-task scores);
- held-out/ood move with the evolve score - if evolve rises while held-out stalls, the loop is memorizing.

## 7. Report honestly

Give baseline vs final on evolve, holdout and ood with the paired CI from `transfer_report`, tokens per task,
dollars, and what the kept edits actually were. Say so when a gain is within the noise band.

References: `docs/RSI-101.md` (concepts), `docs/methods/<method>.md` (paper specs),
`docs/methods/<method>-impl.md` (APIs + reproduced claims), `experiments/<method>/example_new_problem.py`.
