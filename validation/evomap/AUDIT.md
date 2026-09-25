# EvoMap gene loop: stage-B step-by-step audit

The auditor did not write this code or the stage-A runs. Every number below was **recomputed from the raw per-task
trial scores in `trace.jsonl`**, not read off the gate. The recompute script re-derives U, U_LCB, delta and R for every
hub gate. For every quarantine gate it re-derives delta, dS, dC and the cost rule. For every solidify gate it
re-derives the composite score (§4.10), the keep rule and publish eligibility. It also checks the event parent chain
and every asset_id. The same checks are frozen as the regression test
`tests/test_evomap_validation_audit.py::test_every_gate_recomputes_from_the_traced_trials`.

Runs audited:

- stage A: `offline_katas` (the two agents, now in `_superseded_stageA/`), `live_katas` and `live_katas_r2`;
- stage B, after the fixes, from scratch: `offline_katas` (both agents) and `live_katas_r3` (fresh cache
  `.cache_live_katas_r3`).

## Summary of step verdicts

A "step" is one of the following:

- the signals, memory advice and selector analysis;
- one hub search;
- the solve (and gene-writer proposal);
- a solidify or skip gate;
- a hub-publish gate;
- a quarantine gate;
- an adoption report;
- the state/decision update.

| run | steps | correct | questionable | wrong | unverifiable |
|---|---|---|---|---|---|
| offline_katas/agent0 (stage A) | 58 | 46 | 9 (A1 x4, A2 x4, A4) | 3 (B2 x3) | 0 |
| offline_katas/agent1 (stage A) | 46 | 42 | 2 (A2) | 2 (B1, B4) | 0 |
| live_katas (stage A) | 41 | 37 | 1 (B3) | 2 (W1, B2) | 1 (transfer) |
| live_katas_r2 (stage A) | 41 | 37 | 1 (A1) | 2 (W2, W3) | 1 (transfer) |
| offline_katas/agent0 (stage B re-run) | 58 | 49 | 9 (A1 x4, A2 x4, A4) | 0 | 0 |
| offline_katas/agent1 (stage B re-run) | 45 | 43 | 2 (A2) | 0 | 0 |
| live_katas_r3 (stage B re-run) | 41 | 39 | 1 (A1) | 0 | 1 (transfer) |
| **total** | **330** | **293** | **25** | **9** | **3** |

- **Gate arithmetic.** All 60 gate recomputations match the loop, in every run, old and new. The composite 0.95
  equals the float result of Evolver's `round2(0.955)`.
- **What the wrong steps were.** None of them was an arithmetic error. They were rule or design defects: stage A fixed
  W1 to W3, and this stage fixed B1 to B4.
- **Questionable steps.** These are correct under the rule, but the evidence behind the decision is too thin to
  trust (A1, A2), or the behavior is faithful to Evolver but surprising (A4).

## Per-round step audit (evidence = trace seq)

### offline_katas/agent0

KataSimSolver + KataMockProposer(0.7), safe mode, SafeHub bank = the sealed test split. Stage-B run; stage-A
differences are noted inline.

- **Baseline.** Evolve S=0.35 (k=4), holdout 0.40 (seq 1, 4). This is trace-only: the baseline is not an input to
  the loop. Verified by code reading: `loop.py` never reads the tracer.
- **c1–c4 (days_between, same_text, count_in_range, backoff_delays).**
  - *Analysis: correct.* The signals are `task:<family>` plus the public meta keywords. The selector says `none`
    because the gene scores are 0 (require_match). The memory advice score 0.2665 equals 0.4·prior with prior =
    Laplace 2/3 and a 45-day half-life (§4.4, current engine).
  - *Hub search: correct.* The hub is empty.
  - *Scratch solve: correct.* It fails: 0/3, 1/3, 1/4 and 0/2 hidden.
  - *Writer: correct.*
    - The prompt holds only the public spec, the reply, "public tests: 2/2 passed" and "NOT solved". No hidden
      counts reach it (the trace prompt shows this).
    - The cards are class-level. The literals in them ("'%m/%d/%Y'", "501") come from public kata specs.
    - The leakage audit is empty.
  - *Retry: correct.* It runs on seed+7919 and solves the task.
  - *Solidify: correct.* Blast is 1 file / 2–5 lines. `python smoke_test.py` fails on W0 and passes on W1. The
    composite recomputes to 0.95 and the streak is 1, so the capsule is not publishable.
  - *Keep: questionable (A1).* The gene is admitted on **one** retry sample.
  - *State update: correct.* Epigenetic mark +0.1, one learning-history entry, the memory outcome is written
    immediately.
  - *Monitor: holdout 0.40 → 0.50 → 0.60 → 0.75 → 0.90.* This restates the simulator's knobs.
- **c5 (round_half_up).**
  - A shallow card ("Be careful with rounding problems") fails its retry, gets `task_check_failed`, and is rolled
    back and not stored. Correct.
  - *Stage A: wrong (B2).* It was classified `hard/constraint`. The stage-B run classifies it `soft/task_check`.
- **c6 (count_in_range, reuse).**
  - The memory graph prefers the boundaries gene: 0.7425 = (2/3 + 0.12·2/3)·decay. Selector score 3.568 = hits 2 +
    0.6·2 tags + 0.4·cos + adj 0.22, before the x1.5 factor (now labeled in the trace). Drift I = 1/√4 = 0.5, but
    there is only one candidate, so no random pick happens. Solved; streak 2; publishable (0.95 ≥ 0.78).
  - **Hub: base_in [0.5, 0.5], gene_in [0.75, 0.75] → U = U_LCB = 0.25 < delta 0.498 → rejected.** Recomputed. The
    stake of 20 is slashed (credits 480), per §9.2.5.
  - *The hub gate is questionable (A2):* 2 bank tasks × 4 trials.
- **c7 (days_between).** Reuse, then verified: U = U_LCB = 0.50 ≥ delta 0.458, R = 0. Recomputed. Stake refunded.
  (A2 applies.)
- **c8 (same_text).**
  - The unicode gene fails (1/3 hidden). Rejected, anti-pattern recorded, epigenetic mark 0.1 → 0.0 (−0.1 per
    §4.12). Correct.
  - *Stage A: wrong (B2):* a `hard` anti-pattern.
- **c9 (round_half_up).**
  - Scratch solves 4/4, so the cycle is a safe-mode skip: no event, no outcome. Correct.
  - No rounding gene was ever re-proposed, because the writer only runs on failure.
- **c10 (backoff_delays).** Verified: U = 0.625, U_LCB = 0.50 (the min of the 2 per-task diffs), R = +0.0625 ≥
  −delta. Recomputed.
- **c11 (same_text).**
  - The unicode gene is not preferred: its per-key evidence is 1 success and 1 failure, and preferred needs
    rawSuccess > rawFail. Correct.
  - *Stage A: wrong (B2):* `cautious` was on (max_files 6) because of the hard anti-pattern. The stage-B run has
    `cautious=false`.
  - Solved. Capsule streak 2, although this capsule's history is success, fail, success.
    *Questionable (A4):* failed events carry no capsule id, which is faithful to §3.6.
  - Verified: U = U_LCB = 0.5. Recomputed.
- **c12 (count_in_range).** Solved, streak 3. Not re-submitted, because this content key was already published once.
  Correct: the bundle strips learning metadata, so the asset_id is unchanged.
- **Run end.**
  - The event parent chain and the `meta.parent_asset_id` hash chain are intact across all 11 events, and every
    asset_id verifies.
  - Distillation never fired. It needs ≥ 10 good capsules and there are 4. The trace now notes when the distiller
    declines.

### offline_katas/agent1

Second solver, no writer, same hub.

- **c1.**
  - The hub serves agent0's date gene in an exploration slot (rank 0, no adoptions yet).
  - It is staged: asset_id verified, capsule confidence ×0.6.
  - **Quarantine on the val task `is_leap`:** base [1, 0], gene [1, 1], so dS = +0.5. Delta from the base trials is
    0.983, so the gate is inside the band. Cost rule: shaped = 100·0.5 − 15·1.419 = +28.7 > 0. Floor ok → adopt.
    Recomputed. *Questionable (A2):* 1 task × 2 trials.
  - The report is counted and the author earns +30 (credits 510).
  - The gene is promoted locally with `parent` = the hub asset, the solve succeeds, and the capsule is not published
    (source_type reused). Correct.
- **c2.** No gene and no writer. A failed event with an empty validation list (the safe rule), composite 0.65
  recomputed. Correct.
- **c3.** Backoff gene adopted: dS = +0.5, shaped +36.4. Recomputed. (A2 applies.)
- **c4, c6.** Skips. Correct.
- **c5.**
  - Unicode gene on `count_char`: base [1, 1], gene [1, 1], dS = 0, delta = 0, shaped −16.6 → rejected.
    Recomputed; correct.
  - **Stage A: wrong (B1).** The outcome-0 report was spot-checked and **agent1 was slashed** (−20 credits, −20
    reputation) for an honest ceiling result.
  - Stage B: the report is recorded as `uninformative: no headroom (baseline at ceiling)`, not counted and not
    slashed. Credits stay at 500.
- **c7.**
  - Local reuse of the adopted gene; streak 2.
  - **Stage A: wrong (B4).** The adopted gene was re-published under agent1's name. Only the hub's near-duplicate
    merge stopped it.
  - Stage B: not published (provenance `external`); the trace has no hub-publish event for agent1.
- **c8.** The rejected asset is filtered by the reject memory (`already_rejected: true`). Correct: no p-hacking by
  re-testing.

### live_katas (stage A, Haiku)

- **c1, c3–c7.** Solved from scratch; skip. Correct.
- **c2.**
  - Haiku's card is good (NFKC + casefold, "AVOID lower()"), and the retry passes 3/3.
  - **Wrong (W1):** `pytest -q smoke_test.py` exited 5 and the gene was rejected (fixed in stage A).
  - **Wrong (B2):** the rejection was also classified `hard/constraint`.
- **c8.**
  - *Questionable (B3).* The writer saw only the failed NFC + lower() reply, and its card recommends exactly that
    approach. The cycle-2 card for the same signals, which said "AVOID lower()", was not in the prompt: there was no
    §6.1 history block.
  - Retry 2/3 → rejected. Correct.
- **Transfer: unverifiable.** Holdout is 1.0 without genes.

### live_katas_r2 (stage A, Haiku)

- **c2.** Keep: discriminative under both python and pytest, own task solved. *Questionable (A1):* one retry sample.
- **c8.**
  - Reuse, streak 2, publish.
  - **Wrong (W2):** the bank was at ceiling (1.0 with and without the gene), delta = 0, U = U_LCB = 0 → verified.
    The recomputed gate "as run" accepts it; the fixed gate rejects it.
- **Ledger.** **Wrong (W3), bookkeeping only:** cycle tokens were double-counted (fixed in stage A before r3).
- **Transfer: unverifiable.** Holdout is at ceiling.

### live_katas_r3 (stage B, Haiku, fresh cache, after all fixes)

- **c1, c3–c7.** Skips. Correct.
- **c2.**
  - Scratch 2/3 → the writer (prompt has no history, as expected for the first card of this class) returns
    `gene_unicode_text_comparison` (NFC + casefold, "AVOID lower()").
  - Retry 3/3. `python smoke_test.py` passes after and fails before; kill rate 0.67.
  - Composite 0.95 recomputed. Kept. *Questionable (A1):* one retry sample.
- **c8.**
  - Reuse, 3/3, streak 2, publishable.
  - **Hub: bank at ceiling, U = U_LCB = delta = 0 → now REJECTED** ("LCB ≤ 0"). This confirms the W2 fix live.
- **Tokens.** The ledger cost for c2 is 10,894 = 1,750 (scratch) + 1,920 (retry) + 7,224 (proposer), so there is no
  double count (W3 fix).
- **Spend.** $0.333 total (50 calls; $0.126 of it is trace overhead), 7.5 min. Evolve 0.90 → 1.00; holdout 1.00 →
  1.00.

## Cost and usage accounting

- **Offline agent0.** 137 task calls = 17 loop rollouts + 20 baseline + 100 monitor (5 library versions × 5 holdout
  × k=4). 5 proposer calls.
- **Offline agent1.** 100 = 20 loop rollouts (8 solves + 3 quarantines × 1 task × 2 arms × k=2) + 20 baseline + 60
  monitor (3 versions).
- **Hub bank.** It uses its own reference solver object, so its calls are not billed to the agent. That is correct.
- **Live r3.** $0.3331 = task $0.3033 + proposer $0.0298.
  - Trace overhead of $0.126 is excluded from the budget check.
  - The meter's `_total` also sums the tokens of the `task:cached` role (cost 0). This is a core meter convention;
    `merged_usage` excludes it.

## Paper / reference alignment

### Mechanism level (spec section → trace evidence)

| spec | behaviour in the trace | verdict |
|---|---|---|
| §3.1 order: signals → memory → local select → hub → solve → solidify → publish | every round: analysis → note (hub search only when nothing local fits, `hub_when=no_local`) → proposal/eval → gate → decision/state | faithful (safe variant: the hub is consulted only when nothing local fits; Evolver's EVOMAP-FIRST prompt asks the hub first) |
| §3.2 signals / §4.6 dedup | task-descriptor signals; dedup never fired (no signal in ≥ 3 of the last 8 events, because the classes rotate) | faithful, not exercised |
| §3.3 selector, §4.2 score, §4.3 drift | scores recomputed by hand (hits + 0.6·tag + 0.4·cos + adj); I = 1/√Ne with drift off; ×1.5 preferred; capsule chosen by trigger independently of the gene | faithful; `require_match` is a documented deviation |
| §4.4 memory advice (current engine) | 0.2665, 0.7425, 0.5584, 0.3720 recomputed from Laplace, 0.12·prior and the half-lives; preferred needs rawSuccess > rawFail (c11) | faithful |
| §3.4 mutation / strategy | early-stabilize for cycles 1–5 → `optimize`/low; balanced afterwards → `innovate`/medium (fall-through innovate ≥ 0.5); cautious policy clamps to 6 | faithful |
| §3.6 solidify, §4.8 blast, §4.10 composite | blast from the workspace diff; composite 0.95 (= the §4.10 worked example 0.94, but with 4 signals) and 0.83 / 0.65 recomputed; rollback = gene not stored | faithful; safe additions documented |
| §4.12 failure mode | before B2, safe-mode extra failures counted as hard constraint; now soft | **inconsistent-fixed (B2)** |
| §4.12 epigenetics | +0.1 new mark, +0.05 on repeat, −0.1 on failure (c8 0.1 → 0.0, c11 → 0.05) | faithful |
| §3.6 capsule streak / §4.13 eligibility | streak counts events carrying the capsule id (failed events carry none); eligibility requires ≥ 2, score ≥ 0.7 and ≥ 0.78, and blast ≤ 5 files / 200 lines | faithful (A4) |
| §4.1 asset_id, event chain | all asset_ids verify; parent chain and hash chain intact | faithful |
| §3.7 distillation | never triggered (needs ≥ 10 good capsules; runs have ≤ 4) | not exercised → unverifiable |
| §3.8 ingest | asset_id check, confidence ×0.6, `external_candidate` in the memory graph | faithful |
| §6.1 prompt "Recent Evolution History … DO NOT repeat" | missing from the writer prompt before B3 | **inconsistent-fixed (B3)** |
| §9.2.3 SafeHub uplift | U, U_LCB, delta, R recomputed; zero-uplift acceptance at delta = 0 | **inconsistent-fixed (W2, stage A)**; confirmed live in r3 |
| §9.2.4 spot-check "a disagreement slashes the reporter" | honest ceiling report slashed | **inconsistent-fixed (B1)**; "disagreement" is undefined in the spec |
| §9.2.5 credits | stake 20 slashed on rejection, refunded on verification, +30 per first verified adoption, nothing for publication | faithful |
| §9.2.6 quarantine | A/B on the consumer's own val tasks with RRSIGate + dS > 0; reject memory | faithful; `min_tasks=1` is a documented deviation forced by katas |
| §3.6 publish when source ≠ reused | adopted gene re-published later (agent1 c7) | **inconsistent-fixed (B4, safe mode)**; faithful mode keeps Evolver's rule |

### Behavior level

- **Aligned.** The paper's dynamic of "look locally → hub → solve, then write the lesson as a compact gene" is what
  the trace shows:
  - agent1 inherited two of agent0's genes and its holdout rose 0.25 → 0.50;
  - reuse was measured (counted adoption reports carrying dS proof);
  - vacuous validation was rejected in safe mode (empty list, c2 of agent1; non-discriminative, live c2 of stage A);
  - the naive "self-reported" hub was not run in this validation (X7/X8 cover it).
- **Not reproducible here.** The paper's headline numbers (4,590 trials, CritPt) and the Behind-EvoMap network
  statistics. Offline gains restate the simulator's knobs. Live Haiku is at ceiling on the katas holdout, so no live
  claim that genes help unseen tasks can be tested. That item is unverifiable at CPU scale with this domain.

## Inconsistency register

| # | item | status | evidence |
|---|---|---|---|
| W1 | pytest-uncollectable katas smoke test rejected a correct live gene | inconsistent-fixed (stage A) | live_katas seq 20; live r2 / r3 c2 pass |
| W2 | SafeHub verified a zero-uplift gene (delta calibrates to 0 at ceiling) | inconsistent-fixed (stage A) | r2 seq 73 accepted; r3 seq 73 rejected |
| W3 | cycle tokens double-counted with one LLM | inconsistent-fixed (stage A) | r3 ledger c2 = 10,894 = sum of parts |
| B1 | honest outcome-0 report at ceiling slashed by the spot-check | inconsistent-fixed (this stage) | stage-A agent1 seq 56 vs stage-B agent1 seq 56; test B1 |
| B2 | safe-mode keep-rule failures classified as hard constraint (§4.12) | inconsistent-fixed (this stage) | stage-A agent0 seq 82 / 87 `hard`; stage B `soft/task_check`, c11 `cautious=false` |
| B3 | writer prompt lacked the §6.1 evolution-history block; a live writer re-proposed the failed approach | inconsistent-fixed (this stage) | live_katas c8 prompt; test B3 |
| B4 | adopted hub gene re-published under the adopter's name | inconsistent-fixed (this stage, safe mode) | stage-A agent1 seq 75; stage B has no agent1 publish |
| T1 | trace omitted the writer system prompt; selector scores shown before ×1.5 without a label; distiller declines untraced | inconsistent-fixed (trace only) | agent.py |
| A1 | a new gene is kept on ONE fresh-seed retry of the task it was written from | inconsistent-open (design; spec's keep rule is per-cycle) | 4 offline keeps plus the live keeps; the X10 honest-agent harmful cards (`poisoned_in_stores`) come from this |
| A2 | hub bank of 2 in-scope tasks × k; quarantine of 1 task × 2 trials (delta 0.98): verdicts are statistically weak | documented-deviation (katas size) | hub gates, agent1 c1 / c3 |
| A4 | capsule streak is not broken by a failed cycle that used the same gene | faithful (Evolver) | agent0 c11 |
| A5 | composite score does not see the task outcome (a rejected cycle still records 0.95) | faithful (Evolver §4.10); documented | agent0 c5 / c8 |
| A7 | `hub.metrics()["n_promoted"]` counts verified assets (strict = 0) | inconsistent-open (naming, minor) | summary.json hub_metrics |
| D1 | distillation, plateau, drift, bans and dedup never triggered in ≤ 12 cycles | unverifiable here | traces |
| D2 | transfer of live genes to unseen tasks | unverifiable (Haiku holdout = 1.0) | monitor rows |
| D3 | GDI weights and Behind-EvoMap numbers | unverifiable (source blocked, `[snip:BE]`) | spec §0 |
| C1 | `render_markdown` drops baseline per_task / trials from TRACE.md | core change request (rsi/trace.py) | TRACE.md vs trace.jsonl |

## Fixes and re-runs

- **Code.**
  - `rsi/evomap/safehub.py` (B1);
  - `rsi/evomap/quarantine.py` (the proof carries S_base / S_gene);
  - `rsi/evomap/solidify.py` (B2);
  - `rsi/evomap/prompts.py` and `rsi/evomap/agent.py` (B3, B4, T1).
- **Tests.** `tests/test_evomap_validation_audit.py` has 7 tests. The whole evomap suite passes: 51 tests.
- **Re-runs from scratch.**
  - `offline_katas`: $0. The stage-A copy is kept in `_superseded_stageA/`.
  - `live_katas_r3`: fresh cache, **$0.333**, 7.5 min.
- **Population experiments.** X10, X11 and X6 were re-run in full on the fixed code into a scratch directory
  (`results/` is not owned by this stage).
  - X11 and X6 verdicts are unchanged.
  - X10 verdicts are unchanged except `rank_validity_positive`: CI lower bound +0.018 → −0.005, mean 0.120 → 0.104.
    That claim was marginal and is not robust. This is documented in `evomap-impl.md` §10.

## Remaining open issues

1. **A1:** single-sample keep evidence. Recommended fix: require `rsi-taskcheck` (a paired A/B) or ≥ 2 retries
   before a newly written gene is stored.
2. **A2:** katas are too small for statistically meaningful hub or quarantine verdicts.
3. **A7:** naming of `n_promoted`.
4. **X10:** `poisoned_in_stores` conflates harmful cards an honest agent wrote itself with hub poison
   (experiment-script metric, not owned here).
5. **D2:** a harder live domain is needed to test transfer.
