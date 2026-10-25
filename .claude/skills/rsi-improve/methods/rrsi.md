# RRSI: regularized recursive self-improvement of an agent harness

Method reference for `rsi.improve(..., method="rrsi")`. Sources: `docs/methods/rrsi/paper-spec.md` (the spec),
`implementation.md`, `claims-audit.md`, `rsi/rrsi/`. Tags are the spec's: `[Alg. 1]`/`[Alg. 2]` (proposal / selection
side), `[Eq. (anneal|history|yield|explore|prune|tokenbudget|floor|estimate)]`, `[code:...]` (reference code
`google-research/rrsi@be50316`), `[doc]`, `[sec:...]` (secondary summaries). **The spec's author could not open the
paper (arXiv 2609.24972): there are no `[paper §n]` tags.** Anything tagged `[code]` is what the released code does;
anything not in the spec is "not specified in the paper" below.

## 1. In one paragraph

Xia et al. (Google Cloud AI Research, arXiv 2609.24972) evolve the **harness** `H` (prompts, control flow, config,
output plumbing, context management, tools, skills, memory, sub-agents) around a **frozen** policy model. Plain harness
evolution reuses one small task set both to suggest edits and to judge them, so it overfits: big practice gains, small or
negative gains on unseen benchmarks, and a bloated harness [doc][sec:abstract]. RRSI keeps the edit space open and
regularizes how the search moves. Proposal side: an annealed cap `b_t` on independent edits per candidate, a ledger of
past edits with measured score/cost change, a reserved slot for never-tried components when progress stalls. Selection
side: a leakage critic before any evaluation, a noise-adjusted floor `S' >= S* - delta`, a token-cost rule, and pruning
directives. What it optimises is the measured evolve score `S_hat` (mean verifier reward over `k` trials per task), but
only among candidates that survive these guards; the goal is gain on benchmarks the loop never sees, at lower token cost
[code:rrsi/README.md][doc].

## 2. Inputs and setup

Needed before round 1:
- **Artifact `H_0`**: the editable harness (files). Frozen: policy model and its settings, grader, task set, interface
  contract [code:rrsi/propose.py:SYSTEM_TMPL]. Here: `Artifact` (content-addressed, no git).
- **Splits**: `evolve` (D; the only split the loop reads). Strongly recommended `holdout` (same kind) and `ood`
  (different kind); the paper's runs used held-out and OOD benchmarks only for the final report [spec 7.1].
- **Grader**: deterministic and outside the harness; reward `r in [0,1]` per trial, plus tokens `c` per trial (the cost).
  Feedback text per trial is what the analyst/proposer read.
- **Models**: frozen task model (policy); proposer, analyst/digesters and critic (all Claude Opus 4.8 in the paper). Here:
  `llm_task`, `llm_propose`; `llm_critic` defaults to `llm_propose`. `analyst="auto"` uses the LLM analyst only if
  `llm_analyst` is passed, otherwise a deterministic heuristic clusterer over grader feedback; set `analyst="llm"` to
  use `llm_propose` for the three-lens analyst and digesters.
- **Domain extras** (optional): component taxonomy `components`/`structural_components` (K, K_str), `critic_patterns`
  (task-id and grader-artefact regexes), non-compensatory `guards` (paper: eng valid-rate and no-payload guards),
  `regression_threshold(k)`, a constitution (SKILL.md/PATTERNS.md; a generic one is generated).
- **Budgets**: `T` rounds, `m` candidates/round, `k` trials/task, `repair_rounds`; optional `Budget(max_usd, max_rounds,
  max_rollouts, max_wall_s)`.

Measured first (`RRSIRun.baseline`, `calibrate`) [Alg. 1 setup, code:rrsi/loop.py:Run.baseline]:
1. Evaluate `H_0` on all of D with `k` trials (job `base`) -> `S_0`, `C_0`. Abort if missing trials exceed
   `invalid_missing_frac * n_expected` (one retry first).
2. Initialise `frontier`: incumbent = `H_0`, `S* = S_0`, `trajectory = [S_0]`; history gets a `BASELINE` row.
3. Noise band `delta`: fixed `Config.delta` if set (the paper's runs used fixed 0.017 / 0.004 / 0.020), else
   `delta = delta_z * sd_null` [spec 4.4]. One base evaluation: within-task bootstrap of trials (2000 reps, seed 7),
   `sd_null = sqrt(2) * se`. `calibration_repeats = R >= 2`: `sd_null = sd(S_hat_r) * sqrt(2)` over R base evaluations.
   If `delta == 0` (k=1, or a deterministic grader) a warning is written: set `delta` yourself.

## 3. Round by round

One round `t = 0..T-1` = Algorithm 1 (draft + screen) then Algorithm 2 (select) [spec 3, code:rrsi/loop.py:Run.round].

1. **Incumbent.** `H_t` and its stored evaluation (no re-run). `S_t`, `C_t` from that evaluation; `S*` = best incumbent
   score so far.
2. **Analyze.** `F_t`: from the incumbent's stored per-task results take the `n_fail_traces` lowest-mean tasks (their
   worst trial) and `n_success_traces` highest (best trial). An analyst plus read-only digester sub-agents (three lenses:
   failure, capability gap, success) write a report of failure modes, capability gaps and success habits that the change
   "MUST NOT break". Needs at least `0.5 * min(|D|, n_fail + n_success)` traces or the round raises.
3. **Directives** (none depend on `F_t`):
   - `b_t = ceil(b_min + (b_max - b_min) * 0.5 * (1 + cos(pi t / T)))` [Eq. (anneal)];
   - tried `T_t` = components with a *measured* edit in the ledger; untried `U_t = K \ T_t`;
   - stall `sigma_t = 1[S_t - S_{t-w} <= delta]`, 0 while `t < w` [Eq. (explore)];
   - yield `g_t(l) = max dS of l over the last n_prune rounds`; prune set `B_t = {l in T_t : g_t(l) <= 0}` with the
     accepted machinery in each [Eq. (yield)][Eq. (prune)];
   - edit ledger `L_t` (last 40 records) and attribution scoreboard (last 20 rows).
4. **Draft `m` candidates**, each independently from the **same** `H_t` (variants A, B, ...). The proposer reads: the
   constitution + pattern library, `L_t` with the "rejected mechanism is negative evidence, do not redraw it" header,
   the scoreboard, `E_t`, `B_t` (prune edits are legitimate), `F_t`, per-task digests, the full current harness source,
   `b_t`, and the per-trial traces of the incumbent. It must ship **at most `b_t` independent edits**, each declaring
   `id, component in K, hypothesis, targets_mode, predicted_affected (task ids), retroactive_check` (corrective /
   preservative / transfer). There is no abort: it must ship a candidate. Reserved slot: if `sigma_t = 1` and `U_t` is
   non-empty, the last `m_draft` variants must put at least one edit on a component in `U_t`; checked on the declared
   tag and again on the diff-derived tag.
5. **Screen (no evaluation spent yet).** Regex/denylist precheck of the diff, then LLM leakage critic (six REJECT
   rules: task-specific content, degenerate edits, grader gaming, undeclared bundling, runtime memory leakage, unbounded
   work). A reject goes back to the proposer for up to `repair_rounds` repairs, else the candidate is dropped
   (`critic_reject`). Component tags are re-derived from the diff (declared tag kept only if the diff evidences it).
   Then a liveness smoke (not a selection rule): `smoke_n` evolve tasks, 1 trial, must run (`smoke_fail` otherwise).
6. **Evaluate** every surviving candidate on **all of D**, `k` trials per task, on a fresh job (own seed block)
   -> `S'`, `C'`. If missing trials exceed `invalid_missing_frac` after one retry: `eval_invalid`.
7. **Accept (Alg. 2).** For each candidate: `dS = S' - S_t`, `dC = (C' - C_t)/C_t` (0 if a token count is missing),
   `nu` = number of edit components in K_str with zero accepted edits so far (novelty).
   Admissible iff all of, in this order:
   - floor: `S' >= S* - delta` [Eq. (floor)] ("below noise-adjusted floor");
   - cost rule `c` ("cost rule failed"): if `dS > delta`: `dC <= beta0 + beta1 * dS` [Eq. (tokenbudget)];
     otherwise (inside the band, including negative `dS`): `w_s*dS - w_c*dC + w_n*nu > 0`;
   - every domain guard holds ("domain guard violated").
   Winner = `argmax S'` over admissible (first on ties), **not** the argmax of the shaped score. No admissible
   candidate -> `H_{t+1} = H_t`.
8. **Update state.** Ledger: one row per edit with `dS`, `dC`, outcome (`ACCEPTED`, `LOST`, `REJECTED`, `critic_reject`,
   `smoke_fail`, `eval_invalid`, `no_proposal`); bundle members all carry the bundle's single measurement; unmeasured
   rows do not enter `T_t`/`g_t`. Scoreboard rows (hits on `predicted_affected`, unpredicted regressions). Incumbent
   = winner; `S* = max(S*, S_{t+1})`; append `S_{t+1}` to the trajectory.

```text
setup: ev0 = Evaluate(H0, D, k); S* = ev0.S; delta = cfg.delta or z * sd_null(ev0)
for t in 0..T-1:                                  # stop early: STOP file, Budget, 3 consecutive round failures
    b_t = ceil(b_min + (b_max-b_min) * (1+cos(pi*t/T))/2)
    sigma = [S_t - S_{t-w} <= delta]; U = K - tried; B = prune_set(t, n_prune)
    F = analyze(worst/best traces of incumbent's stored eval)
    for v in 0..m-1:
        reserved = sigma and U and v >= m - m_draft
        edits, diff = propose(H_t, F, L_t, b_t, U if reserved, B)        # <= b_t edits
        diff = critic_with_repair(diff, edits, repair_rounds)            # else drop
        if not smoke(diff): drop
    for c in survivors: c.S, c.C = Evaluate(c, D, k)                     # full evolve split
    adm = [c for c in survivors if c.S >= S* - delta and cost_ok(c) and guards_ok(c)]
    winner = argmax_{adm} c.S  or None
    H_{t+1} = winner or H_t; S* = max(S*, S_{t+1})
return H_T                                        # the final incumbent, not the best-scoring artifact
```

**What changes across rounds**: `b_t` shrinks (cosine), the ledger and scoreboard grow, `T_t` grows (so `U_t` shrinks),
`B_t` accumulates components that stopped paying, `S*` ratchets up (and the floor with it), the incumbent moves only on
acceptance. `delta`, `beta*`, `w_*` are frozen for the whole run (and for transfer tests [doc]).
**Stopping rule**: the paper runs a fixed horizon `T`; there is no convergence test. Here also `Budget`, a `STOP`
file in the run dir, or `infra_failures` (3 consecutive failed rounds). A killed run resumes from `out_dir`.

## 4. Sample by sample

One "sample" is one task `x` in `D_evolve` inside one evaluation.
- **Trials**: `k` independent rollouts of the frozen policy with the harness (paper: k=2 coding/workspace, k=4 eng).
  Seeds: not specified in the paper. Here each evaluation job (`base`, `r<t><V>`) gets its own block of `k` seeds
  derived from `(Config.seed, job name)`, so candidate and incumbent are **not** seed-paired; noise is handled by
  `delta`, not by pairing.
- **Recorded per trial**: reward `r`, tokens `c`, steps, error, grader feedback, trace (capped at `trace_chars=3000`).
  Per task: mean reward, missing count.
- **Missing trial** (crash, timeout, infrastructure; here `error` starting `infra:`): reward 0, **full denominator**,
  excluded from the token mean, carries the task's weight. Applies to candidates and the baseline alike.
- **Aggregation**: `S_hat = sum(r*w) / sum(w)` over all `k|D|` trials [Eq. (estimate)] (weights 1 unless a task sets
  `meta["weight"]`); `C_hat` = mean tokens over trials with positive token count. Round score of a candidate = this
  `S_hat` on the whole evolve split. No minibatches, no early stopping, no per-sample accept/reject.
- **Per-sample uses**: ranking for trace selection (step 2); the scoreboard compares candidate vs incumbent task means
  (hit = predicted task improved; regression = drop `>= regression_threshold`, default `1/k`); the cost rule and floor see
  only the aggregate.
- **Splits**: the loop reads `evolve` only (evaluations, analyst traces, proposer context, smoke). `holdout`/`ood`/`test`
  are sealed from the loop's evaluator and must not reach any prompt. They are read only by (a) the write-only shadow
  monitor (after each round settles, separately metered, `shadow_monitor=True`), (b) optional `heldout_monitor`
  (logged only), (c) `rsi.transfer_report`/`paired_transfer` at the end.

## 5. Paper defaults vs this package

Paper values come from the released instances (`rrsi.json`) quoted in the spec [code]; the paper's own appendix is
unverified. `Config.preset("coding"|"workspace"|"eng"|"overview")` loads them.

| setting | paper value [tag] | `rsi.rrsi.Config` field = default | note |
|---|---|---|---|
| rounds | 20 / 20 / 40 (coding/workspace/eng); overview 10 | `T = 20` | |
| trials per task | 2 / 2 / 4 [code] | `k = 2` | |
| candidates per round | 2 [code] | `m = 2` | 1..8 |
| edit budget bounds | 1..4 / 1..3 / 1..4 [code]; [Eq. (anneal)] | `b_min = 1`, `b_max = 4`, `budget_rounding = "ceil"` | ceil never reaches `b_min` in a run; see 6 |
| stall window | 3 [code] | `w = 3` | |
| reserved slots | 1 [code] | `m_draft = 1` | |
| noise band | 0.017 / 0.004 / 0.020 fixed [code] | `delta = None` (calibrate), `delta_z = 2.0`, `calibration_repeats = 1`, `calibration_reps = 2000`, `calibration_seed = 7` | set `delta` or `bootstrap_small_k_correction=True` for k=2 |
| cost rule | beta0 0.10/0.10/0.15; beta1 44.5/35.4/24.4 [code] | `beta0 = 0.10`, `beta1 = 40.0` | relative `dC`; beta0=0.10 means 10% free |
| shaped in-band rule | w_s 0/1414/244; w_c 15/15/2; w_n 0.5 [code] | `w_s = 100.0`, `w_c = 15.0`, `w_n = 0.5` | defaults are the code's `RRSIConfig`; w_s scales with S units, presets matter |
| prune window | 4 / 4 / 5 [code] | `n_prune = 4` | |
| critic repairs | 5 [code] | `repair_rounds = 5` | |
| invalid evaluation | 0.2 / 0.1 / 0.15 [code] | `invalid_missing_frac = 0.15` | |
| analyst evidence | 30/6, 60/6, 22/6 fail/success traces [code] | `n_fail_traces = 22`, `n_success_traces = 6` | |
| liveness smoke | 2 tasks (coding, workspace), 4 (eng) [code] | `smoke_n = 2`, `smoke_require_score = False` | eng preset: 4 and True |
| domain guards | eng: valid-rate drop <= 0.03, no-payload rise <= 0.02 [code] | `run(..., guards=(MetricGuard("valid", 0.03),))` | none by default |
| proposer protocol | 40-turn JSON action agent [code] | `proposer_protocol = "rewrite"` (or `"json_actions"`) | |
| analyst | multi-turn digesters [code] | `analyst = "auto"` (`llm`/`heuristic`/`agentic`) | `auto` without `llm_analyst` = heuristic |
| models | Claude Opus 4.8 in all roles [doc] | `llm_propose`, `llm_critic`, `llm_analyst`, `llm_task` | any |
| hyperparameters frozen for transfer | yes [doc] | `frontier.json` stores the config | |

## 6. Where our implementation differs from the paper/code

Documented in `implementation.md` section 5 and `claims-audit.md`. Those that change how you use it:
- **No git**: candidates are content-addressed artifacts; "fast-forward" = update the frontier. Decisions unchanged.
- **Proposer/analyst are single-shot by default** (one rewrite call, one digester call per trace with the trace inline),
  not the 40-turn tool-using agents; opt in with `proposer_protocol="json_actions"`, `analyst="agentic"` (differential-tested
  against the reference on scripted replies; a live Haiku smoke of these failed, 43% non-JSON replies).
- **Precheck scope**: scans added lines (`precheck_scope="added"`; the code greps the whole diff), strict verdict parsing.
  Denylist = decision-split task ids + domain patterns + credential patterns + harness code that imports `rsi`/walks
  the call stack. Literal answers and entities are the LLM review's job, as in the code (`precheck_answers=True` is an
  extension that adds an answer key).
- **Taxonomy-less domains**: declared non-structural tags kept for code edits (`trust_code_tags`); otherwise the code
  would re-tag everything `prompt` and every reserved slot would fail.
- **Order**: directives are computed before `F_t` (they do not depend on it).
- **Resume fix**: novelty counts use accepted edits from rounds `< t` only.
- **Extensions, off by default**: `bootstrap_small_k_correction`, `budget_rounding="floor_at_bmin_last"`,
  `proposer_numbers` (show delta/S*/weights to the proposer; the code's proposer does not see them as directives, though
  the ledger's `detail` column quotes them from round 1), `tie_eps`, `heldout_monitor`, `component_aliases`.
- **Ablations**: `RegularizerSwitches` (`full`, `no_proposal`, `no_acceptance`, `none`, `budget_only`, `selector_only`).
  The unregularized baseline and the ablation groups are inferred, not defined in the paper.

Audit verdicts that matter (116 claims: 63 reproduced, 13 partial, 1 not reproduced, 36 not testable, 3 contradicted):
- Mechanism functions (Alg. 2, `normalize`, `render`, `edit_budget`, `stall_flag`, T/g/B, nu, `aggregate`, repeated-run
  delta) match the reference with 0 mismatches in randomized differential tests.
- **CONTRADICTED, budget** (M9): the paper snippet, the code's proposer prompt and its unit test say the budget "ends at
  one"; the released schedule over `t <= T-1` ends at **2** (coding 4x8, 3x5, 2x7). We follow the code. Use
  `budget_rounding="floor_at_bmin_last"` for a final single-edit round.
- **CONTRADICTED, delta coverage** (M37): the docstring's "unchanged harness clears the floor ~97.5%" holds at k=4
  (95.6%) but not at the default k=2 (about 91% with the reference's own estimator). The released instances fix `delta`.
- **CONTRADICTED, third-party claim** (C7): the annealed budget alone does *not* explain the OOD gain (budget-only
  +2.9 vs full +17.3 OOD pts on the synthetic world); the selector side carries most of it.
- **NOT REPRODUCED**: removing only the proposal regularizers lowering OOD (L4); it appears only with a
  prompt-collapsing mock proposer. Treat "annealing/exploration help transfer" as plausible, not demonstrated here.
- **PARTIAL**: cost rule gives a lighter harness but "equal or higher OOD" is not certified (L16: tokens -4.9x, OOD -1.7);
  the within-band rule keeps 6.8% of lucky nulls vs 10.4% for keep-if-better (L15), i.e. it reduces but does not
  remove luck; ablation groups (M34/M35) are our reading.
- **Not testable here**: all absolute paper numbers (Terminal-Bench, Harvey LAB, EngDesign, ...). Paper results are
  single runs without error bars [doc].
- Live evidence is one small run: Haiku critic caught an answer leak but passed practice-family regex routers
  (evolve 0.33 -> 1.00, holdout 0.42 -> 0.83, OOD 0.46 -> 0.46). The critic is not a guarantee.

## 7. Applying it with rsi

```python
import rsi
from rsi.rrsi import Config
cfg = Config(T=8, k=2, m=2, analyst="llm", bootstrap_small_k_correction=True)  # delta=None -> calibrated
# Config.preset("coding"|"workspace"|"eng") fixes the paper's delta; a None override is ignored, use .replace(delta=None)
res = rsi.improve(dom, seed, method="rrsi", llm_task=task_llm, llm_propose="claude:haiku",
                  config=cfg, out_dir="runs/x", cache_dir=".rsi_cache/x")        # config may also be a dict of Config fields
report = rsi.transfer_report(dom, task_llm, {"seed": res.baseline, "rrsi": res.best}, k=2)
```
Fields users usually set: `T`, `k`, `m`, `delta` (or `calibration_repeats >= 2` / `bootstrap_small_k_correction`),
`b_max`, `beta0`/`beta1`, `w_s` (scale to your score units), `repair_rounds`, `n_fail_traces`, `analyst`, `editable`
(globs the proposer may edit), plus `guards=`, `budget=Budget(max_usd=...)`, `critic_patterns=` on `run`/`improve`.
`res.best` is the final incumbent `H_T`. `res.trajectory` has one row per round (S, C, S*, b_t, sigma_t, winner, decisions).
Give the domain `components`/`structural_components` and sealed `holdout`/`ood`; without them the audit is weaker.

**Audit with `python -m rsi.cli inspect runs/x`** (`TRACE.md`, `audit.json`, `trace.jsonl`):
- `noise`: delta, method, warnings (delta = 0?). `round_start`: `b_t`, `sigma_t` arithmetic, `T_t`/`U_t`, reserved
  variants, `B_t`; recompute `b_t` by hand.
- `proposal`: declared edits vs actual diff (count <= `b_t`; one declared edit hiding several changes is a red flag);
  `critic`: precheck vs LLM, repairs; `note`: declared vs diff-derived tags, smoke, drops before evaluation.
- `gate`: S', C', S_t, S*, delta, floor, dS, dC, band branch, `beta0 + beta1*dS`, shaped score. Verify one by hand;
  check the winner is the max-S' admissible candidate and that `S_star` after = max(before, S').
- `monitor`: holdout/OOD of each new incumbent (never shown to the loop). Evolve rising while holdout/OOD stall =
  memorizing. Final answer = `transfer_report` with paired CI, plus tokens per task.
- `history.jsonl`/`attribution.jsonl`: low `hit_rate` or many `unpredicted_regressions` = edits are not doing what they claim.

**Method-specific failure modes**:
- `delta` too small or zero (k=1, near-deterministic or saturated grader): floor and band degenerate, lucky edits get
  kept. Too large: nothing clears the band; the run keeps `H_0` ("may keep nothing" is a valid outcome).
- Winner's curse: the incumbent is never re-measured, so `S_t` and the running-max `S*` are biased upward and later real
  gains must beat an inflated bar; the within-band rule can also walk `S_t` down (bounded by the floor `S* - delta`).
- Budget never reaches `b_min`; bundled credit is shared by every edit in a bundle, so early ledger entries are coarse.
- Pruning is advisory: `B_t` only lists components; removal happens only if the proposer ships a prune edit that also passes
  critic and Alg. 2. `B_t` can list components with nothing to remove.
- Novelty bonus can admit neutral structural additions; the cost term `w_c*dC` and pruning counteract it.
- Component tags are heuristics over the whole diff; mis-tags corrupt `T_t`, `U_t`, nu. Stalled rounds on a domain without
  a taxonomy can burn reserved slots.
- Critic only sees the diff: it can pass task-family routing (generic-looking but practice-specific code) and obfuscated
  leaks. Inspect kept diffs yourself.
- Domain guards bound each step, not cumulative drift (valid rate can still slide over many sub-tolerance steps).
- Cost: every surviving candidate is a full evolve evaluation (`m * |D| * k` trials/round) plus analyst, proposer, critic
  and shadow-monitor calls. Cap spend with `Budget(max_usd=...)` and cache the LLMs.
- Needs enough evolve tasks for a stable `S_hat` and a strong proposer; small splits make paired CIs too wide to rank.
