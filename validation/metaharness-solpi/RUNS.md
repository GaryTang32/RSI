# Meta-Harness and SoL-Pi: from-scratch validation runs

Every run starts from its domain's **untouched seed artifact**, in a fresh directory `validation/metaharness-solpi/<run>/` that is deleted first. Live runs also get a fresh LLM cache, `validation/metaharness-solpi/.cache_<run>/`, also deleted first. Nothing comes from `.rsi_cache` or earlier results; no earlier attempt of this stage existed.

Every run was produced by `python experiments/metaharness-solpi/validate_metaharness_solpi.py <run>`. Each run directory holds:

| file | contents |
|---|---|
| `trace.jsonl` / `TRACE.md` | the audit trace (`rsi.trace` format), rendered with `rsi.trace.inspect` |
| `report.json` | seed vs final on every split via `rsi.core.transfer_report` (report-only unsealing); spend from the meters **and** from the fresh cache's entries |
| `audit.json` / `audit.md` | one row per iteration, re-derived **independently** from trace + ledger + store, plus the checks below |
| `ledger.jsonl`, `store/` (Meta-Harness), `firewall_r1/heldout.jsonl`, `rounds.json` (SoL-Pi) | the loops' own records |

**What the trace records.**
- *Meta-Harness* (`rsi/metaharness/tracing.py`), one round per iteration:
  - `round_start`: evaluations used and budget, the frontier, the population, and what the history view exposes (files by kind, characters, visible systems);
  - `analysis`: the proposer's own diagnosis (the mock's move + evidence, or the LLM's text outside the file blocks);
  - `proposal`: the prompt and reply, the claimed hypothesis / base / components, and the **actual** diff base → candidate;
  - `gate`: admissibility only (interface validation). Meta-Harness has no score gate;
  - `eval`: per unit + raw trials;
  - `decision`: frontier membership, dominated-by, both deltas, incumbent before/after;
  - `monitor`, `state`, then the `finalize` note and `run_end`.
- *SoL-Pi* (`rsi/solpi/tracing.py`), one round per lineage iteration: 01 rollouts (eval) → 02 map-reduce evidence (analysis) → 03/04 mechanism + Ralph repairs + actual diff (proposal) → 05 review (critic) → 06 screen eval → dual gate with every number → decision (frozen / route back / abandoned). One more round per driver round covers the firewall verdicts (audit copy of the write-only sink) and the composition.
- *Shadow monitor*: scores each new incumbent (Meta-Harness: every new frontier `_best`; SoL-Pi: the base and each composed harness) on the sealed holdout/ood splits. It writes only to the trace, and its model calls are metered as `shadow:*`. Meta-Harness leaves its `test` split to `finalize()`, which evaluates it once.
- `tests/test_metaharness-solpi_validation.py` proves the monitor and the trace are write-only for both methods: runs with the monitor on, with it off, and with the trace off give identical ledgers (status, score, cost, artifact), the same best and the same final results. All 60 tests of this method pass (`pytest tests/test_metaharness-solpi_*.py`).

## Summary

| run | setup | seed → final (decision split) | seed → final (sealed, transfer_report) | audit | spend / wall |
|---|---|---|---|---|---|
| `mh_memoclassify_offline` | Meta-Harness, MemoClassify, MockProposer, N = 6, k = 2, history full | search 0.458 → **0.590** (6 frontier changes) | test 0.523 → 0.634; ood 0.504 → 0.558 (best-by-ood incumbent was 0.654, see below) | 12 checks, all pass | $0, 14 s |
| `solpi_agentworld_offline` | SoL-Pi protocol, AgentWorld, all 10 ideas (C/P/D/T/R), aggregate dual gate, holdout firewall | screen score 1.0 → 1.0; tokens −49%, cost −8.9% | holdout 1.000 → **0.988**, tokens −69%; ood 1.0 → 1.0, tokens −66% | 10 checks, all pass | $0, 7 s |
| `mh_agentqa_live` | Meta-Harness, AgentQA, **haiku** as task model and RewriteProposer, N = 3, k = 2 | evolve 0.5 → **1.0** | holdout 0.5 → 1.0; ood 0.25 → 1.0 | **fails 1 check (2/6)**: bug found, fixed, re-run | $0.78, 10.7 min |
| `mh_agentqa_live_r2` | same, after fix 12 | evolve 0.5 → 0.9 | holdout 0.5 → 0.667; ood 0.25 → 0.25 | 12 checks, all pass | $0.65, 9.7 min |
| `solpi_agentworld_live` | SoL-Pi, 2 free-form ideas, **haiku** implementer + LLM reviewer, mock agent backend | no survivor | (base only) | passes; LLM-facing doc bug found (fix 13) | $0.28, 8.9 min |
| `solpi_agentworld_live_r2` | same, after fix 13 | no survivor | (base only) | passes; second doc gap found (fix 14) | $0.20, 6.3 min |
| `solpi_agentworld_live_r3` | same, after fix 14 | no survivor (all 4 rejected by the reviewer; 3 of them wrongly) | (base only) | passes; reviewer gap found (fix 15, not re-run) | $0.24, 7.8 min |

**Live spend for this stage, from the fresh caches' entries (ground truth).**
- Connectivity check: $0.001.
- `mh_agentqa_live`: $0.782. The meters show $0.616 for the loop and $0.145 for the monitor. The $0.021 gap is 5 validation smokes that run in a forked child the meters cannot see.
- `mh_agentqa_live_r2`: $0.654 (loop $0.563, monitor $0.062, smokes $0.029).
- `solpi_agentworld_live`: $0.284.
- `solpi_agentworld_live_r2`: $0.198.
- `solpi_agentworld_live_r3`: $0.244.
- **Total ≈ $2.16** (limit ~$3). Live wall time ≈ 43 min, slightly over the ~40 min guide; that is why fix 15 was not re-run live. The `transfer_report` steps cost $0: every call hit the cache.

---

## Run 1: `mh_memoclassify_offline`

**Setup.**
- MemoClassify seed 0, scale 1: 3 search datasets (`evolve` = their val parts), sealed `test` (their test parts) and sealed `ood` (4 unseen datasets).
- Frozen deterministic MemoLM-A.
- H0 = the two untouched release baselines, `no_memory` (0.111) and `fewshot_all` (0.458, the seed).
- MockProposer: it may use only what the view exposes. N = 6, k = 2, full history, objectives (score, context chars). Validation on, no leakage screen (faithful). Finalize once on `test`.

**Narrative.** In every iteration the mock read both parents' code and scores and their 6 raw trace files (`files_read_by_kind`). Its diagnosis cites trace statistics (for example "96/144 prompts longer than 11000 chars").

- **it 1.**
  - `retrieve_topk` on `fewshot_all`: 0.535 at 2.5k chars, against fewshot_all's 0.458 at 17.7k. Joins the frontier and becomes `_best`; ood 0.504 → 0.604.
  - `label_list` on `no_memory`: 0.382 at 428 chars. Joins the frontier as a cheap point.
- **it 2.**
  - `contrastive` on the incumbent: 0.521 at 2.47k, on the frontier because it is cheaper.
  - `coverage` on no_memory: 0.507 at 3.6k, dominated.
  - Incumbent unchanged.
- **it 3.** `notes` applied to two parents:
  - on `contrastive`: 0.556, the new best (ood 0.650);
  - on the incumbent: 0.535 at a higher cost, dominated.
- **it 4.**
  - `coverage` on the best: 0.5625, the new best (ood 0.654).
  - A parameter variant `char_budget 14000`: *identical* score and cost to `retrieve_topk`, because the budget does not bind. It is also on the frontier (an exact tie, which the release definition keeps). One evaluation was wasted on a no-op.
- **it 5.** A crossover of no_memory + coverage reaches 0.569 at only 1.2k chars and becomes the best, but ood drops to 0.625. `notes_only` is dominated.
- **it 6.** A *parameter variant* (`char_budget 4500`) of the crossover: 0.590 at 5.7k chars becomes the best, and ood drops to **0.558**.

**Finalize.** Tested 6 systems once (baselines ∪ Pareto ∪ per-unit bests): best 0.634, notes 0.634, crossover 0.630, fewshot_all 0.523.

**Verdict.** Every step is mechanically correct: the audit re-derives the frontier, deltas, diffs and admissibility independently, 12/12 checks. Two behaviours deserve a flag:
- **Selection on 3 search units overfits.** The last two incumbent changes raised search accuracy (+0.7, +2.1 points) while the sealed ood score *fell* (0.654 → 0.625 → 0.558). The paper has no guard against this: it selects the highest-accuracy Pareto point.
- **The winner is a parameter variant**, which the skill explicitly discourages. That is how the offline mock works, not evidence about an LLM proposer.

## Run 2: `solpi_agentworld_offline`

**Setup.**
- AgentWorld seed 0: training screen of 8 tasks × {repofix, buildfix, logtriage}; sealed holdout of 6 × {configfix, datalookup}, used by the firewall only; sealed ood of 8 × the same held-out families, never touched by the protocol.
- Untouched base harness (no extensions), MockAgentLLM('A').
- All 10 `AGENTWORLD_IDEAS`: the 4 released mechanisms, 3 tricks, 2 do-less shortcuts and 1 dud. Their ground-truth kind is recorded in the trace but never shown to the gate.
- LibraryProposer, SmokeReviewer.
- Predeclared aggregate dual gate: score within 2% relative AND tokens or cost saving > 2%.
- n_lineages 10, max_iters 4.

**Narrative (one line per lineage).**
- Oracle ranking: D1 0.72, T11 0.72, T3 0.67, T7 0.67, C6 0.53, P14 0.28, P8 0.28, C23 0.11, P20 0.002, R5 0.002.
- **D1 (EPR).** Frozen at iteration 0: tokens −48.6%, cost −33.5%. Firewall **pass** (held-out tokens −31%, cost −24%).
- **T11 pytest_quiet (trick).** Frozen at iteration 0 on training (cost −39%). Firewall **reject**: no efficiency gain on the held-out families, which do not run pytest.
- **T3 tail_trim (trick).** Variants 20 → 40 → 120 lines fail the capability floor (0.83, 0.83, 0.92). The grid is exhausted and the lineage is abandoned.
- **T7 head_trim (trick).** Same pattern; abandoned.
- **C6 OCC (general).** Frozen (tokens −18%, cost −4.4%). Firewall **reject**: held-out cost +12.7%, score 0.986.
- **P14 no_verify (do-less).** Fails the capability floor (0.90); single variant, abandoned.
- **P8 Action Fusion.** Frozen (cost −18%). Firewall **pass**.
- **C23 ObservationPack.** Frozen on its *most aggressive* variant (full_sends 1, no excerpt): tokens −8.4% but **cost +5.4%**. It passes because the gate needs only one efficiency metric to improve. Firewall **pass**.
- **P20 turn_cap (do-less).** 10 and 16 turns fail the floor; **24 turns passes the training gate** (0.984, cost −6%). The firewall rejects it only because the cap does not bind on held-out tasks ("no efficiency gain"), not because of lost capability.
- **R5 prompt_slim (dud).** No efficiency gain (0.07%); the walk goes to variant −1, which is exhausted.

**Composition** of the survivors {D1, P8, C23} (union of `harness.json` entries, verified), measured on the screen:
- score 1.0; tokens 450k → 229k; cost **0.2407 → 0.2193 (−8.9%)**; steps 14.4 → 18.0.
- D1 alone saved 33% of cost, so the composed stack is *worse on cost than one of its parts*. The composition is not re-gated (`validate_composition` is off by default; this is faithful).
- Sealed holdout: 1.000 → 0.988 (within tolerance); ood 1.0 → 1.0 with tokens −66%.

**Verdict.**
- Gate arithmetic (15/15), the variant-walk rule (19/19) and firewall verdicts (recomputed from the sink) all check out independently. No lineage touched a sealed split. The oracle selection recomputes.
- What the protocol did right: every trick and do-less shortcut is out of the final harness, and 3 of the 4 released mechanisms survive.
- Two tricks and one shortcut passed the *training* gate: T11, and P20 at 24 turns. The firewall rejected them for "no efficiency gain" on held-out, so held-out evaluation did the filtering, not the capability floor.
- ObservationPack survived while raising cost.

## Run 3: `mh_agentqa_live` (haiku; bug found)

**Setup.**
- AgentQA suite seed 0: evolve 10 numeric; sealed holdout 6 numeric; sealed ood 4 (dates / numbertheory / strings / lists).
- Seed = `AgentQADomain.seed_artifact()`: one direct call with "You are a helpful assistant.", returning the last line.
- Task model and proposer = `ClaudeCLI("haiku")` behind a fresh CachedLLM. The proposer is **RewriteProposer** (one completion over the rendered full history, 60k-char budget), not the paper's coding agent.
- N = 3, k = 2, cost = tokens per trial, smoke timeout 240 s. Finalize on holdout + ood.

**Narrative.**
- **Seed.** Evolve 0.5; the monitor gives holdout 0.5, ood 0.25.
- **it 1.** The proposer (27/27 history files rendered, $0.063) diagnosed the seed's failures from raw traces: "Task 004: expected '234', last line was verification text …".
  - `extract_formatted`: 0.6, dominated.
  - `code_first` (have the model write Python, run it, extract from stdout): **1.0** at 1296 tokens. It becomes the best; the monitor shows holdout 1.0, ood 1.0.
- **it 2. Both candidates were written to `agents/<name>/src/harness.py`.** Haiku copied the history's `candidates/<name>/src/` layout. The loop added them as *new dead files*, so each candidate ran its base harness unchanged:
  - `code_answer_marked` got exactly code_first's (1.0, 1295.7): identical prompts → identical cached replies;
  - `harness_generated_solver` got exactly the seed's (0.5, 2378.2).
  - Worse, `code_answer_marked` then **became `_best` on an exact tie**, because the frontier breaks (score, cost) ties by name.
  - The whole iteration was wasted. The new audit check `changes_a_file_the_base_harness_has` fails 2/6 on this run, which I left as recorded.
- **it 3.**
  - `code_generalized_extract` (from code_first, generic system prompt): 1.0 at 1216 tokens, the new best.
  - `code_with_classification`: 1.0 at 3162, dominated.

**Finalize.** Holdout 0.5 → 1.0; ood 0.25 → 1.0 for the best and for code_first.

**Verdict.** The search outcome is real on these small sealed splits (n = 6 and 4). But iteration 2 was wrong, and the loop could not notice. This is **fix 12**: `_collect` maps `src/<path>` → `<path>`, and the output instructions now state the layout.

## Run 4: `mh_agentqa_live_r2` (after fix 12)

The setup is identical. Because the cache is fresh, the seed replies differ: evolve 0.5 at 2229 tokens.

- **it 1.**
  - `exploit-robust-extraction` (parse-only change): 0.7, the new best. Its monitor scores equal the seed's (holdout 0.5, ood 0.25).
  - `explore-structured-format`: 0.6, dominated.
- **it 2** (67/78 files rendered).
  - `exploit-expression-evaluation` re-evaluates arithmetic expressions found in the reply: 0.9, the new best (holdout 0.667, ood 0.25).
  - `explore-tool-driven-computation`: 0.8 at 1516 tokens, on the frontier.
  - Note: the parse-only candidates have the *same* token cost as the seed because they reuse the seed's cached replies. That makes the comparison paired, but no new model samples were drawn.
- **it 3** (only 59 of 130 files fitted the render budget).
  - `explore-direct-pattern-computation` **hard-codes regex solvers for the practice generator's question templates** (modular exponentiation, digit sums, bit counts, ledgers). It needs no model call on evolve (0.7 at context cost 0), so it sits on the frontier as the cheapest point. That is exactly the dataset-specific overfitting the skill forbids and the main loop does not guard against (spec A2/A8). Sealed: holdout 0.667, ood 0.25 (it falls back to the seed prompt).
  - `exploit-enhanced-computation-verification`: 0.7, dominated.

**Finalize.** Best `exploit-expression-evaluation`: holdout 0.5 → 0.667, ood 0.25 → 0.25. `explore-tool-driven-computation` scores holdout 0.833 but ood 0.0.

**Verdict.**
- All 12 checks pass, and the fix held: every candidate changes a file the harness runs.
- Run 3 and run 4 differ greatly: the model wrote a code-first harness in one and not in the other. With one evaluation per candidate (trials = 1), the noisy haiku backend, and N = 3, **one live run is anecdotal**.

## Runs 5–7: SoL-Pi live lineages (haiku implementer + haiku reviewer)

**Setup.**
- AgentWorld seed 0, small: 3 tasks per training family, and 3 per held-out family in each of holdout and ood.
- Untouched base; MockAgentLLM('A') as the offline agent backend.
- Two free-form ideas: L1 "stop replaying large successful outputs, keep them recallable" and L2 "condense long failing logs, keep them recallable". Haiku writes each mechanism as `extensions/<name>.py` against `RUNTIME_API_DOC`.
- `LLMReviewer` (smoke + haiku contract review). Aggregate dual gate. n_lineages 2, max_iters 2, ralph_max 2.

**`solpi_agentworld_live`.**
- **L2 it0.** Haiku imported `Extension`/`ToolResult` from the `anthropic` SDK. Two Ralph repairs failed with ImportError, and the lineage abandoned the iteration.
- **L2 it1.** The reviewer rejected the mechanism: its recall key is not discoverable.
- **L1 it0.** The mechanism read `event.call_id`, which does not exist (it is `event.call.id`). The AttributeError was swallowed by the fail-open wrapper, so the mechanism was a **silent no-op**. The gate correctly reports "no efficiency gain" with savings of exactly 0.0.
- **L1 it1.** Rejected by the reviewer (no recall).
- The API doc never said the names are pre-imported, nor how to get the call id: this is **fix 13**.

**`solpi_agentworld_live_r2`** (after fix 13). No import or attribute errors.
- **L2 it0 and it1.** Rejected by the reviewer: the logs were stored under `rt.store["/<id>.txt"]`, but the agent was told to `cat /.solpi/...`. I checked `agentworld/base.py:_read_any`: store keys must *be* the `/.solpi/` paths, so the reviewer was **right** and the recall would have returned ENOENT.
- **L1 it0.** A new `recall` tool, reviewed as pass. The gate result: tokens −65%, cost −36%, but score 0.892 < 0.98 floor → "capability below floor". The lineage routed back to 01.
- **L1 it1.** Rejected (no recall mechanism).
- The doc gap about store keys is **fix 14**.

**`solpi_agentworld_live_r3`** (after fix 14). Haiku now stored under the correct `/.solpi/...` keys (checked in the diffs), but the reviewer rejected all four mechanisms:
- **L2.0 and L1.1:** "rt.store is in-memory, `cat /.solpi/...` unverified". **Wrong**: `_read_any` serves exactly those keys.
- **L1.0:** "returning None from the context hook on exception is an unverified assumption". **Wrong**: the API documents `context -> new list | None`.
- **L2.1:** "no try/except around the condensation logic". Plausibly right, because the contract demands fail-open; whether the runtime would have contained the exception is **unverifiable** from this run.
- Cause: `LLMReviewer`'s prompt did not contain the runtime API the implementer was given. **Fix 15** appends `RUNTIME_API_DOC` to the review prompt for SoL-Pi harnesses (unit-tested, not re-run live).

**Verdict on the SoL-Pi live runs.** Every protocol step was correct: gate arithmetic recomputed, no sealed evaluation in lineages, and reviewer verdicts checked against the runtime. The failures sat in the *interface docs given to the LLM roles*: two gaps in the implementer's API doc (fixes 13 and 14) and a reviewer that never saw the API (fix 15). All three are now fixed. The independent reviewer caught two real defects the smoke test could not see (r2), and made three false rejections without the API (r3). No live mechanism survived, which is consistent with the paper's "about one idea in forty survived", but n = 2 ideas says nothing about rates.

---

## Paper alignment audit (spec `docs/methods/meta-harness-and-sol-pi.md`)

### Meta-Harness: aligned
- No parent-selection rule, no mutation operators, no keep gate. Every valid candidate is evaluated once on the search split and kept (A3.1). The audit confirms that `evaluated ⇔ admissible` and that only the search split is evaluated during evolution.
- Pareto frontier (score up, context down, strict dominance, exact ties kept) and "best = highest-score Pareto point" (A4). The audit recomputes both independently every iteration.
- Per-unit best = argmax (score, −cost).
- Both deltas are logged: the release's post-iteration quirk and the pre-iteration value (A4).
- Test evaluated once in `finalize()` on baselines ∪ Pareto ∪ per-unit bests, and locked afterwards. `results/` never appears in any view (A2/A3.2).
- k = 2 matches the paper (the skill says 3); H0 = the release baselines no_memory + fewshot_all.
- The proposer reads raw traces. The mock's `files_read` includes traces every iteration, and the live proposer quoted trace lines.

### Meta-Harness: inconsistent or unverifiable
1. **Proposer.** The paper's proposer is a coding agent (Opus, grep/cat, it decides what to read). The live runs used `RewriteProposer`, where a *renderer* decides: priority order and a 60k-char budget, so iteration 3 saw only 59 of 130 files. `AgentProposer` exists but was not run live (cost/time). How far this validates the paper's "full, uncompressed history" is therefore **unverifiable**.
2. **Tie-break.** The frontier sorts by (−score, cost, **name**). The spec says (−a, c), and the release's tie order is list order, not name. Run 3 changed the incumbent on an exact tie because of this. It is minor, but it is not the release's behaviour.
3. **Interface validation.** Our smoke *runs a task with the model* (AgentQA). The release's text-classification check is an import that "prints OK within 30 s". I raised the timeout to 240 s for live runs; with 30 s, slow model calls would mark valid harnesses invalid.
4. **Overfitting.** No guard, faithful to the paper's main loop. Run 4 shows the consequence live: a template-regex harness reached the frontier. Run 1 shows that selecting on 3 search units can pick versions that are worse on ood.
5. **MemoClassify inner loop** is online (as in the paper's §4.1); the release default is offline (a deviation already listed in the impl doc).
6. **Proposer transcripts** of earlier iterations are not in the view. `AgentProposer.files_read` is the whole view (known).

### SoL-Pi: aligned
- The predeclared, digested gate: ∀ capability within τ ∧ ∃ efficiency better (B4.1). Recomputed independently for every gate call and every firewall verdict.
- Lineage stages 01–06 in order: rollouts, then map-reduce, then one mechanism, then the Ralph loop until the exit check passes, then the reviewer, then screen validation, then freeze or route back.
- Held-out firewall: frozen candidates only, each once. The driver receives a bool; lineages receive nothing; results go to a write-only sink. A held-out failure rejects without feedback (B3.1).
- The final (ood) split is never touched by the protocol (only by the report and the audit monitor).
- Survivors are composed as independent opt-in mechanisms in registration order.

### SoL-Pi: inconsistent or unverifiable
1. **Reviewer → Implementation.** In the paper's figure a reviewer rejection routes back to implementation. Here it consumes a lineage iteration and restarts at 01 (new rollouts), and the proposer starts again from the base. This is a deviation; with max_iters = 2 it ended both live lineages.
2. **"Route back to 04 (fix)"** is only partly modelled. After a gate failure the proposer always proposes from the *base* using gate feedback (a grid walk, or LLM history), never repairing its own last candidate.
3. **Held-out pass criterion = the same dual gate.** The spec marks this [inferred]; the sources do not spell it out. Tolerances of 2% / 2% are [inferred]; the only published numbers are the ObservationPack sweep's (−2% quality, 10% bill saving).
4. **The ∃-efficiency rule** lets a mechanism that raises the API bill pass (C23, cost +5.4%). The paper's stated objective is "API cost per unit of task score". Faithful to the gate as written, but at odds with that objective.
5. **The capability floor did not stop do-less shortcuts.** P20 at 24 turns passed training, and the firewall caught it only for lack of efficiency. The composed stack cost more than EPR alone, and composition is not re-gated. The blog itself warns that losses "can accumulate once mechanisms combine". Both are consistent with the impl doc's S6 / S7 findings.
6. **Oracle analysis** only orders the lineages when n_lineages equals the pool size (as here). The oracle ranks tricks near the top, as expected: it estimates opportunity, not validity.
7. **Scale.** 10 ideas and 3 families against 152 ideas and 535 environments. The rates (for example "1 in 40 survive") are **unverifiable** at this scale.
