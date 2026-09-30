# Claim-by-claim audit against the papers

Seven independent auditors listed every claim each paper (and the overview) makes. For each one they checked our code line by line against the paper text or reference code, and matched each result to evidence from `results/` and `validation/`. Where evidence was missing, they ran a cheap offline check (no live LLM calls). No code was changed by the audit.

Verdicts: **reproduced**, **partial**, **not reproduced**, **not testable here** (needs frontier models, GPUs or the original benchmarks), **contradicted**.

| Method | Claims | Reproduced | Partial | Not reproduced | Not testable here | Contradicted | Retry-2 live spend |
|---|---:|---:|---:|---:|---:|---:|---:|
| [RRSI](../methods/rrsi/claims-audit.md) | 116 | 63 (60) | 13 (15) | 1 (3) | 36 (37) | 3 (1) | $3.16 |
| [Dream-RSI](../methods/dream-rsi/claims-audit.md) | 83 | 43 (37) | 12 (17) | 1 | 24 (25) | 3 | $2.05 |
| [Autoresearch](../methods/autoresearch/claims-audit.md) | 65 | 51 (52) | 10 | 1 (0) | 2 | 1 | $3.62 |
| [SoL-Pi](../methods/metaharness-solpi/claims-audit-solpi.md) | 61 | 31 | 11 (12) | 1 (2) | 18 (16) | 0 | $0 |
| [GEPA](../methods/gepa/claims-audit.md) | 58 | 28 (29) | 11 | 2 (0) | 17 | 0 (1) | $3.52 |
| [EvoMap](../methods/evomap/claims-audit.md) | 54 | 28 (25) | 10 (11) | 1 (5) | 14 (12) | 1 | $0 |
| [Meta-Harness](../methods/metaharness-solpi/claims-audit-metaharness.md) | 38 | 13 (12) | 12 (13) | 0 (2) | 12 (10) | 1 | $3.85 |
| **Total** | **475** | **257** (246) | **79** (89) | **7** (13) | **123** (119) | **9** (8) | **$16.20** |

The counts are final after two rounds of work. Numbers in parentheses are the counts after the first fix round (28 Sep 2026), where they differ.

**Fix round (28 Sep).** Every mismatch between our code and the papers was fixed, documented, or classified as not testable here. Each method's file has a "Fix log", and each fix was checked by an independent verifier. Counts moved from 218/116/15/118/8 at audit time to 246/89/13/119/8.

**Retry round 2 (29-30 Sep).** Every claim still PARTIAL, NOT REPRODUCED or CONTRADICTED got a second attempt, 110 claims in total. Each one was diagnosed with a single root cause:
- (a) our code still deviated from the paper;
- (b) the experiment was a weak test;
- (c) the claim needs scale we don't have;
- (d) a genuine negative on a faithful implementation;
- (e) an error in the paper or overview itself.

The claim was then handled according to that cause:
- a code deviation was fixed, with a regression test;
- a weak test was replaced by a better experiment, run as preregistered: the hypothesis, metric, seeds and pass threshold were written into the claims file before the run, and every run is reported;
- other claims had their evidence strengthened.

Two independent skeptics then tried to refute every verdict change, one on statistics and honesty, one on fidelity to the paper and reference code. A resolve pass fixed each valid objection or rejected it with a reason. The reviews reversed or tempered several first-pass upgrades, for example RRSI L12/L4, Dream L2, GEPA L4, autoresearch O26 and Meta-Harness K4/L4. They also reverted one first-pass downgrade: Dream L5, which rested on a hard-coded mock. Each claims file has a "Retry round 2" section with the preregistration, a per-claim table (old verdict, root cause, action, evidence, new verdict) and the review outcomes.

## Short answer

**Mechanisms: largely yes.** The loops, formulas, defaults and data structures match the papers and reference code. Several were verified by differential tests or byte comparison:
- RRSI: 0 mismatches on the budget, stall, prune, novelty and aggregation functions; 18 of 6,000 keep decisions differ, all on exact float ties.
- GEPA: the meta-prompt is byte-identical, and 22,000 randomized render/parse checks gave 0 mismatches.
- SoL-Pi: all the reference test vectors pass.
- EvoMap: a hash fuzz against the GEP SDK found no mismatches outside two edge cases.

**Headline numbers: not reproducible here.** 123 claims are table numbers or need a frontier-agent sample size (with a power analysis), on frontier models, GPUs or specific benchmarks (Terminal-Bench, Harvey, KernelBench, AIME, CritPt, EdgeBench, the H100 nanochat runs). They are marked "not testable here" rather than implied.

**Qualitative claims: mostly directional.** Most qualitative findings reproduce in direction on CPU analogues. Some do not reproduce, and a few are contradicted.

## Contradicted claims (9)

- **Dream-RSI:**
  - Q15: Fig. 3b says Dream is "consistently superior" across both models. The paper's own source data show Pro is worse at rounds 3-4 at equal rounds.
  - Q22: "matches or surpasses strong baselines" in math. The paper's own Table 1 has autocorrelation worse (1.456375 vs SimpleTES 1.453675; lower is better).
  - C7: the overview calls the code "open". As of 29 Sep 2026, the official repository's README still says "Full codebase: Being prepared".
- **RRSI:**
  - M9: the paper says the edit budget "ends at one". The released schedule always ends at 2, while the reference's own prompt and unit test say 1, so the source is internally inconsistent.
  - M37: the z=2 noise band should let an unchanged harness clear the floor about 97.5% of the time. The reference's own `calibrate()` gives 90.8-91.1% at its default k=2; it is within tolerance at k=4.
  - C7: a third-party hypothesis, not a paper claim, that the annealed budget explains the gain. Budget-only gives +2.9 on unseen tasks, against +17.3 for the full method.
- **Autoresearch:** T3, the overview's claim that locking `prepare.py` means the agent "can't change how it is graded". Upstream's lock is only an instruction, and `evaluate_bpb` sums losses from the agent-owned `model.forward`. In faithful mode, 7 exploit classes that leave `prepare.py` untouched post fake gains (5 seeds). Our hardened mode delivers what the overview claims.
- **EvoMap:** M8a, "looks locally, then asks the hub". Evolver asks the hub first (v1), and v2's reuse before solving is opt-in and pools hub and local candidates. The mismatch is in the study's description, not our code.
- **Meta-Harness:** L4, code-space search "regularises towards coherent algorithms". On AgentQA, 4 of 12 live candidates keyed on the generator's question templates, 3 reached the frontier, and one was selected as best.

No longer contradicted: GEPA L6, "merge occurs sparsely". The retry showed merges are sparse for 1- and 4-module programs, and the excess is confined to 2-module cells. The earlier pooled threshold had been set after a seed-0 run, so the claim is now PARTIAL.

## Not reproduced (7)

- **RRSI L4:** removing the proposal regularizers should lower unseen-task scores by 1.7. The primary run gives +0.4 [-1.4, +2.2]. The effect appears only with a mock proposer that collapses its prompts, and the preregistered prune-as-proposal grouping reverses it.
- **Dream-RSI L6:** guidance "over-constrains the search". The live LLM test is negative (p = 0.60): guided code was more diverse, and the test had 0.70-0.95 power at a 15% reduction.
- **Autoresearch P28:** "if you run out of ideas, think harder". At a replayed stuck point, live Haiku made only single-knob tweaks, with no combinations and no structural changes.
- **SoL-Pi L2:** keeping only changes that "survive everywhere" should make them transfer. On 20 fresh seeds, held-out transfer is +0.008 [-0.008, +0.023], and backend A is negative.
- **GEPA:**
  - L2: GEPA should beat GRPO at GRPO's 4x budget. Our analogue ties (+0.011 [-0.004, +0.027]), and the interval excludes the paper's +0.059.
  - L13: merge degradation should come from budget allocation and timing. Neither the hard cap nor late timing removes it.
- **EvoMap B7:** "blast radius is the dominant GDI lever". Under the study's formula and a one-field ablation it is the smallest lever. The ranking depends on the per-field metadata ranges, which only the crawl has.

No longer "not reproduced" after retry round 2:
- **Resolved:** Dream-RSI Q13 (discovered solvers vs scikit-learn/glmnet) is now PARTIAL. The earlier loss came from asymmetric timing; with compute-only timing on both sides, the paper's solver is faster on 4 of 4 datasets. RRSI L12 (a weaker policy gains more) is now PARTIAL. EvoMap B9 (GDI collapse) and M19 (marketplace), and Meta-Harness L2 (summaries), are now PARTIAL.
- **Reclassified as not testable here, each with a power analysis:** SoL-Pi Q3 and Q13, EvoMap V4 and V5, and Meta-Harness Q4 and Q5.

## Mismatches found by this audit: all resolved (fix round)

The audit found about 90 new mismatches across the seven methods. All were fixed (with a regression test that fails on the pre-fix code) or dispositioned, and the affected experiments were re-run. Highlights:

1. **Dream-RSI:** policies now get a fresh namespace for every replay episode, and a static check rejects state that could outlive an episode, so the memo cheater gains nothing. The Listing 1 and 2 prompts are now verbatim. Per-round budgets are capped at Fixed's. The developer's cost is counted. Autocorrelation is implemented.
2. **RRSI:**
   - The precheck denylist matches the reference (task ids and patterns; the answer list is opt-in).
   - The proposer no longer sees the noise band or cost weights.
   - Ties are compared exactly as in the reference, missing trials use the task's weight, the smoke check covers 2-4 tasks, and a resumed run's budget counts spend from before the interruption.
3. **GEPA:** the hard merge cap now limits invocations; the truncation check works on cached replies; the monitor's wall time no longer counts toward the time budget; E9 was added.
4. **Meta-Harness:** context cost counts every call; the rewrite proposer now sees traces; the read accounting is honest; the stale live JSONs were regenerated; a coding-agent proposer ran live.
5. **SoL-Pi:** 21 port differences against the NVlabs TypeScript are fixed, and the capability floor rejects the lenient turn cap.
6. **EvoMap:** faithful mode now asks the hub first and injects the hub gene alongside the local one. Hashing matches the GEP SDK and rounding matches JavaScript. Safe mode keeps a new gene only after a 3-seed A/B test. All results were regenerated. The farm-rate sensitivity of the Behind-EvoMap numbers is now reported.
7. **Autoresearch:** the default keep rule applies upstream's simplicity criterion and the soft VRAM constraint. The agent sees the full results.tsv. NEVER STOP no longer exits early, and a crashing baseline goes to the fix path. The summary block matches upstream. Run memory is the run's own peak.

What remains is scale (frontier models, GPUs, original benchmarks) and a few results that did not reproduce at our scale, all listed per method.

## Code changes in retry round 2

Every default stays faithful. New behaviour is either a faithful port or an opt-in option, and each change has a regression test.
- **SoL-Pi:** a real deviation was fixed. The compaction summary is now billed as a standalone uncached request with Pi 0.85.1's verbatim prompts (`rsi/solpi/pi_compaction.py`); we had billed it as a cached continuation. The LogTriage environment no longer crashes at 16 or more subtasks.
- **EvoMap:**
  - the Behind-EvoMap GDI formula with last-activity freshness is now the default ranker;
  - Evolver's layer-3 hub signal layer is ported;
  - a `GitWorkspace` port of `gitOps.js` was added;
  - bounties, swarm splits, referrals and dormancy were added, with task ranking ported from v2 `taskReceiver.js` (identical on 400 random cases).
- **Meta-Harness:**
  - the LLM summarizer now reads every trace unit fairly; it had been reading only the head of the first unit;
  - new structured-optimizer baselines (OpenEvolve, TTT-Discover, GEPA policies) run at equal budget.
- **RRSI:** opt-in ports of the reference's agentic protocols: the JSON-action proposer and the agentic analyst, with differential tests against the reference.
- **Dream-RSI:**
  - the paper's C++/Eigen Lasso setting (`lasso_cpp.py`) with its 17 search instances and held-out protocol;
  - `developer_base` and `pareto_attainment` options for the two readings the paper leaves open.
- **GEPA:** `merge_start_frac`, an opt-in merge schedule used to test the timing claim. The default is bit-identical.
- **Autoresearch:** no library change was needed. The experiment harness is resumable, and the X3c/X5b follow-ups were preregistered.

## What would close the gaps

- Run the papers' own settings with frontier models and the real benchmarks: Opus/Gemini proposers, Terminal-Bench, Harvey, KernelBench on GPU, H100 nanochat, EdgeBench.
- Run larger live experiments than the roughly $26 of Haiku spent so far ($10 in the showcase and fix rounds, $16 in retry round 2). The power analyses in the claims files give the sample sizes needed, e.g. about 100-400 frontier-agent task pairs for SoL-Pi Q3, and 566-8,856 live trials per arm for EvoMap V4/V5.
- Find task sets with headroom for Haiku, since the katas and AgentQA are near ceiling. Several representation and transfer claims are untestable until then.
