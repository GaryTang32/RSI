# EvoMap: claim-by-claim audit of `rsi.evomap`

This page checks whether our implementation matches every claim made about EvoMap. The claims come from four sources:

- the Evolver engine;
- the GEP protocol;
- the vendor paper "From Procedural Skills to Strategy Genes" (arXiv 2604.15097);
- the independent study "Behind EvoMap" (arXiv 2605.25815);

plus the user's overview page. It is an audit only: no code was changed.

**Sources used.**

- Spec: `docs/methods/evomap.md`.
- Overview: `scratchpad/doc.txt`, EvoMap section.
- Reference code: `scratchpad/src/EvoMap__evolver` (v1.94.0) and the deobfuscated modules in `scratchpad/deob/out/`.
- Papers: `scratchpad/papers/strategy-genes.txt` and `behind-evomap.txt`.
- Our code: `rsi/evomap/`, `rsi/domains/{katas,geneworld}/`.
- Our evidence: `docs/methods/evomap-impl.md`, `results/evomap/*.json`, and `validation/evomap/{AUDIT,RUNS}.md` with their run directories.

**Honesty note on the vendor and network numbers.** We could not access the full text of either paper; arxiv and the mirrors are egress-blocked. What we have:

- The Strategy Genes numbers (4,590 trials, 54.0 / 49.9 / 51.0, CritPt) come from the Evolver README, search snippets and one unverified third-party summary.
- The Behind-EvoMap numbers come from the verbatim abstract (98%, 84%, "small fraction") and from search snippets (the GDI weights, the 10% figure, 99% zero votes, 40.2 → 36.0).
- The scale figures (1.5M assets, 128K agents) describe the live network.

None of these can be re-measured on a CPU box without the live network, Gemini-class models and the papers' benchmarks. Every quantitative "reproduction" below is a **simulation of the mechanism** at toy scale, and its magnitudes depend on knobs we chose.

## Summary

54 claims about EvoMap itself (the main table). The SafeHub extension is listed separately in its own table.

| verdict | count | which |
|---|---|---|
| **REPRODUCED** | 24 | 13 mechanisms (M1–M5, M9, M10, M12, M13, M16, M17, M20, M21); 4 Behind-EvoMap findings (B2, B5, B8, B10); 7 qualitative claims (C1–C7) |
| **PARTIAL** | 12 | M6, M7, M8b, M11, M14, M15, M18; V3; B1, B3, B4, B6 |
| **NOT REPRODUCED** | 5 | M19 (marketplace, worker pool, bounties: not built); V4 and V5 (composition and failure-encoding ablations, offline and knob-driven); B7 (blast radius is the dominant GDI lever); B9 (GDI "collapses" onto the intrinsic part) |
| **NOT TESTABLE HERE** | 12 | V1, V2, V6–V11 (vendor benchmarks and live-network scale); C8–C11 (caveats about sources) |
| **CONTRADICTED** | 1 | M8a: the overview says "look locally first, then ask the hub". The Evolver source does the reverse: it queries the hub first on every non-idle cycle |

**Bottom line.**

- **What we reproduce.** The EvoMap *mechanisms* are ported closely: asset format, hashing, the local loop, solidify, validation quirks, eligibility and the naive hub. The Behind-EvoMap *failure mechanisms* also reproduce in simulation: publication-paid credits, self-reported validation, and a ranking built from claimed metadata.
- **What we do not reproduce.**
  - None of the vendor performance numbers were tested for real: offline X1–X3 restate the simulator's knobs, and live Haiku scores at ceiling on our katas.
  - The network percentages depend on how fast we let farmers publish (see N2).
  - Two GDI findings do not reproduce in our reconstruction: that blast radius dominates, and that the score collapses onto the intrinsic part.

## Claim table

Legend:

- Type: **M** = mechanism, **Q** = quantitative, **L** = qualitative, **C** = caveat.
- "faithful" means our `mode="faithful"` port of Evolver v1.94.0; "safe" means our extension.
- Line numbers refer to the current tree.

### Mechanisms (Evolver engine and GEP)

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| M1 | Genes are compact strategy cards: signals_match, summary, strategy, AVOID, constraints, validation | overview; SG snippets; GEP spec §2.1 | M | `Gene` `rsi/evomap/assets.py:130`; paper card template in `render_gene` `prompts.py:27`. Carries the engine drift fields (`avoid`, `provenance`, epigenetic objects). Faithful. | `tests/test_evomap_core.py` (21 core tests pass); the X1 gene card averages 143 tokens (`results/evomap/x1_representation.json`) | REPRODUCED |
| M2 | Capsules are worked case traces of a gene applied to a task | overview; spec §2.2 | M | `Capsule` `assets.py:175`; built in `Solidifier.solidify` `solidify.py:326-346`: build and validate trace steps, diff text ≤ 8000 characters, confidence = composite, streak recomputed from events (`solidify.py:396`). Matches the deobfuscated `solidify.js` (§3.6) | AUDIT.md: every capsule and asset_id verifies in the traced runs | REPRODUCED |
| M3 | Events record every outcome; append-only audit trail | overview; spec §2.3 | M | `EvolutionEvent` `assets.py:207`, written at `solidify.py:347-361`; parent chain plus our extra hash chain `meta.parent_asset_id` (`store.py`). `LocalStore.audit` `store.py:243` | X13 (`results/evomap/x13_audit.json`): 10 seeds, round trip identical, lineage exact, 100% tamper detection for field, delete, swap, byte and re-stamped-event edits; **0%** for a re-stamped gene edit | REPRODUCED (a re-stamped gene or capsule needs an external anchor) |
| M4 | `asset_id` = sha256 of the canonical JSON without `asset_id` | GEP spec §5, App. A; `contentHash.js` | M | `rsi/evomap/hashing.py:54-89`. **New check (this audit):** a node differential fuzz against `@evomap/gep-sdk@1.14.0` gave 0 mismatches in 3,000 random nested objects (unicode, control characters, U+2028, floats down to 5e-324, ±2^53 ints). Two edge cases differ (N5) | fuzz scripts `scratchpad/claims_evomap/{gen*.py,cmp*.mjs}`; `tests/test_evomap_core.py` | REPRODUCED (two edge-case divergences, N5) |
| M5 | GEP schema 1.14.0: strict schema and engine drift | GEP spec §5 | M | The four sdk 1.14.0 schemas are copied unchanged into `rsi/evomap/schemas/`; `JsonSchemaValidator` `schema.py` has a strict and a lenient mode | tests | REPRODUCED |
| M6 | Signals come from logs (regex, then weighted keywords, then a hub LLM), with ≥ 3-of-8 dedup and control signals | spec §3.2, §4.6 | M | Layers 1–2 and all dedup and plateau rules: `signals.py:166-414`. Layer 3 (hub LLM) is **not implemented**. On task streams we add a `TaskSignalExtractor` (`signals.py:227`), a documented deviation. | AUDIT.md: "dedup never fired", D1 "plateau / drift / bans / dedup never triggered in ≤ 12 cycles"; X4 exercises bans in GeneWorld | PARTIAL |
| M7 | Selector: pattern hits + 0.6·tag + 0.4·cosine, history adjustment, memory advice (Laplace, half-lives, bans), ×1.5 preferred, drift I = 1/√Ne | spec §3.3, §4.2–4.5 | M | `selector.py:58-241`, `memory.py:52-297`. `require_match=True` is a documented deviation. AUDIT recomputed the scores 0.2665, 0.7425 and 3.568 by hand. | X4 (`x4_local_loop.json`): oracle accuracy full vs none +0.36 [+0.25, +0.47]. In-loop ban latency: spec rule 5.0, where the arithmetic expects 4 (`ban_latency_spec_eq_4: false`, explained by task-varying keys). "Drift finds the better gene": it is discovered (0.875 → 0.96) but not exploited (0.08 → 0.10) | PARTIAL |
| M8a | "For a new task the agent first searches its local store, then asks the hub; only if nothing fits does it work out a new approach" | overview | M | **Evolver does the opposite order.** The stage order is `extractSignals → hubCoordinate → enrich (hub search) → select` (spec §3.1, deob `src/evolve.js`, `pipeline/enrich.js`), and the executor prompt says "PROBLEM RESOLUTION PRIORITY (EVOMAP-FIRST): 1. FIRST: Search evomap Hub". The hub is asked on every non-idle cycle, whatever the local store holds. | code reading (spec §3.1, §6.1) | CONTRADICTED (the overview's description, not our code) |
| M8b | Our hub lookup and reuse | spec §3.1, §4.16 | M | Faithful mode: `hub_when="always"` (`config.py:30`). Local selection runs first, then the hub (`agent.py:443-478`), and a hit **replaces** the local gene (documented approximation). In Evolver the hit sits next to the gene as a "STRONG REFERENCE" block. Safe mode implements the overview's order (`hub_when="no_local"`). The hub pre-filters on ≥ 1 pattern hit (`hub.py:297`), whereas the overview describes matching "by what the asset does". | X0, X6; AUDIT "§3.1 order … faithful (safe variant)" | PARTIAL |
| M9 | Solidify keep rule: constraints ∧ validation ∧ no protocol violation; composite score; rollback | spec §3.6, §4.10 | M | `solidify.py:283-403`; composite at `solidify.py:143-177`, with weights identical to deob `solidify.js:571-580`. Two small divergences from the deobfuscated code (N3, N4). | AUDIT: all 60 gate recomputations match; composite 0.95 = Evolver `round2(0.955)`; X16: vacuous or skipped validation still reaches ≥ 0.78 in 100% of cycles | REPRODUCED |
| M10 | Local validation command: node-only allowlist, blocked eval flags, missing scripts **skipped**, empty list ok, 2 retries | spec §4.9 | M | Ported to Python: `validation.py:60-104` (policy), `:257-305` (runner). Faithful: skip and empty ok; safe: an empty list fails. | X9 (`x9_vacuity.json`): Evolver's runner rejects only 25% of seeded vacuous validations (GeneWorld 24/96, katas 50/200) | REPRODUCED |
| M11 | Solve and distil: every 5th successful solidify or `shouldDistill` → heuristic synthesizer → LLM fallback; failure distiller | overview; spec §3.7, §4.14 | M | `distill.py:57-268`: triggers, argmax(2·count + average), validate_synth. The heuristic fallback validation differs (N6). Not implemented: auto-publishing a distilled gene as a Skill, and skill2gep. | X17: the failure distiller lowers the faithful holdout (GeneWorld −0.15 [−0.19, −0.11]). The success distiller **never fired** in any traced run (AUDIT D1, "needs ≥ 10 good capsules") | PARTIAL |
| M12 | Publish eligibility: score ≥ 0.7, streak ≥ 2, ≤ 5 files / 200 lines, score ≥ 0.78, not `reused` | spec §4.13 | M | `solidify.py:30-33, 396-401`; `agent.py:638-641` | AUDIT recomputed eligibility; A4 (a failed cycle does not break the streak) is faithful | REPRODUCED |
| M13 | Hub: verify asset_id and substance → candidate → validators run the proposer's commands in an **empty** directory → promote | spec §3.9; Hub guide | M | `NaiveEvoMapHub.publish` `hub.py:325-366`. Promotion on the self-reported `overall_ok` (default) or on the empty-directory validator. Hub-side distribution needs non-zero blast radius (`hub.py:355`). In-process only: no A2A envelope, heartbeats or `/a2a/*` API. | X9: the empty-directory validator accepts only `--version` and rejects every real check (false-reject rate 1.0) | REPRODUCED (as an in-process replica) |
| M14 | GDI ranking = 0.35 intrinsic + 0.30 usage + 0.20 social + 0.15 freshness | overview; BE snippets | M | `GDIRanker` `hub.py:192-241`. The weights match the snippets; the six intrinsic metrics, their normalizations, usage, social (0.067 for an included event) and freshness are **our reconstruction** (impl §6) | X8, X7 | PARTIAL (internals unknown) |
| M15 | Credit economy: 500 starter, +100 per promotion, +5 per fetch, validators paid; bounties, referrals, swarm splits | overview; Hub guide | M | `hub.py:35-38, 128-171, 353, 363, 370`. The validator pool is paid a flat +20 (source: +10–30). **Bounties, referrals, swarm splits and dormancy are not implemented** | X7: 97% of credits come from promotion | PARTIAL |
| M16 | Client reuse score r = confidence·min(max(streak, 1), 5)·rep/100 + 0.3·sim; thresholds 0.72 / 0.55 | spec §4.16 | M | `hub.py:174-183`; `agent.py:266-274`; reputation fixed at the default 50 | tests | REPRODUCED |
| M17 | A2A ingest: reject on asset_id mismatch, confidence ×0.6, `external_candidates`, promotion only with `--validated` | spec §3.8, §4.15 | M | `store.py:35, 198-241` | AUDIT "§3.8 ingest faithful" | REPRODUCED |
| M18 | Installs locally, runs in any git repo; the hub is optional | overview | M | The workspace is a `{path: text}` map, not git (documented deviation); `run(..., hub=None)` works on any `rsi.core.Domain` | X0: three domains plus a bare `FunctionDomain` in `tests/test_evomap_review.py` | PARTIAL (no git rollback or stash) |
| M19 | Marketplace, worker pool, bounties; "a layer above MCP and skill files" | overview | M | not implemented (impl §6) | – | NOT REPRODUCED (not built) |
| M20 | The memory graph reads the executor's self-printed EvolutionEvent, so self-assessment steers learning | spec §4.11 (deob `memoryGraph.js`) | M | `OutcomeInferrer` faithful vs safe, `memory.py:244-297`; one-cycle-late timing `agent.py:604-610` | X15: with an over-reporting executor the preferred gene's true effect is 0.30 vs 1.26 in safe mode (paired +0.97 [+0.76, +1.20]) | REPRODUCED (simulation) |
| M21 | Assets published by agents on different models are inherited by others | overview | M | `PopulationSimulator`, heterogeneous `GeneWorldModel` abilities | X6: 60% [54, 67] of the hub genes weak agents took were written by strong agents | REPRODUCED (simulation) |

### Vendor results (Strategy Genes paper, Evolver README, overview)

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| V1 | 4,590 controlled trials over 45 scientific code-solving scenarios | SG abstract (Evolver README) | Q | – | none; the benchmark is not available | NOT TESTABLE HERE (unreleased scenarios, Gemini models, full text blocked) |
| V2 | Gene 54.0% vs Skill 49.9% vs no guidance 51.0% | SG snippets; mpx summary | Q | Arms exist: `render_gene` / `render_skill` / `SkillInjector` (`prompts.py:27,71`; `inject.py:69`) | Offline X1 (mock): none 0.50, Skill 0.82, Gene 0.91. This **restates `KataSimSolver`'s knobs**, and the mock even has Skill ≫ none, the opposite sign to the paper's −1.1 (N11). The live X1 was never run; Haiku is at ceiling on the katas (holdout 1.0 without genes: `results/evomap/live_smoke.json`, RUNS.md) | NOT TESTABLE HERE (needs live LLM runs on a benchmark with headroom) |
| V3 | Gene about 230 tokens vs Skill about 2,500 tokens | SG snippets | Q | card and skill renderers | Gene 143 tokens vs Skill 1,252 (about 9×, versus the paper's about 11×) | PARTIAL (form only) |
| V4 | Composition: a single gene is best (54.0); two complementary genes are worst (44.9), worse than two conflicting (53.2) | SG snippets; mpx | Q | – | X1/X3 offline: single 0.91, complementary 0.90, conflicting 0.56. "Complementary worse than conflicting" is **not** reproduced; the model is additive by construction | NOT REPRODUCED (knob-driven) |
| V5 | Failure history works best as compact AVOID warnings (54.4) rather than appended logs | SG snippets; mpx | Q | `render_gene(failure_log=...)` | X2 offline: AVOID-only 0.58 < appended log 0.90 | NOT REPRODUCED (knob-driven) |
| V6 | CritPt 9.1% → 18.57% (Gemini 3 Pro, "Version A") | SG; README | Q | – | none | NOT TESTABLE HERE (70 CritPt tasks and Gemini models) |
| V7 | CritPt 17.7% → 27.14% (Gemini 3.1 Pro, "Version B") | SG; README; critpt repo | Q | – | none | NOT TESTABLE HERE (same; the comparison also changes the model, the gene pool and routing) |
| V8 | OpenClaw 9.1% → 18.57% over five versions; tokens rise then fall | README; overview | Q | Token accounting exists (`res.usage`, `CycleResult.tokens`); X14 is not implemented | GeneWorld tokens are programmed (`world.py:266`: 1800·(1 − 0.4·helpful) + injected tokens), so they cannot rise and fall on their own | NOT TESTABLE HERE (needs a versioned live agent; the vendor blog is not accessible) |
| V9 | 1.5M+ shared assets by April 2026 | overview; BE abstract | Q | – | our hub holds about 1,600 assets per run (X7) | NOT TESTABLE HERE (live network) |
| V10 | 128K (daily active) agents | overview; BE abstract | Q | – | 40 simulated agents | NOT TESTABLE HERE. Note the overview says "daily active" and the abstract says "128K agents" |
| V11 | About 9,000 GitHub stars by July 2026 | overview | Q | – | not re-checked | NOT TESTABLE HERE |

### Behind-EvoMap findings

X7 base setup: 40 agents × 30 epochs × 20 seeds with 15% farmers publishing 8 assets per epoch (`results/evomap/x7_behind_evomap.json`).

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| B1 | 98% of shared assets are never reused | BE abstract | Q | `ReuseMetrics.never_reused_*` `metrics.py:56-80`; reuse = a non-author adoption or review | X7: 97.5% [97.3, 97.6]; without farmers 60.2%. **New sensitivity run:** farm rate 8 / 2 / 1 gives 97.9% / 91.8% / 84.5% (N2) | PARTIAL (the mechanism reproduces; the number is set by the farm-rate knob) |
| B2 | Rewards are tied to publication, not adoption | BE abstract; overview | Q/L | +100 per promotion vs +5 per fetch (`hub.py:35-36`), taken from the vendor guide | X7: 97.1% of earned credits come from promotion | REPRODUCED |
| B3 | About 10% of agents hold most credits, earned by repeated publishing rather than reuse or bounties | BE snippets; overview | Q | `CreditLedger.top_share` `hub.py:159` | X7: the top 10% hold 61% of earned credits (Gini 0.83), and all of them are farmers. At farm rate 2 the share is 49% (not "most"). Bounties are not modelled, so "rather than bounties" is untested | PARTIAL |
| B4 | 84%+ of approved assets passed validation with vacuous tests (e.g. `console.log`) | BE abstract | Q | Promotion on the self-reported report (`hub.py:359`). Our `vacuous_share_promoted` is a *discriminative* re-check (`metrics.py:41-52`), a broader notion than "the test command is vacuous" (N9) | X7: 92.3%; without farmers 10.7%; with real-validation honest writers 91.9%; farm rate 2 / 1 → 75% / 61% | PARTIAL |
| B5 | Validation evidence is self-reported and not independently verified; the only thing the validator can run is trivial | BE abstract; evolver `sandboxExecutor.js` | M | naive hub promotion rule and the empty-directory validator (`hub.py:350-360`) | X9: the empty-directory validator accepts only `--version`; the local runner rejects 25% of vacuous kinds | REPRODUCED |
| B6 | 35% of the ranking comes from an "intrinsic" component made of self-reported metadata, which agents can inflate | BE snippets; overview | Q/M | `GDIRanker.intrinsic`: five of six metrics are claimed (`hub.py:208-219`) | X8: inflating every claimed field moves an honest asset up 74 percentile points [72, 76] (GDI +9.2). **New decomposition:** intrinsic is 66–68% of the mean GDI *level* but only 2–6% of its *variance* across promoted assets (N1) | PARTIAL (inflatable: yes; "35% weight" is taken as input) |
| B7 | Blast radius is the dominant lever: degrading only it drops the optimal score 40.2 → 36.0 | BE snippets | Q | reconstructed blast score (`hub.py:203-206`) | X8: the largest single lever is **streak** (+41 pp), then blast (+27 pp). Degrading blast gives 55.6 → 50.0; the relative drop (10.0%) is close to the paper's 10.4%, but the ranking of levers is not | NOT REPRODUCED |
| B8 | 99% of assets get zero votes from other agents | BE snippets | Q | naive consumers review each asset once (`agent.py:614-618`) | **new:** 97–98% of promoted assets have zero reviews at farm rate 8 (83–92% at rates 1–2) | REPRODUCED (simulation) |
| B9 | The four-dimensional GDI "collapses into a one-dimensional metric dominated by the Intrinsic component" | BE snippets | Q | – | **new:** the intrinsic part carries 2–6% of GDI variance; freshness carries 21–56% and usage 13–26% (`scratchpad/claims_evomap/gdi_decomp.json`); X8 also recorded `intrinsic_var_share` = 0.049 | NOT REPRODUCED (in our reconstruction the rank is driven by freshness and usage) |
| B10 | Networks need verifiable execution and trustworthy evaluation, not self-reporting | BE abstract; overview | L | the SafeHub extension (see below) | X9, X10, X11 | REPRODUCED (simulation; see E1–E8) |

### Qualitative claims and caveats (overview)

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| C1 | Agents grade their own work; quality signals are easy to game | overview "Watch out" | L | faithful keep rule and self-report paths | X16: Spearman(composite, task) = 0.010 under vacuous validation, and failed tasks still clear the 0.78 publish bar; X15; X8 | REPRODUCED |
| C2 | Publishing is rewarded over usefulness, so the library fills with unused assets | overview | L | naive credits | X7 (never reused 97.5%, duplicate floods) | REPRODUCED (magnitude set by the knob) |
| C3 | Inheriting another agent's strategy means trusting code of unknown quality; treat it as untrusted until tested | overview | L | faithful direct apply vs quarantine | X11: naive hub plus direct apply → consumer solve rate 0.175 vs 0.721 isolated, with 92% of consumer cycles running a poisoned gene; quarantine brings it to 0.720 | REPRODUCED (simulation) |
| C4 | A lesson learned once can be inherited by many agents, even on different models | overview | L | – | X6: solve rate +0.017 [+0.011, +0.025]; weak models +0.044, strong models −0.009 | REPRODUCED (small, simulated; strong models lose slightly) |
| C5 | An audit trail is built into every change | overview | L | M3 | X13 | REPRODUCED |
| C6 | The guard against overfitting is weak; no held-out set; no explicit cost rule | overview "Side by side" | L | faithful keep rule is a process score (spec §8.2) | X16; code reading | REPRODUCED |
| C7 | Reuse avoids re-solving (the only cost control) | overview | L | – | X6: from-scratch discoveries 356 → 189; tokens per solve −28% (the token saving is partly programmed, V8) | REPRODUCED (simulation) |
| C8 | The vendor figures come from EvoMap's own team, are single runs, and have not been replicated | overview | C | – | still true: we did not replicate them (V1–V8) | NOT TESTABLE HERE |
| C9 | The independent study measured the network, not benchmark performance | overview | C | – | – | NOT TESTABLE HERE (a statement about the source) |
| C10 | Name clash: EvoMap's "AutoResearch" is unrelated to Karpathy's autoresearch | overview | C | – | – | NOT TESTABLE HERE (documentation only) |
| C11 | Source discrepancies: "daily active" vs "agents"; Behind-EvoMap dated 25 vs 26 May | spec §8.5 | C | – | – | NOT TESTABLE HERE |

### Our SafeHub extension (not EvoMap claims; not counted above)

| # | claim (ours, spec §9.2) | code | evidence | verdict |
|---|---|---|---|---|
| E1 | Adoption ranking and credits raise reuse among promoted assets | `safehub.py:174+` | X10: 0.86 vs 0.025 (+0.84 [+0.81, +0.87]) | REPRODUCED (simulation) |
| E2 | Verified execution drives vacuous promotions to about 0 | discriminative check plus `UpliftLCB` `safehub.py:85` | X10: 0 vs 0.92; X9: precision and recall 1.00 on seeded kinds | REPRODUCED (simulation; seeded vacuity kinds only) |
| E3 | Rank validity ρ > 0 and better than GDI | adoption rank | X10: 0.12 vs 0.068, paired +0.05 [−0.05, +0.16] (not significant). After the stage-B fixes the ρ CI lower bound is −0.005 (impl §10), but `results/evomap/x10_safehub.json` still says `rank_validity_positive: true` (N7) | PARTIAL |
| E4 | Exploration slots cut time to first reuse | Thompson slots | X10: paired 0.0 [0, 0] | NOT REPRODUCED |
| E5 | Farmers earn about 0 | stake plus adoption-only credits | X10: farmer credit share 0 vs 0.905 | REPRODUCED |
| E6 | Consumer quarantine blocks poison; SafeHub alone blocks poison | `quarantine.py:48` | X11: 0 of 360 poisoned bundles promoted; quarantine removes the harm | REPRODUCED |
| E7 | SafeHub rank is invariant to claimed metadata | – | X8: Δrank 0 for every field | REPRODUCED |
| E8 | Evolver's failure distiller pollutes a faithful library (spec-derived) | `distill.py:170` | X17: −0.15 GeneWorld, −0.11 katas | REPRODUCED (depends on our 1 h per cycle clock) |

A confound applies to E1–E5: the X10 arms change both the hub and the agents' regime (safe vs faithful agents).

## Newly found mismatches

These were not reported by `evomap-impl.md` §9–10, `RUNS.md` or `AUDIT.md`.

- **N1. The GDI "collapse" does not reproduce.** A decomposition of the X7 base hub (2 seeds each at farm rates 8, 2 and 1; `scratchpad/claims_evomap/gdi_decomp.py`) gives these shares of promoted assets' GDI:

  | component | share of mean GDI level | share of variance across assets |
  |---|---|---|
  | intrinsic | 66–68% | 2–6% |
  | freshness | 12–13% | 21–56% |
  | usage | 17% | 13–26% |

  In our replica, ranks are driven by freshness and usage, because every farmer claims the same optimal metadata, so the intrinsic score barely varies. The study's claim (snippets) is that the intrinsic part dominates the ranking. X8 already logged `intrinsic_var_share` = 0.049 in its JSON, but the gap was never discussed.
- **N2. The headline Behind-EvoMap percentages are a function of one knob.** Varying the farmer publish rate (8 / 2 / 1 assets per farmer per epoch; everything else as in X7 base):

  | metric | farm rate 8 | farm rate 2 | farm rate 1 |
  |---|---|---|---|
  | never reused | 97.9% | 91.8% | 84.5% |
  | vacuous among promoted | 92% | 75% | 61% |
  | top-10% credit share | 61% | 49% | 53% |

  The chosen rate of 8 is what lands the numbers near 98% and >84%. At rate 2, "about 10% of agents hold *most* credits" no longer holds. The impl doc reports only the farmers-on / farmers-off arms.
- **N3. The estimate-drift penalty is dead code in Evolver, but live in ours and in the spec.**
  - Evolver's composite reads `blastRadiusEstimate.files_changed` (deob `src__gep__solidify.js:540`).
  - Dispatch writes the estimate as `{files, lines}` (`src__evolve__pipeline__dispatch.js:119-121`), so the ×0.5 / ×0.7 penalty never applies.
  - Our `composite_score` uses `estimate["files"]` (`rsi/evomap/solidify.py:165-167`), and spec §4.10 documents the penalty as active.
  - There is no practical effect today: our estimate is ≥ 2 files and actual diffs are about 1 file, so the ratio never exceeds 2.
- **N4. Rounding differs from Evolver.**
  - Evolver: `Math.round(x*100)/100`. Ours: Python `round(s, 2)` (`solidify.py:176`).
  - Over all 403,200 combinations of component values, they disagree by 0.01 on 3.6% (`scratchpad/claims_evomap/round_check.py`).
  - 358 of those combinations cross the 0.7 broadcast bar and 52 cross the 0.85 self-PR bar; none cross the 0.78 publish bar.
  - All the crossings involve unusual states (no gene, or a failed canary).
- **N5. Two `asset_id` edge cases.**
  - With a random corpus that includes them, 740 of 3,000 hashes mismatch the SDK. With them excluded, 0 of 3,000 mismatch.
  - (a) Object keys that mix astral characters (e.g. emoji) with BMP characters ≥ U+E000: JavaScript sorts keys by UTF-16 code units, Python by code points (`hashing.py:67`).
  - (b) Integers ≥ 1e21: JavaScript prints `1e+21` (`hashing.py:29-30`). Integers above 2^53 are not representable in JavaScript at all.
  - Neither case occurs in realistic GEP assets.
- **N6. The heuristic distiller's fallback validation differs.**
  - Evolver's `autoDistill` falls back to `["node --test"]` (deob `skillDistiller.js:673`). The allowlist filters it out, so the gene ships with an **empty** list: it passes with validation component 0.5, composite about 0.815.
  - Our faithful fallback is `python --version` (`distill.py:59`): it passes with component 1.0, composite about 0.94.
  - Our docstring (`distill.py:11`) says Evolver uses "node --version". That is true only for the LLM distiller prompt.
  - Both are vacuous and both clear 0.78, but they sit in different vacuity classes (an empty list vs an info-only command).
- **N7. Committed results predate the stage-B fixes.**
  - `results/evomap/*.json` were written 06:26–06:41 on 2026-09-25.
  - `agent.py`, `safehub.py`, `solidify.py`, `quarantine.py` and `prompts.py` were changed 13:00–13:12 (fixes B1–B4).
  - The X10 JSON still reports `rank_validity_positive: true`, while the post-fix re-run (impl §10) has CI lower bound −0.005.
  - The impl doc's §4 table still shows the pre-B numbers. They were re-run only into a scratch directory, which is not in the repo.
- **N8. The overview's "look locally, then ask the hub" is not what Evolver does** (M8a). Our faithful mode matches neither: it selects locally first, then lets a hub hit *replace* the local gene. Evolver queries the hub first and gives the LLM both the hub hit and the local gene.
- **N9. "Vacuous" is not measured the way the study measures it.** Our `vacuous_share_promoted` marks an asset vacuous when its validation does not *discriminate* before from after (`metrics.py:41-52`). The study counts vacuous *test commands* such as `console.log`. Real-but-weak checks count as vacuous for us, so our 92% and the study's 84% are not the same measurement.
- **N10. The naive hub only surfaces assets with ≥ 1 literal pattern hit** (`hub.py:297-299`), then ranks by GDI. Evolver's hub also offers semantic search ("matches by what the asset does"). A farmer's broad signals are what earn it exposure in our replica.
- **N11. The offline X1 is described as "direction as in the paper", but it is not.** The mock gives Skill 0.82 ≫ none 0.50, while the paper has Skill *below* none (49.9 vs 51.0). Only Gene > Skill matches. The paper's central claim, that documentation-style guidance does not help, is contradicted by our simulator's knobs, not supported by them.

## Top gaps

1. **No real test of the representation claims (V2–V5).** Every Gene / Skill / none comparison is either offline and knob-driven, or live on katas where Haiku is already at ceiling (holdout 1.0 without genes). A live X1–X3 run on tasks with headroom is the missing experiment.
2. **The vendor benchmarks were not attempted** (V1, V6–V8): the 45 scenarios, CritPt and the OpenClaw token trajectory. X14 ("tokens rise then fall"), X5 and X12 are not implemented.
3. **The Behind-EvoMap magnitudes are set by knobs** (N2), and the GDI internals are reconstructed. Blast-radius dominance (B7) and intrinsic collapse (B9) do not reproduce.
4. **Several engine mechanisms never ran in a traced run:** distillation, drift, plateau override, dedup and bans (AUDIT D1). Also missing: the hub LLM signal layer, the git workspace, and marketplace, bounties and worker pool (M6, M11, M18, M19).
5. **Stale evidence** (N7): `results/evomap/*.json` and the impl §4 table predate the stage-B fixes, and the X10 rank-validity verdict in the JSON is no longer supported.

## What would be needed to fully reproduce

- **Strategy Genes (V1–V5).**
  - The paper's 45 scenarios (or a stand-in with measured headroom for the chosen model, e.g. harder katas or SWE-style tasks where the no-guidance pass rate is 30–70%).
  - The three arms (none / Skill about 2,500 tokens / Gene about 230 tokens), derived from the same per-scenario experience.
  - About 100 trials per cell to resolve a 3-point gap: at p ≈ 0.5, the SE of a difference over 4,590 trials is about 1.5 pp.
  - The perturbation, failure-encoding and composition ablations.
  - Needs live LLM calls, so it is out of scope for a CPU-only box.
- **CritPt (V6, V7).** The 70-task CritPt set with its judge, Gemini 3 / 3.1 Pro (or the same base model for both arms), and an evolving gene pool with routing. The comparison should hold the base model fixed, which the vendor's did not.
- **Tokens rise then fall (V8).** Five or more versioned libraries of a live agent, with tokens per solved task measured on a fixed task set. This is X14.
- **Behind-EvoMap (B1–B9).**
  - Either crawl data from the live hub: asset metadata, call counts, votes and credit ledgers, to compute the same statistics directly.
  - Or calibrate the simulator's farmer share, farm rate and validator behaviour to the study's published distributions, which requires the full paper.
  - The real GDI formula: the six intrinsic metrics and their normalizations.
- **Engine coverage.**
  - Run faithful mode for ≥ 100 cycles on a live or error-logging domain, so that distillation, dedup, bans, drift and plateau actually fire and can be audited.
  - Add the git workspace, hub signal layer 3 and the Skill auto-publish.
  - Fix N3–N6 so faithful mode matches the deobfuscated engine byte for byte.
- **Evidence hygiene.** Regenerate `results/evomap/*.json` on the current code (about 45 min CPU in total, per impl §4) and update the impl §4 table.

## Checks run for this audit (offline, no LLM calls)

- `pytest tests/test_evomap_*.py`: 51 passed (about 20 s).
- `asset_id` differential fuzz against `@evomap/gep-sdk@1.14.0` in node:
  - scripts `scratchpad/claims_evomap/gen.py`, `gen2.py`, `gen3.py`, `gen4.py`, `cmp.mjs`, `cmp2.mjs`;
  - the full random corpus has 740 mismatches in 3,000, all from the two edge cases;
  - with astral-plus-high-BMP keys excluded (`gen4`) or ints kept within ±2^53 (`gen3`), 0 of 3,000.
- Composite rounding sweep: `scratchpad/claims_evomap/round_check.py`.
- GDI decomposition and farm-rate sensitivity: `scratchpad/claims_evomap/gdi_decomp.py` → `gdi_decomp.json`. It used the X7 base population, 40 agents × 30 epochs, seeds 0–1, farm rates 8 / 2 / 1, and took 59 s.
