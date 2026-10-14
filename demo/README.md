# Live demo: the package improving a real LLM agent

Every run here started from an untouched seed and used Claude Haiku both as the frozen task model and as the proposer. Every point on a chart is a real candidate that Haiku wrote and a locked grader scored. Held-out and OOD scores come from a write-only shadow monitor, so the loop never saw them. The interactive version is `demo/index.html`. It shows every candidate, the gate's verdict and the code each run produced.

## Results

### Standard AgentQA

These tasks are numeric word problems. The OOD tasks are dates, number theory, strings and lists.

| Run | Method | Practice | Held-out | OOD | Spend |
|---|---|---|---|---|---:|
| `runs/metaharness` (Run 4) | Meta-Harness | 0.29 → 0.54 | 0.50 → 0.69 | 0.56 → 0.56 | $0.91 |
| `runs/rrsi` | RRSI | 0.33 → **0.79** | 0.50 → **0.94** | 0.56 → **0.75** | $2.42 |
| `runs/autoresearch` | autoresearch (tinylm) | val_bpb 2.956 → **2.637** | hidden test 3.004 → 2.734 | | $0.70 |

Lower is better for val_bpb and the hidden test.

### Hard AgentQA

The practice tasks are 5×5 logic grids and messy ledgers. The OOD tasks are business-day scheduling and chained string transformations.

| Run | Method | Practice | Held-out | OOD | Spend |
|---|---|---|---|---|---:|
| `runs/metaharness_hard` | Meta-Harness | 0.75 → **1.00** | 0.67 → **1.00** | 0.17 → **0.67** | $6.85 |
| `runs/rrsi_hard` | RRSI | 0.62 → 0.62 (kept the seed) | 0.67 → 0.67 | 0.17 → 0.17 | $5.82 |

All seed → final scores were re-scored after the run on the same tasks: two trials per task on standard AgentQA, one on the hard tasks. Spend is each run's own live calls. Calls replayed from a run's cache cost $0.

![Meta-Harness, hard tasks](figures/metaharness_hard_iterations.png)
![Meta-Harness replicates](figures/metaharness_replicates.png)
![RRSI, hard tasks](figures/rrsi_hard_iterations.png)
![Seed vs final](figures/seed_vs_final.png)
![RRSI, standard tasks](figures/rrsi_iterations.png)
![Meta-Harness Run 4](figures/metaharness_iterations.png)
![autoresearch](figures/autoresearch_iterations.png)

## What happened

- **Meta-Harness, hard tasks.**
  - **What it found.** These tasks are built so that no single trick works. Haiku solves the logic grids by reasoning, but its brute-force code times out. The ledgers are solvable only with code. Iteration 3 produced `hybrid-task-router`, which routes questions by keywords:
    - transactions, totals and dates go to a write-the-code-and-run-it path;
    - houses, clues and hobbies go to step-by-step reasoning.
  - **Scores.** The router scored 1.0 on practice and on the sealed held-out tasks. On OOD it solved 3 of 3 scheduling questions, which mention dates and so go to code, but only 1 of 3 ciphers. The keyword lists are tuned to the practice families.
  - **Leaked strings.** Its prompts use strings copied from practice traces as format examples: one practice answer (`2379.20`) and three answer names (`Harper`, `Delta`, `Cobalt`). Faithful Meta-Harness has no leakage guard, as in the paper.
  - **Leak check.** `experiments/demo/leak_ablation.py` replaced those strings with neutral ones. The harness then scored 1.00 vs 0.92 on held-out and 0.83 vs 0.75 on OOD, with two trials per task. The copied text did not drive the result.
- **RRSI, hard tasks.** RRSI kept the seed.
  - Round 0: both candidates tied the seed on practice but cost more tokens, so the cost rule rejected them.
  - Round 1: one candidate fell below the noise floor and the other scored 0.125.
  - The leakage critic rejected the first draft in both rounds, before any evaluation. The $5 budget then stopped the run.
  - RRSI's small guarded edits found nothing worth keeping within budget. Meta-Harness, which keeps whole rewrites, found the router.
- **Meta-Harness replicates on the standard tasks.** Four complete runs used identical settings.
  - Runs 1–3 found the general strategy (write Python, run it, read the printed answer) and reached 1.0 on practice and held-out.
  - Run 4 kept a better answer-extraction harness and only tried Python in iterations 3–4. Each of those harnesses had an extraction bug.
  - Run 1's final harness also hard-coded regex templates for the generator's question shapes, and its comments quoted six practice questions.
  - One run is an anecdote. The spread across runs is the result.
- **RRSI, standard tasks.** Round 0 kept a general fix: parse the Python in the model's reply, run it, and force a follow-up grounded in the tool output. The leakage critic rejected the first draft of both kept edits. The final harness contains no task answer, task id or question text.
- **autoresearch.** Three changes were kept: learning rate 0.003 → 0.01 → 0.02, then hidden width 128 → 256. The last keep improved validation but made the hidden test slightly worse. The strict keep rule locked in noise.

## The replay fix, and a verified replay

Meta-Harness runs used to be impossible to replay from their LLM cache, because the proposer's prompt was different on every run. Four things fed run-varying text into it:
- wall-clock fields (`created_at`, `latency_s`, `runtime_seconds`, `timing_s`, `seconds`) in the files the proposer reads;
- the proposer's own bill in its session record, which a cached replay meters as $0;
- the sandbox's random scratch path (`/tmp/rsi_sbx_k3j9x/...`) inside tracebacks, which also reached the task model through tool output.

Nothing else varies: views are identical across processes with different `PYTHONHASHSEED`.

The fix:
- `ExperienceStore.view(strip_volatile=True)` drops those fields. `Config.deterministic_view` is on by default.
- The sandbox now reports a stable path, `/tmp/rsi_sandbox`.
- Tests: `tests/metaharness-solpi/test_metaharness_deterministic_view.py` and `tests/core/test_core_sandbox_stable_paths.py`.

**Proof.** `python experiments/demo/live_demo.py metaharness --replay-check` re-ran Run 4 from its cache with the model switched off, so that any cache miss is an error. The result:
- all 8 proposer prompts were identical;
- every candidate score was identical;
- there were 208 cache hits and 0 misses, and the replay spent $0.

**Resuming a run cut short.** The same property makes an interrupted run resumable. Run 4 was cut off after iteration 3 by the account usage cap. Rerunning the same command replayed iterations 1–3 from 209 cached calls for $0 and ran iteration 4 live. The hard runs were resumed the same way after two container restarts and a second cap.

## Harder tasks

`rsi/domains/agentqa/hard.py` adds four families, built with `make_hard_suite()` and the `--suite hard` option:

| Family | Role | What it is |
|---|---|---|
| `logic` | practice | 5-house × 5-attribute logic grids, each with a unique solution |
| `ledger` | practice | 60–85-row exports with three date formats, European amounts, status synonyms, correction rows and FX |
| `schedule` | sealed OOD | business days over holidays |
| `cipher` | sealed OOD | 5–7 chained string transformations |

`tests/core/test_agentqa_hard.py` re-solves every task from its question text alone, using an independent solver.

Calibration on the default hard suite (seed 0: 12 practice tasks and 8 OOD tasks), with one trial per task:

| Harness | Logic | Ledger | Schedule (OOD) | Cipher (OOD) |
|---|---:|---:|---:|---:|
| Plain one-call seed | 1.0 | 0.0 | 0.25 | 0.75 |
| "Write Python and run it" | 0.0 (its brute force times out) | 1.0 | 1.0 | 1.0 |

No single trick wins. That is what makes these tasks a demonstration of harness engineering.

## Spend

The whole demo cost $41.56 of live Haiku calls, summed from every LLM cache:
- the six runs above;
- the archived replicates and the capped or restarted attempts;
- the leak check ($2.56);
- two hard-suite calibrations ($6.48).

Run directories that ended early sit under `runs/_archive/`, with their logs. Each run's `summary.json` records `infra_errors`. A run with any model-call failure, such as the usage cap, is marked not clean and is not reported as a result.

## Reproduce

```bash
python experiments/demo/live_demo.py metaharness [--suite hard]   # or rrsi, autoresearch; refuses to overwrite a run
python experiments/demo/live_demo.py metaharness --replay-check   # $0 replay of a finished run, compares every prompt and score
experiments/demo/run_chain.sh "metaharness --suite hard" "rrsi --suite hard --max-usd 5"   # one at a time; stops on the usage cap
python experiments/demo/leak_ablation.py                          # neutralise the copied strings, re-score the sealed splits
python experiments/demo/plot_demo.py && python experiments/demo/build_page.py
```

Each `demo/runs/<run>/TRACE.md` holds every step: the proposer prompt and reply, the diff, the critic verdict, per-task scores and the gate arithmetic.
