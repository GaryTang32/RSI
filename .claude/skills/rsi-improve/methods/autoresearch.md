# Autoresearch: keep-if-better on one locked metric, one edit at a time

Method reference for `rsi.improve(..., method="autoresearch")` (alias `karpathy`). **There is no method paper.** The primary
source is Karpathy's repo `karpathy/autoresearch` (first commit `b11d6f2`, 6 Mar 2026; master HEAD `228791fb`, re-checked
25 Oct 2026): `program.md` (114 lines), `README.md`, `prepare.py`, `train.py`, cited below as [program.md:line], [README:line],
[prepare.py:line], [train.py:line]. arXiv 2607.27687 (Ji & Ding, "Rehearse: Stepping Back from the Confidence Cliff in
Self-Improving Autoresearch", Jul 2026) is a **third-party study** of autoresearch-style loops, not the method's paper; it
cites the repo at `228791fb` and is cited here as [Rehearse §n]. Secondary: our spec `docs/methods/autoresearch/paper-spec.md`
([spec n]; its tags `[code:mlx/...]`, `[code:xgb/...]`, `[code:home/...]` are community ports, `[gh-disc:#32|#43]` agent
session reports, `[nanochat:LEADERBOARD]`, `[inferred]`), `implementation.md`, `claims-audit.md`, and the code in
`rsi/autoresearch/` for "this package". The discussions #32/#43 could not be fetched for this check; their numbers are as
the spec reports them. Upstream has no formulas beyond the metric and no numeric loop hyperparameters; anything else is
"not specified upstream". Upstream results are single anecdotal runs [spec 7].

## 1. In one paragraph

A coding agent is given a small real training setup with three files that matter: `prepare.py` (fixed constants, data prep,
tokenizer, dataloader, evaluation; "Not modified"), `train.py` (GPT model, Muon + AdamW optimizer, training loop; "the single
file the agent edits") and `program.md` (the agent's instructions; "edited and iterated on by the human") [README:13-15].
Every experiment trains for a **fixed 5-minute wall-clock budget, excluding startup/compilation** (`TIME_BUDGET = 300`)
[program.md:23; prepare.py:31] and is judged on one number, validation bits per byte
`val_bpb = sum(l*m) / (ln 2 * sum(b))` over target tokens (l = per-token cross-entropy in nats, b = the token's byte length,
m = [b > 0]; lower is better, vocabulary-size-independent) [prepare.py:344-365 `evaluate_bpb`; README:17]. "If val_bpb
improved (lower), you 'advance' the branch, keeping the git commit"; "If val_bpb is equal or worse, you git reset back to
where you started" [program.md:103-104], tempered by a judgment-based simplicity criterion and a VRAM soft constraint
[program.md:35-37]. The loop runs "FOREVER" [program.md:94, 112]; at ~5 min per experiment that is "approx 12/hour, for a
total of about 100" overnight [program.md:114; README:64]. What it optimises is `train.py` against one metric on one pinned
validation shard, as one greedy chain on one branch; the strategy changes only when a human edits `program.md` [README:7].

## 2. Inputs and setup

Upstream setup, done by the agent "with the user" [program.md:7-19]:
1. agree on a run tag (e.g. `mar5`); the branch `autoresearch/<tag>` "must not already exist";
2. `git checkout -b autoresearch/<tag>` from master;
3. read the in-scope files `README.md`, `prepare.py` ("Do not modify"), `train.py` ("the file you modify");
4. verify that `~/.cache/autoresearch/` has data shards and a tokenizer, else ask the human to run `uv run prepare.py`;
5. create `results.tsv` with only the header row;
6. "Confirm and go"; after confirmation the experimentation starts.

Rules [program.md:25-37]: edit only `train.py` ("Everything is fair game"); do not modify `prepare.py`, the `evaluate_bpb`
harness ("the ground truth metric"), or install packages beyond `pyproject.toml`. The goal is "the lowest val_bpb"; the code
must run without crashing and "finish within the time budget". The lock on `prepare.py` is an instruction only.
Requirements: one NVIDIA GPU (tested on H100), the agent is started in the repo "(and disable all permissions)" [README:23, 44].

**The baseline** [program.md:39, 84]: "Your very first run should always be to establish the baseline, so you will run the
training script as is"; it is logged as a `keep` row with description `baseline`. Upstream measures no noise and repeats
nothing.

What you need for this package (rsi choices): an editable file set and a locked file set (`ScriptResearchTask`, or any
`rsi.core.Domain` wrapped as `DomainResearchTask`); a metric and direction computed by locked code; a fixed per-run budget
(`RunBudget`); one LLM role, the researcher (`llm_propose`); a `program.md` (`ProgramSpec` preset `upstream|xgboost|simplify`
or a file path); a stopping budget. Strongly recommended, and absent upstream: a hidden split (script tasks `task.audit`,
Domain tasks `holdout`/`ood`) [spec 8.2]. Setup differences from upstream are in section 6.

## 3. Round by round (upstream)

One round = one experiment, run entirely by the agent with a shell and git, on branch `autoresearch/<tag>`
[program.md:90-104]. "LOOP FOREVER":

1. **Look at the git state**: the current branch/commit (the incumbent = the branch tip). It is not re-run.
2. **Edit**: "Tune `train.py` with an experimental idea by directly hacking the code" (one idea per experiment).
3. **`git commit`**.
4. **Run**: `uv run train.py > run.log 2>&1` ("do NOT use tee or let output flood your context").
5. **Read the result**: `grep "^val_bpb:\|^peak_vram_mb:" run.log`.
6. **Crash** if the grep output is empty: `tail -n 50 run.log`, attempt a fix; "If you can't get things to work after more
   than a few attempts, give up." [program.md:101]. Judgment [program.md:110]: "something dumb and easy to fix (e.g. a typo,
   a missing import), fix it and re-run"; "If the idea itself is fundamentally broken, just skip it, log 'crash' ... and
   move on." Crash rows use `0.000000` and `0.0` [program.md:75-76].
7. **Log** the row to `results.tsv` (tab-separated; `commit` (7 chars), `val_bpb` (6 decimals), `memory_gb`
   (`peak_vram_mb/1024`, .1f), `status` (`keep|discard|crash`), `description`); the file stays **untracked** by git
   [program.md:66-79, 102].
8. **Keep**: if val_bpb improved (lower), advance the branch (keep the commit) [program.md:103].
9. **Discard**: if equal or worse, `git reset` back to where you started [program.md:104].

**Keep rule exactly as upstream states it.** Mechanical part: strictly lower `val_bpb` than "where you started" (the branch
tip; upstream never names the comparison point explicitly), one run each, no noise margin. Judgment parts, left to the agent:
- **Simplicity criterion** [program.md:37]: "weigh the complexity cost against the improvement magnitude. A 0.001 val_bpb
  improvement that adds 20 lines of hacky code? Probably not worth it. A 0.001 val_bpb improvement from deleting code?
  Definitely keep. An improvement of ~0 but much simpler code? Keep." (So an equal result can be kept if it simplifies,
  contrary to the literal step 9.)
- **VRAM soft constraint** [program.md:35]: "Some increase is acceptable for meaningful val_bpb gains, but it should not blow
  up dramatically." No thresholds are given.

**Timeout** [program.md:108]: each experiment "should take ~5 minutes total"; "If a run exceeds 10 minutes, kill it and treat
it as a failure (discard and revert)". The agent enforces this itself.
**Rewind** [program.md:106]: "you can rewind but you should probably do this very very sparingly (if ever)".

```text
setup:  tag, branch autoresearch/<tag>, read in-scope files, check data, results.tsv header, confirm
        run train.py unmodified -> row "baseline keep"
loop forever (until the human interrupts):
    look at git state (tip)
    edit train.py with one idea; git commit
    uv run train.py > run.log 2>&1                                 # 5 min training + startup/eval; kill at 10 min
    grep "^val_bpb:\|^peak_vram_mb:" run.log
    empty -> tail -n 50 run.log, fix + re-run if easy, give up after "more than a few" attempts -> crash
    append results.tsv row (untracked)
    lower val_bpb (weighed against complexity and VRAM) -> keep commit; else git reset to the tip
```

**What changes across rounds.** Only the branch tip and the history the agent has produced (commits, `results.tsv`, its own
context). No annealing, no frontier, no population, no automatic strategy change. What the agent reads each round is not
specified beyond the git state and the grep/tail output; reading `results.tsv` is not an explicit step [spec 3.2, inferred].
The validation shard is the same for every decision [prepare.py:43]. Parallelism upstream: the deleted `spawn.sh` ran N
independent chains, one branch and GPU each, no sharing [spec 8.6; `b11d6f2:spawn.sh`].

**Stopping rule.** Never: "do NOT pause to ask the human if you should continue ... The loop runs until the human interrupts
you, period." Out of ideas -> "think harder — read papers referenced in the code, re-read the in-scope files for new angles,
try combining previous near-misses, try more radical architectural changes" [program.md:112]. The initial commit's
`spawn.sh` watcher typed "Keep going. Do not stop" into idle agents every 120 s (`nudge_interval=120`) [`b11d6f2:spawn.sh`].

## 4. Sample by sample (upstream)

There is no per-task inner loop: the unit is **one training run = one scalar**.
- **Trials**: one run per experiment and per baseline. `train.py` pins `torch.manual_seed(42)` and `torch.cuda.manual_seed(42)`
  [train.py:458-459]; the seed is in editable code, so a seed edit is itself a legal experiment (kept once, 42->137,
  -0.000439 in [gh-disc:#32]; "Seed 137 also didn't help here (+0.0007)" in [gh-disc:#43], both via spec 7.1).
- **Recorded per run**: the summary block `val_bpb, training_seconds, total_seconds, peak_vram_mb, mfu_percent, total_tokens_M,
  num_steps, num_params_M, depth` [program.md:45-56; train.py:622-630] in `run.log`, and one `results.tsv` row with
  `memory_gb = round(peak_vram_mb/1024, 1)`. Agent tokens/cost are not tracked [spec 8.10].
- **Aggregation**: none; the round's score is the single value. The incumbent is never re-measured, so later candidates face
  the incumbent's one lucky-or-not draw (and, since the tip only advances on improvement, the running minimum).
- **Inside one run** [train.py]: the training clock counts only steps after the first 11 (`if step > 10: total_training_time
  += dt`) [train.py:578], and the loop stops at `step > 10 and total_training_time >= TIME_BUDGET` [train.py:603]; LR, weight
  decay and warmdown are functions of `progress = training_time / TIME_BUDGET` [train.py:516-532, 555]; NaN or train loss
  > 100 prints `FAIL` and exits [train.py:570-572]; then `evaluate_bpb(model, tokenizer, DEVICE_BATCH_SIZE)` runs
  [train.py:613]. Startup, compilation and evaluation are outside the budget.
- **Evaluation** [prepare.py:344-365]: `EVAL_TOKENS = 40 * 524288` [prepare.py:32] over `EVAL_TOKENS // (batch_size *
  MAX_SEQ_LEN)` batches (floor division, with the agent-editable `DEVICE_BATCH_SIZE`) from a fresh deterministic val loader;
  the loss comes from the agent-owned `model.forward(x, y, reduction='none')`.
- **Data use**: training uses shards `0..N-1` of `climbmix-400b-shuffle`; validation is the **pinned shard `shard_06542`**
  (`VAL_SHARD = MAX_SHARD`), excluded from training and tokenizer training [prepare.py:41-44, 126-127, 254-261]. Locked (by
  instruction): `prepare.py` (constants `MAX_SEQ_LEN = 2048`, `TIME_BUDGET`, `EVAL_TOKENS`, `VOCAB_SIZE = 8192`, data,
  tokenizer, dataloader, `evaluate_bpb`) and dependencies. **Hidden: nothing** - there is no test split [spec 8.2].

## 5. Upstream defaults vs this package

"Upstream" = the repo at `228791fb`. Task-level settings live on the `ResearchTask`, loop settings in `rsi.autoresearch.Config`.

| setting | upstream value [source] | rsi field = default | note |
|---|---|---|---|
| experiments | "LOOP FOREVER"; ~12/h, ~100/night [program.md:94,114] | `max_experiments = 100` | rsi counts agent turns after the baseline |
| per-run budget | 300 s training wall clock [prepare.py:31] | `RunBudget(kind="wallclock", amount=25.0)` | rsi choice; set it on the task |
| warm-up steps excluded | 11 (`step > 10`) [train.py:578,603] | `RunBudget.warmup_excluded_steps = 11` | |
| kill rule | > 10 min -> kill, "failure (discard and revert)" [program.md:108] | `kill_after=None` -> `2*amount+5 s` (wallclock/ceiling), 600 s (tokens); Domain 1800 s, soft | rsi logs a timeout as `crash` |
| keep rule | lower val_bpb + simplicity + VRAM judgment [program.md:35-37,103-104] | `keep_rule="upstream"` (`eps=0.001, lines_per_eps=20, mem_tol=0.10, mem_blowup=2.0`) | thresholds are rsi choices; only the 0.001/20-line example is upstream's |
| bare strict | lower = keep, equal or worse = reset [program.md:103-104] | `keep_rule="strict"` | |
| noise-aware gate | none upstream; MLX port `rigor.py`: 3 runs, P >= 0.95, 20,000 resamples, rng 1234, early reject [code:mlx/rigor.py] | `keep_rule="rigor"`: `repeats=3, confidence=0.95, reps=20000, vary_seed=True` | opt-in; ~25% fewer ideas at equal compute (E5) |
| noise measurement | none upstream; 5 re-runs suggested [spec 9.4, inferred] | `noise_runs = 0` | only gate rules (`rrsi`, core gates) use delta |
| crash fixes | fix if easy, give up "after more than a few attempts" [program.md:101,110] | `max_fix_attempts=3`, `crash_fix="agent"` | 3 is rsi's reading (spec 9.4) |
| seed | `train.py` pins 42 [train.py:458] | `run_seed = 0` | framework-owned, passed to every run |
| confirm and go | setup step 6, before the baseline [program.md:16-19] | `confirm = None` | rsi: opt-in, called after the baseline |
| NEVER STOP | prompt rule [program.md:112]; `spawn.sh` nudge every 120 s [b11d6f2] | `max_consecutive_invalid = None` | the loop owns the turns; "think harder" note after empty turns |
| agent history | not specified upstream | `history_rows = None` (whole `results.tsv`) | |
| grader lock | instruction only [program.md:29-31] | `mode = "hardened"` (alt. `"faithful"`) | see 6 |
| hidden audit / re-eval | none | `hidden_audit=True`, `reeval_seeds=0` | |
| validation reuse | one pinned shard for every decision [prepare.py:43] | `val_resample_every = None` | ablation only |
| chains | one; deleted `spawn.sh`: N independent | `workers = 1`; `executor="local"\|"slurm"` (fake SLURM) | |
| strategy | human edits `program.md` [README:7] | `program="upstream"` or a path | a path is re-read every experiment |
| researcher | "Claude/Codex or whatever you want" with permissions disabled [README:44] | `llm_propose`, `editor="rewrite"` or `"agent"` | |

## 6. Where our implementation differs

From `implementation.md` "Deviations", `claims-audit.md` and `rsi/autoresearch/loop.py`, `keep.py`, `task.py`:
- **The framework runs the loop.** Upstream's agent runs git, training, grep and logging and is the keep/discard judge. Here
  the agent only proposes; `AutoresearchLoop` commits, runs, parses, decides and logs. A round in rsi:
  ```text
  ctx = program.md (re-read if a path), tip files, whole results.tsv, kept-commit log, best value, "think harder" note after empty turns
  cand = agent.propose(ctx)        # invalid / identical reply -> retry <= max_propose_attempts=3; may reply REWIND <commit>
  hardened: ScopeGuard(inc, cand) -> "REJECTED (...)" discard row, metric 0.000000, never run (counts as an experiment)
  rigor only: exact repeat of a scored file -> skipped, turn used (never_repeat)
  commit; run(seed=run_seed) under a hard watchdog, log to logs/<name>.log
  crash -> agent sees tail -n 50, returns a fix or GIVE_UP (<= 3; timeouts and violations never fixed) -> crash row, reset
  rigor: first run no better than the best kept mean -> early discard, else 2 more runs (seeds run_seed+i)
  keep.decide(cand samples, ref)   # ref = tip samples; best kept for rigor; ceiling budget: over budget -> discard
  keep -> branch advances, samples join keeps; else reset; results.tsv row + ledger node (parent, diff, samples, verdict, program version)
  ```
  The agent never sees per-sample traces, hidden splits, or discarded diffs (only their descriptions).
- **Keep rule made mechanical** (`UpstreamKeep`, default): `gain = inc.mean - cand.mean` (`min`); `net` = added minus removed
  non-blank, non-`#` lines in editable files; `net > 0` -> keep iff `gain > 0.001*net/20`; `net < 0` -> keep iff
  `gain >= -0.001`; `net = 0` -> `gain > 0`; memory ratio `> 2.0` -> discard, `> 1.10` -> threshold `max(thr,0) + 0.001`,
  inclusive. All thresholds are rsi choices. Spec 9.4 suggested rigor for hardened mode [inferred]; E5 shows rigor tests ~25%
  fewer ideas at equal compute, so it is opt-in. Other rules: `strict`, `rigor`, `simplicity`, `rrsi`, any core `Gate`.
- **Timeout status**: upstream says "treat it as a failure (discard and revert)"; rsi logs a killed run as `crash`
  (`0.000000`, `0.0`) and offers no fix.
- **Confirm and go** is an opt-in callback called *after* the baseline (`loop.run`: `setup`, `baseline`, `confirm_setup`);
  upstream confirms before the first run. Reusing an `out_dir` is refused unless `overwrite=True` (upstream: the branch must
  not exist). Workspace is in memory unless `workspace="git"`.
- **Baseline**: `keep_rule.repeats` runs (1, or 3 for rigor); a crashing baseline goes through the fix path and raises if it
  stays broken. Optional `noise_runs` extra baseline runs feed `NoiseCalibrator`. Upstream: one run, no noise estimate.
- **Grader lock.** Upstream: `prepare.py` locked by instruction only; `evaluate_bpb` sums losses from the agent-owned
  `forward`; the budget clock and fast-fail live in editable `train.py`; the metric is grepped from an agent-written log; the
  agent self-reports `results.tsv`. Hardened mode (default) adds `ScopeGuard` + tamper scan, read-only re-sealed locked files
  with hash checks around each run, a locked budget clock, logits-only evaluator, import-time snapshots with an integrity flag,
  a framework-owned result record and unmounted hidden data. **Not a security boundary** (same process; see
  `implementation.md` Limitations). Faithful mode parses `^<metric>:` from the log, as upstream.
- **Seeds**: rsi passes `run_seed=0` to every run (tinylm seeds its RNG from both its editable `SEED` and the run seed).
  `BootstrapRigorKeep` uses seeds `run_seed+i` (`vary_seed=True`); the MLX `rigor.py` repeats the same pinned seed, so it
  only measures GPU nondeterminism (`vary_seed=False` reproduces that).
- **Budget kinds** `tokens`/`ceiling`/`rollouts` added; `max_experiments` counts turns (incl. rejected, invalid, duplicate),
  `max_runs` training runs; other stops `max_wall_s`, `max_usd` (shadow-monitor spend excluded), a `STOP` file in `out_dir`.
- **tinylm stands in for nanochat** (numpy byte-level MLP, seconds-long budgets, memory = peak RSS printed as `peak_vram_mb`,
  same nine summary keys); tabular uses HistGradientBoosting on a synthetic drifting world.
- **Ports are simulated:** SLURM = local fake scheduler, at-home = JSON files under `flock`, embeddings = char-3-gram cosine,
  MLX not run. Parallel variants: `workers>1` (judged against the incumbent at completion), `MultiChainLauncher` (N independent
  chains), `SharedHub`/`CollaborativeLoop` (claim, publish, adopt the swarm best every `sync_every=5` experiments).
- **Domain tasks**: one experiment = one evaluation of the whole `evolve` split with `k` trials per task (default 1), seeds
  `run_seed`-derived (offset `seed*1000`), score = mean reward, `memory_gb = 0` (VRAM clause inert), soft kill, `grade` is the lock.
- **Hidden splits** (`holdout`, `ood`, `test_iid`, `test_shift`, `test`) are read only by the write-only shadow monitor, the
  post-hoc `HiddenAudit` of every keep, `Reeval` (fresh seeds) and `rsi.transfer_report`; never by the loop or the agent.

Audit verdicts (65 claims: 51 reproduced, 10 partial, 1 not reproduced, 2 not testable here, 1 contradicted):
- **CONTRADICTED (T3):** "locking `prepare.py` means the agent can't change how it is graded". Upstream as built does not do
  this; in faithful mode 9 of 11 exploit classes win bogus gains at equal compute without touching `prepare.py` (5/5 seeds for
  7 classes). Hardened mode: 0 bogus classes. Use hardened for anything you will report.
- **Reproduced (mechanism):** baseline first, fixed budget with 11 warm-up steps, strict keep recomputed 44/44, `results.tsv`
  byte-identical to [program.md:83-87], crash rows, kill rule. "Strictly better on one noisy number locks in luck" on the
  landscape: keeps with true delta <= 0 are 19% (strict/upstream) vs 4.6% (rigor); seed-only keeps ~1.1 vs 0.26 per night.
  Rigor shrinks the optimism gap (0.0041 -> 0.0015) at an efficiency cost; not resolved on tinylm at 3 seeds.
- **Partial:** validation reuse (O26): val-specific optimism of about 0.015 bpb confirmed, growth over hundreds of experiments
  not significant. Strategy via `program.md` (O6/O23): a human edit changed behaviour (p = 0.001) but not outcomes (p = 0.22).
  Transfer to another machine (O17): gains shrink, no sign flips. ~12/h (P29/O2): 11.98/h scripted at 300 s, 9.59/h with a
  live agent. Backend port (O18) and SLURM (O19) only simulated.
- **Not reproduced:** P28, "if you run out of ideas, think harder (combine near-misses, radical changes)": at a replayed stuck
  point the live agent made only single-knob tweaks. Consistent with [Rehearse §1, §4.4, §5.1-5.2]: a memoryless judge's selective
  accuracy falls from 82.8% to 56.9% once three or more successes accumulate, and a vanilla nanochat loop (30-min budget,
  5 seeds) "largely plateaus" past experiment 50. Seed the idea pool through `program.md`.
- **Not testable here:** H100-scale numbers (O7, O16a) and the MLX night 2.667 -> 1.808.
- Upstream's own results are anecdotal: 15 kept of 89 (0.997900 -> 0.977287, [gh-disc:#32]) incl. one seed-only keep; #43
  reports that the warmup and seed wins did not reproduce. The transfer to nanochat d24 was accepted on 5 identical runs, not
  one [nanochat:LEADERBOARD].

## 7. Applying it with rsi

```python
import rsi
from rsi.autoresearch import Config, RunBudget, ScriptResearchTask, run
task = ScriptResearchTask("my-problem",
    {"evaluate.py": open("evaluate.py").read(),            # locked entry point: data + metric, calls report(r2=...)  (paste RECORD_HELPER)
     "train.py": open("train.py").read()},                  # the only editable file
    metric="r2", direction="max", editable_paths=["train.py"], locked_paths=["evaluate.py"],
    run_cmd=["python", "evaluate.py"], budget=RunBudget(kind="ceiling", amount=60, kill_after=120),
    tamper_patterns=[r"\bX_VAL\b", r"\breport\b"], description="What the model does and how it is scored.")
res = run(task, llm_propose="claude:haiku",                  # a spec string or an LLM; None -> scripted MockResearchAgent
          config=Config(max_experiments=50, mode="hardened", keep_rule="rigor", noise_runs=5, reeval_seeds=3, max_usd=5),
          out_dir="runs/x")
# existing Domain (prompts/harness/solver): same loop, one experiment = one evaluation of `evolve`
res = rsi.improve(dom, seed, method="autoresearch", llm_task=llm, llm_propose="claude:haiku",
                  config={"max_experiments": 30, "keep_rule": "rigor"}, out_dir="runs/x", cache_dir=".rsi_cache/x",
                  task_kwargs={"k": 2})                      # `config` is a dict of Config fields or a Config
```
Fields users usually set: `max_experiments`, `max_runs`/`max_usd`/`max_wall_s`, `mode`, `keep_rule` (+ `keep_kwargs`), `noise_runs`,
`reeval_seeds`, `program` (path to an editable `program.md`), `crash_fix`, `history_rows`, `workers`, `val_resample_every`, `workspace`,
`overwrite`, `confirm`. Task side: `editable_paths`, `locked_paths`, `budget`, `tamper_patterns`. For a Domain pass `task_kwargs`
(`k`, `split`, `audit_splits`). `res.best` is the final incumbent (branch tip), not necessarily the best row ever scored; `res.meta`
has `analysis` (keep rate, running best, top hits, experiments/hour), `audit` (hidden table of every keep), `reeval`, `counters`,
`crash_kinds`, `scope`, `workspace`. Files: `results.tsv`, `ledger.jsonl`, `artifacts/`, `logs/`, `program.md`, `groundtruth_all.tsv`,
`progress.png`, `trace.jsonl`.

**Audit with `python -m rsi.cli inspect runs/x`** (`TRACE.md`):
- `round_start`/`analysis`: what the agent read (results tail, kept log, nudge). `proposal`: exact prompt and reply, claimed change vs actual diff.
- `critic`: the ScopeGuard verdict (`REJECTED` rows). `eval`: metric, memory, wall, crash/kill/over-budget, parsed summary, per-task scores.
- `gate`: candidate vs reference values, `gain`, `lines_added/removed`, `memory_ratio`, threshold, running best, early reject. Recompute one by hand.
- `decision`: status, commit before/after, the `results.tsv` row; `state`: counters, budget, program version. Check HEAD == last keep.
- `monitor` and `groundtruth_all.tsv`: hidden scores of each keep. Visible metric improving while hidden stalls or drops = luck, overfitting to the
  shard, or a shift trap. Compare `reeval` (fresh seeds) against the recorded best: the gap is the optimism.
- Read the **kept diffs**, not only the numbers: sub-noise keeps (gain below the noise band) and seed-only keeps are the luck.

**Failure modes to watch:**
- Lucky keeps: the upstream rule keeps anything below the tip on one noisy run. Measure noise (`noise_runs>=3`, or repeat the baseline) and
  prefer `rigor` or a gate when per-run noise is comparable to typical gains. If gains are of the same size as the noise band, say so.
- Winner's curse: the incumbent is never re-measured, so its stored score is a lucky draw and later real gains must beat that inflated bar; use `reeval_seeds`.
- Scale mismatch: `eps=0.001` and the 20-lines clause assume a metric of upstream's scale; for a 0..1 pass-rate every added prompt line costs
  0.00005 and a loss of up to 0.001 is "kept" for deleting any line. Rescale via `keep_kwargs` or use `strict`/`rigor`. Domain `memory_gb = 0` disables the VRAM clause.
- Budget bias: a fixed wall-clock budget rewards what trains best in that time on this machine ("the most optimal model for your platform in
  that time budget", results "not comparable to other people running on other compute platforms" [README:64]); re-train winners at 4x/16x budget
  before trusting them. A `tokens` budget removes machine dependence but also the size pressure.
- Validation reuse and shift: one shard judges every decision; keep a hidden split and look at both iid and shifted audits. Early keeps that raise
  the visible metric can lower a shifted split (xgboost port, `x7` trap in tabular) [spec 7.4].
- Grader leakage in faithful mode or with weak `tamper_patterns`: loss rescaling, budget-clock edits, train-on-val, forged records. Hardened blocks the
  catalogued classes only. Frame-dependent features (`prepare(df)` using whole-frame statistics) inflate CV: use the per-row check.
- Greedy single chain: no memory beyond `results.tsv`; a live agent at a stuck point makes knob tweaks (P28; [Rehearse §5.2]). Put stuck-point guidance into `program.md`.
- Throughput is agent latency, not the budget: about 95 s of latency per turn at a short budget; wall-clock budgets on a shared machine add
  about +/-15% compute noise, so parallel wall-clock runs are only comparable on separate hardware.
- Cost: one agent call per turn (including rejected/invalid turns) plus one run per candidate (3 for rigor); cap with `max_usd`/`max_runs`, cache the LLM.
