# SoL-Pi: end-to-end methodology (for `rsi.improve(..., method="solpi")`)

Sources, in order: `docs/methods/metaharness-solpi/paper-spec.md` Part B (spec), then `README.md`,
`implementation.md`, `claims-audit-solpi.md`, and `rsi/solpi/{driver,research,gate}.py`. Read this first:
**the SoL-Pi paper text (arXiv 2609.20519) was never read by the spec authors** (arXiv was blocked). Every
paper-level fact carries a secondary tag: `[blog:<section>]` (authors' project page), `[ye-blog]` (co-first
author), `[sec:<id>]` (third-party summaries, unverified), `[code:SoL-Pi/...]` (the NVlabs release). The
research orchestration and prompts are NOT public, so the rsi protocol below is a reconstruction (spec §B3.1,
marked `[inferred]` there). Anything the spec does not state is "not specified in the paper".

## 1. In one paragraph

When coding agents run for hours, most tokens go to repeated work: predictable test runs after an edit, large
outputs replayed every request, finished subtasks kept in context, long logs read by an expensive model
[code:README "Introduction"]. SoL-Pi (Liu, Ye et al., 2026) uses auto-research loops to find harness mechanisms
for the small open harness Pi that cut tokens and cost without making the agent do less. It does NOT optimise
task score. It optimises efficiency under a predeclared capability floor: "reductions in cost or token use subject
to a predeclared capability-preservation criterion" [blog:Method]; co-author wording: "API cost per unit of task
score, subject to a capability floor" [ye-blog]. Breadth (152 ideas, spec §B3.1), depth (one disposable lineage
per idea), a dual gate on 535 training environments in two families, then one frozen-candidate check on an
isolated held-out benchmark (EdgeBench). About 1 idea in 40 survived; four did (Action Fusion, Online Context
Compact, ObservationPack, Evidence-Preserving Reducer) [blog:Breadth escapes local search basins]. Reported:
44.7-49.0% fewer tokens, about one third lower cost, about 94% of Pi's score on 51 EdgeBench tasks [ye-blog].

## 2. Inputs and setup

Before round 1 you need (rsi names in brackets):
- **Artifact**: the base harness the mechanisms attach to (paper: unmodified Pi, "No Pi patches", extensions
  only [code:README]). Frozen: model weights, base harness source, the research procedure, the metrics and
  tolerances, the held-out tasks/verifiers [blog:Method][sec:pengqian Q5.1]. [`seed_artifact`]
- **Idea pool**: a list of one-line directions in six families C Context, P Progress, T Tools, D Delegation,
  R Prompt&policy, M Improvement&eval (paper: 152). [`ideas=[Idea(id, family, title, mechanism, grid, oracle)]`;
  required for any domain except built-in `agentworld`/`agentqa`]
- **Environments in several families**, each with an executable verifier [blog:Two environment families]
  (paper: 495 issue-to-fix tasks kept only if the test fails before the fix and passes after, plus 40
  verifier-first tasks). Splits: [`evolve` = training screen; `holdout` = firewall acceptance set; `ood` = final].
- **Metrics predeclared before search** (paper: capability metrics with tolerances; efficiency metrics such as
  bill, tokens, turns, cost per response; no numeric tolerance published, spec §B4.1). Only the ObservationPack
  sweep gates are known: 10% bill saving, -2% quality. [`Config.gate = GateSpec(...)`, digest recorded]
- **Cost metering**: the domain must return `Execution.tokens`, `cost_usd`, `steps`, and score in `[0,1]`. If
  tokens and cost are 0 for everyone, no efficiency gain can ever be seen and nothing is accepted.
- **Models**: paper searched with GPT-5.6 Sol xhigh and applied the stack to Opus 5 with no further search
  [blog:Results][ye-blog]. rsi: `llm_task` (the frozen agent) and `llm_propose` (implementer/proposer LLM, or
  `proposer=` for your own).
- **Measured first** (every driver round): the base artifact on the screen = all `evolve` tasks x `k` seeds ->
  `Metrics` (aggregate and per family: score, solved, tokens, cost, steps, eta = cost/score), then the Oracle
  Analysis on those trajectories. The paper's Oracle Analysis "estimates opportunity from existing
  trajectories" before rollout budget is spent, e.g. 12.3% adjacent edit-then-command transitions
  [blog:fig then-run-research-overview]. rsi reads `Trial.meta["oracle"]` (your domain must supply it, else every
  idea scores 0.05 and ranking is by id).

## 3. Round by round

SoL-Pi has no single "round" in the paper. Two levels: a **lineage iteration** (paper stages 01-06) and the
**research pass** (breadth, lineages in parallel, held-out, composition). rsi "driver round" = one research pass.

**One research pass** (`AutoResearchDriver.run_round`; spec §B3.1):
1. Evaluate base on the screen; compute oracle estimate per idea; keep the top `n_lineages` ideas (paper: ideas
   "selected", count and rule not specified).
2. Run one `Lineage` per chosen idea (paper: parallel; rsi: sequential, `workers` only threads rollouts).
3. Each lineage returns at most one frozen candidate (see below).
4. **Held-out firewall**: each frozen candidate is run once on `holdout` and gated against the base's holdout
   metrics with the same dual gate. A failure rejects the candidate; the result goes to a write-only sink and
   only a bool returns [blog:Method: "A failed held-out evaluation rejects the frozen candidate without becoming
   feedback"]. Which held-out criterion the paper uses is not specified (spec marks it [inferred]).
5. **Compose** survivors onto the base (JSON union, else 3-way merge; conflicting edits are dropped and listed
   in `conflicts`). The composed stack is re-measured on the screen only, never on `holdout`.
6. If `Config.rounds > 1` the composed stack is the next base (stops with no survivors). The paper calls
   recursion "a long-term research vision" [blog:Recursive Efficient Improvement]; rsi: untested.

**One lineage iteration** (`Lineage._run`; paper stages 01-06 [blog:Method], figure routing in spec §B3.1):
1. **01 Rollouts** of the CURRENT variant on `rollout_tasks_per_family` tasks per family, 1 seed (current = base
   on iteration 0, then the last proposed variant). Paper: sample size not specified.
2. **02 Map-reduce analysis**: per-trajectory findings (repeated actions, context growth, large observations,
   sparse diagnostics) reduced to one evidence dict [sec:pengqian].
3. **03 Proposal**: ONE mechanism, given the idea, the evidence and the lineage's own history. Reads: base
   artifact + evidence + this lineage's past rows. It does not read other lineages or any sealed split.
4. **04 Implementation (Ralph loop)**: repair up to `ralph_max` times until the exit check passes (rsi exit check
   = `domain.smoke`: one task runs without error). Paper: "until an explicit exit condition passes".
5. **05 Independent review** against the behavioural contract (rsi `SmokeReviewer`: smoke + no held-out names in
   added lines; or `LLMReviewer`). A rejection goes back to 04 with the objections, at most `review_max` times,
   then the iteration ends.
6. **06 In-trajectory validation**: the candidate on the FULL screen (`evolve` x `k` seeds), then the dual gate.
7. **Keep rule (formula)**, with `m_j` capability metrics, `tau_j` tolerances, `e_k` efficiency metrics
   [blog:Capability floors]:
   `accept(c) <=> for all j: m_j(c) within tau_j of m_j(b)  AND  exists k: e_k(c) better than e_k(b)`.
   rsi (relative mode): `m_j(c) >= m_j(b) * (1 - tol_j)` for score AND solved-rate (2% each), and some of
   tokens/cost saves more than `min_gain` = 2% relative to base. Then "among passing candidates keep the
   nondominated" [blog]; rsi keeps the passing variant with lowest eta among the nondominated.
8. Gate fail routes back to 01 (paper: or to 04, or abandon). Gate pass: with `sweep=True` the lineage keeps
   going through its variant grid and freezes the best passer at the end; `sweep=False` freezes the first pass.

```
base_m = metrics(evaluate(base, evolve, k)); est = {i: oracle(i, base_trials)}
frozen = []
for idea in top(n_lineages, est):                        # paper: parallel, disposable lineages
    cur, passing = base, []
    for it in range(max_iters):
        ev = reduce(analyze(rollouts(cur, 2 tasks/family, seed 0)))      # 01-02
        prop = implement_until_smoke_passes(propose(idea, base, ev, history), ralph_max)   # 03-04
        for _ in range(review_max):                                       # 05
            if review(prop).ok: break
            prop = implement_until_smoke_passes(fix(prop, objection))
        if not review.ok: continue
        m = metrics(evaluate(prop, evolve, k)); g = dual_gate(base_m, m)  # 06
        if g.accept: passing.append(prop); (if not sweep: break)
        cur = prop
    if passing: frozen.append(min(nondominated(passing), key=eta))
survivors = [f for f in frozen if dual_gate(base_hold, metrics(evaluate(f, holdout, k))).accept]  # once each
best = compose(base, survivors)            # = res.best; 'ood' is never read by the loop
```

**What changes across rounds.** No annealing, schedule or temperature. The base metrics and the gate
(digest-frozen) never change within a pass. Per lineage only its own history grows; lineages never share state.
The gate always compares with the ORIGINAL base, not with the previous iteration. Mechanisms are gated one at
a time, "so the small losses it permits can accumulate once mechanisms combine" [blog:Capability floors].

**Stopping rule.** Per lineage: `max_iters` (default 4), proposer exhausted (variant grid used up), or first
freeze if `sweep=False`. Paper: lineage budget not specified; "after roughly five to ten iterations ... even
GPT-5.6 Sol at xhigh could get stuck in a local basin" [blog:Breadth escapes local search basins] (a
qualitative observation [ye-blog]). Per run: `rounds` passes or no survivors. `stop_reason` is always `"rounds"`.

## 4. Sample by sample

- **Unit**: one task (one environment instance) of the screen. Per task, `k` trials with seeds `0..k-1`
  (default `k=1`, so ONE seed, no noise band). Paper: trials per task not specified.
- **Rollouts for analysis** (01): first `rollout_tasks_per_family`=2 tasks per family, 1 seed. **Gate evidence**
  (06): the whole `evolve` split x `k`.
- **Recorded per trial**: score, `solved` (= score >= 1), `tokens`, `cost_usd`, `steps`, trace text, feedback,
  `meta["oracle"]` stats. Paper cost accounting: official API-equivalent prices, token traffic split into input,
  cache-read, cache-write and output [sec:pengqian Q1]. In `agentworld`: `TokenMeter` prefix-cache simulation.
- **Aggregation**: mean over ALL trial rows (so a family with more tasks weighs more), aggregate and per
  family; eta = mean cost / mean score. The gate uses aggregate (`mode="aggregate"`, faithful) or also each
  family (`mode="per_family"`, the literal "survives everywhere" reading, rsi-inferred). Missing predeclared
  metric = fail closed. Infra errors count as missing and use the failure score.
- **Inside one trajectory** the four mechanisms act on every request or tool result: Action Fusion (`then_run`
  after edit/write, hash guard, markers), ObservationPack (results over 10 KiB are sent in full twice, then
  replaced by a recallable placeholder), Evidence-Preserving Reducer (diagnostic output of 4,096 B or more is
  turned into a receipt of verbatim-verified quotes, else raw fallback), Online Context Compact (compacts at
  plan-step boundaries only when `S>0` and savings repay the cache rewrite `W*(rho-1)`), spec §B3.4/§B4.3-4.4.
- **Splits**: `evolve` read by rollouts, validation, base and composed metrics. `holdout` read ONLY by the
  firewall (one evaluation per frozen candidate plus the base). `ood`, `test` never read by the loop; the
  `Evaluator` raises on sealed splits. Check them yourself with `rsi.transfer_report`. Paper: held-out =
  EdgeBench, 11 of 51 tasks as one-way acceptance and the rest for final evaluation only [sec:pengqian];
  note the 51-task headline includes the 11 acceptance tasks (spec §B8.3).

## 5. Paper defaults vs this package

| Setting | Paper value [tag] | rsi Config field and default | Note |
|---|---|---|---|
| Idea pool size | 152 in 6 families [ye-blog][blog:Method] | `ideas=` (no default for new domains); built-in agentworld 12 | you supply it |
| Ideas explored | "selected", count not specified | `n_lineages=10` | oracle ranks, top n get lineages |
| Lineage depth | not specified; basin after ~5-10 iters [blog] | `max_iters=4` | AF lineage reported 27 iters / 10 batches |
| Ralph loop | until explicit exit condition [blog:Method] | `ralph_max=3` | exit check = `domain.smoke` |
| Review rounds | independent reviewer; count not specified | `review_max=2` | rsi addition: route back to 04 |
| Rollout sample | not specified | `rollout_tasks_per_family=2` | 1 seed |
| Screen trials | not specified | `k=1`; `screen_split="evolve"` | no noise band |
| Capability metrics | set predeclared, not listed (spec §B4.1) | `GateSpec.capability=(("score",0.02),("solved",0.02))` | tolerances are rsi's choice |
| Efficiency metrics | bill, tokens, turns, cost/response [sec] | `efficiency=("tokens","cost")`, `min_gain=0.02` | "better" = >2% relative saving |
| Gate scope | aggregate gates [blog:Capability floors]; per-env not shown | `mode="aggregate"` | `"per_family"`, `"efficiency_only"`, `"eta_better"` |
| Nondominated keep | yes [blog] | `sweep=True` | best-eta passer frozen |
| Held-out check | frozen once, never feeds back [blog:Method] | `firewall=True`, `holdout_split="holdout"` | silently OFF if no `holdout` split |
| Composition | merge survivors, opt-in extensions | `compose=True`, `validate_composition=False` | the re-check is rsi-inferred |
| Recursion | preliminary [blog] | `rounds=1` | untested; lineages run sequentially (`workers` = rollout threads) |
| Cache write/read rho | 12.5 [code:agents-install.md] | OCC `cache_write_read_ratio=12.5` | mechanism parameter, not in `Config` |
| ObservationPack | 10 KiB threshold, 2 full sends, 512/512 B excerpt [code] | grid `full_sends`, `excerpt_bytes` (registry default 2 / 1024) | blog's V2 is 2048/1536 (spec §B4.3) |

## 6. Where our implementation differs

From `implementation.md` §5/§11/§12 and `claims-audit-solpi.md` (61 claims after retry round 2: 31 reproduced,
11 partial, 1 not reproduced, 18 not testable here, 0 contradicted):
- **Reconstructed protocol.** The paper's prompts and orchestration are unpublished; roles, thresholds
  (2% tolerance, 2% min gain), the two-metric floor (score AND solved) and `review_max` routing are ours.
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
  - **R7 composition -> PARTIAL**: gating one at a time let a composed loss through; `validate_composition=True`
    (our addition) re-gates the stack and drops survivors greedily.
  - **Q1/Q2 headline savings -> PARTIAL**: tokens -54..-69% (larger than paper), cost -16..-61% (S7 A default
    -19.6% after R2-F1); the stack the protocol composed saved only 8.9%. On backend A, EPR alone was cheaper.
  - **Q3 "about 94% of Pi's score" -> NOT TESTABLE** (needs ~100-400 frontier-agent task pairs).
  - **Q13 ObservationPack 10% bill gate -> NOT TESTABLE**: in our sims replayed reads are <=6% of the bill; the
    paper's own add-one ObservationPack row is also below it ($1,339 -> $1,271).
- Paper caveats: ~94% of Pi's score, not 100%; Terminal-Bench 4 solved 15 vs 18; humans set priors, filtered
  ideas and cleaned survivor code [spec §B8].

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
Needs: `evolve` with several `family` values, a sealed `holdout` (else the firewall is silently off and
nothing guards against tricks), ideally `ood`; token/cost metering in `Execution`; `Trial.meta["oracle"]`
statistics if you want oracle ranking. Measure the noise band first (SKILL.md step 3) and set `k >= 2`: the
gate has no noise band, so a 2% tolerance can sit inside run-to-run noise.

**Audit with TRACE.md** (`python -m rsi.cli inspect runs/solpi`): `round_start` (base, ranked oracle and chosen
ideas), per-lineage-iteration `rollouts` + map-reduce evidence, each `proposal` (diff, Ralph errors), the
`critic` (review verdict), `gate` arithmetic (capability base/cand/tol/pass, efficiency saving/improved, spec
digest: recompute it), `decision`, then the "07 held-out firewall + composition" round (frozen, passed,
survivors, conflicts, dropped, base vs composed screen metrics). Also `rounds.json` and `firewall_r1/heldout.jsonl`.

**Failure modes to watch**
- Savings from doing less: tokens fall because the agent stops early; read `solved` and per-family scores, not
  only the aggregate; use `per_family` or a stricter `solved` tolerance.
- Task-specific tricks (truncate logs, hard-coded commands): check the diff for strings tied to your tasks and
  confirm the holdout rejected them; a trick that is lossless on the screen will pass the gate.
- Composition loss: the composed stack was never run on `holdout`; run `transfer_report` and consider
  `validate_composition=True`.
- Zero tokens/cost in `Execution`, no `holdout` split, or all oracle estimates equal (ranking is arbitrary).
- Small screen (8 tasks per family was not enough in our sims). No survivors is normal (paper: ~1 in 40);
  `res.best` is then the seed. Gains can vanish on another model (mechanisms fire less on Opus 5): test on the
  model you deploy. Do not tune the gate on holdout results between runs without refreshing the split.
