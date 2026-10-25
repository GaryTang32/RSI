# EvoMap: a gene library built from validated solves (GEP / Evolver), optionally shared through a hub

Method reference for `rsi.improve(..., method="evomap")`. Sources: `docs/methods/evomap/paper-spec.md` (the spec),
`implementation.md`, `claims-audit.md`, `rsi/evomap/`. **Read this first: the "paper" has no algorithm to follow.** The
spec's tags are `[spec:§n]` (GEP protocol spec 1.14.0), `[code:...]` (plain Evolver / SDK code), `[deob:...]`
(deobfuscated Evolver v1.94.0 modules, the real loop), `[snip:SG]` (Strategy Genes paper, arXiv 2604.15097, known only
from search snippets; full text never read), `[abs:BE]`/`[snip:BE]` (Behind EvoMap, arXiv 2605.25815), `[sec:mpx]`
(unverified third-party summary), `[doc]` (overview), `[inferred]`. There are no `[paper §n]`, `[Alg.]` or `[Eq.]` tags
for EvoMap. The loop below is the engine's (`[deob]`/`[spec]`); the papers give results, not a procedure. Anything not
in the spec is "not specified in the paper".

## 1. In one paragraph

EvoMap's idea is that agents should not each learn the same lesson alone [doc]. Evolver, the local engine, turns each
solve into small content-hashed assets: a **Gene** (compact strategy card: trigger signals, 3-10 imperative steps, AVOID
warnings, constraints, validation commands), a **Capsule** (one validated application) and an **EvolutionEvent** (audit
record) [spec:§2]. Each cycle it extracts signals, picks the best-matching gene from a local store (helped by a memory
graph of past outcomes and random "drift"), asks the host agent to apply it, then **solidifies**: runs the gene's
validation commands, checks blast-radius constraints, scores the cycle, writes the event, rolls back on failure
[spec:§3, deob:solidify.js]. Repeated successes are distilled into new genes; good capsules can be published to a hub
where other agents fetch them. What is improved is the **gene library**, never model weights or the host harness
[spec §2 table]. What it optimises is not a measured task metric: the keep rule is "validation commands pass and
constraints hold", ranking on the hub is GDI (mostly self-reported metadata). The Strategy Genes paper reports that a
~230-token gene beats a ~2,500-token skill (54.0% vs 49.9%, 51.0% with no guidance, 4,590 trials) and CritPt
9.1%->18.57% / 17.7%->27.14% [snip:SG]; Behind EvoMap found 98% of assets never reused and >84% of approved assets
validated by vacuous tests [abs:BE]. `rsi.evomap` ports the engine (`mode="faithful"`) and adds a corrected variant
(`mode="safe"`, the default).

## 2. Inputs and setup

Needed before cycle 1:
- **Frozen harness** `Artifact` (e.g. `prompt.md`). It is never edited. Genes are injected into it at solve time
  (`FileInjector` writes `genes/*`; `AppendInjector`/`SkillInjector`/`TextInjector` exist). The improved artifact is
  `res.best` = harness + `genes/library.json`.
- **Tasks with public signals**: each `Task` should carry `family` (becomes `task:<family>`) and
  `meta={"signals": [...]}`/`meta["keywords"]`. Signals are how a gene is matched to a task; without them every task looks
  alike. Hidden answers stay in `target`/`grade`.
- **Splits**: `evolve` (`Config.split`) is the practice stream: the loop walks it one task per cycle, and the gene writer
  sees only its public text and trace. `val` (`Config.heldout_split`, falls back to `evolve`) is read only when a hub is
  attached, for the consumer quarantine A/B. `holdout`/`ood`/`test` are sealed: naming one raises `SealedSplitError`.
  The only sealed split the method touches is the SafeHub's hidden `TaskBank` (hub-owned), by design.
- **Grader**: locked `Domain.grade` -> score in [0,1] + feedback. A task counts as solved at score >=
  `success_threshold` (0.5). Optional domain hooks: `pre_workspace`/`workspace` (W0/W1 for validation checks),
  `public_feedback`, `public_text`, `leakage_terms`, `counted_policy`.
- **Models**: `llm_task` = frozen solver (required). `llm_propose` = gene writer (`None` gives a reuse-only agent, no new
  genes). `hub` optional (`NaiveEvoMapHub` or `SafeHub`); `rsi.improve(..., hub=hub)` forwards kwargs to `run`.
- **Budget**: `Config.cycles` (default 30) = one task per cycle; `Budget(max_rollouts=..., max_usd=...)` via `budget=`.
  Rollouts counted: solves, `rsi-taskcheck` A/Bs, quarantine re-tests.
- **Measured first**: nothing that feeds a decision. The `baseline` trace event (seed harness on the evolve split,
  `trace_baseline_k=2`) is trace-only and excluded from the budget. The `noise` event says `delta=None`: **the loop has
  no run-level noise band**, and the paper has none either [spec §8.2 "no held-out set, no noise band"]. Measure delta
  yourself (SKILL.md step 3) and check headroom: if the harness already solves ~everything on `evolve`, no gene is ever
  written (live Haiku on our katas: holdout 1.0 -> 1.0).

## 3. Round by round

One round = one **cycle** = one task (`AgentNode._cycle`, following `[spec:§3.1]` + `[spec:§3.6]`). Differences between
`mode="faithful"` (Evolver v1.94.0) and `mode="safe"` are marked F / S.

1. **Task**: take the next id from a shuffled copy of the `evolve` split (`task_order="shuffle"`; re-shuffled when
   exhausted). Solve seed `seed_base + t`, with `seed_base` derived from `(Config.seed, agent name)`.
2. **Signals** [spec:§3.2]: `task:<family>` + task meta signals + regex layer + weighted-keyword layer over the log
   corpus; F also feeds the previous attempt's trace as the corpus (`carry_log_signals`) and runs the hub "layer 3"
   every 5th cycle starting at the first (`llm_signal_layer`, empty without a hub analyzer). The memory-graph outcome
   of the **previous** cycle is written now (F, `outcome_timing="next_cycle"`; S writes it right after solidify).
3. **Dedup / control signals** [spec:§4.6]: drop a signal seen in >=3 of the last 8 events (task descriptors are
   protected); repair loops, empty cycles, `consecutive_failure_streak_N`, `failure_loop_detected` + `ban_gene:<id>`
   (5 failures), `high_failure_ratio`, `plateau_pivot_*`.
4. **Hub first (F only, if a hub is attached)**: `Hub.search` on every cycle, before local selection; best hit with
   client score `r = confidence*min(max(streak,1),5)*rep/100 + 0.3*sim >= 0.72` (0.55 with problem signals) is injected as
   a "STRONG REFERENCE" next to the local gene; a miss with problem signals adds `hub_search_miss_with_problem`.
5. **Select a gene** [spec:§3.3, §4.2-4.5]: plateau override, memory advice, bans (advice bans, >=2 overlapping failed
   capsules, `ban_gene:`, epigenetic hard-suppression at boost <= -0.3), then
   `score(g) = hits + 0.6*tag + 0.4*cos + adj` (adj from last-8 learning history +0.12 / -0.22 hard / -0.08 soft, epigenetic
   boost, anti-pattern penalties; `gene_distilled_*` x0.8; memory-preferred x1.5). With probability
   `I = min(1, 1/sqrt(Ne))` (more under plateau) pick uniformly among the top `max(2, ceil(n*I))` instead of the best
   (drift; see 4.3: this fires even with drift "off"). F: a gene needs no pattern hit (`require_match=False`); S:
   `require_match=True`. The capsule is chosen independently by trigger match.
6. **Hub (S only)**: if no local gene fits, `Hub.search` -> `stage_external` (asset_id check, confidence x0.6) ->
   `QuarantineGate` A/B on `heldout_split` tasks (`quarantine_k=2`, `quarantine_n=12`, `quarantine_min_tasks=3`, RRSI
   floor + dS>0) -> promote or reject (a rejected asset is never re-tested) -> adoption report with proof.
7. **Policy, personality, mutation** [spec:§3.4]: adaptive file cap, `PersonalityState` drift, mutation
   category/risk. In this port they feed the score, protocol checks and trace; the solver sees only the gene card.
8. **Solve** the task once with harness + injected gene(s) (or the bare harness if none).
9. **Write a gene** only if no local gene was selected and the solve failed: `llm_propose` gets signals, task public text,
   the last 1,500 chars of trace, and a history block of earlier genes in scope. One gene per cycle. The gene is judged
   on its own task: F one fresh-seed retry (`new_gene_retries=1`); S a paired sample (`new_gene_check="paired"`, 3
   fresh seeds with and without the gene; kept iff > half solve AND it beats the bare harness).
10. **Solidify = keep rule** [spec:§3.6, §4.8-4.10]. `success = constraints_ok AND validation_ok AND no protocol
    violation` (F). S adds `AND validation is non-vacuous (lint + discriminative W0/W1 run) AND own task solved`. Constraint
    checks: hard caps 60 files / 20000 lines, `max_files`, forbidden paths, hollow commit, ethics lint. Validation runs
    the gene's commands (F: missing scripts skipped, empty list passes, 2 retries; S: no skips, empty list fails).
    Score (not the keep rule):
    `0.05 sig + 0.10 sel + 0.05 mut + 0.15 blast + 0.25 constraint + 0.25 validation + 0.10 protocol + 0.05 canary`,
    rounded like JS `Math.round(x*100)/100`. A success writes event + capsule and stores a new gene; a failure rolls the
    post-state back (the gene is not stored) but its learning metadata (history, anti-patterns, epigenetic marks) persists.
11. **State updated**: events (append-only, parent chain + hash chain), capsules, memory graph (signal / hypothesis /
    attempt / outcome), gene learning, personality stats, epigenetic marks, ledger node.
12. **Distil** (only after a successful solidify): every 5th solidify (or 24 h / 7-of-10 rule) -> heuristic
    `auto_distill` of >=10 good capsules (score >= 0.7) -> `gene_distilled_<src>`. F also runs the failure distiller
    (>=5 failed capsules -> `gene_repair_distilled_*`); S turns it off.
13. **Publish** (hub attached): needs success, score >= 0.78, streak >= 2, <=5 files / 200 lines, not `reused`.

```text
store = LocalStore(); for t in range(cycles):                     # stop: Budget (rounds / rollouts / usd) only
    task = next(shuffled evolve split); signals = dedupe(extract(task, prev_trace), recent_events)
    hubhit = hub.search(signals) if F and hub else None           # F: hub FIRST
    gene = select(store.genes, signals, memory.advice, bans, drift)   # score + 1/sqrt(Ne) random pick
    if S and gene is None and hub: gene = quarantine(hub.search(signals))   # A/B on val tasks
    trial = solve(harness + gene (+hubhit as reference), task)
    if gene is None and trial failed: gene = llm_propose(...); trial = retry_check(gene)   # 1 retry F / paired S
    r = solidify(gene, W0 -> W1)   # success = constraints AND validation (AND non-vacuous AND task solved, S)
    if r.success and gene is new: store.add(gene);  memory.record_outcome(...)   # F: next cycle
    distill_if_due(); publish_if_eligible()
return harness + genes/library.json                               # the whole final library, no argmax
```

**What changes across rounds**: the library grows (genes added on success, distilled genes, adopted hub genes); each gene
accumulates learning history (<=20), anti-patterns (<=12), epigenetic marks; memory-graph edge values (Laplace, 30-day
half-life) steer preference and bans; drift intensity falls as the library grows (`1/sqrt(Ne)`); plateau detectors can force
innovation. Nothing is annealed on a schedule (presets are fixed; `early-stabilize` applies to cycles 1-5 only for the
mutation preset). **Stopping rule**: not specified in the paper beyond "daemon loop with adaptive sleep"; here
`Budget(max_rounds=cycles)` plus optional rollout/USD caps; no convergence test. Calling `run` again with the same
`out_dir` continues the persisted store.

## 4. Sample by sample

One sample = one task in one cycle.
- **Trials**: one solve per cycle. A new gene adds 1 (F) or 3 paired pairs = 6 (S) retry rollouts on the *same task*;
  S's `rsi-taskcheck` (if a gene's validation is that command) adds `taskcheck_n=4` in-scope tasks x with/without gene
  (`taskcheck_k=1`); a hub candidate adds the quarantine A/B (`quarantine_k=2` x up to 12 tasks x 2 arms). Seeds: solve
  `seed_base + t`; retries `seed + 7919*i` (paired arms share seeds); not specified in the paper.
- **Recorded**: score, output, trace (last 4,000 chars carried to the next cycle's signals in F), tokens, validation
  command results with exit codes, blast radius, composite score, failure mode (hard/soft), the event and capsule.
- **Aggregation**: there is none across samples. Each cycle is its own accept/reject on its own task; there is no
  round score, no incumbent score, no comparison to a baseline. `rolling_solve_rate` (last 10 cycles) is a diagnostic.
  Gene quality accrues only through `learning_history` and memory-graph counts (edge value = Laplace `(s+1)/(n+2)` x decay).
- **Splits**: `evolve` supplies tasks, traces and grader feedback (the gene writer sees trace + public checks, with a
  leakage audit stripping literals that occur only in hidden text). `val` is used only for hub-asset quarantine. The loop
  may never read `holdout`/`ood`/`test`; they are read only by the write-only shadow monitor (new library versions) and by
  `evaluate_library` / `transfer_report` at the end.

## 5. Paper defaults vs this package

The Strategy Genes paper gives no loop hyperparameters. Values are Evolver's (`[deob]`/`[code]`).

| setting | reference value [tag] | rsi `Config` field = default | note |
|---|---|---|---|
| cycles | daemon loop, adaptive sleep [code:README] | `cycles=30` | one task per cycle |
| mode | Evolver v1.94.0 | `mode="safe"` (`"faithful"` = port) | `None` fields take mode defaults |
| gene score | hits + 0.6 tag + 0.4 cos [deob:selector, §4.2] | `selector_mode="current"` (`"spec"` = hits only) | |
| memory | Laplace, half-life 30 d, Jaccard >= 0.34 [§4.4] | `memory_mode="current"`, `use_memory=True` | gene prior half-life 45 d |
| drift | `1/sqrt(Ne)` + offset [§4.3] | `drift=False`, `plateau_override=True` | random pick still fires at ~0.24 with 17 genes |
| strategy preset | balanced 20/20/50/10 [§4.7] | `strategy_preset="balanced"` | shares not sampled; only `innovate >= 0.5` used |
| ban rules | per-key >=4 and best < 0.15; 2 failed capsules [§4.4-4.5] | `failed_capsule_bans=True`; rule F absolute, S relative | S needs failures > successes too |
| keep rule | constraints AND validation AND no protocol violation [§3.6] | F as paper; S: `vacuity_check=True`, `require_task_success=True` | no held-out gate in either |
| validation runner | skip missing, empty ok, 2 retries, 180 s [§4.9] | F same; S no skips, empty fails | Python (`python`, `pytest`, `rsi-taskcheck`) not node |
| composite weights | .05/.10/.05/.15/.25/.25/.10/.05 [§4.10] | fixed; `estimate_drift_penalty=False` | Evolver's drift penalty is dead code |
| new-gene check | none (LLM output taken) | F `new_gene_retries=1` single; S 3 paired | |
| distill | every 5th solidify; >=10 capsules >= 0.7 [§4.14] | `distill=True`, `distill_every=5`, `distill_min_capsules=10`, `llm_distill=False` | interval/24 h rule not modelled (1 h per cycle clock) |
| failure distill | >=5 failed capsules, 12 h [§3.7] | F on, S off (`failure_distill`) | |
| hub reuse | 0.72 / 0.55 with problem signals [§4.16] | `reuse_threshold=0.72`, `reuse_threshold_problem=0.55`, `hub_k=5` | |
| publish bar | score >= 0.78, streak >= 2 [§4.13] | `publish=True`, `publish_requires_eligibility=True` | |
| hub order | hub first [deob:enrich.js] | F `hub_when="first"`; S `"no_local"` | overview's "local first" is contradicted by the code |
| epigenetic suppression | boost <= -0.3 [§4.5] | `epigenetic_suppression=True` | |
| rollback | `stash` [§3.6] | `rollback="stash"` | map workspace: new gene just not stored |
| seeds | not specified | `seed=0` | |
| audit | none | `trace=True`, `shadow_monitor=True` | extensions |

## 6. Where our implementation differs

From `implementation.md` section 6 and `claims-audit.md` (54 claims after retry round 2: 28 reproduced, 10 partial, 1 not
reproduced, 14 not testable here, 1 contradicted).
- **Workspace and language**: `{path: text}` map with W0/W1 from domain hooks (git port `GitWorkspace` exists, opt-in);
  Python validation commands; no canary (skipped, as Evolver does outside its repo); no in-place genes, stub LLM reviewer,
  heartbeats, marketplace, A2A envelope, skill2gep, epoch triggers. Hubs are in-process objects.
- **Task-stream adapter**: signals come from `family`/`meta` (Evolver reads logs); descriptor signals are protected from
  dedup. The mutation/personality machinery is ported but the solver never sees it.
- **Safe mode** (extension, not EvoMap): non-vacuous validation, own-task success, paired new-gene check, immediate
  measured outcomes, relative failed-capsule ban, quarantine of hub assets, `empty validation fails`.
- **Hub**: `NaiveEvoMapHub` promotes on the publisher's self-reported report; GDI is partly reconstructed (usage, social,
  reputation unknown). `SafeHub` (hidden `TaskBank`, uplift LCB, adoption ranking, stake) is our design, not EvoMap.
- Verdicts a user needs: **REPRODUCED** engine mechanisms (hashing byte-identical, selector, solidify, composite, eligibility,
  vacuity, self-assessed memory outcome: X15, the composite is not a task-gain measure: X16 Spearman ~0). **PARTIAL**: signals
  (hub LLM layer is a stand-in), distiller (success distiller never reached 10 distinct capsules in long runs), GDI
  internals, Behind-EvoMap percentages (set by our farm-rate knob). **NOT REPRODUCED**: blast radius dominating GDI (B7),
  SafeHub rank validity > 0 (E3), exploration slots cutting time to first reuse (E4). **CONTRADICTED**: the overview's
  "local store first, then hub" (Evolver asks the hub first). **NOT TESTABLE**: all vendor numbers (54.0/49.9/51.0,
  CritPt, 4,590 trials); offline X1-X3 only restate simulator knobs, and live Haiku has no headroom on our katas.
  Gains in our simulations: GeneWorld safe 0.41 -> 0.56, katas 0.39 -> 0.84, faithful far smaller; agentqa not significant.

## 7. Applying it with rsi

```python
import rsi
from rsi.evomap import Config, evaluate_library
res = rsi.improve(dom, seed, method="evomap", llm_task="claude:haiku", llm_propose="claude:sonnet",
                  config=Config(cycles=40, mode="safe", split="evolve", heldout_split="val"),
                  out_dir="runs/genes", cache_dir=".rsi_cache/genes")        # hub=None: one offline agent
rep = evaluate_library(dom, task_llm, res, splits=("holdout", "ood"))        # no genes vs routed library, sealed splits
```
`res.best["genes/library.json"]` is the library; `evaluate_library`/`GeneRoutedDomain` pick one gene per task by pattern
score. Fields users set: `cycles`, `mode`, `split`, `heldout_split`, `success_threshold`, `validation_hint`,
`default_validation`, `distill_every`, `llm_distill`, `quarantine_min_tasks` (set 1 on tiny task sets), `workers`. Sharing
needs `hub=SafeHub(TaskBank(dom, harness, model, split="test", ...))` and several agents (`PopulationSimulator`);
`rsi.improve` runs ONE agent. Gene writer prompt must state the allowed validation command format, or inline checks
(`python -c`) are blocked and nothing solidifies.

**Audit with `python -m rsi.cli inspect runs/genes`** (`TRACE.md`, `trace.jsonl`, `ledger.jsonl`, `store/`, `trajectory.json`):
- `round_start`: library, capsules, event chain, memory counts, pending outcome, personality, rollouts used.
- `analysis`: raw -> deduped signals, plateau/drift, memory advice (preferred, bans), selector mode and scores. Recompute one
  score by hand.
- `proposal`: the gene tried (reuse / hub / scratch / newly written with writer prompt, reply, leakage audit) and the
  injected diff. Check the gene is generic, not a copied answer.
- `eval`: every graded rollout (solve, retry arms, taskcheck, quarantine). `gate`: solidify with every keep-rule input
  (constraints, each validation command + exit, vacuity lint, discriminative before/after, task score, composite,
  failure mode, publish numbers). Verify `success` from these by hand.
- `decision`: library before/after; `state`: memory outcome actually written; `monitor`: holdout/ood score of each new
  library version (library growth with flat holdout = noise or memorization). `res.meta["audit"]` and
  `res.meta["safe_mode_fixes"]`.

**Method-specific failure modes**:
- **Self-grading**: F's keep rule tests the host repo, not task gain; vacuous/skipped/empty validation still passes
  and scores >= 0.78 (X16: 100% of such cycles publishable). Prefer `mode="safe"`.
- **Self-reported outcome**: F's memory graph can read the executor's printed EvolutionEvent (X15: preferred gene's true
  effect 0.30 vs 1.26). If the domain's traces never contain error text, every F outcome is `stable_no_error` (inert) and a
  gene is banned after 8 inert outcomes; use S or put an error line in failure traces.
- **No headroom**: a seed that already solves the stream writes no genes; no genes also means nothing to evaluate.
- **Noisy small-N keep**: each gene is judged on one task (3 paired seeds in S). Stochastic graders produce false keeps
  and false bans; measure the noise first and read `rsi-taskcheck` LCBs.
- **Overfitting to the practice stream**: genes written from `evolve` traces can hide the answer; read `leakage_audit`
  and score `holdout`/`ood` via `evaluate_library`.
- **Hub poison / spam** (population runs): naive hub + direct apply ran a poisoned gene in 94% of consumer cycles (X11);
  keep quarantine on and never trust a self-reported `validation`. Drift still picks a non-best gene ~24% of cycles.
- **Missing signals**: tasks without `family`/`meta["signals"]` match nothing; genes will not be reused.
