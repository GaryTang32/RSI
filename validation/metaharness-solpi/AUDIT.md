# Meta-Harness and SoL-Pi: stage-B independent audit

**Scope.** I did not write this code or these runs. Every step of every run under `validation/metaharness-solpi/` was re-derived from the raw records, without reusing the stage-A audit (`validate_metaharness_solpi.audit_mh / audit_sp`). The raw records are `trace.jsonl` (raw per-trial scores), the experience store (sources, per-task trial records), `ledger.jsonl` and the firewall sink.

**Tool.** The audit script is `experiments/metaharness-solpi/validate_audit_b.py`. It writes one row per step to `audit_b/<run>.json`, and each row has a verdict and the evidence behind it.

**Verdicts.**

- *correct*: recomputed and it matches, and the step makes sense.
- *questionable*: mechanically right, but the step does not make sense or is off-spec in behaviour.
- *wrong*: the loop did the wrong thing.
- *unverifiable*: I could not establish the truth.

**Independent checks.**

- **Meta-Harness.** Scores and costs recomputed from the store's per-task trial records. Pareto front, dominance, `_best`, both deltas, finalize system set and admissibility recomputed. Every proposal's actual diff rebuilt from the stored sources. Every added line scanned for task ids, question text and literal answers of *all* splits. Every proposer prompt scanned for sealed task inputs.
- **SoL-Pi.** Gate arithmetic recomputed from the trace metrics and raw trial scores. Rollout target recomputed from the route-back rule. Oracle ranking recomputed. Firewall verdicts recomputed from the sink. The offline run's frozen candidates were independently re-evaluated on the holdout with a fresh evaluator. Composition checked against the union of the survivors' `harness.json`. Every LLM-reviewer verdict checked by hand against the runtime (`agentworld/base.py:_read_any`, `solpi/runtime.py`).

## 1. Summary of step verdicts

| run | steps | correct | questionable | wrong | unverifiable |
|---|---|---|---|---|---|
| `mh_memoclassify_offline` | 63 | 61 | 2 | 0 | 0 |
| `solpi_agentworld_offline` (re-run after fix 16, identical ledger) | 99 | 97 | 2 | 0 | 0 |
| `mh_agentqa_live` (haiku) | 33 | 27 | 4 | **2** | 0 |
| `mh_agentqa_live_r2` (haiku) | 32 | 27 | 5 | 0 | 0 |
| `solpi_agentworld_live` (haiku) | 22 | 21 | 1 | 0 | 0 |
| `solpi_agentworld_live_r2` (haiku) | 23 | 20 | 1 | **1** | 1 |
| `solpi_agentworld_live_r3` (haiku) | 22 | 18 | 1 | **3** | 0 |
| `solpi_agentworld_live_r4` (haiku, stage B, after fixes 15 + 16) | 24 | 22 | 2 | 0 | 0 |
| **total** | **318** | **293** | **18** | **6** | **1** |

**Where the "wrong" steps are.**

- *Loop-side bug (already fixed in stage A as fix 12).* In `mh_agentqa_live` it 2, both candidates were dead-file copies of their base.
- *Wrong LLM-reviewer verdicts in SoL-Pi.* All were false rejections. Stage A said 3; there are 4, because r2 L2.1 was also a false rejection that RUNS.md had called right.

No gate or keep decision was mis-computed in any run. No sealed number reached a decision.

## 2. Per-round step audit (compact)

### 2.1 `mh_memoclassify_offline` (MockProposer, MemoLM-A, N = 6, k = 2)

H0: `no_memory` S = 0.111 at ctx 191; `fewshot_all` S = 0.458 at 17,724. Both recomputed from 3 × 48 raw per-example records. The monitor on the seed gives ood 0.504.

| it | proposals (base -> cand, move) | S / ctx (recomputed) | frontier / incumbent | verdict |
|---|---|---|---|---|
| 1 | fewshot_all -> retrieve_topk; no_memory -> label_list | 0.535 / 2,484 ; 0.382 / 428 | both on the front; `_best` fewshot_all -> retrieve_topk (ood 0.604) | all correct; diagnoses cite visible traces ("96/144 prompts > 11,000 chars") |
| 2 | topk -> contrastive; no_memory -> coverage | 0.521 / 2,467 ; 0.507 / 3,606 | contrastive on the front (cheaper), coverage dominated; incumbent unchanged | correct |
| 3 | topk -> notes; contrastive -> notes | 0.535 / 3,548 ; 0.556 / 3,523 | notes(contrastive) new `_best` (ood 0.650) | correct |
| 4 | notes -> coverage; topk -> `param_char_budget_14000` | 0.5625 / 4,670 ; 0.535 / 2,484 | coverage `_best` (ood **0.654**, the best of the run) | **questionable**: the param variant is a behavioural no-op, with per-unit scores and cost identical to its base. It joins the front as an exact tie; one evaluation wasted |
| 5 | coverage -> notes_only; no_memory -> crossover(no_memory + coverage) | 0.549 / 1,949 ; 0.569 / 1,217 | crossover `_best`, ood 0.625 | correct |
| 6 | crossover -> `param_char_budget_4500`; coverage -> label_list | 0.590 / 5,658 ; 0.5625 / 4,945 | param variant `_best`, ood **0.558** | **questionable**: a parameter variant, which the skill says to rewrite. It wins on search while sealed ood falls |

- **Finalize** (one event, after the last iteration): systems = baselines ∪ Pareto ∪ per-unit best = {no_memory, fewshot_all, i06_0, i05_1, i01_1, i03_1}, recomputed and matching `results/` exactly. Test: best 0.634, fewshot_all 0.523.
- **Proposer input.** The mock parses the whole view: code, scores and traces of every candidate. `files_read` lists only the parents it diagnosed.
- **Sealed data.** No `results/`, `frontier.json` or `finalized.json` path is ever read.
- **Re-proposing.** The mock never re-proposed an exact genome, and it down-weights moves that regressed (`_regressed_moves`). `label_list` (it 6) and `notes` (it 3) were re-applied to new bases after they had helped, so they are not falsified ideas.

### 2.2 `mh_agentqa_live` (haiku, RewriteProposer, N = 3, k = 2)

| it | proposals | S / tokens | decision | verdict |
|---|---|---|---|---|
| 1 | seed -> extract_formatted, seed -> code_first | 0.6 / 2,332 ; **1.0** / 1,296 | code_first `_best` (monitor holdout 1.0, ood 1.0) | correct. The analysis cites real trace lines of evolve 004/007 |
| 2 | code_first -> code_answer_marked; seed -> harness_generated_solver | 1.0 / 1,296 ; 0.5 / 2,378 | code_answer_marked `_best` | **wrong ×2**: both wrote `agents/<n>/src/*`, so only NEW dead files were added and each ran its base unchanged, with identical cached replies. **Questionable** decision: the new `_best` ties code_first exactly and wins only on the name tie-break. **Proposer input questionable**: 2 of 30 traces cut by the renderer |
| 3 | code_first -> code_generalized_extract; code_first -> code_with_classification | 1.0 / 1,216 ; 1.0 / 3,162 | generalized `_best` | correct. The proposer saw only 20 of 50 trace files (render budget); it also hallucinated `<function_calls>` it cannot execute |

- **Finalize**: {seed, generalized, extract_formatted, code_first}, recomputed. Holdout 0.5 -> 1.0, ood 0.25 -> 1.0.
- **Spend.** Loop meter $0.616 + monitor $0.145 + **unmetered forked smokes $0.021** = cache ground truth $0.782, so the accounting is questionable; fixed by fix 17.
- **Why the trace's per-eval `usd` ($0.405) is larger than what was paid.** Cache hits report the *artifact's* cost, not spend.

### 2.3 `mh_agentqa_live_r2` (after fix 12)

| it | proposals | S / tokens | decision | verdict |
|---|---|---|---|---|
| 1 | seed -> exploit-robust-extraction (parse only); seed -> explore-structured-format | 0.7 / 2,229 ; 0.6 / 4,019 | robust `_best` (monitor = the seed's 0.5 / 0.25) | correct. Grounded in the analysis ("007, 009 computed right, extraction failed") |
| 2 | robust -> exploit-expression-evaluation; seed -> explore-tool-driven | **0.9** / 2,229 ; 0.8 / 1,516 | expression-eval `_best` (holdout 0.667) | **questionable**: the docstring quotes the search instance evolve-numeric-007 with its answer (`"3^163 ≡ 435 (mod 584)"`) and numbers from evolve-002. Comment only, no functional lookup, but it is exactly what the skill forbids. Proposer input: 20 of 30 traces |
| 3 | expr -> enhanced-verification; seed -> **explore-direct-pattern-computation** | 0.7 / 2,229 ; 0.7 / **0** | incumbent unchanged; pattern-computation joins the front at cost 0 | **questionable**: regex solvers for the generator's four question templates. Proposer input **questionable**: only 10 of 50 trace files rendered |

- **Finalize**: holdout 0.5 -> 0.667, ood 0.25 -> 0.25.
- **Paired evaluation.** Parse-only candidates re-use the seed's cached replies (same prompt and seed), so they are compared on the same samples.
- **Spend.** $0.563 + $0.062 + smokes $0.029 = $0.654.

### 2.4 `solpi_agentworld_offline` (re-run from scratch after fix 16; ledger identical to stage A's)

- **Base screen.** S = 1.0, tokens 449,605, cost 0.2407 on 24 evolve tasks. The monitor gives holdout 1.0, ood 1.0.
- **Oracle** (seq 4, recomputed): D1 0.662, T11 0.662, T3 0.599, T7 0.599, C6 0.476, P14 0.283, P8 0.283, C23 0.154, P20 0.002, R5 0.002. Stage A's RUNS.md listed different numbers; now corrected. All 10 were selected because `n_lineages` = pool size.

| lineage | iterations (gate recomputed: S vs floor 0.98; tokens / cost saving) | decision | verdict |
|---|---|---|---|
| D1 EPR | 1.0; +48.6% / +33.5% | frozen | correct |
| T11 pytest-quiet (trick) | 1.0; +58.7% / +38.6% | frozen (the training screen families all use pytest) | correct per rule |
| T3 tail-trim (trick) | 0.828, 0.828, 0.917 < 0.98; then grid exhausted | abandoned | correct. Rollouts after each failure are of the failed candidate, as the route-back rule says |
| T7 head-trim (trick) | 0.828, 0.828; exhausted | abandoned | correct |
| C6 OCC | 1.0; +17.9% / +4.4% | frozen | correct |
| P14 no-verify (do-less) | 0.902; exhausted | abandoned | correct |
| P8 Action Fusion | 1.0; +29.8% / +17.8% | frozen | correct |
| C23 ObservationPack | 1.0; tokens +8.3%, **cost -5.4%** | frozen on its most aggressive variant | **questionable**: passes the ∃-efficiency rule while the bill rises |
| P20 turn-cap (do-less) | 0.755 (10), 0.914 (16), **0.984 (24)** | frozen at 24 turns | correct per rule (a do-less shortcut passes the floor) |
| R5 prompt-slim (dud) | 1.0; +0.07% / +0.05% | no gain, grid exhausted | correct |

**Firewall** (seq 166–171). Each verdict was recomputed from the sink. D1 and P8 were also *independently re-evaluated on the holdout* and matched to the token.

| candidate | held-out result | verdict |
|---|---|---|
| D1 | pass (tokens +30.8%, cost +23.7% saving) | correct |
| T11 | reject: saving +1.4% ≤ 2% | correct |
| C6 | reject: score 0.986 (within tolerance) but cost -12.7% | correct |
| P8 | pass | correct |
| C23 | pass (cost +8.2% saving on holdout) | correct |
| P20 | reject: identical to the base, because the cap never binds | correct |

**Composition** (seq 172–173) = union {AF, EPR, OP(full_sends 1, excerpt 0)}, verified. Screen cost saving is only **+8.9%**, against **+33.5%** for EPR alone, and the stack is not re-gated. **Questionable.** I measured every subset on the screen:

| stack | S | tokens | cost | steps |
|---|---|---|---|---|
| base | 1.000 | 449,605 | 0.2407 | 14.4 |
| EPR | 1.000 | 231,096 | 0.1602 | 15.8 |
| AF + EPR | 1.000 | **175,890** | **0.1350** | 12.2 |
| EPR + OP | 0.992 | 235,096 | 0.2149 | 19.8 |
| AF + EPR + OP (shipped) | 1.000 | 229,380 | 0.2193 | 18.0 |

The loss comes from an **EPR × ObservationPack interaction under the MockAgent**. When evidence is not visible, the agent first recalls *every* placeholder, two pages each, before it reads the EPR archive. The recall pages are themselves over 10 KiB and get packed again. On one repofix task this took 35 steps -> 74 steps (both scored 1.0).

- Packing `obs_recall` results is faithful to the release (`observation.ts:createObservation` has no tool exclusion).
- The recall-everything habit belongs to the simulated agent and is **unverifiable** for real agents.

The monitor (seq 174) gives the composed harness holdout 0.988 and ood 1.0, with tokens -66%.

### 2.5 SoL-Pi live runs r1–r3 (haiku implementer + haiku LLMReviewer, MockAgent backend)

All gate arithmetic, rollout targets, decisions, firewall and no-sealed-eval checks are **correct**. The loop worked. The questions are about the LLM roles. I checked every review verdict against the runtime: a store key is readable by the agent iff it starts with `/.solpi/` (`_read_any`), and the runtime keeps the original result when a handler raises.

| run | cand | review | my check |
|---|---|---|---|
| r1 | L2.0 | (never reviewed) | haiku imported `ToolResult` from `anthropic.types`, ImportError; the Ralph loop correctly refused it (fix 13) |
| r1 | L2.1 | reject | **right**: key `bash_log_<id>` is unreadable and no path is given |
| r1 | L1.0 | pass | **questionable**: a silent no-op (`event.call_id` AttributeError swallowed by the runtime). The gate then correctly reported a saving of exactly 0 |
| r1 | L1.1 | reject | **right**: key `output_<id>` is unreadable |
| r2 | L2.0 | reject | **right**: stored at `/<id>.txt`, agent told `/.solpi/<id>.txt` |
| r2 | L2.1 | reject | **WRONG** (stage A called it right): stored at `/.solpi/full_log_<n>.txt` and the agent is pointed at exactly that |
| r2 | L1.0 | pass | **questionable**: forces `is_error=False` on every large result, and hides each output before the agent has seen it once. Its gate failure (S 0.892) is **unverifiable** as a capability loss: the MockAgent never calls free-form recall tools (`policy.py:_fallback`) |
| r2 | L1.1 | reject | **right**: `self.seen` persists across projections, so even the first appearance is replaced from the next request on, with no recall |
| r3 | L2.0 | reject | **WRONG**: `/.solpi/full_log_<n>.txt` is readable |
| r3 | L2.1 | reject | **questionable**: no try/except, but the runtime already fails open; the reviewer could not know that |
| r3 | L1.0 | reject | **WRONG** for the stated reason (`context -> None` is documented). An unmentioned real flaw: it hides the newest output at once |
| r3 | L1.1 | reject | **WRONG**: `/.solpi/tool_cache_<id>.txt` is readable |

**The loop-side cause of the losses in r1–r3.** A reviewer rejection used up the whole lineage iteration and restarted at 01 with a *new* proposal. The objection was never sent back to implementation, as spec B3.1 requires. With `max_iters = 2`, every lineage died on one or two reviewer opinions. This is **fix 16**.

### 2.6 `solpi_agentworld_live_r4` (stage B: fresh cache; fixes 15 + 16; same config as r3)

**Base screen.** 9 tasks, S = 1.0, tokens 545,286, cost 0.2722.

| round | step | what happened | verdict |
|---|---|---|---|
| L2.0 | 03/04 | haiku wrote `condense_bash_failures`: failing bash outputs ≥ 2000 chars are reduced to keyword lines ±1, plus the last 5; the full log is stored at `/.solpi/bash_log_<call id>.txt` and the agent is pointed at it | correct (readable path, try/except) |
| L2.0 | 05 | pass (the reviewer now sees `RUNTIME_API_DOC`) | correct |
| L2.0 | 06 + gate | S 1.0 vs floor 0.98; tokens +66.5%, cost +45.7% saving -> **frozen** (recomputed) | correct |
| L1.0 | 05 | reject on a theoretical MD5-collision objection | questionable (a nitpick) |
| L1.0r1 | 04 (fix 16) | the objection was routed back to implementation, and the repair (SHA-256 + content check) passed review in the same iteration | correct: this is the spec's Reviewer -> Implementation arrow working live |
| L1.0r1 | gate | tokens **-60.6%**, cost -32.4% (the mechanism *adds* traffic) -> no efficiency gain -> route back to 01 | correct |
| L1.1 | 05 | reject: "no try/except in the context handler" | questionable: `AgentRuntime.project()` already ignores a raising context handler |
| L1.1r1 | 04 (fix 16) | haiku's repair reply had no file block -> iteration abandoned, lineage at max_iters | correct |
| firewall | L2 | held-out S 1.0 vs 1.0; tokens +38.4%, cost +24.5% saving -> **pass** (recomputed; sink == trace) | correct |
| composition | {L2} | composed = base + `condense_bash_failures` (verified) | correct |
| monitor | composed | holdout S 1.0 (tokens 200,660 -> 123,618); ood S 1.0 (197,133 -> 108,488) | correct (trace only) |

**Outcome.** This is the first live haiku-written mechanism to survive the protocol end to end.

- It is an EPR-like log condenser with a recall path, the same family as the paper's Evidence-Preserving Reducer.
- It survived at equal quality on the training screen, the held-out firewall and the untouched ood split.
- Caveat: the agent is the offline MockAgent, whose skills read exactly the kind of error lines the condenser keeps. With a real agent this result is **unverifiable**.
- Spend: loop meter $0.276 = cache ground truth $0.276 (9 calls). The trace's proposer usage now includes the repair calls (fix 16).


## 3. Paper alignment

### 3a. Mechanism level

**Meta-Harness** (spec Part A)

| spec item | traced behaviour (seq refs are `mh_memoclassify_offline` unless named) | status |
|---|---|---|
| A3.1 H0 = valid baselines, evaluated on the search split | seq 2–3: `no_memory` + `fewshot_all` (release `config.yaml`); AgentQA: H0 = {seed} | faithful |
| A3.1 / A4 no parent selection, no mutation operators, no keep gate | seq 1 (noise: none); every admissible candidate is evaluated and kept (`evaluated ⇔ gate accept`, 12/12 + 12/12) | faithful |
| A4 k = 2 per iteration (paper §4.1; skill says 3) | round_start `k = 2` | faithful (documented choice) |
| A3.2 interface validation before evaluation | gate events; AgentQA smoke *runs one model call* (release: import prints OK within 30 s), timeout raised to 240 s live | documented deviation |
| A3.2 evaluate on the search split only | every eval event: split `evolve`, units ⊂ evolve | faithful |
| A4 Pareto (score up, context down), strict dominance, exact ties kept; `_best` = highest-score Pareto point | recomputed every iteration (seq 14, 25, 35, 46, 57, 68) | faithful formula; **tie order differs**: name vs the release's `rglob` order. Seen live: `mh_agentqa_live` it 2, where an exact copy became `_best` |
| A4 context cost = mean of non-zero per-unit context | recomputed from per-task records | faithful except the release's `int()` truncation (cosmetic) |
| A4 log both deltas (release post-iteration quirk + pre-iteration) | decision `delta_logged_release`, `delta_vs_pre_best_pts`, recomputed | faithful |
| A3.2 finalize once on baselines ∪ Pareto ∪ per-unit best; `results/` never visible | seq 71; no `results/` path in any view | faithful |
| A1 / A5 proposer reads code, scores and raw traces itself (grep/cat) | Mock: whole view. Live: `RewriteProposer`, a renderer with a 60k-char budget; traces rendered 28/30 and 20/50 (r1), 20/30 and **10/50** (r2) | **documented deviation**; `AgentProposer` (the paper's coding agent) never run live, so **unverifiable** |
| A5 proposer transcripts in history | only `sessions/*/meta.json` exposed | documented deviation |
| A2 / A8.2 no mechanical leakage guard | `leakage_screen = False`; consequences seen live (§2.3) | faithful |
| A4 online inner loop (paper) vs offline (release) | MemoClassify online | documented deviation (follows the paper) |
| N = 20, search sets 50–100 examples | N = 6 (offline) / 3 (live); 3 datasets × 48 / 10 tasks | scale deviation |

**SoL-Pi** (spec Part B)

| spec item | traced behaviour (seq refs are `solpi_agentworld_offline`) | status |
|---|---|---|
| B4.1 predeclared, frozen gate (∀ capability within τ ∧ ∃ efficiency better) | run_start seq 0 `gate_digest`; 15/15 gates recomputed | faithful; τ = 2% and min_gain = 2% are [inferred] (no published tolerances) |
| B3.1 oracle analysis before rollout budget | seq 4 | faithful in form; selects nothing when n_lineages = pool |
| B3.1 lineage 01 rollouts -> 02 map-reduce -> 03 one mechanism -> 04 Ralph loop until exit -> 05 reviewer -> 06 screen -> gate | seq 6–14 per lineage | faithful order |
| B3.1 "Reviewer -> Implementation" (`repeat … until rv.pass`) | before stage B, a rejection ended the iteration (r1–r3) | **inconsistent-fixed** (fix 16; exercised live in r4) |
| B3.1 gate failure -> "04 (fix) or 01 (new rollouts)" | always 01, with rollouts of the failed candidate, and a fresh proposal from the base plus gate feedback (a grid walk offline) | documented deviation |
| B4.1 "retain nondominated results" | only among one lineage's variants, and only with `sweep = True` (off in these runs, so the *first* passing variant freezes: C23 froze the most aggressive OP variant, which is neither the release's 2 sends / 512 + 512 nor the blog's V2) | documented deviation, questionable consequence |
| B3.1 held-out firewall: frozen only, once each, write-only, a failure is not feedback | seq 166–171; sink == trace == driver; `evaluate_frozen` raises on a second evaluation; no lineage holds the evaluator | faithful |
| held-out pass criterion = the same dual gate | firewall math | [inferred] in the spec; unverifiable |
| final split never touched by the protocol | only the monitor and the report touched `ood` | faithful |
| composition = survivors as independent opt-in extensions, not re-gated | seq 172–173 | faithful (humans refactored in the paper); `validate_composition` is an [inferred] option, off |
| four mechanisms as ports | the combination measurements above; `obs_recall` output re-packing checked against `observation.ts` | faithful where checked |

### 3b. Behaviour level

**Meta-Harness**

- **Matches.**
  - The proposer diagnosed failures from raw traces. Both live runs quote concrete trace lines (for example "007: computed 435 but output the whole verification line"), and candidates target those diagnoses.
  - Every valid candidate was evaluated; the frontier trades accuracy for context.
  - The offline best uses 3.1× less context than `fewshot_all` at +13 points on search and +11 on test. This matches the paper's direction ("+7.7 points with 4× fewer context tokens").
  - The test was evaluated once, after search.
- **Does not match, and why.**
  1. *"Code-space search regularizes towards coherent algorithms"* (paper §3, §5). The live r2 run produced a template-regex solver at cost 0 (on the front) and a docstring holding a search instance with its answer. The paper's own post-hoc regex audit would be the only guard (A8.2); the loop has none.
  2. *Selection on the search set does not track generalization.* Offline, the last two incumbent changes raised search (+0.7, +2.1 points: 1 and 3 examples) while sealed ood fell 0.654 -> 0.625 -> 0.558. Live r2's `_best` gained 0.4 on search and 0.167 on holdout but 0 on ood. This matches A8.3/A8.4 (small transfer, noisy selection, no noise band).
  3. *Improvement rate.* 6 of 12 offline candidates, and 2 of 6 per live run, became incumbents, against "fewer than 5 of 100 edits" (A8.4). The cause is weak seeds and 10–144-example search sets, not the method.
  4. *"Full, uncompressed history".* With the renderer budget, the r2 proposer saw 20% of the traces in iteration 3. Not the paper's setting (the proposer chooses what to read).
  5. *Live outcome variance.* Two identical-config live runs ended at ood 1.0 and 0.25. With N = 3, trials = 1 and n = 6/4 sealed tasks, one run is anecdotal.

**SoL-Pi**

- **Matches.**
  - Tokens fell at equal quality: composed screen tokens -49% at S 1.0; held-out -69% at 0.988 (within 2%); ood -66% at 1.0. The blog reports -44.7…-49% tokens at about 94% of Pi's score; we lose less quality.
  - Every trick and do-less idea was kept out of the final stack: T3, T7 and P14 by the floor; T11 and P20 by the firewall.
  - The firewall is one-way, and failures were never fed back.
- **Does not match, and why.**
  1. *Cost.* The composed stack saves 8.9% on cost against about 33% reported. Two causes: the ∃-rule admitted the cost-increasing OP variant, and the EPR × OP interaction under the MockAgent (table above).
  2. *OCC was rejected by the firewall.* Held-out sessions are short (10–16 requests), so a compaction does not repay the cache rewrite. This is the economics the impl doc predicts, not a bug.
  3. *Tricks and shortcuts passing the training gate.* T11 and P20@24 were stopped by the firewall only for "no gain", not by the capability floor: an environment-specific trick passes a single-environment-family screen.
  4. *About 1 in 40 ideas survive.* At 10 ideas (a library of mostly real mechanisms), 3 of 10 survived offline and 1 of 2 live (r4). **Unverifiable** at this scale.
  5. *Live lineages.* All of the live evidence uses the MockAgent as the backend. A free-form recall tool is never used by it, so capability verdicts on recall-based live mechanisms are unverifiable.

## 4. Inconsistency register

| # | item | status | evidence |
|---|---|---|---|
| 1 | MH candidates written to `agents/<n>/src/*` became dead files | inconsistent-fixed (stage A fix 12) | `mh_agentqa_live` it 2; r2 passes the check 6/6 |
| 2 | MH forked-smoke model calls missing from the loop meters | **inconsistent-fixed (fix 17)** | $0.021 / $0.029 gaps; `test_forked_smoke_usage_reaches_the_parent_meter` |
| 3 | SoL-Pi review rejection did not route back to implementation (spec B3.1, blog figure) | **inconsistent-fixed (fix 16)** | r1–r3 lineages ended on reviewer opinions; `test_review_rejection_routes_back_to_implementation`; live r4 |
| 4 | Ralph-repair usage dropped from lineage usage; an exhausted proposer was asked to "repair" 3× | **inconsistent-fixed (fix 16)** | `cannot repair: cannot repair: cannot repair:` in the trace; `test_ralph_loop_sums_repair_usage_and_stops_when_exhausted` |
| 5 | LLM reviewer judged without the runtime API (false rejections) | inconsistent-fixed (stage A fix 15; validated live in r4) | r2 L2.1, r3 L2.0 / L1.0 / L1.1 |
| 6 | RUNTIME_API_DOC gaps (pre-imports, `event.call.id`, `/.solpi/` keys) | inconsistent-fixed (stage A fixes 13, 14) | r1, r2 |
| 7 | RUNS.md oracle numbers and the r2 L2.1 verdict | inconsistent-fixed (doc corrected) | trace seq 4; §2.5 |
| 8 | driver docstring said "keep nondominated survivors", but the code composes all survivors | inconsistent-fixed (docstring) | `driver.py` |
| 9 | impl-doc fix table broken by a paragraph | inconsistent-fixed | `metaharness-solpi-impl.md` §9 |
| 10 | Pareto / `_best` exact-tie order by name (the release's order is filesystem `rglob` order); `per_unit_best` ties go the other way | inconsistent-open (minor) | `mh_agentqa_live` it 2 incumbent changed on an exact tie |
| 11 | RewriteProposer renders the history under a budget instead of letting the proposer choose; `AgentProposer` never run live | documented-deviation / unverifiable | traces 10/50 rendered in r2 it 3 |
| 12 | Interface smoke calls the model (release: import check) | documented-deviation | gate events |
| 13 | no leakage / overfitting guard in the MH loop | faithful (paper) | r2 it 2 docstring, it 3 template regex |
| 14 | MemoClassify online inner loop (paper) vs offline (release) | documented-deviation | impl doc §5 |
| 15 | SoL-Pi gate failure always routes to 01 (never 04 fix) | documented-deviation | route-back rule, 19/19 |
| 16 | "Nondominated" retention only within a lineage and only with `sweep = True`; first passing variant frozen otherwise | documented-deviation (questionable consequence: C23 aggressive variant) | seq 120 |
| 17 | ∃-efficiency rule admits a cost increase (C23 +5.4% cost) | faithful to the stated gate; at odds with the "cost per unit score" objective | seq 120 |
| 18 | composed stack not re-gated; costs more than EPR alone | faithful (blog warns); `validate_composition` inferred option off | subset table |
| 19 | tolerances 2% / 2% and held-out pass criterion | unverifiable (sources publish none) | spec B4.1 |
| 20 | EPR × OP recall cascade under MockAgent | unverifiable for real agents (simulation behaviour; port faithful to `observation.ts`) | 35 -> 74 steps example |
| 21 | Live capability verdicts on free-form recall mechanisms | unverifiable (MockAgent never uses such tools) | r2 L1.0 |
| 22 | survival rate, breadth-vs-depth, scale (10 ideas / 5 families vs 152 / 535) | unverifiable | spec B8.7 |
| 23 | trace per-eval `usd` counts cached trials at their original cost | documented (reporting nuance, not spend) | §2.2 |
| 24 | a forked smoke killed on timeout still loses its usage | inconsistent-open (minor) | `validate.py` |

## 5. Fixes and re-runs (stage B)

**Fix 16** (`rsi/solpi/research.py`, `driver.py`, `tracing.py`, `mocks.py`).

- A reviewer rejection now calls `proposer.fix(idea, prop, "<objections>")`, runs the Ralph loop on the repair and reviews again, up to `Config.review_max` times (default 2); `review_max = 0` gives the old behaviour.
- Traced as `<idea>.<it>r<n>` proposals and critics.
- `implement()` sums the usage of every repair and stops on an exhausted proposer.
- The LLM fix prompt now says the failing check may be the review.

**Fix 17** (`rsi/metaharness/validate.py`). The forked smoke ships its per-role usage delta back, and the parent adds it to every meter along the LLM wrapper chain.

**Regression tests** (in `tests/test_metaharness-solpi_validation.py`). All 63 tests of this method pass (`pytest tests/test_metaharness-solpi_*.py`).

- `test_review_rejection_routes_back_to_implementation`
- `test_ralph_loop_sums_repair_usage_and_stops_when_exhausted`
- `test_forked_smoke_usage_reaches_the_parent_meter`

**Re-runs from scratch.**

- `mh_memoclassify_offline`: ledger and trace identical to stage A.
- `solpi_agentworld_offline`: identical ledger; only the 4 "grid exhausted" proposal events changed text.
- `solpi_agentworld_live_r4`: new live run with a fresh cache (§2.6).

I did not re-run the live Meta-Harness runs. Fix 17 changes only metering, and the budget did not allow it.

**Documentation.** RUNS.md corrections, the impl-doc table (fixes 16–17 added), and the driver docstring.

## 6. Remaining open issues

1. **Exact-tie tie-break (register #10).** Recommend preferring the earlier-registered system (store `order`) so that an exact copy never displaces the incumbent. Not changed, because it would alter M1–M5 frontier orderings.
2. **Rendered history.** The MH live evidence uses the renderer (budget-limited, 10/50 traces at iteration 3). The paper's claim that the proposer "chooses what to read" needs an `AgentProposer` live run, which is more expensive.
3. **No overfitting guard in the paper loop.** Live r2 shows template-level and comment-level leakage reaching the frontier. A validation config could enable `leakage_screen` plus a regex audit of added lines, as the pilot does.
4. **SoL-Pi composition.** The shipped composition is worse than a subset. Recommend `sweep = True` for grid ideas (keep the nondominated variant) and `validate_composition = True`, or at least report the subset table.
5. **Mock backend limits.** SoL-Pi live lineages cannot validate recall-based free-form mechanisms with the MockAgent. An `LLMAgent` backend run is needed.
6. **Forked smoke on timeout** still loses its spend (register #24).

## 7. Spend

| item | USD |
|---|---|
| stage A live runs (from their fresh caches' entries) | 2.163 |
| stage B: `solpi_agentworld_live_r4` (loop meter = cache ground truth) | **0.276** |
| stage B offline re-runs, subset measurements, holdout re-evaluation, audit | 0 |
| total for this method's validation | **2.44** (cap ~$3) |

Stage-B wall time: the live run took 8.8 min. The offline re-runs and the audit took about 2 min.
