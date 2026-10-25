# GEPA: reflective prompt evolution with a Pareto pool of candidates

Method reference for `rsi.improve(..., method="gepa")`. Primary source: the paper, Agrawal et al., "GEPA: Reflective
Prompt Evolution Can Outperform Reinforcement Learning", arXiv 2507.19457v2 (14 Feb 2026; ICLR 2026 per the repo
spec). Citations `[paper §x]`, `[paper Alg. n l.k]`, `[paper Eq. n]`, `[paper Table n]`, `[paper App. X]` use the
**arXiv v2 numbering** (Alg. 1 has 23 lines; the experimental setup is App. E). The repo spec
`docs/methods/gepa/paper-spec.md` cites the OpenReview camera-ready, whose appendix letters differ (its `App.G.4` =
arXiv App. E.4). `[code:...]` = reference code `gepa-ai/gepa@d771eb21`; "this package" = `rsi/gepa/` (also
`implementation.md`, `claims-audit.md`). "rsi choice" = a decision of this package only; "not specified in the
paper" = the paper is silent. The paper's objective is Eq. 1-2; its procedure is Algorithms 1-4.

## 1. In one paragraph

GEPA optimises the **prompts** `Pi` of a compound AI system `Phi = (M, C, X, Y)`; model weights stay frozen and only
the prompt set is evolved [paper §2, §3]. The goal is Eq. 2: maximise expected metric `mu` in [0,1] subject to at
most `B` rollouts, a rollout being one invocation of `Phi` plus evaluation by `mu` [paper §2, Eq. 1-2]. Scalar-reward
RL such as GRPO typically needs tens of thousands of rollouts [paper §1]; GEPA instead has a reflection LM read
execution traces and textual evaluator feedback (`mu_f`) and rewrite one module's instruction [paper §3]. Each
iteration: sample a parent from the per-instance **Pareto frontier** over `D_pareto` [paper Alg. 2], pick a module
round-robin, run the parent on a minibatch of `b` examples from `D_feedback`, reflect, rewrite that module, re-run the
child on the same minibatch, and keep it only if its **average minibatch score improves**; a kept child is scored on
all of `D_pareto` and added to the pool [paper Alg. 1 l.9-20]. An optional system-aware **merge** combines modules
evolved in different lineages [paper App. D.1, Alg. 3-4]. The answer is the candidate with the best average on
`D_pareto` [paper Alg. 1 l.23]. Reported: aggregate +9.62 (Qwen3-8B) and +12.19 (GPT-4.1 mini) points over the
unoptimised baseline, +13.33 for GEPA+Merge on GPT-4.1 mini; ahead of MIPROv2 in every setting; ahead of GRPO
(24,000 rollouts) on 5 of 6 tasks with up to 35x fewer rollouts, +6% on average [paper Tables 1-2, Obs. 1-2, abstract].

## 2. Inputs and setup

What the paper needs [paper Alg. 1 l.1-3, §3]: system `Phi` with seed prompts, dataset `D_train`, metric `mu`,
feedback function `mu_f`, budget `B`; hyperparameters `b` (minibatch size) and `n_pareto` (= |D_pareto|).
- **Artifact** `Phi_0`: here the files of an `Artifact`. Evolvable **components** = text files (`Config.components`;
  default `domain.component_paths()`, else text-like files, else non-`*.py`); every other file is frozen; the
  component set is fixed at the seed (rsi choice mirroring the paper's fixed module set `M` [paper §2]).
- **Splits**: Alg. 1 l.3 splits `D_train` into disjoint `D_feedback` (minibatches, traces, feedback) and
  `D_pareto` (scores only, for selection). In the experiments the benchmark's train split is `D_feedback` and its
  validation split is `D_pareto`; the test split is held out, and optimisers may track validation scores but not
  read validation contents [paper §4, App. E.4]. Here: `evolve` = `D_feedback` (`train_split`, falls back to
  `train`), `val` = `D_pareto` (`val_split`), `holdout`/`ood`/`test` sealed. **If there is no non-empty `val` split,
  `D_pareto = D_train` with a warning** (rsi choice): the paper does this only on purpose, for inference-time search
  [paper §5.1]; for generalisation it overfits (E6: gap +0.42 vs +0.03 at |train| = 6). Build a real `val` split.
- **Grader**: `mu` plus `mu_f`, which returns a score and `feedback_text` (compiler errors, failed rubrics, which
  documents are still missing...), possibly module-specific, possibly human-written explanations [paper §3; the
  per-benchmark feedback functions are in App. E.1]. Here: `Domain.grade` -> score in `domain.score_range` plus a
  feedback string that should say *why*; optional `domain.reflective_record(task, trial, component)` gives
  module-specific records (E9).
- **Models**: frozen task model; a reflection LM. All modules of a system use the same model [paper App. E.2]; the
  reflection LM used in the main runs is **not specified in the paper** (Table 4 reports reflection-call counts per
  task model, which suggests, but does not state, that it is the task model). Here `llm_task` and a required
  `llm_propose`; optional `critic=` (rsi choice).
- **Budget**: `B` rollouts [paper §2]. Experiments: GEPA capped at MIPROv2's per-benchmark rollout count (MIPROv2
  used 2,270-6,926), usage within 10.15% [paper App. E.4]; GEPA totals 1,839-7,051 [paper Table 1]. Here
  `Config.max_metric_calls` (default 500, rsi choice). A size rule such as 15-30 x |D_pareto| is
  not specified in the paper (it comes from the reference docs).
- **Measured first**: the seed on every `D_pareto` example, one rollout each, `|D_pareto|` rollouts [paper Alg. 1
  l.4-7]. The paper measures no noise band; measure delta yourself (SKILL.md step 3).

## 3. Round by round (the paper)

One iteration = Alg. 1 l.8-21, with merge as the second proposal strategy [paper Fig. 3, App. D.1].

1. **Stop check**: `while budget B not exhausted` [paper Alg. 1 l.8]; checked at the top of the loop, so the last
   iteration may overrun `B` (the paper's budgets differ from the target by up to 10.15% [paper App. E.4]).
2. **Select candidate** `k` = SelectCandidate(P, S) [paper Alg. 1 l.9, Alg. 2]: for each `D_pareto` instance `i`,
   `s*[i] = max_k S_k[i]` and `P*[i]` = every candidate attaining it (ties all join) [Alg. 2 l.3-6]; `C` = union of
   the `P*[i]`; repeatedly remove a candidate "dominated by another" remaining one [Alg. 2 l.8-11] (the removal
   order and the exact dominance test are not specified in the paper); `f[Phi]` = number of instances whose pruned
   set contains `Phi`; sample with probability proportional to `f` [Alg. 2 l.12-14]. Ablations: SelectBestCandidate
   and BeamSearch(N=4) [paper §4 Obs. 3, Table 3].
3. **Select module** `j` = SelectModule(Phi_k), a round-robin policy [paper Alg. 1 l.10, §3]. Whether the pointer is
   global or per candidate is not specified in the paper.
4. **Minibatch** `M` of size `b` from `D_feedback`, "stochastically sampled" [paper Alg. 1 l.11, §3]; `b = 3` in all
   runs [paper App. E.4]. The sampling scheme (with/without replacement, epochs) is not specified in the paper.
5. **Run the parent** on `M`, gathering scores, `mu_f` feedback and traces for module `j` [paper Alg. 1 l.12, §3].
6. **Reflect**: `pi'_j = UpdatePrompt(pi_j, feedbacks, traces[j])` [paper Alg. 1 l.13]: the reflection LM gets the
   App. C meta-prompt filled with the current instruction and the minibatch's inputs, outputs and feedback, and
   returns the new instruction inside ``` blocks [paper App. C]. One module, one new candidate per iteration
   [paper §3, Fig. 3].
7. **Mutate**: `Phi'` = copy of `Phi_k` with module `j` replaced [paper Alg. 1 l.14].
8. **Minibatch acceptance**: `sigma, sigma'` = average score on `M` before/after; **keep iff `sigma'` improved**
   [paper Alg. 1 l.15-16]; "if the score improves" [paper §3]. Read as strict `sigma' > sigma`; the paper does not
   say how ties are treated or whether the parent's minibatch is re-run. A rejected child is discarded.
9. **If kept**: append `Phi'` to `P`, record parent `k` in `A` [l.17], score `Phi'` on every `D_pareto` instance
   [l.18-20]. There is no validation gate: every minibatch-accepted child stays in the pool and can become a parent.
10. **Merge (GEPA+Merge only)** [paper App. D.1, Alg. 3-4]: "GEPA routinely checks if the pool has 2 such
    candidates, invoking merge when identified". Conditions: `i, j` not ancestors of each other [Alg. 4 l.3-6];
    a common ancestor `a` not tried before for `(i, j)` with `S[a] <= min(S[i], S[j])` [l.7-13]; `DESIRABLE`: some
    module where one descendant equals the ancestor and the other differs [Alg. 3]. Child = copy of `P[a]`; per
    module take the side that changed it; both changed -> the higher-scoring side, ties random; else `pi_i`
    [Alg. 4 l.17-31]. Alg. 4 l.2 draws `i, j` from all of `P`, while the prose says both are Pareto-optimal. Alg. 4
    has no acceptance test, but Fig. 3 and §3 (iii) say any new candidate is first evaluated on a minibatch and
    added "if it outperforms its parent(s)", then scored on `D_pareto`; the exact inequality, the minibatch source
    and the schedule are not specified in the paper. Merge is invoked at most 5 times per run [paper App. E.4].
11. **Return** `Phi*` maximising the average score on `D_pareto` [paper Alg. 1 l.23]; tie-breaking not specified.

```text
P=[Phi]; A=[None]; S[0][v]=mu(Phi(x_v),m_v) for v in D_pareto            # Alg.1 l.4-7: |D_pareto| rollouts
while budget B not exhausted:                                           # l.8, checked at the top only
    k = SelectCandidate(P, S)                                           # l.9, Alg.2
    j = SelectModule(P[k])                                              # l.10, round-robin
    M = minibatch(D_feedback, b=3)                                      # l.11
    E = run P[k] on M with mu_f (scores, feedback, traces)              # l.12: b rollouts
    pi_j' = reflect(pi_j, E)  ;  child = P[k] with module j := pi_j'    # l.13-14, App. C prompt
    sigma, sigma' = mean(E.scores), mean(run child on M)                # l.15: b rollouts
    if sigma' improved:                                                 # l.16
        P += [child]; A += [k]; S[new][v] = mu(child, v) for v in D_pareto   # l.17-20: |D_pareto| rollouts
    (GEPA+Merge: when Alg.3-4 find a valid triplet, propose a merged child instead, same minibatch-then-D_pareto test)
return argmax_c mean_v S[c][v]                                          # l.23
```

**What changes across rounds**: the pool and the ancestry `A` grow; the per-instance frontier shifts; the
round-robin position advances. Nothing is annealed: `b`, the selector, the acceptance test and the meta-prompt
are fixed (no schedule in the paper). The reflection LM sees only the current minibatch, not rejected edits; the
lineage carries lessons forward because children are derived from parents [paper §3]. Cost: rejected iteration
`2b`, accepted `2b + |D_pareto|`; "the majority of GEPA's rollout budget is spent on validation" [paper Obs. 1].
**Stopping rule**: budget only; the paper has no convergence test.

## 4. Sample by sample (the paper)

One sample = one `(x, m)` instance run through one candidate and scored by `mu` / `mu_f`: one rollout [paper §2].
- **Trials**: one rollout per (candidate, instance) per evaluation, for `D_pareto` [paper Alg. 1 l.6, l.19] and for
  the minibatch [l.12, l.15]; no repeats. Seeds/repeats: not specified in the paper. Decoding is stochastic (Qwen3-8B
  T=0.6, top-p 0.95, top-k 20; GPT-4.1 mini T=1.0; 16,384-token context) [paper App. E.2], so the minibatch
  comparison is between single noisy draws; only the kernel-search runs remove sampling noise with a cache
  [paper §5.1].
- **Recorded**: per minibatch example, the module's inputs, outputs and reasoning from the execution trace, plus
  the `mu_f` score and feedback text, which can include evaluation traces (compiler errors, profiling) and
  module-specific feedback [paper §3]. Only scores are kept for `D_pareto` evaluations [paper Alg. 1 l.19].
- **Aggregation**: minibatch -> average `sigma` over the `b` scores [paper Alg. 1 l.15]. `D_pareto` -> a
  per-instance score vector `S[c][i]` [l.19]; the per-instance maxima form the frontier that drives parent selection
  [Alg. 2]; the mean of `S[c]` picks the returned candidate [Alg. 1 l.23].
- **Splits**: `D_feedback` supplies the minibatches and the text the reflection LM sees; `D_pareto` (the
  benchmark's validation split) only supplies scores; the test split is used only for the final report
  [paper §4, App. E.4]. Split sizes (train/val/test): HotpotQA and HoVer 150/300/300, IFBench 150/300/294, PUPA
  111/111/221, AIME 45/45 (AIME 2022-24) with AIME-2025 x5 as test, LiveBench-Math 368 split in thirds
  [paper App. E.1].

## 5. Paper defaults vs this package

| setting | paper value [cite] | rsi `Config` field = default | note |
|---|---|---|---|
| minibatch size b | 3 [paper App. E.4] | `minibatch_size = 3` | |
| rollout budget B | MIPROv2-matched; GEPA 1,839-7,051 [paper Table 1, App. E.4] | `max_metric_calls = 500` | rsi choice; `None` needs another stopper |
| D_feedback / D_pareto | train / val split [paper App. E.4]; disjoint split of D_train [Alg. 1 l.3] | `train_split="evolve"`, `val_split="val"` | missing val -> D_pareto = D_train (rsi choice) |
| parent selection | Pareto [paper Alg. 2]; ablations SelectBest, BeamSearch(N=4) [paper Table 3] | `candidate_selection="pareto"`; `current_best`, `epsilon_greedy`(`epsilon=0.1`), `top_k_pareto`(`top_k=5`), `beam_search`(`beam_n=4`) | Beam's exact rule not specified in the paper |
| frontier keys | per D_pareto instance [paper Alg. 2 l.3-6] | `frontier_type="instance"` | `objective`/`hybrid`/`cartesian` are extensions |
| module choice | round-robin [paper §3, Alg. 1 l.10] | `module_selector="round_robin"` (`all`) | per-candidate pointer: reference/rsi choice |
| accept rule | avg on M, "if sigma' improved" [paper Alg. 1 l.15-16] | `acceptance="strict_improvement"`: `sum(after) > sum(before)` (`improvement_or_equal`; `noise_margin` + `noise_margin=0.0`) | sum `>` = mean `>` at fixed b; ties not specified in the paper |
| perfect-score skip | not specified in the paper | `skip_perfect_score=True`, `perfect_score=None` (= `score_range[1]`) | reference-code default |
| merge | off (GEPA), on (GEPA+Merge), "invoked a maximum of 5 times" [paper App. E.4] | `use_merge=False`, `max_merge_invocations=5`, `merge_cap_mode="reference_soft"` | default cap is soft, see 6 |
| merge test / subsample | minibatch, "if it outperforms its parent(s)" [paper §3, Fig. 3]; size not specified | `merge_subsample_size=5` (D_pareto ids, `>=` best parent), `merge_val_overlap_floor=5` | reference-code values |
| reflection prompt | App. C meta-prompt [paper App. C] | `reflection_template=None` (= App. C text) | `"optimize_anything"` or custom text |
| reflection LM | not specified in the paper | `llm_propose` (required) | |
| task decoding | Qwen T=0.6 / GPT-4.1 mini T=1.0, 16k ctx [paper App. E.2] | the `llm_task` you pass | |
| seeds | not specified in the paper | `seed=0`, `val_seed=0` | |
| result | best mean on D_pareto [paper Alg. 1 l.23] | `res.best` | ties: coverage, then lowest index (rsi choice) |
| outage guard / audit | none | `max_consecutive_infra_failures=3`, `trace=True`, `shadow_monitor=True` | extensions |

## 6. Where our implementation differs

The engine follows the reference code `gepa-ai/gepa@d771eb21` wherever code and paper text disagree. Claims audit:
58 claims, 28 reproduced, 11 partial, 2 not reproduced, 17 not testable, 0 contradicted (final verdicts).
- **Iteration order** (reference): parent is selected, the minibatch is drawn and the parent is run *before* the
  module is chosen (paper: module at Alg. 1 l.10, before the minibatch). Skipped iterations (below) do not advance
  the round-robin pointer. Pointer is per candidate; a child inherits the parent's advanced pointer.
- **Extra skips** (not in the paper): no reflection when every parent minibatch score is perfect
  (`skip_perfect_score`; such a child could not improve anyway), when a rollout failed with an `infra:` error
  (retried twice first), or when the reflection reply has no usable fenced text (`no_proposal`; known-truncated
  replies dropped). Optional `critic=` screens the diff before child rollouts. 3 consecutive infra-lost iterations
  stop the run.
- **Minibatch sampling** (reference): epoch-shuffled, each `D_feedback` id once per epoch, padded to a multiple of
  `b` with the least-used ids. Parent and child rollouts get independent seeds from `(seed, "mb", i, role, j)`, so
  they are not seed-paired; every `D_pareto` rollout uses `val_seed`. Reflection records cap traces at 3,000 chars.
- **Pareto pruning** (reference): set-cover domination (a candidate is dropped when every instance it wins is also
  won by some other remaining candidate), removing the lowest mean first; Alg. 2 l.9 reads as pairwise dominance.
- **Merge** (reference): pair drawn from Pareto survivors (prose, not Alg. 4 l.2), up to 10x10 retries, ancestor
  drawn with weights `S[a]`, extra duplicate check on (i, j, module sources), overlap floor of 5 scored ids (so no
  merge when |D_pareto| < 5); accepted iff `sum(child) >= max(sum S_i, sum S_j)` on a 5-id **D_pareto** subsample
  (paper: a minibatch, "outperforms its parent(s)"); attempted only in the iteration right after an accepted
  reflective child while `merges_due > 0` (paper: "routinely checks"). If no triplet exists the iteration falls
  through to reflective mutation.
- **Merge cap is soft by default** (`reference_soft`): the cap gates only scheduling and counts accepted merges, so
  invocations are unbounded and accepted merges exceeded 5 in 73-100% of E4 runs. `merge_cap_mode="hard"` is the
  paper's "invoked a maximum of 5 times"; `"accepted"` caps accepted merges only. `merge_zero_weight="uniform"`
  avoids the reference crash when every ancestor has mean 0; `merge_start_frac` (extension) delays merges.
- **Budget**: `budget_mode="reference_soft"` overshoots by up to `2b + |D_pareto|`; `"hard"` stops mid-iteration.
- **Also**: candidates are `Artifact`s; sorted iteration order; exact resume; extra selectors, frontiers and
  stoppers; `noise_margin`, shadow monitor. Not implemented: parallel proposals, ComBEE, refiner, seedless mode,
  multimodal side-info, DSPy adapter.

Audit verdicts that matter to a user (CPU analogues with a mock reflection LM; no paper number is testable here):
- **REPRODUCED**: mechanisms match the reference (125 replayed steps, 0 mismatches; meta-prompt byte-identical to
  the reference, which differs from App. C only in placeholder names and blank lines); validation takes most of the
  budget (73% at b=3); feedback that names the fix beats score-only (E2: 0.900 vs 0.721; an ablation the paper did
  not run); few train examples overfit unless `D_pareto` is separate; a saturated seed is returned unchanged;
  module-specific records help (E9).
- **PARTIAL**: rollout efficiency vs scalar RL holds at equal small budgets (5.7-6.1x fewer rollouts), but 5.0-35.6x
  only in 11/20 seeds. Pareto beats greedy `SelectBestCandidate` only via tie-breaking on all-or-nothing rewards, and
  loses to it in the paper-budget analogue (-0.050 / -0.066). Beating MIPRO-lite: a tie with a live proposer. Merge
  helps only when rationed (hard cap 5: +0.014 [+0.001, +0.027] at B=4000); not sparse for 2-module programs.
- **NOT REPRODUCED**: "outperforms GRPO at GRPO's ~4x budget" (tie, +0.015 [-0.004, +0.035]; RL ahead where it gets
  a reward signal); "merge degradation is explained by budget allocation/timing" (neither lever removes it).
- **Not testable**: all absolute paper numbers (Tables 1-4, GRPO on GPUs, the real benchmarks).

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
(+ `merge_cap_mode="hard"` for the paper's cap), `module_selector` (`"all"` for many components),
`candidate_selection`, `acceptance="noise_margin"` + `noise_margin=delta`, `feedback`, `workers`,
`max_reflection_cost`, `stop_at_score`. `improve(..., critic=rsi.core.LeakageCritic(...),
budget=Budget(max_usd=...))` adds a pre-screen and a dollar cap. `res.best` = best mean `D_pareto` candidate;
`res.meta` has `rollouts_by_phase`, `candidates`, `frontier`, `n_accepted`, `n_merges_accepted`, `n_exec_errors`,
`n_infra_errors`, `n_reflection_failed`, `seed_val_error_rate`.

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
- **Noisy 3-example gate**: single stochastic draws, strict `>`, no noise band (none in the paper either). Many false
  accepts and rejects (E5: ~48% false accepts at b=3). Every accepted child stays in the pool and can be a parent.
- **Winner's curse on the answer**: `res.best` is the argmax of many single-draw `D_pareto` means. Report it on a
  sealed split only; with a small `val` the argmax is noise.
- **Missing `val` split** silently degrades to `D_pareto = D_train` (a warning): overfits.
- **Minibatch overfitting by the reflection LM**: the App. C meta-prompt asks it to include "all niche and domain
  specific factual information", so rewrites copy ticket facts or literal answers; a live Haiku run wrote "You are
  solving modular arithmetic problems..." and scored below the seed on validation. Use `critic=` and read the diffs.
- **Weak feedback**: with only "expected X, got Y", text feedback gave no edge over score-only (AgentQA analogue). A
  seed already perfect on `D_train` is returned untouched.
- **All-or-nothing rewards**: children tie their parent at validation 0; selection then depends on tie-breaking,
  not on frontier diversity.
- **Merge**: the soft cap lets merges multiply (5 + |D_pareto| rollouts each when accepted); it degrades some
  multi-module runs (the paper saw degradation on Qwen3-8B [paper Obs. 5]); no merge with |D_pareto| < 5. Use
  `merge_cap_mode="hard"` or leave `use_merge=False` (the paper's main GEPA row).
- **Budget overshoot / outage**: the last iteration can exceed `B` by `2b + |D_pareto|`; pass `cache_dir` and a USD cap.
- **Components fixed at the seed**: GEPA never adds or removes files and never edits control flow; a harness whose
  gains need code or tool changes belongs to rrsi/metaharness.
