# Claim-by-claim audit against the papers

Seven independent auditors listed every claim each paper (and the overview) makes. For each one they checked our code line by line against the paper text or reference code, and matched each result to evidence from `results/` and `validation/`. Where evidence was missing, they ran a cheap offline check (no live LLM calls). No code was changed by the audit.

Verdicts: **reproduced**, **partial**, **not reproduced**, **not testable here** (needs frontier models, GPUs or the original benchmarks), **contradicted**.

| Method | Claims | Reproduced | Partial | Not reproduced | Not testable here | Contradicted |
|---|---:|---:|---:|---:|---:|---:|
| [RRSI](rrsi.md) | 116 | 60 (57) | 15 (18) | 3 | 37 | 1 |
| [Dream-RSI](dream-rsi.md) | 83 | 37 (29) | 17 (25) | 1 (2) | 25 (24) | 3 |
| [Autoresearch](autoresearch.md) | 65 | 52 (44) | 10 (18) | 0 | 2 | 1 |
| [SoL-Pi](solpi.md) | 61 | 31 (27) | 12 (15) | 2 (3) | 16 | 0 |
| [GEPA](gepa.md) | 58 | 29 (27) | 11 (13) | 0 | 17 | 1 |
| [EvoMap](evomap.md) | 54 | 25 (24) | 11 (12) | 5 | 12 | 1 |
| [Meta-Harness](metaharness.md) | 38 | 12 (10) | 13 (15) | 2 | 10 | 1 |
| **Total** | **475** | **246** (218) | **89** (116) | **13** (15) | **119** (118) | **8** |

Numbers in parentheses are the counts at audit time, before the fix round (28 Sep 2026). Every mismatch the audit found was then fixed, documented, or classified as not testable here. Each method's file has a "Fix log" listing the finding, the fix, the code location, the regression test and the re-run evidence, and each fix was checked by an independent verifier.

## Short answer

**Mechanisms: largely yes.** The loops, formulas, defaults and data structures match the papers and reference code. Several were verified by differential tests or byte comparison:
- RRSI: 0 mismatches on the budget, stall, prune, novelty and aggregation functions; 18 of 6,000 keep decisions differ, all on exact float ties.
- GEPA: the meta-prompt is byte-identical, and 22,000 randomized render/parse checks gave 0 mismatches.
- SoL-Pi: all the reference test vectors pass.
- EvoMap: a hash fuzz against the GEP SDK found no mismatches outside two edge cases.

**Headline numbers: not reproducible here.** 118 claims are table numbers on frontier models, GPUs or specific benchmarks (Terminal-Bench, Harvey, KernelBench, AIME, CritPt, EdgeBench, the H100 nanochat runs). They are marked "not testable here" rather than implied.

**Qualitative claims: mostly directional.** Most qualitative findings reproduce in direction on CPU analogues. Some do not reproduce, and a few are contradicted.

## Contradicted claims

- **Dream-RSI:**
  - Fig. 3b says Dream-RSI is "consistently superior at lower compute". At equal rounds it is never better here, and on synthetic worlds it is significantly worse.
  - The introduction says it "matches or surpasses" baselines in math. The paper's own Table 1 shows autocorrelation worse.
  - The overview calls the code "open". It is not yet released.
- **GEPA:** the paper says merge "occurs sparsely". Under the reference default cap, our runs attempt 25-69 merges.
- **Meta-Harness:** the paper says code-space search "regularises towards coherent algorithms". A live run put a template-specific regex solver on the frontier.
- **EvoMap:** the overview says the agent "looks locally, then asks the hub". Evolver actually searches the hub first.
- **Autoresearch:** the overview says the agent "can't change how it is graded". Upstream's lock is only an instruction, and our faithful mode shows 9 of 11 grader exploits posting fake gains. Hardened mode fixes this.
- **RRSI:** a third-party hypothesis (not a paper claim) that the edit budget explains the gain. Budget-only gives +2.7 on unseen tasks, against +16.2 for selection-only.

## Not reproduced (after the fix round)

- **RRSI:**
  - Removing the proposal guards does not raise the practised score.
  - A weaker search policy does not gain more.
  - The z=2 noise band clears the unchanged harness 91.6% of the time, not about 97.5%, at k=2.
- **Dream-RSI:**
  - Discovered solvers do not beat scikit-learn.
- **SoL-Pi:**
  - The ObservationPack sweep never reaches the 10% bill-saving gate.
  - The composed stack still loses capability overall (the floor now rejects the lenient turn cap; see the SoL-Pi Fix log).
  - One quality claim did not reproduce.
- **EvoMap:**
  - "GDI collapses onto the intrinsic part" (it is 2-6% of the variance between assets here).
  - "Blast radius is the dominant lever".
  - The composition and failure-encoding ablations.
  - Marketplace and bounties are not built.
- **Meta-Harness:**
  - "Matches the next-best method after 4 of 60 evaluations".
  - "Summaries don't help", which holds only by construction of our summarizer.

## Mismatches found by this audit: all resolved

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

## What would close the gaps

- Run the papers' own settings with frontier models and the real benchmarks: Opus/Gemini proposers, Terminal-Bench, Harvey, KernelBench on GPU, H100 nanochat, EdgeBench.
- Run larger live experiments than the current $10 of Haiku. Each method's file lists the specific runs needed.
- Fix the open mismatches above and re-run the affected experiments.
