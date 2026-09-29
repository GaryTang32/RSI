# Autoresearch

An agent edits one training script, trains for a fixed budget, and keeps the edit only if the locked metric improved.

**Source:** Karpathy, March 2026 (open-source repo, no paper).

## Read in this order

1. [paper-spec.md](paper-spec.md): the method as the paper and reference code describe it. Every fact is tagged with its source.
2. [implementation.md](implementation.md): how `rsi` implements it, the API, how to apply it to a new problem, experiment results and deviations.
3. [claims-audit.md](claims-audit.md): claim-by-claim check against the paper (65 claims: 52 reproduced, 10 partial, 0 not reproduced, 2 not testable here, 1 contradicted), with a Fix log and a preregistered retry round 2 (sections 5-6: O26 upgraded, O17 downgraded).
4. [Validation audit](../../../validation/autoresearch/AUDIT.md): step-by-step audit of fresh runs from the seed; [RUNS.md](../../../validation/autoresearch/RUNS.md) describes the runs.

## Where everything is

| What | Path |
|---|---|
| Code | [`rsi/autoresearch/`](../../../rsi/autoresearch) |
| Domains | [`rsi/domains/tinylm/`](../../../rsi/domains/tinylm), [`rsi/domains/tabular/`](../../../rsi/domains/tabular) |
| Tests | [`tests/autoresearch/`](../../../tests/autoresearch) |
| Experiments (scripts) | [`experiments/autoresearch/`](../../../experiments/autoresearch) |
| Results (JSON, figures) | [`results/autoresearch/`](../../../results/autoresearch) |
| Validation runs (traces, TRACE.md) | [`validation/autoresearch/`](../../../validation/autoresearch) |

## Run it

```bash
python -m pytest tests/autoresearch                       # offline tests
python -m rsi.cli experiments autoresearch                 # list experiments
```
