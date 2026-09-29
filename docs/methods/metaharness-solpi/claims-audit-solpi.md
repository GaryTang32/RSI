# SoL-Pi: claim-by-claim audit of `rsi.solpi`

Paper: Liu, Ye et al. (NVIDIA, NTU, MIT), *SoL-Pi: Recursively Scaling Auto-Research Loops for Efficient Agent Harness*, arXiv 2609.20519 (17 Sep 2026).
Audited code: `rsi/solpi/`, `rsi/domains/agentworld/`.
Audit date: 2026-09-25.
The audit itself changed no code. A later fix pass (stage C, same day) resolved every open finding; §6 "Fix log" lists each one with its fix, regression test and evidence, and the rows below carry the post-fix verdicts.

## 0. Honesty statement: what this audit could and could not see

- **I could not read the paper text.** arXiv, alphaxiv, HF and hyper.ai were all blocked, and no mirror of the full text was found. Every paper-level claim below therefore rests on one of these:
  - the authors' project page (gh-pages source; the `[blog]` tag in the spec);
  - co-first author Tian Ye's blog (`[ye-blog]`);
  - secondary summaries in `scratchpad/papers/solpi_secondary/`. Of these, `pengqian` says it was built from the full arXiv HTML, including Tables 1–4. `vollero`, `inkeast`, `capsule`, `nbardy`, `inmatrix`, `j8` and `hundeok` are shorter.

  A claim that appears only in a secondary summary is marked **[sec]**.
- **Mechanism fidelity is checked against primary code.** The reference is `NVlabs/SoL-Pi` @ `1559b5c` (`scratchpad/src/NVlabs__SoL-Pi`) and its vitest suites. For this audit I also downloaded Pi itself (`@earendil-works/pi-coding-agent@0.85.1` from npm, into `scratchpad/npmpk/pi/`) so I could check two host-side semantics: `getContextUsage` and `fromExtension`.
- **Nothing here runs at GPT-5.6 Sol / Claude Opus 5 scale.**
  - Every quantitative result in the repo comes from AgentWorld, a CPU simulation with 5 environment families and deterministic `MockAgent` backends A and B.
  - The only live LLM runs are small haiku runs: $2.44 in total for this method's validation, plus $0.37 for the stage-C re-run (r5). The LLM plays the implementer, reviewer and reducer roles, never the agent backend.
  - The paper's magnitudes (EdgeBench, Terminal-Bench 4, IMO 2026, the swarm) cannot be tested here.
  - Where our numbers agree with the paper, it shows that the mechanisms behave as claimed *under our simulated world*. It does not show that the paper's effect sizes transfer.
- **Offline checks run for this audit.** No LLM calls; about 25 s of CPU in total. The scripts live outside the repo in `scratchpad/claims_solpi/`:
  - `vectors.py` → `vectors.json`: ports the reference state, plan, EPR and ObservationPack test vectors, plus probes of TS semantics.
  - `fusion_probe.py`: Action Fusion edge semantics.
  - `occ_w.py` → `occ_w.json`: reruns the S4 OCC arm with Pi's `getContextUsage` semantics for `W`. 5 seeds × 4 length bins × 2 backends.

## 1. Summary

After retry round 2 (§7, 2026-09-29), with the stage-C figures for comparison:

| verdict | count after retry round 2 | after the stage-C fixes | before the fixes |
|---|---|---|---|
| REPRODUCED | 32 | 31 | 27 |
| PARTIAL | 11 | 12 | 15 |
| NOT REPRODUCED | 1 | 2 | 3 |
| NOT TESTABLE HERE | 17 | 16 | 16 |
| CONTRADICTED | 0 | 0 | 0 |
| **total claims** | **61** | 61 | 61 |

By type (after retry round 2):

| type | claims | R | P | NR | NT |
|---|---|---|---|---|---|
| mechanism | 14 | 14 | 0 | 0 | 0 |
| research protocol (mechanism) | 9 | 7 | 1 | 0 | 1 |
| quantitative | 17 | 1 | 5 | 1 | 10 |
| qualitative | 9 | 2 | 5 | 0 | 2 |
| caveat | 12 | 8 | 0 | 0 | 4 |

**Retry round 2 verdict changes** (§7):
- **R7:** PARTIAL → REPRODUCED. It had been judged on cost, which is Q2's claim. Its own preregistered test passes on 20 fresh seeds.
- **Q3:** NOT REPRODUCED → NOT TESTABLE HERE. The MockAgent cannot lose quality by this channel, and a power analysis shows a live test needs ~100–400 frontier-agent task pairs.
- **Everything else examined keeps its verdict,** with new evidence:
  - Q13 stays NOT REPRODUCED: two preregistered 20-seed long-session runs, V2 bill saving ≈ 0%.
  - L4 stays PARTIAL: its preregistered test FAILED on fresh seeds; see 7.3.
  - L2 stays PARTIAL: its preregistered test FAILED narrowly.
- **One implementation fix (R2-F1):** Pi's compaction summary is a standalone uncached request. The fix moves cost numbers by up to ~4 points (S7 A default cost −15.8% → −19.6%). S4's verdict and M14 are unchanged.

Stage-C verdict changes (2026-09-25): M10, M13, R1, R4 PARTIAL → REPRODUCED; L4 NOT REPRODUCED → PARTIAL. Every other row keeps its verdict; the evidence of the re-run experiments (S1–S7, the offline validation run) is updated in place.

- **The four runtime mechanisms are faithful ports.**
  - Every formula in OCC's economics matches the release (`decideCompaction`, `estimateRemainingRequests`), and the reference economics and state vectors pass exactly.
  - Receipt validation matches `receipt.ts` check for check.
  - The ObservationPack constants and id scheme are identical.
  - The 13 fidelity mismatches this audit found in our code (§3 items 1–13) are fixed, with regression tests (§6). The auditor's reference-vector probe now passes 28/28 (was 20/28). The fixes are behaviour-neutral for the MockAgent in S1–S3 (identical results) and move S4's OCC-vs-late cost comparisons by at most 2.5 points (verdict unchanged).
- **Protocol claims reproduce at toy scale.** The protocol here is 12 ideas in all six families (the oracle ranks them and the top 10 get rollouts) over 5 simulated families.
  - The firewall and the dual gate are faithful; the gate's predeclared capability metrics are now the mean score AND the fully-solved rate.
  - Lineages keep the nondominated best-η variant by default.
  - Diversity-driven survival is PARTIAL.
  - The capability floor now rejects both do-less shortcuts (S6), but a composed stack still loses 3.8 points to an environment-specific trick that passes the per-mechanism floor (L4 PARTIAL).
  - Retry round 2 (20 fresh seeds) found the floor is only as strong as its screen sample. It admitted the 24-turn cap in 2/20 seeds. Tail-trim@120, admitted in 13/20, loses 4–11 points on unseen tasks in 7 of them. The preregistered L4 test FAILED (7.3).
- **Headline numbers do not transfer as claimed.**
  - Tokens: the simulation shows a larger cut than the paper (−54…−69%).
  - Cost: "about a third" appears only in some cells (−16…−61%). The stack actually composed by the protocol saves just 8.9%.
  - Quality: the paper's ~6% quality loss never appears, because the mock agent's quality does not degrade. Q3 is NOT TESTABLE HERE since retry round 2 (power analysis, 7.3).
  - The ObservationPack bill gate is unreachable in this world for a domain reason: the replayed outputs are at most 5–6% of the simulated bill (Q13). Two things explain this:
    - 89–100% of the large outputs in the long-running families (repofix, configfix) are failing commands (64–82% in buildfix), which the release never packs (Pi's bash tool throws on a non-zero exit, so they are `isError`);
    - long sessions do not help: V2 saves ≈ 0% (retry round 2, 7.3).

    The paper's own EdgeBench add-one row for ObservationPack is also below the gate: −5.1% cost ($1,339 → $1,271, vollero Table 4).

## 2. Claim table

Legend:
- **Verdicts:** R = REPRODUCED, P = PARTIAL, NR = NOT REPRODUCED, NT = NOT TESTABLE HERE.
- **Paths:** results are `results/metaharness-solpi/`; validation runs are `validation/metaharness-solpi/`.
- **Code:** references are `file:line` in our code, and "ref" is the NVlabs TypeScript under `src/sol-pi/extensions/`.

### 2.1 Mechanisms (runtime extensions)

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| M1 | Action Fusion: `edit`/`write` take an optional `then_run {command, timeout?}`. Mutation and command run in one tool call and return one observation. | code, blog, [sec] | mechanism | Faithful. `fusion.py:139-148` replaces `edit`/`write` and calls the built-ins, each with its own `then_run` description (`EDIT_`/`WRITE_THEN_RUN_DESCRIPTION`, `:52-61`). `fusion.py:174-214` ≙ ref `then-run.ts:78-127`. Success appends `[then_run:succeeded]\n<out>`, or just the marker for empty output (`:212`). A non-zero exit keeps the edit and returns `mutation\n\n[then_run:failed]\n\n<err>` with empty parts dropped, as an error (`:209`). A failed mutation adds the `skipped` suffix. A malformed `then_run` is a validation error; an empty command is run, not skipped. §3 items 9–10 fixed (§6). | `tests/metaharness-solpi/test_metaharness-solpi_mechanisms.py:90` (markers); `test_af_text_details_match_then_run_ts`. S1 safety checks 4/4 (`s1_action_fusion.json` `safety_checks`). | R |
| M2 | Hash guard and per-file queue: hash, yield, hash again. If the content changed, `[then_run:skipped] target content changed…`; the command is not run. | code | mechanism | Faithful: `fusion.py:153-170` ≙ ref `assertUnchangedBeforeCommand`, including the release's ENOENT text for a vanished target. The queue is keyed on `canonical_queue_key` (`:88-96`), i.e. the `realpath` like `file-queue.ts:181-197`, so a symlink and its target share one lock. Production code now yields between the hashes (`time.sleep(0)`, `:99-101`, the analogue of `setImmediate`); the test hook only replaces it. | `test_af_hash_guard_yields_by_default_and_reports_enoent`, `test_af_queue_key_is_realpath_so_symlinks_share_a_queue`; `scratchpad/claims_solpi/fusion_probe.py` (after: ENOENT text) | R |
| M3 | Fusion removes the decision-free turn ("1 model round-trip avoided"; "3 API calls → 2"). | code TUI, [sec] pengqian Q5 | mechanism | as M1 | S1, backend A: requests −23.1% [−21.8, −24.0], tokens −20.4%, cost −11.7%, success Δ 0.000. Backend B: requests −9.3%. | R |
| M4 | ObservationPack. A pure-text, non-error result over 10 KiB (bytes > 10,240) is sent in full on its first 2 provider requests. After that it becomes a stable placeholder with a whole-line 512 + 512 B head/tail excerpt and id `obs_`+sha256(tool‖\0‖callId‖\0‖sha256(text))[:24]. | code, [sec] | mechanism | Faithful: `obspack.py:39-48` constants; `create_observation` ≙ ref `createObservation`; `placeholder_for` ≙ `placeholderFor` (same text); `_context` ≙ the `context` handler, including `prev = sentCounts ?? #assistant-after`. Fixed (§6): the excerpt splits only after `\n` (`obspack.py:96-107`); placeholder ledger rows carry `originalLines` (`:217`); token estimates use UTF-16 length; the runtime projects once per request, so auto-compaction turns no longer count two sends (`runtime.py:344`). | `test_obspack_placeholder_after_full_sends_and_recall_fidelity`, `test_obspack_excerpt_splits_after_newline_only`, `test_obspack_placeholder_ledger_entry_has_original_lines`, `test_runtime_projects_once_per_request_even_when_auto_compacting`; vectors_after.py 28/28 | R |
| M5 | Exact paged recall with `obs_recall` (≤ 16,384 − 512 B, ≤ 400 − 2 lines, UTF-8 safe). A packing error fails open. | code | mechanism | Faithful: `read_recall_chunk` ≙ `readRecallChunk`; the recall tool with its hard-limit check; fail-open. Fixed (§6): arguments are validated like the release schema `{id: string, offset?: integer ≥ 0}` (`obspack.py:234-250`), so a negative offset is rejected instead of paging from the end. There is no symlink / `O_NOFOLLOW` handling, because the store is in memory (documented). | S2: recall fidelity 100% over 300 payloads, including multi-byte UTF-8. Injected storage failures do not change quality (`s2_observation_pack.json`). `test_obs_recall_rejects_negative_and_non_integer_offsets` | R |
| M6 | ObservationPack skips EPR receipts, so verified evidence is not compressed twice. | code, [sec] pengqian | mechanism | Faithful: `obspack.py:85-86` (any line == `sol_pi_evidence_receipt_v1`). | `test_obspack_rules_threshold_errors_receipts_utf8_and_fail_open` | R |
| M7 | EPR trigger. A bash command, or `then_run.command`, matching `DIAGNOSTIC_COMMAND` with a body ≥ 4,096 B. Over 600,000 chars → `source-over-max-chars`; a `LIKELY_SECRET` match → `likely-secret`. The exact untruncated `/tmp/pi-bash-*.log` is used when Pi truncated the output. | code, [sec] | mechanism | Regexes are verbatim. Order of checks as in ref `index.ts:reduceToolResult`. `exact_body` ≙ `exactBodyFromInline`. `reducible_tool_result` ≙ `candidate.ts`, including the separator rule. Fixed (§6): `maxChars` counts UTF-16 units like JavaScript; an archive integrity failure now throws like `archive.ts`, and the runtime keeps the original result and records an extension error, as Pi's runner does (it used to journal a `model-call-exception` fallback). | S3; `test_epr_handles_fused_then_run_results`; `test_epr_lengths_are_utf16_units` | R |
| M8 | EPR receipt validation. A receipt is accepted only if all of these hold:<br>• it is JSON;<br>• the schema matches;<br>• `source_sha256` matches;<br>• the status matches `is_error`;<br>• `uncertain` is a bool;<br>• it has ≤ 12 items;<br>• each item's kind is in the allowed set;<br>• each quote is 1–600 chars and an exact substring of the log.<br>Duplicates are dropped. A failing log with a `FAILURE_SIGNAL` must carry fatal or failure evidence. | code, [sec] | mechanism | Faithful, check for check: `validate_receipt` ≙ ref `receipt.ts:validateReceipt` (same rejection reasons, same order). `receipt_text` ≙ `receiptText`. Fixed (§6): `source_lines = body.split("\n").length` (`reducer.py:81-83`) and quote lengths in UTF-16 units (`:143`). | S3: accepted receipts contained 0 non-verbatim quotes for h ∈ {0, .1, .3, .6, 1}. Acceptance 1.00/.90/.67/.31/.00 (unchanged on the re-run). Failure logs always carried failure evidence (`s3_reducer.json`). The ref vector ("line=2", status=failure, smaller than the source) passes; the invented-quote vector gives `unverifiable-quote`. `test_epr_source_lines_is_split_length`, `test_epr_lengths_are_utf16_units` | R |
| M9 | EPR falls back to the untouched raw output on any failure, or when the receipt is not smaller than the log. | code | mechanism | Faithful: `reducer.py:449-466` (`model-response-error` when stop ∉ {stop, length}; `receipt-not-smaller`). | `test_validate_receipt_all_rejection_reasons`; S3 fallback histogram | R |
| M10 | The EPR reducer is a separate low-cost model (GPT-5.6 Luna, "high" effort) with a 90 s timeout, `maxTokens` = min(2048, model.maxTokens) and `cacheRetention` none. | [sec] pengqian; code provider.ts | mechanism | The route is configurable: `LLMReducer` (`reducer.py:272-319`). Fixed (§6): the call runs under the 90 s deadline (a late reply is `model-call-timeout`); `max_tokens = min(2048, llm.max_tokens)` when the backend declares a limit; the backend's stop reason (`raw["stop_reason"]`, kept on cache hits) is mapped like pi-ai's Anthropic `mapStopReason`, so a refusal becomes `model-response-error` instead of a silent "stop". No effort setting (the release sets none either). `cacheRetention` has no counterpart: our backends expose no cache knob and each log is one independent call. The model is haiku, not Luna. Offline, the `DeterministicReducer` is our own stand-in. | Live: haiku reducer on 4 logs, 4/4 accepted, 0 non-verbatim, 91,825 → 6,028 B, $0.18 (`live_smoke.json` `parts.epr`); re-parsed at $0 with the fixed `LLMReducer` (4/4 cache hits, identical outcome). `test_llm_reducer_timeout_max_tokens_and_stop_reason` | R |
| M11 | OCC economics. Symbols: `A = max(0, W−F−K)` with K = 20,000; `S = A − m` with m = 1,000; `b = W·max(0, ρ−1)/S`; `b_c = (D + W·ρ')/S`. Horizon: `μ`, `L` (the k·s and small-sample rules), `R_unb = 1 + ⌊L·B_rem·scale⌋`, `R_win = max(0, ⌊(W_win − C)/Δ̄⌋)`, `R = min(R_unb, R_win)`. First compaction: `R_eff = min(2R, R_win)`. Later compactions: `b ≤ R ∧ 1.5b ≤ R ∧ b_c ≤ R`. Window protection: `C ≥ W_win − 16,384`. Compact iff `S > 0 ∧ (window ∨ economic)`. The reason precedence is part of the claim. | code economics.ts, [sec] pengqian Q5 | mechanism | Exact port, line for line: `occ.py:69-89` ≙ `estimateRemainingRequests`; `:92-154` ≙ `decideCompaction`, including every intermediate and the reason ladder. Constants: `:44-66`. | All 6 ref `online-context-compact-economics.test.ts` vectors pass exactly (`test_occ_economics_test_vectors`; `s4_occ.json` `economics_vectors`). | R |
| M12 | OCC state and cache-debt accounting:<br>• each request: `D ← max(0, D−r)`, and `r ← 0` once D = 0;<br>• a boundary appends the interval;<br>• a compaction: epoch+1, reset, count+1, `D = W·ρ'`, `r = A−m`, set to 0 if `fromExtension`;<br>• a correction resets. | code state.ts, extension.ts | mechanism | Exact: `occ.py:175-201` ≙ `state.ts`. `_session_compact` `:493-497` ≙ the ref handler. I verified in Pi 0.85.1 (`dist/core/agent-session.js:1495-1512`) that `fromExtension` is true only when a `session_before_compact` hook supplies the summary. Our default `from_extension=False` for OCC's own compaction is therefore correct. | All 6 ref `online-context-compact-state.test.ts` vectors pass (vectors.py `state.*`), except restore-from-session, which is not modelled. | R |
| M13 | OCC runtime loop:<br>• `update_plan` marks boundaries;<br>• at a clean `turn_end` it prices the compaction, checks native feasibility, then aborts;<br>• at `agent_settled` it compacts with `BOUNDARY_COMPACTION_INSTRUCTIONS`;<br>• it then sends the hidden `POST_COMPACTION_PLAN_REMINDER`. | code extension.ts, tools.ts, plan.ts | mechanism | Control flow is faithful; the instruction and reminder strings are verbatim. Fixed (§6): the `{"steps":[…]}` snapshot, the release's three advice lines and strict parsing / argument validation (`occ.py:216-345`); `W` follows Pi's `getContextUsage` (`occ.py:401-411`, `runtime.py:269-290`); `turn_end` returns early on an error / aborted reply or an aborted signal (`occ.py:454`). Session restore and tree events are not modelled (no session files in the runtime; documented). | `test_occ_compacts_at_boundary_and_reminds`; `test_plan_*` (the release's plan vitest vectors), `test_occ_write_tokens_follow_pi_get_context_usage`, `test_occ_turn_end_skips_aborted_or_error_replies`. vectors_after.py: plan vectors 11/11. S4 re-run: verdict unchanged. | R |
| M14 | OCC compacts only when the savings repay the cache rewrite. It is better than compacting at every boundary or never compacting, and near-best overall. | code, blog, [sec] | mechanism | as M11–M13 | S4 (re-run after the §6 fixes), 4 arms × 4 lengths × 2 backends:<br>• OCC's η is within 5% of the best arm in 8/8 cells and best in 5;<br>• 0 compactions with S ≤ 0;<br>• compacting at every boundary costs +30% vs late compaction (was +28%);<br>• never compacting overflows up to 40% of runs (`s4_occ.json`).<br>**Retry round 2** (after R2-F1, which bills compaction summaries as standalone uncached requests like Pi): S4 re-run, verdict unchanged. OCC is within 5% of the best arm in 8/8 cells, and compacting at every boundary now costs +34% vs late compaction (was +30%). | R |

### 2.2 Research protocol

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| R1 | Breadth: 152 directions in 6 families (C 24, P 26, T 26, D 15, R 15, M 46), ranked by an Oracle Analysis of existing trajectories before rollout budget is spent. | blog, [sec] | mechanism | `research.py:41-81` (`Idea`, `IdeaPool`, `oracle_estimate`); pool in `mocks.py:39-63`. Fixed (§6): the pool has 12 ideas over all six families, including family M (M5 cost attribution, M12 fail-before/pass-after); the turn cap's oracle statistic is the work a cap could avoid (`late_turn_tokens`, was the system-prompt share); 12 ideas against the default `n_lineages` = 10, so the oracle ranks AND filters. The scale (12 vs 152 ideas) is not reproduced (Q15/Q16). | `solpi_agentworld_offline` re-run: oracle ranks 12 ideas, R5 (0.002) and M5 (0.05) get no rollouts (trace seq 4; audit `oracle_selection_recomputed` 1/1). `test_idea_pool_covers_family_m_and_the_oracle_filters`, `test_protocol_runs_only_the_oracle_top_ideas` | R (mechanism, toy scale) |
| R2 | Depth: one disposable Karpathy-style lineage per idea. The stages are rollouts, map-reduce analysis, one mechanism, a Ralph loop until an exit check passes, an independent reviewer (rejection → implementation) and in-trajectory validation. The orchestration is discarded and only the candidate and its evidence are kept. | blog, [sec] | mechanism | `research.py:205-342` in that stage order. `implement()` `:158-182` is the Ralph loop. Review → re-implementation `:285-302` (fix 16). A gate failure always routes to 01, never to 04: a documented deviation. | Offline run: 15/15 gate decisions recomputed (AUDIT §2.4); stage-C re-run 18/18 (AUDIT §6c). Live r4 and r5: a haiku-written condenser survived end to end after a reviewer → implementation repair (AUDIT §2.6, §6c). | R |
| R3 | A predeclared dual gate: every capability metric within τ **and** at least one efficiency metric improved. Metrics and tolerances are fixed before search and outside the optimizer's control. | blog, ye-blog, [sec] | mechanism | `gate.py:53-66` is a frozen, digested `GateSpec`; `accept`. Since §6 the predeclared capability metrics are the mean score AND the fully-solved rate (a missing metric fails closed). τ = 2% relative and min_gain = 2% are inferred, because no tolerances or metric lists are published. | `gate_digest` in run_start; offline re-run: 18/18 gates recomputed | R |
| R4 | "Among candidates that pass the capability floor, the loop retains nondominated results." | blog, [sec] | mechanism | Fixed (§6): `Config.sweep=True` is the default. A lineage keeps iterating after a pass and freezes the nondominated passing variant with the best η = cost / score (`research.py:334-342`; `gate.py` `nondominated`); `sweep=False` restores first-pass freezing. Survivors of different lineages are different mechanisms and are composed, not filtered for dominance (our reading, documented). | Offline re-run: the C23 lineage evaluated all 4 ObservationPack variants (all pass; all raise cost 5.4–9.5%) and froze the best-η one. `test_lineage_sweeps_and_freezes_the_nondominated_best_eta_variant` | R |
| R5 | Held-out firewall: a frozen candidate is evaluated once, no agent in the loop sees the result, and a failure rejects without becoming feedback. | blog, [sec] | mechanism | `gate.py:222-262`: a write-only sink, a bool return, and an exception on a second evaluation. | `test_firewall_is_one_way_and_lineages_cannot_read_holdout`; sink == trace (AUDIT §2.4) | R |
| R6 | EdgeBench split: 11 of 51 tasks for one-way acceptance, 40 for final evaluation only. | [sec] pengqian Q5, vollero | mechanism | Analog: `holdout` (acceptance, used only by the firewall) and `ood` (final, never touched by the protocol), `domain.py:79-83`. S5 reports the final split only, which is stricter than the paper's 51-task aggregate. | S5 held-out numbers are on `ood` (`s5_survive.py:72-74`) | R |
| R7 | Composition: the survivors are merged as independent opt-in extensions, each gated one at a time, and small losses may accumulate. | blog, [sec] | mechanism | `research.py:403-428` `compose`: a JSON-config union. Faithful, with no re-gating (`validate_composition` is an inferred option, off by default). | Offline protocol stack (re-run after §6, same survivors D1, P8, C23): +8.9% cost saving, against +33.5% for EPR alone. AF + EPR is the best subset (AUDIT §2.4 subset table). The loss comes from the ∃-efficiency rule admitting cost-raising ObservationPack variants (every variant saves tokens but raises cost) and the EPR × OP interaction under the MockAgent.<br>**Retry round 2** (P-R7, 20 fresh seeds, `s6b_floor_fresh_seeds.json`): PASS. The composed harness is the union of the survivors' extensions in 20/20 seeds. Losses accumulate: the composed `evolve` success falls > 0.5 points below the worst standalone survivor in 19/20 seeds, and composed retention is 0.949 [0.930, 0.967] (the paper: ~94%). The old PARTIAL judged cost, which is Q2's claim. | R (mechanism, toy scale) |
| R8 | Search environments: 535 of them, 495 repository issue→PR tasks kept only if the regression test fails before the fix and passes after, plus 40 verifier-first tasks with graded scores. None is derived from EdgeBench. | blog, ye-blog, [sec] | mechanism | `agentworld/base.py` `validity_filter` (fails before, passes after the reference fix). Only 5 simulated families (repofix, buildfix and logtriage for training; configfix and datalookup held out). Every family is scored as a fraction of subtasks (graded), and logtriage is verifier-first, but there are no synthesised verifier-first environments at scale. Not a code defect: a scale limit (documented). | `test_every_environment_passes_the_validity_filter`<br>**Retry round 2:** root cause (c). The validity filter is faithful and every family is graded. The claim is about scale (535 environments, 495 repository issue→PR tasks); a CPU analogue with 5 simulated families cannot show it. Needed: hundreds of real validity-filtered repositories plus verifier-first environments. No experiment; unchanged. | P |
| R9 | Search on GPT-5.6 Sol at xhigh; the frozen stack is applied to Opus 5 "without any further search". | blog, ye-blog, [sec] | mechanism | Analog: MockAgent A for search, B for transfer (`policy.py`). There is no real frontier backend. | – | NT (no frontier-model backend; cost) |

### 2.3 Quantitative results

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| Q1 | EdgeBench vs Pi: token traffic −49.0% (Sol, 2.1538 → 1.0990 B) and −44.7% (Opus, 2.3697 → 1.3101 B). | ye-blog, [sec] pengqian, vollero, doc | quantitative | `meter.py` (total tokens = cache_read + cache_write + output) | S7 (re-run after §6) full stack: −54.0% (A default), −58.4% (A long), −61.8% (B default), −68.5% (B long). The protocol-composed stack: −49% on screen, −69% held-out, −66% ood (offline re-run). The direction holds and the size is larger. It is a different world, not EdgeBench.<br>**Retry round 2** (S7 re-run after R2-F1): tokens −54.8% / −58.4% (A default / long) and −61.8% / −68.4% (B). In the long cells (the EdgeBench analogue) the CIs [56.8, 59.8] (A) and [68.1, 68.9] (B) exclude 49.0% and 44.7% (P-RERUN). The direction holds in 4/4 cells. Root cause (c). | P |
| Q2 | About one third lower API cost: −33.2% (Sol, $1,339 → $894) and −33.5% (Opus, $1,741 → $1,158). | same | quantitative | invented prices with ρ = 12.5 (`meter.py:34-39`) | S7 (re-run) full stack: −15.8% (A default), −30.8% (A long), −44.4% (B default), −61.1% (B long). The protocol stack saves only −8.9%. On backend A, EPR alone (−32.8%) is cheaper than the full stack.<br>**Retry round 2** (S7 re-run after R2-F1): full-stack cost −19.6% / −32.7% (A default / long) and −44.4% / −61.0% (B).<br>• A long: 32.7% [29.5, 35.2] contains the paper's 33.2%.<br>• B long: 61.0% [60.4, 61.5] does not contain 33.5% (P-RERUN).<br>Root cause (c). The protocol-composed stack is unchanged at −8.9% (offline validation re-run). | P |
| Q3 | At 93.7% (Sol: 44.833 → 42.003) and 94.3% (Opus: 44.756 → 42.224) of Pi's score. | ye-blog, [sec] | quantitative | – | Full-stack success ratio is 1.000 / 1.000 / 0.998 / 1.006. The quality loss is **not** observed: the MockAgent's skills read exactly the evidence the mechanisms preserve.<br>**Retry round 2:** root cause (c).<br>• The MockAgent has no channel for this loss.<br>• The stack's success ratio is 1.000 / 1.000 / 0.998 / 1.006 in S7's re-run.<br>• A live test is out of reach: with an assumed per-task paired SD of 10–20 points, detecting 44.83 → 42.00 at 80% power needs 98–392 task pairs. $4 of haiku buys about 14 short AgentWorld pairs, with a different model and different tasks (7.3). | NT (needs a frontier agent on EdgeBench-scale tasks; a paired test of −2.83 points needs ~100–400 task pairs, see 7.3) |
| Q4 | EdgeBench table: Codex 34.74 / $1,787 / 3.05 B; Claude Code 43.69 / $2,535 / 2.00 B. Hourly savings $8.75–13.50 vs native harnesses and $4.36–5.71 vs Pi. | blog figs, [sec] | quantitative | – | – | NT (needs EdgeBench and the frontier backends) |
| Q5 | Vs native harnesses: 35–64% fewer tokens and 50–54% lower cost (2.00× / 2.19× cost, 2.78× / 1.53× tokens). | blog | quantitative | – | – | NT (no native-harness baseline exists here) |
| Q6 | Table 1 also includes an unranked GPT-5.5 reference row and OpenSquilla, Oh-My-Pi, OpenCode and Oh-My-Opencode (numbers unavailable). | [sec] pengqian Q6 | quantitative | – | – | NT |
| Q7 | SoL-Pi [Performance]: ObservationPack on Sol scores 47.208 at $1,271; Action Fusion on Opus scores 50.482 at $1,605. | [sec] vollero, inkeast | quantitative | – | Single mechanisms never raise success in the simulation (S7 ratios ≈ 1.0). | NT (score gains require a real agent) |
| Q8 | Terminal-Bench 4 (63 CPU tasks): SoL-Pi solves 15, Pi 18, Codex 18. Cost $211.12 vs $286.45 (−26.3%); $14.07 vs $15.91 per solved task. | blog figs, ye-blog, [sec] | quantitative | – | – | NT |
| Q9 | IMO 2026 (6 problems, Lean-verified): 3/6, 3/6 and 5/6. Cost $62.69 / $75.95 / $114.47. | [sec] | quantitative | – | – | NT |
| Q10 | Kernel swarm (one 2 h run each): 1,127 cycles at $60.11 (SoL-Pi) vs 1,366 at $82.12 (Pi) vs 1,333 at $39.20 (single agent). 17.5% fewer cycles and 26.8% lower cost than the Pi swarm. | blog, ye-blog, [sec] | quantitative | – | – | NT |
| Q11 | Action Fusion oracle: 12.3% of cross-turn transitions are edit→command (bash 85.1%). Counterfactual −10.8% turns, −11.5% tokens. 87.7% uptake. 27-iteration lineage. | blog fig, ye-blog, [sec] | quantitative | `oracle_stats` in `domain.py`; `research.py:69-81` | S1: the oracle share is 32%, predicted −29.9% requests against −23.1% measured. The oracle-predicts-saving relation holds; the magnitudes are those of the simulated world.<br>**Retry round 2:** root cause (c). The testable relation (the oracle share predicts the measured saving) holds. The magnitudes (12.3%, −10.8% turns, −11.5% tokens, 87.7% uptake, a 27-iteration lineage) describe GPT-5.6 Sol trajectories on the paper's environments. No CPU analogue can produce them without being tuned to them; unchanged. | P |
| Q12 | Activation depends on the model: AF fires 70.58 vs 13.54 times per triggered task (Sol vs Opus); OCC triggers on 92.2% vs 33.3% of tasks. | ye-blog, [sec] | quantitative | trigger counters in `domain.py` | S7 (re-run): AF 3.29 (A) vs 1.91 (B) per triggered task. OCC triggers on 5% / 38% of tasks (A default / long) vs 0% / 22% (B). The direction is reproduced; the magnitudes are much smaller.<br>**Retry round 2** (S7 re-run): Action Fusion fires per triggered task at 3.29 / 13.1 (A default / long) vs 1.91 / 4.57 (B), a long-cell ratio of 2.87 (paper 5.2; within the preregistered 2× band). OCC triggers on 38.3% (A long) vs 21.7% (B long); A misses the ≥ 46.1% band (the paper: 92.2%). PARTIAL under P-RERUN. Root cause (c). | P |
| Q13 | ObservationPack sweep: V0–V7 with gates of 10% bill saving and −2% quality. V2 (2 sends) is "the only configuration inside the quality gate" (9.1% / −0.8%). The paired EdgeBench A/B gives bill −23.58% and score +22.92%. | blog fig | quantitative | `ObservationPack(excerpt_bytes, head_frac, full_sends)` | S2 (re-run, now with the blog's exact V2 point, 2,048 B head + 1,536 B tail / 2 sends): 9/9 configurations inside the quality gate, 0 reach the 10% bill-saving gate (bill −1.9…+3.3%, tokens −8…−15%). **A domain property, not a parameter one** (S2 `bill_decomposition`): replayed large outputs are only 5.9% (A) / 4.8% (B) of the base bill as cache reads, which is the ceiling for any packing. Packing cuts cache reads 15% / 11% but re-writes the prefix after each swap (cache writes +12% / +8%, priced 12.5×). Priced at ρ = 1 the same packing saves 5.8% / 6.1%, at the ceiling. Recall traffic is negligible (0.2 / 0.02 recalls per task). Longer sessions do not help: a scratch run at 16–20 subtasks gives bill −4…−5% for every setting (`scratchpad/claims_solpi/op_bill_probe.json`).<br>**Retry round 2:** root cause (d). The port is re-checked against `observation-pack/{index,observation}.ts`, including `isPureTextResult`'s `!message.isError`. Pi 0.85.1's bash tool throws on a non-zero exit, so failing test runs are never packed, in the release as here.<br>• P-Q13 (20 fresh seeds, 16–20 subtasks): V2 bill saving on backend A +0.3% [−0.1, 0.6].<br>• P-Q13b (logtriage crash fixed): −0.0% [−0.4, 0.3]. The CI half-width is ~0.4 points against the claimed 9.1, so the test could detect the effect.<br>• Why it fails: in the long-running families (repofix, configfix) 89–100% of the > 10 KiB outputs are errors, and the replay ceiling is 4–6% of the bill.<br>• The quality-gate half needs a real LLM (c). | NR (two preregistered long-session runs: V2 saves ≈ 0% of the bill; see 7.3) |
| Q14 | Add-one ablation: every mechanism reduces total tokens on its own under both backends, and the full stack has the lowest tokens **and** the lowest cost in both backend blocks (Table 4). | [sec] pengqian Q9 | quantitative | – | S7 (re-run): every mechanism saves tokens alone and the full stack has the fewest tokens in 4/4 cells, but the lowest cost in only 2/4 (backend B). On A, EPR alone is cheapest.<br>**Retry round 2** (S7 re-run plus the diagnostic `scratchpad/retry2/solpi/q14_probe.json`): unchanged at 2/4 cells. On backend A, EPR+AF is the cheapest configuration (−43% default / −57% long vs full −20% / −33%). Adding OP (−10 points of saving) or OCC (−3 / −10) to EPR raises cache writes, so the full stack is not cheapest there. Root cause (d), a property of this world. | P |
| Q15 | About 1 idea in 40 survived validation. | blog, ye-blog | quantitative | – | 3 of 10 survived offline (a library of mostly real mechanisms); 1 of 2 live (r4). This is a property of the pool design. | NT (scale) |
| Q16 | Search scale: more than 3,000 runs, more than 60,000 interactions, "not a scaling law". | ye-blog, [sec] | quantitative | – | – | NT |
| Q17 | cacheWriteReadRatio 12.5 (GPT-5.6 Sol cache write/read). | code agents-install.md | quantitative | `occ.py:350` default; `meter.py` prices keep write/read = 12.5 | config | R |

### 2.4 Qualitative claims

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| L1 | "Same quality for less cost"; "matched the original Pi harness". | doc, [sec] abstract | qualitative | dual gate (non-inferiority + efficiency) | The simulation keeps 100% of quality at lower cost (S7). The overview's word "matched" is itself stronger than the sources, which report 93.7–94.3%, and the simulation cannot show whether that loss occurs.<br>**Retry round 2:** unchanged. S7's re-run keeps success ratios 0.998–1.006 at lower cost in all 4 cells. Whether frontier quality is retained is Q3 (NT). Root cause (c). | P |
| L2 | "Keep only the changes that survive everywhere": search across diverse environments makes the survivors transfer. | doc, blog | qualitative | `per_family` gate option (`gate.py`) plus the firewall | S5 (re-run after §6):<br>• multi-family dual gate + firewall: held-out capability 1.000, η saving +0.39;<br>• a single-environment efficiency objective: 0.603 / +0.27;<br>• a **single-environment dual gate**: 1.000 / +0.40, statistically the same.<br>The floor and the firewall do the work; the extra environments only reject more tricks at the gate (3.0 → 1.6). With the two-metric floor the multi-family gate no longer admits the turn cap (do-less 1.0 → 0.0).<br>**Retry round 2** (P-L2, 20 fresh seeds, `s5b_diversity_power.json`; power ≈ 0.99 for S5's hinted effect): FAIL at the preregistered threshold. Held-out capability without firewall, multi − single, is +0.011 [−0.0015, +0.023] (A +0.002 [−0.009, 0.012], B +0.020 [0.000, 0.040]). With the firewall (secondary) it is +0.011 [+0.001, +0.022]. The multi-family gate admits half the tricks (1.5 vs 3.0). The effect is small and positive but not significant as preregistered. Root cause (d). | P |
| L3 | What survives transfers beyond where it was developed: to held-out tasks and to an unseen backend without further search. | doc, blog, [sec] | qualitative | – | S5: firewall survivors keep 0.99 of held-out success on backend B. The composed stack scores holdout 0.988 and ood 1.0 (RUNS). | R (in simulation) |
| L4 | The capability floor blocks "saving by doing less". | ye-blog, blog | qualitative | Fixed (§6): the predeclared floor has two capability metrics, mean score AND solved rate (`gate.py:56`) | S6 (re-run): the dual gate now rejects both do-less shortcuts in 5/5 seeds (no-verify and the turn cap at every length; the 24-turn cap scores 0.984 but finishes only 22/24 tasks). The composed training stack still loses 3.8 points (−0.038 [−0.063, −0.012], was −0.073): the environment-specific trick tail-trim@120 passes the per-mechanism floor in 3/5 seeds and loses only in composition (caveat C1; the firewall, not the floor, stops tricks in S5). S6's predeclared criterion (≤ 2 points composed loss) is therefore still not met: the script's verdict stays NOT REPRODUCED.<br>**Retry round 2** (P-L4, 20 fresh seeds, `s6b_floor_fresh_seeds.json`): FAIL.<br>• L4-1: 18/20 seeds admit no do-less candidate; the 24-turn cap passed the screen in seeds 12 and 18 and loses 1.7–2.1 points on unseen tasks.<br>• L4-2: PASS. Efficiency-only admits do-less candidates in 20/20 seeds and loses 49.7 points.<br>• L4-3: 108/115 admitted survivors within 2 points on the unseen `test` split, against a 95% threshold. Tail-trim@120 fails in 7 seeds (−4.2 … −11.5 points).<br>• The composed loss is −5.1 points [−7.0, −3.3]. Leave-one-out: T3 accounts for +6.0 points, Action Fusion +2.7, almost all through AF × T3.<br>The floor blocks most do-less savings, but only as far as its screen sample reveals them. Root cause (d). | P |
| L5 | Efficiency waste is task-independent, so efficiency mechanisms transfer where score hacks do not. | ye-blog | qualitative | – | General mechanisms pass on the held-out families, and tricks break there. But which tricks break is built into the simulated families' log layouts (S5 caveat).<br>**Retry round 2:** root cause (c). Which tricks break where is set by how the simulated families lay out their logs, so any AgentWorld test of this claim is circular. A non-circular test needs independently authored environments (e.g. repository tasks and Terminal-Bench-style tasks) and a real agent. No experiment; unchanged. | P |
| L6 | Breadth escapes local basins after 5–10 iterations of depth-first refinement. | blog, ye-blog ("qualitative observation") | qualitative | not implemented (spec S8) | – | NT (not run; the paper itself ran no equal-budget comparison) |
| L7 | The four mechanisms are useful for any long-running agent: opt-in extensions, no patches to the base. | doc, code README | qualitative | extensions only through `register_tool` / `on` (`runtime.py:206-224`) | Genericity tests (`tests/metaharness-solpi/test_metaharness-solpi_genericity.py`); `example_new_problem.py` part 2 | R |
| L8 | Recursive efficient improvement: SoL-Pi becomes the next base. | blog, [sec] ("long-term vision") | qualitative | `Config.rounds > 1` (`driver.py:72`), not evaluated | – | NT (the paper does not demonstrate it either) |
| L9 | Agents propose, implement and validate the mechanisms (LLM roles, end to end). | ye-blog, blog | qualitative | `LLMMechanismProposer`, `LLMReviewer` (`mocks.py`) | Live r1–r3 had no survivor. Live r4, and r5 under the stage-C defaults (fresh cache, $0.37), each had one haiku-written EPR-like condenser survive the gate, the firewall and ood, but with the MockAgent backend. In r5 the sweep ran a second haiku variant, which the first dominated. Cost $2.81 in total (stages A–C).<br>**Retry round 2:** root cause (c). The research roles are LLM-driven end to end (r4 and r5), but the agent backend in rollouts is the MockAgent. An LLM agent backend on AgentWorld costs about $0.14–0.5 per task run, and a gated lineage needs ≥ 2 × 24 screen runs per variant, which is over the $4 cap. No live spend; unchanged. | P |

### 2.5 Caveats (from the sources)

| # | claim | source | type | fidelity + code ref | our evidence | verdict |
|---|---|---|---|---|---|---|
| C1 | Gating one mechanism at a time lets small losses accumulate once mechanisms combine. | blog Capability floors | caveat | faithful; there is no composition re-gate by default | S6 (re-run): individually lossless survivors (tail-trim@120 with the four mechanisms) lose 4.8–8.3 points of training success in composition in 3/5 seeds. The subset table shows the composed protocol stack is worse than AF + EPR. | R |
| C2 | The OCC gate prices only the cache rewrite, not the summarisation call. | code, [sec] vollero | caveat | `occ.py:105-108` has no summariser term. Our meter *does* bill the summarisation request (`runtime.py:322-326`), so the unpriced cost is real in our cost numbers. | S4 cost includes it | R |
| C3 | EPR checks prove the quotes are real, not that they are sufficient. `evidence: []` is accepted on success, or on a failure with no signal word. | code, [sec] vollero | caveat | faithful (`reducer.py:150`) | vectors.py: both empty-evidence cases accepted. The S7 caveat notes that the deterministic reducer drops per-test lines on very long logs (repofix 0.97 → 0.88). | R |
| C4 | Fewer solves off EdgeBench (TB4 15 vs 18; IMO 3/6 vs Codex 5/6). | [sec], ye-blog | caveat | – | – | NT |
| C5 | Held-out data is partly reused: 11 acceptance tasks sit inside the 51-task headline. | [sec] pengqian, vollero | caveat | acceptance and final splits kept apart (R6) | S5 reports only the untouched final split | R |
| C6 | Search on one backend; mechanisms trigger less on the other backend but remain net-positive. | [sec] pengqian Q10, ye-blog | caveat | – | Q12 direction; the full stack is still net-positive on B (S7) | R |
| C7 | The shipped ObservationPack excerpt (512 / 512) differs from the blog's selected V2 (2,048 head / 1,536 tail). | code vs blog | caveat | We follow the release: `PLACEHOLDER_EXCERPT_BYTES=1024`, `head_frac=0.5`. | – | R |
| C8 | The selected V2 missed its own 10% cost gate (9.1%). | blog fig | caveat | – | – | NT |
| C9 | Numeric tolerances, search prompts, environment manifests and discovery cost are unpublished; there is no run-to-run variance. | [sec] vollero | caveat | our 2% / 2% tolerances are inferred | – | NT (a fact about the paper) |
| C10 | Humans set priors, filtered ideas and refactored the survivors' code. | blog | caveat | – | – | NT |
| C11 | OCC uses a fixed per-session ρ, assumes Pi's 20,000-token retained tail, and must abort the run to compact. | code docs | caveat | `occ.py:350-363`, `:479` `rt.abort()` | – | R |
| C12 | The EPR secret regex "is a precaution rather than a complete secret scanner". | code SECURITY.md | caveat | verbatim regex `reducer.py:66` | – | R |

## 3. Mismatches newly found by this audit

None of these appeared in the impl doc §5 or §8, `AUDIT.md` or `RUNS.md`. Each was checked by the scratch scripts named in §0. File:line references in this list are to the pre-fix code. **Status after stage C:** items 1–13 are FIXED (§6 gives the fix, file:line, regression test and evidence for each); item 14 is a source discrepancy and is DOCUMENTED.

1. **[FIXED] OCC plan snapshot format.** `occ.py:219-220` emits `<sol-pi-plan …>[{"id": "build", …}]` (a Python list with spaces). The ref `plan.ts:formatPlanSnapshot` emits `JSON.stringify({steps})`, i.e. `{"steps":[…]}`. The ref vector fails. This changes the agent-facing tool result, but in token count only.
2. **[FIXED] OCC plan advice.** `analyze_plan_transition` (`occ.py:210-216`) emits only "Keep at most one step in_progress." The release also warns "Plan step … changed goal; reuse an id only for the same goal." and "Mark one pending plan step in_progress before starting it." The ref vector fails. This is agent-facing.
3. **[FIXED] OCC plan parsing is lenient.** `parse_plan_steps` accepts empty `id`/`goal` and extra keys; the ref requires non-empty strings and exactly 3 keys. `_progress` (`:223-230`) truncates over-long progress arrays and does not reject them or check the 1,000-char item limit; the ref schema rejects both.
4. **[FIXED] EPR `source_lines` is off by one** for any log ending in `\n`. Ours is `count("\n") + (0 if endswith("\n") else 1)` (`reducer.py:360-361`); the ref `archive.ts` is `body.split("\n").length`. The probe gives 3000 vs the ref's 3001. The value appears in the reducer input and in the receipt text.
5. **[FIXED] EPR lengths are measured in code points, not UTF-16 units.** A 400-emoji quote (800 UTF-16 units) is accepted by us and rejected by the ref's 600-char limit. The same applies to `maxChars` (600,000). This is an edge case.
6. **[FIXED] The ObservationPack excerpt splitter differs.** We use `str.splitlines(keepends=True)` (`obspack.py:80`), which also splits on `\r`, `\x0b`, `\x0c`, `\x1c–\x1e`, `\x85`, U+2028 and U+2029. The ref splits only after `\n`. On a log with `\r` progress bars, the head excerpt is 502 B in ours vs 5 B in the ref. The tail matches. This affects the text of placeholders for CR-heavy logs.
7. **[FIXED] `obs_recall` accepts a negative offset.** `int(args["offset"])` with offset −5 returns the last 5 bytes with `next_offset=0` and `eof=False`. The ref schema has `minimum: 0`. An LLM agent could be fed misleading pages.
8. **[FIXED] OCC `W` at `turn_end` does not follow Pi's `getContextUsage`.**
   - I read Pi 0.85.1 `agent-session.js:2708` and `compaction.js:131-156`: `tokens` = the last assistant usage (its prompt + output) + the estimated tokens of the messages after it.
   - Our "reported" value is the size of the last *request* (`occ.py:287-295`, `runtime.py:307`). It leaves out the boundary reply and its tool results.
   - Review fix 3 therefore implemented `max(reported, estimated)` with the wrong "reported".
   - **Quantified** (`occ_w.json`): a rerun with Pi semantics moves S4 cost by +0.5% (A 4–8 subtasks) and 0.0–0.07% in the other cells. The S4 verdict is unchanged.
9. **[FIXED] Action Fusion text details:**
   - an empty command output yields `…\n[then_run:succeeded]\n`, where the ref gives `…\n[then_run:succeeded]` (probe);
   - `write` reuses the *edit* `then_run` description (`fusion.py:76`), where the ref has a separate `WRITE_THEN_RUN_DESCRIPTION`;
   - an empty `then_run.command` silently skips the command.
10. **[FIXED] Action Fusion path canonicalisation.**
    - `resolve_tool_path("~/proj/a.py")` returns `proj/a.py` (cwd-relative), where the ref resolves it against `homedir()`.
    - There is no `realpath` queue key, so a symlink and its target get different locks.
    - These are harmless in the virtual workspace but diverge from `file-queue.ts`.
11. **[FIXED] Runtime double projection.** On a turn where Pi-style auto-compaction fires, `runtime.py:298-304` calls `project()` twice. The ObservationPack `context` handler counts one provider request as two sends, so a large result can be packed one request early. There is also a threshold difference: we compare `ctx >= window − reserve`, while Pi's `shouldCompact` uses strict `>` (`compaction.js:163`).
12. **[FIXED] Stale domain docstring.** `domain.py:17-20` says `holdout` holds acceptance tasks of `datalookup` only. `HELDOUT_FAMILIES = ("configfix", "datalookup")` and the code put both families in `holdout` and `ood`.
13. **[FIXED] Idea pool is missing family M.** It has no "Improvement & evaluation" idea (`mocks.py:36-55`); this is the paper's largest family, 46 of 152. Also, `n_lineages` = pool size means the Oracle Analysis never excludes an idea.
14. **[DOCUMENTED] Source discrepancy, not a code issue.** `solpi_inkeast.md` lists the Opus SoL-Pi score as 43.7. That is Claude Code's native score (43.69). The other sources and the blog SVG give 42.22. I did not use inkeast's value.

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
4. **Fix the fidelity items in §3.** Done in stage C (§6), with the release's plan vitest vectors and the audit's probes ported as regression tests (`tests/metaharness-solpi/test_solpi_fixes.py`). Still open: porting the ObservationPack projection/placeholder-stability and EPR fused-result vitest suites wholesale (their behaviour is covered by the existing mechanism tests).
5. **Decide the unpublished parameters.** These are the capability metrics and tolerances, the held-out pass criterion, and cross-lineage nondominated retention. Report sensitivity to them. S6 showed that a score-only 2% floor admits a turn cap; the two-metric floor (score AND solved rate) now rejects it, but a trick that is lossless alone still costs capability in composition.
6. **An LLM reducer at volume.** Run a cheap-model reducer over hundreds of real build and test logs, measuring acceptance, fallback reasons and downstream solve rate. This tests the sufficiency caveat (C3) and the deterministic-reducer artefact in the S7 long-log caveat.

## 5. Evidence index

- Spec: `docs/methods/metaharness-solpi/paper-spec.md` Part B.
- Impl notes: `docs/methods/metaharness-solpi/implementation.md` §3–§9.
- Results: `results/metaharness-solpi/s1_action_fusion.json` … `s7_composition.json`, `live_smoke.json`.
- Validation: `validation/metaharness-solpi/{AUDIT.md,RUNS.md}` and the directories `solpi_agentworld_offline/`, `solpi_agentworld_live{,_r2,_r3,_r4}/`.
- Tests: `tests/metaharness-solpi/test_metaharness-solpi_mechanisms.py`, `_solpi.py`, `_genericity.py`, `_validation.py`, `_review.py`; the stage-C regression tests `tests/metaharness-solpi/test_solpi_fixes.py`.
- This audit's scratch checks (outside the repo): `/tmp/claude-0/-home-user-RSI/ebd00391-ba98-5b98-9125-83abd1dce979/scratchpad/claims_solpi/{vectors.py,vectors.json,fusion_probe.py,occ_w.py,occ_w.json}`. The Pi package used for the host-semantics checks is in `…/scratchpad/npmpk/pi/package/`.

## 6. Fix log (stage C, 2026-09-25)

Every finding was first reproduced with the auditor's probes (`scratchpad/claims_solpi/vectors.py`: 20/28 reference vectors before the fixes; `fusion_probe.py`) or a new scratch probe, then fixed, then locked by a regression test that fails on the pre-fix code. The new tests are in `tests/metaharness-solpi/test_solpi_fixes.py`. 25 of its 26 cases fail when run against a copy of the tree with `rsi/solpi` and `rsi/domains/agentworld` from `HEAD`; the exception, `test_protocol_runs_only_the_oracle_top_ideas`, is a supporting behaviour test whose regression partner is `test_idea_pool_covers_family_m_and_the_oracle_filters`. After the fixes the probe passes 28/28 (`vectors_after.py`, which calls our `source_lines` instead of recomputing the old formula inline).

| # | finding | fix | file:line | regression test | evidence |
|---|---|---|---|---|---|
| F1 | §3.1 plan snapshot was a Python list with spaces | `JSON.stringify({steps})`: compact separators, key order, no ASCII escaping | `occ.py:330-333` | `test_plan_snapshot_is_json_stringify_of_steps_object` | plan vitest vector passes |
| F2 | §3.2 two advice lines missing ("changed goal", "Mark one pending … in_progress"); "at most one" text differed | the release's three lines in its order | `occ.py:312-327` | `test_plan_advice_matches_release_vectors`, `test_update_plan_tool_result_carries_snapshot_and_advice` | vectors_after 28/28 |
| F3 | §3.3 lenient plan parsing; progress arrays truncated | `parsePlanSteps` (exactly 3 keys, non-empty bounded strings); the `update_plan` TypeBox schema is validated before execution (1–128 steps, no extra keys, progress arrays ≤ 128/64/64 of ≤ 1,000 graphemes) and a violation is a `Validation failed for tool "update_plan"` error | `occ.py:216-310` | `test_plan_parsing_is_strict_like_plan_ts_and_the_tool_schema` | – |
| F4 | §3.4 `source_lines` off by one for logs ending in `\n` | `body.split("\n").length` | `reducer.py:81-83`, used in `_archive` | `test_epr_source_lines_is_split_length` | 3001 lines for `"x\n"*3000` |
| F5 | §3.5 EPR lengths in code points | UTF-16 units for the 1..600 quote limit, `maxChars` and `ArchiveObject.chars`; the offline reducer uses the same measure | `reducer.py:76-78,143,423` | `test_epr_lengths_are_utf16_units` | 400-emoji quote rejected |
| F6 | §3.6 excerpt splitter broke on `\r`, U+2028 … | `re.split(r"(?<=\n)")` like `text.split(/(?<=\n)/)` | `obspack.py:96-107` | `test_obspack_excerpt_splits_after_newline_only` | CR-log head excerpt 5 B, as the reference |
| F7 | §3.7 `obs_recall` accepted a negative offset | schema check `{id: string, offset?: integer ≥ 0}` before the tool runs; `read_recall_chunk` also refuses a negative offset | `obspack.py:132-135,234-250` | `test_obs_recall_rejects_negative_and_non_integer_offsets` | – |
| F8 | §3.8 OCC `W` used the last request's size | `AgentRuntime.context_usage()` = Pi 0.85 `getContextUsage().tokens` (last reply's usage, i.e. its request + its output, plus the estimates of the messages after it; unknown right after a compaction); OCC uses it for `reported` | `runtime.py:269-290`, `occ.py:401-411` | `test_occ_write_tokens_follow_pi_get_context_usage`; the review test now feeds `context_usage()` | S4 re-run: OCC-vs-late cost moves ≤ 2.5 points, verdict unchanged (auditor's estimate: ≤ 0.5%) |
| F9 | §3.9 AF text: trailing newline on empty output; `write` used the edit description; empty command skipped | empty output gives just `[then_run:succeeded]`; `WRITE_THEN_RUN_DESCRIPTION`; the command always runs (as `thenRun !== undefined`); a failed command joins non-empty parts with blank lines; a malformed `then_run` is a validation error | `fusion.py:55-60,104-114,205-213` | `test_af_text_details_match_then_run_ts` | fusion_probe: `empty_output_matches_TS: true` |
| F10 | §3.10 `~/x` resolved cwd-relative; no `realpath` queue key | `resolve_tool_path` ports `resolveToolPath` (`~` → home, `file://`, `@`, unicode spaces without U+200B, relative → cwd); the queue key is `canonical_queue_key` = realpath; AgentWorld's file tools resolve paths like Pi's built-ins, so the agent's own path goes to the built-in mutation as in the release | `fusion.py:66-96,133-137,176-183`; `agentworld/base.py:28-93` | `test_af_path_resolution_matches_file_queue_ts`, `test_af_queue_key_is_realpath_so_symlinks_share_a_queue`, `test_af_on_agentworld_passes_the_agent_path_to_the_builtin` | S1 re-run identical (MockAgent paths are plain relative) |
| F11 | M2: no yield between the hashes in production; a vanished file reported "content changed" | default `time.sleep(0)` yield (the hook only replaces it); `[then_run:skipped] ENOENT: …; the command was not run.` | `fusion.py:99-101,153-170` | `test_af_hash_guard_yields_by_default_and_reports_enoent` | fusion_probe: ENOENT text |
| F12 | §3.11 two projections on auto-compaction turns; `>=` threshold | the threshold check runs on the stored messages with `context_usage()` and strict `>` (`should_auto_compact`), then the context is projected exactly once | `runtime.py:292-302,339-345` | `test_runtime_projects_once_per_request_even_when_auto_compacting`, `test_runtime_auto_compaction_threshold_is_strict_and_uses_context_usage` | – |
| F13 | §3.12 stale docstrings (held-out = datalookup only; "four families") | both held-out families named in `domain.py`, `__init__.py`, `envs.py` | `agentworld/domain.py:17-21`, `__init__.py:3-4`, `envs.py:3,20-25` | `test_agentworld_docstrings_name_both_heldout_families` | – |
| F14 | §3.13 no family-M idea; the oracle never filtered (pool = `n_lineages`); the turn cap's oracle statistic was the system-prompt share | pool of 12 ideas over C, P, T, D, R, M (M5 `cost_attribution` instrument, M12 `fail_before_pass_after` directive, both evaluation ideas that cannot save tokens); P20's oracle is `late_turn_tokens` (input tokens after request 10); 12 > default `n_lineages` = 10 | `mocks.py:39-63`, `tricks.py:114-140`, `registry.py:37,71-72`, `agentworld/domain.py:202-247` | `test_idea_pool_covers_family_m_and_the_oracle_filters`, `test_protocol_runs_only_the_oracle_top_ideas` | offline re-run: R5 (0.002) and M5 (0.05) get no rollouts; M12 is rejected for "no efficiency gain" |
| F15 | §3.14 inkeast lists 43.7 for the Opus SoL-Pi score | documented (source discrepancy; 42.22 used) | – | – | – |
| F16 | M4: placeholder ledger rows lacked `originalLines`; token estimate in code points | added; `ceil(utf16_len / 4)` | `obspack.py:55-62,217` | `test_obspack_placeholder_ledger_entry_has_original_lines` | – |
| F17 | M10: `LLMReducer` had no timeout, no `min(2048, maxTokens)`, and always reported "stop" | 90 s deadline → `model-call-timeout`; `min(requested, llm.max_tokens)`; the backend stop reason (kept on cache hits) mapped like pi-ai | `reducer.py:264-319` | `test_llm_reducer_timeout_max_tokens_and_stop_reason` | live EPR smoke re-parsed at $0 with the fixed reducer: 4/4 hits, identical 4/4 accepted |
| F18 | M13: `turn_end` did not check `stopReason` aborted / error | early return on an error / aborted reply or an aborted signal (`rt.aborted`); session restore and tree events stay unmodelled (documented: no session files) | `occ.py:452-456`, `runtime.py:226-232` | `test_occ_turn_end_skips_aborted_or_error_replies` | – |
| F19 | M7: an archive integrity failure was journalled as a `model-call-exception` fallback | it throws like `archive.ts`; the runtime keeps the original result and records a handler error, as Pi's runner does | `reducer.py:405-411,430-433` | `test_epr_archive_integrity_failure_throws_like_archive_ts` | – |
| F20 | L4 / S6: the capability floor admitted a lenient turn cap | the predeclared floor has two capability metrics, the mean score AND the fully-solved rate (each within 2%); a missing metric fails closed; `GateSpec(capability=(("score", 0.02),))` is the old score-only floor as an explicit option | `gate.py:9-16,56,89,137-141` | `test_default_gate_rejects_the_lenient_turn_cap`, `test_turn_cap_lineage_is_abandoned_under_the_default_floor` | S6 re-run: do-less admitted 1.0 → 0.0 in 5/5 seeds; composed training loss −7.3 → −3.8 points (a trick, tail-trim@120, remains); S5: the multi-family gate admits 0 do-less (was 1.0). Recorded runs re-gated at $0: only offline P20.2 changes (accept → reject); live r1–r4 unchanged |
| F21 | R4: nondominated retention only with a non-default `sweep=True` | `Config.sweep=True` by default; the lineage freezes the nondominated passing variant with the best η; `sweep=False` = the pseudocode's first pass | `driver.py:69`, `research.py:211,334-342`, `mocks.py:80-82` | `test_lineage_sweeps_and_freezes_the_nondominated_best_eta_variant` | offline re-run: C23 evaluated all 4 variants and froze the best-η one (the same `e0_s1`: every variant raises cost, so the composition is unchanged) |
| F22 | Q13: the ObservationPack sweep never reaches the 10% bill gate | not a parameter issue (the blog's exact V2 and 4 sends added; nothing reaches 10%); S2 now reports the bill decomposition: replayed outputs are ≤ 5.9% of the bill as cache reads, and each swap re-writes the prefix at 12.5× | `experiments/metaharness-solpi/s2_observation_pack.py` | – (experiment) | `s2_observation_pack.json` `A:/B:bill_decomposition`; `scratchpad/claims_solpi/op_bill_probe.json`, `op_bill_attrib.json`. Documented: a domain property; Q13 stays NR |
| F23 | R7 / Q2: the composed protocol stack saves only 8.9% cost | documented: the ∃-efficiency rule (faithful) admits cost-raising ObservationPack variants, plus the EPR × OP interaction under the MockAgent (AUDIT register #17, #20) | – | – | offline re-run: same survivors and +8.9% |
| F24 | R8: 5 simulated families, not 535 environments | documented (scale; every family is graded and validity-filtered) | – | – | – |
| F25 | found while fixing: S5's no-firewall ablation rebuilt the *last* frozen variant, which is wrong once lineages sweep | it rebuilds the variant the lineage froze | `s5_survive.py:57-62` | – (experiment) | S5 re-run |
| F26 | not testable here | Q4–Q10, Q15, Q16, R9, L6, L8, C4, C8–C10 need EdgeBench, frontier models, GPUs or the unpublished orchestration | – | – | NOT TESTABLE HERE |
| F27 | found by the adversarial verification (2026-09-28): `validate_update_plan_args` accepted `progress: null`, which `tools.ts` (`progress: Type.Optional(progressSchema)`) rejects | only an absent `progress` is optional; `null` fails with "progress: must be object" | `occ.py:284` | `test_plan_parsing_is_strict_like_plan_ts_and_the_tool_schema` (new `progress: None` case) | no caller sends null (MockAgent policy always sends an object), so no result changes |

**Verification (2026-09-28).** Every S1-S7 script was re-run with the current code (5 seeds, 2 workers) into a scratch directory, and the offline validation protocol was re-run as well. All seven JSONs match the committed `results/metaharness-solpi/s*.json` exactly (only the figure path differs), and the offline ledger matches `solpi_agentworld_offline/ledger.jsonl` line for line. The committed results therefore reflect the final code, including the `fusion.py` and `reducer.py` edits made after those runs.

**Re-runs** (all offline at full settings, 5 seeds, 2 workers; `results/metaharness-solpi/`): S1 and S3 identical to the pre-fix results; S2 identical on the original 8 points, plus V2 and the decomposition; S4 verdict unchanged (+30% every-boundary vs late); S5 PARTIAL (do-less at the multi-family gate 1.0 → 0.0); S6 NOT REPRODUCED under its predeclared criterion (do-less 0.0, composed loss −3.8 points); S7 PARTIAL (A default −54.0% tokens / −15.8% cost). `validation/metaharness-solpi/solpi_agentworld_offline` was re-run from scratch with `experiments/metaharness-solpi/sp_validate.py` (audit 10/10 check types pass).

## 7. Retry round 2 (2026-09-29)

Second attempt on every PARTIAL / NOT REPRODUCED row (R7, R8, Q1, Q2, Q3, Q11, Q12, Q13, Q14, L1, L2, L4, L5, L9), plus a scan of the NOT TESTABLE rows. Scratch work is in `scratchpad/retry2/solpi/`.

### 7.1 Retry round 2: preregistration

Written 2026-09-29 18:51 UTC, before any of the experiments below were run. The diagnosis that led to it (R2-F1 below, plus the domain profile in P-Q13) used no claim-outcome data from the new experiments. Two exceptions are stated openly:
- the S5 power analysis used S5's existing seeds 0–4;
- the old S2/S6/S7 results were already known from round 1.

For that reason every new experiment uses **fresh seeds**, disjoint from seeds 0–4, which are the seeds behind every round-1 number. Every run is reported, pass or fail. Unless stated otherwise, CIs are 95% bootstrap CIs over seeds (`rsi.core.stats` via `_common.summarize` / `paired`).

**Code fix found by the diagnosis (applied before any re-run).**
- **R2-F1 (root cause (a)): compaction billing.** Pi 0.85.1's native compaction (`dist/core/compaction/compaction.js:generateSummaryWithUsage`, `utils.js:serializeConversation`, `completeSummarization`) sends the summarisation as a standalone request:
  - the summarisation system prompt plus ONE user message;
  - the message holds the serialised conversation, with every tool result cut to 2,000 characters;
  - it is sent with `cacheRetention: "none"`, so it shares no cached prefix with the conversation.

  Our runtime billed it as a continuation of the conversation's cached prefix (mostly cache reads, `runtime.py:324` before the fix) and then reset the conversation's cache. This affects every cost number that involves Pi auto-compaction or OCC: S4 and S7, and S2 in long sessions.

  Fix: `rsi/solpi/pi_compaction.py`, `AgentRuntime.compact`, `TokenMeter.uncached`. Regression test: `tests/metaharness-solpi/test_solpi_retry2.py`; it fails on the old code (the compaction request read 38,117 cached tokens). Pi's own `serializeConversation`, run under node 22, supplies the serialisation vector.

**P-RERUN (M14, Q1, Q2, Q12, Q14, L1, and Q13's S2 half).** S2, S4 and S7 are re-run with the fixed code at their original settings (seeds 0–4, 2 workers). The scripts and their predeclared verdict logic are unchanged. The pre-fix JSONs are kept in `results/metaharness-solpi/solpi_retry2/pre_fix/`. Decision rules:
- **M14 (currently R).** If S4's own verdict (OCC η within 5% of the best arm in every cell; 0 compactions with S ≤ 0) no longer holds, M14 is downgraded.
- **Q14.** REPRODUCED only if S7's own criterion holds in all 4 cells: every mechanism saves tokens alone, AND the full stack has the lowest tokens AND the lowest cost in every cell. Otherwise PARTIAL (if every mechanism still saves tokens alone) or NOT REPRODUCED.
- **Q1 / Q2.** REPRODUCED only if, in the long-task cells (the EdgeBench analogue), the full stack's 95% CI contains the paper's value on both backends:
  - Q1, token saving: 49.0% for A, 44.7% for B;
  - Q2, cost saving: 33.2% for A, 33.5% for B.

  Otherwise PARTIAL if the direction holds in all 4 cells.
- **Q12.** REPRODUCED only if both hold:
  - the Action Fusion fires-per-triggered-task ratio A/B lies in [2.6, 10.4] (within 2× of 70.58 / 13.54 = 5.2);
  - the OCC task-trigger rates are within 2× of the paper's (A ≥ 46.1%, B ≤ 66.6% and ≥ 16.7%) in the long cells.

  Otherwise PARTIAL if the direction (A > B) holds in every cell.
- **L1.** At most PARTIAL whatever the outcome: whether frontier-model quality is retained is Q3.

**P-Q13 (Q13), `experiments/metaharness-solpi/s2b_observation_pack_long.py`.**
- **Hypothesis:** in long sessions (the blog's stated reason for EdgeBench: replay "accumulates" over hours), the blog's V2 reaches its reported bill saving.
- **Setting:** all five AgentWorld families, unchanged. There is no family selection and no change to outputs or the agent. Two lengths:
  - default: 4–8 subtasks;
  - long: 16–20 subtasks, `max_turns` 200. This is S4's longest bin, fixed before this experiment.
- **Seeds and scale:** fresh seeds 10–29 (20 seeds); n_train = n_final = 6 per family; backends A and B.
- **Arms:** base, V0 (0 B / 1 send), the release default (1,024 B / 2 sends), V2 (2,048 B head + 1,536 B tail / 2 sends).
- **Primary metric:** V2's bill saving, 1 − cost/base cost, paired per seed, backend A, long cells.
- **Pass ("bill component reproduced on CPU analogue"):** the 95% CI lower bound is > 0 AND the CI contains 9.1% or lies above it.
- **Secondary checks** (reported, not decisive):
  - "reaches the 10% gate": mean ≥ 10% AND quality change ≥ −2%;
  - long minus default, paired by seed.
- **Descriptive:**
  - per family, the share of > 10 KiB results that are errors. Pi's bash tool throws on a non-zero exit, so the release never packs a failing test run. That is faithful; see the diagnosis in 7.2.
  - the replay ceiling.
- **Verdict cap:** Q13's quality-gate half ("V2 the only configuration inside −2%") and the EdgeBench A/B cannot be tested by the MockAgent. At most PARTIAL.

**P-L4 (L4), `experiments/metaharness-solpi/s6b_floor_fresh_seeds.py`.**
- **Claim tested** (blog wording): "A cheaper candidate fails if it saves by stopping early, skipping necessary verification, or removing evidence required to finish the task … The gate applies to one mechanism at a time, so the small losses it permits can accumulate once mechanisms combine … What the gate rules out is savings that come from getting less done."
- **Setup:** fresh seeds 10–29 (20 seeds); S6's two main arms (efficiency-only objective vs the dual gate, no firewall, backend A); n_train = n_final = 8 and n_test = 8. The `test` tasks are fresh tasks of the training families that the gate never sees.
- **Pass requires all three:**
  - **L4-1:** the dual gate admits no do-less candidate (P14, any P20 variant) in 20/20 seeds.
  - **L4-2:** the efficiency-only objective admits ≥ 1 do-less candidate in ≥ 19/20 seeds, and its composed stack loses > 2 points on `evolve` (the floor is what blocks them).
  - **L4-3:** ≥ 95% of the (seed, admitted survivor) pairs of the dual gate lose ≤ 2 points *standalone* on the unseen `test` split. This tests whether admitted candidates really do not save by doing less, rather than only passing on the gate's own sample.
- **Also reported, with the result stated plainly:** the old S6 criterion (composed stack within 2 points on `evolve`). The blog itself says composed losses accumulate (to ~94% retention), so that criterion is a stronger reading than the claim.

**P-R7 (R7), same runs.** R7's claim: the survivors are merged as independent opt-in extensions, each gated one at a time, and small losses may accumulate. Pass requires both:
- the composed harness's extension set equals the union of the survivors' mechanisms in 20/20 dual-gate seeds;
- accumulation is observed: the composed `evolve` change is more than 0.5 points below the worst standalone survivor's `evolve` change in ≥ 3/20 seeds.

Cost magnitudes are not part of R7; they belong to Q2.

**D-LOO (diagnostic, no threshold), same runs.** For each dual-gate seed, the composed stack minus each survivor, on `evolve` and `test`. This attributes the composed loss to components. If a released mechanism is implicated, its port is re-checked against the TypeScript and its vitest vectors.

**P-L2 (L2), `experiments/metaharness-solpi/s5b_diversity_power.py`.**
- **Power analysis** from S5's seeds 0–4, multi_aggregate − single_env_dual, paired:
  - backend B, no firewall: +0.037, SD 0.040 (5 seeds ≈ 50% power; about 9 seeds give 80%);
  - backend A, no firewall: +0.001, SD 0.020.
- **Design:** fresh seeds 10–29 (20 seeds); S5's `job` unchanged; protocols single_env_dual and multi_aggregate.
- **Primary:** the held-out-family (`ood`) capability ratio of the training-admitted stack WITHOUT the firewall, paired multi − single, averaged over backends A and B per seed.
- **Pass:** the pooled 95% CI lower bound is > 0 AND neither backend's CI upper bound is < 0.
- **Secondary:** the same with the firewall; the tricks admitted at the training gate.

**P-Q3 (Q3), power analysis only, no experiment.**
- **Scenario:** a paired test of the paper's effect, 44.833 → 42.003 (−2.83 points on a 0–100 scale), with an assumed per-task paired SD of 20 points.
- **What it needs:** n ≈ ((1.96 + 0.84) · 20 / 2.83)² ≈ 390 task pairs.
- **What the budget buys:** at our cheapest live agent (haiku via `claude -p`, one full-transcript call per turn, about $0.3–0.5 per short AgentWorld task), $4 buys about 4–6 pairs.
- **Consequence:** if the arithmetic holds, Q3 is reclassified NOT TESTABLE HERE with the requirement stated.

**No new experiment (root cause (c)) for** R8, Q11, L5 and L9. The reason is given per row in 7.2.

**Live LLM budget:** none planned ($0).

**P-Q13b (follow-up to P-Q13, added 2026-09-29 18:55 UTC, AFTER P-Q13's result was seen).**
- **Why:** P-Q13 ran as preregistered and FAILED (7.3). Its long cells had a domain defect: `LogTriageEnv` has only 15 sampleable items, so every logtriage task with ≥ 16 subtasks raised `ValueError: Sample larger than population` and scored 0 with 0 requests in every arm (both backends, all 20 seeds).
- **Why it matters:** logtriage is the family whose large outputs are 0% errors, i.e. the most packable family. The defect therefore biased P-Q13 against ObservationPack.
- **Fix:** logtriage is clamped to its 15-item maximum. The RNG sequence is unchanged for n ≤ 15, so no earlier result moves. Regression test: `test_logtriage_env_clamps_to_its_item_pool`.
- **Design:** P-Q13 is re-run with the identical design, seeds and thresholds, into `solpi_retry2/s2b_observation_pack_long_fixed.json`.
- **Reporting:** both runs are reported; P-Q13's FAIL stands as a recorded result.

**P-L2b (follow-up to P-L2, added 2026-09-29 23:10 UTC, AFTER P-L2's result was seen, in response to a reviewer objection).**
- **Why:** L2 says "keep only the changes that survive **everywhere**". S5's own docstring labels `multi_per_family` (the dual gate must pass in every training family) as the literal arm, but P-L2 compared `single_env_dual` only with `multi_aggregate` (a pooled screen). P-L2 therefore did not test the claim's literal protocol.
- **Not blind, stated openly:** P-L2's `multi_aggregate` result on these seeds is known, and in S5 (seeds 0–4) `multi_per_family` and `multi_aggregate` gave identical per-seed numbers. The outcome may therefore repeat P-L2; the run is done because it is the claim's literal arm, not because a different result is expected.
- **Script:** `experiments/metaharness-solpi/s5c_per_family_power.py` (S5's `job` unchanged, as in s5b). Output `results/metaharness-solpi/solpi_retry2/s5c_per_family_power.json`.
- **Seeds:** the same fresh seeds 10–29 (20); protocols `single_env_dual` (re-run, and checked for identity with s5b's rows) and `multi_per_family`.
- **Primary (identical to P-L2):** held-out-family (`ood`) capability ratio of the training-admitted stack WITHOUT the firewall, paired `multi_per_family` − `single_env_dual`, averaged over backends A and B per seed.
- **Pass (identical to P-L2):** pooled 95% bootstrap CI lower bound > 0 AND neither backend's CI upper bound < 0. A paired t-test p-value is reported alongside, not decisive.
- **Power (stated before the run):** S5's hinted pooled effect is +0.019 (paired SD 0.028, seeds 0–4); at n = 20, α = 0.05 two-sided, power ≈ 0.82 (noncentral t).
- **Secondary:** the same with the firewall (t-test and Wilcoxon p reported); tricks admitted at the training gate; `multi_per_family` vs `multi_aggregate` identity per seed.
- **Verdict mapping:** L2 is at most PARTIAL (the environments are simulated families; see L5). PASS keeps PARTIAL with the direction supported on the literal arm; FAIL keeps PARTIAL only if the point estimate is ≥ 0 on both backends, otherwise NOT REPRODUCED.

### 7.2 Retry round 2: per-claim table

Root causes: (a) implementation, (b) experiment design, (c) scale, (d) genuine negative, (e) source error. Paths are under `results/metaharness-solpi/` unless stated. "fresh seeds" means 10–29.

| claim | old verdict | root cause | what was done | evidence | new verdict |
|---|---|---|---|---|---|
| R7 | P | (b): the claim was judged on a metric that is not its own (the protocol stack's cost belongs to Q2) | P-R7 on fresh seeds, testing the stated claim: survivors merged as independent extensions, each gated alone, and losses may accumulate | `solpi_retry2/s6b_floor_fresh_seeds.json`: union 20/20; accumulation in 19/20 seeds; composed retention 0.949 [0.930, 0.967] | **R** (mechanism, toy scale) |
| R8 | P | (c) | Diagnosis only: the filter is faithful; the environment count is scale | – | P |
| Q1 | P | (c) | Implementation fix R2-F1, then S7 re-run (P-RERUN) | `s7_composition.json`: long-cell token saving A 58.4% [56.8, 59.8], B 68.4% [68.1, 68.9]; the paper's 49.0% / 44.7% lie outside both CIs; direction 4/4 | P |
| Q2 | P | (c) | R2-F1, then S7 re-run | `s7_composition.json`: A long −32.7% [29.5, 35.2] contains 33.2%; B long −61.0% [60.4, 61.5] does not contain 33.5%. Protocol stack −8.9% (offline validation re-run) | P |
| Q3 | NR | (c) | Power analysis (P-Q3); S7 re-run | 44.83 → 42.00 needs 98–392 task pairs (per-task paired SD 10–20); $4 buys about 14 haiku pairs on different tasks. MockAgent success ratio 0.998–1.006 | **NT** |
| Q11 | P | (c) | Diagnosis only | The oracle-predicts-saving relation holds (S1); the magnitudes are Sol trajectories | P |
| Q12 | P | (c) | S7 re-run (P-RERUN) | AF ratio A/B in long cells 2.87 (inside the band); OCC A-long trigger rate 38.3% < 46.1% (outside the band); direction A > B in every cell | P |
| Q13 | NR | (d) | Port re-checked against the TS and Pi's bash tool; P-Q13 and P-Q13b (20 fresh seeds each, long sessions); S2 re-run | `solpi_retry2/s2b_observation_pack_long.json`: V2 A-long +0.3% [−0.1, 0.6]. `…_fixed.json`: −0.0% [−0.4, 0.3]. 89–100% of the large outputs in the long families are errors; replay ceiling 4–6% of the bill | NR |
| Q14 | P | (d) | S7 re-run; add-to-EPR diagnostic | `s7_composition.json`: full stack cheapest in 2/4 cells. `scratchpad/retry2/solpi/q14_probe.json`: on A, EPR+AF −43% / −57% vs full −20% / −33%; OP and OCC raise cost on top of EPR | P |
| L1 | P | (c) | S7 re-run | Success ratio 0.998–1.006 at lower cost in all cells; "matched Pi" at frontier scale is Q3 | P |
| L2 | P | (d) | Power analysis on S5; P-L2 (20 fresh seeds, power ≈ 0.99 for S5's hinted effect) | `solpi_retry2/s5b_diversity_power.json`: pooled +0.011 [−0.0015, +0.023] → FAIL; B +0.020 [0.000, 0.040]; with firewall +0.011 [+0.001, +0.022]; tricks 3.0 → 1.5 | P |
| L4 | P | (d) | P-L4 on fresh seeds (criteria from the blog's wording) + D-LOO attribution + Action Fusion pairwise probe | `solpi_retry2/s6b_floor_fresh_seeds.json`: FAIL. L4-1 18/20; L4-2 20/20 (−49.7 points); L4-3 108/115 < 95%. Old S6 criterion not met (−5.1 points). LOO: T3 +6.0, AF +2.7 (AF × T3) | P |
| L5 | P | (c) | Diagnosis only: an AgentWorld test would be circular | – | P |
| L9 | P | (c) | Diagnosis only: an LLM agent backend is over the $4 budget | – | P |
| M14 | R | (a), examined because of R2-F1 | S4 re-run after the fix | `s4_occ.json`: within 5% of the best arm in 8/8 cells; every boundary +34% vs late | R |

### 7.3 Retry round 2: results, every run reported

All runs are offline, with 2 worker processes; there are no live LLM calls. Scratch copies are in `scratchpad/retry2/solpi/`.

**R2-F1: implementation fix (compaction billing).**
- **What was wrong:** the runtime billed Pi's summarisation call as a continuation of the conversation's cached prefix and then reset the conversation cache.
- **What Pi 0.85.1 does:**
  - sends the call standalone: `SUMMARIZATION_SYSTEM_PROMPT` plus one user message holding `<conversation>` (serialised with `serializeConversation`, tool results cut to 2,000 chars), `<previous-summary>` and the prompt;
  - sets `cacheRetention: "none"`;
  - leaves the conversation's cache untouched.
- **Fix:** `rsi/solpi/pi_compaction.py`; `AgentRuntime.compact`; `TokenMeter.uncached`; `PriceTable.uncached`. Anthropic-like tables bill uncached input at write/1.25.
- **Tests:** `tests/metaharness-solpi/test_solpi_retry2.py`, with the Pi `serializeConversation` vector produced by node.
- **Effects:**
  - S2 (default length) is identical.
  - S4: every boundary vs late compaction goes from +30% to +34%; the verdict is unchanged.
  - S7: full-stack cost savings rise by 0–4 points (A default −15.8% → −19.6%, A long −30.8% → −32.7%). Pi's own auto-compaction is now dearer for the base harness, and the full stack avoids some of it.
  - Offline validation: 2 of 32 ledger rows change (OCC lineage cost 0.23130 → 0.23186); survivors, composition and the 10/10 audit checks are unchanged.

**P-RERUN (S2, S4, S7, seeds 0–4; the scripts' own verdicts).**
- S2: PARTIAL, text unchanged.
- S4: REPRODUCED.
- S7: PARTIAL (tokens −54.8%, cost −19.6% on A default; cheapest in 2/4 cells).
- The decision rules for M14, Q1, Q2, Q12, Q14 and L1 are applied in 7.2.

**P-Q13 (ran as preregistered): FAIL.**
- **Primary (backend A, long sessions):** V2 bill saving +0.3% [−0.1, 0.6]; quality change 0.
- **Other long cells:**
  - V0 +1.8%, release default +0.7% on A;
  - on B: V2 +0.5%, V0 +1.9%.
- **Default cells:** V2 −1.0% (A) and −1.1% (B).
- **Long minus default** (V2, A, paired): +1.3% [0.4, 2.4].
- **Disclosed defect:** in the long cells every logtriage task crashed in every arm (0 requests; see P-Q13b).

**P-Q13b (follow-up, logtriage clamped): FAIL.**
- **Primary:** V2 A-long −0.0% [−0.4, 0.3]; long minus default +1.0% [0.0, 2.1].
- **Other long cells:** V0 +1.7% (A) and +1.5% (B); release default +0.4% (A) and +0.3% (B).
- **Why the domain cannot reach the paper's 9.1% (descriptive):**
  - in the long-running families (repofix, configfix) 89–100% of the > 10 KiB outputs are failing commands;
  - the release never packs a failing command, because Pi's bash tool throws on a non-zero exit and `isPureTextResult` requires `!isError`;
  - the packable outputs sit in short batch tasks (logtriage and datalookup, about 6 requests);
  - the base's replay-read ceiling is 5.2–5.5% of the bill.
- **What would be needed:** real long-session trajectories whose large outputs are successful reads or commands (the blog's "a large file or tool result reappeared in every later request"), i.e. TB40 or EdgeBench with a real agent.
- **Consistency with the paper's own table:** the add-one ObservationPack row there is −5.1% cost ($1,339 → $1,271), also below the 10% gate.

**P-L4: FAIL.**

| criterion | result |
|---|---|
| L4-1 | 18/20 seeds admit no do-less candidate |
| L4-2 | 20/20 seeds; efficiency-only composed `evolve` change −0.497 |
| L4-3 | 108/115 admitted survivors within 2 points (93.9%, threshold 95%) |

- **The failures:**
  - `turn_cap(max_turns=24)` in seeds 12 and 18 scores 0.0 on the gate's screen but −1.7 and −2.1 points on unseen tasks;
  - `tail_trim` (@120 in 6 seeds, @40 in seed 13) scores −4.2 to −11.5 points on unseen tasks.
- **The old S6 criterion** (composed `evolve` change ≥ −2 points) is not met: −0.051 [−0.070, −0.033], so the dual gate's retention is 94.9%.
- **Held-out change:**
  - `test` split: −0.042 [−0.057, −0.028];
  - `ood` families: −0.025 [−0.039, −0.013].
- **Composed cost saving:** 36.4% [33.2, 40.1].
- **Plain statement:** the floor blocks almost all doing-less. Efficiency-only admits do-less candidates in 20/20 seeds and loses 49.7 points; the floor admits them in 2/20. But the floor only sees its screen sample: a 2% tolerance on 24 screen tasks admits a turn cap and an evidence-removing trick that lose on fresh tasks.

**D-LOO attribution (diagnostic).**
- **Mean success restored by removing each survivor** (evolve / test):
  - T3 tail-trim: +6.0 / +5.6 points (13 seeds);
  - P20: +3.5 / +1.0 (2 seeds);
  - P8 Action Fusion: +2.7 / +0.6;
  - C23 ObservationPack: +0.6 / +0.1;
  - D1 EPR: +0.15 / −0.12;
  - C6 OCC: 0.0 / +0.7;
  - T11: −0.8 / −1.2.
- **Pairwise follow-up on Action Fusion** (seeds 11, 16, 21, 23; `scratchpad/retry2/solpi/af_pairs.json`):
  - P8 alone: 0.0;
  - P8 with D1, T11 or C23: 0.0;
  - P8 + C6: −0.6 to −0.7 points (3 seeds);
  - P8 + T3: −4.2 to −12.5 points.
- **Conclusion:** the composed loss comes from the environment-specific trick tail-trim, directly and through its interaction with Action Fusion.
- **Port check:** the Action Fusion port passes the release's `then-run.ts` / `file-queue.ts` vectors (M1, M2; §6 F9–F11). The four released mechanisms composed without tricks keep success (S7: 0.998–1.006). No porting defect was found.

**P-R7: PASS.**
- Union: 20/20.
- Accumulation: 19/20 seeds.
- Retention: 0.949 [0.930, 0.967].

**P-L2: FAIL.**
- **Primary** (pooled, without firewall, multi − single): +0.0108 [−0.0015, +0.0230].
  - backend A: +0.0016 [−0.0089, 0.0121];
  - backend B: +0.0199 [0.0000, 0.0397].
- **Secondary** (with firewall): +0.0107 [+0.0009, +0.0221].
- **η cost:** without the firewall, multi-family keeps less η saving (A 0.270 vs 0.281, B 0.354 vs 0.385), because it admits fewer cost-cutting tricks.
- **Tricks admitted:** 3.0 → 1.5.

**P-Q3 (power analysis).**
- **Required pairs** for a paired detection of −2.83 points (44.83 → 42.00) at α = 0.05 and 80% power:

  | assumed per-task paired SD | task pairs needed |
  |---|---|
  | 5 | 25 |
  | 10 | 98 |
  | 20 | 392 |
  | 30 | 882 |

- **Budget:** a refined cost estimate for a haiku LLM agent is about 8 calls × ~15k tokens per short AgentWorld task, ≈ $0.14 per run and ≈ $0.28 per pair. $4 buys about 14 pairs (the preregistration's rougher estimate said 4–6).
- **Validity:** even 14 pairs would test a different model on different tasks.
- **Verdict:** NOT TESTABLE HERE. It needs a frontier agent on EdgeBench-scale tasks with ≥ 100 task pairs (or repeated runs).

### 7.4 Retry round 2: scan of the NOT TESTABLE HERE rows

No row gained a faithful CPU analogue:
- **Q4–Q10, C4:** need the benchmarks and the frontier backends.
- **R9:** needs a frontier search backend.
- **Q15, Q16:** scale.
- **L6:** the paper ran no equal-budget comparison. A LibraryProposer "depth" lineage walks a fixed grid, so an AgentWorld DFS-vs-BFS run would test our pool, not the claim.
- **L8:** not demonstrated by the paper.
- **C8–C10:** facts about the paper.

Upstream check, `git ls-remote https://github.com/NVlabs/SoL-Pi.git` on 2026-09-29: `main` = `1559b5c`, the audited commit.

### 7.5 Retry round 2: spend and tests

- **Live LLM spend:** $0. Every experiment is offline, and no cache was used or created.
- **Tests:** `tests/metaharness-solpi/` (the SoL-Pi files) pass at 116, including the 6 new cases in `test_solpi_retry2.py`.
- **Regression checks on the old code:**
  - the compaction-billing test fails (it read 38,117 cached tokens);
  - the logtriage test fails (`ValueError`).
