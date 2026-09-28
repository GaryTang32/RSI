# Documentation

Start with the [guide](guide/RSI-101.md) if you are new to self-improving loops, then pick a method.

## Guide

- [RSI-101](guide/RSI-101.md): the shared loop, the two risks (noise, overfitting), and each technique in plain language.
- [Architecture](guide/ARCHITECTURE.md): how the package is organized and the contracts every method follows.
- [Applying rsi to your own problem](../.claude/skills/rsi-improve/SKILL.md): the step-by-step recipe (also a Claude Code skill).

## Methods

| Method | Improves | Folder |
|---|---|---|
| Autoresearch | An agent edits one training script, trains for a fixed budget, and keeps the edit only if the locked metric improved | [methods/autoresearch/](methods/autoresearch/README.md) |
| RRSI | Harness evolution with seven guards (annealed edit budget, failure memory, forced exploration, leakage critic, noise floor, cost rule, pruning), so kept changes generalize | [methods/rrsi/](methods/rrsi/README.md) |
| Dream-RSI | Improves a discovery system's search policy (written as code) by replaying it over recorded search trees, with no new model calls | [methods/dream-rsi/](methods/dream-rsi/README.md) |
| EvoMap | Agents distil what worked into small, content-hashed strategy genes and share them through a hub; a faithful naive hub and a verified SafeHub | [methods/evomap/](methods/evomap/README.md) |
| GEPA | Reflects on full execution traces in plain language to rewrite prompts, keeping a per-instance Pareto frontier and merging lineages | [methods/gepa/](methods/gepa/README.md) |
| Meta-Harness and SoL-Pi | Meta-Harness: a coding agent reads every past candidate's code, scores and traces and rewrites the harness | [methods/metaharness-solpi/](methods/metaharness-solpi/README.md) |

Each method folder has a README hub, a paper spec, implementation notes and a claims audit.

## Core

- [core/implementation.md](core/implementation.md): the shared core (`rsi/core`): test coverage, bugs fixed, calibration.

## Reports

- [reports/claims-summary.md](reports/claims-summary.md): all 475 paper claims across the seven methods, with verdicts after the fix round.
- [../validation/README.md](../validation/README.md): step-by-step validation of fresh runs from the seed.
- [reports/report.html](reports/report.html): the illustrated report (validation, claims, cost per method).
