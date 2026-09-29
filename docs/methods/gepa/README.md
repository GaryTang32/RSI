# GEPA

Reflects on full execution traces in plain language to rewrite prompts, keeping a per-instance Pareto frontier and merging lineages.

**Source:** Agrawal et al., Reflective Prompt Evolution Can Outperform Reinforcement Learning (ICLR 2026).

## Read in this order

1. [paper-spec.md](paper-spec.md): the method as the paper and reference code describe it. Every fact is tagged with its source.
2. [implementation.md](implementation.md): how `rsi` implements it, the API, how to apply it to a new problem, experiment results and deviations.
3. [claims-audit.md](claims-audit.md): claim-by-claim check against the paper (58 claims: 29 reproduced, 5 partial, 6 not reproduced, 17 not testable here, 1 contradicted, after retry round 2), with a Fix log and a preregistered retry round (§6).
4. [Validation audit](../../../validation/gepa/AUDIT.md): step-by-step audit of fresh runs from the seed; [RUNS.md](../../../validation/gepa/RUNS.md) describes the runs.

## Where everything is

| What | Path |
|---|---|
| Code | [`rsi/gepa/`](../../../rsi/gepa) |
| Domains | [`rsi/domains/ruleworld/`](../../../rsi/domains/ruleworld), [`rsi/domains/agentqa/`](../../../rsi/domains/agentqa) (shared) |
| Tests | [`tests/gepa/`](../../../tests/gepa) |
| Experiments (scripts) | [`experiments/gepa/`](../../../experiments/gepa) |
| Results (JSON, figures) | [`results/gepa/`](../../../results/gepa) |
| Validation runs (traces, TRACE.md) | [`validation/gepa/`](../../../validation/gepa) |

## Run it

```bash
python -m pytest tests/gepa                       # offline tests
python -m rsi.cli experiments gepa                 # list experiments
```
