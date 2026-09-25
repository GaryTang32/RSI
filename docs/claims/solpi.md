# SoL-Pi: claim-by-claim audit of `rsi.solpi`

Paper: Liu, Ye et al. (NVIDIA, NTU, MIT), *SoL-Pi: Recursively Scaling Auto-Research Loops for Efficient Agent Harness*, arXiv 2609.20519 (17 Sep 2026).
Audited code: `rsi/solpi/`, `rsi/domains/agentworld/`.
Audit date: 2026-09-25.
This audit changed no code. The only file it wrote in the repo is this document.

## 0. Honesty statement: what this audit could and could not see

- **I could not read the paper text.** arXiv, alphaxiv, HF and hyper.ai were all blocked, and no mirror of the full text was found. Every paper-level claim below therefore rests on one of these:
  - the authors' project page (gh-pages source; the `[blog]` tag in the spec);
  - co-first author Tian Ye's blog (`[ye-blog]`);
  - secondary summaries in `scratchpad/papers/solpi_secondary/`. Of these, `pengqian` says it was built from the full arXiv HTML, including Tables 1–4. `vollero`, `inkeast`, `capsule`, `nbardy`, `inmatrix`, `j8` and `hundeok` are shorter.

  A claim that appears only in a secondary summary is marked **[sec]**.
- **Mechanism fidelity is checked against primary code.** The reference is `NVlabs/SoL-Pi` @ `1559b5c` (`scratchpad/src/NVlabs__SoL-Pi`) and its vitest suites. For this audit I also downloaded Pi itself (`@earendil-works/pi-coding-agent@0.85.1` from npm, into `scratchpad/npmpk/pi/`) so I could check two host-side semantics: `getContextUsage` and `fromExtension`.
- **Nothing here runs at GPT-5.6 Sol / Claude Opus 5 scale.**
  - Every quantitative result in the repo comes from AgentWorld, a CPU simulation with 5 environment families and deterministic `MockAgent` backends A and B.
  - The only live LLM runs are small haiku runs: $2.44 in total for this method's validation. The LLM plays the implementer, reviewer and reducer roles, never the agent backend.
  - The paper's magnitudes (EdgeBench, Terminal-Bench 4, IMO 2026, the swarm) cannot be tested here.
  - Where our numbers agree with the paper, it shows that the mechanisms behave as claimed *under our simulated world*. It does not show that the paper's effect sizes transfer.
- **Offline checks run for this audit.** No LLM calls; about 25 s of CPU in total. The scripts live outside the repo in `scratchpad/claims_solpi/`:
  - `vectors.py` → `vectors.json`: ports the reference state, plan, EPR and ObservationPack test vectors, plus probes of TS semantics.
  - `fusion_probe.py`: Action Fusion edge semantics.
  - `occ_w.py` → `occ_w.json`: reruns the S4 OCC arm with Pi's `getContextUsage` semantics for `W`. 5 seeds × 4 length bins × 2 backends.

## 1. Summary

| verdict | count |
|---|---|
| REPRODUCED | 27 |
| PARTIAL | 15 |
| NOT REPRODUCED | 3 |
| NOT TESTABLE HERE | 16 |
| CONTRADICTED | 0 |
| **total claims** | **61** |

By type:

| type | claims | R | P | NR | NT |
|---|---|---|---|---|---|
| mechanism | 14 | 12 | 2 | 0 | 0 |
| research protocol (mechanism) | 9 | 4 | 4 | 0 | 1 |
| quantitative | 17 | 1 | 5 | 2 | 9 |
| qualitative | 9 | 2 | 4 | 1 | 2 |
| caveat | 12 | 8 | 0 | 0 | 4 |

- **The four runtime mechanisms are faithful ports.**
  - Every formula in OCC's economics matches the release (`decideCompaction`, `estimateRemainingRequests`), and the reference economics and state vectors pass exactly.
  - Receipt validation matches `receipt.ts` check for check.
  - The ObservationPack constants and id scheme are identical.
  - This audit found 11 small fidelity mismatches, listed in §3. Most concern agent-facing text or edge cases. None changes a reported result; the one I could quantify (OCC `W`) moves S4 cost by ≤ 0.5%.
- **Protocol claims reproduce at toy scale.** The protocol here is 10 ideas and 5 simulated families.
  - The firewall and the dual gate are faithful.
  - Diversity-driven survival is PARTIAL.
  - The capability floor fails against a lenient turn cap (S6 NOT REPRODUCED).
- **Headline numbers do not transfer as claimed.**
  - Tokens: the simulation shows a larger cut than the paper (−54…−68%).
  - Cost: "about a third" appears only in some cells (−16…−61%). The stack actually composed by the protocol saves just 8.9%.
  - Quality: the paper's ~6% quality loss never appears, because the mock agent's quality does not degrade.

## 2. Claim table

Legend:
- **Verdicts:** R = REPRODUCED, P = PARTIAL, NR = NOT REPRODUCED, NT = NOT TESTABLE HERE.
- **Paths:** results are `results/metaharness-solpi/`; validation runs are `validation/metaharness-solpi/`.
- **Code:** references are `file:line` in our code, and "ref" is the NVlabs TypeScript under `src/sol-pi/extensions/`.

### 2.1 Mechanisms (runtime extensions)

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| M1 | Action Fusion: `edit`/`write` take an optional `then_run {command, timeout?}`. Mutation and command run in one tool call and return one observation. | code, blog, [sec] | mechanism | Faithful. `fusion.py:71-79` replaces `edit`/`write` and calls the built-ins. `fusion.py:86-127` ≙ ref `then-run.ts:78-127`. Success appends `[then_run:succeeded]\n<out>` (`:127`). A non-zero exit keeps the edit and returns `mutation\n\n[then_run:failed]\n\n<err>` as an error (`:122-125`). A failed mutation adds the `skipped` suffix (`:95-104`). Minor text diffs: see §3 items 9–10. | `tests/test_metaharness-solpi_mechanisms.py:90` (markers). S1 safety checks 4/4 (`s1_action_fusion.json` `safety_checks`). | R |
| M2 | Hash guard and per-file queue: hash, yield, hash again. If the content changed, `[then_run:skipped] target content changed…`; the command is not run. | code | mechanism | Logic faithful: `fusion.py:110-116` ≙ ref `assertUnchangedBeforeCommand`. Two limits:<br>• The queue is a per-path `threading.Lock` (`:64-69`) keyed on a stripped path. It is not a `realpath` key (ref `file-queue.ts:181-197`).<br>• In production nothing yields between the two hashes. The single-threaded runtime exercises the guard only through the test `yield_hook`.<br>Probe: a target deleted between the hashes still gives `skipped` with the command not run, but with the "changed" message instead of the ref's ENOENT text. | `test_action_fusion_hash_guard_skips_on_interleaved_write`; `scratchpad/claims_solpi/fusion_probe.py` | R |
| M3 | Fusion removes the decision-free turn ("1 model round-trip avoided"; "3 API calls → 2"). | code TUI, [sec] pengqian Q5 | mechanism | as M1 | S1, backend A: requests −23.1% [−21.8, −24.0], tokens −20.4%, cost −11.7%, success Δ 0.000. Backend B: requests −9.3%. | R |
| M4 | ObservationPack. A pure-text, non-error result over 10 KiB (bytes > 10,240) is sent in full on its first 2 provider requests. After that it becomes a stable placeholder with a whole-line 512 + 512 B head/tail excerpt and id `obs_`+sha256(tool‖\0‖callId‖\0‖sha256(text))[:24]. | code, [sec] | mechanism | Faithful: `obspack.py:32-39` constants. `:66-76` ≙ ref `createObservation`. `:92-111` ≙ `placeholderFor` (same text). `:168-209` ≙ the `context` handler, including `prev = sentCounts ?? #assistant-after`. Diffs:<br>• the excerpt splitter uses `splitlines` (§3 item 6);<br>• the placeholder ledger entry lacks `originalLines`;<br>• sends are double-counted on auto-compaction turns (§3 item 11). | `test_obspack_placeholder_after_full_sends_and_recall_fidelity`; vectors.py `obs.placeholder_head_tail` PASS | R |
| M5 | Exact paged recall with `obs_recall` (≤ 16,384 − 512 B, ≤ 400 − 2 lines, UTF-8 safe). A packing error fails open. | code | mechanism | Faithful: `obspack.py:114-131` ≙ `readRecallChunk`; `:212-231` ≙ the recall tool with its hard-limit check; fail-open at `:206-208`. Diff: a negative offset is accepted (§3 item 7). There is no symlink / `O_NOFOLLOW` handling, because the store is in memory. | S2: recall fidelity 100% over 300 payloads, including multi-byte UTF-8. Injected storage failures do not change quality (`s2_observation_pack.json`). | R |
| M6 | ObservationPack skips EPR receipts, so verified evidence is not compressed twice. | code, [sec] pengqian | mechanism | Faithful: `obspack.py:68-69` (any line == `sol_pi_evidence_receipt_v1`). | `test_obspack_rules_threshold_errors_receipts_utf8_and_fail_open` | R |
| M7 | EPR trigger. A bash command, or `then_run.command`, matching `DIAGNOSTIC_COMMAND` with a body ≥ 4,096 B. Over 600,000 chars → `source-over-max-chars`; a `LIKELY_SECRET` match → `likely-secret`. The exact untruncated `/tmp/pi-bash-*.log` is used when Pi truncated the output. | code, [sec] | mechanism | Regexes are verbatim (`reducer.py:56-61`). Order of checks as in ref `index.ts:reduceToolResult` (`reducer.py:363-377`). `exact_body` `:283-298` ≙ `exactBodyFromInline`. `reducible_tool_result` `:301-323` ≙ `candidate.ts`, including the separator rule. Diffs:<br>• an archive I/O error falls back instead of throwing (documented);<br>• `maxChars` counts code points, not UTF-16 units (§3 item 5). | S3; `test_epr_handles_fused_then_run_results` | R |
| M8 | EPR receipt validation. A receipt is accepted only if all of these hold:<br>• it is JSON;<br>• the schema matches;<br>• `source_sha256` matches;<br>• the status matches `is_error`;<br>• `uncertain` is a bool;<br>• it has ≤ 12 items;<br>• each item's kind is in the allowed set;<br>• each quote is 1–600 chars and an exact substring of the log.<br>Duplicates are dropped. A failing log with a `FAILURE_SIGNAL` must carry fatal or failure evidence. | code, [sec] | mechanism | Faithful, check for check: `reducer.py:110-137` ≙ ref `receipt.ts:validateReceipt` (same rejection reasons, same order). `receipt_text` `:140-152` ≙ `receiptText`. Diffs:<br>• `source_lines` is off by one for logs ending in `\n` (§3 item 4);<br>• quote length counts code points (§3 item 5). | S3: accepted receipts contained 0 non-verbatim quotes for h ∈ {0, .1, .3, .6, 1}. Acceptance 1.00/.90/.67/.31/.00. Failure logs always carried failure evidence (`s3_reducer.json`). The ref vector ("line=2", status=failure, smaller than the source) passes; the invented-quote vector gives `unverifiable-quote`. | R |
| M9 | EPR falls back to the untouched raw output on any failure, or when the receipt is not smaller than the log. | code | mechanism | Faithful: `reducer.py:399-415` (`model-response-error` when stop ∉ {stop, length}; `receipt-not-smaller`). | `test_validate_receipt_all_rejection_reasons`; S3 fallback histogram | R |
| M10 | The EPR reducer is a separate low-cost model (GPT-5.6 Luna, "high" effort) with a 90 s timeout, `maxTokens` = min(2048, model.maxTokens) and `cacheRetention` none. | [sec] pengqian; code provider.ts | mechanism | The route is configurable: `LLMReducer` `reducer.py:249-267`. No timeout is passed; `TIMEOUT_MS` is defined but unused. `max_tokens` is 2048 without the model min. No effort setting (the release sets none either). Offline, the `DeterministicReducer` (`:173-208`) is our own stand-in. | Live: haiku reducer on 4 logs, 4/4 accepted, 0 non-verbatim, 91,825 → 6,028 B, $0.18 (`live_smoke.json` `parts.epr`). | P |
| M11 | OCC economics. Symbols: `A = max(0, W−F−K)` with K = 20,000; `S = A − m` with m = 1,000; `b = W·max(0, ρ−1)/S`; `b_c = (D + W·ρ')/S`. Horizon: `μ`, `L` (the k·s and small-sample rules), `R_unb = 1 + ⌊L·B_rem·scale⌋`, `R_win = max(0, ⌊(W_win − C)/Δ̄⌋)`, `R = min(R_unb, R_win)`. First compaction: `R_eff = min(2R, R_win)`. Later compactions: `b ≤ R ∧ 1.5b ≤ R ∧ b_c ≤ R`. Window protection: `C ≥ W_win − 16,384`. Compact iff `S > 0 ∧ (window ∨ economic)`. The reason precedence is part of the claim. | code economics.ts, [sec] pengqian Q5 | mechanism | Exact port, line for line: `occ.py:56-76` ≙ `estimateRemainingRequests`; `:79-141` ≙ `decideCompaction`, including every intermediate and the reason ladder. Constants: `:31-50`. | All 6 ref `online-context-compact-economics.test.ts` vectors pass exactly (`test_occ_economics_test_vectors`; `s4_occ.json` `economics_vectors`). | R |
| M12 | OCC state and cache-debt accounting:<br>• each request: `D ← max(0, D−r)`, and `r ← 0` once D = 0;<br>• a boundary appends the interval;<br>• a compaction: epoch+1, reset, count+1, `D = W·ρ'`, `r = A−m`, set to 0 if `fromExtension`;<br>• a correction resets. | code state.ts, extension.ts | mechanism | Exact: `occ.py:162-188` ≙ `state.ts`. `_session_compact` `:369-373` ≙ the ref handler. I verified in Pi 0.85.1 (`dist/core/agent-session.js:1495-1512`) that `fromExtension` is true only when a `session_before_compact` hook supplies the summary. Our default `from_extension=False` for OCC's own compaction is therefore correct. | All 6 ref `online-context-compact-state.test.ts` vectors pass (vectors.py `state.*`), except restore-from-session, which is not modelled. | R |
| M13 | OCC runtime loop:<br>• `update_plan` marks boundaries;<br>• at a clean `turn_end` it prices the compaction, checks native feasibility, then aborts;<br>• at `agent_settled` it compacts with `BOUNDARY_COMPACTION_INSTRUCTIONS`;<br>• it then sends the hidden `POST_COMPACTION_PLAN_REMINDER`. | code extension.ts, tools.ts, plan.ts | mechanism | Control flow is faithful (`occ.py:259-373`); the instruction and reminder strings are verbatim. Mismatches:<br>• plan snapshot format, missing advice and lenient parsing (§3 items 1–3);<br>• `W` at `turn_end` excludes the reply and the boundary tool results, whereas Pi includes them (§3 item 8);<br>• `turn_end` does not check `stopReason` aborted/error;<br>• session restore and tree events are not modelled. | `test_occ_compacts_at_boundary_and_reminds`. vectors.py: plan vectors 7/11 pass; the failures are snapshot, advice ×2 and the empty/extra-key parse. `occ_w.json`: Pi-semantics `W` changes S4 cost by +0.5% (A 4–8) and ≤ 0.07% elsewhere. | P |
| M14 | OCC compacts only when the savings repay the cache rewrite. It is better than compacting at every boundary or never compacting, and near-best overall. | code, blog, [sec] | mechanism | as M11–M13 | S4, 4 arms × 4 lengths × 2 backends:<br>• OCC's η is within 5% of the best arm in 8/8 cells and best in 5;<br>• 0 compactions with S ≤ 0;<br>• compacting at every boundary costs +28% vs late compaction;<br>• never compacting overflows up to 40% of runs (`s4_occ.json`). | R |

### 2.2 Research protocol

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| R1 | Breadth: 152 directions in 6 families (C 24, P 26, T 26, D 15, R 15, M 46), ranked by an Oracle Analysis of existing trajectories before rollout budget is spent. | blog, [sec] | mechanism | `research.py:41-81` (`Idea`, `IdeaPool`, `oracle_estimate`); pool in `mocks.py:36-55`. Gaps:<br>• **10 ideas in 5 families. Family M (Improvement & evaluation), the largest in the paper, is absent.**<br>• `n_lineages` = pool size, so the oracle ranks but selects nothing (AUDIT §2.4). | `solpi_agentworld_offline` trace seq 4 (oracle recomputed in AUDIT) | P |
| R2 | Depth: one disposable Karpathy-style lineage per idea. The stages are rollouts, map-reduce analysis, one mechanism, a Ralph loop until an exit check passes, an independent reviewer (rejection → implementation) and in-trajectory validation. The orchestration is discarded and only the candidate and its evidence are kept. | blog, [sec] | mechanism | `research.py:205-340` in that stage order. `implement()` `:158-182` is the Ralph loop. Review → re-implementation `:285-302` (fix 16). A gate failure always routes to 01, never to 04: a documented deviation. | Offline run: 15/15 gate decisions recomputed (AUDIT §2.4). Live r4: a haiku-written condenser survived end to end after a reviewer → implementation repair (AUDIT §2.6). | R |
| R3 | A predeclared dual gate: every capability metric within τ **and** at least one efficiency metric improved. Metrics and tolerances are fixed before search and outside the optimizer's control. | blog, ye-blog, [sec] | mechanism | `gate.py:42-53` is a frozen, digested `GateSpec`; `:137-173` `accept`. τ = 2% relative and min_gain = 2% are inferred, because no tolerances are published. | `gate_digest` in run_start; 15/15 gates recomputed | R |
| R4 | "Among candidates that pass the capability floor, the loop retains nondominated results." | blog, [sec] | mechanism | `gate.py:185-191` `nondominated` is used only inside one lineage and only with `sweep=True` (`research.py:334-339`). Survivors of different lineages are all composed. | AUDIT register #16: C23 froze its most aggressive variant, which raised cost 5.4% | P |
| R5 | Held-out firewall: a frozen candidate is evaluated once, no agent in the loop sees the result, and a failure rejects without becoming feedback. | blog, [sec] | mechanism | `gate.py:204-244`: a write-only sink, a bool return, and an exception on a second evaluation. | `test_firewall_is_one_way_and_lineages_cannot_read_holdout`; sink == trace (AUDIT §2.4) | R |
| R6 | EdgeBench split: 11 of 51 tasks for one-way acceptance, 40 for final evaluation only. | [sec] pengqian Q5, vollero | mechanism | Analog: `holdout` (acceptance, used only by the firewall) and `ood` (final, never touched by the protocol), `domain.py:79-83`. S5 reports the final split only, which is stricter than the paper's 51-task aggregate. | S5 held-out numbers are on `ood` (`s5_survive.py:72-74`) | R |
| R7 | Composition: the survivors are merged as independent opt-in extensions, each gated one at a time, and small losses may accumulate. | blog, [sec] | mechanism | `research.py:400-425` `compose`: a JSON-config union. Faithful, with no re-gating (`validate_composition` is an inferred option, off by default). | Offline protocol stack: +8.9% cost saving, against +33.5% for EPR alone. AF + EPR is the best subset (AUDIT §2.4 subset table). | P |
| R8 | Search environments: 535 of them, 495 repository issue→PR tasks kept only if the regression test fails before the fix and passes after, plus 40 verifier-first tasks with graded scores. None is derived from EdgeBench. | blog, ye-blog, [sec] | mechanism | `agentworld/base.py` `validity_filter` (fails before, passes after the reference fix). Only 5 simulated families (repofix, buildfix and logtriage for training; configfix and datalookup held out), with no graded verifier-first family. | `test_every_environment_passes_the_validity_filter` | P |
| R9 | Search on GPT-5.6 Sol at xhigh; the frozen stack is applied to Opus 5 "without any further search". | blog, ye-blog, [sec] | mechanism | Analog: MockAgent A for search, B for transfer (`policy.py`). There is no real frontier backend. | – | NT (no frontier-model backend; cost) |

### 2.3 Quantitative results

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| Q1 | EdgeBench vs Pi: token traffic −49.0% (Sol, 2.1538 → 1.0990 B) and −44.7% (Opus, 2.3697 → 1.3101 B). | ye-blog, [sec] pengqian, vollero, doc | quantitative | `meter.py` (total tokens = cache_read + cache_write + output) | S7 full stack: −53.8% (A default), −58.7% (A long), −61.8% (B default), −68.4% (B long). The protocol-composed stack: −49% on screen, −69% held-out, −66% ood (RUNS). The direction holds and the size is larger. It is a different world, not EdgeBench. | P |
| Q2 | About one third lower API cost: −33.2% (Sol, $1,339 → $894) and −33.5% (Opus, $1,741 → $1,158). | same | quantitative | invented prices with ρ = 12.5 (`meter.py:413-418`) | S7 full stack: −15.6% (A default), −30.6% (A long), −44.4% (B default), −61.2% (B long). The protocol stack saves only −8.9%. On backend A, EPR alone (−32.8%) is cheaper than the full stack. | P |
| Q3 | At 93.7% (Sol: 44.833 → 42.003) and 94.3% (Opus: 44.756 → 42.224) of Pi's score. | ye-blog, [sec] | quantitative | – | Full-stack success ratio is 1.000 / 1.000 / 0.998 / 1.006. The quality loss is **not** observed: the MockAgent's skills read exactly the evidence the mechanisms preserve. | NR (the simulated agent cannot lose quality the way a frontier model does) |
| Q4 | EdgeBench table: Codex 34.74 / $1,787 / 3.05 B; Claude Code 43.69 / $2,535 / 2.00 B. Hourly savings $8.75–13.50 vs native harnesses and $4.36–5.71 vs Pi. | blog figs, [sec] | quantitative | – | – | NT (needs EdgeBench and the frontier backends) |
| Q5 | Vs native harnesses: 35–64% fewer tokens and 50–54% lower cost (2.00× / 2.19× cost, 2.78× / 1.53× tokens). | blog | quantitative | – | – | NT (no native-harness baseline exists here) |
| Q6 | Table 1 also includes an unranked GPT-5.5 reference row and OpenSquilla, Oh-My-Pi, OpenCode and Oh-My-Opencode (numbers unavailable). | [sec] pengqian Q6 | quantitative | – | – | NT |
| Q7 | SoL-Pi [Performance]: ObservationPack on Sol scores 47.208 at $1,271; Action Fusion on Opus scores 50.482 at $1,605. | [sec] vollero, inkeast | quantitative | – | Single mechanisms never raise success in the simulation (S7 ratios ≈ 1.0). | NT (score gains require a real agent) |
| Q8 | Terminal-Bench 4 (63 CPU tasks): SoL-Pi solves 15, Pi 18, Codex 18. Cost $211.12 vs $286.45 (−26.3%); $14.07 vs $15.91 per solved task. | blog figs, ye-blog, [sec] | quantitative | – | – | NT |
| Q9 | IMO 2026 (6 problems, Lean-verified): 3/6, 3/6 and 5/6. Cost $62.69 / $75.95 / $114.47. | [sec] | quantitative | – | – | NT |
| Q10 | Kernel swarm (one 2 h run each): 1,127 cycles at $60.11 (SoL-Pi) vs 1,366 at $82.12 (Pi) vs 1,333 at $39.20 (single agent). 17.5% fewer cycles and 26.8% lower cost than the Pi swarm. | blog, ye-blog, [sec] | quantitative | – | – | NT |
| Q11 | Action Fusion oracle: 12.3% of cross-turn transitions are edit→command (bash 85.1%). Counterfactual −10.8% turns, −11.5% tokens. 87.7% uptake. 27-iteration lineage. | blog fig, ye-blog, [sec] | quantitative | `oracle_stats` in `domain.py`; `research.py:69-81` | S1: the oracle share is 32%, predicted −29.9% requests against −23.1% measured. The oracle-predicts-saving relation holds; the magnitudes are those of the simulated world. | P |
| Q12 | Activation depends on the model: AF fires 70.58 vs 13.54 times per triggered task (Sol vs Opus); OCC triggers on 92.2% vs 33.3% of tasks. | ye-blog, [sec] | quantitative | trigger counters in `domain.py` | S7: AF 3.29 (A) vs 1.91 (B) per triggered task. OCC triggers on 5% / 37% of tasks (A default / long) vs 0% / 18% (B). The direction is reproduced; the magnitudes are much smaller. | P |
| Q13 | ObservationPack sweep: V0–V7 with gates of 10% bill saving and −2% quality. V2 (2 sends) is "the only configuration inside the quality gate" (9.1% / −0.8%). The paired EdgeBench A/B gives bill −23.58% and score +22.92%. | blog fig | quantitative | `ObservationPack(excerpt_bytes, head_frac, full_sends)` `obspack.py:137-143` | S2: 8/8 configurations are inside the quality gate, so it does not discriminate, and 0 reach the 10% bill-saving gate (bill −1…+3%, tokens −9…−15%). V2's exact 2048 / 1536 split, V4 and V7 were not swept. | NR |
| Q14 | Add-one ablation: every mechanism reduces total tokens on its own under both backends, and the full stack has the lowest tokens **and** the lowest cost in both backend blocks (Table 4). | [sec] pengqian Q9 | quantitative | – | S7: every mechanism saves tokens alone and the full stack has the fewest tokens in 4/4 cells, but the lowest cost in only 2/4 (backend B). On A, EPR alone is cheapest. | P |
| Q15 | About 1 idea in 40 survived validation. | blog, ye-blog | quantitative | – | 3 of 10 survived offline (a library of mostly real mechanisms); 1 of 2 live (r4). This is a property of the pool design. | NT (scale) |
| Q16 | Search scale: more than 3,000 runs, more than 60,000 interactions, "not a scaling law". | ye-blog, [sec] | quantitative | – | – | NT |
| Q17 | cacheWriteReadRatio 12.5 (GPT-5.6 Sol cache write/read). | code agents-install.md | quantitative | `occ.py:236` default; `meter.py` prices keep write/read = 12.5 | config | R |

### 2.4 Qualitative claims

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| L1 | "Same quality for less cost"; "matched the original Pi harness". | doc, [sec] abstract | qualitative | dual gate (non-inferiority + efficiency) | The simulation keeps 100% of quality at lower cost (S7). The overview's word "matched" is itself stronger than the sources, which report 93.7–94.3%, and the simulation cannot show whether that loss occurs. | P |
| L2 | "Keep only the changes that survive everywhere": search across diverse environments makes the survivors transfer. | doc, blog | qualitative | `per_family` gate option (`gate.py:154-172`) plus the firewall | S5:<br>• multi-family dual gate + firewall: held-out capability 1.000, η saving +0.39;<br>• a single-environment efficiency objective: 0.555 / +0.27;<br>• a **single-environment dual gate**: 1.000 / +0.40, statistically the same.<br>The floor and the firewall do the work; the extra environments only reject more tricks at the gate (3.0 → 1.6–1.8). | P |
| L3 | What survives transfers beyond where it was developed: to held-out tasks and to an unseen backend without further search. | doc, blog, [sec] | qualitative | – | S5: firewall survivors keep 0.99 of held-out success on backend B. The composed stack scores holdout 0.988 and ood 1.0 (RUNS). | R (in simulation) |
| L4 | The capability floor blocks "saving by doing less". | ye-blog, blog | qualitative | – | S6: the floor rejects no-verify in 5/5 seeds but admits the 24-turn cap in 5/5. The composed stack loses 7 points (−0.073 [−0.115, −0.030]). | NR |
| L5 | Efficiency waste is task-independent, so efficiency mechanisms transfer where score hacks do not. | ye-blog | qualitative | – | General mechanisms pass on the held-out families, and tricks break there. But which tricks break is built into the simulated families' log layouts (S5 caveat). | P |
| L6 | Breadth escapes local basins after 5–10 iterations of depth-first refinement. | blog, ye-blog ("qualitative observation") | qualitative | not implemented (spec S8) | – | NT (not run; the paper itself ran no equal-budget comparison) |
| L7 | The four mechanisms are useful for any long-running agent: opt-in extensions, no patches to the base. | doc, code README | qualitative | extensions only through `register_tool` / `on` (`runtime.py:195-213`) | Genericity tests (`tests/test_metaharness-solpi_genericity.py`); `example_new_problem.py` part 2 | R |
| L8 | Recursive efficient improvement: SoL-Pi becomes the next base. | blog, [sec] ("long-term vision") | qualitative | `Config.rounds > 1` (`driver.py:64`), not evaluated | – | NT (the paper does not demonstrate it either) |
| L9 | Agents propose, implement and validate the mechanisms (LLM roles, end to end). | ye-blog, blog | qualitative | `LLMMechanismProposer`, `LLMReviewer` (`mocks.py`) | Live r1–r3 had no survivor. Live r4 had one haiku-written EPR-like condenser survive the gate, the firewall and ood, but with the MockAgent backend. Cost $2.44 in total. | P |

### 2.5 Caveats (from the sources)

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| C1 | Gating one mechanism at a time lets small losses accumulate once mechanisms combine. | blog Capability floors | caveat | faithful; there is no composition re-gate by default | S6: individually tolerable losses add up to 4–13 points in 4/5 seeds. The subset table shows the composed stack is worse than AF + EPR. | R |
| C2 | The OCC gate prices only the cache rewrite, not the summarisation call. | code, [sec] vollero | caveat | `occ.py:92-95` has no summariser term. Our meter *does* bill the summarisation request (`runtime.py:281-283`), so the unpriced cost is real in our cost numbers. | S4 cost includes it | R |
| C3 | EPR checks prove the quotes are real, not that they are sufficient. `evidence: []` is accepted on success, or on a failure with no signal word. | code, [sec] vollero | caveat | faithful (`reducer.py:135`) | vectors.py: both empty-evidence cases accepted. The S7 caveat notes that the deterministic reducer drops per-test lines on very long logs (repofix 0.97 → 0.88). | R |
| C4 | Fewer solves off EdgeBench (TB4 15 vs 18; IMO 3/6 vs Codex 5/6). | [sec], ye-blog | caveat | – | – | NT |
| C5 | Held-out data is partly reused: 11 acceptance tasks sit inside the 51-task headline. | [sec] pengqian, vollero | caveat | acceptance and final splits kept apart (R6) | S5 reports only the untouched final split | R |
| C6 | Search on one backend; mechanisms trigger less on the other backend but remain net-positive. | [sec] pengqian Q10, ye-blog | caveat | – | Q12 direction; the full stack is still net-positive on B (S7) | R |
| C7 | The shipped ObservationPack excerpt (512 / 512) differs from the blog's selected V2 (2,048 head / 1,536 tail). | code vs blog | caveat | We follow the release: `PLACEHOLDER_EXCERPT_BYTES=1024`, `head_frac=0.5`. | – | R |
| C8 | The selected V2 missed its own 10% cost gate (9.1%). | blog fig | caveat | – | – | NT |
| C9 | Numeric tolerances, search prompts, environment manifests and discovery cost are unpublished; there is no run-to-run variance. | [sec] vollero | caveat | our 2% / 2% tolerances are inferred | – | NT (a fact about the paper) |
| C10 | Humans set priors, filtered ideas and refactored the survivors' code. | blog | caveat | – | – | NT |
| C11 | OCC uses a fixed per-session ρ, assumes Pi's 20,000-token retained tail, and must abort the run to compact. | code docs | caveat | `occ.py:236-249`, `:355` `rt.abort()` | – | R |
| C12 | The EPR secret regex "is a precaution rather than a complete secret scanner". | code SECURITY.md | caveat | verbatim regex `reducer.py:61` | – | R |

## 3. Mismatches newly found by this audit

None of these appeared in the impl doc §5 or §8, `AUDIT.md` or `RUNS.md`. Each was checked by the scratch scripts named in §0.

1. **OCC plan snapshot format.** `occ.py:219-220` emits `<sol-pi-plan …>[{"id": "build", …}]` (a Python list with spaces). The ref `plan.ts:formatPlanSnapshot` emits `JSON.stringify({steps})`, i.e. `{"steps":[…]}`. The ref vector fails. This changes the agent-facing tool result, but in token count only.
2. **OCC plan advice.** `analyze_plan_transition` (`occ.py:210-216`) emits only "Keep at most one step in_progress." The release also warns "Plan step … changed goal; reuse an id only for the same goal." and "Mark one pending plan step in_progress before starting it." The ref vector fails. This is agent-facing.
3. **OCC plan parsing is lenient.** `parse_plan_steps` accepts empty `id`/`goal` and extra keys; the ref requires non-empty strings and exactly 3 keys. `_progress` (`:223-230`) truncates over-long progress arrays and does not reject them or check the 1,000-char item limit; the ref schema rejects both.
4. **EPR `source_lines` is off by one** for any log ending in `\n`. Ours is `count("\n") + (0 if endswith("\n") else 1)` (`reducer.py:360-361`); the ref `archive.ts` is `body.split("\n").length`. The probe gives 3000 vs the ref's 3001. The value appears in the reducer input and in the receipt text.
5. **EPR lengths are measured in code points, not UTF-16 units.** A 400-emoji quote (800 UTF-16 units) is accepted by us and rejected by the ref's 600-char limit. The same applies to `maxChars` (600,000). This is an edge case.
6. **The ObservationPack excerpt splitter differs.** We use `str.splitlines(keepends=True)` (`obspack.py:80`), which also splits on `\r`, `\x0b`, `\x0c`, `\x1c–\x1e`, `\x85`, U+2028 and U+2029. The ref splits only after `\n`. On a log with `\r` progress bars, the head excerpt is 502 B in ours vs 5 B in the ref. The tail matches. This affects the text of placeholders for CR-heavy logs.
7. **`obs_recall` accepts a negative offset.** `int(args["offset"])` with offset −5 returns the last 5 bytes with `next_offset=0` and `eof=False`. The ref schema has `minimum: 0`. An LLM agent could be fed misleading pages.
8. **OCC `W` at `turn_end` does not follow Pi's `getContextUsage`.**
   - I read Pi 0.85.1 `agent-session.js:2708` and `compaction.js:131-156`: `tokens` = the last assistant usage (its prompt + output) + the estimated tokens of the messages after it.
   - Our "reported" value is the size of the last *request* (`occ.py:287-295`, `runtime.py:307`). It leaves out the boundary reply and its tool results.
   - Review fix 3 therefore implemented `max(reported, estimated)` with the wrong "reported".
   - **Quantified** (`occ_w.json`): a rerun with Pi semantics moves S4 cost by +0.5% (A 4–8 subtasks) and 0.0–0.07% in the other cells. The S4 verdict is unchanged.
9. **Action Fusion text details:**
   - an empty command output yields `…\n[then_run:succeeded]\n`, where the ref gives `…\n[then_run:succeeded]` (probe);
   - `write` reuses the *edit* `then_run` description (`fusion.py:76`), where the ref has a separate `WRITE_THEN_RUN_DESCRIPTION`;
   - an empty `then_run.command` silently skips the command.
10. **Action Fusion path canonicalisation.**
    - `resolve_tool_path("~/proj/a.py")` returns `proj/a.py` (cwd-relative), where the ref resolves it against `homedir()`.
    - There is no `realpath` queue key, so a symlink and its target get different locks.
    - These are harmless in the virtual workspace but diverge from `file-queue.ts`.
11. **Runtime double projection.** On a turn where Pi-style auto-compaction fires, `runtime.py:298-304` calls `project()` twice. The ObservationPack `context` handler counts one provider request as two sends, so a large result can be packed one request early. There is also a threshold difference: we compare `ctx >= window − reserve`, while Pi's `shouldCompact` uses strict `>` (`compaction.js:163`).
12. **Stale domain docstring.** `domain.py:17-20` says `holdout` holds acceptance tasks of `datalookup` only. `HELDOUT_FAMILIES = ("configfix", "datalookup")` and the code put both families in `holdout` and `ood`.
13. **Idea pool is missing family M.** It has no "Improvement & evaluation" idea (`mocks.py:36-55`); this is the paper's largest family, 46 of 152. Also, `n_lineages` = pool size means the Oracle Analysis never excludes an idea.
14. **Source discrepancy, not a code issue.** `solpi_inkeast.md` lists the Opus SoL-Pi score as 43.7. That is Claude Code's native score (43.69). The other sources and the blog SVG give 42.22. I did not use inkeast's value.

Confirmed *not* mismatches:
- `fromExtension` handling (§2.1 M12).
- The economics and state vectors.
- EPR check order and fallback reasons.
- ObservationPack id, threshold, sends and placeholder text.

## 4. What would be needed to fully reproduce

1. **A real agent backend.** Run `LLMAgent` with at least one frontier model and one different-vendor model at high effort, on long-horizon executable tasks (hours, 1e8–1e9 tokens per suite). This is the only way to test Q3 (the ~6% quality loss), Q7 (single mechanisms *raising* score), and the ObservationPack sweep (Q13), where re-reading an old output actually matters. Estimated cost: thousands of USD (the paper reports $894–$2,535 per 51-task arm).
2. **The benchmarks.** EdgeBench's 51 public tasks, with the 11 / 40 split reported separately. Terminal-Bench 4 (CPU subset), IMO 2026 with the Lean 4 harness, and the kernel take-home swarm. Real Pi 0.85.1 (now downloadable), not our Python runtime, plus the native Codex and Claude Code harnesses as baselines, at dated real API prices.
3. **Protocol scale.**
   - About 150 ideas in all six families (including M), with the oracle actually filtering.
   - Hundreds of validity-filtered repository environments plus verifier-first environments with graded scores.
   - At least 3 repeated runs per arm.
   - An equal-budget breadth-vs-depth comparison (S8), which the paper did not run.
4. **Fix the fidelity items in §3.** Items 1–8 and 11 are cheap. After the fixes, port the remaining vitest suites (plan, ObservationPack projection/placeholder stability, EPR fused-result vectors) as regression tests.
5. **Decide the unpublished parameters.** These are the capability tolerances, the held-out pass criterion, and cross-lineage nondominated retention. Report sensitivity to them, because S6 shows the 2% floor admits a turn cap.
6. **An LLM reducer at volume.** Run a cheap-model reducer over hundreds of real build and test logs, measuring acceptance, fallback reasons and downstream solve rate. This tests the sufficiency caveat (C3) and the deterministic-reducer artefact in the S7 long-log caveat.

## 5. Evidence index

- Spec: `docs/methods/meta-harness-and-sol-pi.md` Part B.
- Impl notes: `docs/methods/metaharness-solpi-impl.md` §3–§9.
- Results: `results/metaharness-solpi/s1_action_fusion.json` … `s7_composition.json`, `live_smoke.json`.
- Validation: `validation/metaharness-solpi/{AUDIT.md,RUNS.md}` and the directories `solpi_agentworld_offline/`, `solpi_agentworld_live{,_r2,_r3,_r4}/`.
- Tests: `tests/test_metaharness-solpi_mechanisms.py`, `_solpi.py`, `_genericity.py`, `_validation.py`.
- This audit's scratch checks (outside the repo): `/tmp/claude-0/-home-user-RSI/ebd00391-ba98-5b98-9125-83abd1dce979/scratchpad/claims_solpi/{vectors.py,vectors.json,fusion_probe.py,occ_w.py,occ_w.json}`. The Pi package used for the host-semantics checks is in `…/scratchpad/npmpk/pi/package/`.
