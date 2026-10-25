# Dream-RSI: improve the search policy of a discovery loop by replaying past searches

Method reference for `rsi.improve(..., method="dream")` (alias `dream-rsi`). **Primary source: the paper, arXiv 2609.14858
v2 (6 Oct 2026)**, checked here against its HTML text. Citations: `[paper §x]`, `[paper Eq. 1]` (its only numbered
equation), `[paper Table n]`, `[paper Fig. n]`, `[paper Listing 1 Ln]` / `[paper Listing 2 Ln]` (line n of the prompts in
App. D.1 / D.2). **v1 (14 Sep 2026) differs**: its Eq. 1 is `best - beta1*N + beta2*N/max(1,k)`, its §3 has an off-by-one in
M, it has no Table 3, no harness task and no Table 2, and its prompts are App. B.1/B.2 (the listing text and line numbers are
the same). `[paper v1 ...]` marks v1-only text. The repo's `docs/methods/dream-rsi/paper-spec.md` (tags `[paper:...]`,
`[doc]`, `[inferred]`), `implementation.md` and `claims-audit.md` were written from the v1 PDF. The code in `rsi/dream/` is
the source of truth for "this package". The paper links `github.com/zhengkid/Dream-RSI`. In Sept 2026 that repo held only a
README and the PDF (spec); we did not re-check it. "Not specified in the paper" = v2 does not say. "rsi choice" = our decision.

## 1. In one paragraph

Zheng et al. (Google, UMD, Google DeepMind, UVA; arXiv 2609.14858) improve the **exploration policy** of an agentic
discovery system: the code that decides which attempts to extend, how many to run in parallel, when to open a new branch and
when to stop [paper §1, §2]. Models, coding agent, evaluator and execution interfaces stay fixed; only the policy code
changes [paper §3]. Key idea: a finished search is a simulator. It is stored as a *discovery tree* (every attempt, its parent,
its score). Another policy can be tested on it by walking it differently, and outcomes are *revealed* from the record, with no
new agent or evaluator calls [paper §2, Fig. 2]. Each outer round: (1) the deployed policy runs online (expensive); (2) the
tree joins a pool of replay worlds; (3) a policy-development agent rewrites the policy code M-1 times, each version is
replayed on every world under a sweep of budget preferences rho, and the version with the best mean replay score (current
policy included) is deployed next [paper §3, Fig. 1; App. B.2]. Optimised: **Eq. 1**, per world the normalised hypervolume
of the quality-vs-calls Pareto frontier over the rho sweep minus `lambda` times a parallelism penalty (sequential rounds per
call) [paper §3 Eq. 1]. Reported: 9 tasks in 4 domains: Lasso path solver, 3 math tasks, 4 KernelBench kernels (Gemini-3.1-Pro;
Gemini-3.7-Flash on Lasso only) and Terminal-Bench 2.1 harness optimisation (Claude Opus 4.8 with Claude Code)
[paper §4]. Lasso: about 1.7x fewer discovery-agent calls than Recursive Fixed Exploration (317 vs 550 Pro, 1879 vs 3200
Flash) with lower mean held-out runtime [paper §4.3, Fig. 4a]. Quality gains are mixed: math is better on 1 of 3 tasks
[paper Table 1], kernels are faster or cheaper [paper §4.1, Fig. 3], and Pro's Lasso gain comes from RCV1 alone [paper §4.3].

## 2. Inputs and setup

Needed before round 1:
- **Artifact** (seed program): any `rsi.core.Artifact`. The paper's root `r` is "the initial workspace state" [paper §3].
  Whether round t starts from the seed or from the best program so far is not specified in the paper. rsi choice: `Config.root="best"`
  (default) or `"seed"`.
- **Domain / task**: any `rsi.core.Domain` (wrapped by `DomainTask`), a discovery domain from `rsi.domains.discovery`, or a
  `DiscoveryTask`. Scores must be larger-is-better [paper §3]: negate minimisation tasks. rsi: the grader also returns a typed
  `fail_class`. The decision split is the first non-empty of `evolve`, `train`, `val`. A sealed or empty split raises.
  `task_kwargs` (e.g. `directions=[...]`, `k=...`, `split=...`) configure the wrapper.
- **Models**: `llm_propose` = the frozen discovery agent (`EditorAgent`, Listing-1 prompt); `llm_develop` = the policy
  developer (Listing-2 prompt), default `llm_propose`. That matches the paper, where "the same base agent dreams"
  [paper Fig. 1] and Claude Opus 4.8 is both agent and developer on the harness task [paper §4.4]. `llm_task` runs the domain.
  Without LLMs the domain's `mock_agent()` and `ParametricMutator` are used (offline only).
- **Budgets** [paper Table 3, App. B.2]: T online rounds (Lasso 5, kernels 9, math 10, harness 9). W workers (10 Pro,
  32 Flash, 4 harness). A per-round cap B = branches x attempts per branch, *including the initial attempt*: 110 = 10 x 11,
  640 = 32 x 20, 16 = 4 x 4. M = 4 policy versions per offline phase (the current one + 3 new). B is an upper limit: the
  policy may spend less. rsi: `rounds`, `W`, `branch_count x (refine_count+1)`, `M`, optional total `max_calls`.
- **Measured first**: not specified in the paper beyond defining the root score `s_r = 0` for the objective [paper §3].
  The policy API exposes a `baseline_score` [paper Listing 2 L37]. rsi: the seed is scored once on the decision split
  (`seed_eval`, one trial per task) and becomes the root score. No noise band is measured (the `noise` trace event has
  `delta=None`), so measure the grader's noise yourself (SKILL.md step 3).
- **Initial policy pi_1**: "the same hand-designed starting policy" for both arms [paper §4]. v1 calls it parallel refining:
  independent workspaces, each refining its own candidate [paper v1 §4]. Listing 2 calls it "a parallel-refine floor to beat"
  [paper Listing 2 L250-251]. rsi: `parallel_refine` opens all `branch_count` roots in round 1 (in one batch when
  `W >= branch_count`), then deepens every branch each round. Both arms behave identically in round 1 [paper §4.3], so
  `Config(dream=False)` = Recursive Fixed Exploration, the paper's controlled baseline.

## 3. Round by round (what the paper does)

One outer iteration `t` = online rollout + pool update + offline dreaming [paper §3, Fig. 1].

1. **Plan the grid.** Before the live grid exists, the deployed policy's deterministic `plan_grid(context)` returns
   `GridPlan(branch_count, refine_count, reason)`. `context` holds only earlier live manifests, fallback/hard caps, the worker
   cap and (in replay) the trace's support fields. It must not read the current cycle's outcomes [paper Listing 2 L190-220].
   The runner checks `1 <= branch_count <= hard_max_branch_count` and `0 <= refine_count <= hard_max_refine_count`
   [paper Listing 2 L208-210]. What happens to an invalid plan is not specified in the paper. A "direction provider assigns those
   new roots their directions" [paper Listing 2 L236-237]; how it does so is not specified. The §3 formalism does not mention
   `plan_grid` at all.
2. **Online rollout** (policy code fixed). The agent has access to the earlier history `H_{t-1}` as context. The tree
   starts as `{r}`. At round `k < K1` the policy picks a batch `C` of eligible nodes, `|C| <= W`. Eligible nodes are `r` plus
   the current leaves. Each `v in C` goes to a worker, which resumes v's saved workspace, generates a candidate and evaluates
   it. Each attempt adds one child of v. The new child becomes its branch's selectable leaf, and `r` stays selectable to open
   new branches. The rollout ends on an empty batch or after `K1` rounds [paper §3]. Batches hold distinct legal cells, "may
   contain several roots and/or one frontier from each opened branch", and never a parent with its child [paper Listing 2
   L103-105]. The grid is a hard bound [paper Listing 2 L239-240], and B is the per-round maximum, the same for both arms
   [paper App. B.2, §4].
3. **Record the world.** The final tree `T_t` is appended: `H_t = H_{t-1} ∪ {T_t}` [paper §3]. Each node keeps its
   generation history, filesystem snapshot and score `s_v` [paper §3]. A `trace_pool/iter*/live_cycle_manifest.json`
   records the final best score and the baked-in beta [paper Listing 2 L169-172], plus planned/effective grid, opened
   width/depth, probe work, decision rounds and scores [paper Listing 2 L216-217].
4. **Deliverable.** Not specified in the paper. Results report the best-so-far program (e.g. [paper Fig. 5a]).
5. **Dream (offline).** Versions `pi^0 = pi_t, ..., pi^{M-1}`. For `m = 0..M-2` the policy-development agent reads the
   replay trajectories and scores of `pi^m` plus feedback from earlier revisions, then rewrites the code into `pi^{m+1}`.
   That version is replayed on the same t worlds before the next revision [paper §3]. With M = 4 that is 3 rewrites
   [paper App. B.2]. The developer prompt also says: read earlier rounds' policy code and `beta_sweep.json`, "start from a
   strong recent policy" [paper Listing 2 L246-249]; use `policy_execution_traces.jsonl` (one replay episode per
   frozen trace and beta) as between-round feedback only [paper Listing 2 L253-259]; read live manifests outside `solve()`
   [paper Listing 2 L261-263]; beat the parallel-refine baseline [paper Listing 2 L250-251].
6. **Replay every version on every world, for every budget preference** `rho in {0, 0.25, 0.5, 0.75, 1}` [paper §3]. Per
   `(m, i, rho)`: start from `{r}`. Each round the policy, conditioned on rho, picks a batch from the revealed prefix. A
   non-root pick reveals its unique recorded child. A root pick reveals "the earliest-created child of r" not yet revealed.
   A pick with no recorded continuation reveals nothing. Every nonempty batch counts as one round. Replay stops on an empty
   batch, after `K2` rounds, or when the whole tree is revealed [paper §3]. Branches are traversed in recorded order and no
   outcome outside the tree is produced [paper §3]. The prompt's API differs on roots: unopened roots are distinct cells with
   `meta` tags, and "do not choose roots merely because their branch id is small" [paper Listing 2 L33-35, L238]. A plan
   beyond the trace's recorded width or depth "is out of support and cannot earn replay reward" [paper Listing 2 L210-212].
   Replay runs with `budget=None` [paper Listing 2 L140].
7. **Score** [paper §3 Eq. 1]. Per world i: `B_i` = recorded non-root nodes; `S_i > 0` = best recorded score. Per rho:
   `n` = revealed non-root nodes (calls represented); `s` = best revealed score (0 if `n = 0`); `a = clip(s/S_i, 0, 1)`;
   `k*` = completed rounds. Keep the non-dominated `(n, a)` points over rho, sorted by `n`, with `a_0 = 0`. Then
   `V_i^m = (1/B_i) * sum_j (a_j - a_{j-1}) * (B_i - n_j)  -  lambda * (1/|R|) * sum_{rho: n>0} k*/n`
   (normalised hypervolume minus parallelism penalty; zero-call replays add 0 to the penalty). `lambda` is not specified in the paper.
   Listing 2 describes the same objective as `pareto.reward = pareto.auc - lambda * parallel_penalty`, with a batch of size k
   costing `ceil(k/W)` sequential rounds [paper Listing 2 L12-22]. Version value: `V^m = (1/t) sum_i V_i^m`, the plain mean
   over **all** t worlds (no held-out world) [paper §3].
8. **Select and deploy.** `pi_{t+1} = pi^{m*}`, with `m* in argmax_{m in 0..M-1} V^m`. The current policy is a candidate,
   so `V^{m*} >= V^0`: never worse **on replay over H_t**. Nothing is claimed online [paper §3]. Tie-breaking is not specified in the paper.
9. **Default beta for the next cycle.** The policy's single knob `beta` is fixed within an episode and swept by the evaluator
   [paper Listing 2 L145-162]. When writing a version, the developer sets the baked-in default from the last 2-3 live manifests
   plus their `beta_sweep.json`. If the live best plateaued and a higher beta reaches more at reasonable cost, raise it by
   about 0.1-0.2, clamped to [0, 1]. If high beta only adds work, lower it. If the evidence is thin, use about 0.6
   [paper Listing 2 L163-184]. That Listing 2's beta is §3's rho is our reading: the paper never equates them.

**What changes across rounds**: the world pool grows by one tree per round [paper §3], and Table 2 credits that growth: ConvDiv
1.90 with all history vs 1.59 replaying only the current round [paper §5, Table 2]. The deployed policy changes only when a
rewrite wins the argmax. `plan_grid` reads the growing manifest history. Its rules: widen when many roots improve early,
deepen when gains come late on few directions, widen when every direction has plateaued, shrink on repeated hard failures
[paper Listing 2 L222-233]. The agent's context is the whole earlier history ("not a sample") [paper §3; Listing 1 L9].
Nothing is annealed. Observed pacing: ConvDiv attempts per round go 110 -> 50 while progress is fast, then back up after a
plateau [paper §5, Fig. 7; one run]. **Stopping**: a fixed number of online rounds T per task [paper Table 3]. There is no
convergence test. rsi also stops at `max_calls` (checked at the top of each round).

```text
H=[]; pi=pi_1 (hand-designed parallel refine)                 # [paper §3, §4]
for t in 1..T:
    plan = pi.plan_grid(ctx(live manifests, caps, W))         # Listing 2 L190-242, before any outcome
    T_t  = online_rollout(pi, plan, W, K1, history=H)         # <= B agent+grader calls; ends on empty batch / K1
    H.append(T_t)
    V = []
    for m in 0..M-1:                                          # M = 4 [App. B.2]
        if m > 0: pi_m = developer.rewrite(pi_{m-1}, replay traces, scores, earlier feedback)   # LLM, Listing 2
        V.append(mean_i Eq1(pi_m, T_i, rho in {0,.25,.5,.75,1}))          # replay only, no agent calls
    pi = pi_{argmax V}                                        # current policy included: V* >= V0 on replay
```

## 4. Sample by sample

"Sample" = one **attempt** (node = one discovery-agent call from the parent's saved workspace + one evaluation by the fixed
evaluator [paper §3]); inside replay, one revealed node.
- **Trials per attempt**: one agent call per node [paper §3]. The grader's repeats are the task's protocol: Lasso times
  candidates on the 17 SimpleTES synthetic instances [paper §4.3]. Kernels score 1/ms after a correctness check against the
  PyTorch reference [paper §4.1, App. B.1]. Harness candidates run the 89 Terminal-Bench 2.1 tasks twice each [paper §4.4].
  Random seeds are not specified in the paper. Replay is deterministic [paper §3]. rsi: one grader pass over the decision split
  with `k=1` (`DomainTask(k=1)`; raise via `task_kwargs={"k": n}`). The node score is the mean task score. The node seed is a
  hash of `(Config.seed, round, branch, attempt)`. Developer calls use `seed*100003 + t*101 + m`. The subprocess sandbox
  fixes the hash seed.
- **Recorded per attempt**: the agent writes only `proposal.md` (mechanism, evidence from history, why it is not a repeat,
  expected benefit/risk) and the program [paper Listing 1 L25]. Each attempt also has `eval/score.json` and, on failure,
  `error.txt` [paper Listing 1 L9]. The policy's `Observation` carries branch, attempt, score, evaluated, valid,
  `fail_class`, error, `delta_vs_baseline`, `delta_vs_parent`, `n_valid`, `n_total` [paper Listing 2 L40-42]. Success means
  `error is None and fail_class == "ok"`, even when `valid` is false [paper Listing 2 L47-51]. The paper names only
  `ok` and `compile_other` among the fail classes [paper Listing 2 L48, L70]. rsi classes: `compile_other` when every trial
  errored or the agent produced nothing, `env_error` for agent/evaluator crashes or all-infra errors. Diagnostics hold the
  worst-2 task feedback. Tokens and USD go to the cost meter.
- **What each reader sees**: the discovery agent must read every `proposal.md` under sibling `attempt_*/`, the history dir
  and the baseline dir, with their scores and errors, "not a sample" [paper Listing 1 L9]. The **policy** sees only revealed
  observations, `baseline_score`, legal sets, structural `meta` and helper signals. It never sees unrevealed scores, targets,
  winning cell ids or trace data. `best_so_far` and `budget_spent` are bookkeeping only [paper Listing 2 L9-10, L53-55,
  L133-135]. rsi: `PrefixGuard` enforces this. A violation, an illegal batch, a crash, or `reset()` after the first probe
  disqualifies the episode, which scores below every honest one.
- **Aggregation**: per world, Eq. 1 over the rho sweep. Across worlds, a plain mean [paper §3 Eq. 1].
- **Splits**: replay worlds have no held-out split [paper §3]. The paper's only held-out evaluation is Lasso: search on 17
  synthetic instances, report on six downstream datasets [paper §4.3, Fig. 4a]. Math, kernel and harness numbers are on the
  search objective itself [paper Table 1, Fig. 3, §4.4]. rsi: every node score and replay value comes from the decision split.
  The loop never reads `holdout`, `ood` or `test` (`DomainTask` raises on sealed splits). Only the write-only shadow monitor
  (once per new best, `shadow_monitor=True`) and `rsi.transfer_report` afterwards touch them.

## 5. Paper defaults vs this package

| setting | paper value [citation] | rsi `Config` field = default | note |
|---|---|---|---|
| rounds T | Lasso 5, kernels 9, math 10, harness 9 [paper Table 3] | `rounds=5` | |
| calls per round B | 110 = 10 x 11, 640 = 32 x 20, 16 = 4 x 4, attempts per branch *incl.* the first [paper Table 3, App. B.2] | `branch_count=4, refine_count=4` (20); `round_budget="fallback"` | paper 10 x 11 = `branch_count=10, refine_count=10` |
| hard grid caps | exist and are checked [paper Listing 2 L208-210]; values not specified | `hard_max_branch=12, hard_max_refine=12` | raise them for larger grids or plans get clipped |
| workers W | 10 (Pro), 32 (Flash), 4 (harness) = branch count [paper Table 3] | `W=4` | set `W=branch_count` |
| K1, K2 | not specified; replay uses `budget=None` [paper Listing 2 L140] | `K1=None, K2=None` | |
| versions per phase M | 4, incl. the current policy [paper App. B.2, Table 3] | `M=4`, `m_semantics="versions"` | `"revisions"` covers v1's off-by-one reading |
| objective | Eq. 1 = Pareto hypervolume over rho - lambda * penalty [paper §3 Eq. 1] | `objective="eq1"` = **v1's** Eq. 1 | `objective="pareto"` is the v2 form (§6) |
| beta1, beta2 | v1 Eq. 1 only, values not given [paper v1 §3] | `beta1=0.01, beta2=0.005, normalize=True` | rsi choice (overview toy values `[doc]`) |
| rho grid, lambda | rho in {0, 0.25, 0.5, 0.75, 1} [paper §3]; lambda not specified | `beta_grid=(0.2,...,1.0)`, `lam=0.1` | rsi choice |
| attainment | `clip(s/S_i, 0, 1)`, with `s_r = 0` [paper §3] | `pareto_attainment="shift"`: `(s-s_r)/(S-s_r)` | `"ratio"` = paper |
| default policy beta | about 0.6; step 0.1-0.2 [paper Listing 2 L179-184] | Listing-2 text (LLM developer); `choose_default_beta` (mock) | rarely fires on short runs |
| out-of-support plan | "cannot earn replay reward" [paper Listing 2 L210-212] | `support="no_reward"` (`"clip"` option) | |
| root rule in replay | earliest-created branch [paper §3] vs addressable root cells [paper Listing 2 L33-35] | `root_mode="earliest"` | |
| selection | argmax incl. the current policy [paper §3]; ties not specified | `selector="argmax"`, `include_incumbent=True` | ties keep the incumbent; `"guarded"` is an extension |
| developer base | `pi^m -> pi^{m+1}` [paper §3] vs "a strong recent policy" [paper Listing 2 L247] | `developer_base="strongest"` | `"latest"` = §3 |
| root of round t; dreaming after the last round | not specified | `root="best"`; `dream_last=False` | a last dream cannot affect any live search |
| sandbox | not specified | `sandbox="subprocess"` | never run LLM policies in-process |
| agent / developer model | Gemini CLI with 3.1-Pro / 3.7-Flash; Claude Opus 4.8 + Claude Code (harness); developer = the same agent [paper §4, §4.4, Fig. 1] | `llm_propose` / `llm_develop` (default = `llm_propose`) | |
| agent history shown | all history, "not a sample" [paper Listing 1 L9] | `agent_history_cycles=None` (all) | caps are options |

## 6. Where our implementation differs

The audit (`claims-audit.md`: 83 claims; 43 reproduced, 12 partial, 1 not reproduced, 24 not testable here, 3 contradicted)
and `implementation.md` §4 were written against **v1**. v2 changes below are marked "(v2)".
- **Objective (v2).** Our default `objective="eq1"` is v1's `best - beta1*N + beta2*N/max(1,k)`, run once per world with
  no rho sweep. The paper's current Eq. 1 is the rho-swept Pareto form, which `objective="pareto"` approximates with four
  gaps. (a) It averages attainment and probe fraction per beta across worlds, then builds one frontier; the paper builds one
  frontier per world and averages `V_i`. (b) The default attainment is `"shift"`, not `clip(s/S_i)`. (c) The grid is
  0.2..1.0, not 0..1. (d) Zero-call and disqualified replays get penalty 1, not 0. Under `"eq1"` only the deployed version is
  beta-swept (for `beta_sweep.json`), whereas the paper replays every version under every rho.
- **Reimplementation from text and prompts.** Listings 1 and 2 are verbatim apart from paths and a `FRAMEWORK_NOTES` block.
  Offline experiments use mock agents, not Gemini. Live runs are tiny (Haiku). There are no GPU kernels and no
  Terminal-Bench run. The direction provider (classes cycled, shuffled per round) is ours.
- **Gaps resolved by us** (all `[inferred]`): several root cells per batch (the paper's set formalism allows `r` once;
  Listing 2 allows several roots). A missing continuation is an empty reveal that still counts a round (this matches §3's
  round counting). Invalid plans fall back to the fallback grid and out-of-cap plans are clamped. Every episode starts in a
  fresh policy namespace, because cross-episode memory was a real cheat (audit N1), and `reset()` is allowed only before the
  first probe. Disqualified Eq.1 episodes score `floor - beta1*|world| - 1`. The best program is replaced on any **strict**
  gain, with no paired test or noise band.
- **Extras the paper lacks**: AST lint, subprocess sandbox, leakage screen (best cell ids and scores, one repair round),
  `GuardedSelector`, `DiscoveryTree.from_ledger` (any method's ledger becomes a world), write-only audit trace, shadow monitor,
  `max_calls`.

Audit verdicts that change how to use it:
- **Reproduced**: replay is an exact, near-free simulator (64/64 replays match the live rollout, 0 agent calls). The
  no-peeking guard disqualifies oracle and memory cheaters. Including the incumbent gives `V* >= V0` (150/150 phases).
  Replay ranks policies well **inside** the recorded support (Spearman 0.92 synthetic, 0.78-0.81 on two real tasks). Dream
  makes fewer calls per round at equal rounds (31-40% fewer; the paper's Lasso-Pro figure is 42%). The policy conserves
  calls, and re-expands after plateaus on some tasks. Candidates replay cheaply (12,000 episodes in 128 s).
- **Partial / not reproduced**: better quality at equal rounds does **not** reproduce (synthetic -0.038 to -0.040, sum-diff
  and circle packing tie, autocorrelation slightly worse, Lasso inconclusive or worse). At equal calls Dream wins only on
  synthetic worlds, and a hindsight-best fixed 3x3 grid ties it there, so the gain is "spend fewer calls per round".
  Written guidance hurts on synthetic worlds but not on circle packing. "Helps early" and loss of diversity with a live LLM
  did not reproduce (audit L5, L6). The objective choice is not robust: the same winner in only 5/12 variants (E14). The
  developer is a real part of the bill: 14-30% of spend in our live runs; about 9% per phase at paper scale (Pro), above 10% in
  some phases (A1).
- **Contradicted (against v1 wording)**: "consistently superior" on Lasso (Pro is worse in rounds 2-4 of v1 Fig. 3b); "matches or
  surpasses strong baselines" (autocorrelation is worse); the overview's "open code". **(v2)** drops "consistently" ("by the
  final round", [paper §4.3]) and narrows math to "two of three tasks" [paper §1]. Re-audit both.
- **Not yet tested here (v2)**: the history-accumulation result [paper Table 2] and the harness result [paper §4.4].

## 7. Applying it with rsi

```python
import rsi
from rsi.dream import Config
cfg = Config(rounds=6, branch_count=6, refine_count=5, W=6, M=4, hard_max_branch=12, hard_max_refine=12,
             max_calls=300)                                   # or a dict of Config fields
# closest to the paper's current Eq. 1: objective="pareto", pareto_attainment="ratio", beta_grid=(0, .25, .5, .75, 1)
res = rsi.improve(dom, seed, method="dream", llm_task="claude:haiku", llm_propose="claude:sonnet",
                  config=cfg, out_dir="runs/x", cache_dir=".rsi_cache/x", llm_develop="claude:sonnet")
report = rsi.transfer_report(dom, task_llm, {"seed": res.baseline, "dream": res.best}, k=2)   # sealed splits
```
Run `Config(dream=False)` with the same numbers as the Fixed Exploration control. Fields users usually set: `rounds`, `branch_count`, `refine_count`, `W`, `max_calls`,
`M`, `objective` (`"eq1"`/`"pareto"`), `selector="guarded"` (+ `holdout_frac`, `noise_z`), `root`, `dream_last`, `sandbox`.
`res.best` is the best program found. `res.meta` holds `policy` (the final `method.py`), `worlds`, `manifests` and `cost`.

**Audit with `python -m rsi.cli inspect runs/x`** (`TRACE.md`, `trace.jsonl`; also `trajectory.json`, `policies.jsonl`,
`trace_pool/iter<t>/`, `history/r####_*/`):
- `round_start`: the deployed policy, requested vs effective plan (clipped by `hard_max_*`?), directions, root score.
- `online_round` notes: the prefix the policy saw, legal-set size, the batch, the reveals. `proposal`/`eval` per attempt: the
  agent prompt, the real program diff vs its parent, the score. Check that it edited the intended files only.
- `live_cycle_manifest` note: calls, `N`, `k`, batch sizes, round best. Calls per round should fall below the cap if dreaming
  works. A flat 100% of the cap means the policy never changed.
- Policy `proposal` (Listing-2 prompt, reply, `method.py` diff), `critic` (lint + leakage), `eval` (V, per-world `V_i`, Eq. 1
  terms): recompute one `V_i` by hand. `gate`/`decision` for selection: the values of all versions, who won and why (ties
  keep the incumbent). `beta_sweep` note: the curve must vary with beta, else the dial is dead. `monitor`: the sealed-split
  score per new best.

**Method-specific failure modes**:
- **Dreaming is often a no-op**: live Haiku policies mostly tie the parallel-refine baseline on replay, so the incumbent is kept
  and the run equals Fixed Exploration. Check `selected` per phase.
- **Frugality bias**: replay quality is capped at the best recorded score, so replay rewards reaching it with fewer probes.
  This holds in both Eq. 1 versions. A replay-selected frugal policy found significantly less online (-0.004 to -0.006).
  Treat Dream as a cost saver.
- **Winner's curse / overfit**: the argmax runs over the same worlds the developer read, with no noise margin, and at t=1
  there is one world. Use `selector="guarded"` once there are >= 3 worlds. It narrows the gap but does not remove it (E9).
- **Support limit**: replay cannot score a policy that wants wider or deeper grids than were recorded. Keep early grids generous.
- **Single noisy trial per node**: replay treats each recorded score as truth, and assumes outcomes do not depend on the
  sibling history the agent read. Noisy or timing graders corrupt every world. Re-measure finalists on a sealed split.
- **Objective sensitivity**: v1 vs v2 Eq. 1, lambda, the rho grid and the attainment definition change the winner, and the
  paper gives no lambda. Fix them before the run and record them.
- **Cost**: developer requests (one per revision per phase) come on top of agent calls. Count them.
