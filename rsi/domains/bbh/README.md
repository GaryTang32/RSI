# BBH domain (`rsi.domains.bbh`)

BIG-Bench Hard (BBH) as a harness-evolution domain. It reuses AgentQA's harness interface: a harness is `harness.py` with
`solve(question, llm, tools, files) -> str` plus prompt files, run in-process with AgentQA's watchdog and budgets
(12 model calls, 8 Python tool calls, 60 s of the harness's own time). `BBHDomain` subclasses `AgentQADomain`, so RRSI, GEPA,
Meta-Harness, SoL-Pi, autoresearch (`DomainResearchTask`), EvoMap, Dream (`DomainTask`) and Harness² (`rollout`,
`harness_brief`, `grader_verdict_markers`, `domain_of`) run on it exactly as on AgentQA. The offline AgentQA mocks work
too: the CLI, Meta-Harness's mock library and SoL-Pi's offline proposer accept any `AgentQADomain` subclass.

## Data and licence

- Source: [suzgunmirac/BIG-Bench-Hard](https://github.com/suzgunmirac/BIG-Bench-Hard) (Suzgun et al. 2022,
  "Challenging BIG-Bench Tasks and Whether Chain-of-Thought Can Solve Them"), MIT licence. Pinned at commit
  `9ee07bd481feebf959a6b59d61ea57bdcf30964d`: 27 `bbh/<task>.json` files (6,511 examples) and 27
  `cot-prompts/<task>.txt` files.
- Nothing from the dataset is committed. `data.py` downloads each file once into a cache and checks it against the sha256
  in `manifest.json` (committed). Cache: `cache_dir=` argument, else `$RSI_BBH_CACHE`, else `~/.cache/rsi/bbh`.
- A wrong hash is never used or cached. A tampered cache file is downloaded again, or refused with `download=False`.
  Offline with an empty cache, the loader raises `BBHDataError` and the message says how to fix it.
- Downloaded files are parsed as data only: `json.loads` with a shape check, and the CoT prompts as text. The canary
  header of each CoT prompt is stripped.

```python
from rsi.domains.bbh import ensure_all
ensure_all()            # fetch + verify all 54 files once (about 3 MB)
```

## Splits (`make_suite`)

| split | families | default per family | default total |
|---|---|---|---|
| evolve | practice | 6 | 36 |
| val | practice | 0 (off) | 0 |
| holdout (sealed) | practice | 10 | 60 |
| ood (sealed) | unseen | 10 | 60 |
| pilot (`make_pilot_suite`) | pilot candidates (all 27 by default) | 10 | 270 |

- **Practice:** multistep_arithmetic_two, word_sorting, dyck_languages, logical_deduction_five_objects,
  tracking_shuffled_objects_five_objects, date_understanding. Each needs several exact steps, so a Haiku-class model
  answering in one call is unlikely to be at ceiling.
- **OOD:** navigate, object_counting, temporal_sequences, geometric_shapes, penguins_in_a_table, web_of_lies. These
  test different skills, and none is a sibling of a practice family.
- The experiment reads its families from `experiments/bbh/families.json`, so a pilot can re-pick them.
- **Disjointness:** each family has one fixed permutation (seed `bbh-pilot-v1`, independent of the suite seed). Its
  first 10 examples are reserved for the pilot. The rest is shuffled with the suite seed and cut into
  evolve/val/holdout or ood. No example is ever in two splits, and the pilot never touches an evaluation example.
- Task ids are `<split>-<family>-<BBH index>`. Duplicate inputs within a family are dropped (keep first).
- Everything is configurable: `make_suite(practice, ood, per_family={...}, seed=..., pilot_reserve=...)`.

## Grading (`grading.py`)

The answer is the text after the last `ANSWER:` (any case, bold allowed), else the last non-empty line. It is normalised
the AgentQA way: unwrap bold, code and `\boxed{}`, strip quotes and trailing periods, lower-case. Then, by the gold
target's form:

| target | correct when |
|---|---|
| `(A)`..`(R)` | exactly one option letter is named in parentheses and it matches, or a bare letter (`A`, `A)`, `option A`), or the target option's text when no other option has the same text. Two letters are always wrong. |
| True/False, Yes/No, valid/invalid | the word itself, or a short answer (at most 8 words) naming only that word of the pair, with no negation |
| integers | the last number matches (`1,234`, `12.0` and Unicode minus are fine), or a number word up to twenty |
| Dyck closings (`] ) }`) | the trailing bracket run, whitespace removed, matches the target; the full sequence (the input followed by the closing) also counts |
| word lists and free text | same tokens (commas, brackets and quotes dropped) after an optional lead-in ("the answer is", "sorted list:") or a `label:` prefix. Extra leading words are wrong. |

- Feedback keeps AgentQA's wording (`Correct (answer '...')`, `Incorrect. Extracted answer '...'; expected '...'.`), so
  the target is revealed exactly as on AgentQA. Harness²'s grader-verdict screen uses the same markers.
- Every gold target in all 27 files round-trips, and a flipped answer fails for every one (`tests/bbh`).
- `leakage_terms()` lists only task ids and non-generic targets (full word lists, Dyck closings of 3 or more tokens),
  never `(A)` or `True`.
- A tamper tripwire, extending AgentQA's, restores the BBH grading functions if a harness replaces them.

## Fixed reference and leak check

- `cot3_artifact(families)` is the official 3-shot CoT prompt of each family plus `Q: … A: Let's think step by step.`
  It extracts "the answer is X" into `ANSWER: X`. It needs to know each question's family, so evaluate it with
  `BBHDomain(suite, family_hint=True)`, which adds `_task/family.txt` to `files`. Searched harnesses never get this.
- `leak_check(harness, suite)` checks a final harness against the evolve examples. It fails on:
  - a 40-character substring of an evolve question that no other evolve question contains (shared family
    boilerplate is allowed);
  - a non-generic evolve target written as a standalone string.

  Caveat: a 3-token Dyck closing that happens to appear in a worked example in a prompt counts as a hit. The JSON
  lists the hits so you can judge them.

## Offline use

- `BBHSimModel(suite)` is AgentQA's `SimModel` on BBH questions, for tests and dry runs only.
- `rsi improve --problem bbh --method rrsi --llm sim --out runs/bbh_rrsi` runs offline. It still needs the data cache.

## How to run the comparison

See `experiments/bbh/compare_live.py`, which runs one arm per command:
`pilot`, `seed`, `cot3`, `rrsi`, `gepa`, `metaharness`, `harness2` and `bestofn`.

```bash
python experiments/bbh/compare_live.py --arm all --offline          # dry run with mocks, no spend
python experiments/bbh/compare_live.py --arm seed --smoke           # 2 tasks live: formats and cost
python experiments/bbh/compare_live.py --arm pilot --cap 2 [--apply]
python experiments/bbh/compare_live.py --arm seed --cap 3
python experiments/bbh/compare_live.py --arm rrsi --cap 10           # one background command per arm
python experiments/bbh/merge.py                                      # results/bbh/merged.{json,md}
```
