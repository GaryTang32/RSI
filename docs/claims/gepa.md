# GEPA: claim-by-claim audit

This page checks our implementation (`rsi/gepa/`, `rsi/domains/ruleworld/`) against **every** claim in "GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning" (ICLR 2026 camera-ready). It covers mechanisms, table numbers, observations and caveats. The audit itself changed no code; a fix round followed, and §5 ("Fix log") lists every finding with its fix, code location, regression test and evidence. The verdicts below are after the fix round; rows it changed say so.

**Sources used.**
- The paper. Camera-ready text: `scratchpad/papers/gepa.txt`. LaTeX source: `scratchpad/papers/gepa-iclr2026-latex.txt`.
- Our verified spec, `docs/methods/gepa.md`, and the user's overview, `scratchpad/doc.txt`.
- The reference code, `gepa-ai/gepa@d771eb21`, in `scratchpad/src/gepa-ai__gepa`.
- Our evidence:
  - `docs/methods/gepa-impl.md`;
  - `results/gepa/*.json`;
  - `validation/gepa/{AUDIT,RUNS}.md` and the run directories.

**New checks run for this audit.** All were offline, took under 1 min of CPU each, and made no LLM calls. The scripts are in `scratchpad/claims_gepa/` (outside the repo).
- `diff_prompt.py` compares the meta-prompts, the renderer and the parser with the reference code:
  - it checks both templates byte for byte;
  - it renders 2,000 fuzzed record sets with the reference `prompt_renderer`;
  - it parses 20,000 fuzzed replies with the reference `parse_proposal`.
- `e1_reanalyse.py` re-analyses the E1 raw rows, pairing GEPA and RL seed by seed.
- `e1_match_rl.py` re-runs E1's GEPA arm (20 seeds, B = 6000). It reproduces the stored final scores exactly and measures the rollouts GEPA needs to match RL's final score.
- `transfer_and_multitask.py` runs two analogues:
  - E8, cross-task-model transfer;
  - E10, inference-time search with D_train = D_pareto against independent single-task runs.
- `tests/test_gepa_*.py`: 67 passed in 22 s (79 after the fix round, §5).

## 1. Summary

| verdict | mechanism | quantitative | qualitative | caveat | **total** | before the fix round |
|---|---|---|---|---|---|---|
| REPRODUCED | 17 | 1 | 5 | 6 | **29** | 27 |
| PARTIAL | 1 | 3 | 7 | 0 | **11** | 13 |
| NOT REPRODUCED | 0 | 0 | 0 | 0 | **0** | 0 |
| NOT TESTABLE HERE | 0 | 16 | 0 | 1 | **17** | 17 |
| CONTRADICTED | 0 | 0 | 1 | 0 | **1** | 1 |
| **total** | 18 | 20 | 13 | 7 | **58** | 58 |

Changed by the fix round: M11 PARTIAL → REPRODUCED (the hard cap now caps invocations, as the paper says) and M16 PARTIAL → REPRODUCED (spec E9 run). Every other verdict is unchanged; the evidence of M4, M8, Q7, L2, L3, L5, L6 and L13 was updated with the fixed code and the regenerated results.

**Mechanisms.** The mechanisms are faithful to the reference code. The reflection meta-prompt is byte-identical to the repo: SHA-256 prefix `c8125637a50786c3`, 942 bytes. In the fuzz tests the renderer and parser matched the reference exactly (0 mismatches in 2,000 renders and 20,000 parses). Stage-B replayed all 125 audited steps exactly. Before the fix round three mechanisms were PARTIAL; one remains:
- Algorithm 4 as printed (M4). The reference code, and therefore ours, departs from it; every departure is now listed in impl §5.16.
- The "invoked a maximum of 5 times" merge cap (M11) is now reproduced by `merge_cap_mode="hard"`, which caps invocations; the default still follows the reference's soft cap.
- Module-level credit assignment (M16) is now isolated by E9.

**Quantitative results.** Every table number in the paper comes from Qwen3-8B / GPT-4.1-mini runs, with GRPO fine-tuning on GPUs, so none of them can be tested here. Our CPU analogues reproduce the *direction* of four claims:
- rollout efficiency;
- beating a few-shot optimizer;
- shorter prompts;
- validation dominating the budget.

The *size* is reproduced for none of them.

**Headline caveat from the new re-analysis.** The paper's main comparison is GEPA with about 2–7k rollouts against GRPO with 24k. The analogue here is GEPA at B = 6000 against ScalarRL at B = 24000:
- GEPA − RL = **+0.015 [−0.004, +0.035]**, which is not significant (the audit's own bootstrap gave [−0.004, +0.036]; the E1 script now reports it with `rsi.core.stats`).
- RL reaches the oracle prompt in 7 of 20 seeds; GEPA never does, plateauing at about 0.92.

So "outperforms RL" holds at matched *small* budgets (paired +0.745 at 800 rollouts), but at the paper's 1:4 budget ratio it is only a tie here.

## 2. Claim table

**Legend.**
- Type: **M** = mechanism, **Q** = quantitative, **L** = qualitative, **C** = caveat.
- "Code ref" gives `file:line` in this repo. `ref:` means `gepa-ai/gepa/src/gepa/...`.
- Evidence paths are relative to the repo root.

### 2.1 Mechanisms

| # | Claim | Source | Type | Fidelity + code ref | Our evidence | Verdict |
|---|---|---|---|---|---|---|
| M1 | Algorithm 1 loop: score the seed on D_pareto; then per iteration select a parent, run it on a minibatch, reflect, re-run the child on the same minibatch, and if σ′ improved score it on D_pareto and add it; stop when B is exhausted | Alg. 1, p.18 | M | Faithful. Seed evaluation at `rsi/gepa/engine.py:371`; iteration at `engine.py:434-601`; stop check only at the top of each iteration (`engine.py:388`), as in `ref:core/engine.py` | Stage-B replay: 125 steps, 2883/2883 mechanical checks (`validation/gepa/AUDIT.md` §1, M1, M9) | REPRODUCED |
| M2 | Algorithm 2: per-instance Pareto sets with ties, dominated candidates removed, sampling ∝ frontier frequency | Alg. 2, p.18 | M | A line-for-line port of `ref:gepa_utils.py` in `rsi/gepa/frontier.py:29-90`. It uses the code's *set-cover* domination, not the paper's literal "dominated by another" (spec §3). Front members are visited in sorted order (documented deviation 2, which changed 2 of 114 draws against Python set order) | Probe vectors are unit-tested; 114/114 draws replayed (AUDIT M2) | REPRODUCED |
| M3 | Algorithm 3, DESIRABLE: some module where one descendant equals the ancestor and the descendants differ | Alg. 3, p.19 | M | `rsi/gepa/merge.py:49-54`, identical to the paper | 11/11 merge triplets replayed (AUDIT M12) | REPRODUCED |
| M4 | Algorithm 4, MERGE: sample i, j from **all of P**; skip direct ancestry; take ancestors with S[a] ≤ min; module-wise "take the changed side"; both changed → higher S (ties random); return the child with **no acceptance test** | Alg. 4, App. D.1 | M | `merge.py` follows the *reference code*, not Alg. 4 as printed. (i) i, j are drawn from the Pareto survivors (`merge.py:250`, matching the paper's prose "are pareto-optimal"). (ii) It adds a 5-id D_pareto subsample gate, Σ child ≥ max(Σ parents) (`engine.py:477`, `merge.py:262`). (iii) It adds an overlap floor of 5 (`merge.py:252-253`). (iv) If every eligible ancestor has aggregate 0, it falls back to uniform weights (`merge.py:94-99`). **Fix round (F4):** these and the other departures of the reference from the printed algorithm (ancestor drawn ∝ S[a] instead of the first eligible one, 10 × 10 retries, the extra (i, j, module-sources) duplicate check, the `π_a ≠ π_i and π_a ≠ π_j` "both changed" test, the schedule) are now listed in impl §5.16, each marked as reference behaviour | 11/11 merges replayed against the transcribed reference (AUDIT M12) | PARTIAL (faithful to the code; the paper's printed algorithm differs; now fully documented) |
| M5 | Minibatch b = 3, drawn from D_feedback | App. G.4 | M | `config.py:28`. The epoch-shuffled sampler with least-frequent padding is an exact port (`strategies.py:171-203`) | Reference sampler replay 114/114, including the duplicate padded id (AUDIT M3) | REPRODUCED |
| M6 | One module per iteration, chosen by a round-robin policy | §3 p.5, Alg. 1 l.8 | M | `strategies.py:146-152`. A child inherits max(parent pointers) (`state.py` `add_candidate`) | Replay 77/77 (AUDIT M5) | REPRODUCED |
| M7 | The reflection meta-prompt (App. C) | App. C, p.17 | M | `reflection.py:24-40` is **byte-identical** to `ref:strategies/instruction_proposal.py:InstructionProposalSignature.default_prompt_template` (new diff: `True`, SHA-256 `c8125637a50786c3…`, 942 B). The `optimize_anything` template is identical too. Against the paper's listing, only the placeholder names and **two** blank lines differ (see §3, item 2). Deviation 1: the trailing newline of `<curr_param>` is stripped, then restored (`reflection.py:201,216`) | New fuzz: 0/2000 render mismatches against the reference `prompt_renderer`; 67/67 offline prompts match byte for byte (AUDIT M6) | REPRODUCED |
| M8 | Parse the new instruction from the ``` block | App. C | M | `reflection.py` ports `ProposalAdapter.parse`, including the known-truncation rejection. **Fix round (F3):** the provider reason now comes from `response_finish_reason` (`reflection.py:124`): `raw["stop_reason"]`, which `CachedLLM` now keeps on cache hits, or `raw["finish_reason"]`, strings only, as the reference `_finish_reason` | New fuzz: 0/20000 mismatches against the reference `parse_proposal`; 76/76 children match (AUDIT M7). Live check: `claude -p` reports `stop_reason`, and the proposer sees `end_turn` on both the miss and the cache hit (`results/gepa/live_stop_reason_check.json`) | REPRODUCED |
| M9 | Strict acceptance: "if σ′ improved" (averages on the same M) | Alg. 1 l.13-14 | M | `strategies.py:219-223` (sum after > sum before), applied at `engine.py:570`. Same as `ref:strategies/acceptance.py:StrictImprovementAcceptance` | 76/76 gates recomputed from the raw evaluations; no float-tie flips (AUDIT M8) | REPRODUCED |
| M10 | An accepted child is scored on all of D_pareto and added whatever that score; a rejected child is discarded | Alg. 1 l.15-18 | M | `engine.py:573-600`. Rejected children become ledger `x<i>` nodes and are never scored on validation | AUDIT M9 (`no_val_eval_for_rejected`) | REPRODUCED |
| M11 | Merge is "invoked a maximum of 5 times" and "occurs sparsely" | App. G.4, App. D.1 | M | **Fix round (F1).** `merge_cap_mode="hard"` now caps merge *invocations* (merges built and scored on their subsample, accepted or rejected): no merge is checked once `n_invocations` (= `len(merges_performed[0])`) reaches the cap (`merge.py:236-238`). The default `"reference_soft"` still reproduces the reference, whose `total_merges_tested` counts accepted merges only and gates only scheduling (`ref:core/engine.py:702, 1038-1039`). The pre-audit behaviour, a cap on accepted merges, is kept as `"accepted"` (impl §5.4) | Fresh `results/gepa/e4_merge.json`: hard cap 5 → **5.0 invocations per run (max 5 in 60/60 runs)**, 3.2 accepted, 3–8% of rollouts on merge. Soft cap 5 (reference) → 11.8 / 20.3 invocations per run at B = 1500 / 4000, 100% of runs above 5. Accepted cap → up to 13 invocations. Regression tests `tests/test_gepa_claims_fixes.py::test_merge_proposer_hard_cap_counts_invocations_not_acceptances`, `::test_hard_merge_cap_bounds_accepted_plus_rejected_end_to_end` | REPRODUCED with `merge_cap_mode="hard"` (was PARTIAL). The default follows the reference code, which does not cap invocations |
| M12 | Budget B counts rollouts ("invocations of Φ plus evaluation by μ") | §2 p.4 | M | The `RolloutCounter` by phase (`state.py:27-43`) charges every phase (`engine.py:204-209`). The spec §4.5 identity holds. Overshoot ≤ 2b + \|V\| in the default mode; `budget_mode="hard"` is optional | Identity holds in all 6 runs; overshoot 18/300, 34/1500, 1/120 (AUDIT M14) | REPRODUCED |
| M13 | Return the Φ with the highest mean on D_pareto | Alg. 1 l.21 | M | `state.py` `best_idx`: ties go to coverage, then the lowest index (`FullEvaluationPolicy`) | AUDIT M15 | REPRODUCED |
| M14 | Skip reflection when the parent's minibatch is perfect (a code default; not in the paper) | ref `api.py` | M | `engine.py:525-527`. The perfect score is the domain maximum (`engine.py:126-127`) | 31 skip steps checked (AUDIT M4); E12 | REPRODUCED |
| M15 | Only the prompts Π evolve; the weights Θ and the control flow stay frozen | §3 p.4 | M | `default_components` excludes `*.py` (impl §2); every other file is checked unchanged | 76/76 `other_files_unchanged` (AUDIT M18) | REPRODUCED |
| M16 | μ_f gives module-specific feedback, enabling "implicit credit assignment" | §3 p.5 | M | A `domain.reflective_record(task, trial, component)` hook (`adapter.py` `make_reflective_dataset`). RuleWorld gives the reply module the upstream notes. **Fix round:** spec E9 was run with a whole-system record for comparison (`RuleWorldDomain.system_reflective_record`) | **New E9** (`results/gepa/e9_credit_assignment.json`, 30 seeds, B = 1500): module-specific − system-level records **+0.047 [+0.025, +0.068]** (round-robin) and **+0.169 [+0.148, +0.190]** (all); misplaced rule lines in the returned prompts 0.0 vs 5.3. `all` beats round-robin only with module-specific records (+0.056 vs −0.065). Caveat: the mock reflection LM is module-blind by design, which sets the size of the effect | REPRODUCED (mechanism, in the analogue; was PARTIAL) |
| M17 | The optimizer sees the train split; validation gives scores only; test is held out | §4 p.6 | M | The adapter refuses sealed ids (`adapter.py` `evaluate`, `_allowed`). Only minibatch records from D_train reach the reflection prompt | No D_pareto or sealed input in any of the 76 prompts (AUDIT M16) | REPRODUCED |
| M18 | Inference-time search: D_train = D_pareto = the tasks to solve | App. E p.19-20 | M | `val_split=None` → D_pareto = D_train (`engine.py:119-121`, `adapter.py` `resolve_splits`) | **New:** 10 seeds, 30 RuleWorld tasks, B = 1500: seed 0.000 → 0.882 [+0.863, +0.900] on the task set | REPRODUCED (mechanism) |

### 2.2 Quantitative results

| # | Claim | Source | Type | Fidelity / what we can run | Our evidence | Verdict |
|---|---|---|---|---|---|---|
| Q1 | Table 1, Qwen3-8B: every row (Baseline, GRPO, MIPROv2, GEPA, GEPA+Merge) × 6 tasks (HotpotQA, IFBench, HoVer, PUPA, AIME-2025, LiveBench-Math), aggregates and budgets (GEPA 1839–7051 rollouts; GRPO 24k) | Table 1 p.6 | Q | Needs Qwen3-8B inference and LoRA-GRPO on GPUs, plus the DSPy programs and datasets | none | NOT TESTABLE HERE (no GPU, no Qwen, no benchmarks) |
| Q2 | Table 2, GPT-4.1-mini: every row (Baseline, Trace, MIPROv2-No-Demos, MIPROv2, TextGrad, GEPA, GEPA+Merge, GEPA-Qwen-Opt) × 6 tasks | Table 2 p.7 | Q | Needs the GPT-4.1-mini API and about $500 | none | NOT TESTABLE HERE (other-provider API, budget) |
| Q3 | Table 3: Pareto 61.28 vs SelectBestCandidate 54.89 vs BeamSearch(4) 53.95; "up to 8.17 / 11.33 pp" | Table 3, Obs. 3 | Q | Selectors exist: `strategies.py:61-124`. BeamSearch is our [inferred] APO-style version | Qualitative analogue only (see L3) | NOT TESTABLE HERE |
| Q4 | Table 4: 17–92 reflection-LM calls per run | Table 4 p.87 | Q | We meter the calls | E1: **213 calls** per GEPA run at B = 6000 (\|V\| = 30, against the paper's 111–300). Not comparable | NOT TESTABLE HERE |
| Q5 | "+6 pp on average over GRPO" (Qwen aggregate +5.94) | Abstract, overview | Q | ScalarRL is a Bernoulli-logit REINFORCE analogue (`rl.py`), not LoRA-GRPO | See L2 for the analogue (+0.015, not significant) | NOT TESTABLE HERE |
| Q6 | "up to 19 pp" over GRPO (overview: "about 20"); GEPA+Merge 21 pp (HotpotQA) | Abstract, Obs. 1 | Q | as Q5 | Largest per-seed E1 gap at a matched 6000 budget is +0.093 mean | NOT TESTABLE HERE |
| Q7 | "up to 35× fewer rollouts"; "4–35× fewer to optimal test performance" | Abstract, Obs. 1 | Q | Rollouts are counted in the same units; analogue only | `e1_sample_efficiency.json` (re-run in the fix round; RuleWorld rows bit-identical): median RL/GEPA ratio **5.7×** to reach 80% of the oracle and **6.1×** to reach 90%. GEPA overtakes RL's final score (at 24k) in 11/20 seeds after **5.0–35.6× fewer rollouts (median 11.5×)**; in the other 9/20 seeds RL ends higher (now computed by the script: `matched_budget_ratio.gepa_rollouts_to_rl_final`) | PARTIAL (inside 4–35× on a subset; not uniform) |
| Q8 | Matches GRPO's best validation after 243 / 402 / 330 / 1143 / 1179 / 306 rollouts ("up to 78×"); 79–737 train rollouts | Obs. 1 | Q | as Q7 | The Q7 analogue only | NOT TESTABLE HERE |
| Q9 | IFBench: 38.61% after 678 rollouts, against GRPO's 35.88% at 24k | Table 1 caption | Q | – | none | NOT TESTABLE HERE |
| Q10 | Beats MIPROv2 in every setting, by up to 11.1 pp (GPT, PUPA) / 12 pp (Qwen, AIME); overview: "over 10 points" | Obs. 2 | Q | MIPRO-lite (`baselines.py` `FewShotDemoOptimizer`) replaces Optuna TPE with softmax sampling | E7: GEPA − MIPRO-lite **+0.179 [+0.153, +0.204]** at B = 1500 and **+0.211 [+0.189, +0.232]** at B = 4000 (`e7_fewshot.json`) | PARTIAL (direction and "every setting" hold against the analogue; the benchmark pp are not testable) |
| Q11 | AIME-2025 +12 pp over MIPROv2 (Qwen, 32.00 vs 20.00) | Obs. 2, overview | Q | – | none | NOT TESTABLE HERE |
| Q12 | Aggregate gains more than double MIPROv2's (+13.33 / +12.19 vs +5.64) | Obs. 2 | Q | – | Analogue: the gain over seed is about 0.86 for GEPA and 0.68 for MIPRO-lite at B = 1500 (seed test 0.0) | NOT TESTABLE HERE |
| Q13 | Prompts "up to 9.2× shorter" than MIPROv2's; "less than 33% of the size" | App. H Obs. 4, Fig. 16 | Q | Tokens counted on the returned prompts | E7: median ratio **1.5–1.6×** (409 vs 642 tokens); **new:** the largest per-seed ratio is 2.0× | PARTIAL (shorter, not by the paper's margin) |
| Q14 | Merge: up to +5 pp (GPT HoVer); aggregate +1.14 (GPT) / −2.45 (Qwen); Qwen IFBench falls to 28.23 | Obs. 5, Tables 1-2 | Q | – | Qualitative analogue in L5 / L13 | NOT TESTABLE HERE |
| Q15 | GEPA-Qwen-Opt on GPT-4.1-mini: +9.00 aggregate, +27.67 on HotpotQA | Table 2, Obs. 6 | Q | – | Analogue in L10 | NOT TESTABLE HERE |
| Q16 | Inference-time search: NPUEval 4.25 → 30.52% vector utilisation (26.85% without RAG); KernelBench fast₁ from about 0 to above 20% | §5, App. E | Q | Needs an NPU / V100 and GPT-4o | Mechanism only (M18) | NOT TESTABLE HERE |
| Q17 | Adversarial search: GPT-5 Mini on AIME-2025, 76% → 10% | App. F | Q | Inverted reward is possible through a custom Domain; E11 was not run | none | NOT TESTABLE HERE |
| Q18 | All GPT-4.1-mini runs cost under $500 (GEPA $86, +Merge $67) | App. G.3 | Q | – | Our live spend: $0.20 smoke, about $2.31 validation | NOT TESTABLE HERE |
| Q19 | "The majority of GEPA's rollout budget is spent on validation" | Obs. 1 | Q | Per-phase counter | E5: **73%** at b = 3 (0.729 [0.719, 0.737]); 78–85% in the validation runs (AUDIT §3b) | REPRODUCED |
| Q20 | Full-parameter GRPO (SkyRL, 2-hop HoVer) shows "a comparable performance gap" | App. H Fig. 10 | Q | – | none | NOT TESTABLE HERE |

### 2.3 Qualitative findings

| # | Claim | Source | Type | Fidelity / setup | Our evidence | Verdict |
|---|---|---|---|---|---|---|
| L1 | Reflective evolution is far more rollout-efficient than scalar-reward RL | Abstract, Obs. 1 | L | RuleWorld, rich μ_f; RL gets 4× the budget | E1: the GEPA curve is ≥ the RL curve at 7/7 budgets ≤ 6000. Paired GEPA − RL: +0.745 [+0.712, +0.776] at 800 and +0.791 [+0.758, +0.822] at 1600 (now reported by the E1 script, `matched_budget_ratio.paired_gepa_minus_rl_at_equal_budget`) | REPRODUCED |
| L2 | GEPA *outperforms* GRPO at GRPO's much larger budget | Title, Table 1 | L | as L1 | GEPA@6000 − RL@24000 = **+0.015 [−0.004, +0.035]**, not significant; GEPA higher in 11 seeds, RL in 9. RL reaches the 0.95 oracle in 7/20 seeds; GEPA plateaus at 0.917 (0/20). **Fix round (F5):** now computed and reported by `e1_sample_efficiency.py` (`matched_budget_ratio`, `verdict_matched_ratio`) and stated in impl §4 E1 and §3 row 9 | PARTIAL |
| L3 | Pareto selection beats greedy (SelectBestCandidate) and escapes local optima | Obs. 3, Table 3, Fig. 4 | L | Reference `idxmax` greedy; plus a control that breaks ties toward the newest candidate. **Fix round:** `CurrentBestSelector` checked against `ref:strategies/candidate_selector.py:CurrentBestCandidateSelector`: the same `idxmax` over the same mean, so ties go to the oldest candidate exactly as in the reference (pinned by `test_current_best_ties_go_to_oldest_like_reference_idxmax`) | E3 (re-run in the fix round, bit-identical): +0.603 / +0.745 with binary rewards; −0.027 / −0.051 with partial / interference rewards at B = 1500. Against the newest-tie control Pareto is significantly better in **0/8** cells, so the gain comes from tie-breaking, not frontier diversity (`e3_selection.json`) | PARTIAL |
| L4 | BeamSearch(4) is the weakest selector | Table 3 | L | BeamSearch is [inferred] (absent from the reference) | E3: weakest arm in 3/4 worlds at B = 1500; not at B = 4000 | PARTIAL |
| L5 | Merge can help ("up to 5%"), but not always | Obs. 5 | L | Code-faithful merge plus the paper's invocation cap (fixed) | Fresh E4: complementary merges beat both parents 86% / 65% of the time under the reference soft cap (+0.040 / +0.027 at B = 1500 / 4000) and 95% under the hard cap (+0.049). Net gain only when rationed: the paper's hard cap of 5 invocations +0.014 [+0.001, +0.027] and soft cap 1 +0.017 [+0.006, +0.029] at B = 4000; no arm differs at B = 1500 | REPRODUCED |
| L6 | "These strict lineage conditions mean merge occurs sparsely" | App. D.1 | L | Reference soft cap (the default) and the paper's hard invocation cap | Fresh E4, reference soft cap 5: **11.8 / 20.3 invocations and 7.7 / 12.6 accepted merges per run** at B = 1500 / 4000 (valid triplets found in 41% / 31% of checks), 19% / 12% of rollouts; validation merge run: 11 invocations and 7 accepted in 59 iterations (AUDIT §3b). With the paper's hard cap merge is sparse (5 invocations, 3–8% of rollouts), but by construction of the cap, not because of the lineage conditions | CONTRADICTED (the lineage conditions alone do not make merge sparse in RuleWorld; its 2-module world makes `DESIRABLE` easy to meet) |
| L7 | Reflection exploits textual feedback / evaluation traces | §3; overview "works best when feedback includes text" | L | μ_f ∈ {rich, symptom, score_only} | E2: rich − score-only +0.179 [+0.146, +0.211]. Symptom vs score-only is not significant. Caveats: the paper never ran this ablation, and RuleWorld's rich feedback names the fix by construction | REPRODUCED |
| L8 | Evolved instructions have a lower generalization gap than MIPROv2's | Obs. 2, App. J | L | Gap = validation − test (exact) | **New** paired gap GEPA − MIPRO-lite: **−0.028 [−0.044, −0.013]** (B = 1500) and −0.030 [−0.049, −0.011] (B = 4000) | REPRODUCED |
| L9 | Instruction-only optimization beats joint instruction + few-shot optimization | Obs. 2 | L | MIPRO-lite analogue | E7 (Q10 numbers) | REPRODUCED |
| L10 | Prompts transfer across models (Qwen → GPT) | Obs. 6 | L | Analogue: optimize under task model A (default) and evaluate under task model B (capacity 8, dilution 0.05, slip 0.15, p_demo 0.1) in the same world | **New:** transferred gain +0.595 against direct-on-B +0.570; difference +0.026 [−0.020, +0.072]; retention 1.05 (paper: about 0.74). The transfer is close to trivial here, because both "models" read the same rule semantics | PARTIAL |
| L11 | GEPA is a promising inference-time search; lessons carry across tasks | §5, App. E; overview "promise … for optimizing code" | L | Multi-task (D_train = D_pareto) vs independent single-task runs (b = 1) at equal total rollouts. Not code | **New:** multi-task 0.882 against single-task 0.935: **−0.052 [−0.075, −0.031]**. The cross-task advantage does not appear here (single-task runs may memorise ticket facts and get 30× more reflection calls) | PARTIAL |
| L12 | SelectBestCandidate "led to a local optimum after one iteration"; Pareto gives "a balanced search tree" | Fig. 4 | L | Tree metrics (`analysis.py`) | E3 binary: CurrentBest tree depth about 1.3 and stalled for the last 174 of 178 iterations, against a Pareto depth of about 17.7. With partial rewards the depths are similar | PARTIAL |
| L13 | The merge degradation on Qwen comes from budget allocation and invocation timing | Obs. 5 | L | Arms vary the cap | Fresh E4: unrationed soft caps (20–27 invocations per run at B = 4000) show no gain; the rationed hard cap 5 and soft cap 1 do. But early merges gained *more* than late ones (+0.022 vs +0.005 at B = 1500; +0.011 vs −0.002 at B = 4000) | PARTIAL |

### 2.4 Caveats

| # | Claim | Source | Type | Evidence | Verdict |
|---|---|---|---|---|---|
| C1 | "It needs a good metric", and works best with text feedback | overview | C | E2 (score-only is 0.18 worse); E12 (a train-memorising seed scores 1.0 on D_pareto = D_train but 0.0 on test) | REPRODUCED |
| C2 | "It changes prompts only, not tools, memory or control flow" | overview; §3 | C | M15; `components=` can relax it explicitly | REPRODUCED |
| C3 | "With few examples it can still overfit; keep a separate validation set" | overview; App. J | C | E6: at \|train\| = 6 the gap is +0.424 with D_pareto = D_train and +0.031 with a separate validation set; test 0.576 vs 0.644 (confound noted in impl §4 E6) | REPRODUCED |
| C4 | GRPO still wins on AIME-2025 (38.00 vs 32.00) | Table 1 | C | none | NOT TESTABLE HERE |
| C5 | The meta-prompt pulls "niche, domain-specific facts" into prompts, including example content | App. C; spec weakness 4 | C | RuleWorld prompts carry 3–15 ticket facts (E6); Haiku copied 5 training answers (AUDIT §2) | REPRODUCED |
| C6 | The noisy 3-example single-draw gate and the D_pareto argmax cause false accepts and winner's curse | spec weaknesses 3 and 8 (implicit in Alg. 1) | C | E5 false-accept rate 0.48 at b = 3. The merge validation run returned the 7th-best of 40 candidates (AUDIT §3b). E12: 5% noise gives 12 spurious accepts per run | REPRODUCED |
| C7 | A saturated seed is returned unchanged | spec (skill gotcha §9) | C | E12: 0 accepts; the seed is returned in 100% of noise-free runs | REPRODUCED |

Not counted: E13 (determinism and exact resume) is a design goal of this repo, not a paper claim. It is REPRODUCED (`e13_determinism.json`).

## 3. Mismatches the earlier reviews missed

Each item now carries its status after the fix round (details in §5).

1. **The "hard" merge cap limits accepted merges, not invocations** (medium). **Status: FIXED (F1)** — `"hard"` now caps invocations; the old behaviour is `"accepted"`.
   - The paper says merge "is invoked a maximum of 5 times".
   - `merge_cap_mode="hard"` blocks attempts only once `total_merges_tested >= 5` (`merge.py:206`). That counter increments only on *acceptance* (`merge.py:210-212`, and the same in `ref:core/engine.py:1038-1039`). Rejected merges still cost 5 rollouts each and are unlimited.
   - `e4_merge.json`, hard5 at B = 1500, seed 0: 5 accepted + 7 rejected = 12 merges evaluated, 25 attempts, 450 of 1500 rollouts spent on merge. *(These numbers came from the stale pre-fix-round E4 JSON (§5, S1). With the current code the old semantics, now `"accepted"`, build 8 merges for seed 0 and 13 for seed 1, 5 accepted in each; the finding stands.)*
   - impl §5.4 and the spec §4.4 recipe present `"hard"` as the paper's semantics. Neither mode implements an invocation cap.
2. **Spec §6.1 miscounts the whitespace difference in the meta-prompt** (doc only). **Status: FIXED (F2)** in the spec.
   - The LaTeX listing has an extra blank line after "…perform a task for me:" **and** after "…could be better:". The spec names only the first.
   - The code is byte-identical to the repo, as intended.
3. **The finish-reason truncation check is effectively dead in live runs** (low). **Status: FIXED (F3)** — the core cache keeps `stop_reason`, the proposer reads it (and `finish_reason`), and a live check confirmed `claude -p` reports it.
   - `reflection.py:211` looks for `resp.raw["stop_reason"]`.
   - A `CachedLLM` hit returns `raw={"cached": True, "usage": …}` (`rsi/core/llm.py:382`), so every cached reflection skips the check.
   - Whether the `claude -p` JSON carries `stop_reason` at all was not verified.
   - The reference keeps `finish_reason` through `.strip()` (`ref:lm.py:46`). So only the `<think>` heuristic protects our live runs.
4. **Algorithm 4's departures are not listed as paper deviations in impl §5** (doc). **Status: DOCUMENTED (F4)** in impl §5.16. The Pareto-only pair pool, the 5-id ≥ acceptance gate and the overlap floor of 5 are in the spec §3, but impl §5 lists only the zero-weight and cap deviations.
5. **The E1 verdict omits the matched-ratio comparison** (evidence gap). **Status: FIXED (F5)** — the E1 script computes it and the docs state it.
   - At the paper's own 1:4 budget ratio, GEPA only ties RL (+0.015, not significant), and RL finds the oracle in 7/20 seeds.
   - impl §4 E1 reports only rollouts-to-target, which reads more favourably.
6. **The reflection-call scale differs from the paper** (observation). **Status: DOCUMENTED (F6)**, not a code defect. We make 213 reflection calls per run at B = 6000, against the paper's 17–92. Budgets per candidate are not comparable, so rollout ratios transfer only loosely.
7. **Stale implementation docs** (low). **Status: FIXED (F7)**; the fix round also found the stored E4 results stale (S1) and regenerated them.
   - `docs/methods/gepa-impl.md` says there are 63 tests; there are now 67, all passing.
   - It says `engine.py` has 492 lines; it now has 601.
   - The E1 AgentQA table still predates the mock fix (already flagged in AUDIT §6).

Re-confirmed as faithful, with no new issue: the Pareto pruning, the sampler, round-robin inheritance, strict acceptance, the renderer and parser (fuzzed), the budget identity and the argmax return.

## 4. What would be needed to fully reproduce

- **Models and hardware.**
  - Qwen3-8B served locally, for example with vLLM on one or more 24–80 GB GPUs, using the paper's decoding (T = 0.6, top-p 0.95, top-k 20; 16k context).
  - GPT-4.1-mini API access (`gpt-4.1-mini-2025-04-14`, T = 1.0).
  - GPT-4o for the kernel search and GPT-5 Mini for adversarial search.
  - The reflection LM matched to the task LM in each setting.
- **Systems and data.** The DSPy programs and splits from App. G.1:
  - HotpotQA and HoVer: 150/300/300;
  - IFBench: 150/300/294 with IF-RLVR;
  - AIME 2022–24 for train and validation, AIME-2025 × 5 for test;
  - LiveBench-Math, shuffled with seed 0;
  - PUPA / PAPILLON: 111/111/221.
  - Plus the paper's μ_f feedback functions, which also need a DSPy adapter; we have none.
- **Baselines.**
  - LoRA-GRPO through mmGRPO: rank 16, α 64, group 12, 4 instances/step, grad-accum 20, lr 1e-5, β 0.01, 500 steps = 24k rollouts, per task and model.
  - SkyRL full-parameter GRPO for the HoVer check.
  - MIPROv2 `auto=heavy`, with and without demos.
  - Trace (OptoPrime) and TextGrad.
- **Budget matching.** Record MIPROv2's rollouts per benchmark and cap GEPA within 10.15%. Run GEPA and GEPA+Merge with at most 5 merge invocations: `merge_cap_mode="hard"` (§3, item 1, fixed).
- **Extended results.** NPUEval on AMD XDNA2 and KernelBench on a V100 with a retrieval-augmented μ_f, run under a cache. The adversarial AIME setup with an inverted reward.
- **Statistics.** The paper reports single runs. A faithful check would repeat each cell over at least 3 seeds.
- **Cost estimate.** About $500 of GPT-4.1-mini (the paper's App. G.3 figure), plus GPU time for 12 GRPO runs of 24k rollouts. This is out of reach on this CPU-only machine with a $3 live cap.
- **Cheaper steps.** Done in the fix round: E9 (module-specific vs system-level feedback), a true invocation cap with E4 re-run, and the E1 AgentQA table re-run with the fixed mock. Still open here:
  - E11 (inverted reward) on RuleWorld;
  - a larger live `claude -p` run on the Tier-2 constraint task (spec §9.2) to test absolute gains with a real reflection LM.

## 5. Fix log

The fix round covered every open GEPA finding: the seven items of §3, the two open items of the validation register (`validation/gepa/AUDIT.md` §4) and the evidence gaps behind M11, M16, L2 and L3. Each code finding was first reproduced with a scratch script, then fixed, then pinned by a regression test that fails on the old code (checked by running the new tests against a copy of the package with the pre-fix `rsi/gepa/` files: 7 of the 8 code tests fail there; the eighth, the tie-breaking test, pins behaviour that was already correct). All 79 GEPA tests pass (`python -m pytest -q tests/test_gepa_*.py`, about 25 s). File references are to the fixed code.

| # | finding | disposition and fix | code | regression test (`tests/test_gepa_claims_fixes.py`) | evidence |
|---|---|---|---|---|---|
| F1 | §3.1: `merge_cap_mode="hard"` capped *accepted* merges; rejected merges (5 rollouts each) were unlimited, although the paper says merge "is invoked a maximum of 5 times" | **Fixed.** `"hard"` now caps *invocations*: merges built and scored on their subsample, accepted or rejected. `MergeProposer.n_invocations` = `len(merges_performed[0])`, so it is persisted and survives resume. Checks that find no triplet stay free (the paper: "invoking merge when identified"). The old behaviour is kept, non-default, as `"accepted"`; the default `"reference_soft"` is unchanged because it is the reference code. Soft vs hard against the reference's actual semantics: impl §5.4 and spec §4.4 | `rsi/gepa/merge.py:59` (modes), `:224` (`n_invocations`), `:236-240` (`should_attempt`); `config.py:48,105`; `api.py:55` (`meta["n_merge_invocations"]`); `tracing.py:133` | `test_merge_proposer_hard_cap_counts_invocations_not_acceptances`, `test_hard_merge_cap_bounds_accepted_plus_rejected_end_to_end`, `test_hard_merge_cap_one_invocation_and_soft_cap_unbounded`, `test_e4_results_hard_cap_bounds_invocations` | Reproduced before the fix: RuleWorld B = 1500, seeds 0 / 1, old hard cap → 8 / 13 merges built (5 accepted each). After: 5 / 5 (2 and 1 accepted). Fresh E4: 5.0 invocations per run, max 5 in 60/60 runs |
| F2 | §3.2: spec §6.1 named one of the two extra blank lines of the LaTeX meta-prompt listing | **Fixed (doc).** §6.1 now names both (after "…for me:" and after "…could be better:"). The §4.4 recipe that had suggested the accepted-merge guard, and the §9 class sketch, were corrected too | `docs/methods/gepa.md` §6.1, §4.4, §9 | none (documentation); the template's byte identity with the reference is already tested (`test_gepa_core.py`) | Checked against `scratchpad/papers/gepa-iclr2026-latex.txt` (the lstlisting after line 1116) |
| F3 | §3.3: the finish-reason truncation check never fired on `CachedLLM` hits | **Fixed.** The core fix (`rsi.core.CachedLLM` stores `stop_reason` and returns it on hits; not in this package) makes the old reader work on hits. In this package `ReflectionProposer` now reads the reason through `response_finish_reason`: `raw["stop_reason"]` or `raw["finish_reason"]` (OpenAI / LiteLLM style, which the old reader missed), strings only, as the reference `_finish_reason`; it is recorded per component (`ReflectionResult.finish`, trace `finish_reasons`). Unverified before: whether `claude -p` reports a stop reason at all. It does | `rsi/gepa/reflection.py:124` (`response_finish_reason`), `:231-232`; `rsi/gepa/tracing.py:237` | `test_truncated_reflection_is_rejected_on_cache_miss_and_hit`, `test_finish_reason_key_and_complete_fence_semantics` | Reproduced: a `max_tokens`-truncated reply is rejected on the miss and on the hit; a cache entry written before the core fix (no reason stored) still passes, since nothing is known about it. Live (`results/gepa/live_stop_reason_check.json`, fresh cache): `claude -p` → `end_turn`, the same on miss and hit. No recorded GEPA live reply lacks a fence pair (16 live-smoke replies, 9 validation replies with text), so no earlier decision changes and no live re-run was needed |
| F4 | §3.4: impl §5 did not list the Pareto-only pair pool, the 5-id ≥ acceptance gate or the overlap floor as departures from Alg. 4 | **Documented.** impl §5.16 lists every departure of the reference (and ours) from the printed Alg. 4: pair pool, retries, ancestor drawn ∝ S[a], the "both changed" test, the extra duplicate check, the overlap floor, the subsample acceptance test and the schedule. The reference does all of them, so none was changed | `docs/methods/gepa-impl.md` §5.16 | none (documentation) | Cross-checked against `ref:proposer/merge.py` and `ref:core/engine.py:1007-1093` |
| F5 | §3.5: the E1 summary reported only rollouts-to-target, not the paper's own 1:4 budget ratio | **Fixed.** `e1_sample_efficiency.py` computes `matched_budget_ratio` (paired final test, oracle-reaching seeds, rollouts GEPA needs to reach RL's final score, paired curves at equal budgets) and prints `verdict_matched_ratio`; impl §4 E1 and §3 row 9 state it | `experiments/gepa/e1_sample_efficiency.py:132-190` | `test_e1_results_report_the_matched_budget_ratio` | GEPA@6000 − RL@24000 = +0.015 [−0.004, +0.035], not significant; RL reaches the oracle in 7/20 seeds, GEPA in 0/20 |
| F6 | §3.6: 213 reflection calls per run against the paper's 17–92 | **Documented** (observation, not a code defect). Per-candidate budgets differ (\|V\| = 30, RuleWorld's cheap rollouts), so rollout ratios transfer only loosely; Q4 stays NOT TESTABLE HERE | – | – | Re-run E1: 213 calls per GEPA run at B = 6000 (unchanged) |
| F7 | §3.7: stale impl doc (63 tests, `engine.py` 492 lines, the pre-fix E1 AgentQA table) | **Fixed.** Counts updated (79 tests, 611 lines) and the E1 AgentQA table replaced by the re-run | `docs/methods/gepa-impl.md` §1, §4 E1 | `test_e1_results_report_the_matched_budget_ratio` (checks the 15 regenerated AgentQA rows) | see V2 |
| V1 | AUDIT §4: the shadow monitor's wall time counted toward `Budget.max_wall_s` / `Timeout` | **Fixed.** After each monitor observation the engine credits `ShadowMonitor.last_elapsed_s` to every stopper with a `credit` method: `Timeout.credit`, and `BudgetStopper.credit` → the core's `Budget.credit`. The credited total goes to the trace's `run_end` (not to `state.json`, which stays deterministic) | `rsi/gepa/engine.py:200` (`credit_wall_time`); `rsi/gepa/tracing.py:311`; `rsi/gepa/stoppers.py:39,105,142` | `test_monitor_wall_time_does_not_count_toward_wall_clock_stoppers[budget]`, `[timeout]` | Reproduced with a fake clock (monitor 100 s per observation): before, both `Budget(max_wall_s=50)` and `Config(timeout_s=50)` stopped at iteration 0 after 30 rollouts; after, the run reached its rollout budget (11 iterations). The offline validation runs, re-run from scratch, are byte-identical |
| V2 | AUDIT §4: the E1 AgentQA table predated the AgentQA mock fix | **Fixed** by re-running E1 at full settings | `results/gepa/e1_sample_efficiency.json` | `test_e1_results_report_the_matched_budget_ratio` | Holdout / OOD: GEPA 0.928 / 0.920, ScoreOnly 0.948 / 0.960, ScalarRL 0.906 / 0.890, seed 0.445 / 0.390; OOD GEPA − ScoreOnly −0.040 [−0.070, −0.010] (5 seeds). The confound is gone and text feedback still gives no edge on AgentQA. The RuleWorld rows reproduced bit for bit |
| S1 | Found in the fix round: `results/gepa/e4_merge.json` was stale | **Fixed** by re-running E4 (with the new `accepted` arm and invocation counts). Its merge rows predated the review change that gave the merge subsample fresh rollout seeds (impl §5.6): no merge row reproduced with the current code (seed 0, soft cap 5, B = 1500: stored 0.936, current 0.848), while the GEPA arm reproduced exactly | `experiments/gepa/e4_merge.py` | `test_e4_results_hard_cap_bounds_invocations` | Fresh numbers in M11, L5, L6, L13 and impl §4 E4. The other result files were spot-checked with the current code (18 rows of E2, E5, E6, E7 and E12: all identical) and E3 and E13 were re-run (identical), so they are current |
| L3 | "Pareto's win comes from tie-breaking": make sure CurrentBest breaks ties like the reference | **Already correct; verified and pinned.** `CurrentBestSelector` = the reference `idxmax(program_full_scores_val_set)`, same summation order, ties to the oldest candidate. No change; L3 stays PARTIAL | `rsi/gepa/strategies.py:61` | `test_current_best_ties_go_to_oldest_like_reference_idxmax` | E3 re-run: bit-identical to the stored JSON |
| M16 | Module-level credit assignment was never isolated (spec E9 not run) | **Fixed (evidence gap).** New whole-system record `RuleWorldDomain.system_reflective_record`, a `misplaced_rule_lines` metric and the E9 experiment | `rsi/domains/ruleworld/domain.py:150,165`; `experiments/gepa/e9_credit_assignment.py` | `test_system_record_shows_every_module_and_misplaced_rules_are_counted`, `test_e9_results_module_specific_records_avoid_misplaced_rules` | Module-specific − system-level +0.047 [+0.025, +0.068] (round-robin), +0.169 [+0.148, +0.190] (all); misplaced rules 0.0 vs 5.3 |

**Not changed, and why.**
- The default merge cap stays `"reference_soft"`: it is what `gepa.optimize(use_merge=True)` does, and this implementation follows the reference wherever the reference and the paper's text disagree (M2, M4). The paper's semantics are one option away (`merge_cap_mode="hard"`).
- Claims needing Qwen3-8B, GPT-4.1-mini, GRPO on GPUs or the paper's benchmarks stay NOT TESTABLE HERE (Q1–Q6, Q8, Q9, Q11, Q12, Q14–Q18, Q20, C4).
- Paper-internal points stay documented, not "fixed": Alg. 4 as printed versus the released code (M4), the "sparse merge" wording (L6, still CONTRADICTED in RuleWorld under the reference's semantics), and the whitespace of the printed meta-prompt (F2).

**Experiments re-run in the fix round** (offline, full settings, 2 workers): E1 (5 min), E3 (5 min, identical), E4 (2 min), E9 (new, 15 s), E13 (1 min, identical); the three offline validation runs and stage B into a scratch directory (identical; see `validation/gepa/AUDIT.md` §5). Live: two `claude -p` haiku calls for the stop-reason check, **$0.012** in total.

**Core change requests.** None was needed. Two notes for the core owner: `rsi.core.AnthropicAPI` returns no `raw`, so no stop reason reaches the truncation check on that backend; and `Budget._t0`'s `default_factory=time.time` binds the real clock at import, which tests that fake the clock must work around.
