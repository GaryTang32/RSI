# RRSI

Harness evolution with seven guards (annealed edit budget, failure memory, forced exploration, leakage critic, noise floor, cost rule, pruning), so kept changes generalize.

**Source:** Xia et al., Regularized Recursive Self-Improvement of Agent Harnesses (arXiv 2609.24972).

## Read in this order

1. [paper-spec.md](paper-spec.md): the method as the paper and reference code describe it. Every fact is tagged with its source.
2. [implementation.md](implementation.md): how `rsi` implements it, the API, how to apply it to a new problem, experiment results and deviations.
3. [claims-audit.md](claims-audit.md): claim-by-claim check against the paper (116 claims after retry round 2 and its review: 63 reproduced, 13 partial, 1 not reproduced, 36 not testable here, 3 contradicted), with a Fix log and the preregistered second attempt on every open claim (§6–7, review follow-ups in §7.1).
4. [Validation audit](../../../validation/rrsi/AUDIT.md): step-by-step audit of fresh runs from the seed; [RUNS.md](../../../validation/rrsi/RUNS.md) describes the runs.

## Where everything is

| What | Path |
|---|---|
| Code | [`rsi/rrsi/`](../../../rsi/rrsi) |
| Domains | [`rsi/domains/harnessworld/`](../../../rsi/domains/harnessworld), [`rsi/domains/agentqa/`](../../../rsi/domains/agentqa) (shared) |
| Tests | [`tests/rrsi/`](../../../tests/rrsi) |
| Experiments (scripts) | [`experiments/rrsi/`](../../../experiments/rrsi) |
| Results (JSON, figures) | [`results/rrsi/`](../../../results/rrsi) |
| Validation runs (traces, TRACE.md) | [`validation/rrsi/`](../../../validation/rrsi) |

## Run it

```bash
python -m pytest tests/rrsi                       # offline tests
python -m rsi.cli experiments rrsi                 # list experiments
```
