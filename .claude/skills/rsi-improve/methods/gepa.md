# GEPA: reflective prompt evolution with a Pareto pool of candidates

Method reference for `rsi.improve(..., method="gepa")`. Sources: `docs/methods/gepa/paper-spec.md` (the spec),
`implementation.md`, `claims-audit.md`, `rsi/gepa/`. Tags are the spec's: `[paper:<loc>]` = the ICLR 2026 camera-ready
(`§3`, `Alg.1`, `App.G.4`, ...), `[code:...]` = reference code `gepa-ai/gepa@d771eb21`, `[docs:...]`, `[doc]` =
secondary summaries, `[inferred]` = the spec author's reading. The paper has no numbered equations; the rules are in
Algorithms 1-4. Anything not in the spec is "not specified in the paper" below.

## 1. In one paragraph

Agrawal et al. (arXiv 2507.19457, ICLR 2026) adapt the **prompts** of an LLM system (one text per module; weights,
control flow, metric and data splits stay frozen) [paper:§3,p.4]. Scalar-reward RL (GRPO) needs 10^4 rollouts because
a run is squeezed into one number; GEPA instead lets a reflection LLM read the run's text (inputs, outputs, grader
feedback) and rewrite one module's instruction [paper:§1,§3]. Each iteration: pick a parent from a per-example
**Pareto frontier**, run it on a 3-example minibatch, reflect, rewrite one module, keep the child only if it beats the
parent on **those same 3 examples**, then score it on the whole validation set `D_pareto` and add it to the pool
[paper:Alg.1]. An optional **merge** crosses two lineages [paper:Alg.3-4, App.D.1]. What it optimises: expected
metric `mu` in [0,1] under a rollout budget `B`; the answer is the pool member with the best mean `D_pareto` score
[paper:§2, Alg.1 l.21]. Reported: +9.6 (Qwen3-8B) / +12.2 (GPT-4.1-mini) aggregate points over baselines, ahead of
MIPROv2 everywhere and of GRPO on 5 of 6 tasks with up to 35x fewer rollouts [paper:Tables 1-2, Obs.1].

## 2. Inputs and setup

Needed before round 1:
- **Artifact** `Phi_0`: files of an `Artifact`. Evolvable **components** = text files (`Config.components`; default
  `domain.component_paths()`, else text-like files, else non-`*.py`). Every other file is frozen. The component set is
  fixed at the seed [code:core/state.py]. Pass `.py` files explicitly only if you accept evolving code as text.
- **Splits** (names matter): `evolve` = `D_train` (`train_split`, falls back to `train`), the only split reflection
  ever reads; `val` = `D_pareto` (`val_split`), scored but never shown to the reflection LM [paper:§4,p.6];
  `holdout`/`ood`/`test` sealed. **If there is no non-empty `val` split, `D_pareto = D_train` with a warning**:
  this overfits (E6: gap +0.42 vs +0.03 at |train| = 6) [implementation E6]. Build a real `val` split.
- **Grader**: locked, in `Domain.grade`, score in `domain.score_range` (default 0..1) plus a **feedback string**. The
  feedback (`mu_f`) is GEPA's learning signal: it should say *why* ("expected X, got Y; rule Z violated"), as in the
  paper's per-benchmark feedback functions [paper:App.G.1]. Optional hook `domain.reflective_record(task, trial,
  component)` gives module-specific records [paper:§3, impl E9].
- **Models**: frozen task model `llm_task`; reflection LM `llm_propose` (the paper never names it for the main runs;
  likely the task model [paper:Table 4, inferred]; the code default is a frontier model). Optional `critic=` screen.
- **Budget**: `Config.max_metric_calls` (default 500) = rollouts, one per (candidate, example) evaluation. Paper:
  matched to MIPROv2's rollouts, 1,839-7,051 [paper:App.G.4]. Guidance 15-30 x |D_pareto| [docs:guides/faq.md].
- **Measured first** (`GEPAEngine.initialize`): the seed on all of `D_pareto`, one rollout per example
  (`val_seed`), `|V|` rollouts, no traces [paper:Alg.1 l.1-5]. No noise band is measured by the engine (the paper has
  none). Measure delta yourself first (SKILL.md step 3). If the seed raises on every example a `RuntimeWarning` fires.

## 3. Round by round

One iteration `i` = Algorithm 1 lines 7-19 plus the optional merge branch of the reference engine.

1. **Stop check** (only here, never mid-iteration, unless `budget_mode="hard"`): rollouts >= `max_metric_calls`,
   `gepa.stop` file, `max_iterations`, `timeout_s`, `stop_at_score`, `no_improvement_patience`, `Budget`, 3 consecutive
   infra-lost iterations. State is saved first.
2. **Merge branch** (only if `use_merge`, a merge is due, and the previous iteration's reflective child was
   accepted): try `MERGE` (step M). If it returns a child, test it and **end the iteration** (accepted or not). If no
   valid triplet exists, fall through to step 3 in the same iteration [code:core/engine.py].
3. **Select parent** `k`: Algorithm 2. Per-example frontier `P*[v]` = all candidates tied for best on example `v`;
   drop dominated candidates (set-cover rule, ascending mean); `P(c) = f[c] / sum f` with `f[c]` = number of
   examples `c` still wins [paper:Alg.2]. Alternatives (`candidate_selection`): `current_best`, `epsilon_greedy`,
   `top_k_pareto`, `beam_search`.
4. **Minibatch**: `b = 3` ids from `D_train`, epoch-shuffled (each example once per epoch, padded to a multiple of b)
   [code:strategies/batch_sampler.py].
5. **Run the parent** on the minibatch **with traces**; charge `b` rollouts. Skip the iteration (no reflection) if no
   trajectories, if any rollout failed for infra reasons, or if every score >= `perfect_score` (`skip_perfect_score`).
6. **Pick the module(s)**: round-robin over components, one pointer per candidate, inherited by children
   [paper:§3,p.5]. `module_selector="all"` updates every component in one call.
7. **Reflect**: build records `{Inputs, Generated Outputs, Feedback}` for the minibatch; fill the verbatim App.C
   meta-prompt (`<curr_param>` = current text, `<side_info>` = markdown records); **one LLM call per selected
   component**; parse the text between the first and last ``` fence; known-truncated replies are dropped. No
   usable text -> `no_proposal`, iteration ends. Exactly **one** child per iteration.
8. **Optional critic** (`critic=`, extension) screens the diff before any child rollout.
9. **Run the child** on the **same minibatch** (fresh rollouts, with traces); charge `b` more.
10. **Accept rule** (strict improvement): `sum_m mu(child(x_m)) > sum_m mu(parent(x_m))`. The paper writes only "if
    sigma' improved" [paper:Alg.1 l.13-14]; strictness is from the code. Ties reject. A rejected child is
    discarded: it is never scored on `D_pareto` and never enters the pool (the ledger keeps it as `x<i>`).
11. **If accepted** (`FULL_EVAL_AND_ADD`): score the child on **all of `D_pareto`** (`|V|` rollouts); append it to the
    pool; update the per-example frontier (`>` replaces, `==` joins); the child inherits the parent's advanced
    round-robin pointer. There is **no validation gate**: every minibatch-accepted child stays in the pool
    whatever its validation score. If `use_merge`, an accepted reflective child schedules one merge (`merges_due += 1`
    while `total_merges_tested < max_merge_invocations`).
12. **Merge step M** (when due): sample two non-dominated candidates `i, j` that are not ancestors of each other, with
    a common ancestor `a` where `S[a] <= min(S[i], S[j])` and some module was changed by only one side
    (`DESIRABLE`); build the child module-wise (take the side that changed it; both changed -> the higher mean);
    score it on a 5-id `D_pareto` subsample; accept iff `sum(child) >= max(sum S_i, sum S_j)` on those ids; if
    accepted, full `D_pareto` evaluation and add with parents `[i, j]`. Needs >= 5 shared scored ids, so it never
    fires when `|D_pareto| < 5`. The acceptance inequality is the code's; the paper gives none [paper:App.D.1].

```text
setup: P=[Phi0]; S[0][v]=mu(Phi0,v) for v in D_pareto; frontier from S; rr[0]=0; evals=|V|
while not stop(evals, ...):                                  # checked only at the top
    if use_merge and merges_due>0 and last_found_new:        # step 2
        prop = MERGE(P, frontier)                            # None -> fall through
        last_found_new=False
        if prop: (add if sum(sub_after) >= max(sub_i,sub_j)); continue
    k  = pareto_select(frontier, mean(S))                    # step 3
    M  = next_minibatch(D_train, b=3)                        # step 4
    E  = eval(P[k], M, traces=True); evals += b
    if all(E.scores >= perfect): continue                    # step 5 skip
    c  = round_robin(k); text = reflect_LLM(P[k][c], records(E, c))   # one call
    child = P[k] with c := text
    E2 = eval(child, M, traces=True); evals += b
    if sum(E2.scores) > sum(E.scores):                       # step 10
        S[new] = eval(child, D_pareto); evals += |V|; add to P, frontier   # step 11
        last_found_new = True; merges_due += 1 (soft-capped)
return argmax_c mean_v S[c][v]                               # ties: more coverage, lowest index
```

**What changes across rounds**: the pool and genealogy DAG grow; the per-example frontier shifts; each candidate's
round-robin pointer advances; the epoch sampler walks through `D_train`; `merges_due` accumulates. Nothing is
annealed: `b`, the selector, the gate and the prompt template are constant for the whole run (not specified as
varying in the paper). The reflection LM sees only the current minibatch's records, not the history of rejected
edits. Cost: a rejected iteration costs `2b`, an accepted one `2b + |V|`, a skipped one `b`; most of the budget goes to
validation [paper:Obs.1]. **Stopping rule**: budget only; the paper has no convergence test. The stop check runs
between iterations, so the last iteration overshoots `B` by up to `2b + |V|` (`budget_mode="reference_soft"`);
`"hard"` raises mid-iteration and discards the partial iteration. A killed run resumes from `out_dir` exactly.

## 4. Sample by sample

One "sample" is one example (a task) evaluated for one candidate; `mu(Phi(x), m)` in [0,1] is a **rollout**.
- **Trials**: exactly **one** rollout per (candidate, example) per evaluation. There is no `k`. Number of seeds:
  not specified in the paper. Here: minibatch rollouts of parent and child get independent seeds derived from
  `(Config.seed, "mb", iteration, role, j)`, so they are **not** seed-paired and a 3-example comparison is a
  comparison of single noisy draws. `D_pareto` rollouts of every candidate use the same `val_seed` (default 0).
- **Recorded**: score, grader feedback, output, execution trace (capped at 3000 chars in the reflection record),
  tokens/steps (stored in the trial; not used by any decision). Infra failures (`error` starting `infra:`) are
  retried twice, then counted; a minibatch with infra failures is skipped rather than compared.
- **Aggregation**: minibatch = **sum** of the `b` scores (strict `>` of sums = strict `>` of means). `D_pareto` =
  per-example scores kept as a vector `S[c][v]`; the aggregate is their mean, used for tie-breaks, domination
  order, merge ancestry and the final answer. The per-example vector, not the mean, drives parent selection.
- **Splits**: `D_train` (`evolve`) supplies the minibatch, the traces and the feedback text that reach the reflection
  prompt. `D_pareto` (`val`) supplies only scores (seed, accepted children, merge subsamples); its content never
  enters a prompt. `holdout`/`ood`/`test` are refused by the adapter (`PermissionError`); they are read only by the
  write-only shadow monitor (once per new incumbent, `shadow_monitor=True`) and by `rsi.transfer_report` /
  `gepa.report` at the end.

## 5. Paper defaults vs this package

| setting | paper value [tag] | rsi `Config` field = default | note |
|---|---|---|---|
| minibatch size b | 3 [paper:App.G.4] | `minibatch_size = 3` | |
| rollout budget B | matched to MIPROv2, 1,839-7,051 [paper:App.G.4] | `max_metric_calls = 500` | size ~15-30 x \|val\| [docs:faq]; `None` needs another stopper |
| D_feedback / D_pareto | train / val split [paper:App.G.4]; 150/300 etc. | `train_split="evolve"`, `val_split="val"` | missing val -> D_pareto = D_train |
| parent selection | Pareto, Alg.2; ablations SelectBest, Beam(N=4) [paper:Table 3] | `candidate_selection="pareto"`; `current_best`, `epsilon_greedy`(`epsilon=0.1`), `top_k_pareto`(`top_k=5`), `beam_search`(`beam_n=4`) | Beam is [inferred] (not in reference code) |
| frontier keys | per instance [paper:Alg.1] | `frontier_type="instance"` | `objective`/`hybrid`/`cartesian` are code extensions |
| module choice | round-robin [paper:§3,p.5] | `module_selector="round_robin"` (`all`) | |
| accept rule | "if sigma' improved" [paper:Alg.1], strictness unstated | `acceptance="strict_improvement"` (`improvement_or_equal`, `noise_margin` + `noise_margin=0.0`) | `noise_margin` is an RRSI-style extension |
| perfect-score skip | not stated | `skip_perfect_score=True`, `perfect_score=None` (= `score_range[1]`) | code default |
| merge | off (GEPA), on (GEPA+Merge), "max 5" [paper:App.G.4] | `use_merge=False`, `max_merge_invocations=5`, `merge_cap_mode="reference_soft"` | see 6 |
| merge subsample / overlap | not stated | `merge_subsample_size=5`, `merge_val_overlap_floor=5` | code values |
| reflection prompt | App.C template [paper:App.C] | `reflection_template=None` (default = verbatim App.C) | `"optimize_anything"` or custom text |
| reflection LM | not named [paper] | `llm_propose` (required) | |
| task decoding | Qwen T=0.6 / GPT-4.1-mini T=1.0, 16k ctx [paper:App.G.2] | the `llm_task` you pass | |
| seed | not stated | `seed=0`, `val_seed=0` | |
| result | best mean on D_pareto [paper:Alg.1 l.21] | `res.best` | ties: coverage, lowest index |
| outage guard / audit | none | `max_consecutive_infra_failures=3`, `trace=True`, `shadow_monitor=True` | extensions |

## 6. Where our implementation differs

From `implementation.md` section 5 and the claims audit (58 claims: 28 reproduced, 11 partial, 2 not reproduced,
17 not testable, 0 contradicted after the fix round and retry 2).
- **Reference semantics win over the paper's text** where they differ: set-cover domination (Alg.2 reads as single
  dominators), Pareto over `D_pareto` (paper prose says "training instance"), merge pairs drawn from survivors with up
  to 10x10 retries and ancestor chosen with weights `S[a]`, merge accepted on a `D_pareto` subsample with `>=`.
- **Merge cap is soft** (default `reference_soft`): `max_merge_invocations` only gates scheduling, so due merges
  pile up while no triplet exists and then all fire; accepted merges exceeded the cap in 73-100% of E4 runs.
  `merge_cap_mode="hard"` is the paper's "invoked a maximum of 5 times" (built and scored merges <= cap);
  `"accepted"` caps only accepted merges. `merge_zero_weight="uniform"` avoids the reference's crash when every
  ancestor has mean 0. `merge_start_frac` (extension) delays merges.
- **Candidates are `Artifact`s**; deterministic (sorted) iteration order; seed-derived iteration ids; exact resume
  (RNG, sampler, merge, selector state saved). Not implemented: parallel proposals, ComBEE, refiner, seedless mode,
  multimodal side-info, DSPy adapter. Extra: `critic=`, `noise_margin`, infra-skip and outage stopper, shadow monitor.

Audit verdicts that matter to a user:
- **REPRODUCED**: mechanisms match the reference (125 replayed steps, 0 mismatches; meta-prompt byte-identical);
  validation takes most of the budget (73% at b=3); text feedback that names the fix beats score-only (E2: 0.900 vs
  0.721), but this ablation is not in the paper and a mock reflection LM was used; few train examples overfit unless
  `D_pareto` is separate; a saturated seed is returned unchanged; module-specific records help (E9).
- **PARTIAL**: rollout efficiency vs scalar RL holds at equal small budgets (5.7-6.1x fewer rollouts), but 5.0-35.6x
  only in 11/20 seeds. Pareto beats the reference greedy `SelectBestCandidate` only because of tie-breaking on
  all-or-nothing rewards (vs a newest-tie greedy selector: no significant win in 8 cells); with partial rewards greedy
  is as good or better. Beating MIPRO-lite: tie with a live proposer. Merge helps only rationed (hard cap 5: +0.014
  [+0.001, +0.027] at B=4000). Merge "sparse": not for 2-module programs under the soft cap.
- **NOT REPRODUCED**: "GEPA outperforms RL at RL's 4x budget" (tie, +0.015 [-0.004, +0.035]; RL ahead where it
  gets a reward signal); "merge degradation is explained by budget allocation/timing" (neither rationing nor delaying
  removes it everywhere).
- **Not testable**: all absolute paper numbers (Tables 1-4, GPU/GRPO, real benchmarks).

## 7. Applying it with rsi

```python
import rsi
from rsi.gepa import Config
cfg = Config(max_metric_calls=600, minibatch_size=3)          # or a dict of Config fields
res = rsi.improve(dom, seed, method="gepa", llm_task="claude:haiku", llm_propose="claude:sonnet",
                  config=cfg, out_dir="runs/x", cache_dir=".rsi_cache/x")
report = rsi.transfer_report(dom, task_llm, {"seed": res.baseline, "gepa": res.best}, k=2)   # sealed splits
```
No domain class: `rsi.gepa.optimize(seed_dict, trainset, valset, metric=fn, llm_propose=..., testset=...)` with
`metric(candidate_dict, example) -> score | (score, feedback[, output])`.
Fields users usually set: `max_metric_calls`, `components`, `train_split`/`val_split`, `minibatch_size`, `use_merge`
(+ `merge_cap_mode="hard"`), `module_selector` (`"all"` for many components), `candidate_selection`,
`acceptance="noise_margin"` + `noise_margin=delta`, `feedback`, `workers`, `max_reflection_cost`, `stop_at_score`.
`improve(..., critic=rsi.core.LeakageCritic(...), budget=Budget(max_usd=...))` adds a pre-screen and a dollar cap.
`res.best` = best mean `D_pareto` candidate; `res.meta` has `rollouts_by_phase`, `candidates`, `frontier`,
`n_accepted`, `n_merges_accepted`, `n_exec_errors`, `n_infra_errors`, `n_reflection_failed`, `seed_val_error_rate`.

**Audit with `python -m rsi.cli inspect runs/x`** (`TRACE.md`, `trace.jsonl`, `run_log.jsonl`, `ledger.jsonl`):
- `run_start`/`noise`: delta is `None` unless `noise_margin` is set; GEPA does not measure noise for you.
- `round_start`: rollouts used/remaining, Pareto sampling weights, incumbent, round-robin `next_component`, merge
  counters (`merges_due`, `n_invocations`). Recompute the weights from the frontier once.
- `analysis`: the exact records the reflection LM saw (only `evolve` ids); `proposal`: prompt, reply, actual diff;
  check the rewrite is generic and not copying minibatch facts.
- `gate` (minibatch acceptance): `before`, `after`, sums; verify `sum(after) > sum(before)` by hand. `decision`:
  `accepted` / `rejected` / `skip_perfect` / `no_proposal` / `skip_infra_error` / `critic_rejected` / merge events.
- `state`/`run_end`: `rollouts_by_phase` (validation should dominate) and `events` counts per outcome.
- `monitor`: holdout/ood score of each new incumbent. Evolve/val rising while holdout/ood stall = memorizing.

**Method-specific failure modes**:
- **Noisy 3-example gate**: single draws, strict `>`. Many false accepts and false rejects (E5 ~48% false accepts at
  b=3). Every accepted child enters the pool, so lucky or overfitted ones persist and can be selected as parents.
- **Winner's curse on the answer**: `res.best` is the argmax of many single-draw `D_pareto` means. Report it on a
  sealed split only; with small `val` the argmax is noise.
- **Missing `val` split** silently degrades to `D_pareto = D_train` (a warning): overfits.
- **Minibatch overfitting by the reflection LM**: the meta-prompt asks to include "niche and domain specific factual
  information", so rewrites copy ticket facts or literal answers; a live Haiku run wrote "You are solving modular
  arithmetic problems..." and scored below the seed on validation. Use `critic=` and read the diffs.
- **Weak feedback**: with only "expected X, got Y", text feedback gave no edge over score-only (AgentQA analogue). A seed
  already perfect on `D_train` is returned untouched.
- **All-or-nothing rewards**: children tie their parent at validation 0; selection quality then depends on
  tie-breaking, not on frontier diversity.
- **Merge**: soft cap lets merges multiply (5 + |V| rollouts each when accepted); degrades some multi-module runs; no
  merge with `|D_pareto| < 5`. Use `merge_cap_mode="hard"` or leave `use_merge=False` (the paper's main row).
- **Budget overshoot / outage**: the last iteration can exceed `B` by `2b + |V|`; always pass `cache_dir` and a USD cap.
- **Components fixed at the seed**: GEPA never adds or removes files and never edits control flow; a harness whose
  gains need code or tool changes belongs to rrsi/metaharness.
