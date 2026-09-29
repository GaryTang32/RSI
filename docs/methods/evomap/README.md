# EvoMap

Agents distil what worked into small, content-hashed strategy genes and share them through a hub; a faithful naive hub and a verified SafeHub.

**Source:** EvoMap Evolver / GEP, Strategy Genes (arXiv 2604.15097), Behind EvoMap (arXiv 2605.25815).

## Read in this order

1. [paper-spec.md](paper-spec.md): the method as the paper and reference code describe it. Every fact is tagged with its source.
2. [implementation.md](implementation.md): how `rsi` implements it, the API, how to apply it to a new problem, experiment results and deviations.
3. [claims-audit.md](claims-audit.md): claim-by-claim check against the paper (54 claims: 30 reproduced, 7 partial, 1 not reproduced, 15 not testable here, 1 contradicted after retry round 2), with a Fix log and a preregistered "Retry round 2".
4. [Validation audit](../../../validation/evomap/AUDIT.md): step-by-step audit of fresh runs from the seed; [RUNS.md](../../../validation/evomap/RUNS.md) describes the runs.

## Where everything is

| What | Path |
|---|---|
| Code | [`rsi/evomap/`](../../../rsi/evomap) |
| Domains | [`rsi/domains/katas/`](../../../rsi/domains/katas), [`rsi/domains/geneworld/`](../../../rsi/domains/geneworld) |
| Tests | [`tests/evomap/`](../../../tests/evomap) |
| Experiments (scripts) | [`experiments/evomap/`](../../../experiments/evomap) |
| Results (JSON, figures) | [`results/evomap/`](../../../results/evomap) |
| Validation runs (traces, TRACE.md) | [`validation/evomap/`](../../../validation/evomap) |

## Run it

```bash
python -m pytest tests/evomap                       # offline tests
python -m rsi.cli experiments evomap                 # list experiments
```
