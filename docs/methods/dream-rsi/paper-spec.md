# Dream-RSI: Recursive Self-Improvement through Evolving Worlds

Method spec for the `rsi` framework.

Zheng, Wu, Zhang, et al. (Google, UMD, Google DeepMind, UVA), arXiv 2609.14858. **Cited version: v2, 6 October 2026** (this file refreshed 25 October 2026). It was first written from v1 (14 September 2026); text only v1 has is tagged `[paper-v1:...]`.

## Source tags and provenance

| Tag | Meaning |
|---|---|
| `[paper:<loc>]` | arXiv 2609.14858**v2** (6 Oct 2026), read in full from its arXiv HTML text, including Appendices A–E. The HTML has no page numbers, so locations are `§` (section), `Eq.`, `Fig.`, `Table`, `App.`, and `L1:n`/`L2:n` = line *n* of prompt Listing 1/2 (App. D.1/D.2). |
| `[paper-v1:<loc>]` | v1 (14 Sep 2026): the PDF in `github.com/zhengkid/Dream-RSI` (`papers/Dream-RSI.pdf`, 36 pages, CreationDate 2026-09-14; text extracted to `scratchpad/papers/dream-rsi.txt`, Appendices A–C) and the v1 arXiv HTML. Used only for text v2 no longer has; `p.` = v1's printed page. |
| `[paper:Fig.X, digitized]` | A number read from the **v1 PDF**'s vector plot data with `pdfplumber` (marker coordinates mapped linearly from the axis ticks; endpoints matched printed numbers to within about 0.1%). X is the v2 figure number. v2's figures show the same runs (the printed labels, endpoints and ratios are unchanged); the points were not re-digitized from v2. Approximate. |
| `[code:zhengkid__Dream-RSI/<path>]` | The official repo. It currently holds only the README, CITATION.cff, assets and the PDF. **The official code is not released** (README release plan: "Full codebase ⏳ Being prepared", "Reproduction scripts ⏳ Being prepared", "Discovered programs ⏳ Being prepared"). |
| `[doc]` | The user's overview HTML ("Four ways AI is learning to improve itself"). |
| `[inferred]` | My own reconstruction or suggestion. It is not stated in any source. |

**v1 → v2.** Listings 1–2 have the same text and line numbers in both versions, so `L1:n`/`L2:n` tags carry over. Renumbered: experiments are reordered to §4.1 kernels (v1 §4.3), §4.2 math, §4.3 Lasso (v1 §4.1), new §4.4 harness; v1 §5.1/§5.2 are now §5; Fig. 3 (Lasso) → Fig. 4, Fig. 4 (kernels) → Fig. 3, Fig. 5 (guidance) → Fig. 6, Fig. 6 (pacing) → Fig. 7; App. A (tasks) → App. B.3, App. B.1/B.2 (prompts) → App. D.1/D.2, App. C (solver) → App. E.1. Content changes: **Eq. 1 is new** (§4.1 here; it now matches Listing 2's beta-sweep reward, §4.3); §3 defines `s_r = 0`, sweeps a budget preference ρ, fixes v1's M off-by-one and drops "replay resets the policy's per-rollout state"; **Table 3 / App. B.2 give T, W, B and M** (§4.8); new are the harness task (§7.5), the history-accumulation ablation (Table 2, §7.6), a PACEvolve kernel baseline (App. C), hardware (App. B.1) and the ConvDiv kernel (App. E.2); claims are softened ("9 tasks in 4 domains", "competitive quality", "in several settings", "consistently" dropped, math "two of three tasks", guidance "may constrain exploration", "a large number" for v1's "thousands").

**Fact-check pass (of v1, 2026-09-24).** A second reviewer checked every formula, quote, default, table value and page reference in this file against `dream-rsi.txt`, the PDF, the repo files and the overview HTML (including its demo JavaScript). The reviewer also re-digitized v1's Fig. 3b, Fig. 4 and Fig. 5 (v2 Fig. 4b, 3 and 6) independently from the PDF vector paths, calibrated on the gridlines. The re-digitized values agree with the tables below to within about ±0.005 (1/ms), ±1 ms and ±1 call. Corrections and additions from that pass are folded into the text, and gaps are marked `[inferred]` or `[unverified]`.

**arXiv ID provenance.** `2609.14858` comes from the README BibTeX [code:zhengkid__Dream-RSI/README.md] and from the overview doc ("arXiv 2609.14858"; "14 September 2026 Dream-RSI appears on arXiv") [doc]. The same repo still shows an "arXiv – coming soon" badge and "arXiv posting 🔜 In progress". `CITATION.cff` has `date-released: 2026-09-11` and a TODO placeholder `arXiv:XXXX.XXXXX` [code:zhengkid__Dream-RSI/CITATION.cff]. The PDF's CreationDate is `D:20260914002109Z`. A third-party note in `scratchpad/secondary/orze.md` links `arxiv.org/html/2609.14858v1` and dates it 09-14. v1 does exist ("arXiv:2609.14858v1 [cs.CL] 14 Sep 2026"), and v2 is "arXiv:2609.14858v2 [cs.CL] 06 Oct 2026"; that the repo PDF is byte-identical to v1 is still `[unverified]`. On 2026-09-24 the reviewer re-fetched the README from raw.githubusercontent.com and it was unchanged: the code is still unreleased.

Sources I could not reach in September 2026: the dream-rsi.com project page and interactive demo, arXiv, alphaXiv and HuggingFace (all blocked by the egress proxy; the fact-check re-test got connection failures). The v1 and v2 arXiv HTML texts used for the October refresh were obtained separately. I also could not get secondary write-ups (hyper.ai, dejan.ai, aiwiki.ai, all blocked), or the internal `see.policy.*` package that the App. D.2 (v1 App. B.2) prompt refers to (not public). PR #5 on the official repo lists unofficial community reimplementations: `patrykorwat/open-dream-rsi`, `TheAstrayDev/dream-rsi-sdk` and `robinber/dream-rsi-spark`. Web search also turned up `juanmackie/pi-Dream-RSI` and `DumboDhruvi/DreamLocalRsi`. **I used none of these as a source for any fact below.**

---

## 1. The idea in one paragraph (101)

AI discovery systems such as AlphaEvolve and SimpleTES improve a program or a mathematical construction by running a coding agent thousands of times. Each run is a propose → evaluate → refine step. How well they do depends heavily on the **exploration policy**: which candidates to keep refining, how many to run in parallel, when to open a new direction and when to stop [paper:§1; doc]. Improving that policy is expensive, because you only find out whether a policy is good after a long, costly discovery run [paper:§1].

Dream-RSI's insight is that **a finished discovery run is already a simulator**. The run is recorded as a *discovery tree*: every attempt, the attempt it started from, and its evaluated score. A different policy can be tested on that tree by walking it differently: other branches, another order, other parallel groupings, another stopping point. Each outcome is simply *revealed* from the record, so no new LLM calls and no new evaluations are needed [paper:§2, Fig.2].

The paper illustrates the payoff this way: "a large number of candidate policies can be tested inside it … one expensive online run can yield a large number of fast, low-cost off-policy evaluations" [paper:Fig.2 caption]. v1 said "Thousands of candidate policies" and "thousands of rapid, zero-execution-cost off-policy evaluations", and labelled the replay feedback "Quality ↑; Cost ↓; Latency ↓" [paper-v1:Fig.2 caption, p.4].

The machinery is "a lightweight orchestration layer" that "makes exploration explicit and programmable" and "controls branching, parallelism, and stopping while leaving the underlying base agent unchanged" [paper:Abstract; §1]. The paper evaluates it on "9 discovery tasks across four domains": 4 kernels, 3 math tasks, 1 Lasso task and 1 harness-optimisation task (Terminal-Bench 2.1) [paper:§1, §4]. v1 had "8 scientific discovery tasks spanning three distinct domains", without the harness [paper-v1:§1 p.3].

The authors compare this to model-based RL and Dreamer ("dreaming" inside a world model) [paper:§2]. A fixed "policy-development agent" ("the same base agent", [paper:Fig.1]) rewrites the exploration-policy **code** many times, scores each version by replaying it over all past trees ("replay worlds"), and deploys the best version for the next real run. That run adds a new tree to the world pool, and the loop repeats [paper:§3, Fig.1]. Only the exploration-policy code changes. The models, the coding agent and the evaluator stay frozen [paper:§3].

---

## 2. What is improved, what is frozen, who grades

| Aspect | Dream-RSI |
|---|---|
| **Improved** | The *exploration policy*: an executable Python class (`OptimalPolicy(LLMDesignedMethod)`) with (a) `solve(question, budget)`, which decides batch by batch which nodes to extend or open and when to stop; (b) `plan_grid(context) -> GridPlan(branch_count, refine_count, reason)`, the pre-episode width×depth plan; (c) a baked-in default scalar `beta` routed through `_schedule(beta)` [paper:App.D.2 L2:1–2, L2:114–127, L2:145–165, L2:190–242]. Paper framing: "Only the policy code changes; the models, evaluator, and execution interfaces stay fixed" [paper:§3]. |
| **Frozen** | The discovery agent (Gemini CLI with Gemini-3.1-Pro or Gemini-3.7-Flash; Claude Opus 4.8 with Claude Code on the harness task) and its exploration prompt [paper:§4, §4.2, §4.4; App.D.1]; the task evaluator (correctness checks + score) [paper:§3]; the policy-development agent ("a fixed agent iteratively revises the code" [paper:§3]; v1: "A fixed LLM-based policy-development agent" [paper-v1:§3 p.4]); the per-round budget limit B (110 calls/round for 3.1-Pro, 640 for 3.7-Flash, 16 on the harness), an upper limit [paper:App.B.2, Table 3]; model weights ("Zero gradient steps on the coding agent" [code:zhengkid__Dream-RSI/README.md, stats image alt text]; the SVG reads "0 gradient steps on the coding agent — only the exploration-policy code changes" [code:zhengkid__Dream-RSI/assets/stats-light.svg]). |
| **Who grades candidates (object level)** | Each attempt "is scored by a fixed evaluator" [paper:§3] (v1: it "scores the resulting candidates and provides diagnostic feedback" [paper-v1:§3 p.4]). "Scores are generated from a fixed protocol, and larger values are better. We define the root score s_r = 0" [paper:§3]. "Programmatic" is [inferred] from the App. B.3 task definitions: the paper does not rule out other graders. Example: Lasso scores the inverse geometric-mean runtime after an objective-value correctness gate, and a failed check gives score 0 [paper:App.B.3 Problem 1]. |
| **Who grades policies (meta level)** | The **replay objective**, computed only from recorded evaluator scores in past trees. No LLM judge is involved [paper:§3 Eq.1]. Selection: the argmax of the mean replay score over *all* past worlds, and the current policy is always a candidate [paper:§3]. |
| **Who proposes policies** | An LLM policy developer ("controller-development agent") following the Listing 2 prompt [paper:App.D.2]: "the same base agent dreams" [paper:Fig.1]. On the harness task Claude Opus 4.8 with Claude Code is "both the discovery agent and the policy-development agent" [paper:§4.4]; for the Gemini tasks the developer model is not named separately (v1 named none). |
| **Overview-doc summary** | "What changes: The exploration strategy, written as code. What stays fixed: The models, the coding agent and the evaluator. How it decides: Best average score when replayed over every past search" [doc]. |

---

## 3. The loop, step by step (precise pseudocode)

### 3.1 Notation [paper:§3]

- `r`: the root, the initial workspace state. Every non-root node `v` has exactly one *primary parent*. The parent is where the attempt starts: the agent resumes the parent's saved workspace and uses its accumulated observations as context. Node `v` "keeps all previous generation history for it, and its own filesystem snapshot, together with an evaluation score s_v", larger meaning better, and `s_r = 0` [paper:§3]. (v1 also listed the generated artifact and evaluation diagnostics [paper-v1:§3 p.5].)
- `A(T) = {r} ∪ {v ∈ T : v is a leaf}`: the eligible nodes, with leaves taken from the *currently observed* tree [paper:§3].
- `W ≥ 1`: the number of parallel workers. Each worker runs one generation–evaluation request at a time [paper:§3].
- `A(T; W) = {C ⊆ A(T) : |C| ≤ W}`: the feasible batches. Each selected node is the starting point of one attempt [paper:§3].
- `t = 1, 2, …`: outer iterations. `π_1` is the initial policy and `H_0 = ()` [paper:§3].
- `K1`: the maximum number of online decision rounds. `K2`: the maximum number of replay decision rounds [paper:§3].
- `M ≥ 1`: the number of policy versions per offline phase, `π_t^0 … π_t^{M−1}` with `π_t^0 = π_t` [paper:§3].
- `ρ ∈ R = {0, 0.25, 0.5, 0.75, 1}`: the budget preference each replay is conditioned on; `λ`: the parallelism-penalty weight of Eq. 1, value not given [paper:§3 Eq.1]. (v1 had instead `β1, β2 ≥ 0` [paper-v1:§3 Eq.1].)

**Structural consequence** [inferred from the A(T) definition, confirmed by the prompt wording]: only `r` and leaves are selectable, and replay gives each non-root node "its unique recorded child". So a discovery tree is **a root with a set of chains (branches)**. Branching happens only at the root, and each branch is one workspace's refinement chain. Listing 2 calls the replay environment "a frozen, irregular branch×attempt grid. A policy opens a root or refines the next cell of an already-open branch" [paper:App.D.2 L2:7–8].

**Formalism gap** [inferred]: taken literally, `C ⊆ A(T)` is a *set*, so `r` could appear at most once per batch, allowing only one new branch per round. That contradicts the parallel-refine baseline, which opens 10 or 32 branches in round 1 [paper:Table 3], and the prompt's "It may contain several roots" [paper:App.D.2 L2:104]. The implementation clearly exposes **distinct root cells**, one per planned branch (`legal_roots() # unopened roots only`) [paper:App.D.2 L2:33]. The framework should model roots as `branch_count` distinct root slots.

### 3.2 Outer RSI loop [paper:§3, Fig.1]

```text
Dream-RSI(π_1, T, W, B, K1, K2, M, R, λ):          # T, W, B, M per task: Table 3
    H ← ()                                   # pool of replay worlds (completed trees)
    π ← π_1                                  # initial policy = parallel-refine (Sec. 3.5)
    for t in 1..T:
        # ---- Stage 1: online exploration (stochastic, expensive) ----
        plan ← π.plan_grid(GridPlanningContext(history=live_manifests(H), caps, worker_cap=W))   # [paper:App.D.2 L2:190-242]
        T_t  ← OnlineRollout(π, plan, W, K1, context=H)                                           # Sec. 3.3
        # ---- Stage 2: construct replay simulator ----
        H ← H ∪ {T_t}                                                                             # [paper:§3]
        write trace_pool/iter<t>/live_cycle_manifest.json                                         # [paper:App.D.2 L2:170]
        # ---- Stage 3: dreaming-based policy improvement (cheap, offline) ----
        versions ← [π];  scores ← [ReplayScore(π, H, R)]     # Eq. 1 per world, mean over H; π_t^0 = π_t
        for m in 0..M-2:            # [paper:§3]; v1 said m = 0..M-1 (off-by-one note)
            fb  ← replay trajectories + scores of versions[m] (+ feedback from earlier revisions)
            π' ← PolicyDeveloper.revise(code=versions[m], feedback=fb,
                                         history_dir, trace_pool, method_file)   # LLM, Listing 2
            versions.append(π');  scores.append(ReplayScore(π', H, R))   # evaluated on the same t worlds
        m* ← argmax_m scores[m]                                                                   # [paper:§3]
        π  ← versions[m*]                     # guarantees V^{m*} ≥ V^0 on H_t (replay only)
    return π, H
```

- **M versions, M−1 revisions.** v2 revises "At each step m = 0, …, M−2" and selects over m ∈ {0, …, M−1} [paper:§3]; "Each offline phase evaluates the current policy and three newly generated policy versions (M = 4)" [paper:App.B.2]. v1 had an off-by-one ("For each m = 0,…,M−1 … revises", "After M revisions … selected from all M evaluated versions") that allowed an M-revisions / M+1-candidates reading [paper-v1:§3 p.5–6]; v2 removes it.
- **Scale of M**: v2's captions say the agent dreams, "generating many candidate policies" and that "a large number of candidate policies can be tested" [paper:Fig.1–2], with M = 4 LLM-written versions per phase [paper:App.B.2]. So "many" is most likely the (version × world × ρ) replay episodes [inferred]. v1's captions said "a massive pool of alternative policies" and "Thousands of candidate policies" [paper-v1:Fig.1–2 captions].
- **Which parent the revision starts from**: the paper says the developer "inspects the replay trajectories and scores of π_t^m, along with feedback from earlier revisions" and rewrites it into π_t^{m+1} [paper:§3]. The prompt says "Start from a strong recent policy, retain mechanisms that raised `pareto.reward`, and make a concrete change when progress stalls" [paper:App.D.2 L2:247–249]. So the developer may start from any earlier strong version, not strictly the latest [inferred].
- v2 gives T, W, B and M per task [paper:Table 3] (§4.8): T = 5 (Lasso), 9 (kernels, harness), 10 (math); M = 4. It gives no `K1`, `K2` or `λ`. (v1 gave only the per-round budgets and the Lasso and math round counts [paper-v1:§4 p.7, §4.2 p.9].) The guidance variants in Fig. 6 show 11 (Dream+Guidance) and 8 (Fixed+Guidance) plotted rounds [paper:Fig.6, digitized].
- Implied lower bound on `K1` [inferred]: B = 10 × 11 is 10 branches × 11 attempts per branch "including the initial attempt" [paper:App.B.2], and each branch advances at most one cell per round. So `K1 ≥ 11` (Pro) and `K1 ≥ 20` (Flash) are needed for the baseline to use its full grid.

### 3.3 Online rollout [paper:§3]

```text
OnlineRollout(π, plan, W, K1, context=H_{t-1}):
    T ← {r};  k ← 0;  π.reset_episode_state()
    # "the fixed policy π_t guides the discovery agent"; "The history informs exploration but is kept
    #  separate from the tree under construction."
    while k < K1:
        C ← π.select_batch(observed(T), legal=A(T) within plan grid, max_size=W)
        if C == ∅: break                                     # empty batch = stop
        parallel for v in C (one worker each):
            ws    ← restore_workspace(v)                     # r → initial workspace
            art   ← DiscoveryAgent(prompt=Listing1(node_dir, history_dir, baseline_dir,
                                   eval_program, problem_file, direction_guidance), workspace=ws)
            res   ← Evaluator(art)                           # score, diagnostics, validity, error
            child(v) ← Node(parent=v, snapshot, artifact=art, eval=res, score=res.score)
        T ← T ∪ {children};  k ← k + 1                       # previously recorded nodes unchanged
        # the new child becomes the selectable leaf of the extended branch; r stays selectable
    return T                                                  # recorded as T_t
```

- The transition is **stochastic**, because the same starting workspace can produce different outcomes [paper:§3].
- The rollout ends on an empty batch or after `K1` rounds [paper:§3].
- The per-round budget cap B is the grid: 110 = 10 × 11 (3.1-Pro), 640 = 32 × 20 (3.7-Flash), 16 = 4 × 4 (harness), branches × attempts per branch including the initial attempt; "These budgets are upper limits; actual discovery-agent calls depend on the policy's allocation and stopping decisions" [paper:App.B.2, Table 3]. The runtime grid is a hard bound: "controller thresholds may use less, but can never create branches or attempts beyond the effective plan" [paper:App.D.2 L2:239–240].
- `plan_grid` "runs before a new live grid is created … must never inspect a current episode's outcomes" [paper:App.D.2 L2:199–200]. A "direction provider assigns those new roots their directions" [paper:App.D.2 L2:236–237]. The paper does not describe the direction provider. Presumably it fills `$direction_guidance` in Listing 1 [inferred].
- Unspecified: whether round t's root `r` is the original seed program or the best program so far. The paper only says the tree "is rooted at r, the initial workspace state", with `s_r = 0` [paper:§3], and the agent reads `$history_dir` / `$baseline_dir` [paper:App.D.1 L1:9].

### 3.4 Replay (the "dream") [paper:§3; App.D.2]

```text
ReplayEpisode(π, T_i, W, K2, rho):           # one (m, i, ρ) triple [paper:§3]; T_i is a fixed recorded tree
    π.config["beta"] ← rho                      # "conditioned on ρ" [paper:§3]; L2's swept beta [paper:App.D.2 L2:160-161; ρ = beta inferred]
    plan ← π.plan_grid(ctx(history=manifests before T_i?, caps, W,
                          trace_branch_count(T_i), trace_refine_count(T_i)))   # [paper:App.D.2 L2:210-212; inferred]
    #   cells outside plan ∩ support are illegal; out-of-support plans "cannot earn replay reward"
    π.reset_episode_state()                     # each replay "starts from {r}" [paper:§3]; v1: "replay resets the policy's per-rollout state" [paper-v1:§3 p.5]
    O ← {r};  k ← 0                             # O = revealed subtree T_i^{m,k} ⊆ T_i
    loop:
        if k == K2 or O == T_i: break           # round limit, or everything revealed
        C ← π.select_batch(observations(O), legal=A(O), max_size=W)   # prefix-only
        if C == ∅: break
        for v in C:
            if v == r:  c ← earliest-created child of r in T_i \ O      # open next unrevealed branch (paper §3;
                                                                          #  implementation: v is a specific root cell, see below)
            else:       c ← the unique recorded child of v in T_i, if any (it is unrevealed since v is a leaf of O)
            if c exists: O ← O ∪ {c}             # "reveal" = expose its stored observations
            # otherwise Child(v) = ∅ "when no unobserved continuation remains"; nothing is revealed
        k ← k + 1                               # every nonempty batch counts as one round
        # optional hook: on_reveal → _record_curve(res, question) for the AUC objective
    N ← |O| − 1                                 # revealed non-root nodes = generation–evaluation requests represented
    k* ← k
    return O, N, k*
```

Replay semantics in detail:

- **"Reveal"** means that selecting node `v` exposes the stored observation of `v`'s recorded child: score, validity, fail class, error, deltas and so on. Replay "returns recorded children of the selected nodes deterministically rather than generating new candidates" [paper:§3]. "every branch is traversed in its recorded parent–child order, and no outcome outside T_i is produced" [paper:§3].
- **Root selection: the paper formalism and the implementation API disagree.** In §3, selecting `r` reveals "the earliest-created child of r outside T_i^{m,k}, opening one previously unrevealed branch" [paper:§3]. Under that rule, branches open in creation order and the policy only chooses *how many* to open. The Listing 2 API instead exposes several distinct root cells, `legal_roots() # unopened roots only`. Each has `meta(cell_id)` with `.branch .attempt .parent_id .seq .tags`, and batches "may contain several roots" [paper:App.D.2 L2:33–35, L2:104]. The prompt also warns "Do not choose roots merely because their branch id is small" [paper:App.D.2 L2:238–239]. That warning would be pointless if the choice of root did not matter, so in the implementation the policy most likely picks *which* unopened branch to open, using structural `meta`/`.tags` such as a direction label from the direction provider [inferred]. The framework should support both modes. `root_mode="earliest"` follows the paper formalism. `root_mode="addressable"` follows the implementation API, where roots are distinct cells revealed by id.
- **Nodes the recorded tree lacks**: selecting a leaf with no recorded continuation reveals ∅ [paper:§3]. At the grid level, the API exposes only existing frontier cells as legal ("legal_actions() -> roots + opened-branch frontiers") [paper:App.D.2 L2:32], so the "irregular grid" hides missing cells. For out-of-support grid plans, see the next bullet.
- **`plan_grid` also constrains replay** [paper:App.D.2 L2:210–212; mechanics inferred]. "In replay, a requested plan beyond the frozen trace's `context.trace_branch_count` or `context.trace_refine_count` is out of support and cannot earn replay reward." So each replay episode also calls `plan_grid` on a `GridPlanningContext` that carries the frozen trace's support fields, and the plan bounds which cells are legal. The paper does not say whether an out-of-support plan scores 0, is clipped to the support, or is penalised. The §3 formalism and Eq. 1 do not mention `plan_grid` at all [inferred].
- **Termination** happens on an empty batch, `k = K2`, or once every recorded node is revealed [paper:§3]. In the implementation, "Replay calls with `budget=None`. Always terminate when no batch is selected; do not assume a budget cap exists" [paper:App.D.2 L2:140–141].
- **Batch legality**: the cells must be distinct and legal *before* the call. "It may contain several roots and/or one frontier from each opened branch. It must never contain a parent and its child together", and it holds at most `question.max_parallelism` cells with no duplicates [paper:App.D.2 L2:103–105, L2:142–143].
- **No-peeking (prefix-only) constraint** [paper:App.D.2 L2:9–10, L2:53–55, L2:133–139, L2:256–263]:
  - "The policy sees only the cells it has revealed so far; unrevealed scores are unknown."
  - "decisions may use revealed observations, `baseline_score`, legal sets, structural `meta`, and helper signals. Never use unrevealed scores, a true optimum, hardcoded winning cell ids, absolute score targets, or internal trace data."
  - "Do **not** use `question.best_so_far` or `question.budget_spent` to decide what to explore; they are bookkeeping only."
  - `policy_execution_traces.jsonl` "is **between-round feedback only**: never read it inside `solve()`, and never copy a trace-specific branch, cell id, score, or target into policy logic."
  - "Keep all thresholds relative to the prefix; never use absolute score cutoffs" [paper:App.D.2 L2:167].
  - Overview doc: "They can't peek at unrevealed scores or use known best answers" [doc].
- **Same interface online and offline**: "Both the online and offline phase use this decision interface, and differ only in how state transitions after an action: one executes for real, the other replays" [paper:§3]. The online transition is stochastic, and costs model calls and compute. The replay transition is fixed and "nearly free", and it "can reach new ideas: No, only recorded ones" [doc]. The paper's own wording is "fast, low-cost off-policy evaluations" [paper:Fig.2 caption] (v1: "zero-execution-cost" [paper-v1:Fig.2 caption p.4]) and "at negligible execution cost" [paper:§7]. Replay still costs policy-code CPU time and, in the dreaming stage, the developer LLM's calls. The paper does not quantify either [inferred].

### 3.5 Initial policy π_1 = the "parallel refining" strategy [paper:§4; paper-v1:§4]

v2 calls it only "the same hand-designed starting policy" [paper:§4] and "a parallel-refine floor to beat" [paper:App.D.2 L2:250–251]. v1 described it: it "launches multiple independent exploration workspaces in parallel, with each workspace maintaining its own local discovery trajectory and repeatedly refining its current candidate based on the history accumulated within that workspace" [paper-v1:§4 p.7]. In grid terms [inferred]:

```text
ParallelRefine(W_branches, R):              # B: 3.1-Pro 10 branches × 11 attempts; 3.7-Flash 32 × 20; harness 4 × 4
    round 1:   batch = all W_branches roots
    rounds 2..: batch = frontier of every still-extendable branch (deepen-all)
    stop when no legal frontier remains (i.e., after the plan's depth)
```

- **Recursive Fixed Exploration** (the primary controlled baseline) uses "identical discovery agents, evaluators, initialization, per-round budget limits, and the same hand-designed starting policy"; "From the second round onward, Recursive Fixed Exploration keeps its policy fixed" [paper:§4]. "By design, both methods behave identically in the first round" [paper:§4.3]; in the figures they share the round-1 data point [paper:Fig.4b, digitized].
- The prompt tells new policies "Do not use a fixed widen-all / deepen-all wave schedule" [paper:App.D.2 L2:105–106].
- "We measure discovery cost as the cumulative number of discovery-agent calls" [paper:§4].
- 10 × 11 = 110 is 11 attempts per branch: "The parenthesized budget decomposition specifies the maximum number of branches and attempts per branch, including the initial attempt" [paper:App.B.2], i.e. attempt 0 plus 10 refinements (`refine_count=10`). v1's "up to 11 refinement steps (10 × 11 = 110 discovery-agent calls)" had left this open [paper-v1:§4 p.7].

---

## 4. Formulas, objectives, acceptance rules, schedules

### 4.1 Replay score (paper Eq. 1) [paper:§3 Eq.1]

Per world `i`: `B_i = |T_i| − 1 > 0` recorded non-root nodes and `S_i = max_{v ∈ T_i \ {r}} s_v > 0` the best recorded score. For each `ρ ∈ R = {0, 0.25, 0.5, 0.75, 1}`: `n_{i,ρ}^m = |T_{i,ρ}^{m,k*}| − 1` calls represented, `s_{i,ρ}^m` the best revealed non-root score (0 if `n = 0`), attainment `a_{i,ρ}^m = clip(s/S_i, 0, 1)` and `k*_{i,ρ}^m` completed rounds. Keep the distinct non-dominated points `(n̄_j, ā_j)`, `j = 1..L`, ordered by increasing calls, with `ā_0 = 0`:

```
V_i^m = (1/B_i) Σ_{j=1..L} (ā_j − ā_{j−1}) · (B_i − n̄_j)   −   λ · (1/|R|) Σ_{ρ ∈ R, n_{i,ρ}^m > 0} k*_{i,ρ}^m / n_{i,ρ}^m
        (normalized hypervolume)                                (parallelism penalty)
```

- Term 1 is "the normalized area dominated by the quality–cost Pareto frontier, rewarding policies that attain higher quality with fewer discovery-agent calls". Term 2 is "the average number of completed sequential rounds per call across the budget sweep, thereby favoring policies that execute more calls in parallel; replays with zero calls contribute zero to this term" [paper:§3]. A point is dominated "if another point uses no more calls and attains no lower quality, with at least one strict improvement" [paper:§3].
- The root is excluded (`s_r = 0`; `s = 0` when nothing is revealed). Attainment is capped at 1, i.e. at the best recorded score [paper:§3].
- **λ: NOT REPORTED.** Neither are `K1` and `K2`.

**v1's Eq. 1** [paper-v1:§3 Eq.1 p.6], which `rsi`'s `objective="eq1"` implements (the default is v2's Eq. 1, `objective="v2"`, since 25 Oct 2026) (one replay per world, no ρ sweep; `T_i^{m,k*}` the revealed subtree, `N_i^m = |T_i^{m,k*}| − 1`):

```
V_i^m = max_{v ∈ T_i^{m,k*}} s_v   −   β1 · N_i^m   +   β2 · N_i^m / max{1, k*_i^m}
        (discovery quality)            (execution cost)   (parallelism bonus)
```

- v1: term 1 is "the best solution quality attained during replay", term 2 "penalizes the number of attempted generations", term 3 "rewards the average number of attempts executed per decision round, favoring policies that batch useful continuations rather than execute them sequentially" [paper-v1:§3 p.6]. The max includes the root, so an empty replay scores `s_r` [inferred].
- β1, β2 were never reported. The overview doc's toy demo uses β1 = 0.010 ("cost per attempt") and β2 = 0.005 ("parallelism bonus") on a made-up tree; its paraphrase is "replay score = best result found − β1 × attempts used + β2 × attempts per round" [doc]. Illustrative only.

### 4.2 Policy evaluation and selection [paper:§3]

```
V^m = (1/t) Σ_{i=1..t} V_i^m                           # mean over ALL worlds in H_t (no held-out split)
m* ∈ argmax_{m ∈ {0,…,M−1}} V^m ;   π_{t+1} = π_t^{m*}
Guarantee: V^{m*} ≥ V^0  ("never worse than π_t on the average replay score over H_t"; nothing is guaranteed online)
```

### 4.3 The objective in the actual prompt: beta-sweep Pareto reward [paper:App.D.2 L2:5–22, L2:145–188]

The policy-development prompt states the evaluator objective in its own terms. In v2 this is Eq. 1 written for the developer: the swept `beta` plays the role of §3's ρ (the paper never equates them [inferred]), `pareto.auc` is the normalized hypervolume and `parallel_penalty` the second term. In v1, whose Eq. 1 was the β1/β2 form, the prompt and Eq. 1 disagreed and the paper did not say which produced the results [paper-v1:§3 vs App.B.2].

```
The evaluator sweeps the policy's single `beta` knob over a fixed grid and ranks the resulting curve by
    pareto.reward = pareto.auc − λ · parallel_penalty
pareto.auc          rewards "reaching high per-trace attainment with few total probes"
parallel_penalty    = mean over the sweep of ( effective_sequential_rounds / total_probes )
    a batch of size k with W = question.max_parallelism workers costs 1 decision round and ceil(k/W)
    effective sequential rounds; "A serial policy has penalty near 1; useful full batches approach 1/W."
```

- v2 §3 now gives the grid (ρ ∈ {0, 0.25, 0.5, 0.75, 1}), the area (hypervolume over calls) and the attainment `clip(s/S_i, 0, 1)`, normalized by each trace's best score, as Listing 2's "a frozen trace's known ceiling" suggested [paper:§3; App.D.2 L2:187–188]. **λ is still not reported.** v1 gave none of these.
- **Relation to Eq. 1** [inferred]: Listing 2 charges a batch of size k `ceil(k/W)` effective sequential rounds, Eq. 1 counts one round per batch; since every batch has `k ≤ W` they agree. Against v1's Eq. 1, `parallel_penalty` = rounds/probes is the reciprocal of the parallelism term `N/max(1,k*)`; both reward large batches and trade quality against probes.
- A "legacy AUC-only sweep is useful code history but is not numerically comparable to the current reward" [paper:App.D.2 L2:249–250]. The objective changed during the project [inferred].

### 4.4 The policy's internal `beta` knob (not β1/β2) [paper:App.D.2 L2:145–188]

Naming clash: the policy's `beta` is a behavioural aggressiveness knob in [0,1]. It is not v1's Eq. 1 coefficients β1/β2; it is most likely v2's budget preference ρ [inferred].

- Read exactly one scalar: `beta = float(self.config.get("beta", <sensible_default>))`.
- **Within an episode beta is fixed.** "Route every behavioral threshold through one `_schedule(beta) -> dict`. High beta means more width, deeper patience, and weaker pruning. Low beta means fewer probes, earlier stagnation stops, and stronger pruning. Never change beta from observations inside `solve()`." Recovery eligibility, the reserve threshold and waiting also go through the schedule: "high beta is more patient; low beta remains selective without treating one repairable failure as automatic closure" [paper:App.D.2 L2:157–159].
- **During offline evaluation** the evaluator sweeps a fixed beta grid. "This measures whether the policy exposes a real attainment/work/parallelism trade-off; it is not online beta adaptation."
- **Across cycles** the developer picks the baked-in default once, from the most recent 2–3 live `live_cycle_manifest.json` files (final best score and actual beta) plus the matching `beta_sweep.json`:
  - live best still improving → keep the prior default unless the sweep clearly shows a better nearby beta;
  - live best has plateaued and a higher beta reaches higher attainment at reasonable cost → **raise by about 0.1–0.2, clamped to [0, 1]**;
  - a high default has already been tried through a plateau and high-beta points add work without attainment → lower it by a small step;
  - history insufficient or conflicting → use a **moderately exploratory default of about 0.6**, "rather than pretending the replay ceiling is a live stopping signal" [paper:App.D.2 L2:183–184].
- Evidence rule: "Scores alone do not establish that beta caused a change, so always use both sources" (live manifests and sweeps) [paper:App.D.2 L2:173–174]. Sweep sanity: "The beta sweep is non-degenerate only if beta changes the attainment/work trade-off. It also reveals whether the policy batches" [paper:App.D.2 L2:186–187]. A framework check should flag a policy whose sweep curve is flat in beta [inferred].
- The overview doc summarises this as "a dial the strategy tunes across cycles" [doc]. The paper's Fig. 7 pacing result is the behavioural consequence (§7.3).

### 4.5 Grid planning rule [paper:App.D.2 L2:190–242]

- `GridPlan(branch_count=W, refine_count=R, reason=...)` accepts arbitrary integers. It creates branches `0..W−1` and attempts `0..R`, where R is the number of refinements after each root. Here "W" means *branches*, not workers [naming clash, inferred].
- The runner validates `1 ≤ W ≤ context.hard_max_branch_count` and `0 ≤ R ≤ context.hard_max_refine_count`.
- `plan_grid` must return a non-None plan on every path: "do not inherit the template stub and do not delegate grid choice to the runner's fallback" [paper:App.D.2 L2:201–202]. It is "deterministic" [paper:App.D.2 L2:192]. With empty or insufficient history it returns "an explicit conservative bootstrap plan derived from the context's fallback/hard-cap fields, with a factual reason".
- Width versus depth rules:
  - many distinct roots improve early while deeper refinements stall → increase width, reduce or hold depth;
  - high gains arrive late on a small repeatable set of directions → reduce or hold width, increase depth;
  - all directions plateau after sufficient depth while direction classes remain uncovered → increase width;
  - repeated hard, unrecoverable failures or redundant directions → reduce both conservatively;
  - conflicting or insufficient history → conservative bootstrap.
- Inputs are limited to prefix-safe context: completed earlier live manifests (planned and effective grids, opened width and depth, probe work, decision rounds, scores, beta), the fallback and hard caps, the worker cap, and replay structural-support fields. It must not read "raw trace outcomes or a current cycle result".

### 4.6 Batch-decision loop inside `solve()` [paper:App.D.2 L2:57–101]

1. Reconstruct each opened branch's **ordered prefix trajectory**: its successful anchor, score trend, regressions, failure/repair sequence, and explored versus remaining depth. Close only branches with *cumulative* evidence of being hard-unrecoverable or repeatedly unpromising.
2. Classify failed frontiers as **hard-unrecoverable / repairable implementation failure / weak-but-underexplored / repeatedly unpromising after sufficient valid evidence**. "Output/correctness mismatch, shared-memory/resource limits, and variable/code, mask/layout/shape errors are normally repairable." "`compile_other` alone is not permanently hard." "a later successful result reopens the branch."
3. Rank legal roots and frontiers only on prefix signals: successful anchor, parent→child gain, full trajectory, success versus failure evidence, recoverability, prior repair outcomes, remaining depth, cross-branch comparison. Keep repairable failures and underexplored frontiers in **deterministic queues** ranked by "trajectory, recoverability, remaining depth, repeated failures, and beta". "A repairable failure retains eligibility unless cumulative evidence lowers its relative priority" [paper:App.D.2 L2:85–88].
4. Build one **dynamic portfolio** batch of up to `max_parallelism` cells. It combines *exploitation* (strong refinements), *exploration* (new roots or underexplored branches) and **at most one** *recovery* (an actual repairable failure). "give exploration and justified recovery representation before filling remaining slots by priority; adapt … rather than fixed quotas. Recovery must not displace normal successful refinements or leave workers idle. Never sample randomly, and do not default to a singleton merely because its top candidate is clear."
5. **Portfolio-level stop**: "Stop only after considering the whole revealed portfolio: active, underexplored, recoverable, unopened, and remaining legal candidates. Do not stop while an eligible high-priority recovery or underexplored candidate remains; every remaining action needs an evidence-based decision to continue, reserve, or close" [paper:App.D.2 L2:98–101].

Further hard rules from the same prompt:

- "Never label it repairable solely because `valid` is false" [paper:App.D.2 L2:49–50].
- "Derive any decision statistic from `question.observed()`" [paper:App.D.2 L2:54–55].
- "Do not infer algorithmic failure from one such error. `n_valid == 0` and `branch_failed_hard(obs)` are signals, not unconditional closure: use `fail_class` and `error` to distinguish a repairable zero-valid failure from an environment/dependency failure" [paper:App.D.2 L2:67–70].
- "A repairable latest failure must not erase its historical successful anchor or by itself cause permanent starvation" [paper:App.D.2 L2:138–139].
- "Weigh recovery value against new roots and ordinary refinements while keeping batches parallel" [paper:App.D.2 L2:25–26].
- Batches are adaptive: "adapt batch composition after every revealed prefix" [paper:App.D.2 L2:106].

**Success semantics** [paper:App.D.2 L2:47–51]: "an evaluated observation with `error is None` and `fail_class == "ok"` is a successful evaluation, even when `valid == False` or `n_valid`/`n_total` are unavailable." A *successful anchor* is the best historical score from such an evaluation.

### 4.7 Task scoring formulas (object level) [paper:App.B.3]

- **Lasso path.** `F_k(w) = (1/2n)‖y − Xw‖²₂ + λ_k‖w‖₁` for a decreasing sequence `λ_1 > … > λ_K`. A candidate passes if `F_k(w̃_k) ≤ F_k(w_{k,sklearn}) + 10⁻⁶` for every k, checked on **fresh instances distinct from the timing instances**. If any check fails, the score is 0. Otherwise `R_search = (∏_{i∈I} t_i)^{−1/|I|}`, the inverse geometric-mean path runtime over the timing instances `I`.
- **Sum–difference.** Maximize `Γ(A) = log(|A+A|/|A|) / log(|A−A|/|A|)` over finite `A ⊂ ℤ`.
- **Circle packing.** For n ∈ {26, 32}, maximize `Σ r_i` subject to `r_i ≤ x_i ≤ 1−r_i`, `r_i ≤ y_i ≤ 1−r_i` and `(x_i−x_j)² + (y_i−y_j)² ≥ (r_i+r_j)²`.
- **Autocorrelation inequalities.** f is supported on [−1/4, 1/4] with ∫f = 1, and `(f∗f)(t) = ∫ f(t−x) f(x) dx`:
  - Φ1: f ≥ 0, minimize max_t (f∗f)(t);
  - Φ2: f ≥ 0, maximize ‖f∗f‖²₂ / (‖f∗f‖₁ ‖f∗f‖_∞);
  - Φ3: f may be signed, minimize max_t |(f∗f)(t)|.
  - The paper does not say which one Table 1's "Auto Correlation (↓)" reports. "(↓)" rules out Φ2, which is maximized. AlphaEvolve's third-inequality upper bound is 1.4557, which matches Table 1's AlphaEvolve entry 1.455700, so it is probably Φ3 [inferred]. That bound comes from outside knowledge of Novikov et al. 2025 and is not in the provided sources.
  - The autoconvolution is evaluated for t ∈ [−1/2, 1/2] [paper:App.B.3 Problem 4].
- **Kernels (KernelBench).** Performance = inverse runtime (1/ms), subject to correctness checks against the reference implementation [paper:§4.1], the PyTorch reference under the official KernelBench protocol, on an NVIDIA L40S [paper:App.B.1]. VGG16, ConvDiv, ConvMax and LayerNorm are defined in App. B.3 Problems 5–8.
- **Harness (Terminal-Bench 2.1).** Score = the full-suite score over 89 tasks, two independent runs per task, evaluated with Claude Opus 4.8 [paper:§4.4].
- Convention: all scores are oriented so that larger is better, and `s_r = 0` [paper:§3]. Minimization tasks must be transformed [inferred]; v2's attainment `s/S_i` also needs positive scores (`S_i > 0`) [paper:§3].

### 4.8 Hyperparameters found

| Symbol / name | Value | Source |
|---|---|---|
| T (online rounds) | Lasso 5 (Pro and Flash); VGG16, LayerNorm, ConvDiv, ConvMax 9; sum–difference, autocorrelation, circle packing 10; harness 9 | [paper:Table 3] |
| W (max parallel workers) | 10 (Gemini-3.1-Pro tasks), 32 (Lasso, Gemini-3.7-Flash), 4 (harness) | [paper:Table 3] |
| B (max calls per online round) | 110 = 10 × 11 (Pro), 640 = 32 × 20 (Flash), 16 = 4 × 4 (harness): branches × attempts per branch, including the initial attempt; an upper limit | [paper:Table 3, App.B.2] |
| M (versions per offline phase) | 4: the current policy and three new versions | [paper:Table 3, App.B.2] |
| ρ grid | {0, 0.25, 0.5, 0.75, 1} | [paper:§3] |
| λ, K1, K2 | **Not reported** | — |
| β1, β2 | v1's Eq. 1 only; **not reported** | [paper-v1:§3 Eq.1] |
| Default beta when evidence is insufficient | about 0.6 | [paper:App.D.2 L2:183–184] |
| Beta step across cycles | about 0.1–0.2, clamped to [0,1] | [paper:App.D.2 L2:179–180] |
| Recovery slots per batch | ≤ 1 | [paper:App.D.2 L2:91–92] |
| Lasso correctness tolerance | 10⁻⁶ on the objective value | [paper:App.B.3] |
| Lasso search instances | the 17 synthetic SimpleTES instances | [paper:§4.3] |
| Policy developer LLM | "the same base agent" [paper:Fig.1]; Claude Opus 4.8 with Claude Code on the harness [paper:§4.4]; not named separately for the Gemini tasks | [paper:Fig.1, §4.4] |
| Discovery agent | Gemini-3.1-Pro (all scientific tasks) and Gemini-3.7-Flash (Lasso) through the Gemini CLI (geminicli.com); Claude Opus 4.8 with Claude Code (harness) | [paper:§4, §4.2, §4.4; paper-v1:§4] |
| Hardware | Lasso and math: KVM VM, AMD EPYC 7B12, 8 vCPUs, 32 GB RAM; kernels: NVIDIA L40S; harness: 96-core (192 threads), 708 GiB CPU server with Docker | [paper:App.B.1] |

---

## 5. Data structures and artifacts

### 5.1 Discovery tree / node (paper model) [paper:§3]

```text
DiscoveryTree
  root: Node r (initial workspace state; score s_r = baseline)
  nodes: {node_id → Node}
Node
  node_id
  parent_id                  # exactly one primary parent (r or earlier node)
  created_seq                # creation order (needed for "earliest-created child of r")
  workspace_snapshot         # resulting filesystem snapshot (resumable)
  artifact                   # generated program / construction
  eval_diagnostics           # evaluator diagnostics (error text, logs)
  score s_v                  # fixed task-scoring protocol, larger = better
  inherited_context          # the parent's accumulated observations ("inherits its accumulated observations as context")
```

### 5.2 Replay-side grid view (implementation API) [paper:App.D.2 L2:28–45, L2:103–106]

```text
Question (environment handle; same API online and in replay)
  reset()
  observed()        -> dict[cell_id, Observation]   # revealed prefix only
  legal_actions()   -> list[cell_id]                # roots + opened-branch frontiers
  legal_roots()     -> list[cell_id]                # unopened roots only
  opened_branches() -> list[int]
  meta(cell_id)     -> CellMeta                     # .branch .attempt .parent_id .seq .tags
  probe_batch(cells, on_reveal=callback) -> list[Observation]
  baseline_score                                    # allowed signal
  max_parallelism                                   # = W workers
  best_so_far, budget_spent                         # bookkeeping ONLY (forbidden as decision inputs)
Observation
  branch, attempt, score, evaluated, valid, fail_class, error,
  delta_vs_baseline, delta_vs_parent, n_valid, n_total
Helper signals (see.policy.observation_signal)
  branch_promising, branch_failed_hard, probe_improved_vs_parent, probe_improved_vs_baseline
Policy scaffolding (see.policy.api)
  LLMDesignedMethod (base class; self.config), SimResult, _budget_done(question, budget),
  _record_curve(res, question), finalize_result(question, res),
  GridPlan(branch_count, refine_count, reason), GridPlanningContext(history, fallback/hard caps,
  worker cap, hard_max_branch_count, hard_max_refine_count, trace_branch_count, trace_refine_count)
```

- A known `fail_class` value is `"ok"`. `"compile_other"` is mentioned as a failure class [paper:App.D.2 L2:48, L2:70]. The full enumeration is not given.
- The minimal policy skeleton, quoted from [paper:App.D.2 L2:110–127]. `update_closed` and `select_batch` are placeholders the developer must write:

```python
from see.policy.api import (
    LLMDesignedMethod, SimResult, _budget_done, _record_curve, finalize_result,
)

def solve(self, question, budget=None):
    question.reset()
    res, closed = SimResult(), set()
    while not _budget_done(question, budget):
        prefix = question.observed()
        update_closed(closed, prefix, question)
        batch = select_batch(prefix, question, closed)
        if not batch:
            break
        question.probe_batch(batch, on_reveal=lambda _: _record_curve(res, question))
    return finalize_result(question, res)
```

### 5.3 Online workspace layout (discovery agent) [paper:App.D.1]

| Path | Content | Access |
|---|---|---|
| `$node_dir/` | this attempt's directory | writable: only `proposal.md` and `$eval_program` |
| `$node_dir/proposal.md` | "mechanism, evidence from history, why it's not a repeat, expected benefit/risk" | write |
| `$node_dir/$eval_program` | the candidate program | write |
| sibling `attempt_*/proposal.md`, `attempt_*/eval/score.json`, `attempt_*/error.txt` | previous attempts in this rollout | read-only |
| `$history_dir` | the completed history H_{t−1} from earlier rounds [inferred mapping; the prompt only says to read every `proposal.md` there] | read-only |
| `$baseline_dir` | the baseline solution [inferred from the name] | read-only |
| `$problem_file` | the task statement | read-only |
| `$direction_guidance` | text injected at the top of the prompt | per-branch direction or the ablation's guidance [inferred] |

Naming clash [inferred]: Listing 1's `$history_dir` holds the *discovery* history (attempt dirs with `proposal.md`, `eval/score.json`, `error.txt`). Listing 2's `{history_dir}` holds the *policy-development* history (`r####_*/` dirs with policy code and `proposal_results/`). They are different directories, and the framework should give them different names.

### 5.4 Offline / policy-development artifacts [paper:App.D.2 L2:169–175, L2:244–263]

| Artifact | Content |
|---|---|
| `{method_file}` (`method.py`) | policy code: `NAME = "OptimalPolicy"`, `class OptimalPolicy(LLMDesignedMethod)`, `solve`, `plan_grid`, `__init__` reading `beta`, `_schedule(beta)`, and a module docstring covering "prefix signals, batch rule, beta schedule, default-beta rationale, grid-planning rule …, and safeguards against over-pruning, over-stopping, permanent starvation after repairable failures, and serial probes" |
| `{history_dir}/r####_*/` | earlier policy-development rounds: their policy code and `proposal_results/` |
| `{history_dir}/baseline/` | the parallel-refine baseline policy ("floor to beat") |
| `proposal_results/beta_sweep.json` | per round: `pareto.reward`, AUC, parallel penalty, per-beta frontier |
| `proposal_results/policy_execution_traces.jsonl` | "one replay episode per `(frozen trace, beta)`" with prefix state, selected batch and revealed outcomes at each decision round. Between-round feedback only |
| `{trace_pool}/iter*/live_cycle_manifest.json` (+ `_current`) | per live iteration: final best score, actual baked-in beta, planned and effective grid, opened width and depth, probe work, decision rounds |
| `{trace_pool}` raw traces | the recorded trees (replay worlds). May be read only outside `solve()`, and manifests are preferred over raw outcomes |

---

## 6. LLM roles and their prompts

### 6.1 Discovery agent: Gemini CLI, frozen [paper:§4; App.D.1 Listing 1]

Purpose: from a selected node's workspace, produce one new candidate. The prompt "requires the agent to inspect the complete available discovery history before proposing a new solution, explicitly reason about both successful and failed attempts, and avoid repeatedly exploiting a locally saturated direction" [paper:App.D.1]. Key fragments (verbatim):

- "You must read every historical proposal before proposing or implementing a new solution." (L1:1)
- "read every `proposal.md` under sibling `attempt_*/` dirs, `$history_dir`, and `$baseline_dir` in full – not a sample, not just recent cycles or the current branch. For each, read its matching `eval/score.json` (and `error.txt` if it failed). Trust the measured result over what the proposal claims about itself." (L1:9)
- "For failures, figure out *why*: a flawed core idea, or a good idea let down by a bug, bad parameters, or an implementation slip? Don't repeat the former. The latter is worth retrying – but only once you've actually located the bug in the code … and only with a specific fix in hand." (L1:13)
- "If most attempts cluster around small variations of one mechanism with flattening returns, that's a local optimum - resist proposing another small tweak there. Deliberately favor a structurally different mechanism or an untried combination … Exploration diversity matters as much as the next incremental gain." (L1:17)
- "The new idea must be a genuinely new mechanism, a new combination of previously-successful pieces, or a targeted fix to a specific bug found in step 2 - never a repeat or rename of something already tried. … Don't claim it compiles, is correct, or beats SOTA until it's actually evaluated." (L1:21)
- "Write only `$node_dir/proposal.md` … and `$node_dir/$eval_program`. Everything else is read-only." (L1:25). "Never execute pkill, kill, killall, or terminate unrelated processes." (L1:28)

Tension [inferred]: v1 described the fixed baseline as each workspace refining "based on the history accumulated within that workspace" [paper-v1:§4 p.7], but Listing 1 tells the agent to read *all* sibling attempts and all history.

### 6.2 Policy-development ("controller-development") agent, frozen LLM [paper:§3; App.D.2 Listing 2]

Purpose: revise `OptimalPolicy` using replay feedback, "while remaining restricted to prefix-observable information" [paper:App.D.2]. The model is "the same base agent" [paper:Fig.1], named only for the harness task (Claude Opus 4.8 with Claude Code [paper:§4.4]). Key fragments (verbatim):

- "You are improving one **prefix-only exploration policy**. Edit only `{method_file}` and implement `OptimalPolicy.solve(self, question, budget=None)`. Do not solve the scientific task and do not edit any other program." (L2:1–3)
- "The environment is a frozen, irregular branch×attempt grid. A policy opens a root or refines the next cell of an already-open branch. Each revealed cell costs one probe." (L2:7–9)
- "choose only promising probes, but batch independent promising probes whenever possible." (L2:21–22)
- "A local implementation failure does not by itself prove that its parent direction is poor." (L2:24–25)
- "Every prune, widen, deepen, batch, and stop decision must be explainable from the current prefix. Shallow weak scores are not enough to discard a branch: deeper attempts can recover." (L2:136–138)
- "Start from a strong recent policy, retain mechanisms that raised `pareto.reward`, and make a concrete change when progress stalls." (L2:247–249)
- "Before finishing, verify trajectory-based ranking, the stated success semantics, non-automatic zero-valid closure, deterministic recovery competition, and portfolio-level stop." (L2:271–273)

The full list of hard constraints and schedules is in §3.4, §4.3–4.6.

### 6.3 Other LLM roles

- **Guidance summarizer** (ablation only): "abstract prior trajectories into high-level directional insights, which are directly injected into the prompt as explicit semantic guidance for subsequent rounds" [paper:§5]. The prompt is not given. The paper does not say an LLM performs this summarization; that is [inferred]. The injection point is presumably Listing 1's `$direction_guidance` [inferred].
- **Direction provider**: "the direction provider assigns those new roots their directions" [paper:App.D.2 L2:236–237]. The paper does not say whether this is an LLM or how it works.
- **Evaluator**: programmatic, not an LLM [paper:§3, App.B.3]; on the harness task the evaluation itself runs Claude Opus 4.8 inside the harness under test [paper:§4.4].

---

## 7. Experimental protocol and headline results (as reported)

Common protocol: on the scientific tasks Dream-RSI and Recursive Fixed Exploration share the discovery agent, evaluator, initialization, per-round budget limits and starting policy. They are identical in round 1. Cost = cumulative discovery-agent calls. The harness task has no Fixed arm: "an additional full search is estimated to cost over $100,000" [paper:§4]. Configurations: Table 3 (§4.8). All results are single runs with no error bars [doc; the paper reports none].

### 7.1 Algorithm engineering: Lasso regularization path [paper:§4.3, Fig.4a]

Setup: the SimpleTES benchmark setting with 17 synthetic search instances (diverse dimensionality, sparsity, feature correlation and active-set structure). Six held-out downstream datasets. 5 rounds for both methods. Baselines: sklearn, glmnet, SimpleTES (GPT-OSS-120B, 51,200 generations).

Final wall-clock runtime (ms, lower is better) on the six held-out datasets [paper:Fig.4a]:

| Method | Model | Compute (calls) | Gisette | RCV1 | DNA | Leukemia | Colon | Duke Breast | Avg. |
|---|---|---|---|---|---|---|---|---|---|
| sklearn | – | – | 11275.2 | 252881.7 | 93.8 | 227.2 | 229.8 | 374.0 | 44180.3 |
| glmnet | – | – | 9063.6 | 73072.8 | 351.9 | 45.0 | 24.2 | 47.7 | 13767.5 |
| SimpleTES | gpt-oss-120b | 51,200 | 3141.9 | 19625.6 | 15.9 | 15.5 | 11.6 | 18.1 | 3804.8 |
| SimpleTES† | gpt-oss-120b | 51,200 | 8651.0 | 41143.1 | 37.6 | 28.2 | 19.5 | 31.1 | 8318.4 |
| Recursive Fixed Exploration | Gemini-3.1-Pro | 550 | 1861.8 | 19550.1 | 41.5 | 26.1 | 14.5 | 28.4 | 3587.1 |
| Recursive Fixed Exploration | Gemini-3.7-Flash | 3200 | 1133.1 | 13873.0 | 29.8 | 24.1 | 15.7 | 24.4 | 2516.7 |
| **Dream-RSI** | Gemini-3.1-Pro | **317** | 2841.0 | 14616.0 | 49.9 | 30.2 | 16.4 | 32.5 | 2931.0 |
| **Dream-RSI** | Gemini-3.7-Flash | **1879** | 1091.9 | 12923.4 | 31.4 | 21.0 | 12.2 | 23.6 | 2350.6 |

- Gisette and RCV1 are "Non-biological". DNA, Leukemia, Colon and Duke Breast are "Biological" (table header grouping). v2's caption says "† denotes results reproduced by us" [paper:Fig.4 caption]; v1 did not explain it. The v1 arXiv LaTeX source has a commented-out version of the table whose caption says "† denotes results obtained by re-evaluating the discovered programs under the same evaluation setting as our approach" [arXiv source `Main_Text/table/lasso_main_results.tex`, retry round 2].
- Reported headline: Pro "reduces the average runtime … from 3587.1ms to 2931.0ms while using only 317 … calls, compared with 550". Flash goes "from 2516.7ms to 2350.6ms using 1879 calls instead of 3200". The solvers "outperform the standard sklearn and glmnet implementations on all six held-out datasets" [paper:§4.3].
- Headline ratios: "up to 162× fewer agent calls than SimpleTES" [paper:§1]; "approximately 162× and 27× fewer discovery-agent calls" for Pro and Flash, "although these comparisons use different model backbones" [paper:§4.3]. v1 also said "1.7× over fixed-exploration" [paper-v1:§1 p.3]. The README gives "1.22× faster runtime, 1.74× less discovery compute, 162× fewer calls than SimpleTES" for algorithm engineering on Gemini-3.1-Pro [code:zhengkid__Dream-RSI/README.md; assets/stats-light.svg]. Check: 3587.1/2931.0 = 1.224; 550/317 = 1.735; 51200/317 = 161.5 [inferred: computed].
- Recomputed per-dataset statistics [inferred: computed from the table]:

  | Method | Geometric-mean runtime (ms) |
  |---|---|
  | sklearn | 1317.2 |
  | glmnet | 479.2 |
  | SimpleTES | 121.3 |
  | Fixed-Pro | 159.1 |
  | **Dream-Pro** | **179.4** (worse than Fixed-Pro) |
  | Fixed-Flash | 127.6 |
  | **Dream-Flash** | **117.9** (best) |

  Against Fixed, Dream-Pro is slower on 5 of 6 datasets, and Dream-Flash is faster on 5 of 6. SimpleTES beats both Dream-RSI variants on all 4 biological datasets. v2 now states the first two itself: "For 3.1 Pro, the average improvement over fixed exploration is driven by RCV1, despite slower runtimes on the other five datasets. For 3.7 Flash, runtime improves on five of the six datasets" [paper:§4.3].
- Discovered solver (App. E.1 Listing 3, v1 App. C; C++/Eigen/OpenMP inside a Python `CPP_CODE` string between `# EVOLVE-BLOCK-START/END`, `COMPILE_FLAGS = ["-fopenmp", "-ffast-math"]`). Its ingredients are "strong-rule screening with Cauchy–Schwarz-based KKT pruning … falling back to a full refresh when pruning becomes ineffective … active-set bookkeeping, lazy Gram-matrix construction, and hardware-aware implementation" [paper-v1:§4.1 p.9]; v2: "strong-rule screening with adaptive Cauchy–Schwarz KKT pruning, disjoint active-set bookkeeping, lazy Gram-matrix construction, and hardware-aware optimizations" [paper:App.E.1]. Constants in the discovered code (a domain artifact, not framework hyperparameters):
  - CS pruning enabled when `p ≥ 500 && n ≥ 150`;
  - full reset when `num_to_compute > 0.3·p` or `lambdas_since_reset ≥ 8`;
  - strong-rule threshold `2λ − λ_prev`;
  - KKT bound `λ(1+1e−9)`;
  - `thresh = 1e−9`, `maxit = 100000` [paper:App.E.1 Listing 3].

**Recursive dynamics, per round** [paper:Fig.4b, digitized; the round-5 endpoints match the table to within 1 ms or 1 call]. *Exact values (retry round 2):* the v1 arXiv source (`Main_Text/Figures/scaling_lasso.tex`) holds the plotted points: Pro Fixed 110/5267.05, 220/5365.40, 330/5295.23, 440/4691.08, 550/3587.07; Pro Dream 110/5267.05, 119/5349.15, 147/5368.90, 234/5705.43, 317/2931.00; Flash Fixed 640/2735.6, 1280/2988.3, 1920/3011.13, 2560/2909.18, 3200/2516.68; Flash Dream 640/2735.6, 976/2394.92, 1230/2378.05, 1529/2327.70, 1879/2350.58. The metric is the "arithmetic mean runtime over six real downstream tasks". The source comments say that for Flash, Fixed's "Iter 1--2 have no downstream evaluation" and Dream's "Iter 1 has no downstream evaluation", although values are plotted at those points. Values are the average held-out runtime in ms at the end of each round, against cumulative calls. The curves are **not monotone**, so the plotted value is the held-out runtime of that round's program, not a running best [inferred].

| Round | Fixed-Pro calls / ms | Dream-Pro calls / ms | Fixed-Flash calls / ms | Dream-Flash calls / ms |
|---|---|---|---|---|
| 1 | 110 / ~5267 | 110 / ~5267 (shared) | 640 / ~2736 | 640 / ~2736 (shared) |
| 2 | 220 / ~5364 | ~119 / ~5350 | 1280 / ~2988 | ~976 / ~2395 |
| 3 | 330 / ~5295 | ~147 / ~5367 | 1920 / ~3011 | ~1230 / ~2378 |
| 4 | 440 / ~4690 | ~234 / ~5706 | 2560 / ~2909 | ~1529 / ~2327 |
| 5 | 550 / 3587 | 317 / 2931 | 3200 / 2517 | 1879 / 2351 |

- Notable [digitized, inferred]: Dream-Pro spent only about 9 calls in round 2 and about 28 in round 3. All of its held-out gain comes from round 5, and its held-out runtime got worse in round 4. Dream-Flash's round-4 value (about 2327) is better than its reported final value (2350.6).
- **v1's text overstated the curve** [paper-v1:§4.1 p.8 vs Fig.3b, digitized]. v1 said the trajectories "diverge markedly: Dream-RSI consistently achieves superior downstream performance while requiring substantially lower cumulative compute across both Gemini-3.1-Pro and Gemini-3.7-Flash". v2 claims only the endpoint: "By the final round, Dream-RSI achieves lower average downstream runtime with fewer discovery-agent calls than Recursive Fixed Exploration on both backbones" [paper:§4.3], which the round-5 points support. For Pro, Dream-RSI's held-out runtime is *worse* than Fixed's in rounds 2–4: about 5349/5369/5705 ms against about 5365/5295/4691 ms, equal at round 2 within digitization error. Pro only overtakes in round 5. The claim holds for Flash in rounds 2–5.
- Per-round Dream-RSI call counts implied by Fig. 4b (exact, from the v1 source): Pro 110, 9, 28, 87 and 83. Flash 640, 336, 254, 299 and 350. The fixed per-round budget is 110 or 640.
- v1 characterised the two discovered solvers: "the program discovered by Gemini-3.1-Pro appears particularly well suited to large-scale matrices such as RCV1. In contrast, Gemini-3.7-Flash discovers a more general-purpose program that performs consistently across different problem scales" [paper-v1:§4.1 p.8]; v2 drops this. App. E.1 does not say which model discovered the listed solver [paper:App.E.1].

### 7.2 Mathematical optimization [paper:§4.2, Table 1]

Setup: Gemini-3.1-Pro through the Gemini CLI, 10 rounds for both methods, W = 10, B = 110 [paper:§4.2, Table 3]. Dream's math call counts are not reported. v1 said Dream stays within 1k generations ("fewer than 1,000 generations") against SimpleTES's 51,200 ("over 50× budget savings") [paper-v1:§1 p.3; §4.2 p.9–10]; v2 drops both.

| Method | LLM | Sum Diff (↑) | Auto Correlation (↓) | Circle Packing (↑) |
|---|---|---|---|---|
| AlphaEvolve | Gemini-2.0 Pro + Flash | – | 1.455700 | 2.635862 |
| AlphaEvolveV2 | Gemini-2.0 Pro + Flash | 1.121936 | – | 2.635983 |
| OpenEvolve | - | – | 1.460000 | - |
| CodeEvolve | - | – | – | 2.635980 |
| ShinkaEvolve | Mixed | – | 1.457800 | 2.635982 |
| TTS-Discovery | Qwen3-8B | – | – | 2.635983 |
| ThetaEvolve | Distilled-Qwen3-8B | – | 1.493000 | 2.635983 |
| EvoX | Gemini-3.0-Pro | – | 1.458900 | 2.635900 |
| SimpleTES | GPT-OSS-120B | 1.143975 | **1.453675** | 2.635983 |
| Recursive Fixed Exploration | Gemini-3.1-Pro | 1.144047 | 1.456001 | 2.635983 |
| **Dream-RSI** | Gemini-3.1-Pro | **1.145427** | 1.456375 | 2.635983 |

- The paper claims Dream-RSI is "outperforming SimpleTES and Recursive Fixed Exploration" on sum–difference, "matching the strongest reported result among the compared methods" on circle packing, and "remaining competitive" on autocorrelation [paper:§4.2]; overall it "matches or exceeds the best results among the compared baselines on two of three tasks" [paper:§1] (v1: "matches or surpasses strong baselines" [paper-v1:§1 p.3]). On autocorrelation, SimpleTES is state of the art and Dream-RSI is slightly worse than Fixed. Circle packing is presumably n = 26 [inferred from the 2.6359… values]. The README reports "2 of 3 at or above selected baseline" [code:zhengkid__Dream-RSI/README.md].

### 7.3 GPU kernel engineering (KernelBench) [paper:§4.1, Fig.3]

Setup: VGG16, LayerNorm, ConvDiv, ConvMax. Gemini-3.1-Pro, 9 rounds, W = 10, B = 110 [paper:Table 3]. Performance = 1/ms subject to correctness. Same protocol and initialization as the fixed baseline. New in v2: a PACEvolve baseline capped at 48 h of wall clock per task, with "Dream-RSI surpasses PACEvolve in kernel performance" [paper:§4.1; App.C, Fig.8]; Fig. 3's PACEvolve labels are 0.2002, 0.8264, 0.6452 and 0.2654 1/ms (presumably its best-so-far [inferred]). The discovered ConvDiv kernel is App. E.2: it fuses the convolution with the downstream pooling and reductions on register-resident values [paper:§4.1].

| Kernel | Reported result vs. Recursive Fixed Exploration |
|---|---|
| VGG16 | comparable performance with **2.43× fewer** generations |
| LayerNorm | comparable performance with **1.79× fewer** generations |
| ConvDiv | **2.09× higher** performance at a similar budget |
| ConvMax | **1.44× higher** performance at a similar budget |

Digitized curves [paper:Fig.3, digitized]: performance in 1/ms at the end of each round, and cumulative generations. The fixed baseline is at 110, 220, …, 990 generations. *Exact data (retry round 2):* the v1 arXiv source ships the plotted `.dat` files and a `manifest.json` built from the run directories (`Main_Text/raw_data/data/`). They agree with the digitized values, with three differences the plots do not show: the manifest's VGG16 Dream run continues to 513 and 598 attempts (no further gain) while the plot stops at 408; the Fixed runs continue beyond 990 (VGG16 0.5556 and ConvDiv 1.3038/1.3441 at 1100/1210); and for LayerNorm Fixed the manifest has 1.1364 at 770–880 and 1.1377 at 990–1100, while the plotted `.dat` has 1.1274 from 660 to 990. The run directories are named `…_no_direction_no_champion`.

| Kernel | Series | Cumulative generations | Performance (1/ms) |
|---|---|---|---|
| VGG16 | Dream-RSI | 110, ~126, ~146, ~163, ~186, ~222, ~276, ~356, ~408 | 0.368, 0.444, 0.450, 0.450, 0.455, 0.469, 0.513, 0.521, 0.532 |
| VGG16 | Fixed | 110…990 | 0.368, 0.449, 0.510, 0.521, 0.526, 0.529, 0.535, 0.535, 0.538 |
| LayerNorm | Dream-RSI (8 markers plotted) | 110, ~188, ~268, ~348, ~381, ~448, ~501, ~553 | 1.030, 1.066, 1.105, 1.121, 1.121, 1.121, 1.122, 1.122 |
| LayerNorm | Fixed | 110…990 | 1.030, 1.098, 1.114, 1.117, 1.117, 1.127, 1.127, 1.127, 1.127 |
| ConvDiv | Dream-RSI | 110, 220, 307, 387, 437, 529, 609, 700, 786 | 0.427, 0.625, 0.855, 1.403, 1.488, 1.499, 1.770, 1.880, 1.898 [exact labels, Fig.7] |
| ConvDiv | Fixed | 110…990 | 0.426, 0.506, 0.530, 0.530, 0.608, 0.877, 0.909, 1.282, 1.282 |
| ConvMax | Dream-RSI | 110, ~220, ~295, ~372, ~407, ~488, ~560, ~610, ~668 | 0.237, 0.246, 0.258, 0.276, 0.278, 0.279, 0.279, 0.279, 0.431 |
| ConvMax | Fixed | 110…990 | 0.237, 0.246, 0.277, 0.294, 0.298, 0.299, 0.299, 0.300, 0.300 |

How the headline ratios are formed [digitized, inferred]:

- 2.43× = 990/~408 and 1.79× = 990/~553. These compare total budgets at the *final* points, where Dream-RSI is slightly *below* Fixed: VGG16 0.532 against 0.538, LayerNorm 1.122 against 1.127.
  - Measured as generations-to-target instead, the ratio is smaller: the budget at which Fixed first matches or exceeds Dream-RSI's final value. VGG16: Fixed first reaches at least 0.532 at 770 (0.535), so 770/408 ≈ 1.9×. LayerNorm: Fixed first reaches at least 1.122 at 660 (1.127), so 660/553 ≈ 1.2×. Fixed's LayerNorm curve is flat from 660 to 990.
  - The introduction's "reaches similar speeds with 1.79×–2.43× fewer generations" [paper:§1] (v1: "reaches target execution speeds" [paper-v1:§1 p.3]) therefore describes total spend, not time-to-target.
- 2.09× = 1.898 / 0.909, which is Dream-RSI at 786 generations against Fixed at **770**. This is a genuinely matched budget. Fixed jumps to 1.282 at 880 and ends there at 990, which gives about 1.48× against Fixed's final point.
- 1.44× = 0.431 / 0.299, which is Dream-RSI at about 668 against Fixed at 660. Against Fixed's 990 endpoint (0.300) it is still about 1.44×, so the ConvMax ratio does not depend on which Fixed point is used.
- The ConvMax gain comes from a single jump in the last round (0.278 → 0.431). Before that jump, Dream-RSI's curve sat at or below Fixed's for most of the budget. For example, it was 0.278 at about 610 generations, while Fixed was about 0.299 from 660 generations on.
- The README image claims "4 / 4 kernels improved", and its alt text says "4 of 4 kernels improved" [code:zhengkid__Dream-RSI/assets/stats-light.svg; README.md]. On VGG16 and LayerNorm the "improvement" is lower cost at slightly lower final performance, not a higher score.

**Exploration pacing on ConvDiv** [paper:§5, Fig.7; exact figure labels]:

| Round | E0 | E1 | E2 | E3 | E4 | E5 | E6 | E7 | E8 |
|---|---|---|---|---|---|---|---|---|---|
| Round-best performance (1/ms) | 0.427 | 0.625 | 0.855 | 1.403 | 1.488 | 1.499 | 1.770 | 1.880 | 1.898 |
| Evaluated attempts in round | 110 | 110 | 87 | 80 | 50 | 92 | 80 | 91 | 86 |

The paper's reading: "as performance improves, it initially conserves discovery compute (e.g., reducing the number of evaluated attempts from 110 to 50). When progress subsequently plateaus, it increases exploration effort again, coinciding with further performance gains" [paper:§5]. The attempts sum to 786, Table 2's compute for this run.

### 7.4 Ablation: history as replay versus history as written guidance [paper:§5, Fig.6]

Design: past trajectories are abstracted into "high-level directional insights" and injected into the discovery prompt. This is applied to both Fixed and Dream-RSI on ConvDiv. Claim: "explicit directional guidance yields lower final performance for both methods on ConvDiv. This suggests that such guidance may constrain exploration" [paper:§5]. v1 was stronger: guidance "consistently underperforms its unguided counterpart across both paradigms under equivalent discovery budgets … tends to over-constrain the search space and impede diverse exploration" [paper-v1:§5.1 p.11].

Digitized [paper:Fig.6, digitized]. Only the two *unguided* curves (Dream-RSI and Fixed) have a plotted point at 0 generations, at about 0.118 1/ms. Presumably that is the initial program [inferred]. The two guidance curves start at the shared round-1 point, (110, 0.427). Fig. 3 has no 0-generation points.

| Series | Cumulative generations at round ends | Performance (1/ms) |
|---|---|---|
| Dream-RSI | 110, 220, 307, 387, 437, 529, 609, 700, 786 | 0.426 … 1.898 (as in Fig. 7) |
| Dream-RSI + Guidance | 110, ~190, ~260, ~349, ~439, ~502, ~582, ~622, ~684, ~764, ~844 | 0.426, 0.916, 1.073, 1.117, 1.302, 1.367, 1.367, 1.356, 1.370, 1.374, 1.489 |
| Fixed | 110 … 990 | 0.426, 0.507, 0.531, 0.531, 0.610, 0.877, 0.909, 1.283, 1.283 |
| Fixed + Guidance | 110 … 880 | 0.426, 0.517, 0.585, 0.620, 0.636, 0.634, 0.634, 0.634 |

Nuance [digitized, inferred]: guidance *helps early*. Dream+Guidance leads Dream-RSI at about 190–350 generations, and Fixed+Guidance leads Fixed in rounds 2–5. Guidance then plateaus, and at the final or equal budget the unguided variants win: Dream 1.898 against Dream+G about 1.37–1.49, and Fixed 1.283 against Fixed+G 0.634. The overview doc's phrase "did worse than no guidance at all" [doc] holds at equal final budgets, which is what v2's "lower final performance" claims.

### 7.5 Harness optimisation (new in v2) [paper:§4.4, Fig.5]

Setup: the Meta-Harness setting, seeded with the Terminus-Kira harness. Each harness is scored on the full Terminal-Bench 2.1 suite (89 tasks, two independent runs per task). Claude Opus 4.8 with Claude Code is both the discovery and the policy-development agent, and Opus 4.8 also runs the evaluation; Meta-Harness is reproduced with Opus 4.8 [paper:§4.4]. T = 9, W = 4, B = 16 = 4 × 4 [paper:Table 3]. No Fixed arm (cost) [paper:§4].

Result: best-so-far score 0.7247 → 0.8090 (generation axis 0–60) [paper:Fig.5a]. Full-suite comparison: Claude Code 0.75, Terminus-Kira 0.72, Meta-Harness 0.79, Dream-RSI 0.81 [paper:Fig.5b]. The introduction calls it "over 8 points" [paper:§1] (0.8090 − 0.7247 = 0.084 [computed]). The score is the search objective itself, not a held-out set [inferred].

### 7.6 Replay-world accumulation (new in v2) [paper:§5, Table 2]

ConvDiv, replaying all history against replaying only the current round's tree:

| Variant | Performance (1/ms) ↑ | Compute (calls) ↓ |
|---|---|---|
| Dream-RSI (All History) | 1.90 | 786 |
| Current History Only | 1.59 | 430 |

The paper's reading: "Accumulating replay worlds may help learn a more general exploration policy and avoid overly conservative exploration associated with fitting to a single world" [paper:§5]. Single runs, and the compute differs (786 vs 430), so this is not an equal-budget comparison [inferred].

---

## 8. Known weaknesses and critiques

1. **Replay has limited support.** It can only reveal what was recorded. "Can reach new ideas: No, only recorded ones" [doc]. Plans beyond recorded width or depth "cannot earn replay reward" [paper:App.D.2 L2:210–212]. A policy that would explore somewhere new cannot be scored fairly [doc].
2. **The replay ceiling biases toward frugality** [inferred]. In both versions of Eq. 1 the quality term is capped at the best recorded score (v2: attainment ≤ 1 = S_i), so replay mostly rewards reaching the recorded best with fewer probes. This fits the very small early Dream-RSI rounds: about 9 calls in Lasso-Pro round 2, and 16–23 calls in VGG16 rounds 2–5 [digitized]. It could under-explore when the unrecorded frontier is valuable.
3. **Counterfactual contamination** [inferred]. Recorded outcomes were produced under the recording policy's context: the agent reads *all* sibling attempts and history (Listing 1). Replay assumes a child's outcome does not depend on which other nodes a different policy would have revealed. The off-policy estimate is therefore biased, and the paper never validates replay-versus-online agreement.
4. **Selection can overfit past searches** [doc]. The argmax is over the mean replay score on the *same* worlds the developer inspected, with no held-out worlds and no noise margin. At t = 1 there is a single world [paper:§3; inferred]. The "no worse than current" guarantee holds only on replay [paper:§3]. The overview doc suggests RRSI-style guards: held-out worlds, and ignoring wins smaller than the noise [doc].
5. **Single runs, no error bars; some gains are small** [doc]. Against Fixed, the math gaps are +0.00138 on sum–difference (1.145427 vs 1.144047) and −0.00037 on autocorrelation (1.456375 vs 1.456001, i.e. worse), with an exact tie on circle packing [paper:Table 1; computed]. The ConvMax gain is a single last-round jump [digitized].
6. **The average hides the per-dataset picture** [doc; computed]. Lasso-Pro is slower than Fixed on 5 of 6 datasets, and its average gain comes from RCV1 (v2 §4.3 now says so). Under a geometric mean, which is what the *search* score itself uses, Dream-Pro (179.4 ms) is worse than Fixed-Pro (159.1 ms).
7. **"162× fewer calls than SimpleTES" compares different models** (gpt-oss-120b against Gemini) [doc]; v2 adds "although these comparisons use different model backbones" [paper:§4.3]. The fair comparison is against Fixed: 1.7× (550/317).
8. **How the kernel ratios are formed matters** [digitized; §7.3]. The "fewer generations" ratios (2.43×, 1.79×) divide Fixed's *total* budget by Dream-RSI's, and Dream-RSI ends slightly *below* Fixed. Measured as generations-to-target, they are about 1.9× and 1.2×. The "similar budget" ConvDiv ratio (2.09×) is a fair matched-budget comparison: 786 against 770 generations. Against Fixed's 990-generation endpoint it shrinks to about 1.48×, because Fixed jumps at 880. The ConvMax ratio is about 1.44× either way.
9. **The objective is still not fully specified.** v2's Eq. 1 is the prompt's `pareto.auc − λ·parallel_penalty` over a ρ sweep (§4.1, §4.3), and Table 3 gives T, W, B and M, but λ, K1 and K2 are not reported. In v1, Eq. 1 (β1, β2) and the prompt's sweep were different and no coefficient, M or developer model was given. The official code has not been released [paper; code:README]. v2's Eq. 1 is close to what the v1 arXiv source held as an **unpublished** method section (`Main_Text/method.tex`, commented out of `main.tex`) that defines attainment as a_{i,β} = clip(S_{i,β}/G_i, 0, 1), calls the AUC the "normalized area" of the probe–attainment frontier, gives J(π) = mean AUC − λ·mean P_i and says "We select the highest-scoring valid controller across proposal rounds"; λ and the beta grid are still not given, and its appendix reference (`app:controller`) does not exist [arXiv source, retry round 2].
10. **Formalism gaps** [inferred]: `C ⊆ A(T)` as a set conflicts with opening several branches per round (§3.1). v1's M-versions off-by-one is fixed in v2 (§3.2). It is unspecified whether each round's root is the seed or the best-so-far.
11. **Confounded recursion** [inferred]. Both methods also grow the agent's textual history context across rounds. Dream-RSI uses fewer calls per round, so its history is smaller. Round-over-round gains mix policy effects with context effects.
12. **Held-out evaluation exists only for Lasso.** Math, kernel and harness results are reported on the search objective itself [paper:§4]. The overview doc summarises this as "Held-out datasets in one domain; selection can still overfit" [doc].
13. **Guidance ablation nuance**: early rounds favour guidance. The claim holds at the final or equal budget (§7.4) [digitized]. The ablation is run on one kernel only (ConvDiv) and is a single run [paper:§5]. The same holds for the history-accumulation ablation (§7.6).
14. **Some text claims overstated the figures in v1; v2 softens them** [paper vs digitized figures]. v1's "Dream-RSI consistently achieves superior downstream performance" on Lasso [paper-v1:§4.1 p.8] does not hold for Pro in rounds 2–4 of Fig. 4b (§7.1); v2 claims only the final round. The pacing story ("as performance improves, it initially conserves discovery compute … When progress subsequently plateaus, it increases exploration effort again" [paper:§5]) is still read off a single ConvDiv run of 9 rounds. The paper shows no causal ablation of the beta or `plan_grid` machinery.
15. **The paper's formalism does not fully match its own prompt.** In v2, Eq. 1 now matches Listing 2's beta-sweep reward, but the §3 root rule ("earliest-created child") still differs from Listing 2's addressable root cells, and §3 never mentions `plan_grid` or its support limits in replay (§3.4). The paper never says which root rule produced the results [paper:§3 vs App.D.2]. (In v1, Eq. 1 differed from the prompt too.)
16. **"Open code" is not yet true.** The overview doc says "RRSI and Dream-RSI both have open code" [doc]. As of 2026-09-24 the official repo still lists "Full codebase", "Reproduction scripts" and "Discovered programs" as "⏳ Being prepared" [code:zhengkid__Dream-RSI/README.md]. Re-checked on 2026-09-29 with `git ls-remote`: `main` is still commit `4149ea9` (tree: README, CITATION.cff, assets, the PDF); the two open PRs add unrelated files (#2) and a list of independent community reimplementations to the README (#5). Only the PDF, including the solver listing (v1 App. C, v2 App. E.1), is public. Not re-checked for the v2 refresh.

---

## 9. Reproduction blueprint (domain-agnostic, CPU-only)

### 9.1 Minimal components and interfaces [inferred design, grounded in §3–§5 above]

```python
# ---- object-level (frozen during a run) ----
class Evaluator(Protocol):                         # fixed grader; larger = better
    def evaluate(self, artifact_path: Path, workdir: Path) -> EvalResult: ...
@dataclass
class EvalResult:
    score: float | None; evaluated: bool; valid: bool; fail_class: str  # "ok" | "compile_other" | ...
    error: str | None; n_valid: int | None; n_total: int | None; diagnostics: dict; cost: dict  # tokens, seconds

class DiscoveryAgent(Protocol):                    # frozen: `claude -p` wrapper or deterministic mock
    def attempt(self, parent: Node, workspace: Path, context: DiscoveryContext) -> Proposal: ...
    # DiscoveryContext: problem_file, sibling attempts (proposal.md + score.json + error.txt),
    #   history_dir (H_{t-1}), baseline_dir, direction_guidance (str | None)   [paper:App.D.1]

# ---- tree / world ----
@dataclass
class Node:                                        # [paper:§3]
    id: str; parent_id: str | None; branch: int; attempt: int; seq: int      # seq = creation order
    snapshot_ref: str; artifact_ref: str; eval: EvalResult; tags: dict
class DiscoveryTree:                               # root + chains; JSON-serializable; = a replay world
    root: Node; nodes: dict[str, Node]
    def children(self, node_id) -> list[Node]; def branches(self) -> list[list[Node]]

# ---- the shared decision interface (identical online & replay) ----   [paper:§3, App.D.2 API]
class Question(Protocol):
    max_parallelism: int; baseline_score: float
    def reset(self) -> None
    def observed(self) -> dict[str, Observation]           # revealed prefix only
    def legal_actions(self) -> list[str]                   # unopened roots + frontiers of opened branches
    def legal_roots(self) -> list[str]
    def opened_branches(self) -> list[int]
    def meta(self, cell_id: str) -> CellMeta               # branch, attempt, parent_id, seq, tags
    def probe_batch(self, cells: list[str], on_reveal=None) -> list[Observation]
class OnlineQuestion(Question): ...   # runs DiscoveryAgent+Evaluator in a W-worker pool; appends to a live tree
class ReplayQuestion(Question): ...   # reveals recorded children; root_mode='earliest' (paper §3: root → earliest-created
                                      # unrevealed branch) | 'addressable' (Listing 2 API: specific root cell);
                                      # missing continuation → nothing revealed; plan ∩ trace support bounds legality;
                                      # tracks N, k, per-round batch sizes

# ---- the thing being improved ----
class ExplorationPolicy(Protocol):                  # loaded from a code file (hot-swappable)
    config: dict                                    # {"beta": float}
    def plan_grid(self, ctx: GridPlanningContext) -> GridPlan   # pre-episode; never sees current outcomes
    def solve(self, q: Question, budget: int | None = None) -> SimResult

# ---- guards ----
class PrefixGuard:     # wraps Question passed to policy: no access to hidden tree, best_so_far, budget_spent;
                       # validates batch legality, ≤ W, no duplicates, no parent+child; per-episode reset;
                       # runs policy in a subprocess sandbox with a timeout; static lint for forbidden attrs
class PolicyStaticCheck:  # plan_grid present & returns GridPlan on all paths; NAME/class present; imports allowed

# ---- meta level ----
class ReplayObjective(Protocol):
    def score_episode(self, revealed: list[Node], N: int, k: int) -> float
class Eq1Objective(ReplayObjective): beta1: float; beta2: float        # [paper-v1 Eq.1]
class ParetoSweepObjective: beta_grid: list[float]; lam: float          # [paper Eq.1 (v2); App.D.2]; area over probes
class ReplayEvaluator:
    def evaluate(self, policy, worlds: list[DiscoveryTree]) -> PolicyReport   # V^m, per-world V_i, traces.jsonl
class PolicyDeveloper(Protocol):                    # LLM (`claude -p`, Listing-2-style prompt) or mock mutator
    def revise(self, history: DevHistory, feedback: PolicyReport, out_file: Path) -> Path
class Selector:                                     # argmax over versions incl. current (paper)
    def select(self, reports: list[PolicyReport]) -> int    # optional: held-out worlds + noise margin (extension)

# ---- orchestration & logging ----
class DreamRSILoop:     # §3.2 outer loop; writes live_cycle_manifest.json, beta_sweep.json, r####_*/ dirs
class CostMeter:        # agent calls (primary cost), rounds, tokens, wall-clock, replay CPU time
class Baselines:        # ParallelRefine (Recursive Fixed Exploration); +Guidance variant (summarizer → direction_guidance)
```

Key invariants to unit-test [inferred]:

- (a) Replaying the recording policy on its own tree reproduces the online trajectory exactly: the same revealed set, `N`, `k` and best score.
- (b) Replay is deterministic, and repeated evaluation gives identical `V`.
- (c) `V^{m*} ≥ V^0` always.
- (d) The guard raises on any access to unrevealed data.
- (e) A root probe always opens the earliest-created unrevealed branch.
- (f) Selecting an exhausted leaf reveals nothing but increments `k`.
- (g) v1's Eq. 1 on a hand-computed toy matches exactly. Use the doc's 3-branch × 5-attempt, 3-worker example with β1 = 0.010, β2 = 0.005. The data are taken from the overview HTML's demo script (`replayDemo()`) [doc]:
  - Branch scores: A = [0.40, 0.52, 0.61, 0.63, 0.64]; B = [0.35, 0.37, 0.36, 0.38, 0.37]; C = [0.30, 0.45, 0.58, 0.70, 0.71].
  - "Refine everything" reveals all 15 cells in 5 rounds of 3. Best 0.71, N = 15, k = 5, V = 0.71 − 0.15 + 0.005·3 = **0.575**.
  - "Drop the flat branch" uses rounds {A1,B1,C1}, {A2,B2,C2}, {A3,C3}, {A4,C4}, {C5}. Best 0.71, N = 11, k = 5, V = 0.71 − 0.11 + 0.005·2.2 = **0.611**. This one wins at the default settings.
  - "Stop early" uses rounds {A1,B1,C1}, {A2,B2,C2}. Best 0.52, N = 6, k = 2, V = 0.52 − 0.06 + 0.005·3 = **0.475**. [computed]
  - The demo excludes the root: best starts at 0 and the root is not counted in N. That matches Eq. 1 when s_r ≤ min recorded score [inferred].

### 9.2 CPU-only experiment design

**Track A: synthetic discovery worlds** (mock agent, deterministic, cheap, many seeds). Generative model [inferred]: each branch gets a latent type such as steady improver, late bloomer, early plateau, hard-fail, or repairable-fail with recovery. A child's score is `f(parent, type, depth) + noise`, with typed failures (`fail_class`, `error`). An optional slow "history context" drift across rounds imitates agent learning. Because the generator is known, *true online* performance of any policy can be measured, which the paper cannot do.

**Track B: small real task with `claude -p`** as the discovery agent (policy developer = `claude -p`, or the mock). Suitable CPU tasks with cheap, deterministic evaluators:

- sum–difference Γ(A), per App. B.3;
- circle packing n = 26 with a feasibility check;
- a tiny numpy Lasso-path solver with the App. B.3 correctness gate (`F_k ≤ F_k(sklearn) + 1e-6`) and an inverse geometric-mean-runtime score.

Budget example: W = 4 workers, a 4 × 5 grid (20 calls/round), 4–5 rounds, 3 seeds [inferred].

| # | Claim demonstrated | Measure | Baseline / ablation | Confirming outcome |
|---|---|---|---|---|
| E1 | Replay is an exact, near-free simulator | Trajectory equality for the recording policy; LLM calls during replay; replay wall time per episode | none | Identical trajectories; 0 agent calls; milliseconds per episode |
| E2 | Off-policy replay ranks policies usefully (Track A) | Spearman ρ between replay V and true online value across about 20 hand-written policies; stratify by support coverage | none | ρ clearly > 0; the ranking degrades for policies that want out-of-support depth or width (the critique in §8.1) |
| E3 | Main claim: dreaming improves the quality/cost trade-off | Best score against cumulative agent calls; calls to reach a target; AUC; final best at equal budget | Recursive Fixed Exploration (ParallelRefine, same per-round cap) | Dream-RSI reaches comparable or better quality with fewer calls. Report mean ± CI over seeds |
| E4 | Safe selection (current policy is a candidate) | V^{m*} − V^0 per iteration; how often a new version is chosen | Selection without the incumbent | Always ≥ 0 with the incumbent; without it, occasional regressions |
| E5 | Replay beats written advice | Final best at equal budget; branch diversity (distinct mechanisms/types opened); early versus late rounds | Fixed, Fixed+Guidance, Dream, Dream+Guidance. Guidance comes from an LLM or mock summarizer injected via `direction_guidance`; in Track A, model advice as biasing new roots toward the historically best type | Guidance lowers diversity and final quality at equal budget, and may help early (per the §7.4 nuance) |
| E6 | Learned pacing | Attempts per round against round-over-round improvement; correlation between a plateau indicator and next-round effort | Fixed (constant 20/round) | Effort drops while improving and rises after a plateau (qualitatively matching Fig. 7) |
| E7 | One objective, explicit dials | Sweep β1, β2 (v1 Eq. 1) and the policy `beta` (Pareto sweep): probes, rounds, mean batch size, best | none | Higher β1 gives fewer probes; higher β2 gives larger batches and fewer rounds; `beta` traces a monotone work/attainment frontier |
| E8 | No-peeking constraint matters | Replay V and true online value for an "oracle" policy that reads hidden scores, against an honest policy | PrefixGuard on/off | Without the guard the oracle's replay V is inflated and fails online; the guard blocks it |
| E9 | Overfitting risk (critique; doc's "they need each other") | Replay V on train worlds versus held-out worlds for the selected policy; online outcome | Paper selector against a selector with held-out worlds and a noise margin (RRSI-style) | The gap between train and held-out replay V grows with M; the guarded selector narrows it |
| E10 | Support limitation | True value against replay value for a "go deeper" policy when recorded depth is shallow | Worlds recorded with deeper grids | Replay underestimates such policies; the estimate recovers when support covers their plan |

### 9.3 Suggested framework defaults (all [inferred], not from the paper)

- Normalize scores per world, for example `(s − s_r)/(max_{T_i} s − s_r)`, before applying v1's Eq. 1, so that averaging across worlds and tasks is scale-free. v1 averages raw `V_i`; v2's Eq. 1 normalizes by `S_i` itself.
- β1 = 0.01 and β2 = 0.005 on the normalized scale, mirroring the doc's toy values (v1 Eq. 1 only).
- K2 = ∞ (the paper's replay uses `budget=None`); M = 4 (v2's value); beta grid {0.2, 0.4, 0.6, 0.8, 1.0} (v2 uses ρ ∈ {0, 0.25, 0.5, 0.75, 1}); λ = 0.1 (v2 gives none).
- Default policy beta = 0.6, as the paper suggests for insufficient evidence.

---

## 10. Capability checklist (overview doc → framework component)

| # | Capability or claim attributed to Dream-RSI in the overview doc | Component(s) |
|---|---|---|
| 1 | "Every attempt resumes from a saved earlier attempt (or starts fresh), and its code, diagnostics and score are stored as a node in a tree" | `Node`, `DiscoveryTree`, workspace snapshots in `OnlineQuestion`, `DiscoveryAgent`, `Evaluator` |
| 2 | "Record every attempt and its result … test a new search strategy by replaying it over the map instead of running it for real" | `ReplayQuestion`, `ReplayEvaluator` |
| 3 | "Grow the library. The finished tree is added to the collection of past searches, called replay worlds" | `DreamRSILoop` world pool `H`, trace_pool persistence, `live_cycle_manifest.json` |
| 4 | Strategy "written as code"; "At every step the strategy sees the tree so far and picks a batch of up to W starting points" | `ExplorationPolicy.solve`, `Question` API, `max_parallelism` |
| 5 | "Each pick either opens a new branch from the start or extends the latest attempt on an existing branch. An empty batch means stop" | `legal_roots` / `legal_actions`, batch validation in `PrefixGuard`, termination in both `Question` implementations |
| 6 | "In replay, picking a node simply reveals the attempt that was recorded after it" | `ReplayQuestion.probe_batch` (root → earliest-created branch; missing → nothing) |
| 7 | "Strategies must decide using only what they have revealed so far. They can't peek at unrevealed scores or use known best answers" | `PrefixGuard` (sandbox, attribute firewall, static lint), per-episode reset |
| 8 | "replay score = best result found − β1 × attempts used + β2 × attempts per round" | `Eq1Objective` (v1's Eq. 1; the doc quotes it) plus `ParetoSweepObjective`, the closest to v2's Eq. 1 and the prompt |
| 9 | "Dream. A separate AI 'policy developer' reads how the current strategy behaved in replay, rewrites the strategy code, and repeats many times. Each version is scored on every past search" | `PolicyDeveloper` (LLM via `claude -p` or mock), `ReplayEvaluator` over all worlds, `policy_execution_traces.jsonl`, `r####_*/` history |
| 10 | "Deploy the best version, including the current one as a candidate, for the next real search" | `Selector` (argmax including the incumbent), `DreamRSILoop` deploy step |
| 11 | "What stays fixed: The models, the coding agent and the evaluator" | Frozen `DiscoveryAgent` / `Evaluator` configuration; only the policy file is hot-swapped |
| 12 | "How it decides: Best average score when replayed over every past search" | `ReplayEvaluator` mean over `H`; `Selector` |
| 13 | Online versus replay table (runs agent vs reveals; random vs fixed; model calls vs nearly free; can vs cannot reach new ideas) | `OnlineQuestion` vs `ReplayQuestion` behind one interface; `CostMeter`; support-coverage metric (E1, E2, E10) |
| 14 | Baseline "Recursive Fixed Exploration … the strategy never changes. Both start from a simple strategy of several independent workspaces"; 10 × 11 = 110 and 32 × 20 = 640 budgets | `Baselines.ParallelRefine`, `GridPlan` caps, identical per-round budgets |
| 15 | Lasso, math and kernel results (e.g. "317 vs 550 agent calls") | Experiment harness plus task plug-ins (Evaluator/problem file); E3 reproduces the qualitative claim on CPU tasks |
| 16 | "Replay beats advice. Summarizing past searches into written guidance … did worse than no guidance at all" | Guidance summarizer → `direction_guidance` in `DiscoveryContext`; E5 ablation |
| 17 | "The strategy learned to pace itself … cut attempts per round from 110 to about 50 … then spent more again once progress stalled" | Policy `beta` + `_schedule`, cross-cycle default-beta rule in the developer prompt, `plan_grid`; per-round effort logging (E6) |
| 18 | "It treats search history as an executable simulator, not as notes for the next prompt or as training data" | `ReplayQuestion` + `ReplayEvaluator` (contrast with the E5 guidance variant) |
| 19 | "improves how the system searches … almost for free between real runs" | Offline dreaming stage in `DreamRSILoop`; `CostMeter` separates agent calls from replay CPU time |
| 20 | "puts quality, spending and parallelism into one explicit objective, with a dial the strategy tunes across cycles" | `Eq1Objective` / `ParetoSweepObjective`; policy `beta` dial and beta sweep (`beta_sweep.json`); E7 |
| 21 | "The same approach worked across … numerical code, math constructions and GPU kernels" | Domain-agnostic `Evaluator` / `DiscoveryAgent` plug-ins; Track B tasks |
| 22 | Watch out: "Replay can only test choices that were actually tried" | Support tracking (`trace_branch_count` / `trace_refine_count`), out-of-support plans get no reward, E10 |
| 23 | Watch out: "Choosing the strategy that scores best on past searches can overfit those searches" | Optional `Selector` extension with held-out worlds and a noise margin; E9 |
| 24 | Watch out: results are single runs without error bars; "162×" compares different models | Multi-seed harness with CIs; compare only against same-model Fixed (E3) |
| 25 | "They also need each other": keep some past searches aside, ignore wins smaller than noise; RRSI evaluations could be replayed | `Selector` guard hooks; `ReplayQuestion` reusable by other methods' evaluators |
| 26 | "Log experiments as a tree. Record each attempt's parent, change, score and cost. That history becomes a simulator" | `Node` fields (`parent_id`, `proposal.md`, `eval`, `cost`), `DiscoveryTree` JSON |
| 27 | Glossary: discovery tree, replay world, exploration strategy | `DiscoveryTree`, world pool `H`, `ExplorationPolicy` |
| 28 | "Dream-RSI automates the strategy … turns that strategy into code and improves it cheaply by replaying past runs" | `ExplorationPolicy` as code + `PolicyDeveloper` + `DreamRSILoop` |
| 29 | "Search shape: A tree of parallel branches"; "Cost control: Cost and parallelism in the objective"; "Scale: Hundreds to thousands of agent calls" | Worker pool with W workers, `GridPlan`, objective terms, `CostMeter` |
| 30 | Interactive-demo notion: compare strategies such as "Refine everything", "Drop the flat branch" and "Stop early" on one recorded tree, with β1 and β2 sliders | A replay CLI or notebook over one world: a table of Best / Attempts / Rounds / Score per policy, and a per-cell reveal-round map. The demo's exact tree and expected scores (0.575 / 0.611 / 0.475) are in §9.1 (g) |
| 31 | "Tested on: A Lasso solver, three math problems and four GPU kernels"; "Models used: Gemini 3.1 Pro and Gemini 3.7 Flash via Gemini CLI" | Task plug-ins (`Evaluator` + problem file per task); `DiscoveryAgent` adapter for any headless coding-agent CLI (`claude -p` here, standing in for Gemini CLI) |
| 32 | "Who improves the strategy: An AI, automatically" (side-by-side table) | `PolicyDeveloper` + `DreamRSILoop`, with no human approval step; per-revision artifacts under `r####_*/` for audit |
| 33 | "Dream-RSI replays results that were really measured" (section "The grader decides everything") | `ReplayQuestion` reads only `Evaluator`-recorded observations; no LLM judge anywhere in `ReplayObjective` |
| 34 | "Guard against overfitting: Held-out datasets in one domain; selection can still overfit" | Optional held-out `Evaluator` per task, which scores the deployed best artifact on unseen instances (Lasso-style), plus the `Selector` extension (E9) |
| 35 | "Read the rows, not just the average … Every discovered solver beats scikit-learn and glmnet on all six datasets" | Harness reports a per-instance table plus arithmetic and geometric means, with win/loss counts against baselines (§7.1 statistics) |
| 36 | "Keep rule: Best replay score over past searches" | `Selector.select` = argmax of mean replay V over all worlds, incumbent included |
| 37 | "Evidence: Paper with controlled comparisons" | `Baselines.ParallelRefine` run with the same agent, evaluator, initialization and per-round cap (E3) |
| 38 | "RRSI and Dream-RSI both have open code, but their experiments used frontier models and many evaluations" | Contradicted by the official README: the code is not yet released (§8 item 16). The framework reimplements the method from the paper; the CPU-only Track A/B design (§9.2) stands in for full-scale reproduction |
| 39 | "…and the best kernel kept improving each round" (pacing finding) | Per-round best and per-round attempt logging in `live_cycle_manifest.json`; E6 |
