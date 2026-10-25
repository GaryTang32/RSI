# Meta-Harness: end-to-end methodology (for `rsi.improve(..., method="metaharness")`)

Sources, in order: the paper, Lee et al., "Meta-Harness: End-to-End Optimization of Model Harnesses",
arXiv 2603.28052v1 (the only version), read in full from arxiv.org/html and cited as [paper §x], [paper Alg. 1],
[paper Table n], [paper App. x]. Next, the authors' release as summarised in
`docs/methods/metaharness-solpi/paper-spec.md` Part A, cited as [release: ...]. Last, `implementation.md`,
`claims-audit-metaharness.md` and `rsi/metaharness/{config,loop,api,store,frontier,proposer,validate}.py` for what
this package does. "Not specified in the paper" means the paper does not say. "rsi choice" means only this package
does it.

## 1. In one paragraph

An LLM application is a frozen model plus a harness: the code that decides what to store, retrieve and present
to the model [paper Abstract, §1]. Meta-Harness searches over harness code with an outer loop. A coding-agent
proposer (Claude Code with Opus 4.6 [paper §3], "max reasoning" [paper §4.1]) uses terminal tools such as
`grep` and `cat` to query a growing filesystem holding the source code, scores and execution traces of every
earlier candidate. It then writes new harnesses [paper §3, Fig. 2]. The candidates are scored on a search set and
everything is logged back. The outer loop is kept minimal on purpose. It has no parent-selection rule [paper §3],
no predefined mutation operators [paper §3], and no fixed scaffold, archive of discoveries or persistent memory
mechanism [paper §2]. It still maintains a population and a Pareto frontier over evaluated harnesses
[paper §3]. Every candidate that passes interface validation is evaluated and stored [paper Alg. 1]. The
objective is the expected reward with the model fixed: `H* = argmax_H E_{x~X, tau~p_M(H,x)} r(tau, x)`
[paper §3, an unnumbered display equation]. When there are several objectives, such as accuracy and context cost,
candidates are compared by Pareto dominance [paper §3]. The loop runs for a fixed number of iterations, then
evaluates the Pareto frontier once on the test set [paper §3]. The paper's key claim is that raw execution
traces, rather than scores or LLM summaries, are "the key ingredient" [paper Table 3].

## 2. Inputs and setup

Before round 1 you need:
- **Artifact**: a harness the proposer may rewrite in full. In the paper each harness is a single-file Python
  program [paper §3]. The base model `M` is always frozen [paper §3]. The proposer gets a "minimal
  domain-specific skill" that says where to write harnesses, how to inspect earlier ones and their traces, and
  which files it may and may not modify [paper §3]. The skill text is "the strongest lever" [paper App. D].
- **Splits**: a search set gives all of the proposer's feedback, and it "never sees test-set results"
  [paper §3]. The test sets are held out until the final evaluation [paper §4.1]. Advice from [paper App. D]:
  make the search set hard for the baseline, either by keeping the examples it gets wrong or by picking a
  diverse set of difficult ones. Keep it small enough for about 50 full evaluations per run: 50-100 examples
  for classification, 88 problems for math. §4.2 says 250 math problems instead. In rsi: `Config.search_split="evolve"`,
  and `test_splits=("test",)` is read once by `finalize()`. Keep `holdout`/`ood` sealed for your own transfer
  check (rsi choice).
- **Grader and traces**: evaluation is automated and runs outside the proposer [paper App. D]. Traces contain
  prompts, tool calls, model outputs and state updates [paper §3], and are logged as navigable JSON
  [paper App. D]. In rsi, `Domain.execute` must return a rich `Execution.trace`, and `grade` returns a score
  and a feedback string.
- **Cost objective** (optional; Pareto [paper §3]): a per-task cost in `Trial.meta[Config.cost_metric]`, or tokens.
- **Models**: `llm_task` (the frozen model `M`) and `llm_propose`. With `proposer_kind="auto"`, a *bare*
  `ClaudeCLI` gives the coding-agent proposer. Any other LLM gives the one-completion `RewriteProposer`, and
  that includes a `ClaudeCLI` wrapped by `cache_dir`/`CachedLLM`. `None` or a plain `MockLLM` gives the offline
  `MockProposer`. To get the coding agent when `cache_dir` is set, pass `proposer_kind="agent"`.
- **Budgets**: `iterations`, `k`, `eval_budget` (the most candidate evaluations allowed), `proposer_timeout_s`.
  `Config` has no dollar cap.
- **Initial population H** [paper Alg. 1 l.2]: text classification uses zero-shot, few-shot, ACE and MCE
  [paper §4.1]. Math uses zero-shot, few-shot and ACE [paper §4.2]. TerminalBench-2 (TB2) uses Terminus 2 and
  Terminus-KIRA [paper §4.3]. In rsi, `baselines={name: Artifact}` defaults to `{seed_name: seed}`.
- **Measured first**: every initial harness is evaluated on the search set and stored, before any proposal
  [paper Alg. 1 l.4-6]. A noise band is not specified in the paper.

## 3. Round by round (what the paper does)

One iteration is one proposer step of [paper Alg. 1 l.7-12].

1. **Query the filesystem** [paper Alg. 1 l.8]. The proposer decides which earlier artifacts to inspect. It
   reads code, scores and execution traces, then reasons about likely failure modes before writing [paper §3].
   In the TB2 run (10 iterations) it read a median of 82 files per iteration (range 69-99). The mix was 41%
   harness code, 40% traces, 6% score/summary files and 13% other [paper App. A.1, Table 8], drawn from "over
   20 prior candidates per step" [paper §1].
2. **Parent / incumbent selection: none.** "Imposes no parent-selection rule". The proposer often starts from a
   strong prior harness, but this behaviour emerges and is not a rule [paper §3]. It can also merge lineages
   [paper App. B.2].
3. **Propose k harnesses** [paper Alg. 1 l.9]. The edit can be anything from a local change to a full rewrite
   [paper §3]. The paper gives several counts:
   - Text classification: 2 per iteration, 40 in 20 iterations [paper §4.1].
   - Math: 109 candidates over 40 iterations [paper §4.2].
   - "Typical run": "roughly 60 harnesses over 20 iterations" [paper §3]; §1 and Fig. 1 also refer to 60 proposals.
   - TB2: k is not specified in the paper. The release skill says 1 [release: TB2 SKILL.md].
4. **Interface validation** [paper Alg. 1 l.11]. The test imports the module, instantiates the class and calls
   both methods on a tiny set of examples [paper App. D]. A candidate that fails is not evaluated.
5. **Evaluate and store** [paper Alg. 1 l.12]. Each valid candidate is evaluated on the search set, and
   `(H, scores, traces)` is added to `D`, in a new directory per candidate [paper §3, Fig. 2]. The proposer does
   not run the evaluations [paper App. D].
6. **Selection / keep rule: none.** Every evaluated harness stays in `D`. The paper tracks a Pareto frontier
   [paper §3] but gives no dominance formula; the formula is the release's `compute_pareto_frontier`
   [release]. The system reported as "Meta-Harness" in text classification is the highest-accuracy frontier
   point [paper App. B.1, Table 9, footnote 2]. Selection uses search-set performance only [paper §4.1].
7. **State update**: `D` only grows. It also stores the proposer's reasoning traces [paper Fig. 2]. The loop
   has no schedule, annealing or temperature. In the authors' experience, accumulated traces end up shaping the
   proposer more than the skill does [paper App. D].

```
H = initial valid harnesses; D = {}                           # [paper Alg. 1 l.1-3]
for H in H: D += (H, Evaluate(H, M, X_search))                 # code, scores, traces
for t = 1..N:                                                  # fixed N [paper §3]
    P queries D (grep/cat; any prior code, scores, traces)
    P proposes k harnesses
    for H in them: if passes_interface_validation(H): D += (H, Evaluate(H, M, X_search))
return Pareto frontier of D  ->  one final test-set evaluation of the frontier   # [paper Alg. 1 l.13, §3]
```

**The proposer's workflow inside a session** is not specified in the paper beyond "inspect, diagnose,
propose" [paper §3] and the skill guidance [paper App. D]. The release skill and rsi's `SKILL_TEXT` (in
`proposer.py`) both prescribe this workflow:
- Step 0: write post-eval reports for iterations that have none.
- Step 1: read the summary, the frontier, the code, and failed AND successful traces.
- Step 2: form k falsifiable hypotheses, each about a different mechanism.
- Step 3: prototype on real trace examples. The coding agent runs `python3`; the one-completion proposer works "on paper".
- Step 4: write k complete harnesses, starting from a strong base and changing a mechanism. A parameter-only
  variant should be rewritten.
- Tag each candidate with an axis (A-F) and rotate axes when the last 3 iterations used the same one. This is an
  rsi/release choice.

**Stopping rule.** A fixed number of iterations [paper §3] with no convergence test: 20 for text classification
[paper §4.1], 40 for math [paper §4.2], and 10 in the logged TB2 run [paper App. A]. rsi also stops at
`eval_budget` evaluated candidates, and `stop_reason` is "eval_budget" or "iterations" (rsi choice).

**Final evaluation.** The Pareto frontier is evaluated once on the test set [paper §3]:
- Math selects a single harness on search-set performance with GPT-OSS-20B, then tests it on 200 problems
  with 5 models [paper §4.2].
- TB2 searches and evaluates on the same 89 tasks, used as a discovery problem [paper §4.3].

## 4. Sample by sample (what the paper does)

- **Unit and rollouts**: a task instance `x` with rollout `tau ~ p_M(H, x)`, scored by `r(tau, x)` [paper §3].
  The number of rollouts per task during search is not specified in the paper. At test time, math reports
  pass@1 averaged over 3 samples per problem [paper §4.2, Table 6]. The release uses 1 seed (`[42]`) for
  text classification and 2 trials per task for TB2 [release: config.yaml, DEFAULT_SEARCH_TRIALS].
- **Inside one text-classification rollout** the setup is online: "an LLM receives labeled examples one at a
  time, updates its memory, and is evaluated on a held-out test set" [paper §4.1]. The release default is
  offline, 1 epoch, and logs no per-example predictions on validation [release: inner_loop.py].
- **Stored per candidate**: a directory with source code, scores and execution traces (prompts, tool calls,
  model outputs, state updates) [paper §3]. The format should be machine-readable, hierarchical, and named so
  that regex search works [paper App. D]. Per-example storage beyond that is not specified in the paper.
- **Aggregation**: text classification reports per-dataset accuracy and its average over 3 datasets
  [paper Table 2]. TB2 reports pass rate over 89 tasks [paper Table 7]. Context cost is "additional input
  tokens in context (thousands)" in [paper Table 2] and "average additional characters" in [paper Table 9].
  Which model calls count, and how per-task costs are averaged, is not specified in the paper.
- **Splits**: the proposer only gets search-set feedback, and test results stay hidden [paper §3].
  - Text classification: held-out test sets per dataset, plus 9 unseen OOD datasets [paper §4.1, Table 5].
  - Math: 250 search problems and 200 IMO-level test problems, with the retrieval corpus decontaminated
    against both [paper §4.2, App. C.2].
  - TB2: search = test, 89 tasks, checked afterwards by manual inspection and regex leakage audits
    [paper §4.3].
- **rsi equivalents**:
  - Evaluation: `trials` rollouts per task with seeds `0..trials-1` (default 1). A crash or an infra error
    gets the domain's failure score. Score = mean over tasks of the per-task mean over seeds.
  - Storage: the trace goes to `candidates/<name>/eval/search/traces/<task>.jsonl` and the trial records to
    `per_task/<task>.json`.
  - Cost: per-task mean of `cost_metric`. The system's cost is the mean of the non-zero per-task costs, as the
    release's `print_frontier` computes it.
  - Sealed splits: `holdout`/`ood`/`test` are sealed (`SealedSplitError`), and only `finalize()` opens `test`.

## 5. Paper defaults vs this package

| Setting | Paper value [source] | rsi `Config` field and default | Note |
|---|---|---|---|
| Iterations N | 20 text-cls [§4.1]; 40 math [§4.2]; 10 TB2 run [App. A] | `iterations=20` | release CLI default 20 (TB2: 5) |
| Candidates per round | 2 [§4.1]; 109/40 math [§4.2]; "~60 over 20" [§3]; TB2 not specified | `k=2` | paper inconsistent; release skill 3 (TB2 1) |
| History | all code + scores + raw traces [§3, Table 1, Table 3] | `history_mode="full"` | also `scores_summary`, `scores_only`, `window`, `last_only`, `seed_only` (ablation arms) |
| Proposer | Claude Code + Opus 4.6 [§3], max reasoning [§4.1] | `llm_propose` + `run(proposer_kind="auto")` | Opus never run here; live runs used haiku |
| Proposer timeout | not specified in the paper; 2400 s [release] | `proposer_timeout_s=2400.0` | |
| Parent selection | none [§3] | none | |
| Keep rule | none; Pareto frontier [§3, Alg. 1] | none; `objectives=("score","context_cost")` | `("score",)` = scalar |
| Context cost | "additional input tokens" [Table 2] / "characters" [Table 9]; call scope not specified | `cost_metric="context_chars"` (all calls) | release counts the last call (`"context_chars_last_call"`); `"tokens"` |
| Validation | import, instantiate, call both methods on a tiny set [Alg. 1 l.11, App. D] | `validate=True`, `validate_timeout_s=30.0`, `validate_in_subprocess=True` | |
| Leakage guard | none in loop; post-hoc manual + regex audit (TB2) [§4.3] | `leakage_screen=False` | on = in-loop screen (rsi choice) |
| Rollouts per task (search) | not specified in the paper; math test 3 samples [§4.2] | `trials=1` | release: seed 42; TB2 2 trials |
| Noise band | not specified in the paper | `reeval_incumbent=0`, `reeval_max_per_iteration=3` | rsi choice |
| Eval budget | ~50 full evals per run advised [App. D]; equal-budget comparisons [§4.1] | `eval_budget=None` | counts evaluated candidates only |
| Search set size | 50-100 (cls), 88 math [App. D]; 250 math [§4.2]; 89 TB2 [§4.3] | your `evolve` split | |
| Search / test | search set; final test on the frontier [§3] | `search_split="evolve"`, `test_splits=("test",)`, `finalize=True` | |
| Inner loop | online [§4.1] (release default offline, 1 epoch) | domain-defined | MemoClassify is online |
| Base model M | GPT-OSS-120B [§4.1]; GPT-OSS-20B + 4 held-out models [§4.2]; Opus 4.6, Haiku 4.5 [§4.3] | `llm_task` | |
| Skill text | "the strongest lever" [App. D] | `SKILL_TEXT` in `proposer.py` | adapted from the release |

## 6. Where our implementation differs

From `implementation.md` §5, `claims-audit-metaharness.md` and the code:
- **Proposer.** `AgentProposer` is `claude -p` with Read/Edit/Write/Glob/Grep. Its Bash access is limited to
  `python3`/`python` and read-only commands. The release runs full Bash and allows subagents (TB2); the paper
  does not specify the tool set. `python3` is still unrestricted code execution, so use `prototype=False` or a
  container if that is unacceptable. `RewriteProposer` (one completion over a 60k-char render of the history,
  split 41/40/6/13% as in [paper Table 8]) and `MockProposer` (offline, reads only its parents' traces) are rsi
  choices with no counterpart in the paper.
- **Auto proposer selection.** `proposer_kind="auto"` falls back to `RewriteProposer` when `llm_propose` is
  wrapped in `CachedLLM`, which `rsi.improve(..., cache_dir=...)` does. Pass `proposer_kind="agent"`.
- **Final test set**: baselines ∪ Pareto ∪ per-unit bests (as in the release); the paper says the Pareto
  frontier [paper §3]. Finalisation re-runs the inner loop on test, whereas the release reloads `memory.json` and
  only predicts. The two are equal only for deterministic models.
- **Context metric** sums all model calls of a query. The release counts only the last call, which a tiny final
  call can game (audit N2: 17,724 chars reported as 46.5). The paper does not define the metric, but it
  describes its two-call harness's "overall context cost ... even with two model invocations" [paper App. B.1].
- **Rsi additions, all optional or trace-only**:
  - an in-loop `leakage_screen`;
  - `reeval_incumbent`;
  - the shadow monitor on sealed `holdout`/`ood`;
  - `deterministic_view`;
  - `delta` measured against the post-iteration best (a release quirk), with `delta_pre` logged as well;
  - k shrinks when `eval_budget` is nearly used up;
  - a proposer error yields no candidates, but the round still counts.
- **Not implemented**: the pilot's in-agent `evaluate_harness` tool, `DomainOnboarding`, the TB2 `AgentHarness`
  subclass check, ACE/MCE baselines, and a long-horizon Opus run. `eval_budget` counts evaluated candidates
  only, while the pilot also charges rejected ones.

Audit verdicts that change how you use it. The audit covers 38 claims: 13 reproduced, 12 partial, 0 not
reproduced, 12 not testable here, 1 contradicted.
- **Loop mechanics** (M3-M6): REPRODUCED. These cover no parent selection, validate-then-evaluate-all, the Pareto
  frontier, the sealed test and one finalisation.
- **"Raw traces are the key ingredient"** (M1/Q3/L1) [paper Table 3]: PARTIAL.
  - Offline, the effect is small and built into the mock.
  - Live (haiku, 5 snapshots), traces beat scores-only by +1.2 points [0.5, 1.8].
  - Summaries vs scores-only: -0.3 [-2.3, 1.5].
  - The paper's +15.4 median gap (50.0 vs 34.6) is not reproduced.
- **Speed and equal-budget claims** (Q4/Q5): NOT TESTABLE HERE.
  - Q4: beating Best-of-N, OpenEvolve, TTT-Discover and GEPA at an equal budget [paper Table 4].
  - Q5: matching OpenEvolve/TTT-Discover with 10x fewer evaluations [paper §4.1, Fig. 1].
  - Why: the mock proposer is Markovian, so it cannot show the benefit of reading the full history. Do not promise this speed-up.
- **"Code-space search biases towards coherent, not hard-coded, solutions"** (L4) [paper §3, §5]: CONTRADICTED.
  On AgentQA, 4 of 12 live candidates keyed on the generator's templates, and one was selected as best. Treat
  the loop as having no overfitting protection.
- **Noisy single-seed selection** (K3): PARTIAL. The winner's curse is about +0.9 points, and 9% of incumbent
  changes are false.
- **Cost** (K4): reading the full history costs 54-96x a windowed optimiser in tokens (mostly cache reads) and
  2.5-4x in dollars.
- **No in-loop guard against test-specific edits** (K2): REPRODUCED. The paper only audits after the run [paper §4.3].

## 7. Applying it with rsi

```python
import rsi
res = rsi.improve(dom, seed, method="metaharness", llm_task=llm,
                  llm_propose="claude:sonnet", proposer_kind="agent",   # coding agent even though cache_dir wraps it
                  config={"iterations": 8, "k": 2, "trials": 2, "reeval_incumbent": 2,
                          "leakage_screen": True, "objectives": ("score",),   # or keep context_cost + cost_metric
                          "eval_budget": 20},
                  out_dir="runs/mh", cache_dir=".rsi_cache/mh")
res.best                       # highest-score Pareto point on the SEARCH split
res.meta["frontier"]["_pareto"]; res.meta["final"]["splits"]["test"]
rsi.transfer_report(dom, llm, {"seed": res.baseline, "mh": res.best}, k=2)   # holdout/ood
```
Set `trials >= 2` and measure the noise band first (SKILL.md step 3). Choose `objectives`/`cost_metric` to match
what cost means for you; with a single metric, use `("score",)`. Use a search set the seed often fails on
[paper App. D]. Debug the skill and domain with a few 3-5-iteration runs before a long one [paper App. D]. The
coding-agent proposer bypasses the LLM cache, so its sessions are not replayable at $0.

**Audit with TRACE.md** (`python -m rsi.cli inspect runs/mh`). Per round it shows:
- `round_start`: view size, kinds of files shown, frontier;
- `analysis`: files read by kind, reports;
- each `proposal`: claimed change vs real diff, parent, `identical_to_base`;
- the admissibility gate (validate/screen);
- per-task scores;
- `decision`: which candidates joined the frontier, what dominates the rest, incumbent before/after;
- shadow holdout/ood scores for each new `_best`;
- the one-time `finalize` note.

Also read `store/evolution_summary.jsonl` and `store/sessions/iterNNN/meta.json`.

**Failure modes to watch**
- Evolve score rises while shadow holdout/ood stalls or falls: memorisation. Read the kept diffs for task ids,
  literals, lookup tables, and regexes tied to the task generator.
- `identical_to_base` candidates, parameter-only edits, or proposals that name an unknown base (`base_fallback`).
- A new `_best` that wins by less than the measured delta: single-seed luck. Raise `trials` or `reeval_incumbent`.
- A gamed cost objective (a tiny last call, or a lookup that answers without any model call): check
  `cost_metric` and the per-task costs.
- The proposer sees too little: `view.by_kind` has few trace files, or `files_read` is near zero (the
  one-completion proposer truncates traces). Or every candidate is invalid: check `reason` in `meta.json`.
- No keep gate: the chosen harness is only "best on search". `finalize()` is the only test read and it locks
  the run (start a new `out_dir` to continue). An `incomplete` status means some test trials were missing.
- Cost: full-history coding-agent sessions are the expensive part, so cap spend yourself between runs.
