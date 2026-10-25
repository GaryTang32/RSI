# Dream-RSI: improve the search policy of a discovery loop by replaying past searches

Method reference for `rsi.improve(..., method="dream")` (alias `dream-rsi`). Sources: `docs/methods/dream-rsi/paper-spec.md`
(the spec), `implementation.md`, `claims-audit.md`, `rsi/dream/`. Tags are the spec's: `[paper:§3]`, `[paper:App.B.2 L2:n]`
(line n of prompt Listing 2), `[paper:App.B.1 L1:n]` (Listing 1), `Eq.1` (the only numbered equation), `[doc]` (overview page),
`[inferred]` (the spec author's reading). The spec has no `[Alg. n]` tags: the paper gives the loop in prose (§3) and prompts
(App. B). **The official code is unreleased**, so nothing is checkable against reference code. Anything the spec does not
state is "not specified in the paper" below.

## 1. In one paragraph

Zheng et al. (Google, UMD, DeepMind, UVA; arXiv 2609.14858) improve the **exploration policy** of an agentic discovery system:
the code that decides which attempts to extend, how many to run in parallel, when to open a new direction and when to stop
[paper:§1,§3]. Models, coding agent, evaluator and budget stay frozen [paper:§3]. Key idea: a finished search is a simulator.
It is stored as a *discovery tree* (every attempt, its parent, its evaluated score), and a different policy can be tested on
it by walking it differently; outcomes are *revealed* from the record, with no new model calls and no new evaluations [paper:§2,
Fig.2]. Each outer round: (1) run the deployed policy online (expensive), (2) add the tree to a pool of "replay worlds", (3) a
policy-developer LLM rewrites the policy code several times, each version is scored by replay on all worlds, the best-scoring
version (incumbent included) is deployed next [paper:§3]. Optimised: the **mean replay score** `V` (best score found minus a
cost per attempt plus a parallelism bonus, Eq.1), which trades quality, spend and parallelism. Reported: Lasso solver, 3 math
tasks, 4 GPU kernels with Gemini-3.1-Pro / 3.7-Flash; about 1.7x fewer discovery calls than fixed exploration on Lasso, mixed
quality gains [paper:§4; spec §7].

## 2. Inputs and setup

Needed before round 1:
- **Artifact** (seed program): any `rsi.core.Artifact`. The discovery agent edits it; the root of round t is the **best program
  so far** (`Config.root="best"`) or the seed (`"seed"`). Not specified in the paper [spec §3.3].
- **Domain / task**: any `rsi.core.Domain` (wrapped by `DomainTask`), or a discovery domain from `rsi.domains.discovery`, or a
  `DiscoveryTask`. The locked grader returns a score (larger is better; negate minimisation tasks) plus a typed `fail_class`.
  Decision split = first non-empty of `evolve`, `train`, `val`; a sealed or empty split raises. `task_kwargs` (for example
  `directions=[...]`, `k=...`, `split=...`) configure the wrapper.
- **Models**: `llm_propose` = the frozen discovery agent (`EditorAgent`, Listing-1 prompt); `llm_develop` (default = `llm_propose`)
  = the policy developer (Listing-2 prompt); `llm_task` for the domain. Without LLMs the domain's `mock_agent()` and
  `ParametricMutator` are used (offline only). The paper does not name the developer model.
- **Budgets**: `rounds` (live searches), a per-round grid `branch_count x (refine_count+1)` = calls per round, `W` workers,
  optional `max_calls`. Paper: 10 x 11 = 110 calls (Pro) or 32 x 20 = 640 (Flash) per round; 5 rounds (Lasso), 10 (math)
  [paper:§4].
- **Measured first**: the seed once on the decision split (`seed_eval`, one trial per task). No noise band is measured (the
  `noise` trace event has `delta=None`); measure the grader's noise yourself (SKILL.md step 3). The initial policy pi_1 is
  `parallel_refine`: round 1 opens all `branch_count` roots in one batch, then deepens every branch each round [paper:§4 p.7].
  Round 1 therefore equals Recursive Fixed Exploration, the baseline (`Config(dream=False)`).

## 3. Round by round

One outer round `t` = online search + pool update + dreaming [paper:§3, Fig.1]. Unless noted, step numbers are our loop.

1. **Plan the grid.** The deployed policy's `plan_grid(ctx)` returns `GridPlan(branch_count, refine_count)` before any outcome
   of this round exists. `ctx` holds only earlier `live_cycle_manifest.json` records, fallback/hard caps, and `worker_cap=W`
   [paper:App.B.2 L2:190-242]. An invalid plan falls back to `(branch_count, refine_count)`; the plan is clamped by
   `hard_max_branch/refine`. A direction provider assigns each new root a direction label (cycled, shuffled per round).
2. **Online rollout** (`OnlineQuestion`, policy code fixed during the rollout). Loop: the policy sees only the observed tree and
   the legal cells (unopened roots, one frontier per opened branch) and returns a batch of <= `W` distinct cells with no parent
   and its child together; an empty batch means stop. Each cell = one discovery-agent call from the parent's saved workspace
   (root: the round's root program) followed by one evaluation. Ends on empty batch, `K1` rounds (default none), the plan
   exhausted, or the per-round call cap. The cap `round_budget="fallback"` = `branch_count*(refine_count+1)`: **a Dream round
   can never spend more than Fixed's round** (the policy may only spend less).
3. **Record the world.** The tree (nodes: id `b{branch}.a{attempt}`, parent, snapshot, program, score, `fail_class`, error,
   `n_valid/n_total`) is appended to the pool `H`; `trace_pool/iter<t>/{tree.json, live_cycle_manifest.json}` are written. The
   manifest holds planned and effective grid, opened width, max depth, probe work `N`, decision rounds `k`, batch sizes, round
   best, final best, the policy's baked-in `beta`, early/late gain fractions, failure fractions [paper:App.B.2 L2:169-175].
4. **Update the deliverable.** The best program is the highest-scoring successful node of the tree if it **strictly** exceeds the
   best so far. There is no paired test and no held-out gate on this step.
5. **Dream** (skipped after the last round unless `dream_last=True`, and skipped when `dream=False`). `M` policy versions with
   `pi^0` = incumbent: `n_revisions = M-1` (`m_semantics="versions"`) or `M` (`"revisions"`); the spec flags the paper's indexing as
   an off-by-one [spec §3.2]. For each revision the developer LLM gets the Listing-2 prompt, the earlier versions with their
   replay traces (`policy_execution_traces.jsonl`), `beta_sweep.json`, the manifests, and the parallel-refine baseline "floor to
   beat". It starts from the **strongest** version so far (`developer_base="strongest"`, [paper:App.B.2 L2:247]) or from the
   latest (`"latest"`, [paper:§3]: "revises pi^m into pi^{m+1}"). Each new `method.py` must pass `static_check` (AST lint) and a
   leakage screen against best cell ids and scores, with one repair round.
6. **Replay each version on every world** (`ReplayEvaluator`). Per world: `plan_grid` once (a plan beyond the tree's recorded
   width/depth is out of support and "cannot earn replay reward", `support="no_reward"`), then the same `solve()` interface on
   `ReplayQuestion`: choosing a cell **reveals the recorded child**; a root reveals the earliest-created unrevealed branch
   (`root_mode="earliest"`, [paper:§3]) or the named branch (`"addressable"`, [paper:App.B.2]); a cell with no recorded child
   reveals nothing but still counts a round. Stops on empty batch, `K2` (default none), or all revealed [paper:§3].
7. **Score** (Eq.1, per world `i`, version `m`): `V_i = max_{revealed v} s_v - beta1*N + beta2*N/max(1,k)`, `N` = revealed
   non-root nodes, `k` = decision rounds [paper:§3 Eq.1]. Default here: per-world normalised scores `(s-s_r)/(ceiling-s_r)`
   (`normalize=True`; the paper averages raw `V_i`). Version value `V^m` = mean over **all** worlds in `H` (no held-out split).
   Alternative `objective="pareto"`: sweep the policy's `beta` over `beta_grid`, `reward = auc - lam*parallel_penalty`
   [paper:App.B.2 L2:5-22,145-188]; the paper never says which objective produced its results [spec §4.3].
8. **Select and deploy.** `m* = argmax_m V^m`, incumbent included (`include_incumbent=True`); ties go to the incumbent (index 0 if
   it is among the winners within 1e-12). So `V^{m*} >= V^0` on replay; nothing is guaranteed online [paper:§3 p.6].
   `selector="guarded"` instead deploys a candidate only if its gain on held-out worlds beats `max(margin_floor, z*se)` (extension).
9. **Sweep for the next cycle.** The chosen version is swept over `beta_grid` on the development worlds (`beta_sweep.json`),
   feeding the cross-cycle default-beta rule: raise the baked-in default by ~0.1-0.2 if the live best plateaued and a higher beta
   reaches more at reasonable cost, lower it if high beta only adds work, else about 0.6 [paper:App.B.2 L2:176-184].

**What changes across rounds**: the world pool `H` grows by one tree per round; the deployed policy changes only when a revision
beats the incumbent on replay; `plan_grid` reads the growing manifest history (widen when early gains come from many roots,
deepen when gains come late, shrink on repeated hard failures); the agent's history context grows (by default every earlier
search is shown in full). Nothing is annealed. Paper's observed pacing: effort drops while progress is fast and rises after a
plateau (ConvDiv 110 -> 50 attempts per round, then back up) [paper:§5.2, one run]. **Stopping**: `rounds` exhausted or
`max_calls` reached (checked at the top of each round); inside a round, empty batch / `K1` / call cap. The paper gives no
convergence test.

```text
H=[]; pi=pi_1 (parallel_refine); best=seed
for t in 1..rounds:                                   # stop earlier if max_calls is spent
    plan  = pi.plan_grid(ctx(manifests, caps, W))     # prefix-safe, before any outcome
    tree  = online_rollout(pi, plan, W, K1, root=best, cap=branch_count*(refine_count+1))   # agent + grader calls
    H.append(tree); manifests.append(live_manifest(tree)); best = argmax(best, tree nodes)  # strict >
    if dream and (t<rounds or dream_last):
        V = [mean_i replay(pi, H_i)]                  # pi^0, no LLM calls
        for m in 1..M-1:
            code = developer.revise(base=strongest|latest, traces, sweep, manifests)   # LLM, Listing 2
            if static_check(code).ok: V.append(mean_i replay(code, H_i)) else V.append(-inf)
        pi = versions[argmax V]                       # incumbent wins ties; guarded selector optional
```

## 4. Sample by sample

"Sample" here = one **attempt** (one node = one agent call + one evaluation), and, inside replay, one revealed cell.
- **Trials**: one agent call per node; one grader pass over the decision split with `k=1` trial per task (`DomainTask(k=1)`,
  raise via `task_kwargs={"k": n}`). Node score = mean task score on that split. Seed per node = hash of
  `(Config.seed, round, branch, attempt)`; the paper states no seeds. Replay is deterministic (no seed; sandbox fixes the hash
  seed); policy-developer calls use `seed*100003 + t*101 + m`.
- **Recorded per node**: program and `proposal.md` (mechanism, evidence, expected benefit), score, `evaluated`, `valid`,
  `fail_class` (`ok`; `compile_other` if every trial errored; `env_error` for agent/evaluator crashes or all-infra errors, never
  blamed on the policy), error text, `n_valid/n_total`, diagnostics (worst-2 task feedback), tokens/USD in the cost meter.
  Success means `error is None and fail_class=="ok"` even if `valid` is false [paper:App.B.2 L2:47-51].
- **What each reader sees**: the discovery agent reads the lineage in its branch, all sibling attempts, all earlier searches and
  the baseline (Listing 1), including grader feedback text. The **policy** sees only `Observation`s of revealed cells (score,
  validity, `fail_class`, error, deltas, `n_valid/n_total`), structural `meta`, `baseline_score`, legal sets: never unrevealed
  scores, `best_so_far`, `budget_spent`, or raw traces [paper:App.B.2 L2:53-55,133-139]. `PrefixGuard` enforces this; a violation,
  illegal batch, crash, or `reset()` after the first probe disqualifies the episode (scored below every honest one).
- **Aggregation**: per world, Eq.1 over the revealed subtree; across worlds, a plain mean. The deliverable uses the best node score.
- **Splits**: every node score and every replay value comes from the single decision split (`evolve`, else `train`, else `val`).
  The loop may **never read** `holdout`, `ood`, `test` (`DomainTask` raises on sealed splits). They are touched only by the write-only
  shadow monitor (once per new best program, `shadow_monitor=True`) and by `rsi.transfer_report` afterwards. The paper's only held-out
  evaluation is the six Lasso datasets; math and kernel numbers are on the search objective itself [spec §8.12].

## 5. Paper defaults vs this package

| setting | paper value [tag] | rsi `Config` field = default | note |
|---|---|---|---|
| rounds T | Lasso 5, math 10 [paper:§4.1,§4.2]; kernels not stated | `rounds=5` | 10 for math-like tasks |
| calls per round | 10 x 11 = 110 (Pro), 32 x 20 = 640 (Flash) [paper:§4] | `branch_count=4, refine_count=4` (20 calls); `round_budget="fallback"` | cells per branch = `refine_count+1`; "11 refinement steps" is ambiguous [spec §3.5] |
| hard grid caps | the grid is a hard bound [paper:App.B.2 L2:239] | `hard_max_branch=12, hard_max_refine=12` | **raise them for grids above 12** or the plan is clipped (it happened at 32 x 20) |
| workers W | not stated (presumably = workspaces) | `W=4` | set `W=branch_count` so round 1 runs all roots in parallel |
| online / replay round caps K1, K2 | not reported; replay uses `budget=None` | `K1=None, K2=None` | |
| versions per phase M | not reported | `M=4` incl. incumbent; `m_semantics="versions"` | off-by-one in paper text [spec §3.2] |
| beta1, beta2 | not reported; doc toy 0.010 / 0.005 [doc] | `beta1=0.01, beta2=0.005, normalize=True` | beta2 changes the argmax only from about 0.1 (E7) |
| lambda, beta grid | not reported | `lam=0.1, beta_grid=(0.2,...,1.0)` (`objective="pareto"`) | spec §9.3 values, `[inferred]` |
| default policy beta | about 0.6; step 0.1-0.2 [paper:App.B.2 L2:176-184] | in the developer's rule `choose_default_beta` | rarely fires on short runs |
| objective | Eq.1 in §3; Pareto sweep in App.B.2 | `objective="eq1"` | the two rank policies almost independently (rho=-0.06, E7) |
| out-of-support plan | "cannot earn replay reward" [paper:App.B.2 L2:210] | `support="no_reward"` (`"clip"` option) | |
| root rule in replay | earliest-created branch [paper:§3] vs addressable cells [App.B.2] | `root_mode="earliest"` | |
| selection | argmax incl. incumbent [paper:§3] | `selector="argmax"`, `include_incumbent=True` | `"guarded"` adds held-out worlds + noise margin |
| developer base | strong recent [L2:247] vs latest [§3] | `developer_base="strongest"` | |
| root of round t; dreaming after last round | not specified | `root="best"`; `dream_last=False` | `"seed"` restarts; a last dream cannot affect any live search |
| sandbox | not in paper | `sandbox="subprocess"` | never run LLM policies in-process |
| agent / developer model | Gemini CLI 3.1-Pro / 3.7-Flash; developer not named | `llm_propose` / `llm_develop` | |
| agent history shown | all history, "not a sample" [paper:App.B.1 L1:9] | `agent_history_cycles=None` (all) | caps are options |

## 6. Where our implementation differs

From `implementation.md` section 4 and `claims-audit.md` (83 claims: 43 reproduced, 12 partial, 1 not reproduced, 24 not
testable here, 3 contradicted).
- **Reimplementation from text and prompts** (Listings 1 and 2 verbatim apart from paths and a `FRAMEWORK_NOTES` block). Offline
  experiments use mock agents, not Gemini; live runs are tiny (Haiku, <= 18 calls).
- **Gaps resolved by us** (all `[inferred]`): several root cells per batch (the set formalism allows one); missing continuation
  = an empty reveal that still counts a round; per-world score normalisation; `beta` sweep for the deployed version only under
  Eq.1; every episode starts in a fresh policy namespace (cross-episode memory was a real cheat, audit N1) and `reset()` is
  only allowed before the first probe; disqualified episodes score `floor - beta1*|world| - 1`.
- **Extras the paper lacks**: AST lint, subprocess sandbox, leakage screen, `GuardedSelector`, `DiscoveryTree.from_ledger`
  (any method's ledger becomes a world), write-only audit trace and shadow monitor. No GPU kernels; direction provider is ours.

Audit verdicts that change how to use it:
- **Reproduced**: replay is an exact, near-free simulator (64/64 replays identical to the live rollout, 0 agent calls); the
  no-peeking guard disqualifies oracle and memory cheaters; the incumbent-as-candidate rule gives `V* >= V0` (150/150 phases);
  replay ranks policies well **inside** the recorded support (Spearman 0.78-0.92); fewer calls per round (31-40% at equal
  rounds, paper 42% on Lasso-Pro); the policy conserves calls and re-expands after plateaus on some tasks; thousands of
  candidates replay cheaply (12,000 episodes in 128 s).
- **Partial / not reproduced**: better quality at equal rounds does **not** reproduce (synthetic worlds -0.038, sum-diff and
  circle packing tie, autocorrelation slightly worse, Lasso inconclusive); at equal calls Dream wins only on synthetic worlds,
  and a hand-tuned smaller fixed grid (3x3) ties it, so the gain is "spend fewer calls per round", not better within-round choices.
  Written guidance hurts on synthetic worlds but not on circle packing; "helps early" and loss of diversity with a live LLM did
  not reproduce (audit L5, L6). Objective choice is not robust: the same winner in only 5/12 variants (E14). Developer cost is a
  real part of the bill: about 9% of spend at paper scale (Pro) per phase, above 10% in some phases (A1).
- **Contradicted by the paper's own data**: "consistently superior" on Lasso (Fig.3b: Pro is worse in rounds 2-4), "matches or
  surpasses strong baselines" (autocorrelation is worse), and the overview's "open code" (code unreleased).

## 7. Applying it with rsi

```python
import rsi
from rsi.dream import Config
cfg = Config(rounds=6, branch_count=6, refine_count=5, W=6, M=4, hard_max_branch=12, hard_max_refine=12,
             max_calls=300)                                   # or a dict of Config fields
res = rsi.improve(dom, seed, method="dream", llm_task="claude:haiku", llm_propose="claude:sonnet",
                  config=cfg, out_dir="runs/x", cache_dir=".rsi_cache/x", llm_develop="claude:sonnet")
report = rsi.transfer_report(dom, task_llm, {"seed": res.baseline, "dream": res.best}, k=2)   # sealed splits
```
Run `Config(dream=False)` with the same numbers as the Fixed Exploration control. Fields users usually set: `rounds`, `branch_count`, `refine_count`, `W`, `max_calls`,
`M`, `objective` (`"eq1"`/`"pareto"`), `selector="guarded"` (+ `holdout_frac`, `noise_z`), `root`, `dream_last`, `sandbox`.
`res.best` is the best program found, `res.meta` holds `policy` (final `method.py`), `worlds`, `manifests`, `cost`.

**Audit with `python -m rsi.cli inspect runs/x`** (`TRACE.md`, `trace.jsonl`; also `trajectory.json`, `policies.jsonl`,
`trace_pool/iter<t>/`, `history/r####_*/`):
- `round_start`: deployed policy, requested vs effective plan (clipped by `hard_max_*`?), directions, root score.
- `online_round` notes: the prefix the policy saw, legal-set size, the batch, the reveals. `proposal`/`eval` per attempt: the
  agent prompt, the real program diff vs its parent, the score; check it edited the intended files only.
- `live_cycle_manifest` note: calls, `N`, `k`, batch sizes, round best. Calls per round should fall below the cap if dreaming
  works; flat 100% of the cap means the policy never changed.
- Policy `proposal` (Listing-2 prompt, reply, `method.py` diff), `critic` (lint + leakage), `eval` (V, per-world `V_i`, Eq.1
  terms): recompute one `V_i` by hand. `gate`/`decision` for selection: values of all versions, who won and why (ties keep the
  incumbent). `beta_sweep` note: the curve must vary with beta, else the dial is dead. `monitor`: sealed-split score per new best.

**Method-specific failure modes**:
- **Dreaming is often a no-op**: live Haiku policies mostly tie the parallel-refine baseline on replay, so the incumbent is kept
  and the run equals Fixed Exploration. Check `selected` per phase.
- **Frugality bias**: the replay quality term is capped at the best recorded score, so replay rewards reaching it with fewer
  probes; a replay-selected frugal policy found significantly less online (-0.004 to -0.006). Treat Dream as a cost saver.
- **Winner's curse / overfit**: argmax over the same worlds the developer read, no noise margin; at t=1 there is one world. Use
  `selector="guarded"` once there are >= 3 worlds; it narrows but does not remove the gap (E9).
- **Support limit**: replay cannot score a policy that wants wider or deeper grids than were recorded. Keep early grids generous.
- **Single noisy trial per node**: replay treats each recorded score as truth, and replay assumes outcomes do not depend on the
  sibling history the agent read. Noisy or timing graders corrupt every world; re-measure finalists on a sealed split.
- **Objective sensitivity**: Eq.1 vs Pareto, beta1/beta2/lambda are not given by the paper and change the winner; fix them before
  the run and record them.
- **Cost**: developer requests (one per revision per phase) come on top of agent calls; count them.
