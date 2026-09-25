# EvoMap (gene loop): from-scratch traced validation runs

Script: `experiments/evomap/validate_evomap.py offline|live [--name <dir>]`. Every run starts from the untouched seed
harness (`katas.seed_harness()`: `prompts/system.md` "You are an expert Python programmer..." + `prompts/task.md`,
id `4ce5cbccd4` with an empty `genes/library.json`), an empty gene store, a fresh run directory and, for live runs, a
fresh LLM cache (`validation/evomap/.cache_<run>`). Each agent directory holds `trace.jsonl` (rsi.trace schema), a
rendered `TRACE.md`, the ledger, the GEP store (`store/`, `store.gepx.tgz`) and `summary.json`.

**Domain layout (`KatasDomain(scheme="audit")`, added for this validation).** Per kata class (boundaries, unicode,
dates, rounding, retry): evolve = kata 0 (5 tasks the agent practises on), val = kata 1 (5 tasks: a consumer's own
quarantine tasks), test = katas 2-3 (10 tasks: the SafeHub's hidden bank), holdout = kata 4 (5 tasks, sealed; read
only by the shadow monitor and the final `transfer_report`). Five katas per class leave no room for more; the
consumer quarantine therefore has 1 in-scope task per gene and runs use `quarantine_min_tasks=1` (the default 3 can
never be met on katas).

**What is traced per cycle** (round = cycle): `round_start` (library, capsules, failed capsules, event chain,
memory-graph counts, personality, rollouts, hub statuses and credits), `analysis` (signals, dedup, plateau, memory
advice, selector scores), `proposal` (the gene tried; for new genes the writer prompt and reply; `diff` = the actual
change of the solver's input), `eval` (every graded rollout incl. quarantine and hub-bank arms), `critic`
(consumer quarantine), `gate` (solidify with all inputs of the keep rule; hub publish verification; quarantine
arithmetic), `decision` (library version before/after, why), `state`, `monitor` (holdout score of every new library
version, never shown to the loop). Trace-only rollouts are excluded from the budget check;
`tests/test_evomap_validation.py` proves trace + monitor leave every decision unchanged.

## Summary

| run | setup | evolve: seed -> library | holdout: seed -> library | spend | wall |
|---|---|---|---|---|---|
| `offline_katas/agent0` | KataSimSolver(0.0) + KataMockProposer(0.7), safe, 12 cycles, publishes to SafeHub (bank ref = KataSimSolver, k=4) | 0.35 -> 0.80 (paired +0.45 [+0.15, +0.80]) | 0.40 -> 0.90 (+0.50 [+0.25, +0.70]) | $0 (142 mock calls) | 2 s |
| `offline_katas/agent1` | second solver KataSimSolver(0.0, other name), **no gene writer**, safe, 8 cycles, same SafeHub: inherits genes via quarantine | 0.35 -> 0.55 (+0.20 [0.00, +0.50]) | 0.25 -> 0.50 (+0.25 [0.00, +0.55]) | $0 (100 mock calls) | (same run) |
| `live_katas` | Claude Haiku 4.5 (`ClaudeCLI("haiku")`, cached) as solver, gene writer and hub reference, safe, 8 cycles, SafeHub (k=2) | 0.90 -> 0.90 (empty library) | 1.00 -> 1.00 | **$0.223** (32 calls; $0.149 of it trace baseline + monitor) | 4.0 min |
| `live_katas_r2` | same, re-run after the two fixes below | 0.80 -> 1.00 (+0.20 [0.00, +0.60]; the gain is on the task the gene was written from) | 1.00 -> 1.00 | **$0.385** (50 calls; $0.207 trace overhead) | 8.0 min |

All scores k=4 (offline) / k=2 (live) trials per task, `rsi.evomap.evaluate_library` -> `rsi.core.transfer_report`
(no genes vs the gene library routed by `GeneRoutedDomain`). Total live spend of this stage: **$0.61**, ~12 min.
Offline numbers restate the kata simulator's knobs (its response to gene text is programmed), so they validate the
plumbing and the decision logic, not the claim that genes help. Live Haiku has **no headroom** on katas (holdout
1.0 without genes), so no live transfer claim can be tested here.

## offline_katas: round by round

**agent0** (gene writer on; `TRACE.md`). Baseline on evolve (k=4): count_in_range 0.0, same_text 0.75,
days_between 0.0, round_half_up 0.25, backoff_delays 0.75 -> S=0.35; shadow holdout 0.40.

1. `days_between`: no gene, hub empty -> scratch solve fails (hand-rolled 365/30-day math passes the 2 public asserts,
   fails the hidden ones). Writer (sees only the public spec, the reply, "public tests: 2/2 passed", "NOT solved")
   returns `gene_datetime_not_manual_math`; retry with the card on a fresh seed solves it; solidify: blast 1 file /
   4 lines, `python smoke_test.py` fails on the stub (W0) and passes on the solution (W1) = discriminative, task solved
   -> kept. Composite 0.95. Holdout 0.40 -> 0.50 (dates family 0.5 -> 1.0).
2. `same_text`: same pattern -> `gene_unicode_normalize_casefold` kept. Holdout 0.60.
3. `count_in_range` -> `gene_inclusive_boundaries` kept. Holdout 0.75.
4. `backoff_delays` -> `gene_capped_backoff` kept. Holdout 0.90.
5. `round_half_up`: writer returns a shallow card (`gene_rounding_generic_288`, "Be careful with rounding problems");
   retry still fails -> `task_check_failed`, rolled back, gene not stored. Correct.
6. `count_in_range`: local selector picks `gene_inclusive_boundaries` and the matching capsule -> solved, streak 2 ->
   publishable (0.95 >= 0.78). SafeHub: static gate ok, discriminative ok, bank (2 in-scope tasks x k=4): with 0.75
   vs without 0.50, U=+0.25, U_LCB=+0.25 < delta=0.498 -> **rejected**, stake kept by the hub.
7. `days_between` with the date gene -> solved, streak 2 -> hub **verified** (U=+0.50, U_LCB=+0.50 >= delta 0.458,
   off-scope R=0).
8. `same_text` with the unicode gene -> not solved (the simulator is stochastic) -> rejected, failure recorded on the
   gene; the event carries no capsule id.
9. `round_half_up`: no rounding gene -> scratch solve succeeds -> safe-mode skip (nothing to solidify).
10. `backoff_delays` -> verified (U=+0.625, U_LCB=+0.50, R=+0.06).
11. `same_text` with the unicode gene -> solved; capsule streak counted as 2 although cycle 8 failed with the same
    gene (see audit A4) -> published -> verified (U=+0.50).
12. `count_in_range` -> solved (streak 3); not re-published (this gene was already submitted once).

**agent1** (no writer; inherits). Baseline 0.35, shadow holdout 0.25.

1. `days_between`: nothing local -> hub search returns agent0's verified date gene in an exploration slot -> staged in
   `external_candidates` (asset_id verified, confidence x0.6) -> quarantine A/B on its own val task `is_leap`
   (k=2): no gene 0.50, gene 1.00, delta (from 1 task x 2 trials) 0.98, dS=+0.50 within band but the RRSI cost rule's
   shaped score is positive (100*0.5 - 15*1.42 > 0) and dS > 0 -> **adopt**; adoption report (outcome 1, proof =
   tasks hash + dS) counted by the hub; promoted locally, solve succeeds, solidified with `source_type=reused`, not
   re-published. Holdout 0.25 -> 0.35.
2. `round_half_up`: no gene, no hub asset, no writer -> failed event (empty validation fails in safe mode).
3. `backoff_delays`: adopts agent0's backoff gene the same way (dS=+0.50) -> solved. Holdout 0.50.
4. `count_in_range` solved without a gene (skip). The boundaries gene was never offered (hub rejected it).
5. `same_text`: hub offers the unicode gene -> quarantine on `count_char`: 1.00 vs 1.00 (ceiling), dS=0, cost +111%
   -> **rejected**; outcome-0 report -> the hub's random spot-check (rho=0.2) measured U_LCB=0.50 >= delta and
   **slashed agent1** (-20 reputation, -stake) for an honest "no gain on my task" report (audit A3). Solved anyway
   without a gene.
6. `count_in_range` skip. 7. `days_between` with the adopted gene (now "local") -> solved and **re-published under
   agent1's name**; the hub's near-duplicate check merged it into agent0's lineage (audit A5).
8. `same_text`: the unicode asset is in the reject memory -> not re-tested (anti p-hacking rule) -> scratch solve ok.

Hub end state: date gene verified (1 counted adoption), backoff verified (1 counted adoption), unicode verified (1
ignored/slashed report), boundaries rejected. Nothing reached `promoted` (needs n_min=2 distinct adopters).

## live_katas (Haiku, first run)

Baseline evolve 0.90 (only `same_text` 1/2), holdout 1.00. Cycles 1, 3-7: solved without a gene -> skipped
(correct). Cycle 2 `same_text`: scratch reply passes 2/3 hidden asserts; Haiku writes a good card
(`gene_unicode_casefold_normalize`: NFKC/NFC + casefold, "AVOID lower()"); retry passes 3/3 hidden; but its validation
`["python solution.py", "pytest -q smoke_test.py"]` failed: **pytest exited 5 (no tests collected)** because the
kata smoke test was a module-level-assert script while the writer prompt explicitly offers `pytest -q
<existing_file>.py`. The keep rule rejected a helpful gene for a tooling reason (audit W1). Cycle 8 `same_text`:
new card recommends `lower()` instead of casefold -> retry fails 2/3 -> correctly rejected. Library empty; evolve and
holdout unchanged.

## live_katas_r2 (Haiku, after fixes W1 and W3)

Baseline evolve 0.80, holdout 1.00. Cycle 2 `same_text` fails without a gene; the writer's `gene_unicode_text_comparison`
(NFKC + casefold) solves it on retry, both `python smoke_test.py` and `pytest -q smoke_test.py` fail on W0 and pass on
W1 (mutation kill rate 0.67) -> kept. Shadow holdout stays 1.00 (ceiling). Cycles 3-7 skipped (solved without a
gene). Cycle 8 `same_text`: local gene selected -> solved -> streak 2 -> published -> the hub bank (Haiku, k=2) had
**1.00 with and without the gene** in scope, delta calibrated to 0.000, U=U_LCB=0 -> **verified** (audit W2; fixed
after the run, and an offline replay of the same bank calls from the run's cache, $0, now rejects it:
"U=+0.000 LCB=+0.000 >= delta=0.000 but LCB <= 0").

## Step-by-step audit: is every step correct?

Verified against the trace numbers (by reading `trace.jsonl` and recomputing where possible):

* **Correct.** Signal extraction (task-class keywords + `task:<class>`), selector (no gene -> `none`; matching gene
  selected with pattern score; non-matching genes score 0), hub-first-when-nothing-local order, scratch -> writer ->
  retry sequence, solidify arithmetic (blast from the workspace diff, discriminative before/after, task threshold
  0.5, composite 0.95 = the spec §4.10 weights with 4 signals), rollback and "gene not stored" on failure, memory
  outcome written immediately with the measured status, streak/eligibility thresholds (0.7 / 0.78 / streak 2 / 5
  files / 200 lines), hub static gate + discriminative check + bank uplift arithmetic (U, U_LCB, delta, R shown in
  the gate), quarantine RRSI arithmetic (recomputed cost-rule shaped score -16.6 for agent1 cycle 5), reject memory,
  no re-publish of a reused asset in its adoption cycle, split discipline (the agent never reads holdout/test; the
  monitor and hub read them through their own `allow_sealed` evaluators), audit ok on every store.
* **W1 (wrong, fixed).** The katas smoke test was not pytest-collectable while the gene-writer prompt allows
  `pytest -q <existing_file>.py`: a correct live gene was rejected (live_katas cycle 2). Fix: `smoke_test.py` now
  defines `test_public()` and runs it under `__main__`; both commands are discriminative
  (`test_katas_smoke_test_is_discriminative_under_python_and_pytest`).
* **W2 (wrong, fixed).** SafeHub verified a gene with zero measured uplift: when every baseline bank trial succeeds
  the calibrated delta is 0 and `U_LCB >= delta` holds at U_LCB = 0. Fix: `UpliftLCB` also requires U_LCB > 0
  (`test_uplift_gate_rejects_zero_uplift_at_zero_delta`). Offline hub decisions are unchanged (their U_LCB = 0.25-0.5).
* **W3 (wrong, fixed; bookkeeping only).** When one LLM object is both solver and writer (every live run), a cycle's
  `tokens` (ledger cost) counted each solve twice (cycle 1 of live_katas_r2: 3,076 recorded for a 1,538-token solve).
  Now only proposer-role tokens are added (`test_cycle_tokens_not_double_counted_with_one_llm`). No decision uses it.
* **A1 (questionable, by design).** The local keep rule's evidence that a gene helps is one retry on a *fresh seed*
  of the task it was written from. Re-sampling alone can flip the outcome (agent0 baseline trials on same_text were
  1,1,1,0), so a shallow card can be kept by luck; in a pre-run dry check a "Be careful with unicode problems" card
  was kept this way. `rsi-taskcheck` (paired gene vs no gene) exists but neither the mock writer nor Haiku chose it.
* **A2 (questionable).** Tiny samples decide sharing: the hub bank has 2 in-scope tasks per class, so the "95%
  lower bound" equals the smaller of two per-task differences (U_LCB = U in 3 of 4 verifications), and delta ~0.46-0.5
  comes from 8 trials; the consumer quarantine uses 1 task x 2 trials (delta 0.98, so any dS > 0 that pays its cost
  passes). These are limits of the 25-kata domain, not of the gates, but no verdict here is statistically strong.
* **A3 (questionable, design flaw in our SafeHub).** The spot-check treats an outcome-0 report as dishonest whenever
  the hub measures U_LCB >= delta, so a consumer that honestly saw no gain (ceiling on its own task) was slashed. A
  report should be judged against its claimed dS and its own noise, not against the hub's in-scope uplift. Not
  changed in this stage (it changes the published X10/X11 hub economics); flagged for review.
* **A4 (faithful to Evolver, surprising).** A failed cycle with the same gene does not break a capsule's success
  streak (failed events carry no capsule id; spec §3.6 "consecutive trailing successes of this capsule id"), so
  agent0 cycle 11 published after a success-fail-success history.
* **A5 (gap).** A gene adopted from the hub later counts as "local"; when it succeeds again the agent publishes it as
  its own (agent1 cycle 7). Only the hub's near-duplicate merge stopped a duplicate asset. Spec §9.2.6 sets
  `source_type="reused"` only for the adoption cycle; Evolver would re-publish too. A provenance check
  (`gene.provenance.kind == "external"` -> do not publish, or publish with `parent`) would close it.
* **A6 (minor).** A vacuity failure is classified as a hard `constraint` failure mode (it goes through the
  constraint-violation path of `classify_failure_mode`); it only affects the selector's history adjustment of a gene
  that was not stored.
* **A7 (minor).** `hub.metrics()["n_promoted"]` counts verified assets (3) while no asset reached `promoted`
  (`n_promoted_strict` 0); the name is misleading.
* **Unverifiable here.** Whether any gene improves an unseen live task (Haiku is at ceiling on holdout); faithful-mode
  behaviour (not run in this stage); distillation (never triggered: needs >= 10 capsules); plateau / drift / bans
  (never triggered in <= 12 cycles).

## Paper / reference alignment

* **Aligned.** Gene form and injection (the paper's "Domain keywords / Summary / Strategy / AVOID" card, one gene per
  task, as the composition finding recommends); Capsule / EvolutionEvent records with a hash-chained event log;
  solidify's keep rule shape (constraints + validation + protocol, §3.6) with the §4.10 composite (0.95 here equals
  the spec's worked example: a clean success scores ~0.94 whatever it does to the task); publish eligibility (§4.13);
  memory-graph advice; capsule chosen by trigger independently of the gene (§3.3); reject-on-asset_id mismatch and
  confidence x0.6 on ingest (§9.2.6.1).
* **Deliberate deviations (safe mode, documented in `evomap-impl.md` §6):** measured outcome instead of transcript
  self-report; empty validation fails; discriminative validation + own graded task required; hub assets quarantined
  and A/B-tested instead of injected as "STRONG REFERENCE"; SafeHub (hidden bank, uplift LCB, adoption-based
  credits) instead of the GDI hub. The trace shows each of these firing as designed.
* **Inconsistencies found in this stage.** (1) The spec's SafeHub rule "accept when U_LCB >= delta" admits zero-uplift
  genes when delta calibrates to 0 (W2; implementation now also requires U_LCB > 0). (2) The spec's spot-check rule
  "a disagreement slashes the reporter" does not define disagreement; our reading slashes honest no-gain reports (A3).
  (3) The spec's quarantine (§9.2.6) and bank (§9.2.3) assume enough tasks per signal class; on katas neither the
  `hub` scheme (1 val kata per class) nor any layout can meet the default `quarantine_min_tasks=3`, so the X11 live
  variant (`--llm claude:haiku`, scheme `hub`) can never adopt anything. (4) Evolver's composite score does not see
  the task outcome at all; in safe mode a cycle rejected for `task_check_failed` still records composite 0.95 in its
  event (status "failed"), which is faithful but makes the event score meaningless as a quality signal.
* **Not reproducible here.** The paper's headline numbers (4,590 trials, CritPt) and the Behind-EvoMap network
  statistics need other benchmarks/data; this stage validates mechanics only.
