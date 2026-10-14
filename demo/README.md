# Live demo: the package improving a real LLM agent

Every run here started from an untouched seed. Claude Haiku was both the frozen task model and the proposer, and a locked exact-match grader scored every candidate. Held-out and OOD tasks are sealed: the loop never saw them. A write-only shadow monitor scored each new incumbent on them for the charts.

The headline numbers are a re-score after each run with fresh trials: trial seeds 100 and 101, which no loop decision and no monitor reading used ([how the numbers were measured](#how-the-numbers-were-measured)). The interactive version is `demo/index.html`. It shows every candidate, what became of it and the code each run produced.

## Results

### Hard AgentQA

The practice tasks are 5×5 logic grids and messy transaction ledgers. The OOD tasks are business-day scheduling and chained string transformations. There are 8 practice, 6 held-out and 6 OOD tasks.

| Run | Method | Practice | Held-out | OOD | Cost |
|---|---|---|---|---|---:|
| `runs/metaharness_hard` | Meta-Harness | 0.69 → **1.00** | 0.58 → **0.92** | 0.42 → **0.75** | $8.80 |
| `runs/rrsi_hard` | RRSI | 0.63 → 0.63 (kept the seed) | 0.50 → 0.50 | 0.58 → 0.58 | $7.62 |

### Standard AgentQA

The practice tasks are large-number arithmetic (digit sums, modular arithmetic, bit counts) plus one word problem. The OOD tasks are dates, number theory, strings and lists. There are 12 practice, 8 held-out and 8 OOD tasks.

| Run | Method | Practice | Held-out | OOD | Cost |
|---|---|---|---|---|---:|
| `runs/metaharness` (Run 4) | Meta-Harness | 0.17 → **0.46** | 0.31 → **0.75** | 0.56 → 0.56 | $2.26 |
| `runs/rrsi` | RRSI | 0.25 → **0.92** | 0.50 → **0.94** | 0.56 → **0.81** | $3.21 |
| `runs/autoresearch` | autoresearch (tinylm) | val_bpb 2.952 → **2.653** | hidden test 3.004 → 2.734 | shifted test 3.063 → 2.846 | $0.70 |

Each cell is seed → final, two fresh trials per task. The 95% confidence intervals of the paired gains are on the page. On the hard suite they are wide: Meta-Harness's gain is +0.31 [0, +0.63] on practice, +0.33 [−0.08, +0.75] on held-out and +0.33 [−0.33, +0.83] on OOD. The same seed harness, re-scored with the same trial seeds in the two hard runs' separate caches, scored 0.69 / 0.58 / 0.42 and 0.63 / 0.50 / 0.58: sampling noise alone moves a 6-task split by 0.17.

Cost is every Haiku call on the run's trajectory. That includes earlier attempts cut short by the account's usage cap or a container restart, whose calls the final attempt replayed from the cache ([Spend](#spend)). The re-scores are listed separately.

For autoresearch, lower is better. Its row shows the mean of a post-run re-evaluation of the baseline and final `train.py` over three fresh training seeds. The values the loop selected on were 2.956 → 2.637.

![Meta-Harness, hard tasks](figures/metaharness_hard_iterations.png)
![RRSI, hard tasks](figures/rrsi_hard_iterations.png)
![Seed vs final](figures/seed_vs_final.png)
![Meta-Harness replicates](figures/metaharness_replicates.png)
![RRSI, standard tasks](figures/rrsi_iterations.png)
![Meta-Harness Run 4](figures/metaharness_iterations.png)
![autoresearch](figures/autoresearch_iterations.png)

## What happened

### Meta-Harness, hard tasks

- **What it found.** No single trick solves these tasks. Haiku solves the logic grids by reasoning, but its brute-force code usually times out. On the ledgers its arithmetic without code is unreliable, and with code it is reliable.
- **The router.** Iteration 3 produced `hybrid-task-router`, the run's 5th candidate. Its proposer built it by combining two earlier candidates: `clear-format-answers`, strong on the logic grids, and `code-generation-with-templates`, strong on the ledgers.
  - **Routing rule.** A question with more data words (transactions, totals, dates) than logic words goes to a write-the-code-and-run-it path. Everything else, ties included, goes to step-by-step reasoning.
  - **Scores.** In the loop it scored 1.0 on practice, and the monitor's single held-out reading was also 1.0. With fresh trials it scored 1.00 on practice, 0.92 on held-out (one logic trial missed) and 0.75 on OOD.
  - **Scheduling vs ciphers.** Scheduling questions mention dates, so they go to code: 0 → 1.0. Ciphers match no data word and fall to reasoning, where the router did worse than the seed: 0.83 → 0.50. The keyword lists are tuned to the practice families.
- **Copied strings.** Its prompts copy strings from practice traces as format examples:
  - three practice answers: `2379.20` (also written `$2,379.20`), `Harper` and `Delta`;
  - a ledger customer name, `Cobalt`, which is no task's answer;
  - one practice task's exchange rate, `EUR_RATE = 1.145`.

  `Delta` is also the answer of one held-out ledger task. That task routes to the code path, whose prompts do not contain it. Faithful Meta-Harness has no leakage guard, as in the paper.
- **Leak check.** `experiments/demo/leak_ablation.py` replaced every one of those strings with a neutral one. Before scoring, it checks that no task's answer and no practice exchange rate remains. It then scored both versions on the same fresh trials:

  | Split | Router as found | Neutralised copy | Paired difference (95% CI) |
  |---|---|---|---|
  | Practice | 1.00 | 1.00 | 0 |
  | Held-out | 0.92 | 1.00 | +0.08 [0, +0.25] |
  | OOD | 0.75 | 0.67 | −0.08 [−0.50, +0.25] |

  Each difference is one trial. There is no sign the copied text drove the result, but 6 tasks per split cannot rule out a small effect. (A first check, now in `leak_ablation_v1.json`, missed the comma-formatted `$2,379.20` and the exchange rate.)

### RRSI, hard tasks

RRSI kept the seed.
- **Round 0.** Both candidates tied the seed on practice (0.75 in the loop) but cost more tokens, so the cost rule rejected them.
- **Round 1.** Both fell below the noise-adjusted floor of 0.55, at 0.50 and 0.125.
- **Leakage critic.** The critic is an LLM review; the answer-denylist precheck was off. It rejected the first draft of one candidate per round:
  - in round 0, because the draft declared two edits it did not make;
  - in round 1, because the edit's description named a practice task and its answer (the repair changed only that description).

  It passed both round-0 candidates, although each copied the practice answer `Sawyer` as a format example. That is the same kind of copy as in the Meta-Harness router. Neither candidate was kept.
- **Budget.** The $5 cap stopped the run after 2 of its 4 rounds.

This is not a controlled comparison with Meta-Harness. There is one run of each, and RRSI's per-round overhead (calibration, failure analysis, critic repairs, smoke tests) meant $5 bought 4 candidates. Meta-Harness, with no cap, found its router at its 5th candidate. After 4 candidates, neither method had beaten the seed on practice. Whether RRSI would find something like the router with all 4 rounds is untested.

### Meta-Harness on the standard tasks: four runs

There were four complete runs with the same configuration. The replay fix landed between them, so the code changed slightly from run to run. Run 1 also shares its LLM cache with the interrupted replicate shown on the page.

| Run | Practice | Held-out | OOD | What it found |
|---|---|---|---|---|
| Run 1 | 0.17 → **1.00** | 0.38 → **1.00** | 0.69 → **0.88** | write Python, run it with the tool, read the printed answer |
| Run 2 | 0.21 → **1.00** | 0.38 → **1.00** | 0.63 → **0.88** | the same |
| Run 3 | 0.21 → **1.00** | 0.38 → **1.00** | 0.56 → **1.00** | the same |
| Run 4 | 0.17 → **0.46** | 0.31 → **0.75** | 0.56 → 0.56 | better answer extraction; never executed code |

All four runs are fresh re-scores, two trials per task.
- **Run 1's harness.** Its final harness also hard-coded regex templates for five of the generator's question shapes, and its comments quote six practice questions.
- **Run 4 never executed code.** From iteration 2 on, its candidates told Haiku to write and run Python, but none called the Python tool: there were 0 tool calls in all 96 candidate task runs.
  - Haiku printed imitation tool calls with made-up, often wrong outputs, e.g. `ANSWER: 63` for 40627866717431 mod 148, which is 91.
  - Those candidates scored 0.08–0.25 in the loop. 40 of their 42 failures were wrong answers and 2 were extraction losses.
  - The proposer's own report misread its traces as "Python got it right, extraction picked the wrong number"; the traces contain no Python output.

One run is an anecdote. The spread across runs is the result: three of four found code execution and reached 1.0 on practice and held-out.

### RRSI, standard tasks

- **Round 0.** It kept a general fix: parse the Python in the model's reply, run it with the tool, and force a follow-up grounded in the tool output. In-loop practice went 0.17 → 0.83.
- **Round 1.** It reached 1.0 in the loop.
- **Round 2.** Both candidates scored 0.92, inside the noise band, and the cost rule rejected them. The $2.50 budget then stopped the run.
- **Leakage critic.** It rejected the first draft of both kept edits and passed them after repair. One draft had numeric-domain branching and worked examples; the other named task ids and answers.

The final harness contains no task answer, task id or question text. The fresh re-score puts it at 0.92 on practice, below the loop's 1.0: the loop's single-trial winner was partly lucky. It also scored 0.94 on held-out and 0.81 on OOD.

### autoresearch

Three changes were kept: learning rate 0.003 → 0.01 → 0.02, then hidden width 128 → 256.
- **The last keep was noise.** It improved validation by 0.011, inside the run's noise.
- **Hidden splits got worse.** It made both hidden test splits worse: the in-distribution one by 0.008 (2.725 → 2.734), the shifted one by 0.066 (2.780 → 2.846).
- **Rerun.** Re-running that commit gave 2.664, worse than the previous keep's 2.648.

The strict keep rule locked in noise.

## How the numbers were measured

A Meta-Harness or RRSI run produces three kinds of score:
- **In-loop scores.** The practice scores the loop decided on (one trial per task, trial seed 0 for Meta-Harness). They carry selection bias: the winner is the candidate whose sample happened to score highest.
- **Shadow-monitor readings.** The sealed held-out and OOD scores of each new incumbent (one trial per task, trial seed 0). The loop never saw them, and they are the orange and green lines on the charts.
- **The fresh re-score.** Each run's seed and final harness on every split, two trials per task, with trial seeds 100 and 101 (`experiments/demo/rescore.py`; runs recorded from now on get it from `live_demo.py`). These are the tables above.

The LLM cache is keyed on the trial seed. The first version of the post-run report used seeds from 0, so its trial 0 replayed the very samples the loop and the monitor had drawn. On the hard Meta-Harness run it made no new model call at all: its "re-scored" 1.00 on practice was the sample that selected the router. The fresh re-score replaced it; `transfer_report(seed_offset=...)` makes that possible.

## The replay fix, and a verified replay

Meta-Harness runs used to be impossible to replay from their LLM cache, because the proposer's prompt was different on every run. Three things fed run-varying text into it:
- wall-clock fields (`created_at`, `latency_s`, `runtime_seconds`, `timing_s`, `seconds`, `wall_s`, `elapsed_s`) in the files the proposer reads;
- the proposer's own bill in its session record, which a cached replay meters as $0;
- the sandbox's random scratch path (`/tmp/rsi_sbx_k3j9x/...`) inside tracebacks, which also reached the task model through tool output.

The fix:
- **Volatile fields.** `ExperienceStore.view(strip_volatile=True)` drops those fields from the files the store writes. It leaves candidate sources and raw execution traces exactly as written. `Config.deterministic_view` is on by default.
- **Stable sandbox path.** The sandbox reports a stable path, `/tmp/rsi_sandbox`, and replaces the longer path first when the temp directory is a symlink. Sandboxed code also runs with `PYTHONHASHSEED=0`, so printed sets come out in the same order every run.
- **Tests.** `tests/metaharness-solpi/test_metaharness_deterministic_view.py` and `tests/core/test_core_sandbox_stable_paths.py`.

**Proof.** `python experiments/demo/live_demo.py metaharness --replay-check` re-ran Run 4 from its cache with the model switched off, so any cache miss is an error. It was re-run on the current code. The result:
- the prompts of all 4 proposer calls (one per iteration, each yielding 2 candidates) were identical;
- all 8 candidate scores were identical;
- there were 208 cache hits in the main process (plus the interface-validation calls, which run in forked children) and 0 misses, and the replay spent $0.

The cache is keyed on a hash of the whole prompt, so zero misses proves the full prompts matched. The trace keeps only their first 60,000 characters.

**What can still vary.** The replay above held, but these can still make a replay miss:
- output of model-written code that is not deterministic (object addresses, the current time, unseeded randomness);
- wall-clock tool and harness timeouts (the hard run's traces hold 12 `[killed: timeout]`);
- error messages, which are recorded verbatim (a usage-cap message carries its reset time) and reach later proposer prompts.

Exact replay also covers only the rewrite proposer used here. The coding-agent proposer (`proposer_kind="agent"`) bypasses the LLM cache.

**Resuming a run cut short.** The account usage cap cut Run 4 off after iteration 3. The attempt was moved to `runs/_archive/metaharness_v3_capped` and the same command run again. It replayed the seed evaluation and iterations 1–3 from the cache for $0 (119 cached calls, which had cost $1.35 before the cap) and ran iteration 4 live.

The hard Meta-Harness run was resumed the same way after two container restarts and a second cap. RRSI on the hard tasks reused the cache of an earlier attempt that crashed during calibration. `live_demo.py --resume` now does the archiving step itself.

## Harder tasks

`rsi/domains/agentqa/hard.py` adds four families, built with `make_hard_suite()` and the `--suite hard` option:

| Family | Role | What it is |
|---|---|---|
| `logic` | practice | 5-house × 5-attribute logic grids, each with a unique solution |
| `ledger` | practice | 64–93-row exports (60–85 transactions plus 4–8 correction rows) with three date formats, European amounts, status synonyms and FX; asked as one exact figure or the top customer |
| `schedule` | sealed OOD | business days over holidays |
| `cipher` | sealed OOD | 5–7 chained string transformations |

`tests/core/test_agentqa_hard.py` re-derives every task of a 34-task suite and of the demo's suite from the question text alone:
- the logic grids with an independent search;
- the ledgers by re-parsing every row;
- the schedules with numpy's business-day calendar;
- the ciphers by applying each step as worded, checking that every operation occurs.

Calibration on the default hard suite (seed 0: 12 practice tasks and 8 OOD tasks), one trial per task. It replays at $0 with `python experiments/demo/calibrate_hard.py`:

| Harness | Logic | Ledger | Schedule (OOD) | Cipher (OOD) |
|---|---:|---:|---:|---:|
| Plain one-call seed | 1.0 | 0.0 | 0.25 | 0.75 |
| "Write Python and run it" | 0.0 (11 of 12 runs timed out, 1 crashed) | 1.0 | 1.0 | 1.0 |

The seed row is one trial per task and partly measures answer format. The seed returns its reply's last line:
- **Ledgers.** In 3 of the 6 ledger replies (all three "which customer" questions), the reply stated the correct answer above a last line that was a breakdown row or a sentence. The three "average" ledgers were real arithmetic misses, off by cents.
- **Schedules.** In 2 of the 3 failed schedule replies, the reply stated the correct date above the last line.
- **Sample variation.** The same seed solved 2 of the same 4 practice ledgers in the hard Meta-Harness run.

Even so, no single trick wins. Plain reasoning gets the logic grids right but is unreliable on the ledgers' exact arithmetic. Code gets the arithmetic right, but its brute force times out on the grids.

## Spend

The whole demo cost $56.38 of live Haiku calls. `experiments/demo/spend.py` sums every LLM cache and credits each call to the attempt that was running when it was written. Its output, `demo/spend.json`, is the record; git does not keep the write times the attribution needs.

| What | Cost |
|---|---:|
| Meta-Harness, hard tasks: four attempts cut short (two by the usage cap, two by container restarts), then the final one | $8.80 |
| RRSI, hard tasks: an attempt that crashed in calibration when the usage cap hit, then the final one | $7.62 |
| RRSI, standard tasks: a restarted attempt, then the final one | $3.21 |
| Meta-Harness Run 4: cut off by the usage cap after iteration 3, then resumed | $2.26 |
| Meta-Harness Runs 1–3 ($2.98, $1.68, $2.57) | $7.24 |
| The interrupted replicate (`runs/metaharness_a`) | $2.70 |
| autoresearch | $0.70 |
| Fresh re-scores of the seven runs above | $11.92 |
| Leak checks (the first $2.56, the complete one $2.90) | $5.46 |
| Hard-suite calibrations | $6.48 |
| **Total** | **$56.38** |

Run directories that ended early sit under `runs/_archive/`, with their logs. Each run's `summary.json` records `infra_errors` and `clean`. Those infrastructure errors are failed model calls (usage cap, timeouts, offline cache misses) and trials missing from the loop's evaluations, the monitor or the re-score. `rrsi` and `autoresearch` predate that check. Their traces were scanned afterwards with the same check, `results.tsv` was checked for crash rows, and they are clean. Every `rescore.json` records the same check for the re-score. `run_chain.sh` stops at the first job that exits non-zero or loses a model call.

## Reproduce

```bash
python experiments/demo/live_demo.py metaharness [--suite hard]   # or rrsi, autoresearch; refuses to overwrite a run
python experiments/demo/live_demo.py metaharness --resume         # archive an unfinished attempt, continue from its cache
python experiments/demo/live_demo.py metaharness --replay-check   # $0 replay of a finished run, compares every prompt and score
python experiments/demo/rescore.py [run]                          # fresh-trial re-score of recorded runs
python experiments/demo/leak_ablation.py                          # neutralise the copied strings, re-score with the same trials
python experiments/demo/calibrate_hard.py                         # the hard-suite calibration ($0 from its cache)
experiments/demo/run_chain.sh "metaharness --suite hard" "rescore.py metaharness_hard"   # one at a time; stops on any failure
python experiments/demo/spend.py && python experiments/demo/plot_demo.py && python experiments/demo/build_page.py
```

Each `demo/runs/<run>/TRACE.md` holds every step: the proposer prompt and reply, the diff, the critic verdict, per-task scores and the gate arithmetic.
