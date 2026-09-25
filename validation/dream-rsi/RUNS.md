# Dream-RSI: from-scratch validation runs

Each run starts from its domain's **untouched seed artifact**, in a fresh run directory `validation/dream-rsi/<run>/` that is deleted first. The live run also uses a fresh LLM cache, `validation/dream-rsi/.cache_sumdiff_live/`, deleted first. Nothing comes from `.rsi_cache` or earlier results. There was no earlier attempt of this stage to resume: `validation/dream-rsi/` did not exist.

Every run was produced by `python experiments/dream-rsi/validate_dream.py <run>`. The loop writes `trace.jsonl` in the `rsi.trace` format. One trace round is one **live cycle t**: the online search, then the dreaming phase. The trace records:

| kind | what it holds for Dream-RSI |
|---|---|
| `run_start` | full `Config`, seed artifact id, initial policy, agent / developer / LLMs, objective, selector, sandbox |
| `baseline` | the seed program's locked evaluation (per task + raw trial scores) |
| `noise` | "none": the paper's argmax has no noise band (the guarded selector would record its per-selection margin) |
| `round_start` | loop state before the live search: deployed policy (rev, id, baked-in beta), the plan it asked for and the validated plan, the direction of each root, the live root program and score, best so far, number of worlds / manifests / evaluated versions, calls used and left, spend |
| `note: online_round` | every online decision round: the prefix the policy saw, the size of the legal set, the batch it chose, the revealed outcomes |
| `proposal` + `eval` (`t<t>/b<b>.a<a>`) | every discovery-agent attempt: the prompt, the raw reply, the claimed change and hypothesis, the **actual** program diff against the parent workspace, then the locked evaluation (per task + raw trials, delta vs parent / root) |
| `gate` + `decision` (object level) | the round's best attempt against the best so far (strict `>`), and the best program before and after |
| `note: live_cycle_manifest` | the manifest that later `plan_grid` calls may read |
| `eval` (`r####_<label>`) | every policy version's replay report: V, per-world V_i, and for every world the plan, N, k, batch sizes, best, root, ceiling, the Eq.-1 terms (quality, −β1·N, +β2·N/k) and the full reveal batches |
| `analysis` | the feedback the developer sees (incumbent diagnostics on the development worlds, leakage-screen terms) |
| `proposal` + `critic` (`r####`) | every developer revision: prompt + reply (LLM path; the full text is also in `dream_prompts/`) or the mutator's feedback-directed moves and changed parameters; the **actual** `method.py` diff against the version it started from; the static-check and leakage-screen verdicts, including repair rounds |
| `gate` + `decision` (policy level) | per version: V^m against V^0, all V, argmax index, β1/β2; the deployed policy before and after |
| `note: beta_sweep` | the beta sweep of the deployed version |
| `monitor` | `rsi.trace.ShadowMonitor` scores of the seed and of every new best program on the sealed splits (AgentQA only; sum-difference has no sealed split) |
| `state`, `run_end` | the trajectory row and meters after each cycle; totals and spend |

The trace and the monitor are write-only. `tests/test_dream-rsi_validation.py` proves that runs with the monitor, without it, without a trace and without a run directory make identical decisions: the same discovery ledger (statuses, scores), policy ledger (statuses, replay values), trajectory, best program and final policy. It also checks that the monitor's model spend never enters the loop's usage.

Files in each run directory:

| file | contents |
|---|---|
| `TRACE.md` | the rendered trace (`rsi.trace.inspect`) |
| `report.json` | seed vs final via `rsi.core.transfer_report` (evolve, plus holdout / ood when the domain has them); spend from the meters |
| `audit.md` / `audit.json` | per cycle, an **independent** re-derivation from the files on disk (see below) |
| `trace_pool/iter<t>/`, `history/r####_*/`, `snapshots/`, `discovery.jsonl`, `policies.jsonl`, `dream_prompts/` | the loop's own records |

**Independent checks in `audit.md`.** They use only the files on disk, and my own code wherever that is possible.
- **Seed and every attempt re-graded.** Sum-difference is re-graded in a fresh process with my own Γ (pure Python sets, not the domain's numpy convolution). AgentQA is re-graded with a fresh `Evaluator` and `SimModel`.
- **Diffs.** Every attempt's trace diff equals the parent → child program diff rebuilt from `snapshots/`.
- **Online batches.** Each batch has at most W cells and at most one cell per branch. It stays inside the plan, never probes a child before its parent, and equals the batches recorded in `tree.json`.
- **Live root.** It equals the best program so far.
- **Best-program rule.** A new best replaces the old one only on a strict improvement.
- **Replay fidelity.** The world is re-replayed with the policy that recorded it, on the online plan. It must reveal exactly the online batches.
- **Eq. 1.** V_i and V are recomputed from the revealed cells, with every revealed score looked up in `tree.json` and the world's root and ceiling recomputed.
- **Replay determinism.** Every version is re-replayed from `history/` with a fresh evaluator. It must give the recorded V.
- **Selection.** The selected version is the argmax, with the incumbent kept on ties, and it is the version the next cycle deploys.
- **Developer revisions.** Each revision's diff equals the code diff in `history/`. The mutator's claimed moves are checked against its parameter changes.
- **Leakage.** The deployed policy passes the static check and contains no best cell id or score of any world.
- **Manifest beta.** It equals the deployed code's baked-in beta.
- **Call budget.** The call count reconciles: attempts = meter = trajectory.
- **Parse artifacts.** For every failed attempt with a `SyntaxError`, the audit asks whether the failure was a reply-parsing artifact.

**Ground truth (offline runs only).** For every dreaming phase, each version is run on 8 fresh one-cycle live searches: new agent seeds, the root program the next cycle starts from, and the manifests the policy would see. Each search is scored with Eq. 1 on a common scale (gain over the root divided by the largest gain any probe of that phase reached). This asks whether replay ranked the versions the way their online behaviour ranks them, and whether the deployed version was really no worse. The live run has no ground truth: re-running its versions online would cost as much as the run itself. So its dreaming decisions are **unverifiable** beyond replay-level correctness.

## Summary

| run | setup | calls per cycle | seed → final (evolve) | sealed (holdout / ood) | audit | spend |
|---|---|---|---|---|---|---|
| `sumdiff_offline` | sum-difference, mock agent + ParametricMutator, 3 × 5 grid, W = 3, T = 4, M = 4 | 15, 12, 7, 9 (43 in total; fixed π₁ would use 60) | Γ 0.9105 → **1.0190** | none (single-instance problem) | 55 checks, 0 FAIL | $0 |
| `agentqa_offline` (extra, exercises the monitor) | AgentQA harness via DomainTask, SimModel, agentqa mock agent + ParametricMutator, 3 × 3 grid, W = 3, T = 4, M = 4 | 9, 6, 8, 10 | S 0.250 → **1.000** (saturated at the first attempt) | holdout 0.375 → 0.750; ood 0.500 → 0.875 | 51 checks, 0 FAIL | $0 |
| `sumdiff_live` | sum-difference, **claude haiku as discovery agent and policy developer**, 3 × 4 grid, W = 3, T = 3, 3 revisions per phase | LIVE_CALLS | LIVE_SCORES | none | LIVE_AUDIT | LIVE_SPEND |


## Run 1: `sumdiff_offline`

**Setup.**
- Problem: sum-difference, maximize Γ(A) = log(|A+A|/|A|) / log(|A−A|/|A|). The seed is the untouched `construct.py` (a 12-element set, no stages; Γ = 0.9105).
- Discovery agent: the domain's offline mock. Each attempt appends one search stage (hill, anneal, window, fringe or grow) to the parent's recipe.
- Direction: the branch's direction, except that 25% of later attempts switch mechanism (documented mock behaviour).
- Developer: `ParametricMutator`.
- π₁: parallel refine on a 3 × 5 grid (branch_count 3, attempts 0..4).
- Loop settings: W = 3, T = 4 live cycles, M = 4 versions per phase (incumbent + 3 revisions), Eq. 1 with β1 = 0.01 and β2 = 0.005, per-world normalization, argmax selection, root_mode `earliest`, no dreaming after the last cycle.
- Sandboxes: policies and programs both run in subprocess sandboxes.
- Wall time: 2.1 s for the loop and ~25 s for the audit, including its ground-truth searches.

**Results.**
- Seed Γ 0.9105 → final **1.0190** (MSTD: more sums than differences).
- Total cost: 43 agent calls. Fixed π₁ would have spent 60.
- Replay: 60 episodes, 0.21 s CPU (3.5 ms per episode).
- `transfer_report` covers `evolve` only: 0.9105 → 1.0190. There is no sealed split, so there is no shadow monitor.

**What the loop did, cycle by cycle** (from `TRACE.md`; every step also passes the audit):

1. **Cycle 1 (π₁ = r0000, parallel refine, plan 3 × 5).**
   - Online search: five batches of 3, all 15 cells, 15 calls.
   - Best program: 0.9105 → 1.0000, at `t1/b1.a3` (a symmetric set, so |A+A| = |A−A|). The same value 1.0 appears again at `b1.a4` and `b2.a4`.
   - Dreaming on the single world:
     - Incumbent: V = 0.865 (attainment 1, N = 15, k = 5).
     - Mutator revision 1: the adaptive template (prefix trajectories, dynamic portfolio, beta schedule, `plan_grid`) replaces parallel refine, plus feedback-directed moves ("ceiling reached with many probes: try a cheaper search"). Its replay closes b1 after two flat attempts, keeps b0 and b2, and deepens b2 to `b2.a4`, which also holds 1.0. Result: attainment 1 with N = 10, so **V = 0.910**.
     - Revisions 2 and 3 tie at 0.910.
   - Decision: argmax → **r0001 deployed**. Correct per the paper's rule (re-derived).
   - **Ground truth disagrees.** On 8 fresh online searches from the next root, r0001 used 7.1 calls against 15, but found significantly less:
     - mean gain 0.0078 vs 0.0138;
     - selected − incumbent gain −0.0060, 95% CI [−0.0115, −0.0006];
     - online Eq.-1 V −0.28 [−0.60, +0.03];
     - Spearman(replay V, online V) = −1.0 over the 4 versions.

   Replay credited the frugal policy with reaching the ceiling only because the recorded tree happened to contain the ceiling value in a second branch. This is the spec's critique §8.2 (the replay ceiling biases toward frugality) and §8.4 (at t = 1 there is a single world). Paper §3 guarantees V^{m*} ≥ V^0 **on replay only**, and that guarantee held.
2. **Cycle 2 (r0001).**
   - `plan_grid` holds 3 × 5 ("live best still improving with balanced gains"). This is the bootstrap/hold path; the only manifest is cycle 1's.
   - Online search: batches [3, 3, 3, 2, 1], 12 calls, 2 planned cells left unprobed.
     - The adaptive policy closed b2 after three flat results (1.0071 ×3).
     - It closed b1 after one flat refinement.
     - It spent the last singleton on b0, the only improving branch. That is a singleton only because the other branches were closed, not "merely because its top candidate is clear".
   - Best program: 1.0000 → 1.0172.
   - Dreaming on 2 worlds: V = [0.901, 0.901, 0.901, 0.8996]. Two revisions tie with the incumbent (their PARAMS perturbations do not change a single decision on these small worlds), and one is slightly worse. **Incumbent kept** (the tie rule).
   - Ground truth agrees: all four versions behave identically online (9 calls, same gain).
3. **Cycle 3 (r0001).**
   - Online search: batches [3, 3, 1], 7 calls, stopped with 3 cells left.
   - Best program: 1.0172 → 1.0177.
   - Dreaming on 3 worlds: V = [0.9146, 0.9146, 0.9126, 0.9146]. **Incumbent kept.**
   - Ground truth: same calls and gain for all four versions. The small online-V differences come only from k.
4. **Cycle 4 (r0001).**
   - `plan_grid` now reads two manifests with early gains and widens to 4 branches × 3 attempts ("roots improved early while depth stalled: widen, trim depth").
   - Online search: batches [3, 3, 2, 1], 9 calls; the fourth root opened in round 2.
   - Best program: 1.0177 → **1.0190**, at `t4/b3.a2` on the new branch.
   - No dreaming after the last cycle (`dream_last=False`).

**Is every step correct?**
- Mechanically, yes: every audit check passes.
- Every attempt re-grades identically with my own Γ.
- Every recorded diff is the real change, and the mock's claimed stage is the appended stage.
- Every world replays exactly under the policy that recorded it.
- Every V is reproducible, and every selection is the argmax.
- Two things are **not** good steps even though they follow the rule:
  - The cycle-1 deployment was a replay artefact: significantly less online gain.
  - The mutator's later revisions change nothing observable: every phase after the first is a tie, and the incumbent is kept.
- Also: before this validation, the mutator's change text claimed perturbations that clamping or rounding had turned into no-ops (e.g. `perturb plan_depth_step -> 1` when it already was 1). The text now says `unchanged at … (clamp/round)`; decisions are unchanged.

## Run 2: `agentqa_offline` (extra)

**Setup.**
- Problem: the shared AgentQA harness domain, wrapped by `DomainTask`. The seed is the untouched `AgentQADomain.seed_artifact()` (one direct model call, "You are a helpful assistant.").
- Score: mean evolve S over 8 numeric tasks, k = 1, with `SimModel`.
- Sealed splits: holdout (8) and ood (8, 2 per unseen family). Only the shadow monitor and the final transfer report score them.
- Agent: `agentqa_mock_agent`, one mechanism knob per attempt (format, reason, verify, tool, simplify).
- Developer: `ParametricMutator`.
- Loop settings: π₁ = parallel refine 3 × 3, W = 3, T = 4, M = 4.

**Results.**
- Evolve: 0.25 → **1.00**. The very first attempt (`t1/b0.a0`, the `tool` mechanism) already scores 1.00.
- Shadow monitor (never shown to the loop): seed holdout 0.375 / ood 0.500 → `t1/b0.a0` holdout 0.750 / ood 0.875.
- `transfer_report` gives the same numbers:
  - holdout +0.375 [+0.125, +0.750];
  - ood +0.375 [−0.125, +0.750], so the ood gain is **not** significant at n = 8;
  - families: dates 0 → 1, numbertheory 0 → 1, strings 1 → 0.5, lists 1 → 1.

**Narrative.**
- **Cycle 1.** Parallel refine: 9 calls, best 0.25 → 1.00 at the first attempt.
  - Dreaming on one world: V = [0.925, 0.933, 0.933, 0.942].
  - r0003 is deployed: the adaptive template with early stopping reaches the world's ceiling with fewer probes.
  - Ground truth: nothing can beat 1.00 from the saturated root, so every online search has gain 0. r0003 uses 6 calls vs 9, a real saving (+0.03 online V), but it is an efficiency gain on a saturated task.
- **Cycles 2–4.** The root is already at the evolve ceiling of 1.00.
  - Every attempt scores ≤ 1.00, and the best program never changes.
  - Replay can then only reward cheapness: the normalized attainment is 0 on these flat worlds. The policy stops early, with 3–5 planned cells left unprobed.
  - Cycle 2 replaced r0003 by r0005 on a +0.0067 replay edge. Online, both behave identically (8 calls, gain 0), so the change is neutral, not an improvement.
  - Cycle 3 kept the incumbent.
  - `plan_grid` widened the grid to 4 and then 5 branches ("live best plateaued: widen to cover new directions"). That is exactly the Listing-2 rule, but it spends calls on a task that is already solved.

**Takeaway.** The run works end to end through a generic `rsi.core.Domain` with sealed splits, and it exercises the shadow monitor. Once the task saturates, however, it says little about dreaming.

LIVE_SECTION_PLACEHOLDER
