# EvoMap / GEP: implementation notes (`rsi.evomap`, `rsi.domains.katas`, `rsi.domains.geneworld`)

This page documents how the EvoMap spec (`docs/methods/evomap.md`: the Evolver engine, the GEP protocol, Strategy Genes and the "Behind EvoMap" study) is implemented on the shared `rsi.core` API. It covers the module map, the public API with a copy-pasteable example for a new problem, the capability checklist mapped to code, experiments and results, deviations and limitations.

All numbers come from `results/evomap/*.json`, produced by `experiments/evomap/*.py` at their default (full) offline settings. CIs are 95% bootstrap CIs over seeds (`rsi.core.stats.summarize_runs`), and paired differences use `paired_diff_ci`.

**Naming.** "EvoMap", "GEP" and "Genome Evolution Protocol" are EvoMap trademarks. The package lives at `rsi/evomap` (the path the framework assigns to this method), but its docstrings describe it as a *gene hub* "compatible with GEP schema 1.14.0". No Evolver source was copied. The four JSON Schemas in `rsi/evomap/schemas/` are copied unchanged from `@evomap/gep-sdk@1.14.0` (Apache-2.0; NOTICE included). `asset_id` hashing reproduces the reference `contentHash.js` byte for byte, checked against node in `tests/test_evomap_core.py`.

## 1. Module map

| module | role (spec section) | built on |
|---|---|---|
| `hashing.py` | `canonicalize` (JS number and string rules), `asset_id`, `verify_asset_id`, hub-side capsule id (§4.1) | – |
| `schema.py` | `JsonSchemaValidator`: the Draft-07 subset the GEP schemas use. `strict` = schema as published; lenient = the engine's documented drift fields allowed (§5) | the copied sdk 1.14.0 schemas |
| `assets.py` | `Gene`, `Capsule`, `EvolutionEvent`, `Mutation`, `ValidationReport`, `PersonalityState`: `to_dict` omits `None` (null changes the hash), `stamp`, `verify`, `to_gep` (strict projection); deterministic `Clock` (§5) | – |
| `store.py` | `LocalStore`: `genes.json`, `capsules.jsonl`, append-only `events.jsonl` (parent id chain plus a hash chain `meta.parent_asset_id`), memory graph, `external_candidates.jsonl` quarantine zone (asset_id check, confidence ×0.6, `--validated` promote), failed capsules, distiller log. `audit()` checks ids, chain, forks and links; `export_archive` / `import_archive` write a `.gepx`-like tar.gz with manifest and `checksum.sha256` (§3.8, §5.10) | `MemoryGraph` |
| `signals.py` | `match_pattern` (substring / `/regex/flags` / `a\|b` aliases), `expand_signals` tags, `RegexSignalExtractor` (layer 1), `KeywordSignalScorer` (layer 2), `TaskSignalExtractor` (task-stream adapter), `SignalDeduper` (all §4.6 rules incl. `ban_gene:<id>`, plateau pivots), `PlateauDetector` (§3.1–3.2, §4.6) | – |
| `memory.py` | `MemoryGraph.advice` in `spec` mode (Laplace, 30-day half-life, exact or Jaccard key, ban at n ≥ 2 and value < 0.18) and `current` mode (Jaccard ≥ 0.34 × sim, ×1.15 predictive, 45-day gene prior, inert outcomes, three ban rules, epochs). `OutcomeInferrer`: `faithful` (transcript EvolutionEvent first, else the §4.11 heuristic with predictive block) or `safe` (measured outcome only) (§4.4, §4.11) | – |
| `selector.py` | `GeneScorer` (`spec`: pattern hits; `current`: hits + 0.6·tag + 0.4·cosine + learning-history/epigenetic/anti-pattern adjustment, distilled ×0.8), `drift_intensity`, `ban_from_failed_capsules`, `GeneSelector` (bans, forced gene, preferred ×1.5, epigenetic suppression, drift pick among the top n, capability-gap directed, distilled fallback, capsule chosen by trigger independently of the gene) (§3.3, §4.2–4.5) | `MemoryGraph.Advice` |
| `mutation.py` | `STRATEGIES` presets, `StrategyPolicy` (preset resolution and the adaptive per-cycle policy), `MutationBuilder` (category and risk with the personality safety downgrades), `PersonalityModel` (natural selection, triggered mutations, `force_pivot`) (§3.4, §4.7) | – |
| `validation.py` | `CommandPolicy` (no shell, allowlist, blocked eval flags, script required unless info-only), `SubprocessExecutor` (sandboxed scratch copy, `rsi.core.sandbox`), `InProcessExecutor` (simulation only), `ValidationRunner` (`faithful`: missing script skipped, empty list ok, 2 retries; `safe`: no skips, **empty list fails**), `VacuityDetector` (AST lint plus discriminative before/after run with the new tests on the old code, mutation kill rate) (§4.9, §9.2.2–9.2.3) | `rsi.core.sandbox` |
| `solidify.py` | `CountedFilePolicy`, `blast_radius`, `ConstraintChecker` (hard caps, overruns, forbidden and critical paths, hollow commit, ethics lint, destructive changes), `composite_score` (exact §4.10 weights), `classify_failure_mode`, epigenetic marks, `Solidifier` (event, capsule, failed capsule, rollback, gene learning, success streak, broadcast/publish eligibility) (§3.6, §4.8, §4.10, §4.12, §4.13) | `ValidationRunner`, `VacuityDetector` |
| `distill.py` | `Distiller` (every-5th trigger, `should_distill`, heuristic `auto_distill`, LLM fallback with the §6.4 prompt, failure distillation, `validate_synth`). `LeakageAuditor` (skill2gep-style private-vocabulary redaction plus a fast `rsi.core.LeakageCritic` denylist) (§3.7, §4.14) | `rsi.core.LeakageCritic` |
| `prompts.py` | `render_gene` (paper template, ablation parts), `render_skill` (long SKILL.md form), gene-writer / reuse / hub-matched / distiller prompts, `parse_gene` (§6) | `rsi.core.extract_json` |
| `inject.py` | the shared injection interface (`FileInjector`, `AppendInjector`, `SkillInjector`, `TextInjector`), workspace adapters (`pre_workspace` / `post_workspace` hooks), `GeneRoutedDomain` (evaluates `harness + genes/library.json` on any domain) | `rsi.core.Domain` |
| `hub.py` | `Hub` protocol, `Bundle` (Gene + Capsule + Event + self-reported report + proof material), `AssetRecord`, `CreditLedger` (Gini, top-q share), `GDIRanker` (0.35 I + 0.30 U + 0.20 S + 0.15 F), `client_reuse_score` (§4.16), `NaiveEvoMapHub` (§3.9, §4.13, §4.16–4.18) | `rsi.core.Ledger`, `stats.gini` |
| `safehub.py` | `TaskBank` (hidden sealed split, frozen reference executor), `UpliftLCB` (a `rsi.core.Gate`), `SafeHub` (static gate, verified execution, adoption ranking with a Beta LCB and exploration slots, stake and adoption-only credits, report rules with spot-checks and slashing, dedup lineage, deprecation, revocation), `injection_lint` (§9.2) | `rsi.core.Evaluator`, `NoiseFloor`, `noise_from_trials`, `paired_diff_ci`, `beta_lcb` |
| `quarantine.py` | `QuarantineGate`: consumer-side lint, then an A/B on the consumer's own held-out tasks under `rsi.core.RRSIGate` (floor S* − δ, cost rule) plus dS > 0, or a paired-LCB rule (§9.2.6) | `rsi.core.RRSIGate`, `Evaluator` |
| `metrics.py` | `ReuseMetrics`: reuse and never-reused rates, time to first reuse, adoption success, consumer uplift, credit Gini and top-10% share, vacuous share among promoted, rank validity, surfacing validity, served true effect, duplicate rate (§9.2.7) | `stats.spearman` |
| `agent.py` | `AgentNode.cycle`: signals, look locally, hub (optional), solve / propose, solidify, distil, publish. Ledger node per cycle; `rsi-taskcheck` internal validation (§3.1, §9.1) | all of the above |
| `population.py` | `AgentSpec`, behaviours (honest / inflator / freerider; farmer and poisoner via a domain `Forge`), `inflate`, `PopulationSimulator` (§9.3) | `AgentNode`, hubs |
| `config.py`, `loop.py` | `Config` (all knobs with mode defaults), `run(...) -> ImprovementResult`, `evaluate_library`, `merged_usage` | `rsi.core.run` |
| `domains/geneworld/world.py` | `GeneWorld` (12 signal classes, latent strategy keys with ground-truth effects, CRN success draws, validation workspaces, vacuous kinds), `GeneWorldDomain`, `GeneWorldModel` (ability, optional over-reporting), `GeneWorldProposer` | `rsi.core.Domain` |
| `domains/geneworld/forge.py` | `GeneWorldForge`: farmer bundles (vacuous validation, broad signals, inflated claims) and poisoner bundles (harmful keys or injection, *discriminative* fake proof) | `rsi.evomap` assets |
| `domains/katas/{katas,domain,genes,forge}.py` | 25 katas in 5 classes with public smoke tests and **hidden** asserts graded in a sandboxed subprocess; `KatasDomain` with workspace hooks; `KataSimSolver` / `KataMockProposer` (offline stand-ins); hand-written class genes plus complementary / conflicting genes and raw failure logs; `KatasForge` | `rsi.core.Domain`, `sandbox` |

The largest file, `validation.py`, has 487 lines.

## 2. Public API

```python
from rsi.evomap import Config, run, evaluate_library
res = run(domain, harness, llm_task=model, llm_propose=gene_writer, config=Config(cycles=40, mode="safe"),
          out_dir="runs/genes", hub=None)              # -> rsi.core.ImprovementResult
res.best["genes/library.json"]                          # the evolved gene library (the improved artifact)
evaluate_library(domain, model, res, splits=("evolve", "holdout"))   # no genes vs routed library
```

* **What improves** is the agent's gene library, not its harness. `res.baseline` is the frozen harness and `res.best` is the harness plus `genes/library.json`. `evaluate_library` / `GeneRoutedDomain(domain)` pick one gene per task by pattern score, inject it, and delegate execution and grading to the unchanged domain.
* `res.trajectory` has one row per cycle: task, signals, gene, source (`local`/`hub`/`generated`/`auto`/`none`), graded task score, solidified, composite, tokens, hub hit, quarantine verdict, publish decision, library size and rolling solve rate.
* `res.ledger` is an `rsi.core.Ledger` tree. Evolution events form the trunk (node id = event id, parent = previous event). Skipped cycles and accepted genes (`kind="gene"`) hang off it. Hubs keep their own ledger: `publish` roots with `verify` / `adopt` / `revoke` children.
* `res.usage` is metered per role: `task` (the frozen model), `proposer` (gene writer), `distiller` (LLM distillation), plus `*:cached`.
* `res.meta` holds the resolved config, genes, audit report, proposer calls, quarantine counts and the list of safe-mode fixes applied. `out_dir` receives `summary.json`, `trajectory.json`, `ledger.jsonl`, `library.json`, `store/` and `store.gepx.tgz`.
* Calling `run(...)` again with the same `out_dir` **continues** the persisted store: library, event chain and memory graph.

`mode="faithful"` reproduces Evolver v1.94.0 for replication arms. `mode="safe"` applies the fixes below. Every row can be overridden individually in `Config`:

| knob | faithful | safe |
|---|---|---|
| validation runner | missing scripts skipped, empty list ok, 2 retries, `python` only | no skips, **empty list fails**, `python`/`pytest`/`rsi-taskcheck` |
| keep rule | constraints ∧ validation ∧ no protocol violation | + non-vacuous (lint + discriminative) ∧ own graded task solved |
| memory-graph outcome | written one cycle late; transcript EvolutionEvent first, else §4.11 heuristic; signals include the previous trace | measured solidify outcome, written immediately |
| hub use | always consulted; best client reuse score ≥ 0.72 / 0.55 injected directly (`reference`) | only when nothing local fits; staged in quarantine, A/B on own held-out tasks, adoption report with proof |
| failed-capsule ban | ≥ 2 overlapping failed capsules | ≥ 2 and more failures than successes |
| no gene selected | `gene_auto_<hash>` with a validation that the allowlist blocks | solved → `skipped` (nothing to solidify); unsolved → failed event |

Domain hooks (all optional, duck-typed): `pre_workspace(task)` / `workspace(task, execution)` (validation workspaces W0/W1), `public_feedback(task, execution)` (what the gene writer may see), `public_text(task)`, `validation_executor()`, `counted_policy`, `leakage_terms(split)`.

Population level:

```python
from rsi.evomap import SafeHub, TaskBank, NaiveEvoMapHub, PopulationSimulator, AgentSpec
hub = SafeHub(TaskBank(domain, harness, reference_model, split="test", n=24, k=4))   # or NaiveEvoMapHub()
sim = PopulationSimulator(domain, harness, hub, [AgentSpec("a0", "honest", ability=0.3), ...], config=Config(),
                          model_factory=..., proposer_factory=..., forge=..., truth=...)
summary = sim.run(epochs=30)          # solve rates, credits by kind, hub ReuseMetrics, poisoned cycles, ...
```

### Applying it to a new problem (copy-pasteable; from `tests/test_evomap_example.py`)

```python
import json
from rsi.core import Artifact, FunctionDomain, MockLLM, Task, TaskSuite
from rsi.evomap import Config, SafeHub, TaskBank, evaluate_library, run

# 1. A new problem: format invoice amounts. Tasks carry public signals; targets stay with the grader.
tasks = [Task(f"t{i}", {"amount": i * 1.5}, f"EUR {i * 1.5:.2f}", "money", {"signals": ["money", "format"]})
         for i in range(1, 61)]
suite = TaskSuite(tasks, {"evolve": [f"t{i}" for i in range(1, 21)], "val": [f"t{i}" for i in range(21, 31)],
                          "holdout": [f"t{i}" for i in range(31, 46)], "test": [f"t{i}" for i in range(46, 61)]})

def execute(artifact, task, seed, llm):          # the frozen harness: prompt = every artifact file + the task
    system = "\n".join(artifact[p] for p in sorted(artifact))
    return llm.complete(json.dumps(task.input), system=system, seed=seed, role="task").text

dom = FunctionDomain(suite, execute, lambda task, out: float(out.strip() == task.target), name="invoices")

# 2. A frozen model (mock): prints two decimals only when its guidance says so.
#    Live: model = CachedLLM(ClaudeCLI("haiku"), ".rsi_cache/invoices")
model = MockLLM(lambda p, s, seed, i: f"EUR {json.loads(p)['amount']:.2f}" if "two decimals" in (s or "")
                else f"EUR {json.loads(p)['amount']}", name="invoice-model")

# 3. A gene writer (the same Claude model live). rsi-taskcheck = a discriminative paired A/B on own tasks.
gene = {"id": "gene_two_decimals", "category": "repair", "signals_match": ["money"],
        "summary": "Money amounts must be printed with two decimals.",
        "strategy": ["Read the amount.", "Format it with exactly two decimals.", "Prefix the currency code."],
        "avoid": ["Printing the raw float."], "validation": ["rsi-taskcheck --n 4"]}
proposer = MockLLM(lambda p, s, seed, i: "```json\n" + json.dumps(gene) + "\n```", name="gene-writer")

harness = Artifact({"prompt.md": "Format the invoice amount.\n"})
res = run(dom, harness, llm_task=model, llm_propose=proposer, config=Config(cycles=8, mode="safe"),
          out_dir="runs/invoices")
rep = evaluate_library(dom, model, res, splits=("holdout",), workers=1)
print(rep["splits"]["holdout"]["no_genes"]["S"], "->", rep["splits"]["holdout"]["gene_library"]["S"])  # 0.0 -> 1.0

# 4. Optional: share through a verified hub (its hidden bank = the sealed 'test' split).
hub = SafeHub(TaskBank(dom, harness, model, split="test", n=12, n_off=0, k=2))
res2 = run(dom, harness, llm_task=model, llm_propose=proposer, config=Config(cycles=8, mode="safe"), hub=hub,
           name="alice")
print(hub.published()[0].status, hub.published()[0].hub_report["U_LCB"])     # verified, U_LCB >= delta
```

Every experiment script also takes `--llm claude:haiku`, which switches to the katas domain with a cached headless `claude -p` as solver and gene writer, at small sizes.

## 3. Domains

* **`rsi.domains.geneworld`** (Tier 1). Twelve signal classes, each with four keywords (a task draws three of them plus two noise words) and a Gaussian difficulty. Each class has latent strategy keys with true logit effects (best 1.8, good 0.9, useless ±0.05, harmful −1.2; shuffled per class), a real and an irrelevant AVOID key, two generic keys, `sk_poison` and injection strings. Solving uses P = σ(a_m − d_t + dil·Σe − λ_comp·max(0, \|G\|−1) − λ_len·tok/1000 − inj). λ_comp, λ_len and the skill dilution are **knobs that encode hypotheses**. Draws use common random numbers (u depends only on task and seed), so paired A/Bs are exact. Validation workspace per class: `mod.py` (buggy → fixed after a solve, "wrong" after a failure) plus the real `check.py`. Splits: evolve 8, val 6, holdout 6 and test 24 per class (the test split is the hub's hidden bank).
* **`rsi.domains.katas`** (Tier 2). Twenty-five functions in five classes: boundaries, unicode, dates, rounding, retry. Each has two public asserts (weak: the pitfall variant passes them too) and hidden asserts that exercise the pitfall, graded in a sandboxed subprocess with the result cached by code hash. The harness appends every `genes/` or `skills/` file to the system prompt. Offline stand-ins: `KataSimSolver` (picks the correct or the pitfall implementation with a probability driven by the guidance: hint phrases, AVOID items, contradictory advice, dilution inside skills, composition and length penalties; all knobs) and `KataMockProposer`.
* **`rsi.domains.agentqa`** (shared, unchanged). Genes are appended to `prompts/system.md` (`AppendInjector`) and validated with `rsi-taskcheck` on the agent's own evolve questions.

## 4. Experiments and results

All scripts are `experiments/evomap/<name>.py [--llm sim|claude:haiku] [--seeds N] [--quick]`. Each writes `results/evomap/<name>.json` with the config, per-seed raw rows, mean and 95% bootstrap CI, paired differences and a verdict. `--llm claude:haiku` switches to the katas domain with a cached `claude -p`, at small sizes. `--llm mock-live` exercises those same live code paths offline (every script was run that way as a plumbing check). CPU wall times at 2 workers: X9 7 s, X1 2 s, X13 86 s, X0 41 s, X15 149 s, X4 391 s, X8 55 s, X6 80 s, X11 206 s, X7 408 s, X10 460 s.

**Simulation caveat.** GeneWorld results show whether a *mechanism* produces an effect under stated assumptions: population mix, effect sizes, farm rates, the distiller's "prefer --version" advice. They are not measurements of the real network. Offline X1–X3 results restate the kata simulator's knobs.

| # | claim (spec §9.3) | result (mean [95% CI]) | verdict |
|---|---|---|---|
| X0 | the gene loop is domain-agnostic | holdout pass rate without genes → with the evolved library (10 seeds): GeneWorld safe 0.41 → 0.58 (paired +0.17 [+0.14, +0.21]), faithful 0.41 → 0.64; katas (mock coder, real hidden tests) safe 0.39 → 0.81 (+0.42 [+0.35, +0.50]), faithful 0.39 → 0.71; agentqa (SimModel) safe 0.40 → 0.43 (+0.03 [+0.005, +0.06]), faithful 0.40 → 0.46 (+0.055 [−0.055, +0.15]). Audit ok in every run | **reproduced** on 3 domains (agentqa faithful not significant) |
| X1 | compact genes beat long skill documents | *offline mock:* none 0.50, Gene (143 tok) 0.91, Skill (1,252 tok) 0.82, Skill fragment at the gene's budget 0.60; progressive construction keywords 0.50 → +summary 0.77 → +strategy 0.90 | direction as in the paper, but **only restates the knobs**; live run pending (`--llm claude:haiku`) |
| X2 | failure warnings work best as compact AVOID items | *offline mock:* strategy only 0.90, AVOID only 0.58, strategy + AVOID 0.91, strategy + appended raw log 0.90 | not reproduced offline (AVOID-only < appended log); knob-driven |
| X3 | one targeted gene beats compositions | *offline mock:* single 0.91, two complementary 0.90, two conflicting 0.56 | single ≥ both; the paper's "complementary worse than conflicting" is **not** reproduced by our additive model |
| X4 | local loop reuses what worked, bans what fails, explores when stuck | (a) ban latency with each rule isolated, in-loop with task-varying signals (12 seeds): spec rule 5.0, current rule 5.0, failed-capsule rule 3.75 [3.0, 4.5], all engine rules 2.6 [2.3, 2.8] (epigenetic hard-suppression at boost ≤ −0.3 fires after 3 failures); the current rule with the ×1.15 predictive factor needs 6. With a fixed signal key the arithmetic is exact (4 / 5 / 6 / 2, unit test). (b) oracle-gene selection accuracy in the last quarter: no learning 0.28, memory graph only 0.40, full engine 0.64, full + drift 0.61 (full vs none +0.36 [+0.25, +0.47]). (c) when a worse gene matches more keywords: the full engine escapes through failure penalties (best-gene share 0.32 → 0.46, discovered in 100% of classes) with or without drift; with the pattern score + memory graph only, drift raises discovery (0.875 → 0.96) and plateau override too (→ 0.94), but exploitation stays low (0.08 → 0.10) because the ×1.5 memory preference cannot overcome a 3× pattern-hit advantage | reuse and bans **reproduced**; ban latency differs from the spec's arithmetic in the loop (explained); "drift finds the better gene" **partly**: it finds it, and only failure penalties make the switch |
| X6 | sharing lets a lesson be inherited, even across models | 24 honest agents (weak/strong models), 30 epochs, 20 seeds, isolated vs SafeHub: solve rate +0.017 [+0.011, +0.025]; weak models +0.044 [+0.033, +0.056], strong −0.009 [−0.015, −0.002]; from-scratch discoveries 356 → 189 (−167 [−180, −155]); tokens per solve 3,906 → 2,804 (−28%); 60% [54, 67] of the genes weak agents took from the hub were written by strong-model agents | **reproduced** (modest solve gain, large cost saving) |
| X7 | Behind-EvoMap dynamics on a publish-rewarding, self-reported hub | 40 agents × 30 epochs, 20 seeds: never reused 97.5% [97.3, 97.6]; top-10% of agents hold 61% of earned credits (Gini 0.83), and all of them are farmers; 97% of credits come from promotion rather than fetches; vacuous validation among promoted 92.3%; rank validity (Spearman GDI vs true effect) 0.068 [0.056, 0.081]; mean true effect of served assets −0.018. Arms: without farmers never-reused drops to 58.7% and vacuous to 11.4%; honest writers that always attach the real check leave vacuous at 91.9% | all four findings **reproduced as mechanisms**; the 98% and >84% figures hinge on mass-publishing farmers (without them 59% / 11%) |
| X16 | the composite process score is not a task-gain measure | Spearman(composite, graded task) over cycles with vacuous or skipped validation 0.015 [−0.005, 0.035]; with real validation 0.14 (base) to 0.57 (no-farmers arm); 100% of vacuous-validation cycles, including those whose task *failed*, reach the 0.78 publish bar | **reproduced** |
| X8 | GDI is manipulable via self-reported metadata; blast radius dominates | inflating all claimed fields moves an honest asset up 75 percentile points [73, 77] (GDI +9.3); single levers: streak +42 pp, blast radius +28 pp, confidence +11 pp, outcome score +8 pp, report 0; degrading only blast radius from the optimum drops GDI 55.6 → 50.0 (paper: 40.2 → 36.0). SafeHub: rank change 0 for every field; inflated resubmissions (renamed genes) never gain a rank | manipulability **reproduced**; "blast radius dominates" **not** reproduced (streak is the larger lever under our reconstructed normalizations, which are unknown); SafeHub invariance holds |
| X9 | verifiable execution stops vacuous validation | combined lint + discriminative detector: precision 1.00, recall 1.00, false rejects 0.00 on both GeneWorld (108 items) and katas (225 items, real subprocesses); lint alone recall 0.875 (misses the weak assert that ignores the change); Evolver's local runner rejects only 25% (the blocked `-c`/`echo` kinds); the naive hub's empty-directory validator accepts only `--version` and rejects every real check | **reproduced** |
| X10 | adoption ranking + adoption credits + verified execution fix incentives | same population and seeds as X7, SafeHub vs naive: reuse among promoted 0.86 vs 0.025 (+0.84 [+0.80, +0.87]); vacuous among promoted 0 vs 0.92; rank validity 0.136 [0.038, 0.246] vs 0.068; surfacing validity 0.173 vs 0.058; true effect of served assets +0.372 vs −0.018; farmer credit share 0 vs 0.905; consumer true uplift +0.37 vs +0.02; exploration slots raise the share of verified assets ever adopted (+0.035 [+0.018, +0.054]) but do not measurably cut time to first reuse (−0.025 [−0.075, 0]) | **reproduced**, except exploration's time-to-first-reuse effect (not significant). Reuse among *all* submissions falls (0.010), because rejected spam stays in the denominator |
| X11 | untrusted assets must be quarantined and re-tested | 30 agents with 20% poisoners, 12 seeds, consumer solve rate vs an isolated baseline 0.721: naive hub + direct apply 0.178 (−0.54 [−0.57, −0.52]; 24% of consumer cycles ran a poisoned gene; 38 poisoned genes in local stores); naive + quarantine 0.723 (+0.002; 377 of 381 fetched assets rejected); SafeHub + direct 0.729; SafeHub + quarantine 0.729; SafeHub promoted 0 of 360 poisoned bundles | **reproduced**: quarantine removes the harm, and SafeHub alone already blocks poison |
| X13 | every change traceable; tampering detectable | 10 seeds: archive round trip identical (genes, events, capsules), lineage exact, ledger trunk = event chain; detection 100% for field edits without re-stamping, deletions, swaps, archive byte flips, and re-stamped event edits (via the `meta.parent_asset_id` hash chain); 0% for a re-stamped gene edit | **reproduced**; re-stamped gene or capsule edits need an external anchor (hub-held asset ids) |
| X15 | self-assessment contaminates learning | memory graph as the only learning channel, 12 seeds: with an executor that always prints a success EvolutionEvent, the faithful inferrer records 100% successes and the preferred genes' true effect is 0.30 vs 1.24 in safe mode (paired +0.94 [+0.76, +1.16]); last-quarter accuracy 0.25 vs 0.37. With an honest executor, the faithful heuristic is roughly truthful (54.7% successes recorded vs 55.6%) | **reproduced** |
| X5, X12, X14 | inert domination; evolved vs reference-distilled genes; tokens rise then fall | not implemented (see §6) | – |

**Live smoke** (`experiments/evomap/live_smoke.py`, Claude Haiku 4.5 as both solver and gene writer, katas, safe mode, 8 cycles, cached in `.rsi_cache/evomap`). Three invocations of the same smoke cost **$0.91 in total** and about 9 minutes:

1. The first invocation revealed a policy issue. Haiku's genes carried strong inline checks (`python -c "... assert money_total(['0.1','0.2']) == '0.30' ..."`), which the command policy blocks, as Evolver blocks `node -e`. So nothing solidified.
2. After the gene-writer prompt stated the allowed command format, `gene_unicode_nfkc_casefold` solidified (composite 0.96, validation `python smoke_test.py`, discriminative).
3. The last invocation re-ran on the final code (`results/evomap/live_smoke.json`: 102 s, $0.29). It resumed the persisted store, reused that gene locally on `strip_accents` and `same_text` (both solidified, 0.96 / 0.95), and a new money gene failed its task and was rolled back. The audit was ok.

Haiku solved 6 of the 8 evolve katas and all 10 holdout katas without any gene (holdout 1.0 → 1.0). Our katas give Haiku **no headroom**, so harder katas are needed before live X1–X3 runs are informative.

## 5. Capability checklist (spec §10)

| # | overview claim | code | evidence |
|---|---|---|---|
| 1 | spreads improvements across a network | `Hub` (`NaiveEvoMapHub`, `SafeHub`), `AgentNode.publish` / `_consult_hub`, `PopulationSimulator` | X6, X10 |
| 2 | agents turn what worked into genes others inherit | `Solidifier` → `Capsule`, `Distiller` → `Gene`, `Hub.publish`, quarantine then `promote_external` | X6 (60% of weak agents' hub genes from strong agents) |
| 3 | 98% never reused | `ReuseMetrics.never_reused_*` | X7: 97.5% (needs farmers) |
| 4 | GEP; compact genes beat skill documents | GEP-1.14.0-compatible assets, `asset_id` = reference hash, `JsonSchemaValidator` (strict / lenient) | tests; X1 offline mechanism only |
| 5 | shared library; local store plus hub; agents on different models | `LocalStore`, `Hub`, heterogeneous `GeneWorldModel` abilities | X6 |
| 6 | look before solving: local store, then hub, then a new approach | `AgentNode.cycle`: `GeneSelector` over the store → `Hub.search` (pattern match + similarity, client reuse score) → solve from scratch → gene writer; `hub_when` | X0, X6 |
| 7 | solve and distil | `Solidifier`, `Distiller` (every 5th, heuristic, LLM fallback, failure genes), `LeakageAuditor` | tests; X0 |
| 8 | validate and record; audit trail | `ValidationRunner` + `CommandPolicy` + `CountedFilePolicy` / `ConstraintChecker` + `VacuityDetector`; `EvolutionEvent` chain; `OutcomeInferrer` | X9, X13, X15 |
| 9 | publish and rank; credit economy | `GDIRanker`, SafeHub adoption rank, `CreditLedger` | X7, X8, X10 |
| 10 | models fixed; own harness; local validation plus hub rank | frozen harness plus injection; `Solidifier` keep rule; `Hub.rank` | all |
| 11 | runs in any repo; hub optional | domain-agnostic `run(domain, harness, hub=None)`; file-map workspaces (not git, §6) | X0 (3 domains) |
| 12 | genes, capsules, events | `assets.py`; SafeHub gene size ceiling (500 tokens) | tests |
| 13 | GEP defines creation, validation, sharing; hub, marketplace, workers, credits | assets + `Solidifier` / `ValidationRunner` / hubs / `CreditLedger`; marketplace, worker pool and bounties **not implemented**; MCP out of scope | – |
| 14 | compact genes strongest; skills unstable (4,590 trials) | X1–X3 arms + injection interface | offline only; live pending |
| 15 | CritPt 9.1% → 18.57%, 17.7% → 27.14% | not reproducible on CPU; analogues X0 / X6 | – |
| 16 | tokens rose then fell | X14 not implemented; tokens per solve tracked (X6) | – |
| 17 | vendor results not replicated | multi-seed CIs everywhere; `NaiveEvoMapHub` replica | all |
| 18 | Behind EvoMap measured 1.5M assets, 128K agents | `PopulationSimulator` + `ReuseMetrics`, scaled down to 40 agents and about 1,600 assets | X7 |
| 19 | rewards tied to publishing | naive credits: +100 per promotion, +5 per fetch | X7: 97% of credits from promotion |
| 20 | ~10% of agents hold most credits | `CreditLedger.top_share(0.10)`, `gini()` | X7: 61%, all farmers |
| 21 | 84%+ vacuous validation | empty-directory validator, faithful runner, `VacuityDetector` | X7 92% (needs farmers); X9 |
| 22 | 35% intrinsic, inflatable | `GDIRanker` (0.35 / 0.30 / 0.20 / 0.15, six-metric intrinsic) | X8 |
| 23 | need verifiable execution | `SafeHub.verify`: discriminative + `UpliftLCB` + `NoiseFloor` → hub-signed report | X9, X10, X11 |
| 24 | population; inherited across models | `PopulationSimulator`, weak/strong models | X6 |
| 25 | small structured units | `Gene` + render template + size ceiling | X1 |
| 26 | audit trail in every change | events with parent chain + hash chain, asset ids, provenance, archives | X13 |
| 27 | the only public network, hence measurable | `ReuseMetrics` dashboard for any hub | X7, X10 |
| 28 | agents grade their own work | SafeHub ignores claims; consumer-side re-test; spot-checks with slashing; safe `OutcomeInferrer` | X8, X10, X15, X16 |
| 29 | publishing rewarded over usefulness | stake, zero promotion credit, adoption-only credits, dedup lineage, deprecation | X10 |
| 30 | treat shared assets as untrusted | `external_candidates` quarantine + `QuarantineGate` + `injection_lint` + sandboxed commands | X11 |
| 31 | side-by-side profile (population, keep rule, weak guard, credits, reuse as cost control) | `PopulationSimulator`, `Solidifier` + `Hub.rank`, `SafeHub`, `CreditLedger`, tokens per solve | X6, X7, X10 |
| 32 | never let the loop grade itself | `QuarantineGate` (`rsi.core.RRSIGate`), safe keep rule, `rsi-taskcheck` | X11 |
| 33 | measure reuse, not output | `ReuseMetrics` (reuse rate, consumer uplift, rank validity) | X7, X10 |
| 34 | RRSI-style independent check before spreading | `TaskBank.measure`: δ from `noise_from_trials`, `UpliftLCB` (LCB ≥ δ), `NoiseFloor` (R ≥ −δ) | X10, X11 |
| 35 | hub optional; shared assets untrusted | `hub=None`; quarantine is the default in safe mode | X0, X11 |
| 36 | glossary: A2A, Gene, Capsule, GEP | assets; in-process hub API (the A2A envelope is **not implemented**) | – |
| 37 | name clash with EvoMap's AutoResearch | documentation note (this page) | – |
| 38 | EvoMap's curated related-work list | documentation note | – |

## 6. Deviations from the spec and from Evolver

* **Workspace.** The spec's git workspace is replaced by `{path: text}` maps (W0 before, W1 after) supplied by domain hooks. Blast radius comes from the dict diff under a domain-configurable `CountedFilePolicy` (Python and prompt files count). Rollback means the new gene is not stored and the post-state is discarded; gene learning metadata persists, as in Evolver.
* **Validation language.** Python instead of node. The faithful policy allows `python` only; eval-style flags `-c -i -m -X -W` and shell metacharacters are blocked. Safe mode adds `pytest` and the agent-internal `rsi-taskcheck`: a paired A/B of the gene on the agent's own in-scope decision tasks, passing iff the one-sided 90% paired-bootstrap LCB > 0. It exists because pure *strategy* genes have no code diff to test.
* **Task-stream adapter.** Evolver extracts signals from logs. We add `task:<family>` and the task's public meta signals. These descriptor signals are protected from the ≥ 3-of-8 suppression rule, and the adapter's `task` token is excluded from the cosine term. In faithful mode, the previous cycle's trace is also the log corpus (`carry_log_signals`), so the §4.11 heuristic sees whether errors persisted.
* **Selector.** `require_match=True` (our default) gives genes with no pattern hit and no token overlap a score of 0; the engine would let tag overlap or history make them candidates. `require_match=False` restores the engine rule.
* **Safe-mode fixes** (listed in `res.meta["safe_mode_fixes"]`):
  * no silent skips, and an empty validation list fails;
  * validation must be discriminative, and the agent's own graded decision task must be solved (an evolve-split decision, allowed by `rsi.core` discipline);
  * the memory graph gets the measured outcome, written immediately;
  * hub assets are quarantined and A/B-tested;
  * the failed-capsule ban is relative (failures must also outnumber successes; with stochastic graders the engine's "2 failures" rule bans a gene that works 57% of the time);
  * a task solved with no gene is a `skipped` ledger node, not a failed event (otherwise it feeds spurious failures to the plateau detector);
  * the distiller never adds a `--version` fallback.
* **Discriminative check.** It runs the *new* tests against the *old* code (W0 plus the validation scripts from W1). Without this, a check script added by the change "fails before" merely by being absent. Mutants exclude the validation scripts, and internal task checks run without the gene for W0.
* **Extensions beyond GEP.** An event hash chain (`meta.parent_asset_id`); manifest `head_event_asset_id` and `gene_library_version`. Gene loading never normalizes content: an early version reset invalid categories and thereby masked 0.7% of tampers in X13.
* **Hubs.**
  * No network or A2A envelope; hubs are in-process objects.
  * `NaiveEvoMapHub` promotes on the publisher's self-reported ValidationReport (`overall_ok`), claimed score ≥ 0.7 and non-zero claimed blast radius. `promote_rule="validator"` instead requires the empty-directory validator run to pass.
  * Validators are modelled as one `validator_pool` earning +20 per run.
  * GDI's internals are known only from snippets. The six intrinsic metrics (blast score with a 1–2 file / ≤ 50 line optimum, confidence, streak/5, outcome score, report ok, content substance), usage (log fetches + review success), social (reviews + 0.067 for an included event) and freshness (half-life 10 epochs) are our reconstruction.
  * SafeHub parameters are our choices: stake 20, adoption credit 30 capped at 3 per epoch, ρ = 0.2, r_min = 10, ν = 3, h = 15, ε = 0.2, n_min = 2, bank n = 24 in-scope / 12 off-scope tasks × k = 4, δ = `noise_from_trials` with z = 2, LCB = paired bootstrap at alpha 0.10 (one-sided 95%).
  * Strategy genes whose validation is only `rsi-taskcheck` skip the workspace check (a) and rely on the uplift test (b), as §9.2.3 distinguishes. Exploration puts a Thompson-sampled fresh asset first with probability ε.
* **Not implemented** (optional or low priority):
  * the hub LLM signal layer 3;
  * canary (always "skipped", which is what Evolver does outside its own repo);
  * the stub LLM reviewer; reflection-suggested personality mutations;
  * heartbeats, bounties, worker pool, marketplace, recipes/packs, self-PRs, anti-pattern publishing;
  * skill2gep, beyond its leakage audit and forgery guard; the conversation distiller; epoch-boundary triggers (the API exists; the engine does not call it either);
  * X5, X12 and X14.
* **Determinism.** A logical clock: one cycle = 1 h, one population epoch = +24 h. GeneWorld uses common random numbers, and all ids are counters, so runs are bit-reproducible.

## 7. Limitations

* **Simulated worlds decide the headline numbers.** The Behind-EvoMap replication shows the *mechanisms* (publication-paid credits + self-reported validation + claim-based ranking ⇒ junk floods, credits concentrate, ranking decouples from value), not the network's actual percentages. Those depend on farmer share and farm rate (see the `no_farmers` arm). Effect sizes in GeneWorld are assumptions.
* **Offline representation experiments are circular.** `KataSimSolver`'s response to genes, skills, AVOID items and compositions is a set of knobs, so X1–X3 offline test the pipeline, not the claims. The live variant is one command away (`x1_representation.py --llm claude:haiku`, about 225 calls). Our katas are too easy for Haiku, though (live smoke: 10/10 holdout without genes), so harder tasks are needed first.
* **Adoption-based rank validity is modest** (0.14): among verified assets, which are all beneficial, adoption reflects demand as much as quality. The large gain is in *what is served* (true effect +0.37 vs −0.02).
* **Safe mode trades false positives for false negatives.** It keeps fewer genes: GeneWorld 6.6 vs 11.3 library genes, holdout gain +0.17 vs +0.24 for faithful mode, where noisy genes still add coverage. On agentqa's SimModel, whose outcomes re-draw with any prompt change, the strict task check keeps 0.7 genes per run vs 2.5 in faithful mode, and the holdout gain is small (+0.03).
* **Sandboxing.** `SubprocessExecutor` uses `rsi.core.sandbox` rlimits and a scratch directory, which is not a security boundary. `InProcessExecutor` executes world-generated scripts in-process and is for simulations only.
* **Tamper evidence** covers unsigned edits and event-chain rewrites. A re-stamped gene or capsule needs an external anchor, such as hub-held asset ids or a published head hash.
* **Live evidence** is one small smoke run. No live claim was tested at scale.

## 8. Requests for `rsi.core` (worked around locally)

* `Ledger` defines `__len__`, so an empty ledger is falsy and `ledger or Ledger()` silently drops a caller's ledger. This caused a real bug here. Please add `__bool__` returning True (the same holds for any container-like core object).
* `LeakageCritic.denylist_hits` compiles one regex per term per call. With hundreds of terms (a hub's task bank) this thrashes the `re` cache and dominated runtime (38 of 45 s). A substring prefilter or a precompiled alternation would fix it. `LeakageAuditor.deny_hits` does the prefilter locally.
* `MockLLM` stores every call in `self.calls` (unbounded memory in long simulations). An opt-out flag would help; `GeneWorldModel` subclasses `LLM` directly for this reason.
* `Task.meta` has no public/private distinction. A convention such as `meta["public"]` or a `Task.public` view would let methods show agents task information safely; `rsi.evomap.inject.public_task` whitelists keys meanwhile.
* The agentqa `SimModel` seeds its RNG on the whole prompt, so any irrelevant prompt change re-draws every outcome (no common random numbers). Paired A/Bs of guidance are therefore noisy. Seeding the correctness draw on (question, seed) and applying prompt effects as a threshold shift would make paired comparisons exact.
* `Evaluator` evaluates one artifact per call; per-task artifact routing (injecting a different gene per task) needs a wrapper domain (`GeneRoutedDomain`). An optional `artifact_fn(task)` would make such methods simpler.
