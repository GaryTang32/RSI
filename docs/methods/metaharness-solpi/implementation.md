# Meta-Harness and SoL-Pi — implementation notes (`rsi.metaharness`, `rsi.solpi`)

This document covers two methods and the two domains built for them:

* `rsi/metaharness/`: **Meta-Harness** (Lee et al., 2026). A proposer reads every earlier candidate's code, scores and raw traces, then writes k whole-program harnesses per iteration. There is no keep gate. The output is a Pareto frontier, and the test split is evaluated once.
* `rsi/solpi/`: **SoL-Pi** (Liu, Ye et al., 2026). It has two layers:
  - a Pi-like agent runtime with the four released efficiency mechanisms;
  - the auto-research protocol: idea pool, disposable lineages, a predeclared dual gate across environment families, a one-way held-out firewall, and composition of survivors.
* `rsi/domains/memoclassify/`: online text classification in which the harness is a memory system around a frozen, deterministic model. This is Meta-Harness's CPU analogue.
* `rsi/domains/agentworld/`: multi-step agent environments with large tool outputs. There are three training families and two held-out families, a context-reading mock agent with two "backends", and an adapter for real LLM agents. This is SoL-Pi's MiniAgentWorld.

The spec is `docs/methods/metaharness-solpi/paper-spec.md`. Section numbers below (A3, B4.4, …) refer to it.

**Headline (offline, simulated worlds; details in §4).**

Meta-Harness:
- History ablation (M1, PARTIAL): full history beats scores-only and scores+summaries. The median proposed candidate gains +3.4 and +2.4 points, with CIs that exclude 0. The paper's second finding does not reproduce: here summaries help slightly over scores-only (+1.0 median, CI [0.4, 1.6]).
- Equal budget (M2, PARTIAL): Meta-Harness has the best final search score of the four arms. It beats Best-of-N (+6.2 points; Best-of-N now samples only from the run's seed, see §8) and GEPA-style reflection (+1.7) significantly. Its lead over the OPRO-style window (+2.6) is not significant. The "10× fewer evaluations" claim does not reproduce.
- Transfer (M4): the selected harness gains +10.2 points on unseen datasets and +13–16 points on an unseen model.
- Leakage (M5): without a guard, leaky candidates reach the frontier and win selection in 100% of runs. The pilot-style screen rejects all of them.

SoL-Pi:
- All four mechanisms are faithful ports: the economics test vectors pass exactly, and EPR receipts never contain a non-verbatim quote under injected hallucination. Since the review, EPR reduces the exact untruncated log and OCC prices `W = max(reported, estimated)`, as the release does (§8). The claims audit's 13 further fidelity mismatches (plan snapshot/advice/parsing, `source_lines`, UTF-16 lengths, excerpt splitting, recall offsets, Pi's `getContextUsage`, Action Fusion text and paths, the double projection) are fixed (§11); the release's reference vectors now pass 28/28.
- The full stack cuts tokens 54–69% and cost 16–61% at unchanged success (S7, PARTIAL). On backend A, EPR alone is cheaper than the full stack.
- Surviving across environments (S5, PARTIAL):
  - A single-environment efficiency objective admits tricks and do-less shortcuts and loses 40% of held-out capability. The multi-family dual gate plus the held-out firewall keeps 100% of held-out capability at a 39% cost-per-score saving.
  - A single-environment *dual gate* with the same firewall does just as well: 100% capability and a 40% saving. Training on more families rejects more tricks at the gate (3.0 → 1.6–1.8), but it gives no significant held-out gain once the capability floor and the firewall are in place.
  - What does the work here is the floor plus the firewall, not the environment diversity.
- Capability floor (S6, NOT REPRODUCED against the predeclared criterion): since the claims-audit fix the predeclared floor has two capability metrics (mean score AND solved rate), and it rejects both do-less shortcuts in every seed (the lenient turn cap used to pass). The composed stack still loses 3.8 points of training success, from an environment-specific trick (tail-trim@120) that is lossless alone; the criterion allows 2. An inferred composition re-check (`Config.validate_composition`) closes the gap.
- Effect sizes are smaller than the papers', and several sub-claims reproduce only partially. They are marked below.

---

## 1. Module map

### `rsi/metaharness/` (≈1,500 lines)

| module | contents |
|---|---|
| `config.py` | `Config`: `iterations` (N=20), `k` (2), `history_mode`, `window`, `objectives` (`("score","context_cost")`), `cost_metric` (`context_chars` = all model calls of a query; `context_chars_last_call` = the release's measurement; `tokens`), `search_split`, `test_splits`, `trials`, `reeval_incumbent` (0 = off, faithful; optional noise band), `tradeoff` (desired trade-off stated to the proposer), `eval_budget`, `leakage_screen` (False = faithful), `validate*`, `proposer_timeout_s` (2400), `finalize`, `summaries`. `HISTORY_MODES`. |
| `store.py` | `ExperienceStore`: the filesystem D (spec A5).<br>• Per-candidate `candidates/<name>/{src/, meta.json, eval/search/{scores.json, per_task/, traces/, summary.md}}`.<br>• Run files: `evolution_summary.jsonl`, `frontier_val.json`, `pending_eval.json`, `reports/`, `sessions/iterNNN/{prompt.md, response.md, meta.json}`.<br>• Sealed `results/<split>/<name>/test.json`, `frontier.json` and `finalized.json`, written only by `finalize()`.<br>• `view(mode)` projections: `full`, `scores_summary`, `scores_only`, `window`, `last_only`, `seed_only`. No view exposes `results/`.<br>• `materialize()`, and "history CLI" helpers `cli_frontier / cli_top / cli_diff / cli_show`. |
| `frontier.py` | `pareto_frontier` (a port of `compute_pareto_frontier`), `per_unit_best` (argmax (score, −cost)), `hypervolume`, `dominates`. Exact ties keep registration order in both (the release's list order). |
| `proposer.py` | `SKILL_TEXT` / `TASK_PROMPT`, adapted from the release's `SKILL.md` and `render_task_prompt`: Step 0 post-eval reports, Step 2 prototype (`PROTOTYPE_RUN` / `PROTOTYPE_ON_PAPER`), exploitation-axis rotation.<br>• `AgentProposer`: `claude -p` coding agent via `rsi.core.AgentEditor` and `TranscriptCLI`; the view is read-only under `_context/`, and the agent writes `agents/<name>/…`, `pending_eval.json` and `reports/…`. `prototype=True` (default, release behaviour) adds Bash allow-listed to `python3` and read-only commands; `files_read` comes from the `--verbose` transcript; `max_budget_usd` caps a session.<br>• `RewriteProposer`: any LLM, with the history rendered by `render_view`, whose budget is split by file kind (`READ_MIX`, the paper's 41/40/6/13 reading mix) so trace excerpts are always included.<br>• `LLMSummarizer`, `clean_code_block`, `resolve_base`, `CandidateSpec` / `ProposalBatch` (`files_read`, `files_scanned`, `reports`). |
| `mock.py` | `MockProposer`: a deterministic choice rule that may only use what the view exposes. It diagnoses its parents' raw traces (Markovian), falls back to summaries, then to random untried moves or crossover, writes Step-0 post-eval reports, and has an optional `leak_rate`. `files_read` = what a proposal was based on; `files_scanned` = what it only parsed.<br>Program libraries: `MemoClassifyLibrary(budget_hint=11000)` (the long-prompt prior, tuned to MemoLM-A), `AgentQALibrary`, `library_for(domain)`. |
| `validate.py` | `InterfaceValidator`: compile check, then `domain.smoke` in a raw-forked child that is killed after 30 s; the child streams its metered usage to the parent as it happens, so a killed smoke's completed calls are still counted.<br>`LeakageScreen`: the pilot's forbidden-reference check via `rsi.core.LeakageCritic`, plus a string-lookup-table shape check. |
| `loop.py` | `MetaHarnessLoop`: `run_baselines` → `iterate(t)` (view → propose k with the domain brief + `objective_text()` → store post-eval reports → screen → validate → evaluate on the search split → frontier → optional incumbent re-evaluation → summary rows) → `run` → `finalize()`. `finalize()` tests baselines ∪ Pareto ∪ per-unit best once, writes `results/` and `frontier.json`, locks further evolution, and is idempotent. |
| `api.py` | `run(...) -> ImprovementResult` and `make_proposer`. |

### `rsi/solpi/` (≈2,300 lines)

| module | contents |
|---|---|
| `meter.py` | `TokenMeter`: prefix-cache simulator. `cache_read` is the longest unchanged message prefix since the last request in the same scope; the rest is `cache_write`; output is billed separately; accounting is per role. `PriceTable` / `PRICES` (ρ = write/read = 12.5), `CostModel` (η = cost / score). |
| `runtime.py` | `AgentRuntime`, the Pi-like extension API:<br>• `register_tool(spec, replaces)` and `builtin()`;<br>• `on(event)` for `session_start / context / before_provider_request / tool_result / turn_end / agent_settled / input / session_compact`;<br>• `compact(instructions)` (native compaction: keeps the last `keep_recent_tokens` = 20,000, cut at an assistant boundary);<br>• `abort()` / `aborted`, `send_hidden()`, `append_entry()`, and a private `store` readable at `/.solpi/…`;<br>• `context_usage()` = Pi 0.85 `getContextUsage().tokens` (the last reply's usage, i.e. its request + its output, recorded as `details["usage_tokens"]`, plus the estimates of the messages after it; `None` right after a compaction);<br>• Pi-style auto-compaction (`should_auto_compact`) when `context_usage()` is strictly greater than window − 16,384, checked on the stored messages *before* the single `context` projection of each request.<br>Also `Message`, `ToolSpec`, `ToolResult`, `Extension`, `builtin_tools`, `default_summarizer`. |
| `fusion.py` | `ActionFusion`: `then_run` on edit/write (separate `EDIT_` / `WRITE_THEN_RUN_DESCRIPTION`), `resolve_tool_path` (port of `resolveToolPath`: unicode spaces, `@`, `file://`, `~` → home, relative → cwd), a per-file FIFO lock keyed on `canonical_queue_key` (realpath, so a symlink and its target share a queue), a sha256 hash guard around a real yield (`time.sleep(0)`; a test hook can replace it; a vanished file gives the release's ENOENT text), and the `[then_run:succeeded / failed / skipped]` markers with the release's exact text joins. |
| `obspack.py` | `ObservationPack`: projection-only rewriting. Results over 10 KiB that are non-error and not receipts become content-addressed observations; after `FULL_SENDS` = 2 they are replaced by a placeholder with a whole-line head/tail excerpt (lines end at `\n` only, as `split(/(?<=\n)/)`; token estimates use UTF-16 length). `obs_recall` validates `{id: string, offset?: integer ≥ 0}` like the release's schema and pages with a ≤ 15,872 B / 398-line UTF-8-safe body. There is a ledger (placeholder rows carry `originalLines`), and it fails open. Excerpt size, sends and head fraction are exposed for the sweep. |
| `reducer.py` | EPR, a `tool_result` handler:<br>• `DIAGNOSTIC_COMMAND`, `FAILURE_SIGNAL` and `LIKELY_SECRET` regexes (verbatim);<br>• the 4,096 B / 600,000-char limits (chars = UTF-16 units, as JavaScript counts), a content-addressed archive (`source_lines = body.split("\n").length`), and `reducer_instructions()` / `reducer_input()` (verbatim);<br>• `validate_receipt` with every rejection reason, `receipt_text`, and the "receipt-not-smaller" check;<br>• a journal of `candidate / provider_response / fallback / applied` entries.<br>Reducers: `DeterministicReducer`, `MockReducer(h)`, `LLMReducer(llm)` (like `provider.ts`: 90 s deadline, `max_tokens = min(2048, llm.max_tokens)`, the backend's stop reason mapped to Pi's `stopReason`). |
| `occ.py` | OCC:<br>• `decide_compaction` and `estimate_remaining_requests`, an exact port with every intermediate;<br>• `OnlineState` and its `record_*` transitions;<br>• plan parsing (`plan.ts`: exactly `{id, goal, status}`, non-empty bounded strings) and the `update_plan` tool with the release's argument schema, `{"steps":[…]}` snapshot and three advice lines;<br>• `W = max(getContextUsage().tokens, estimate)` via `AgentRuntime.context_usage()`; `turn_end` skips error / aborted replies;<br>• handlers for `turn_end` → abort, `agent_settled` → compact with `BOUNDARY_COMPACTION_INSTRUCTIONS` plus a hidden `POST_COMPACTION_PLAN_REMINDER`, and `session_compact` → debt `W·ρ'` repaid at `A − m` per request.<br>`policy="always"` gives the S4 "every boundary" arm. |
| `tricks.py` | Negative controls:<br>• environment-specific tricks `TailTrim`, `HeadTrim`, `PytestQuiet`;<br>• do-less shortcuts `NoVerify`, `TurnCap`;<br>• the dud `PromptSlim`;<br>• family M ("Improvement & evaluation") ideas `CostAttribution` (M5, a behaviour-neutral instrument) and `FailBeforePassAfter` (M12, a directive). |
| `registry.py` | Harness config → ordered extensions, in the release's registration order AF, OP, EPR, OCC. Code mechanisms come from `extensions/<name>.py` (`MECHANISM` or `register(rt, cfg)`). Also `with_mechanism`, and `parse_solpi_config`, which validates `sol-pi.json` strictly. |
| `gate.py` | `GateSpec` (frozen and digested; default capability metrics: mean `score` AND the fully-solved rate `solved`, 2 % each; a missing metric fails closed; modes `aggregate`, `per_family`, `efficiency_only`, `eta_better`), `DualGate` (with `as_core_gate()`, equivalent to `rsi.core.gates.DualGate`), `Metrics` / `metrics_from_eval`, `nondominated`, and `HoldoutFirewall`. The firewall is one-way: each frozen candidate is evaluated once, the result goes to a write-only sink, and only a bool is returned. |
| `research.py` | `Idea` / `IdeaPool`, `oracle_estimate`, `analyze` / `reduce_findings` (map-reduce), `implement` (the Ralph loop), `SmokeReviewer`, `Lineage`, `FrozenCandidate`, `compose` / `merge3`.<br>`Lineage` runs rollouts → analysis → propose → implement → review → validate → dual gate. It routes back on a failure; in sweep mode (the default) it keeps iterating after a pass and freezes the nondominated passing variant with the best η = cost / score (`sweep=False` freezes the first pass). |
| `mocks.py` | `AGENTWORLD_IDEAS` (12 ideas over all six families C, P, T, D, R, M; more than the default `n_lineages` = 10, so the oracle filters), `LibraryProposer` (walks a variant grid on in-lineage gate feedback; after a pass, the remaining variants in order), `AGENTQA_IDEAS` + `AgentQAEditProposer`, `LLMMechanismProposer` (writes a code extension against `RUNTIME_API_DOC`, or edits any artifact), `LLMReviewer`. |
| `driver.py` | `Config` and `AutoResearchDriver`: base metrics → oracle ranking → lineages → firewall → compose survivors → optional next round. Also `run(...) -> ImprovementResult`.<br>`Config.validate_composition` (off by default; an inferred extension): re-gates the composed stack on the training screen and drops survivors greedily, leave-one-out, until it passes. |

### Domains

| path | contents |
|---|---|
| `rsi/domains/memoclassify/` | `data.py`: synthetic datasets with confusable label clusters, label keywords, hard examples, optional in-input option lists and memorisable ids; 3 search + 4 OOD datasets + 1 leaky dataset.<br>`model.py`: `MemoLM` variants A/B. It reads demonstrations, label lists and label notes; answers only labels present in the prompt; loses demonstrations "in the middle" past its character budget; and emits JSON `final_answer`.<br>`memory.py`: the `MemorySystem` ABC (a port) and the `no_memory` / `fewshot_all` seed programs.<br>`domain.py`: `MemoClassifyDomain`, with an online inner loop (the release's offline mode is an option), full traces by default (every model call's prompt and raw reply for train and eval steps, per-step memory-state size, full-state checkpoints at step 0 and the last training step; `trace_detail="compact"` = the earlier thin format), `context_chars` summed over all model calls of a query (`Σ max(0, len(prompt_i) − len(input))`; `context_chars_last_call` = the release's measurement), `comparators()` (few-shot-N, N ∈ {4, 8, 16, 32, 64}), `validate_memory`, and `leakage_terms`.<br>`programs.py`: the offline program library (a `CONFIG` genome rendered as a readable `memory.py`, mechanism moves, parameter variants, trace diagnosis, a lossy summariser, and a leaky-lookup builder). |
| `rsi/domains/agentworld/` | `base.py`: the `Env` base, a virtual workspace at `cwd = /repo` whose file tools resolve paths like Pi's built-ins (`resolve_path`: `./a`, `/repo/a`, `@a` are one file; `~/x` is `/home/agent/x`), plus a mini shell (cat, ls, grep, head/tail, sed, wc, pipes; Pi's 50 KB / 2,000-line bash truncation), `Subtask`, the large project guide, and `validity_filter`. The filter checks that the verifier fails initially and passes after the reference fix.<br>`code_envs.py`: `repofix` (pytest; evidence at the tail), `buildfix` (make; the first error in the middle, then cascades) and `configfix` (held-out; validator error at the top of a long dump).<br>`data_envs.py`: `logtriage` (report checked by `make check`; needs the whole log) and `datalookup` (held-out; JSONL records, checker message at the top).<br>`envs.py`: re-exports and `FAMILIES`.<br>`skills.py`: per-family "skills" that parse evidence from the text the agent can see, and `skill_for`.<br>`policy.py`: `MockAgent` profiles A and B (habits: re-run after edits, fused-call uptake, recall and receipt use), `MockAgentLLM`, and the `LLMAgent` adapter.<br>`domain.py`: `AgentWorldDomain` (harness config artifact; splits `evolve` = training families, `holdout` = held-out acceptance, `ood` = held-out final, `test`), `harness(...)`, `oracle_stats`, `mechanism_audit`, and `render_trace`. |

---

## 2. Public API and applying the methods to a new problem

Both entry points follow the house contract: `run(domain, seed_artifact, *, llm_task, llm_propose, config, out_dir, ...) -> rsi.core.ImprovementResult`. `experiments/metaharness-solpi/example_new_problem.py` runs both parts below offline in a few seconds.

### Meta-Harness on your problem

```python
from rsi.core import Artifact, FunctionDomain, TaskSuite, ClaudeCLI, CachedLLM
from rsi.metaharness import Config, run

# 1. your problem: any rsi.core.Domain (here two plain functions) with an "evolve" (search) split and a
#    sealed "test" split; execute() should return rich Execution.trace text - the proposer reads it.
domain = FunctionDomain(TaskSuite(tasks, {"evolve": search_ids, "test": test_ids}), execute_fn, grade_fn,
                        name="my-problem", description="what the harness is, its interface, how it is graded")
seed = Artifact({"harness.py": open("my_harness.py").read()}, meta={"name": "seed"})

# 2a. paper setup: a coding agent reads the whole history directory (claude -p, Read/Edit/Write/Glob/Grep)
res = run(domain, seed, llm_task=my_frozen_model, llm_propose=ClaudeCLI("sonnet"),
          config=Config(iterations=10, k=2, objectives=("score", "context_cost"), cost_metric="tokens"),
          out_dir="runs/mh-my-problem", baselines={"seed": seed, "other_baseline": other})
# 2b. cheaper: one completion over the rendered history (any LLM, cached)
res = run(domain, seed, llm_task=my_frozen_model,
          llm_propose=CachedLLM(ClaudeCLI("haiku"), ".rsi_cache/mh"), proposer_kind="rewrite", ...)
# 2c. offline / your own heuristics: pass proposer=MyProposer() (subclass rsi.metaharness.Proposer)

res.best                            # highest-score Pareto point on the search split
res.meta["frontier"]["_pareto"]     # the frontier (score, context cost)
res.meta["final"]["splits"]["test"] # one-time test evaluation (baselines + frontier + per-unit bests)
res.loop.store.view("full")         # the experience store the proposer saw
```

Knobs: `history_mode` (ablations and baseline arms), `eval_budget` (equal-budget comparisons), `leakage_screen=True` (the pilot's pre-evaluation screen; forbidden terms come from `domain.leakage_terms()`), `trials`, and `test_splits`.

### SoL-Pi on your agent

```python
from rsi.solpi import (AgentRuntime, TokenMeter, PRICES, builtin_tools, ActionFusion, ObservationPack,
                       EvidencePreservingReducer, LLMReducer, OnlineContextCompact)

rt = AgentRuntime(my_backend,                 # .act(messages, tools, rt) -> assistant Message
                  system_prompt="...", env=my_env,   # env: read_file/tool_read/tool_write/tool_edit/tool_bash
                  meter=TokenMeter({"main": PRICES["sim-a"], "reducer": PRICES["reducer"],
                                    "compaction": PRICES["sim-a"]}))
for spec in builtin_tools(my_env):
    rt.register_tool(spec)
for ext in (ActionFusion(), ObservationPack(), EvidencePreservingReducer(LLMReducer(cheap_llm)),
            OnlineContextCompact(cache_write_read_ratio=12.5)):
    rt.add_extension(ext)                     # opt-in, registration order matters (OCC last)
rt.run(task_text)
rt.meter.snapshot()                           # tokens by kind (cache read/write/output) and $ per role
```

The research protocol runs on any domain with task families, a sealed `holdout` split and an idea pool:

```python
from rsi.solpi import Config, GateSpec, Idea, run
res = run(domain, base_harness, llm_task=agent_backend, llm_propose=CachedLLM(ClaudeCLI("haiku"), ".cache"),
          ideas=[Idea("C1", "C", "Stop replaying large successful outputs; keep them recallable"), ...],
          config=Config(gate=GateSpec(capability=(("score", 0.02),), efficiency=("tokens", "cost"),
                                      mode="aggregate"), n_lineages=8, max_iters=4),
          out_dir="runs/solpi-my-agent")
res.best                                  # composed harness of the firewall survivors
res.meta["rounds"][0]["lineages"]         # every lineage iteration with its gate result
res.meta["rounds"][0]["heldout_passed"]   # firewall verdicts (driver-only)
```

Offline there are ready-made idea pools and proposers for `agentworld` (`LibraryProposer`) and `agentqa` (`AgentQAEditProposer`). For a new domain, pass `llm_propose` (LLM implementer) or your own `proposer` with `propose(idea, base, evidence, history)` / `fix(...)`.

---

## 3. Capability checklist → code → experiment → result

### Meta-Harness (spec A10)

| # | overview claim | code | experiment / test | result |
|---|---|---|---|---|
| 1 | Coding agent reads a filesystem of every earlier candidate's code, traces and scores | `ExperienceStore` (+`view("full")`), `AgentProposer` (`claude -p` over `_context/`), `RewriteProposer` | `test_views_expose_exactly_what_the_mode_allows`; live smoke (haiku RewriteProposer) | implemented; the live run evaluated haiku-written candidates |
| 2 | Proposes new harness code (store / retrieve / present) | `MemorySystem` interface (`learn_from_batch` = store, `predict` prompt building = retrieve and present); whole-program candidates | M1–M5 | implemented |
| 3 | New harness is evaluated on the tasks | `InterfaceValidator` → `Evaluator` on the search split | `test_interface_validator_catches_errors_and_hangs` | implemented (compile + forked smoke, 30 s kill) |
| 4 | All logs saved to a new directory; loop repeats | `store.add_candidate / write_eval / log_session`, `evolution_summary.jsonl`, `MetaHarnessLoop.run` (resumable) | `test_views…`, `test_run_end_to_end_is_deterministic` | implemented |
| 5 | Scale of feedback vs OPRO (window) / TextGrad (current only) | `history_mode` ∈ full / scores_summary / scores_only / window / last_only / seed_only | M1, M2 | M1 partial: full history wins (small effect), but summaries do help over scores-only, unlike the paper. M2 partial |
| 6 | +7.7 points over SOTA context management with 4× fewer context tokens | `context_cost` objective, `pareto_frontier`, `per_unit_best`, `hypervolume` | M3 | reproduced qualitatively: a frontier harness beats `fewshot_all` on test with ~6× less context in 100% of runs |
| 7 | One harness improves maths on five models it wasn't tuned on | `rsi.core.transfer_report` over unseen datasets and an unseen model | M4 | reproduced qualitatively (selection model reported separately from MemoLM-B) |
| 8 | Also tested on agentic coding | Domain-agnostic loop; runs on `agentqa` (agent harness with tools); AgentWorld harness configs are artifacts too; any new `FunctionDomain` | `test_runs_on_agentqa_second_domain`, `test_metaharness_on_new_function_domain_with_scripted_llm` | partial: the TB2-style `AgentHarness` subclass validator is not implemented |
| 9 | Practised best (93.0) but <1 point unseen | practised vs unseen gap in M5 / M4 | M4, M5 | reproduced as a mechanism (the gap appears when leakage is possible) |
| 10 | Reading the full history is expensive | `sessions/iterNNN/meta.json`: `files_read`, `files_read_by_kind`, `view_chars`, `read_chars`, proposer usage | M1 read accounting; live smoke | full mode reads ~10× more history characters per iteration (541k vs 54k); live haiku proposer ≈ 23k input tokens / call |
| 11 | No explicit check for test-specific edits | `Config.leakage_screen=False` by default; `LeakageScreen` (pilot) | M5 | reproduced |
| 12 | Agent-ring row (edits harness code, learns from all history, no guard, end-to-end goal) | whole package; `finalize()` + transfer | all | implemented |
| 13 | Full, uncompressed history | `history_mode="full"` default, no summarisation layer | M1 | implemented |
| 14 | Coding agent rewrites harness code end to end | whole-program candidates, no templates or mutation operators in the loop | – | implemented (the offline mock uses a program library, §5) |
| 15 | Harness = prompts, tools, memory, control flow, context around a frozen model | `MemorySystem` + `MemoLM`; `agentqa` harness | – | implemented |
| (M7) | Test set never seen during search; finalize once and lock | sealed `test` split (`SealedSplitError`), `results/` outside views, `finalize()` lock and idempotence | `test_test_split_never_touched_during_evolution_and_finalize_locks` | implemented (ports of the two release contract tests) |

### SoL-Pi (spec B10)

| # | overview claim | code | experiment / test | result |
|---|---|---|---|---|
| 1 | Tokens are the bottleneck; efficiency metrics first-class | `TokenMeter` (prefix cache, per-kind tokens), `CostModel` (η) | S1–S7 | implemented |
| 2 | Autoresearch-style loops on the harness across environments | `AutoResearchDriver`, `Lineage` (rollouts → map-reduce → one mechanism → Ralph loop → review → validation) over AgentWorld families | S5, S6; `test_protocol_end_to_end_agentworld` | implemented |
| 3 | Keep only changes that survive everywhere | `DualGate` (`aggregate`; `per_family` literal option), `HoldoutFirewall`, held-out families, backend B | S5 | partial:<br>• held-out capability is 1.00 with the multi-family gate and firewall, vs 0.60 for a single-environment efficiency objective;<br>• tricks reaching the final stack drop from 1.2 to 0.2;<br>• a single-environment *dual* gate with the firewall is statistically indistinguishable (1.00 capability; η saving 0.40 vs 0.39). |
| 4 | Start from Pi (no patches; public extension API) | `AgentRuntime` base tools; mechanisms only through `register_tool` / `on` | tests | implemented |
| 5 | More and more varied environments | families + `validity_filter`; `Config.rounds > 1` (next round from the composed base; experimental and **not evaluated**, as in the sources) | `test_every_environment_passes_the_validity_filter` | implemented (5 families, not 535 environments) |
| 6 | What survives transfers | held-out families (configfix, datalookup) + backend B evaluation | S5, S7 | reproduced in simulation: firewall survivors keep 0.99 of held-out success on unseen backend B; the full stack keeps success on both backends |
| 7 | Four mechanisms | `ActionFusion`, `OnlineContextCompact`, `ObservationPack`, `EvidencePreservingReducer` | S1–S4, mechanism tests | implemented (faithful ports) |
| 8 | EdgeBench: −44.7..−49.0% tokens, ~−33% cost at ~94% of Pi's score, two backends | full stack on two backends | S7 | partial:<br>• full stack −54.0% tokens / −15.8% cost at 100% of base success (A, default);<br>• −54..−69% tokens and −16..−61% cost over 2 backends × 2 lengths;<br>• EPR alone is cheaper than the full stack on backend A. |
| 9 | Open code (~3k stars) | documentation pointer (NVlabs/SoL-Pi, MIT) | – | – |
| 10 | Same quality for less cost; one benchmark / two models / one harness | dual gate (non-inferiority + efficiency), two backends, base-agnostic runtime | S5–S7 | implemented |
| 11–12 | Agent-ring / timeline rows | whole stack | – | implemented |
| 13 | Mechanisms useful for any long-running agent | extensions against the generic `AgentRuntime`, independent of the driver | `example_new_problem.py` part 2 | implemented |
| 14 | Fight overfitting by surviving across environments | `DualGate` + `HoldoutFirewall` + training / held-out split | S5; `test_firewall_is_one_way_and_lineages_cannot_read_holdout`; `test_solpi_protocol_on_new_function_domain` | implemented. In S5 the floor and the firewall carry the effect; extra training families add no significant held-out gain. |
| 15 | Context compaction (summarise / drop old material) | OCC + ObservationPack | S2, S4 | implemented |
| 16 | Make cost part of the decision | `TokenMeter`, `CostModel`, efficiency half of `DualGate` | S5, S6 | implemented; S6 shows the floor is needed (efficiency-only admits do-less shortcuts, −51 points success). The two-metric floor (score AND solved) rejects every do-less shortcut; per-mechanism gating still lets a trick that is lossless alone through (−3.8 points composed) |
| (S9) | Held-out never feeds back | firewall owns the only unsealed evaluator; lineages hold no reference; each candidate evaluated once | test above | implemented |
| (S10) | Lineage works end to end with real LLM roles | `LLMMechanismProposer` + `LLMReviewer` | live smoke part 3 + `live_reanalysis.py` | partial: haiku wrote 6 mechanism files; after fence cleanup all compile; none passed the gate (one cut tokens 62% but lost capability; the floor rejected it) |
| (S8) | Breadth escapes local basins | – | not run | not implemented (the spec marks it as an uncontrolled comparison) |

---

## 4. Experiments and results

Every script is `python experiments/metaharness-solpi/<name>.py [--llm sim|claude:haiku] [--seeds N] [--quick] [--workers W]`. It writes `results/metaharness-solpi/<name>.json` containing the config, per-seed raw numbers, a summary (mean + 95% bootstrap CI via `rsi.core.stats.summarize_runs`, paired CIs where arms are paired) and a verdict. PNG figures are written where useful. All offline runs below were executed at full settings.

### 4.1 Meta-Harness (MemoClassify, 10 seeds, 3 search datasets, MemoLM-A)

All M1–M6 numbers below come from the re-runs after the claim-audit fixes (§10): full traces, all-calls context metric, registration-order ties, honest read accounting. M1–M5's selection numbers are identical to the pre-fix runs; the fixes changed what is logged and what the proposer is shown, not the mock's choices.

**M1 (history ablation), N = 8, k = 2.** Verdict: PARTIAL. The main claim holds: full history beats both ablations on the median, with CIs excluding 0, and on the best candidate on average. The sub-claim that summaries are no better than scores-only does not hold. The review tightened the verdict rule so that REPRODUCED requires both parts; the numbers are unchanged after the rerun.

| arm | median search | best search | selected test |
|---|---|---|---|
| scores_only | 0.544 | 0.591 | 0.559 |
| scores_summary | 0.554 | 0.583 | 0.559 |
| full | 0.578 | 0.618 | 0.575 |

Paired differences:

| comparison | median | best |
|---|---|---|
| full − scores_only | +0.034 [0.016, 0.059] | +0.027 [0.000, 0.062] (CI touches 0) |
| full − scores_summary | +0.024 [0.005, 0.052] | +0.035 [0.008, 0.074] |
| summaries − scores-only | +0.010 [0.004, 0.016] | −0.008 [−0.024, 0.008] |

The paper found summaries no better than scores. Here they help slightly on the median and not on the best.

The strongest Table-3 sub-claim, "even the median full-history candidate beats the best candidate of either ablation", holds in 2 of 10 seeds. The effect is far smaller than the paper's (+15 points median).

Read accounting (honest since §10): per iteration the full-history mock bases its proposals on 10.7 files (5.3 trace files, from 1.8 candidates: its parents) and only parses another 50.5 for bookkeeping; the scores-only arm uses 5.5 and parses 23.4. The full view is 22.9M characters per iteration, because traces now hold every prompt and raw reply (2.4M characters per candidate evaluation). The mock diagnoses only its parents' traces, so the full arm tests "the parents' raw traces vs none", not a non-Markovian use of the whole history. The offline proposer is a deterministic mock, so this shows that the views gate the information channel as designed, not how an LLM uses traces. Figure: `m1_history_ablation.png`. Sensitivity of M1 to the mock's tuned long-prompt prior (11,000 chars, next to MemoLM-A's hidden 12,000): see §10.

**M2 (equal budget, 20 evaluations).** Verdict: PARTIAL. Rerun after the review fixed the Best-of-N arm.
- Before the fix, the `seed_only` view showed both baselines, and because of dict order 77% of the samples mutated the zero-shot harness.
- It now shows only the run's seed (`fewshot_all`), per "independent samples from the seed".
- Best-of-N's final best moved from 0.568 to 0.558. The other arms are unchanged.

| arm | final best (search) | selected harness (test) |
|---|---|---|
| Best-of-N | 0.558 | 0.543 |
| OPRO window (w = 4) | 0.593 | 0.554 |
| GEPA-style reflection | 0.602 | 0.563 |
| Meta-Harness | 0.619 | 0.573 |

Meta-Harness has the highest mean and its curve is on top (`m2_equal_budget.png`), but the OPRO comparison is not significant. Paired differences, Meta-Harness − other:

| vs | search | selected harness on test |
|---|---|---|
| Best-of-N | +0.062 [0.038, 0.084] | +0.031 [0.004, 0.055] |
| OPRO | +0.026 [−0.003, 0.062] | +0.019 [0.003, 0.036] |
| GEPA-style | +0.017 [0.005, 0.032] | +0.010 [−0.003, 0.023] |

The paper's "matches others with 10× fewer evaluations" does not reproduce. Meta-Harness needs this many evaluations to match another arm's final value. Two medians are reported (`reach_stats`): over all seeds, counting a seed where Meta-Harness never matches as 21, and over only the seeds where it matches. The verdict uses the first.

| arm | median, all seeds (never = 21) | median, seeds where it matches | seeds where Meta-Harness never matches within 20 |
|---|---|---|---|
| Best-of-N | 4 | 4 | 1 of 10 |
| OPRO | 10 | 7.5 | 2 of 10 |
| GEPA-style | 10 | 10 | 1 of 10 |

**M3 (Pareto).** Verdict: REPRODUCED for the frontier claims; the Pareto objective gives a larger hypervolume than the scalar one but no better selected harness.
- The Pareto frontier holds 6.0 non-dominated harnesses on average.
- In 100% of runs a frontier harness beats `fewshot_all` in *test* accuracy with less context. The selected (highest-accuracy) harness uses 6.2× less context.
- Example seed 0: `fewshot_all` scores 0.458 search / 0.523 test at 17.7k chars; the selected harness scores 0.590 search / 0.634 test at 5.7k chars. This is the shape of Table 2 (+7.7 points with 4× fewer tokens).
- Pareto − scalar hypervolume: +290 [118, 498] (paired CI above 0; earlier versions of this document called it not significant). Frontier size is 6.0 vs 6.7, and selected test accuracy 0.575 vs 0.580.
- `fewshot_all` overflows MemoLM-A's context budget on two of three datasets, so "6.2× less context than `fewshot_all`" flatters the harness. Against the release's few-shot-N baseline (N ∈ {4, 8, 16, 32, 64}, evaluated after the search, outside the population), the best few-shot variant by search score is `fewshot_64` or `fewshot_all` (5 seeds each). The selected harness beats it on test by +0.105 [0.078, 0.131] with 4.5× [3.6, 5.4] less context, and beats the best few-shot variant that uses no more context than itself by +0.244 [0.205, 0.280]. In every run a frontier harness beats the best few-shot variant on test with less context.

**M4 (transfer, no re-search).** Verdict: REPRODUCED. Gains of the selected harness over `fewshot_all`:

| setting | gain |
|---|---|
| search | +0.143 [0.128, 0.160] |
| test (same datasets, model A) | +0.113 |
| 4 unseen datasets (model A) | +0.102 [0.083, 0.121] |
| unseen model MemoLM-B, test | +0.132 [0.107, 0.155] |
| unseen model MemoLM-B, unseen datasets | +0.158 [0.143, 0.174] |

On unseen datasets the gain is smaller than the search gain, as the claim predicts. On model B the gains are *larger*, because `fewshot_all` overflows B's smaller context budget. The selection model (A) is reported separately from the truly unseen model (B), per spec A8.10.

**M5 (leakage), leak rate 0.3 per iteration.** Verdict: REPRODUCED.
- Screen OFF: 9.8 leaky candidates evaluated per run and 4.2 on the frontier; a leaky harness is selected in 100% of runs. Search gain is +0.52 while test gain is −0.08, giving a practised-vs-unseen gap of 0.60. Leakage propagates through lineages, and once it dominates the metric, real regressions cannot be seen.
- Screen ON: 3.4 rejected before evaluation, 0 evaluated, gap 0.02.
- Gap difference (off − on): +0.58 [0.48, 0.69].
- The leak rate is injected. The experiment measures consequences, not how often LLM proposers leak.
- The script's "leaky" label comes from the same screen, which would be circular. The review therefore audited ground truth: the mock's `lookup` move, plus candidates whose source carries a lookup table.
  - Screen OFF: 36 injected lookups and 62 descendants that inherited the table were evaluated (9.8 per run).
  - Screen ON: all 34 injected lookups were rejected, with 0 false positives, and none of the 126 evaluated candidates carries a table.

**Live smoke** (`live_smoke.py`, claude-haiku-4.5; `results/metaharness-solpi/live_smoke.json`).
- **Meta-Harness (re-run after the claim-audit fixes, fresh cache, $0.214):** 2 iterations × k = 2 with a haiku RewriteProposer (about 26k input / 11k output tokens per call). The renderer now shows 6 and 8 trace files (it showed none of the traces on this domain before), haiku wrote the Step-0 reports (`reports/iter0.md`, `iter1.md`), tagged axes, and walked mechanisms through trace examples "on paper". Three of four candidates validated (one syntax error): search 0.421, 0.263 and 0.298 at 950–2,018 context chars, against `fewshot_all`'s 0.474 at 8,668. None beats the baseline; `selective_relevance` joins the frontier at 9× less context (test 0.476 vs 0.560).
- **The first live smoke's Meta-Harness numbers were wrong** (claim audit N1): every haiku file was written as `agents/<name>/src/memory.py`, a dead file (fixed later as fix 12), so the four "candidates" were copies of `fewshot_all` and scored exactly its numbers (0.4737 / 0.5595 / 8,667.7). `live_reanalysis.py` now replays those two proposer calls through `CachedLLM(offline=True)` ($0; recorded prompts in `results/metaharness-solpi/live_smoke_original_sessions/`) and re-parses them with the current code: 4/4 validate and all are real edits, scoring 0.456, 0.421, 0.404 and 0.456 on search (test 0.548, 0.524, 0.440, 0.524): all below `fewshot_all` (0.474 / 0.560), one at 2,471 context chars.
- **Coding-agent proposer (`live_smoke_agent.py`, $0.737, first live run of `AgentProposer`):** haiku as `claude -p` with `prototype=True`, 2 iterations × k = 2, $0.6 cap per session. It opened 8 of 20 and 8 of 40 history files (by its own transcript; traces of one candidate per iteration, read with `head` and with Python scripts), ran Python 12 times per iteration, wrote 2 and 6 prototype scripts, and wrote both post-eval reports. All 4 candidates validated; three match `fewshot_all`'s search score (0.474) at 1,792–2,443 context chars, so `adaptive_topk` dominates `fewshot_all` on the search frontier (test 0.536 vs 0.560). This is a small haiku run, not the paper's Opus setting.
- **EPR:** a haiku reducer produced 4/4 accepted receipts with 0 non-verbatim quotes.
- **LLM lineage:** haiku wrote mechanism code. After fence cleanup all 6 files compile. One cut tokens 62% but lost capability (it replaced large outputs before the agent read them), and the dual gate's floor rejected it.

### 4.2 SoL-Pi (AgentWorld, 5 seeds × {backend A, backend B})

**S1 Action Fusion.** Verdict: REPRODUCED. On backend A:
- Requests −23.1% [−21.8, −24.0], tokens −20.4%, cost −11.7%, with success unchanged (paired Δ = 0.000).
- The oracle-predicted request reduction (adjacent edit→command transitions / requests) is −29.9%. The oracle adjacent-transition share is 32% (the blog reports 12.3%).
- All four scripted safety checks pass: an interleaved write gives `[then_run:skipped]` and the command does not run; a failed mutation never runs the command; a non-zero exit keeps the edit; success gives the `succeeded` marker.
- Backend B uses fusion much less (1.3 vs 3.5 fused calls per task): requests −9.3%, cost −8.4%.

**S2 ObservationPack.** Verdict: PARTIAL.
- Recall fidelity is 100% over 300 payloads, including multi-byte UTF-8, and fail-open works (injected storage failures: no quality change, originals kept).
- Every sweep point stays inside the −2% quality gate, so the gate does not discriminate as in the blog's sweep. None reaches the 10% bill-saving gate, including the blog's exact V2 point (2,048 B head + 1,536 B tail, 2 sends; added after the claims audit). Token savings are 11–15% (A) and 8–11% (B), but bill savings are only −2..+3%.
- Why (claims audit Q13: a domain property, not a parameter one; `bill_decomposition` in the JSON): replayed large outputs are only 5.9% (A) / 4.8% (B) of the base bill as cache reads, which caps what any packing can save. Packing cuts cache reads 15% / 11%, but each placeholder swap-in breaks the prompt prefix and re-writes the suffix (cache writes +12% / +8%) at 12.5× the read price. Priced at ρ = 1 the same packing saves 5.8% / 6.1%, at the ceiling. Recalls are negligible (0.2 / 0.02 per task), and longer sessions make the bill worse, not better (scratch probe at 16–20 subtasks).
- 1-send configurations save more money than 2-send ones.
- Our agents rarely need an old large successful output again, so packing seldom costs quality. This is a property of the simulated world, stated as a limitation.

**S3 Evidence-Preserving Reducer.** Verdict: REPRODUCED.
- Accepted receipts contained **0 non-verbatim quotes** at every injected hallucination rate h ∈ {0, 0.1, 0.3, 0.6, 1.0}.
- Failure logs always carried failure evidence.
- Acceptance falls monotonically with h (bench: 1.00 / 0.90 / 0.67 / 0.31 / 0.00), with fallbacks distributed over invalid-json, schema-mismatch, unverifiable-quote and missing-failure-evidence.
- With a faithful reducer on backend A, tokens fall 45.0% and cost 30.4%, with success unchanged. On the log bench, bytes go from 267 KB to 29 KB.
- Rerun after the review. EPR now reduces the exact, untruncated log (`/tmp/pi-bash-*.log`) when Pi truncated the bash output, as `candidate.ts:exactBodyFromInline` does. Before, it reduced the 50 KB tail preview; the earlier figures were −47.8% tokens and −32.4% cost.

**S4 Online Context Compact** (4 arms × 4 task-length bins × 2 backends, ranked by cost per score η). Verdict: REPRODUCED.
- The economics test vectors pass exactly, and OCC never compacted with S ≤ 0 (0 violations).
- OCC's η is within 5% of the best arm in 8/8 (backend, length) cells, and it is the best arm in 5 of them: A 12–16 and all four B cells.
- OCC cost vs Pi's late compaction:
  - backend A: +1.8%, +0.2%, −1.5%, +2.3% for 4–8, 8–12, 12–16 and 16–20 subtasks;
  - backend B: −7.8%, −17.3%, −13.2%, −4.9%.
  - This is the re-run after the claims-audit fixes (§11): `W` now follows Pi's `getContextUsage` (the last reply's usage plus the tool results after it), and Pi-style auto-compaction checks the same measure with a strict `>` before a single projection. The comparisons moved by at most 2.5 points (the earlier rerun, with the last request's size as "reported": A +1.3 / +0.0 / −2.3 / +2.3, B −5.3 / −15.6 / −12.1 / −4.2).
- On short backend-A sessions the plan-tool overhead is not repaid, so "never" and "late" (which do not compact there) tie for best.
- Compacting at every boundary costs +30% vs late on average (+28% before the §11 fixes).
- Never compacting overflows the 200k window in up to 40% of the longest runs, and those runs fail (success 0.60 on A 16–20).
- Figure: `s4_occ.png`.

**S5 survive across environments** (5 seeds; 12 ideas: 4 general, 3 environment-specific tricks, 2 do-less shortcuts, 1 dud, 2 evaluation-family ideas; the oracle gives the top 10 rollouts). Verdict: PARTIAL. This was REPRODUCED before the review; the review added a direct test of environment diversity (see the end of this subsection). Re-run after the claims-audit fixes (§11: two-metric capability floor, nondominated sweep, 12-idea pool).

Four training protocols were compared, each followed by the held-out firewall (configfix, datalookup) and composition. Final-stack capability ratio and η saving were measured on the held-out families:

| protocol | admitted by training gate (general / trick / do-less) | tricks after firewall | held-out A: capability / η saving (firewall) | same, no firewall | held-out B capability (firewall) |
|---|---|---|---|---|---|
| single environment, efficiency only (η) | 3.4 / 3.0 / 2.0 | 1.2 | 0.603 / +0.27 | 0.590 / +0.19 | 0.526 |
| single environment, dual gate | 4.0 / 3.0 / 0.0 | 0.6 | 1.000 / +0.40 | 0.990 / +0.28 | 0.956 |
| all training families, aggregate dual gate | 4.0 / 1.6 / 0.0 | 0.2 | 1.000 / +0.39 | 0.992 / +0.26 | 0.988 |
| all training families, per-family dual gate | 3.6 / 1.6 / 0.0 | 0.2 | 1.000 / +0.39 | 0.992 / +0.30 | 0.988 |

- More environments at training time reject more tricks: 3.0 → 1.6 admitted.
- The firewall removes most of the rest: 0.2 reach the final stack.
- The firewall also raises η saving by 0.09–0.12, because it drops mechanisms that do not pay on the unseen families. OCC (C6) is rejected by the firewall in every run: held-out sessions are short, and the plan overhead is not repaid there.
- The composed stack of survivors keeps 0.99 of held-out success on the unseen backend B.
- Caveat: which tricks break where is a property of the simulated families (evidence position in outputs).

**Is it the environments?** Hold the capability floor fixed and compare the multi-family protocols (c, d) with the single-family dual gate (b), pairing by seed. The held-out outcomes are statistically the same:

| multi-family − single-family dual gate | backend A capability | backend A η saving | backend B capability |
|---|---|---|---|
| aggregate | +0.000 | −0.004 [−0.010, 0.000] | +0.031 [0.000, 0.069] |
| per family | +0.000 | −0.012 [−0.025, −0.002] | +0.031 [0.000, 0.069] |

More training families do reject more tricks at the gate: 3.0 are admitted with one family and 1.6 with three. (Before the two-metric floor, the aggregate multi-family gate also admitted the lenient turn cap in every seed; now no protocol with a floor admits a do-less shortcut.) In this simulation the separation between tricks and general mechanisms comes from the capability floor and the held-out firewall. The claim that the extra environments make the survivors transfer better is not shown. Hence the verdict is PARTIAL.

**S6 capability floor** (5 seeds, no firewall, to isolate the training gate). Verdict: NOT REPRODUCED under the predeclared criterion, which requires 0 do-less shortcuts admitted by the dual gate AND at most 2 points of composed training-success loss. Re-run after the claims-audit fix (§11): the predeclared floor now has two capability metrics, the mean score AND the fully-solved rate. The first half of the criterion now holds; the second does not.

| arm | do-less admitted | general admitted | Δ success train | Δ success held-out | token saving (train) | cost saving (train) |
|---|---|---|---|---|---|---|
| efficiency only | 2.0 | 4.0 | −0.509 [−0.543, −0.467] | −0.461 | 92.7% | 73.0% |
| dual gate (score AND solved within 2%) | **0.0** (was 1.0) | 4.0 | −0.038 [−0.063, −0.012] (was −0.073) | −0.002 | 72.1% | 33.3% |
| dual gate + composition re-check (inferred) | 0.0 | 3.4 | 0.000 [0.000, 0.000] | −0.013 | 76.7% | 44.3% |

- The floor rejects both do-less shortcuts in every seed: skipping re-verification (P14), and the turn cap at every length. The mildest cap (24 turns) scores 0.984, inside a score-only 2% floor, but finishes only 22 of 24 tasks (solved 0.917), so the second capability metric rejects it.
- The remaining composed loss comes from an environment-specific *trick*, not a do-less shortcut: tail-trim at 120 lines is lossless on the training screen in 3/5 seeds and passes the floor, but loses 4.8–8.3 points once composed with the other survivors. This is the failure mode the blog warns about for composed stacks; in the full protocol (S5) the firewall, not the floor, stops such tricks.
- The composition re-check (`Config.validate_composition=True`) re-gates the composed stack and greedily drops survivors until it passes: it dropped tail-trim 2×, Action Fusion 2× and OCC once, restoring the training floor (±0.0 points). It is our addition; the sources gate mechanisms one at a time.
- `GateSpec(capability=(("score", 0.02),))` is the former score-only floor (explicit option); with it the turn cap is admitted in every seed (before: do-less 1.0, composed loss −7.3 points).

**S7 composition** (full stack vs each mechanism alone; 5 seeds; 2 backends × {default, long} task lengths). Verdict: PARTIAL. Re-run after the claims-audit fixes (§11); the numbers moved by at most 0.4 points (OCC alone by up to 1.8).

| cell | +AF | +OP | +EPR | +OCC | full stack | full-stack success ratio |
|---|---|---|---|---|---|---|
| A default | −24.9% / −14.9% | −12.5% / +0.6% | −47.5% / **−32.8%** | −17.3% / −2.3% | −54.0% / −15.8% | 1.000 |
| A long | −28.3% / −19.9% | −4.1% / +1.2% | −56.2% / **−45.9%** | −23.5% / −1.5% | −58.4% / −30.8% | 1.000 |
| B default | −9.1% / −5.9% | −10.6% / −0.3% | −49.1% / −43.4% | −8.9% / +0.1% | −61.8% / **−44.4%** | 0.998 |
| B long | −16.6% / −12.6% | −5.9% / −2.2% | −61.6% / −60.6% | −25.3% / −13.2% | −68.5% / **−61.1%** | 1.006 |

Each cell is the token change / cost change vs the base harness; bold marks the cheapest arm.

- Every mechanism saves tokens alone, and success is unchanged. The full stack saves the most tokens in every cell.
- On cost the full stack is the cheapest arm only on backend B. On backend A, EPR alone is cheaper, because OP swap-ins and OCC compactions rewrite the cache prefix at ρ = 12.5.
- Headline vs the paper: −54.0% tokens and −15.8% cost at 100% of base success (A, default), against EdgeBench's −44.7..−49.0% tokens and about −33% cost at about 94% of Pi's score. Before the review the A-default figures were −56.5% / −19.4%: EPR had reduced the truncated tail preview of large pytest logs, which understated their size.
- Mechanism uptake differs by backend, as the blog reports:
  - Action Fusion fires 3.29 times per triggered task on A vs 1.91 on B;
  - OCC triggers in 5% of A-default tasks, 38% of A-long, 0% of B-default and 22% of B-long.
- A caveat found in the review: with the faithful exact-log EPR, the stand-in `DeterministicReducer` quotes the *first* 11 failure lines under the 12-item cap. On very long multi-failure pytest logs (16–20 failing tests, 80-turn cap) this drops the per-test summary lines, and repofix success falls (spot check: 0.97 → 0.88 on backend A). The S7 "long" cell (12–16 subtasks, 200-turn cap) is not affected. An LLM reducer that follows the instruction to "prefer … failing targets" may behave differently.


---

## 5. Deviations from the spec (and why)

**Meta-Harness**
1. *Offline proposer.* The paper's proposer is Claude Code with Opus. Offline we use `MockProposer`, which writes whole programs from a design space via a `CONFIG` literal that it parses back from source:
   - its choice rule sees only the view (raw-trace diagnosis → summary diagnosis → untried random move or crossover);
   - the design space includes plausible-but-harmful mechanisms and **parameter variants**, because the release's skill warns that parameter sweeps "almost always regress or tie";
   - without these, random proposals improved about half the time, far above the <5% useful-edit rate reported for Meta-Harness-style runs [ye-blog];
   - this prior is a stated modelling choice and shapes M1/M2.
2. *Domain scale.* Datasets have 14–36 labels (spec: 20–200); search sets have 48 examples per dataset; MemoLM is a lexical nearest-neighbour "LLM" with a context budget.
3. *Test finalisation* re-runs the (deterministic) inner loop instead of reloading `memory.json`. Under MemoLM these are equivalent.
4. *Online inner loop* by default (the paper's setting; the release default is offline). Per-example eval records are always written, fixing spec A8.12.
5. *AgentProposer.* It uses `rsi.core.AgentEditor` (Read/Edit/Write/Glob/Grep) through `TranscriptCLI`. Since the claim-audit fixes (§10) it follows the release more closely: `prototype=True` (default) adds Bash, allow-listed to `python3`/`python` and read-only inspection commands (the release runs full Bash with `--dangerously-skip-permissions`); the `--verbose` transcript gives `files_read` (files opened with Read or cat/head/tail/grep/…); the agent may write `reports/`. There are no subagents (the text-classification skill forbids them). SAFETY: the allow-list filters shell commands (network and writes outside the workspace need approval and are denied under `-p`), but `python3` is unrestricted code execution; use `prototype=False` or a container when that is not acceptable. The RewriteProposer cannot run code, so its skill asks for a prototype "on paper"; the mock never prototypes.
6. Defaults: k = 2 (paper §4.1; the skill says 3). Experiments use N = 8 (M1, M3–M5) or a budget of 20 evaluations (M2) instead of N = 20.
7. The `evolution_summary` row logs both `delta` (post-iteration best, the release quirk) and `delta_pre`.
8. The history "CLI" is a set of functions. There is no `DomainOnboarding`. The pilot's in-agent `evaluate_harness` tool (spec A3.4) is not implemented; `eval_budget` covers the equal-budget use. `eval_budget` counts only *evaluated* candidates. In the pilot, a candidate rejected by the leakage or interface check also consumes budget.
9. *Leakage screen.* It matches whole tokens (via `rsi.core.LeakageCritic`, min term length 4) over added lines, plus a string-table shape check. The pilot uses case-insensitive substrings over the whole source.
10. *Context cost.* A system's context cost is the mean of its *non-zero* per-unit context chars, as `print_frontier` computes it. Per query, MemoClassify sums the injected context of ALL model calls (§10, audit N2); the release (`inner_loop.py:evaluate_memory`) counts only the last call, which a harness can game with a tiny final call (17,724 → 46.5 chars). The paper's own two-call harness is described as keeping "the overall context cost" low "even with two model invocations", so it counts both. `Config(cost_metric="context_chars_last_call")` restores the release's measurement. Prompts are recorded by the domain's model wrapper, not by the candidate's `call_llm` bookkeeping (§8); the release measures inside the candidate, where a candidate could bypass or override the measurement.
11. *Noise band (optional, off).* `Config.reeval_incumbent > 0` re-scores every new frontier `_best` on more seeds before it is accepted. Not in the paper or the release (both select on one seed); it exists to measure the winner's curse (M6).

**SoL-Pi**
1. *Runtime and agent.* A Python re-implementation of the subset of Pi's extension API that the four mechanisms use. The agent is a deterministic context-reading policy with two habit profiles, not GPT-5.6 Sol / Opus 5. `LLMAgent` exists but was not run live because of cost.
2. *Tokens and prices.* The token estimate is chars/4. The prefix cache works at message granularity. Prices are invented but keep ρ = 12.5.
3. *OCC.* Abort → settle → compact → hidden reminder happens synchronously inside the loop. The native summariser is deterministic and keeps the task and the agent's NOTE/PLAN/DONE lines. Session restore (`restoreOnlineState` from the branch) and session-tree events are not modelled: the runtime has no session files or branches. `input` / correction handling is implemented but never triggered by the mock agents. `W = max(getContextUsage().tokens, estimated)` with Pi 0.85's semantics (`AgentRuntime.context_usage()`: the last reply's reported usage plus the estimates of the messages after it; claims audit §3 item 8 — before the fix the size of the last request stood in for it).
4. *EPR.* An archive integrity failure throws, as in the release; the runtime, like Pi's extension runner, keeps the original result and records a `tool_result_handler_error` entry. There is no reasoning-effort setting. `LLMReducer` strips a stray Markdown fence before byte-exact validation. Truncated bash results are reduced from the exact full log: `details.fullOutputPath`, or the inline `Full output: <path>` note. Only a `pi-bash-*.log` directly in `/tmp` is trusted, and the runtime store stands in for the file system. The offline `DeterministicReducer` is our own stand-in; it quotes the first failure-looking lines verbatim.
5. *Research protocol.* The prompts were not released, so ours are our own. Other details:
   - an efficiency metric "improves" only by more than `GateSpec.min_gain` = 2% relative, and capability tolerances are relative (2%). The sources give no numeric thresholds; the ObservationPack sweep used a −2% quality gate and a 10% bill-saving gate;
   - the predeclared capability metrics are the mean task score AND the fully-solved rate ("every capability metric must stay within a tolerance declared before the search starts"; the sources do not list the metrics). A score-only floor admitted a 24-turn cap (score 0.984, but 2 of 24 tasks unfinished; claims audit L4); `GateSpec(capability=(("score", 0.02),))` restores it;
   - `SmokeReviewer`'s denylist is generic split words plus the task families that occur only in the domain's sealed splits, derived from the domain;
   - the oracle statistics come from trajectories;
   - the offline implementer maps ideas to registry mechanisms with variant grids, and its Ralph-loop exit check is `domain.smoke`;
   - the reviewer is `SmokeReviewer` (smoke + held-out-reference denylist) or `LLMReviewer`;
   - the held-out pass criterion is the same dual gate on the held-out split [inferred in the spec];
   - `per_family` = capability within tolerance in every family + aggregate efficiency gain + no family regressing >5% [inferred];
   - "nondominated" retention applies among a lineage's gate-passing variants and is on by default (`sweep=True`): the lineage freezes the nondominated variant with the best η = cost / score. Survivors of different lineages are composed (union of opt-in mechanisms), not filtered for dominance: they are different mechanisms, not competing variants;
   - the Oracle Analysis ranks the pool and only the top `n_lineages` ideas get rollouts (12 AgentWorld ideas vs the default 10);
   - `Config.validate_composition` (off by default; used only in the S6 third arm) re-gates the composed stack. The sources gate each mechanism alone, and S6 shows why a re-check may be needed.
6. *Environments.* Five simulated families (not 535 real repositories and 40 synthesised environments). The idea pool has 12 ideas over all six families (not 152); family M is represented by two evaluation ideas that cannot save tokens in a harness. Idea `kind` labels are ground truth used only in analysis.
7. The multi-round loop (`rounds > 1`) is implemented but not evaluated. S8 (breadth vs depth) was not run.

---

## 6. Limitations (read before citing numbers)

* **Everything offline is a simulation designed for the claim it tests.** MemoLM, the MockProposer, the AgentWorld families and the MockAgent encode assumptions: where evidence sits in outputs, how often edits help, and how agents react to placeholders and receipts. Verdicts show that the *mechanisms and protocols behave as claimed under the stated models*, not that the papers' magnitudes transfer. Effect sizes are generally smaller than reported: M1 +3.4 points vs +15; M2 is significant against only two of three baselines.
* **Offline proposers are not LLMs.** M1/M2's history effects come from a rule-based proposer. The one live run (2 iterations, haiku) found no improvement over the baseline.
* **ObservationPack and OCC economics depend on session length.** Our sessions are 15–60 requests, not hours, so ObservationPack saves tokens but little money, OCC is cost-neutral on backend A and saves 4–16% on backend B, and the full stack is not the cheapest arm on backend A (S7). All of this matches what the economics predict.
* **Noise.** Small screens (3 tasks per family) let tricks slip through training gates by chance, as the quick runs showed. The full runs use 8 tasks per family and a 6-task-per-family firewall.
* **Gating one mechanism at a time is not enough here (S6).** With a score-only floor a lenient turn cap passed; the predeclared floor now also requires the fully-solved rate within 2% and rejects every do-less shortcut. A trick that is lossless alone (tail-trim@120) still passes and costs 3.8 points in composition. We report this as non-reproduction of S6's criterion instead of tuning the tolerance or the environments until it passes.
* **Live coverage.** A single live smoke ($0.48) exercised the haiku proposer, reducer and implementer. The coding-agent proposer (`AgentProposer`) and the LLM agent backend were not run live. Every script accepts `--llm claude:haiku` for showcases.

---

## 7. Files

`rsi/metaharness/*`, `rsi/solpi/*`, `rsi/domains/memoclassify/*`, `rsi/domains/agentworld/*`; tests `tests/metaharness-solpi/test_metaharness-solpi_{metaharness,mechanisms,solpi,genericity,review}.py` (53 tests, about 20 s); experiments `experiments/metaharness-solpi/{m1..m5,s1..s7}_*.py`, `live_smoke.py`, `live_reanalysis.py`, `example_new_problem.py`, `sp_validate.py` (SoL-Pi validation runs after the claims-audit fixes); SoL-Pi regression tests for those fixes `tests/metaharness-solpi/test_solpi_fixes.py`; results `results/metaharness-solpi/*.json|png`.

---

## 8. Adversarial review log (2026-09-25)

A second engineer reviewed the code against the spec line by line. They added tests on new domains, re-ran every experiment whose code or library code changed, and corrected claims.

**Bugs and fidelity gaps fixed**

| # | problem | fix | effect on results |
|---|---|---|---|
| 1 | *Best-of-N was not "independent samples from the seed".* The `seed_only` view showed every baseline, and the mock proposer took `pool[0]` by dict order, so 77% of M2's Best-of-N samples mutated the zero-shot harness. | The loop takes `seed_names`, and `run()` passes the run's seed. The view shows that harness only. | M2 re-run: Best-of-N 0.568 → 0.558; Meta-Harness lead +0.051 → +0.062 |
| 2 | *EPR reduced the truncated preview.* Pi's bash tool keeps the last 50 KB of a large output. `candidate.ts:exactBodyFromInline` reduces the untruncated `/tmp/pi-bash-*.log`; the port reduced the preview. | `exact_body()`: uses `details.fullOutputPath` or the inline `Full output:` note, with the same path-safety rule. | S3 and S7 re-run: EPR A-default −47.8%/−32.4% → −45.0%/−30.4% tokens/cost; see the S7 caveat on long logs. |
| 3 | *OCC's write tokens ignored the provider-reported context.* `extension.ts:contextTokens` is `max(reported, estimated)`. | Implemented (runtime request size = reported). | S4 re-run: shifts of 0.4 points or less |
| 4 | *AgentProposer evaluated candidates from a failed or timed-out agent.* The release skips the iteration when `claude_wrapper` returns ok=False (for example exit 124), even if `pending_eval.json` was written. | The iteration is skipped on `agent error`. | none offline |
| 5 | *Graders the artifact could influence.*<br>• MemoClassify measured context cost through the candidate's own `call_llm` bookkeeping; a candidate calling `self._llm` directly reported 0 context.<br>• AgentWorld code mechanisms run in-process next to the token meter that grades efficiency. | • MemoClassify records the last prompt in the domain's model wrapper.<br>• AgentWorld runs an integrity check after each run (meter identity, no instance-patched methods, every provider request billed); a violation fails the trial. | none for library candidates (M3–M5 re-run: identical) |
| 6 | *Genericity.* `make_proposer` sent any plain `MockLLM` to the offline `MockProposer`, which raises for domains without a program library. A scripted LLM on a new domain therefore crashed. | A plain `MockLLM` goes to `RewriteProposer` when the domain has no library. | none |
| 7 | `SmokeReviewer` hard-coded AgentWorld's held-out family name (`datalookup`) in generic code. | The denylist is derived from the domain: families that occur only in sealed splits. | none (built-in diffs never mention family names) |
| 8 | Live mode (`--llm claude:haiku`) crashed in S1, S2 and S7 before any LLM call: `randint(3, 2)` for buildfix in the 2-subtask live domain. S7 also divided by a zero base score. Live figures would have overwritten the offline PNGs. | Guarded both. Figures go to `<name>_live.png` or next to `--out`. | offline results unchanged. Every script's live path was exercised end to end with a free stand-in LLM (`scratchpad/mhsp_review/fake_live.py`); no real LLM was called. |
| 9 | Context cost was a plain mean over units. The release averages only non-zero per-unit context. | `store.context_mean` | none (no zero units in MemoClassify) |
| 10 | *LLM backend outages were graded as harness failures.*<br>• In MemoClassify, the `infra:` error surfaced as `RuntimeError: infra: …` (so it was cached), or disappeared if the candidate caught it.<br>• In AgentWorld, `LLMAgent` treated a failed call as "agent stops", and the partial state was graded. | Both domains now report `Execution(error="infra: …")`, even when the harness swallows the exception, as AgentQA does. `rsi.core.Evaluator` then counts the trial as missing and does not cache it. | none offline (live only) |
| 11 | *Finalisation marked a run "complete" even when test trials were missing.* | Missing trials make finalisation `incomplete`, which leaves evolution open, as in the release. The report carries `status` and `failures`. | none offline |

**Claims corrected**

- M1: REPRODUCED → PARTIAL. The spec's confirming outcome includes "summaries no better than scores-only", and that part fails.
- M2: stays PARTIAL. The 10× claim is now checked explicitly and reported with the seeds where Meta-Harness never matches the other arm.
- S5: REPRODUCED → PARTIAL. Against the single-environment *dual* gate, the extra training environments give no significant held-out gain.
- S7: numbers updated.

**Genericity tests** (`tests/metaharness-solpi/test_metaharness-solpi_genericity.py`):

- *Meta-Harness on a keyword-sentiment problem.* It is a new `FunctionDomain`, driven once by a scripted LLM through `RewriteProposer` and once by a user `Proposer`. The tests check:
  - the sealed test split is executed only by `finalize()`, once per system;
  - no proposer prompt contains a test input;
  - the full view (and only it) lets the proposer fix the per-task failures.
- *SoL-Pi's research protocol on a "verbosity" problem.* The idea pool holds a general saving, a do-less shortcut and a trick that breaks only on the held-out family. The tests check:
  - the dual gate rejects the shortcut;
  - the firewall rejects the trick;
  - the general idea survives;
  - the held-out split is run only on the base and the frozen candidates, and the final split is never touched.
- *The same SoL-Pi protocol with a scripted LLM implementer.* It edits a generic artifact through `RewriteEditor`.

Neither method needed changes for either new domain beyond fix 6.

**Remaining limitations noted by the review (not fixed)**

- The offline `DeterministicReducer` takes the first N failure lines; see the S7 caveat.
- `AgentProposer.files_read` is the whole view, not the files the agent opened. *(Fixed in §10: taken from the agent's transcript.)*
- The history view hides earlier proposer transcripts; only `meta.json` is shown.
- Code mechanisms run in-process. The meter check catches tampering with the efficiency grader, but it is not a sandbox.

## 9. From-scratch validation with the audit trace (2026-09-25)

Both loops now write the uniform `rsi.trace` audit trace whenever `out_dir` is given (`Config.trace`, default on):
`rsi/metaharness/tracing.py` (`MHTracer`) and `rsi/solpi/tracing.py` (`SolpiTracer`, one trace round per lineage
iteration, plus a base round and a firewall/composition round per driver round). The shadow monitor
(`rsi.trace.ShadowMonitor`, calls metered as `shadow:*`) scores every new frontier `_best` (Meta-Harness, default
on, sealed holdout/ood only - the `test` split is left to `finalize()`) and the base + each composed harness
(SoL-Pi, **default off** because the protocol's contract test counts held-out runs; validation runs switch it on).
`tests/metaharness-solpi/test_metaharness-solpi_validation.py` proves the monitor and the trace are write-only (identical ledgers and
results with monitor on / off / trace off, both methods).

Runs, audits and the paper-alignment review are in `validation/metaharness-solpi/RUNS.md`
(`experiments/metaharness-solpi/validate_metaharness_solpi.py`). Fixes that came out of it:

| # | problem (found live) | fix |
|---|---|---|
| 12 | `RewriteProposer`/`AgentProposer`: haiku copied the history layout and wrote `agents/<name>/src/harness.py`. The candidate got dead files, the harness it actually ran was its base unchanged, and a whole iteration was wasted (both candidates). | `_collect` maps `src/<path>` to `<path>` when the base has no `src/` directory; the output instructions state the layout explicitly. |
| 13 | `RUNTIME_API_DOC` (SoL-Pi LLM implementer) did not say that `Extension`, `ToolResult`, ... are pre-imported, nor how a tool-result event exposes the call id. Haiku imported them from an SDK (ImportError, lineage abandoned) and read `event.call_id` (AttributeError swallowed by fail-open: a no-op mechanism). | The doc states the pre-imports, "standard library only", and `event.call.id` / `m.tool_call_id`. |
| 14 | `RUNTIME_API_DOC` said "bash can read paths under /.solpi/" but not that `rt.store` keys must BE those paths. Haiku stored logs under `"/<id>.txt"` or `"cached_output_<id>"` and told the agent to `cat /.solpi/...`; the LLM reviewer correctly rejected these (the recall would return ENOENT). | The doc states that store keys are the absolute paths the agent reads back. |
| 15 | `LLMReviewer` judged SoL-Pi mechanisms without the runtime API. After fix 14 (live r3), haiku-written mechanisms used correct `/.solpi/` store keys and the documented `context -> None` fail-open, and the reviewer rejected three of four for exactly those reasons (false rejections). | The review prompt appends `RUNTIME_API_DOC` for SoL-Pi harnesses. Validated live in stage B (`solpi_agentworld_live_r4`, see `validation/metaharness-solpi/AUDIT.md`). |
| 16 | *Stage B.* A review rejection consumed the whole lineage iteration: the lineage restarted at 01 (new rollouts) and asked for a fresh proposal, although the spec (B3.1 `repeat: implement ... review ... until rv.pass`) and the blog figure ("Reviewer -> Implementation") route the objection back to implementation. With `max_iters = 2` this ended both live lineages. Also `implement()` dropped the usage of every Ralph repair from the lineage's usage, and it asked an exhausted proposer to repair nothing three times. | `Lineage` / `Config.review_max` (default 2): a rejection calls `proposer.fix(idea, prop, "<reviewer objections>")`, runs the Ralph loop on the repair and reviews again; only after `review_max` repairs does the rejection end the iteration (`review_max = 0` = old behaviour). Traced as `<idea>.<it>r<n>` proposals and critics. `implement()` returns the summed usage and stops on `meta["exhausted"]`. Offline runs: identical ledgers. |
| 17 | *Stage B.* `InterfaceValidator` ran the smoke in a forked child, so its model calls never reached the parent's meters (live: $0.021 and $0.029 per Meta-Harness run missing from the loop meter). | The child ships its per-role usage delta back through the pipe and the parent adds it to every meter along the LLM wrapper chain. (Since §10 the child streams each usage as it happens, so a child killed on timeout no longer loses the calls it completed.) |

Known quirks the validation surfaced (see `validation/metaharness-solpi/AUDIT.md`): the frontier broke exact (score, cost) ties by system name, so an equal candidate with an alphabetically smaller name became the new `_best` (seen live), and `per_unit_best` broke ties the opposite way. *Fixed in §10*: both keep registration order, like the release's list order. The release truncates context cost to `int` (not reproduced; cosmetic).

---

## 11. SoL-Pi claims-audit fixes (stage C, 2026-09-25)

`docs/methods/metaharness-solpi/claims-audit-solpi.md` audited SoL-Pi claim by claim against the paper sources, the NVlabs/SoL-Pi release (@ `1559b5c`, TypeScript + vitest vectors) and Pi 0.85.1. Its §3 listed 13 fidelity mismatches in our code, and several PARTIAL / NOT REPRODUCED rows were caused by our code. All are fixed or documented; the claims file's §6 "Fix log" has one row per finding (fix, file:line, regression test, evidence). Summary:

| area | what changed (default behaviour is now the release's / the paper's) |
|---|---|
| OCC | `{"steps":[…]}` plan snapshot; the release's three advice lines; `parsePlanSteps` strictness and the `update_plan` TypeBox schema (a violation is a validation error, not a silent truncation); `W` = `max(getContextUsage().tokens, estimate)` with Pi 0.85 semantics (`AgentRuntime.context_usage()`); `turn_end` skips error / aborted replies |
| EPR | `source_lines = split("\n").length`; UTF-16 lengths for quotes, `maxChars` and `chars`; an archive integrity failure throws (the runtime keeps the original result, like Pi's runner); `LLMReducer`: 90 s deadline, `min(2048, maxTokens)`, the backend's stop reason |
| ObservationPack | excerpts split after `\n` only; UTF-16 token estimate; `originalLines` on placeholder rows; `obs_recall` argument validation (no negative offsets) |
| Action Fusion | release text joins (no trailing newline on empty output; empty parts dropped on failure); separate write description; empty commands run; `~` → home, `file://`, unicode spaces (without U+200B); realpath queue key; a real yield between the hashes; the release's ENOENT text; AgentWorld's file tools resolve paths like Pi's built-ins |
| runtime | one `context` projection per request; Pi-style auto-compaction on `context_usage()` with a strict `>` |
| protocol | the predeclared capability floor has two metrics (mean score AND solved rate; `GateSpec(capability=(("score", 0.02),))` is the old floor); lineages sweep by default and freeze the nondominated best-η variant (`sweep=False` = first pass); 12 ideas over all six families incl. M, more than `n_lineages` = 10, so the oracle filters; the turn cap's oracle statistic is `late_turn_tokens` |
| docs | AgentWorld docstrings name both held-out families (configfix, datalookup) and five families |

Regression tests: `tests/metaharness-solpi/test_solpi_fixes.py` (26 cases; 25 fail on the pre-fix code, the 26th is a supporting protocol test). Two existing tests were adapted to the new interfaces without weakening them: the OCC review test feeds `context_usage()` instead of the last request's size, and the validation-trace test checks every predeclared capability metric instead of `score` alone; the gate unit test's synthetic metrics carry `solved`.

Re-runs (offline, full settings): S1–S3 identical (the fixes do not change MockAgent trajectories); S2 adds the blog's exact V2 point and a bill decomposition showing that the 10% bill gate is out of reach in this world for a domain reason (replayed outputs are ≤ 5.9% of the bill as cache reads; every swap re-writes the prefix at 12.5×); S4 verdict unchanged (comparisons moved ≤ 2.5 points); S5 PARTIAL (no protocol with a floor admits a do-less shortcut any more); S6 NOT REPRODUCED under its criterion (do-less 0.0, composed loss −3.8 points from a trick); S7 PARTIAL (−54.0% tokens / −15.8% cost, A default). The offline validation run was re-run from scratch with `experiments/metaharness-solpi/sp_validate.py` (same survivors D1, P8, C23; P20 now rejected by the floor; audit 10/10 check types pass), and `solpi_agentworld_live_r5` repeats r4's live configuration under the new defaults (see `validation/metaharness-solpi/AUDIT.md` §6c).
