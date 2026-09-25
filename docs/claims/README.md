# Claim-by-claim audit against the papers

Seven independent auditors listed every claim each paper (and the overview) makes. For each one they checked our code line by line against the paper text or reference code, and matched each result to evidence from `results/` and `validation/`. Where evidence was missing, they ran a cheap offline check (no live LLM calls). No code was changed by the audit.

Verdicts: **reproduced**, **partial**, **not reproduced**, **not testable here** (needs frontier models, GPUs or the original benchmarks), **contradicted**.

| Method | Claims | Reproduced | Partial | Not reproduced | Not testable here | Contradicted |
|---|---:|---:|---:|---:|---:|---:|
| [RRSI](rrsi.md) | 116 | 57 | 18 | 3 | 37 | 1 |
| [Dream-RSI](dream-rsi.md) | 83 | 29 | 25 | 2 | 24 | 3 |
| [Autoresearch](autoresearch.md) | 65 | 44 | 18 | 0 | 2 | 1 |
| [SoL-Pi](solpi.md) | 61 | 27 | 15 | 3 | 16 | 0 |
| [GEPA](gepa.md) | 58 | 27 | 13 | 0 | 17 | 1 |
| [EvoMap](evomap.md) | 54 | 24 | 12 | 5 | 12 | 1 |
| [Meta-Harness](metaharness.md) | 38 | 10 | 15 | 2 | 10 | 1 |
| **Total** | **475** | **218** | **116** | **15** | **118** | **8** |

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

## Not reproduced

- **RRSI:**
  - Removing the proposal guards does not raise the practised score.
  - A weaker search policy does not gain more.
  - The z=2 noise band clears the unchanged harness 91.6% of the time, not about 97.5%, at k=2.
- **Dream-RSI:**
  - Discovered solvers do not beat scikit-learn.
  - Autocorrelation was not implemented.
- **SoL-Pi:**
  - The ObservationPack sweep never reaches the 10% bill-saving gate.
  - The capability floor admits a lenient turn cap.
  - One quality claim did not reproduce.
- **EvoMap:**
  - "GDI collapses onto the intrinsic part" (it is 2-6% of the variance between assets here).
  - "Blast radius is the dominant lever".
  - The composition and failure-encoding ablations.
  - Marketplace and bounties are not built.
- **Meta-Harness:**
  - "Matches the next-best method after 4 of 60 evaluations".
  - "Summaries don't help", which holds only by construction of our summarizer.

## New mismatches found by this audit (open)

Each method file lists its own under "Newly found mismatches". The most important:

1. **Dream-RSI, real bug:** a policy can keep module-level state across replay episodes (one session is reused per sweep). This defeats no-peeking for the Pareto objective and the beta sweep. Eq.1 selection is unaffected.
2. **Dream-RSI:** the Listing-2 developer prompt is a condensed paraphrase that drops about 20 of the paper's rules. The Listing-1 prompt drops a safety line.
3. **GEPA:** the hard merge cap limits accepted merges, not invocations. Neither mode matches "invoked a maximum of 5 times".
4. **RRSI:**
   - The leakage denylist includes the practice set's literal answers. That is an oracle the reference lacks, and it flatters the critic.
   - The proposer is shown the noise band and cost weights, which the reference never shows.
   - Keep decisions tie-break differently at exact float ties.
5. **Meta-Harness:**
   - The context-cost objective can be gamed (only the last call is counted).
   - The live RewriteProposer sees none of the trace files on MemoClassify.
   - The stored live-smoke JSONs predate a later fix.
6. **SoL-Pi:** about 13 small port differences. Examples: the plan-snapshot shape, an off-by-one in `source_lines`, UTF-16 vs code-point limits, and the auto-compaction threshold `>=` vs `>`.
7. **EvoMap:**
   - The 98% / 84% / top-10% figures depend on one knob (the farmer publish rate).
   - "Vacuous" is measured differently from the study.
   - Some committed result JSONs predate later fixes.
8. **Autoresearch:**
   - The simplicity criterion and the soft VRAM constraint are not applied by the strict keep rule.
   - The agent sees only the last 40 `results.tsv` rows.
   - The preset `program.md` drops several upstream lines.

## What would close the gaps

- Run the papers' own settings with frontier models and the real benchmarks: Opus/Gemini proposers, Terminal-Bench, Harvey, KernelBench on GPU, H100 nanochat, EdgeBench.
- Run larger live experiments than the current $10 of Haiku. Each method's file lists the specific runs needed.
- Fix the open mismatches above and re-run the affected experiments.
