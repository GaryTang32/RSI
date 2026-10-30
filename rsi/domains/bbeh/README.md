# BBEH domain (`rsi.domains.bbeh`)

BIG-Bench Extra Hard (BBEH) as a harness-evolution domain. It is built on the BBH machinery in `rsi.domains.bbh`:

- one loader, suite builder, leak check and domain, all driven by a `Benchmark` spec;
- `BBEHDomain` subclasses `BBHDomain`, which subclasses `AgentQADomain`.

So every method runs on it as on AgentQA and BBH: RRSI, GEPA, Meta-Harness, SoL-Pi, autoresearch, EvoMap, Dream and
Harness². The live BBH pilot put Haiku at ceiling (96.3% mean, 20 of 27 families at 10/10), which is why the
experiment moved to BBEH.

## Data and licence

- **Source:** [google-deepmind/bbeh](https://github.com/google-deepmind/bbeh) (Kazemi et al. 2025), pinned at commit
  `80d12ca916b7158f22293fcf3144f4d3d854d4be`.
- **Licence:** data CC-BY 4.0; code Apache 2.0 (Google LLC).
- **Files:** 23 tasks, 4,520 examples (`bbeh/benchmark_tasks/bbeh_<task>/task.json`). Most tasks have 200 examples;
  disambiguation_qa has 120. Inputs are long: the median is about 2.5k characters, the longest about 32k.
- **Storage:** nothing is committed. Files are downloaded once and checked against `manifest.json`.
  - Cache: `cache_dir=` argument, else `$RSI_BBEH_CACHE`, else `~/.cache/rsi/bbeh`.
  - Files are parsed as data only.
  - Offline with an empty cache, the loader raises `BBHDataError` with the fix in the message.
- **No CoT prompts:** BBEH publishes none, so there is no cot3 reference.
  - `BBEHDomain(family_hint=True)` raises.
  - `load_cot_prompt(..., benchmark=BBEH)` raises.
  - `--arm cot3` exits with an error.

## Splits

Sizes are cost-driven (about $0.015 per call).

| split | default |
|---|---|
| pilot (`make_pilot_suite`) | 5 per family × all 23 = 115, from a fixed per-family shuffle (`bbeh-pilot-v1`) |
| evolve | 4 per practice family = 24 |
| holdout (sealed) | 6 per practice family = 36 |
| ood (sealed) | 6 per unseen family = 36 |

- **Initial families:** `experiments/bbeh/families.json`.
  - Practice: multistep_arithmetic, boolean_expressions, time_arithmetic, word_sorting, web_of_lies,
    causal_understanding.
  - OOD: object_counting, temporal_sequence, shuffled_objects, dyck_languages, geometric_shapes, hyperbaton.
- **Re-picking families:** the pilot re-picks them with BBH's rule, using the accuracy band [0.15, 0.75].
- **Disjointness:** no example is ever in two splits, the pilot included. Duplicate inputs within a family are
  dropped: 6 in linguini, 1 in spatial_reasoning.

## Grading (`grading.py`)

1. **Our extraction:** the text after the last `ANSWER:` (any case, bold allowed), else the whole output.
2. **The official pipeline of `bbeh/evaluate.py`,** ported by hand (Apache 2.0, attributed in the file; read as
   text, never imported or run):
   - `extract_answer`: split on "The answer is:", "The final answer is ", "The final answer is: " and "The answer is ";
     drop one trailing "."; then `strip_latex` (`$..$`, `boxed{}`, `text{}`, `texttt{}`).
   - Lower-case, turn ", " into ",", drop `**`, keep the first line, drop a trailing ".".
   - `fuzzy_match`: `(a)` vs `a`, float equality, quote removal, `[x]` brackets, a trailing `?`.
   - The port reproduces the 8 examples printed by `evaluate.py`.
3. **One addition:** a reference ending in "." is also compared without that period. This affects 11 linguini
   targets such as "He really cried.", which the official pipeline can never credit because it drops the
   prediction's period but keeps the reference's.

Every gold target in all 23 files is graded correct both as `ANSWER: <target>` and as
`The final answer is: <target>.`.

**Feedback** uses AgentQA's exact wording and markers.

**Leak check:** the BBH rule, a specific 40-character evolve-question substring or a standalone non-generic evolve
target. For BBEH, "non-generic" means more than 2 tokens, e.g. `no, yes, no` or a sorted word list. A harness whose
prompt shows an answer-format example such as `A, A, A` therefore fails if that string is an evolve target.

## How to run

```bash
python experiments/bbeh/compare_live.py --arm all --offline          # dry run with mocks, no spend
python experiments/bbeh/compare_live.py --arm seed --smoke           # 2 tasks live
python experiments/bbeh/compare_live.py --arm pilot --cap 3 --apply   # re-pick families
python experiments/bbeh/compare_live.py --arm rrsi --cap 8            # one background command per arm
python experiments/bbeh/merge.py
```

These scripts are thin wrappers that call `experiments/bbh/compare_live.py --benchmark bbeh` and
`experiments/bbh/merge.py --benchmark bbeh`.

| setting | BBEH default |
|---|---|
| final eval (all holdout + ood arms) | k = 2, paired by example |
| rrsi | T = 4, k = 2 |
| gepa | B = 150 |
| metaharness | 3 iterations × k = 2, $0.75 per agent session |
| autoresearch | 6 experiments, each on evolve at k = 2 |
| solpi | 4 ideas × 2 iterations, k = 1; firewall 2 per practice family |
| dream | 2 live searches × 4 attempts, k = 1; M = 3 policy versions |
| evomap | 12 cycles, safe mode, local store; rsi-taskcheck on 4 tasks |
| harness2 / bestofn | k = 1 on 36 tasks (18 holdout + 18 ood); best-of-n matched to Harness² calls |

## The four later arms

All four use the same suite, seed harness and model (agent and proposer) as the other arms. They search on evolve
only. Each final harness is leak-checked and scored on holdout + ood at k = 2.

### autoresearch

- **Setup:** the loop edits `harness.py` and `prompts/*`. One experiment is one evaluation on evolve at k = 2.
- **Keep rule:** keep iff the gain over the incumbent exceeds the seed's noise band. This is the same band GEPA uses
  (one k = 2 seed evaluation on evolve, within-task bootstrap, z = 2). The upstream rule, which keeps any gain from
  one run, is not used.
- **Why:** the live evolve split is noisy. The same seed scored 0.50 and 0.625 in two measurements, so a noise-blind
  rule would keep luck.

### SoL-Pi

- **Objective:** fewer tokens and dollars at held accuracy.
- **Ideas:** four generic efficiency ideas (short scratchpad, no restating, the Python tool for counting and
  arithmetic, a bare final answer line). Each is implemented by the same model, screened on evolve and frozen per
  lineage.
- **Firewall:** a one-way check on a sealed `firewall` split.
  - It holds 2 examples per practice family (12 in all).
  - It is cut after holdout, so every other split keeps its examples.
  - It is disjoint from evolve, holdout, ood and the pilot.
  - Only SoL-Pi's firewall reads it.
- **Reporting:** `merge.py` shows tokens per answer, $ per example and the paired token difference vs seed for
  every 120-example arm, so "fewer tokens at equal accuracy" is visible.

### Dream-RSI

- **Setup:** two live searches of an LLM discovery agent on evolve. Each search has 4 attempts (k = 1, one
  24-example evaluation per attempt).
- **Dreaming:** between the searches, the developer rewrites the exploration policy and scores the new versions by
  replaying the recorded search. This makes no task calls.
- **Result:** `best` is the best program on evolve.
- **Fairness:**
  - Fair: same data, model, budget scale and final evaluation as every arm.
  - Unfair to the method: the paper's regime is many cheap searches. Here there is one recorded search of 4
    attempts to dream on, so the policy revision has little to learn from. The arm behaves close to Fixed
    Exploration with 8 attempts.
  - Winner's curse: picking the best of 8 noisy k = 1 evolve scores biases the evolve number upward. The held-out
    eval is unaffected.

### EvoMap

- **Setup:** a local gene store, no hub, safe mode.
  - A gene is written after an unsolved evolve task.
  - It is kept only if `rsi-taskcheck` passes. The check runs the same family's practice tasks with and without
    the gene, paired.
  - The library is then frozen and evaluated through `GeneRoutedDomain`: per task, the best-matching gene is
    appended to the system prompt.
- **Why evolve-then-eval rather than streaming:** EvoMap's cycle grades its own task to decide whether to keep a
  gene, so streaming the eval tasks would use their labels.
- **Consequence:** genes keyed to a practice family match no unseen-family task. Those tasks run the plain seed and
  replay its cached calls.

### Answer length for older arms

`experiments/bbh/rescore_tokens.py --benchmark bbeh --arm seed rrsi gepa metaharness` replays each final eval from the
shared cache in cache-only mode. It never calls the model, and it writes `<arm>.tokens.json` beside `<arm>.json`
without changing it.
