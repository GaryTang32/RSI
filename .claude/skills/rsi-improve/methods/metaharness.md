# Meta-Harness: end-to-end methodology (for `rsi.improve(..., method="metaharness")`)

Sources, in order: `docs/methods/metaharness-solpi/paper-spec.md` (Part A; tags `[paper:MH <loc>]` = the paper
arXiv 2603.28052, `[code:...]` = the authors' release), then `implementation.md`,
`claims-audit-metaharness.md`, and `rsi/metaharness/{config,loop,api,store,frontier}.py`. The paper was read through
third-party mirrors. Anything the spec does not state is marked "not specified in the paper".

## 1. In one paragraph

An LLM application is a frozen model plus a harness: the code that decides what to store, retrieve and show the
model [paper:MH abstract]. Meta-Harness (Lee et al., 2026) searches over whole single-file harness programs with an
outer loop. A coding agent (the proposer: Claude Code, Opus 4.6, max effort [paper:MH §3, §4.1]) reads a filesystem
holding every earlier candidate's source, scores and raw execution traces, using `grep`/`cat`, and writes new
harnesses. A separate evaluator scores them on a search set and writes everything back. The design choice is to
not compress feedback: no parent-selection rule, no mutation operators, no archive policy, no keep gate
[paper:MH §3, Alg. 1]. It optimises the expected task reward of the harness `H` with the model fixed
(`H* = argmax_H E[r(tau, x)]`, [paper:MH §3], no equation number in the spec), or a Pareto frontier when several
objectives matter (accuracy vs context cost). The test set is evaluated once at the end. Raw traces, not scores
or LLM summaries, are claimed to be the key ingredient [paper:MH Table 3].

## 2. Inputs and setup

Before round 1 you need:
- **Artifact**: a harness the proposer can rewrite in full (`Artifact` of files), plus a domain description that
  states its interface and how it is graded. Frozen: the model `M`, the proposer and its skill, the outer loop,
  the evaluator, the splits [paper:MH §3; code SKILL.md "what you can and cannot modify"].
- **Splits**: a search split (rsi: `Config.search_split="evolve"`) the loop may read, and a sealed `test` split
  (`Config.test_splits=("test",)`) read once by `finalize()`. The paper says the search set should be small enough
  for about 50 full evaluations per run and hard for the baseline: filter for examples the baseline gets wrong
  [paper:MH App. D]. Also keep `holdout`/`ood` sealed for your own transfer check.
- **Grader and feedback**: automated and outside the proposer [paper:MH App. D]. `Domain.execute` must return a rich
  `Execution.trace` (prompts, replies, tool calls, state updates; [paper:MH §3]); `grade` returns score and a feedback
  string. The traces are what the proposer reads.
- **Cost objective** (optional, paper §3 Pareto): a per-task cost in `Trial.meta[Config.cost_metric]`, or tokens.
- **Models**: `llm_task` (frozen model run inside the harness) and `llm_propose`. A `ClaudeCLI` backend gives the
  coding-agent proposer (paper setup); any other LLM gives the one-completion `RewriteProposer`; `None` or a plain
  `MockLLM` gives the offline `MockProposer`.
- **Budgets**: `iterations`, `k`, `eval_budget` (max candidate evaluations), `proposer_timeout_s`. There is no dollar cap.
- **Population H0**: `baselines={name: Artifact}`; default is `{seed_name: seed}`. The paper uses zero-shot,
  few-shot, ACE, MCE [paper:MH §4.1]; the release uses `no_memory`, `fewshot_all` [code:config.yaml].
- **Measured first**: every baseline is evaluated once on the search split (`run_baselines`), then the frontier is
  computed. The release's Phase 0 does the same [code:meta_harness.py:run_evolve]. The paper reports no noise band
  and neither does the loop by default (see section 6).

## 3. Round by round

One round = one proposer session = `MetaHarnessLoop.iterate(t)` [paper:MH Alg. 1 inner loop].

1. **State check.** Refuse if the run is finalised. Clear the pending file.
2. **Parent / incumbent selection**: none. The paper imposes no parent-selection rule; the proposer may open any
   prior harness and trace [paper:MH §3]. The incumbent is only the highest-score Pareto point (`_best`), used for
   logging. The proposer names a `base_system` per candidate, recorded in the ledger.
3. **What the proposer reads.** The history view (`Config.history_mode="full"`): every candidate's source,
   `meta.json`, `scores.json`, per-task records and raw traces, `evolution_summary.jsonl`, `frontier_val.json`,
   post-eval `reports/`, earlier proposer sessions. It never contains `results/` (test). It also gets the domain
   description and the objective text (Pareto objectives, how cost is counted, `Config.tradeoff`).
4. **How many candidates.** `k = Config.k` (2), capped by `eval_budget` left. The paper says 2 per iteration
   (40 in 20 iterations) in §4.1 but "roughly 60 harnesses over 20 iterations" in §3; the release skill says 3.
5. **Proposer workflow** (skill, [code:text_classification SKILL.md]; rsi `SKILL_TEXT`): Step 0 write short reports
   on earlier iterations that have results but no report; Step 1 read summary, frontier, code and failed AND
   successful traces, form k falsifiable hypotheses on different mechanisms; Step 2 prototype on real trace
   examples (coding agent runs `python3`; one-completion proposer does it "on paper"); Step 3 write k complete
   harnesses, copying a strong base and changing a mechanism (parameter-only variants are to be rewritten); tag an
   axis (A prompt, B state content, C selection, D sizing, E learn trigger, F model use inside the harness) and rotate it
   if the last 3 iterations used the same one. The proposer does NOT run benchmarks.
6. **Admission.** Optional leakage screen (`leakage_screen`, default off), then interface validation
   (`validate`: compile plus a smoke run in a forked child, killed after `validate_timeout_s`). A candidate that
   fails is recorded as `invalid`/`rejected_leakage` and not evaluated. There is no score threshold.
7. **Execution and scoring.** Every admitted candidate is evaluated on the search split, `trials` seeds per task.
   Score `S` = mean over tasks of the per-task mean over seeds. Cost per task = mean of `cost_metric` over its trials.
8. **Keep rule**: none. Every evaluated candidate is stored in the population. The frontier keeps non-dominated
   points: point `p` is dominated if some `q` has `score_q >= score_p`, `cost_q <= cost_p` and one strict
   [paper:MH §3; code compute_pareto_frontier]. `_best` = frontier point with the highest score; exact ties keep
   registration order. With `objectives=("score",)` the frontier is just the best score.
9. **State update.** Write `scores.json` and traces, `evolution_summary.jsonl` rows (`delta` is vs the
   POST-iteration best, a quirk copied from the release; `delta_pre` vs the pre-iteration best), `frontier_val.json`
   (per-unit bests, `_pareto`, `_best`, `_hypervolume`), the ledger node, the proposer session log (`files_read`,
   usage). Optional `reeval_incumbent` re-scores a new `_best` on more seeds (not in the paper).

```
run_baselines(): for H in H0: evaluate(H, search, trials) -> store; frontier()
for t in start .. start+iterations-1:                  # start = last logged iteration + 1 (resumable)
    if eval_budget exhausted: break
    view  = store.view(history_mode)                   # code + scores + raw traces; never test
    batch = proposer.propose(view, k = min(k, budget_left), brief + objective)
    for c in batch.candidates:
        if screen(c) and validate(c):                  # interface only, no score gate
            store(c); evaluate(c, search, trials); n_evaluated += 1
    frontier = pareto(all stored, (score up, cost down)); _best = max score on it
    [optional] re-evaluate new _best on trials + reeval_incumbent seeds
finalize(): test once on baselines U Pareto U per-unit bests; lock the run
return best = highest-score Pareto point on the SEARCH split
```

**What changes across rounds.** The history only grows (full mode: no truncation by the loop; the one-completion
proposer renders it under a 60k-char budget, 41/40/6/13 % code/traces/scores/other). No annealing, no schedule,
no temperature. The proposer's behaviour drifts with accumulated traces more than with the skill [paper:MH App. D].
`k` can shrink at the end if `eval_budget` is set.

**Stopping rule.** Fixed `iterations` (paper: 20 text classification, 40 math, ~10 logged TB2 run [paper:MH §4]) or
`eval_budget` evaluated candidates, whichever comes first; `stop_reason` is "eval_budget" or "iterations". No
convergence test. A proposer error yields no candidates for that round but the round still counts.

## 4. Sample by sample

- **Unit**: one task of the search split. For each task, `trials` rollouts with seeds `0..trials-1`
  (`Evaluator.evaluate`; default 1). The release uses seeds `[42]`, and 2 trials per task on TB2 [code:config.yaml].
- **Per rollout**: `Domain.execute(artifact, task, seed, llm)` then `grade`; a crash or non-finite grade becomes
  the failure score. Recorded: score, grader feedback, output, tokens, steps, error, `meta` (cost fields), and
  the full trace text. The trace is written to `candidates/<name>/eval/search/traces/<task>.jsonl`; per-trial
  records (without trace) to `per_task/<task>.json`. In the paper's text-classification task the harness learns
  online: predict, score, `learn_from_batch`, one example at a time [paper:MH §4.1]; the release default is offline,
  1 epoch, with no per-example val predictions logged [paper-spec A8 item 12; code inner_loop.py].
- **Aggregation**: per-task mean over seeds, then mean over tasks = round score. Context cost: per-task mean,
  then the system's cost is the mean of the NON-ZERO per-task costs [code:print_frontier]. The frontier uses these.
- **Splits**: search split (`evolve`) is read by the loop and the proposer. `holdout`, `ood`, `test` are sealed:
  the loop's `Evaluator` raises `SealedSplitError`. `finalize()` alone opens `test` (`allow_sealed=True`). The
  trace-only shadow monitor scores each new `_best` on sealed `holdout`/`ood` (`shadow_monitor=True`) and writes
  to TRACE.md only; nothing returns to the loop. Exception in the paper: on TB2 search and evaluation are the
  same 89 tasks [paper:MH §4.3].

## 5. Paper defaults vs this package

| Setting | Paper value [tag] | rsi `Config` field and default | Note |
|---|---|---|---|
| Iterations N | 20 text-cls [paper:MH §4.1]; 40 math [§4.2]; 10 TB2 log [App. A] | `iterations=20` | release CLI default 20 |
| Candidates per round | 2 [§4.1]; "~60 over 20 iter" [§3]; release skill 3 | `k=2` | paper inconsistent |
| History | full code + scores + raw traces [§3, Table 3] | `history_mode="full"` | also `scores_summary`, `scores_only`, `window`, `last_only`, `seed_only` (ablation/baseline arms) |
| Proposer | Claude Code, Opus 4.6, max effort [§3] | `llm_propose` + `proposer_kind="auto"` | Opus never run here; haiku coding agent only |
| Proposer timeout | 2400 s [code] | `proposer_timeout_s=2400.0` | |
| Parent selection | none [§3] | none | |
| Keep rule | none, Pareto frontier [§3] | none; `objectives=("score","context_cost")` | `("score",)` = scalar |
| Context cost | last model call, characters [code] | `cost_metric="context_chars"` (all calls) | `"context_chars_last_call"` = release; `"tokens"` |
| Validation | import + tiny smoke [Alg. 1, App. D] | `validate=True`, `validate_timeout_s=30.0`, `validate_in_subprocess=True` | |
| Leakage guard | none in loop [§4.3]; pilot has one [code] | `leakage_screen=False` | on = pilot-style screen |
| Seeds per task | release `[42]`; TB2 2 trials | `trials=1` | |
| Noise band | none | `reeval_incumbent=0`, `reeval_max_per_iteration=3` | optional, not in paper |
| Eval budget | ~50 full evals per run advised [App. D] | `eval_budget=None` | counts evaluated candidates only |
| Search / test | search set; test once at end [§3] | `search_split="evolve"`, `test_splits=("test",)`, `finalize=True` | |
| Inner loop | online (paper); offline 1 epoch (release) | domain-defined | |
| Base model | GPT-OSS-120B/20B; Opus 4.6, Haiku 4.5 (TB2) | `llm_task` | |
| Post-eval reports / skill text | skill is "the strongest lever" [App. D] | `SKILL_TEXT` in `proposer.py` | adapted from release |

## 6. Where our implementation differs

From `implementation.md` section 5 and `claims-audit-metaharness.md`:
- **Proposer.** `AgentProposer` is `claude -p` with Read/Edit/Write/Glob/Grep plus Bash limited to `python3` and
  read-only commands (release: full Bash). No subagents. `python3` is unrestricted code execution; use
  `prototype=False` or a container if that is unacceptable.
- **Finalisation** re-runs the inner loop on test; the release reloads `memory.json` and only predicts. Equal for
  deterministic models only.
- **Context metric** sums all model calls of a query; the release counts the last call, which a tiny final call can
  game (audit N2: 17,724 chars reported as 46.5).
- **Not implemented**: the pilot's in-agent `evaluate_harness` tool, `DomainOnboarding`, the TB2 `AgentHarness`
  subclass check, ACE/MCE baselines, a long-horizon Opus run.
- **Eval budget**: ours counts evaluated candidates only; the pilot also charges rejected ones.

Audit verdicts that change how you use it (38 claims: 13 reproduced, 12 partial, 0 not reproduced, 12 not testable
here, 1 contradicted):
- Loop mechanics (no parent selection, validate-then-evaluate-all, Pareto, sealed test, one finalisation): REPRODUCED.
- "Raw traces are the key ingredient" (M1/Q3/L1): PARTIAL. Offline the effect is small and built into the mock;
  live (haiku, 5 snapshots) traces beat scores-only by +1.2 points [0.5, 1.8], summaries gave no detectable gain.
  The paper's +15 median is not reproduced.
- "10x fewer evaluations than OpenEvolve/TTT-Discover/GEPA" (Q4/Q5): NOT TESTABLE HERE. The mock proposer is
  Markovian, so the analogue cannot show full-history use. Do not promise this speed-up.
- "Code-space search resists hard-coded solutions" (L4): CONTRADICTED. On AgentQA 4 of 12 live candidates keyed on
  the generator's templates; one was selected as best. Treat the loop as having no overfitting protection.
- Noisy single-seed selection (K3): PARTIAL; winner's curse about +0.9 points, 9% of incumbent changes false.
- Cost (K4): reading full history is 54-96x a windowed optimiser in tokens (mostly cache reads), 2.5-4x in dollars.
- No guard against test-specific edits (K2): REPRODUCED, as in the paper.

## 7. Applying it with rsi

```python
import rsi
res = rsi.improve(dom, seed, method="metaharness", llm_task=llm,
                  llm_propose="claude:sonnet",            # ClaudeCLI -> coding-agent proposer
                  config={"iterations": 8, "k": 2, "trials": 2, "reeval_incumbent": 2,
                          "leakage_screen": True, "objectives": ("score",),   # or keep context_cost + cost_metric
                          "eval_budget": 20},
                  out_dir="runs/mh", cache_dir=".rsi_cache/mh")
res.best                       # highest-score Pareto point on the SEARCH split
res.meta["frontier"]["_pareto"]; res.meta["final"]["splits"]["test"]
rsi.transfer_report(dom, llm, {"seed": res.baseline, "mh": res.best}, k=2)   # holdout/ood
```
Set `trials >= 2` and measure the noise band first (SKILL.md step 3). Choose `objectives`/`cost_metric` to match
what cost means for you; with a single metric use `("score",)`. Use a search set where the seed fails often.

**Audit with TRACE.md** (`python -m rsi.cli inspect runs/mh`): per round, `round_start` (view size, kinds of files
shown, frontier), `analysis` (files read by kind, reports), each `proposal` (claimed change vs real diff, parent,
`identical_to_base`), the admissibility gate (validate/screen), per-task scores, `decision` (which candidates joined
the frontier, what dominates the rest, incumbent before/after), shadow holdout/ood scores for each new `_best`,
and the one-time `finalize` note. Also read `store/evolution_summary.jsonl` and `store/sessions/iterNNN/meta.json`.

**Failure modes to watch**
- Evolve score rises while shadow holdout/ood stalls or falls: memorisation. Read the kept diffs for task ids,
  literals, lookup tables, regexes tied to the task generator.
- `identical_to_base` candidates, parameter-only edits, or proposals that name an unknown base (`base_fallback`).
- A new `_best` that wins by less than the measured delta: single-seed luck. Raise `trials` or `reeval_incumbent`.
- Cost objective gamed (a tiny last call, or a lookup answering without any model call): check
  `cost_metric` and per-task costs.
- Proposer sees too little: `view.by_kind` with few trace files, or `files_read` near zero (the one-completion
  proposer truncates traces); candidates all invalid (check `reason` in `meta.json`).
- No keep gate: the chosen harness is only "best on search". `finalize()` is the only test read, it locks the run
  (start a new `out_dir` to continue), and an `incomplete` status means some test trials were missing.
- Cost: full-history coding-agent sessions are the expensive part; cap spend yourself between runs.
