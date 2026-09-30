# Meta-Harness and SoL-Pi

Meta-Harness: a coding agent reads every past candidate's code, scores and traces and rewrites the harness. SoL-Pi: harness mechanisms that cut tokens, kept only if they survive every environment family.

**Source:** Lee et al., Meta-Harness (arXiv 2603.28052); Liu et al., SoL-Pi (arXiv 2609.20519).

## Read in this order

1. [paper-spec.md](paper-spec.md): the method as the paper and reference code describe it. Every fact is tagged with its source.
2. [implementation.md](implementation.md): how `rsi` implements it, the API, how to apply it to a new problem, experiment results and deviations.
3. [claims-audit-metaharness.md](claims-audit-metaharness.md): claim-by-claim check against the paper (Meta-Harness, 38 claims: 14 reproduced, 12 partial, 2 not reproduced, 10 not testable here, 0 contradicted, after retry round 2), with a Fix log and the retry-round-2 preregistration and results.
4. [claims-audit-solpi.md](claims-audit-solpi.md): claim-by-claim check against the paper (SoL-Pi, 61 claims: 31 reproduced, 11 partial, 1 not reproduced, 18 not testable here, 0 contradicted, after retry round 2), with a Fix log and the retry-round-2 preregistration and results (§7).
5. [Validation audit](../../../validation/metaharness-solpi/AUDIT.md): step-by-step audit of fresh runs from the seed; [RUNS.md](../../../validation/metaharness-solpi/RUNS.md) describes the runs.

## Where everything is

| What | Path |
|---|---|
| Code | [`rsi/metaharness/`](../../../rsi/metaharness), [`rsi/solpi/`](../../../rsi/solpi) |
| Domains | [`rsi/domains/memoclassify/`](../../../rsi/domains/memoclassify), [`rsi/domains/agentworld/`](../../../rsi/domains/agentworld) |
| Tests | [`tests/metaharness-solpi/`](../../../tests/metaharness-solpi) |
| Experiments (scripts) | [`experiments/metaharness-solpi/`](../../../experiments/metaharness-solpi) |
| Results (JSON, figures) | [`results/metaharness-solpi/`](../../../results/metaharness-solpi) |
| Validation runs (traces, TRACE.md) | [`validation/metaharness-solpi/`](../../../validation/metaharness-solpi) |

## Run it

```bash
python -m pytest tests/metaharness-solpi                       # offline tests
python -m rsi.cli experiments metaharness                 # list experiments
```
