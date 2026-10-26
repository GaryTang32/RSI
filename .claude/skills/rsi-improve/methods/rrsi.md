# RRSI: regularized recursive self-improvement of an agent harness

Method reference for `rsi.improve(..., method="rrsi")`. Primary source: the paper, Xia et al., "RRSI: Regularized
Recursive Self-Improvement of Agent Harnesses", arXiv 2609.24972 (v3, 4 Oct 2026; v1 is checked where it differs).
Tags: `[paper §n]` (sections and appendices, e.g. §D.3), `[paper Alg. n]`, `[paper Eq. n]`, `[paper Table n]`.
`[code]` = the released reference code (`google-research/rrsi@be50316`) as quoted in `docs/methods/rrsi/paper-spec.md`;
`[doc]` = the project blog/overview. "rsi choice" = only this package's decision; "not specified in the paper" = the
paper is silent. Package facts come from `rsi/rrsi/` and `docs/methods/rrsi/{implementation,claims-audit}.md`.

## 1. In one paragraph

Xia et al. (Google Cloud AI Research, with Stanford, WashU and UNC) evolve the **harness** `H` (prompts, control flow,
config, output plumbing, context management, tools, skills, memory, sub-agents) around a **frozen** policy [paper §2,
§3.1, Eq. 12]. Plain harness evolution reuses one finite evolve set both to suggest edits and to judge them, so it overfits:
large evolve gains that shrink or vanish out of distribution, and a costlier harness [paper §1, Table 1, Table 2].
RRSI keeps the edit space open and regularizes how the search moves [paper §3.1]. Proposal side [paper §3.2]: an
annealed cap `b_t` on independently attributable edits per candidate (`L0`-style), an edit history with measured score
and cost changes (rejected mechanisms stay negative evidence), and reserved slots for never-exercised components when
progress stalls. Selection side [paper §3.3]: a leakage critic on the diff before any evaluation, a noise-adjusted
floor `S' >= S* - delta`, a token-cost rule (Ridge/`L2`-style), and pruning targets handed back to the proposer
(Lasso/`L1`-style). The objective is still the measured evolve score `S_hat` (mean verifier reward over `k` trials per
task), maximized only over candidates that pass these non-compensatory checks [paper Eq. 8]. Reported effect: up to
+6.0 evolve and +4.3 OOD points, on 30% fewer policy tokens than unregularized evolution [paper abstract, v3].

## 2. Inputs and setup

Needed before round 1:
- **Artifact `H_0`**: the editable harness. Frozen: the policy model [paper §2, §4.1]; the grader, task set and
  interface contract are frozen in the code's proposer prompt [code]. Here: `Artifact` (content-addressed, no git).
- **Splits**: `evolve` (`D_evolve`, the only split the loop reads [paper §2]). The paper evolves on one suite and runs
  the final harness unchanged on an in-distribution held-out split (Harvey LAB 120/40) and OOD benchmarks; EngDesign
  has no ID held-out split; no hyperparameter consults held-out/OOD data [paper §4.1, §B.3, §B.7, §E.1]. Here:
  strongly recommended sealed `holdout` (same kind) and `ood` (different kind).
- **Grader**: a verifier outside the harness with reward `r(x, tau) in [0,1]` per trial (unit tests, a simulator, or an
  LLM judge in the paper) and policy tokens `c(tau)` per trial as the cost [paper §2 Eq. 1, §B]. Feedback text per
  trial is rsi's input to the analyst/proposer.
- **Models**: frozen policy (Claude Opus 4.8; Gemini 3.5 Flash in a robustness run); proposer, analyst and critic all
  Claude Opus 4.8 [paper §4.1, §4.3 Table 3]. Here: `llm_task`, `llm_propose`; `llm_critic` defaults to `llm_propose`.
  `analyst="auto"` uses an LLM analyst only if `llm_analyst` is passed, else a deterministic heuristic clusterer over
  grader feedback (rsi choice); `analyst="llm"` uses `llm_analyst` (else `llm_propose`) for the code's three-lens analyst and digesters.
- **Domain extras** (optional): component vocabulary `K` (paper: 9 components, Eq. 12) and `K_str` = {client_tool,
  skill, memory, subagent} [paper Eq. 15] (`components`/`structural_components`), `critic_patterns`, non-compensatory
  `guards` [paper Alg. 2 l.13], `regression_threshold(k)`, a constitution (SKILL.md/PATTERNS.md [code]; generic here).
- **Budgets**: `T` rounds, `m` candidates/round, `k` trials/task [paper Table 5, §E.1]; `repair_rounds` [code];
  optional `Budget(max_usd, max_rounds, max_rollouts, max_wall_s)` (rsi choice).

Measured first [paper §D.1, §3.3]:
1. Evaluate `H_0` on all of `D_evolve` with `k` trials -> `S_0`, `C_0`; start with `S* = S_hat(H_0)` [paper §D.1].
   Here (job `base`): abort if missing trials exceed `invalid_missing_frac * n_expected` after one retry [code].
2. Here: `frontier` incumbent = `H_0`, `S* = S_0`, `trajectory = [S_0]`; history gets a `BASELINE` row.
3. **Noise band `delta`**: the paper evaluates the unchanged base harness repeatedly before evolution and takes `delta`
   as "how far apart two evaluations of the same harness land": 3 passes (coding), 60 criterion verdicts (workspace);
   5 passes on EngDesign, read off the first percentile of a null built by half-splitting the base evaluation's own
   trials. Results 0.017 / 0.004 / 0.020; never tuned [paper §3.3, §D.1, §E.1, Table 5]. Here: `Config.delta` if set (presets carry
   the paper's values), else `delta = delta_z * sd_null` (rsi/code estimator): one base evaluation -> within-task
   bootstrap of trials (2000 reps, seed 7), `sd_null = sqrt(2) * se`; `calibration_repeats = R >= 2` -> `sd_null` from
   the R base scores. `delta == 0` (k=1, deterministic grader) writes a warning: set `delta` yourself.

## 3. Round by round (what the paper does)

A run applies Alg. 1 (proposal) then Alg. 2 (selection) for `t = 0..T-1` [paper §D.1].

1. **Incumbent** `H_t` with its stored `S_hat_t`, `C_hat_t`; `S*` = best evolve score so far [paper Alg. 2 l.1, §3.3].
   Re-measuring the incumbent is not specified in the paper (rsi: never re-run).
2. **Analyze** `F_t <- Analyze(H_t, D_evolve)`: failure feedback of the incumbent, written by an analyst LLM [paper
   Alg. 1 l.3, §4.1]. Which traces and how many: not specified in the paper.
3. **Directives** [paper Alg. 1 l.4-9]:
   - `b_t = ceil(b_min + (b_max - b_min) * 0.5 * (1 + cos(pi t / T)))` [paper Eq. 4];
   - `T_t` = components with at least one measured edit; `U_t = K \ T_t` [paper Eq. 11, Eq. 13];
   - stall `sigma_t = 1[S_hat_t - S_hat_{t-w} <= delta]` [paper Eq. 13] (value for `t < w`: not specified; rsi: 0);
   - `E_t = (sigma_t, U_t, m_draft)` [paper Eq. 13];
   - `g_t(l) = max{dS_i : l_i = l, t - t_i <= n_prune}`, `max {} = -inf` [paper Eq. 11]; `B_t = {l in T_t :
     g_t(l) <= 0}` [paper Eq. 14], so a tried component with no recent measured edit is also a target.
4. **Draft** `H_t ~ P_reg(. | H_t, F_t, L_t, b_t, E_t, B_t)`, all from the same `H_t` [paper Eq. 8, Alg. 1 l.10];
   `m = 2` candidates [paper §E.1]. The proposer drafts a pool `E_t` of atomic edits (about 80) and a candidate
   applies a subset `z_t` with `||z_t||_0 <= b_t` [paper §D.2 Eq. 9, §E.1]. Each atomic edit is tagged with its
   component and hypothesis [paper Alg. 1 l.11]. It conditions on the history (rejected mechanisms = negative evidence,
   accepted ones keep credit) [paper §3.2], and receives `B_t` with the accepted edits of those components and an
   instruction to remove unproductive machinery [paper §D.2]. Stall: if `sigma_t = 1`, `m_draft` slots are reserved
   for edits on components in `U_t` [paper §3.2, Eq. 13]. The exact prompt contents are not specified in the paper.
5. **Screen** (no evaluation yet): the critic reads the **diff, not the proposal** [paper Alg. 1 l.12] and rejects
   task names, entity names, task-specific values, answers or other evolve-specific logic, and inert machinery
   [paper §3.3]; in the case study it also rejected a candidate whose declared component was not the one the diff
   changed [paper §F]. Repair attempts and a liveness smoke are not specified in the paper.
6. **Evaluate** every screened candidate on the **full** evolve set, `k` trials per task, in parallel -> `S'`, `C'`
   [paper Alg. 2 l.4-5].
7. **Accept** [paper Alg. 2, §D.3]: `dS = S' - S_hat_t`, `dC = (C' - C_hat_t)/C_hat_t` [paper Eq. 6]; `nu` = number
   of distinct `K_str` types the candidate touches with no previously accepted edit record [paper Eq. 16].
   Admissible iff **all** of (a conjunction; order irrelevant):
   - floor `S' >= S* - delta` [paper Eq. 5];
   - if `dS > delta`: `dC <= beta0 + beta1 * dS` [paper Eq. 7]; else (inside the band, including `dS < 0`):
     `w_s*dS - w_c*dC + w_n*nu > 0` [paper Eq. 17] (coding sets `w_s = 0`, so in-band score gains earn nothing);
   - every domain guard holds [paper Alg. 2 l.13].
   `H_{t+1} = argmax S'` over admissible candidates, **not** the shaped score; none -> `H_{t+1} = H_t` [paper Eq. 8,
   Alg. 2 l.18]. Ties: not specified in the paper (rsi: first variant).
   Worked example (coding, `delta = 0.017`, `beta = (0.10, 44.5)`): +7 passes of 178 (`dS = 0.039`) may grow tokens
   by `0.10 + 44.5*0.039 = 185%` and its 130% is admitted; a +1.69-point, +26.1%-cost candidate is in-band and is
   rejected; a -2.81-point, -13.6%-cost candidate fails the floor [paper §E.1, Table 8].
8. **Update** `S* <- max(S*, S_hat_{t+1})` [paper Alg. 2 l.19]; record each measured edit `(t, l, h, d, dS, dC, a)`
   with `a = 1` iff its candidate became `H_{t+1}`; admissible losers get `a = 0`; all edits of a bundle share its
   `dS`, `dC`; candidates that fail before a valid measurement are left out of `L_t` [paper Alg. 2 l.20, §D.2 Eq. 10].

```text
setup: ev0 = Evaluate(H0, D, k); S* = ev0.S; delta = measured on repeated base evals  # rsi: cfg.delta or z*sd_null
for t in 0..T-1:                                  # fixed horizon [paper §D.1]; rsi adds STOP file / Budget / infra stop
    F = Analyze(H_t, D)                           # rsi computes the directives first (independent of F)
    b_t = ceil(b_min + (b_max-b_min) * (1+cos(pi*t/T))/2)
    sigma = [S_t - S_{t-w} <= delta]; U = K - tried; B = {l in tried : g_t(l) <= 0}
    cands = P_reg(H_t, F, L_t, b_t, (sigma, U, m_draft), B)   # m candidates, <= b_t edits each, reserved slots on U
    cands = [c for c in cands if critic(c.diff) == accept]    # rsi: + repair loop + liveness smoke
    for c in cands: c.S, c.C = Evaluate(c, D, k)              # full evolve split
    adm = [c for c in cands if c.S >= S* - delta and cost_ok(c) and guards_ok(c)]
    H_{t+1} = argmax_{adm} c.S or H_t; S* = max(S*, S_{t+1}); record edits with a = [c is winner]
return H_T                                        # the final incumbent, not the best-scoring artifact
```

**What changes across rounds**: `b_t` shrinks (cosine, from `b_max` at `t = 0`), the history grows, `T_t` grows (so
`U_t` shrinks), `B_t` lists components whose recent edits stopped paying, `S*` ratchets up (and the floor with it),
the incumbent moves only on acceptance. `delta`, `beta*`, `w_*` are fixed before the run and never re-tuned, including
for transfer [paper §3.3, §D.3, §E.1]. **Stopping rule**: fixed horizon `T`, no convergence test [paper §D.1]. Here
also `Budget`, a `STOP` file in the run dir, or `infra_failures` (3 consecutive failed rounds); a killed run resumes.

## 4. Sample by sample

One "sample" is one task `x` in `D_evolve` inside one evaluation.
- **Trials**: `k` independent rollouts per task (k = 2 coding/workspace, 4 eng) [paper Eq. 3, Table 5]. Seeds: not
  specified in the paper; it evaluates a harness and its baseline "in the same window, with the same tool environment,
  the same judge and the same number of trials" [paper §B]. Here each job (`base`, `r<t><V>`) gets its own seed block
  from `(Config.seed, job name)`, so candidate and incumbent are **not** seed-paired; `delta` absorbs the noise.
- **Recorded**: per trial `r` and `c(tau)` [paper Eq. 1]; per evaluated edit `(component, hypothesis, diff, dS, dC, a)`
  [paper §3.2, Eq. 10]. Here also steps, error, grader feedback and a trace capped at `trace_chars=3000` per trial,
  and per task the mean reward and missing count.
- **Missing trial**: for the loop's `S_hat` not specified in the paper; its benchmark reports count a missing rollout
  or deliverable as a failure over the full denominator [paper §B.3, §B.6]. Here (`error` starting `infra:`): reward 0,
  full denominator, excluded from the token mean; applies to candidates and the baseline alike.
- **Aggregation**: `S_hat` = plain mean of `r` over all `k|D|` trials, `C_hat` = mean of `c` over all `k|D|` trials
  [paper Eq. 3]. Here (rsi choice): `S_hat = sum(r*w)/sum(w)` with weights 1 unless a task sets `meta["weight"]`
  (e.g. rubric size, as Harvey LAB's criterion-level score [paper §B.3]); `C_hat` averages only trials with a positive
  token count. A candidate's round score is `S_hat` on the whole evolve split: no minibatches, no early stopping, no
  per-sample accept/reject [paper Alg. 2 l.5].
- **Per-sample uses** (rsi/code, not in the paper): ranking tasks for analyst trace selection; the attribution
  scoreboard (hit = a `predicted_affected` task improved; regression = drop `>= regression_threshold`, default `1/k`).
  The floor and the cost rule see only the aggregates [paper Alg. 2].
- **Splits**: the loop reads `evolve` only; held-out/OOD never feed back [paper §1, §4.1, §E.1]. Here `holdout`/`ood`/
  `test` are sealed from the loop's evaluator and prompts; they are read only by (a) the write-only shadow monitor
  (`shadow_monitor=True`, separately metered), (b) optional `heldout_monitor` (logged only), (c) `rsi.transfer_report`
  / `paired_transfer` at the end. The paper's final CIs are a paired task-level bootstrap (10,000 resamples, tasks not
  trials) [paper §E.2, Table 6].

## 5. Paper defaults vs this package

Paper values are Table 5 unless tagged otherwise. `Config.preset("coding"|"workspace"|"eng")` loads them (plus
the code's values for what the paper leaves out); `"overview"` is the blog's illustration [doc].

| setting | paper value (coding / workspace / eng) | `rsi.rrsi.Config` field = default | note |
|---|---|---|---|
| rounds | 20 / 20 / 40 [paper Table 5]; overview 10 [doc] | `T = 20` | |
| trials per task | 2 / 2 / 4 [paper Table 5] | `k = 2` | |
| candidates per round | 2 [paper §E.1] | `m = 2` | 1..8 |
| edit budget | `b_min` 1/1/1, `b_max` 4/3/4 [paper Table 5, Eq. 4] | `b_min = 1`, `b_max = 4`, `budget_rounding = "ceil"` | ceil never reaches `b_min` for `t <= T-1`; see 6 |
| stall window | 3 [paper Table 5] | `w = 3` | |
| reserved slots | `m_draft` 1 [paper Table 5] | `m_draft = 1` | |
| noise band | 0.017 / 0.004 / 0.020, measured on repeated base evals [paper Table 5, §E.1] | `delta = None` (calibrate), `delta_z = 2.0`, `calibration_repeats = 1`, `calibration_reps = 2000`, `calibration_seed = 7` | `bootstrap_small_k_correction=True` by default (since 26 Oct 2026): the code's plug-in bootstrap is (k-1)/k low; `False` restores it; better, set `delta` or `calibration_repeats >= 2` |
| cost rule | `beta0` 0.10/0.10/0.15; `beta1` 44.5/35.4/24.4 [paper Table 5] (25%/25%/10% tokens per pass or per 100 criteria [paper §E.1]) | `beta0 = 0.10`, `beta1 = 40.0` | relative `dC`; `beta0 = 0.10` = 10% growth free; `beta1 = 40` is the code's `RRSIConfig` default |
| in-band weights | coding `w_s = 0`, others `w_s > 0`; values not given (§D.3 says Table 5 lists them; it does not) [paper §D.3] | `w_s = 100.0`, `w_c = 15.0`, `w_n = 0.5` | code: `w_s` 0/1414/244, `w_c` 15/15/2, `w_n` 0.5 [code]; `w_s` scales with S units |
| prune window | 4 / 4 / 5 [paper Table 5] | `n_prune = 4` | |
| critic repairs | not specified in the paper; 5 [code] | `repair_rounds = 5` | |
| invalid evaluation | not specified in the paper; 0.2 / 0.1 / 0.15 [code] | `invalid_missing_frac = 0.15` | |
| analyst evidence | not specified in the paper; 30/6, 60/6, 22/6 fail/success traces [code] | `n_fail_traces = 22`, `n_success_traces = 6` | |
| liveness smoke | not specified in the paper; 2 / 2 / 4 tasks [code] | `smoke_n = 2`, `smoke_require_score = False` | eng preset: 4 and True |
| domain guards | coding/workspace none; eng: valid-output rate drop <= 0.03, no-submission rise <= 0.02 [paper v1 §C.3; v3 §D.3 keeps only "any domain guard the instance defines"] | `run(..., guards=(MetricGuard("valid", 0.03),))` | none by default |
| proposer protocol | not specified in the paper; 40-turn JSON action agent [code] | `proposer_protocol = "rewrite"` (or `"json_actions"`) | |
| analyst | an LLM writing cross-round failure feedback [paper §4.1]; multi-turn digesters [code] | `analyst = "auto"` (`llm`/`heuristic`/`agentic`) | `auto` without `llm_analyst` = heuristic |
| models | Claude Opus 4.8 in every role [paper §4.1] | `llm_propose`, `llm_critic`, `llm_analyst`, `llm_task` | any |
| hyperparameters frozen | yes, before the run, no sweep [paper §E.1] | `frontier.json` stores the config | |

## 6. Where our implementation differs from the paper

From `implementation.md` §5, `claims-audit.md` and a check against the paper text. Those that change how you use it:
- **Noise band**: the paper measures `delta` from repeated base evaluations (eng: 1st percentile of a half-split null)
  [paper §E.1]; ours defaults to `2 * sd_null` from a bootstrap of one base evaluation (rsi/code). Presets use the
  paper's fixed values; `calibration_repeats >= 2` is closest to the paper.
- **Edit pool**: the paper drafts a pool of ~80 atomic edits and each candidate applies at most `b_t` of them [paper
  §D.2, §E.1]; ours asks each variant directly for at most `b_t` declared edits (no shared pool).
- **No git**: candidates are content-addressed artifacts; "fast-forward" = update the frontier. Decisions unchanged.
- **Proposer/analyst are single-shot by default** (one rewrite call, one digester call per trace with the trace inline),
  not the code's 40-turn tool-using agents; opt in with `proposer_protocol="json_actions"`, `analyst="agentic"`
  (differential-tested against the reference on scripted replies; a live Haiku smoke failed, 43% non-JSON replies).
  The analyst also reads success traces (three lenses [code]); the paper only names failure feedback [paper Alg. 1].
- **Screening extras** (code, not paper): six critic REJECT rules (task-specific content, degenerate edits, grader
  gaming, undeclared bundling, runtime memory leakage, unbounded work), up to `repair_rounds` repairs, then a liveness
  smoke (`smoke_n` tasks, 1 trial). Precheck scans added lines (`precheck_scope="added"`; the code greps the whole
  diff); denylist = evolve task ids + domain patterns + credentials + harness code that imports `rsi` or walks the call
  stack; `precheck_answers=True` adds an answer key (extension).
- **Taxonomy-less domains**: declared non-structural tags kept for code edits (`trust_code_tags`); otherwise every edit
  would be re-tagged `prompt` and every reserved slot would fail.
- **Order**: directives are computed before `F_t` (paper: after [paper Alg. 1]); they do not depend on it.
- **Estimator details** (rsi choice): task weights in `S_hat`, `C_hat` over trials with tokens, `dC = 0` when a token
  count is missing, `sigma_t = 0` for `t < w`, ties to the first variant, incumbent never re-measured.
- **Defaults**: `beta1 = 40`, `w_s = 100` sit between the instances (code `RRSIConfig`); use a preset or scale them.
- **Resume fix**: novelty counts use accepted edits from rounds `< t` only (matches `N_t` [paper Eq. 16]).
- **Small-k correction, on by default**: `bootstrap_small_k_correction=True` scales the bootstrap sd by sqrt(k/(k-1)).
- **Extensions, off by default**: `budget_rounding="floor_at_bmin_last"`,
  `proposer_numbers`, `tie_eps`, `heldout_monitor`, `component_aliases`.
- **Ablations**: `RegularizerSwitches` (`full`, `no_proposal`, `no_acceptance`, `none`, `budget_only`, `selector_only`).
  The paper names the arms (unregularized, w/o proposal, w/o acceptance [paper Table 2]) and defines unregularized
  evolution only abstractly (unconstrained `P_0`, keep the argmax of candidates and incumbent [paper Eq. 2]); it puts
  the critic and pruning on the selection side [paper abstract, §3.3] but does not list each arm's contents. Our `none`
  keeps a `b_max` cap.

Audit verdicts that matter (116 claims: 63 reproduced, 13 partial, 1 not reproduced, 36 not testable, 3 contradicted):
- Mechanism functions (Alg. 2, `normalize`, `render`, `edit_budget`, `stall_flag`, T/g/B, nu, `aggregate`, repeated-run
  delta) match the reference code with 0 mismatches in randomized differential tests.
- **CONTRADICTED, budget** (M9), now confirmed in the paper text: Eq. 4 with `t = 0..T-1` [paper Eq. 4, §D.1] ends at
  **2** (coding 4x8, 3x5, 2x7), while Table 5 calls `b_min` the "final-round edit budget" and §E.1 says "the closing
  rounds carry a single edit". The paper is internally inconsistent; we follow Eq. 4. Use
  `budget_rounding="floor_at_bmin_last"` for a final single-edit round.
- **CONTRADICTED, delta coverage** (M37): the code docstring's "unchanged harness clears the floor ~97.5%" holds at k=4
  (95.6%) but not at k=2 (about 91% with the reference's own estimator). Not a paper claim: the paper's `delta` is
  measured differently and fixed [paper §E.1].
- **CONTRADICTED, third-party claim** (C7): the annealed budget alone does *not* explain the OOD gain (budget-only
  +2.9 vs full +17.3 OOD pts on the synthetic world); the selector side carries most of it.
- **NOT REPRODUCED**: removing only the proposal regularizers lowering OOD (L4; paper: 43.6 -> 41.9 [paper Table 2]);
  it appears only with a prompt-collapsing mock proposer. Treat "annealing/exploration help transfer" as plausible.
- **PARTIAL**: cost rule gives a lighter harness but "equal or higher OOD" is not certified (L16: tokens -4.9x, OOD -1.7);
  the within-band rule keeps 6.8% of lucky nulls vs 10.4% for keep-if-better (L15): it reduces, not removes, luck.
- **Not testable here**: all absolute paper numbers (Terminal-Bench 2.1, Harvey LAB, EngDesign, ...). v3 reports
  task-level paired bootstrap CIs and an independent second run of every search (OOD avg 43.6 vs 43.7) [paper §E.2,
  Tables 6-7]; v1 had neither, so the audit's "no error bars" caveat (C1) is outdated for v3.
- Live evidence is one small run: Haiku critic caught an answer leak but passed practice-family regex routers
  (evolve 0.33 -> 1.00, holdout 0.42 -> 0.83, OOD 0.46 -> 0.46). The critic is not a guarantee.

## 7. Applying it with rsi

```python
import rsi
from rsi.rrsi import Config
cfg = Config(T=8, k=2, m=2, analyst="llm", calibration_repeats=3)   # delta=None -> calibrated from 3 base evals
# Config.preset("coding"|"workspace"|"eng") fixes the paper's delta; a None override is ignored, use .replace(delta=None)
res = rsi.improve(dom, seed, method="rrsi", llm_task=task_llm, llm_propose="claude:haiku",
                  config=cfg, out_dir="runs/x", cache_dir=".rsi_cache/x")        # config may also be a dict of Config fields
report = rsi.transfer_report(dom, task_llm, {"seed": res.baseline, "rrsi": res.best}, k=2)
```
Fields users usually set: `T`, `k`, `m`, `delta` (or `calibration_repeats >= 2`),
`b_max`, `beta0`/`beta1`, `w_s` (scale to your score units), `repair_rounds`, `n_fail_traces`, `analyst`, `editable`
(globs the proposer may edit), plus `guards=`, `budget=Budget(max_usd=...)`, `critic_patterns=` on `run`/`improve`.
`res.best` is the final incumbent `H_T`. `res.trajectory` has one row per round (S, C, S*, b_t, sigma_t, winner, decisions).
Give the domain `components`/`structural_components` and sealed `holdout`/`ood`; without them the audit is weaker.

**Audit with `python -m rsi.cli inspect runs/x`** (renders `trace.jsonl` to `TRACE.md`; `rsi.rrsi.audit.audit_run` writes `audit.json`):
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
- Budget never reaches `b_min` under ceil; bundled credit is shared by every edit in a bundle, so early history is coarse.
- Pruning is advisory: `B_t` only lists components; removal happens only if the proposer ships a prune edit that also passes
  critic and Alg. 2. `B_t` can list components with nothing to remove.
- Novelty bonus can admit neutral structural additions; the cost term `w_c*dC` and pruning counteract it.
- Component tags are heuristics over the whole diff; mis-tags corrupt `T_t`, `U_t`, nu. Stalled rounds on a domain without
  a taxonomy can burn reserved slots.
- Critic only sees the diff: it can pass task-family routing (generic-looking but practice-specific code) and obfuscated
  leaks; the paper's own case study shows baselines fitting the scorer without naming tasks [paper §F, Table 9].
- Domain guards bound each step, not cumulative drift (valid rate can still slide over many sub-tolerance steps).
- Cost: every surviving candidate is a full evolve evaluation (`m * |D| * k` trials/round) plus analyst, proposer, critic
  and shadow-monitor calls. Cap spend with `Budget(max_usd=...)` and cache the LLMs.
- Needs enough evolve tasks for a stable `S_hat` and a strong proposer; small splits make paired CIs too wide to rank.
