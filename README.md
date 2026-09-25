# RSI: a toolkit for recursive self-improvement loops

`rsi` reproduces the mechanisms of seven 2026 self-improvement techniques on one shared,
tested core, so they can be applied to new problems:

| Method | Improves | Keep rule | Package |
|---|---|---|---|
| **autoresearch** (Karpathy) | the work: one editable script under a fixed budget | strictly better on one number | `rsi.autoresearch` |
| **RRSI** (Google, arXiv 2609.24972) | the agent harness | noise floor + leakage critic + cost rule + pruning | `rsi.rrsi` |
| **Dream-RSI** (DeepMind, arXiv 2609.14858) | the search strategy, as code | best replay score over past searches | `rsi.dream` |
| **EvoMap** (+ "Behind EvoMap") | a shared library of strategy genes | local validation + hub ranking (naive vs safe) | `rsi.evomap` |
| **GEPA** (ICLR 2026) | prompts inside a compound system | minibatch improvement, Pareto frontier, merge | `rsi.gepa` |
| **Meta-Harness** (Stanford) | harness code, end to end | none: all candidates evaluated; Pareto frontier | `rsi.metaharness` |
| **SoL-Pi** (NVIDIA) | token-saving harness mechanisms | dual gate across environment families | `rsi.solpi` |

All of them run the same loop: propose a change, run it, score it with a grader the loop
cannot touch, then keep or discard. New to the topic? Start with **[docs/RSI-101.md](docs/RSI-101.md)**.

## Install

```bash
pip install -e ".[science,dev]"      # numpy, scipy, scikit-learn, matplotlib, pytest
```

Model backends (`rsi.core.get_llm`):
- `mock` / simulated models: offline and deterministic, used by the tests.
- `claude:<model>`: the headless `claude -p` CLI.
- `api:<model>`: the Anthropic API; needs `ANTHROPIC_API_KEY`.

## Quick start

```bash
python -m rsi.cli methods                                    # what each technique does
python -m rsi.cli improve --method rrsi --problem agentqa --llm sim --out runs/rrsi-sim     # offline, seconds
python -m rsi.cli improve --method rrsi --problem agentqa --llm claude:haiku --out runs/rrsi-live --set T=6
python -m rsi.cli inspect runs/rrsi-live                     # per-iteration audit trail -> TRACE.md
python -m rsi.cli experiments                                # every reproduction experiment
python -m rsi.cli experiment rrsi e0_overfitting_trap        # run one
```

```python
import rsi
from rsi.domains.agentqa import AgentQADomain

dom = AgentQADomain()                                          # practice: number problems; unseen: dates, number theory, text, lists
res = rsi.improve(dom, method="rrsi", llm_task="claude:haiku", llm_propose="claude:haiku",
                  config={"T": 6}, out_dir="runs/rrsi")
print(rsi.transfer_report(dom, rsi.get_llm("claude:haiku"), {"seed": res.baseline, "rrsi": res.best}))
```

## Apply it to your own problem

1. Write a `Domain`. Implement `execute(artifact, task, seed, llm)` to run your artifact, and
   `grade(task, execution)` to score it with your locked grader. Or wrap two plain functions
   with `FunctionDomain`.
2. Build a `TaskSuite` with an `evolve` split, plus `holdout`/`ood` splits the loop never sees.
3. Measure noise on the seed (`Evaluator` + `noise_from_trials`).
4. Pick a method (`rsi.recommend(...)`), run `rsi.improve(...)`, then read the
   `transfer_report` and the trace.

The Claude Code skill in `.claude/skills/rsi-improve/SKILL.md` walks through these steps. Each
method also ships `experiments/<method>/example_new_problem.py` as a template.

## What is verified

- `docs/methods/<method>.md`: specs built from the papers and the authors' released code.
  Every detail is tagged with its source; each spec was fact-checked by a second pass.
- `docs/methods/<method>-impl.md`: the module map, the API, and every claim from the
  overview mapped to code, an experiment, and the result. Claims that did **not** reproduce
  are stated as such.
- `experiments/<method>/*.py` produce `results/<method>/*.json` and figures. They use
  multiple seeds and report means with 95% bootstrap CIs.
- `tests/`: offline and deterministic (`pytest`). Live smoke tests run with `pytest -m live`.

## Layout

```
rsi/core/       shared machinery: LLM backends, Artifact, TaskSuite (sealed splits), Domain, Evaluator,
                stats (noise band, CIs), gates (keep rules), Ledger, editors, leakage critic, sandbox
rsi/<method>/   one package per technique
rsi/domains/    agentqa (shared harness domain) + method-specific domains with ground truth
rsi/api.py      rsi.improve(), rsi.recommend()      rsi/cli.py   command line
rsi/trace.py    per-iteration trace, shadow held-out monitor, run inspector
docs/           RSI-101, ARCHITECTURE, method specs and implementation notes
experiments/    reproduction scripts          results/   their outputs          tests/   pytest
```

## Limits

- Everything runs on CPU. The papers' GPU-scale experiments (nanochat, KernelBench, frontier-model
  benchmark suites) are reproduced *qualitatively*, on small analogues with known ground truth.
- The RRSI and SoL-Pi paper texts could not be retrieved. For those, the specs follow the
  authors' released code and secondary sources, and mark this.
- Numbers from simulated domains show that a mechanism behaves as claimed. They are not
  benchmark results.
