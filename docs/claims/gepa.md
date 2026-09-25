# GEPA: claim-by-claim audit

This page checks our implementation (`rsi/gepa/`, `rsi/domains/ruleworld/`) against **every** claim in "GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning" (ICLR 2026 camera-ready). It covers mechanisms, table numbers, observations and caveats. It is an audit only: no code was changed.

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
- `tests/test_gepa_*.py`: 67 passed in 22 s.

## 1. Summary

| verdict | mechanism | quantitative | qualitative | caveat | **total** |
|---|---|---|---|---|---|
| REPRODUCED | 15 | 1 | 5 | 6 | **27** |
| PARTIAL | 3 | 3 | 7 | 0 | **13** |
| NOT REPRODUCED | 0 | 0 | 0 | 0 | **0** |
| NOT TESTABLE HERE | 0 | 16 | 0 | 1 | **17** |
| CONTRADICTED | 0 | 0 | 1 | 0 | **1** |
| **total** | 18 | 20 | 13 | 7 | **58** |

**Mechanisms.** The mechanisms are faithful to the reference code. The reflection meta-prompt is byte-identical to the repo: SHA-256 prefix `c8125637a50786c3`, 942 bytes. In the fuzz tests the renderer and parser matched the reference exactly (0 mismatches in 2,000 renders and 20,000 parses). Stage-B replayed all 125 audited steps exactly. The three PARTIAL mechanisms are all places where the reference code, and therefore ours, departs from the paper's text:
- Algorithm 4 as printed;
- the "invoked a maximum of 5 times" merge cap;
- module-level credit assignment, which was never isolated in an experiment.

**Quantitative results.** Every table number in the paper comes from Qwen3-8B / GPT-4.1-mini runs, with GRPO fine-tuning on GPUs, so none of them can be tested here. Our CPU analogues reproduce the *direction* of four claims:
- rollout efficiency;
- beating a few-shot optimizer;
- shorter prompts;
- validation dominating the budget.

The *size* is reproduced for none of them.

**Headline caveat from the new re-analysis.** The paper's main comparison is GEPA with about 2–7k rollouts against GRPO with 24k. The analogue here is GEPA at B = 6000 against ScalarRL at B = 24000:
- GEPA − RL = **+0.015 [−0.004, +0.036]**, which is not significant.
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
| M4 | Algorithm 4, MERGE: sample i, j from **all of P**; skip direct ancestry; take ancestors with S[a] ≤ min; module-wise "take the changed side"; both changed → higher S (ties random); return the child with **no acceptance test** | Alg. 4, App. D.1 | M | `merge.py:78-137` follows the *reference code*, not Alg. 4 as printed. (i) i, j are drawn from the Pareto survivors (`merge.py:217`, matching the paper's prose "are pareto-optimal"). (ii) It adds a 5-id D_pareto subsample gate, Σ child ≥ max(Σ parents) (`engine.py:467`, `merge.py:229-234`). (iii) It adds an overlap floor of 5 (`merge.py:219-220`). (iv) If every eligible ancestor has aggregate 0, it falls back to uniform weights (`merge.py:70-75`). Rules (ii)–(iii) are in the spec but are **not** in impl §5's list of deviations from the paper | 11/11 merges replayed against the transcribed reference (AUDIT M12) | PARTIAL (faithful to the code; the paper's printed algorithm differs) |
| M5 | Minibatch b = 3, drawn from D_feedback | App. G.4 | M | `config.py:28`. The epoch-shuffled sampler with least-frequent padding is an exact port (`strategies.py:171-203`) | Reference sampler replay 114/114, including the duplicate padded id (AUDIT M3) | REPRODUCED |
| M6 | One module per iteration, chosen by a round-robin policy | §3 p.5, Alg. 1 l.8 | M | `strategies.py:146-152`. A child inherits max(parent pointers) (`state.py` `add_candidate`) | Replay 77/77 (AUDIT M5) | REPRODUCED |
| M7 | The reflection meta-prompt (App. C) | App. C, p.17 | M | `reflection.py:24-40` is **byte-identical** to `ref:strategies/instruction_proposal.py:InstructionProposalSignature.default_prompt_template` (new diff: `True`, SHA-256 `c8125637a50786c3…`, 942 B). The `optimize_anything` template is identical too. Against the paper's listing, only the placeholder names and **two** blank lines differ (see §3, item 2). Deviation 1: the trailing newline of `<curr_param>` is stripped, then restored (`reflection.py:201,216`) | New fuzz: 0/2000 render mismatches against the reference `prompt_renderer`; 67/67 offline prompts match byte for byte (AUDIT M6) | REPRODUCED |
| M8 | Parse the new instruction from the ``` block | App. C | M | `reflection.py:111-144` ports `ProposalAdapter.parse`, including the known-truncation rejection | New fuzz: 0/20000 mismatches against the reference `parse_proposal`; 76/76 children match (AUDIT M7) | REPRODUCED |
| M9 | Strict acceptance: "if σ′ improved" (averages on the same M) | Alg. 1 l.13-14 | M | `strategies.py:219-223` (sum after > sum before), applied at `engine.py:570`. Same as `ref:strategies/acceptance.py:StrictImprovementAcceptance` | 76/76 gates recomputed from the raw evaluations; no float-tie flips (AUDIT M8) | REPRODUCED |
| M10 | An accepted child is scored on all of D_pareto and added whatever that score; a rejected child is discarded | Alg. 1 l.15-18 | M | `engine.py:573-600`. Rejected children become ledger `x<i>` nodes and are never scored on validation | AUDIT M9 (`no_val_eval_for_rejected`) | REPRODUCED |
| M11 | Merge is "invoked a maximum of 5 times" and "occurs sparsely" | App. G.4, App. D.1 | M | The default `merge_cap_mode="reference_soft"` (`merge.py:200-212`) reproduces the reference's soft cap: accepted merges exceed 5 in 63–87% of runs. The `"hard"` mode checks `total_merges_tested`, which counts **accepted merges only** (`merge.py:206,210-212`). So neither mode caps *invocations* (see §3, item 1) | `results/gepa/e4_merge.json`: hard cap 5 → 4.6–4.8 accepted, but **19–26 attempts** per run, and 21% of rollouts go to merge at B = 1500. Seed 0: 5 accepted + 7 rejected = 12 merges evaluated | PARTIAL |
| M12 | Budget B counts rollouts ("invocations of Φ plus evaluation by μ") | §2 p.4 | M | The `RolloutCounter` by phase (`state.py:27-43`) charges every phase (`engine.py:204-209`). The spec §4.5 identity holds. Overshoot ≤ 2b + \|V\| in the default mode; `budget_mode="hard"` is optional | Identity holds in all 6 runs; overshoot 18/300, 34/1500, 1/120 (AUDIT M14) | REPRODUCED |
| M13 | Return the Φ with the highest mean on D_pareto | Alg. 1 l.21 | M | `state.py` `best_idx`: ties go to coverage, then the lowest index (`FullEvaluationPolicy`) | AUDIT M15 | REPRODUCED |
| M14 | Skip reflection when the parent's minibatch is perfect (a code default; not in the paper) | ref `api.py` | M | `engine.py:525-527`. The perfect score is the domain maximum (`engine.py:126-127`) | 31 skip steps checked (AUDIT M4); E12 | REPRODUCED |
| M15 | Only the prompts Π evolve; the weights Θ and the control flow stay frozen | §3 p.4 | M | `default_components` excludes `*.py` (impl §2); every other file is checked unchanged | 76/76 `other_files_unchanged` (AUDIT M18) | REPRODUCED |
| M16 | μ_f gives module-specific feedback, enabling "implicit credit assignment" | §3 p.5 | M | A `domain.reflective_record(task, trial, component)` hook (`adapter.py` `make_reflective_dataset`). RuleWorld gives the reply module the upstream notes | The E3 `+all` arms touch on it. Spec E9 (module-specific vs system-level feedback) was **not run** | PARTIAL |
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
| Q7 | "up to 35× fewer rollouts"; "4–35× fewer to optimal test performance" | Abstract, Obs. 1 | Q | Rollouts are counted in the same units; analogue only | `e1_sample_efficiency.json`: median RL/GEPA ratio **5.7×** to reach 80% of the oracle and **6.1×** to reach 90%. **New** (`e1_match_rl.py`): GEPA overtakes RL's final score (at 24k) in 11/20 seeds after 675–4848 rollouts, which is **5.0–35.6× fewer (median 11.5×)**; in the other 9/20 seeds RL ends higher | PARTIAL (inside 4–35× on a subset; not uniform) |
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
| L1 | Reflective evolution is far more rollout-efficient than scalar-reward RL | Abstract, Obs. 1 | L | RuleWorld, rich μ_f; RL gets 4× the budget | E1: the GEPA curve is ≥ the RL curve at 7/7 budgets ≤ 6000. **New** paired GEPA − RL: +0.745 [+0.713, +0.776] at 800 and +0.791 at 1600 | REPRODUCED |
| L2 | GEPA *outperforms* GRPO at GRPO's much larger budget | Title, Table 1 | L | as L1 | **New** (`e1_reanalyse.py`): GEPA@6000 − RL@24000 = **+0.015 [−0.004, +0.036]**, not significant. RL reaches the 0.95 oracle in 7/20 seeds; GEPA plateaus at 0.917 | PARTIAL |
| L3 | Pareto selection beats greedy (SelectBestCandidate) and escapes local optima | Obs. 3, Table 3, Fig. 4 | L | Reference `idxmax` greedy; plus a control that breaks ties toward the newest candidate | E3: +0.603 / +0.745 with binary rewards; −0.027 / −0.051 with partial / interference rewards at B = 1500. Against the newest-tie control Pareto is significantly better in **0/8** cells, so the gain comes from tie-breaking, not frontier diversity (`e3_selection.json`) | PARTIAL |
| L4 | BeamSearch(4) is the weakest selector | Table 3 | L | BeamSearch is [inferred] (absent from the reference) | E3: weakest arm in 3/4 worlds at B = 1500; not at B = 4000 | PARTIAL |
| L5 | Merge can help ("up to 5%"), but not always | Obs. 5 | L | Code-faithful merge plus a hard-cap option | E4: complementary merges beat both parents 94% of the time (+0.048). Net gain only when rationed: hard cap +0.014 [+0.003, +0.026] and soft cap 1 +0.016 at B = 4000; none at B = 1500 | REPRODUCED |
| L6 | "These strict lineage conditions mean merge occurs sparsely" | App. D.1 | L | Reference soft cap (the default) | E4: **25–69 attempts and 7–14 accepted merges per run**, using 21–32% of rollouts; validation merge run: 11 attempts and 7 accepted in 59 iterations (AUDIT §3b) | CONTRADICTED (under reference semantics) |
| L7 | Reflection exploits textual feedback / evaluation traces | §3; overview "works best when feedback includes text" | L | μ_f ∈ {rich, symptom, score_only} | E2: rich − score-only +0.179 [+0.146, +0.211]. Symptom vs score-only is not significant. Caveats: the paper never ran this ablation, and RuleWorld's rich feedback names the fix by construction | REPRODUCED |
| L8 | Evolved instructions have a lower generalization gap than MIPROv2's | Obs. 2, App. J | L | Gap = validation − test (exact) | **New** paired gap GEPA − MIPRO-lite: **−0.028 [−0.044, −0.013]** (B = 1500) and −0.030 [−0.049, −0.011] (B = 4000) | REPRODUCED |
| L9 | Instruction-only optimization beats joint instruction + few-shot optimization | Obs. 2 | L | MIPRO-lite analogue | E7 (Q10 numbers) | REPRODUCED |
| L10 | Prompts transfer across models (Qwen → GPT) | Obs. 6 | L | Analogue: optimize under task model A (default) and evaluate under task model B (capacity 8, dilution 0.05, slip 0.15, p_demo 0.1) in the same world | **New:** transferred gain +0.595 against direct-on-B +0.570; difference +0.026 [−0.020, +0.072]; retention 1.05 (paper: about 0.74). The transfer is close to trivial here, because both "models" read the same rule semantics | PARTIAL |
| L11 | GEPA is a promising inference-time search; lessons carry across tasks | §5, App. E; overview "promise … for optimizing code" | L | Multi-task (D_train = D_pareto) vs independent single-task runs (b = 1) at equal total rollouts. Not code | **New:** multi-task 0.882 against single-task 0.935: **−0.052 [−0.075, −0.031]**. The cross-task advantage does not appear here (single-task runs may memorise ticket facts and get 30× more reflection calls) | PARTIAL |
| L12 | SelectBestCandidate "led to a local optimum after one iteration"; Pareto gives "a balanced search tree" | Fig. 4 | L | Tree metrics (`analysis.py`) | E3 binary: CurrentBest tree depth about 1.3 and stalled for the last 174 of 178 iterations, against a Pareto depth of about 17.7. With partial rewards the depths are similar | PARTIAL |
| L13 | The merge degradation on Qwen comes from budget allocation and invocation timing | Obs. 5 | L | Arms vary the cap | E4: unrationed soft caps show no gain; rationed ones do. But early merges gained *more* than late ones (+0.031 vs +0.009) | PARTIAL |

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

1. **The "hard" merge cap limits accepted merges, not invocations** (medium).
   - The paper says merge "is invoked a maximum of 5 times".
   - `merge_cap_mode="hard"` blocks attempts only once `total_merges_tested >= 5` (`merge.py:206`). That counter increments only on *acceptance* (`merge.py:210-212`, and the same in `ref:core/engine.py:1038-1039`). Rejected merges still cost 5 rollouts each and are unlimited.
   - `e4_merge.json`, hard5 at B = 1500, seed 0: 5 accepted + 7 rejected = 12 merges evaluated, 25 attempts, 450 of 1500 rollouts spent on merge.
   - impl §5.4 and the spec §4.4 recipe present `"hard"` as the paper's semantics. Neither mode implements an invocation cap.
2. **Spec §6.1 miscounts the whitespace difference in the meta-prompt** (doc only).
   - The LaTeX listing has an extra blank line after "…perform a task for me:" **and** after "…could be better:". The spec names only the first.
   - The code is byte-identical to the repo, as intended.
3. **The finish-reason truncation check is effectively dead in live runs** (low).
   - `reflection.py:211` looks for `resp.raw["stop_reason"]`.
   - A `CachedLLM` hit returns `raw={"cached": True, "usage": …}` (`rsi/core/llm.py:382`), so every cached reflection skips the check.
   - Whether the `claude -p` JSON carries `stop_reason` at all was not verified.
   - The reference keeps `finish_reason` through `.strip()` (`ref:lm.py:46`). So only the `<think>` heuristic protects our live runs.
4. **Algorithm 4's departures are not listed as paper deviations in impl §5** (doc). The Pareto-only pair pool, the 5-id ≥ acceptance gate and the overlap floor of 5 are in the spec §3, but impl §5 lists only the zero-weight and cap deviations.
5. **The E1 verdict omits the matched-ratio comparison** (evidence gap).
   - At the paper's own 1:4 budget ratio, GEPA only ties RL (+0.015, not significant), and RL finds the oracle in 7/20 seeds.
   - impl §4 E1 reports only rollouts-to-target, which reads more favourably.
6. **The reflection-call scale differs from the paper** (observation). We make 213 reflection calls per run at B = 6000, against the paper's 17–92. Budgets per candidate are not comparable, so rollout ratios transfer only loosely.
7. **Stale implementation docs** (low).
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
- **Budget matching.** Record MIPROv2's rollouts per benchmark and cap GEPA within 10.15%. Run GEPA and GEPA+Merge with at most 5 merges; that needs an *invocation* cap (§3, item 1).
- **Extended results.** NPUEval on AMD XDNA2 and KernelBench on a V100 with a retrieval-augmented μ_f, run under a cache. The adversarial AIME setup with an inverted reward.
- **Statistics.** The paper reports single runs. A faithful check would repeat each cell over at least 3 seeds.
- **Cost estimate.** About $500 of GPT-4.1-mini (the paper's App. G.3 figure), plus GPU time for 12 GRPO runs of 24k rollouts. This is out of reach on this CPU-only machine with a $3 live cap.
- **Cheaper steps still open here.**
  - Run E9 (module-specific vs system-level feedback) and E11 (inverted reward) on RuleWorld.
  - Implement a true invocation cap and re-run E4.
  - Re-run the E1 AgentQA table with the fixed mock.
  - Run a larger live `claude -p` run on the Tier-2 constraint task (spec §9.2) to test absolute gains with a real reflection LM.
