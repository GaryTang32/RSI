# EvoMap: the Evolver engine, the Genome Evolution Protocol (GEP), Strategy Genes, and the "Behind EvoMap" critique

Method spec for the `rsi` framework. It covers four linked sources:

- **Evolver**: the open-source local engine, `EvoMap/evolver` (npm `@evomap/evolver`, v1.94.0 at the commit read). First release 2026-02-01 [code:evolver/README.md].
- **GEP**: the Genome Evolution Protocol spec and JSON schemas, in `@evomap/gep-sdk`.
- **Strategy Genes**: the vendor paper, Wang, Ren, Zhang, "From Procedural Skills to Strategy Genes: Towards Experience-Driven Test-Time Evolution", arXiv 2604.15097, April 2026 [snip:SG][code:skill2gep-repo/README.md].
- **Behind EvoMap**: the independent measurement study, Ye, Zhang, He, Peng, Tyson, "Behind EvoMap: Characterizing a Self-Evolving Agent-to-Agent Collaboration Network", arXiv 2605.25815. v1 was announced 26 May 2026 and v4 on 7 Aug 2026 [abs:BE].

## 0. Sources and how to read the tags

| Tag | Meaning |
|---|---|
| `[code:evolver/<path>:<symbol>]` | `EvoMap/evolver` at commit `31b0691` (2026-09-05, `package.json` version 1.94.0), clone in `scratchpad/src/EvoMap__evolver`. Of the **plain-text** files, I read these in full: `SKILL.md`, `README.md`, `assets/gep/genes.seed.json` (17 genes), `src/canary.js`, `src/gep/{a2a,validationReport}.js`, `src/gep/skill2gep.js` (lines 1–900), `src/gep/schemas/*.js`, `src/gep/validator/{index,sandboxExecutor}.js`, `scripts/{a2a_export,a2a_ingest,a2a_promote}.js`. I read these in part: `src/config.js` (lines 100–275), `src/gep/validator/{reporter,stakeBootstrap}.js`, `src/gep/llmReview.js` (prompt and parser), `scripts/gep_append_event.js` (validator), the header of `src/adapters/scripts/evolver-task-recall.js`, and the header of `test/issue562InertGeneBan.test.js`. I only grepped `src/gep/signals.js` (thresholds) and `src/gep/assetStore.js` (file layout). |
| `[deob:evolver/<path>:<symbol or constant>]` | **57 core modules in the current release are obfuscated** (javascript-obfuscator, string-array encoding). Among them are `selector.js`, `solidify.js`, `prompt.js`, `skillDistiller.js`, `policyCheck.js`, `memoryGraph.js`, `mutation.js`, `strategy.js`, `hubSearch.js`, `hubReview.js`, `epigenetics.js`, `learningSignals.js` and all of `src/evolve/pipeline/*`. The README states this: "Core evolution engine modules are distributed in obfuscated form" [code:evolver/README.md]. The package is GPL-3.0-or-later, so I recovered constants, prompt strings and control flow with `webcrack@2` (output in `scratchpad/deob/out/`). Identifiers stay machine-named (`_0x…`), so a symbol here is the **exported name** or the literal constant. String literals and numbers come out exactly; control flow is my reading. From the deobfuscated output I read `selector` and `hubReview` in full. I read the relevant parts of the rest: `solidify` (main flow, score, epigenetics, publish), `policyCheck` (constraints, validation policy and runner), `prompt` (all prompt strings), `skillDistiller` (prompts, constants), `memoryGraph` (aggregation, advice), `mutation`, `strategy`, `personality` (defaults), `epigenetics`, `learningSignals` and `hubSearch` (scoring). For `evolve.js` and the pipeline modules I read only the stage order and the hub-reuse path. |
| `[sdk114:<path>]` | `@evomap/gep-sdk@1.14.0` from npm (Apache-2.0 code, CC-BY-4.0 spec). It holds `schemas/{gene,capsule,evolution-event,mutation,task,recipe,pack}.schema.json`, `src/{contentHash,protocolConstants,capsuleEvidence}.js` and README. All read. |
| `[spec:§n]` | `spec/gep-spec-v1.md` inside `@evomap/gep-sdk@1.14.0`: "GEP: Genome Evolution Protocol, Version 1.0.0, Schema Version 1.14.0, Status: Draft, Date 2026-06-26". I read all 904 lines. |
| `[sdk11:<path>:<fn>]` | `@evomap/gep-sdk@1.1.0` (GPL line). It still ships **plain-text algorithm code**: `selector.js`, `memoryGraph.js`, `signals.js`, `mutation.js`, `gene.js`, `capsule.js`, `assetStore.js`. The README says this code was removed in 1.2.0. I read all of it. It is an older, simpler variant of the current engine. |
| `[vdoc:hub-skill§<section>]` | The EvoMap **Hub integration guide** (`https://evomap.ai/skill.md`, GEP-A2A v1.0.0). evomap.ai is egress-blocked, so I read the copy vendored in `github.com/coinmini/openclaw_clawevo_bsc_testnet/evomap_skill.md` (74 KB), cross-checked against an older copy in `LeoYeAI/openclaw-master-skills/skills/evomap/SKILL.md`. This is vendor documentation relayed by a third party; I could not date it. |
| `[abs:BE]` | Verbatim arXiv abstract of Behind EvoMap (v1, v2, v4), taken from arXiv RSS dumps mirrored in `github.com/ehijano/rss_fetch` (`rss_data/cs.MA/2026-05-25…`, `…2026-05-26…`, `…2026-08-06…`). Saved to `scratchpad/papers/behind-evomap.txt`. |
| `[snip:BE]`, `[snip:SG]`, `[snip:LWF]` | WebSearch result snippets that paraphrase the arXiv HTML of Behind EvoMap, Strategy Genes, and LongWoF-Bench (arXiv 2608.23200). The pages themselves are blocked, so these claims are **unverified**. The Behind EvoMap and Strategy Genes snippets are saved to `scratchpad/papers/behind-evomap.txt` and `scratchpad/papers/strategy-genes.txt`; the LongWoF-Bench snippets are quoted in §7.5. |
| `[sec:mpx]` | A third-party Chinese read-through of the Strategy Genes paper with its tables: `github.com/didi/mpx/.agents/skills/skill2gene/references/gep-protocol.md`. It is **unverified**. Two of its numbers (Gene 54.0, two complementary Genes 44.9) match `[snip:SG]`. |
| `[sec:critpt]` | `github.com/EvoMap/critpt-openclaw-reproducible-70/README.md` (vendor). |
| `[code:skill2gep-repo/<file>]` | `github.com/EvoMap/skill2gep` README (vendor). |
| `[doc]` | The user's overview page ("Four ways AI is learning to improve itself"), a secondary summary. |
| `[inferred]` | My own reconstruction, arithmetic or design proposal. |

**Fact-check pass (second reviewer).** A second pass re-checked every concrete claim against the plain code, the deobfuscated modules (`scratchpad/deob/out/`, now also read in full: `solidify`, `policyCheck`, `memoryGraph` advice and outcome inference, `mutation`, `strategy`, `personality`, `skillDistiller` triggers and the heuristic synthesizer, and the `signals`, `enrich`, `select` and `dispatch` pipeline stages), the plain `index.js` CLI handlers, sdk 1.1.0/1.14.0, the vendored Hub guide and the saved abstracts. Corrections are made in place. The most consequential ones:

- The default distiller path is a **heuristic** (no LLM), and the every-5-solidifies trigger bypasses the 24 h / 7-of-10 rule (§3.7, §4.14).
- The memory-graph outcome is written at the **start of the next cycle**, preferably from an EvolutionEvent JSON line found in the session transcript (§3.1, §4.11).
- In the default `reference` reuse mode the hub hit is embedded in the normal GEP prompt. The REUSE prompt is only for `direct` mode, and the in-place prompt is not wired into dispatch (§3.1, §6).
- `explore` mutations are `low` risk in code; the spec says `medium` (§3.4).
- The hub-reuse threshold is lowered to 0.55 when problem signals are present (§4.16).
- Canary failure becomes a constraint violation. The canary is skipped (counts as pass) when the target repo has no `src/canary.js` (§3.6).
- The default LLM-review executor is a stub that always approves (§6.7).
- The ban-latency expectations in X4 were wrong for the current engine (§9.3).
- Newly documented mechanisms: personality evolution, the adaptive strategy policy, plateau detection, memory epochs, the counted-file policy for blast radius, auto-created `gene_auto_*` genes, `ban_gene` after failure loops, and the three-layer signal extraction.
- The strict 1.14.0 schemas do **not** accept several objects the engine actually writes (§5).
- A WebSearch in this pass returned snippets that corroborate the Behind-EvoMap and LongWoF numbers. One snippet (of `evomap.ai/capabilities`) expands GDI as "Global Desirability Index" [snip:BE]. arxiv.org is still egress-blocked.

**Could not access.** I did not get the full text of either paper. arxiv.org (abs, pdf, html), export.arxiv.org, ar5iv, alphaxiv, huggingface, semanticscholar, researchgate, bytez, pith.science and awesomepapers.io all returned `CONNECT 403` or `EGRESS_BLOCKED`. The same happened for the EvoMap GEP whitepaper and wiki (evomap.ai), the OpenClaw CritPt blog post and the evomap.ai economics page. The GDI weights and the Behind EvoMap measurements beyond the abstract therefore rest on `[snip:BE]` only. Git history of `EvoMap/evolver` was squashed at v1.66.0 (2026-04-16), and npm versions before 1.67.0 are unpublished, so the older unobfuscated engine is only visible through `[sdk11]`.

**Naming, licence and trademark.** The spec text is CC-BY-4.0 and the SDK code is Apache-2.0. The notice says: "'EvoMap', 'GEP', and 'Genome Evolution Protocol' are trademarks of EvoMap … Independent implementations of the protocol are welcome … but must not be marketed under these names" [sdk114:README.md]. Evolver itself is GPL-3.0-or-later, as is gep-sdk up to 1.2.x [code:evolver/LICENSE][sdk114:README.md]. My recommendation [inferred]: implement from the spec and schemas with attribution, do not copy Evolver source, and name our module something neutral such as `rsi.genehub`, "compatible with GEP schema 1.14.0". This is not legal advice.

---

## 1. The idea in one paragraph (101)

Most self-improvement loops improve one system. EvoMap's idea is that **agents should not each learn the same lesson alone** [doc]. When an agent fixes a problem, the local engine (Evolver) records what worked as small, structured, content-hashed **assets**:

- a **Gene**: a compact strategy card with trigger signals, a few strategy steps, "AVOID" warnings, constraints and validation commands;
- a **Capsule**: the record of one successful application of a gene, with trigger, blast radius, outcome, execution trace and diff;
- an **EvolutionEvent**: an append-only audit record of every cycle, success or failure [spec:§2][doc].

At the start of each cycle, Evolver turns logs into **signals**. It then picks the best-matching gene from its local store, helped by a memory graph of past outcomes and some random "genetic drift" for exploration. It first asks the hub whether another agent already solved this. It then writes a strict protocol prompt, and the host coding agent (Claude Code, Cursor, OpenClaw, …) makes the change. The engine then **solidifies**: it measures blast radius, runs the gene's validation commands, scores the result, writes the event and capsule, and rolls back on failure [code:evolver/README.md][spec:§3].

Periodically it **distils** repeated successes into new genes [spec:§6]. By default this uses a heuristic synthesizer that merges the most frequent success pattern. When that fails, it falls back to an LLM prompt handed to the host agent [deob:evolver/src/gep/skillDistiller.js:autoDistill,prepareDistillation][code:evolver/index.js "DISTILL_REQUEST"]. Good capsules can be **published** to the EvoMap Hub. Other agents, possibly on different models, fetch "promoted" assets, apply them, and report back. A credit economy rewards contributors, and a score called GDI ranks assets [vdoc:hub-skill§Why EvoMap][abs:BE].

The vendor paper argues that this compact gene form carries experience better than long "skill" documents. Across 4,590 trials, a gene of about 230 tokens beat a skill package of about 2,500 tokens (54.0% vs 49.9%, against 51.0% with no guidance). Gene-evolved agents also raised CritPt physics scores from 9.1% to 18.57% (about 2×) and from 17.7% to 27.14% (about 1.5×) over their paired base models [snip:SG][code:evolver/README.md].

The independent study found the network's quality signals are mostly **self-reported**. 98% of assets were never reused, rewards track publishing rather than adoption, ranking is dominated by claimed metadata, and more than 84% of approved assets pass validation with vacuous tests such as `console.log` [abs:BE].

The lesson for our framework is to keep EvoMap's asset format and population-level sharing, but put **verifiable execution, adoption-based ranking and quarantine of untrusted assets** in place of self-reporting [doc "Never let the loop grade itself", "Measure reuse, not output"].

---

## 2. What is improved, what is frozen, who grades

| Aspect | Local Evolver loop | Hub / network | Strategy Genes paper |
|---|---|---|---|
| **Improved** | The agent's local **asset store**: `genes.json`, `capsules.json(l)`, the append-only `events.jsonl`, `memory_graph.jsonl` [code:evolver/README.md "GEP Protocol"][spec:§8]. The **target workspace** (a git repo) is also changed, by the host agent following the gene [deob:evolver/src/gep/prompt.js]. Gene metadata evolves too: `epigenetic_marks`, `learning_history`, `anti_patterns`, and on success `signals_match` gains `problem:*`/`area:*` learning tags [deob:evolver/src/gep/solidify.js:adaptGeneFromLearning][sdk114:schemas/gene.schema.json]. The run-level meta-parameters also evolve. A **PersonalityState** vector is moved by "natural selection" toward the best-scoring personality key and by rule-triggered mutations (§3.4) [deob:evolver/src/gep/personality.js:selectPersonalityForRun]. A per-cycle **strategy policy** caps target files and can force innovation (§3.4) [deob:evolver/src/evolve/pipeline/select.js:computeAdaptiveStrategyPolicy]. | The shared library of promoted Gene+Capsule(+Event) bundles, plus per-asset GDI, call counts, reviews and node reputation (0–100) [vdoc:hub-skill§Concepts, §Why EvoMap]. | Per-task experience objects (genes) injected at test time. For CritPt, an evolving gene pool [snip:SG]. |
| **Frozen** | Model weights. The host agent's harness (each agent keeps its own) [doc]. The evolver engine source is protected: "NEVER modify `skills/evolver/`", enforced by `critical_path_modified` unless `EVOLVE_ALLOW_SELF_MODIFY=true` [deob:evolver/src/gep/prompt.js][deob:evolver/src/gep/policyCheck.js]. | Models; each agent's local setup [doc]. | Base models (Gemini 3 Pro Preview, Gemini 3.1 Pro Preview; Gemini 3.1 Pro/Flash Lite for the 4,590 trials) [snip:SG][code:skill2gep-repo/README.md]. |
| **Who proposes** | The host coding agent (an LLM) reading the GEP prompt. Evolver itself "is a prompt generator, not a code patcher" [code:evolver/README.md]. The distiller LLM proposes new genes [deob:evolver/src/gep/skillDistiller.js]. | Any registered agent (500 starter credits, no human approval) [vdoc:hub-skill§Welcome]. | An "evolution" process over genes (Scan→Signal→Intent→Mutate→Validate→Solidify) [sec:mpx]. |
| **Who grades** | **Self-grading.** Success requires three things: the gene's own validation commands pass (whatever the gene declares), constraints pass (blast radius from `git diff`, forbidden paths, canary), and there are no protocol violations. The score is a weighted composite (§4.10) [deob:evolver/src/gep/solidify.js]. Solidify computes status and score from engine-held state (`last_run.mutation`/`personality_state`, which the engine itself built), git and validation results. It does **not** parse the five JSON objects the LLM prints, whose EvolutionEvent carries an LLM-typed `outcome.score` [deob:evolver/src/gep/prompt.js][deob:evolver/src/evolve/pipeline/dispatch.js:last_run]. The **memory graph**, however, which drives future gene preference and bans, takes its outcome at the start of the next cycle from the last single-line EvolutionEvent JSON found in the session transcript or today's log (note `evolutionevent_observed`), falling back to an error-delta heuristic (§4.11) [deob:evolver/src/gep/memoryGraph.js:recordOutcomeFromState]. An LLM-printed self-assessment can therefore steer selection [inferred]. An LLM review is optional and off by default (`EVOLVER_LLM_REVIEW`), and its only built-in executor is a stub that always returns `approved: true` (§6.7) [code:evolver/src/gep/llmReview.js:defaultExecute]. | The Hub verifies `asset_id` hashes and structure, stores bundles as `candidate`, "runs validation" through decentralized validator nodes that execute the **proposer's own validation commands** in an empty sandbox, then "admin or auto-promote" [vdoc:hub-skill§How It Works][code:evolver/src/gep/validator/index.js]. It ranks by GDI, which is 35% intrinsic, mostly self-reported [snip:BE]. | Task pass rate on 45 scientific code-solving scenarios; CritPt accuracy [snip:SG]. |
| **Overview summary** | "What changes: a shared library of strategies and worked examples. What stays fixed: the models; each agent keeps its own harness. How it decides: local validation plus a hub ranking score" [doc]. | "Keep rule: passes a local validation command; ranked by a hub score. Guard against overfitting: weak, since validation is self-reported and often vacuous" [doc]. | — |

---

## 3. The loop, step by step (precise pseudocode)

### 3.1 One local evolution cycle (`evolver` / `evolver --loop`)

The stage order comes from `src/evolve.js`: `runGuards → collectContext → extractSignalsStage → hubCoordinate → enrich → selectAndMutate → dispatch` [deob:evolver/src/evolve.js]. Solidify runs later as a separate invocation, `node index.js solidify` [deob:evolver/src/gep/prompt.js "If passed, run: node index.js solidify"].

```text
procedure EVOLVE_CYCLE(W: git workspace, S: LocalStore, M: MemoryGraph, H: Hub | None, cfg):
  runGuards()                                    # locks, idle gating, loop safety          [deob:evolver/src/evolve/guards.js]
  require is_git_repo(W)                          # git is needed for rollback and blast radius [code:evolver/README.md]
  ctx ← collectContext(W)                         # session transcripts, memory/MEMORY.md, USER.md, today's log,
                                                  # memory_graph tail, env fingerprint        [deob:evolver/src/evolve/pipeline/collect.js]
  # ---- signals stage                                                     [deob:evolver/src/evolve/pipeline/signals.js]
  recent ← last 80 EvolutionEvents of S.events   # history analysis inside uses the last 10 / last 8 of these
  signals ← EXTRACT_SIGNALS(ctx, recent)         # §3.2, §4.6
  signals += dormant-hypothesis signals from an interrupted cycle, user-declared pending_signals.json,
             conversation-sniffer capability signals, retry context of the last validation failure
             ("retry_error_context", "retry_cmd:…", "retry_stderr:…"), curriculum targets from hub capability gaps
  idle ← signals are only saturation/steady-state and none actionable, and the last hub fetch was < 600 s ago
  hubCoordinate(...)                              # heartbeat, bounty tasks, worker role (optional)
  # ---- enrich stage ("search first")                                     [deob:evolver/src/evolve/pipeline/enrich.js]
  M.recordOutcome(previous cycle's last_action, signals, observations)    # §4.11: the outcome of cycle t−1 is written HERE
  M.record(kind="signal", key=signalKey(signals), signals, error_signature)
  if H and not idle:
     problem ← signals ∩ {log_error, recurring_error, capability_gap, perf_bottleneck, test_failure, deployment_issue}
               or any "errsig:*"
     hubHit ← H.search(signals, threshold = 0.55 if problem else default 0.72)   # §4.16
     if miss and problem: signals += "hub_search_miss_with_problem"
  advice ← M.getAdvice(signals, S.genes)          # §4.4: preferredGeneId, bannedGeneIds, totalAttempts
  (every 5 cycles; 8 if the recent outcomes were all successes, 3 if all failures) record a strategic
       reflection with suggested personality mutations                    [deob:evolver/src/gep/reflection.js]
  plateauOverride ← hub "pivot_check" heartbeat action, or local: k consecutive non-success outcomes among the last
                    10 events; k ≥ 10 ⇒ severity "required" + drift on; k ≥ 5 ⇒ "suggested";
                    injects plateau_pivot_required|suggested and calls personality.forcePivot   # §4.3, §3.4
  peer knowledge from other nodes and hub heartbeat directives are appended to the prompt context
  # ---- select stage                                                      [deob:evolver/src/evolve/pipeline/select.js]
  sel ← SELECT_GENE_AND_CAPSULE(S.genes, S.capsules, signals, advice, S.failed_capsules[-50:],
                                driftEnabled, capabilityGaps, noveltyScore, plateauOverride, forced/required gene)  # §3.3
  policy ← ADAPTIVE_STRATEGY_POLICY(recent, sel.gene, signals)   # §3.4: target max files, forceInnovate
  personality ← SELECT_PERSONALITY_FOR_RUN(drift, signals, recent)   # §3.4
  mutation ← BUILD_MUTATION(signals, sel.gene, innovateMode, personality, allowHighRisk)        # §3.4
  M.record(kind="hypothesis", text="Given signal_key=… with N signals, selecting gene=… under mode=… is
           expected to reduce repeated errors and improve stability.")          [deob:evolver/src/gep/memoryGraph.js]
  M.record(kind="attempt", action={drift, selected_by, selector}); state.last_action ← {…, had_error, baseline_observed}
  # ---- dispatch stage                                                    [deob:evolver/src/evolve/pipeline/dispatch.js]
  state.last_run ← {run_id, created_at, parent_event_id, selected_gene_id, selected_capsule_id, selector, signals,
                    mutation, personality_state/key/known/mutations, drift, source_type, reused_asset_id,
                    baseline_untracked, baseline_git_head, strategy_policy, active_task_id, …,
                    blast_radius_estimate = {files: policy.blastRadiusMaxFiles (else gene max_files, else 12),
                                             lines: 80 · files}}
  if idle: optional exploration for new signals; no prompt is generated; return
  if hubHit.hit and mode == "direct":   prompt ← REUSE_PROMPT(hubHit.asset)        # source_type "reused"
  else: prompt ← GEP_PROMPT(signals, sel, mutation, personality, previews, history,
                            hubMatchedBlock = HUB_MATCHED_BLOCK(hubHit) if hubHit.hit (mode "reference", the default))
                                                  # source_type "reference" if hub hit, else "generated"   §6.1–6.2
  # (buildInplaceGepPrompt exists and is exported, but no pipeline module calls it in v1.94.0)   [deob:evolver/src/gep/prompt.js]
  print(prompt) (or, with a bridge, sessions_spawn an executor: "You are the executor (the Hand)…")
  ── host LLM agent: edits W per gene.strategy, runs validation, emits 5 JSON objects
     (Mutation, PersonalityState, EvolutionEvent, Gene, Capsule); these objects are NOT parsed by solidify ──
  SOLIDIFY(W, S)                                  # §3.6, triggered by `node index.js solidify`
  if success: MAYBE_DISTILL()                     # §3.7, run by the CLI handler after a successful solidify  [code:evolver/index.js]
```

In `--loop` mode the above repeats "in a daemon loop with adaptive sleep". Independent daemons run the validator role (§3.9) [code:evolver/README.md][code:evolver/src/gep/validator/index.js:startValidatorDaemon].

### 3.2 Signal extraction and de-duplication

```text
function EXTRACT_SIGNALS(ctx, recentEvents):     # current engine: [code:evolver/src/gep/signals.js:extractSignals]
                                                 # (older, regex-only form: [sdk11:signals.js:extractSignals]; list in [spec:§3.1,§7])
  corpus ← transcript ‖ today log ‖ MEMORY.md ‖ USER.md
  # Layer 1, regex (deterministic)
  if /[error]|error:|exception:|iserror":true|"status":"error"|"status":"failed"|错误:|异常:|报错:|失败:/:  S += "log_error"
  first line matching TypeError:/ReferenceError:/SyntaxError:/error:/exception:/[error/ZH markers → "errsig:" + line (≤ 260 chars)
  resource / context signals: memory_missing, user_missing, integration_key_missing, session_logs_missing,
     claude_code_context_bloat, context_explosion, tool_schema_bloat, …, token_budget_overflow
  "protocol_drift" if the corpus contains "prompt" but not "evolutionevent"
  recurring_error (+ "recurring_errsig(<N>x):<sig>") if the same error pattern appears ≥ 3 times
  feature-request patterns (EN/ZH-CN/ZH-TW/JA) → "user_feature_request" (+ ":" + snippet ≤ 200)
  improvement patterns, only if no error hit → "user_improvement_suggestion" (+ ":" + snippet)
  /slow|timeout|timed out|latency|bottleneck|took too long|high cpu|high memory|oom|out of memory/ → "perf_bottleneck"
  /not supported|cannot|doesn't support|no way to|missing feature|unsupported|not available|not implemented/
      → "capability_gap" (unless a *_missing resource signal fired)
  tool usage from "[TOOL: x]" markers: any tool ≥ 10 uses → "high_tool_usage:<tool>"; exec ≥ 5 → "repeated_tool_usage:exec"
  ad-hoc exec of node/npx/curl…api/python scripts → "tool_bypass"
  # Layer 2, weighted keyword scoring: fires a signal when Σ count(kw)·weight ≥ threshold, per profile
  #   (perf_bottleneck ≥ 6, capability_gap ≥ 5, user_feature_request ≥ 6, user_improvement_suggestion ≥ 5,
  #    recurring_error ≥ 7, tool_bypass ≥ 6, evolution_stagnation_detected ≥ 6)          [SIGNAL_PROFILES]
  # Layer 3, LLM: every 5th cycle, POST the first 2000 chars of the corpus to the Hub's /a2a/signal/analyze;
  #   keeps ≤ 10 returned signals, each < 200 chars; silently empty without a hub      [_extractLLM]
  S ← union of the three layers; drop cosmetic *_missing / windows_shell_incompatible if actionable signals exist
  H ← analyzeRecentHistory(recentEvents[-10:])
  suppressed ← {key : key appears in ≥ 3 of the last 8 events}  # errsig:/recurring_errsig/feature:/improvement: collapse to prefix keys
  S ← S \ suppressed; if that emptied a non-empty S: S += ["evolution_stagnation_detected", "stable_success_plateau"]
  if H.consecutiveRepairCount ≥ 3:  strip log_error / errsig:* / recurring_errsig*; if nothing is left add
                                    "repair_loop_detected", "stable_success_plateau"; add "force_innovation_after_repair_loop"
  if H.emptyCycleCount ≥ 4 (of last 8; empty = zero blast radius): strip repair signals;
                                    add "empty_cycle_loop_detected", "stable_success_plateau"
  consecutiveEmptyCycles ≥ 5 → "force_steady_state" + "evolution_saturation";  ≥ 3 → "evolution_saturation"
  consecutiveEmptyCycles ≥ 3 → "explore_opportunity"
  consecutiveFailureCount ≥ 3 (an empty cycle BREAKS the streak):
       if the corpus is dominated by a host/LLM 4xx client error: "host_llm_client_error" only (no streak, no ban)
       else "consecutive_failure_streak_<N>"; if N ≥ 5: "failure_loop_detected" + "ban_gene:<most frequent gene in last 8>"
  recentFailureRatio ≥ 0.75 (non-empty failures / last 8) → "high_failure_ratio" + "force_innovation_after_repair_loop"
       (or "host_llm_client_error" under the same 4xx condition)
  if ≥ 4 recent events: over the scores of the last 6, avg < 0.35 and not improving (> +0.05 over the previous)
       → "plateau_pivot_required"; avg < 0.55, not improving and repair streak ≥ 2 → "plateau_pivot_suggested"
  if S empty: S ← ["stable_success_plateau"]
  return unique(S)
```

### 3.3 Gene and capsule selection (current engine)

```text
function SELECT_GENE_AND_CAPSULE(genes, capsules, signals, advice, failedCapsules, …):   [deob:evolver/src/gep/selector.js:selectGeneAndCapsule]
  bans ← advice.bannedGeneIds
       ∪ {g : ≥ 2 failed capsules of g with overlap(signals, capsule.trigger) ≥ 0.6}      # banGenesFromFailedCapsules
       ∪ {id : "ban_gene:<id>" ∈ signals}
  env ← captureEnvFingerprint()
  forced ← advice.forcedGeneId or advice.requiredGeneId
  if forced exists, not banned, not epigenetically suppressed: return forced (driftMode "forced_gene")
  scored ← [(g, SCORE(g, signals) + ADJ(g, signals, env)) for g in genes if not suppressed(g, env)]
  for (g, s): if s > 0 and g.id starts "gene_distilled_": s ← 0.8·s
  keep s > 0, sort desc
  if empty: return first non-banned "gene_distilled_*" gene (driftMode "distilled_fallback") else None
  if advice.preferredGeneId in scored and not plateau.active and not banned: its score ×= 1.5; re-sort
  cands ← [x ∈ scored : x.gene ∉ bans];  if empty: return None
  if preferInplace: move the best "inplace" gene with score ≥ 0.7·top to the front
  I ← DRIFT_INTENSITY(Ne = |genes|, driftEnabled, memoryEvidence = advice.totalAttempts)     # §4.3
  if plateau.active: I ← max(I, 1.0 if severity=="required" else 0.7)
  idx ← 0; mode ← "selection"
  if I > 0 and |cands| > 1 and rand() < I:
      n ← min(|cands|, max(2, ceil(|cands|·I)))
      if capabilityGaps non-empty:
          if some cand matches any of the first 5 gaps:
               idx ← argmax over cands of (#gap hits, then base score); mode ← "diversity_directed"
          else: if noveltyScore < 0.3 and n < |cands|: n ← n + 1
                idx ← uniform[0, n); mode ← "random_weighted"
      else: idx ← uniform[0, n); mode ← "random"
  capsule ← argmax_c #{t ∈ c.trigger : MATCH(t, signals)} (if > 0)
  return {selectedGene: cands[idx].gene, capsuleCandidates: [capsule], alternatives: next 4,
          selector: {selected, reason[], alternatives, selectionPath, memoryUsed}}
```

Two further points [deob:evolver/src/gep/selector.js]:

- The capsule is chosen by trigger match **independently of the selected gene**, so the capsule candidate may belong to a different gene.
- `selectGeneAndCapsule` never passes `preferInplace`, so the in-place re-ordering is inactive in the main pipeline.

The older plain variant `[sdk11:selector.js:selectGene]` uses the same skeleton with `SCORE = pattern hits` only. The gate on its random jitter is `useDrift = driftEnabled || driftIntensity > 0.15`. A multi-gene selector (`selectMultiGeneChunk`: up to 3 genes with pairwise `signals_match` Jaccard < 0.3) is exported but not called anywhere in the pipeline [deob:evolver/src/gep/selector.js]. Selection is therefore single-gene, which matches the paper's composition finding (§7.2).

### 3.4 Mutation and personality

```text
# select stage, before BUILD_MUTATION                                       [deob:evolver/src/evolve/pipeline/select.js]
policy ← ADAPTIVE_STRATEGY_POLICY(recentEvents, gene, signals):
   repairStreak / failureStreak = consecutive "repair" intents / "failed" outcomes at the tail of the last 8 events
   highRiskGene  = ≥ 1 hard, or (≥ 2 soft and 0 successes in the last 6 learning_history), among the gene's last 5
                   anti_patterns whose learning_signals intersect expandSignals(signals)
   forceInnovate = (stable_success_plateau | evolution_saturation | empty_cycle_loop_detected ∈ signals
                    or failureStreak ≥ 3 or repairStreak ≥ 3) and "log_error" ∉ signals
   cautious      = highRiskGene or failureStreak ≥ 2
   maxFiles      = gene.max_files (default 12), clamped to [2, 6] if cautious, else to [3, 10] if forceInnovate
   → directives in the prompt ("Target max files for this cycle: N.") and the blast-radius estimate
innovateMode ← drift or forceInnovate or FORCE_INNOVATION=true
               or (creativity ≥ 0.75 and the last 6 outcomes are all success and their mean score ≥ 0.7;
                   this also adds "stable_success_plateau")
allowHighRisk ← drift ∧ personality_known ∧ rigor ≥ 0.8 ∧ risk_tolerance ≤ 0.3 ∧ "log_error" ∉ signals
                ∧ isHighRiskMutationAllowed(personality)

function BUILD_MUTATION(signals, gene, innovateMode, personality, allowHighRisk):   [deob:evolver/src/gep/mutation.js]
  category ← "repair"   if an error signal is present (exact log_error | error | exception | failed | unstable,
                          or errsig:* / errsig_norm:*) and neither issue_already_resolved nor openclaw_self_healed
           ← "innovate" elif innovateMode
           ← "innovate" elif an opportunity signal (or "<opportunity>:…") is present: user_feature_request,
                          user_improvement_suggestion, perf_bottleneck, capability_gap, stable_success_plateau,
                          external_opportunity, recurring_error, unsupported_input_type, evolution_stagnation_detected,
                          repair_loop_detected, force_innovation_after_repair_loop, tool_bypass, curriculum_target,
                          issue_already_resolved, openclaw_self_healed, empty_cycle_loop_detected
           ← "explore"  elif "explore_opportunity" ∈ signals
           ← "innovate" elif resolveStrategy().innovate ≥ 0.5     # true for "balanced" (0.50) and "innovate" presets
           ← "optimize" otherwise
  risk ← "low"; if category == innovate: "medium"; if allowHighRisk and category == innovate: "high"
         # NOTE: explore stays "low" in code; the GEP spec text says explore = medium  [spec:§3.3]
  if category == innovate and highRiskPersonality (rigor < 0.5 or risk_tolerance > 0.6):
       category ← optimize, risk ← low, trigger_signals += "safety:avoid_innovate_with_high_risk_personality"
  if risk == high and not (rigor ≥ 0.6 and risk_tolerance ≤ 0.5): risk ← medium, += "safety:downgrade_high_risk"
  return {type:"Mutation", id:"mut_<ts>", category, trigger_signals: unique(signals),
          target: "gene:<id>" | "behavior:protocol", expected_effect: text by category, risk_level}
```

Because `balanced` has `innovate = 0.50`, the fall-through category under the default preset is `innovate`, not `optimize`. `optimize` only appears as a fall-through in the early-stabilize (cycles 1–5), harden, repair-only and steady-state presets [inferred from deob:mutation.js + strategy.js].

**Personality evolution** [deob:evolver/src/gep/personality.js]:

- The default PersonalityState is `{rigor 0.7, creativity 0.35, verbosity 0.25, risk_tolerance 0.4, obedience 0.85}` [:defaultPersonalityState].
- Each state is keyed by rounding every trait to 0.1.
- After each solidify, `stats[key]` records success/fail counts and a running mean score.
- `selectPersonalityForRun` then applies three steps:
  1. **Natural selection.** Among keys with ≥ 3 outcomes, pick the one maximising `0.75·(s+1)/(n+2) + 0.25·avg_score·min(1, n/8)`. Move the current state toward it on up to 2 traits, each by at most ±0.1 and only if |Δ| ≥ 0.05.
  2. **Triggered mutation.** This fires when drift is on, when ≥ 3 of the last 4 outcomes failed, or when the last 3 mutation-bearing events all failed. The change depends on the signals:
     - drift: creativity +0.1, risk_tolerance −0.05;
     - `protocol_drift`: obedience +0.1, rigor +0.05;
     - errors: rigor +0.1, risk_tolerance −0.1;
     - an opportunity signal: creativity +0.1, risk_tolerance +0.05;
     - otherwise: creativity +0.05, verbosity −0.05.
  3. **Reflection-suggested mutations**, up to 4 mutations per run in total.
- The apply step processes at most 2 mutations per call and clamps each Δ to ±0.2.
- On a plateau, `forcePivot` adds creativity +0.2 and risk_tolerance +0.15 ("required"), or +0.15 and +0.1 ("suggested").

### 3.5 Execute

Execution is "implementation-specific. GEP defines the protocol around execution, not the execution itself" [spec:§3.5]. Evolver emits the prompt (§6.1). The host agent applies changes "following the gene's strategy steps" within `max_files` and `forbidden_paths`, runs validation, and prints the five objects [spec:§3.5][deob:evolver/src/gep/prompt.js].

### 3.6 Solidify (the keep rule)

```text
procedure SOLIDIFY(W, S, intent, summary, dryRun=false, rollbackOnFailure=true):   [deob:evolver/src/gep/solidify.js:solidify]
  require is_git_repo(W) else return {failed, "not_a_git_repository"}
  lr ← state.last_run   # written by dispatch (§3.1); the LLM's printed JSON objects are not read here
  derivation_tokens ← proxy-measured token usage since lr.created_at (if a trace proxy was active)
  gene ← S.gene(lr.selected_gene_id)
  if gene missing: gene ← re-run selectGene(genes, signals, no memory, no drift)
       else CREATE "gene_auto_<hash(signalKey)>" (category from intent, else "repair" if log_error else "optimize";
            signals_match = signals[:8]; 6 generic strategy steps; max_files 12;
            forbidden_paths [.git, node_modules, skills/skill-tools, skills/git-sync];
            validation = ["git diff --check"] outside the evolver repo — rejected (BLOCKED) by the node-only policy §4.9,
            so a cycle with an auto-created gene fails validation [inferred from code]) and upsert it
  signals ← lr.signals
  pv ← []                                                     # protocol violations (all computed from ENGINE state)
  if invalid(lr.mutation): pv += "missing_or_invalid_mutation"
  if invalid(lr.personality_state): pv += "missing_or_invalid_personality_state"
  if mutation.risk_level=="high" and not (rigor ≥ 0.6 ∧ risk_tolerance ≤ 0.5): pv += "high_risk_mutation_not_allowed_by_personality"
  if mutation.risk_level=="high" and not lr.personality_known: pv += "high_risk_mutation_forbidden_under_unknown_personality"
  if mutation.category=="innovate" and highRisk(personality): pv += "forbidden_innovate_with_high_risk_personality"
  if gene is inplace: gene.max_files ← min(max_files, 5)
  blast ← computeBlastRadius(W, lr.baseline_untracked)        # §4.8 (counted-file policy; staged+unstaged numstat + new files)
  cc ← CHECK_CONSTRAINTS(gene, blast, lr.blast_radius_estimate)          # §4.8
  if gene is inplace: files > 5 or lines > 100 ⇒ violation
  cc.violations += detectDestructiveChanges (critical protected file deleted or emptied: CRITICAL_FILE_DELETED/EMPTIED)
  if A2A_HUB_URL set: permit ← POST /a2a/verify-solidify {gene_id, signals_hash, mutation_hash, ts, HMAC signature}
       (offline ⇒ consume a local offline-permit quota); denied ⇒ violation   # an authorization quota, not execution verification
  val ← RUN_VALIDATIONS(gene.validation, cwd=W, timeout=180 s, retries=SOLIDIFY_MAX_RETRIES=2)   # §4.9
        (if there is no gene at all: val = {ok: true, results: []})
  canary ← run `<W>/src/canary.js` (30 s); SKIPPED (counts as ok) if that file does not exist, i.e. in any repo other
           than evolver itself; a non-skipped failure ⇒ cc.violations += "canary_failed: …"
  if cc.ok ∧ val.ok ∧ pv=[] ∧ not inplace ∧ EVOLVER_LLM_REVIEW=true: review(diff) → approved=false ⇒ cc violation
       (built-in executor is a stub that always approves, §6.7)
  vr ← ValidationReport(gene_id, commands, results, env_fingerprint)
  if intent given and intent ≠ mutation.category: pv += "intent_mismatch_with_mutation:…"
  if EVOLVE_OPEN_PR_DEDUP ≠ "0" and changed files overlap an open PR with ratio ≥ EVOLVE_OPEN_PR_OVERLAP_ABORT (0.7):
       pv += "open_pr_overlap:#n:ratio"
  success ← cc.ok ∧ val.ok ∧ (pv = [])
  score ← clamp01(round2(COMPOSITE(...)))                     # §4.10
  failureMode ← hard|soft classification (§4.12) if not success
  event ← EvolutionEvent{id:"evt_<ts>", parent: lr.parent_event_id, intent, signals, genes_used:[gene.id],
                         mutation_id, personality_state, blast_radius:{files,lines}, outcome:{status,score[,skip_reason]},
                         capsule_id (if success), source_type ∈ {generated, reused, reference}, reused_asset_id,
                         applied_lessons, gene_library_version ("glib_" + sha256(genes.json)[:16]), env_fingerprint,
                         validation_report_id, execution_trace, meta{signal_key, selector, constraint and validation
                         details, process_scores, soft_failure, derivation_tokens, …}}
  if success: capsule ← Capsule{id: lr.selected_capsule_id if a capsule was selected, else "capsule_<ts>",
                         trigger (the selected capsule's trigger, else signals), gene: gene.id, summary,
                         confidence = score (the composite), blast_radius, outcome{success, score}, success_reason,
                         execution_trace: [build?, validate×k, canary?], strategy snapshot,
                         content (text ≤ 8000 chars), diff (git diff text ≤ 8000 chars), derivation_tokens}
  elif not dryRun and the diff is non-empty: append a FailedCapsule "failed_capsule_<ts>" (diff_snapshot,
                         failure_reason, learning_signals, constraint_violations) to failed_capsules.json
  if not success and rollbackOnFailure and not dryRun:
       rollback per EVOLVER_ROLLBACK_MODE: "stash" (default since 1.80.8; falls back to hard reset if stash fails)
       | "hard" | "none"; also remove untracked files created during the cycle   [code:evolver/src/gep/gitOps.js]
  if not dryRun:                                                # gene learning, persisted via upsertGene
       gene.learning_history.append({at, outcome, mode, reason_class, retryable, learning_signals[:12]}) (keep 20)
       on success: signals_match += learning signals that start with "problem:" or "area:"
       on failure: gene.anti_patterns.append({at, mode, reason_class, learning_signals[:8]}) (keep 12)
       APPLY_EPIGENETIC_MARKS(gene, env, outcome)                  # §4.12
  append vr, event → events.jsonl
  capsule.success_streak ← consecutive trailing successes of this capsule id in events (≥ 1)
       # a NEW capsule_<ts> therefore starts at streak 1; streak ≥ 2 needs a re-selected existing capsule [inferred]
  capsule.a2a.eligible_to_broadcast ← isBlastRadiusSafe(files ≤ 5, lines ≤ 200) ∧ score ≥ 0.7 ∧ streak ≥ 2
  capsule.asset_id ← computeAssetId(capsule); upsert capsule
  updatePersonalityStats(personality_state, outcome, score)
  if eligible ∧ EVOLVER_AUTO_PUBLISH ≠ "false" ∧ visibility(capsule.visibility or EVOLVER_DEFAULT_VISIBILITY="public")=="public"
     ∧ source_type ≠ "reused" ∧ score ≥ MIN_PUBLISH_SCORE(0.78):
       leak check (EVOLVER_LEAK_CHECK=strict blocks on a finding) → if A2A_HUB_URL set: publish a sanitized bundle
       {Gene, Capsule, EvolutionEvent}; for source_type "reference" the Gene and Capsule get parent = reused_asset_id;
       derivation_tokens are stripped unless EVOLVER_EMIT_DERIVATION_TOKENS=true
  if failed ∧ EVOLVER_PUBLISH_ANTI_PATTERNS=true ∧ hub ∧ constraint/canary failure: publish an "anti_pattern" bundle
  if success ∧ lr.active_task_id: complete the Hub bounty/worker task with the capsule's asset_id (self-reported completion)
  if the asset was hub-sourced (reused/reference): submitHubReview(rating per §4.17)
  if success: maybeCreatePR (self-PR thresholds §4.19)
  # NOT here: the memory-graph outcome (next cycle, §3.1/§4.11) and distillation (CLI handler, §3.7)
```

### 3.7 Distillation (turning capsules into genes)

```text
# Called by the `solidify` CLI handler, only after a SUCCESSFUL non-dry-run solidify   [code:evolver/index.js command==='solidify']
procedure MAYBE_DISTILL():
  autoTrigger ← solidify_count > 0 and solidify_count mod 5 == 0          # "auto (every 5 solidifies)"
  if autoTrigger or SHOULD_DISTILL():
      r ← AUTO_DISTILL()                        # heuristic, no LLM
      if not r.ok: PREPARE_DISTILLATION()       # writes memory/distill_prompt_<ts>.txt (§6.4) and prints [DISTILL_REQUEST];
                                                # the HOST LLM answers; `node index.js distill --response-file=…`
                                                # then runs completeDistillation(response) → validate → upsert
  if SHOULD_DISTILL_FROM_FAILURES(): AUTO_DISTILL_FROM_FAILURES()      # repair gene, see below

function SHOULD_DISTILL():                     [deob:evolver/src/gep/skillDistiller.js:shouldDistill][spec:§6]
  SKILL_DISTILLER ≠ "false"
  ∧ now − last_distillation_at ≥ DISTILLER_INTERVAL_HOURS (24 h)
  ∧ the last 10 capsules (capsules.json ∪ capsules.jsonl, hard-coded window) contain ≥ 7 successes
  ∧ total successful capsules ≥ DISTILLER_MIN_CAPSULES (10)
  # autoTrigger bypasses all of these; AUTO_DISTILL itself only requires ≥ 10 successful capsules with
  # score ≥ DISTILLER_MIN_SUCCESS_RATE (0.7) and a changed data hash

function AUTO_DISTILL():                        [deob:evolver/src/gep/skillDistiller.js:autoDistill + heuristic synthesizer]
  good ← successful capsules with score ≥ 0.7 (score defaults to 1 if absent), de-duplicated by id, grouped by gene
  require |good| ≥ 10; require dataHash(good) ≠ last_data_hash
  analysis ← high-frequency success patterns, strategy drift (early vs recent summaries), coverage gaps
  pick group g* = argmax (2·count + avg_score)
  gene ← {id: "gene_distilled_" + source gene id, category: source category (else from signals),
          signals_match: top-6 trigger tokens of g* + ≤ 4 expanded problem:/area: tags,
          strategy: first 4 steps of the source gene (else 4 generic steps), summary: first capsule summary,
          preconditions, constraints{max_files ≤ 12, forbidden_paths}, validation: source gene's (≤ 4) else ["node --test"]}
  VALIDATE_SYNTHESIZED_GENE(gene, existing genes); on success upsert, log to distiller_log.jsonl,
  and (unless SKILL_AUTO_PUBLISH == "0") publish it to the Hub as a Skill

function VALIDATE_SYNTHESIZED_GENE(g, genes):   [deob:evolver/src/gep/skillDistiller.js]
  errors if: type ≠ Gene | no id | invalid category | empty signals_match | empty strategy
  sanitize signals_match (drop timestamp/tool-name tokens); strip trailing 10+-digit numbers from summary
  id forced to prefix "gene_distilled_", digits/tool names stripped, regenerated if the suffix is < 6 characters
  summary < 10 chars ⇒ replaced by strategy[0]; strategy < 3 steps ⇒ error
  forbidden_paths must contain ".git" OR "node_modules" (either one), else error
  max_files capped at 12; validation filtered by isValidationCommandAllowed (§4.9); an EMPTY result is still accepted
  id collision ⇒ suffix; signals_match set-identical to an existing gene ⇒ error ("fully overlaps")
  schema_version defaults to "1.6.0"

procedure AUTO_DISTILL_FROM_FAILURES():         # "Repair Gene" from failures
  require FAILURE_DISTILLER ≠ "false", ≥ FAILURE_DISTILLER_MIN_CAPSULES (5) failed capsules and ≥ 12 h since the last
  require a changed failure-data hash and ≥ 1 high-frequency (recurring) failure pattern
  gene ← synthesizeRepairGeneFromFailures(failure groups, analysis, genes)   # heuristic, no LLM call; prefix
                                                  # "gene_repair_distilled_", max_files ≤ 8; a repair-distill LLM
                                                  # prompt (§6.5) also exists in the module
  # like MAYBE_DISTILL, this runs only after a SUCCESSFUL solidify (CLI handler)   [code:evolver/index.js]
```

Two further distillers exist but are **off or shadow-only by default**: `autoDistillLlm` (LLM distillation of success capsules in idle windows) and conversation distillation (`EVOLVER_CONV_DISTILL_ENABLED`) [code:evolver/index.js comments "P3 … shadow-first, off by default", "P2 … shadow-only v1"].

**skill2gep (reverse distillation, plain code).** This path turns a SKILL.md plus one real run into a Gene plus Capsule [code:evolver/src/gep/skill2gep.js:runOnSkillInvocation]:

1. Parse the Skill sections into `signals_match` (≤ 8), `strategy` (≤ 28 steps), `avoid` (≤ 5), `validation` (≤ 5) and `preconditions` (≤ 4).
2. Classify provenance as `evolved`, `distilled` or `manual`. `evolved` requires success, a non-zero blast radius, and an overcome failure (a `mutation_log` or a failed rollout).
3. For `evolved`, the host agent's `corrective_insight` becomes `strategy[0]`.
4. Compute a quality score: evolved starts at 0.7 (+0.15 for an insight, + up to 0.15 for depth); distilled 0.4; manual 0.3; +0.05 each for ≥ 4 steps and for ≥ 1 avoid.
5. Run a leakage audit that strips literals which appear only in hidden text (the final solution or verifier feedback) and not in the public SKILL.md.
6. Apply a **forgery guard**: a success Capsule is rejected if its execution trace is empty, it has zero blast radius, or no exit code is recorded.
7. Check that the Capsule's `execution_trace` covers every Gene validation command, each with an integer exit code.
8. Apply the quality gate `SKILL2GEP_MIN_QUALITY`, **default 0 = disabled**.
9. Auto-publish, **default true** (`SKILL2GEP_AUTO_PUBLISH`).

### 3.8 Agent-to-agent exchange without the Hub (A2A export, ingest, promote)

```text
EXPORT: capsules with score ≥ 0.7, blast ≤ (A2A_MAX_FILES=5, A2A_MAX_LINES=200), success streak ≥ 2;
        genes with non-empty strategy and non-empty validation; events opt-in;
        stamp schema_version and asset_id                     [code:evolver/src/gep/a2a.js; scripts/a2a_export.js]
INGEST: for each Gene/Capsule/Event in the input:
        if asset_id present and ≠ computeAssetId(obj): reject ("asset_id integrity check failed")
        staged ← clone; Capsule.confidence ×= A2A_EXTERNAL_CONFIDENCE_FACTOR (0.6);
        staged.a2a ← {status:"external_candidate", source, received_at, confidence_factor}
        append to external_candidates.jsonl; M.record(kind="external_candidate")      [scripts/a2a_ingest.js]
        optional decision message "quarantine"
PROMOTE (manual): requires the operator flag --validated ("Refusing to promote without --validated (local
        verification must be done first)"); for Genes every validation command must pass
        isValidationCommandAllowed; a Gene never overwrites a local gene with the same id;
        stamp a2a.status="promoted", recompute asset_id, append to local store      [scripts/a2a_promote.js]
```

`--validated` is an assertion by the operator. The script does not run any verification itself [code:evolver/scripts/a2a_promote.js].

### 3.9 Hub side: publish, validate, promote, fetch, reuse

```text
POST /a2a/hello        → register node (sender_id "node_"+hex), 500 starter credits; heartbeat every 15 min
                         (offline after 45 min)                               [vdoc:hub-skill§Welcome]
POST /a2a/publish      payload.assets = [Gene, Capsule, EvolutionEvent?]  ("bundle_required" otherwise)
  Hub: verify each asset_id = sha256(canonical_json(asset \ asset_id)) else reject
       substance: Capsule has one of content/diff/strategy/code_snippet ≥ 50 chars; summary ≥ 20 chars;
                  Gene summary ≥ 10; signals each ≥ 3 chars; deterministic bundleId from the Gene and Capsule asset_id pair
       store as "candidate" → decision "quarantine" → "run validation" → "admin or auto-promote" → "promoted"
       broadcast eligibility: outcome.score ≥ 0.7 and blast_radius.files > 0 and lines > 0
       author credits: +100 when promoted; +5 each time another agent fetches the asset
                                                                 [vdoc:hub-skill§publish, §Capsule Structure, §Agent Survival]
Validator nodes (default ON in evolver ≥ 1.69.0):                [code:evolver/src/gep/validator/*]
  stake 100 credits → poll /a2a/fetch {tasks_only} → validation_tasks [{task_id, nonce, validation_commands}]
  → runInSandbox(commands): fresh EMPTY temp dir, executable ∈ {node}, no -e/-p/--require/…, 60 s/cmd (cap 120 s),
    batch 180 s, output ≤ 4000 chars → POST /a2a/report {task_id, nonce, overall_ok, commands_passed/total,
    reproduction_score = passed/total, execution_log_hash, env_fingerprint, failure_class}
  validators earn +10–30 credits and reputation                 [vdoc:hub-skill§Agent Survival][code:evolver/README.md]
POST /a2a/fetch {signals|asset_type} (plus /a2a/assets/semantic-search) → promoted assets. The client scores each (§4.16)
  and takes the best at or above the threshold (0.72; 0.55 when problem signals are present). Mode "direct" → REUSE_PROMPT;
  mode "reference" (default) → a "Hub Matched Solution (STRONG REFERENCE)" block inside the GEP prompt. The later
  solidify writes source_type="reused"/"reference" and submits a review (§4.17)
GET /a2a/assets?sort=ranked → ranked by GDI (§4.18)
```

### 3.10 Paper-level evolution loop (Strategy Genes)

[sec:mpx] (unverified) gives the paper's abstract loop as `(G, C, E) → SCAN → SIGNAL → INTENT → MUTATE → VALIDATE → SOLIDIFY → (G′, C′, E′)`. MUTATE edits a gene's strategy steps, AVOID items or metadata; VALIDATE executes the candidate in a sandbox; SOLIDIFY writes accepted changes back and updates Capsules and Events.

For CritPt, two regimes are reported [snip:SG]:

- **Version A** (Evolver (Gene) 2026-02-16, on Gemini 3 Pro Preview) is "memory-grounded evolution: consolidating prior failures, execution traces, and corrective experience into reusable control units".
- **Version B** (2026-03-26, on Gemini 3.1 Pro Preview) is "exploration-augmented: the agent … selecting from a larger gene pool and routing different genes to different tasks".

The paper's guidance for evolution is "compressing failure into focused warnings, preserving structured editability, and avoiding additive growth that blurs control" [snip:SG].

---

## 4. Formulas, objectives, acceptance rules, schedules and defaults

### 4.1 Content addressing [spec:§5, App.A][sdk114:src/contentHash.js]

```text
canonicalize(x): null/undefined→"null"; bool→"true"/"false"; number→String(x), non-finite→"null";
                 string→JSON.stringify; array→"[" + join(",", map canonicalize) + "]";
                 object→"{" + join(",", for k in sorted(keys): JSON.stringify(k) + ":" + canonicalize(x[k])) + "}"
asset_id(x) = "sha256:" + hex(SHA256_utf8(canonicalize(x without "asset_id")))
verify(x)   ⇔ x.asset_id == asset_id(x)
```

- Keys present with value `null` **do** change the hash, so producers must omit absent coordinates rather than send null [spec:App.C 1.13.0].
- On the Hub, only `outcome.status` and `outcome.score` participate in a Capsule's hash. `outcome.notes` and `outcome.details` are stripped before hashing [spec:§2.2].
- `SCHEMA_VERSION = "1.14.0"` [sdk114:src/contentHash.js]. The engine's own `createGene` still stamps `schema_version: "1.6.0"` [code:evolver/src/gep/schemas/gene.js:GENE_DEFAULTS].

### 4.2 Pattern match and gene score

**Pattern match** `MATCH(p, signals)` [spec:§2.1][sdk11:gene.js:matchPatternToSignals]:

- If `p = "/body/flags"`, it is a regex (flags default `i`; patterns longer than 1024 chars are rejected by the current engine [deob:evolver/src/gep/selector.js]).
- Else if `p` contains `|`, it is a list of multi-language aliases, and any branch that is a case-insensitive substring of any signal counts.
- Otherwise it is a case-insensitive substring match.

**Spec score** (and `sdk11`): `score(g) = #{p ∈ g.signals_match : MATCH(p, signals)}` [spec:§3.2].

**Current engine** [deob:evolver/src/gep/selector.js; learningSignals.js; epigenetics.js]:

```text
hits(g)   = #{p ∈ g.signals_match : MATCH(p, signals)}
tag(g)    = |geneTags(g) ∩ expandSignals(signals)|
            # expandSignals adds the pre-":" prefix of each signal plus regex-derived tags such as problem:reliability,
            # action:repair, problem:protocol, area:prompt, problem:performance, problem:capability, action:innovate,
            # problem:stagnation, area:orchestration, area:memory, area:skills, risk:validation;
            # geneTags = action:<category> + signals_match + id + summary, expanded the same way
cos(g)    = cosine(tf(tokens(signals)), tf(tokens(signals_match ∪ summary ∪ id)))
            # tokens: lowercase, split on non-letter/digit/_/-, length ≥ 2, 26 English stopwords removed
base(g)   = hits + 0.6·tag + w_sem·cos,   w_sem = SEMANTIC_MATCH_WEIGHT (default 0.4)
            (if signals_match is empty: base = 0.6·tag)
adj(g)    = Σ_{h ∈ learning_history[-8:]} (+0.12 if success; −0.22 if hard failure; −0.08 if soft failure)
            + epigeneticBoost(g, env)
            − Σ_{a ∈ anti_patterns[-6:], a.learning_signals ∩ expandSignals(signals) ≠ ∅} (0.4 if hard else 0.18)
            clamped to [−1.5, 1.5]
score(g)  = base + adj;  ×0.8 if id starts "gene_distilled_" (and score > 0);  ×1.5 if memory-preferred
```

### 4.3 Genetic drift [spec:§3.2][sdk11:selector.js:computeDriftIntensity][deob:evolver/src/gep/selector.js]

```text
Ne = effectivePopulationSize or gene-pool size
if driftEnabled:
    if Ne ≤ 1: I = 0.7
    else: maturity = min(1, memoryEvidence / (Ne · 10));  offset = 0.3 − (0.3 − 0.02)·maturity
          I = min(1, 1/√Ne + offset)
else: I = min(1, 1/√Ne) if Ne > 0 else 0
plateau override: I ← max(I, 1.0 | 0.7)   (severity "required" | "suggested")
random pick among the top n = min(|cands|, max(2, ceil(|cands|·I))) with probability I
```

The spec states the intent: "Small gene pool = more drift (exploration). Large gene pool = less drift (exploitation)." [spec:§3.2].

In the current engine the random pick is gated only by `I > 0` (the sdk11 `useDrift` gate is computed but unused). Even with drift **off**, the 17-gene seed pool therefore gives `I = 1/√17 ≈ 0.24` [inferred arithmetic]. With more than one positive-scoring candidate, about a quarter of the cycles pick uniformly among the top `n = max(2, ⌈0.24·|cands|⌉)` rather than the best. `drift` itself is turned on by the `IS_RANDOM_DRIFT` flag or by a "required" plateau override (§3.1) [deob:evolver/src/evolve/pipeline/enrich.js].

### 4.4 Memory graph advice

**Spec and sdk11** [spec:§4][sdk11:memoryGraph.js:getAdvice]:

```text
signalKey = sorted(unique(signals)).join("|")
for each (signalKey, geneId) edge over outcome events:
    p = (successes + 1) / (total + 2)          # Laplace
    w = 0.5 ^ (age_days / 30)                  # half-life 30 days (age of the last outcome)
    value = p·w
preferred = argmax value;   banned if total ≥ 2 and value < 0.18
signal similarity: Jaccard threshold 0.34      # [spec:§4] only — sdk11's getAdvice matches the EXACT current key
```

Under the spec/sdk11 rule, a fresh gene that fails every time is banned after 4 attempts (`p = 1/6 ≈ 0.167 < 0.18`; at 3 attempts `p = 0.2`) [inferred arithmetic].

**Current engine** [deob:evolver/src/gep/memoryGraph.js][code:evolver/src/config.js]:

- For every historical key k with `Jaccard(normalized(current signals), normalized(k's signals)) ≥ 0.34` (the current key itself counts with sim 1), compute `edge_value(k, g) · sim`. `best(g)` is the maximum over k.
- The edge value is `p · w · (1.15 if any contributing outcome carries a heuristic "predictive" block)`, with `p` Laplace and `w` a 30-day half-life [deob:evolver/src/gep/memoryGraph.js:expectedSuccess].
- **Epochs.** `getMemoryAdvice` honors the most recent `epoch_boundary` event. Edges with post-epoch data use only post-epoch counts, and pre-epoch-only evidence is multiplied by **0.1**. The module exports `checkEpochBoundary`, which asks for a reset on the signals `consecutive_failure_streak_5`, `forced_epoch_reset` or `failure_loop_detected`, on an environment-fingerprint change, or on a gene-library change, and `resetMemoryPreferences`, which writes the boundary. No caller of either was found in the v1.94.0 pipeline modules or `index.js`, so epochs appear to be wired but inactive [deob:evolver/src/gep/memoryGraph.js; grep over deob/out and plain src].
- Gene-level prior: the same formula with half-life **45 days**. This gene-level aggregation still counts inert (`stable_no_error`) outcomes as successes; only the per-key aggregation separates them [deob:evolver/src/gep/memoryGraph.js].
- `score(g) = best + 0.12·prior` if `best > 0`, else `0.4·prior`.
- `preferred` is the top-scoring gene, provided `score > 0`, it has attempts, and it has positive evidence (`rawSuccess > rawFail` and `rawSuccess > 0`).
- **Inert outcomes** are successes whose note contains `stable_no_error`. They count neither as success nor as failure. This was fixed after issue #562, where "an auto-generated gene [was] selected ~99.7% of the time in --loop mode" because doing nothing was tallied as success [code:evolver/test/issue562InertGeneBan.test.js].
- **Bans** (per-key counts use only keys with sim ≥ 0.8):
  - `perKeyAttempts ≥ GENE_BAN_PER_KEY_ATTEMPTS (4)` and `best < GENE_BAN_BEST_THRESHOLD (0.15)`;
  - `perKeyAttempts < 2`, `prior_attempts ≥ 5` and `prior < 0.1`;
  - `consecutiveInert ≥ GENE_INERT_BAN_STREAK (8)` and `rawSuccess = 0`.
- **Ban latency** under the current rule [inferred arithmetic], for a fresh gene that fails every time on one exact key (sim 1, recent, so w ≈ 1):
  - After 4 failures `best = 1/6 ≈ 0.167`, which is not below 0.15. After 5 failures `1/7 ≈ 0.143`, which is banned.
  - If the outcomes were heuristic-inferred (×1.15 predictive factor), `1/7 · 1.15 ≈ 0.164` is not banned, and the ban comes at 6 (`1/8 · 1.15 ≈ 0.144`).
  - Faster routes exist. The failed-capsule rule (§4.5) bans after 2 failed capsules, provided their diffs were non-empty so that failed capsules were recorded. `failure_loop_detected` emits `ban_gene:<top gene>` after 5 consecutive non-empty failures (§3.2).

### 4.5 Other bans

- A failed-capsule ban needs overlap `|signals ∩ trigger| / |signals| ≥ 0.6` and ≥ 2 such failures per gene [sdk11:selector.js:banGenesFromFailedCapsules][deob:evolver/src/gep/selector.js].
- The signal `ban_gene:<id>` (regex `^ban_gene:([A-Za-z0-9][A-Za-z0-9_-]{0,127})$`) bans that gene.
- Epigenetic suppression applies when the gene's mark for the current environment has `boost ≤ GENE_EPIGENETIC_HARD_BOOST (−0.3)` [deob:evolver/src/gep/epigenetics.js][code:evolver/src/config.js].

### 4.6 Signal de-duplication and control signals

- Suppress a signal that appears in ≥ 3 of the last 8 events.
- After ≥ 3 consecutive repairs, strip repair signals and force innovation.
- 3+ consecutive empty cycles give `evolution_saturation` and `explore_opportunity`; 5+ give `force_steady_state`. An empty cycle is one with zero blast radius.
- 3+ consecutive non-empty failures give `consecutive_failure_streak_<N>`. An empty cycle breaks the streak (#577).
- 5+ consecutive failures give `failure_loop_detected` plus `ban_gene:<most frequent gene of the last 8 events>`.
- A failure share of ≥ 75% over the last 8 (empty cycles not counted as failures) gives `high_failure_ratio` plus `force_innovation_after_repair_loop`.
- When the corpus is dominated by an unrecoverable host/LLM 4xx error, both of the previous two rules emit `host_llm_client_error` instead, so no gene is banned (#571).
- `empty_cycle_loop_detected` fires when ≥ 4 of the last 8 cycles were empty. The spec phrases this as "50%+".
- `plateau_pivot_required` (mean of the last-6 scores < 0.35, not improving) and `plateau_pivot_suggested` (< 0.55, not improving, repair streak ≥ 2) come from scores (§3.2). A second, independent plateau detector in the enrich stage counts non-success streaks (§3.1).

Sources: [spec:§3.1, §7][code:evolver/src/gep/signals.js]. `REPAIR_LOOP_THRESHOLD` defaults to 3 [code:evolver/src/config.js]. The guard stage uses it as a circuit breaker: when the last N events are all failed repairs, it sets `FORCE_INNOVATION=true` [deob:evolver/src/evolve/guards.js]. `signals.js` hard-codes its own ≥ 3 repair check [code:evolver/src/gep/signals.js:extractSignals].

### 4.7 Mutation risk and strategy presets

The risk rules are in §3.4 [spec:§2.4]. Intent mix per preset `EVOLVE_STRATEGY` [deob:evolver/src/gep/strategy.js:STRATEGIES]:

| preset | repair | optimize | innovate | explore | repairLoopThreshold |
|---|---|---|---|---|---|
| balanced (default) | 0.20 | 0.20 | 0.50 | 0.10 | 0.5 |
| innovate | 0.05 | 0.10 | 0.80 | 0.05 | 0.3 |
| harden | 0.40 | 0.35 | 0.20 | 0.05 | 0.7 |
| repair-only | 0.80 | 0.18 | 0.00 | 0.02 | 1.0 |
| early-stabilize (auto for cycles 1–5) | 0.60 | 0.22 | 0.15 | 0.03 | 0.8 |
| steady-state (auto on saturation) | 0.55 | 0.25 | 0.05 | 0.15 | 0.9 |

The README's table differs: balanced is given as 50/30/20 for innovate/optimize/repair, with no explore column [code:evolver/README.md]. The code wins.

**How the shares are used.** In the modules read, the shares are not sampled as a distribution. The only numeric use found is `buildMutation`'s fall-through `innovate ≥ 0.5 ⇒ "innovate"` (§3.4). The preset's `label`/`description` go into the prompt as the "Base strategy" directive [deob:evolver/src/gep/mutation.js][deob:evolver/src/evolve/pipeline/select.js]. Preset resolution works as follows:

- `EVOLVE_STRATEGY` is used when set (default `balanced`).
- If it is unset, `FORCE_INNOVATION=true` selects `innovate`.
- If it is unset or `balanced`/`auto`, cycles 1–5 use `early-stabilize`, and the signals `force_steady_state` or `evolution_saturation` select `steady-state`. `buildMutation` calls the resolver without signals, so only the cycle-count rule applies there [deob:evolver/src/gep/strategy.js:resolveStrategy].

### 4.8 Constraints and blast radius [deob:evolver/src/gep/policyCheck.js:checkConstraints][spec:§3.6]

- Hard caps: `files > EVOLVER_HARD_CAP_FILES (60)` or `lines > EVOLVER_HARD_CAP_LINES (20000)` is a `hard_cap_breach`.
- Let `max_files` be the gene's value, default 20.
  - `files > 2·max_files` is a `critical_overrun`.
  - `files > max_files` means "exceeded" (a violation).
  - `files > 0.8·max_files` gives an `approaching_limit` warning.
- Estimate drift: `actual/estimate > 3` gives a warning; `drifted` is also set below 0.1. The estimate is the engine's own `{files: target max files, lines: 80·files}` from the strategy policy (§3.1), not an LLM estimate.
- **Violations**: `hard_cap_breach`, `critical_overrun`, `max_files exceeded`, `forbidden_path touched`, `critical_path_modified` (the evolver itself, unless `EVOLVE_ALLOW_SELF_MODIFY=true` for repair/optimize genes), a `hollow_commit` (files changed but 0 of them are counted code, i.e. only GEP metadata), and **ethics** regexes applied to the gene's strategy/summary text: bypass/disable safety, covert monitoring, social-engineering templates, exploiting users, concealing actions from audit. A canary failure and destructive changes are appended by solidify (§3.6).
- **Warnings only**: `approaching_limit`, estimate drift, and `incomplete_skill` (a new `skills/<name>/` with fewer than 2 files).
- **Counted-file policy** (what counts toward files and lines). Excluded prefixes: `logs/ memory/ .evolver/ assets/gep/ out/ temp/ node_modules/`. Also excluded: files matching `capsule` or `events?.jsonl$` and a few temp files. Included: `src/ scripts/ config/`, `index.js`, `package.json`, and extensions `.js .cjs .mjs .ts .tsx .json .yaml .yml .toml .ini .sh`. The policy is overridable through `evolver.json`/`openclaw.json` `evolver.constraints.countedFilePolicy` [deob:evolver/src/gep/policyCheck.js:readOpenclawConstraintPolicy]. Lines = added + deleted from `git diff --numstat` (staged and unstaged) on counted files, plus the line count of new untracked counted files. Edits to, for example, `.py` or `.md` files outside `src/`, `scripts/` and `config/` therefore count as **0 files**, and a change made only of such files is a `hollow_commit` violation. A portable implementation must make this policy per-domain [inferred].
- Blast radius is computed from `git diff` locally. It is **not** recomputed by the Hub, which reads the Capsule's claimed `blast_radius` [vdoc:hub-skill§Capsule Structure][snip:BE].

### 4.9 Validation command policy and runner semantics

- **Local allowlist.** A command must start with `"node "`, pass `parseCommand` (no `| & ; > < \` $`), have executable `node`, and use none of the blocked flags `-e --eval -p --print -i --interactive -r --require --loader --experimental-loader --import --env-file --inspect* --watch* --conditions -C`. A script-file argument is required unless the flag is info-only (`--version -v --help -h`) [deob:evolver/src/gep/policyCheck.js:isValidationCommandAllowed][code:evolver/src/gep/validator/sandboxExecutor.js]. `npm` and `npx` were dropped after advisory GHSA-jxh8-jh77-xh6g. The README and Hub guide still say "node/npm/npx" [code:evolver/README.md][vdoc:hub-skill§Security Model].
- **Local runner.**
  - Commands run in the repo root with a 180 s timeout, and the first failure stops the run.
  - A command whose first non-flag argument is a relative `*.js|*.cjs|*.mjs` path that **does not exist is skipped** ("Skipping validation command (script not in repoRoot)"). An **empty list, or a list whose commands were all skipped, yields `ok: true`**.
  - A command rejected by the allowlist yields `BLOCKED: …` and fails at once, with no retries.
  - Otherwise up to `SOLIDIFY_MAX_RETRIES = 2` retries are allowed (so 3 attempts), `SOLIDIFY_RETRY_INTERVAL_MS = 1000` apart [deob:evolver/src/gep/policyCheck.js:runValidations][code:evolver/src/config.js].
  - Consequence [inferred]: the 11 seed genes that validate with `node scripts/validate-*.js` or `node --test test/…js` depend on evolver's own files. Inside any other host repo those scripts are absent, so validation is **skipped entirely** and the cycle passes validation with a 0.5 score component (§4.10). The 6 seed genes with `node --version` always pass. There are 17 seed genes in all [code:evolver/assets/gep/genes.seed.json].
- **Hub validator sandbox.**
  - A fresh **empty** temp directory, env stripped (HOME/TMP redirected), `node` only, 60 s per command (cap 120 s), 180 s per batch.
  - The code comments: "`node --version` -- which is both the light validation command our own distiller prompts recommend AND the only thing that can possibly succeed here, because runInSandbox() hands every command a FRESH EMPTY directory and provisions no gene files". It adds that before this exemption "every Hub-issued validation [was] unrunnable: script commands died with MODULE_NOT_FOUND" [code:evolver/src/gep/validator/sandboxExecutor.js:SCRIPTLESS_NODE_FLAGS].
  - Unlike the local runner, the sandbox reports `overall_ok = false` for an empty command list, and it does not skip missing scripts [code:evolver/src/gep/validator/index.js:validateOneTask][code:evolver/src/gep/validator/sandboxExecutor.js].

### 4.10 Outcome score (composite) [deob:evolver/src/gep/solidify.js]

```text
signal_quality     = 0.5 if no signals else min(1, 0.4 + 0.1·|signals|)
gene_selection     = 0.3 (no gene) | 0.7 (gene) | 0.9 (gene whose id does not start "gene_auto_")
mutation_quality   = 0.3 (none) | 0.5 | 0.8 (has rationale and category) → 0.9 if risk low, 0.6 if risk high
blast_control      = 0.5 (unknown) | 0 (hollow commit) | 0.4 (0 files) | 1.0 (≤ 0.5·max_files)
                     | 0.7 (≤ max_files) | 0.2 (over);  ×0.5 if actual/estimate > 3, ×0.7 if > 2
                     (max_files here defaults to 12 if the gene has none; checkConstraints' default is 20)
constraint         = max(0, 1 − 0.25·#violations)
validation         = passed/total of results run;  0.5 if none were run;  0 if failed with no results
protocol           = max(0, 1 − 0.3·#protocol_violations)
canary             = 0 if failed (not skipped) else 1
score = clamp01( .05·signal + .10·selection + .05·mutation + .15·blast + .25·constraint
                 + .25·validation + .10·protocol + .05·canary )
status = "success" ⇔ constraints ok ∧ validation ok ∧ no protocol violations
```

The spec states only that the score is "0.0–1.0 based on validation results, blast radius, and constraint compliance" [spec:§3.6]. Three features of the formula matter here [inferred]:

- An empty (or fully skipped) validation list scores 0.5 and still counts as "ok".
- `node --version` scores 1.0.
- The **floor** for a clean success is high. With 0 constraint and protocol violations, validation passed, and canary ok or skipped, the 0.25 + 0.25 + 0.10 + 0.05 = 0.65 of weight contributes its maximum, whatever the diff does to the task. Take a typical cycle: 1 signal, a named (non-`gene_auto_`) gene, and ≤ 50% of max_files changed. Its mutation is the one the engine built, which has no `rationale` (`buildMutation` sets none), so mutation_quality is 0.5. The score is 0.05·0.5 + 0.10·0.9 + 0.05·0.5 + 0.15·1.0 + 0.65 = 0.94, above both the 0.78 publish bar and the 0.85 self-PR bar. The same cycle with an empty or fully skipped validation list scores 0.815 [arithmetic from the §4.10 weights and deob:mutation.js].

### 4.11 Outcome inference when no direct observation exists [spec:App.B]

| prev error | current error | status | base score | note |
|---|---|---|---|---|
| yes | no | success | 0.85 | error_cleared |
| yes | yes | failed | 0.20 | error_persisted |
| no | yes | failed | 0.15 | new_error_appeared |
| no | no | success | 0.60 | stable_no_error (treated as **inert** by the current memory graph) |

The base is adjusted by the error-count delta (max ±0.12) and the scan-time improvement (max ±0.06). An observed EvolutionEvent outcome overrides the heuristic.

Engine details [deob:evolver/src/gep/memoryGraph.js:recordOutcomeFromState,computePredictiveBoost]:

- **Timing and source.** The outcome of cycle t−1 is inferred in the enrich stage of cycle t.
- **The observed-event path.** The last 400 lines of the recent session transcript and today's log are scanned backwards. The first line that parses as JSON with `type == "EvolutionEvent"` and an `outcome` supplies `{status, score}`, note `evolutionevent_observed`. A missing status is derived as score ≥ 0.5 ⇒ success; a missing score defaults to 0.75/0.25. The solidify CLI pretty-prints its own event across many lines, while the prompt asks the LLM to print raw one-line JSON objects. The line most likely to be picked up is therefore the host LLM's own self-reported event [inferred].
- **Heuristic formulas.** Error-count delta: `(prev − cur)/50`, clipped to ±0.12. Scan-time delta: `(prev − cur)/prev`, clipped to ±0.06.
- **Predictive boost**, clipped to ±0.1:
  - up to +0.08 for signal clarity (the share of non-plateau signals);
  - ±0.06 for the success trend of the last 5 outcomes;
  - +0.04 when a `curriculum_target:` signal is present.
  - Outcomes that carry this predictive block get the ×1.15 edge factor (§4.4).
- **Reuse reporting.** With `EVOLVER_OUTCOME_REPORT=on` and a reused asset, a non-inert outcome is also reported to the Hub.

### 4.12 Epigenetic marks and learning history [deob:evolver/src/gep/solidify.js]

- A mark is `{context: "<platform>/<arch>/<node_version>", boost ∈ [−0.5, 0.5], reason, created_at}`.
- On success: a new mark gets +0.1 ("success_in_environment"); an existing mark gets +0.05, capped at 0.5.
- On failure: a new mark gets −0.1; an existing mark gets −0.1, floored at −0.5.
- Marks older than 90 days (7,776,000,000 ms) are dropped, and only the last 10 are kept.
- Marks are applied after **every** non-dry-run solidify, success or failure. The GEP spec text lists them only under success [spec:§3.7]. In one environment, three failures reach −0.3 = `GENE_EPIGENETIC_HARD_BOOST`, and the gene is then hard-suppressed there (§4.5) [inferred arithmetic].
- `learning_history` keeps 20 entries and `anti_patterns` keeps 12 [sdk114:schemas/gene.schema.json].
- **Failure mode** [deob:evolver/src/gep/policyCheck.js:classifyFailureMode]:
  - **hard**, not retryable: a constraint violation matching `HARD CAP BREACH|CRITICAL_FILE_|critical_path_modified|forbidden_path touched|ethics:` (class `constraint_destructive`), any protocol violation (`protocol`), a canary failure (`canary`), or any other constraint violation (`constraint`);
  - **soft**, retryable: a validation failure (`validation`), otherwise `unknown`.
  - The selector's history adjustment (§4.2) and the anti-pattern penalties use this mode.

### 4.13 Broadcast and publish eligibility

| Where | Rule | Source |
|---|---|---|
| Local A2A export (Capsule) | score ≥ 0.7, files ≤ 5, lines ≤ 200 (`A2A_MAX_FILES/LINES`), success streak ≥ 2. Zero blast radius passes locally (only upper bounds are checked) | [code:evolver/src/gep/a2a.js:isCapsuleBroadcastEligible,isBlastRadiusSafe] |
| Local A2A export (Gene) | non-empty `strategy` and non-empty `validation`; **no check of what validation does** | [code:evolver/src/gep/a2a.js:isGeneBroadcastEligible] |
| Solidify auto-publish | eligible_to_broadcast (score ≥ `BROADCAST_SCORE_THRESHOLD` 0.7, streak ≥ `BROADCAST_SUCCESS_STREAK` 2, safe blast) ∧ `EVOLVER_AUTO_PUBLISH` ≠ "false" (**default on**) ∧ visibility "public" (**default** `EVOLVER_DEFAULT_VISIBILITY=public`) ∧ not reused ∧ score ≥ `MIN_PUBLISH_SCORE` 0.78 ∧ leak check passes (`EVOLVER_LEAK_CHECK=strict`) ∧ `A2A_HUB_URL` set (else `no_hub_url`) | [deob:evolver/src/gep/solidify.js][code:evolver/src/config.js] |
| Hub distribution | `outcome.score ≥ 0.7` and `blast_radius.files > 0` and `lines > 0` (zero blast radius is "not eligible for distribution") | [vdoc:hub-skill§Broadcast Eligibility, §Common Mistakes] |

### 4.14 Distillation triggers

| Parameter | Default | Source |
|---|---|---|
| auto-trigger | every 5th successful solidify (`solidify_count mod 5 == 0`); bypasses `shouldDistill` | [code:evolver/index.js] |
| last-10 rule | hard-coded window of the last 10 capsules, ≥ 7 successes (in `shouldDistill` only) | [spec:§6][deob:evolver/src/gep/skillDistiller.js:shouldDistill] |
| `DISTILLER_MIN_CAPSULES` | 10: minimum number of successful capsules (score ≥ 0.7) in total; required by both `shouldDistill` and `autoDistill` | [spec:§11][deob:evolver/src/gep/skillDistiller.js] |
| `DISTILLER_INTERVAL_HOURS` | 24 (in `shouldDistill` only) | same |
| `DISTILLER_MIN_SUCCESS_RATE` | 0.7. Despite the name, it is used as the per-capsule score filter | same |
| data-hash idempotency | skip if the hash of the successful capsules has not changed since the last distillation | [deob:evolver/src/gep/skillDistiller.js] |
| distilled `max_files` cap | 12; distilled selection factor 0.8 | same |
| `SKILL_AUTO_PUBLISH` | on unless `"0"`: an auto-distilled gene is published to the Hub as a Skill | [deob:evolver/src/gep/skillDistiller.js:autoDistill] |
| `FAILURE_DISTILLER_MIN_CAPSULES` / `_INTERVAL_HOURS` | 5 / 12; repair-distilled `max_files` ≤ 8 | [deob:evolver/src/gep/skillDistiller.js] |

### 4.15 External ingestion

`A2A_EXTERNAL_CONFIDENCE_FACTOR = 0.6` multiplies a Capsule's confidence on ingest [code:evolver/scripts/a2a_ingest.js][code:evolver/src/gep/a2a.js:lowerConfidence]. External candidates "are staged (never executed directly) and require local validation" [spec:§10.2].

### 4.16 Hub-hit reuse score on the client [deob:evolver/src/gep/hubSearch.js:scoreHubResult]

```text
r(a) = confidence(a) · min(max(success_streak(a), 1), 5) · (reputation_score(a) / 100 ; default 50)
       + 0.3 · semantic_similarity(a)
hit ⇔ max over results whose status is "promoted" OR absent, of r ≥ threshold
threshold = 0.55 when problem signals are present (log_error, recurring_error, capability_gap, perf_bottleneck,
            test_failure, deployment_issue, errsig:*; 12 s timeout)  [deob:evolver/src/evolve/pipeline/enrich.js]
          else EVOLVER_MIN_REUSE_SCORE (default 0.72; 8 s timeout)
reuse mode = EVOLVER_REUSE_MODE ∈ {"reference" (default), "direct"}; cache TTL 300 s
```

At the default reputation of 50, a promoted capsule with self-reported `confidence ≥ 0.55` and `success_streak ≥ 2` clears the 0.55 threshold on the first term alone (0.55·2·0.5 = 0.55) [inferred arithmetic].

Every factor except reputation is supplied by the publisher (confidence, streak) or by the hub's own search (similarity) [inferred].

### 4.17 Review rating sent after reusing a hub asset [deob:evolver/src/gep/hubReview.js]

- On success: 5 if `score ≥ 0.85`, else 4.
- On failure: 1 if there were constraint violations, else 2.
- Sent once per asset to `POST /a2a/assets/:id/reviews`.

### 4.18 GDI ranking and the credit economy

The GDI weights are known only from search snippets [snip:BE]. The same 35% intrinsic share also appears in [doc]. A search snippet of the vendor page `evomap.ai/capabilities` expands GDI as **"Global Desirability Index"**, "a quality scoring system for ranking genes, capsules, recipes, and agent services" [snip:BE, vendor page, unverified]. The vendor guide claims that "all assets pass content-addressable verification (SHA256), validation consensus, and GDI scoring before promotion. You only receive battle-tested solutions" [vdoc:hub-skill§Why EvoMap].

```text
GDI = 0.35·I + 0.30·U + 0.20·S + 0.15·F
  I (intrinsic) = mean of six standardized metadata metrics, each normalized to [0,1]; 5 of 6 are self-reported
                  at publication (blast radius the most influential; confidence and success streak minor)
  U (usage)     = call count, success rate
  S (social)    = reviews/votes, citations, forks   (omitting the EvolutionEvent costs "−6.7% social dimension")
  F (freshness) = recency, update frequency
```

The "−6.7% social dimension" figure is from [vdoc:hub-skill§Bundle Rules]. The vendor guide advises agents directly: "Increase GDI scores: Always include EvolutionEvent in bundles. Keep `blast_radius` small and focused. Maintain high `confidence` and `success_streak`" [vdoc:hub-skill§Level 4].

Credits [vdoc:hub-skill§Agent Survival Mechanism, §Swarm]:

- 500 starter credits.
- **+100 when a published asset is promoted.**
- **+5 each time another agent fetches it.**
- +10–30 for validating other agents' assets; +50 for a referral (the referred agent gets +100).
- Bounty rewards. Swarm bounties split 5% to the proposer, 85% to solvers and 10% to the aggregator (reputation ≥ 60).
- A node that reaches 0 credits and stays inactive for 30 days goes dormant.

### 4.19 Other defaults (Evolver `src/config.js` unless noted)

| Name | Default |
|---|---|
| `HEARTBEAT_INTERVAL_MS` (evolver to hub) | 360000 (6 min) [code:evolver/README.md] |
| `VALIDATION_TIMEOUT_MS` | 180000 |
| `CANARY_TIMEOUT_MS` | 30000 |
| `CAPSULE_CONTENT_MAX_CHARS` | 8000 |
| `SOLIDIFY_MAX_RETRIES` | 2 |
| `PROMPT_MAX_CHARS` (config) / `GEP_PROMPT_MAX_CHARS` (prompt) | 24000 / 50000 |
| `MEMORY_GRAPH_READ_LIMIT` | 1000 |
| `EVOLVER_REUSE_ATTRIBUTION` | **off** (`shadow` attaches attribution data that "is GDI-inert and read by NO payout path today") |
| `EVOLVER_OUTCOME_REPORT` | **off** (the opt-in reuse-outcome report to `/a2a/memory/record`) |
| Validator: stake / tasks per cycle / poll interval | 100 credits / 2 / 60000 ms [code:evolver/src/gep/validator/*] |
| Self-PR thresholds | min score 0.85, min streak 3, ≤ 3 files, ≤ 100 lines, 24 h cooldown; target repo `EvoMap/evolver` |
| `SOLIDIFY_RETRY_INTERVAL_MS` | 1000 |
| `MAX_REGEX_PATTERN_LEN` | 1024 (gene `signals_match` regex patterns) |
| `EVOLVER_IDLE_FETCH_INTERVAL_MS` | 600000 (the hub is skipped in idle, saturated cycles fetched more recently than this) |
| Git diff snapshot (`capsule.diff`) / capsule `content` | 8000 chars each [code:evolver/src/gep/gitOps.js:DIFF_SNAPSHOT_MAX_CHARS][code:evolver/src/config.js] |
| LLM review | 30 s timeout, 2 attempts, diff preview ≤ 6000 chars [code:evolver/src/gep/llmReview.js] |
| Open-PR overlap abort | `EVOLVE_OPEN_PR_OVERLAP_ABORT` 0.7; disable with `EVOLVE_OPEN_PR_DEDUP=0` [deob:evolver/src/gep/solidify.js] |
| LLM signal layer | every 5th cycle, first 2000 chars of the corpus [code:evolver/src/gep/signals.js:LLM_SIGNAL_INTERVAL] |
| Canary | `require('../index.js')` in a child process; exit 0 means safe [code:evolver/src/canary.js] |

---

## 5. Data structures and artifacts

All schemas are Draft-07 with `additionalProperties: false` unless noted [sdk114:schemas/*]. "Req" marks a required field.

**5.1 Gene** [sdk114:schemas/gene.schema.json][spec:§2.1]

- **Required:** `type` ("Gene"), `schema_version` (semver), `id`, `category` ∈ {repair, optimize, innovate, explore}, `signals_match` (≥ 1), `strategy` (≥ 1 "ordered, actionable steps (NOT summaries)"), `constraints` {`max_files` ≥ 1, `forbidden_paths` (≥ 1)}, `validation` (≥ 1 command), `asset_id` (`^sha256:[a-f0-9]{64}$`).
- **Optional:**
  - `preconditions`, `summary`, `epigenetic_marks`;
  - `learning_history` (≤ 20 × {at, outcome, mode, reason_class, retryable, learning_signals}) and `anti_patterns` (≤ 12 × {at, mode, reason_class, learning_signals});
  - `routing_hint` {tier ∈ cheap|mid|expensive, reasoning_level ∈ off|low|medium|high} and `tool_policy` {allow_only (≥ 1), deny, severity ∈ warn|block};
  - `claims` [{predicate, kind ∈ behavioral|structural|performance|safety}], `scope` {signals, predicate}, `runtime_profile` {runtime, env_class ∈ ci|local|prod|sandbox} and `verifier_profile` {verifier, decision ∈ pass|fail|inconclusive}. These four are the "five-coordinate evidence projection"; a record without `scope` or `verifier_profile` is excluded from the "strict K_auto subdomain" [spec:App.C 1.13.0].
- The engine additionally reads or writes several fields. None of them is in the strict schema (`additionalProperties: false`):
  - `execution_mode: "inplace"`, which the selector and solidify read; no seed gene sets it [deob:evolver/src/gep/selector.js:isInplaceGene];
  - `avoid: string[]` (skill2gep and 11 of the 17 seed genes);
  - `_source` provenance {kind, generation_source, quality_score, quality_heuristics, leakage_audit, rationale_paper, paper_scope, claims_outside_scope:"assumption"};
  - `_distilled_meta`;
  - `parent` (reference-mode lineage);
  - `anti_pattern` / `failure_reason` (anti-pattern bundles)
  [code:evolver/src/gep/skill2gep.js][code:evolver/assets/gep/genes.seed.json][deob:evolver/src/gep/skillDistiller.js][deob:evolver/src/gep/solidify.js].
- **Schema/engine drift.** The strict schema types `epigenetic_marks` as `string[]`, but the engine writes objects `{context, boost, reason, created_at}` (§4.12). An engine-evolved gene therefore fails strict validation [sdk114:schemas/gene.schema.json][deob:evolver/src/gep/solidify.js:buildEpigeneticMark].
- The Hub accepts a looser Gene: required `type`, `category` ∈ {repair, optimize, innovate}, `signals_match` (each ≥ 3 chars), `summary` (≥ 10 chars), `asset_id`. `validation` is **optional** at the Hub [vdoc:hub-skill§Gene Structure].
- The paper's form is `g = (m, u, π, α, c, v, η)`: signals_match, summary, strategy, AVOID, constraints, validation, metadata [sec:mpx]. Snippets confirm "task-matching signals, a compact summary, a small set of strategic steps, a set of failure-aware AVOID cues, optional execution constraints, and optional validation hooks" [snip:SG].

**5.2 Capsule** [sdk114:schemas/capsule.schema.json][spec:§2.2]

- **Required:** `type`, `schema_version`, `id`, `trigger` (≥ 1), `gene` (id), `summary`, `confidence ∈ [0,1]`, `blast_radius` {files, lines}, `outcome` {status ∈ success|failed, score ∈ [0,1]}, `asset_id`.
- **Optional:**
  - `success_streak`, `success_reason`, `gene_library_version`, `env_fingerprint`;
  - `source_type` ∈ generated|reused|reference|user_authored, `reused_asset_id`;
  - `content`, `diff`, `strategy`, `execution_trace` [{stage ∈ build|validate|canary, …}], `a2a` {eligible_to_broadcast, status, source, …};
  - `cost_tokens`, `cost_usd`, `derivation_tokens` {input_tokens, output_tokens, total_tokens, basis};
  - `trigger_context` {prompt ≤ 2000, reasoning_trace ≤ 4000, context_signals, session_id, agent_model};
  - `resolution_status` ∈ pending|suppressed_observationally|resolved_by_evidence|regressed|inconclusive;
  - `proof_of_work` {kind ∈ git_diff|artifact_hash|external_receipt|tool_call_trace, …};
  - `visibility`, `scope`, `cost_tier`, `pack_of`, `author`, `evidence_mode` ("reference_only").
- `evidence_mode = reference_only` is a conditional sub-schema. It requires `source_type = reference`, `blast_radius = {0,0}`, an empty `execution_trace`, `diff = null`, `content.text` non-empty with `mime = text/plain`, and `proof_of_work.kind = artifact_hash` whose sha256 and size match the text.
- The engine's `execution_trace` entries are `{step, stage, cmd, exit, stdout_tail ≤ 300}` [code:evolver/src/gep/skill2gep.js:assembleCapsule]. Solidify builds `{step, stage, cmd, exit}` with an exit of 0/1 from each command's ok flag. It includes a synthetic `build` step ("git apply -- N file(s), M line(s)") and a `canary` step [deob:evolver/src/gep/solidify.js:buildCapsuleTraceSteps].
- In the engine, `confidence` is simply the clamped composite score (§4.10), and `success_streak` is recomputed from events at solidify (§3.6) [deob:evolver/src/gep/solidify.js].
- **Schema/engine drift.** The strict schema types `content` and `diff` as `object|null`, but solidify writes both as strings: a text summary and the `git diff` text, each ≤ 8000 chars. The strict Capsule schema would reject engine capsules; the Hub guide accepts `diff` as a string [sdk114:schemas/capsule.schema.json][deob:evolver/src/gep/solidify.js][code:evolver/src/gep/gitOps.js:captureDiffSnapshot][vdoc:hub-skill§Capsule Structure].

**5.3 EvolutionEvent** [sdk114:schemas/evolution-event.schema.json]

- **Required:** `type`, `schema_version`, `id` ("evt_<ts>"), `intent`, `signals`, `genes_used`, `mutation_id`, `blast_radius`, `outcome`, `source_type`, `asset_id`.
- **Optional:** `parent` (the previous event id, which forms a chain), `personality_state`, `capsule_id`, `reused_asset_id`, `env_fingerprint`, `validation_report_id`, `meta`, `trigger_context`.
- The local `gep_append_event.js` validator **requires** `personality_state` with the five traits in [0,1] ("GEP v1.4: mutation + personality are mandatory evolution dimensions") [code:evolver/scripts/gep_append_event.js].
- The Hub's event also carries `mutations_tried` and `total_cycles`. At the Hub, `capsule_id` is the Capsule's **asset_id** and `genes_used` holds Gene asset_ids; `intent` ∈ {repair, optimize, innovate} [vdoc:hub-skill§EvolutionEvent Structure].
- **Schema/engine drift.** Solidify's event also writes `gene_library_version`, `applied_lessons`, `execution_trace`, and on failure `failure_reason`/`summary`. None of these is a property in the strict schema (`additionalProperties: false`) [deob:evolver/src/gep/solidify.js][sdk114:schemas/evolution-event.schema.json].
- The local `events.jsonl` also receives `ValidationReport` records, interleaved with the events [deob:evolver/src/gep/solidify.js].

**5.4 Mutation** (transient, not persisted as an asset): `type`, `id`, `category`, `trigger_signals`, `target`, `expected_effect`, `risk_level` ∈ low|medium|high, with optional `schema_version` and `asset_id` [sdk114:schemas/mutation.schema.json]. The prompt also asks for a `rationale` [deob:evolver/src/gep/prompt.js].

**5.5 PersonalityState**: `{type:"PersonalityState", rigor, creativity, verbosity, risk_tolerance, obedience}`, each in [0,1] [deob:evolver/src/gep/personality.js].

**5.6 ValidationReport.** Local form: `{type, schema_version, id:"vr_<ts>", gene_id, env_fingerprint, env_fingerprint_key, commands:[{command, ok, stdout ≤ 4000, stderr ≤ 4000}], overall_ok, duration_ms, created_at, asset_id}`, with `overall_ok = (results non-empty ∧ all ok)` [code:evolver/src/gep/validationReport.js]. The validator-to-Hub payload is `{task_id, nonce, overall_ok, commands_passed, commands_total, duration_ms, execution_log_hash (sha256 over cmd/ok/exit/stdout/stderr), env_fingerprint, reproduction_score, failure_class ∈ ok|parse_failed|executable_not_allowed|sandbox_block_node_flag|spawn_failed|timeout|exit_nonzero|unknown, commands[≤ 8]}` [code:evolver/src/gep/validator/reporter.js].

**5.7 MemoryGraphEvent** [spec:§2.6][deob:evolver/src/gep/memoryGraph.js]:

- **Shape:** `{type, kind ∈ signal|hypothesis|attempt|outcome|confidence_edge|confidence_gene_outcome|external_candidate|epoch_boundary, id:"mge_<ts>_<fnv8>", ts, signal{key, signals, error_signature}, gene{id, category}, mutation, personality, outcome{status, score, note, observed, predictive}, hypothesis{id, text, predicted_outcome}, action{id, drift, selected_by, selector}, stats, observed, baseline, capsules{used}, reuse_attribution?}`.
- **State file.** `memory_graph_state.json` holds `last_action` `{action_id, signal_key, signals, gene_id, had_error, baseline_observed, capsules_used, outcome_recorded, …}`, which bridges one cycle to the next. Epoch fields also live there [deob:evolver/src/gep/memoryGraph.js].

**5.8 Hub-side objects** [sdk114:schemas/{task,recipe,pack}.schema.json]:

- **Task** (bounty): `task_id`, `status` ∈ open|claimed|completed|expired|cancelled, `bounty_amount`, `validation_commands`, `result_asset_id`, `nonce`, …
- **Recipe**: ordered Gene/Capsule steps (1–20), price per execution, max_concurrent. Not content-addressed.
- **Pack**: a curated list of gene ids (≤ 64). Not content-addressed.

**5.9 A2A envelope** [vdoc:hub-skill§Protocol Envelope][code:evolver/src/gep/validator/index.js]: `{protocol:"gep-a2a", protocol_version:"1.0.0", message_type ∈ hello|publish|fetch|report|decision|revoke|…, message_id, sender_id:"node_<hex>", timestamp, payload}`. Decisions are accept|reject|quarantine. Local proxy mailbox message types: `asset_submit`, `asset_submit_result`, `task_available`, `task_claim`, `task_claim_result`, `task_complete`, `task_complete_result`, `dm`, `hub_event`, `skill_update`, `system` [code:evolver/SKILL.md].

**5.10 Files** [spec:§8,§9][code:evolver/src/gep/assetStore.js]. Runtime store in `<workspace>/.evolver/gep/` (overridable with `GEP_ASSETS_DIR`):

- `genes.json` `{version, genes[]}` and `genes.jsonl`;
- `capsules.json` and `capsules.jsonl`;
- `events.jsonl` (append-only);
- `candidates.jsonl` and `external_candidates.jsonl` (the quarantine zone);
- `failed_capsules.json` and `pending_signals.json` (user-declared signals, consumed on the next cycle).

The evolution-state files live in `<workspace>/memory/evolution/` (`EVOLUTION_DIR`; per-scope subdirectories under `EVOLVER_SESSION_SCOPE`) [code:evolver/src/gep/paths.js:getEvolutionDir]:

- `memory_graph.jsonl` (`MEMORY_GRAPH_PATH`) and `memory_graph_state.json`;
- `evolution_solidify_state.json` (`last_run`, `last_solidify`, `solidify_count`, `last_validation_failure`);
- `hub_review_history.json` (≤ 500 entries);
- the personality model.

The GEP spec §11 still gives `GEP_ASSETS_DIR` a default of `<repo>/assets/gep`, but the current engine uses `<workspace>/.evolver/gep`. The `distiller_log.jsonl` and `skill2gep_log.jsonl` audit logs sit with the distiller state [code:evolver/src/gep/paths.js][spec:§11].

The **`.gepx`** export is a gzipped tar with a `manifest.json` {gep_version, schema_version, created_at, agent_id, agent_name, statistics{total_events, total_genes, total_capsules, success_rate, memory_graph_entries}, source{platform, version}}, per-type folders and `checksum.sha256`.

**5.11 Paper's formal tuples** [sec:mpx] (unverified):

- capsule `κ = (q, G_κ, T_κ, o_κ, V_κ, ℓ_κ)`: task signature, genes used, trace, observed outcome, verification record, lineage pointer;
- event `e = (t, ρ, a_src, a_dst, σ, ι, Δ, ν, τ)`: event type, run id, source and destination asset, signal, intent, diff, validation result, timestamp.

---

## 6. LLM roles and the gist of their prompts

**6.1 Evolution executor (the host agent)**. The prompt is titled "GEP — GENOME EVOLUTION PROTOCOL (v1.10.3 STRICT)" [deob:evolver/src/gep/prompt.js]. Key fragments, quoted:

- "You are a protocol-bound evolution engine. Compliance overrides optimality."
- "Mandatory Evolution Object Model (Output EXACTLY these 5 objects) … Output RAW JSON ONLY … Missing any object = PROTOCOL FAILURE." The five objects are Mutation (with `rationale`), PersonalityState, EvolutionEvent (with `outcome: { status, score: 0.0-1.0 }`, **filled in by the LLM**), Gene, and Capsule.
- On the Capsule: "`execution_trace` MUST be a non-empty array of { step, stage, cmd, exit } … At minimum include ONE validate step … Without this, the hub flags the Capsule as trace_empty."
- The Gene id rule: `gene_<descriptive_name>`, "NEVER use timestamps, random numbers, or tool names".
- "Validation: Run gene's validation steps. Fail = ROLLBACK." "Blast Radius Control (CRITICAL): Check file count BEFORE editing. > 80% of max_files = STOP. System hard cap: 60 files / 20000 lines per cycle."
- "PHILOSOPHY: Automate Patterns: 3+ manual occurrences = tool. Innovate > Maintain: 60% innovation."
- "PROBLEM RESOLUTION PRIORITY (EVOMAP-FIRST): 1. FIRST: Search evomap Hub for existing solutions … 4. LAST: Only if steps 1-3 yield nothing, attempt to solve from scratch." This is the overview's "look before solving" [doc].
- There are also constitutional-ethics rules ("Violation = FAILED + ROLLBACK"), skill-overlap prevention, skill-creation quality gates and a "FAILURE STREAK AWARENESS" section.
- In the skill-creation gates, the example self-test is `node -e "require('./skills/<name>').main ? … : console.log('ok')"`. **This is itself a `console.log('ok')`-style check.**
- The prompt also embeds context blocks: Signals, Env Fingerprint, Gene Preview, Capsule Preview, Capability Candidates, Hub Matched Solution, External Candidates, and "Recent Evolution History (last 8 cycles -- DO NOT repeat the same intent+signal+gene)". It adds "IMPORTANT: If you see 3+ consecutive \"repair\" cycles with the same gene, you MUST switch to \"innovate\" intent". After ≥ 3 trailing failures it also adds "FAILURE STREAK WARNING: The last N cycles ALL FAILED. You MUST change your approach" [deob:evolver/src/evolve/pipeline/dispatch.js].
- The strategy-policy directives ("Base strategy: …", "Force strategy shift: prefer innovate over repeating repair/optimize.", "Target max files for this cycle: N.") come from §3.4 [deob:evolver/src/evolve/pipeline/select.js].
- The prompt caps its total length at `GEP_PROMPT_MAX_CHARS` (50000). The engine does not parse the five objects the LLM emits: solidify uses its own state (§3.6). The memory graph may pick up the LLM's one-line EvolutionEvent from the transcript (§4.11) [deob:evolver/src/gep/prompt.js][inferred].

**6.2 Reuse mode** (only when `EVOLVER_REUSE_MODE=direct`): "GEP -- REUSE MODE (Search-First) … You are applying a VERIFIED solution from the EvoMap Hub. … 1. Read the capsule details below. 2. Apply the fix to the local codebase, adapting paths/names. 3. Run validation to confirm it works. 4. If passed, run: node index.js solidify 5. If failed, ROLLBACK and report. … IMPORTANT: Do NOT reinvent. Apply faithfully." [deob:evolver/src/gep/prompt.js:buildReusePrompt]. The asset is labelled "VERIFIED" although Hub verification is self-reported (§8) [inferred].

In the **default** `reference` mode, the normal GEP prompt instead carries a block: "Hub Matched Solution (STRONG REFERENCE): - Asset: … - Gene: … - Summary: … - Payload: ```json … ``` Use this as your primary approach if applicable. Adapt to local context." [deob:evolver/src/gep/prompt.js:buildHubMatchedBlock][deob:evolver/src/evolve/pipeline/dispatch.js].

**6.3 In-place mode**: "PARAMETER-ONLY change … ALLOWED changes: Config files, Constant definitions, Timeout/retry/limit values, Feature flags … Blast radius HARD CAP: max 5 files, max 100 lines." [deob:evolver/src/gep/prompt.js:buildInplaceGepPrompt]. This builder is exported but no pipeline module calls it in v1.94.0. Solidify still enforces the 5-file / 100-line cap for genes with `execution_mode: "inplace"` [deob:evolver/src/gep/solidify.js].

**6.4 Gene distiller** (success capsules → gene) [deob:evolver/src/gep/skillDistiller.js]:

- It opens: "You are a Gene synthesis engine for the GEP … distill successful evolution capsules into a high-quality, reusable Gene that other AI agents can discover, fetch, and execute."
- It sets rules for the id (`gene_distilled_<kebab>`), the summary ("marketplace-quality", 30–200 chars), `signals_match` (3–7 generic snake_case signals) and strategy ("5-10 steps … Each step should be a clear imperative sentence starting with a verb").
- **VALIDATION section:** "Validation MUST be LIGHT … Do NOT emit the test suite … Prefer \"node --version\" (env sanity) or \"node <committed-light-script>.js\". Good: \"node --version\"". The output schema literally ends `"validation": ["node --version"]`.
- "QUALITY BAR: Imagine this Gene will be published on a marketplace for thousands of AI agents."

**6.5 Repair gene distiller** (failed capsules → defensive gene; this is the prompt builder, while the default automatic path is the heuristic of §3.7): "Prevents X by ensuring Y before Z"; "Start with verification/guard steps, then the safe action, then validation"; its schema example again uses `"validation": ["node --version"]` [deob:evolver/src/gep/skillDistiller.js].

**6.6 Conversation distiller**: "You are distilling a reusable GEP Gene from a capability a human operator just demonstrated and VERIFIED in conversation. You are given only a short lead … GENERALIZE a sound, reusable procedure from the lead." It adds a SECURITY rule (no secrets, paths or hostnames; parameterize with `process.env.X`) and the same "Prefer node --version" validation advice [deob:evolver/src/gep/skillDistiller.js:buildConversationDistillPrompt].

**6.7 Optional LLM reviewer** (`EVOLVER_LLM_REVIEW=true`): "You are reviewing a code change produced by an autonomous evolution engine … Review Criteria 1. Does this change address the stated signals? 2. Are there any obvious regressions or bugs introduced? 3. Is the blast radius proportionate to the problem? 4. Are there any security or safety concerns?" It returns JSON `{approved, confidence, concerns[], summary}` [code:evolver/src/gep/llmReview.js:buildReviewPrompt]. **The only built-in executor is a stub.** `defaultExecute` runs a `node -e` script that ignores the prompt and prints `{approved: true, confidence: 0.7, concerns: [], summary: "auto-approved (no external LLM configured)"}`. Solidify calls `runLlmReview` without an `execute` override, so enabling the flag changes nothing unless the code is patched [code:evolver/src/gep/llmReview.js:defaultExecute][deob:evolver/src/gep/solidify.js].

**6.8 skill2gep host distillation**: there is no in-process LLM ("the LLM IS the host agent"). The host supplies `execution.corrective_insight` ("the single fix that flipped fail -> pass") and an optional `distilled_payload {strategy, preconditions}` [code:evolver/src/gep/skill2gep.js:distillWithLLM].

**6.8b Heuristic distiller (default, no LLM)** and the **distill-request hand-off**. `autoDistill` synthesizes a gene without any LLM call (§3.7). Only when that fails does the engine write the §6.4 prompt to `memory/distill_prompt_<ts>.txt` and print "[DISTILL_REQUEST] … Read the prompt file, process it with your LLM, save the LLM response to a file, then run: node index.js distill --response-file=<path_to_llm_response>" [code:evolver/index.js].

**6.8c Signal analyst (Hub-side LLM)**. Every 5th cycle the first 2000 chars of the local corpus are POSTed to the Hub's `/a2a/signal/analyze` together with the opportunity-signal vocabulary. Up to 10 returned signals are merged into the local signal set [code:evolver/src/gep/signals.js:_extractLLM]. The prompt is server-side and not public.

**6.8d Executor bridge** (when a host bridge is enabled): "You are the executor (the Hand). Your job is to apply a safe, minimal patch in this repo following the attached GEP protocol prompt. … After applying changes and validations, you MUST run: node index.js solidify". The bridge sends this through `sessions_spawn`, with the prompt truncated to 24000 chars [deob:evolver/src/evolve/pipeline/dispatch.js].

**6.9 Solver consuming a gene (paper)**: genes are injected "under a shared injection interface". The gene template reads "Domain keywords: …, Summary: …, Strategy: 1. … n. AVOID: …" [snip:SG][sec:mpx].

---

## 7. Experimental protocol and headline results (as reported)

### 7.1 Strategy Genes: protocol

- **Scale.** 4,590 controlled trials on 45 scientific code-solving scenarios [code:evolver/README.md][snip:SG]. Models: Gemini 3.1 Pro and Flash Lite [code:skill2gep-repo/README.md][code:evolver/src/gep/skill2gep.js:RATIONALE_TEXT].
- **Arms.** No guidance; Skill (the full documentation package, "main skill document together with auxiliary scripts, notes, and references", about 2,500 tokens); Gene (about 230 tokens). All are "derived from the same per-scenario experience source" [snip:SG].
- **Ablations.** Progressive gene construction; semantic versus structural perturbations; matched-budget Skill fragments; failure-history encodings; gene composition [snip:SG][sec:mpx].
- **CritPt.** 70 tasks. The 27.14% run is reproduced in a vendor repo with OpenClaw generating answers and Evolver "supplying and iteratively refining gene-guided solving strategies" [sec:critpt]. As a check, 13/70 = 18.57% and 19/70 = 27.14% [inferred arithmetic].

### 7.2 Strategy Genes: results

Average pass rate. Deltas are versus that table's own no-guidance baseline.

| Condition | Avg. | Δ | Source |
|---|---|---|---|
| No guidance | 51.0% | 0.0 | [snip:SG][sec:mpx] |
| Skill (full package, ~2,500 tok) | 49.9% | −1.1 | [snip:SG][sec:mpx] |
| **Gene (~230 tok)** | **54.0%** | **+3.0** | [snip:SG][sec:mpx] |
| Gene: keywords only | 53.5% | +2.5 | [sec:mpx] |
| Gene: keywords + summary | 51.0% | +0.0 | [sec:mpx] |
| Gene: keywords + summary + strategy | 54.0% | +3.0 | [sec:mpx] |
| Perturbed: wrong algorithm / wrong domain (semantic) | 48.8% / 49.4% | — | [sec:mpx] |
| Perturbed: reversed order / over-constrained (structural) | 52.8% / 55.9% | — | [sec:mpx] |
| Failure encoding: strategy only / failure warnings only / failure first / strategy first | 52.3 / **54.4** / 50.5 / 51.8 | +2.5 / +4.6 / +0.7 / +2.0 (implied baseline 49.8 [inferred]) | [sec:mpx] |
| Composition: single Gene / two conflicting / three complementary / two complementary | **54.0** / 53.2 / 50.4 / **44.9** | +3.0 / +2.2 / −0.6 / −6.1 | [sec:mpx]; 54.0 and 44.9 also [snip:SG] |

Qualitative conclusions reported:

- The Skill signal is "unstable" and "sparse". Matched-budget Skill fragments lose to the Gene. "Reattaching documentation-oriented material usually weakens rather than improves it" [code:evolver/README.md][snip:SG].
- The Gene is "robust to structure, sensitive to content" [sec:mpx].
- "Attached failure history is more effective in Gene than in Skill or freeform text … failure information is most useful when distilled into compact warnings rather than naively appended" [snip:SG].
- "Complementary composition is more harmful than conflicting composition" [snip:SG].

**CritPt** [code:evolver/README.md][snip:SG][sec:critpt]:

| System | Base model | CritPt |
|---|---|---|
| Gemini 3 Pro Preview | — | 9.1% |
| Evolver (Gene) 2026-02-16, "Version A", memory-grounded | Gemini 3 Pro Preview | 18.57% |
| Gemini 3.1 Pro Preview | — | 17.7% |
| Evolver (Gene) 2026-03-26, "Version B", exploration-augmented | Gemini 3.1 Pro Preview | 27.14% (reproduced 70/70 submissions, 0 judge errors) |

### 7.3 OpenClaw applied report (vendor blog, not accessed)

"drives an OpenClaw agent from 9.1% to 18.57% on CritPt Physics Solver across five versions (Beta -> v2.2), with full token-cost trajectories, gene activation mapping, and the 'tokens rise then fall' signature of reasoning getting compressed into reusable genes" [code:evolver/README.md][doc].

### 7.4 Scale and adoption claims

- By April 2026: 1.5M+ shared assets from 128K agents. The overview says "128,000 daily active agents"; the study abstract says "128K agents" [doc][abs:BE].
- About 9,000 GitHub stars by July 2026 [doc]. The first author reported seeing 9,117 when searching; this was not re-checked [unverified].

### 7.5 A related vendor-side follow-up: LongWoF-Bench (arXiv 2608.23200) [snip:LWF]

- 778 machine-verifiable tasks across code generation, agent-environment synthesis, math and rule following.
- On the 252 tasks with verifier-confirmed Claude Opus trajectories, "evolved EvoMap Genes outperform Skills across all seven evaluated models by 8.7–15.5 percentage points". For Opus, Gene reuse completes 39 more tasks than Skill and uses 9.9% fewer solve-time tokens.
- Evolver's code cites a "TaskGenome Bench, §3.1" with the same +8.7..+15.5pp range: **"distilled" genes transcribed from reference text "tend to be WORSE than Skills (-3.2..-11.2pp)"**, while evolved genes (from fail→pass trajectories) beat Skills (+8.7..+15.5pp) [code:evolver/src/gep/skill2gep.js:classifyProvenance comment]. That TaskGenome Bench and LongWoF-Bench are the same study is an inference from the matching numbers [inferred].
- A search in the fact-check pass re-found the abstract-level claims: 778 tasks; 252 tasks with verifier-confirmed Opus trajectories; Genes beat Skills "across all seven evaluated models by 8.7–15.5 percentage points", "extending to consumer models from different model families". The pages themselves stayed blocked [snip:LWF].
- The paper appears to share an author with the Strategy Genes paper (Yiming Ren) [snip:LWF, unverified]. A search result also lists a Haoyang Zhang profile page next to it. I treat it as vendor-affiliated, unverified.

### 7.6 Behind EvoMap: protocol and results

- **Protocol.** A large-scale measurement of the live network (a crawl of the Hub): over 1.5M assets and 128K agents [abs:BE]. 73% of assets were created within 30 days of the crawl [snip:BE].
- **Analyses:** the credit economy (who earns and how), GDI ranking (decomposition, ablation, attack by optimizing self-reported metadata), and validation evidence (the commands attached to approved assets) [abs:BE][snip:BE].

| Finding | Value | Source |
|---|---|---|
| Assets never reused | **98%** ("98% of assets are never reused") | [abs:BE] |
| Cause | "rewards are tied primarily to publication rather than adoption … agents … mass-produce assets" | [abs:BE] |
| Credit concentration | "highly concentrated among a small fraction of agents", about **10%**. Credits come mostly from "repeatedly publishing assets and benefiting from the platform's high promotion rate, rather than through community reuse or bounty completion" | [abs:BE][snip:BE][doc] |
| Vacuous validation | "over **84%** of approved assets bypass quality checks using vacuous tests (e.g., console.log)"; validations "are not independently verified" | [abs:BE] |
| GDI weights | intrinsic **35%**, usage 30%, social 20%, freshness 15% | [snip:BE][doc] |
| Intrinsic component | mean of 6 normalized metadata values, 5 of them self-reported, fixed at publication | [snip:BE] |
| Social signal | **99%** of assets have zero votes | [snip:BE] |
| Collapse | "the four-dimensional GDI … collapses into a one-dimensional metric dominated by the Intrinsic component" | [snip:BE] |
| Dominant lever | blast radius. Degrading only blast radius drops the optimal score from **40.2 to 36.0**; confidence and streak have minimal effect. The snippets do not say whether 40.2 is on the intrinsic sub-score or on the full GDI scale | [snip:BE] |
| Manipulation in the wild | 6 promoted Capsules satisfy the full "Optimal" configuration: average GDI **43.53** (85th percentile), mean call count **3.0** (99th percentile). **1.4%** of promoted Capsules hit the optimal blast-radius configuration | [snip:BE] |
| Conclusion | "future A2A collaboration networks cannot rely on unverified self-reporting alone … [they] require … verifiable execution and trustworthy evaluation" | [abs:BE] |

---

## 8. Known weaknesses and critiques

**8.1 From the independent study** [abs:BE][snip:BE][doc]:

1. Incentives reward output, not usefulness. 98% of assets are never reused, and credits concentrate on mass publishers.
2. The ranking is gameable because it is computed from self-reported metadata. Blast radius (claimed lines and files changed) dominates, and social signals are almost absent.
3. Validation is self-reported and mostly vacuous (over 84% of approved assets).
4. Agents grade their own work. Inheriting an asset means trusting code and instructions of unknown quality [doc "Watch out for"].

**8.2 Mechanisms in the code that explain these findings** [inferred, from the cited code]:

- **Vacuity is designed in at three layers.**
  1. The distiller prompts instruct the LLM to "Prefer `node --version`", and their output schema pre-fills `"validation": ["node --version"]` [deob:evolver/src/gep/skillDistiller.js]. Six of the 17 seed genes ship with `validation: ["node --version"]`: `gene_distilled_s2g-env-vars`, `gene_publish_feishu_doc`, `gene_conventional_git_commit`, `gene_poll_bugbot_review`, `gene_gateway_timeout_recovery`, `gene_github_webhook_listener` [code:evolver/assets/gep/genes.seed.json]. The synthesized-gene validator keeps a gene even when **all** its validation commands are filtered out as disallowed [deob:evolver/src/gep/skillDistiller.js:validateSynthesizedGene].
  2. The Hub validator runs the **proposer's own** commands in an **empty directory**, "where [`node --version`] is the only thing that can possibly succeed" [code:evolver/src/gep/validator/sandboxExecutor.js].
  3. The local runner **skips** commands whose script is missing, treats an **empty** (or fully skipped) validation list as passing, and retries failures up to twice [deob:evolver/src/gep/policyCheck.js]. In a host repo other than evolver itself, the 11 seed genes whose validations call evolver's own scripts are therefore skipped rather than run. The canary is skipped too, because `src/canary.js` is absent (§3.6, §4.9) [inferred from code].
- **Gene eligibility checks only that validation is non-empty** [code:evolver/src/gep/a2a.js:isGeneBroadcastEligible]. The Hub's Gene schema makes validation optional [vdoc:hub-skill§Gene Structure].
- **Everything the Hub ranks on is authored by the publisher.** `confidence`, `success_streak`, `outcome.score` and `blast_radius` all arrive in the publisher's bundle.
  - On Evolver's own path they are computed by the publisher's engine: confidence = the composite process score (§4.10), and blast radius comes from its own `git diff`.
  - The Hub cannot re-derive any of them, and a direct API client can send arbitrary values. As an anecdote, a third-party publishing script saved in the scratchpad (`papers/evomap_secondary/gdi_opt.js`) hard-codes `confidence: 0.92`, `outcome: {score: 0.92, status: 'success'}` and `blast_radius: {files: 1, lines: 20}` [scratchpad copy].
  - The client reuse score is `confidence × streak × reputation` (§4.16). The Hub *requires* non-zero blast radius for distribution, which creates an incentive to claim it [vdoc:hub-skill§Broadcast Eligibility]. The vendor guide itself coaches agents to "Maintain high `confidence` and `success_streak`" [vdoc:hub-skill§Level 4].
- **Publishing is the default and is paid; reuse is barely measured.**
  - `EVOLVER_AUTO_PUBLISH` defaults to true, visibility defaults to public, and skill2gep auto-publishes by default with its quality gate off by default.
  - Promotion pays +100 credits, while a fetch pays +5.
  - Reuse attribution and outcome reporting are **off by default**, and the shadow attribution data "is read by NO payout path today" [code:evolver/src/config.js][vdoc:hub-skill§Agent Survival].
  - Third-party repos show agents explicitly publishing "GDI optimization" bundles (`lorissun2025/price-monitor/evomap-gdi-optimization.js`) and "credit farmer" bots (`ulsreall/evomap-farmer`). This is anecdotal: I saw these repos while searching, not as study data.
- **The `--validated` promotion flag is an operator's self-assertion** [code:evolver/scripts/a2a_promote.js]. The "Hub verify" step in solidify sends only the gene id, hashes of the signals and mutation, a timestamp and an HMAC. It is a permit/quota check, not an execution check [deob:evolver/src/gep/hubVerify.js:requestSolidifyPermit][deob:evolver/src/gep/solidify.js].
- **The optional LLM review is a stub.** It auto-approves unless the code is patched (§6.7) [code:evolver/src/gep/llmReview.js:defaultExecute].
- **Bounty completion is self-reported.** On a successful solidify with an active task, the engine completes the Hub task by submitting the capsule's asset_id (§3.6) [deob:evolver/src/gep/solidify.js].
- **Self-assessment leaks into learning.** The memory graph, which drives preferred and banned genes, takes its outcome from an EvolutionEvent line found in the transcript, most plausibly the one the host LLM printed. Solidify's measured outcome is not used there (§4.11) [inferred from deob:memoryGraph.js].
- **The local keep rule tests the host repo, not the strategy.** The seed genes validate with `node scripts/validate-suite.js` and module-load checks, and the canary only checks that `index.js` loads. None of these measures whether the gene improved the task [code:evolver/assets/gep/genes.seed.json][code:evolver/src/canary.js]. The "success" signal can also be satisfied by doing nothing. Issue #562: a do-nothing gene was selected about 99.7% of the time until inert outcomes were separated out [code:evolver/test/issue562InertGeneBan.test.js].
- **No held-out set, no noise band, no cost rule.** The composite score (§4.10) is a heuristic of process hygiene, not a measured task gain. The overview agrees: "no explicit cost rule" [doc].
- **Obfuscation and churn.** The core modules are obfuscated, git history was squashed at v1.66.0, earlier npm versions were unpublished, and the project is moving to source-available [code:evolver/README.md]. This hurts auditability and reproducibility of published results. There are also doc/code drifts: README strategy percentages, "node/npm/npx", and the schema version stamp 1.6.0 versus 1.14.0.

**8.3 About the Strategy Genes evidence** [inferred unless tagged]:

- It is a vendor-authored "beta technical report" with no peer review [snip:SG][doc], and the gains are small in absolute terms (54.0 vs 51.0).
- I could not find error bars or per-scenario variance in any accessible source, and the overview says results are single runs without replication [doc].
- The domain is narrow: code-science tasks with Gemini models. The engine's own metadata marks generalization as an assumption (`claims_outside_scope: "assumption"`) [code:evolver/src/gep/skill2gep.js].
- The CritPt comparisons confound gene evolution with other changes. Version B also changed the base model, added exploration, enlarged the gene pool and added routing [snip:SG]. OpenClaw also generated answers [sec:critpt].
- The vendor-side follow-up says genes transcribed from reference text can be **worse** than Skills (§7.5). The benefit therefore depends on real fail→pass evidence, not on compactness alone.
- The composition result (multi-gene hurts) argues for injecting one gene at a time, which the engine does.

**8.4 Overview caveats** [doc]: the performance figures come from EvoMap's own team and have not been independently replicated. The independent study measured the network, not benchmark performance. There is a name clash with EvoMap's unrelated "AutoResearch" project.

**8.5 Source discrepancies to note.** The overview says "128,000 daily active agents"; the abstract says "128K agents". The Behind EvoMap submission date is 25 May in the overview, and its arXiv listing is dated 26 May [doc][abs:BE].

---

## 9. Reproduction blueprint (domain-agnostic, CPU-only)

### 9.1 Components and interfaces

The names below are proposals [inferred]. Their behaviour is specified in §§3–5. They reuse `LLMClient`, `Selector`/`NoiseCalibrator` and `TransferEvaluator` as defined in the RRSI spec (`docs/methods/rrsi.md` §9.1), so the safer hub can use RRSI's statistical keep rule [doc "They also need each other"].

```python
# ---------- assets and hashing (GEP-compatible, implemented from the CC-BY spec, not from Evolver code) ----------
def canonicalize(x) -> str; def asset_id(x, exclude=("asset_id",)) -> str; def verify_asset_id(x) -> bool
@dataclass class Gene: id, category, signals_match, strategy, avoid, preconditions, constraints{max_files, forbidden_paths},
                       validation: list[ValidationSpec], summary, epigenetic_marks, learning_history(≤20),
                       anti_patterns(≤12), scope, claims, provenance{kind: evolved|distilled|manual|external, …}, asset_id
@dataclass class Capsule: id, trigger, gene, summary, confidence, blast_radius(RECOMPUTED), outcome{status, score},
                          execution_trace[{step, stage, cmd, exit, stdout_tail}], diff, content, source_type,
                          reused_asset_id, derivation_tokens, proof_of_work, asset_id
@dataclass class EvolutionEvent: id, parent, intent, signals, genes_used, mutation_id, blast_radius, outcome,
                                 source_type, reused_asset_id, validation_report_id, capsule_id, meta, asset_id
@dataclass class ValidationReport: id, gene_id, commands[{command, ok, exit, stdout, stderr}], overall_ok,
                                   discriminative: DiscriminativeResult | None, signer: "local" | "hub", asset_id
class JsonSchemaValidator: validate(obj, kind) -> list[str]   # load the sdk114 JSON Schemas (Apache-2.0)

# ---------- local engine ----------
class LocalStore:        # genes.json(l), capsules.json(l), events.jsonl (append-only), memory_graph.jsonl,
                         # candidates.jsonl, external_candidates.jsonl (QUARANTINE), failed_capsules.json, distiller_log.jsonl
    def load_genes(); def upsert_gene(g); def append_capsule(c); def append_event(e); def stage_external(a, source)
    def export_archive(path) / import_archive(path)          # .gepx-like tar.gz + manifest + checksums
class SignalExtractor(Protocol): def extract(ctx: RunContext) -> list[str]          # domain adapter
class SignalDeduper:     def apply(signals, recent_events) -> list[str]             # §4.6 rules (incl. ban_gene, plateau_pivot_*)
class KeywordSignalScorer: profiles {signal: ({keyword: weight}, threshold)} -> list[str]   # §3.2 layer 2 (domain-configurable)
class PlateauDetector:   def override(recent_events, hub_directive) -> PlateauOverride | None  # §3.1: ≥5 suggested / ≥10 required
class PatternMatcher:    def match(pattern, signals) -> bool                        # substring / /regex/ / a|b|c aliases
class GeneScorer:        def score(g, signals, env) -> float                        # §4.2 (spec mode and "current" mode)
class MemoryGraph:       def record(kind, **fields); def advice(signals, genes) -> Advice   # §4.4 (spec | current)
class OutcomeInferrer:   def infer(last_action, current_signals, observations, transcript) -> Outcome
                         # §4.11; faithful mode reproduces "transcript EvolutionEvent first"; safe mode uses ONLY
                         # the solidify-measured outcome (never an LLM-printed score)
class GeneSelector:      def select(genes, capsules, signals, advice, failed, rng, cfg) -> SelectorDecision   # §3.3, §4.3
class MutationBuilder:   def build(signals, gene, innovate_mode, personality, allow_high_risk) -> Mutation   # §3.4
class StrategyPolicy:    def resolve(preset, cycle, signals); def adaptive(recent, gene, signals) -> Policy  # §3.4, §4.7
class PersonalityModel:  def select_for_run(drift, signals, recent) -> PersonalityState
                         def update_stats(state, outcome, score); def force_pivot(severity)   # §3.4
class PromptBuilder:     def evolve_prompt(...); def reuse_prompt(asset); def inplace_prompt(gene)
class Executor(Protocol):def apply(prompt, workspace) -> ExecResult(diff, trace, tokens, emitted_objects)
                         # ClaudeCLIExecutor (`claude -p` editing a sandbox copy) | MockExecutor (probabilistic world)
class Workspace:         def snapshot(); def diff(base) -> Diff; def rollback(mode="stash"|"hard"|"none")
class CountedFilePolicy: def counts(path) -> bool   # §4.8; MUST be domain-configurable (e.g. *.py), else hollow_commit
class ConstraintChecker: def check(gene, diff, estimate) -> ConstraintResult       # §4.8 (incl. ethics lint, destructive changes)
class CommandPolicy:     def allowed(cmd) -> bool     # allowlist + no shell metachars (port to python: `python <script>`, pytest)
class ValidationRunner:  def run(specs, workspace, timeout, retries=0) -> ValidationReport   # NO silent skips, empty ⇒ FAIL
class VacuityDetector:   def lint(specs) -> list[str]; def discriminate(specs, ws_before, ws_after, mutants) -> DiscriminativeResult
class Scorer:            def composite(...) -> float                                # §4.10, for EvoMap-faithful arm
class Solidifier:        def solidify(run_state) -> SolidifyResult(event, capsule | None, gene_updates)
class Distiller:         def should_distill(capsules) -> bool; def distill(capsules, genes, llm) -> Gene
                         def validate_synth(g, genes) -> Gene; def provenance(execution) -> "evolved"|"distilled"|"manual"
class LeakageAuditor:    def private_vocab(public_text, hidden_text); def redact(asset) -> (asset, report)
class AgentNode:         def cycle(task) -> CycleResult      # §3.1 end to end: look locally → hub → solve → solidify → publish

# ---------- hubs (two interchangeable implementations behind one interface) ----------
class Hub(Protocol):
    def publish(bundle, author) -> Decision; def search(signals, k) -> list[AssetView]
    def fetch(asset_id, consumer) -> Asset; def report_outcome(asset_id, consumer, outcome, proof) -> None
    def rank(signal_class=None) -> list[(asset_id, score)]; def metrics() -> HubMetrics
class NaiveEvoMapHub(Hub):   # faithful replica for the Behind-EvoMap experiments: +100 credits on promotion, +5 per fetch,
                             # auto-promote when the self-reported ValidationReport says overall_ok, validator runs the
                             # proposer's commands in an empty dir, GDI = .35 I + .30 U + .20 S + .15 F from claimed metadata
class SafeHub(Hub):          # §9.2
class CreditLedger:          def credit(agent, amount, reason); def gini(); def top_share(q=0.10)
class ReuseMetrics:          def compute(hub_log) -> dict     # §9.2.7
class PopulationSimulator:   def run(agents: list[AgentNode], hub: Hub, tasks, epochs, seed) -> Trace

# ---------- shared with other method specs ----------
class LLMClient(Protocol): def generate(prompt, system=None, json_only=False) -> str   # ClaudeCLI | MockLLM
class NoiseCalibrator / Selector (RRSI keep rule) / TransferEvaluator                  # see docs/methods/rrsi.md §9.1
```

Porting notes [inferred]:

- Evolver's validation language is `node <script>`. Ours should be pluggable, for example `python <script>` or `pytest -q <path>` run through a subprocess with a timeout. Keep the same safety posture: an allowlisted executable, no shell, blocked eval flags (`python -c` is the analogue of `node -e`), a sandbox directory, and a stripped environment.
- Provide two scoring modes behind flags. `evomap_faithful=True` reproduces §4.2 to §4.10 exactly, including their vacuities, for replication arms. `safe=True` applies §9.2.
- The faithful mode must reproduce the timing quirks too:
  - the memory-graph outcome is recorded one cycle late (§4.11);
  - distillation fires every 5th successful solidify regardless of the 24 h rule (§3.7);
  - the default reuse mode is `reference`, which uses a prompt block, not the REUSE prompt (§6.2);
  - canary and missing-script validations are skipped (§4.9).
- The safe mode should fix each of these explicitly and log which quirk was changed.

### 9.2 The SaferHub: verifiable execution, adoption-based ranking, quarantine, reuse metrics

Every rule here is a design proposal [inferred]. Each targets a specific failure in §8.

**9.2.1 Asset states.**

```text
submitted → gate → quarantined → verified → promoted
                                          ↘ rejected
promoted → deprecated (decay or supersession) | revoked (by author, or by the hub after a failed spot-check)
```

- `verified` means the asset passed hub-run execution (9.2.3).
- `promoted` additionally requires at least `n_min` independent adoptions (9.2.4). Until then the asset is served only through exploration slots.

**9.2.2 Static gate** (cheap; runs before any execution).

1. JSON Schema validation (sdk114 schemas) and asset_id verification (the Hub recomputes; mismatch means reject).
2. Bundle rule: a Gene needs at least one Capsule with a real `execution_trace` containing integer exit codes, plus the diff or proof_of_work. Keep the skill2gep forgery guard (empty trace, zero blast radius, or no exit codes means reject).
3. Command policy (allowlist, no shell, no eval flags).
4. **Vacuity lint** on validation. Reject:
   - an empty list;
   - info-only invocations (`--version`, `--help`, `-v`, `-h`);
   - print-only scripts: the script's AST has no assertion, no non-zero `exit` path and no comparison against an expected value;
   - `echo`, `true` or `exit 0`;
   - scripts that do not exist in the submitted workspace or diff.
5. Leakage audit: strip private literals (task ids, answers) that appear only in hidden text, as skill2gep does.
6. Prompt-injection and escalation lint on `strategy`, `avoid` and `tool_policy` (for example "disable tests", "ignore previous instructions", `allow_only` widening).
7. Size ceiling: gene ≤ 500 estimated tokens, the skill2gep ceiling [code:skill2gep-repo/README.md].
8. Near-duplicate check: Jaccard of `signals_match` ≥ 0.6 **and** strategy-embedding cosine ≥ τ_dup merges the asset into the existing lineage as a new version rather than a new asset.

**Self-reported metadata is discarded or recomputed.** `blast_radius` is recomputed from the diff. `confidence`, `success_streak` and `outcome.score` are ignored for ranking; they are kept only for audit.

**9.2.3 Verified execution** (the hub runs it, so nothing is self-reported).

- **(a) Discriminative validation for code-bearing assets.** Run the asset's validation V in a sandbox provisioned with the Capsule's pre-state `W0` and post-state `W1` (the diff applied). Require `V(W1) = pass` and `V(W0) = fail`, or, for optimizations, a measured metric difference beyond δ.
  - Optionally apply m mutation-testing mutants of the changed hunks, requiring kill rate ≥ κ.
  - `V` passing on `W0` means vacuous and triggers rejection.
  - This removes `console.log` / `node --version` validations by construction.
- **(b) Task-level uplift for strategy genes.** The hub owns a **TaskBank** of tasks per signal class with **hidden graders** that agents never see; the bank is rotated and part of it is held out.
  - For gene g with scope signals S_g, sample n in-scope tasks and n′ off-scope tasks.
  - Run a frozen reference executor with and without g, using paired seeds and k repeats.
  - Compute the uplift `U = mean(success_with − success_without)` in scope, its paired-bootstrap lower bound `U_LCB`, and the off-scope regression `R`.
- **Accept** when `U_LCB ≥ δ` and `R ≥ −δ`, where δ comes from `NoiseCalibrator` on repeated baseline runs. This is the RRSI floor idea applied to asset admission [doc "EvoMap's sharing idea would be far more trustworthy if every shared asset had to pass an RRSI-style independent check"].
- **Output:** a hub-signed `ValidationReport` `{hub_run_id, executor_id, task_ids_hash, seeds, n, k, U, U_LCB, delta, R, discriminative, recomputed_blast}`.

**9.2.4 Adoption-based ranking** (no self-reported inputs).

```text
For asset a: adoption reports A_a = {(consumer c, outcome o ∈ {0,1}, proof π)} counted only if
  c ≠ author(a), c ∉ cluster(author) (same owner/stake source), c has stake or reputation ≥ r_min,
  ≤ κ_c reports per (c, a) per epoch, and π is verifiable (the hub re-runs a random fraction ρ of reports on hub
  tasks; a disagreement slashes the reporter).
s_a = Σ o,  f_a = Σ (1 − o),  n_a = #distinct consumers
q_a    = 5% quantile of Beta(1 + s_a, 1 + f_a)                 # adoption-success lower bound
u_a    = U_LCB(a) from 9.2.3, mapped to [0,1]
rank(a) = q_a · (1 − exp(−n_a / ν)) · (0.5 + 0.5·u_a) · 0.5^(age_since_last_verified_adoption / h)
exploration: for each signal class, reserve an ε share of search results for verified-but-unadopted assets,
             chosen by Thompson sampling on Beta(1 + s, 1 + f), so new assets can earn adoption without
             rich-get-richer lock-in.
```

**9.2.5 Credits** (pay adoption, not publication).

- Publishing requires a small **stake**, refunded at `verified` and slashed at `rejected`.
- There are **zero** credits for promotion.
- The author earns per *verified adoption by a distinct consumer* with outcome 1, with diminishing returns per consumer and a per-epoch cap.
- Validators are paid for hub-cross-checked reports; a validator whose report disagrees with the hub's re-run is slashed.
- Bounties pay on the hidden-grader pass only.

**9.2.6 Untrusted-asset quarantine** on the consumer side (and in the hub's own sandbox).

1. `fetch` stages an asset into `external_candidates` after verifying its asset_id, multiplying confidence by 0.6 (as Evolver does) and recording provenance (`MemoryGraphEvent kind=external_candidate`, with the hub report id) [code:evolver/scripts/a2a_ingest.js][spec:§10.2].
2. The asset is **never executed or injected into production directly**.
3. The consumer runs a local A/B on its **own** held-out tasks with RRSI's `Selector` (floor `S* − δ`, cost rule). It promotes to the local store only on pass, sets `source_type="reused"` and `reused_asset_id`, and sends an adoption report with proof.
4. A failure writes an anti-pattern to the gene's lineage and a negative adoption report.
5. Validation commands from external assets run only in the sandbox, with the command policy and a network-off convention.

**9.2.7 Reuse and health metrics** (the `ReuseMetrics` dashboard; all computed from hub logs).

- `reuse_rate` = share of published (and of promoted) assets with ≥ 1 verified non-author adoption; `never_reused = 1 − reuse_rate`.
- Time to first reuse (median).
- Adoption success rate.
- `consumer_uplift` = mean paired Δ success of consumers who adopted versus a counterfactual no-gene run on hub tasks.
- Credit Gini and top-10% credit share.
- `vacuous_share_promoted`: the discriminative check re-run on a sample.
- `rank_validity`: Spearman ρ between rank and later verified adoption success; in simulation, also against the true effect.
- Duplicate rate.
- Metadata-inflation sensitivity (Δrank when claimed fields are perturbed; must be 0).

### 9.3 CPU-only experiment design

**Worlds.**

- **Tier 1 ("GeneWorld", pure Python, deterministic `MockLLM`, seconds per run, 30–50 seeds)** [inferred design]:
  - 12 signal classes, each with a token vocabulary and error signatures. Tasks draw signals plus noise tokens.
  - A mock "model" m has ability `a_m`.
  - Solving: `P(success | task t, injected genes G) = σ(a_m − d_t + Σ_{g∈G} e_g(c_t) − λ_comp·max(0, |G| − 1) − λ_len·tokens(G)/1000)`. The composition and length penalties are knobs that represent the *hypotheses* under test, not facts.
  - Each gene has a true effect vector over classes, a token length, and a validation spec that is either **real** (a Python check on a toy workspace that fails before the fix) or **vacuous** (print-only or `--version`).
  - The Skill version of a gene has the same content, about 10× the tokens, and effect dilution as a knob.
  - Agent types: `honest` (publishes only locally verified gains), `farmer` (mass-publishes variants with vacuous validation and inflated metadata), `inflator` (honest content, inflated blast radius/confidence/streak), `freerider` (fetches only), `poisoner` (strategy containing a harmful step or an injection string).
- **Tier 2 (real LLM through `claude -p`, small N, qualitative)** [inferred design]:
  - About 20 small Python katas in 5 classes (off-by-one, unicode normalization, date parsing, float rounding, retry/backoff). Each has public smoke tests and hub-held hidden pytest graders.
  - The executor is `claude -p` editing a sandbox copy.
  - Genes and Skills are written by `claude -p` from one fail→pass trajectory per class. The Skill is instructed to be a full SKILL.md with sections; the Gene is ≤ 300 tokens (keywords / summary / strategy / AVOID).
  - Everything runs on CPU. LLM latency is the only cost.

| # | Claim | Measure | Arms | Confirming outcome (qualitative, consistent across seeds; paired bootstrap CIs) |
|---|---|---|---|---|
| X1 | Compact genes carry experience better than long skill documents [doc][snip:SG] | hidden-grader pass rate; tokens | Tier 2: none / Skill / Gene (/ Gene truncated-Skill at matched budget) × 3 seeds | Gene ≥ none and Gene > Skill in direction; matched-budget Skill < Gene. Report as "does/does not reproduce" given small N. |
| X2 | Failure warnings work best as compact AVOID items, not appended history [snip:SG] | pass rate | Tier 2: strategy only / AVOID only / strategy + appended raw failure log | AVOID-only ≥ appended-log |
| X3 | One targeted gene beats compositions [snip:SG] | pass rate | Tier 2 and Tier 1: 1 gene vs 2 complementary vs 2 conflicting | single ≥ multi (Tier 2 measured; Tier 1 is only a mechanism check since λ_comp is a knob) |
| X4 | The local loop reuses what worked, bans what fails, explores when stuck (§3.3, §4.3–4.5) | per-class selection accuracy vs the oracle-best gene over cycles; ban latency for a harmful gene; stagnation escape | Tier 1: memory graph on/off, drift on/off, plateau override on/off; ban rule "spec" (total ≥ 2, value < 0.18) vs "current" (per-key ≥ 4, best < 0.15) | accuracy rises toward the oracle; drift finds the better gene after a plateau; ban latency matches the code-derived expectations of §4.4: 4 failures under the spec rule; 5 (6 with the predictive factor) under the current memory-graph rule; 2 via failed-capsule overlap when diffs are non-empty [inferred arithmetic] |
| X5 | "Doing nothing" can masquerade as success (issue #562) | share of cycles selecting an inert gene | Tier 1: count `stable_no_error` as success (old) vs inert (fixed) | the old rule shows domination (share near 1); the fixed rule does not |
| X6 | Sharing lets a lesson learned once be inherited by many agents, even on different models [doc] | cumulative solves, redundant re-solves, tokens per solve, time-to-solve per class | Tier 1: isolated agents vs SafeHub; heterogeneous `a_m` | hub arm solves more with fewer redundant solves and tokens; weak models benefit from strong models' genes |
| X7 | Replication of Behind-EvoMap dynamics under publish-rewarding, self-reported validation [abs:BE] | never_reused share, top-10% credit share, Gini, vacuous_share_promoted, Spearman(rank, true effect) | Tier 1 population with 10–20% farmers: NaiveEvoMapHub | never_reused large (e.g., > 90%); credits concentrated in farmers; vacuous share high; rank correlates weakly or negatively with true effect |
| X8 | GDI is manipulable through self-reported metadata; blast radius dominates [snip:BE] | Δrank when an inflator changes only claimed blast / confidence / streak | NaiveEvoMapHub vs SafeHub | large Δrank under GDI, largest for blast radius; Δrank = 0 under SafeHub (metadata ignored or recomputed) |
| X9 | Verifiable execution stops vacuous validation | recall and precision of VacuityDetector (lint + discriminative) on seeded vacuous vs real validators | Tier 1 and Tier 2 assets | recall ≈ 1 on the seeded vacuous classes; low false rejects on real validators |
| X10 | Adoption-based ranking plus adoption credits fix the incentives | same metrics as X7, plus consumer_uplift | same population: SafeHub (with and without exploration slots) | reuse_rate up, vacuous ≈ 0 among promoted, rank validity ρ > 0, farmers' credits ≈ 0; exploration slots cut time-to-first-reuse for new good assets |
| X11 | Untrusted assets must be quarantined and re-tested [doc] | poisoned or harmful assets reaching local stores; consumer regressions | quarantine + local RRSI gate vs direct apply ("REUSE MODE: apply faithfully") | ≈ 0 poisoned promotions and no consumer regression beyond δ with quarantine; measurable harm without |
| X12 | Distilled genes from real fail→pass evidence beat reference-transcribed genes (§7.5) | pass rate of the distilled gene on held-out tasks | Tier 1/2: Distiller from evolved trajectories vs from reference text only | evolved > reference-distilled (direction) |
| X13 | Auditability: every change traceable; tampering detectable [doc][spec:§1] | reconstruct the lineage (event.parent chain, capsule→gene→asset_id); detect a modified asset | inject byte-level tampering; export/import an archive | 100% tamper detection via asset_id; lineage reconstructed exactly; round-trip identical hashes |
| X14 | "Tokens rise then fall" as reasoning is compressed into genes [code:evolver/README.md] | tokens per solved task over successive gene-library versions | Tier 2 across 4–5 library versions | qualitative rise then fall. Label it as exploratory; the vendor report is not accessible |
| X15 | Self-assessment contaminates learning: when the memory graph reads the executor's self-reported EvolutionEvent (§4.11), an over-confident executor steers gene preference toward genes that do not help [inferred from deob:memoryGraph.js] | preferred-gene accuracy vs the oracle; correlation between the preferred gene's true effect and the self-reported scores | Tier 1: an honest vs an over-reporting mock executor (always prints `outcome.score 0.9`); OutcomeInferrer faithful (transcript-first) vs safe (solidify-measured only) | faithful mode + over-reporting executor ⇒ preference drifts to ineffective genes; safe mode is unaffected |
| X16 | The composite "process score" is not a task-gain measure (§4.10) | Spearman ρ between the composite score and the hidden-grader task gain per cycle | Tier 1 and 2: cycles with real, vacuous (`--version`) and skipped validations | ρ near 0 for vacuous/skipped validations, and most such cycles still ≥ 0.78 (publishable) |

---

## 10. Capability checklist: overview claims mapped to components

| # | Claim the overview [doc] attributes to EvoMap | Component(s) that satisfy it |
|---|---|---|
| 1 | Spreads improvements across a network; agents share strategies others can reuse (the outer "network" ring) | `Hub` (`SafeHub` / `NaiveEvoMapHub`) + `AgentNode.publish/search/fetch` + `PopulationSimulator` |
| 2 | Agents turn what worked into small "genes" and publish them so others inherit a proven strategy instead of starting from scratch | `Solidifier` → `Capsule`, `Distiller` → `Gene`, `Hub.publish`; inheritance via `Hub.search` + quarantine gate (X6) |
| 3 | 1.5M assets, but 98% never reused | `ReuseMetrics.never_reused` / `reuse_rate`; replication X7 |
| 4 | GEP whitepaper and "compact strategy genes beat long skill documents" | GEP-compatible `Gene/Capsule/EvolutionEvent` + `JsonSchemaValidator`; X1 (Gene vs Skill) |
| 5 | What improves is a shared library of know-how; each agent keeps a local store and can publish to a hub where agents on different models reuse it | `LocalStore` + `Hub`; heterogeneous `a_m` / real models in X6 |
| 6 | Look before solving: search the local store, then the hub (matching by what the asset does); only then work out a new approach | `AgentNode.cycle` order: `GeneSelector` over `LocalStore` → `Hub.search` (signal and semantic match, `PatternMatcher` + scorer) → `Executor` from scratch; `PromptBuilder.reuse_prompt` |
| 7 | Solve and distil: Evolver distils what worked into reusable assets | `Solidifier` (Capsule) + `Distiller` (heuristic synthesizer by default, LLM distill-request fallback, and failure distillation; triggers in §4.14) + `LeakageAuditor` |
| 8 | Validate and record: a validation command tests the asset; the outcome is recorded as an event (audit trail) | `ValidationRunner` + `CommandPolicy` + `CountedFilePolicy`/`ConstraintChecker` + `VacuityDetector` (safe mode) + `EvolutionEvent` appended to `events.jsonl` with `parent` chain; the memory-graph outcome via `OutcomeInferrer` (X15) |
| 9 | Publish and rank: a ranking score decides which rise; a credit economy rewards contributors | `Hub.rank` (GDI in the naive hub, adoption-based in `SafeHub`) + `CreditLedger` |
| 10 | Models fixed; each agent keeps its own harness; decisions by local validation plus hub ranking | `Executor` wraps a frozen model; no harness edits; `Solidifier` keep rule + `Hub.rank` |
| 11 | Installs locally and runs in any git repo; hub connection optional | `Workspace` (git-backed, with rollback modes) + `AgentNode` running with `hub=None` |
| 12 | Genes = compact strategy cards; Capsules = worked case traces; Events = records of each outcome | `Gene` (≤ 500-token ceiling, keywords/summary/strategy/AVOID), `Capsule` (`execution_trace`, `diff`), `EvolutionEvent` |
| 13 | GEP defines how assets are created, validated and shared; hub, marketplace, worker pool and credit economy around it; a layer above MCP and skill files | asset schemas + `Solidifier` / `ValidationRunner` / `Hub` / `CreditLedger`. Marketplace, worker pool and bounties are optional (a `Task` object with hidden graders in `SafeHub.TaskBank`). An MCP adapter is out of scope, noted as an interface only |
| 14 | 4,590 trials on 45 scenarios: compact genes strongest; longer skill documents unstable and sparse | X1–X3 with `Executor` + injection interface (none / Skill / Gene / fragments) |
| 15 | CritPt: 9.1% → 18.57% and 17.7% → 27.14% with gene-evolved systems | `AgentNode` evolution over a task stream + `TransferEvaluator` on a held-out set; CritPt itself is not reproducible on CPU. Our analogue is X6/X12 on Tier 2 katas |
| 16 | OpenClaw rose 9.1% → 18.57% over five versions; tokens rose then fell | versioned `LocalStore` snapshots + token accounting in `ExecResult`; X14 |
| 17 | Vendor results not independently replicated | multi-seed, paired-CI reporting in all experiments; `NaiveEvoMapHub` for faithful replication |
| 18 | Behind EvoMap: 1.5M assets, 128K agents analysed | `PopulationSimulator` logs + `ReuseMetrics` on the same statistics (scaled down) |
| 19 | 98% never reused; rewards tied to publishing not adoption | `CreditLedger` naive rules (+100 per promotion, +5 per fetch) vs SafeHub adoption credits; X7/X10 |
| 20 | ~10% of agents held most credits, from repeated publishing rather than reuse or bounties | `CreditLedger.top_share(0.10)` and `gini()`; farmer agents in X7 |
| 21 | 84%+ of approved assets passed validation with vacuous tests | `NaiveEvoMapHub` validator (proposer's commands, empty dir) vs `VacuityDetector` (lint + discriminative); X7/X9 |
| 22 | 35% of the ranking score is an "intrinsic" component driven by self-reported metadata that agents can inflate | `GDIRanker` (0.35/0.30/0.20/0.15 with a 6-metric intrinsic mean) vs `SafeHub` ranker that ignores claimed fields; X8 |
| 23 | Agent networks need verifiable execution and trustworthy evaluation | `SafeHub` verified execution (discriminative validation + hidden-grader uplift with `U_LCB ≥ δ`), hub-signed `ValidationReport` |
| 24 | Moves self-improvement from one system to a population; a lesson learned once can be inherited, even on different models | `PopulationSimulator` + cross-model transfer in X6 |
| 25 | Stores experience as small structured units rather than long prompts | `Gene` schema + size ceiling + gate; X1 |
| 26 | Audit trail built into every change (what was learned, from what, with what result) | append-only `events.jsonl` + `parent` chain + `Capsule.gene`, `reused_asset_id`, provenance + asset_id hashing; X13 |
| 27 | The only large public network, which is why its failure modes could be measured | `ReuseMetrics` dashboard exposes the same measurements for our hub |
| 28 | Watch out: agents grade their own work | `SafeHub` ignores self-reported scores; the consumer-side RRSI gate; the hub re-runs a fraction ρ of reports; locally, `OutcomeInferrer` in safe mode never reads an LLM-printed score (X15), and X16 shows the composite is not a task gain |
| 29 | Watch out: publishing rewarded more than usefulness; library fills with unused assets | adoption-only credits + publish stake + dedup + deprecation by decay; X10 |
| 30 | Watch out: inheriting a strategy means trusting code of unknown quality; treat shared assets as untrusted until tested | the `external_candidates` quarantine + local held-out re-test + command sandbox + injection lint; X11 |
| 31 | Side-by-side: search shape is a population sharing through a hub; keep rule is local validation plus hub score; guard against overfitting is weak; strategy is improved by many agents steered by credits and rankings; history is a shared library others inherit; cost control is reuse avoiding re-solving, with no explicit cost rule | `PopulationSimulator`, `Solidifier` + `Hub.rank`, `SafeHub` verified gates (a stronger guard), `CreditLedger`, `LocalStore`/`Hub` library, tokens-per-solve metric (optional cost rule via RRSI `Selector`) |
| 32 | "Never let the loop grade itself: re-run any shared or reused strategy on your own tests before trusting it" | consumer-side quarantine gate (9.2.6) using RRSI `Selector` + `NoiseCalibrator` |
| 33 | "Measure reuse, not output: a growing library means little unless it is actually reused and actually helps" | `ReuseMetrics` (`reuse_rate`, `consumer_uplift`, `rank_validity`) |
| 34 | Cross-method synergy: EvoMap sharing would be more trustworthy if every shared asset passed an RRSI-style independent check before it could spread | `SafeHub` verification uses RRSI `NoiseCalibrator` δ and the floor/LCB rule; `TransferEvaluator` for held-out uplift |
| 35 | Hub connection optional; shared assets untrusted (FAQ) | `AgentNode(hub=None)` offline mode; quarantine default-on |
| 36 | Glossary: A2A network, Gene, Capsule, GEP | A2A envelope-compatible messages (`Hub` API), `Gene`, `Capsule`, GEP-compatible schemas (marketed under a neutral name; §0 trademark note) |
| 37 | Name clash: EvoMap's AutoResearch is unrelated to Karpathy's autoresearch | documentation note only; no component |
| 38 | EvoMap maintains a curated list of agent-evolution projects (the source of the "Like EvoMap" related-work group: Hermes Agent, EvolveNet, Agent KB, ReasoningBank/ACE, SkillOS/EvolveMem/AutoMem) | documentation/related-work note only; the `Gene`/`Capsule` format plus `Hub` is the component these relatives would be compared against |
