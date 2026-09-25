# Validation: every step, from a pure seed

Each method was instrumented with the per-iteration tracer (`rsi/trace.py`) and a write-only shadow monitor, then run from its untouched seed artifact with fresh LLM caches, offline and live (Claude Haiku via `claude -p`). An independent auditor then re-derived every step from the raw trial scores in `trace.jsonl`, checked each claimed change against the actual diff, verified that sealed splits never reached a decision, compared mechanism and behaviour with the paper, fixed the bugs it found and re-ran.

Per method: `validation/<method>/RUNS.md` (what each run did, round by round), `validation/<method>/<run>/TRACE.md` (rendered trace: prompts, replies, diffs, gate arithmetic, decisions, shadow held-out scores), `validation/<method>/AUDIT.md` (step verdicts, paper alignment, inconsistency register).

## Step audit

| Method | Steps audited | Correct | Questionable | Wrong | Unverifiable | Live spend |
|---|---:|---:|---:|---:|---:|---:|
| RRSI | 640 | 612 | 26 | 1 | 1 | $2.80 |
| GEPA | 125 | 89 | 26 | 0 | 10 | $1.38 |
| autoresearch | 622 | 593 | 5 | 4 | 20 | $0.69 |
| Dream-RSI | 335 | 315 | 4 | 15 | 1 | $2.91 |
| EvoMap | 330 | 293 | 25 | 9 | 3 | $0.61 |
| Meta-Harness + SoL-Pi | 318 | 293 | 18 | 6 | 1 | $2.16 |
| **Total** | 2370 | 2195 | 104 | 35 | 36 | $10.55 |

"Wrong" steps are decisions that were correct under the implemented rule but came from a defect (a mock bug, a design gap, a reviewer false-reject) - no gate arithmetic was miscomputed in any run. Each is listed with its fix in the method's AUDIT.md.

## Inconsistency register (paper vs implementation vs observed behaviour)

| Method | faithful | documented deviation | unverifiable | inconsistent, fixed | inconsistent, open |
|---|---:|---:|---:|---:|---:|
| RRSI | 7 | 4 | 2 | 3 | 4 |
| GEPA | 12 | 7 | 3 | 2 | 2 |
| autoresearch | 8 | 8 | 5 | 4 | 1 |
| Dream-RSI | 13 | 9 | 3 | 6 | 3 |
| EvoMap | 10 | 2 | 3 | 8 | 3 |
| Meta-Harness + SoL-Pi | 7 | 6 | 5 | 9 | 2 |

### Open items

Items the auditors left open, and what happened to them after the audit:

- **RRSI** - Precheck misses literal answers under 4 characters: open - see AUDIT.md.
- **RRSI** - Resumed run budget ignores pre-kill spend; TRACE.md mixes the superseded segment: open - see AUDIT.md.
- **RRSI** - AgentQA split overlap (evolve-numeric-017 == holdout-numeric-012): fixed: `make_suite` now de-duplicates by default (`dedupe=False` reproduces old suites).
- **RRSI** - Live proposer/critic behaviour: dependent edits declared separately, undeclared persona text accepted: open - see AUDIT.md.
- **GEPA** - Monitor wall time counts toward the wall-clock stoppers (Budget.max_wall_s, Timeout): open - see AUDIT.md.
- **GEPA** - The E1 table in gepa-impl.md still carries numbers from before the AgentQA mock fix: open - see AUDIT.md.
- **autoresearch** - Monitor CPU contention with workers>1 under wall-clock budgets: open - see AUDIT.md.
- **Dream-RSI** - Adaptive template labels one manifest 'live best still improving': open - see AUDIT.md.
- **Dream-RSI** - Root cause in rsi.core.parse_file_blocks: fixed in core after the audit (lone/trailing fences and `===` terminators are stripped).
- **Dream-RSI** - Replay ranking vs online value (frugality bias, single world at t=1; spec §8.2/§8.4): open - see AUDIT.md.
- **EvoMap** - A1 new gene kept on a single retry sample: open - see AUDIT.md.
- **EvoMap** - A7 hub.metrics n_promoted counts verified assets (n_promoted_strict = 0): open - see AUDIT.md.
- **EvoMap** - X10 rank_validity_positive robustness: open - see AUDIT.md.
- **Meta-Harness + SoL-Pi** - Exact-tie order of Pareto/_best by name (release: rglob file order); per_unit_best breaks ties the opposite way: open - see AUDIT.md.
- **Meta-Harness + SoL-Pi** - Forked smoke killed on timeout still loses its usage: open - see AUDIT.md.

## Behaviour vs paper

### RRSI

- Annealed budget: aligns. AgentQA bundle sizes are 1/4, 1/1, 3/3 in r0-r2, then single edits. HW goes from 1-4 to 1-2. b_t never reaches 1 (code ceil, documented).
- Failure memory: the mocks skip rejected ideas, and redraw them only when the idea pool is exhausted. Live r1 did not redraw r0B. Shared bundle credit can bury a good idea: the Python tool in the re-run's r2 (spec 8.4).
- Stall-triggered reserved slot: fires as specified in all offline runs. In HW it produced an admissible structural tool (r4B). In AgentQA the slot cannot be satisfied: the mock has no 'tool' idea and its only 'memory' idea is the leak. That is mock-limited.
- Critic before evaluation, with bounded repair: aligns. Blocked leaks: 7 literal + 1 obfuscated (HW), 10 leaky AgentQA drafts (5 repaired, 5 dropped), and 1 real Haiku leak copied from traces (live). None was evaluated. The live precheck caught only 1 of the 3 copied answers; the repair removed all three.
- Floor on S*, not S_t: observed in AgentQA r7. S_t=0.65 after a within-band keep, and the floor stays 0.603 from S*=0.75.
- Cost rule above the band vs shaped rule within it: both branches observed. The within-band rule let the AgentQA loop prune the costly persona smuggled in by the r0 bundle (r6A: dS -0.10 measured, equal quality at k=10, 27% of the tokens). The final AgentQA harness is LIGHTER than H0 (626 vs 736 tokens), matching 'pruning removes growth that stopped paying'. HW E[C] is x1.61, close to the paper's RRSI x1.55 vs H0. The live run is x3.6 (2 rounds, k=1).
- Transfer: AgentQA k=10 truth is +0.26 evolve / +0.27 holdout / +0.18 OOD. HW truth is +0.055 evolve / +0.007 OOD; the residual evolve-only gain comes from the narrow r6B, which RRSI cannot see. The claim 'practises lower but transfers better than unregularized' is unverifiable in these runs (no unregularized arm; see E1).
- Winner's curse / ratcheting S* (spec 8.1) is visible in every run and blocks later real gains when k<=2.

**Auditor verdict.** I re-derived every step of all three runs independently from the raw trial data, and the loop does what the spec says. Every Algorithm 2 verdict, delta, b_t, sigma_t, T_t/U_t, B_t, nu, argmax and S* update matches (0 mismatches over 34 evaluated candidates). No sealed id or question reached a proposer, analyst or critic input, and no rejected candidate was ever evaluated. The monitor is write-only. Two real bugs corrupted the original offline_agentqa run, both in my packages: phantom L_t credit from the AgentQA mock, and the heuristic analyst ranking solved tasks as the top failure mode. Both are fixed with regression tests, and the run was redone from scratch. HarnessWorld and the live run are unaffected; the live run was confirmed by a $0 cache replay. The questionable steps are paper-faithful weaknesses rather than implementation errors: the winner's curse, shared bundle credit, transfer-blind keeps (HW r4A, r6B), and within-band decisions driven by k=1 noise in the live run. One domain-level problem remains open: an evolve/holdout duplicate question in AgentQA. Full details are in /home/user/RSI/validation/rrsi/AUDIT.md.

### GEPA

- Matches: the frontier keeps diverse winners. In the merge run 28 of 48 parent draws were not the incumbent, spread over 28 distinct parents; the final pool has 39 members, 31 of them on the frontier. In ruleworld_offline, it 4 sampled c3 with p=0.26. This agrees with the paper's 'balanced search tree'.
- Matches: reflection uses the textual feedback. In RuleWorld all 73 added rule lines are grounded in the 'Rule:' feedback, although this holds by construction of the mock. Live haiku quoted the failures and expected values it was shown.
- Matches Obs. 1: most of the budget goes to validation. RuleWorld spent 85% and 78% (82% counting merge subsamples); AgentQA 33% (13 skip_perfect iterations); the live run 47%.
- Matches weaknesses 3 and 4 (niche facts, overfitting, winner's curse):
- The returned RuleWorld artifacts carry 6 and 4 ticket facts.
- Haiku copied 5 training answers into its rewrites.
- The merge run returned c33 (D_pareto 0.898, test 0.847), which ranks 7th of 40 by truth.
- Matches weakness 8 (a coarse, noisy gate): 6 false accepts and 8 false rejects over 56 gated steps. Because the gate is in-sample, 4 of the 5 reflective false accepts had a positive expected minibatch gain but no test gain.
- Differs: merge is not sparse. There were 11 attempts and 7 accepts in 59 iterations, because the reference code's soft cap lets merges_due accumulate. The paper says 'at most 5'.
- Not comparable: the size of the gain. RuleWorld goes from 0 to 0.847 exact test at 1.5k rollouts, but in a simulator. The live haiku run stopped on its $1.20 guard after 51 rollouts and returned the seed. The cause is CPU and budget scale, not a bug.

**Auditor verdict.** GEPA's implementation matches the reference step for step. An independent replay of all 125 steps in the 6 run directories reproduced every mechanical detail. It covered:
- every parent draw and minibatch (114 of 114), using the reference sampler and one shared RNG;
- every merge triplet and subsample, and every schedule counter;
- every offline reflection prompt, byte-identical to the reference renderer (67 of 67);
- every child, identical to the reference parser output (76 of 76);
- every gate, frontier, incumbent, round-robin pointer and budget identity.

No step is wrong. The 26 questionable steps come from two causes:
- **The noisy gate (21 steps).** GEPA's minibatch gate uses 3 examples and one draw each, and scores the child on the same examples it was written from. That accounts for 14 false accepts and rejects in RuleWorld and 3 each in the two AgentQA offline runs. Most false accepts added ticket facts that helped only those examples.
- **An outage bug (5 steps), now fixed.** A sustained backend outage charged 3 rollouts per iteration and never stopped the run. A stopper now ends the run after 3 such iterations, with a regression test. The offline runs re-run from scratch reproduce identically.

The 10 unverifiable steps are the live haiku steps, which have no ground truth. The rewrites there copy training answers and drift toward the minibatch topic. Both accepted children scored 0.25 on D_pareto against the seed's 0.50.

Documented deviations from the paper:
- The reference code's soft merge cap gave 7 accepted merges where the paper says at most 5.
- Iteration uses sorted order instead of Python-set order. This changed 2 of 114 draws; the sampling distribution is unchanged.
- The live cache shares identical upstream calls across candidates.

Two issues remain open, both low severity:
- the monitor's wall time counts toward wall-clock stoppers (a core change);
- the E1 table in the implementation doc predates the AgentQA mock fix.

The full report is in /home/user/RSI/validation/gepa/AUDIT.md.

### autoresearch

- Baseline first, fixed wall-clock budget without the warm-up steps, one editable file, locked prepare.py, strict keep else reset, trivial-fix crash rule, kill > kill_after, 5-column results.tsv: all observed in the traces and match the spec mechanically. Scale differs (8 s CPU vs 300 s H100; kill 2x+5 s).
- Steady keeps with a readable log: yes (8/25, 2/6, 8/10, 6/10). Keep rates are far above upstream's 15/89 because the tiny CPU model is far from its optimum and single knobs move 0.03-0.13 bpb (upstream: 0.0003-0.007).
- Luck locked in: reproduced. A pure seed change (42->397, +0.0077) was kept, like upstream's teaser seed 42->137. Most keeps fall inside the noise band (0.035 / 0.026). Recorded best is optimistic against the 3-seed fresh re-eval by +0.0073 (offline), +0.0176 (live) and +0.0044 (live_b); the direction matches the spec, the size is small.
- Budget bias ('more steps > more params'): both live Haiku runs independently shrank BATCH_SIZE (32->6; 32->8) and raised LR, and discarded capacity increases in round 1. The agents' hypotheses cite the fixed budget. Matches spec 8.3 and #32's biggest win (batch halving).
- Validation reuse: the within-model test_iid-val gap grew by about 0.03 in the offline and live chains, coinciding with capacity increases. Too few decisions to attribute it; the spec itself reports no measurable effect at about 70 decisions.
- Grader isolation (hardened): every exploit that reached the loop was stopped as a rejection or violation crash, and no faked number reached the keep rule. This is the spec's hardened design, not upstream's instruction-only lock (faithful mode was not exercised in these runs).
- Agent behaviour: live proposals are grounded in results.tsv. Hypotheses cite earlier gains correctly, except one mis-citation in live_b r10, and falsified ideas are not re-proposed. Both live agents' first proposal changed two knobs despite 'ONE idea'.

**Auditor verdict.** The autoresearch loop does its core job correctly in all 4 from-scratch runs (3 from Stage A, plus a Stage-B live re-run). All 44 keep decisions give the same answer when recomputed from the raw eval values. Every results.tsv row, branch advance or reset, and crash or fix path is correct. None of the 22 byte-exact reconstructed live prompts contains a sealed-split number. Usage matches exactly across the trace, the meter and the cache ($1.323 total live spend for the stage). The traced behaviour matches the spec: baseline first, fixed budget, strict keep else reset, crash rules, 5-column TSV, and hardened isolation stopped every exploit. The runs also reproduce the known weaknesses: a lucky seed keep, keeps inside the noise band, an optimistic recorded best, and a bias toward more steps per budget. Two real bugs were found and fixed with regression tests. First, the shadow monitor used up the loop's wall and dollar budgets, so it was not write-only when those budgets were set (1 vs 18 experiments); no recorded run was affected. Second, the sanitizer's removals never reached the trace. Four Stage-A write-up claims were wrong or overstated and are now corrected: r6 file identity, the fix-1 fence, and three hidden-regression claims that sit inside the audit's re-training noise. The remaining unverifiable items come from the method itself (single-run keeps inside the noise band) or from sample size (validation-reuse attribution). One open issue remains: monitor CPU contention with parallel workers.

### Dream-RSI

- Replay costs 0 agent calls: reproduced in all 4 runs. The agent-call meter equals the tree sizes after every dreaming phase, and replay costs 2.8-4.2 ms CPU per episode.
- Selection that includes the incumbent never lowers the replay score: reproduced in 9/9 phases.
- Dreaming changes the policy and saves calls. Offline this reproduces: sumdiff_offline used 43 calls vs 60 for fixed pi_1; agentqa used 33 vs 36. Live, it reproduced only after the parser fix. In sumdiff_live_b a haiku-written policy was deployed (V 0.933 vs 0.925), and cycle 2 widened to 4x2 for 8 calls. In stage-A sumdiff_live every LLM policy tied pi_1 at V 0.895, making the run effectively Recursive Fixed Exploration. There, world 1's ceiling was in the last round and half the cells were repairable-looking parser failures.
- 'Better quality at lower cost' is not reproduced at this scale. The only offline switch checkable online (sumdiff_offline t=1) traded 8.2 vs 15 calls for significantly less gain: -0.0028 [-0.0046, -0.0012] over 40 searches. This matches the spec's §8.2 frugality bias and §8.4 (one world at t=1). The paper never measures replay against online value. The runs are far smaller than the paper's (9-15 calls per round, T<=4 vs 110-640 calls, 5-10 rounds), so this is not evidence against the paper.
- Pacing (Fig. 6): agentqa went 9 -> 6 -> 8 -> 10 calls, widening on plateau. That matches the story qualitatively, but the task was already saturated, so the calls were wasted. sumdiff_offline went 15 -> 12 -> 7 -> 9, with widening driven by within-cycle early gains rather than a plateau. sumdiff_live_b went 9 -> 8. These are single short runs, so only suggestive.
- Sibling-context coupling (spec §8.3) was observed live: agents copy siblings' recipes, so recorded outcomes depend on what was revealed. The replay-bias magnitude is unverifiable.

**Auditor verdict.** Dream-RSI's mechanics are correct and match the paper. I re-derived all 335 steps of the 4 runs independently: 315 correct, 4 questionable, 15 wrong, 1 unverifiable. A second replay implementation written from paper §3 reproduces all 36 version evaluations. Eq.1, argmax-with-incumbent, batch legality, plan_grid seeing only earlier manifests, prefix-only replay, sealed-split isolation, cost accounting ($2.906 and $1.278, both matching the cache files) and the exact proposer prompts (65/65 rebuilt and matched to cache hashes) all check out. All 15 wrong steps come from one harness bug in the stage-A live run: rsi.core's reply parser left a trailing '==='/fence in files, which turned 12 good agent programs and 3 developer drafts into SyntaxErrors and misled later reasoning. Stage A fixed it, and stage B closed a residual leading-fence case and quoted the offending line in syntax errors. The post-fix live re-run was clean (17/17 attempts, 0 repair rounds, Gamma 1.0361, a haiku policy deployed by replay). At paper-behaviour level, replay costs 0 calls and never lowers the replay score, but 'better quality at lower cost' is not reproduced at this scale. The one checkable offline policy switch saved calls but lost online gain (-0.0028 [-0.0046, -0.0012], 40 searches), the spec's §8.2/§8.4 frugality bias. Open items: the core parser fix, one-manifest 'improving' label in the adaptive template (label-only), and the unverified online value of live dreaming decisions.

### EvoMap

- Aligned: every cycle follows the section 3.1 order: signals, then memory advice, then local selection, then the hub (only when nothing local fits, a documented safe-mode change), then solve and gene writing, then solidify, then publish. Genes take the paper's compact form (keywords / summary / strategy / AVOID), with one gene injected per task.
- Aligned: sharing works as the overview claims. agent1 had no gene writer, yet inherited agent0's date and backoff genes through quarantine; its holdout rose 0.25 -> 0.50 and each adoption was reported to the hub with dS proof (reuse measured, not self-reported).
- Aligned (safe-mode design, spec 9.2): the Behind-EvoMap failure modes are blocked in the trace. An empty validation is rejected (agent1 c2), a non-discriminative validation is rejected (live_katas c2), metadata is recomputed, and hub admission uses verified uplift with an LCB and delta (rejected U_LCB 0.25 < 0.498; verified 0.5 >= 0.458). The naive self-reported hub was not run in this validation (X7/X8 cover it).
- Aligned with Evolver quirks: the composite score is process-only (0.95 even for rejected cycles), the capsule streak is not broken by failed events, and epigenetic marks change on every solidify.
- Not aligned before the fixes: the writer prompt lacked the evolution-history block, so a live writer re-proposed a failed approach (B3). The spot-check punished honest ceiling reports (B1). An adopted gene was re-published (B4). Keep-rule failures were treated as hard constraint breaches (B2).
- Not reproducible here: the paper's numbers (4,590 trials, CritPt about 2x, Behind-EvoMap 98% never reused). Offline gains restate the kata simulator's knobs, and live Haiku is at ceiling on the katas holdout, so gene transfer to unseen tasks cannot be tested live at this scale. Distillation and plateau/drift dynamics were not triggered in 8-12 cycles.

**Auditor verdict.** Every gate decision in all 7 audited traces recomputes exactly from the traced raw trial scores (60 of 60). No step contained an arithmetic error. The 9 wrong steps came from rule or design defects: W1-W3, already fixed in stage A, and B1-B4, fixed in this stage. B1: the SafeHub spot-check slashed honest ceiling reports. B2: safe-mode keep-rule failures were classified as hard constraint violations. B3: the writer prompt lacked section 6.1's history block, and a live writer re-proposed a failed approach. B4: adopted hub genes were re-published under the adopter's name. The offline run was redone from scratch, a fresh live Haiku run was made ($0.333; the fixed hub now rejects the zero-uplift gene live), and the full X10/X11/X6 experiments were re-run on the fixed code; their verdicts hold, except that X10's marginal rank-validity claim is not robust. The mechanisms match the spec and Evolver: memory advice, selector, drift, mutation presets, composite score, epigenetics, streak, asset_id and event chain, credits. Three things remain open. First, new genes are kept on a single retry sample (A1). Second, katas are too small for statistically strong hub or quarantine verdicts (A2). Third, the paper's claim that genes transfer to unseen tasks cannot be tested with a live Haiku that is already at ceiling on this domain.

### Meta-Harness + SoL-Pi

- Meta-Harness matches the paper here. The proposer diagnosed failures from raw traces: both live runs quote concrete trace lines, and the candidates target those diagnoses. Every valid candidate was evaluated on the search split only. The frontier trades accuracy against context: the offline best has 3.1x less context than fewshot_all at +13 points on search and +11 on test, in line with the paper's '+7.7 points with 4x fewer tokens'. The test split was evaluated once, after search.
- Meta-Harness does not match the paper's claim that code-space search regularizes toward coherent algorithms (§3, §5). Live r2 produced a template-regex solver at cost 0 that sits on the Pareto front, and a docstring containing a search instance with its answer. The main loop has no guard against this (spec A8.2).
- Meta-Harness selection on the search set did not track generalization. Offline, the last two incumbent changes gained 1 and 3 search examples while sealed ood fell from 0.654 to 0.558. In live r2, _best gained on search and holdout but 0 on ood. This is consistent with spec A8.3/A8.4 (small transfer, no noise band).
- Meta-Harness improvement rate: 6 of 12 offline candidates, and 2 of 6 per live run, became incumbents, against the reported <5 in 100 useful edits. This comes from weak seeds and search sets of 10-144 examples, not from the method.
- Meta-Harness live outcome variance: two runs with identical configs ended at ood 1.0 and 0.25 (N=3, trials=1, n=6/4 sealed tasks). A single live run is anecdotal.
- Meta-Harness 'full uncompressed history': the live runs used a budgeted renderer that showed only 20% of the traces by iteration 3. The paper's coding-agent proposer was never run live.
- SoL-Pi matches the paper on tokens at equal quality. Composed screen tokens -49% at S 1.0; held-out tokens -69% at 0.988 (within the 2% tolerance); ood tokens -66% at 1.0. The blog reports -44.7% to -49% tokens at ~94% of Pi's score.
- SoL-Pi matches the paper on tricks: every trick and do-less idea stayed out of the final stack. T3, T7 and P14 were stopped by the capability floor; T11 and P20@24 passed the training gate and were stopped by the firewall only for 'no efficiency gain'. The firewall is one-way and never fed back.
- SoL-Pi does not match the paper on cost: -8.9% against ~-33% reported. The efficiency-exists rule admitted a cost-increasing ObservationPack variant (sweep=False froze the first passing, most aggressive variant), and EPR and ObservationPack interact under the MockAgent. AF+EPR alone would save 43.9%.
- SoL-Pi OCC (a released mechanism) was rejected by the firewall because held-out sessions are only 10-16 requests long. This is the expected economics at CPU scale, not a bug.
- SoL-Pi live: before stage B, lineages died on reviewer opinions, partly false. After fixes 15 and 16, r4 produced the first live haiku-written survivor: an EPR-like condenser of failing logs with a /.solpi/ recall path. Screen tokens -66% and cost -46%; holdout tokens -38% and ood tokens -45%, at S 1.0. The agent backend was the MockAgent, so this is unverifiable for real agents.

**Auditor verdict.** No gate, keep, frontier or firewall decision was miscomputed in any of the 8 runs. I checked 318 steps independently of the stage-A audit; 293 are correct, and no sealed-split number flowed back into a decision. Of the 6 wrong steps, two are a Meta-Harness loop bug (dead-file candidates in live run 1, already fixed by stage-A fix 12). The other four are false rejections by the haiku reviewer; stage A counted three and missed r2 L2.1. The main paper inconsistency was structural: a SoL-Pi review rejection did not go back to implementation. Fix 16 corrects this and the r4 live run exercised it: haiku produced the first live survivor, an EPR-like log condenser, but with the offline MockAgent as backend. Fix 17 closes the unmetered-spend gap in the Meta-Harness live runs. Open behaviour issues, all consistent with the papers' own caveats: search-set overfitting (template regex solvers, an answer quoted in a docstring, ood falling while search rises), a composed SoL-Pi stack that costs more than EPR alone, and cost-increasing variants admitted by the any-metric efficiency rule. Still unverifiable: real-agent SoL-Pi behaviour, the AgentProposer's history reading, and the survival rate at scale.

## Core fixes made after the audit

- `parse_file_blocks`: strips a lone trailing fence, `===` / `=== END ===` terminators and an unmatched opening fence (caused 12/36 SyntaxErrors in the live Dream-RSI run); balanced fences in markdown are kept.
- `ClaudeCLI`: strips commit-attribution lines that the headless child inherited from the host session (it broke a live `train.py`).
- `UsageMeter.total()`: replayed cache hits no longer count as billed tokens.
- `rsi.trace`: prompts/replies/diffs kept up to 60k chars; resumed runs flagged; `run_end` rendered last; baseline per-task scores and metric-only evals rendered; the shadow monitor records its own wall time and `Budget.credit()` lets loops exclude it.
- AgentQA suites de-duplicated by default.
