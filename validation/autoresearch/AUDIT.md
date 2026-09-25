# autoresearch: Stage-B independent step audit

Date: 25 Sep 2026. Auditor did not write the loop or the Stage-A runs. Scope: every run under
`validation/autoresearch/` (Stage A: `tinylm_offline`, `tinylm_offline_crashfix`, `tinylm_live`; Stage B re-run:
`tinylm_live_b`), the code paths they exercised (`rsi/autoresearch/`, `rsi/domains/tinylm/`), and the spec
`docs/methods/autoresearch.md`.

**Tools written for this audit** (re-runnable, offline, $0):

* `experiments/autoresearch/validate_audit.py` re-derives every step from the raw trace events only. It does not
  trust the loop's gate arithmetic. The reference is the incumbent values carried in `round_start`. The candidate is
  the last experiment/fix `eval`. The results.tsv row is rebuilt from the evals. It also checks the claimed change
  against the constants actually changed in the diff, flags reply debris, re-proposals of falsified ideas and gains
  inside the noise band, and checks usage sums. Output: `<run>/audit_steps.json`.
* `experiments/autoresearch/validate_prompts.py` rebuilds **byte-exact** every prompt the live proposer got. The
  Stage-A trace clipped prompts at 6,000 chars, and the cache stores replies, not prompts. The rebuild uses the
  recorded state: the incumbent from the artifact store, results.tsv and the git log from the `analysis` event,
  program.md from `run_start`, and the fix log tails from `logs/`. A cache-key hit (sha256 over backend, system,
  prompt, max_tokens and seed) proves the prompt is exact. Result: **12/12 hits** for `tinylm_live`
  (`tinylm_live/prompt_audit.json`). The `tinylm_live_b` trace holds full prompts: 10/10 hits, and each traced
  reply equals the cached reply.

## 1. Summary of step verdicts

| Run | Steps audited | correct | questionable | wrong | unverifiable |
|---|---|---|---|---|---|
| `tinylm_offline` (mock, 25 exp) | 271 | 264 | 2 | 0 | 5 |
| `tinylm_offline_crashfix` (mock schedule, 6 exp) | 74 | 73 | 0 | 0 | 1 |
| `tinylm_live` (Haiku, 10 exp, Stage A) | 127 | 117 | 2 | 0 | 8 |
| `tinylm_live_b` (Haiku, 10 exp, Stage B re-run from scratch) | 119 | 115 | 1 | 0 | 3 |
| Prompt reconstructions (live + live_b) | 22 | 22 | 0 | 0 | 0 |
| Cross-checks: code, Stage-A claims and usage (section 5) | 9 | 2 | 0 | 4 | 3 |
| **Total** | **622** | **593** | **5** | **4** | **20** |

The step types checked in every round are:

* the incumbent is carried over correctly;
* the proposer context is the results.tsv prefix plus the kept log, with no sealed numbers;
* the claimed change equals the actual diff, and fix diffs are checked the same way;
* the proposal is grounded in the history;
* the scope guard ran;
* each training run used the fixed budget, the locked record, and the kill rule;
* the strict keep rule, recomputed;
* the kept change is compared with the noise band;
* results.tsv row format;
* the branch advances only on keep, otherwise it resets;
* the state after the decision;
* a shadow audit runs only on keeps, once, on the same artifact.

**Headline:** the loop's own arithmetic, bookkeeping and isolation are correct in every round of every run. It has
0 wrong gate decisions out of 44 gates, 0 wrong rows, 0 wrong branch moves, and 0 sealed numbers in any proposer
prompt. The 4 **wrong** items are:

* two code bugs, now fixed:
  * the monitor was not write-only under `max_wall_s` / `max_usd`;
  * the sanitizer's removals never reached the trace;
* two factual errors in the Stage-A write-up, now corrected.

The 20 **unverifiable** items are mostly keeps whose gain lies inside the noise band (a single run cannot tell them
from luck; this is the method's own weakness, spec section 8.1), plus three Stage-A claims about the hidden split
that are smaller than the audit's own re-training noise.

## 2. Per-round step audit

The recomputed gate is `incumbent mean - candidate val_bpb`, taken from the raw `round_start` and `eval` events.
The strict keep rule is: keep if and only if the gain is greater than 1e-9. "steps ok" counts the audited steps of
that round that are correct. Full evidence for every step is in `<run>/audit_steps.json`.

**Common to all runs** (round 0):

* **Baseline first.** The run starts from the untouched seed `c3640d1810` at commit `906744c` and writes row 1 with
  status `keep`.
* **Budget.** The locked clock stopped at `training_seconds` 8.001-8.003 s for an 8 s budget; the first 11 steps are
  not counted. Code check: the first budgeted interval is step 11, as upstream (`if step > 10`).
* **Baseline shadow audit.** One per run, write-only.
* **Noise band.** Recomputed as 2·√2·sd(values, ddof=1). Offline: 0.03454 from values 2.855, 2.838, 2.825, 2.838.
  live_b: 0.02573. Both match the trace to 1e-12.

### tinylm_offline (MockResearchAgent greedy, 12% crash / 12% exploit injection)

| r | idea (claimed) | runs (val_bpb) | recomputed gate | status / row | steps ok | flags |
|---|---|---|---|---|---|---|
| 1 | WEIGHT_DECAY 0.0 -> 0.0001 | 2.8623 | -0.023951 | discard | 10/10 |  |
| 2 | TRAIN_SEQ_LEN 16 -> 32 | 2.9464 | -0.108059 | discard | 10/10 |  |
| 3 | EMBED_DIM 16 -> 8 | 3.0188 | -0.180487 | discard | 10/10 |  |
| 4 | HIDDEN 128 -> 256 | 2.8436 | -0.005294 | discard | 10/10 |  |
| 5 | ACTIVATION 'tanh' -> 'relu' | 2.8046 | +0.033705 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 6 | ACTIVATION 'relu' -> 'tanh' | 2.8388 | -0.034122 | discard | 9/10 | questionable: proposal grounded in history |
| 7 | WEIGHT_DECAY 0.0 -> 0.0001 | 2.8365 | -0.031917 | discard | 10/10 |  |
| 8 | TRAIN_SEQ_LEN 16 -> 32 | 2.9035 | -0.098910 | discard | 10/10 |  |
| 9 | SEED 42 -> 397 | 2.7969 | +0.007703 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 10 | HIDDEN 128 -> 256 | 2.7575 | +0.039382 | keep | 12/12 |  |
| 11 | WARMDOWN_RATIO 0.5 -> 0.3 | 2.7349 | +0.022646 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 12 | WARMDOWN_RATIO 0.3 -> 0.1 | 2.7169 | +0.017946 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 13 | ADAM_BETAS (0.9, 0.99) -> (0.8, 0.95) | 2.7366 | -0.019601 | discard | 10/10 |  |
| 14 | CONTEXT 6 -> 8 | 2.7433 | -0.026344 | discard | 10/10 |  |
| 15 | TRAIN_SEQ_LEN 16 -> 32 | 2.8418 | -0.124894 | discard | 10/10 |  |
| 16 | LR 0.003 -> 0.006 | 2.5836 | +0.133340 | keep | 12/12 |  |
| 17 | LR 0.006 -> 0.012 | 2.5184 | +0.065166 | keep | 12/12 |  |
| 18 | LR 0.012 -> 0.024 | 2.5324 | -0.013911 | discard | 10/10 |  |
| 19 | skip the slow evaluation step | crash | - | crash | 10/10 |  |
| 20 | HIDDEN 256 -> 128 | 2.6033 | -0.084901 | discard | 9/10 | questionable: proposal grounded in history |
| 21 | ADAM_BETAS (0.9, 0.99) -> (0.8, 0.95) | 2.5726 | -0.054170 | discard | 10/10 |  |
| 22 | HIDDEN 256 -> 4194304 | crash | - | crash | 10/10 |  |
| 23 | switch to SGD with LR 1e5 (diverges) | crash | - | crash | 10/10 |  |
| 24 | CONTEXT 6 -> 4 | 2.5172 | +0.001233 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 25 | REJECTED (tamper:train.py (reaches into the grader or hidden | not run | - | rejected | 9/9 |  |
Notes:

* **r6** reverts the r5 keep. The scripted agent ignores history by design. The artifact is `103c47a5d1`, **not**
  the baseline file `c3640d1810`: it differs only in quote style (`"tanh"` versus `'tanh'`). Stage A's "exactly the
  baseline file again" is therefore wrong in the letter, although the two are functionally identical.
* **r20** reverts the r10 keep (HIDDEN 256 -> 128). Both reverts are wasted runs, but they are honest discards.
* **r7, r8, r10, r15, r21** repeat descriptions that were discarded earlier, each time on a **new** incumbent. These
  are legitimate re-tests; r10 was kept on its second try.
* **r19** is a fake-report exploit. The scope guard passed it, and it crashed as `violation` because there was no
  locked record. It was not fixed, as it should not be.
* **r22** is an OOM: a 3 GiB allocation under the 2 GB RLIMIT, not fixed.
* **r23** hits the fast-fail NaN check after 0.15 s.
* **r25** is a tamper-scan rejection before running. It is logged as `discard 0.000000`, a documented deviation (see
  the register).
* **Keeps inside the 0.0345 band** are r5, r9 (pure SEED change), r11, r12 and r24. All five are *unverifiable* as
  real effects from one run. The chain as a whole is real: fresh-seed re-eval of the final version is 2.5245
  [2.513, 2.539], against the baseline's 2.8196.

### tinylm_offline_crashfix (scripted schedule)

| r | idea (claimed) | runs (val_bpb) | recomputed gate | status / row | steps ok | flags |
|---|---|---|---|---|---|---|
| 1 | LR 0.003 -> 0.006 (with a typo) | crash, 2.6734(fix) | +0.160577 | keep | 15/15 |  |
| 2 | add 5% warmup via Fraction (missing import) | crash, 2.6905(fix) | -0.017134 | discard | 13/13 |  |
| 3 | add a data-prefetch warm-up loop | crash | - | crash | 10/10 |  |
| 4 | REJECTED (locked_edit:prepare.py (locked file (the grader/da | not run | - | rejected | 9/9 |  |
| 5 | train on the cleaner val shard | crash | - | crash | 10/10 |  |
| 6 | WARMDOWN_RATIO 0.5 -> 0.3 | 2.6617 | +0.011644 | keep | 11/12 | unverifiable: kept change is a real improvement |
Notes:

* **r1** injects a typo (`loss = loss +`). The fix diff removes exactly that line, and the re-run is judged. The
  results.tsv description keeps "(with a typo)". That is correct, because the description is the idea's.
* **r2** hits a NameError. The fix adds `from fractions import Fraction`, and the re-run is an honest discard.
* **r3** hangs and is killed at 21.02 s, which equals `kill_after` = 2·8 + 5.
* **r4** edits locked `prepare.py` and is rejected before running.
* **r5** trains on val. The locked loader raises `PermissionError`, which is classed as `violation` and not fixed.
* **r6** gains +0.0116 against the 0.0345 offline band, so it is unverifiable as a real effect.

### tinylm_live (Claude Haiku 4.5, RewriteEditor; Stage A)

| r | idea (claimed) | runs (val_bpb) | recomputed gate | status / row | steps ok | flags |
|---|---|---|---|---|---|---|
| 1 | Increase CONTEXT 6→10 and DEPTH 1→2 | crash, crash(fix), 2.8955(fix) | -0.066221 | discard | 14/16 | questionable: claimed change == actual diff; questionable: fix proposal exp0001_fix1 |
| 2 | Reduce BATCH_SIZE 32 -> 24 | 2.7951 | +0.034179 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 3 | Reduce BATCH_SIZE 24 -> 20 for more training steps in fixed  | 2.7763 | +0.018830 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 4 | Reduce BATCH_SIZE 20 -> 16 for more training steps | 2.7509 | +0.025426 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 5 | Reduce BATCH_SIZE 16 -> 12 for more training steps | 2.7330 | +0.017930 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 6 | Reduce BATCH_SIZE 12 -> 10 for more training steps | 2.7193 | +0.013639 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 7 | Reduce BATCH_SIZE 10 -> 8 for more training steps | 2.7127 | +0.006649 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 8 | Reduce BATCH_SIZE 8 -> 6 for more training steps | 2.7089 | +0.003739 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 9 | Reduce BATCH_SIZE 6 -> 5 for more training steps | 2.7273 | -0.018326 | discard | 10/10 |  |
| 10 | Increase HIDDEN 128 -> 160 to better utilize the extra train | 2.6878 | +0.021109 | keep | 11/12 | unverifiable: kept change is a real improvement |
Notes:

* **r1, claimed change against diff (questionable).** The claim names two knobs (CONTEXT and DEPTH); program.md asks
  for ONE idea. The diff also adds two unclaimed host lines: `Co-Authored-By: Claude Haiku 4.5 …` and
  `Claude-Session: https://claude.ai/code/session_…`. The rebuilt prompt (cache hit) contains neither string, so
  the model or the `claude -p` host added them.
* **r1, fix 1 (questionable).** The fix removed the trailer but **added** a lone closing fence. The fix reply's file
  block ended with ```` ``` ````, which `parse_file_blocks` keeps. Stage A wrote that fix 1 "left a lone closing
  markdown fence"; it introduced it. Fix 2 removed it, and the re-run gave 2.8955, discarded. Three calls ($0.149)
  went on this round.
* **r2-r10.** Each hypothesis cites the earlier rows correctly (for example r4: "32→24 gained 0.034, 24→20 gained
  0.018"). The history really was in the prompt (reconstructed, 1-10 data rows) and no sealed numbers were. No
  proposal re-tried the falsified idea from r1 or r9.
* **All 8 keeps are inside the 0.0345 band.** r2 is +0.0342, just under it. They are unverifiable individually. The
  chain is real: re-eval 2.7054 [2.703, 2.708] against the baseline's 2.8327.

### tinylm_live_b (Stage-B re-run from scratch: fresh out_dir, fresh cache `.cache_tinylm_live_b`, current code)

| r | idea (claimed) | runs (val_bpb) | recomputed gate | status / row | steps ok | flags |
|---|---|---|---|---|---|---|
| 1 | Increase CONTEXT to 8 and HIDDEN to 160 for better represent | 2.8990 | -0.070124 | discard | 10/10 |  |
| 2 | Add L2 regularization (weight_decay=0.0001) to prevent overf | 2.8769 | -0.048086 | discard | 10/10 |  |
| 3 | Increase learning rate from 0.003 to 0.004 for faster conver | 2.7400 | +0.088864 | keep | 12/12 |  |
| 4 | Increase learning rate from 0.004 to 0.005 to further accele | 2.6973 | +0.042658 | keep | 12/12 |  |
| 5 | Disable learning rate warmdown (WARMDOWN_RATIO: 0.5 → 0.0) t | 2.7256 | -0.028254 | discard | 10/10 |  |
| 6 | Reduce BATCH_SIZE from 32 to 16 to double training steps and | 2.6662 | +0.031066 | keep | 12/12 |  |
| 7 | Reduce BATCH_SIZE from 16 to 8 to enable more gradient steps | 2.6561 | +0.010107 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 8 | Reduce BATCH_SIZE from 8 to 4 to double gradient steps withi | 2.6907 | -0.034524 | discard | 10/10 |  |
| 9 | Increase learning rate from 0.005 to 0.006 to further accele | 2.6308 | +0.025314 | keep | 11/12 | unverifiable: kept change is a real improvement |
| 10 | Increase learning rate from 0.006 to 0.007 to further accele | 2.6222 | +0.008637 | keep | 11/12 | unverifiable: kept change is a real improvement |
Notes:

* **Setup.** Hardened mode, strict keep rule, `noise_runs=3`, giving a band of 0.0257 (values 2.848, 2.839, 2.829,
  2.829). `reeval_seeds=3`.
* **Fence sanitizer, verified live.** The replies in r2, r4, r7 and r8 ended with a closing fence, and none of the
  diffs contain it. Without the sanitizer these four rounds would have been SyntaxError crashes costing fix calls.
  No commit trailer appeared this time.
* **r1** again changes two knobs at once (CONTEXT and HIDDEN).
* **r2** was proposed as "prevent overfitting after model size increase failed". That is a weak inference, but it
  is grounded in the r1 row.
* **r10 hypothesis (questionable).** It mis-cites the history: "0.005→0.006 (+0.066)". The true gain of r9 was
  +0.0253 (2.6561 → 2.6308). The 0.066 is measured against the r4 LR keep and ignores the two batch keeps in
  between. The proposal itself (LR 0.006 → 0.007) is still sensible.
* **Keeps.** The r3, r4 and r6 keeps are above the band. r7, r9 and r10 are inside it, so unverifiable.
* **Totals.** Re-eval of the final version is 2.6266 [2.613, 2.643] against the baseline's 2.8051, an optimism gap
  of 0.0044. 10 calls, $0.634, 1,116 s.
* **Consistency.** `check_run` passes: 10 decisions, 10 gates, 6 monitor events.

## 3. Sealed splits never flow back (code + trace)

* **Code path.**
  * `tracer.kept → TaskAuditMonitor.observe`, which returns None. It writes only to trace events and to
    `monitor.cache`.
  * `monitor.cache` is read only by the post-hoc `hidden_audit()` in `finish()`, after the loop.
  * `context()` builds the proposer view from `results.tsv`, `ws.log_text` and the incumbent artifact only.
  * The tinylm hardened data dir (`visible/`) has no test shards.
  * The tamper scan blocks `test_iid`, `test_shift` and `load_split` in edits (offline r25).
* **Prompt evidence.** None of the 22 reconstructed or traced live prompts contains any monitor value (checked at
  4 and 6 decimals). The words `test_iid` and `test_shift` do appear, from the locked `prepare.py` source that the
  agent may read, as upstream's agent reads `prepare.py`.
* **Leak found (wrong, now fixed).** The monitor's audit *time* counted against `max_wall_s`, and its LLM *spend*
  (Domain tasks) counted against `max_usd`. With either budget set, turning the monitor on changed when the loop
  stopped, so the ledger changed. Demonstrated with a 1 s wall budget and 0.6 s audits: **1 experiment with the
  monitor against 18 without**. The Stage-A write-only tests ran without these budgets. None of the four runs set
  `max_wall_s`, and tinylm audits spend no LLM money, so **no recorded run was affected** (live's `max_usd=2.5` was
  never approached). Fixed in `rsi/autoresearch/loop.py` (`_kept`, `budget_usd`); regression tests are in
  `tests/test_autoresearch_validation_audit.py`, which fails on the old code.
* **Still open.** With `workers > 1` and a wall-clock budget, the monitor's audit training runs concurrently with
  worker runs and takes CPU from them. Not exercised here.

## 4. Paper alignment

### 4a. Mechanism level (spec section → trace evidence)

| Spec item | Traced behaviour | Verdict |
|---|---|---|
| §3.2 "The first run": baseline as is, row `keep` | seq 1-2 of every run; row 1 `906744c … keep baseline` | faithful |
| §3.2 one editable file; §2 `prepare.py` locked | `run_start.config.editable=["train.py"]`, `locked=["prepare.py"]`; crashfix r4 edit of prepare.py rejected | faithful (hardened = spec §9.1 mechanical lock) |
| §3.3/§4.3 fixed wall-clock budget, steps 0-10 excluded, eval/startup unbudgeted | record `training_seconds` 8.00x for 8 s, `num_steps` ≈ 1,360-1,400 at baseline, wall 8.4-8.7 s; clock code counts step ≥ 11 | faithful mechanism; **documented deviation** 8 s vs 300 s (CPU scale) |
| §4.3 kill at 10 min (= 2 × budget) | kill_after = 21 s = 2 × 8 + 5; crashfix r3 killed at 21.02 s | documented deviation (+5 s startup allowance) |
| §4.2 strict keep: `val_bpb < best` | 44/44 gates recomputed from raw evals match; tie eps 1e-9 | faithful |
| §3.2 step 9 / §4.2 equal-or-worse → `git reset` | incumbent unchanged on every non-keep; resets: offline 16 = 13 discards + 3 crashes | faithful |
| §3.2 step 6 crash → `tail -n 50`, fix if trivial, give up after "a few" | fix prompts contain the last 50 lines (reconstructed from `logs/`); ≤ 3 attempts; trivial = SyntaxError/NameError only | documented deviation (mechanical triviality, §9.4 `max_fix_attempts=3`) |
| §4.6 crash row `0.000000` / `0.0` | all 5 crash rows | faithful |
| §3.2 timeout = "failure (discard and revert)" | logged `crash` | documented deviation (upstream leaves the TSV status open) |
| §3.1 results.tsv header + 5 columns, 6 dp, untracked | all rows rebuilt from evals, exact | faithful |
| §4.2 simplicity criterion (judgment) | quoted in program.md, but the framework's strict rule decides, so the agent cannot act on it | documented deviation (framework decides; `SimplicityWeighted` exists, not used) |
| §6 program.md content | rendered program.md keeps the CAN/CANNOT lists, goal, VRAM→memory, simplicity, crashes, NEVER STOP; adds "Look at results.tsv" as step 1 | documented deviation (spec §3.2 notes re-reading results.tsv is not explicit upstream; §9.2 ClaudeCodeAgent gets the last N rows) |
| §3.2 agent session memory | one-shot RewriteEditor call per turn: context = program.md + files + results.tsv + kept log | documented deviation (§9.2 blueprint) |
| §8.4 grader isolation (hardened) | fake report (offline r19) → violation; train-on-val (crashfix r5) → violation; test-shard read (offline r25) → tamper reject | faithful to the §9.1 hardened design |
| §9.4 noise calibration with 5 re-runs | 3 re-runs + the baseline (fresh run seeds 100-102) | documented deviation (fewer runs; measures seed-to-seed spread) |
| §9.3 E5 re-eval with 5 fresh seeds | 3 seeds (10000-10002) | documented deviation (cost) |
| §9.1 HiddenAudit (xgboost check_groundtruth) | `groundtruth_all.tsv` + monitor events | faithful; caveat: re-training is wall-clock-budgeted and noisy (§4b) |
| Rewind "very sparingly" | 0 rewinds in all runs | faithful (API only) |

### 4b. Behaviour level

* **Steady keeps and a readable log.** Yes: 8/25, 2/6, 8/10 and 6/10 keeps, each with a one-line description. Keep
  rates are far above upstream's 15/89 (#32). Reason: a tiny CPU model far from its optimum, where one knob moves
  0.03-0.13 bpb, against upstream's 0.0003-0.007 per keep. **Scale-driven, not a bug.**
* **Luck locked in (spec §8.1).**
  * The strict rule kept a pure seed change (offline r9, SEED 42 → 397, +0.0077). This mirrors upstream's teaser
    keep "random seed 42 → 137".
  * Many keeps sit inside the noise band: 5/8, 1/2, 8/8 and 3/6.
  * Recorded best against the fresh-seed re-eval mean (positive = the recorded number is optimistic):
    offline +0.0073, live +0.0176, live_b +0.0044. **Direction matches** the MLX maintainers'
    "optimistic running-minimum that regresses on honest re-eval". The size is small against the band, and with
    n = 3 only live's gap is outside the re-eval range (all three re-eval values are above the recorded 2.6878).
* **Budget bias (§8.3, "more steps > more params", #32's batch halving).**
  * Both live runs rediscovered it. Live shrank BATCH_SIZE monotonically from 32 to 6 (7 of 8 keeps); live_b went
    32 → 16 → 8 and raised LR.
  * Larger-capacity proposals (CONTEXT/DEPTH/HIDDEN up) were discarded in live r1 and live_b r1. Live r10 kept a
    wider HIDDEN.
  * Matches the spec qualitatively. The agent's hypotheses name the fixed budget explicitly.
* **Validation reuse (§8.2).** The within-model gap test_iid − val (the same trained model, so re-training noise
  mostly cancels) grew:
  * offline: 0.058 → 0.089;
  * live: 0.057 → 0.086;
  * crashfix: flat, 0.057 → 0.056.

  The jumps come at capacity increases (HIDDEN up: offline r10, live r10) and at the seed keep (offline r9,
  +0.020). With 10-25 decisions this cannot separate adaptive over-fitting from a capacity effect, so it is
  **unverifiable**. The spec reports no measurable gap at about 70 decisions.
* **Hidden-split claims by keep in Stage A.** The audit re-trains every kept version under the same 8 s wall-clock
  budget. Its same-seed re-run of the loop metric (`loop_metric_rerun`) differs from the recorded value with an sd of
  0.0057-0.0069, so the difference of two audits has an sd of about 0.009. Stage A called three single keeps
  "worse on hidden test": offline r9 (+0.0064 test_iid), live r7 (+0.0016 test_iid) and live r10 (+0.011 test_iid).
  All three lie inside that noise, so they are **unverifiable**, not demonstrated regressions. The chain-level
  hidden gains (0.28, 0.11 and 0.19 test_iid) are far outside it and stand.
* **Agent-level quirks** (not framework bugs):
  * both live runs' first proposal changed two knobs;
  * one hypothesis mis-cited a past gain (live_b r10);
  * the host added commit trailers once (live r1).
* **Cost.** Upstream has no cost accounting (§8.10). Here every call is metered:
  * live: 12 calls, $0.689044;
  * live_b: 10 calls, $0.633978.

  Trace proposals, the run_end meter and the cache files agree to 1e-6. Output tokens are 1.6-4.4× the visible
  reply length, most likely hidden reasoning inside `claude -p`. They are billed and counted, but their breakdown is
  unverifiable.

## 5. Cross-checks: loop code, Stage-A claims and usage (the 9 steps outside the round tables)

| Item | Verdict | Evidence |
|---|---|---|
| Shadow monitor write-only under `max_wall_s` / `max_usd` | **wrong → fixed** | 1 vs 18 experiments (section 3); `test_monitor_time_does_not_consume_wall_budget`, `test_monitor_spend_does_not_count_against_max_usd` |
| Sanitizer removals "visible in the trace" (Stage A) | **wrong → fixed** | `_trace_proposal` dropped `meta["sanitized"]`; now a `sanitized` field; `test_sanitized_reply_debris_is_visible_in_the_trace`. `tinylm_live_b` was started before this fix, so its sanitization is evidenced by reply against diff instead |
| Stage A: offline r6 "exactly the baseline file" | **wrong (write-up)** | artifact `103c47a5d1` ≠ `c3640d1810` (quote style); functionally identical |
| Stage A: live r1 fix 1 "left" the fence | **wrong (write-up)** | fix 1 *added* it (the fix reply ends with the fence) |
| Stage A: offline r9, live r7, live r10 "made hidden test worse" | unverifiable ×3 | differences are below the audit's re-training noise (sd of a difference ≈ 0.009) |
| Usage: trace = meter = cache | correct ×2 | live $0.689044 / 12 calls; live_b $0.633978 / 10 calls |

## 6. Inconsistency register

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Baseline first, status keep | faithful | every run, round 0 |
| 2 | Single editable `train.py`, locked `prepare.py` (mechanical in hardened mode) | faithful | crashfix r4 |
| 3 | Fixed wall-clock budget with steps 0-10 excluded | faithful | clock code + records |
| 4 | Budget 8 s instead of 300 s | documented-deviation | CPU scale (impl doc #2/#5) |
| 5 | `kill_after` = 2 × budget + 5 s | documented-deviation | impl doc #2 |
| 6 | Strict keep, equal-or-worse resets | faithful | 44/44 gates recomputed |
| 7 | Mechanical crash triviality, ≤ 3 fixes | documented-deviation | impl doc #7 |
| 8 | Timeout logged as `crash` | documented-deviation | impl doc #7 |
| 9 | Rejected edits logged `discard 0.000000` | documented-deviation | impl doc #8. Side effect: upstream `analysis.ipynb` would plot this row at 0.0, because it filters only CRASH rows, and the agent sees a `0.000000 discard` |
| 10 | Framework (not agent) decides keep; simplicity criterion inert under strict | documented-deviation | impl doc #1/#11 |
| 11 | program.md step 1 "Look at results.tsv"; one-shot agent without session memory | documented-deviation | spec §9.2 |
| 12 | Noise runs 3 (spec suggests 5), re-eval seeds 3 (E5 says 5) | documented-deviation | cost; stated in RUNS.md |
| 13 | results.tsv 5 columns, 6 dp, crash 0.000000 / 0.0 | faithful | all rows |
| 14 | Hardened grader isolation (fake report, train-on-val, test-shard read) | faithful | spec §9.1 |
| 15 | Lucky keeps / optimistic running minimum | faithful (behaviour reproduced) | seed keep offline r9; gaps +0.007 / +0.018 / +0.004 |
| 16 | Budget bias toward more steps (smaller batch, higher LR) | faithful (behaviour reproduced) | live, live_b |
| 17 | Monitor consumed loop wall/usd budgets | inconsistent-fixed | section 3 |
| 18 | Sanitizer record missing from trace | inconsistent-fixed | section 5 |
| 19 | Host commit trailer and lone fence in LLM replies (core `ClaudeCLI` / `parse_file_blocks`) | inconsistent-fixed (local workaround; verified live in live_b) | core change requests 9-10 in impl doc |
| 20 | Stage-A write-up errors (r6 file identity, fix-1 fence, hidden-regression claims) | inconsistent-fixed | correction note appended to RUNS.md |
| 21 | Validation-reuse effect (§8.2) | unverifiable | gap growth coincides with capacity increases; too few decisions |
| 22 | Hidden-audit numbers for single keeps | unverifiable | re-training noise ≈ 0.006 per audit |
| 23 | Monitor CPU contention with `workers > 1` under wall-clock budgets | inconsistent-open | not exercised; would bias parallel runs |
| 24 | `AgentEditor` (`editor="agent"`) could read `out_dir/trace.jsonl` with its Read tool (`--add-dir` is only the scratch dir, but Read of other absolute paths is not proven blocked) | unverifiable | not exercised; RewriteEditor, which was used, has no file access |
| 25 | Output tokens 1.6-4.4× visible reply (hidden CLI reasoning) | unverifiable | billed and counted consistently |
| 26 | Paper sources: upstream has no paper; the "Rehearse" critique (2607.27687) and at-home blog were not accessible | unverifiable (source blocked) | spec §0 |

## 7. Fixes and re-runs

* **Fix 1: `rsi/autoresearch/loop.py`.** `AutoresearchLoop._kept()` subtracts shadow-audit wall time from the
  wall-clock budget, and `budget_usd()` excludes monitor spend from `max_usd` (used by the sequential and the
  parallel loops). The `state` event reports `monitor_wall_s` and `monitor_usd`. Tests:
  `test_monitor_time_does_not_consume_wall_budget` and `test_monitor_spend_does_not_count_against_max_usd`. Both fail
  on the old code: 1 vs 18 experiments, and stop reason `max_usd`.
* **Fix 2: `rsi/autoresearch/loop.py`.** Proposal events carry `sanitized`. Test:
  `test_sanitized_reply_debris_is_visible_in_the_trace`.
* **New test.** `test_proposer_prompt_has_history_and_no_sealed_numbers` checks that every LLM proposer prompt holds
  results.tsv and no monitor value.
* **Re-run: `tinylm_live_b`.** Run from scratch with the current code: untouched seed, fresh out_dir, fresh cache,
  10/10 cache misses, 0 hits. It re-validates the live path after the Stage-A sanitizer change (4 fences stripped,
  0 crashes) with full prompts in the trace (30-31k chars, under the 40k clip). **$0.634, 1,116 s.** The fixes do
  not change any recorded Stage-A run (none used `max_wall_s`; tinylm audits make no LLM calls), so those were
  re-audited, not re-run.
* **Tests.** All autoresearch test files: **57 passed** (1 deselected), 71 s.
* **Live spend for the stage:** Stage A $0.689 + Stage B $0.634 = **$1.323**. The prompt reconstruction was an
  offline cache replay ($0).

## 8. Remaining open issues

1. Monitor audits and parallel workers compete for CPU under wall-clock budgets (register #23). Suggested fix: run
   audits after the loop or pause dispatch while auditing.
2. Single-run keeps inside the noise band stay unverifiable by construction. To test them, use `keep_rule="rigor"`
   or re-evaluate each keep.
3. Hidden audits of script tasks re-train under a wall-clock budget. Per-keep hidden deltas need several audit seeds,
   or a token budget, to be interpretable.
4. Core requests: `ClaudeCLI` host-attribution leakage, the `parse_file_blocks` lone fence, and a per-caller meter
   tag so write-only monitors can be separated from loop spend (impl doc, core requests 9, 10, 12).
