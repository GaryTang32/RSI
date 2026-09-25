# Architecture

`rsi` is one Python package in which seven published self-improvement techniques are
implemented on a single shared core. All of them run the same loop:

```
propose a change  ->  run the changed system  ->  score it with a locked grader  ->  keep or discard
```

They differ in **what** changes, **who** checks, and **how far** an improvement spreads.
That difference maps onto code as follows:

| Technique | What changes (`Artifact`) | Proposer | Keep rule (`Gate`) | History role |
|---|---|---|---|---|
| autoresearch | `train.py` of a separate model | coding agent + human `program.md` | `StrictImprovement` (+ `BootstrapRigor` port) | results.tsv log |
| RRSI | the agent harness (any file) | LLM proposer with edit budget, failure memory, forced variety | `RRSIGate` (noise floor, cost rule, within-band rule, guards) + leakage critic + pruning | falsified-idea ledger |
| Dream-RSI | the exploration policy (code) | LLM policy developer ("dreaming") | argmax replay score over past searches (incl. incumbent) | executable replay simulator |
| EvoMap | a shared library of genes/capsules | agents distil what worked | local validation + hub ranking (naive vs safe) | shared, inheritable library |
| GEPA | named prompt components | reflective LLM on full traces | minibatch strict improvement + Pareto frontier + merge | lineage tree |
| Meta-Harness | harness code end-to-end | coding agent reading the full history filesystem | none (all evaluated; Pareto frontier) | full, uncompressed filesystem |
| SoL-Pi | harness mechanisms for efficiency | autoresearch-style lineages | `DualGate` across environment families + holdout firewall | per-lineage logs |

## Package layout

```
rsi/
  core/              shared machinery (owned by the integrator; methods import, never edit)
    llm.py           LLM, MockLLM, ClaudeCLI (headless `claude -p`), AnthropicAPI, CachedLLM, get_llm, extract_*
    artifact.py      Artifact (immutable {path: text}, content-addressed, diff, to_dir/from_dir)
    tasks.py         Task, TaskSuite with split discipline (evolve/val decide; holdout/ood/test sealed)
    domain.py        Domain (execute + locked grade), Execution, Trial, FunctionDomain
    evaluate.py      Evaluator (parallel, cached, k trials, missing=0), EvalResult (S, C, per-task, families)
    stats.py         noise band delta (repeat / within-task bootstrap), CIs, paired diffs, Beta LCB, Spearman, Gini
    gates.py         Scored, GateContext, Verdict; StrictImprovement, MinGain, NoiseFloor, CostRule, MetricGuard,
                     RRSIGate, BootstrapRigor, DualGate, AllOf; select() = argmax over admissible
    ledger.py        Node, Ledger (append-only JSONL tree), ArtifactStore (content-addressed)
    editors.py       Editor, RewriteEditor (one completion -> file blocks), AgentEditor (claude -p coding agent in a
                     scratch dir), Proposal, scope guard for editable paths
    critic.py        LeakageCritic (denylist over added diff lines + optional LLM review; fails closed)
    sandbox.py       run_cmd / run_python / call_function with timeouts and rlimits
    run.py           Budget (rounds/rollouts/usd/wall/STOP file), ImprovementResult, transfer_report()
  domains/
    agentqa/         shared harness domain: LLM-agent harness (harness.py + prompts) on generated questions with
                     exact graders. practice family `numeric`; unseen families dates/numbertheory/strings/lists.
                     SimModel = offline simulated frozen model that rewards generic harness skills.
    <method-specific domains live beside it, owned by the method that needs them>
  autoresearch/  rrsi/  dream/  evomap/  gepa/  metaharness/  solpi/     one subpackage per technique
  api.py, cli.py     unified entry points (integration phase)
experiments/<method>/  runnable scripts reproducing each claim; write results/<method>/*.json + figures
tests/                 pytest; offline by default (MockLLM / SimModel); `-m live` for real-LLM smoke tests
docs/methods/*.md      verified method specs (the "101" + formulas + reproduction blueprint for each technique)
```

## Contracts every method follows

1. **Entry point.** `rsi/<method>/__init__.py` exports a `Config` dataclass and a `run(...)` function (or a
   class with `.run()`), returning `rsi.core.ImprovementResult`. Signature pattern:
   `run(domain, seed_artifact, *, llm_task, llm_propose, config, out_dir) -> ImprovementResult`.
   Method-specific extra inputs are keyword arguments.
2. **Frozen things stay frozen.** Grading happens only inside `Domain.grade`. Proposers never see sealed splits;
   loops evaluate on `evolve` (and `val` where the method defines one). Final numbers on `holdout`/`ood` come from
   `transfer_report` or an equivalent report step that unseals explicitly.
3. **Everything is logged as a tree.** Use `Ledger` nodes (parent, change, score, cost, status) so any run can be
   inspected, resumed, and replayed (Dream-RSI can consume any method's ledger as a replay world).
4. **Offline first.** Every method runs end-to-end with `MockLLM`/`SimModel` in seconds (tests + CI), and with
   `ClaudeCLI("haiku")` for live showcases. No hidden network use.
5. **Honest measurement.** Experiments run multiple seeds, report mean and 95% bootstrap CI (`stats.summarize_runs`),
   compare against the method's stated baseline at equal budget, and state when a claim was *not* reproduced.

## Mapping of cross-method ideas ("they need each other")

* Dream-RSI selection with RRSI guards: held-out replay worlds + noise margin (`rsi.dream.selection`).
* RRSI evaluations replayed Dream-RSI style: RRSI ledgers export as replay worlds.
* EvoMap assets must pass an RRSI-style independent check (`SafeHub` uses `NoiseFloor`/LCB on a hidden task bank)
  before they can spread; consumers quarantine and re-test on their own held-out tasks.
* Autoresearch keep rule can be swapped for `RRSIGate` / `BootstrapRigor`.
