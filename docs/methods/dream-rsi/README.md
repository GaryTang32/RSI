# Dream-RSI

Improves a discovery system's search policy (written as code) by replaying it over recorded search trees, with no new model calls.

**Source:** Zheng et al., Recursive Self-Improvement through Evolving Worlds (arXiv 2609.14858; the docs cite v2, 6 Oct 2026, and mark v1-only text).

## Read in this order

1. [paper-spec.md](paper-spec.md): the method as the paper and reference code describe it. Every fact is tagged with its source.
2. [implementation.md](implementation.md): how `rsi` implements it, the API, how to apply it to a new problem, experiment results and deviations.
3. [claims-audit.md](claims-audit.md): claim-by-claim check against the paper (83 claims: 42 reproduced, 14 partial, 1 not reproduced, 25 not testable here, 1 contradicted, after the v2 refresh of 25 Oct 2026), with a Fix log and a preregistered second attempt (retry round 2, §6–§7).
4. [Validation audit](https://github.com/GaryTang32/RSI/blob/data/validation/dream-rsi/AUDIT.md): step-by-step audit of fresh runs from the seed; [RUNS.md](https://github.com/GaryTang32/RSI/blob/data/validation/dream-rsi/RUNS.md) describes the runs.

## Where everything is

| What | Path |
|---|---|
| Code | [`rsi/dream/`](../../../rsi/dream) |
| Domains | [`rsi/domains/discovery/`](../../../rsi/domains/discovery) |
| Tests | [`tests/dream-rsi/`](../../../tests/dream-rsi) |
| Experiments (scripts) | [`experiments/dream-rsi/`](../../../experiments/dream-rsi) |
| Results (JSON, figures) | [`results/dream-rsi/`](https://github.com/GaryTang32/RSI/tree/data/results/dream-rsi) |
| Validation runs (traces, TRACE.md) | [`validation/dream-rsi/`](https://github.com/GaryTang32/RSI/tree/data/validation/dream-rsi) |

## Run it

```bash
python -m pytest tests/dream-rsi                       # offline tests
python -m rsi.cli experiments dream                 # list experiments
```
