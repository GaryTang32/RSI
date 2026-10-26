# Harness²

Recursive agent harnessing for an open world (Xu et al., arXiv 2610.14224; code `google-research/harness2`): every task
of a stream is solved by refining the agent harness with Propose–Probe–Compose, guided by contrasts between executions,
with no grader, labels or validation feedback; an anonymised selector picks the deliverable, and an experience library
carries lessons to later tasks.

1. [paper-spec.md](paper-spec.md): the reimplementation spec, every detail tagged with its paper or code source.
2. [implementation.md](implementation.md): module map, API, deviations and limitations of `rsi/harness2`.
3. Experiments: `experiments/harness2/compare_sim.py` (offline) and `compare_live.py` + `merge_live.py` (Claude Haiku);
   results on the [data branch](https://github.com/GaryTang32/RSI/tree/data/results/harness2).

There is no claims audit yet; the skill reference `.claude/skills/rsi-improve/methods/harness2.md` is quote-checked in
`docs/skill-claims/ledger/harness2.jsonl`.
