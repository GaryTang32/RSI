# SoL-Pi: end-to-end methodology (for `rsi.improve(..., method="solpi")`)

Sources: the paper (Liu, Ye et al., arXiv 2609.20519v1, 17 Sep 2026), cited as `[paper §x]`, `[paper Fig. n]` or
`[paper Table n]`; it has no algorithm box, no equations and no appendix. Our package: `rsi/solpi/{driver,research,
gate}.py` and `docs/methods/metaharness-solpi/` (paper-spec Part B, implementation.md, claims-audit-solpi.md).
Facts found only elsewhere keep their tags: `[blog:<section>]` (authors' project page), `[code:SoL-Pi/...]` (NVlabs
release). The paper publishes no prompts, numeric thresholds, sample sizes or orchestration code: where it is
silent we write "not specified in the paper"; our own choices are marked "rsi choice".

## 1. In one paragraph

Long-running coding agents spend many tokens on overhead: an edit followed by a separate run command, large tool
outputs re-sent on later requests, a growing context, long build/test logs read by the frontier model [paper §1,
§2.4]. SoL-Pi uses auto-research loops to find mechanisms for the open harness Pi that cut tokens and cost while
preserving the agent's ability to complete the work [paper §2.1]. It does NOT optimise task score: acceptance is two
sequential gates, every capability metric within its predeclared tolerance AND at least one declared efficiency
metric improved [paper §2.1]; token efficiency is reported as "API cost per unit of aggregate task score" [paper §3].
The search is a broad-to-deep funnel: an outer stage over 152 directions in six families, an inner stage that
develops each selected direction in an isolated, disposable lineage [paper §1, §2.2], on 535 environments in two
families [paper §2.3]; a frozen candidate is then checked once on 11 held-out EdgeBench tasks, never feeding back
[paper §2.1, §2.5]. Four of 152 directions survived (about 1 in 38): Action Fusion, Online Context Compact,
ObservationPack, Evidence-Preserving Reducer [paper §2.4]. On the 51 public EdgeBench tasks the complete stack used
49.0% (GPT-5.6 Sol) / 44.7% (Opus 5) fewer tokens and 33.2% / 33.5% lower cost, at 93.7% / 94.3% of Pi's average
score [paper §3.1, Tables 1-2].

## 2. Inputs and setup

Before round 1 you need (rsi names in brackets):
- **Artifact**: the base harness the mechanisms attach to (paper: Pi; all four mechanisms are Pi extensions
  [paper §2.5]; the model stays fixed [paper §4.1]). Capability metrics, tolerances and efficiency metrics are fixed
  before experimentation and isolated from the optimizing agent's control [paper §2.1]; mechanism source,
  configuration, metrics, tolerances and acceptance rule are frozen before held-out evaluation [paper §2.5]. The
  research procedure is NOT frozen: each experiment copies a shared skill template and may modify its orchestration
  code, which is discarded afterwards [paper §2.2]. [`seed_artifact`]
- **Idea pool**: one-line directions in six families: context, progress, tools, delegation, prompt and policy,
  improvement and evaluation (paper: 152) [paper §2.2]. Families record a hypothesis's origin, not where the change
  lands [paper §2.2]. Letter codes C/P/T/D/R/M are an rsi choice. [`ideas=[Idea(id, family, title, mechanism, grid,
  oracle)]`; required for any domain except built-in `agentworld`/`agentqa`]
- **Environments in several families**, each with an executable verifier [paper §2.3]: 495 repository tasks from
  GitHub issue-PR pairs (PR and regression test hidden; kept only if the test fails before the patch and passes
  after) plus 40 verifier-first synthetic tasks, mostly with a Terminal-Bench-2-style verifier interface. Splits:
  [`evolve` = training screen; `holdout` = firewall acceptance set; `ood` = final].
- **Metrics predeclared before search** [paper §2.1]. The capability metrics and all tolerance values are not
  specified in the paper. It reports token traffic (input, cache read, cache write, output), token cost at API prices
  of 17 Aug 2026, average score and $/score [paper §3, Table 1]. The ObservationPack sweep gates (10% bill saving,
  -2% quality) are in the blog only. [`Config.gate = GateSpec(...)`, digest recorded; the domain must return
  `Execution.tokens`, `cost_usd`, `steps`: if they are 0, no efficiency gain is seen and nothing is accepted]
- **Models**: the harness was optimised only on GPT-5.6 Sol trajectories (search backend; xhigh per Fig. 7), then
  applied to Opus 5 "without further search or adaptation" [paper §3.1, §3.4, Table 2]; EPR's auxiliary model is
  GPT-5.6 Luna at high [paper §2.5]; the research AI's model is not specified in the paper. rsi: `llm_task` (frozen
  agent), `llm_propose` (or your own `proposer=`).
- **Measured first**: "Before rollout budgets are assigned, Oracle Analysis examines existing development
  trajectories to identify avoidable work in the base harness" [paper §2.2]; e.g. Action Fusion's oracle measured
  missed adjacent actions ("12.3% direct headroom") and projected an 11.5% token saving under full triggering
  [paper Fig. 8, §3.5]. rsi (every driver round): the base on the screen = all `evolve` tasks x `k` seeds ->
  `Metrics` (aggregate and per family: score, solved, tokens, cost, steps, eta = cost/score), then the oracle on those
  trials via `Trial.meta["oracle"]` (your domain must supply it, else every idea scores 0.05 and ranking is by id).

## 3. Round by round

The paper has no single "round": an outer stage over the idea pool, and inner lineages that "propose a change,
implement it, run a fixed experiment, inspect the result, and retain, revise, or discard the candidate" [paper §2.2].
rsi "driver round" = one pass of both. (The blog's step numbers 01-06 are not the paper's; Fig. 8's 01-04 are the
phases of the Action Fusion lineage.)

**One research pass** (`AutoResearchDriver.run_round`):
1. Oracle Analysis, then "selected" directions get rollout budget; each must name a concrete source of overhead and
   a harness change [paper §2.2]. Selection rule and count: not specified in the paper (rsi choice: top `n_lineages`
   by oracle estimate).
2. One isolated, disposable lineage per selected direction; failures do not couple across lineages [paper §1, §2.2].
   Concurrency is not specified in the paper; rsi runs lineages sequentially (`workers` only threads rollouts).
3. A lineage freezes its candidate [paper Fig. 2]. The paper retains "the nondominated results" among gate-passing
   candidates [paper §2.1]; rsi choice: at most one frozen candidate per lineage.
4. **Held-out firewall**: evaluation "occurs only after the candidate is frozen"; held-out results "never feed back
   into the Auto-Research Loops: a failed validation rejects the candidate without triggering further optimization"
   [paper §2.1, Fig. 2]. The paper uses 11 of EdgeBench's 51 public tasks for this one-way acceptance [paper §2.5].
   The pass criterion is not specified in the paper; rsi: the same dual gate against the base's `holdout` metrics,
   one evaluation per frozen candidate. Only a bool reaches selection; details go to a write-only sink (and to
   `res.meta` after search ends).
5. **Integrate**: retained mechanisms are combined into SoL-Pi and its "hyperparameters and implementation" refined
   "while preserving capability"; the combined harness is evaluated end to end, then frozen [paper §2.1, §2.4,
   Fig. 1]. rsi: compose survivors onto the base (JSON union, else 3-way merge; conflicting edits dropped and listed
   in `conflicts`), re-measured on the screen only; no refinement, no re-gating unless `validate_composition=True`.
6. **Recursion** (SoL-Pi as the starting harness of the next cycle) is "a long-term research vision rather than a
   compounding effect demonstrated" [paper §5.1]. rsi: if `Config.rounds > 1` the composed stack is the next base
   (stops when a pass has no survivors); untested.

**One lineage iteration** (`Lineage._run`; order from [paper Fig. 2]: Trajectory Rollouts -> Map-Reduce Analysis ->
Mechanism Proposal -> Candidate Implementation -> Independent Review -> Development Validation, with "revise" /
"next iteration" loops, then FREEZE -> Held-Out Evaluation):
1. **Rollouts** of the current variant: "each iteration may yield multiple exploration trajectories" [paper §2.2];
   sample size not specified in the paper. rsi: `rollout_tasks_per_family` tasks per family, 1 seed; current = base
   on iteration 0, then the last variant that reached validation.
2. **Map-reduce analysis**: "independent analyzers examine one trajectory each for repeated actions, context growth,
   large observations, or sparse diagnostic signals"; a reducer combines them into "a candidate-level summary that
   guides the next proposal" [paper §2.2].
3. **Proposal** of one mechanism for this lineage's direction; trajectories "guide mechanism proposals and
   implementation" [paper Fig. 2]; other proposer inputs not specified in the paper. rsi: idea + base artifact
   (edits are relative to the base) + evidence + this lineage's own past rows; never other lineages or sealed splits.
4. **Implementation (Ralph Loop)**: "the implementer refines the candidate to meet an explicit completion criterion"
   [paper §2.2]. rsi: up to `ralph_max` repairs; exit check = `domain.smoke` (one task runs without error).
5. **Independent review** "before evaluation. Failed reviews trigger revision" [paper §2.2]; what it checks is not
   specified in the paper ("behavioural contract" is the blog's term). rsi: `SmokeReviewer` (smoke + no held-out
   names in added lines) or `LLMReviewer`; a rejection returns to step 4 with the objections, at most `review_max`
   times, then the iteration ends.
6. **Development validation** on development tasks [paper Fig. 2], then the two gates. In Fig. 8 "some steps evaluate
   multiple candidates in parallel, and only the best result from each parallel batch is retained". rsi: one
   candidate on the FULL screen (`evolve` x `k` seeds), then the dual gate.
7. **Keep rule** [paper §2.1]: "every capability metric must remain within its predeclared tolerance, and the
   candidate must improve at least one declared efficiency metric. Among candidates that pass both gates, the
   pipeline retains the nondominated results under the declared metrics." As a formula (ours; the paper has none),
   with `m_j` capability metrics, `tau_j` tolerances, `e_k` efficiency metrics:
   `accept(c) <=> for all j: m_j(c) within tau_j of m_j(b)  AND  exists k: e_k(c) better than e_k(b)`.
   rsi (relative mode): `m_j(c) >= m_j(b) * (1 - tol_j)` for score AND solved rate (2% each), and tokens or cost
   saves more than `min_gain` = 2% relative to base; with `sweep=True` the lineage keeps the lowest-eta passer among
   the nondominated (score up, tokens and cost down).
8. On a gate failure the paper may "retain, revise, or discard" [paper §2.2]; rsi returns to step 1 with this
   candidate as current. On a pass, `sweep=True` continues through the variant grid and freezes the best passer at
   the end; `sweep=False` freezes the first pass. In the Action Fusion lineage the agent also introduced trigger rate
   as an intermediate acceptance metric next to task score [paper §3.5]; rsi has no intermediate metrics.

```
base_m = metrics(evaluate(base, evolve, k)); est = {i: oracle(i, base_trials)}
frozen = []
for idea in top(n_lineages, est):                        # paper: isolated, disposable lineages
    cur, passing = base, []
    for it in range(max_iters):
        ev = reduce(analyze(rollouts(cur, 2 tasks/family, seed 0)))      # rollouts, map-reduce
        prop = implement_until_smoke_passes(propose(idea, base, ev, history), ralph_max)   # propose, Ralph
        for _ in range(review_max):                                       # independent review
            if review(prop).ok: break
            prop = implement_until_smoke_passes(fix(prop, objection))
        if not review.ok: continue
        m = metrics(evaluate(prop, evolve, k)); g = dual_gate(base_m, m)  # development validation
        if g.accept: passing.append(prop); (if not sweep: break)
        cur = prop
    if passing: frozen.append(min(nondominated(passing), key=eta))
survivors = [f for f in frozen if dual_gate(base_hold, metrics(evaluate(f, holdout, k))).accept]  # once each
best = compose(base, survivors)            # = res.best; 'ood' is never read by the loop
```

**What changes across rounds.** No annealing, schedule or temperature. The base metrics and the gate (digest-frozen,
as [paper §2.1] requires) never change within a pass. Per lineage only its own history grows; lineages share
nothing. rsi choice: the gate always compares with the ORIGINAL base, not the previous iteration (the paper's
comparison baseline is not specified). Mechanisms are explored and gated independently, so integration is a separate
step [paper §2.1]; the blog warns "the small losses it permits can accumulate once mechanisms combine"
[blog:Capability floors].

**Stopping rule.** Paper: each lineage "runs to completion" [paper §2.2]; its budget is not specified in the paper.
The Action Fusion lineage ran 27 iterations: oracle 1, baseline 6, prompt optimisation 18 (10 search batches), final
validation 2 [paper Fig. 8, §3.5]. "Stuck in a local basin after roughly five to ten iterations" is the blog's
[blog:Breadth escapes local search basins], not the paper's. rsi, per lineage: `max_iters` (default 4), proposer
exhausted (variant grid used up), or first freeze if `sweep=False`; per run: `rounds` passes or a pass with no
survivors. `stop_reason` is always `"rounds"`.

## 4. Sample by sample

- **Unit**: one task (one environment instance). Trials per task and seeds: not specified in the paper; search scale
  was ~150 directions, ~500 environments, >3,000 runs, >60,000 agent-environment interactions [paper §1]. rsi: `k`
  trials per task, seeds `0..k-1` (default `k=1`: ONE seed, no noise band). Analysis rollouts: multiple
  trajectories per iteration [paper §2.2]; rsi: first 2 tasks per family, 1 seed. Gate evidence (rsi): all `evolve`
  x `k`.
- **Recorded**: token traffic split into input, cache read, cache write and output; token cost at fixed API prices;
  average score; $/score [paper Table 1]; per mechanism, trigger rate (share of tasks where it fires) and trigger
  intensity (activations per triggered task) [paper §3.4]. rsi per trial: score, `solved` (= score >= 1), `tokens`,
  `cost_usd`, `steps`, trace text, feedback, `meta["oracle"]`; `agentworld` simulates the prefix cache (`TokenMeter`).
- **Aggregation**: the paper reports average score and totals over the task set; $/score = cost per task / average
  score (e.g. $1,339 / 51 / 44.833 = 0.5855) [paper §3, Table 1]. How the gate aggregates across environments is not
  specified in the paper beyond improving efficiency "beyond a single development environment" [paper §2.1]. rsi:
  mean over ALL trial rows (a family with more tasks weighs more), aggregate and per family; eta = mean cost / mean
  score. The gate uses the aggregate (`mode="aggregate"`, default) or also each family (`mode="per_family"`, the
  "survives everywhere" reading, rsi choice). Missing predeclared metric = fail closed. Infra errors count as
  missing and use the failure score.
- **Inside one trajectory** [paper §2.4, §2.5, Fig. 4]: Action Fusion adds an optional follow-up command to
  file-mutation tools (`then_run`, Fig. 4a; 3 API calls -> 2); commands that must inspect the mutation result stay
  separate (hash guard and markers: [code]). Evidence-Preserving Reducer takes build/test logs >= 4 KiB from a
  predefined command set (file reads and searches bypass it), archives the exact log, has a cheaper model write a
  receipt, verifies schema, source hash, exit status, exact quotes and size, and falls back to the original on
  failure, suspected credentials or no size reduction; it runs before ObservationPack, which skips receipts.
  ObservationPack archives results > 10 KiB, sends them in full for the next two provider requests, then a stable
  handle, original size and a 1 KB excerpt of complete head/tail lines; exact pages are recallable. Online Context
  Compact acts at plan-step completion (`update_plan`): it estimates remaining requests (capped by the window-fill
  horizon) and compacts when projected input savings beat the cache-rewrite cost, with a larger margin for later
  compactions, and also near the window limit; the summarisation call is not priced (the formula `W*(rho-1)` is the
  code's, spec §B4.4).
- **Splits**: EdgeBench is kept out of search entirely; 11 of its 51 public tasks are for one-way acceptance of frozen
  candidates and 40 for final evaluation only [paper §2.5]; the 51-task headline includes the 11 [paper §3.1]. rsi:
  `evolve` read by rollouts, validation, base and composed metrics; `holdout` read ONLY by the firewall (one
  evaluation per frozen candidate plus the base); `ood`, `test` never read by the loop (the `Evaluator` raises on
  sealed splits). Check them yourself with `rsi.transfer_report`.

## 5. Paper defaults vs this package

| Setting | Paper value [cite] | rsi field and default | Note |
|---|---|---|---|
| Idea pool size | 152 in 6 families [paper §2.2] | `ideas=` (no default for new domains); agentworld 12 | you supply it |
| Ideas explored | "selected"; count and rule not specified [paper §2.2] | `n_lineages=10` | oracle ranks, top n get lineages |
| Lineage depth | runs to completion; AF: 27 iters, 10 search batches [paper Fig. 8] | `max_iters=4` | blog: basin after ~5-10 iters |
| Candidates per step | sometimes several in parallel, best kept [paper Fig. 8] | 1 per iteration | rsi choice |
| Ralph loop | until an explicit completion criterion [paper §2.2] | `ralph_max=3` | exit check = `domain.smoke` |
| Review | independent; failures trigger revision; count not specified [paper §2.2] | `review_max=2` | the cap is rsi's |
| Rollout sample | not specified | `rollout_tasks_per_family=2` | 1 seed |
| Screen trials | not specified | `k=1`; `screen_split="evolve"` | no noise band |
| Capability metrics | predeclared; list and tolerances not published [paper §2.1] | `GateSpec.capability=(("score",0.02),("solved",0.02))` | rsi choice |
| Efficiency metrics | declared list not published; reports tokens, cost, $/score [paper §3] | `efficiency=("tokens","cost")`, `min_gain=0.02` | "better" = >2% relative saving |
| Gate scope | "beyond a single development environment" [paper §2.1]; aggregation not specified | `mode="aggregate"` | `"per_family"`, `"efficiency_only"`, `"eta_better"` |
| Nondominated keep | yes, among gate passers [paper §2.1] | `sweep=True` | best-eta passer frozen |
| Held-out check | after freeze, no feedback; 11 EdgeBench tasks [paper §2.1, §2.5] | `firewall=True`, `holdout_split="holdout"` | silently OFF if no `holdout` split |
| Integration | combine, refine while preserving capability [paper §2.1] | `compose=True`, `validate_composition=False` | no refinement step |
| Recursion | future work [paper §5.1] | `rounds=1` | untested; lineages sequential |
| OCC cache write/read ratio | used, value not given [paper §2.5] | mechanism param `cache_write_read_ratio=12.5` | 12.5 from [code:agents-install.md] |
| ObservationPack | 10 KiB, 2 full sends, 1 KB excerpt [paper §2.4, Fig. 4c] | grid `full_sends`, `excerpt_bytes` (2 / 1024 = 512+512) | blog's V2 is 2048/1536 (spec §B4.3) |
| EPR | logs >= 4 KiB; GPT-5.6 Luna (high) [paper §2.4, §2.5] | mechanism param `min_bytes=4096` | offline `DeterministicReducer` |

## 6. Where our implementation differs

From `implementation.md` §5/§11/§12 and `claims-audit-solpi.md` (61 claims after retry round 2: 31 reproduced,
11 partial, 1 not reproduced, 18 not testable here, 0 contradicted; the audit predates access to the paper text, so
many claim sources are the blog or secondary summaries):
- **Reconstructed protocol.** The paper gives the stage order and the gate rule but no prompts, thresholds, sample
  sizes or orchestration code. rsi choices: roles, 2% tolerance, 2% min gain, the two-metric floor (score AND
  solved), the review cap, one candidate per iteration, one frozen candidate per lineage, comparison with the
  original base, no intermediate metrics, sequential lineages.
- **Integration.** The paper refines the combined stack while preserving capability [paper §2.1]; rsi only merges by
  default (`validate_composition=True` re-gates the stack and drops survivors greedily).
- **Scale.** 5 simulated families, 12 ideas, deterministic mock agents, invented prices with rho = 12.5.
- **Mechanisms are faithful ports** (14/14 reproduced; OCC economics vectors pass exactly, EPR never admits a
  non-verbatim quote). Not ported: Pi's split-turn compaction path, OCC session restore.
- Audit verdicts that change how you use it:
  - **L2 "survive everywhere" -> NOT REPRODUCED.** More training families reject more tricks at the gate (3.0 -> 1.6
    admitted) but held-out transfer is not better than a single-family dual gate. What did the work was the
    capability floor plus the one-way firewall. Do not expect diversity alone to protect you.
  - **L4 floor blocks "doing less" -> PARTIAL.** The two-metric floor rejects both do-less shortcuts, but a trick
    lossless alone (tail-trim@120) passed and cost 3.8 points once composed. With 8 screen tasks per family the
    floor admitted a 24-turn cap in 2/20 seeds and tail-trim in 13/20; a 3x screen cut failures to 1/20.
  - **R7 composition -> PARTIAL**: gating one at a time let a composed loss through.
  - **Q1/Q2 headline savings -> PARTIAL**: tokens -54..-69% (larger than the paper's), cost -16..-61% (S7 A default
    -19.6% after R2-F1); the stack the protocol composed saved only 8.9%. On backend A, EPR alone was cheaper,
    whereas the paper's full stack is cheapest on both backends [paper §3.4, Table 4].
  - **Q3 "93.7% / 94.3% of Pi's score" -> NOT TESTABLE** (needs ~100-400 frontier-agent task pairs).
  - **Q13 ObservationPack 10% bill gate (blog only) -> NOT TESTABLE**: in our sims replayed reads are <=6% of the
    bill; the paper's add-one ObservationPack row saves only 5.1% under GPT-5.6 Sol ($1,339 -> $1,271), though
    32.5% under Opus 5 ($1,741 -> $1,176) [paper Table 4].
- Paper caveats: 93.7-94.3% of Pi's score, not 100% [paper Tables 1-2]; Terminal-Bench 4 solved 15 vs 18 for Pi and
  Codex [paper Table 3]; "SoL-Pi [Performance]" is the single mechanism with the highest EdgeBench score per backend,
  i.e. chosen on the evaluation set [paper §3.1]; Opus 5 transfer is "preliminary evidence" [paper §3.1]; the search
  counts "do not establish a scaling law" [paper §2.2]. That humans set priors, filtered ideas and cleaned survivor
  code is from [blog:Humans set priors]; the paper does not say so.

## 7. Applying it with rsi

```python
import rsi
from rsi.solpi import Idea, GateSpec
res = rsi.improve(dom, seed, method="solpi", llm_task=llm, llm_propose="claude:sonnet",
                  ideas=[Idea("C1", "C", "Stop replaying large successful outputs; keep them recallable"),
                         Idea("P2", "P", "Fuse an edit with its verifier command")],
                  config={"gate": GateSpec(capability=(("score", 0.02), ("solved", 0.02)),
                                           efficiency=("tokens", "cost")),
                          "n_lineages": 6, "max_iters": 4, "k": 2, "validate_composition": True,
                          "shadow_monitor": True},
                  out_dir="runs/solpi", cache_dir=".rsi_cache/solpi")
res.best                              # composed stack of firewall survivors (== seed if none)
res.meta["rounds"][0]["heldout_passed"], res.meta["rounds"][0]["survivor_ideas"]
rsi.transfer_report(dom, llm, {"seed": res.baseline, "solpi": res.best}, k=2)   # holdout + ood
```
Needs: `evolve` with several `family` values, a sealed `holdout` (else the firewall is silently off and nothing
guards against tricks), ideally `ood`; token/cost metering in `Execution`; `Trial.meta["oracle"]` statistics if you
want oracle ranking. Measure the noise band first (SKILL.md step 3) and set `k >= 2`: the gate has no noise band, so
a 2% tolerance can sit inside run-to-run noise. `validate_composition=True` is the closest rsi gets to the paper's
"refine while preserving capability" integration step.

**Audit with TRACE.md** (`python -m rsi.cli inspect runs/solpi`): `round_start` (base, ranked oracle and chosen
ideas), per-lineage-iteration `rollouts` + map-reduce evidence, each `proposal` (diff, Ralph errors), the `critic`
(review verdict), `gate` arithmetic (capability base/cand/tol/pass, efficiency saving/improved, spec digest:
recompute it), `decision`, then the "07 held-out firewall + composition" round (frozen, passed, survivors,
conflicts, dropped, base vs composed screen metrics). Also `rounds.json` and `firewall_r1/heldout.jsonl`.

**Failure modes to watch**
- Savings from doing less: tokens fall because the agent stops early; read `solved` and per-family scores, not only
  the aggregate; use `per_family` or a stricter `solved` tolerance.
- Task-specific tricks (truncated logs, hard-coded commands): check the diff for strings tied to your tasks and
  confirm the holdout rejected them; a trick that is lossless on the screen will pass the gate.
- Composition loss: the composed stack was never run on `holdout`; run `transfer_report` and consider
  `validate_composition=True`.
- Zero tokens/cost in `Execution`, no `holdout` split, equal oracle estimates (arbitrary ranking), or a small
  screen (8 tasks per family was not enough in our sims). No survivors is normal (paper: 4 of 152); `res.best` is
  then the seed. Mechanisms fire less on a backend they were not searched on [paper §3.4, Fig. 6]: test on the model
  you deploy. Do not tune the gate on holdout results between runs without refreshing the split.
