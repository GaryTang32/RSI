# Autoresearch: keep-if-better on one locked metric, one edit at a time

Method reference for `rsi.improve(..., method="autoresearch")` (alias `karpathy`). Sources: `docs/methods/autoresearch/paper-spec.md`
(the spec), `implementation.md`, `claims-audit.md`, `rsi/autoresearch/`. **There is no paper.** The source is Karpathy's repo
`karpathy/autoresearch@228791fb` (March 2026), so the spec's tags are not `[paper §n]`/`[Alg. n]`/`[Eq. n]` (none exist) but
`[code:ar/<file>]` (upstream `program.md`, `prepare.py`, `train.py`, `README.md`), `[code:mlx/...]` `[code:xgb/...]` `[code:home/...]`
`[code:slurm/...]` `[code:sky/...]` (community ports), `[gh-disc:#32|#43]` (agent session reports), `[nanochat:LEADERBOARD]`, `[doc]`
(the user's overview, secondary), `[inferred]`. Upstream has no formulas beyond the metric and no hyperparameters for the loop itself;
anything the spec does not give is "not specified upstream" below. Results upstream are single anecdotal runs [spec 7].

## 1. In one paragraph

A coding agent is given a small real training setup of three files: `prepare.py` (data, tokenizer, the evaluation function; locked),
`train.py` (model, optimizer, loop; the only file the agent edits) and `program.md` (the instructions; edited by the human)
[code:ar/README.md]. Every experiment trains for a **fixed 5 minutes of wall-clock** and reports one number, validation bits per
byte `val_bpb = sum(m*l) / (ln2 * sum(b))` (lower is better, vocabulary-independent) [code:ar/prepare.py:evaluate_bpb]. If the number is
strictly lower than the branch tip's, the git commit stays and becomes the new baseline; if equal or worse the agent `git reset`s
[code:ar/program.md steps 8-9]. This repeats "forever" (about 12 experiments/hour, about 100 per night). What it optimises is the training
code `train.py` against one metric on one pinned validation shard, as one greedy chain; the strategy improves only when a human rewrites
`program.md` [spec 1, 2, 8.6].

## 2. Inputs and setup

Needed before experiment 1:
- **Artifact**: the editable file(s) (upstream: `train.py` only) and everything else in scope but locked (`prepare.py`, dependencies).
  Here: an `Artifact` (content-addressed files) wrapped in a `ResearchTask`, or any `rsi.core.Domain` wrapped as `DomainResearchTask`.
- **Metric and direction**: one scalar from one run, plus peak memory (`memory_gb`). Locked code computes it; the agent must not.
- **Fixed per-run budget** (`RunBudget`): `wallclock` (upstream), `tokens` (deterministic), `ceiling` (xgboost port: a cap, over-budget
  runs become `discard`), `rollouts` (Domain tasks). Hard kill at `2*amount+5 s` unless `kill_after` is set.
- **Splits**: the loop sees one split (script task: its `val`; Domain task: `evolve`). Hidden splits are strongly recommended and are
  the only honest check: script tasks provide `task.audit` (tinylm: `test_iid`, `test_shift`), Domain tasks `holdout`/`ood`. **Upstream
  has no hidden split at all** [spec 8.2].
- **Models**: one LLM role, the researcher (`llm_propose`; upstream = Claude Code/Codex). No critic, no analyst. `llm_task` only for Domain tasks.
- **`program.md`** (`ProgramSpec`, presets `upstream|xgboost|simplify` or a file path re-read every experiment).
- **Budget**: `max_experiments=100` agent turns, optional `max_runs`, `max_wall_s`, `max_usd`, or a `STOP` file.

Measured first (`AutoresearchLoop.baseline`) [code:ar/program.md "The first run"]: the unmodified files are run with the pinned seed
(`keep_rule.repeats` runs: 1 for upstream/strict, 3 for rigor) and logged as row 1, status `keep`. A crashing baseline goes through the
crash-fix path; if it stays broken the run raises. Optional: `noise_runs=N` extra baseline re-runs -> `NoiseCalibrator` delta
(default 0 = off; upstream has no noise measurement, spec 9.4 suggests 5 [inferred]). Setup also creates branch `autoresearch/<tag>`
(here `Workspace`, in memory by default, `workspace="git"` for a real repo), an empty `results.tsv` with the 5-column header, and an optional
`confirm(summary)` callback ("Confirm and go"). A reused `out_dir` is refused unless `overwrite=True` (upstream: branch must not exist).

## 3. Round by round

One round = one experiment = one agent turn [code:ar/program.md "The experiment loop"]. Upstream the agent itself runs git, training, grep and
logging; here the agent only proposes and the framework does the rest (hardened design, spec 9.1).

1. **Incumbent.** The branch tip (last kept commit) and its stored samples. It is **not re-run**.
2. **What the agent reads.** The rendered `program.md`, the current tip files, the **whole** `results.tsv`
   (`commit, <metric>, memory_gb, status, description`; `history_rows=N` truncates), the kept-commit log, the best value, and after
   empty turns a "NEVER STOP: think harder, combine near-misses, try radical changes" note. It does **not** see per-sample traces, per-task
   feedback, hidden splits, or discarded diffs (only their one-line descriptions); a crash shows `tail -n 50 run.log` once.
3. **One candidate.** One idea, edited directly into the editable file(s) (`RewriteEditor` returns whole files; `editor="agent"` uses file
   tools). Up to `max_propose_attempts=3` tries if the reply is invalid or identical to the tip. The agent may reply `REWIND <commit>`
   (upstream: allowed "very very sparingly"); the loop never rewinds by itself.
4. **Scope check** (hardened only): `ScopeGuard` rejects edits to locked files, out-of-scope files and tamper patterns *before running*;
   logged as a `discard` row `REJECTED (...)` with metric 0.000000, never run. Exact repeats of a scored file are skipped when
   `never_repeat` (rigor only) and cost a turn.
5. **Commit and run.** Commit the candidate, run the task with a hard watchdog (stdout+stderr into `logs/<name>.log`, not into the
   agent's context). Hardened: locked files are restored, chmod 0444 and hash-checked around the run; the metric comes from a
   framework-owned record, not from a grep of agent-written text. Faithful: `grep "^<metric>:"` on the log, as upstream.
6. **Crash handling** [code:ar/program.md "Crashes"]. Empty metric = crash. `crash_fix="agent"`: the agent sees the log tail and either returns
   a fixed file (re-run, up to `max_fix_attempts=3`) or `GIVE_UP`; timeouts and hardened violations are never offered a fix. Then row
   `crash`, `0.000000`, `0.0`, branch reset.
7. **Score.** `Samples` = the run(s) of this candidate: 1 value (upstream/strict) or `repeats=3` for rigor (first run no better than the best kept
   mean -> early discard). Mean of the values is the candidate's score.
8. **Keep rule** (default `UpstreamKeep`, `gain = inc.mean - cand.mean` for `min`, sign-flipped for `max`):
   - strict: keep iff `gain > 0` (upstream: `val_bpb_new < val_bpb_best`, equal resets) [code:ar/program.md];
   - simplicity criterion, made mechanical: with `net` = added minus removed non-blank non-`#` lines of the editable files,
     `net > 0` -> keep iff `gain > eps*net/20`; `net < 0` -> keep iff `gain >= -eps`; `eps = 0.001`
     ("0.001 for 20 lines of hacky code? Probably not worth it" is upstream's only number);
   - VRAM soft constraint: memory ratio `> 2.0` -> discard; `> 1.10` -> `gain >= eps` more is required;
   - a run over budget is `discard` even if the rule accepts. Other rules: `strict`, `rigor` (keep iff bootstrap
     `P(mean(cand) < mean(best kept)) >= 0.95`, 20,000 resamples, 3 runs), `simplicity`, `rrsi` (core `RRSIGate`), or any core `Gate`.
9. **Update state.** Append the `results.tsv` row (never committed). Keep: branch advances, incumbent := candidate, candidate samples
   join the keeps list, running best updates. Discard/crash: `reset_to(incumbent)`; the diff survives only in `ledger.jsonl` and
   `artifacts/`. Ledger node records parent, diff, samples, verdict, program version, crash kind.

```text
setup:  base = run(H0) [repeats x]; log row "baseline keep"; inc = base; keeps = [base]
while no stop (max_experiments turns | max_runs | max_wall_s | max_usd | STOP file):
    ctx = (program.md, inc files, whole results.tsv, kept log, best, nudge)      # program.md re-read if a human edited it
    cand = agent.propose(ctx)                        # ONE edit; invalid/identical -> retry <= 3, else turn wasted
    if hardened and ScopeGuard(inc, cand): log REJECTED discard; continue
    commit(cand); out = run(cand, seed=run_seed)     # crash -> agent fix <= 3 -> else log crash, reset
    s = Samples(runs of cand)                        # 1 run, or 3 for rigor with early reject
    ok = keep.decide(s, ref)                         # ref = inc, or best kept for rigor
    if ok and not over_budget: advance branch; inc = cand; keeps += s     else: reset to inc
    append results.tsv row; ledger node
finish: Analyzer (keep rate, running best, per-keep deltas), HiddenAudit of every keep, optional Reeval (fresh seeds)
return: final incumbent (the branch tip), not necessarily the best row ever scored
```

**What changes across rounds.** Only the incumbent and the history: the tip files, `results.tsv` and the kept log grow, and the agent
may react to them. There is no annealing, no candidate budget, no frontier, no population (one greedy chain) and no automatic strategy change.
`program.md` changes only when a human edits it (versioned; every ledger node carries its version). The validation shard is the
same for every decision unless `val_resample_every=M` (an ablation, not upstream). Parallel variants: `workers>1` (SkyPilot-style
"don't wait", each result judged against the incumbent at completion time), `MultiChainLauncher` (spawn.sh: N independent chains, no sharing),
`SharedHub`/`CollaborativeLoop` (at-home: claim before running, publish results, adopt the swarm best every 5 experiments).

**Stopping rule.** Upstream: never; "until the human interrupts you, period" [code:ar/program.md]. Here: `max_experiments` (counts
turns including rejected, invalid, duplicate ones), `max_runs` (training runs incl. repeats and fixes; use for equal-compute comparisons),
`max_wall_s`, `max_usd` (shadow-monitor spend excluded), a `STOP` file in `out_dir`, or `max_consecutive_invalid` (default `None` = never stop on empty turns).

## 4. Sample by sample

Autoresearch has no per-task inner loop for script tasks. The unit is **one training run = one scalar**.
- **Trials**: upstream 1 run per candidate with `train.py` pinning `seed 42` (an edit of the seed is itself a legal experiment and was kept once:
  42->137, -0.000439 in [gh-disc:#32]; it did not reproduce in #43). Here `run_seed=0` is passed to every run; `StrictKeep`/`UpstreamKeep`
  use `repeats=1`; `BootstrapRigorKeep` uses 3 runs with seeds `run_seed+i` (`vary_seed=True`; the MLX `rigor.py` repeats the
  *same* pinned seed, so it only measures GPU nondeterminism, `vary_seed=False` reproduces that) [code:mlx/rigor.py].
- **Recorded per run**: the summary block (`val_bpb, training_seconds, total_seconds, peak_vram_mb, mfu_percent, total_tokens_M, num_steps,
  num_params_M, depth` upstream; tinylm prints the same nine keys), `memory_gb = round(peak_vram_mb/1024,1)`, wall time, the log file, the
  `results.tsv` row. Tokens/dollars of the agent are not tracked upstream [spec 8.10]; here `res.usage` meters the researcher.
- **Aggregation**: round score = mean of the run values; the incumbent's score is its stored samples, never re-measured, so later
  candidates face a bar that is the best of several noisy draws (winner's curse, see 7).
- **Inside one run** (upstream `train.py`): the wall-clock budget counts only steps `> 10` (first 11 steps = compile warm-up), schedules are
  functions of time progress, NaN or `loss>100` exits with `FAIL`, then `evaluate_bpb` runs on the pinned val shard
  (`EVAL_TOKENS = 40*524288`; the number of rows scored depends on the agent-chosen eval batch size via floor division) [spec 3.3, 3.4, 8.4f].
- **Domain tasks** (`DomainResearchTask`): one experiment = one evaluation of the whole `evolve` split with `k` trials per task
  (`task_kwargs={"k": 2}`, default 1), seeds from `run_seed` (offset `seed*1000`, the same for the incumbent and every candidate), score = mean
  reward, `memory_gb = 0` (the memory clause is inert). Per-task scores are traced but the agent never sees them.
- **Splits**: the loop reads `val`/`evolve` only. Never read by the loop or shown to the agent: `holdout`, `ood`, `test_iid`, `test_shift`,
  `test`. They are read by the write-only shadow monitor (`shadow_monitor=True`, each new incumbent, separately metered), by the post-hoc
  `HiddenAudit` of every keep (`hidden_audit=True`) and by `Reeval` (`reeval_seeds>0`, fresh seeds), and finally by `rsi.transfer_report`.

## 5. Paper defaults vs this package

"Paper" = upstream repo/spec. Task-level settings live on the `ResearchTask`, loop settings in `rsi.autoresearch.Config`.

| setting | paper value [tag] | rsi field = default | note |
|---|---|---|---|
| experiments per run | "forever"; about 100/night [code:ar/program.md] | `max_experiments = 100` | counts agent turns, not runs |
| per-run budget | 300 s wall clock [code:ar/prepare.py:TIME_BUDGET] | `RunBudget(kind="wallclock", amount=25.0)` | tinylm uses seconds; set it on the task |
| warm-up steps excluded | 11 (`step > 10`) [code:ar/train.py] | `RunBudget.warmup_excluded_steps = 11` | |
| kill rule | total run over 10 min = failure [code:ar/program.md] | `kill_after = None` -> `2*amount + 5 s`; Domain: 1800 s, soft | timeout logged as `crash` (upstream leaves it open) |
| keep rule | strictly lower metric + simplicity + VRAM judgment [code:ar/program.md] | `keep_rule = "upstream"` (`eps=0.001`, `lines_per_eps=20`, `mem_tol=0.10`, `mem_blowup=2.0`) | thresholds are rsi's reading; only the 0.001/20-line example is upstream's |
| bare strict | `val_bpb_new < best` [code:ar/program.md] | `keep_rule = "strict"` | |
| noise-aware gate | MLX `rigor.py`: S=3, c=0.95, R=20,000, rng 1234 [code:mlx/rigor.py] | `keep_rule = "rigor"`: `repeats=3, confidence=0.95, reps=20000, vary_seed=True` | opt-in; tests ~25% fewer ideas at equal compute |
| noise measurement | none upstream; 5 re-runs suggested [inferred, spec 9.4] | `noise_runs = 0` | only gate-based rules (`rrsi`, `MinGain`) use delta |
| crash fixes | "more than a few attempts" [code:ar/program.md] | `max_fix_attempts = 3`, `crash_fix = "agent"` | `"trivial"` = typo/import only |
| seed | `train.py` pins 42 [code:ar/train.py] | `run_seed = 0` | framework-pinned |
| NEVER STOP | prompt rule; `spawn.sh` watcher re-prompted idle agents every 120 s [code:ar@b11d6f2:spawn.sh] | `max_consecutive_invalid = None` | no nudger needed: the loop owns the turns |
| agent history | not an explicit step [spec 3.2, inferred] | `history_rows = None` (whole `results.tsv`) | |
| grader lock | by instruction only [spec 2, 8.4] | `mode = "hardened"` (alt. `"faithful"`) | see 6 |
| hidden audit / re-eval | none | `hidden_audit = True`, `reeval_seeds = 0` | |
| validation reuse | fixed shard for all decisions | `val_resample_every = None` | ablation |
| chains | one; `spawn.sh`: N independent | `workers = 1`; `executor = "local"\|"slurm"` (fake SLURM) | |
| strategy | human edits `program.md` | `program = "upstream"` or a path | re-read between experiments |
| researcher | Claude Code / Codex with shell and git | `llm_propose`, `editor="rewrite"` (whole-file replies) or `"agent"` | agent latency sets throughput |

## 6. Where our implementation differs

From `implementation.md` "Deviations" and `claims-audit.md`:
- **Framework runs the loop.** Upstream's agent runs git, training, grep and logging itself and is the keep/discard judge. Here the agent only
  proposes edits; the keep rule, git, runs, parsing and logs are framework code. Faithful mode reproduces upstream's measurement weaknesses,
  not the agent operating a shell.
- **Default keep rule is `UpstreamKeep`, not rigor.** Spec 9.4 suggested rigor for hardened mode ([inferred]); E5 shows rigor tests ~25% fewer
  ideas at equal compute, so it is opt-in. Judgment clauses (simplicity, VRAM) are mechanical here, upstream leaves them to the agent.
- **Grader lock.** Upstream: `prepare.py` is locked only by instruction, and `evaluate_bpb` sums losses from the agent-owned `forward`, the budget
  clock sits in editable code, the metric is grepped from an agent-written log. Hardened mode (default) adds `ScopeGuard` + tamper scan, locked
  budget clock, logits-only evaluator, import-time snapshots with an integrity flag, framework-owned result record, unmounted hidden data.
  It is **not a security boundary** (same process; residual routes in `implementation.md` Limitations).
- **Budget kinds** `tokens`/`ceiling`/`rollouts` added; kill = `2*amount+5 s`; `max_experiments` counts turns; rewinds only on agent request;
  workspace in memory unless `workspace="git"`; memory is peak RSS (tinylm) or 0 (Domain).
- **tinylm stands in for nanochat** (numpy byte-level MLP, seconds-long budgets); tabular uses HistGradientBoosting on a synthetic drifting world.
- **Ports are simulated:** SLURM = local fake scheduler, at-home = JSON files under `flock`, embeddings = char-3-gram cosine, MLX not run.
  
- **Domain tasks** have a soft kill (thread abandoned), no isolation of the artifact beyond its own `execute`; `grade` is the lock.

Audit verdicts that change how you use it (65 claims: 51 reproduced, 10 partial, 1 not reproduced, 2 not testable here, 1 contradicted):
- **CONTRADICTED (T3):** "locking `prepare.py` means the agent can't change how it is graded". Upstream as built does not do this; in faithful mode
  9 of 11 exploit classes win bogus gains at equal compute without touching `prepare.py` (5/5 seeds for 7 classes). Hardened mode: 0 bogus
  classes. Use hardened for anything you will report.
- **Reproduced (mechanism):** baseline first, fixed budget with 11 warm-up steps, strict keep recomputed 44/44, `results.tsv` byte-identical to
  upstream's example, crash rows, kill rule. "Strictly better on one noisy number locks in luck" reproduced on the landscape: keeps with true delta
  <= 0 are 19% (strict/upstream) vs 4.6% (rigor); seed-only keeps ~1.1 per night vs 0.26. Rigor shrinks the optimism gap (0.0041 -> 0.0015) at
  an efficiency cost; not resolved on tinylm at 3 seeds.
- **Partial:** "same validation data judges every experiment" (O26): val-specific optimism of about 0.015 bpb is confirmed, its growth over hundreds of
  experiments is not significant; null on real GBT at ~30 decisions. "Strategy improves only via `program.md`" (O6/O23): a live human edit changed the
  agent's behaviour (p = 0.001) but not outcomes (p = 0.22). "Some findings do not carry to another machine" (O17): gains shrink, no sign flips.
  ~12 experiments/hour (P29/O2): 11.98/h scripted at a 300 s budget, 9.59/h with a live agent. Backend port (O18) and SLURM (O19) only simulated.
- **Not reproduced:** P28, "when out of ideas it thinks harder (combines near-misses, radical changes)": at a replayed stuck point the live agent
  made only single-knob tweaks. Do not rely on this; seed the idea pool through `program.md`.
- **Not testable here:** H100-scale numbers (O7, O16a) and the MLX night 2.667 -> 1.808.
- Upstream's own results are anecdotal: 15 kept of 89 (0.9979 -> 0.9773, [gh-disc:#32]) incl. one seed-only keep; #43 reports warmup and seed wins
  did not reproduce. The transfer to nanochat d24 used 5 identical runs, not one [nanochat:LEADERBOARD].

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
`overwrite`. Task side: `editable_paths`, `locked_paths`, `budget`, `tamper_patterns`. For a Domain pass `task_kwargs` (`k`, `split`, `audit_splits`).
`res.best` is the final incumbent (branch tip); `res.meta` has `analysis` (keep rate, running best, top hits, experiments/hour), `audit`
(hidden table of every keep), `reeval`, `counters`, `crash_kinds`, `scope`, `workspace`. Files: `results.tsv`, `ledger.jsonl`, `artifacts/`,
`logs/`, `program.md`, `groundtruth_all.tsv`, `progress.png`, `trace.jsonl`.

**Audit with `python -m rsi.cli inspect runs/x`** (`TRACE.md`):
- `round_start`/`analysis`: what the agent read (results tail, kept log, nudge). `proposal`: exact prompt and reply, claimed change vs actual diff.
- `critic`: the ScopeGuard verdict (`REJECTED` rows). `eval`: metric, memory, wall, crash/kill/over-budget, parsed summary, per-task scores.
- `gate`: candidate vs reference values, `gain`, `lines_added/removed`, `memory_ratio`, threshold, running best, early reject. Recompute one by hand.
- `decision`: status, commit before/after, the `results.tsv` row; `state`: counters, budget, program version. Check HEAD == last keep.
- `monitor` and `groundtruth_all.tsv`: hidden scores of each keep. Visible metric improving while hidden stalls or drops = luck, overfitting to the
  shard, or a shift trap. Compare `reeval` (fresh seeds) against the recorded best: the gap is the optimism.
- Read the **kept diffs**, not only the numbers: sub-noise keeps (gain below the noise band) and seed-only keeps are the luck.

**Failure modes to watch:**
- Lucky keeps: strict rule on one noisy run keeps anything below the running minimum. Measure noise (`noise_runs>=3`, or repeat the baseline) and
  prefer `rigor` or a gate when per-run noise is comparable to typical gains. If gains are of the same size as the noise band, say so.
- Winner's curse: the incumbent is never re-measured, so its stored score is the luckiest of its draws and later real gains must beat that inflated bar; use `reeval_seeds`.
- Scale mismatch: `eps=0.001` and the 20-lines clause assume a metric of upstream's scale; for a 0..1 pass-rate every added prompt line costs
  0.00005 and a loss of up to 0.001 is "kept" for deleting any line. Rescale via `keep_kwargs` or use `strict`/`rigor`. Domain `memory_gb = 0` disables the VRAM clause.
- Budget bias: a fixed wall-clock budget rewards what trains best in that time on this machine (smaller/faster models, "more steps > more
  params"); re-train winners at 4x/16x budget before trusting them. `tokens` budget removes machine dependence but also the size pressure.
- Validation reuse and shift: one shard judges every decision; keep a hidden split and look at both iid and shifted audits. Early keeps that raise
  the visible metric can lower a shifted split (xgboost port, `x7` trap in tabular) [spec 7.4].
- Grader leakage in faithful mode or with weak `tamper_patterns`: loss rescaling, budget-clock edits, train-on-val, forged records. Hardened blocks the
  catalogued classes only. Frame-dependent features (`prepare(df)` using whole-frame statistics) inflate CV: use the per-row check.
- Greedy single chain: no memory beyond `results.tsv`; a live agent at a stuck point makes knob tweaks (P28). Put stuck-point guidance into `program.md`.
- Throughput is agent latency, not the budget: about 95 s of latency per turn at a short budget; wall-clock budgets on a shared machine add
  about +/-15% compute noise, so parallel wall-clock runs are only comparable on separate hardware.
- Cost: one agent call per turn (including rejected/invalid turns) plus one run per candidate (3 for rigor); cap with `max_usd`/`max_runs`, cache the LLM.
