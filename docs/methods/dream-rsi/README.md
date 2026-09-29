# Dream-RSI

Improves a discovery system's search policy (written as code) by replaying it over recorded search trees, with no new model calls.

**Source:** Zheng et al., Recursive Self-Improvement through Evolving Worlds (arXiv 2609.14858).

## Read in this order

1. [paper-spec.md](paper-spec.md): the method as the paper and reference code describe it. Every fact is tagged with its source.
2. [implementation.md](implementation.md): how `rsi` implements it, the API, how to apply it to a new problem, experiment results and deviations.
3. [claims-audit.md](claims-audit.md): claim-by-claim check against the paper (83 claims: 44 reproduced, 10 partial, 2 not reproduced, 24 not testable here, 3 contradicted), with a Fix log and a preregistered second attempt (retry round 2, §6–§7).
4. [Validation audit](../../../validation/dream-rsi/AUDIT.md): step-by-step audit of fresh runs from the seed; [RUNS.md](../../../validation/dream-rsi/RUNS.md) describes the runs.

## Where everything is

| What | Path |
|---|---|
| Code | [`rsi/dream/`](../../../rsi/dream) |
| Domains | [`rsi/domains/discovery/`](../../../rsi/domains/discovery) |
| Tests | [`tests/dream-rsi/`](../../../tests/dream-rsi) |
| Experiments (scripts) | [`experiments/dream-rsi/`](../../../experiments/dream-rsi) |
| Results (JSON, figures) | [`results/dream-rsi/`](../../../results/dream-rsi) |
| Validation runs (traces, TRACE.md) | [`validation/dream-rsi/`](../../../validation/dream-rsi) |

## Run it

```bash
python -m pytest tests/dream-rsi                       # offline tests
python -m rsi.cli experiments dream                 # list experiments
```
