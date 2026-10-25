# EvoMap: a gene library built from validated solves (GEP / Evolver), optionally shared through a hub

Method reference for `rsi.improve(..., method="evomap")`. **Sources (all read in full, Oct 2026):**
- [SG] Wang, Ren, Zhang, "From Procedural Skills to Strategy Genes", arXiv 2604.15097v4. The vendor paper: gene
  representation results, CritPt runs, and the protocol loop in App. A (it calls GEP the "Gene Evolution Protocol").
- [BE] Ye et al., "Behind EvoMap", arXiv 2605.25815v4. Independent measurement study; §2 describes the agent and
  Hub procedure, Eq. 1 the GDI, App. B Eq. 3 the intrinsic score.
- [LWF] Zhang et al., "LongWoF-Bench: Evaluating EvoMap Genes", arXiv 2608.23200v4. Vendor follow-up; §3.1 gives
  the gene-construction procedure. **None of the three is an "Evolver paper"**: the engine is documented only by
- [GEP spec] `@evomap/gep-sdk@1.14.0` `spec/gep-spec-v1.md` ("Genome Evolution Protocol"), [Evolver README]
  (`EvoMap/evolver`), [wiki: page] (evomap.ai/wiki, incl. the GDI and promotion formulas).
- [engine: spec §n] = Evolver v1.94.0 code behaviour as read (partly deobfuscated) in `docs/methods/evomap/paper-spec.md`;
  not in the papers or public docs, not re-verified here. **rsi choice** = this package only. Our code: `rsi/evomap/`.

## 1. In one paragraph

Agents should not each learn the same lesson alone: an agent's local engine (Evolver) turns validated work into small
content-hashed assets and can share them through a central Hub, "analogous to a Git client that publishes a repository
to a public GitHub server" [BE §2]. A **Gene** is a compact control object `g = (m, u, π, α, c, v, η)`: matching
signals, a one-sentence summary, a small set of ordered strategy steps, AVOID cues, optional constraints and
validation hooks, canonical metadata [SG App. A.3 Eq. 15; §3.3 Eq. 8 omits η]; a **Capsule** is one validated application, an
**EvolutionEvent** an immutable audit record [SG App. A.2; GEP spec §2]. The protocol loop is
scan -> signal -> intent -> mutate -> validate -> solidify; "only assets that satisfy the required criteria are
eligible for persistence" [SG App. A.6 Eq. 18]. In the engine the criteria are the gene's own validation commands
(plus constraints): "the agent only saves the resulting Capsule if all validation commands pass; otherwise, the
changes are reverted" [BE §2.1]. What improves is the asset library; model weights stay fixed [SG §3.1].
Results: a ~230-token gene beats a ~2,500-token Skill package, 54.0% vs 49.9% (51.0% no guidance; checkpoint pass
rate averaged over Gemini 3.1 Pro and Flash Lite; Skill Probe, 1,440 of the 4,590 trials) [SG §4.1.1 Table 1];
CritPt 9.1% -> 18.57% and 17.7% -> 27.14% [SG §4.4]; genes distilled from verifier-confirmed trajectories beat Skill
by 8.7-15.5 pp on 252 tasks while reference-distilled genes trail it by 3.3-11.3 pp [LWF §5.1-5.2]. Behind EvoMap:
98% of assets never called, GDI dominated by self-reported intrinsic metadata, and 84.2% of Genes have no
validation commands (66.0%) or only trivial ones (18.2%) [BE §4.1, §5.2, §6.1 Table 1]. `rsi.evomap` ports the
engine (`mode="faithful"`) and adds a corrected variant (`mode="safe"`, the default; rsi choice).

## 2. Inputs and setup

What the sources assume: a frozen model (θ fixed) [SG §3.1]; a git workspace, runtime logs in `memory/` and a host
coding agent that executes Evolver's prompt ("a prompt generator, not a code patcher") [Evolver README]; for
evaluation, tasks with an executable checker: sandboxed checkpoint scripts, 120 s timeout [SG §3.3.1, App. B.1], or a
private verifier with hidden tests [LWF §2.3]. What `rsi.evomap` needs before cycle 1 (rsi choices):
- **Frozen harness** `Artifact` (e.g. `prompt.md`), never edited; genes are injected at solve time (`FileInjector`
  writes `genes/*`; `AppendInjector`/`SkillInjector`/`TextInjector` exist). `res.best` = harness + `genes/library.json`.
- **Tasks with public signals**: `family` (becomes `task:<family>`) and `meta={"signals": [...]}`/`meta["keywords"]`
  stand in for Evolver's log scan. Without them every task looks alike. Hidden answers stay in `target`/`grade`.
- **Splits**: `evolve` (`Config.split`) is the practice stream, one task per cycle; the gene writer sees only its
  public text and trace. `val` (`Config.heldout_split`, falls back to `evolve`) is read only for the consumer
  quarantine A/B when a hub is attached. `holdout`/`ood`/`test` are sealed (`SealedSplitError`); only the SafeHub's
  hidden `TaskBank` (hub-owned) touches one, by design.
- **Grader**: locked `Domain.grade` -> score in [0,1]; solved at `success_threshold` (0.5). Optional hooks:
  `pre_workspace`/`workspace` (W0/W1), `public_feedback`, `public_text`, `leakage_terms`, `counted_policy`.
- **Models**: `llm_task` = frozen solver (required); `llm_propose` = gene writer (`None` = reuse-only, no new genes);
  `hub` optional (`NaiveEvoMapHub` or `SafeHub`), forwarded by `rsi.improve(..., hub=hub)`.
- **Budget**: `Config.cycles` (30) = one task per cycle; `Budget(max_rollouts=..., max_usd=...)` via `budget=`.
- **Measured first**: nothing feeds a decision. The `baseline` trace event (`trace_baseline_k=2`) is trace-only and the
  loop has no run-level noise band. SG reports no confidence intervals; LWF reports task-level bootstrap CIs and exact
  McNemar tests [LWF §4]. Measure delta yourself (SKILL.md step 3) and check headroom: a harness that already solves
  `evolve` writes no genes (live Haiku on our katas: holdout 1.0 -> 1.0).

## 3. Round by round (what the sources describe)

The papers give the loop at protocol level only: SG's six stages [SG App. A.6], the spec's seven phases
DETECT -> SELECT -> MUTATE -> HYPOTHESIZE -> EXECUTE -> EVALUATE -> SOLIDIFY [GEP spec §3], and BE's per-task
narrative [BE §2.1-2.2]. Formulas below marked [engine] come from the Evolver code, not from any paper.

1. **Scan / detect signals** [SG App. A.6 "Scan", "Signal"; GEP spec §3.1]: runtime context (session and error logs,
   memory, user instructions) -> string signals. Error signals (`log_error`, `errsig:<detail>`, `recurring_error`)
   trigger repair; opportunity signals (`user_feature_request:<snippet>`, `capability_gap`, `perf_bottleneck`,
   `stable_success_plateau`) trigger innovate. De-dup: a signal in >= 3 of the last 8 events is suppressed; all
   suppressed -> `evolution_stagnation_detected`; >= 3 consecutive repairs -> strip repair signals, force innovation.
   Control signals: `consecutive_failure_streak_<N>`, `failure_loop_detected` (5+) with `ban_gene:<id>`,
   `high_failure_ratio` (75%+ of last 8), `evolution_saturation` (3+ empty cycles) [GEP spec §7]. The engine adds a
   weighted-keyword layer and a hub LLM layer every 5th cycle [engine: spec §3.2].
2. **Outcome of the previous attempt** goes to the memory graph ("inferred result of the previous attempt") [GEP spec
   §2.6, §3.7 step 5]. Without direct observation it is inferred from error signals (prev/cur error: 0.85 cleared,
   0.20 persisted, 0.15 new error, 0.60 stable_no_error); an observed EvolutionEvent in the evidence text overrides
   it [GEP spec App. B]. The engine writes it at the start of the next cycle [engine: spec §4.11].
3. **Look for an existing solution.** BE's order: "first searches its local store ... If no adequate match is found
   locally, the agent sends the same query to the remote EvoMap Hub ... If none is found, the agent must generate a
   new one locally" [BE §2.1]. The released engine queries the hub **before** local selection and injects a hit as a
   "STRONG REFERENCE" next to the local gene; client hit score `r = confidence·min(max(streak,1),5)·rep/100 + 0.3·sim
   >= 0.72` (0.55 with problem signals) [engine: spec §3.1, §3.9, §4.16]. External assets "are staged (never executed
   directly) and require local validation" [GEP spec §10.2].
4. **Select a gene** [GEP spec §3.2]: score = number of matching `signals_match` patterns (substring, `/regex/`, or
   `a|b` aliases [GEP spec §2.1]); drop memory-banned genes (unless drift is active); prefer the memory-preferred gene; with probability
   proportional to `driftIntensity = 1/sqrt(Ne)` (Ne = gene count) pick randomly among top candidates; distilled genes
   x0.8. Memory edges: `p = (s+1)/(n+2)`, `value = p·0.5^(age_days/30)`, Jaccard >= 0.34 signal matching, ban at
   >= 2 attempts and value < 0.18 [GEP spec §4]. The engine instead scores `hits + 0.6·tag + 0.4·cos + adj` (x1.5 if
   preferred), bans per key at >= 4 attempts and best < 0.15, after 2 overlapping failed capsules, on `ban_gene:`, and
   at epigenetic boost <= -0.3; its random pick fires whenever `I > 0` (~24% of cycles with 17 genes) [engine: spec
   §3.3, §4.2-4.5]. SG finds one targeted gene beats 2-3 composed ones on Gene-Bench (single 54.0%, two
   complementary 44.9%) [SG §4.2.4], yet the CritPt version B run used 210 gene slots for 70 tasks [SG App. D.3].
5. **Intent and mutation** [SG App. A.6 "Intent", "Mutate"; GEP spec §3.3, §2.4]: category from signals (error ->
   repair, opportunity -> innovate, else optimize); risk repair/optimize low, innovate medium, high only if allowed and
   rigor >= 0.6 and risk_tolerance <= 0.5, with mandatory safety downgrades. In SG "mutate" generates the candidate
   asset (rewritten code, prompt structure, strategy steps, AVOID cues or metadata); in Evolver the Mutation object is
   only a declared intent and the host agent makes the change.
6. **Hypothesize**: record "given these signals, this gene and mutation, I expect this outcome" [GEP spec §3.4].
7. **Execute** (implementation-specific): a prompt with signals, gene, capsule candidates, mutation and constraints
   goes to an LLM/agent, which applies changes following the strategy steps within `max_files`/`forbidden_paths`
   [GEP spec §3.5].
8. **Evaluate** [GEP spec §3.6]: blast radius (files, lines), constraint check, the gene's validation commands, optional
   canary, score in [0,1] "based on validation results, blast radius, and constraint compliance"; hard caps 60 files /
   20,000 lines. Commands must start with `node`/`npm`/`npx`, no shell operators, 180 s each [Evolver README] (engine:
   `node` only [engine: spec §4.9]). Engine score `.05 sig + .10 sel + .05 mut + .15 blast + .25 constraint + .25 validation + .10 protocol
   + .05 canary` (JS rounding) is not the keep rule [engine: spec §4.10].
9. **Solidify = keep rule** [SG App. A.6 "Validate", "Solidify"; BE §2.1; GEP spec §3.7]: an EvolutionEvent is
   appended for every cycle; on success a Capsule is created/updated, epigenetic marks applied, distillation optionally
   triggered; on failure no capsule and optional rollback (git reset). Engine rule: `success = constraints_ok AND
   validation_ok AND no protocol violation`, where a missing script is skipped and an empty or all-skipped list passes
   [engine: spec §3.6, §4.9]. Validation commands are self-declared by the gene [BE §6.1]. In SG the persisted asset
   is "typically a new or revised gene"; the engine's solidify does not parse the agent's printed Gene, so new genes
   come from distillation, auto-created `gene_auto_*` genes or the hub [engine: spec §3.6].
10. **Distil** [GEP spec §6, §11]: when the last 10 capsules have >= 7 successes, >= 24 h since the last run, and >= 10
   capsules: group successful capsules (score >= 0.7) by gene, analyse, LLM-synthesise a Gene, validate; id prefix
   `gene_distilled_`, `max_files` <= 12, selection factor 0.8. Engine: also every 5th successful solidify, heuristic
   synthesiser by default, failure distiller from >= 5 failed capsules [engine: spec §3.7, §4.14]. SG's guidance:
   compress failures into compact warnings (failure warnings only: 54.4% vs strategy only 52.3%) [SG §4.3.3].
11. **Publish** only "when sharing is enabled" [BE §2.1], as a Gene+Capsule(+Event) bundle; hub requires
   `outcome.score >= 0.7` and non-zero blast radius [GEP docs: evomap.ai/skill.md]. Engine auto-publish: success,
   score >= 0.78, streak >= 2, <= 5 files / 200 lines, not `reused` [engine: spec §4.13].
12. **Hub admission**: verify SHA-256 `asset_id`s [wiki: 05], reject very similar assets, hold as hidden `candidate` [BE §2.2];
   validator nodes run the proposer's validation commands in a sandbox [Evolver README "Validator Role"]; GDI recomputed
   hourly; promoted (visible, reusable) at GDI >= 25 [BE §2.2]. Full rule: GDI lower bound >= 25, intrinsic >= 0.4, confidence
   >= 0.5, node reputation >= 30, validation consensus not majority-failed [wiki: 05-a2a-protocol]. GDI = 0.35 I + 0.30
   U + 0.20 S + 0.15 F [BE Eq. 1] (x100 on the wiki, ranked on a lower-bound track) with
   `I = 1/6[clip(C) + min(S/10,1) + max(0, 1 - F·L/1000) + min(T/5,1) + min(ℓ_sum/200,1) + clip(R/100)]` [BE App. B
   Eq. 3]; usage, social and `freshness = exp(-days/90)` per [wiki: 06-billing-reputation]. BE's refit:
   `0.35 I + 0.29 U + 0.17 S + 0.10 F - 1.38`, R² 0.995 [BE Eq. 2].
13. **Fetch and apply**: other agents fetch promoted assets, adapt the Gene's strategy and Capsule's diff to their own
   code and run its validation; REPORT feedback drives selection [wiki: 00-introduction §4; BE §2.1].

```text
for each cycle (daemon loop with adaptive sleep [Evolver README]; ~2 days per CritPt run [SG App. D]):
    signals = dedupe(scan(logs, memory, instructions), last 8 events)            # GEP §3.1
    memory.record_outcome(previous attempt)                                      # GEP §2.6, App. B
    gene = best local match by signals_match (+ memory advice, 1/sqrt(Ne) drift)  # GEP §3.2
    if no local match: gene = hub.search(signals)    # BE §2.1 (engine: hub first, as a reference)
    mutation = intent(signals) with risk downgrades; record hypothesis           # GEP §3.3-3.4
    host agent edits workspace following gene.strategy                           # GEP §3.5
    ok = constraints(blast) and all gene.validation pass                         # GEP §3.6, BE §2.1
    append EvolutionEvent; if ok: write Capsule else revert                      # GEP §3.7
    maybe distil; if sharing and eligible: publish bundle -> candidate -> GDI promotion   # GEP §6, BE §2.2
```

**What changes across rounds**: capsules and events accumulate; genes gain learning history (<= 20), anti-patterns
(<= 12) and epigenetic marks [GEP spec §2.1]; memory edge values (Laplace, 30-day half-life) steer preference and bans
[GEP spec §4]; drift falls as the library grows; distillation adds genes. **Stopping rule**: not specified in the papers;
the CritPt runs lasted ~2 days each [SG App. D]; LWF's per-task discovery stops at the first verifier pass or the end
of a fixed rollout budget whose value is not given [LWF §3.1]. No convergence test anywhere.

## 4. Sample by sample (what the papers do per task)

- **Gene-Bench** [SG §3.3.1, App. B]: the unit is one scenario-model-condition trial: task description in the user
  `contents`, gene (or Skill, or nothing) in `systemInstruction`, T = 0.05, <= 16,384 output tokens; the program runs in
  a sandbox (120 s) and a scenario test script checks checkpoints. Recorded: `r_i = p_i/n_i` and complete pass
  `1[p_i = n_i]` (secondary). Aggregation: `PassRate = mean_i r_i`; "Avg." = mean of Pro and Flash; Δ in pp vs no
  guidance; no CIs. Genes and Skills are built per scenario and evaluated on that same scenario: there is no held-out
  task set. Trials per scenario-condition: not specified in the papers.
- **LongWoF discovery** [LWF §3.1, App. A.3]: the producer attempts the task without verifier information; on failure
  the next rollout gets the previous solution plus sanitized verifier feedback, within a fixed rollout budget, until a
  pass (140 of 252 passed first try; 404 calls in all). The object refined is the task solution, not the gene; a passing
  trajectory is then distilled into a Gene (strategies, prerequisite checks, boundary conditions, corrections).
- **LongWoF reuse** [LWF §2.3, §4]: one recorded trial per task-condition; the consumer sees the public spec plus the
  Gene, never the trajectory, feedback, hidden tests or gold outputs; strict pass = all mandatory checks; paired
  task-level bootstrap CIs and exact McNemar tests. Genes are reused on the tasks they were distilled from, so the result
  measures amortised reuse (and cross-model transfer), not generalisation to new tasks.
- **Engine cycle** [GEP spec §2.2-2.6]: one cycle per signal set, not per benchmark task. Recorded: EvolutionEvent
  (signals, genes used, mutation, blast radius, outcome status/score, source type), Capsule on success (trigger,
  confidence, outcome, streak, diff), ValidationReport (each command's ok/stdout/stderr), memory-graph events. No
  cross-sample aggregation except memory-graph edge statistics; on the hub, usage, votes and validator reports enter GDI.
- **rsi**: one solve per cycle on one `evolve` task; see section 6 for retries, quarantine and splits.

## 5. Paper defaults vs this package

| setting | source value [cite] | rsi `Config` field = default | note |
|---|---|---|---|
| loop length | ~2 days per CritPt run [SG App. D]; daemon loop [Evolver README] | `cycles=30` | one task per cycle |
| mode | Evolver v1.94.0 | `mode="safe"`; `"faithful"` = engine port | rsi choice; `None` fields take mode defaults |
| decoding | T = 0.05, 16,384 tokens, 120 s [SG App. B.3] | set on the LLM, not in `Config` | |
| gene score | pattern-hit count [GEP §3.2]; engine hits + 0.6 tag + 0.4 cos [engine: spec §4.2] | `selector_mode="current"` (`"spec"` = hits only) | |
| memory | Laplace, 30 d half-life, Jaccard 0.34, ban >= 2 attempts & value < 0.18 [GEP §4]; engine per-key >= 4 & best < 0.15 | `memory_mode="current"` (`"spec"`), `use_memory=True` | |
| drift | `1/sqrt(Ne)` [GEP §3.2]; engine adds an offset when enabled | `drift=False`, `plateau_override=True` | random pick still fires (~0.24 at 17 genes) |
| de-dup | >= 3 of last 8 events [GEP §3.1] | always on | task descriptors protected (rsi choice) |
| risk | explore = medium [GEP §3.3]; engine low | ported from engine | |
| strategy preset | engine balanced 20/20/50/10 [engine: spec §4.7]; README says 50/30/20 | `strategy_preset="balanced"` | shares not sampled |
| failed-capsule ban | engine: 2 failed capsules, overlap >= 0.6 [engine: spec §4.5] | `failed_capsule_bans=True`; `failed_capsule_rule` F absolute, S relative | |
| keep rule | validated assets only [SG App. A.6]; all validation commands pass [BE §2.1]; engine + constraints + protocol | F as engine; S adds `vacuity_check=True`, `require_task_success=True` | no held-out gate in either |
| hard caps | 60 files / 20,000 lines [GEP §3.6] | fixed | |
| validation runner | node/npm/npx, 180 s [Evolver README]; engine skips missing, empty passes, 2 retries | F same; S no skips, empty fails, 0 retries | Python commands (rsi) |
| composite weights | engine only [engine: spec §4.10] | fixed; `estimate_drift_penalty=False` | Evolver's drift penalty is dead code |
| gene creation | distil a verifier-passing trajectory [LWF §3.1]; engine: distiller / auto gene / hub | `propose=True`, `retry_after_propose=True`; `new_gene_retries` F 1 / S 3, `new_gene_check` F single / S paired | rsi choice |
| distill | last 10 >= 7 successes, 24 h, >= 10 capsules, score >= 0.7, LLM [GEP §6, §11]; engine every 5th, heuristic | `distill=True`, `distill_every=5`, `distill_min_capsules=10`, `llm_distill=False` | 24 h / 7-of-10 gate also ported (1 cycle = 1 h) |
| failure distill | engine: >= 5 failed capsules, 12 h | `failure_distill` F on, S off | |
| hub order | local first, then hub [BE §2.1]; engine hub first | `hub_when` F `"first"`, S `"no_local"` | sources disagree |
| hub reuse | engine 0.72 / 0.55 with problem signals, mode `reference` | `reuse_threshold=0.72`, `reuse_threshold_problem=0.55`, `hub_k=5`; `reuse_mode` F `reference`, S `quarantine` | |
| publish bar | hub: score >= 0.7, blast > 0 [GEP docs: skill.md]; engine 0.78, streak >= 2 | `publish=True`, `publish_requires_eligibility=True` | |
| promotion | GDI >= 25 [BE §2.2] + 4 more conditions [wiki: 05] | `NaiveEvoMapHub`: self-report + score >= 0.7 + blast > 0 | see section 6 |
| GDI | 0.35/0.30/0.20/0.15 [BE Eq. 1]; I per [BE Eq. 3] | `GDIRanker(variant="be2026")` | U, S, F reconstructed |
| rollback | optional git reset [GEP §3.7]; engine `stash` | `rollback="stash"` | map workspace: gene not stored |
| task success | checkpoint rate [SG]; all checks [LWF] | `success_threshold=0.5` | rsi choice |
| seeds | not specified in the papers | `seed=0` | |
| quarantine / audit | no consumer A/B; external assets staged and validated locally [GEP §10.2] | `quarantine_k=2`, `quarantine_n=12`, `quarantine_min_tasks=3`; `trace=True`, `shadow_monitor=True` | rsi choice |

## 6. Where our implementation differs

Package verdicts from `claims-audit.md` (54 claims after retry round 2: 28 reproduced, 10 partial, 1 not reproduced,
14 not testable here, 1 contradicted), checked against the papers above.
- **The rsi cycle** (`AgentNode._cycle`): next task from a shuffled `evolve` split; signals from `task:<family>` +
  meta + regex/keyword layers (F also uses the previous trace and the hub LLM layer); de-dup; F: hub first; plateau,
  memory advice, select; S: hub only if no local gene fits, then `stage_external` (asset_id check, confidence x0.6)
  and a `QuarantineGate` A/B on `val` tasks; mutation/personality (feed the score and trace only, the solver sees just
  the gene card); **solve once**; if no local or adopted gene was used and the solve failed, `llm_propose` writes one
  gene from signals, public text and the last 1,500 chars of trace, judged on its own task (F 1 fresh-seed retry; S 3
  seeds paired with/without, kept iff > half solve and it beats the bare harness); solidify (S also needs non-vacuous
  validation and the own task solved; F gives a geneless cycle a `gene_auto_*`; S skips a geneless solved cycle);
  store the gene on success; memory outcome (F next cycle, S immediately); distil; publish if eligible.
- **Gene creation** (rsi choice): Evolver never stores the agent's printed gene; LWF distils only verifier-passing
  trajectories after feedback-driven retries. Our writer proposes after one failed scratch attempt.
- **Hub order**: faithful follows the engine (hub first), safe follows BE §2.1 (local first).
- **Task-stream adapter, workspace, language**: signals from `family`/`meta`, not logs; `{path: text}` W0/W1 map
  (`GitWorkspace` opt-in); Python validation commands; no canary, in-place genes, LLM reviewer, heartbeats,
  marketplace, A2A envelope or skill2gep. Hubs are in-process objects.
- **Hub**: `NaiveEvoMapHub` promotes on the publisher's self-reported report, claimed score >= 0.7 and non-zero blast;
  no GDI >= 25 gate, no similarity rejection, no validator consensus. `GDIRanker` uses BE Eq. 3 for I, but usage, social
  and freshness (half-life 10 epochs) are reconstructions and reputation is fixed at 50, although the wiki now documents
  all of them. `SafeHub` (hidden `TaskBank`, uplift LCB, adoption ranking, stake) is rsi's design, not EvoMap.
- **Evaluation**: SG and LWF score genes on the tasks they came from; rsi scores the library on sealed
  `holdout`/`ood` (`evaluate_library`), which is stricter.
- Verdicts: **REPRODUCED** engine mechanisms (hashing byte-identical, selector, solidify, composite, eligibility,
  vacuity; X15 self-assessed memory outcome; X16 composite is not a task-gain measure, Spearman ~0). **PARTIAL**:
  signals (hub LLM layer is a stand-in), distiller (never reached 10 distinct capsules in long runs), GDI internals,
  Behind-EvoMap percentages (set by our farm-rate knob). **NOT REPRODUCED**: B7, blast radius as the dominant GDI
  lever (BE Table 2: 40.2 -> 36.0); SafeHub extension rows (not EvoMap claims): rank validity > 0 (E3), exploration
  slots cutting time to first reuse (E4). **CONTRADICTED**: "local store first, then hub", which is BE §2.1's own
  description (and the overview's), against the released engine. **NOT TESTABLE**: vendor numbers (54.0/49.9/51.0,
  CritPt, 4,590 trials; LWF is not in the audit); offline X1-X3 only restate simulator knobs, and live Haiku has no
  headroom on our katas. Simulated gains: GeneWorld safe 0.41 -> 0.56, katas 0.39 -> 0.84, faithful far smaller;
  agentqa not significant.

## 7. Applying it with rsi

```python
import rsi
from rsi.evomap import Config, evaluate_library
res = rsi.improve(dom, seed, method="evomap", llm_task="claude:haiku", llm_propose="claude:sonnet",
                  config=Config(cycles=40, mode="safe", split="evolve", heldout_split="val"),
                  out_dir="runs/genes", cache_dir=".rsi_cache/genes")        # hub=None: one offline agent
rep = evaluate_library(dom, task_llm, res, splits=("holdout", "ood"))        # no genes vs routed library, sealed splits
```
`res.best["genes/library.json"]` is the library; `evaluate_library`/`GeneRoutedDomain` pick one gene per task by selector
score (no memory, no drift). Fields users set: `cycles`, `mode`, `split`, `heldout_split`, `success_threshold`, `validation_hint`,
`default_validation`, `distill_every`, `llm_distill`, `quarantine_min_tasks` (set 1 on tiny task sets), `workers`. Sharing
needs `hub=SafeHub(TaskBank(dom, harness, model, split="test", ...))` and several agents (`PopulationSimulator`);
`rsi.improve` runs ONE agent. The gene writer prompt must state the allowed validation command format, or inline checks
(`python -c`) are blocked and nothing solidifies.

**Audit with `python -m rsi.cli inspect runs/genes`** (`TRACE.md`, `trace.jsonl`, `ledger.jsonl`, `store/`, `trajectory.json`):
- `round_start`: library, capsules, event chain, memory counts, pending outcome, personality, rollouts used.
- `analysis`: raw -> deduped signals, plateau/drift, memory advice (preferred, bans), selector mode and scores. Recompute one
  score by hand.
- `proposal`: the gene tried (reuse / hub / scratch / newly written with writer prompt, reply, leakage audit) and the
  injected diff. Check the gene is generic, not a copied answer.
- `eval`: every graded rollout (solve, retry arms, taskcheck, quarantine). `gate`: solidify with every keep-rule input
  (constraints, each validation command + exit, vacuity lint, discriminative before/after, task score, composite,
  failure mode, publish numbers). Verify `success` from these by hand.
- `decision`: library before/after; `state`: memory outcome actually written; `monitor`: holdout/ood score of each new
  library version (library growth with flat holdout = noise or memorization). `res.meta["audit"]` and
  `res.meta["safe_mode_fixes"]`.

**Method-specific failure modes**:
- **Self-grading**: F's keep rule tests the gene's own commands, not task gain; vacuous/skipped/empty validation still
  passes and scores >= 0.78 (X16: 100% of such cycles publishable), the pattern BE §6.1 measured. Prefer `mode="safe"`.
- **Self-reported outcome**: F's memory graph can read the executor's printed EvolutionEvent (X15: preferred gene's true
  effect 0.30 vs 1.26). If traces never contain error text, every F outcome is `stable_no_error` (inert) and a gene is
  banned after 8 inert outcomes; use S or put an error line in failure traces.
- **No headroom**: a seed that already solves the stream writes no genes; no genes means nothing to evaluate.
- **Noisy small-N keep**: each gene is judged on one task (3 paired seeds in S). Stochastic graders give false keeps
  and bans; measure noise first and read `rsi-taskcheck` LCBs.
- **Provenance**: LWF's reference-distilled genes trailed Skill on every model [LWF §5.2]; prefer genes written from
  graded, solved attempts and read `leakage_audit`; score `holdout`/`ood` via `evaluate_library`.
- **Hub poison / spam** (population runs): naive hub + direct apply ran a poisoned gene in 94% of consumer cycles (X11);
  keep quarantine on and never trust a self-reported `validation`. Drift still picks a non-best gene ~24% of cycles (17 genes).
- **Missing signals**: tasks without `family`/`meta["signals"]` match nothing; genes will not be reused.
