# autoresearch: implementation notes (`rsi.autoresearch`)

This package implements Karpathy's autoresearch loop (March 2026) and the community
ports described in `docs/methods/autoresearch.md`. It works on any problem that can be
written as a `ResearchTask` (a set of locked and editable files plus a run command) or
as any `rsi.core.Domain` plus a seed `Artifact`. Everything runs offline with scripted
agents, and every experiment script also accepts `--llm claude:haiku`.

**Two modes.** `faithful` keeps upstream's semantics, weaknesses included, so they can
be measured: the grader is locked only by instruction, the metric is grepped from a
log the agent's code writes, and the budget clock lives in editable code. `hardened`
(the default) locks those things mechanically: `ScopeGuard` (locked-file edits rejected,
plus a tamper scan of added lines), `BudgetEnforcer`, a logits-only evaluator with a
causality probe, locked evaluators that score from import-time snapshots and check their
own integrity, a result record owned by the framework, and hidden data that is not
mounted. The framework runs git, the runs, the parsing, the keep rule and the logging, so
the agent only proposes edits. Upstream let the agent do all of this itself (see
Deviations). Hardened mode blocks the catalogued exploit classes; it is not a security
boundary against a determined adversary (see Limitations).

## Results at a glance

All numbers come from `results/autoresearch/<name>.json`, where each file also holds the configuration, the per-seed raw values and a verdict. Figures are the matching `.png` files. Brackets are 95% bootstrap CIs (`rsi.core.stats.summarize_runs`). The runs used the offline scripted agent on a shared 4-core machine whose load average was 4-8 from other jobs during the final re-runs (25 Sep 2026, after the review fixes). tinylm has a wall-clock budget, so machine load moves single runs (±15% training compute, about ±0.06 bpb under that load); the tinylm numbers below are therefore noisier than the landscape and tabular ones, which are deterministic. Every script is re-runnable with `python experiments/autoresearch/<name>.py [--llm sim|claude:haiku] [--seeds N] [--quick]`.

| Exp | Claim | Setup (full settings) | Result | Reproduced? |
|---|---|---|---|---|
| **E1** `e1_progress` | Steady unattended progress | tinylm (2 s budget, hardened), 30 experiments x 3 seeds; noise band from 5 baseline re-runs; best-of-N random search at equal count; landscape 30 seeds x 100; AgentQA via `DomainResearchTask` 3 seeds x 10 | tinylm val_bpb gain: greedy **0.63 [0.47, 0.81]** recorded and **0.63 [0.45, 0.82]** on a fresh-seed honest re-eval, vs a baseline noise band delta of **0.079**; random search (best of N) 0.30 recorded, 0.26 [0.04, 0.55] honest. Keep rate 33% [30, 37]; about 1,410 experiments/h at a 2 s budget. OOM, NaN and trivially broken runs (2 fixed) were handled unattended. Landscape true gain 0.069 [0.067, 0.070] (greedy) vs 0.050 [0.048, 0.053] (random). AgentQA: holdout +0.53 (0.43-0.45 -> 0.95-0.98), OOD +0.49 (0.38-0.50 -> 0.92-0.94) | **yes** |
| **E2** `e2_budget_bias` | Fixed budget favours small/fast models on this machine | Landscape 30 seeds x 100 (wall-clock vs token budget; re-eval at 4x/16x; slow vs fast "machine"); tinylm 20 experiments x 2 seeds, 2 s wall clock vs a fixed 100k-byte token budget, winners re-trained at 4x | Landscape: final model size **0.25x [0.24, 0.26]** of the baseline under wall clock vs **2.9x [2.8, 3.1]** under tokens. Kept gain **+0.069 (1x) -> +0.008 (4x) -> -0.037 (16x)**: it shrinks and then reverses at longer horizons. The slow machine's winner gains +0.025 on the fast machine vs +0.038 for the fast machine's own winner. tinylm (real training): parameter ratio final/base **0.75** under wall clock (both nights keep HIDDEN 128 -> 64 at some point) vs **4.6** under tokens (HIDDEN up to 1024). The wall-clock winners' gain shrinks from **0.71 bpb at 1x to 0.25 at 4x** (it does not reverse; part of it is the baseline's low LR mattering less at longer horizons) | **yes** |
| **E3** `e3_bpb_vocab` | bpb does not depend on the vocabulary, so tokenizer changes are compared fairly | tinylm corpus; bytes vs BPE with 256/768/1792 merges (vocab 512/1024/2048) x n-gram order 1-3, both metrics from the same token log-probs; then a loop on a new in-process `ResearchTask` (train.py = VOCAB, ORDER, ALPHA) judged by val bpb vs by val per-token loss, 30 experiments x 5 seeds | Per-token loss rises with the vocabulary at every order, while bpb falls; the two metrics disagree on **59%** of the model pairs that differ in vocabulary and on 0% of the pairs that share one. Per-token loss picks the byte trigram (**2.87 bpb**); bpb picks the 1024-token trigram (**1.96 bpb**), and ranking by bpb equals ranking by the likelihood of the val byte stream. The loop judged by bpb moves to vocab 2048 in every seed (val **1.86**, hidden test_iid **1.92** bpb); judged by per-token loss it never keeps a vocabulary change (tried 7-16 times per night) and ends at the byte trigram (val **2.87**, test_iid **2.90** bpb). The task is noise-free, so all 5 seeds agree | **yes** |
| **E4** `e4_grader_locking` | Locking the grader keeps the loop honest; upstream locks only socially | Battery: 11 exploit classes x faithful/hardened x 3 seeds on tinylm, each run paired with a baseline run and also run at equal compute under a token budget (deterministic); loop with 15% exploit injection (24 experiments x 2 seeds per mode); landscape grader edit 30 seeds x 60 | Faithful: **9 of 11 classes win bogus gains** that the locked measurement of the same file does not confirm (equal-compute bpb gains: grader edit 0.37, loss rescale 0.81, train-on-val 0.04, fake report 3.03, future-byte peek 3.73, TOKEN_BYTES rebind 0.37, in-place mutation 2.01, numpy patch 0.43; the 2x budget clock claims 0.30 under a wall-clock budget, twice the honest schedule effect); the other two are no-ops in faithful mode (0.0). Hardened: grader edit rejected; the 5 in-process exploits rejected by the tamper scan; train-on-val, fake report and future peek crash as violations; loss rescale and budget clock run with **exactly 0.0 gain at equal compute** (the budget clock's 0.20 wall-clock gain is the legitimate LR-schedule effect: the honest no-warmdown control gains 0.15, difference 0.05 < noise band 0.075). Without the tamper scan, the locked evaluator's integrity check still stops every in-process route except rewriting the result record (0.30). Loop: faithful kept 3 of 9 injected exploits and **both final "bests" (1.0 bpb, a fake report) are invalid under locked grading**; hardened kept 0 of 7, and its recorded best is 0.047 [0.031, 0.063] bpb optimistic against a fresh-seed re-eval (within the 0.075 noise band: E5's winner's curse, not an exploit). Landscape recorded-minus-true: faithful -0.055, hardened -0.004 | **yes** |
| **E5** `e5_strict_luck` | "Strictly better" on one noisy number locks in luck | Landscape (exact truth) 50 seeds, 5 keep rules at **equal total training runs (150)**; tinylm strict vs rigor at 27 runs x 3 seeds, 5 fresh-seed re-evals | Keeps whose TRUE delta <= 0: strict **19% [16, 22]**, rigor **4.6% [2.9, 6.6]**, rigor with the pinned seed (rigor.py literally) 12% [9, 15], noise-band gate 1%. Seed-only keeps per night: strict 1.18, rigor 0.26. Optimism gap (honest minus recorded): strict **0.0041 [0.0035, 0.0046]**, rigor **0.0015 [0.0011, 0.0018]**, pinned rigor 0.0035. **Trade-off:** true final quality is best under strict (0.9563 vs rigor 0.9579, noise band 0.9633) because rigor tests about 25% fewer ideas at equal compute (112 vs 149). tinylm (real wall-clock noise, 3 seeds): strict made 1.3 seed-only keeps per night and rigor none, but the optimism gap is small for both and **not smaller under rigor** (strict 0.007 [-0.008, 0.019], rigor 0.013 [0.008, 0.018] bpb); honest gain strict 0.58, rigor 0.50 | **yes on the landscape** (luck is locked in; rigor shrinks it at a search-efficiency cost); **not resolved on tinylm** at 3 seeds |
| **E6** `e6_val_reuse` | Reusing one validation set slowly tunes to it | Landscape 40 seeds x 300 experiments, val-specific sd {0.0015, 0.005}, fixed vs re-sampled val every 25; tabular 30 experiments x 2 seeds, fixed vs re-sampled CV folds every 10, HiddenAudit of every keep | Landscape (sd 0.005): optimism of the kept chain on its own val grows from **0.0084 (50 experiments) to 0.0128 (300)** with a fixed val; the pure val-specific part grows 0.0064 -> 0.0092 (growth CI [0.0008, 0.0052]). With val re-sampled it stays at **0.0086** (val-specific 0.0062, no growth), and true quality is better (0.958 vs 0.961). The effect is small at sd 0.0015 (0.0062 vs 0.0046). Tabular at 30 decisions: the CV minus hidden-iid gap does **not** grow (0.0087 -> 0.0074 AUC), a null result like the xgboost port's. Separately, the shift trap `x7` is kept in every seed (CV and iid up, shifted test -0.042): distribution shift, not overfitting | **partly**: yes on the landscape; null on real GBT at this N (as the sources report) |
| **E7** `e7_program_md` | The strategy improves only when a person rewrites program.md | Landscape, presets upstream / xgboost / simplify, same seeds, agent through the LLM-agent code path | **Offline control only.** A program-blind agent (the scripted MockLLM) produces identical nights under all three program.md versions, and every ledger node of a night carries the same program version (the loop never edits program.md; a human edit mid-run is picked up, see `test_program_md_edited_by_human_mid_run`). The comparison itself needs a live agent: `python experiments/autoresearch/e7_program_md.py --llm claude:haiku` | **not run live** (harness verified) |
| **E8** `e8_house_rules` | Kill > 2x budget, fix trivial crashes, skip broken ideas, rare rewinds | tinylm scripted night (typo, missing import, hang, OOM, NaN, honest edits, grader edit) in both modes; never-fixable crash; landscape 30 seeds x 60 with 20% crash edits | All 19 checks pass. Typo and missing import fixed once and re-run. Hang killed at kill_after (8.0 s for a 1.5 s budget). NaN fast-fails in 0.14 s (FAIL). OOM logged. Crash rows are `0.000000 / 0.0`. HEAD equals the last kept commit. Give-up after p_fix=0 and after 3 failed fixes. Landscape: 208 crashes (90 OOM, 74 timeouts, 44 NaN) and 144 trivial fixes, all invariants hold, 0 rewinds | **yes** |
| **E10** `e10_executors` | Scheduler/cluster forks: async submission and polling | Script-mode landscape (real subprocesses, 1.2 s runs), 36 experiments x 3 seeds, 10% crash edits: sequential vs 3 local workers vs fake SLURM (3 nodes, queue delay, container image); tinylm CPU-bound check (12 experiments) | Throughput **2.9x** (local-3) and **2.6x** (fake SLURM); tinylm 2.5x. Time to a common best **11 s / 15 s vs 47 s** sequential. States: OOM -> OUT_OF_MEMORY, NaN/typo -> FAILED, hang -> TIMEOUT. **34% / 32%** of parallel experiments were judged against a newer incumbent than they were proposed from (the grid-like search SkyPilot reports). At an equal experiment count the true quality is about the same (0.965 / 0.968 vs 0.969, overlapping CIs). On CPU-bound tinylm the 3 parallel runs share the cores, so each trains less inside its wall-clock budget and the parallel night ended worse (best 3.74 vs 3.56 bpb): parallel wall-clock runs are only comparable when each has its own hardware | **yes** (throughput, states, time-to-best) |
| **E11** `e11_hub` | Collaborative: claim before running, publish full code, shared best | 4 agents (threads) x 20 experiments on a file-backed `SharedHub`, 5 seeds; one agent lies (blatant 0.30 / subtle -0.03) under {no filters, at-home sanity filters, filters + independent re-verification} | Claiming halves cross-agent duplicates: description-level **0.24 [0.22, 0.26] vs 0.48 [0.45, 0.52]**, code-level **0.10 vs 0.22**, and the swarm's final best has better true quality with claiming (0.961 vs 0.968 without). The swarm reaches a common best in **3.4 s vs 9.8 s** of wall time for one agent at equal total compute (80 runs), with the same true quality (0.961 vs 0.961). Liar owns the global best: no filters **100%**; filters vs blatant 0%; filters vs **subtle 100%** (only 17% of global-best updates survive an independent re-eval); **filters + verify 0%** (93% survive) | **yes** |
| **E12** `e12_gbt` | Non-LLM adaptation (GBT vs a held-out metric) | tabular, 25 experiments x 3 seeds: FE+HPO agent vs HPO-only agent vs random HPO (best of N); hidden iid + shifted audits of every keep | FE+HPO: CV **+0.026**, hidden iid **+0.028**, shifted **-0.043** (the `x7` shift trap is kept in all seeds). HPO-only: CV +0.005, iid +0.0035, shift +0.003. Random HPO: iid +0.0034 (the agent's HPO is no better than random HPO). FE adds **+0.024 iid AUC** beyond HPO. CV and iid move in the same direction on 71% of kept steps | **yes** |
| **E13** `e13_frame_dependent` | Frame-dependent features inflate CV but break at audit time (xgboost commit `9d6ee8b`) | tabular; 4 frame-dependent features (counts, frequencies, frame max, frame z-score), each paired with a legal twin fitted on `train`; deterministic | The mechanical per-row check flags **100%** of the frame-dependent features and **0%** of the twins; a prompt rule alone flags nothing. CV cannot tell a feature from its twin (identical CV). On the 50k-row audit frame the worst one (bucket counts) loses **0.006** iid AUC vs its twin; the others are harmless here. None of them raises CV: no labels reach `featurize`, so the port's CV inflation cannot occur in this setup | **partly** (detection yes; damage small; no CV inflation) |
| live smoke (review) `example_new_problem --llm claude:haiku --quick` | One command away on a new problem | polynomial-ridge template, 6 experiments proposed by Claude Haiku 4.5 (RewriteEditor, cached under `.rsi_cache/autoresearch`), hardened | 7 LLM calls, **$0.20**, 19k input / 37k output tokens, 6.6 min wall (LLM latency dominates). 4 keeps (DEGREE 1->5, RIDGE 1.0->0.5->0.2), 3 honest discards (DEGREE 7, DEGREE 6, SCALE 3). R^2 0.274 -> 0.878, the same level the scripted agent reaches in 20 experiments | works |
| live smoke (original) `e1_progress --llm claude:haiku --seeds 1 --quick` | One command away | tinylm 2 s budget, 8 experiments proposed by Claude Haiku 4.5 (24 Sep, before the review fixes) | 8 LLM calls, **$0.47**, 78k input / 63k output tokens, 0 parse failures. 4 keeps (LR 0.003->0.008->0.01, WARMDOWN 0.5->0.1), 4 honest discards. val_bpb 3.83 -> 3.32 on a 3-seed honest re-eval | works |
| example `example_new_problem` | Template for a new user problem | polynomial ridge regression, 20 scripted experiments, hardened mode with a pasted `RECORD_HELPER` | R^2 0.274 -> 0.878 (DEGREE 1->3->5->7, SCALE, RIDGE) | works |

Not run: E9 (backend port, e.g. numpy float64 vs float32) and the live E7 comparison.

## Module map

| File | Contents |
|---|---|
| `rsi/autoresearch/task.py` | `ResearchTask` (editable/locked paths, `RunBudget`, `parse_summary` where an empty grep means a crash, hidden `audit`), `ScriptResearchTask` (files run as a subprocess in a fresh per-experiment workdir; watchdog kill via `rsi.core.sandbox.run_cmd`; faithful log-grep vs hardened framework-owned record; locked files restored, chmod 0444 and hash-checked before and after the run; `record_checks` turn locked-record flags into crashes), `DomainResearchTask` (adapter for any `rsi.core.Domain`), `RunOutcome`, `RECORD_HELPER` |
| `rsi/autoresearch/guard.py` | `ScopeGuard` (out-of-scope and locked-file edits become `Violation`s and are rejected before running; tamper scan of added lines via `default_tamper_patterns`: framework env vars, private names of a locked module, rebinding / subscript-assigning / mutating its attributes, `vars()`/`setattr`/`__dict__`/`sys.modules`, aliasing it with `import m as`, dynamic imports and frame/gc introspection, rebinding attributes of numpy/math/time/os/sys/sklearn/...; `locked_hashes_ok`, `restore`, `lock_dir`), `BudgetEnforcer` (watchdog timeout, classification of over-budget and killed runs), `CrashPolicy` (kinds: trivial / timeout / oom / nan / violation / error; at most `max_fix_attempts` fixes, trivial kinds only) |
| `rsi/autoresearch/results.py` | `ResultsLog`, the exact 5-column `results.tsv` (`commit <metric> memory_gb status description`, 6 dp, crash = `0.000000`/`0.0`, never committed); `Workspace`, the kept-commit chain `autoresearch/<tag>` (`commit`, `amend`, `reset_to`, `rewind`, `log`), in-memory or real git (`backend="git"`, whose working tree mirrors each version exactly, nested files included) |
| `rsi/autoresearch/keep.py` | `KeepRule` plug-ins: `StrictKeep` (upstream), `BootstrapRigorKeep` (MLX `rigor.py`: 3 runs, P >= 0.95, 20,000 resamples, rng 1234, early reject, never-repeat; `vary_seed` switch), `SimplicityWeighted`, `GateKeep` (any core gate, e.g. `RRSIGate`, `MinGain`); `NoiseCalibrator`; `make_keep_rule` |
| `rsi/autoresearch/program.py` | `ProgramSpec`: the human-owned, content-versioned `program.md` (presets `upstream`, `xgboost`, `simplify`); `render(task, mode)` fills in metric, budget, files and interface contract; `edit()` is the only way a new version is made; a loop given a path re-reads it between experiments |
| `rsi/autoresearch/agent.py` | `AgentContext`; `LLMResearchAgent` (via `RewriteEditor` or `AgentEditor`; `fix_crash` path with a `GIVE_UP` escape); `MockResearchAgent` (scripted `ScriptedEdit` pool of helpful/neutral/harmful/crash/exploit edits; greedy coordinate search / random / fixed schedule; exploit and crash injection rates; `p_fix`; "combine previous near-misses" when out of ideas); `RandomSearchAgent` (best-of-N baseline); `knob_edit`, `text_edit`; `scripted_llm` (a MockLLM that answers RewriteEditor prompts) |
| `rsi/autoresearch/loop.py` | `Config`, `AutoresearchLoop` (setup, baseline first, `step` = propose, scope check, commit, run, fix, extra runs, keep rule, log; `run` until `Budget`, which counts agent turns, runs, wall time, $ or a STOP file; `rewind`; post-hoc audit and re-eval), `run()` entry point, `make_agent`, `agent_llms` (the backends an agent meters on, so usage and `max_usd` see them) |
| `rsi/autoresearch/analysis.py` | `Analyzer` (analysis.ipynb: keep rate, running best, per-keep deltas and their telescoping total, top hits, experiments/hour, `progress.png`), `HiddenAudit` (every keep scored post hoc on hidden iid and shifted splits, written to `groundtruth_all.tsv`), `Reeval` (fresh-seed re-runs, 95% CI) |
| `rsi/autoresearch/executors.py` | `SerialExecutor`, `LocalProcessExecutor` (N concurrent runs), `FakeSlurmExecutor` (sbatch template with optional `--container-image`, PENDING/RUNNING/COMPLETED/FAILED/TIMEOUT/OUT_OF_MEMORY/CANCELLED, `squeue`/`sacct`/`scancel`, queue delay, time limit, `ret-<id>.out/.err`, kcxain-style `wait` polling) |
| `rsi/autoresearch/parallel.py` | `ParallelAutoresearchLoop` (SkyPilot "don't wait": up to N in flight, each judged against the incumbent when it completes, committed only if kept), `MultiChainLauncher` (spawn.sh: N independent branches `autoresearch/<tag>-w<i>`) |
| `rsi/autoresearch/hub.py` | `SharedHub` (autoresearch-at-home coordinator on files + flock: claims with TTL 900 s, exact key plus semantic check >= 0.92, verify-after-2-s; results with full source; agent/global/tier bests with the at-home sanity rules; insights, hypotheses, leaderboard, `analyze`; optional independent `verifier`), `CollaborativeLoop` (one participant: sync every 5 experiments, claim up to 5 alternatives, publish a result, an insight and a hypothesis every time, adopt the swarm best), `SanityRule`, `NgramCosine` |
| `rsi/autoresearch/landscape.py`, `landscape_prepare.py` | `LandscapeTask`: known-truth "Domain C" (Chinchilla-style size/compute law, hyperparameter penalties, seed and nondeterminism noise, a val-specific component, hidden iid/shift splits, wall-clock vs token budgets); runs in process (safe AST knob parser; faithful mode executes the artifact's own, possibly edited, `prepare.py`) or as real subprocesses |
| `rsi/autoresearch/pools.py` | `harness_edit_pool()` for AgentQA-style harnesses (the generic-Domain demo) |
| `rsi/domains/tinylm/` | `prepare.py` (locked: stdlib-docstring corpus with train/val/test_iid shards, language-reference help topics as test_shift, dataloader that is also the hardened budget clock, faithful `evaluate_bpb` that sums `model.forward` losses with upstream's floor-division row count, hardened `evaluate_bpb_locked` that scores from private read-only import-time snapshots of its constants, byte table and library functions and records an integrity check `_tampered()`), `train.py` (editable numpy byte-level MLP LM: upstream-style constants block, agent-side budget accounting that excludes steps 0-10, schedules as functions of progress, NaN/`loss>100` fast fail, summary block), `task.py` (`TinyLMTask`, edit pool with 24 knob edits, 5 crash edits and 11 exploit edits; record checks `_untampered`, `_causal`, `_overrun`) |
| `rsi/domains/tabular/` | `prepare.py` (locked: drifting synthetic world, 20k-row train set, 50k-row hidden iid and era-1 shifted test sets, 5-fold stratified CV AUC protocol, `check_per_row`, the same snapshot + integrity scheme as tinylm in hardened mode, live library lookups in faithful mode), `train.py` (editable: `featurize` plus HistGradientBoosting knobs), `task.py` (`TabularTask` with a 20 s ceiling budget, HPO/FE/shift-trap/frame-dependent/crash edits) |
| `experiments/autoresearch/` | `e1_progress`, `e2_budget_bias`, `e3_bpb_vocab` (also defines the in-process `TokenizerTask`), `e4_grader_locking`, `e5_strict_luck`, `e6_val_reuse`, `e7_program_md`, `e8_house_rules`, `e10_executors`, `e11_hub`, `e12_gbt`, `e13_frame_dependent`, `example_new_problem`; shared helpers in `_common.py` |

## Public API

```python
from rsi.autoresearch import Config, run            # run(...) -> rsi.core.ImprovementResult
res = run(task_or_domain, seed_artifact=None, *, llm_task=None, llm_propose=None,
          config=Config(...), out_dir="runs/x", agent=None, keep_rule=None, program=None)
```

`Config` defaults: `max_experiments=100` (about one night; counts agent turns, including
proposals that were rejected, invalid or duplicates), `mode="hardened"`,
`keep_rule="strict"` (`"rigor"`, `"simplicity"`, `"rrsi"`, a `KeepRule` or any core
`Gate`), `program="upstream"` (a preset or a path to a program.md that people can
edit during the run), `max_fix_attempts=3`, `hidden_audit=True`, `reeval_seeds=0`,
`noise_runs=0`, `val_resample_every=None`, `workers=1` (more than 1 runs the parallel
loop with `executor="local"|"slurm"`), `max_runs` / `max_wall_s` / `max_usd`, and
`overwrite=False` (a run must be fresh). Use `max_runs` for equal-compute comparisons.

`res.meta` contains `results_tsv`, `analysis` (Analyzer summary), `audit` (the hidden
table), `reeval`, `counters` (rejected/duplicate/invalid/fixes), `crash_kinds`,
`scope`, `budget_events`, `programs` (the versions used) and `workspace` (branch,
HEAD, resets, rewinds). `res.usage` holds per-role LLM meters (`researcher`,
`researcher_fix`, and `task` for Domain tasks) for every backend the agent uses, also
when you pass your own `agent=` or use `editor="agent"`. Files in `out_dir`:
`results.tsv`, `ledger.jsonl` (tree: parent, diff, samples, verdict, program version,
crash kind), `artifacts/` (full source of every attempt), `logs/*.log` (run.log per
experiment), `program.md`, `progress.png`, `groundtruth_all.tsv`, `summary.json`,
`trajectory.json` and `best_artifact/`.

### Applying it to a new problem (copy-paste)

Put the data, the metric and anything the agent must not change in locked files that
call `report(...)` (paste `rsi.autoresearch.task.RECORD_HELPER` into one of them, and
make that locked file the entry point). Put everything the agent may change in the
editable files. A full runnable version is `experiments/autoresearch/example_new_problem.py`
(polynomial ridge regression, R^2 goes from 0.27 to 0.88 in 20 scripted experiments, and
in 6 experiments with Claude Haiku).

```python
from rsi.autoresearch import Config, RunBudget, ScriptResearchTask, run
from rsi.core import get_llm

task = ScriptResearchTask(
    "my-problem",
    {"evaluate.py": open("evaluate.py").read(),        # locked entry point: data + metric, calls report(r2=...)
     "train.py": open("train.py").read()},              # editable: everything the agent may change
    metric="r2", direction="max",
    editable_paths=["train.py"], locked_paths=["evaluate.py"],
    run_cmd=["python", "evaluate.py"],
    budget=RunBudget(kind="ceiling", amount=60, kill_after=120),   # or kind="wallclock" + a locked clock
    tamper_patterns=[r"\bX_VAL\b", r"\bY_VAL\b", r"\breport\b"],     # names editable code must never touch
    description="What the model does and how it is scored (shown to the agent).")

res = run(task, llm_propose=get_llm("claude:haiku", cache_dir=".rsi_cache/my-problem"),
          config=Config(max_experiments=50, mode="hardened", reeval_seeds=3), out_dir="runs/my-problem")
print(res.meta["analysis"]["top_hits"]); print(res.best["train.py"])
```

For hidden audits, pass `data_dirs={"hardened": <visible data>, "audit": <all data>}` and
have the locked file add an `audit` dict to the reported record in audit mode (see
`rsi/domains/tabular/prepare.py:finish`). A task that is cheap to evaluate in process can
subclass `ResearchTask` directly (see `TokenizerTask` in `e3_bpb_vocab.py`, or
`LandscapeTask`).

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

Tests exercise three new domains written only for testing, with no change to the method:
a `FunctionDomain` whose artifact is a Python solver (`tests/test_autoresearch_generic.py`),
one whose artifact is a JSON config with noisy grading and no `seed_artifact` hook, driven
through the LLM-agent code path, a rigor keep rule, the parallel loop and val re-sampling
(`tests/test_autoresearch_review.py`), and a user-style `ScriptResearchTask`.

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
| 2 | ~12 experiments/hour, no human in the loop | `AutoresearchLoop.run` (no input(); stops only on `Budget`/STOP file), `Analyzer` experiments/hour | E1 (experiments/hour at a 2 s budget), `test_stop_file_halts` | done (LivenessNudger not needed: the loop is framework-driven) |
| 3 | What changes: training code of a small LM | `TinyLMTask` (`editable_paths=("train.py",)`) | E1, E2, E4, E5 | done |
| 4 | Fixed: data, tokenizer, evaluation code, budget | `locked_paths`, `sealed_files`, `ScopeGuard.locked_hashes_ok`, locked loader clock, import-time snapshots | E4, `test_scope_guard` | done |
| 5 | Strictly better keeps; equal or worse resets | `StrictKeep` + `Workspace.reset_to` | `test_keep_rules` (equal => reset), `test_loop_end_to_end` | done |
| 6 | The strategy is improved by a person editing program.md | `ProgramSpec` (versioned, `edit()`, reloaded between experiments) | `test_program_md_edited_by_human_mid_run`; E7 harness + program-blind control | partial (live E7 comparison not run) |
| 7 | One GPU; ports on Macs | CPU domains; `Executor` abstraction | E10 | partial (E9 backend-port study not run) |
| 8 | prepare.py locked => agent can't change grading | `ScopeGuard` (+ tamper scan), sealed restore + chmod + hashes, logits-only evaluator, snapshots + `_tampered()` integrity check, framework-owned record | E4, E8, `test_locked_evaluators_check_their_own_integrity` | done |
| 9 | train.py: anything goes | no restriction inside `editable_paths`; `LLMResearchAgent` rewrites whole files | live smokes | done |
| 10 | program.md is what the person edits | `ProgramSpec` | tests | done |
| 11 | Agent reads instructions + current code, decides | `AgentContext` (rendered program.md, in-scope files, results.tsv, kept-commit log) | live smoke prompts, `test_llm_agent_offline_and_metered` | done |
| 12 | Exactly N minutes wall clock (startup/compile excluded), reads val_bpb | `RunBudget(warmup_excluded_steps=11)`, tinylm `train.py` accounting + locked loader clock, `parse_summary` | `test_tinylm_modes_and_record` | done |
| 13 | Results table: commit, score, memory, status, description | `ResultsLog` (exact 5 columns), peak RSS as memory | `test_results_tsv_exact_format` | done |
| 14 | Improved => commit stays; else reset | `_settle` + `Workspace` | HEAD == last keep checked in E8 and tests | done |
| 15 | Fixed budget makes experiments comparable | `RunBudget` kinds | E2 | done |
| 16 | bpb independent of vocabulary | locked byte-length table (`TOKEN_BYTES`) in `evaluate_bpb`; `TokenizerTask` | E3 | done |
| 17 | One editable file keeps changes reviewable | single `editable_paths`, linear kept chain, per-keep diffs in ledger, `Analyzer` top hits | E1 | done |
| 18 | House rules: kill > 2x, fix trivial, skip broken, rewinds rare | `RunBudget.hard_timeout`, `CrashPolicy`, `fix_crash`, `AutoresearchLoop.rewind` (counted; an API call, never automatic) | E8, `test_rewind_and_multichain` | done |
| 19 | Example log: baseline, LR keep, GeLU discard, OOM crash | `ResultsLog` conventions | E8 TSV | done |
| 20 | MLX first night 2.667 -> 1.808; findings didn't carry to another Mac | fixed-budget bias + machine transfer | E2 (landscape "slow vs fast machine"; tinylm 1x vs 4x) | done (qualitative) |
| 21 | MLX port: same rules, no CUDA | backend-agnostic `ResearchTask` | tinylm/tabular/landscape/tokenizer/Domain tasks run the same loop unchanged | done |
| 22 | Cluster fork: job scheduler inside a container | `FakeSlurmExecutor` (sbatch + container flags, squeue/sacct/scancel, time limit), `ParallelAutoresearchLoop`, `MultiChainLauncher` | E10, `test_fake_slurm_states_and_sbatch` | done (fake scheduler) |
| 23 | Collaborative: claim first, publish results + code, shared leaderboard/best | `SharedHub`, `CollaborativeLoop` | E11, `test_hub_claims_and_bests`, `test_collaborative_loop_publishes_a_hypothesis_every_time` | done |
| 24 | Non-LLM adaptation (GBT vs held-out metric) | `TabularTask`, `HiddenAudit`, `check_per_row` | E12, E13 | done |
| 25 | Readable log by morning; person's job moves up a level | `Analyzer` report + `progress.png`, `ProgramSpec` | E1 figures | done |
| 26 | Steady unattended progress when comparable + grader out of reach | whole loop, hardened mode | E1 + E4 | done |
| 27 | Watch out: strict keep locks in luck | `NoiseCalibrator`, `Reeval`, `BootstrapRigorKeep`, `GateKeep(MinGain)` | E5 | done |
| 28 | Watch out: same val set judges every experiment | `HiddenAudit` (iid and shift reported separately), `val_resample_every` | E6 | done (effect shows in landscape; null at small N on tabular) |
| 29 | Watch out: budget favours small/fast models on that machine | `RunBudget` kinds (wallclock vs tokens), long-horizon re-eval | E2 | done |
| 30 | Watch out: one chain, one idea; strategy changes only via person | single chain (default) vs `ParallelAutoresearchLoop`; `ProgramSpec` versions | E10 (grid-likeness), E7 harness, program versioning test | partial (live E7 not run) |
| 31 | Side-by-side summary row | all of the above; results.tsv fed back as history; ledger tree | n/a | done |
| 32 | "Locks the grader away" (socially only upstream) | faithful vs hardened modes | E4 | done |
| 33 | RRSI fixes the keep rule; Dream-RSI replays runs | `GateKeep(RRSIGate())`; `ledger.jsonl` + `artifacts/` (full source, parent, diff) as a replay world | `test_keep_rules` | done |
| 34 | Agent stays the same; improves a separate model | `ResearchAgent` is stateless; only `editable_paths` change | by construction | done |
| 35 | Runnable at home | CPU domains, offline agents, `--llm claude:haiku` | all experiments | done |
| 36 | Successes are anecdotal | multi-seed experiments with 95% bootstrap CIs, hidden audits, re-evals | E1-E13 | done |
| 37 | Not EvoMap's "AutoResearch" | naming note | n/a | n/a |
| 38 | SoL-Pi runs autoresearch-style loops across environments | `AutoresearchLoop` works with any `ResearchTask`/`Domain` (multi-task keep rules belong to SoL-Pi) | generic Domain tests | done |
| 39 | Measure noise first / keep a set the loop never sees / log as a tree / never let the loop grade itself | `NoiseCalibrator`; `HiddenAudit`; `Ledger` tree; hardened framework-owned grading | E1 noise band, E4, E6 | done |

## Deviations from the spec and from upstream

1. **The framework runs the loop.** Upstream's coding agent runs git, trains, greps, logs and decides by itself. Here the agent only proposes edits. The framework commits, runs, parses, applies the keep rule and logs, which is the spec's hardened design (section 9.1). Faithful mode reproduces upstream's *measurement* weaknesses (grader locked by instruction only, metric grepped from the agent-written log, budget clock in editable code, loss summed from the agent's `forward`, evaluated row count depends on batch size, library functions the grader uses can be patched in-process). It does not reproduce the agent operating git and the shell itself. `AgentEditor` uses file tools only.
2. **Defaults versus the spec's suggested framework defaults (section 9.4, marked "inferred").** `Config` keeps upstream's strict keep rule in both modes, while section 9.4 suggests `BootstrapRigor` for hardened mode, and `noise_runs=0` where 9.4 suggests 5 baseline re-runs. Reason: E5 shows that at equal compute strict reaches slightly better true quality (rigor tests about 25% fewer ideas), so rigor is an opt-in trade-off (`Config(keep_rule="rigor", noise_runs=5)`), not a free improvement. The tinylm default budget is 25 s (9.4: 30 s) and `kill_after` defaults to `2 x budget + 5 s` (9.4: 2 x budget), because startup and evaluation are a larger share of a few-second budget than of upstream's 5 minutes.
3. **`max_experiments` counts agent turns.** Proposals that were rejected by the scope guard are logged as rows; invalid proposals and never-repeat duplicates are ledger-only. All of them count, because each costs an agent (LLM) call. `max_runs` counts training runs and is what the equal-compute comparisons use.
4. **Workspace.** Commits are content-addressed in memory by default. `backend="git"` drives a real repository under `out_dir/workspace`. The default avoids nested repositories inside this checkout. `results.tsv` is never committed. Reusing an `out_dir` needs `Config(overwrite=True)`, mirroring "the branch must not already exist". Rewinds are an API call (`AutoresearchLoop.rewind`) for a person or a policy; the agent has no way to request one, and the loop never rewinds by itself.
5. **tinylm stands in for nanochat.** It is a numpy byte-level MLP LM, not a GPT with Muon. Experiments use 1.5-2 s budgets to stay within the CPU allowance. Evaluation covers 32 KB of val with a byte vocabulary, so `TOKEN_BYTES` is all ones (E3 uses BPE vocabularies through its own task). Memory is peak RSS as reported by the run (the VRAM analogue).
6. **Budget kinds.** `tokens` (a fixed number of training bytes, enforced by the locked loader) and `ceiling` (the xgboost port's wall-time cap, where over-budget runs become `discard`) were added next to `wallclock`.
7. **Crash triviality.** `CrashPolicy` decides mechanically from the traceback's exception class (SyntaxError, NameError, ImportError...) instead of by agent judgment, and allows at most `max_fix_attempts=3`. An LLM agent can still decline with `GIVE_UP`. Timeouts are logged as `crash` (upstream says "discard and revert" and leaves the TSV status open).
8. **Hardened rejections** are logged as `discard` rows with metric `0.000000` and a description starting `REJECTED (...)`. They were never run. In the ledger their status is `rejected`.
9. **Hardened in-process integrity.** Besides the spec's hardened mechanisms, the locked evaluators score from private read-only snapshots taken when `train.py` imports them and report `tampered` (changed public constants, replaced module functions, replaced numpy/sklearn/time functions); a non-empty list rejects the run as a violation. Faithful mode keeps the live, patchable lookups.
10. **BootstrapRigor** defaults to `vary_seed=True` (the spec's suggestion). `vary_seed=False` reproduces `rigor.py` literally (repeats of the pinned seed) and appears as the `rigor_pinned` arm of E5.
11. **SimplicityWeighted** is a mechanical proxy. The spec's proxy is "keep iff better, or |gain| <= eps and the code shrinks". The implementation also charges added lines (a net addition must gain more than `eps` per 20 lines, after upstream's "0.001 improvement that adds 20 lines of hacky code? Probably not worth it"). For one-line knob edits it reduces to the strict rule, which is why the `simplicity` arm of E5 equals `strict`.
12. **E1 runs in hardened mode** (the spec's E1 row says faithful). The scripted E1 agent proposes no exploits, and hardened mode gives the locked record; the modes are compared in E4.
13. **at-home hub.** Ensue is replaced by JSON files under `flock`. Embeddings are replaced by char-3-gram cosine similarity at the faithful 0.92 threshold. `VERIFY_DELAY` is configurable (2 s by default, 0.02 s in the experiments). Tiers are user-supplied strings, not detected VRAM. A participant keeps against its own branch tip and adopts the swarm best every 5 experiments (upstream's prompt is ambiguous here). The optional `verifier` is an addition (the hardened fix), not part of at-home. `MultiChainLauncher` does not pin CPU sets.
14. **SLURM** is a local fake scheduler. There is no real `sbatch`; container flags are rendered but not executed. Each job gets its own workdir, which avoids the containerized fork's shared-root race.
15. **Tabular** uses scikit-learn HistGradientBoosting instead of XGBoost and a synthetic drifting world instead of airline data, with a 20 s ceiling instead of 1 min and 50k-row hidden sets instead of 1M. The CV protocol lives in the locked `prepare.py` (in xgbmin it is in `train.py`).
16. **Domain tasks** have a *soft* `kill_after`: the evaluation thread is abandoned rather than killed. Script tasks have hard kills. Hardened isolation (sealed files, integrity checks) applies to script tasks; a Domain runs its artifact through its own `execute`, and its `grade` is the lock.
17. **E4 measures exploits at equal compute.** Besides wall-clock runs (paired with a baseline run made right before each), every exploit is also run under a fixed token budget, where training is deterministic; classes are judged on those runs, except the budget-clock exploit, which only acts under a wall-clock budget.
18. **Not run:** E9 (backend port) and the live E7 comparison. The `LivenessNudger` is not needed because the loop never idles.

## Limitations

* **Hardened mode is not a security boundary.** The editable code runs in the same process as the locked evaluator. The tamper scan (a regex denylist over added lines) and the evaluators' integrity check stop the catalogued classes: locked-file edits, framework env vars, private names, rebinding / in-place mutation / aliasing of the locked module, dynamic imports, frame/gc introspection by name, and numpy/sklearn/time monkeypatching. Known residual routes for a determined adversary: finding the result-record path without naming the framework variables (for example by iterating `os.environ`) and rewriting the record after the locked evaluator wrote it (E4's "unguarded" arm shows this forgery works when the scan is bypassed); reaching private evaluator state through reflection that the denylist does not name; and doing heavy work in the unbudgeted warm-up steps 0-10 (as upstream allows). For real isolation, run the evaluator in a separate process or container that loads a saved model.
* **tabular train.py sees the training labels,** as in the xgboost port, so a model that memorizes the CV rows would inflate CV. Only the hidden audit catches that.
* **Wall-clock budgets are noisy on a shared 4-core machine.** Under a load average of 4-8, single 2 s tinylm runs varied by about ±15% in training compute and ±0.06 bpb. That widens CIs and moves throughput numbers. E4 therefore judges exploit classes on deterministic token-budget runs.
* **The landscape encodes its effects by construction** (Chinchilla-style law, val-specific component, shift penalty). Landscape experiments show what the *loop* does with such a world. They are not evidence that real training behaves this way. tinylm, tabular and the E3 tokenizer task supply real-training evidence.
* **The scripted agents are hand-written.** Their pools, including the "combine near-misses" fallback, shape keep rates and gains. Live-LLM behaviour was only smoke-tested (two short runs, $0.67 in total).
* **Experiments are small:** 20-36 experiments per tinylm/tabular night and 2-3 seeds. At those decision counts the validation-reuse effect is not measurable on tabular, in line with the xgboost port's evidence.

## Review log (25 Sep 2026)

Bugs found and fixed in review, each with a regression test or a re-run:

1. **In-process grader holes in hardened mode (tinylm and tabular).** Three one-line edits passed the tamper scan and moved the locked metric: `prepare.TOKEN_BYTES[:] = 2` (val_bpb halved), `import prepare as P; P.EVAL_BYTES = 128` (different scored rows; the same aliasing could switch `MODE` and disable the locked budget clock), and `np.take_along_axis = ...` (val_bpb 0.0); on tabular `import sklearn.metrics as m; m.roc_auc_score = lambda y, s: 0.99` gave CV AUC 0.99. Fixed with import-time snapshots in both locked evaluators, the `_tampered()` integrity check and its record check, and a wider tamper scan. Two of these exploits were added to the tinylm pool and to E4. Tests: `test_locked_evaluators_check_their_own_integrity`, `test_tinylm_tamper_scan_blocks_in_process_exploits`.
2. **LLM usage not metered** for a user-supplied `agent=` (usage 0, so `max_usd` never fired) and for `editor="agent"` behind a `CachedLLM` (AgentEditor meters on the inner CLI). Fixed with `agent_llms()`. Test: `test_user_supplied_and_agent_editor_llms_are_metered`.
3. **Git workspace** left stale nested files (for example `prompts/system.md`) in the working tree after a version deleted them, and deleted its own `.gitignore`. Test: `test_git_workspace_mirrors_nested_files`.
4. **Collaborative loop** published a hypothesis only on keeps; at-home makes insight and hypothesis "mandatory every time". Test: `test_collaborative_loop_publishes_a_hypothesis_every_time`.
5. **E4 classified noise as holes.** Under machine load, a pure no-op edit (`scaled_loss` changes `forward`, which hardened training and grading never call) showed a 0.07-0.09 bpb "hardened gain" against an unpaired baseline, and the faithful no-op `forge_record` showed a 0.13 "bogus win". E4 now pairs every run with a baseline run and judges on deterministic equal-compute runs (both no-ops give exactly 0.0).
6. **Stale documentation.** This file quoted numbers from before the previous review round (for example E10 "parallel ends worse", E5 tinylm "rigor halves the optimism gap") that the JSONs no longer supported, and listed E13 as not run. All numbers above were re-taken from JSONs re-run after the fixes.
7. **New experiments:** E3 (bpb vs vocabulary, which moves checklist item 16 to done) and the E7 harness with its program-blind control.

## Core change requests (not applied; `rsi/core` untouched)

1. `Ledger(path)` silently appends to an existing JSONL file, which merged two runs during development. Please add an explicit `resume=False` (truncate) vs `resume=True` flag. The loop works around it with `Config(overwrite=...)` and refuses a used `out_dir` by default.
2. `sandbox.run_cmd`: add a `merge_stderr` option to reproduce upstream's `> run.log 2>&1` interleaving, and return the child's peak RSS (via `os.wait4`) so memory is measured by the framework instead of self-reported by the run.
3. `stats.bootstrap_ci` resamples in a Python loop (5,000 iterations) and dominates `Reeval` time in many-seed simulations. Please vectorize it the way `prob_better_bootstrap` is.
4. `Domain`/`Evaluator`: an optional process-isolated evaluation with a hard timeout (and meter merging), so `DomainResearchTask` can kill a hung harness instead of abandoning the thread.
5. `AgentEditor`: an optional restricted run/Bash tool (and read-only mounts for locked files), so a live faithful mode can let the agent run training and git itself as upstream does.
6. `ArtifactStore`: an in-memory mode. Loops with `persist=False` still write every attempt to disk.
7. `ClaudeCLI.run_agent` meters on the CLI object itself, bypassing a `CachedLLM` wrapper; a wrapper-aware `run_agent` (or a documented rule) would let callers meter one object. `rsi.autoresearch.loop.agent_llms` works around it.
8. `sandbox`: a helper to run a locked scorer in a separate process on a saved model file (the robust fix for the residual in-process routes listed under Limitations).
