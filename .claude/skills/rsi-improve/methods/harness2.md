# Harness²: per-task harness refinement from execution contrasts, with no grader, plus a per-domain library

Method reference for `rsi.improve(..., method="harness2")`. **Sources** (as recorded in
`docs/methods/harness2/paper-spec.md`, which tags every statement; read Oct 2026):
- [paper §n / App. n / Table n] Xu, Chen, CuiZhu, Dalvi Mishra, Ming, Yu, Han, Lee, Pfister, "Harness²: Recursive
  Agent Harnessing for an Open World", arXiv 2610.14224v1 (9 Oct 2026). HTML read verbatim; figures only by caption.
- [code path::symbol] the reference repo `google-research/harness2` (Apache-2.0, `METHOD_VERSION = 2`, prompts
  `VERSION = "harness2-1"`), read but never executed: `harness2/core/{recursion,schedule,selection,library,prompts,
  context,tree,spec,ledger,judge}.py`. Where paper and code disagree the spec takes the code [spec §10].
- [spec §n] = a section of our spec when the point is its synthesis. **rsi choice** = this package only. Our code:
  `rsi/harness2/`; implementation notes: `docs/methods/harness2/implementation.md`.

## 1. In one paragraph

Harness² adapts an agent's harness *at test time*, task by task, when there is no ground truth: tasks arrive as a
stream "with no ground-truth answers, verifiers or labelled validation feedback", each starts from the same common
harness H0, the current task may be re-run within a bounded budget, completed tasks are never replayed and future ones
are unavailable [paper §2.1, App. A.1]. Per task (task-level recursion) it runs **Propose-Probe-Compose**: k reference
rollouts of H0 show how the harness behaves ("not whether its deliverable is correct"); scoped editors propose an INV
candidate (how the agent works: procedure, tools, completion) and a DOM candidate (what a practitioner knows); each
candidate is executed once on the same task (Probe); a composer reads the behavioural contrast and MERGEs, ADOPTs one
or KEEPs_BASE; the composed harness is re-executed and that fresh run is the deliverable [paper §2.2.1; code
recursion.py]. With budget k this is either parallel (2k candidates, k independent composers, an anonymised
deliverable-only selector picks one of the k composed re-runs) or sequential (k chained checkpoints), at exactly
R = 4k rollouts and P = 3k sessions per task [paper §2.2.2; code ledger.py]. After each task (harnessing-level
recursion) a reflector reads the whole improvement record and, only when execution contrasts support it, writes one
lesson; the adopted harness and its EXPERIENCE.md enter a per-domain library that later tasks' editors and composers
read, `R_{t+1} = Update(R_t, H*_t, D_t)` [paper §2.3, Eq. 4]. Scores are used for evaluation only: "Nothing in the
method imports this module" [code judge.py]. Results: at k = 1 Harness² beats every baseline on WorkBuddy-Bench
average in all four executor configs, e.g. GLM-5/OpenCode 64.2 -> 75.7, and parallel k = 5 adds +2.9 to +3.9 more
[paper Table 1, §3.2]; each ablation at k = 1 costs 1.6-3.7 points (WB 77.5 vs w/o library 74.4, w/o scoping
74.6, w/o composition 75.9; JobBench 36.0 vs 34.2 / 32.3 / 34.1) [paper Table 3].

## 2. Inputs and setup

What the sources assume: a frozen executor M under a substrate (OpenCode or Codex) whose harness is 8 editable files
c1..c8 (system prompt, guardrails, memory, tool guidance, subagents, skills, scripts, plugin) [paper Table 5, App.
D.1]; agentic improver sessions (Claude Opus 4.8, high effort) that edit `context/harness/` in a scratch workspace
[paper §3.1, App. D.2]; benchmark adapters that strip criteria and answer keys from what any session sees [code
spec.BenchmarkAdapter]; a per-domain key (practice area, occupation, category) [code spec.domain_of]. What
`rsi.harness2` needs (rsi choices):
- **Domain** with `rollout` (execute without grading; `rsi.core.Domain` has it) and a seed harness `Artifact`. Every
  task starts from that seed. Optional hooks: `harness_brief()` (the harness interface without any grading
  sentence; becomes THE WORK slot), `validate_artifact(tree)`, `grader_verdict_markers()`, `domain_of(task)` (else
  `task.family`), `task_prompt(task)`, `task_source(task)`.
- **Stream**: `Config.split` (default: the sealed `holdout`, else `test`, else `ood`; `"holdout+ood"` joins splits),
  `order` (`sorted` grouped by domain, `shuffle` seeded per [code schedule.stream_order], `split`), `max_tasks`; or pass
  `tasks=[...]`. Labels may exist in the suite but the loop only receives open copies (no `target`, no answer-like
  meta keys).
- **Grader**: not used by the loop. It is only called by `posthoc_report` after the loop, if `report=True`.
- **Models**: `llm_task` = executor (required); `llm_propose` = editors and composers (default: `llm_task`, as in the
  code, where the improver defaults to the solver model); `llm_select=` selector and reflector (default
  `llm_propose`).
- **Budget**: `k` (default 1). Global stop rules between tasks: `budget=Budget(max_rounds=..., max_usd=...)` and
  `Config.max_usd`, which also stops when the next task would likely cross the cap.
- **Measured first**: nothing; there is no score in the loop and no noise band. Do measure the base rollout's
  quality offline if you can: Harness² cannot tell you whether it helped.

## 3. Round by round (one round = one task of the stream)

```text
library = {} per domain                                  # empty at the start of every stream [paper §3.1]
for task q_t in stream order (current task only; no replay, no look-ahead):         # [paper §2.1]
    B = [roll(H0) for i in 1..k]                         # base1..basek; none ok -> "base-failed", stop  [code schedule.run_task]
    parallel:  for i in 1..k, d in {INV, DOM}: cand = editor(d, evidence(q, H0, B, library))   # same evidence, i.i.d.
               roll each candidate once                                          # Probe [paper §2.2.1]
               for j in 1..k: agg_j = composer(evidence + all 2k candidates, diffs, rollouts, notes); roll(agg_j)
    sequential: cur = H0; for t in 1..k: inv_t, dom_t edit cur (see history); roll both;
               c_t = composer(history + round t); roll(c_t); cur = c_t                    # [paper §2.2.2, Eq. 3]
    H*_t = finals[0] if k == 1 else selector(anonymised deliverables of the k finals)     # [code selection.py]
    submit the deliverable of H*_t's re-execution                                          # never a base or candidate run
    D_t = all rollouts, candidates, diffs, notes, adoption; experience = reflector(D_t)    # no library  [paper §2.3]
    if experience has cue + lesson + evidence: library[domain] += (whole adopted tree, EXPERIENCE.md); rebuild INDEX.md
```

Stage details:
1. **Evidence is files** [paper App. H; code context.py]: `context/task.md`, `source/`, `rollouts/<label>.md` (full
   trajectories), `deliverables/`, `harness/` (the editing baseline), `pool/<label>/{harness/, harness.diff,
   rollout.md, produced.md}`, `decisions/<label>.md`, `library/` (only once non-empty). Editors never see other
   candidates of their round; composers see all candidates but not each other.
2. **Editors** reply with one `<notes>` block (each change tied to a trajectory moment) or KEEP_BASE; the harness on
   disk is the proposal (omitted files are inherited) [code recursion._run_stage]. Prompt order: DIVERGENCE, then a
   repeated concrete failure, then a visible risk; verified workspace facts are written as facts; library components
   are copied and re-verified, never invoked by path [paper App. H; spec §6.3].
3. **Validation before any rollout**; an invalid tree is retried once with the validator message, then the stage
   inherits its starting tree (`fell_back`), and that tree is still probed so R stays 4k [paper App. D.2; code
   STAGE_ATTEMPTS = 2].
4. **Composer**: MERGE (expected when an INV and a DOM edit each earn their place), ADOPT ONE WHOLESALE, or KEEP_BASE
   (sequential: keep the current checkpoint; also REPRODUCE an earlier checkpoint). "Every rule that bound the
   proposers binds you ... merging an edit launders it"; an edit tied to a rollout that produced nothing is UNSAFE
   [code prompts.aggregate]. The decision is the resulting tree, not a parsed label [code recursion._accept_tree].
5. **Selection** (k > 1): ids `A, B, ...` from a seeded shuffle; the selector sees `task.md`, `source/` and each
   candidate's complete deliverable, no harness, trajectory, diff, notes, library or score; criteria DELIVERY,
   CONFORMANCE, FIDELITY, INTERNAL CONSISTENCY, OPERATIONAL FITNESS, FINISH; output `<why>` + a ```` ```choice ````
   block; failure falls back to the first final [code selection.py; spec §6.7].
6. **Reflection**: edit rows (`helped | hurt | no observed effect | confounded`, prefer confounded when several
   components changed together or one rollout stands behind a difference) and at most one `<experience>` (cue,
   lesson, evidence, confidence) or NO_ENTRY. "Adoption alone never establishes that an edit helped or will
   transfer" [paper §2.3; code prompts.experience]. NO_ENTRY stores nothing, not even the harness [code library.add].

**What changes across rounds**: only the library. The next task starts from H0 again; earlier components are
"selectively reused or adapted through Propose, rather than automatically inherited" [paper §2.3 "Reuse"]. Model
weights, stage prompts and decision rules stay fixed. **Stopping rule**: the end of the stream; there is no
convergence test or score-based stop.

## 4. Sample by sample (inside one task)

- The unit of work is one task; its evidence is the k reference trajectories (read "as a set": a failure that
  repeats across the base set is a real property, a defect in 1/k is noise) and one rollout per candidate ("a single
  good or bad outcome is weak evidence either way") [code prompts.aggregate "HOW TO WEIGH THE EVIDENCE"].
- Every rollout of the task uses the same model, substrate and timeout, a fresh environment and a different harness
  only; rollouts never carry a score (`RunView` "never carries a score") [paper App. D.1; code spec.RunView].
- Behavioural contrast is not computed: it is laid out as files (candidate diff vs baseline, its rollout, the base
  rollouts, the editors' notes) and read by the composer; it "does not verify task correctness" [paper §2.2.1 Probe].
- Recorded per task: every stage's notes, tree, attempts, fallback; every rollout; the selection (pool, dropped
  non-delivering finals, anonymous ids, why); the reflection; the library entry. Final deliverable: the submitted
  re-execution. Benchmark evaluation: means over three runs, each a shuffled task order [paper §3.1].

## 5. Paper defaults vs this package

| setting | source value [cite] | rsi `Config` field = default | note |
|---|---|---|---|
| budget | k = 1 main tables; k = 5 "+ Recursion"; study k = 1..5 [paper §3.1, §4] | `k=1` | R = 4k, P = 3k enforced (`TaskBudget`) |
| mode | parallel and sequential; CLI default parallel [code cli/_common.py] | `mode="parallel"` | `"sequential"` = chained checkpoints |
| stage retries | retry once, then inherit [paper App. D.2]; `STAGE_ATTEMPTS = 2`, `TRANSPORT_ATTEMPTS = 2` | `retry_invalid=1`; transport 2 per session | no back-off sleep |
| selector | none at k = 1; one anonymised session at k > 1; 2 transport attempts | `selector_attempts=2` | fallback = first delivering final |
| library | per domain, empty per stream, separate per mode [code library.py] | `library=True`, `library_dir=None` -> `<out>/library/<agg|seq>/<domain>` | `False` = the "w/o library" ablation |
| stream order | sorted, or seeded shuffles per domain [code schedule.stream_order] | `order="sorted"`, `seed=0` | `"shuffle"` for the paper's runs |
| always-on cap | 24,000 chars OpenCode, 8,000 Codex [code OPENCODE_SURFACE] | `max_text_chars=24000` | applies to all non-code text |
| observation clip | 50,000 per observation, min 10,000 [code context.py] | `obs_trunc=50000` | per rendered file here |
| improver model | Claude Opus 4.8, high effort [paper §3.1]; code default = solver model | `llm_propose` (default `llm_task`) | |
| rollout caps | 200 turns, 3,600 s [paper App. D.1] | the domain's (AgentQA: 12 model calls, 8 tool calls, 60 s own time) | |
| prompt slots | `BenchPrompts` validated against measurement words [code spec.BenchPrompts] | `prompts={}`; `work` defaults to `domain.harness_brief()` | same validation |
| targets in tasks | adapter strips criteria / answer keys [code spec.BenchmarkAdapter] | `strip_targets=True` | |
| evaluation | judge after selection [code judge.py] | `report=True` (`posthoc_report`) | outside the loop |
| global stop | not specified | `max_tasks=None`, `max_usd=None`, `budget=` | checked between tasks |

## 6. Where our implementation differs

- **Sessions are single text completions**, not agentic file-tool sessions: the `context/` tree is rendered as
  `=== context/<path> ===` blocks and the reply carries `=== FILE: <path> ===` blocks with complete contents (or
  `ADOPT: <label>` / `REPRODUCE: <label>`, or no block = KEEP_BASE). The mission says "you have no file tools".
- **Generic surface and validator** instead of the 8 OpenCode/Codex components: code files compile, no template
  braces, keep every `{placeholder}` and add none, every new file must be named by some code file (the trigger-line
  analogue; rejected, not auto-repaired), text cap, plus `domain.validate_artifact`. The surface block maps the
  levers (skill -> on-demand prompt file, script -> deterministic helper, subagent -> separate model call, plugin ->
  check in the control flow).
- **A stronger guard**: inside the loop the domain's `grade`/`run`/`grade_execution`/`score` raise; tasks reach the
  executor without targets; every improver prompt, proposed tree, experience and library entry is screened for the
  spec's seven verdict markers plus rsi's graded-record keys and the domain's own grader phrases (`GraderLeak`, a
  `BaseException`). No `leak_globs` (tasks are not directories).
- **Serial** stages and rollouts (the code runs two at a time); no in-method session cache (use `cache_dir`); no
  resume.
- **Ported**: parallel and sequential recursion, budget identity, retry-then-inherit, KEEP_BASE semantics per mode,
  anonymised finals-only selector with the reference's seeded ids, reflector with NO_ENTRY, library with INDEX.md and
  EXPERIENCE.md, per-domain and per-mode separation, stream orders, the adapt-pack prompts (substrate text replaced).
  **Not ported**: the `tts` prompt pack, `autotrigger`, the OpenCode/Codex substrates and benchmark adapters, the
  w/o-scoping and w/o-composition ablations (not in the reference core either [spec §10.6]), and the ReasoningBank /
  Harness Scaling / Meta-Harness baselines; Best-of-k is `rsi.harness2.best_of_n` with the same selector.
- **Offline evidence only** (`experiments/harness2/compare_sim.py`, AgentQA, 43-task holdout+ood stream, 3
  shuffled seeds, `SimModel` + `MockImprover`): seed 0.488; Harness² k = 1 0.597 (+0.109 [+0.008, +0.209] paired vs
  seed); k = 3 0.643 (+0.155 [+0.054, +0.256]); Best-of-4 and Best-of-12 with the same selector no better than seed;
  the library ablation has no effect offline because the mock editors find the same edits every task. The mocks know
  the simulator's levers, so this checks the wiring, not live performance. No paper number is reproduced here.

## 7. Applying it with rsi

```python
import rsi
from rsi.harness2 import Config
res = rsi.improve(dom, method="harness2", llm_task="claude:haiku", llm_propose="claude:haiku",
                  config=Config(k=1, split="test", order="shuffle", seed=0, max_usd=10.0,
                                prompts={"work": "Answer short questions with one exact value."}),
                  out_dir="runs/h2", cache_dir=".rsi_cache/h2")
res.meta["deliverables"]     # what to ship: one submitted output per task
res.meta["report"]           # only if the domain has a grader: base vs submitted, paired bootstrap CI
```
Offline: `rsi improve --method harness2 --problem agentqa --llm sim --out runs/h2 --set k=1 max_tasks=10`
(`MockImprover`), or `python experiments/harness2/compare_sim.py`. `rsi recommend --no-grader` names it. Use it when
tasks are open-ended and arrive one at a time; with a reliable grader, a method that selects on the grade (RRSI,
GEPA) uses more information. `res.best` is H0; do not run `transfer_report` on it.

**Audit with `python -m rsi.cli inspect runs/h2`** (`TRACE.md`, `trace.jsonl`, `ledger.jsonl`, `tasks/*.json`,
`library/`):
- `round_start`: task, domain, the library entries visible (only earlier tasks of this domain).
- `rollout`: label, harness id, seed, delivered, output head. Check 4k per task.
- `proposal`: per editor/composer, the exact prompt, the reply, the actual diff, kept_base/fell_back/attempts. Check
  that each change cites a trajectory moment and that fallen-back stages are rare.
- `selection` / `decision`: the anonymous pool, the choice and why, the adopted harness. `reflection` / `library`:
  the edit rows, the lesson or NO_ENTRY, whether an entry was stored.
- `report` (last, after the loop): the only scores. Nothing before it carries a score or a target.

**Method-specific failure modes**:
- **No correctness signal**: the selector judges deliverables by reading them; with near-identical outputs it is
  close to random, and it can prefer a weaker one (the paper's nine regressions [paper E.6]). Offline Best-of-n with
  the same selector did not beat one H0 draw.
- **Shared premise**: candidates and composer share model biases; agreement is support, not proof [paper App. H].
- **Leak tripwire stops the run**: any grader phrase or `"score":` key in a trajectory raises `GraderLeak`; fix the
  domain or its markers, never widen the guard.
- **Prompt growth**: single-shot sessions carry the whole evidence and library; watch improver tokens per task in
  `res.usage` and cap spend with `max_usd`.
- **Library poisoning**: a lesson resting on one rollout is a guess; read EXPERIENCE.md verdicts (`confounded`
  should dominate when several components changed).
