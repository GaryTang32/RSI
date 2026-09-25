# autoresearch: implementation notes (`rsi.autoresearch`)

This package implements Karpathy's autoresearch loop (March 2026) and the community
ports described in `docs/methods/autoresearch.md`. It works on any problem that can be
written as a `ResearchTask` (a set of locked and editable files plus a run command) or
as any `rsi.core.Domain` plus a seed `Artifact`. Everything runs offline with scripted
agents, and every experiment script also accepts `--llm claude:haiku`.

**Two modes.** `faithful` keeps upstream's semantics, weaknesses included, so they can
be measured: the grader is locked only by instruction, the metric is grepped from a
log the agent's code writes, and the budget clock lives in editable code. `hardened`
(the default) locks those things mechanically: `ScopeGuard`, `BudgetEnforcer`, a
logits-only evaluator with a causality probe, a result record owned by the framework,
and hidden data that is not mounted. The framework runs git, the runs, the parsing,
the keep rule and the logging, so the agent only proposes edits. Upstream let the agent
do all of this itself. See Deviations.

## Results at a glance

All numbers come from `results/autoresearch/<name>.json`, where each file also holds the configuration, the per-seed raw values and a verdict. Figures are the matching `.png` files. Brackets are 95% bootstrap CIs (`rsi.core.stats.summarize_runs`). The runs used the offline scripted agent on a shared 4-core machine whose load average was 3-7 from other jobs, which widens the wall-clock noise for tinylm. Every script is re-runnable with `python experiments/autoresearch/<name>.py [--llm sim|claude:haiku] [--seeds N] [--quick]`.

| Exp | Claim | Setup (full settings) | Result | Reproduced? |
|---|---|---|---|---|
| **E1** `e1_progress` | Steady unattended progress | tinylm (2 s budget, hardened), 30 experiments x 3 seeds; noise band from 5 baseline re-runs; best-of-N random search at equal count; landscape 30 seeds x 100; AgentQA via `DomainResearchTask` 3 seeds x 10 | tinylm val_bpb gain: greedy **0.67** recorded, **0.61 [0.48, 0.72]** on a fresh-seed honest re-eval, vs noise band delta **0.081**; random search 0.29 [0.16, 0.49]. Keep rate 25% [20, 32]; about 1,290 experiments/h. Crash, OOM, hang and NaN runs handled unattended. Landscape true gain 0.069 (greedy) vs 0.050 (random). AgentQA: holdout +0.53 (0.43-0.45 -> 0.95-0.98), OOD +0.49 (0.38-0.50 -> 0.92-0.94) | **yes** |
| **E4** `e4_grader_locking` | Locking the grader keeps the loop honest; upstream locks only socially | Battery: 7 exploit classes x faithful/hardened x 3 seeds on tinylm; loop with 15% exploit injection (24 experiments x 2 seeds per mode); landscape grader edit 30 seeds | Faithful: every one of the 7 classes is accepted as an improvement on at least one seed, and none of those wins holds up under the locked measurement (claimed minus baseline: grader edit -0.33 bpb, scaled loss -0.80, 2x budget clock -0.43, train-on-val -0.09, fake report -2.73, future-peek -3.56; the hidden-data 'win' is noise, -0.02, but the kept code reads hidden audit data). Faithful loops kept 3/8 injected exploits and **both final "bests" are invalid under locked grading** (one reports 1.0 bpb from a fake report). Hardened: grader edit rejected, fake report / future peek / train-on-val / hidden read crash as violations, and scaled loss and budget clock run under the locked evaluator and clock with no bogus gain. The hardened loop's recorded best minus honest re-eval is **-0.003 [-0.011, 0.005]** (noise). Landscape recorded-minus-true: faithful -0.055, hardened -0.004 | **yes** |
| **E5** `e5_strict_luck` | "Strictly better" on one noisy number locks in luck | Landscape (exact truth) 50 seeds, 5 keep rules at **equal total training runs (150)**; tinylm strict vs rigor at 27 runs x 3 seeds, 5 fresh-seed re-evals | Keeps whose TRUE delta <= 0: strict **19% [16, 22]**, rigor **4.6% [2.9, 6.6]**, rigor with pinned seed (rigor.py literally) 12%, noise-band gate 1%. Seed-only keeps per night: strict 1.18, rigor 0.26. Optimism gap (honest minus recorded): strict **0.0041 [0.0035, 0.0046]**, rigor 0.0015, pinned rigor 0.0035. **Trade-off:** true final quality is best under strict (0.9563 vs rigor 0.9579, noise-band 0.9633), because rigor tests about 25% fewer ideas at equal compute. tinylm: optimism gap strict 0.035 [0.018, 0.055] vs rigor 0.018 [-0.003, 0.040]; honest gain strict 0.76 vs rigor 0.55 | **yes** (luck is locked in; rigor shrinks it but costs search at equal compute) |
| **E6** `e6_val_reuse` | Reusing one validation set slowly tunes to it | Landscape 40 seeds x 300 experiments, val-specific sd {0.0015, 0.005}, fixed vs re-sampled val every 25; tabular 30 experiments x 2 seeds, fixed vs re-sampled CV folds every 10, HiddenAudit of every keep | Landscape (sd 0.005): optimism of the kept chain on its own val grows from **0.0084 (50 exp) to 0.0128 (300 exp)** with a fixed val. With re-sampled val it stays at **0.0086**, and true quality improves (0.958 vs 0.961). The effect is smaller at sd 0.0015 (0.0062 vs 0.0046). Tabular at 30 decisions: CV minus hidden-iid gap does **not** grow (0.0087 -> 0.0074 AUC), a null result like the xgboost port's. Separately, the shift trap `x7` is kept in every seed (CV and iid up, shifted test -0.056): distribution shift, not overfitting | **partly**: yes on the landscape; null on real GBT at this N (as the sources report) |
| **E8** `e8_house_rules` | Kill > 2x budget, fix trivial crashes, skip broken ideas, rare rewinds | tinylm scripted night (typo, missing import, hang, OOM, NaN, honest edits, grader edit) in both modes; never-fixable crash; landscape 30 seeds x 60 with 20% crash edits | All 19 checks pass. Typo and missing import fixed once and re-run. Hang killed at kill_after (8.0 s for a 1.5 s budget). NaN fast-fails in 0.2 s (FAIL). OOM logged. Crash rows are `0.000000 / 0.0`. HEAD equals the last kept commit. Give-up after p_fix=0 and after 3 failed fixes. Landscape: 208 crashes (90 OOM, 74 timeouts, 44 NaN) and 144 trivial fixes, all invariants hold, 0 rewinds | **yes** |
| **E10** `e10_executors` | Scheduler/cluster forks: async submission and polling | Script-mode landscape (real subprocesses, 1.2 s runs), 36 experiments x 3 seeds, 10% crash edits: sequential vs 3 local workers vs fake SLURM (3 nodes, queue delay, container image); tinylm CPU-bound check | Throughput **2.7x** (local-3) and **2.6x** (fake SLURM); tinylm 2.5x on 4 shared cores. Time to a common best 12 s / 15 s vs 29 s. States: OOM -> OUT_OF_MEMORY, NaN/typo -> FAILED, hang -> TIMEOUT. **31-38%** of parallel experiments were judged against a newer incumbent than they were proposed from (grid-like search). At an **equal experiment count** the parallel runs end *worse* in true quality (0.981 / 0.969 vs 0.961): parallelism buys wall time, not sample efficiency | **yes** (throughput, states, time-to-best); sample efficiency drops |
| **E11** `e11_hub` | Collaborative: claim before running, publish full code, shared best | 4 agents (threads) x 20 experiments on a file-backed `SharedHub`, 5 seeds; one agent lies (blatant 0.30 / subtle -0.03) under {no filters, at-home sanity filters, filters + independent re-verification} | Claiming halves duplicates: description-level **0.23 vs 0.47**, code-level 0.11 vs 0.18. The swarm reaches its best in **3.8 s vs 14.3 s** of wall time for one agent at equal compute, though true quality is slightly worse (0.965 vs 0.961). Liar owns the global best: no filters **100%**; filters vs blatant 0%; filters vs **subtle 100%** (only 29% of global-best updates survive an independent re-eval); **filters + verify 0%** (98% survive) | **yes** |
| **E12** `e12_gbt` | Non-LLM adaptation (GBT vs a held-out metric) | tabular, 25 experiments x 3 seeds: FE+HPO agent vs HPO-only agent vs random HPO (best of N); hidden iid + shifted audits of every keep | FE+HPO: CV **+0.026**, hidden iid **+0.028**, shifted **-0.043** (the `x7` shift trap is kept in all seeds). HPO-only: CV +0.005, iid +0.0035, shift +0.003. Random HPO: iid +0.0034 (agent HPO roughly equals random HPO). FE adds **+0.024 iid AUC** beyond HPO. CV and iid agree in direction on 71% of kept steps | **yes** |
| **E2** `e2_budget_bias` | Fixed budget favours small/fast models on this machine | Landscape 30 seeds x 100 (wall-clock vs token budget; re-eval at 4x/16x; slow vs fast "machine"); tinylm 20 experiments x 2 seeds, 2 s wall clock vs a fixed 100k-byte token budget, winners re-trained at 4x | Landscape: final model size **0.25x** baseline under wall clock vs **2.9x** under tokens. Kept gain **+0.069 (1x) -> +0.008 (4x) -> -0.037 (16x)**. Slow-machine winner on the fast machine +0.025 vs its own winner +0.038. tinylm (real): params ratio final/base **0.95** (wall clock) vs **4.6** (tokens; HIDDEN 128 -> 1024). Gain of the wall-clock winners shrinks from **0.74 bpb at 1x to 0.33 at 4x** (it does not reverse; part of this is the baseline's low LR mattering less at longer horizons) | **yes** |
| live smoke `e1_progress --llm claude:haiku --seeds 1 --quick` | One command away | tinylm 2 s budget, 8 experiments proposed by Claude Haiku 4.5 (RewriteEditor, cached) | 8 LLM calls, **$0.47**, 78k input / 63k output tokens, 0 parse failures. 4 keeps (LR 0.003->0.008->0.01, WARMDOWN 0.5->0.1), 4 honest discards (DEPTH 2, CONTEXT 8, BATCH 64, EMBED 32). val_bpb 3.84 -> 3.21 recorded, **3.83 -> 3.32** on a 3-seed honest re-eval | works |
| example `example_new_problem` | Template for a new user problem | polynomial ridge regression, 20 scripted experiments, hardened mode with a pasted `RECORD_HELPER` | R^2 0.274 -> 0.878 (DEGREE 1->3->5->7, SCALE, RIDGE) | works |

Not run (see Deviations): E3 (bpb vs vocabulary), E7 (program.md variants with a live agent), E9 (backend port) and E13 (frame-dependent features; the mechanism is implemented and tested).

## Module map

| File | Contents |
|---|---|
| `rsi/autoresearch/task.py` | `ResearchTask` (editable/locked paths, `RunBudget`, `parse_summary` where an empty grep means a crash, hidden `audit`), `ScriptResearchTask` (files run as a subprocess in a fresh per-experiment workdir; watchdog kill via `rsi.core.sandbox.run_cmd`; faithful log-grep vs hardened framework-owned record; locked files restored, chmod 0444 and hash-checked before and after the run), `DomainResearchTask` (adapter for any `rsi.core.Domain`), `RunOutcome`, `RECORD_HELPER` |
| `rsi/autoresearch/guard.py` | `ScopeGuard` (out-of-scope and locked-file edits become `Violation`s and are rejected before running; `locked_hashes_ok`, `restore`, `lock_dir`), `BudgetEnforcer` (watchdog timeout, classification of over-budget and killed runs), `CrashPolicy` (kinds: trivial / timeout / oom / nan / violation / error; at most `max_fix_attempts` fixes, trivial kinds only) |
| `rsi/autoresearch/results.py` | `ResultsLog`, the exact 5-column `results.tsv` (`commit <metric> memory_gb status description`, 6 dp, crash = `0.000000`/`0.0`, never committed); `Workspace`, the kept-commit chain `autoresearch/<tag>` (`commit`, `amend`, `reset_to`, `rewind`, `log`), in-memory or real git (`backend="git"`) |
| `rsi/autoresearch/keep.py` | `KeepRule` plug-ins: `StrictKeep` (upstream), `BootstrapRigorKeep` (MLX `rigor.py`: 3 runs, P >= 0.95, 20,000 resamples, rng 1234, early reject, never-repeat; `vary_seed` switch), `SimplicityWeighted`, `GateKeep` (any core gate, e.g. `RRSIGate`, `MinGain`); `NoiseCalibrator`; `make_keep_rule` |
| `rsi/autoresearch/program.py` | `ProgramSpec`: the human-owned, content-versioned `program.md` (presets `upstream`, `xgboost`, `simplify`); `render(task, mode)` fills in metric, budget, files and interface contract; `edit()` is the only way a new version is made; a loop given a path re-reads it between experiments |
| `rsi/autoresearch/agent.py` | `AgentContext`; `LLMResearchAgent` (via `RewriteEditor` or `AgentEditor`; `fix_crash` path with a `GIVE_UP` escape); `MockResearchAgent` (scripted `ScriptedEdit` pool of helpful/neutral/harmful/crash/exploit edits; greedy coordinate search / random / fixed schedule; exploit and crash injection rates; `p_fix`; "combine previous near-misses" when out of ideas); `RandomSearchAgent` (best-of-N baseline); `knob_edit`, `text_edit`; `scripted_llm` (a MockLLM that answers RewriteEditor prompts) |
| `rsi/autoresearch/loop.py` | `Config`, `AutoresearchLoop` (setup, baseline first, `step` = propose, scope check, commit, run, fix, extra runs, keep rule, log; `run` until `Budget`, which counts experiments, runs, wall time, $ or a STOP file; `rewind`; post-hoc audit and re-eval), `run()` entry point, `make_agent` |
| `rsi/autoresearch/analysis.py` | `Analyzer` (analysis.ipynb: keep rate, running best, per-keep deltas and their telescoping total, top hits, experiments/hour, `progress.png`), `HiddenAudit` (every keep scored post hoc on hidden iid and shifted splits, written to `groundtruth_all.tsv`), `Reeval` (fresh-seed re-runs, 95% CI) |
| `rsi/autoresearch/executors.py` | `SerialExecutor`, `LocalProcessExecutor` (N concurrent runs), `FakeSlurmExecutor` (sbatch template with optional `--container-image`, PENDING/RUNNING/COMPLETED/FAILED/TIMEOUT/OUT_OF_MEMORY/CANCELLED, `squeue`/`sacct`/`scancel`, queue delay, time limit, `ret-<id>.out/.err`, kcxain-style `wait` polling) |
| `rsi/autoresearch/parallel.py` | `ParallelAutoresearchLoop` (SkyPilot "don't wait": up to N in flight, each judged against the incumbent when it completes, committed only if kept), `MultiChainLauncher` (spawn.sh: N independent branches `autoresearch/<tag>-w<i>`) |
| `rsi/autoresearch/hub.py` | `SharedHub` (autoresearch-at-home coordinator on files + flock: claims with TTL 900 s, exact key plus semantic check >= 0.92, verify-after-2-s; results with full source; agent/global/tier bests with the at-home sanity rules; insights, hypotheses, leaderboard, `analyze`; optional independent `verifier`), `CollaborativeLoop` (one participant: sync every 5 experiments, claim up to 5 alternatives, publish, adopt the swarm best), `SanityRule`, `NgramCosine` |
| `rsi/autoresearch/landscape.py`, `landscape_prepare.py` | `LandscapeTask`: known-truth "Domain C" (Chinchilla-style size/compute law, hyperparameter penalties, seed and nondeterminism noise, a val-specific component, hidden iid/shift splits, wall-clock vs token budgets); runs in process (safe AST knob parser; faithful mode executes the artifact's own, possibly edited, `prepare.py`) or as real subprocesses |
| `rsi/autoresearch/pools.py` | `harness_edit_pool()` for AgentQA-style harnesses (the generic-Domain demo) |
| `rsi/domains/tinylm/` | `prepare.py` (locked: stdlib-docstring corpus with train/val/test_iid shards, language-reference help topics as test_shift, dataloader that is also the hardened budget clock, faithful `evaluate_bpb` that sums `model.forward` losses with upstream's floor-division row count, hardened `evaluate_bpb_locked`), `train.py` (editable numpy byte-level MLP LM: upstream-style constants block, agent-side budget accounting that excludes steps 0-10, schedules as functions of progress, NaN/`loss>100` fast fail, summary block), `task.py` (`TinyLMTask`, edit pool with 24 knob edits, 5 crash edits and 7 exploit edits) |
| `rsi/domains/tabular/` | `prepare.py` (locked: drifting synthetic world, 20k-row train set, 50k-row hidden iid and era-1 shifted test sets, 5-fold stratified CV AUC protocol, `check_per_row`), `train.py` (editable: `featurize` plus HistGradientBoosting knobs), `task.py` (`TabularTask` with a 20 s ceiling budget, HPO/FE/shift-trap/frame-dependent/crash edits) |

## Public API

```python
from rsi.autoresearch import Config, run            # run(...) -> rsi.core.ImprovementResult
res = run(task_or_domain, seed_artifact=None, *, llm_task=None, llm_propose=None,
          config=Config(...), out_dir="runs/x", agent=None, keep_rule=None, program=None)
```

`Config` defaults: `max_experiments=100` (about one night), `mode="hardened"`,
`keep_rule="strict"` (`"rigor"`, `"simplicity"`, `"rrsi"`, a `KeepRule` or any core
`Gate`), `program="upstream"` (a preset or a path to a program.md that people can
edit during the run), `max_fix_attempts=3`, `hidden_audit=True`, `reeval_seeds=0`,
`noise_runs=0`, `val_resample_every=None`, `workers=1` (more than 1 runs the parallel
loop with `executor="local"|"slurm"`), `max_runs` / `max_wall_s` / `max_usd`, and
`overwrite=False` (a run must be fresh).

`res.meta` contains `results_tsv`, `analysis` (Analyzer summary), `audit` (the hidden
table), `reeval`, `counters` (rejected/duplicate/invalid/fixes), `crash_kinds`,
`scope`, `budget_events`, `programs` (the versions used) and `workspace` (branch,
HEAD, resets, rewinds). `res.usage` holds per-role LLM meters (`researcher`,
`researcher_fix`, and `task` for Domain tasks). Files in `out_dir`: `results.tsv`,
`ledger.jsonl` (tree: parent, diff, samples, verdict, program version, crash kind),
`artifacts/` (full source of every attempt), `logs/*.log` (run.log per experiment),
`program.md`, `progress.png`, `groundtruth_all.tsv`, `summary.json`,
`trajectory.json` and `best_artifact/`.

### Applying it to a new problem (copy-paste)

Put the data, the metric and anything the agent must not change in locked files that
call `report(...)` (paste `rsi.autoresearch.task.RECORD_HELPER` into one of them).
Put everything the agent may change in the editable files. A full runnable version is
`experiments/autoresearch/example_new_problem.py` (polynomial ridge regression, R^2
goes from 0.27 to 0.88 in 20 scripted experiments).

```python
from rsi.autoresearch import Config, RunBudget, ScriptResearchTask, run
from rsi.core import get_llm

task = ScriptResearchTask(
    "my-problem",
    {"evaluate.py": open("evaluate.py").read(),        # locked: data + metric, calls report(r2=...)
     "train.py": open("train.py").read()},              # editable: everything the agent may change
    metric="r2", direction="max",
    editable_paths=["train.py"], locked_paths=["evaluate.py"],
    run_cmd=["python", "train.py"],
    budget=RunBudget(kind="ceiling", amount=60, kill_after=120),   # or kind="wallclock" + a locked clock
    description="What the model does and how it is scored (shown to the agent).")

res = run(task, llm_propose=get_llm("claude:haiku", cache_dir=".rsi_cache/my-problem"),
          config=Config(max_experiments=50, mode="hardened", reeval_seeds=3), out_dir="runs/my-problem")
print(res.meta["analysis"]["top_hits"]); print(res.best["train.py"])
```

The loop can also improve an existing `rsi.core.Domain`, such as a harness, a prompt
set or a solver. One experiment is then one fixed-size evaluation on `evolve`, and
the hidden audits are `holdout` and `ood`:

```python
from rsi.autoresearch import Config, MockResearchAgent, run
from rsi.autoresearch.pools import harness_edit_pool
from rsi.domains.agentqa import AgentQADomain, SimModel

dom = AgentQADomain()
res = run(dom, dom.seed_artifact(), llm_task=SimModel(dom.tasks),        # llm_propose=... for a live agent
          agent=MockResearchAgent(harness_edit_pool()), task_kwargs={"k": 2}, config=Config(max_experiments=10))
```

To use a different keep rule, pass for example `keep_rule=BootstrapRigorKeep()`,
`GateKeep(RRSIGate())` or `Config(keep_rule="rigor")`. For parallel or cluster runs,
use `Config(workers=3, executor="slurm")` or construct
`ParallelAutoresearchLoop(..., pool=FakeSlurmExecutor(...))`. For collaborative runs,
create one `SharedHub(dir)` and a `CollaborativeLoop(task, agent, cfg, hub=hub,
agent_id=...)` per participant.

## Capability checklist (spec section 10): code, experiment, result

Status: **done** = implemented and exercised by a test or experiment. **partial** = the mechanism exists but the claim is not fully measured. **n/a** = nothing to implement.

| # | Claim (overview / ports) | Code | Evidence | Status |
|---|---|---|---|---|
| 1 | Agent edits one training script, trains a fixed budget, keeps if improved, repeats | `AutoresearchLoop`, `ResearchAgent`, `ScriptResearchTask`, `RunBudget`, `StrictKeep`, `Workspace` | E1, `test_loop_end_to_end` | done |
| 2 | ~12 experiments/hour, no human in the loop | `AutoresearchLoop.run` (no input(); stops only on `Budget`/STOP file), `Analyzer` experiments/hour | E1 (about 1,290/h at a 2 s budget), `test_stop_file_halts` | done (LivenessNudger not needed: the loop is framework-driven) |
| 3 | What changes: training code of a small LM | `TinyLMTask` (`editable_paths=("train.py",)`) | E1, E2, E4, E5 | done |
| 4 | Fixed: data, tokenizer, evaluation code, budget | `locked_paths`, `sealed_files`, `ScopeGuard.locked_hashes_ok`, locked loader clock | E4, `test_scope_guard` | done |
| 5 | Strictly better keeps; equal or worse resets | `StrictKeep` + `Workspace.reset_to` | `test_keep_rules` (equal => reset), `test_loop_end_to_end` | done |
| 6 | The strategy is improved by a person editing program.md | `ProgramSpec` (versioned, `edit()`, reloaded between experiments) | `test_program_md_edited_by_human_mid_run` | done (E7 study not run) |
| 7 | One GPU; ports on Macs | CPU domains; `Executor` abstraction | E10 | partial (E9 backend-port study not run) |
| 8 | prepare.py locked => agent can't change grading | `ScopeGuard`, sealed restore + chmod + hashes, logits-only evaluator, framework-owned record | E4, E8 | done |
| 9 | train.py: anything goes | no restriction inside `editable_paths`; `LLMResearchAgent` rewrites whole files | live smoke | done |
| 10 | program.md is what the person edits | `ProgramSpec` | tests | done |
| 11 | Agent reads instructions + current code, decides | `AgentContext` (rendered program.md, in-scope files, results.tsv, kept-commit log) | live smoke prompt | done |
| 12 | Exactly N minutes wall clock (startup/compile excluded), reads val_bpb | `RunBudget(warmup_excluded_steps=11)`, tinylm `train.py` accounting + locked loader clock, `parse_summary` | `test_tinylm_modes_and_record` | done |
| 13 | Results table: commit, score, memory, status, description | `ResultsLog` (exact 5 columns), peak RSS as memory | `test_results_tsv_exact_format` | done |
| 14 | Improved => commit stays; else reset | `_settle` + `Workspace` | HEAD == last keep checked in E8 and tests | done |
| 15 | Fixed budget makes experiments comparable | `RunBudget` kinds | E2 | done |
| 16 | bpb independent of vocabulary | locked byte-length table (`TOKEN_BYTES`) in `evaluate_bpb` | none (E3 not run; byte-level vocab only) | partial |
| 17 | One editable file keeps changes reviewable | single `editable_paths`, linear kept chain, per-keep diffs in ledger, `Analyzer` top hits | E1 | done |
| 18 | House rules: kill > 2x, fix trivial, skip broken, rewinds rare | `RunBudget.hard_timeout`, `CrashPolicy`, `fix_crash`, `AutoresearchLoop.rewind` (counted) | E8, `test_rewind_and_multichain` | done |
| 19 | Example log: baseline, LR keep, GeLU discard, OOM crash | `ResultsLog` conventions | E8 TSV | done |
| 20 | MLX first night 2.667 -> 1.808; findings didn't carry to another Mac | fixed-budget bias + machine transfer | E2 (landscape "slow vs fast machine"; tinylm 1x vs 4x) | done (qualitative) |
| 21 | MLX port: same rules, no CUDA | backend-agnostic `ResearchTask` | tinylm/tabular/landscape/Domain run the same loop unchanged | done |
| 22 | Cluster fork: job scheduler inside a container | `FakeSlurmExecutor` (sbatch + container flags, squeue/sacct/scancel, time limit), `ParallelAutoresearchLoop`, `MultiChainLauncher` | E10, `test_fake_slurm_states_and_sbatch` | done (fake scheduler) |
| 23 | Collaborative: claim first, publish results + code, shared leaderboard/best | `SharedHub`, `CollaborativeLoop` | E11, `test_hub_claims_and_bests` | done |
| 24 | Non-LLM adaptation (GBT vs held-out metric) | `TabularTask`, `HiddenAudit`, `check_per_row` | E12 | done |
| 25 | Readable log by morning; person's job moves up a level | `Analyzer` report + `progress.png`, `ProgramSpec` | E1 figures | done |
| 26 | Steady unattended progress when comparable + grader out of reach | whole loop, hardened mode | E1 + E4 | done |
| 27 | Watch out: strict keep locks in luck | `NoiseCalibrator`, `Reeval`, `BootstrapRigorKeep`, `GateKeep(MinGain)` | E5 | done |
| 28 | Watch out: same val set judges every experiment | `HiddenAudit` (iid and shift reported separately), `val_resample_every` | E6 | done (effect shows in landscape; null at small N on tabular) |
| 29 | Watch out: budget favours small/fast models on that machine | `RunBudget` kinds (wallclock vs tokens), long-horizon re-eval | E2 | done |
| 30 | Watch out: one chain, one idea; strategy changes only via person | single chain (default) vs `ParallelAutoresearchLoop`; `ProgramSpec` versions | E10 (grid-likeness), program versioning test | partial (E7 not run) |
| 31 | Side-by-side summary row | all of the above; results.tsv fed back as history; ledger tree | n/a | done |
| 32 | "Locks the grader away" (socially only upstream) | faithful vs hardened modes | E4 | done |
| 33 | RRSI fixes the keep rule; Dream-RSI replays runs | `GateKeep(RRSIGate())`; `ledger.jsonl` + `artifacts/` (full source, parent, diff) as a replay world | `test_keep_rules` | done |
| 34 | Agent stays the same; improves a separate model | `ResearchAgent` is stateless; only `editable_paths` change | by construction | done |
| 35 | Runnable at home | CPU domains, offline agents, `--llm claude:haiku` | all experiments | done |
| 36 | Successes are anecdotal | multi-seed experiments with 95% bootstrap CIs, hidden audits, re-evals | E1-E12 | done |
| 37 | Not EvoMap's "AutoResearch" | naming note | n/a | n/a |
| 38 | SoL-Pi runs autoresearch-style loops across environments | `AutoresearchLoop` works with any `ResearchTask`/`Domain` (multi-task keep rules belong to SoL-Pi) | generic Domain demo | done |
| 39 | Measure noise first / keep a set the loop never sees / log as a tree / never let the loop grade itself | `NoiseCalibrator`; `HiddenAudit`; `Ledger` tree; hardened framework-owned grading | E1 noise band, E4, E6 | done |

## Deviations from the spec and from upstream

1. **The framework runs the loop.** Upstream's coding agent runs git, trains, greps, logs and decides by itself. Here the agent only proposes edits. The framework commits, runs, parses, applies the keep rule and logs, which is the spec's hardened design (section 9.1). Faithful mode reproduces upstream's *measurement* weaknesses (grader locked by instruction only, metric grepped from the agent-written log, budget clock in editable code, loss summed from the agent's `forward`, evaluated row count depends on batch size). It does not reproduce the agent operating git and the shell itself. `AgentEditor` uses file tools only.
2. **Workspace.** Commits are content-addressed in memory by default. `backend="git"` drives a real repository under `out_dir/workspace`. The default avoids nested repositories inside this checkout. `results.tsv` is never committed. Reusing an `out_dir` needs `Config(overwrite=True)`, mirroring "the branch must not already exist".
3. **tinylm stands in for nanochat.** It is a numpy byte-level MLP LM, not a GPT with Muon. The default budget is 25 s (the spec suggests 30 s). Experiments use 1.5-2 s budgets to stay within the CPU allowance. Evaluation covers 32 KB of val with a byte vocabulary, so `TOKEN_BYTES` is all ones. Memory is peak RSS as reported by the run (the VRAM analogue). `kill_after` defaults to `2 x budget + 5 s`, because startup and evaluation are a larger share of a few-second budget than of upstream's 5 minutes.
4. **Budget kinds.** `tokens` (a fixed number of training bytes, enforced by the locked loader) and `ceiling` (the xgboost port's wall-time cap, where over-budget runs become `discard`) were added next to `wallclock`.
5. **Crash triviality.** `CrashPolicy` decides mechanically from the traceback's exception class (SyntaxError, NameError, ImportError...) instead of by agent judgment, and allows at most `max_fix_attempts=3`. An LLM agent can still decline with `GIVE_UP`. Timeouts are logged as `crash` (upstream says "discard and revert" and leaves the TSV status open).
6. **Hardened rejections** are logged as `discard` rows with metric `0.000000` and a description starting `REJECTED (...)`. They were never run. In the ledger their status is `rejected`.
7. **BootstrapRigor** defaults to `vary_seed=True` (the spec's suggestion). `vary_seed=False` reproduces `rigor.py` literally (repeats of the pinned seed) and appears as the `rigor_pinned` arm of E5.
8. **at-home hub.** Ensue is replaced by JSON files under `flock`. Embeddings are replaced by char-3-gram cosine similarity at the faithful 0.92 threshold. `VERIFY_DELAY` is configurable (2 s by default, 0.02 s in the experiments). Tiers are user-supplied strings, not detected VRAM. A participant keeps against its own branch tip and adopts the swarm best every 5 experiments (upstream's prompt is ambiguous here). The optional `verifier` is an addition (the hardened fix), not part of at-home.
9. **SLURM** is a local fake scheduler. There is no real `sbatch`; container flags are rendered but not executed. Each job gets its own workdir, which avoids the containerized fork's shared-root race.
10. **Tabular** uses scikit-learn HistGradientBoosting instead of XGBoost and a synthetic drifting world instead of airline data, with a 20 s ceiling instead of 1 min. The CV protocol lives in the locked `prepare.py` (in xgbmin it is in `train.py`).
11. **Domain tasks** have a *soft* `kill_after`: the evaluation thread is abandoned rather than killed. Script tasks have hard kills.
12. **Not run as experiments:** E3 (bpb vs vocabulary), E7 (program.md variants with a live agent), E9 (backend port) and E13 (frame-dependent features). E13's mechanism (the `fe_origin_count` edit plus the mechanical per-row check) is implemented and tested. The `LivenessNudger` is not needed because the loop never idles.

## Limitations

* **Hardened mode is not a security boundary.** It blocks the catalogued exploit classes. It does not stop in-process monkeypatching of the locked evaluator, training on the validation shard by reading the file directly (only the loader refuses it), or filesystem searches for hidden data. For those, run the evaluator in a separate process or container.
* **Wall-clock budgets are noisy on a shared 4-core machine.** tinylm's run-to-run sd at a 2 s budget was about 0.02-0.06 bpb, depending on load from other jobs. That is realistic ("~0.03 on Apple Silicon") but it widens CIs, and throughput numbers depend on load.
* **The landscape encodes its effects by construction** (Chinchilla-style law, val-specific component, shift penalty). Landscape experiments show what the *loop* does with such a world. They are not evidence that real training behaves this way. tinylm and tabular supply the real-training evidence.
* **The scripted agents are hand-written.** Their pools, including the "combine near-misses" fallback, shape keep rates and gains. Live-LLM behaviour was only smoke-tested (one run).
* **Experiments are small:** 20-36 experiments per tinylm/tabular night and 2-3 seeds. At those decision counts the validation-reuse effect is not measurable on tabular, in line with the xgboost port's evidence.

## Core change requests (not applied; `rsi/core` untouched)

1. `Ledger(path)` silently appends to an existing JSONL file, which merged two runs during development. Please add an explicit `resume=False` (truncate) vs `resume=True` flag. The loop works around it with `Config(overwrite=...)` and refuses a used `out_dir` by default.
2. `sandbox.run_cmd`: add a `merge_stderr` option to reproduce upstream's `> run.log 2>&1` interleaving, and return the child's peak RSS (via `os.wait4`) so memory is measured by the framework instead of self-reported by the run.
3. `stats.bootstrap_ci` resamples in a Python loop (5,000 iterations) and dominates `Reeval` time in many-seed simulations. Please vectorize it the way `prob_better_bootstrap` is.
4. `Domain`/`Evaluator`: an optional process-isolated evaluation with a hard timeout (and meter merging), so `DomainResearchTask` can kill a hung harness instead of abandoning the thread.
5. `AgentEditor`: an optional restricted run/Bash tool (and read-only mounts for locked files), so a live faithful mode can let the agent run training and git itself as upstream does.
6. `ArtifactStore`: an in-memory mode. Loops with `persist=False` still write every attempt to disk.
