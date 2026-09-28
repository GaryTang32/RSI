# EvoMap: claim-by-claim audit of `rsi.evomap`

This page checks whether our implementation matches every claim made about EvoMap. The claims come from four sources:

- the Evolver engine;
- the GEP protocol;
- the vendor paper "From Procedural Skills to Strategy Genes" (arXiv 2604.15097);
- the independent study "Behind EvoMap" (arXiv 2605.25815);

plus the user's overview page. The audit itself changed no code. A fix pass on 2026-09-28 then fixed or dispositioned every open finding (N1–N11, AUDIT A1 / A7 / X10) and re-ran every `results/evomap` experiment on the fixed code; see "Fix log" at the end. Verdicts, evidence and counts below are the post-fix state; the pre-fix counts are kept in the summary.

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

| verdict | count (after fixes) | before fixes | which (after fixes) |
|---|---|---|---|
| **REPRODUCED** | 25 | 24 | 14 mechanisms (M1–M5, M8b, M9, M10, M12, M13, M16, M17, M20, M21); 4 Behind-EvoMap findings (B2, B5, B8, B10); 7 qualitative claims (C1–C7) |
| **PARTIAL** | 11 | 12 | M6, M7, M11, M14, M15, M18; V3; B1, B3, B4, B6 |
| **NOT REPRODUCED** | 5 | 5 | M19 (marketplace, worker pool, bounties: not built); V4 and V5 (composition and failure-encoding ablations, offline and knob-driven); B7 (blast radius is the dominant GDI lever); B9 (GDI "collapses" onto the intrinsic part) |
| **NOT TESTABLE HERE** | 12 | 12 | V1, V2, V6–V11 (vendor benchmarks and live-network scale); C8–C11 (caveats about sources) |
| **CONTRADICTED** | 1 | 1 | M8a: the overview says "look locally first, then ask the hub". The Evolver source does the reverse: it queries the hub first on every non-idle cycle. This is an error in the overview, not in our code; our faithful mode now follows Evolver's hub-first order (N8) |

SafeHub extension (not counted above): E3 moved from PARTIAL to NOT REPRODUCED after the re-run on the fixed code (rank validity 0.089 [−0.022, +0.204]).

**Bottom line.**

- **What we reproduce.** The EvoMap *mechanisms* are ported closely: asset format, hashing (now byte-identical to the GEP SDK, including the two former edge cases), the local loop, solidify (now with Evolver's rounding and its dead estimate-drift penalty), validation quirks, eligibility, hub-first reuse with the hub hit as a reference next to the local gene, and the naive hub with signal + semantic search. The Behind-EvoMap *failure mechanisms* also reproduce in simulation: publication-paid credits, self-reported validation, and a ranking built from claimed metadata.
- **What we do not reproduce.**
  - None of the vendor performance numbers were tested for real: offline X1–X3 restate the simulator's knobs, and live Haiku scores at ceiling on our katas.
  - The network percentages depend on how fast we let farmers publish (see N2; X7 now reports farm rates 8 / 2 / 1).
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
| M4 | `asset_id` = sha256 of the canonical JSON without `asset_id` | GEP spec §5, App. A; `contentHash.js` | M | `rsi/evomap/hashing.py:25-99`. A node differential fuzz against `@evomap/gep-sdk@1.14.0` (unicode, control characters, U+2028, floats down to 5e-324, big ints, astral and high-BMP keys). The two edge cases of N5 are fixed: keys sort by UTF-16 code units (`hashing.py:62`, `:85`), integers ≥ 2^53 go through a double as in `JSON.parse` (`hashing.py:29-40`) | after the fix: 0 mismatches in 18,000 random objects including both edge cases (the full `gen.py` corpus that gave 740/3,000 before, plus 3 × 5,000 more seeds); SDK vectors frozen in `tests/test_evomap_fixes.py::test_asset_id_matches_gep_sdk_on_edge_cases` | REPRODUCED |
| M5 | GEP schema 1.14.0: strict schema and engine drift | GEP spec §5 | M | The four sdk 1.14.0 schemas are copied unchanged into `rsi/evomap/schemas/`; `JsonSchemaValidator` `schema.py` has a strict and a lenient mode | tests | REPRODUCED |
| M6 | Signals come from logs (regex, then weighted keywords, then a hub LLM), with ≥ 3-of-8 dedup and control signals | spec §3.2, §4.6 | M | Layers 1–2 and all dedup and plateau rules: `signals.py:166-414`. Layer 3 (hub LLM) is **not implemented**. On task streams we add a `TaskSignalExtractor` (`signals.py:227`), a documented deviation. | AUDIT.md: "dedup never fired", D1 "plateau / drift / bans / dedup never triggered in ≤ 12 cycles"; X4 exercises bans in GeneWorld | PARTIAL |
| M7 | Selector: pattern hits + 0.6·tag + 0.4·cosine, history adjustment, memory advice (Laplace, half-lives, bans), ×1.5 preferred, drift I = 1/√Ne | spec §3.3, §4.2–4.5 | M | `selector.py:58-241`, `memory.py:52-297`. `require_match=True` is a documented deviation. AUDIT recomputed the scores 0.2665, 0.7425 and 3.568 by hand. | X4 (`x4_local_loop.json`, re-run): oracle accuracy full vs none +0.39 [+0.27, +0.51]. In-loop ban latency: spec rule 5.0, where the arithmetic expects 4 (`ban_latency_spec_eq_4: false`, explained by task-varying keys). "Drift finds the better gene": it is discovered (0.875 → 0.96) but not exploited (0.08 → 0.10) | PARTIAL |
| M8a | "For a new task the agent first searches its local store, then asks the hub; only if nothing fits does it work out a new approach" | overview | M | **Evolver does the opposite order.** The stage order is `extractSignals → hubCoordinate → enrich (hub search) → select` (spec §3.1, deob `src/evolve.js`, `pipeline/enrich.js`), and the executor prompt says "PROBLEM RESOLUTION PRIORITY (EVOMAP-FIRST): 1. FIRST: Search evomap Hub". The hub is asked on every non-idle cycle, whatever the local store holds. **Our faithful mode now follows Evolver** (`hub_when="first"`, N8); safe mode keeps the overview's order as an explicit option (`hub_when="no_local"`). | code reading (spec §3.1, §6.1); `tests/test_evomap_fixes.py::test_faithful_mode_searches_the_hub_before_local_selection` | CONTRADICTED (the overview's description, not our code; documented) |
| M8b | Our hub lookup and reuse | spec §3.1, §4.16 | M | **Fixed (N8, N10).** Faithful mode: `hub_when="first"` (`config.py:33`) searches the hub *before* local selection on every cycle and adds `hub_search_miss_with_problem` on a miss with problem signals (`agent.py:453-459`, as `enrich.js`). In `reference` mode (Evolver's default) the hit is injected **next to** the locally selected gene and solidify still scores the local gene; the hub gene is never stored (`agent.py:499-510`, `:541-567`), as `dispatch.js` keeps `selected_gene_id`. The naive hub merges a signal search with a semantic search (query built as `hubSearch.js` does; only semantic results carry a similarity, `hub.py:395-445`). The old behaviours stay as non-default options (`reuse_mode="replace"`, `hub_when="always"`, `search_mode="legacy"`). Safe mode keeps the overview's order (`hub_when="no_local"`). The hub's embedding model is not public: our semantic score is a token cosine | `tests/test_evomap_fixes.py::test_faithful_reference_mode_injects_the_hub_hit_next_to_the_local_gene`, `::test_faithful_mode_searches_the_hub_before_local_selection`, `::test_naive_hub_surfaces_assets_by_semantic_similarity`; X7 / X10 naive arms re-run with it | REPRODUCED (in-process; semantic scorer is a stand-in) |
| M9 | Solidify keep rule: constraints ∧ validation ∧ no protocol violation; composite score; rollback | spec §3.6, §4.10 | M | `solidify.py:306-427`; composite at `solidify.py:144-190`, with weights identical to deob `solidify.js:571-580`. The two divergences are fixed: the estimate-drift penalty reads Evolver's dead key `files_changed` (N3, `solidify.py:181`; opt-in `Config.estimate_drift_penalty`) and rounding is JavaScript `Math.round` (N4, `solidify.py:193-201`) | AUDIT gate recomputations match (the frozen recompute test now uses `js_round2`). *Correction:* the old claim "composite 0.95 = Evolver `round2(0.955)`" was wrong; Evolver gives 0.96. X16 (re-run): vacuous or skipped validation still reaches ≥ 0.78 in 100% of cycles | REPRODUCED |
| M10 | Local validation command: node-only allowlist, blocked eval flags, missing scripts **skipped**, empty list ok, 2 retries | spec §4.9 | M | Ported to Python: `validation.py:60-104` (policy), `:257-305` (runner). Faithful: skip and empty ok; safe: an empty list fails. | X9 (`x9_vacuity.json`, re-run, unchanged): Evolver's runner rejects only 25% of seeded vacuous validations (GeneWorld 24/96, katas 50/200) | REPRODUCED |
| M11 | Solve and distil: every 5th successful solidify or `shouldDistill` → heuristic synthesizer → LLM fallback; failure distiller | overview; spec §3.7, §4.14 | M | `distill.py:61-265`: triggers, argmax(2·count + average), validate_synth. The heuristic fallback validation now matches Evolver (N6 fixed: `["python --test"]`, the port of `node --test`, which the allowlist drops, leaving an empty list; `distill.py:64-77`). Not implemented: auto-publishing a distilled gene as a Skill, and skill2gep. | X17 (re-run): the failure distiller lowers the faithful holdout (GeneWorld −0.165 [−0.206, −0.122], katas −0.117 [−0.173, −0.060]). The success distiller **never fired** in any traced run (AUDIT D1, "needs ≥ 10 good capsules") | PARTIAL |
| M12 | Publish eligibility: score ≥ 0.7, streak ≥ 2, ≤ 5 files / 200 lines, score ≥ 0.78, not `reused` | spec §4.13 | M | `solidify.py:30-33, 396-401`; `agent.py:638-641` | AUDIT recomputed eligibility; A4 (a failed cycle does not break the streak) is faithful | REPRODUCED |
| M13 | Hub: verify asset_id and substance → candidate → validators run the proposer's commands in an **empty** directory → promote | spec §3.9; Hub guide | M | `NaiveEvoMapHub.publish` `hub.py:348-389`. Promotion on the self-reported `overall_ok` (default) or on the empty-directory validator. Hub-side distribution needs non-zero blast radius (`hub.py:355`). In-process only: no A2A envelope, heartbeats or `/a2a/*` API. | X9: the empty-directory validator accepts only `--version` and rejects every real check (false-reject rate 1.0) | REPRODUCED (as an in-process replica) |
| M14 | GDI ranking = 0.35 intrinsic + 0.30 usage + 0.20 social + 0.15 freshness | overview; BE snippets | M | `GDIRanker` `hub.py:192-241`. The weights match the snippets; the six intrinsic metrics, their normalizations, usage, social (0.067 for an included event) and freshness are **our reconstruction** (impl §6) | X8, X7 | PARTIAL (internals unknown) |
| M15 | Credit economy: 500 starter, +100 per promotion, +5 per fetch, validators paid; bounties, referrals, swarm splits | overview; Hub guide | M | `hub.py:35-38, 128-171, 353, 363, 370`. The validator pool is paid a flat +20 (source: +10–30). **Bounties, referrals, swarm splits and dormancy are not implemented** | X7 (re-run): 97% of credits come from promotion | PARTIAL |
| M16 | Client reuse score r = confidence·min(max(streak, 1), 5)·rep/100 + 0.3·sim; thresholds 0.72 / 0.55 | spec §4.16 | M | `hub.py:174-183`; `agent.py:266-274`; reputation fixed at the default 50 | tests | REPRODUCED |
| M17 | A2A ingest: reject on asset_id mismatch, confidence ×0.6, `external_candidates`, promotion only with `--validated` | spec §3.8, §4.15 | M | `store.py:35, 198-241` | AUDIT "§3.8 ingest faithful" | REPRODUCED |
| M18 | Installs locally, runs in any git repo; the hub is optional | overview | M | The workspace is a `{path: text}` map, not git (documented deviation); `run(..., hub=None)` works on any `rsi.core.Domain` | X0: three domains plus a bare `FunctionDomain` in `tests/test_evomap_review.py` | PARTIAL (no git rollback or stash) |
| M19 | Marketplace, worker pool, bounties; "a layer above MCP and skill files" | overview | M | not implemented (impl §6) | – | NOT REPRODUCED (not built) |
| M20 | The memory graph reads the executor's self-printed EvolutionEvent, so self-assessment steers learning | spec §4.11 (deob `memoryGraph.js`) | M | `OutcomeInferrer` faithful vs safe, `memory.py:244-297`; one-cycle-late timing `agent.py:604-610` | X15 (re-run, unchanged): with an over-reporting executor the preferred gene's true effect is 0.30 vs 1.26 in safe mode (paired +0.97 [+0.76, +1.20]) | REPRODUCED (simulation) |
| M21 | Assets published by agents on different models are inherited by others | overview | M | `PopulationSimulator`, heterogeneous `GeneWorldModel` abilities | X6 (re-run): 64% of the hub genes weak agents took were written by strong agents | REPRODUCED (simulation) |

### Vendor results (Strategy Genes paper, Evolver README, overview)

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| V1 | 4,590 controlled trials over 45 scientific code-solving scenarios | SG abstract (Evolver README) | Q | – | none; the benchmark is not available | NOT TESTABLE HERE (unreleased scenarios, Gemini models, full text blocked) |
| V2 | Gene 54.0% vs Skill 49.9% vs no guidance 51.0% | SG snippets; mpx summary | Q | Arms exist: `render_gene` / `render_skill` / `SkillInjector` (`prompts.py:27,71`; `inject.py:69`) | Offline X1 (mock, re-run): none 0.50, Skill 0.82, Gene 0.91. This **restates `KataSimSolver`'s knobs**, and the mock even has Skill ≫ none, the opposite sign to the paper's −1.1 (N11; the X1 JSON now records `direction_matches_paper: false` and says so in its note). The live X1 was never run; Haiku is at ceiling on the katas (holdout 1.0 without genes: `results/evomap/live_smoke.json`, RUNS.md) | NOT TESTABLE HERE (needs live LLM runs on a benchmark with headroom) |
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

X7 base setup: 40 agents × 30 epochs × 20 seeds with 15% farmers publishing 8 assets per epoch (`results/evomap/x7_behind_evomap.json`, re-run 2026-09-28 on the fixed code: faithful agents now search the hub first and use hits as references, and the naive hub adds semantic search). The JSON now also has the `farm_rate_2` / `farm_rate_1` arms (N2), the GDI decomposition (N1), the zero-review share (B8) and the study-style trivial-command share (N9).

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| B1 | 98% of shared assets are never reused | BE abstract | Q | `ReuseMetrics.never_reused_*` `metrics.py:56-80`; reuse = a non-author adoption or review | X7 (re-run): 95.8% [95.6, 95.9]; without farmers 72.8%. Farm rate 8 / 2 / 1 (now X7 arms, N2): 95.8% / 89.0% / 84.6% | PARTIAL (the mechanism reproduces; the number is set by the farm-rate knob) |
| B2 | Rewards are tied to publication, not adoption | BE abstract; overview | Q/L | +100 per promotion vs +5 per fetch (`hub.py:35-36`), taken from the vendor guide | X7 (re-run): 97.2% of earned credits come from promotion | REPRODUCED |
| B3 | About 10% of agents hold most credits, earned by repeated publishing rather than reuse or bounties | BE snippets; overview | Q | `CreditLedger.top_share` `hub.py:159` | X7 (re-run): the top 10% hold 57.8% of earned credits (Gini 0.79), and all of them are farmers. At farm rate 2 / 1 the share is 42% / 45% (not "most"). Bounties are not modelled, so "rather than bounties" is untested | PARTIAL |
| B4 | 84%+ of approved assets passed validation with vacuous tests (e.g. `console.log`) | BE abstract | Q | Promotion on the self-reported report (`hub.py:383`). Two measurements now (N9): `vacuous_share_promoted` (ours, a *discriminative* re-check) and `trivial_command_share_promoted` (the study's notion: the test command is trivial as written, a static lint; `metrics.py:64-75`) | X7 (re-run): study notion **80.7%** [80.4, 81.0] (the study: 84%+); our discriminative notion 92.0%. Without farmers 41% / 41%; with real-validation honest writers 78% / 90%; farm rate 2 / 1 → 68% / 60% (study notion) | PARTIAL (mechanism reproduces; the level is set by the farm rate) |
| B5 | Validation evidence is self-reported and not independently verified; the only thing the validator can run is trivial | BE abstract; evolver `sandboxExecutor.js` | M | naive hub promotion rule and the empty-directory validator (`hub.py:350-360`) | X9 (re-run): the empty-directory validator accepts only `--version`; the local runner rejects 25% of vacuous kinds | REPRODUCED |
| B6 | 35% of the ranking comes from an "intrinsic" component made of self-reported metadata, which agents can inflate | BE snippets; overview | Q/M | `GDIRanker.intrinsic`: five of six metrics are claimed (`hub.py:208-219`) | X8 (re-run): inflating every claimed field moves an honest asset up 69 percentile points [67, 71] (GDI +9.3). Decomposition, now in X7 (N1): intrinsic is 67.5% of the mean GDI *level* but only 5.5% [5.3, 5.6] of its *variance* across promoted assets | PARTIAL (inflatable: yes; "35% weight" is taken as input) |
| B7 | Blast radius is the dominant lever: degrading only it drops the optimal score 40.2 → 36.0 | BE snippets | Q | reconstructed blast score (`hub.py:203-206`) | X8 (re-run): the largest single lever is **streak** (+40 pp), then blast (+28 pp). Degrading blast gives 56.1 → 50.5; the relative drop (10.0%) is close to the paper's 10.4%, but the ranking of levers is not | NOT REPRODUCED |
| B8 | 99% of assets get zero votes from other agents | BE snippets | Q | naive consumers review each asset once (`agent.py:614-618`) | X7 (re-run, now a reported metric): 95.7% of promoted assets have zero reviews at farm rate 8 (88% / 83% at rates 2 / 1) | REPRODUCED (simulation) |
| B9 | The four-dimensional GDI "collapses into a one-dimensional metric dominated by the Intrinsic component" | BE snippets | Q | – | X7 (re-run, 20 seeds): the intrinsic part carries 5.5% of GDI variance; freshness 49%, social 12%, usage 12% (farm rates 2 / 1: intrinsic 3.4% / 2.8%); X8 `intrinsic_var_share` 0.06. N1 is documented, not "fixed": every farmer claims the same optimal metadata, so intrinsic barely varies across assets | NOT REPRODUCED (in our reconstruction the rank is driven by freshness, then social and usage) |
| B10 | Networks need verifiable execution and trustworthy evaluation, not self-reporting | BE abstract; overview | L | the SafeHub extension (see below) | X9, X10, X11 | REPRODUCED (simulation; see E1–E8) |

### Qualitative claims and caveats (overview)

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| C1 | Agents grade their own work; quality signals are easy to game | overview "Watch out" | L | faithful keep rule and self-report paths | X16 (re-run): Spearman(composite, task) = 0.010 [−0.019, 0.039] under vacuous validation, and 100% of failed tasks still clear the 0.78 publish bar; X15; X8 | REPRODUCED |
| C2 | Publishing is rewarded over usefulness, so the library fills with unused assets | overview | L | naive credits | X7 (re-run: never reused 95.8%, duplicate floods) | REPRODUCED (magnitude set by the knob) |
| C3 | Inheriting another agent's strategy means trusting code of unknown quality; treat it as untrusted until tested | overview | L | faithful direct apply vs quarantine | X11 (re-run): naive hub plus direct apply → consumer solve rate 0.167 vs 0.733 isolated (−0.57 [−0.59, −0.54]), with 94% of consumer cycles running a poisoned gene and 38.5 hub-poisoned genes in local stores (0 self-written); quarantine brings it to 0.733 | REPRODUCED (simulation) |
| C4 | A lesson learned once can be inherited by many agents, even on different models | overview | L | – | X6 (re-run): solve rate +0.023 [+0.017, +0.029]; weak models +0.050 [+0.038, +0.063], strong models −0.005 [−0.008, −0.001] | REPRODUCED (small, simulated; strong models lose slightly) |
| C5 | An audit trail is built into every change | overview | L | M3 | X13 | REPRODUCED |
| C6 | The guard against overfitting is weak; no held-out set; no explicit cost rule | overview "Side by side" | L | faithful keep rule is a process score (spec §8.2) | X16; code reading | REPRODUCED |
| C7 | Reuse avoids re-solving (the only cost control) | overview | L | – | X6 (re-run): from-scratch discoveries 380 → 224 (−155 [−169, −142]); tokens per solve 10,375 → 6,605 (−36%). The token saving is partly programmed (V8), and the absolute token counts are about 2.5× the pre-fix ones because the A1 paired check adds 5 rollouts to every newly written gene | REPRODUCED (simulation) |
| C8 | The vendor figures come from EvoMap's own team, are single runs, and have not been replicated | overview | C | – | still true: we did not replicate them (V1–V8) | NOT TESTABLE HERE |
| C9 | The independent study measured the network, not benchmark performance | overview | C | – | – | NOT TESTABLE HERE (a statement about the source) |
| C10 | Name clash: EvoMap's "AutoResearch" is unrelated to Karpathy's autoresearch | overview | C | – | – | NOT TESTABLE HERE (documentation only) |
| C11 | Source discrepancies: "daily active" vs "agents"; Behind-EvoMap dated 25 vs 26 May | spec §8.5 | C | – | – | NOT TESTABLE HERE |

### Our SafeHub extension (not EvoMap claims; not counted above)

| # | claim (ours, spec §9.2) | code | evidence | verdict |
|---|---|---|---|---|
| E1 | Adoption ranking and credits raise reuse among promoted assets | `safehub.py:174+` | X10 (re-run): 0.91 vs 0.043 (+0.87 [+0.84, +0.90]) | REPRODUCED (simulation) |
| E2 | Verified execution drives vacuous promotions to about 0 | discriminative check plus `UpliftLCB` `safehub.py:85` | X10 (re-run): 0 vs 0.92 (study notion 0 vs 0.81); X9: precision and recall 1.00 on seeded kinds | REPRODUCED (simulation; seeded vacuity kinds only) |
| E3 | Rank validity ρ > 0 and better than GDI | adoption rank | X10 re-run on the fixed code (N7, AUDIT X10): 0.089 [−0.022, +0.204] vs 0.032 [0.018, 0.045]; paired +0.058 [−0.053, +0.169]. The JSON now says `rank_validity_positive: false`, `rank_validity_better_than_naive: false` | NOT REPRODUCED (was PARTIAL; the earlier positive CI did not survive the fixes) |
| E4 | Exploration slots cut time to first reuse | Thompson slots | X10 (re-run): paired −0.025 [−0.20, +0.15]; exploration does raise the share of verified assets ever adopted (+0.040 [+0.022, +0.056]) | NOT REPRODUCED |
| E5 | Farmers earn about 0 | stake plus adoption-only credits | X10 (re-run): farmer credit share 0 vs 0.865 | REPRODUCED |
| E6 | Consumer quarantine blocks poison; SafeHub alone blocks poison | `quarantine.py:48` | X11 (re-run): 0 of 360 poisoned bundles promoted; quarantine removes the harm (0 hub poison in stores) | REPRODUCED |
| E7 | SafeHub rank is invariant to claimed metadata | – | X8 (re-run): Δrank 0 for every field | REPRODUCED |
| E8 | Evolver's failure distiller pollutes a faithful library (spec-derived) | `distill.py:179` | X17 (re-run): −0.165 GeneWorld, −0.117 katas | REPRODUCED (depends on our 1 h per cycle clock) |

A confound applies to E1–E5: the X10 arms change both the hub and the agents' regime (safe vs faithful agents).

## Newly found mismatches

These were not reported by `evomap-impl.md` §9–10, `RUNS.md` or `AUDIT.md`. Every item is now fixed or dispositioned; see "Fix log" (the text below is the original finding).

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
5. ~~**Stale evidence** (N7)~~: fixed. Every `results/evomap/*.json` was regenerated on the fixed code on 2026-09-28 and the impl §4 table updated; X10's rank-validity verdict is now `false`.

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
  - ~~Fix N3–N6 so faithful mode matches the deobfuscated engine byte for byte.~~ Done (Fix log).
- ~~**Evidence hygiene.**~~ Done: all `results/evomap/*.json` regenerated on 2026-09-28 (offline: about 48 min wall at 2 workers; live smoke $0.40).

## Checks run for this audit (offline, no LLM calls)

- `pytest tests/test_evomap_*.py`: 51 passed (about 20 s).
- `asset_id` differential fuzz against `@evomap/gep-sdk@1.14.0` in node:
  - scripts `scratchpad/claims_evomap/gen.py`, `gen2.py`, `gen3.py`, `gen4.py`, `cmp.mjs`, `cmp2.mjs`;
  - the full random corpus has 740 mismatches in 3,000, all from the two edge cases;
  - with astral-plus-high-BMP keys excluded (`gen4`) or ints kept within ±2^53 (`gen3`), 0 of 3,000.
- Composite rounding sweep: `scratchpad/claims_evomap/round_check.py`.
- GDI decomposition and farm-rate sensitivity: `scratchpad/claims_evomap/gdi_decomp.py` → `gdi_decomp.json`. It used the X7 base population, 40 agents × 30 epochs, seeds 0–1, farm rates 8 / 2 / 1, and took 59 s.

## Fix log (2026-09-28)

The user asked to fix every open finding, re-run, then push and merge. This section records the EvoMap part. Method for each code finding: reproduce it (reusing the auditors' scratch scripts), fix it, then add a regression test that fails on the pre-fix code. All 12 tests in `tests/test_evomap_fixes.py` were run against the pre-fix tree (`git archive 79e464d`): 11 fail there and pass now. The 12th, `test_a_real_new_gene_is_still_kept`, is a guard that the A1 fix does not reject real genes, so it passes on both trees.

| finding | disposition | fix (file:line) | regression test | evidence after the fix |
|---|---|---|---|---|
| N1 GDI "collapse" | **documented** (not a code defect: our GDI internals are a reconstruction) | X7 now reports the decomposition for every arm (`experiments/evomap/x7_behind_evomap.py` `gdi_decomposition`) | – (a measurement) | X7 base: intrinsic is 67.5% of the GDI level but 5.5% of its variance; freshness 49%, social 12%, usage 12%. B9 stays NOT REPRODUCED |
| N2 farm-rate knob | **reported** | X7 arms `farm_rate_2` / `farm_rate_1`, verdict `farm_rate_sensitivity` | – | never reused 95.8 / 89.0 / 84.6%; trivial-command share 81 / 68 / 60%; top-10% credit share 58 / 42 / 45% at rates 8 / 2 / 1 |
| N3 estimate-drift penalty | **fixed (faithful default = Evolver)** | `composite_score(estimate_key="files_changed")` reads Evolver's dead key, `rsi/evomap/solidify.py:144-190` (`:181`); opt-in `Config.estimate_drift_penalty` → `Solidifier(estimate_drift_penalty=True)` `solidify.py:295-297, :344`. The spec's §4.10 text (not owned here) still describes the penalty as active | `test_estimate_drift_penalty_is_dead_as_in_evolver_unless_enabled` | ratio-5 estimate: 0.95 (Evolver) vs 0.87 with the opt-in penalty |
| N4 rounding | **fixed** | `js_round2` (`Math.round(x*100)/100`, exact tie test) `solidify.py:193-201`, used at `:190`; the frozen AUDIT recompute test now uses it | `test_composite_rounds_like_javascript` | 0.955 → 0.96 (was 0.95). Every "0.95" composite in the pre-fix traces is 0.96 now (live smoke, mock runs). No publish decision changes: the 0.78 bar is never crossed (round_check.py) |
| N5 hash edge cases | **fixed** | UTF-16 key order `hashing.py:62-68, :85`; integers ≥ 2^53 as doubles (`1e+21`, 2^53+1 → 9007199254740992, overflow → `null`) `hashing.py:29-40` | `test_asset_id_matches_gep_sdk_on_edge_cases` (vectors from the SDK in node) | 0 / 18,000 mismatches vs `@evomap/gep-sdk@1.14.0` (was 740 / 3,000 on the same `gen.py` corpus) |
| N6 distiller fallback | **fixed** | fallback `["python --test"]` (port of `node --test`), always filtered by the mode's allowlist → `[]`; docstring corrected (`node --version` is only the LLM distiller prompt's advice). `rsi/evomap/distill.py:1-20, :64-77`; `config.py` docstring | `test_heuristic_distiller_fallback_validation_is_empty_like_evolver` | the heuristic gene ships with an empty list (validation component 0.5), as in Evolver |
| N7 stale results | **re-run** | every `results/evomap/*.json` regenerated on the fixed code (list below); impl §4 table updated | – | see "Experiments re-run" |
| N8 / M8a hub order and reuse | **fixed (faithful default = Evolver); overview error documented** | `hub_when="first"` (faithful default, `config.py:33`): hub search before local selection, `hub_search_miss_with_problem` on a miss with problem signals `agent.py:453-459`; `reference` reuse injects the hit next to the local gene, solidify scores the local gene, the hub gene is never stored `agent.py:499-510, :541-567`; the gene writer still runs when no local gene fits and the attempt with the reference failed (`agent.py:579-582`). Old behaviour: `reuse_mode="replace"`, `hub_when="always"` (non-default) | `test_faithful_reference_mode_injects_the_hub_hit_next_to_the_local_gene`, `test_faithful_mode_searches_the_hub_before_local_selection` | M8b → REPRODUCED. X7 base (faithful agents): 851 of 960 cycles of one seed ran a hub reference; hub-sourced poison never enters a faithful store (X10 naive `poisoned_in_stores_hub` 0) |
| N9 vacuity notion | **fixed (second metric)** | `ReuseMetrics.is_trivial_command` + `trivial_command_share_promoted` `rsi/evomap/metrics.py:64-75, :108-114` (static lint: empty, info-only, shell no-op, inline `-c`, missing script, print-only or constant-assert script) | `test_trivial_command_share_is_reported_next_to_discriminative_vacuity` (weak assert: vacuous for us, not trivial for the study) | X7 base: 80.7% (study: 84%+) next to our 92.0% |
| N10 semantic hub search | **fixed** | `NaiveEvoMapHub(search_mode="signal+semantic")` default: signal search merged with a semantic search whose query follows `hubSearch.js`; only semantic hits carry a similarity (`rsi/evomap/hub.py:395-445`). `"signal"` = `HUBSEARCH_SEMANTIC=false`; `"legacy"` = the old pre-filter | `test_naive_hub_surfaces_assets_by_semantic_similarity` | an asset with no literal pattern hit is now served |
| N11 X1 direction wording | **fixed (wording) and documented** | X1 verdict adds `paper_skill_below_none`, `direction_matches_paper` and a corrected note (`experiments/evomap/x1_representation.py`); impl §4 row reworded | – | X1 re-run: `direction_matches_paper: false` (Skill 0.82 ≫ none 0.50) |
| A1 single-sample keep | **fixed (safe default)** | `Config.new_gene_check="paired"`, `new_gene_retries=3` (safe): 3 fresh seeds with the gene and the same seeds without it; solved iff more than half solve AND the gene beats the bare harness. Faithful keeps one retry (`"single"`, Evolver has no such check). `rsi/evomap/agent.py:599-637`, `config.py`. The cycle's reported outcome (`task_score` / `task_success`) stays the first retry, the same seed the old single retry used, so solve-rate metrics remain comparable; the keep rule uses the sample verdict | `test_a_useless_new_gene_is_not_kept_on_a_lucky_retry` (pre-fix: `gene_dates_shallow_3` kept on one lucky retry), `test_a_real_new_gene_is_still_kept`; trace `new_gene_sample` recomputed in `test_every_gate_recomputes_from_the_traced_trials` | X10 / X11 safe stores hold 0 self-written harmful cards (was 1.15 per run in X10 safe); X0 safe holdout gain GeneWorld +0.15 (was +0.17), katas +0.46 (was +0.42); cost: 5 extra rollouts per written gene (X6 tokens per solve about 2.5× the pre-fix level); live smoke: money gene 3/3 with vs 0/3 without |
| A7 `n_promoted` naming | **fixed** | `n_promoted` = status `promoted` only; `n_admitted` + `admitted_states` for the admitted tier (`metrics.py:96-99`); X10 reports both | `test_n_promoted_counts_promoted_status_only` | X10 safe: `n_promoted` 13.9, `n_admitted` 17.0 |
| X10 poison attribution + rank-validity robustness | **fixed + re-run** | `PopulationSimulator.poisoned_in_stores_split()` (hub vs self, by provenance, hub-asset parent or foreign author) and `gene_poisoned` over every injected gene (`rsi/evomap/population.py:152-163, :202-225`); X10 / X11 report `poisoned_in_stores_{hub,self}`; X10 verdict adds the rank-validity CI and `rank_validity_better_than_naive` | `test_poisoned_in_stores_is_split_by_origin` | X11 naive + direct: 38.5 hub-poisoned vs 0 self-written; X10 rank validity 0.089 [−0.022, +0.204] → `rank_validity_positive: false` (E3 → NOT REPRODUCED) |
| C/V claims needing frontier models, the live network or the unreleased benchmarks (V1, V2, V6–V11, C8–C11) | **NOT TESTABLE HERE** (unchanged) | – | – | – |
| M19, B7, B9, V4, V5 | **not code defects**: not built (M19), or our reconstruction / knob-driven simulator does not produce the effect (B7, B9, V4, V5) | – | – | re-run values in the tables above |

**Experiments re-run on the fixed code (N7), all at full default settings, 2 workers:** X7 (1138 s, now 5 arms), X10 (657 s), X11 (174 s), X6 (73 s), X0 (40 s), X1 (3 s), X4 (409 s), X8 (77 s), X9 (8 s), X13 (119 s), X15 (147 s), X17 (33 s), and the live smoke (`live_smoke.py`, Claude Haiku, safe mode, 8 cycles, fresh cache in the scratchpad, **$0.40**). The old `results/evomap/live_smoke_run` store was deleted first, because a re-run resumes it. After two late changes (the poison attribution also counts foreign-authored genes; a cycle's reported outcome is the first retry, not the sample verdict) X10, X11, X6, X0 and X13 were re-run once more and the live smoke was replayed from its cache ($0 extra); the committed JSONs are from that final run.

**What changed in the numbers.** Most verdicts hold. The changes: E3 is no longer positive; X0 GeneWorld safe gain +0.15 (was +0.17), katas safe +0.46 (was +0.42), agentqa safe +0.018 [0.000, +0.040] (was +0.03, and its CI now touches 0); X6 hub gain +0.023 (was +0.017), strong models still lose slightly (−0.005); X7 never-reused 95.8% (was 97.5%), top-10% credit share 58% (was 61%); X11 isolated baseline 0.733 (was 0.721), naive + direct 0.167 (was 0.175). Live: two genes kept after paired samples (3/3 vs 0/3 and 3/3 vs 2/3); holdout is still 1.0 → 1.0 (no headroom).

**Not changed (documented):** the spec text `docs/methods/evomap.md` §4.10 (estimate-drift penalty described as active) and §10 row 6 (the overview's "look locally, then hub") are outside this fix's ownership. The discrepancy is recorded here and in the impl doc.

**Checks.** `pytest tests/test_evomap*.py`: 63 passed (51 before plus the 12 in `tests/test_evomap_fixes.py`). Fuzz: `scratchpad/claims_evomap/gen.py` + `cmp.mjs`, 0 / 18,000.
