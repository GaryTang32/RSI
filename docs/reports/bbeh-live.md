# Live comparison on BIG-Bench Extra Hard (BBEH)

Five RSI methods and two controls improve the same one-call Claude Haiku harness on BIG-Bench Extra Hard, and the
results are scored on held-out examples and on task families no method saw. Run 28-29 Oct 2026. Recorded output, the LLM
cache and every run directory are on the
[`data` branch](https://github.com/GaryTang32/RSI/tree/data/results/bbeh); the charts are in the gallery's BBEH group
(`demo/gallery.html`).

## Result

| Arm | n | Accuracy [95% CI] | Held-out | Unseen families | vs seed (paired) | Search cost |
|---|---|---|---|---|---|---:|
| Seed harness | 120 | 0.417 [0.342, 0.492] | 0.467 | 0.367 | | |
| RRSI | 120 | 0.438 [0.362, 0.517] | 0.500 | 0.375 | **+0.021 [+0.004, +0.042]** | $38.10 |
| GEPA | 120 | 0.417 (kept the seed) | 0.467 | 0.367 | 0 | $11.80 |
| Meta-Harness | 120 | 0.417 (kept the seed) | 0.467 | 0.367 | 0 | $16.29 |

Harness² and its matched control run on a 36-task stream (3 per family, half held-out, half unseen), with no labels in the
loop:

| Arm | Calls per task | Accuracy [95% CI] | vs its own first rollout | Cost |
|---|---|---|---|---:|
| Harness² k = 1 | ~8 | 0.444 [0.278, 0.611] | +0.028 [-0.139, +0.194] | ≥ $21.92 |
| Best-of-7, Haiku picks one answer | 8 | 0.556 [0.389, 0.722] | +0.139 [-0.028, +0.306] | $29.74 |
| Harness² minus best-of-7 | | | **-0.111 [-0.250, +0.028]** | |

The seed harness costs $0.22 per example (two trials of one call). Harness²'s cost is a lower bound: its improver and
selector sessions run as agents, which the cache does not record, so only the last resumed chunk's sessions are counted. Differences are paired by example, 95% percentile
bootstrap. Costs price every call an arm used, including calls replayed from the shared cache.

## What it shows

1. **RRSI's gain is real and small, and it is a formatting fix.** The one candidate it kept (round 2 of 4) leaves the
   prompt unchanged and rewrites only the answer line: `(D) Rodrigo` becomes `(D)`, `C` becomes `(C)`. BBEH's official
   answer check rejects the long form, so multiple-choice families gained: shuffled_objects 0.55 to 0.75,
   geometric_shapes 0.25 to 0.30. The other ten families did not move. Because the prompts are unchanged, its final eval
   replayed the seed's model calls from the cache.
2. **GEPA and Meta-Harness found nothing better than the seed.** GEPA proposed 17 prompt rewrites; 16 lost to the seed
   on their 3-example minibatch and the one it admitted scored 0.583 against the seed's 0.625 on its Pareto set.
   Meta-Harness's coding agent wrote six harnesses (decomposed sub-goals, structured reasoning, code solver, error
   recovery, a pattern table for linguini, code only where it helps); they scored 0.29-0.58 against the seed's 0.625.
3. **The 24-example search set is too noisy for small effects.** The same seed scored 0.50 in RRSI's calibration and
   0.625 in GEPA's and Meta-Harness's (k = 2 each). RRSI's noise band asked for +0.19 before accepting a candidate.
4. **Haiku as its own code editor often breaks the harness.** Two of RRSI's eight candidates failed its smoke check with
   `NameError`s in the code Haiku wrote.
5. **Harness² did worse than plain best-of-7 at the same number of calls.** This reverses the AgentQA result, but the
   interval includes zero. Per-family swings are large (web_of_lies 0.67 to 0.33, linguini 0.33 to 0.67 on 3 tasks each),
   which is what a 36-task stream can show.

## Design

- **Data.** BBEH (Kazemi et al., 2025, arXiv 2502.19187), 23 task files at commit `80d12ca9`, checked against a sha256
  manifest and cached outside the repo (CC-BY 4.0 data, Apache 2.0 code). Nothing from the dataset is committed.
- **Grading.** A port of the official `bbeh/evaluate.py` (prefix extraction, LaTeX stripping, fuzzy match), after our own
  step: the text after the last `ANSWER:` line, with a wrapping `<...>` removed. Every one of the 4,520 gold targets grades
  correct as `ANSWER: <t>` and as `The final answer is: <t>.`; one deliberate difference: a reference ending in "." is also
  compared without it (the official check can never credit 11 linguini targets otherwise).
- **Families, chosen by a pilot.** The seed harness answered 5 reserved examples from each of the 23 families (mean
  0.635). The 12 families with accuracy in [0.15, 0.75] were split into practice (boolean_expressions,
  causal_understanding, linguini, object_properties, web_of_lies, shuffled_objects) and unseen (buggy_tables,
  geometric_shapes, nycc, temporal_sequence, sarc_triples, sportqa). Families Haiku solved 5/5 (multistep_arithmetic,
  object_counting, time_arithmetic, ...) were left out: they leave no room to measure a gain.
- **Splits.** Search (evolve): 4 per practice family (24). Held-out: 10 per practice family (60). Unseen: 10 per unseen
  family (60). The pilot examples are reserved and never reused. Methods see only the evolve split; the leak check fails
  an arm whose harness contains a 40-character evolve question fragment or a multi-token evolve answer (all passed).
- **Arms.** One model (Claude Haiku 4.5 via `claude -p`) answers and proposes in every arm. RRSI: T = 4 rounds, k = 2.
  GEPA: 150 metric calls, acceptance margin from the seed's noise band. Meta-Harness: 3 iterations of 2 candidates,
  coding-agent proposer. Final evals: k = 2. Harness² k = 1 and best-of-7 stream the same 36 tasks; best-of-7's n is
  matched to Harness²'s calls per task.

## Why BBEH, not BBH

The first pilot used BIG-Bench Hard. With the same one-call harness Haiku averaged 0.96 over its 27 families (10 examples
each), 20 of them at 10/10: no method could show a gain. BBEH replaces each BBH task with a harder one. Published BBEH
scores (micro average over all 23 families): o3-mini (high) 54.2%, DeepSeek R1 34.9%, GPT-4o 22.3%. Our 0.417 is on the
harder half for Haiku by construction and is not comparable to those numbers; the all-family pilot mean was 0.635 on 115
examples.

## Running it

```bash
python experiments/bbeh/compare_live.py --arm pilot --apply          # pick families (rewrites families.json)
python experiments/bbeh/compare_live.py --arm seed --cap 30          # then rrsi, gepa, metaharness, harness2, bestofn
python experiments/bbeh/merge.py                                     # results/bbeh/merged.{json,md}
python experiments/bbeh/compare_live.py --arm all --offline          # the same pipeline with simulated models, no spend
```

Each arm resumes from its run directory and the shared LLM cache; `--cap` is in US dollars. A BBEH call with Haiku
produces about 10,700 output tokens (median) and costs about $0.06-0.11; the whole comparison spent about $150.

## Problems found while running, and fixed

- BBH grader: answers like "No, Fidel does not tell the truth." were marked wrong; option-text targets did not accept
  the letter.
- BBEH grader: the seed prompt's own `ANSWER: <answer>` placeholder led to answers wrapped in angle brackets.
- Long BBEH answers ran past the 5-minute call timeout; it is now 15 minutes with 2 attempts.
- An account usage limit made every call fail and the methods scored the empty replies; `claude -p` errors that report a
  usage limit now stop the run (exit 75), and as failures are never cached the rerun resumes cleanly. The affected runs
  are kept under `results/bbeh/aborted_usage_limit/` on the data branch.
- Meta-Harness's 30-second interface smoke check rejected every candidate (one BBEH call takes minutes); it now follows
  the call timeout.
- Best-of-n rolled out and selected one task at a time (~7 hours); rollouts are now prewarmed in parallel and selection
  runs 8 tasks at once.

## Limits

- One search run per method, one model, 24 search examples: a method that needs a larger search set or a stronger
  proposer is not given a fair chance here.
- Harness² and best-of-7 rest on 36 tasks (3 per family); their intervals are wide.
- The 12 families were picked for headroom, so absolute accuracy describes this subset of BBEH only.
