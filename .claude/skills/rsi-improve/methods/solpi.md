# SoL-Pi: end-to-end methodology (for `rsi.improve(..., method="solpi")`)

Sources: the paper, Liu, Ye et al., "SoL-Pi: Recursively Scaling Auto-Research Loops for Efficient Agent Harness",
arXiv 2609.20519v1 (17 Sep 2026), cited as `[paper §x]`, `[paper Fig. n]` or `[paper Table n]`. The paper has no
algorithm box, no equations and no appendix. Our package: `rsi/solpi/{driver,research,gate}.py`,
`docs/methods/metaharness-solpi/{paper-spec.md Part B, implementation.md, claims-audit-solpi.md}`. Facts that are
only in other sources keep their tags: `[blog:<section>]` (authors' project page), `[ye-blog]` (co-first author),
`[code:SoL-Pi/...]` (NVlabs release). The paper does not publish its prompts, numeric thresholds, sample sizes or
orchestration code. Where it is silent we write "not specified in the paper"; our own choices are marked "rsi choice".

## 1. In one paragraph

Long-running coding agents spend many tokens on overhead: an edit followed by a separate run command, large tool
outputs re-sent on every later request, a context that keeps growing, and long build/test logs read by the frontier
model [paper §1, §2.4]. SoL-Pi uses auto-research loops to find mechanisms for the open harness Pi that cut tokens
and cost while preserving the agent's ability to finish the work [paper §2.1]. It does NOT optimise task score.
Acceptance has two sequential gates: every capability metric stays within its predeclared tolerance, and at least
one declared efficiency metric improves [paper §2.1]. The paper reports token efficiency as "API cost per unit of
aggregate task score" [paper §3]. The search is a broad-to-deep funnel. An outer stage holds 152 directions in six
families [paper §2.2]. An inner stage develops each selected direction in an isolated, disposable lineage
[paper §1, §2.2]. Search uses 535 environments in two families [paper §2.3]. A frozen candidate is then checked
once on 11 held-out EdgeBench tasks, and that result never feeds back into search [paper §2.1, §2.5]. Four of the 152
directions survived (about 1 in 38): Action Fusion, Online Context Compact, ObservationPack and Evidence-Preserving
Reducer [paper §2.4]. On the 51 public EdgeBench tasks the complete stack used 49.0% (GPT-5.6 Sol) and 44.7% (Opus 5)
fewer tokens and cost 33.2% / 33.5% less, at 93.7% / 94.3% of Pi's average score [paper §3.1, Tables 1-2].

## 2. Inputs and setup

Before round 1 you need the following (rsi names in brackets):
- **Artifact**: the base harness the mechanisms attach to. In the paper this is Pi, and all four mechanisms are
  implemented as Pi extensions [paper §2.5]. The model is kept fixed [paper §4.1]. Before experimentation, the
  capability metrics, their tolerances and the efficiency metrics are fixed and isolated from the optimizing agent's
  control [paper §2.1]. Before held-out evaluation, the mechanism source, configuration, metrics, tolerances and
  acceptance rule are frozen [paper §2.5]. The research procedure is NOT frozen: each experiment copies a shared
  skill template and may modify its orchestration code, which is discarded afterwards [paper §2.2]. [`seed_artifact`]
- **Idea pool**: one-line directions in six families: context, progress, tools, delegation, prompt and policy,
  improvement and evaluation (paper: 152) [paper §2.2]. Families record where a hypothesis came from; they do not
  constrain where the change is made [paper §2.2]. The letter codes C/P/T/D/R/M are an rsi choice.
  [`ideas=[Idea(id, family, title, mechanism, grid, oracle)]`; required for any domain except the built-in
  `agentworld`/`agentqa`]
- **Environments in several families**, each with an executable verifier [paper §2.3]. Paper: 495 repository tasks
  from GitHub issue-PR pairs, with the PR and regression test hidden and only tasks whose test fails before the patch
  and passes after kept. Plus 40 verifier-first synthetic tasks, mostly with a Terminal-Bench-2-style verifier
  interface. Splits: [`evolve` = training screen; `holdout` = firewall acceptance set; `ood` = final].
- **Metrics predeclared before search** [paper §2.1]. The paper does not list the capability metrics or give any
  tolerance value (not specified in the paper). It reports total token traffic (input, cache read, cache write,
  output), token cost at API prices of 17 Aug 2026, average score, and $/score [paper §3, Table 1]. The ObservationPack
  sweep's gates (10% bill saving, -2% quality) are in the blog only, not the paper. [`Config.gate = GateSpec(...)`,
  digest recorded]
- **Cost metering**: the domain must return `Execution.tokens`, `cost_usd` and `steps`. If they are 0, no efficiency
  gain is ever seen and nothing is accepted.
- **Models**: the harness was optimised only on GPT-5.6 Sol trajectories (the search backend; xhigh in Fig. 7). It
  was then applied to Opus 5 "without further search or adaptation" [paper §3.1, §3.4, Table 2]. EPR's auxiliary
  model is GPT-5.6 Luna at high [paper §2.5]. The research AI's model is not specified in the paper. rsi: `llm_task`
  (frozen agent), `llm_propose` (or your own `proposer=`).
- **Measured first**: "Before rollout budgets are assigned, Oracle Analysis examines existing development
  trajectories to identify avoidable work in the base harness" [paper §2.2]. Example: Action Fusion's oracle stage
  measured missed adjacent actions ("12.3% direct headroom") and projected an 11.5% token saving under full
  triggering [paper Fig. 8, §3.5]. rsi (every driver round): the base artifact on the screen = all `evolve` tasks x
  `k` seeds -> `Metrics` (aggregate and per family: score, solved, tokens, cost, steps, eta = cost/score), then the
  oracle on those trials. rsi reads `Trial.meta["oracle"]`. Your domain must supply it, otherwise every idea scores
  0.05 and ideas are ranked by id.

## 3. Round by round

The paper has no single "round". There is an outer stage over the idea pool and inner lineages that run
"propose, implement, run a fixed experiment, inspect, then retain, revise or discard" [paper §2.2]. The rsi
"driver round" is one pass of both stages. The blog numbers the inner steps 01-06. The paper's Fig. 8 numbers
something else: the phases of the Action Fusion lineage (01 oracle analysis, 02 baseline build, 03 prompt
optimisation, 04 final validation).

**One research pass** (`AutoResearchDriver.run_round`):
1. Oracle Analysis, then "selected" directions get rollout budget. Each must name a concrete source of overhead and
   a harness change [paper §2.2]. Selection rule and count: not specified in the paper. rsi choice: the top
   `n_lineages` by oracle estimate.
2. One isolated, disposable lineage per selected direction. Failures do not couple across lineages [paper §1,
   §2.2]. Whether lineages run concurrently is not specified in the paper. rsi runs them sequentially; `workers`
   only threads rollouts.
3. A lineage freezes its candidate [paper Fig. 2]. The paper keeps "the nondominated results" among gate-passing
   candidates [paper §2.1]. rsi choice: at most one frozen candidate per lineage.
4. **Held-out firewall**: evaluation "occurs only after the candidate is frozen". Held-out results "never feed back
   into the Auto-Research Loops: a failed validation rejects the candidate without triggering further optimization"
   [paper §2.1, Fig. 2]. The paper uses 11 of EdgeBench's 51 public tasks for this one-way acceptance [paper §2.5].
   The held-out pass criterion is not specified in the paper. rsi: the same dual gate against the base's `holdout`
   metrics, one evaluation per frozen candidate. Only a bool reaches selection. The details go to a write-only sink
   and to `res.meta` after search ends.
5. **Integrate**: retained mechanisms are combined into SoL-Pi, and its "hyperparameters and implementation" are
   refined "while preserving capability". The combined harness is then evaluated end to end and frozen
   [paper §2.1, §2.4, Fig. 1]. rsi: survivors are composed onto the base (JSON union, else 3-way merge;
   conflicting edits are dropped and listed in `conflicts`). The stack is re-measured on the screen only. There is
   no refinement, and there is no re-gating unless `validate_composition=True`.
6. **Recursion** ("use SoL-Pi as the starting harness for the next research cycle") is "a long-term research vision
   rather than a compounding effect demonstrated" [paper §5.1]. rsi: if `Config.rounds > 1`, the composed stack is
   the next base, and the run stops when a round has no survivors. Untested.

**One lineage iteration** (`Lineage._run`; order from [paper Fig. 2]: Trajectory Rollouts -> Map-Reduce Analysis
-> Mechanism Proposal -> Candidate Implementation -> Independent Review -> Development Validation, with "revise" /
"next iteration" loops, then FREEZE -> Held-Out Evaluation):
1. **Rollouts** of the current variant. "Each iteration may yield multiple exploration trajectories" [paper §2.2];
   the sample size is not specified in the paper. rsi: `rollout_tasks_per_family` tasks per family, 1 seed. Current
   = the base on iteration 0, then the last variant that reached validation.
2. **Map-reduce analysis**: "independent analyzers examine one trajectory each for repeated actions, context growth,
   large observations, or sparse diagnostic signals". A reducer combines their findings into "a candidate-level
   summary that guides the next proposal" [paper §2.2].
3. **Proposal** of one mechanism for this lineage's direction. Trajectories "guide mechanism proposals and
   implementation" [paper Fig. 2]. Other proposer inputs: not specified in the paper. rsi: the idea, the base
   artifact (edits are made relative to the base), the evidence and this lineage's own past rows. It does not read
   other lineages or any sealed split.
4. **Implementation (Ralph Loop)**: "the implementer refines the candidate to meet an explicit completion
   criterion" [paper §2.2]. rsi: up to `ralph_max` repairs, with the exit check `domain.smoke` (one task runs
   without error).
5. **Independent review** "before evaluation. Failed reviews trigger revision" [paper §2.2]. What the reviewer
   checks: not specified in the paper ("behavioural contract" is the blog's term). rsi: `SmokeReviewer` (smoke run
   + no held-out names in added lines) or `LLMReviewer`. A rejection goes back to step 4 with the objections, at
   most `review_max` times. After that the iteration ends.
6. **Development validation** on development tasks [paper Fig. 2], followed by the two gates. In Fig. 8 "some steps
   evaluate multiple candidates in parallel, and only the best result from each parallel batch is retained". rsi:
   one candidate on the FULL screen (`evolve` x `k` seeds), then the dual gate.
7. **Keep rule** [paper §2.1, quoted]: "every capability metric must remain within its predeclared tolerance, and the
   candidate must improve at least one declared efficiency metric. Among candidates that pass both gates, the
   pipeline retains the nondominated results under the declared metrics." Written as a formula (ours; the paper has
   no equation), with `m_j` capability metrics, `tau_j` tolerances and `e_k` efficiency metrics:
   `accept(c) <=> for all j: m_j(c) within tau_j of m_j(b)  AND  exists k: e_k(c) better than e_k(b)`.
   rsi (relative mode): `m_j(c) >= m_j(b) * (1 - tol_j)` for score AND solved rate (2% each), and tokens or cost
   saves more than `min_gain` = 2% relative to the base. With `sweep=True`, the lineage keeps the passing variant
   with the lowest eta among the nondominated (score up, tokens and cost down).
8. On a gate failure the paper will "retain, revise, or discard" [paper §2.2]. rsi: back to step 1 with this
   candidate as the current variant. On a gate pass, `sweep=True` keeps going through the variant grid and freezes
   the best passer at the end; `sweep=False` freezes the first pass. In the paper's Action Fusion lineage, the agent
   also added trigger rate as an intermediate acceptance metric next to task score [paper §3.5]. rsi has no
   intermediate metrics.

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

**What changes across rounds.** No annealing, schedule or temperature. The base metrics and the gate never change
within a pass; the gate is digest-frozen, matching [paper §2.1]. Within a lineage, only its own history grows.
Lineages share nothing. rsi choice: the gate always compares with the ORIGINAL base, not with the previous
iteration; the paper's comparison baseline is not specified. Mechanisms are explored and gated independently, so
integration is a separate step [paper §2.1]. The blog warns that "the small losses it permits can accumulate once
mechanisms combine" [blog:Capability floors].

**Stopping rule.** Paper: each lineage "runs to completion" [paper §2.2]. The budget is not specified in the paper.
The Action Fusion lineage ran 27 iterations: oracle 1, baseline 6, prompt optimisation 18 (10 search batches), final
validation 2 [paper Fig. 8, §3.5]. The blog says lineages stuck in a local basin "after roughly five to ten
iterations" [blog:Breadth escapes local search basins]; this is not in the paper. rsi, per lineage: `max_iters`
(default 4), proposer exhausted (variant grid used up), or the first freeze if `sweep=False`. Per run: `rounds`
passes, or stop when a pass has no survivors. `stop_reason` is always `"rounds"`.

## 4. Sample by sample

- **Unit**: one task (one environment instance). Trials per task and seeds: not specified in the paper. Search scale
  was about 150 directions, about 500 environments, more than 3,000 runs and more than 60,000 agent-environment
  interactions [paper §1]. rsi: `k` trials per task with seeds `0..k-1` (default `k=1`, so ONE seed and no noise
  band).
- **Rollouts for analysis**: multiple trajectories per iteration [paper §2.2]. rsi: the first
  `rollout_tasks_per_family`=2 tasks per family, 1 seed. **Gate evidence** in rsi: the whole `evolve` split x `k`.
- **Recorded**: token traffic split into input, cache read, cache write and output; token cost at fixed API prices
  (17 Aug 2026); average score; $/score [paper Table 1]. Also each mechanism's trigger rate (share of tasks where it
  fires) and trigger intensity (activations per triggered task) [paper §3.4]. rsi per trial: score, `solved`
  (= score >= 1), `tokens`, `cost_usd`, `steps`, trace text, feedback, `meta["oracle"]`. In `agentworld`,
  `TokenMeter` simulates the prefix cache.
- **Aggregation**: the paper reports average score and totals over the task set. $/score = cost per task / average
  score, e.g. $1,339 / 51 / 44.833 = 0.5855 [paper §3, Table 1]. How the gate aggregates across environments is not
  specified in the paper. It only requires efficiency to improve "beyond a single development environment"
  [paper §2.1]. rsi: mean over ALL trial rows, so a family with more tasks weighs more, aggregate and per family;
  eta = mean cost / mean score. The gate uses the aggregate (`mode="aggregate"`, the default) or also each family
  (`mode="per_family"`, the "survives everywhere" reading, an rsi choice). A missing predeclared metric fails
  closed. Infra errors count as missing and get the failure score.
- **Inside one trajectory** each mechanism acts on requests or tool results [paper §2.4, §2.5, Fig. 4]:
  - Action Fusion adds an optional follow-up command to file-mutation tools (`then_run`, Fig. 4a), so 3 API calls
    become 2. Commands that need to inspect the mutation result stay separate. The hash guard and markers are from
    [code].
  - Evidence-Preserving Reducer takes build/test logs of at least 4 KiB from a predefined command set; file reads
    and searches bypass it. It archives the exact log and has a cheaper model write a receipt. A deterministic
    verifier checks the schema, source hash, exit status, exact quotes and size. It falls back to the original log
    on a verification failure, suspected credentials, or no size reduction. It runs before ObservationPack, which
    skips receipts.
  - ObservationPack archives results over 10 KiB and sends them in full for the next two provider requests. After
    that it sends a stable handle, the original size and a 1 KB excerpt of complete head/tail lines; exact pages
    can be recalled.
  - Online Context Compact acts at plan-step completion (`update_plan`). It estimates the remaining requests, capped
    by the window-fill horizon, and compacts when the projected input saving beats the cache-rewrite cost. Later
    compactions need a larger margin, and it also compacts near the window limit. The summarisation call is not
    priced. The formula `W*(rho-1)` is the code's, spec §B4.4.
- **Splits**: the paper keeps EdgeBench fully out of search. 11 of its 51 public tasks are for one-way acceptance of
  frozen candidates, and 40 are for final evaluation only [paper §2.5]. The 51-task headline includes the 11
  acceptance tasks [paper §3.1]. rsi: `evolve` is read by rollouts, validation, base and composed metrics. `holdout`
  is read ONLY by the firewall (one evaluation per frozen candidate, plus the base). `ood` and `test` are never read
  by the loop; the `Evaluator` raises on sealed splits. Check them yourself with `rsi.transfer_report`.

## 5. Paper defaults vs this package

| Setting | Paper value [cite] | rsi Config field and default | Note |
|---|---|---|---|
| Idea pool size | 152 in 6 families [paper §2.2] | `ideas=` (no default for new domains); built-in agentworld 12 | you supply it |
| Ideas explored | "selected"; count and rule not specified [paper §2.2] | `n_lineages=10` | oracle ranks, top n get lineages |
| Lineage depth | runs to completion; AF lineage 27 iters, 10 search batches [paper Fig. 8] | `max_iters=4` | blog: basin after ~5-10 iters |
| Candidates per step | sometimes several in parallel, best of batch kept [paper Fig. 8] | 1 per iteration | rsi choice |
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
| Recursion | future work [paper §5.1] | `rounds=1` | untested; lineages run sequentially |
| OCC cache write/read ratio | used, value not given [paper §2.5] | `cache_write_read_ratio=12.5` | 12.5 from [code:agents-install.md] |
| ObservationPack | 10 KiB, 2 full sends, 1 KB excerpt [paper §2.4, Fig. 4c] | grid `full_sends`, `excerpt_bytes` (default 2 / 1024 = 512+512) | blog's V2 is 2048/1536 (spec §B4.3) |
| EPR | logs >= 4 KiB, GPT-5.6 Luna (high) [paper §2.4, §2.5] | `min_bytes=4096` | offline `DeterministicReducer` |

## 6. Where our implementation differs

From `implementation.md` §5/§11/§12 and `claims-audit-solpi.md` (61 claims after retry round 2: 31 reproduced,
11 partial, 1 not reproduced, 18 not testable here, 0 contradicted). The audit predates access to the paper text, so
many of its claim sources are the blog or secondary summaries.
- **Reconstructed protocol.** The paper gives the stage order and the gate rule but no prompts, thresholds, sample
  sizes or orchestration code. Ours are rsi choices: the roles, 2% tolerance, 2% min gain, the two-metric floor
  (score AND solved), the review cap, one candidate per iteration, one frozen candidate per lineage, comparison
  with the original base, and no intermediate metrics.
- **Integration.** The paper refines the combined stack while preserving capability [paper §2.1]. rsi only merges
  it by default; `validate_composition=True` re-gates the stack and drops survivors greedily.
- **Scale.** 5 simulated families, 12 ideas, deterministic mock agents, invented prices with rho = 12.5.
- **Mechanisms are faithful ports** (14/14 reproduced; OCC economics vectors pass exactly; EPR never admits a
  non-verbatim quote). Not ported: Pi's split-turn compaction path, OCC session restore.
- Audit verdicts that change how you use it:
  - **L2 "survive everywhere" -> NOT REPRODUCED.** More training families reject more tricks at the gate (3.0 -> 1.6
    admitted), but held-out transfer is no better than with a single-family dual gate. The capability floor plus
    the one-way firewall did the work. Do not expect diversity alone to protect you.
  - **L4 floor blocks "doing less" -> PARTIAL.** The two-metric floor rejects both do-less shortcuts. But a trick
    that is lossless alone (tail-trim@120) passed and cost 3.8 points once composed. With 8 screen tasks per family
    the floor admitted a 24-turn cap in 2/20 seeds and tail-trim in 13/20; a 3x screen cut failures to 1/20.
  - **R7 composition -> PARTIAL**: gating one mechanism at a time let a composed loss through.
  - **Q1/Q2 headline savings -> PARTIAL**: tokens -54..-69% (larger than the paper's), cost -16..-61% (S7 A default
    -19.6% after R2-F1). The stack the protocol composed saved only 8.9%. On backend A, EPR alone was cheaper,
    whereas the paper's full stack is cheapest in both backends [paper §3.4, Table 4].
  - **Q3 "93.7% / 94.3% of Pi's score" -> NOT TESTABLE** (needs ~100-400 frontier-agent task pairs).
  - **Q13 ObservationPack 10% bill gate (blog only) -> NOT TESTABLE**: in our sims, replayed reads are <=6% of the
    bill. The paper's add-one ObservationPack row also saves only 5.1% under GPT-5.6 Sol ($1,339 -> $1,271); under
    Opus 5 it saves 32.5% ($1,741 -> $1,176) [paper Table 4].
- Paper caveats:
  - The stack scores 93.7-94.3% of Pi, not 100% [paper Tables 1-2].
  - On Terminal-Bench 4, SoL-Pi solved 15 tasks against 18 for Pi and Codex [paper Table 3].
  - The "SoL-Pi [Performance]" point is the single mechanism with the highest EdgeBench score per backend, i.e. it
    was chosen on the evaluation set [paper §3.1].
  - Transfer to Opus 5 is "preliminary evidence" [paper §3.1].
  - The search counts "do not establish a scaling law" [paper §2.2].
  - That humans set priors, filtered ideas and cleaned survivor code comes from [blog:Humans set priors]; the paper
    does not say so.

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
Needs:
- `evolve` with several `family` values;
- a sealed `holdout` split (without one the firewall is silently off and nothing guards against tricks), and
  ideally `ood`;
- token/cost metering in `Execution`;
- `Trial.meta["oracle"]` statistics if you want oracle ranking.

Measure the noise band first (SKILL.md step 3) and set `k >= 2`: the gate has no noise band, so a 2% tolerance can
sit inside run-to-run noise. `validate_composition=True` is the closest rsi gets to the paper's "refine while
preserving capability" integration step.

**Audit with TRACE.md** (`python -m rsi.cli inspect runs/solpi`). It shows, in order:
- `round_start`: the base, the ranked oracle and the chosen ideas;
- per lineage iteration: `rollouts` and the map-reduce evidence, each `proposal` (diff, Ralph errors), the `critic`
  (review verdict), the `gate` arithmetic (capability base/cand/tol/pass, efficiency saving/improved, spec digest:
  recompute it), and the `decision`;
- the "07 held-out firewall + composition" round: frozen, passed, survivors, conflicts, dropped, base vs composed
  screen metrics.

Also check `rounds.json` and `firewall_r1/heldout.jsonl`.

**Failure modes to watch**
- Savings from doing less: tokens fall because the agent stops early. Read `solved` and the per-family scores, not
  only the aggregate; use `per_family` or a stricter `solved` tolerance.
- Task-specific tricks (truncated logs, hard-coded commands): check the diff for strings tied to your tasks and
  confirm the holdout rejected them. A trick that is lossless on the screen will pass the gate.
- Composition loss: the composed stack was never run on `holdout`. Run `transfer_report` and consider
  `validate_composition=True`.
- Zero tokens/cost in `Execution`, no `holdout` split, or equal oracle estimates (arbitrary ranking).
- A small screen: 8 tasks per family was not enough in our sims.
- No survivors is normal (paper: 4 of 152 directions survived); `res.best` is then the seed.
- Mechanisms fire less often and less intensely on a backend they were not searched on [paper §3.4, Fig. 6]: test
  on the model you deploy.
- Do not tune the gate on holdout results between runs without refreshing the split.
