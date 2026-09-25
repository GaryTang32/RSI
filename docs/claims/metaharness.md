# Meta-Harness: claim-by-claim audit

Paper: Lee, Nair, Zhang, Lee, Khattab, Finn, "Meta-Harness: End-to-End Optimization of Model Harnesses", arXiv 2603.28052v1. It was read through third-party mirrors: `scratchpad/papers/meta-harness.txt` (HTML clipping) and `meta-harness-pdftext.txt` (PDF text, the source of Table 2's numbers).
Implementation audited: `rsi/metaharness/`, `rsi/domains/memoclassify/`, at commit `75c8464`.
Evidence audited:
- `results/metaharness-solpi/m1..m5*.json`, `live_smoke.json` and `live_reanalysis.json`;
- `validation/metaharness-solpi/{RUNS.md, AUDIT.md, mh_*}`;
- the spec in `docs/methods/meta-harness-and-sol-pi.md` Part A and the implementation notes in `docs/methods/metaharness-solpi-impl.md`.

This audit changed no code. Four new offline checks were run with scratch scripts outside the repo, in `scratchpad/claims_mh/c1..c4_*.py`. They made no LLM calls and used less than 3 minutes of CPU in total. Their results are quoted inline as **[c1]–[c4]**.

## Summary

| verdict | count |
|---|---|
| REPRODUCED | 10 |
| PARTIAL | 15 |
| NOT REPRODUCED | 2 |
| NOT TESTABLE HERE | 10 |
| CONTRADICTED | 1 |
| **total claims** | **38** |

**What the numbers show.**
- **The loop is faithful.** The outer-loop mechanics are sound and pass an independent audit: evaluate every valid candidate, no parent selection, a Pareto frontier over (accuracy, context), and one sealed test evaluation at finalisation.
- **Every quantitative headline stays unreproduced.** None of the paper's headline numbers can be reproduced here: +7.7 over ACE with 4× fewer context tokens, +4.7 on math across five models, the TerminalBench-2 numbers, the Table 3 ablation magnitudes, and 82 files read per iteration.
  - Three of these domains need infrastructure and models we do not run: GPT-OSS-120B/20B, a 535K-problem math corpus, and Harbor with Opus 4.6 at about $500 per TB2 iteration.
  - The CPU analogue (MemoClassify, MemoLM, MockProposer) reproduces only their *direction*.
- **The proposer is not the paper's.** The paper's central component is an Opus-scale coding agent that decides for itself what to read. It was **never run**.
  - The only LLM proposer run live (RewriteProposer, haiku) sees a renderer-selected slice of the history. On MemoClassify that slice contains **0 of 42 trace files** by iteration 7 [c3].
  - The offline evidence for "full history wins" therefore comes from a hand-written mock whose trace diagnosis is richer than its summary diagnosis *by construction*.
- **The overfitting caveats reproduce.** RRSI found that Meta-Harness transfers under 1 point, and the overview says there is no guard against test-specific edits. Here, leakage reaches the frontier when it is injected (M5). A live haiku run put a template-regex solver on the frontier. Offline, incumbent changes happen inside the seed-noise band [c2].
  - The RRSI magnitude does not reproduce: without leakage, the simulated transfer gain is +10 points, not under 1.

## Claim table

**Verdict key.** R = REPRODUCED, P = PARTIAL, NR = NOT REPRODUCED, NT = NOT TESTABLE HERE, C = CONTRADICTED.

### Mechanisms

| # | Claim | Source | Type | Fidelity + code ref | Our evidence | Verdict |
|---|---|---|---|---|---|---|
| M1 | A coding-agent proposer reads a filesystem holding *all* prior candidates' code, scores and traces, and decides what to inspect with grep/cat | Abstract; §3; Fig. 2 | mechanism | **Store:** faithful. `view("full")` exposes source, scores, per-task records, traces, run files and session metadata of every candidate (`rsi/metaharness/store.py:257-260`).<br>**Proposers:** three exist.<br>• `AgentProposer` (`proposer.py:220-259`) has Read/Edit/Write/Glob/Grep but **no Bash and no Agent** (`proposer.py:227`). It logs `files_read = sorted(view)`, the whole view, not the files it opened (`proposer.py:240`). It was never run live.<br>• `RewriteProposer` renders the view in a fixed priority order: run files, then *all* sources, then scores, then traces. Each file is cut at 12k chars and the total at 60k (`proposer.py:262-292`), so a renderer, not the proposer, decides what is read.<br>• `MockProposer` parses every source and score but diagnoses only the traces of the **parent it is editing** (`mock.py:323-326`). | Offline: `mh_memoclassify_offline` read traces from exactly **2 candidates per iteration**, 6 trace files every iteration, 11–31 files in total [c3].<br>Live: haiku RewriteProposer on AgentQA saw 28/30, 20/50, 20/30 and 10/50 trace files (`AUDIT.md` §2.2–2.3).<br>Renderer on the MemoClassify store after iteration 6: **16 of 134 files, 0 of 42 traces** [c3]. | P |
| M2 | Each evaluated harness gets a directory with source, scores and execution traces ("prompts, tool calls, model outputs, and state updates") | §3 | mechanism | Layout `candidates/<name>/{src, meta.json, eval/search/{scores.json, per_task, traces}}` (`store.py:7-21`, `115-138`).<br>MemoClassify traces are thinner than the paper's (`domains/memoclassify/domain.py:214-245`):<br>• no raw model output (only the parsed `pred`);<br>• the full prompt only for at most 12 *wrong* predictions per unit, cut to 6,000 chars;<br>• no prompts for train-phase steps;<br>• one memory checkpoint, cut to 3,000 chars. | `test_views_expose_exactly_what_the_mode_allows`; audit of the store (`AUDIT.md` §3a) | P |
| M3 | Population plus frontier, no parent-selection rule, no mutation operators; every candidate that passes validation is evaluated and stored | §3; Algorithm 1 | mechanism | `loop.py:236-256`: screen (off by default) → validate → evaluate every admissible candidate on the search split. Parents are whatever the proposer names. | `AUDIT.md` §3a: evaluated ⇔ admissible, 12/12 and 12/12 | R |
| M4 | Interface validation before evaluation ("import the module, instantiate the class, call both methods on a tiny set of examples") | Algorithm 1; App. D | mechanism | `validate_memory` does exactly this, plus a state round trip (`domain.py:273-300`). `InterfaceValidator` runs it in a forked child with a 30 s timeout (`validate.py:31-35`). | `test_interface_validator_catches_errors_and_hangs`; live runs rejected fence-wrapped files | R |
| M5 | Pareto dominance over (accuracy, context cost); `_best` = the highest-accuracy Pareto point | §3; `benchmark.py:compute_pareto_frontier` | mechanism | `frontier.py:14-18` matches the release's formula. Exact ties are broken by **name** (release: list order), and an exact copy became `_best` live (`AUDIT.md` register #10). The context metric is gameable; see new mismatch N2. | Audit recomputed the frontier every iteration; M3 frontier size 6.0 | R |
| M6 | The proposer never sees test results; one final test evaluation on the frontier | §3 | mechanism | The `test` split is sealed (the Evaluator raises). `finalize()` evaluates baselines ∪ Pareto ∪ per-unit best once, writes `results/` and locks the run (`loop.py:281-309`). No view includes `results/` (`store.py:226-295`). | `test_test_split_never_touched_during_evolution_and_finalize_locks`; `AUDIT.md` "no sealed number reached a decision" | R |
| M7 | A minimal domain-specific skill says where to write harnesses, how to inspect history, and what may and may not be modified | §3; App. D | mechanism | `SKILL_TEXT` (`proposer.py:30-53`) keeps the release's core rules: no benchmarks, exactly k candidates, change mechanisms not parameters, no dataset hints. It drops several release rules:<br>• the mandatory **prototype** step (there is no Bash to run one);<br>• the exploitation-axis rotation rule;<br>• **Step 0, post-eval reports**. No proposer can write `reports/`: `AgentProposer` may write only `agents/*` and `pending_eval.json` (`proposer.py:237`), so `reports/` is always empty. | Stores of all runs: `reports/` empty | P |
| M8 | The proposer is Claude Code with Opus 4.6 at max reasoning | §3; §4.1 | mechanism | `AgentProposer` + `ClaudeCLI` could run `claude -p`, but without Bash, subagents or stream-json session logging. | Never run: the validation budget was about $3, and one TB2-scale Opus iteration costs about $500 (release README) | NT |
| M9 | 20 iterations × 2 candidates = 40 (text classification); "typically ~60 over 20 iterations" | §3; §4.1 | mechanism | `config.py:26-27` defaults are N = 20 and k = 2. Experiments used N = 6–8 (M1, M3–M5), a budget of 20 evaluations (M2), and N = 3 live. | `m1_history_ablation.json` config: N = 8, k = 2 | P |
| M10 | Initial population = zero-shot, few-shot, ACE, MCE | §4.1 | mechanism | Only `no_memory` and `fewshot_all` (`memory.py:211`, the release `config.yaml` defaults). There is no ACE/MCE analogue. | – | P |
| M11 | Online text-classification setting (predict → learn, one example at a time) | §4.1 | mechanism | Online by default (`domain.py:205-221`). The release's default is offline; ours follows the paper. | `mh_memoclassify_offline` | R |

### Quantitative results

| # | Claim | Source | Type | Fidelity + code ref | Our evidence | Verdict |
|---|---|---|---|---|---|---|
| Q1 | +7.7 points over ACE (48.6 vs 40.9) with 4× fewer context tokens (11.4K vs 50.8K) | Abstract; §4.1; Table 2 | quantitative | No ACE analogue, so the comparator is `fewshot_all`. That comparator overflows MemoLM-A's 12,000-char budget by design (17.7k chars; `model.py:43`). In Table 2, few-shot(all) uses about the same context as Meta-Harness (12.3K vs 11.4K).<br>The context metric counts only the **last** prompt, so it can be gamed (N2). | M3: the selected harness scores +11.3 on test vs `fewshot_all` at 6.2× less context, and in 10/10 seeds some frontier harness beats `fewshot_all` on test with less context (`m3_pareto.json`).<br>Direction only; there is no strong comparator. | P |
| Q2 | +8.6 over MCE (28.5K context) | §4.1 | quantitative | No MCE analogue | – | NT |
| Q3 | Table 3: full 50.0/56.7 (median/best) vs scores-only 34.6/41.3 and scores+summary 34.9/38.7; count above zero-shot 39 vs 26/23; "even its median candidate outperforms the best candidate found under either ablation" | Table 3 | quantitative | Arms are store views (`store.py:257-268`). The offline proposer's trace diagnosis (`programs.py:348-391`) is written by us, and it is richer than its summary diagnosis (`programs.py:412-420`) by construction. | M1 (10 seeds, N = 8, k = 2), full − scores-only:<br>• median +3.4 [1.6, 5.9];<br>• best +2.7 [0.0, 6.2], CI touches 0.<br>Paper: +15.4 median.<br>**Full median > best of both ablations in 2/10 seeds** (recomputed from `per_seed`). The count above zero-shot is not discriminating: 15–16 of 16 in every arm, because zero-shot scores 0.111. | P |
| Q4 | At equal evaluation budget Meta-Harness beats Best-of-N, OpenEvolve, TTT-Discover and GEPA (median 50.0 vs 32.6–39.1) | Table 4 | quantitative | The baselines are *view projections* of one mock proposer (`seed_only`, `window`, `last_only`; `store.py:269-279`), not OpenEvolve, TTT-Discover or GEPA. | M2 (`m2_equal_budget.json`), final best: Meta-Harness 0.619 vs Best-of-N 0.558 / OPRO 0.593 / GEPA-style 0.602. Paired diffs: +6.2 [3.8, 8.4], **+2.6 [−0.3, 6.2]**, +1.7 [0.5, 3.2]. | P |
| Q5 | Matches the next-best optimizer's final accuracy after 4 of 60 evaluations ("10× fewer") and ends >10 points above | §1; §4.1; Fig. 1; Fig. 4 | quantitative | Same as Q4 | Median evaluations Meta-Harness needs to match each arm's final best, of 20: 4 (Best-of-N), 7.5 (OPRO), 10 (GEPA-style). It never matches in 1, 2 and 1 of 10 seeds. Final lead: +1.7 to +6.2 points. | NR |
| Q6 | OOD: 73.1 vs ACE 70.2 on 9 unseen datasets; best on 6/9 | Table 5 | quantitative | 4 synthetic OOD datasets; `fewshot_all` as comparator, no ACE | M4: +10.2 [8.3, 12.1] on unseen datasets (model A); the selected harness beats `fewshot_all` in 33/40 (seed, dataset) pairs, with 2 ties (`m4_transfer.json`) | P |
| Q7 | More than 32 few-shot examples hurts on 7/9 OOD tasks | §4.1 | quantitative | MemoLM's "lost in the middle" budget is a hand-set constant (`model.py:43, 156-160`), so the outcome would hold by construction | – | NT |
| Q8 | Math: one discovered harness gives +4.7 on average over no retrieval across 5 models (38.8 vs 34.1) and +1.3 over BM25, on 200 IMO-level problems | Abstract; §4.2; Table 6 | quantitative | No retrieval-math domain, corpus or LLMs. The analogue is transfer to an unseen simulated model. | M4 analogue: model B test +13.2 [10.7, 15.5], unseen datasets + model B +15.8. 40/40 dataset-level wins on B. | NT |
| Q9 | TB2, Opus 4.6: 76.4% (#2; KIRA 74.7) | §4.3; Table 7 | quantitative | No Harbor or TB2 adapter. The spec's TB2-style `AgentHarness` subclass validator is not implemented (impl doc §3 row 8). | – | NT |
| Q10 | TB2, Haiku 4.5: 37.6% (#1; Goose 35.5) | §4.3; Table 7 | quantitative | Same as Q9 | – | NT |
| Q11 | A single evaluation produces up to 10 MTok of diagnostics, about 3 orders of magnitude above other optimizers (0.002–0.026) | §1; Table 1 | quantitative | Measured on the offline store [c3]. | About 287k trace chars per evaluation (0.07 MTok, at 4 chars per token). The full view is 2.6M chars per iteration (M1) and 4.2M chars after 6 iterations (about 1M tokens) [c3].<br>That is about 140× below the paper per evaluation, but still above every other method in Table 1. | P |
| Q12 | The proposer reads a median of 82 files per iteration (69–99): 41% code, 40% traces, 6% scores, 13% other; "referencing over 20 prior candidates per step" | §1; App. A.1; Table 8 | quantitative | Not measurable. `AgentProposer` logs the whole view (`proposer.py:240`). The mock's `files_read` counts every parsed source and score as "read" (`mock.py:270-276`). | Mock: 11–31 files per iteration; traces from exactly 2 candidates per iteration [c3] | NT |
| Q13 | The text-classification search yields a family of non-dominated variants (Table 9: 8 frontier points) | App. B.1; Table 9 | quantitative | `pareto_frontier` over (score, context) | M3: frontier size 6.0 [4.9, 7.2] | R |
| Q14 | Given the desired trade-off, the proposer discovers harnesses across the frontier ("smooth Pareto curve"; Fig. 3) | §4.1; Fig. 3 | quantitative | The trade-off preference never reaches the proposer's prompt. Only the frontier set changes, and the mock sometimes picks a cheaper frontier parent (`mock.py:355-366`). | M3, Pareto vs scalar objective:<br>• hypervolume +290, not significant;<br>• frontier size 6.0 vs 6.7;<br>• selected test accuracy 0.575 vs 0.580. | P |

### Qualitative findings

| # | Claim | Source | Type | Fidelity + code ref | Our evidence | Verdict |
|---|---|---|---|---|---|---|
| L1 | Full history (raw traces) beats scores-only and summaries; traces are "the key ingredient" | Abstract; §4.1; §5 | qualitative | As in Q3: the views are faithful, the proposer is a mock | M1 median wins with CIs above 0. The effect is designed into the mock, so it shows that the views gate the information channel, not that an LLM proposer uses traces. | P |
| L2 | Summaries do not recover the missing signal and "may even hurt" | §4.1 | qualitative | Our summariser is a deterministic lossy stand-in (`programs.py:394-409`) | Summaries − scores-only: median +1.0 [0.4, 1.6]; best −0.8 [−2.4, 0.8]. Both directions come from our own choice of stand-in, so this is not evidence either way. | NR |
| L3 | The proposer forms causal hypotheses across iterations (finds the confound, then pivots to an additive change) | §4.3; App. A.2 | qualitative | Needs a long-horizon coding-agent run | Live haiku runs cite concrete trace lines (`AUDIT.md` §3b) but last only 3 iterations; no confound isolation was observed | NT |
| L4 | Code-space search regularises towards coherent algorithms rather than brittle hard-coded solutions; overfitting is inspectable | §3; §5 | qualitative | No guard in the loop, which is faithful | `mh_agentqa_live_r2` it 3: haiku wrote regex solvers for the generator's four question templates. The candidate sat on the frontier at cost 0. The it-2 candidate's docstring quotes a search instance with its answer (`RUNS.md` run 4; `AUDIT.md` §2.3). This is one small haiku run. | C |
| L5 | A discovered harness transfers to base models not seen in search | §4.2; §5 | qualitative | `transfer_report` on MemoLM-B, reported separately from the selection model | M4: B gains are positive in every seed and split | R |
| L6 | Meta-Harness "can improve automatically as coding agents become more capable"; only one proposer was tested | §3; §5 | qualitative | – | Needs several proposer agents at scale | NT |

### Caveats

| # | Claim | Source | Type | Fidelity + code ref | Our evidence | Verdict |
|---|---|---|---|---|---|---|
| K1 | RRSI: Meta-Harness practised best (93.0 vs 89.4) but added under 1 point on unseen benchmarks | doc.txt (RRSI Table) | caveat | There is no cross-method head-to-head; RRSI's domains (Harvey, JobBench, GDPval, APEX) are unavailable. | **The mechanism reproduces:**<br>• With leakage injected (M5), practised gain +52 vs test −8 (gap 0.60), and a leaky harness is selected in 10/10 runs.<br>• Offline validation: the last two incumbent changes raised search while ood fell 0.654 → 0.625 → 0.558.<br>**The magnitude does not:** without leakage, search +14.3 vs unseen +10.2 (M4). That is far from "under 1 point". | P |
| K2 | There is no explicit check for test-specific edits (overfitting is guarded only by the prompt and post-hoc regex) | doc.txt; §4.3 | caveat | `Config.leakage_screen = False` by default (`config.py:37`). The optional pilot-style screen is `validate.py:137`. | M5: screen off → 4.2 leaky candidates on the frontier; screen on → all 34 injected lookups rejected, 0 false positives. Live r2 leakage (L4). | R |
| K3 | Selection on the search set is noisy ("fewer than 5 of 100 edits produced a real improvement", ye-blog) | spec A8.4 | caveat | Default `trials = 1` (`config.py:34`); no noise band | **[c2]**, 8 seeds per candidate on the offline store:<br>• the per-candidate SD of the search score is 0.5–1.3 points;<br>• the it-5 incumbent change to `crossover` (logged 0.569, *its best of 8 seeds*) has seed-mean 0.544, **below** the incumbent it replaced (coverage 0.564);<br>• the final winner's logged 0.590 is its maximum over 8 seeds (mean 0.576).<br>The useful-edit rate is not reproduced: 6/12 offline, set by how the mock's library was designed. | P |
| K4 | Reading the full history is expensive (the proposer consumes far more tokens per step) | doc.txt | caveat | Session `meta.json` records view and read characters and proposer usage (`loop.py:185-192`) | The full view is 43× the scores-only view (2.6M vs 61k chars per iteration, M1). The live haiku RewriteProposer used about 23k input tokens per call only because the renderer caps the history at 60k chars; a real coding agent would read more. | P |
| K5 | TB2 search set = evaluation set (discovery framing) | §4.3 | caveat | Our loop keeps the search and test splits separate | Not applicable without TB2 | NT |
| K6 | "Five held-out models" includes the selection model GPT-OSS-20B; only 4 are unseen | Abstract vs §4.2 | caveat | M4 reports the selection model (A) and the unseen model (B) separately | Confirmed from the paper text | R |
| K7 | The paper is internally inconsistent: ~60 vs 40 candidates; 250 vs 88 math search problems; Table 2 context in "tokens" but Fig. 3 and Table 9 in chars (11.4 ≈ 45.5/4); 10× vs 15× (4 of 60) evaluations | §3, §4.1, App. D, Tables 2 and 9, §1 | caveat | – | Confirmed from both mirrors | R |

## Newly found mismatches (not in the impl doc, RUNS.md or AUDIT.md)

**N1. The live-smoke Meta-Harness numbers never tested what haiku wrote.** [c4]
- In `results/metaharness-solpi/live_smoke.json` and `live_reanalysis.json`, and the impl doc §4.1 "Live smoke", all four haiku candidates evaluated at *exactly* `fewshot_all`'s numbers: search 0.4737, test 0.5595, context 8667.7.
- Re-parsing the same cached replies (`/tmp/rsi_metaharness-solpi_runs/live/metaharness/store/sessions/iter00*/response.md`) shows why. Every file was written as `agents/<name>/src/memory.py`. That is the dead-file bug later fixed as fix 12 (`proposer.py:169-173`), and both scripts ran before the fix.
- With the current `_collect`, the real candidates score 0.456, 0.421, 0.404 and 0.456: all **below** `fewshot_all` (0.474). One of them sits at a lower context cost (0.421 at 2,471 chars).
- The conclusion ("none beats the baseline") survives, but the stated numbers and "4/4 validate" describe unchanged copies of the base. Both JSONs and the impl-doc paragraph are stale.

**N2. The context-cost objective can be gamed, and multi-call harnesses under-report context.** [c1]
- Context cost is measured on the *last* model call only (`domain.py:181, 194-234`), as in the release's `inner_loop.py`.
- Test: a two-call variant of `fewshot_all` makes its usual big call, then a tiny "Labels: <draft>" confirm call. Its reported context falls from 17,724 to **46.5 chars (381× less)**, while real tokens *rise* 2% (605,757 → 618,424). Its search score is 0.444, so it would dominate `no_memory` and join the frontier.
- The paper's own Draft Verification harness uses two calls, so its 5.4K figure may be understated in the same way.
- Any "N× fewer context tokens" claim from this loop should use `cost_metric="tokens"` or sum over calls.

**N3. The only LLM proposer run so far cannot see traces on MemoClassify.** [c3]
- On the offline store after 6 iterations, `render_view(view, 60000)` shows 16 of 134 files: run files and 7 of 14 sources. It shows **0 of 42 trace files**.
- Each trace averages about 96k chars, is cut to 12k, and ranks below all sources and scores.
- So `history_mode="full"` with RewriteProposer is, on this domain, *less* than scores-only. AUDIT noted budget truncation on AgentQA (10/50) but not this total loss.

**N4. The mock's "full" arm is not non-Markovian.**
- Every iteration it diagnoses exactly the traces of the 2 parents it edits: 6 trace files from 2 candidates (`mock.py:323-326`; [c3]).
- Its `files_read` also counts every parsed source and score as "read" (`mock.py:270-276`). That inflates the "files per iteration" figure it is compared with in M1 and K4.

**N5. The offline incumbent changes fall within seed noise.** [c2]
- See K3. With `trials = 1`, a lucky seed made `crossover` the incumbent although its 8-seed mean is 2 points below the system it displaced.
- Earlier reviews called this "selection on 3 units overfits". It is more precisely a winner's curse on single-seed scores.

**N6. The strongest Table 3 sub-claim is not reproduced, and one Table 3 statistic says nothing here.**
- "Median full candidate > best of either ablation" holds in 2/10 seeds.
- The count-above-zero-shot statistic (16/16/16 vs the paper's 39/26/23) cannot discriminate between arms, because `no_memory` scores 0.111.
- Earlier reports gave only mean differences.

**N7. Traces omit what the paper says they contain.** The paper lists "prompts, tool calls, model outputs, and state updates". MemoClassify logs:
- no raw model reply (only the parsed label);
- no prompts for train-phase steps;
- at most 12 error prompts per unit, cut to 6,000 chars;
- one state checkpoint, cut to 3,000 chars (`domain.py:214-245`).

The impl doc says the spec's A8.12 concern is "fixed". That holds for per-example records, not for the full content.

**N8. The proposer loses release behaviours it is supposed to have.**
- Post-eval reports can never be written: `reports/` is always empty (`proposer.py:237`).
- The mandatory prototyping step is dropped, because there is no Bash (`proposer.py:227`).
- Exploitation-axis rotation is dropped.

The paper calls the skill "the strongest lever", so these omissions matter.

**N9. M3 and the mock's diagnosis encode knowledge of the simulator.**
- The trace diagnoser's `budget_hint=11000` (`programs.py:348`) is tuned to MemoLM-A's hidden 12,000-char budget (`model.py:43`).
- The `fewshot_all` comparator overflows that budget by design. That makes "6.2× less context" a comparison against a strawman: in the paper, few-shot(all) and Meta-Harness use about the same context.

**N10 (minor). Unknown base systems are replaced silently.** When a proposer names a `base_system` that does not exist, `_collect` falls back to the first visible artifact (`proposer.py:168`).

**N11 (minor). M2 reports two different "median evaluations to match" for OPRO.** The verdict string gives 10.0 (never-matched seeds counted as 21); the summary field and the impl doc give 7.5 (reached seeds only). Neither is wrong, but the impl doc does not say which one it quotes.

## Reproducing the offline checks

Each check runs from `scratchpad/claims_mh/` with `python3 <script>`:

| check | what it measures | CPU | result |
|---|---|---|---|
| `c1_ctx_loophole.py` | context loophole | ~20 s | 17,724 → 46.5 reported chars |
| `c2_noise_band.py` | 8-seed noise band of every stored candidate | ~60 s | SD 0.5–1.3 points |
| `c3_render_budget.py` | renderer coverage and files read per iteration | ~2 s | 0/42 traces rendered |
| `c4_live_reparse.py` | the cached live replies, re-parsed with the current code | ~30 s | real scores 0.404–0.456 |

## What would be needed to fully reproduce

1. **The paper's proposer.** Run `AgentProposer` with Claude Code (Opus-class, max effort), with the missing capabilities added:
   - Bash (grep/cat, and prototyping on real examples), optionally subagents;
   - stream-json session logging that records the files *actually* read.

   That gives the 82-files and >20-candidates statistics (Q12) and the chance to observe confound-style causal reasoning (L3). Budget: tens to hundreds of dollars per 20-iteration run.
2. **The history ablation with LLM proposers (Table 3).** Run the three views with the coding-agent proposer, N = 20, k = 2, and at least 3 seeds per arm.
   - Use an *LLM* summariser for the summary arm (`LLMSummarizer` exists).
   - Replace the renderer (N3) or give the agent the filesystem.
   - This is the only way L1/L2 become evidence rather than design.
3. **The real text-classification benchmark.**
   - Data: LawBench, Symptom2Disease and USPTO-50k, plus the 9 OOD sets.
   - Model: GPT-OSS-120B.
   - Baselines: ACE and MCE, both in the initial population and as comparators.
   - Context: cost measured over *all* calls or in tokens (N2).
   - Evaluation: ≥ 3 evaluation seeds per candidate, or a noise band on selection (N5).
4. **Real optimizer baselines at equal budget** (Q4/Q5): OpenEvolve, TTT-Discover's PUCT reuse and GEPA, each given 60 evaluations, to test "matches after 4 evaluations, >10 points above".
5. **Math retrieval.**
   - A corpus of about 535K deduplicated and decontaminated problems with a BM25 stack.
   - Search sets of 250 or 88 problems, selecting with GPT-OSS-20B.
   - Evaluation on 200 IMO-level problems across 5 models, reporting the selection model separately (K6).
6. **TerminalBench-2.**
   - A Harbor adapter, the `Terminus2`-subclass validator, and the KIRA and Terminus 2 baselines.
   - Opus 4.6 and Haiku 4.5 at 89 tasks × 2 search trials and × 5 final trials. The release README puts this at about $500 per iteration.
7. **RRSI head-to-head (K1).** Run the same base harness, model and budget under Meta-Harness and RRSI on a practised benchmark plus unseen benchmarks, to measure the "under 1 point" transfer directly rather than through injected leakage.
8. **Housekeeping.**
   - Regenerate `live_smoke.json` and `live_reanalysis.json` after fix 12, and correct impl doc §4.1 (N1).
   - Let proposers write `reports/`.
   - Break exact frontier ties by registration order.
