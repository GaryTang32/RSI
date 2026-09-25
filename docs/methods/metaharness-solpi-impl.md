# Meta-Harness and SoL-Pi — implementation notes (`rsi.metaharness`, `rsi.solpi`)

This document covers two methods and the two domains built for them:

* `rsi/metaharness/`: **Meta-Harness** (Lee et al., 2026). A proposer reads every earlier candidate's code, scores and raw traces, then writes k whole-program harnesses per iteration. There is no keep gate. The output is a Pareto frontier, and the test split is evaluated once.
* `rsi/solpi/`: **SoL-Pi** (Liu, Ye et al., 2026). It has two layers:
  - a Pi-like agent runtime with the four released efficiency mechanisms;
  - the auto-research protocol: idea pool, disposable lineages, a predeclared dual gate across environment families, a one-way held-out firewall, and composition of survivors.
* `rsi/domains/memoclassify/`: online text classification in which the harness is a memory system around a frozen, deterministic model. This is Meta-Harness's CPU analogue.
* `rsi/domains/agentworld/`: multi-step agent environments with large tool outputs. There are three training families and two held-out families, a context-reading mock agent with two "backends", and an adapter for real LLM agents. This is SoL-Pi's MiniAgentWorld.

The spec is `docs/methods/meta-harness-and-sol-pi.md`. Section numbers below (A3, B4.4, …) refer to it.

**Headline (offline, simulated worlds; details in §4).**

Meta-Harness:
- History ablation (M1, PARTIAL): full history beats scores-only and scores+summaries. The median proposed candidate gains +3.4 and +2.4 points, with CIs that exclude 0. The paper's second finding does not reproduce: here summaries help slightly over scores-only (+1.0 median, CI [0.4, 1.6]).
- Equal budget (M2, PARTIAL): Meta-Harness has the best final search score of the four arms. It beats Best-of-N (+6.2 points; Best-of-N now samples only from the run's seed, see §8) and GEPA-style reflection (+1.7) significantly. Its lead over the OPRO-style window (+2.6) is not significant. The "10× fewer evaluations" claim does not reproduce.
- Transfer (M4): the selected harness gains +10.2 points on unseen datasets and +13–16 points on an unseen model.
- Leakage (M5): without a guard, leaky candidates reach the frontier and win selection in 100% of runs. The pilot-style screen rejects all of them.

SoL-Pi:
- All four mechanisms are faithful ports: the economics test vectors pass exactly, and EPR receipts never contain a non-verbatim quote under injected hallucination. Since the review, EPR reduces the exact untruncated log and OCC prices `W = max(reported, estimated)`, as the release does (§8).
- The full stack cuts tokens 54–68% and cost 16–61% at unchanged success (S7, PARTIAL). On backend A, EPR alone is cheaper than the full stack.
- Surviving across environments (S5, PARTIAL):
  - A single-environment efficiency objective admits tricks and do-less shortcuts and loses 45% of held-out capability. The multi-family dual gate plus the held-out firewall keeps 100% of held-out capability at a 39% cost-per-score saving.
  - A single-environment *dual gate* with the same firewall does just as well: 100% capability and a 40% saving. Training on more families rejects more tricks at the gate (3.0 → 1.6–1.8), but it gives no significant held-out gain once the capability floor and the firewall are in place.
  - What does the work here is the floor plus the firewall, not the environment diversity.
- Capability floor (S6, NOT REPRODUCED against the predeclared criterion): the dual gate blocks one of the two do-less shortcuts, but a lenient turn cap still passes it in every seed. Composed with the other survivors, it costs 7 points of training success. An inferred composition re-check (`Config.validate_composition`) closes the gap.
- Effect sizes are smaller than the papers', and several sub-claims reproduce only partially. They are marked below.

---

## 1. Module map

### `rsi/metaharness/` (≈1,500 lines)

| module | contents |
|---|---|
| `config.py` | `Config`: `iterations` (N=20), `k` (2), `history_mode`, `window`, `objectives` (`("score","context_cost")`), `cost_metric`, `search_split`, `test_splits`, `trials`, `eval_budget`, `leakage_screen` (False = faithful), `validate*`, `proposer_timeout_s` (2400), `finalize`, `summaries`. `HISTORY_MODES`. |
| `store.py` | `ExperienceStore`: the filesystem D (spec A5).<br>• Per-candidate `candidates/<name>/{src/, meta.json, eval/search/{scores.json, per_task/, traces/, summary.md}}`.<br>• Run files: `evolution_summary.jsonl`, `frontier_val.json`, `pending_eval.json`, `reports/`, `sessions/iterNNN/{prompt.md, response.md, meta.json}`.<br>• Sealed `results/<split>/<name>/test.json`, `frontier.json` and `finalized.json`, written only by `finalize()`.<br>• `view(mode)` projections: `full`, `scores_summary`, `scores_only`, `window`, `last_only`, `seed_only`. No view exposes `results/`.<br>• `materialize()`, and "history CLI" helpers `cli_frontier / cli_top / cli_diff / cli_show`. |
| `frontier.py` | `pareto_frontier` (a port of `compute_pareto_frontier`), `per_unit_best` (argmax (score, −cost)), `hypervolume`, `dominates`. |
| `proposer.py` | `SKILL_TEXT` / `TASK_PROMPT`, adapted from the release's `SKILL.md` and `render_task_prompt`.<br>• `AgentProposer`: `claude -p` coding agent via `rsi.core.AgentEditor`; the view is read-only under `_context/`, and the agent writes `agents/<name>/…` and `pending_eval.json`.<br>• `RewriteProposer`: any LLM, with the history rendered by `render_view` in priority order under a character budget.<br>• `LLMSummarizer`, `clean_code_block`, `CandidateSpec` / `ProposalBatch`. |
| `mock.py` | `MockProposer`: a deterministic choice rule that may only use what the view exposes. It diagnoses raw traces, falls back to summaries, then to random untried moves or crossover, and has an optional `leak_rate`.<br>Program libraries: `MemoClassifyLibrary`, `AgentQALibrary`, `library_for(domain)`. |
| `validate.py` | `InterfaceValidator`: compile check, then `domain.smoke` in a raw-forked child that is killed after 30 s.<br>`LeakageScreen`: the pilot's forbidden-reference check via `rsi.core.LeakageCritic`, plus a string-lookup-table shape check. |
| `loop.py` | `MetaHarnessLoop`: `run_baselines` → `iterate(t)` (view → propose k → screen → validate → evaluate on the search split → frontier → summary rows) → `run` → `finalize()`. `finalize()` tests baselines ∪ Pareto ∪ per-unit best once, writes `results/` and `frontier.json`, locks further evolution, and is idempotent. |
| `api.py` | `run(...) -> ImprovementResult` and `make_proposer`. |

### `rsi/solpi/` (≈2,300 lines)

| module | contents |
|---|---|
| `meter.py` | `TokenMeter`: prefix-cache simulator. `cache_read` is the longest unchanged message prefix since the last request in the same scope; the rest is `cache_write`; output is billed separately; accounting is per role. `PriceTable` / `PRICES` (ρ = write/read = 12.5), `CostModel` (η = cost / score). |
| `runtime.py` | `AgentRuntime`, the Pi-like extension API:<br>• `register_tool(spec, replaces)` and `builtin()`;<br>• `on(event)` for `session_start / context / before_provider_request / tool_result / turn_end / agent_settled / input / session_compact`;<br>• `compact(instructions)` (native compaction: keeps the last `keep_recent_tokens` = 20,000, cut at an assistant boundary);<br>• `abort()`, `send_hidden()`, `append_entry()`, and a private `store` readable at `/.solpi/…`;<br>• Pi-style auto-compaction at window − 16,384.<br>Also `Message`, `ToolSpec`, `ToolResult`, `Extension`, `builtin_tools`, `default_summarizer`. |
| `fusion.py` | `ActionFusion`: `then_run` on edit/write, path canonicalisation, a per-file FIFO lock, a sha256 hash guard around a yield hook, and the `[then_run:succeeded / failed / skipped]` markers. |
| `obspack.py` | `ObservationPack`: projection-only rewriting. Results over 10 KiB that are non-error and not receipts become content-addressed observations; after `FULL_SENDS` = 2 they are replaced by a placeholder with a whole-line head/tail excerpt. `obs_recall` pages with a ≤ 15,872 B / 398-line UTF-8-safe body. There is a ledger, and it fails open. Excerpt size, sends and head fraction are exposed for the sweep. |
| `reducer.py` | EPR, a `tool_result` handler:<br>• `DIAGNOSTIC_COMMAND`, `FAILURE_SIGNAL` and `LIKELY_SECRET` regexes (verbatim);<br>• the 4,096 B / 600,000-char limits, a content-addressed archive, and `reducer_instructions()` / `reducer_input()` (verbatim);<br>• `validate_receipt` with every rejection reason, `receipt_text`, and the "receipt-not-smaller" check;<br>• a journal of `candidate / provider_response / fallback / applied` entries.<br>Reducers: `DeterministicReducer`, `MockReducer(h)`, `LLMReducer(llm)`. |
| `occ.py` | OCC:<br>• `decide_compaction` and `estimate_remaining_requests`, an exact port with every intermediate;<br>• `OnlineState` and its `record_*` transitions;<br>• plan parsing and limits, and the `update_plan` tool;<br>• handlers for `turn_end` → abort, `agent_settled` → compact with `BOUNDARY_COMPACTION_INSTRUCTIONS` plus a hidden `POST_COMPACTION_PLAN_REMINDER`, and `session_compact` → debt `W·ρ'` repaid at `A − m` per request.<br>`policy="always"` gives the S4 "every boundary" arm. |
| `tricks.py` | Negative controls:<br>• environment-specific tricks `TailTrim`, `HeadTrim`, `PytestQuiet`;<br>• do-less shortcuts `NoVerify`, `TurnCap`;<br>• the dud `PromptSlim`. |
| `registry.py` | Harness config → ordered extensions, in the release's registration order AF, OP, EPR, OCC. Code mechanisms come from `extensions/<name>.py` (`MECHANISM` or `register(rt, cfg)`). Also `with_mechanism`, and `parse_solpi_config`, which validates `sol-pi.json` strictly. |
| `gate.py` | `GateSpec` (frozen and digested; modes `aggregate`, `per_family`, `efficiency_only`, `eta_better`), `DualGate` (with `as_core_gate()`, equivalent to `rsi.core.gates.DualGate`), `Metrics` / `metrics_from_eval`, `nondominated`, and `HoldoutFirewall`. The firewall is one-way: each frozen candidate is evaluated once, the result goes to a write-only sink, and only a bool is returned. |
| `research.py` | `Idea` / `IdeaPool`, `oracle_estimate`, `analyze` / `reduce_findings` (map-reduce), `implement` (the Ralph loop), `SmokeReviewer`, `Lineage`, `FrozenCandidate`, `compose` / `merge3`.<br>`Lineage` runs rollouts → analysis → propose → implement → review → validate → dual gate. It freezes a candidate or routes back, and in sweep mode keeps a nondominated passing variant. |
| `mocks.py` | `AGENTWORLD_IDEAS` (10 ideas over six families), `LibraryProposer` (walks a variant grid on in-lineage gate feedback), `AGENTQA_IDEAS` + `AgentQAEditProposer`, `LLMMechanismProposer` (writes a code extension against `RUNTIME_API_DOC`, or edits any artifact), `LLMReviewer`. |
| `driver.py` | `Config` and `AutoResearchDriver`: base metrics → oracle ranking → lineages → firewall → compose survivors → optional next round. Also `run(...) -> ImprovementResult`.<br>`Config.validate_composition` (off by default; an inferred extension): re-gates the composed stack on the training screen and drops survivors greedily, leave-one-out, until it passes. |

### Domains

| path | contents |
|---|---|
| `rsi/domains/memoclassify/` | `data.py`: synthetic datasets with confusable label clusters, label keywords, hard examples, optional in-input option lists and memorisable ids; 3 search + 4 OOD datasets + 1 leaky dataset.<br>`model.py`: `MemoLM` variants A/B. It reads demonstrations, label lists and label notes; answers only labels present in the prompt; loses demonstrations "in the middle" past its character budget; and emits JSON `final_answer`.<br>`memory.py`: the `MemorySystem` ABC (a port) and the `no_memory` / `fewshot_all` seed programs.<br>`domain.py`: `MemoClassifyDomain`, with an online inner loop (the release's offline mode is an option), per-example traces, `context_chars` (`max(0, prompt_len − len(input))`), `validate_memory`, and `leakage_terms`.<br>`programs.py`: the offline program library (a `CONFIG` genome rendered as a readable `memory.py`, mechanism moves, parameter variants, trace diagnosis, a lossy summariser, and a leaky-lookup builder). |
| `rsi/domains/agentworld/` | `base.py`: the `Env` base, a virtual workspace plus a mini shell (cat, ls, grep, head/tail, sed, wc, pipes; Pi's 50 KB / 2,000-line bash truncation), `Subtask`, the large project guide, and `validity_filter`. The filter checks that the verifier fails initially and passes after the reference fix.<br>`code_envs.py`: `repofix` (pytest; evidence at the tail), `buildfix` (make; the first error in the middle, then cascades) and `configfix` (held-out; validator error at the top of a long dump).<br>`data_envs.py`: `logtriage` (report checked by `make check`; needs the whole log) and `datalookup` (held-out; JSONL records, checker message at the top).<br>`envs.py`: re-exports and `FAMILIES`.<br>`skills.py`: per-family "skills" that parse evidence from the text the agent can see, and `skill_for`.<br>`policy.py`: `MockAgent` profiles A and B (habits: re-run after edits, fused-call uptake, recall and receipt use), `MockAgentLLM`, and the `LLMAgent` adapter.<br>`domain.py`: `AgentWorldDomain` (harness config artifact; splits `evolve` = training families, `holdout` = held-out acceptance, `ood` = held-out final, `test`), `harness(...)`, `oracle_stats`, `mechanism_audit`, and `render_trace`. |

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
| 3 | Keep only changes that survive everywhere | `DualGate` (`aggregate`; `per_family` literal option), `HoldoutFirewall`, held-out families, backend B | S5 | partial:<br>• held-out capability is 1.00 with the multi-family gate and firewall, vs 0.55 for a single-environment efficiency objective;<br>• tricks reaching the final stack drop from 1.2 to 0.2;<br>• a single-environment *dual* gate with the firewall is statistically indistinguishable (1.00 capability; η saving 0.40 vs 0.39). |
| 4 | Start from Pi (no patches; public extension API) | `AgentRuntime` base tools; mechanisms only through `register_tool` / `on` | tests | implemented |
| 5 | More and more varied environments | families + `validity_filter`; `Config.rounds > 1` (next round from the composed base; experimental and **not evaluated**, as in the sources) | `test_every_environment_passes_the_validity_filter` | implemented (5 families, not 535 environments) |
| 6 | What survives transfers | held-out families (configfix, datalookup) + backend B evaluation | S5, S7 | reproduced in simulation: firewall survivors keep 0.99 of held-out success on unseen backend B; the full stack keeps success on both backends |
| 7 | Four mechanisms | `ActionFusion`, `OnlineContextCompact`, `ObservationPack`, `EvidencePreservingReducer` | S1–S4, mechanism tests | implemented (faithful ports) |
| 8 | EdgeBench: −44.7..−49.0% tokens, ~−33% cost at ~94% of Pi's score, two backends | full stack on two backends | S7 | partial:<br>• full stack −53.8% tokens / −15.6% cost at 100% of base success (A, default);<br>• −54..−68% tokens and −16..−61% cost over 2 backends × 2 lengths;<br>• EPR alone is cheaper than the full stack on backend A. |
| 9 | Open code (~3k stars) | documentation pointer (NVlabs/SoL-Pi, MIT) | – | – |
| 10 | Same quality for less cost; one benchmark / two models / one harness | dual gate (non-inferiority + efficiency), two backends, base-agnostic runtime | S5–S7 | implemented |
| 11–12 | Agent-ring / timeline rows | whole stack | – | implemented |
| 13 | Mechanisms useful for any long-running agent | extensions against the generic `AgentRuntime`, independent of the driver | `example_new_problem.py` part 2 | implemented |
| 14 | Fight overfitting by surviving across environments | `DualGate` + `HoldoutFirewall` + training / held-out split | S5; `test_firewall_is_one_way_and_lineages_cannot_read_holdout`; `test_solpi_protocol_on_new_function_domain` | implemented. In S5 the floor and the firewall carry the effect; extra training families add no significant held-out gain. |
| 15 | Context compaction (summarise / drop old material) | OCC + ObservationPack | S2, S4 | implemented |
| 16 | Make cost part of the decision | `TokenMeter`, `CostModel`, efficiency half of `DualGate` | S5, S6 | implemented; S6 shows the floor is needed (efficiency-only admits do-less shortcuts, −51 points success), but per-mechanism gating alone lets a lenient turn cap through |
| (S9) | Held-out never feeds back | firewall owns the only unsealed evaluator; lineages hold no reference; each candidate evaluated once | test above | implemented |
| (S10) | Lineage works end to end with real LLM roles | `LLMMechanismProposer` + `LLMReviewer` | live smoke part 3 + `live_reanalysis.py` | partial: haiku wrote 6 mechanism files; after fence cleanup all compile; none passed the gate (one cut tokens 62% but lost capability; the floor rejected it) |
| (S8) | Breadth escapes local basins | – | not run | not implemented (the spec marks it as an uncontrolled comparison) |

---

## 4. Experiments and results

Every script is `python experiments/metaharness-solpi/<name>.py [--llm sim|claude:haiku] [--seeds N] [--quick] [--workers W]`. It writes `results/metaharness-solpi/<name>.json` containing the config, per-seed raw numbers, a summary (mean + 95% bootstrap CI via `rsi.core.stats.summarize_runs`, paired CIs where arms are paired) and a verdict. PNG figures are written where useful. All offline runs below were executed at full settings.

### 4.1 Meta-Harness (MemoClassify, 10 seeds, 3 search datasets, MemoLM-A)

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

Full mode reads about 5 trace files per iteration and about 10× more history characters. The effect is far smaller than the paper's (+15 points median). The offline proposer is a deterministic mock, so this shows that the views gate the information channel as designed, not how an LLM uses traces. Figure: `m1_history_ablation.png`.

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

The paper's "matches others with 10× fewer evaluations" does not reproduce. Meta-Harness needs this many evaluations to match another arm's final value (medians over the seeds where it matches):

| arm | evaluations | seeds where Meta-Harness never matches within 20 |
|---|---|---|
| Best-of-N | 4 | 1 of 10 |
| OPRO | 7.5 | 2 of 10 |
| GEPA-style | 10 | 1 of 10 |

**M3 (Pareto).** Verdict: REPRODUCED for the frontier claims; the Pareto-vs-scalar objective difference is not significant.
- The Pareto frontier holds 6.0 non-dominated harnesses on average.
- In 100% of runs a frontier harness beats `fewshot_all` in *test* accuracy with less context. The selected (highest-accuracy) harness uses 6.2× less context.
- Example seed 0: `fewshot_all` scores 0.458 search / 0.523 test at 17.7k chars; the selected harness scores 0.590 search / 0.634 test at 5.7k chars. This is the shape of Table 2 (+7.7 points with 4× fewer tokens).
- Pareto − scalar hypervolume: +290 (not significant). Frontier size is 6.0 vs 6.7, and selected test accuracy 0.575 vs 0.580.

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

**Live smoke** (`live_smoke.py`, claude-haiku-4.5, total $0.48; `results/metaharness-solpi/live_smoke.json`).
- **Meta-Harness:** 2 iterations × k = 2 with a haiku RewriteProposer (about 23k input / 7.7k output tokens per call). Two of the four candidates evaluated at the `fewshot_all` level (0.474); two were invalid because they arrived wrapped in Markdown fences with trailing prose, which `rsi.core.parse_file_blocks` does not unwrap. `clean_code_block` now handles this. `live_reanalysis.py` re-parsed the cached replies at no cost: 4/4 validate, but none beats the baseline in 2 iterations.
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
- Every sweep point stays inside the −2% quality gate, so the gate does not discriminate as in the blog's sweep. None reaches the 10% bill-saving gate. Token savings are 12–15% (A) and 9–11% (B), but bill savings are only −1..+3%: each placeholder swap-in breaks the prompt prefix and forces a cache rewrite at 12.5× the read price, which short sessions do not repay.
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
  - backend A: +1.3%, +0.0%, −2.3%, +2.3% for 4–8, 8–12, 12–16 and 16–20 subtasks;
  - backend B: −5.3%, −15.6%, −12.1%, −4.2%.
  - This is the rerun with the release's `W = max(provider-reported, estimated)` (§8). Before, OCC used the estimate only; the numbers moved by at most 0.4 points.
- On short backend-A sessions the plan-tool overhead is not repaid, so "never" and "late" (which do not compact there) tie for best.
- Compacting at every boundary costs +28% vs late on average: +33..+49% on A and +8..+26% on B.
- Never compacting overflows the 200k window in up to 40% of the longest runs, and those runs fail (success 0.60 on A 16–20).
- Figure: `s4_occ.png`.

**S5 survive across environments** (5 seeds; 10 ideas: 4 general, 3 environment-specific tricks, 2 do-less shortcuts, 1 dud). Verdict: PARTIAL. This was REPRODUCED before the review; the review added a direct test of environment diversity (see the end of this subsection).

Four training protocols were compared, each followed by the held-out firewall (configfix, datalookup) and composition. Final-stack capability ratio and η saving were measured on the held-out families:

| protocol | admitted by training gate (general / trick / do-less) | tricks after firewall | held-out A: capability / η saving (firewall) | same, no firewall | held-out B capability (firewall) |
|---|---|---|---|---|---|
| single environment, efficiency only (η) | 3.4 / 3.0 / 2.0 | 1.2 | 0.555 / +0.27 | 0.542 / +0.19 | 0.482 |
| single environment, dual gate | 4.0 / 3.0 / 0.0 | 0.6 | 1.000 / +0.40 | 0.990 / +0.28 | 0.956 |
| all training families, aggregate dual gate | 4.0 / 1.8 / 1.0 | 0.2 | 1.000 / +0.39 | 0.997 / +0.27 | 0.988 |
| all training families, per-family dual gate | 3.6 / 1.6 / 0.0 | 0.2 | 1.000 / +0.39 | 0.992 / +0.30 | 0.988 |

- More environments at training time reject more tricks: 3.0 → 1.6–1.8 admitted.
- The firewall removes most of the rest: 0.2 reach the final stack.
- The firewall also raises η saving by 0.09–0.12, because it drops mechanisms that do not pay on the unseen families. OCC (C6) is rejected by the firewall in every run: held-out sessions are short, and the plan overhead is not repaid there.
- The composed stack of survivors keeps 0.99 of held-out success on the unseen backend B.
- Caveat: which tricks break where is a property of the simulated families (evidence position in outputs).

**Is it the environments?** Hold the capability floor fixed and compare the multi-family protocols (c, d) with the single-family dual gate (b), pairing by seed. The held-out outcomes are statistically the same:

| multi-family − single-family dual gate | backend A capability | backend A η saving | backend B capability |
|---|---|---|---|
| aggregate | +0.000 | −0.004 [−0.010, 0.000] | +0.031 [0.000, 0.069] |
| per family | +0.000 | −0.012 [−0.025, −0.002] | +0.031 [0.000, 0.069] |

More training families do reject more tricks at the gate: 3.0 are admitted with one family and 1.6–1.8 with three. But the aggregate multi-family gate admits the lenient turn cap (P20) in every seed, while the single-family gate never does. In this simulation the separation between tricks and general mechanisms comes from the capability floor and the held-out firewall. The claim that the extra environments make the survivors transfer better is not shown. Hence the verdict is PARTIAL.

**S6 capability floor** (5 seeds, no firewall, to isolate the training gate). Verdict: NOT REPRODUCED under the predeclared criterion, which required 0 do-less shortcuts admitted by the dual gate and at most 2 points of composed training-success loss.

| arm | do-less admitted | general admitted | Δ success train | Δ success held-out | token saving (train) | cost saving (train) |
|---|---|---|---|---|---|---|
| efficiency only | 2.0 | 4.0 | −0.509 [−0.543, −0.467] | −0.461 | 92.6% | 72.9% |
| dual gate (2% capability tolerance) | 1.0 | 4.0 | −0.073 [−0.115, −0.030] | −0.002 | 79.9% | 48.8% |
| dual gate + composition re-check (inferred) | 0.8 | 3.2 | −0.002 [−0.005, 0.000] | −0.007 | 72.5% | 48.0% |

- The floor does what it is for against the blunt shortcut: skipping re-verification (P14) is rejected in every seed.
- The mildest turn cap (P20, 24 turns) stays within the 2% tolerance on the training screen, so it is admitted in every seed.
- Once composed with the other survivors (ObservationPack placeholders, and tail-trim where it slipped through), individually tolerable losses add up to 4–13 points of training success in 4 of 5 seeds. This is the failure mode the blog warns about for composed stacks.
- The composition re-check (`Config.validate_composition=True`) re-gates the composed stack and greedily drops survivors until it passes. That restores the floor (−0.2 points) but not by removing the shortcut:
  - it dropped ObservationPack (C23) 3×, tail-trim (T3) 2×, the turn cap once and OCC once;
  - it kept the turn cap in 4 of 5 seeds.
- The composition re-check is our addition. The sources gate mechanisms one at a time.

**S7 composition** (full stack vs each mechanism alone; 5 seeds; 2 backends × {default, long} task lengths). Verdict: PARTIAL. Rerun after the EPR and OCC fidelity fixes (§8).

| cell | +AF | +OP | +EPR | +OCC | full stack | full-stack success ratio |
|---|---|---|---|---|---|---|
| A default | −24.9% / −14.9% | −12.5% / +0.6% | −47.5% / **−32.8%** | −17.5% / −2.6% | −53.8% / −15.6% | 1.000 |
| A long | −28.3% / −19.9% | −4.1% / +1.2% | −56.2% / **−45.9%** | −23.8% / −2.3% | −58.7% / −30.6% | 1.000 |
| B default | −9.1% / −5.9% | −10.6% / −0.3% | −49.1% / −43.4% | −7.4% / +1.9% | −61.8% / **−44.4%** | 0.998 |
| B long | −16.6% / −12.6% | −5.9% / −2.2% | −61.6% / −60.6% | −24.2% / −12.2% | −68.4% / **−61.2%** | 1.006 |

Each cell is the token change / cost change vs the base harness; bold marks the cheapest arm.

- Every mechanism saves tokens alone, and success is unchanged. The full stack saves the most tokens in every cell.
- On cost the full stack is the cheapest arm only on backend B. On backend A, EPR alone is cheaper, because OP swap-ins and OCC compactions rewrite the cache prefix at ρ = 12.5.
- Headline vs the paper: −53.8% tokens and −15.6% cost at 100% of base success (A, default), against EdgeBench's −44.7..−49.0% tokens and about −33% cost at about 94% of Pi's score. Before the review the A-default figures were −56.5% / −19.4%: EPR had reduced the truncated tail preview of large pytest logs, which understated their size.
- Mechanism uptake differs by backend, as the blog reports:
  - Action Fusion fires 3.29 times per triggered task on A vs 1.91 on B;
  - OCC triggers in 5% of A-default tasks, 37% of A-long, 0% of B-default and 18% of B-long.
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
5. *AgentProposer.* It uses `rsi.core.AgentEditor` (Read/Edit/Write/Glob/Grep). There is no Bash, no subagents and no `stream-json` transcript, so its `files_read` is the whole view, not the files actually opened.
6. Defaults: k = 2 (paper §4.1; the skill says 3). Experiments use N = 8 (M1, M3–M5) or a budget of 20 evaluations (M2) instead of N = 20.
7. The `evolution_summary` row logs both `delta` (post-iteration best, the release quirk) and `delta_pre`.
8. The history "CLI" is a set of functions. There is no `DomainOnboarding`. The pilot's in-agent `evaluate_harness` tool (spec A3.4) is not implemented; `eval_budget` covers the equal-budget use. `eval_budget` counts only *evaluated* candidates. In the pilot, a candidate rejected by the leakage or interface check also consumes budget.
9. *Leakage screen.* It matches whole tokens (via `rsi.core.LeakageCritic`, min term length 4) over added lines, plus a string-table shape check. The pilot uses case-insensitive substrings over the whole source.
10. *Context cost.* A system's context cost is the mean of its *non-zero* per-unit context chars, as `print_frontier` computes it. In MemoClassify the last prompt is recorded by the domain's model wrapper, not by the candidate's `call_llm` bookkeeping (§8). The release measures inside the candidate, where a candidate could bypass or override the measurement.

**SoL-Pi**
1. *Runtime and agent.* A Python re-implementation of the subset of Pi's extension API that the four mechanisms use. The agent is a deterministic context-reading policy with two habit profiles, not GPT-5.6 Sol / Opus 5. `LLMAgent` exists but was not run live because of cost.
2. *Tokens and prices.* The token estimate is chars/4. The prefix cache works at message granularity. Prices are invented but keep ρ = 12.5.
3. *OCC.* Abort → settle → compact → hidden reminder happens synchronously inside the loop. The native summariser is deterministic and keeps the task and the agent's NOTE/PLAN/DONE lines. Session-tree events are not modelled. `input` / correction handling is implemented but never triggered by the mock agents. `W = max(provider-reported, estimated)`; the size of the runtime's last provider request (tool schemas included) stands in for Pi's `getContextUsage().tokens`.
4. *EPR.* An archive I/O error falls back with reason `model-call-exception` (the release would throw). There is no reasoning-effort setting. `LLMReducer` strips a stray Markdown fence before byte-exact validation. Truncated bash results are reduced from the exact full log: `details.fullOutputPath`, or the inline `Full output: <path>` note. Only a `pi-bash-*.log` directly in `/tmp` is trusted, and the runtime store stands in for the file system. The offline `DeterministicReducer` is our own stand-in; it quotes the first failure-looking lines verbatim.
5. *Research protocol.* The prompts were not released, so ours are our own. Other details:
   - an efficiency metric "improves" only by more than `GateSpec.min_gain` = 2% relative, and capability tolerances are relative (2%). The sources give no numeric thresholds; the ObservationPack sweep used a −2% quality gate and a 10% bill-saving gate;
   - `SmokeReviewer`'s denylist is generic split words plus the task families that occur only in the domain's sealed splits, derived from the domain;
   - the oracle statistics come from trajectories;
   - the offline implementer maps ideas to registry mechanisms with variant grids, and its Ralph-loop exit check is `domain.smoke`;
   - the reviewer is `SmokeReviewer` (smoke + held-out-reference denylist) or `LLMReviewer`;
   - the held-out pass criterion is the same dual gate on the held-out split [inferred in the spec];
   - `per_family` = capability within tolerance in every family + aggregate efficiency gain + no family regressing >5% [inferred];
   - "nondominated" retention applies among a lineage's variants (`sweep=True`); survivors of different lineages are composed (union of opt-in mechanisms);
   - `Config.validate_composition` (off by default; used only in the S6 third arm) re-gates the composed stack. The sources gate each mechanism alone, and S6 shows why a re-check may be needed.
6. *Environments.* Five simulated families (not 535 real repositories and 40 synthesised environments). The idea pool has 10 ideas (not 152). Idea `kind` labels are ground truth used only in analysis.
7. The multi-round loop (`rounds > 1`) is implemented but not evaluated. S8 (breadth vs depth) was not run.

---

## 6. Limitations (read before citing numbers)

* **Everything offline is a simulation designed for the claim it tests.** MemoLM, the MockProposer, the AgentWorld families and the MockAgent encode assumptions: where evidence sits in outputs, how often edits help, and how agents react to placeholders and receipts. Verdicts show that the *mechanisms and protocols behave as claimed under the stated models*, not that the papers' magnitudes transfer. Effect sizes are generally smaller than reported: M1 +3.4 points vs +15; M2 is significant against only two of three baselines.
* **Offline proposers are not LLMs.** M1/M2's history effects come from a rule-based proposer. The one live run (2 iterations, haiku) found no improvement over the baseline.
* **ObservationPack and OCC economics depend on session length.** Our sessions are 15–60 requests, not hours, so ObservationPack saves tokens but little money, OCC is cost-neutral on backend A and saves 4–16% on backend B, and the full stack is not the cheapest arm on backend A (S7). All of this matches what the economics predict.
* **Noise.** Small screens (3 tasks per family) let tricks slip through training gates by chance, as the quick runs showed. The full runs use 8 tasks per family and a 6-task-per-family firewall.
* **Gating one mechanism at a time is not enough here (S6).** A lenient turn cap passes the per-mechanism floor and costs capability only in composition. We report this as non-reproduction instead of tuning the tolerance or the environments until it passes.
* **Live coverage.** A single live smoke ($0.48) exercised the haiku proposer, reducer and implementer. The coding-agent proposer (`AgentProposer`) and the LLM agent backend were not run live. Every script accepts `--llm claude:haiku` for showcases.

---

## 7. Files

`rsi/metaharness/*`, `rsi/solpi/*`, `rsi/domains/memoclassify/*`, `rsi/domains/agentworld/*`; tests `tests/test_metaharness-solpi_{metaharness,mechanisms,solpi,genericity,review}.py` (53 tests, about 20 s); experiments `experiments/metaharness-solpi/{m1..m5,s1..s7}_*.py`, `live_smoke.py`, `live_reanalysis.py`, `example_new_problem.py`; results `results/metaharness-solpi/*.json|png`.

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

**Genericity tests** (`tests/test_metaharness-solpi_genericity.py`):

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
- `AgentProposer.files_read` is the whole view, not the files the agent opened.
- The history view hides earlier proposer transcripts; only `meta.json` is shown.
- Code mechanisms run in-process. The meter check catches tampering with the efficiency grader, but it is not a sandbox.

## 9. From-scratch validation with the audit trace (2026-09-25)

Both loops now write the uniform `rsi.trace` audit trace whenever `out_dir` is given (`Config.trace`, default on):
`rsi/metaharness/tracing.py` (`MHTracer`) and `rsi/solpi/tracing.py` (`SolpiTracer`, one trace round per lineage
iteration, plus a base round and a firewall/composition round per driver round). The shadow monitor
(`rsi.trace.ShadowMonitor`, calls metered as `shadow:*`) scores every new frontier `_best` (Meta-Harness, default
on, sealed holdout/ood only - the `test` split is left to `finalize()`) and the base + each composed harness
(SoL-Pi, **default off** because the protocol's contract test counts held-out runs; validation runs switch it on).
`tests/test_metaharness-solpi_validation.py` proves the monitor and the trace are write-only (identical ledgers and
results with monitor on / off / trace off, both methods).

Runs, audits and the paper-alignment review are in `validation/metaharness-solpi/RUNS.md`
(`experiments/metaharness-solpi/validate_metaharness_solpi.py`). Fixes that came out of it:

| # | problem (found live) | fix |
|---|---|---|
| 12 | `RewriteProposer`/`AgentProposer`: haiku copied the history layout and wrote `agents/<name>/src/harness.py`. The candidate got dead files, the harness it actually ran was its base unchanged, and a whole iteration was wasted (both candidates). | `_collect` maps `src/<path>` to `<path>` when the base has no `src/` directory; the output instructions state the layout explicitly. |
| 13 | `RUNTIME_API_DOC` (SoL-Pi LLM implementer) did not say that `Extension`, `ToolResult`, ... are pre-imported, nor how a tool-result event exposes the call id. Haiku imported them from an SDK (ImportError, lineage abandoned) and read `event.call_id` (AttributeError swallowed by fail-open: a no-op mechanism). | The doc states the pre-imports, "standard library only", and `event.call.id` / `m.tool_call_id`. |
| 14 | `RUNTIME_API_DOC` said "bash can read paths under /.solpi/" but not that `rt.store` keys must BE those paths. Haiku stored logs under `"/<id>.txt"` or `"cached_output_<id>"` and told the agent to `cat /.solpi/...`; the LLM reviewer correctly rejected these (the recall would return ENOENT). | The doc states that store keys are the absolute paths the agent reads back. |

Known quirks the validation surfaced but did not change: the frontier breaks exact (score, cost) ties by system name (`frontier.py`), so an equal candidate with an alphabetically smaller name becomes the new `_best` (seen live); `InterfaceValidator` runs the smoke in a forked child, so the model calls it makes are cached but invisible to the parent's meters (live: $0.02 of $0.78, recovered from the cache entries).
| 15 | `LLMReviewer` judged SoL-Pi mechanisms without the runtime API. After fix 14 (live r3), haiku-written mechanisms used correct `/.solpi/` store keys and the documented `context -> None` fail-open, and the reviewer rejected three of four for exactly those reasons (false rejections). | The review prompt appends `RUNTIME_API_DOC` for SoL-Pi harnesses. Not re-validated live (stage budget). |
