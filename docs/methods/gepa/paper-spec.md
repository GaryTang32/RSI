# GEPA — Reflective Prompt Evolution Can Outperform Reinforcement Learning

Lakshya A Agrawal, Shangyin Tan, Dilara Soylu, Noah Ziems, Rishi Khare, Krista Opsahl-Ong, Arnav Singhvi, Herumb Shandilya, Michael J Ryan, Meng Jiang, Christopher Potts, Koushik Sen, Alexandros G. Dimakis, Ion Stoica, Dan Klein, Matei Zaharia, Omar Khattab (UC Berkeley, Stanford, BespokeLabs.ai, Notre Dame, Databricks, MIT) [paper:p.1]. arXiv 2507.19457 [code:gepa/CITATION.cff][code:gepa/README.md]. First posted July 2025, revised February 2026, ICLR 2026 oral [doc]. The camera-ready page 1 says only "Published as a conference paper at ICLR 2026"; the oral status comes from the overview alone. The name stands for **Ge**netic-**Pa**reto [paper:abstract]. The overview adds that "the backronym 'Genetic-Evolutionary Prompt Adaptation' that circulates online is wrong" [doc].

## 0. Sources and how to read the tags

| Tag | Meaning |
|---|---|
| `[paper:<loc>]` | **The ICLR 2026 camera-ready paper** ("Published as a conference paper at ICLR 2026"). I got it as text extracted from the OpenReview PDF (forum id `RQm2KQTM5r`), which is vendored in the GitHub repo `HawkFranklin-Research/train-llm-papers` (`papers/extracted_text/iclr2026/paper_pdf/RQm2KQTM5r__…txt`) and fetched with raw.githubusercontent. Saved as `scratchpad/papers/gepa.txt`. Locations give the camera-ready section and page, e.g. `[paper:§3.1,p.5]`, `[paper:Table 1,p.6]`, `[paper:App.D.1,p.19]`. |
| `[paper-src:<file>]` | **The first author's own LaTeX source of the same paper**: repo `LakshyAAAgrawal/temp_poster_repo` at `dd52127` (23 Apr 2026), `overleaf/*.tex`, flattened with comments stripped into `scratchpad/papers/gepa-iclr2026-latex.txt`. Every table and hyperparameter matches the camera-ready text. *(Fact-check correction: the wording is not the only difference.)* The camera-ready says "pp" where the source says "%". It gives the Obs. 2 Qwen margin as "12pp" (AIME) where the source says "10.3%" (PUPA, 91.85 − 81.55). The source's Obs. 5 sentence "providing an aggregate 2% additional improvement" is **absent from the camera-ready**, which says only "can outperform GEPA by as much as 5%" [paper:App.H]. This source is used only to quote algorithms, the prompt and figure metadata without PDF-extraction artefacts; every number is cited from the camera-ready. |
| `[oa-paper:<loc>]` | "`optany`: Unified Text Optimization can Outperform Specialized Systems". This is an anonymous ACM CAIS '26 submission about the `optimize_anything` API, found in the same author repo (`optimizeanything_overleaf/main.tex`) and flattened into `scratchpad/papers/gepa-optimize-anything-latex.txt`. It is a submission draft, not a peer-reviewed paper. |
| `[code:gepa/<path>:<symbol>]` | `gepa-ai/gepa` at `d771eb21b5dd` (22 Sep 2026, package version `0.1.4`), cloned to `scratchpad/src/gepa-ai__gepa`. I read `src/gepa/{api.py, optimize_anything.py, gepa_launcher.py, gepa_utils.py}`, `core/{engine,state,adapter,data_loader,result}.py`, `proposer/{base,merge}.py`, `proposer/reflective_mutation/{base,reflective_mutation,reflection_lm}.py`, all of `strategies/`, `utils/stop_condition.py`, the default, optimize_anything and dspy adapters, `oa/config.py`, and `CITATION.cff`. |
| `[skill:<file>]` | The repo's Claude skill `.claude/skills/gepa-optimize-anything/` (`SKILL.md`, `references/gotchas.md`, `references/writing_evaluators.md`). |
| `[docs:<file>]` | The documentation site source in the repo (`docs/docs/guides/*.md`, `docs/docs/blog/posts/*/index.md`). The live site gepa-ai.github.io is blocked, so I read the Markdown it is built from. |
| `[doc]` | The user's overview page. It is a secondary summary. |
| `[run:<id>]` | My own probe runs of the repo code (see §4.7). The fact-check pass added `[run:merge-cap-probe]` and `[run:overshoot-probe]`; their scripts are in `scratchpad/gepa_probe/`. |
| `[unverified]` | A claim I could not check against a primary source. |
| `[inferred]` | My own reconstruction, arithmetic or design proposal. |

**Access report.** arxiv.org, export.arxiv.org, openreview.net (from both curl and WebFetch), semanticscholar, alphaxiv, huggingface, gepa-ai.github.io and decagon.ai were all blocked by the egress proxy. The camera-ready paper and its LaTeX source were reachable through GitHub mirrors, so **the primary paper was accessed in full**, including the appendices and Tables 1–4. I could not read the individual figures: rollout-vs-score curves, search trees and generalization-gap plots are only available as captions and as numbers extracted from the PDF. I also could not access independent critiques, beyond WebSearch snippets.

**Fact-check pass (adversarial review).** Every formula, default, schema field, prompt quote and table number was re-checked against the camera-ready text, the LaTeX source and `gepa-ai/gepa@d771eb21` (engine, state, merge, reflective_mutation, reflection_lm, strategies/*, gepa_utils, api, gepa_launcher, optimize_anything, oa/{config,budget,engines/gepa}, adapters/{optimize_anything,dspy,default}, combee, stop_condition, callbacks), plus probe runs.
- Corrections, each marked *(Fact-check correction)* in the text:
  - the merge cap is soft;
  - Obs. 5's "aggregate 2%" is absent from the camera-ready;
  - the DSPy parse-failure text sits in a different field;
  - Fig. 4's model;
  - epoch-coverage and resume/determinism claims;
  - overshoot applies only to the non-eval-server entry points.
- Additions:
  - a merge crash edge case;
  - the "no text-feedback ablation" caveat;
  - the hard-capped OA eval server;
  - ComBEE, `OptimizationState` and extra stoppers;
  - merge being impossible when |D_pareto| < 5.
- Tables 1–4, the meta-prompt, the Pareto pruning rule, the acceptance inequalities, the batch sampler, the round-robin inheritance, the merge crossover rules and the budget identity were all confirmed as written.

---

## 1. The idea in one paragraph (101)

Many AI products are "compound systems": several LLM calls, each with its own prompt, wired together with retrieval or tools. The usual way to adapt such a system to a new task with reinforcement learning (for example GRPO) squeezes each run into one number, the reward, and needs thousands to tens of thousands of runs [paper:§1,p.1–2]. GEPA's bet is that a run is mostly text: the prompts, the model's reasoning, the tool calls, and even the grader's own messages (for example a compiler error) before they are reduced to a score. A strong LLM can read that text and work out *why* the run failed. Each GEPA iteration does the following:
1. Pick one of the current prompt sets.
2. Run it on a tiny minibatch (3 examples).
3. Show a "reflection" LLM the inputs, outputs and textual feedback.
4. Have the reflection LLM rewrite the instruction of one module.
5. Keep the rewrite only if it scores better on those same 3 examples. It is then scored on a validation set and added to a growing tree of candidates.

Instead of always improving the single best candidate, which tends to stall in a local optimum, GEPA keeps every candidate that is best on at least one validation example (a per-example Pareto frontier). It samples parents from that frontier in proportion to how many examples they win. An optional "merge" step combines modules that different lineages improved separately. Only prompts change; model weights stay frozen [paper:§3,p.4–6][doc].

---

## 2. What is improved, what is frozen, who grades

**Improved: the system's prompts Π_Φ.** A candidate is a map from component name to text, `dict[str, str]`, such as one instruction per module [code:gepa/src/gepa/api.py:optimize docstring]. The paper formalizes the system as Φ = (M, C, 𝒳, 𝒴), with modules M_i = (π_i, θ_i, 𝒳_i, 𝒴_i) and control flow C. It states: "GEPA evolves only the set of prompts, denoted as Π_Φ, whereas the underlying LLM weights, denoted by Θ_Φ remains fixed" [paper:§2–3,p.2–4]. The paper's experiments optimize instructions only. They keep no few-shot demonstrations, and they compare against MIPROv2, which also optimizes demonstrations [paper:Obs.2,p.7].
- The code generalizes "prompt" to *any* text component: "prompts in a AI system, code snippets/code files/functions/classes in a codebase, etc." [code:gepa/src/gepa/api.py:optimize docstring].
- `optimize_anything` goes further and treats code, agent architectures, configs, SVGs and scheduling policies as the evolvable text [code:gepa/src/gepa/gepa_launcher.py:module docstring][oa-paper:§2].
- The paper's own extensions evolve CUDA/NPU kernel prompts and do inference-time code search [paper:§5,App.E].

**Frozen:**
- The task LLM weights Θ [paper:§3,p.4].
- The system's control flow C and the module structure. The component names are fixed at the seed: `list_of_named_predictors = list(seed_candidate.keys())` [code:gepa/src/gepa/core/state.py:GEPAState.__init__].
- The metric μ and the feedback function μ_f.
- The data splits.
- The reflection meta-prompt, which is a fixed template.
- The hyperparameters: minibatch b, Pareto-set size n_pareto, budget B [paper:Alg.1,p.18].
- The overview's caveat: "It changes prompts only, not tools, memory or control flow" [doc]. This is true of the paper's prompt-optimization setting. The later `optimize_anything` API relaxes it whenever that code is expressed as the evolvable text [code:gepa/src/gepa/gepa_launcher.py:module docstring][oa-paper:§4.3 ARC-AGI].

**Who grades:**
- *Score:* a user-supplied metric μ: 𝒴 × ℳ → [0,1], such as exact match, F1 or pass rate [paper:§2,p.3]. In code this is `adapter.evaluate(...) → EvaluationBatch.scores`, where higher is better [code:gepa/src/gepa/core/adapter.py:GEPAAdapter.evaluate].
- *Feedback:* a feedback function μ_f that returns the score plus `feedback_text`, which can be module-specific. It is built from execution traces and evaluation traces such as compiler errors and failed rubric items [paper:§3,p.5]. In code this is `adapter.make_reflective_dataset(...)` [code:gepa/src/gepa/core/adapter.py:GEPAAdapter.make_reflective_dataset], or the evaluator's `side_info` ("Actionable Side Information", ASI) in `optimize_anything` [code:gepa/src/gepa/gepa_launcher.py:SideInfo].
- *Keep or discard:* deterministic rules in the engine.
  - A child's minibatch score sum must strictly exceed the parent's [code:gepa/src/gepa/strategies/acceptance.py:StrictImprovementAcceptance].
  - Acceptance into the pool follows automatically; there is no validation gate.
  - The final answer is the candidate with the best mean on D_pareto (the validation set) [paper:Alg.1 l.21][code:gepa/src/gepa/strategies/eval_policy.py:FullEvaluationPolicy.get_best_program].
- *Held-out check:* a test split never seen by the optimizer. "Although optimizers may monitor the performance of candidate parameters … by tracking scores on the validation set …, direct access to the content of validation instances is restricted" [paper:§4,p.6]. In `optimize_anything`, `test_set` is scored outside the budget and never enters the eval server [code:gepa/src/gepa/optimize_anything.py:_score_test].
- *Who proposes:* the reflection LM. The paper never names the reflection LM for its main experiments [inferred: absence]. Two pieces of indirect evidence suggest that each setting reflected with its own task model. First, Table 4 counts reflection calls in separate GPT-4.1-mini and Qwen3-8B columns [paper:Table 4,p.87]. Second, Table 2's caption and Obs. 6 say "GEPA-Qwen-Opt" was "optimized entirely for (and using) the weaker Qwen3-8B", while the baselines "optimized directly for (and using) GPT-4.1-Mini" [paper:Table 2 caption; App.H Obs.6]. The conclusion is still an inference [inferred]. The code default in `optimize_anything` is `"openai/gpt-5.1"` [code:gepa/src/gepa/gepa_launcher.py:ReflectionConfig.reflection_lm], and the FAQ recommends "a leading frontier model" [docs:guides/faq.md].

---

## 3. The loop, step by step (precise pseudocode)

The paper's Algorithm 1 is reproduced faithfully below. I annotate it with the reference implementation's exact semantics. Where the two differ, the code is more precise, and I say so.

```text
# ---------- Inputs [paper:Alg.1, p.18] / [code:gepa/src/gepa/api.py:optimize] ----------
Φ0            seed candidate: dict[component_name -> text]            (seed_candidate)
D_train       feedback set; minibatches are drawn from it             (trainset → D_feedback)
D_pareto      selection/validation set                                (valset; if None → D_train)
μ, μ_f        metric and feedback function (inside the adapter)       (adapter.evaluate / make_reflective_dataset)
R             reflection LM                                           (reflection_lm)
B             rollout budget ("metric calls")                         (max_metric_calls, or other stoppers)
b             minibatch size, default 3                               [paper:App.G.4][code:api.py: reflection_minibatch_size or 3]
rng           random.Random(seed=0), shared by the candidate selector, batch sampler and merge

# ---------- Initialization [paper:Alg.1 l.1–5] ----------
P  = [Φ0];  parents = [[None]];  rr = [0]                  # rr = round-robin module pointer per candidate
S[0][v] = μ(Φ0(x_v), m_v)  for all v in D_pareto           # one batched adapter call, no traces
evals  = |D_pareto|                                          # the seed eval counts toward the budget
front_best[v] = S[0][v];  front_progs[v] = {0}              # per-instance Pareto frontier
# (optimize_anything default frontier_type="hybrid" also keeps per-objective fronts; see §4.3)

i = -1
while not STOP(state):             # checked ONLY at the top of an iteration (stopper protocol):
                                   #   evals >= B | file run_dir/gepa.stop | timeout | score threshold | …
    save_state(run_dir)            # gepa_state.bin, run_log.json, candidates.json
    i += 1

    # ---- (1) Merge, if enabled and scheduled [code:core/engine.py:GEPAEngine.run] ----
    # last_iter_found_new_program is set ONLY by an accepted *reflective* child (step 2);
    # an accepted merge never sets it, so two merges never run back to back.
    if use_merge and merges_due > 0 and last_iter_found_new_program:
        prop = MERGE(state)                        # see below; returns None if no valid triplet
        last_iter_found_new_program = False
        if prop is not None:
            if sum(prop.sub_after) >= max(sum(sub_i), sum(sub_j)):   # merge acceptance: ">= best parent"
                FULL_EVAL_AND_ADD(prop.candidate, parents=[i_, j_])
                merges_due -= 1;  total_merges_tested += 1
            continue                               # merge accepted OR rejected → skip reflection this iteration
        # prop is None → fall through: a reflective mutation runs in this same iteration,
        # and merges_due is NOT decremented (it keeps accumulating; see "soft cap" below)
    if use_merge: last_iter_found_new_program = False

    # ---- (2) Reflective mutation [paper:Alg.1 l.7–19] ----
    k    = SELECT_CANDIDATE(state)                 # Alg. 2 (Pareto); alternatives in §4.2
    Mids = BATCH_SAMPLER.next(i)                   # epoch-shuffled, size b, from D_train
    E    = adapter.evaluate(D_train[Mids], P[k], capture_traces=True);  evals += b
    if E.trajectories is empty:                               continue      # skip
    if skip_perfect_score and all(s >= perfect_score for s in E.scores):  continue
    comps = MODULE_SELECTOR(state, k)              # round-robin: [names[rr[k]]]; rr[k] = (rr[k]+1) mod |names|
                                                   #   ("all" → every component at once)
    refl  = adapter.make_reflective_dataset(P[k], E, comps)   # {comp: [ {Inputs, Generated Outputs, Feedback}, …]}
    new = {}
    for c in comps with non-empty refl[c]:
        prompt = TEMPLATE.replace("<curr_param>", P[k][c]).replace("<side_info>", MARKDOWN(refl[c]))
        raw    = R(prompt)
        text   = PARSE_FENCED(raw)                 # between first and last ``` ; reject known-truncated output
        if text is not None: new[c] = text
    if not new:                                    continue      # no child, no extra evals
    Φ'   = copy(P[k]); Φ'.update(new)
    E'   = adapter.evaluate(D_train[Mids], Φ', capture_traces=True);  evals += b
    if sum(E'.scores) > sum(E.scores):             # StrictImprovementAcceptance [paper: "if σ' improved"]
        FULL_EVAL_AND_ADD(Φ', parents=[k])         # paper l.15–18
        if use_merge:
            last_iter_found_new_program = True
            if total_merges_tested < max_merge_invocations: merges_due += 1   # SOFT cap: gates scheduling,
                                                   # not acceptance; merges_due can accumulate
                                                   # beyond the cap while MERGE keeps returning None
    else:
        log_reject(k, Mids, E.scores, E'.scores, Φ')

return argmax_c  mean_v S[c][v]                    # [paper:Alg.1 l.21]; ties → higher coverage, then lowest index

# ---------- FULL_EVAL_AND_ADD [code:core/engine.py:_add_evaluated_program, core/state.py:update_state_with_new_program] ----------
FULL_EVAL_AND_ADD(Φ', parents):
    V = adapter.evaluate(D_pareto, Φ');  evals += |D_pareto| (cache misses only, if caching is on)
    n = len(P);  P.append(Φ');  parents.append(parents)
    rr.append(max(rr[p] for p in parents))         # child inherits the parent's (already advanced) pointer
    S[n] = V
    for v in D_pareto:
        if V[v] >  front_best[v]:  front_best[v] = V[v];  front_progs[v] = {n}
        elif V[v] == front_best[v]: front_progs[v].add(n)
    record num_metric_calls_by_discovery[n] = evals_before_this_eval

# ---------- SELECT_CANDIDATE: Pareto-based [paper:Alg.2, p.18][code:gepa_utils.py:select_program_candidate_from_pareto_front] ----------
SELECT_CANDIDATE(state):
    F   = { key: set(progs) }                      # key = val id ("instance"); objective/hybrid/cartesian in §4.3
    agg = [ mean_v S[c][v]  for c in P ]
    C   = sorted(unique programs appearing in F, key=agg, ascending)
    D   = {}
    repeat until no change:                        # "remove_dominated_programs"
        for y in C \ D (ascending agg):
            others = C \ D \ {y}
            if every front f∈F with y∈f also contains some program in others:
                D.add(y); break                    # restart the scan
    F̂   = { key: f \ D }
    freq[c] = #{ key : c ∈ F̂[key] }
    return rng.choice([c repeated freq[c] times])  # P(c) = freq[c] / Σ freq

# ---------- MERGE: system-aware crossover [paper:Alg.3–4, App.D.1, p.19][code:proposer/merge.py] ----------
MERGE(state):
    cands = non-dominated programs of F (same pruning as above)          # "find_dominator_programs"
                                                   # (paper Alg.4 samples i,j from ALL of P; its prose
                                                   #  says merged candidates "are pareto-optimal")
    if |cands| < 2 or |P| < 3: return None
    repeat up to 10 outer attempts, each running find_common_ancestor_pair
    with up to 10 inner pair samples (≤ 100 pair draws in total):
        (i, j) = rng.sample(cands, 2), ordered i < j
        if i is an ancestor of j or vice versa: retry                   # "skip direct ancestry"
        A = common ancestors of i and j, filtered:
              not already merged as (i, j, a);
              agg[a] <= agg[i] and agg[a] <= agg[j];                    # both descendants improve on a
              DESIRABLE(a, i, j): ∃ module m with (π_a[m]==π_i[m] or π_a[m]==π_j[m]) and π_i[m] != π_j[m]
        if A empty: retry
        a = rng.choices(A, weights=[agg[x] for x in A])
                                                   # edge case: if every a∈A has agg 0.0, random.choices raises
                                                   # ValueError("Total of weights must be greater than zero").
                                                   # No metric call has happened yet in that iteration, so the
                                                   # engine re-raises even with raise_on_exception=False → the run
                                                   # ABORTS [run:merge-zero-weight-probe][code:core/engine.py:run]
        Φ' = copy(P[a])
        for m in modules:
            if   π_a==π_i and π_j!=π_i: Φ'[m] = π_j        # take the side that changed m
            elif π_a==π_j and π_i!=π_j: Φ'[m] = π_i
            elif π_i!=π_a and π_j!=π_a:             Φ'[m] = π of argmax(agg[i], agg[j]); ties → rng
                                                   # (code does not require π_i != π_j here; if equal, same text anyway)
            else (π_i==π_j==π_a):                   Φ'[m] = π_i
        if (i, j, module-source-tuple) already tried: retry
        require |ids scored for both i and j| >= merge_val_overlap_floor (default 5)
                                                   # ⇒ with FullEval and |D_pareto| < 5, merge NEVER fires
                                                   #   (e.g. optimize_anything single-task mode, |D_pareto|=1) [inferred from code]
        record (i, j, a)
        sub = 5 val ids: up to ceil(5/3)=2 each from {i better}, {j better}, {tie}, filled randomly
        sub_after = μ on sub for Φ';  evals += 5 (cache misses)
        return Proposal(Φ', parents=[i, j], sub_before=[Σ S[i][sub], Σ S[j][sub]], sub_after)
    return None
```

**Behaviours that follow from the code and that a faithful reimplementation must keep** [code refs as above]:
- **Rejected children are discarded.** They are never scored on D_pareto and never enter the pool. Only the iteration trace remembers them (`run_log.json`, and the `iterations/<id>/` directories when `write_agent_state=True`).
- **The Pareto frontier is over the validation set, not the minibatch.** The minibatch only gates entry. The paper's prose says "for each training instance", but Algorithm 1 builds it over D_pareto, and in the experiments D_pareto is the validation set [paper:§3.1,p.5][paper:App.G.4,p.24].
- **The domination rule is a set-cover rule.** In the code, a program y counts as dominated when every frontier key that y wins is also won by some other surviving program. Candidates are removed in ascending order of aggregate score. So a low-aggregate generalist can be pruned in favour of two higher-aggregate specialists that together cover its wins [run:pareto-probe] (§4.7). The paper words it as "dominated by another in C \ D" [paper:Alg.2 l.9], which reads as a single dominator. The code is looser.
- **The merge schedule has two parts, and its cap is soft.** *(Fact-check correction: the earlier text said merges run only "while fewer than `max_merge_invocations` merges have been accepted"; that is wrong.)*
  - Merge is attempted only in the iteration right after an accepted **reflective** child, and only while `merges_due > 0`.
  - `max_merge_invocations` is checked only when `merges_due` is *incremented*. It is not checked when a merge is attempted or accepted.
  - When `MERGE` returns None (no valid triplet yet), `merges_due` is not consumed. So every accepted reflective child adds 1 to it while `total_merges_tested < max_merge_invocations`. Once triplets become available, all the accumulated merges can be accepted.
  - A rejected merge consumes neither counter, but it does clear `last_iter_found_new_program`.
  - **Probe** [run:merge-cap-probe]: with `max_merge_invocations=1` on a 3-component toy with trade-offs, `merges_due` climbed to 9 while no triplet existed. After that, 9 merged children were accepted. Across 8 seeds, 3–9 merges were accepted per run with cap 1.
  - The paper's statement "merge is invoked a maximum of 5 times" [paper:App.G.4] is therefore **not** a hard guarantee of the current code.
  - The paper summarizes merge as: "GEPA routinely checks if the pool has 2 such candidates, invoking merge when identified. These strict lineage conditions mean merge occurs sparsely" [paper:App.D.1,p.19].
- **Budget overshoot** (in `gepa.optimize` and the legacy `gepa_launcher.optimize_anything`).
  - The stop condition is checked only between iterations. With single-mutation sampling and no refiner, the final iteration can therefore overshoot B by up to 2b + |D_pareto|.
  - Probes: B = 150 stopped at 156 [run:toy-e2e]. Independently, B = 200 stopped at 210 and B = 151 at 156 [run:overshoot-probe].
  - The new `gepa.optimize_anything.optimize_anything` entry point behaves differently. Its eval server enforces `max_evals` as a **hard** cap: `BudgetTracker.record` raises `BudgetExhausted` in the middle of an iteration. `GepaEngine` then reloads the result from the last `gepa_state.bin`, which is saved at the top of each iteration. The partial iteration's evaluations are spent but discarded [code:gepa/src/gepa/oa/budget.py:BudgetTracker.record][code:gepa/src/gepa/oa/engines/gepa.py:GepaEngine.run].
- **Minibatch evaluations are never served from the engine cache.** With `cache_evaluation=True`, the parent and child minibatch evaluations always run and are always charged. Their results are only *written* to the cache. Only D_pareto evaluations (`_evaluate_programs_on_valset`) and merge subsample evaluations (`cached_evaluate_full`) read the cache [code:proposer/reflective_mutation/reflective_mutation.py:propose][code:core/engine.py:_evaluate_programs_on_valset]. The `optimize_anything` adapter has its own separate cache layer (`cache_mode`) [code:adapters/optimize_anything_adapter/optimize_anything_adapter.py].
- **Parallel proposals.** In the current code, one iteration can create several (parent, minibatch) tasks. The options are `SameParentSampling(n)`, `IndependentSampling(n)` and `PxNSampling(p, n)`, and a `SelectionStrategy` (`AllImprovements` by default, or `BestImprovement` / `TopKImprovements(k)`) chooses which improving children enter the pool. The default `SingleMutationSampling` reproduces the paper's single mutation [code:gepa/src/gepa/strategies/proposal_sampling.py][code:gepa/src/gepa/strategies/proposal_selection.py][docs:blog/2026-07-30-parallel-proposals].

**Operating modes** [code:gepa/src/gepa/gepa_launcher.py:optimize_anything docstring][oa-paper:§2.2]:
- *Generalization:* `dataset` and `valset` are both given. This is the paper's setting.
- *Multi-task search:* only `dataset` is given, so valset = dataset. The paper's "inference-time search" sets D_train = D_pareto = the set of tasks to solve [paper:App.E,p.19].
- *Single-task search:* neither is given. The dataset is a single sentinel item, the minibatch is 1, and "the candidate *is* the solution".
- *Seedless:* `seed_candidate=None` plus an `objective`. The reflection LM writes the first candidate.

---

## 4. Formulas, objectives, acceptance rules, schedules, defaults

### 4.1 Objective [paper:§2,p.3–4]
- Task instance (x, m). m is evaluator metadata such as gold answers, rubrics or unit tests.
- Output y = Φ(x; ⟨Π, Θ⟩_Φ).
- Metric μ: 𝒴 × ℳ → [0,1].
- Optimization problem: ⟨Π*, Θ*⟩_Φ = argmax E_{(x,m)∼𝒯}[ μ(Φ(x; ⟨Π,Θ⟩_Φ), m) ], subject to #rollouts ≤ B.
- GEPA searches Π only, with Θ frozen.
- A **rollout** is "invocations of Φ plus evaluation by μ" [paper:§2,p.4]. In code this is one "metric call": one (candidate, example) evaluation [code:gepa/src/gepa/utils/stop_condition.py:MaxMetricCallsStopper]. The overview's glossary defines a rollout as "One complete run of an AI system on a task" [doc].

### 4.2 Selection, acceptance, return
- **Per-instance frontier** [paper:Alg.2]:
  - s*[i] = max_k S_{P[k]}[i]
  - P*[i] = {P[k] : S_{P[k]}[i] = s*[i]} (ties kept)
  - Ĉ = non-dominated candidates in ∪_i P*[i]
  - f[Φ] = |{i : Φ ∈ P̂*[i]}|
  - P(select Φ) = f[Φ] / Σ_{Φ'∈Ĉ} f[Φ']
- **Minibatch acceptance:** accept Φ' iff Σ_{m∈M} μ(Φ'(x_m)) > Σ_{m∈M} μ(Φ_k(x_m)) [code:gepa/src/gepa/strategies/acceptance.py:StrictImprovementAcceptance]. The paper defines σ, σ' as the "avg score on M (before, after)" and writes the test only as "if σ' improved" [paper:Alg.1 l.13–14]. It never says whether the inequality is strict. The code's strict `>` on sums is equivalent to a strict `>` on averages over the same M. The option `"improvement_or_equal"` accepts ties [code:gepa/src/gepa/strategies/acceptance.py:ImprovementOrEqualAcceptance].
- **Skip rule:** if every parent minibatch score is ≥ `perfect_score` (default 1.0), skip reflection. This is on by default in `gepa.optimize` (`skip_perfect_score=True`) and off by default in `optimize_anything` [code:gepa/src/gepa/api.py:optimize][code:gepa/src/gepa/gepa_launcher.py:ReflectionConfig].
- **Merge acceptance:** Σ_sub μ(Φ') ≥ max(Σ_sub S_i, Σ_sub S_j) on the 5-id merge subsample drawn from D_pareto [code:gepa/src/gepa/core/engine.py:GEPAEngine.run]. The paper states that "both candidates improve upon the aggregate performance of the ancestor" (a precondition) but gives no explicit acceptance inequality [paper:App.D.1].
- **Return:** Φ* = argmax_c (1/|D_pareto|) Σ_v S_c[v] [paper:Alg.1 l.21].
- **Alternative selectors in the code:**
  - `current_best`: argmax of the aggregate. Its semantics match the paper's "SelectBestCandidate" ablation. That the paper's runs used this exact class is an assumption [inferred].
  - `epsilon_greedy`: ε = 0.1, uniform over all candidates with probability ε.
  - `top_k_pareto`: k = 5; the Pareto rule restricted to the top k by aggregate [code:gepa/src/gepa/api.py:factories][code:gepa/src/gepa/strategies/candidate_selector.py].
  - The paper's `BeamSearch(N=4)` ablation ("maintains a pool of the top-N candidates (used by APO)") is **not in the repository** [paper:Table 3,Obs.3,p.7–8][code: no "beam" anywhere in src/].

### 4.3 Frontier keys (code extension, `frontier_type`) [code:gepa/src/gepa/core/state.py:_get_pareto_front_mapping][docs:guides/candidate-selection.md]
- `instance` (the default in `gepa.optimize`, and the paper's version): one key per validation example.
- `objective`: one key per objective in `objective_scores`. The objective values are the per-objective means over validation.
- `hybrid` (the default in `optimize_anything`): instance keys ∪ objective keys.
- `cartesian`: one key per (example, objective) pair.
- In `optimize_anything`, objective scores come from `side_info["scores"]`, plus `side_info["<param>_specific_info"]["scores"]` renamed to `"<param>::<k>"` [code:gepa/src/gepa/adapters/optimize_anything_adapter/optimize_anything_adapter.py:_extract_objective_scores].
- Documented caveat: a candidate that is good across the board but never best on any key "never enters the frontier" [docs:guides/candidate-selection.md].
- **`hybrid` falls back to `instance`** when the evaluator's `side_info` has no `"scores"` key. The adapter then returns an empty objective dict per example, so the objective front is empty and only instance keys remain [code:…/optimize_anything_adapter.py:_assemble_no_refiner_batch, _extract_objective_scores][inferred]. In single-task mode (|D_pareto| = 1), all frontier diversity therefore has to come from `side_info["scores"]` sub-scores.

### 4.4 Schedules and samplers
- **Epoch-shuffled minibatches** [code:gepa/src/gepa/strategies/batch_sampler.py:EpochShuffledBatchSampler]:
  - Each epoch, shuffle all train ids.
  - Pad with the least-frequent ids until the length L is a multiple of b.
  - The minibatch at iteration i is `shuffled[(i·b) mod L : (i·b) mod L + b]`, and a new epoch starts when ⌊i·b / L⌋ increases.
  - The index i is the engine's `state.i`, which advances on **every** iteration, including merge iterations that never draw a minibatch. A merge iteration therefore skips one minibatch slot of the current epoch. Iterations skipped by the perfect-score or no-trajectories rules evaluate the batch but do not reflect on it. So "every train example is reflected on once per epoch" holds only when no merges and no skips occur [code:strategies/batch_sampler.py:next_minibatch_ids][inferred].
  - With multi-proposal sampling, repeated calls within one iteration take consecutive chunks [code:strategies/batch_sampler.py].
- **Module selection** [code:gepa/src/gepa/strategies/component_selector.py]:
  - Round-robin per candidate. The paper says "based on a policy (round-robin)" [paper:§3,p.5].
  - Or `"all"`, which updates every module in one reflection; the FAQ says this gives "a large boost in rollout efficiency" [docs:guides/faq.md].
- **Merge schedule:** see §3. The paper says merge "is invoked a maximum of 5 times" [paper:App.G.4]. The code default is `max_merge_invocations = 5` [code:gepa/src/gepa/api.py:optimize], but it is a **soft** cap on scheduling, not on accepted merges [run:merge-cap-probe]. A faithful "invoked a maximum of 5 times" needs an attempt-time guard on the number of merges **invoked**, i.e. built and scored on their subsample, accepted or rejected (`len(merges_performed[0]) < max_merge_invocations`) [inferred]. *(Claim-audit correction: an earlier version of this recipe proposed `total_merges_tested < max_merge_invocations`; that counter increments only on acceptance [code:core/engine.py:1038-1039], so the guard caps accepted merges and leaves rejected ones (5 rollouts each) unlimited.)* The paper's "GEPA routinely checks if the pool has 2 such candidates, invoking merge when identified" [paper:App.D.1] separates a *check* (no valid triplet: no rollout, fall through to reflection) from an *invocation* (triplet found, child built and scored).

### 4.5 Budget accounting (per run, no caching) [code:core/engine.py, proposer/reflective_mutation/reflective_mutation.py, proposer/merge.py][run:toy-e2e]

```
evals_total = |D_pareto|                                   # seed
            + Σ_iters  b·[parent evaluated] + b·[child proposed]
            + |D_pareto| · (#accepted reflective children)
            + Σ_merges 5 + |D_pareto|·[merge accepted]
```

- A rejected iteration costs 2b. An accepted one costs 2b + |D_pareto|.
- Reflection-LM calls are *not* metric calls. They are tracked separately as cost (`max_reflection_cost` stopper) [code:gepa/src/gepa/api.py:optimize].
- The paper confirms: "The majority of GEPA's rollout budget is spent on validation, where scores are utilized solely for candidate selection and not for producing learning signals" [paper:Obs.1,p.6].

### 4.6 Hyperparameters and defaults

| Symbol / knob | Paper experiments | Code default (`gepa.optimize`) | Code default (`optimize_anything`) |
|---|---|---|---|
| minibatch b | 3 [paper:App.G.4] | `reflection_minibatch_size or 3` [code:api.py] | 1 for single-task, otherwise 3 [code:gepa_launcher.py] |
| n_pareto = \|D_pareto\| | the validation split: 300 (HotpotQA, IFBench, HoVer), 111 (PUPA), 45 (AIME), about 123 (LiveBench) [paper:App.G.1][inferred: 90/2; 368/3] | valset, or trainset if None | valset, or dataset if None |
| budget B | matched per benchmark to MIPROv2's rollouts (within 10.15%); 1,839–7,051 [paper:App.G.4,Table 1] | `max_metric_calls` (required unless another stopper is given) | Two entry points. The new `gepa.optimize_anything.optimize_anything` uses `OptimizeAnythingConfig.max_evals = 100`, a hard cap enforced by the eval server [code:oa/config.py][code:oa/budget.py]. The legacy `gepa.gepa_launcher.optimize_anything` uses `EngineConfig.max_metric_calls = None` and needs a stopper [code:gepa_launcher.py:EngineConfig]. Sizing: the skill recommends ≳ 15–20 × \|valset\| in generalization mode, ≳ 15–20 × \|dataset\| in multi-task mode and ≳ 15–20 in single-task mode [skill:SKILL.md]; the FAQ says 15–30 × \|valset\| [docs:guides/faq.md] |
| candidate selection | Pareto; ablations SelectBestCandidate and BeamSearch(N=4) | `"pareto"` | `"pareto"` |
| frontier type | per instance | `"instance"` | `"hybrid"` |
| module selector | round-robin | `"round_robin"` | `"round_robin"` |
| acceptance | "σ' improved" | `"strict_improvement"` | `"strict_improvement"` |
| merge | off (GEPA) / on (GEPA+Merge), "invoked a maximum of 5 times" [paper:App.G.4] | `use_merge=False`, `max_merge_invocations=5` (soft cap, §3), `merge_val_overlap_floor=5` | `merge=None` (off); `MergeConfig(5, 5)` |
| merge subsample | not stated | 5 val ids | same |
| perfect-score skip | not stated | `skip_perfect_score=True`, `perfect_score=1.0` | `skip_perfect_score=False`, `perfect_score=None` |
| seed | not stated | `seed=0` | `EngineConfig.seed=0` |
| reflection LM | not named. Table 4 and the "optimized … using the weaker Qwen3-8B" wording suggest it matches the task LM per setting [paper:Table 2 caption, Table 4][inferred]. Extended applications used GPT-4o (kernels) and GPT-5 Mini (adversarial) as task models [paper:App.E–F] | required (string via LiteLLM, or a callable) | `"openai/gpt-5.1"` |
| task LM decoding | Qwen3-8B: T = 0.6, top-p 0.95, top-k 20; GPT-4.1-mini: T = 1.0; context ≤ 16,384 tokens [paper:App.G.2] | n/a (user's system) | n/a |
| eval cache | "operating under a cache" for kernel search only [paper:App.E] | off | off |
| parallel evaluation | not stated | sequential `adapter.evaluate` unless the adapter implements `batch_evaluate` | `EngineConfig.parallel=True`, `max_workers = os.cpu_count() or 32` [code:gepa_launcher.py:EngineConfig] |
| other stoppers | — | `stop_callbacks` (Timeout, NoImprovement, File, Signal, MaxTrackedCandidates, MaxCandidateProposals, ScoreThreshold, Composite) [code:utils/stop_condition.py] | also `max_candidate_proposals`, `stop_at_score`, `max_reflection_cost` [code:gepa_launcher.py:EngineConfig] |
| per-example warm-start history | — | — | `best_example_evals_k = 30`: an evaluator that declares `opt_state` receives `OptimizationState.best_example_evals`, the top-K earlier `{score, side_info}` for that example [code:gepa_launcher.py:OptimizationState] |

### 4.7 Probe runs of the reference code (sanity vectors for unit tests)
- **[run:pareto-probe]**: `remove_dominated_programs`.
  - Input front {0:{0,1}, 1:{1}, 2:{2,3}, 3:{3}}, agg = [0.4, 0.6, 0.5, 0.7]. Output {0:{1}, 1:{1}, 2:{3}, 3:{3}}. Over 10,000 samples the selection frequency was ≈ 50/50 between 1 and 3.
  - Input front {1:{0,1}, 2:{0,2}}. With agg = [0.1, 0.5, 0.6] the result is {1:{1}, 2:{2}}, so the generalist 0 is pruned. With agg = [0.9, 0.5, 0.6] the result is {1:{0}, 2:{0}}.
- **[run:toy-e2e]**: `gepa.optimize` with a keyword-toy adapter (|train| = 24, |val| = 12, b = 3, B = 150) and a mock reflection LM that adds one missing keyword per call. The run produced 9 candidates in one linear chain, 8 reflection calls and 156 metric calls, which is exactly 12 + 8·(6 + 12). This confirms §4.5 and the boundary-check overshoot.
- The fact-check pass re-ran both pareto-probe vectors against `gepa_utils.py` and got identical outputs (selection counts 5020/4980 over 10,000 draws).
- **[run:overshoot-probe]** (fact-check pass; `scratchpad/gepa_probe/`): a 3-component keyword toy (|train| = 18, |val| = 12, b = 3). B = 150 gave 150 calls, B = 151 gave 156, and B = 200 gave 210.
- **[run:merge-zero-weight-probe]** (fact-check pass): candidates `[{A:s,B:s}, {A:x,B:s}, {A:s,B:y}]` with parents `[[None],[0],[0]]` and merge candidates `[1,2]`.
  - With agg `[0.0, 0.5, 0.5]`, the call raises `ValueError: Total of weights must be greater than zero`.
  - With agg `[0.1, 0.5, 0.5]`, it returns `({A:x, B:y}, 1, 2, 0)`, which is the expected module-wise merge.
- **[run:merge-cap-probe]** (fact-check pass; `scratchpad/gepa_probe/merge_cap_probe.py`, `merge_cap_trace.py`):
  - Setup: the same toy, but a component only "counts" its last 2 tokens. That creates trade-offs, so lineages branch.
  - With `use_merge=True`, `max_merge_invocations=1`, B = 1500 and seed = 1, `merges_due` rose 1→9 over the first 8 merge attempts, each of which returned None. After that, 9 merges were proposed and accepted, and 9 two-parent children were in the pool.
  - Over seeds 0–7, accepted merges per run ranged from 3 to 9 with cap 1. This is the regression vector for the soft-cap behaviour.

---

## 5. Data structures and artifacts

- **Candidate**: `dict[str, str]`, from component name to text. A plain `str` seed is wrapped as `{"current_candidate": s}` in `optimize_anything` [code:gepa/src/gepa/gepa_launcher.py:_STR_CANDIDATE_KEY].
- **EvaluationBatch** [code:gepa/src/gepa/core/adapter.py]: `outputs: list`, `scores: list[float]` (summed for minibatch acceptance, averaged for validation), `trajectories: list | None` (required when `capture_traces=True`), `objective_scores: list[dict[str,float]] | None`, `num_metric_calls: int | None`.
- **Reflective dataset record**, per component [code:gepa/src/gepa/core/adapter.py:make_reflective_dataset docstring]: `{"Inputs": …, "Generated Outputs": … , "Feedback": str}` plus optional extra keys.
  - The DSPy adapter picks one trace instance of the predictor per example. It prefers a failed parse, and otherwise draws one with a seeded `rng.choice`.
    - On a parse failure, `"Generated Outputs"` becomes "Couldn't parse the output as per the expected output format. The model's raw response was: …" and `"Feedback"` becomes "Your output failed to parse. Follow this structure:" followed by the ChatAdapter-formatted signature.
    - Otherwise `"Feedback"` is the `feedback` text returned by the user's per-predictor feedback function (`feedback_map[predictor]`) [code:gepa/src/gepa/adapters/dspy_adapter/dspy_adapter.py:make_reflective_dataset].
    - *(Fact-check correction: the "Couldn't parse" text goes into Generated Outputs, not into Feedback.)*
  - `optimize_anything` copies every `side_info` key, renames `"scores"` to `"Scores (Higher is Better)"` and merges `"<comp>_specific_info"` for the targeted component [code:…/optimize_anything_adapter.py:make_reflective_dataset].
- **CandidateProposal** [code:gepa/src/gepa/proposer/base.py]: `candidate`, `parent_program_ids`, `subsample_indices`, `subsample_scores_before/after`, `eval_before/after: SubsampleEvaluation(scores, outputs, objective_scores, trajectories)`, `tag ∈ {"reflective_mutation","merge"}`, and `metadata` (for example `prompt:<comp>`, `raw_lm_output:<comp>`, `proposal_id`, or `ancestor` for merges).
- **GEPAState** [code:gepa/src/gepa/core/state.py:GEPAState]: This is the search tree plus the frontier. It is pickled to `run_dir/gepa_state.bin`, and resuming from it is supported.
  - Candidates and lineage: `program_candidates`, `parent_program_for_candidate` (the genealogy DAG; merges have two parents), `iteration_ids_by_candidate_idx`.
  - Scores: `prog_candidate_val_subscores[c][val_id]`, `prog_candidate_objective_scores[c]`.
  - Frontiers: `pareto_front_valset[val_id]` (best score), `program_at_pareto_front_valset[val_id]` (set of candidates), and the objective and cartesian analogues.
  - Module bookkeeping: `list_of_named_predictors`, `named_predictor_id_to_update_next_for_program_candidate` (the round-robin pointers).
  - Counters: `i`, `total_num_evals`, `num_full_ds_evals`, `num_metric_calls_by_discovery[c]`.
  - `full_program_trace` (one dict per iteration; below), `best_outputs_valset`, `evaluation_cache` (keyed by (sha256(candidate), split, example_id)), `adapter_state`.
- **Iteration trace entry** (`run_log.json`) [code:core/engine.py, reflective_mutation.py, merge.py]:
  - Always: `i`, `iteration_id` (8 hex chars).
  - Reflective path: `selected_program_candidate`, `subsample_ids`, `subsample_scores`, `new_subsample_scores`, `n_tasks`, `tasks[]` (per task: `parent_idx`, `subsample_ids`, `subsample_scores`, `new_subsample_scores`), and, when accepted, `new_program_idx`, `new_program_indices`, `evaluated_val_indices`.
  - Written only when `write_agent_state=True`: `proposal_accepted`, `eval_before` / `eval_after` (scores, outputs, objective_scores, trajectories) and `proposed_candidate` [code:core/engine.py:_run_reflective_batch, _record_proposal_evals].
  - Merge path: `invoked_merge` (set even if `MERGE` returns None), `merged`, `merged_entities=(id1,id2,ancestor)`, `subsample_ids`, `id1_subsample_scores`, `id2_subsample_scores`, `new_program_subsample_scores`. An accepted merge also gets `proposal_accepted=True` when `write_agent_state` is on.
- **run_dir layout** [code:gepa/src/gepa/core/state.py:save, _save_agent_directory][code:gepa/src/gepa/api.py:optimize docstring]:
  - `gepa_state.bin`, `run_log.json`, `candidates.json`, `run_log.txt`, `candidate_tree.html`.
  - `generated_best_outputs_valset/task_<val_id>/iter_<i>_prog_<k>.json`.
  - `gepa.stop`, a stop file checked every iteration.
  - With `write_agent_state=True`:
    - `iterations/<id>/{meta.json, components/<name>.txt, components/_index.json, val_scores.json, outputs/, trajectories/}`. The seed goes in `iterations/seed/`, and rejected proposals get their own directories too.
    - `meta.json` holds `iteration_id`, `trace_i`, `accepted`, `parent_iteration_ids`, `candidate_idx`, `subsample_ids` and `subsample_scores_before/after`. Accepted candidates also get `avg_val_score`, `num_val_scored`, `objective_scores` and `metric_calls_to_discover`.
    - The docstrings of `api.optimize` and `EngineConfig` also promise a per-iteration `trace.json`, but no code path writes one [code:core/state.py:_save_iteration_dirs; grep].
    - `<id>` is a random `uuid4().hex[:8]` that is **not** seeded, so directory names differ between otherwise identical runs [code:core/state.py:new_iteration_id].
    - `pareto/{instance,objective,cartesian}_front.json`.
    - A `gepa_state.json` index (best iteration, frontier iteration ids, the 20 hardest examples).
- **GEPAResult** [code:gepa/src/gepa/core/result.py]:
  - The pool: `candidates`, `parents`, `val_aggregate_scores`, `val_subscores`, `per_val_instance_best_candidates`, `discovery_eval_counts`, objective fronts, `best_outputs_valset`, `total_metric_calls`, `num_full_val_evals`.
  - Derived: `best_idx` = argmax of the aggregate and `best_candidate`.
  - In `optimize_anything`: `metadata["test_score"]` (mean) and `metadata["test_scores"]` (per example), their `baseline_test_score` / `baseline_test_scores` counterparts for the seed, and `eval_log` [code:gepa/src/gepa/optimize_anything.py:_run_engine][skill:gotchas.md §2].
- **Callbacks** [code:gepa/src/gepa/core/callbacks.py]: `on_optimization_start/end`, `on_iteration_start/end`, `on_candidate_selected`, `on_minibatch_sampled`, `on_evaluation_start/end/skipped`, `on_reflective_dataset_built`, `on_proposal_start/end`, `on_candidate_accepted/rejected`, `on_valset_evaluated`, `on_pareto_front_updated` (with `new_front` and `displaced_candidates`), `on_merge_attempted/accepted/rejected`, `on_budget_updated`, `on_state_saved`, `on_error`.

---

## 6. LLM roles and the gist of their prompts

| Role | Frozen? | What it sees | Source |
|---|---|---|---|
| **Task LM(s)**: the modules of Φ | frozen weights; prompts are evolved | module inputs and the current instruction | [paper:§2] |
| **Reflection LM**: the proposer | frozen | current instruction plus a minibatch of (inputs, outputs, feedback) | [paper:App.C][code:strategies/instruction_proposal.py] |
| **Judge LM** (optional, inside μ / μ_f) | frozen | output plus rubric; returns rating and critique | [skill:writing_evaluators.md][docs:guides/faq.md] |
| **Seed generator** (seedless mode) | the reflection LM | objective, background and up to 3 dataset examples | [code:gepa_launcher.py:_build_seed_generation_prompt] |
| **Refiner LM** (optional, `RefinerConfig`) | defaults to the reflection LM | candidate plus evaluation feedback, after each evaluation; the better of original and refined is kept; `refiner_prompt` is itself a co-evolved component | [code:gepa_launcher.py:RefinerConfig, DEFAULT_REFINER_PROMPT] |

**6.1 The default reflection meta-prompt.** It is verbatim in both [paper:App.C,p.17] and [code:gepa/src/gepa/strategies/instruction_proposal.py:InstructionProposalSignature.default_prompt_template]. The two are word-for-word identical. Only whitespace differs: the LaTeX listing has **two** extra blank lines, one after "…to perform a task for me:" and one after "…how the assistant's response could be better:", each between the sentence and its opening ``` fence [paper-src:App.C listing]. *(Claim-audit correction: an earlier version of this note named only the first.)* The code template, which is what runs, has neither blank line; `rsi/gepa/reflection.py` is byte-identical to the code. The paper shows the placeholders as `<current instruction>` and `<Inputs, Outputs and Feedback for minibatch of examples>`:

````text
I provided an assistant with the following instructions to perform a task for me:
```
<curr_param>
```

The following are examples of different task inputs provided to the assistant along with the assistant's response for each of them, and some feedback on how the assistant's response could be better:
```
<side_info>
```

Your task is to write a new instruction for the assistant.

Read the inputs carefully and identify the input format and infer detailed task description about the task I wish to solve with the assistant.

Read all the assistant responses and the corresponding feedback. Identify all niche and domain specific factual information about the task and include it in the instruction, as a lot of it may not be available to the assistant in the future. The assistant may have utilized a generalizable strategy to solve the task, if so, include that in the instruction as well.

Provide the new instructions within ``` blocks.
````

- **Rendering of `<side_info>`** [code:instruction_proposal.py:prompt_renderer.format_samples]: Each record becomes `# Example {n}`, each key a `## {key}` heading, and nested dicts or lists become `###`, `####` and so on, down to `######`. List elements get `Item k` headings, and scalars are rendered as `str(value).strip()`. Images become `[IMAGE-n — see visual content]` and are attached as a multimodal message.
- **Parsing** [code:instruction_proposal.py:ProposalAdapter.parse]:
  - Take the text between the first and last triple-backtick fence and drop a language tag on the opening line.
  - If there is no fence pair, use the whole stripped output. This is the "permissive" legacy behaviour.
  - Reject the output, so that the component is skipped, when it is known to be truncated. This check runs **only when there is no complete fence pair**. The output counts as truncated if `finish_reason ∈ {length, max_tokens}`, or if it *starts* with `<think>` and has more `<think>` than `</think>` tags [code:instruction_proposal.py:ProposalAdapter._is_known_truncated].
  - Rejected outputs are recorded in `ReflectionProposal.metadata["rejected_outputs"]` [code:proposer/reflective_mutation/reflection_lm.py:StatelessReflectionLM.reflect_many].
- **Custom templates** must contain both `<curr_param>` and `<side_info>`. A template can apply to all components or be given per component through a dict [code:instruction_proposal.py:validate_prompt_template].
- **Replacing the proposer entirely:** `adapter.propose_new_texts` or `custom_candidate_proposer(candidate, reflective_dataset, components_to_update, *, metadata)` [code:core/adapter.py:ProposalFn].
- **Pluggable reflection strategy** (`reflection_strategy=`, the `ReflectionLM` protocol): `reflect(candidate, refl_ds, comps) -> (ReflectionProposal, next_lm)`. The default `StatelessReflectionLM` makes one LM call per component that has feedback [code:proposer/reflective_mutation/reflection_lm.py].
  - **ComBEE** is a map/reduce alternative for large minibatches [code:proposer/reflective_mutation/combee.py:ComBEEReflectionLM].
    - It duplicates each of the n records p times (default `duplication_factor = 2`) and shuffles them with the seeded RNG.
    - It splits them into k = max(1, ⌊√n⌋) groups and makes one reflection call per group. A final "reduce" call synthesizes the k intermediate proposals.
    - It degenerates to a single call when n < 4, and costs k + 1 calls per component.
    - It is meant to pair with a raised `reflection_minibatch_size` (the docstring suggests 9–25). Motivation: [docs:blog/2026-04-09-gepa-at-scale-with-combee].

**6.2 `optimize_anything` default template.** Verbatim from [code:gepa/src/gepa/gepa_launcher.py:optimize_anything_reflection_prompt_template]:

````text
I am optimizing a parameter in my system. The current parameter value is:
```
<curr_param>
```

Below is evaluation data showing how this parameter value performed across multiple test cases. The data contains performance metrics, diagnostic information, and other relevant details from the evaluation:
```
<side_info>
```

Your task is to propose a new, improved parameter value that can be used as a drop-in replacement for the current one.

Carefully analyze all the evaluation data provided above. Look for patterns that indicate what works and what doesn't. Pay special attention to:
- Performance metrics and how they correlate with parameter behavior
- Recurring issues, errors, or failure patterns across multiple test cases
- Successful patterns or behaviors that should be preserved or enhanced
- Any domain-specific requirements, constraints, or factual information revealed in the evaluation data
- Specific technical details that are crucial for understanding the parameter's role

Based on your analysis, propose a new parameter value that addresses the identified issues while maintaining or improving upon what works well. Your proposal should be directly informed by the patterns and insights from the evaluation data.

Provide the new parameter value within ``` blocks.
````

When `objective` or `background` is given, the template is built dynamically instead [code:gepa_launcher.py:_build_reflection_prompt_template]. The built prompt contains these parts:
- It opens with "You are an expert optimization assistant. Your task is to analyze evaluation feedback and propose an improved version of a system component."
- An "## Optimization Goal" section holding the objective.
- A "## Domain Context & Constraints" section holding the background.
- "## Current Component" (`<curr_param>`) and "## Evaluation Results" (`<side_info>`).
- "## Your Task", which lists analysis points:
  - "**Goal alignment**" (only if an objective is given)
  - "**Failure patterns**"
  - "**Success patterns**"
  - "**Root causes**"
  - "**Constraint compliance**" (only if a background is given)
- It then asks for "an improved version that: 1. Addresses the identified failure patterns and root causes 2. Preserves successful behaviors … 3. Makes meaningful improvements rather than superficial changes".
- It ends with "## Output Format — Provide ONLY the improved version within ``` blocks … a complete, drop-in replacement".

A custom template together with objective or background raises an error, because the two are mutually exclusive.

**6.3 Seed-generation prompt (seedless mode)** [code:gepa_launcher.py:_build_seed_generation_prompt]: "You are an expert assistant. Your task is to generate an initial candidate that will be iteratively refined by an optimization system." It is followed by `## Goal`, the optional `## Domain Context & Constraints`, the optional `## Sample Inputs` (the first 3 dataset items), and "Provide ONLY the candidate within ``` blocks."

**6.4 Refiner prompt** (verbatim head) [code:gepa_launcher.py:DEFAULT_REFINER_PROMPT]: "You are a refinement agent improving candidates in an optimization loop. ## What We're Optimizing For … {objective} … ## Domain Knowledge {background} ## Your Task Given a candidate and its evaluation feedback: 1. Understand why it scored the way it did 2. Fix any errors (errors = zero score) 3. Make improvements that move toward the objective 4. Return the complete improved candidate".

**6.5 Feedback functions μ_f used in the paper** [paper:App.G.1,p.22–23]:
- HotpotQA: "identifies the set of relevant documents remaining to be retrieved at each stage of the program, and provides that as feedback to the modules at that stage".
- IFBench: "provides the descriptions of constraints satisfied and failed-to-be-satisfied".
- HoVer: "identifies the set of correct documents retrieved, and the set of documents remaining to be retrieved".
- PUPA: "the breakdown of the aggregate score, consisting of a response quality score and a PII leakage score".
- AIME and LiveBench-Math: the feedback is not described in the text.
- Kernel tasks: "retrieving relevant manual sections based on rollout failures (e.g., compiler error messages)" [paper:App.E].
- Human-written explanations can also serve as `feedback_text` [paper:§3,p.5].
- Default adapter evaluator [code:adapters/default_adapter/default_adapter.py:ContainsAnswerEvaluator]:
  - Score 1.0 if the gold answer string appears in the response.
  - Feedback on failure: "The generated response is incorrect. The correct answer is '{answer}'. Ensure that the correct answer is included in the response exactly as it is." plus any additional context.

**6.6 What a learned prompt looks like** [paper:Fig.2,p.3][paper-src:prompt_example.tex]. The seed for HotpotQA's second-hop query writer is "Given the fields `question`, `summary_1`, produce the fields `query`." GEPA's GPT-4.1-mini result is a long declarative instruction. It has sections "Input Understanding", "Purpose and Context", "Key Observations and Lessons", "How to Build the Query" and "Practical Strategy". It includes lessons such as "First-hop documents often cover one entity or aspect" and "Avoid merely paraphrasing the original question". It even carries a worked example ("Madeira archipelago population in 2011"). This is the "niche and domain specific factual information" that the meta-prompt asks for.

---

## 7. Experimental protocol and headline results (as reported)

### 7.1 Protocol [paper:§4,p.6; App.G,p.22–24]
- **Splits.** "We adopt a standard train/validation/test split. Optimizers have full access to the train split, including text and labels", and validation gives scores only [paper:§4]. In GEPA, D_feedback is the train split and D_pareto is the validation split [paper:App.G.4].

| Benchmark | System under optimization | Train / Val / Test |
|---|---|---|
| HotpotQA | HoVerMultiHop program with the last hop changed to answer | 150 / 300 / 300 |
| IFBench | 2-stage: answer, then rewrite to satisfy constraints; train and val from IF-RLVR Train, test = IFBench (58 new OOD constraints) | 150 / 300 / 294 |
| AIME-2025 | single-step ChainOfThought; AIME 2022–24 (90) split equally into train and val; test = AIME-2025 × 5 repeats | 45 / 45 / 30×5 [inferred: 90/2] |
| LiveBench-Math | single-step ChainOfThought; 368 questions, shuffled with seed 0, split equally | ≈123 / 123 / 122 [inferred: 368/3] |
| HoVer | HoverMultiHop from LangProBe: up to 3 hops, 2 query writers and 2 summarizers | 150 / 300 / 300 |
| PUPA | PAPILLON: query rewriter, untrusted-model call, response rewriter | 111 / 111 / 221 |

- **Models.**
  - Qwen3-8B (T = 0.6, top-p 0.95, top-k 20).
  - GPT-4.1-mini (`gpt-4.1-mini-2025-04-14`, T = 1.0).
  - All modules in a system use the same model, with a 16,384-token context [paper:App.G.2].
- **Baselines** [paper:App.G.4]:
  - Unoptimized baseline.
  - MIPROv2 with `auto=heavy`: 18 instruction candidates and 18 bootstrapped few-shot sets; 2,270 (PUPA) to 6,926 (HoVer) rollouts.
  - MIPROv2-No-Demos.
  - Trace (OptoPrime) and TextGrad. These were reimplemented in Trace with the same programs, data, metric and feedback. Per-module feedback is unsupported there.
  - GRPO:
    - Setup: multi-module implementation from mmGRPO; LoRA rank 16, α = 64, dropout 0.05, targeting q, k, v, o, up, down and gate.
    - Batching: group size 12, 4 instances per step, gradient accumulation 20.
    - Optimization: learning rate 1e-5, β = 0.01, gradient norm clip 0.1.
    - Schedule: 500 steps = 24,000 rollouts, with validation every 20 steps for early stopping.
    - A full-parameter single-module variant is used for 2-hop HoVer (SkyRL): group size 16, batch 32, learning rate 1e-6.
- **Budget matching.** "We first record the number of rollouts expended by MIPROv2 for each benchmark, and then cap GEPA's optimization to match this rollout budget… the discrepancy is always within 10.15%" [paper:App.G.4]. GEPA uses minibatch 3 and at most 5 merges.
- **Cost.** All GPT-4.1-mini runs in Table 2 cost under $500 in total: GEPA $86, GEPA+Merge $67, MIPROv2 $76, Trace and TextGrad $172 [paper:App.G.3].
- **Seeds and error bars.** Tables 1–3 report single numbers with no confidence intervals or seed counts [paper:Tables 1–3][inferred: none stated].
- **Ablations actually run.**
  - App.G.4 lists "2 ablations created by replacing the Pareto-based sampling strategy with a naive, SelectBestCandidate strategy (SelectBestCandidate and SelectBestCandidate+Merge)". Only SelectBestCandidate, plus BeamSearch(N=4), appears in Table 3 [paper:App.G.4; Table 3].
  - The paper has **no ablation that removes the textual feedback** (μ_f vs a score-only μ). The role of feedback text is argued in §3 but never isolated experimentally [paper: full text searched; inferred: absence].
  - The closest evidence is the optimize_anything draft's ASI ablation, which compares per-aspect sub-scores against an aggregate score only; it is not a text-feedback ablation (§7.7).

### 7.2 Table 1: Qwen3-8B, test scores [paper:Table 1,p.6]

| Qwen3 8B | HotpotQA | IFBench | HoVer | PUPA | AIME-2025 | LiveBench-Math | Aggregate | Improvement |
|---|---|---|---|---|---|---|---|---|
| Baseline | 42.33 | 36.90 | 35.33 | 80.82 | 27.33 | 48.70 | 45.23 | — |
| GRPO | 43.33 | 35.88 | 38.67 | 86.66 | **38.00** | 51.26 | 48.91 | +3.68 |
| MIPROv2 | 55.33 | 36.22 | 47.33 | 81.55 | 20.00 | 46.60 | 47.84 | +2.61 |
| GEPA | 62.33 | **38.61** | **52.33** | **91.85** | 32.00 | **51.95** | **54.85** | **+9.62** |
| GEPA+Merge | **64.33** | 28.23 | 51.67 | 86.26 | 32.00 | **51.95** | 52.40 | +7.17 |
| *Budget (# rollouts)*: GEPA (+Merge) | 6871 | 3593 | 7051 | 2426 | 1839 | 1839 | 3936 | — |
| *Budget*: GRPO | 24000 | 24000 | 24000 | 24000 | 24000 | 24000 | 24000 | — |

The caption says: "for IFBench, GEPA found optimal prompts after just 678 rollouts achieving 38.61%, outperforming GRPO's test set score of 35.88% with 24,000 rollouts."

### 7.3 Table 2: GPT-4.1-mini, test scores [paper:Table 2,p.7]

| GPT-4.1 Mini | HotpotQA | IFBench | HoVer | PUPA | AIME-2025 | LiveBench-Math | Aggregate | Improvement |
|---|---|---|---|---|---|---|---|---|
| Baseline | 38.00 | 47.79 | 46.33 | 78.57 | 49.33 | 58.20 | 53.03 | — |
| Trace (OptoPrime) | 60.33 | 51.19 | 46.00 | 74.18 | 45.33 | 60.74 | 56.30 | +3.27 |
| MIPROv2-No-Demos | 38.00 | 52.04 | 51.33 | 91.85 | 48.67 | 60.97 | 57.14 | +4.11 |
| MIPROv2 | 58.00 | 49.15 | 48.33 | 83.37 | 51.33 | 61.84 | 58.67 | +5.64 |
| TextGrad | 62.33 | 48.64 | 47.67 | 85.68 | 46.67 | 63.84 | 59.14 | +6.11 |
| GEPA | **69.00** | 52.72 | 51.67 | 94.47 | **59.33** | **64.13** | 65.22 | +12.19 |
| **GEPA+Merge** | 65.67 | **55.95** | **56.67** | **96.46** | **59.33** | **64.13** | **66.36** | **+13.33** |
| GEPA-Qwen-Opt (optimized with Qwen3-8B, evaluated on GPT-4.1-mini) | 65.67 | 49.83 | 54.67 | 90.05 | 52.67 | 59.31 | 62.03 | +9.00 |

### 7.4 Table 3: candidate-selection ablation, Qwen3-8B, "keeping the evolution harness fixed" [paper:Table 3,p.8]

| Qwen3 8B | HotpotQA | IFBench | HoVer | PUPA | Aggregate | Improvement |
|---|---|---|---|---|---|---|
| Baseline | 42.33 | 36.90 | 35.33 | 80.82 | 48.84 | — |
| SelectBestCandidate | 58.33 | 30.44 | 45.33 | 85.45 | 54.89 | +6.05 |
| BeamSearch (N=4) | 57.33 | 36.39 | 41.00 | 81.08 | 53.95 | +5.11 |
| GEPA (Pareto) | **62.33** | **38.61** | **52.33** | **91.85** | **61.28** | **+12.44** |

Figure 4 shows PUPA search trees. SelectBestCandidate "led to a local-optima after one iteration", while the Pareto version produced "a balanced search tree" [paper:Fig.4,p.8]. The caption names no model, but the figure's source files are `Papillon_PAPILLON_gpt-41-mini_GEPAWLinear.pdf` and `…_gpt-41-mini_GEPA.pdf`. That suggests the trees come from **GPT-4.1-mini** runs, not from the Qwen3-8B runs in Table 3 [paper-src:Fig.4 includegraphics paths][inferred].

### 7.5 Observations as stated [paper:§4 p.6–8; App.H p.25–26]
1. **Sample efficiency versus RL.**
   - GEPA beats GRPO "by up to 19pp while using up to 35× fewer rollouts" and "reaches optimal test performance with 4–35× fewer rollouts". It exceeds GRPO on 5 of 6 tasks, by 19.0, 2.73, 13.66, 5.19 and 0.7 pp (HotpotQA, IFBench, HoVer, PUPA, LiveBench-Math) [paper:Obs.1][inferred: column mapping from Table 1]. GRPO wins on AIME-2025, 38.00 vs 32.00.
   - It "matches GRPO's best validation after only 243, 402, 330, 1143, 1179, and 306 rollouts—up to 78× greater sample efficiency".
   - Counting train rollouts only, it needs "79 to 737 rollouts to reach optimal performance", and it matches GRPO's best validation with "102, 32, 6, and 179 train rollouts for four tasks".
   - GEPA+Merge beats GRPO by 21 pp at a comparable budget.
   - Check: 24,000 / 678 ≈ 35.4× [inferred].
2. **Instruction-only beats joint instruction and few-shot optimization.** GEPA beats MIPROv2 in every setting, "achieving margins as high as 11.1pp for GPT-4.1 mini and 12pp for Qwen3 8B" [paper:Obs.2,p.7].
   - The 11.1 pp is PUPA on GPT-4.1-mini (94.47 vs 83.37). The 12 pp is AIME-2025 on Qwen3-8B (32.00 vs 20.00) [inferred from Tables 1–2]. The pre-camera-ready source said "10.3%", which is the Qwen PUPA gap [paper-src].
   - The aggregate gains are more than double MIPROv2's: "+13.33pp and +12.19pp vs +5.64pp", all GPT-4.1-mini numbers. For Qwen3-8B the gap is +9.62 vs +2.61 [paper:Obs.2; Table 1].
   - Evolved instructions show a lower generalization gap, defined as test score minus best validation score [paper:App.J,Fig.15].
3. **Candidate selection matters.** Pareto beats BeamSearch by up to 11.33 pp and SelectBestCandidate by up to 8.17 pp. The aggregate margins are +7.33 and +6.4 pp (Table 3).
4. **Instruction prompts are cheaper.** GEPA prompts are "up to 9.2× shorter than those from MIPROv2", and in aggregate the higher-scoring optimizers produced shorter prompts [paper:App.H Obs.4]. The figures are in App.K. The Fig. 16 caption says GEPA's prompts are "around less than 33% of the size of MIPROv2's prompts" [paper:App.K,Figs.16–17].
5. **Merge helps, but not always.** "GEPA+Merge can outperform GEPA by as much as 5%" [paper:App.H Obs.5].
   - The 5 pp is GPT-4.1-mini HoVer, 56.67 vs 51.67 [inferred from Table 2].
   - *(Fact-check correction: the earlier "about 2 pp in aggregate" came from the pre-camera-ready LaTeX, not the camera-ready.)* Table 2 gives an aggregate of only +1.14 pp for GPT-4.1-mini (66.36 − 65.22). Table 1 gives −2.45 pp for Qwen3-8B (52.40 − 54.85) [inferred from Tables 1–2].
   - Merge "lead to performance degradation when used with Qwen3 8B. Even Qwen3 8B benefits from Merge on one out of four tasks": HotpotQA, 64.33 vs 62.33. The Qwen3-8B IFBench score falls to 28.23, below the 36.90 baseline.
   - The paper attributes this to "the way the rollout budget is allocated between reflective mutation and crossover, and the timing of invocation", with the same hyperparameters used for both models [paper:App.H Obs.5].
6. **Cross-model transfer.** Prompts optimized with Qwen3-8B give +9.00 on GPT-4.1-mini, "with gains as high as +27.67% on HotpotQA". This beats MIPROv2 (+5.64), TextGrad (+6.11) and Trace (+3.27), all of which optimized directly on GPT-4.1-mini [paper:Table 2; App.H Obs.6].
7. **Full-parameter GRPO.** On a 2-hop HoVer program, GRPO with full fine-tuning (SkyRL) shows "a comparable performance gap" to GEPA as LoRA GRPO does [paper:App.H,Fig.10,p.26].

### 7.6 Extended applications [paper:§5 p.8–9; App.E–F p.19–21; Table 4 p.87]
- **Inference-time search (D_val = D_train)**, all with GPT-4o:
  - NPUEval (AMD XDNA2 kernels), agent Sequential10:
    - Sequential10 alone: 4.25% mean vector utilization.
    - Sequential10 + RAG: 16.33%.
    - Sequential10 + MIPROv2: 19.03%.
    - GEPA: 30.52% mean, with some kernels reaching 70%.
    - A single GEPA prompt, without RAG: 26.85%.
  - KernelBench (35 tasks, V100), agent Sequential5: fast₁ rises from ≈0% to above 20%.
  - Generation stochasticity was removed "by operating under a cache".
- **Adversarial prompt search (inverted reward)**, GPT-5 Mini on AIME-2025, 30 problems × 5 runs: a trivia-laden "universal adversarial instruction" drops pass@1 from 76% to 10%. The evolution pool was AIME 2022–24.
  - Proposals were constrained so that prompts "do not contradict the task and still contain all information needed to solve it" [paper:App.F].
  - The authors' own manual inspection shows *why* scores dropped: GPT-5 Mini ended most responses with the literal placeholder `### <final answer>`. The drop is therefore a formatting-rule misreading triggered by the distractors, not degraded maths [paper:App.F].
- **Reflection LM calls per run** (Table 4, GPT-4.1-mini / Qwen3-8B):

| Benchmark | GPT-4.1-mini | Qwen3-8B |
|---|---|---|
| AIME-2025 | 24 | 90 |
| LiveBench-Math | 34 | 38 |
| HotpotQA | 69 | 64 |
| IFBench | 21 | 17 |
| HoVer | 92 | 50 |
| PUPA | 46 | 38 |

### 7.7 `optimize_anything` results (submission draft; not the ICLR paper) [oa-paper:Table 1, §4]
- **Coding-agent skills (Bleve):**
  - Haiku 4.5: 79.3 → 98.3% pass rate.
  - Sonnet 4.5: 94.8 → 100%.
  - Resolution time falls by 47%.
- **Cloud scheduling (ADRS):**
  - CloudCast: 40.2% cost savings versus Dijkstra.
  - Can't Be Late: 7.8% savings.
- **ARC-AGI agent architecture (Gemini 3 Flash):** 32.5 → 89.5% test accuracy.
- **AIME prompt (GPT-4.1-mini, proposer GPT-5):** 46.67 → 60.00%.
- **KernelBench, multi-task mode (31 problems):** 87% of kernels match or beat PyTorch.
- **Circle packing, n = 26:** score 2.63598+, "outperforming AlphaEvolve's … reported solution".
- **Blackbox optimization (56 EvalSet problems, single-task mode):** with 8,000 evaluations per problem, versus Optuna it has 7 wins, 40 ties and 9 losses. On 10 selected problems at 2,000 evaluations it wins 7 of 10 [oa-paper:App.A].
- **ASI ablation (Facility Support Analysis):** the arms are per-aspect **sub-scores** plus the aggregate, versus the aggregate score only. It is not an ablation of free-text feedback. With sub-scores, validation reaches 0.80 within 100 rollouts, against about 600 without; final test 86.32 vs 82.5 [oa-paper:§4.9].
- **Multi-task vs single-task ablation:** on 10 KernelBench problems, multi-task search wins at every speed-up threshold [oa-paper:§4.8]. The draft picks "the 10 problems where multi-task mode performed best" and re-runs them in single-task mode with an equal per-problem budget. This selection favours the multi-task arm, so the comparison is biased [inferred].
- The repo README reports GPT-4.1 Mini on AIME 2025 going from 46.6% to 56.6% with `max_metric_calls=150` and reflection LM `openai/gpt-5` [code:gepa/README.md].

---

## 8. Known weaknesses and critiques

1. **Only the prompts change.**
   - Tools, memory and control flow are fixed in the paper setting [doc].
   - Even the component set is fixed at the seed.
   - Structural change needs the `optimize_anything` framing, where code is text [code:gepa_launcher.py][inferred].
2. **It depends on the metric and the feedback.**
   - "It needs a good metric, and works best when feedback includes text explaining failures" [doc].
   - The evidence for the text part is weaker than it looks. The ICLR paper never ablates feedback text (§7.1). The optimize_anything ASI ablation (86.32 vs 82.5) shows that per-aspect **sub-scores** help, not free-text explanations [oa-paper:§4.9].
   - With binary scores, a 3-example minibatch gives a very coarse acceptance signal [inferred]. The repo's own ConfidenceAdapter post names this problem: "the optimizer gets no gradient signal" from 0/1 scores [docs:blog/2026-03-17-confidence-adapter-benchmark].
3. **Overfitting and selection bias.**
   - "With few examples it can still overfit; keep a separate validation set" [doc].
   - The returned candidate is the argmax over many candidates on D_pareto, which gives winner's-curse inflation. The skill's example is a selected best of 0.87 against a runner-up of 0.60 and a median of 0.57 [skill:gotchas.md §2].
   - Web-search snippets from independent 2026 papers report prompts that underperform the seed on small training sets and "prompt bloat" [secondary: WebSearch snippets only; full texts not accessible; unverified].
4. **Test-specific content leaks into prompts.**
   - The meta-prompt explicitly says "Identify all niche and domain specific factual information about the task and include it in the instruction" [paper:App.C][code:strategies/instruction_proposal.py]. Early prompts "contain training example content", and GEPA can copy "specific keywords or phrases" (the Dropbox case) [docs:guides/faq.md].
   - The mitigation is a constraint added through the feedback. There is no built-in leakage critic [inferred; contrast RRSI].
5. **Task drift.** The reflection LM can change the task definition, for example a 1–5 rating scale to 1–3 or the output schema [docs:guides/faq.md].
6. **Reward hacking.** A correctness-only KernelBench score drove GEPA to a prompt that wraps the reference implementation: pass@1 ≈ 0.87 with no real speed-up [skill:gotchas.md §1].
7. **Saturation.** If the seed already solves the training minibatches, every proposal is rejected and the seed comes back unchanged [skill:gotchas.md §9].
8. **Noise.** Acceptance is a single strict comparison on 3 stochastic rollouts, and N = 1 per evaluation by default [skill:gotchas.md §3][inferred]. There is no noise margin, unlike RRSI's δ floor. The paper reports single runs without error bars [inferred from Tables 1–3].
9. **Most of the budget goes to validation.** This is stated by the authors [paper:Obs.1]. Their suggested fixes are smaller or dynamically chosen validation subsets.
10. **Merge is fragile.**
    - It depends on hyperparameters and hurt Qwen3-8B: IFBench fell to 28.23, against 38.61 for GEPA, and the aggregate fell by 2.45 pp [paper:Table 1, App.H].
    - Its conditions are strict, so "merge occurs sparsely" [paper:App.D.1].
    - In the reference code, the "max 5 invocations" limit is a soft scheduling cap. Merges that were due can accumulate and all fire later, so the number of accepted merges can exceed the cap [run:merge-cap-probe]. This interacts with the paper's own diagnosis that merge *timing* drives the Qwen degradation [inferred].
    - **Crash edge case** [run:merge-zero-weight-probe]:
      - The ancestor is drawn with `rng.choices(weights=agg)`. If every eligible common ancestor has validation aggregate 0.0, Python raises `ValueError`.
      - This is plausible on hard tasks where the seed and early accepted children score 0 on D_pareto.
      - The error fires before any metric call in the iteration, so the engine re-raises regardless of `raise_on_exception`.
      - Direct reproduction: `sample_and_attempt_merge_programs_by_common_predictors` with seed agg 0.0 and two single-module children raises. With seed agg 0.1 it returns the merged candidate.
      - A reimplementation should fall back to uniform weights [inferred].
    - Merge acceptance is decided on 5 **validation** ids whose parent scores are already known. So D_pareto scores gate merges directly, not just selection [code:proposer/merge.py:select_eval_subsample_for_merged_program][inferred].
11. **The per-key frontier ignores interior trade-offs.**
    - A candidate that is good across the board but never best on any key is never selectable [docs:guides/candidate-selection.md].
    - The code's set-cover pruning can drop a low-aggregate generalist in favour of specialists [run:pareto-probe].
12. **Reflection does not scale with the minibatch.** "Context overload": raising the reflection batch from 3 to 100 dropped Formula accuracy from 87.0% to 72.5%, which motivated ComBEE map-shuffle-reduce reflection [docs:blog/2026-04-09-gepa-at-scale-with-combee].
13. **The comparison baselines are narrow.**
    - GRPO used LoRA with a fixed 500 steps, and the authors report exploring "several values" of learning rate, β and clipping [paper:App.G.4]. They partly address the LoRA objection with a single 2-hop HoVer run using full-parameter GRPO (Fig. 10).
    - The budget was matched to MIPROv2, not to GRPO.
    - GRPO still wins on AIME-2025 [paper:App.G.4, Table 1].
    - The overview's "about 6 points on average" is the Qwen3-8B aggregate (+5.94) [inferred: 54.85 − 48.91]. The older v1 abstract in `CITATION.cff` says "Across four tasks … by 10% on average", a version drift readers may see quoted [code:gepa/CITATION.cff].
    - "Beats MIPROv2 by over 10 points" is a per-benchmark maximum (11.1 / 12 pp). The aggregate margins are 6.6–7.7 pp (GPT-4.1-mini) and 7.0 pp (Qwen3-8B) [inferred from Tables 1–2].
14. **Budget overshoot.** In `gepa.optimize`, stop conditions are checked only between iterations, so the run can exceed B by up to 2b + |D_pareto| [run:toy-e2e][run:overshoot-probe]. The new `optimize_anything` eval server instead hard-stops mid-iteration and discards the partial iteration (§3). With `cache_evaluation` on, `max_evals` counts only cache misses and a converged search can spin [skill:gotchas.md §7].
15. **Limited reproducibility and resume fidelity in the reference code.**
    - Iteration ids come from unseeded `uuid4`, so `run_log.json` and the `iterations/` directory names differ across runs with the same seed [code:core/state.py:new_iteration_id].
    - On resume, `random.Random(seed)` is recreated from scratch and the batch sampler's epoch and shuffle state is not saved in `gepa_state.bin`. A resumed run therefore does **not** retrace the uninterrupted run [code:api.py:optimize (rng = random.Random(seed)); code:core/state.py (no RNG fields)][inferred].

---

## 9. Reproduction blueprint (domain-agnostic, CPU-only)

### 9.1 Components and interfaces

The names are proposals [inferred]. Their behaviour is specified in §§3–6. Where possible they share types with the RRSI, Dream-RSI and autoresearch specs (`LLMClient`, `Evaluator`, a tree-shaped experiment log).

```python
Candidate = dict[str, str]                                     # component name -> text  [code:core/adapter.py]

# --- the only domain-specific part: the system under optimization + its grader ---
class TaskSystem(Protocol):                                    # == GEPAAdapter
    def evaluate(self, batch: list[Any], cand: Candidate, capture_traces: bool) -> EvalBatch: ...
    def make_reflective_dataset(self, cand: Candidate, ev: EvalBatch,
                                comps: list[str]) -> dict[str, list[dict]]: ...  # {"Inputs","Generated Outputs","Feedback",...}
    propose_new_texts: ProposalFn | None = None                # optional override of reflection
@dataclass
class EvalBatch: outputs: list; scores: list[float]; trajectories: list | None
                 objective_scores: list[dict[str, float]] | None = None; num_metric_calls: int | None = None
class FeedbackFn(Protocol):                                    # μ_f; score + text, optionally per module and per objective
    def __call__(self, example, output, trace) -> Feedback      # Feedback(score, text, module_text: dict, objectives: dict)
class SimpleEvaluatorSystem(TaskSystem):                       # optimize_anything wrapper: evaluate(candidate[, example]) -> (score, side_info)
    modes: Literal["single_task", "multi_task", "generalization"]; capture_log: bool
class DataLoader(Protocol): def all_ids(self) -> list; def fetch(self, ids) -> list; def __len__(self) -> int

# --- method components ---
class LLMClient(Protocol): def __call__(self, prompt: str | list[dict]) -> str   # ClaudeCLI(`claude -p`), MockLLM
class ReflectionProposer:                                      # §6.1–6.2; template must contain <curr_param>, <side_info>
    template: str | dict[str, str]
    def render(self, current_text: str, records: list[dict]) -> str              # markdown "# Example n / ## key / ###…"
    def parse(self, raw: str) -> str | None                                        # fenced; reject known-truncated
    def propose(self, cand, refl_ds, comps) -> tuple[dict[str, str], dict]        # new texts + prompts/raw outputs
class FrontierTracker:                                         # instance | objective | hybrid | cartesian  (§4.3)
    def update(self, cand_idx, val_scores: dict, obj_scores: dict | None) -> FrontDelta
    def mapping(self) -> dict[Key, set[int]]
class CandidateSelector(Protocol): def select(self, state) -> int
    # Pareto (set-cover pruning + frequency sampling, §3), CurrentBest, EpsilonGreedy(0.1), TopKPareto(5),
    # BeamSearch(N=4)  <- must be implemented for the Table 3 ablation (absent from the repo) [inferred semantics]
class ComponentSelector(Protocol): def __call__(self, state, cand_idx) -> list[str]   # RoundRobin (per-candidate ptr), All
class BatchSampler(Protocol): def next_ids(self, loader, iteration: int) -> list      # EpochShuffled(b, pad least-frequent)
class AcceptanceCriterion(Protocol): def accept(self, proposal, state) -> bool
    # StrictImprovement (Σ after > Σ before), ImprovementOrEqual; extension: NoiseMargin(δ) shared with RRSI
class EvaluationPolicy(Protocol):                              # FullEval (default); extension: subset/dynamic valset
    def val_ids(self, loader, state) -> list; def best(self, state) -> int
class MergeProposer:                                           # §3 MERGE, Alg. 3–4; max_invocations=5, overlap_floor=5, subsample=5
    cap_mode: Literal["reference_soft", "hard", "accepted"] = "reference_soft"   # soft = gepa code (cap gates
                                                               # scheduling only); hard = paper wording "invoked a
                                                               # maximum of 5 times" (built+scored merges <= cap);
                                                               # accepted = caps accepted merges only (not the paper)
    def propose(self, state) -> Proposal | None
class ReflectionStrategy(Protocol):                            # == ReflectionLM; Stateless (default) | ComBEE(map/reduce, k=⌊√n⌋)
    def reflect(self, cand, refl_ds, comps) -> tuple[ReflectionProposal, "ReflectionStrategy"]
class RolloutCounter:                                          # metric calls; hooks; per-phase breakdown (seed/minibatch/val/merge)
class Stopper(Protocol): def __call__(self, state) -> bool     # MaxRollouts, MaxReflectionCost, Timeout, ScoreThreshold,
                                                               # NoImprovement(k), File, Composite(any|all)
class EvalCache:                                               # key = (sha256(sorted(cand.items())), split, example_id)
class SearchState:                                             # §5 GEPAState fields; JSON+pickle; resume; tree export
    candidates; parents; val_scores; objective_scores; frontier; rr_ptr; trace: list[dict]
    rng_state; sampler_state; merge_counters                   # NOT persisted by gepa (weakness 15); persist them so
                                                               # resume == uninterrupted run [inferred]; seed-derived iteration ids
class GEPAEngine:                                              # §3 loop; callbacks; run_dir layout; deterministic given seed
    def run(self) -> Result
class Result: candidates; parents; val_aggregate; best_idx; best_candidate; frontier; total_rollouts; test_scores
class TestReporter:                                            # scores seed & best on a sealed test split, outside the budget

# --- baselines needed to test the paper's claims ---
class ScalarRLBaseline:        # GRPO-style group-relative policy gradient on a parameterized prompt/policy (scalar reward only)
class ScoreOnlyReflection:     # same engine, but the reflective dataset contains only scores (ASI / text-feedback ablation)
class FewShotDemoOptimizer:    # "MIPRO-lite": bootstrap successful demos + instruction candidates, random/TPE search [inferred]
class BestOfN:                 # N independent rewrites of the seed, keep the best on val (baseline in the repo's skill)
```

Design notes [inferred]:
- **Keep the pure parts separate and deterministic.** Make `FrontierTracker`, the Pareto pruning/sampling, `EpochShuffledBatchSampler`, `MergeProposer`'s triplet logic and `RolloutCounter` pure functions of a seeded `random.Random`. Unit-test them against the vectors in §4.7. The expected budget identity for a run is `evals = |V| + 2b·#iters_with_child + b·#skipped_after_parent_eval + |V|·#accepted + merge terms`.
- **Keep the frontier module generic.** Expose `FrontierTracker` plus `ParetoSelector` as a reusable "diversity-preserving parent selector" that other methods can plug in. Examples are an RRSI candidate pool, or a Dream-RSI exploration policy choosing which branch to extend [doc "The busiest ring is the agent"].
- **Offer RRSI's guards as options.** Provide `NoiseMargin(δ)` acceptance and a leakage-critic hook as optional `AcceptanceCriterion` / pre-evaluation filters. They are off by default, so that "faithful GEPA" and "guarded GEPA" can be compared.
- **Precedent for one front door over several methods.** The reference repo's new `gepa.optimize_anything.optimize_anything(seed, evaluator, dataset, valset, test_set, config=OptimizeAnythingConfig(engine=...))` already runs four engines behind one budgeted eval server: `gepa`, `autoresearch`, `meta_harness` and `best_of_n` [code:gepa/src/gepa/oa/engines/*, oa/eval_server.py, oa/budget.py].
  - The server owns the eval-call ledger with a hard cap. Proposer spend is a separate cap (`max_token_cost`) enforced by each engine.
  - `test_set` is scored outside the budget, for both seed and best.
  - This is a tested template for the rsi framework's cross-method `Evaluator` / `Budget` / `TestReporter` layer [inferred].

### 9.2 CPU-only experiment design

The constraints are: no GPU, `claude -p` available as an LLM, and a deterministic mock. Every experiment below logs each candidate as a tree node (parent(s), changed component, minibatch before and after, validation vector, rollouts at discovery). Every experiment reports paired bootstrap CIs over seeds.

**Tier 1: "RuleWorld", a synthetic compound system with fully mocked LLMs (seconds per run; 50+ seeds).**
- **Latent lessons.** There are K ≈ 16 latent lessons ℓ in 4 families. Some lessons are module-specific: module A is a "query writer" and module B an "answerer".
- **Examples.** Each example x requires a set R_A(x) and a set R_B(x). There are three families of lesson:
  - *Conflicting* lessons: family-1 rule r₁ helps family 1 but breaks family 2 unless the conditional variant r₁|fam is present. This creates a local optimum.
  - *Specific-only* lessons: tokens tied to single examples, which help only those examples. This is the overfitting lure.
  - *Distractor* lessons.
- **Mock task LM.** For each module, it succeeds iff the module instruction contains every lesson in R_·(x) and no active conflict. It slips with probability ε, which gives noisy rollouts.
- **Feedback function μ_f**, at three levels:
  - `rich`: names the violated lesson for the module at fault. This is the analogue of HoVer's "documents remaining".
  - `symptom`: describes the failure but does not name the lesson.
  - `score_only`.
- **Mock reflection LM.**
  - It reads the rendered `<side_info>` and appends at most c lessons it can identify.
  - With probability q it copies an example-specific token. This models the meta-prompt's "include niche facts" behaviour.
  - With probability r it drops a line.
  - Under `score_only` it adds a random vocabulary lesson.
  - It only sees what is in the prompt, so the information flow is honest.
- **Ground truth.** Expected validation and test scores are computed analytically, so the true test score is exact.
- **Scalar-RL baseline.** A GRPO-style update on independent Bernoulli inclusion probabilities per (module, lesson), with group size 12, 4 instances per step and learning rate swept. It uses the same rollout accounting.

**Tier 2: real reflection with `claude -p` and deterministic graders** (minutes per run; 3–5 seeds).
- **(a) IFBench-like constraint following.**
  - The system has two modules, answer then rewrite, as in the paper's IFBench system. The task LM is `claude -p` with a small or fast model.
  - Around 25 programmatic output-constraint checkers (word counts, keyword frequency, forbidden characters, format) give text feedback listing the constraints satisfied and violated.
  - Train, validation and test use disjoint constraint types, so the test is OOD.
  - Sizes: train 30, val 20, test 60; b = 3; B ≈ 300–600 rollouts.
- **(b) Code inference-time search.**
  - 10–20 small Python function tasks, with CPU unit tests and timing as ASI.
  - D_train = D_val = the task set. The candidate is either the code itself or a "kernel-writing prompt".
- The mock LLM stands in for either role in CI.

| # | Claim (source) | Measure | Arms | Confirming outcome (qualitative) |
|---|---|---|---|---|
| E1 | Reflective evolution is far more rollout-efficient than scalar-reward RL [paper:Obs.1][doc] | rollouts to reach X% of the best achievable test score; test score at fixed budgets | GEPA vs ScalarRLBaseline (Tier 1); GEPA vs ScoreOnlyReflection (Tier 2a) | GEPA reaches the target with ≥ 10× fewer rollouts in Tier 1; its curve dominates at every budget |
| E2 | Text feedback (evaluation traces / ASI) is the key signal. This is argued in [paper:§3] but never ablated there; [oa-paper:§4.9] ablates only sub-scores; [doc "works best when feedback includes text"]. So E2 tests a claim the primary sources did not test directly | validation curve vs rollouts; final test | μ_f ∈ {rich, symptom, score_only} | rich > symptom > score_only in both speed and final score |
| E3 | Pareto selection escapes local optima that greedy selection hits [paper:Table 3, Fig.4][doc "keep every candidate best on at least one example"] | final test; tree shape (depth, branching, number of distinct lineages); fraction of iterations spent on the top candidate | Pareto vs CurrentBest vs BeamSearch(4) vs EpsilonGreedy vs TopKPareto; with the conflicting-lesson world | Pareto has the highest final score and a balanced tree; CurrentBest stalls after its first improvement |
| E4 | Merge combines complementary lessons from different lineages [paper:App.D.1, Obs.5][doc "combine complementary lessons"] | score of accepted merges vs their parents; how often merges are found; final test | GEPA vs GEPA+Merge; world where modules A and B improve in separate lineages; vary merge timing and max invocations; `cap_mode` ∈ {reference_soft, hard} | merged children beat both parents when lineages are module-disjoint; the gain vanishes or turns negative when merge fires early or lineages overlap (reproducing the Qwen degradation qualitatively); under reference_soft, the count of accepted merges can exceed the cap [run:merge-cap-probe] |
| E5 | The minibatch gate is cheap and most of the budget goes to validation [paper:Obs.1] | split of rollouts into seed / minibatch / validation / merge; false-accept and false-reject rates of the 3-example gate against ground truth | b ∈ {1, 3, 5, 10}; strict vs ≥; optional NoiseMargin(δ) | validation share > 50%; larger b lowers false accepts but spends more per iteration; there is a best b for the budget |
| E6 | Overfitting with few examples; a separate validation set helps [doc watch-out][paper:App.J] | test − validation gap; prompt tokens that are example-specific; test score | \|train\| ∈ {6, 15, 30, 60}; D_pareto = held-out val vs D_pareto = D_train; copy rate q | small train sets with a shared train/val split show a larger gap and more copied tokens; a separate validation set shrinks the gap |
| E7 | Instruction-only optimization beats few-shot demo optimization and gives shorter prompts [paper:Obs.2, Obs.4][doc "beat MIPROv2"] | test score; prompt length; generalization gap | GEPA vs FewShotDemoOptimizer (MIPRO-lite), both on Tier 2a | GEPA has an equal or higher test score with markedly shorter prompts |
| E8 | Learned prompts transfer across models [paper:Obs.6] | test score of prompts optimized on one task LM and run on another | Tier 1: two mock task LMs with different slip and conflict profiles; Tier 2a: two `claude -p` models | positive transfer gain; prompts with more example-specific tokens transfer worse |
| E9 | Module-level credit assignment from traces [paper:§3 "implicit credit assignment"] | the changed module vs the module actually at fault; rollouts to fix | module-specific vs system-level feedback; RoundRobin vs All | module-specific feedback fixes the faulty module faster; `All` is more rollout-efficient when faults are spread across modules [docs:faq] |
| E10 | Inference-time search for code works [paper:App.E][doc "promise as a search strategy for optimizing code"] | fraction of tasks passing tests and faster than the reference, vs budget | D_train = D_val (multi-task) vs independent single-task runs with the same per-task budget vs BestOfN | the multi-task arm solves more tasks per budget, showing cross-task transfer [oa-paper:§4.8]. Fix the task subset *before* running either arm, to avoid the oa-paper's post-hoc "10 best multi-task problems" selection. Note that \|D_pareto\| = 1 per single-task run disables merge and the instance frontier (§3, §4.3) |
| E11 | The same engine does adversarial search when the reward is inverted [paper:App.F] | task accuracy under the evolved "universal" instruction | inverted μ with a task-preservation constraint in the feedback | accuracy falls far below the clean prompt while the task still reads as intact |
| E12 | Saturation returns the seed [skill:gotchas §9] | accepted/proposed ratio | a seed that already solves all training examples | ≈ 0 accepted; the seed is returned (sanity check) |
| E13 | Deterministic, resumable, auditable. This is a design goal for rsi. The reference code does not fully meet it: unseeded uuid4 iteration ids, and RNG and sampler state not persisted (weakness 15) [code:state.py][inferred] | identical trees across reruns with the mock; resume after a kill | seed-fixed reruns; kill then resume | identical candidate sequence and scores, and a bit-identical `run_log.json` once ids are seed-derived, under the mock; the resumed run reaches the same final state *only if* RNG and sampler state are persisted, which is our extension |

What to report for every arm [inferred]:
- Test score of the seed and of the best candidate, with the test split sealed from the engine.
- Validation score of the best candidate and the winner's-curse gap.
- Rollouts, split by phase.
- Number of reflection calls and their tokens.
- Number of accepted and proposed candidates.
- Frontier size over time.
- Final prompt length.

---

## 10. Capability checklist: overview claims mapped to components

| # | What the overview [doc] attributes to GEPA | Component(s) that satisfy it |
|---|---|---|
| 1 | Tunes "the prompts inside an AI system", which "can contain several prompts" | `Candidate` (component name → text) + `TaskSystem` adapter + `ComponentSelector` (RoundRobin/All) |
| 2 | Runs the system "on a handful of examples" and keeps "the full traces: reasoning, tool calls and tool outputs" | `BatchSampler` (EpochShuffled, b = 3) + `TaskSystem.evaluate(capture_traces=True)` → `EvalBatch.trajectories` → `make_reflective_dataset` |
| 3 | "An AI reflects on those traces in plain language, diagnoses the problems, and proposes better prompts" | `ReflectionProposer` (the meta-prompt of §6.1 verbatim, markdown rendering, fenced parsing) + `LLMClient` (ClaudeCLI / MockLLM) |
| 4 | "A score tells you that something failed; the trace tells you why" (evaluation traces as feedback) | `FeedbackFn` μ_f (score plus text, per module) / `SimpleEvaluatorSystem` side_info; experiment E2 |
| 5 | "Test the new prompts" | minibatch re-evaluation plus `AcceptanceCriterion` (StrictImprovement), then full validation evaluation (`EvaluationPolicy.FullEval`) |
| 6 | "Keep every candidate that is best on at least one example (a 'Pareto frontier'), so different strengths survive" | `FrontierTracker` (instance keys; optionally objective / hybrid / cartesian) + Pareto `CandidateSelector` (pruning + frequency sampling); E3. Precision: *every* candidate stays in the pool (it is never deleted), but only frontier members that survive set-cover pruning are *selectable* as parents. A tie-covered winner can be pruned (§3, [run:pareto-probe]) |
| 7 | "Combine complementary lessons from different candidates, then repeat" | `MergeProposer` (system-aware crossover, Alg. 3–4, with `cap_mode` for the soft vs hard 5-merge cap) + lineage accumulation in `SearchState.parents`; `GEPAEngine` loop; E4. Note that merge is off in plain "GEPA" and on only in "GEPA+Merge" [paper:App.G.4] |
| 8 | Name = Genetic-Pareto | documentation; the genetic tree (`SearchState.parents`) + Pareto selection |
| 9 | "Beat reinforcement-learning fine-tuning (GRPO) by about 6 points on average and up to about 20, while using up to 35 times fewer trial runs" (six tasks) | `RolloutCounter` + `ScalarRLBaseline` + budget-matched comparison harness; E1 (qualitative analogue); reported numbers in §7.2. The paper's own figures are "6 percentage points on average and by up to 19pp" (abstract). GEPA+Merge reaches 21 pp on HotpotQA [paper:abstract; Obs.1] |
| 10 | "Beat MIPROv2 … by over 10 points, including a 12-point gain on AIME-2025" | `FewShotDemoOptimizer` (MIPRO-lite) baseline; E7. The 12 pp is Qwen3-8B AIME, 32.00 vs 20.00 (§7.2) |
| 11 | "Showed promise as a search strategy for optimizing code" | inference-time / multi-task mode (D_train = D_val) in `GEPAEngine` + `SimpleEvaluatorSystem` modes; E10 |
| 12 | Watch out: "It changes prompts only, not tools, memory or control flow" | `Candidate` fixed at the seed's component set (documented boundary); an optional code-as-text candidate via `SimpleEvaluatorSystem` to relax it |
| 13 | Watch out: "It needs a good metric, and works best when feedback includes text explaining failures" | `FeedbackFn` contract + E2 (μ_f ablation) + E12 (saturation); reward-hacking guidance (gated scores) |
| 14 | Watch out: "With few examples it can still overfit; keep a separate validation set" | separate `DataLoader`s for D_train / D_pareto / test + `TestReporter`; E6 |
| 15 | Comparison table: learns from "plain-language reflection on full traces" | `ReflectionProposer` + trajectories → reflective dataset |
| 16 | Comparison table: avoids overfitting by "keeps candidates that each win somewhere" | `FrontierTracker` + Pareto selector (plus the sealed test set, which the overview recommends anyway) |
| 17 | Comparison table: main goal "quality from few trial runs"; headline "+6 points over RL on average, up to 35× fewer runs" | `RolloutCounter`, budget stoppers (`MaxRollouts`), E1 |
| 18 | "Built into DSPy"; "a standard optimizer in DSPy"; "if you already use DSPy, GEPA is the easiest start: it can optimize an existing pipeline's prompts with a few examples" | `TaskSystem` adapter layer (a DSPy adapter is optional and out of CPU scope) + the `optimize(seed, trainset, valset, …)` quick-start API |
| 19 | Glossary: "Rollout: one complete run of an AI system on a task" | `RolloutCounter` (one metric call = one (candidate, example) evaluation) |
| 20 | Glossary: "Pareto frontier: the set of candidates that are each best on at least one example" | `FrontierTracker.mapping()` |
| 21 | "GEPA keeps a diverse set of candidates that each win somewhere" (its anti-overfitting strategy, contrasted with Meta-Harness, SoL-Pi and RRSI) | Pareto selector; optional RRSI guards as pluggable `AcceptanceCriterion` (NoiseMargin) and a pre-evaluation leakage filter for the cross-method comparison |
| 22 | Related work: Hermes Agent "tunes its skills with DSPy and GEPA"; DSPy with GEPA and MIPROv2 | skills are text components → `SimpleEvaluatorSystem` generalization mode (cf. gskill [docs:blog/2026-02-18-automatically-learning-skills-for-coding-agents]) |
| 23 | General practices: "Keep a set the loop never sees", "Log experiments as a tree", "Never let the loop grade itself", "Measure your noise first" | `TestReporter` (sealed test); `SearchState` tree export (parents, deltas, rollouts at discovery); grader lives in `TaskSystem`, outside the proposer; optional `NoiseMargin` fed by repeated seed evaluations |
