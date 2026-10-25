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

Check the method's requirements before running it:

| Method | Looks at each iteration | Needs | Watch out for |
|---|---|---|---|
| rrsi | m candidates per round (paper: 2), each scored on the whole evolve split with k trials per task; an analyst condenses the incumbent's failures | enough evolve tasks for a stable score; a noise band delta measured on repeated base evaluations (set `delta`, or `calibration_repeats` >= 2) | most expensive per round; may keep nothing. Keep rule: S' >= S* - delta AND (dS > delta ? dC <= beta0 + beta1*dS : w_s*dS - w_c*dC + w_n*nu > 0) AND domain guards; winner = max S' among admissible, else keep H_t |
| metaharness | every candidate on the whole evolve split (`trials` seeds, default 1); the proposer reads all past code, scores and raw traces | a strong coding-agent proposer (pass `proposer_kind="agent"` when `cache_dir` is set), rich `Execution.trace` text, disk for every candidate | no keep rule: every valid candidate is kept and the best is the top-score Pareto point on evolve from one seed; nothing blocks hard-coded answers unless `leakage_screen=True`: check it on holdout/ood |
| gepa | the parent and one rewritten child (one module, round-robin) on the same minibatch of `minibatch_size` (default 3) evolve tasks; only a child whose minibatch score strictly improves is scored on all of `val` (D_pareto) and joins the pool | named text components, a separate non-empty `val` split, grader feedback that says *why* | edits text only; the keep rule is a strict `>` on 3 single draws with no noise band (`acceptance="noise_margin"` adds one); every accepted child stays in the pool; `res.best` is the argmax of `val` means, so judge it on sealed splits |
| autoresearch | upstream: the git state, the grep of the last run's log and its own results.tsv; here: program.md, the tip files, the whole results.tsv and the kept-commit log (never per-sample traces or hidden splits) | editable file(s) (upstream: only train.py), one metric computed by locked code, a fixed budget per run, one run per experiment | the default keep rule (`upstream`: strictly better than the tip on ONE run, plus rsi's mechanical simplicity/VRAM thresholds) keeps lucky changes: measure noise (`noise_runs`) and prefer `keep_rule="rigor"` or a gate |
| solpi | each candidate on the whole evolve split x k (aggregate gate; per family with `gate=GateSpec(mode="per_family")`); the proposer sees only its own lineage's rollouts (2 tasks/family) and history; each frozen candidate runs once on holdout | several task families with executable verifiers, token/cost metering, a sealed holdout split (else the firewall is silently off), ideally `Trial.meta["oracle"]` | optimises tokens/cost under a capability floor, not score (paper: full stack kept 93.7-94.3% of Pi's score); default k=1 has no noise band; mechanisms gated one at a time can lose quality once composed |
| dream | one live search per round with the deployed policy (up to `branch_count x (refine_count+1)` agent + grader calls on the decision split), then M policy versions (incumbent + M-1 developer rewrites) replayed over every recorded search tree with no agent or grader calls; deploys the argmax of mean replay score (incumbent kept on ties) | a many-attempt search problem with a larger-is-better score, an agent (`llm_propose`) and optionally `llm_develop` (defaults to the agent, as in the paper) | the keep rule picks the search policy, not the program: the best program is replaced on any strict gain with no noise band, so confirm it with `transfer_report`; the default `objective="eq1"` is the paper's v1 formula (v2's Eq. 1 is the rho-swept Pareto form, approximated by `objective="pareto"`); replay cannot show new discoveries |
| evomap | one agent's own recent outcomes; genes from the hub | `rsi.improve` runs ONE agent: sharing needs the hub/population code; SafeHub needs hidden tasks | the naive hub trusts self-reported gains |

A keep rule that accepts any gain accepts an unchanged artifact about half the time under model noise
(measured here: ~46% strict vs ~9% with the noise band), so always compare gains with delta from step 3.
GEPA's keep rule is the paper's [Alg. 1 l.15-16]: keep the child iff its average score on the parent's minibatch is higher
(`sum(after) > sum(before)`, ties reject). Pass `acceptance="noise_margin", noise_margin=delta` to apply the noise band.
Dream-RSI applies no noise band at either level: policies are chosen by argmax over the same replay worlds the
developer read (use `selector="guarded"` once there are >= 3 worlds), and `res.best` is replaced on any strict gain, so
re-score it on sealed splits before reporting a gain.
SoL-Pi's keep rule is a dual gate [paper §2.1]: every predeclared capability metric stays within its tolerance of
the base AND at least one efficiency metric improves; among passers the nondominated are kept. rsi's thresholds (score
and solved rate within 2% relative, tokens or cost more than 2% lower) are its own: the paper publishes no values.

## 5. Run with a trace

```python
import rsi
res = rsi.improve(dom, seed, method="rrsi", llm_task=llm, llm_propose="claude:haiku",
                  config={"T": 8}, out_dir="runs/my-rrsi")
res.save()
report = rsi.transfer_report(dom, llm, {"seed": res.baseline, "rrsi": res.best}, k=2)   # the ONLY place sealed splits are read
```
Before a live run:
- **Cap the spend yourself.** Method configs have no dollar cap: check `llm.meter.total().cost_usd` between
  rounds or runs and stop at your limit.
- **Always pass `cache_dir`** (or wrap the model in `CachedLLM`): an interrupted run resumes from the cache,
  and a finished run can be replayed offline at $0 to check it.
- Live models need the `claude` CLI or an API key; usage limits can stop a long run part-way, so prefer
  several small runs that each save their results over one long run.
- In an ephemeral environment (e.g. a cloud container), push work and results as you go; anything only on
  local disk can disappear when the environment is reclaimed.

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
dollars, and what the kept edits actually were. Say so when a gain is within the noise band. With 6-8
tasks per split the interval is usually too wide to rank methods: say that too, and do not compare
absolute scores across runs (the same seed harness can score differently run to run); compare gains
over a baseline re-scored in the same run.

## Method flows

Before running a method, read its end-to-end reference: what one round does, what happens to each task inside
a round, the keep rule with its formula (Meta-Harness has none; its reference gives the Pareto-dominance rule instead), paper defaults vs this package's Config, and where the implementation
departs from the paper. Each cites its paper directly (RRSI: arXiv 2609.24972v3; see each file's header for the version used).

- RRSI: [methods/rrsi.md](methods/rrsi.md)
- Meta-Harness: [methods/metaharness.md](methods/metaharness.md)
- SoL-Pi: [methods/solpi.md](methods/solpi.md)
- GEPA: [methods/gepa.md](methods/gepa.md)
- autoresearch: [methods/autoresearch.md](methods/autoresearch.md)
- Dream-RSI: [methods/dream.md](methods/dream.md)
- EvoMap: [methods/evomap.md](methods/evomap.md)

References: `docs/guide/RSI-101.md` (concepts), `docs/methods/<slug>/README.md` (per-method hub: paper spec,
implementation notes, claims audit), `experiments/<method>/example_new_problem.py`.
