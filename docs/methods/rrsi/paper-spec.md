# RRSI — Regularized Recursive Self-Improvement of Agent Harnesses

Xia, Han, Wang, Chen, Zhuang/Zhang, Lee, Huang, Yu, CuiZhu, Ming, Yao, Gokturk, Pfister, Lee (Google Cloud AI Research with Stanford, WashU and UNC), arXiv 2609.24972, 21 Sep 2026 [doc][code:rrsi/README.md]. The **fifth** author is spelled "Zhuang, Yufan" in the current repository bibtex and in the project-page bibtex [code:rrsi/README.md][sec:projectpage]. The spelling is "Yufan Zhang" in the arXiv listing mirrors [sec:abstract] and in an earlier README snapshot (`jinuk0211/rrsi`, whose code is byte-identical to `be50316`) [sec:fork].

## 0. Sources and how to read the tags

| Tag | Meaning |
|---|---|
| `[code:rrsi/<path>:<symbol>]` | `google-research/rrsi` at commit `be50316e1db05914068a973f322770ef08ed7ba1`, cloned to `scratchpad/src/google-research__rrsi`. I read every file under `rrsi/`, `rrsi.py`, `tests/`, all three `domains/*/{adapter.py,briefs.py,rrsi.json,SKILL.md,PATTERNS.md,README.md,render.py}`, plus the relevant domain scripts and the `mechanisms.py` substrates. The unit tests pass locally (`python3 tests/test_core.py`: 8/8 ok). |
| `[doc]` | The user's overview page ("Four ways AI is learning to improve itself"), which is a secondary summary. |
| `[sec:<id>]` | A secondary source that paraphrases or quotes the paper (for example equation numbers, table numbers, figure captions). **Not verified against the paper.** IDs: `abstract` is the verbatim arXiv abstract mirrored in recynie/research-pipeline, dsa04156/research-db and rxmna8502/vybe-intelligence-vault. `shibing` is shibing624/ai-paper-analysis, a long Chinese read-through with formulas, tables and figure captions. `inkeast` is inkeast/MessageDaily, a read-through that cites Eq. 1–7 and Table 5. `harry` is HarryHeYu/DailyPaper, a deep dive that quotes the formulas and Tables 1–4. `roacher` is RoacherM/ai-harness-notes. `awesome` is Bing2030/awesome-rsi `site/curated.json`. `tony` is TonyLeng1314/paper-brief. `search` is a WebSearch result snippet, probably from the arXiv HTML or neurohive.io; the fact-checker re-ran it on 2026-09-24 and it reproduced verbatim, but the page itself could not be opened. Further IDs added by the fact-check pass are listed below the table. Copies are in `scratchpad/papers/rrsi_secondary/`. |
| `[inferred]` | My own reconstruction, arithmetic or design proposal. |

Secondary-source IDs added by the fact-check pass:
- `projectpage` is `ia3andy/devoured` `templates/full-content/2026-09-23/ai-7.html`, a text mirror of the regularized-rsi.com project page (headings, one-line mechanism descriptions and bibtex).
- `fork` is `jinuk0211/rrsi`, an earlier README snapshot of the official repository.
- `jjak-take` is a third-party speculative "take" (jjakimoto/research-issues item 1672), **not evidence**.
- `demo` is the JavaScript of the overview's interactive "overfitting trap" (`<script>` block in the uploaded HTML).

**I could not open the paper itself.** arxiv.org (abs, pdf and html), regularized-rsi.com, alphaxiv, huggingface, hyper.ai, cctest.ai, aiweekly.co, daily.dev, semanticscholar, ar5iv and web.archive.org were all blocked by the egress proxy (curl returned `CONNECT 403`, WebFetch returned `EGRESS_BLOCKED`). The fact-check pass repeated this on 2026-09-24 with the same result. It tried export.arxiv.org, arxiv.org/pdf, regularized-rsi.com, papers.cool, emergentmind, chatpaper, scholar.archive.org and others, and every curl got no connection. A GitHub code search for `"2609.24972"` found no repository that vendors the PDF or LaTeX. As a result, no tag in this document reads `[paper:…]`. Every paper-level claim comes from the code (whose README says it maps paper to code), from the overview, or from a secondary source. The code is the most authoritative source I had, because its docstrings name the paper's equations ("Eq. (anneal)", "Eq. (tokenbudget)", "Eq. (floor)", "Algorithm 1/2") and its `rrsi.json` files list "Paper values".

---

## 1. The idea in one paragraph (101)

An LLM agent is a frozen model wrapped in a **harness**: prompts, the control loop, tools, memory and skill files, and context management. Recent "harness evolution" methods let an LLM read the agent's failure traces, edit the harness, re-run it on the same practice tasks (the **evolve set**) and keep whatever scores best. Because the same small task set both suggests the edits and judges them, the loop behaves like a model that keeps peeking at its test set. It learns task-specific tricks, keeps changes that won by luck, and lets the harness grow without limit, so large practice gains shrink or vanish on unseen benchmarks [doc][sec:abstract]. RRSI does not restrict *what* may be edited; every harness component stays editable. It regularizes *how the search moves*, borrowing ideas from ML regularization. **Proposal side:**
- a cosine-annealed cap on how many independent edits one candidate may bundle (L0-like);
- a full ledger of past edits with their measured score and cost changes, so falsified ideas are not redrawn;
- when progress stalls inside the noise band, a proposal slot reserved for components the run has never tried (entropy-like).

**Selection side:**
- an LLM plus regex "leakage critic" that rejects task-specific edits *before* any evaluation is spent;
- a noise-adjusted floor (no candidate may fall more than the measured noise δ below the best score seen);
- a cost rule under which extra tokens must be paid for by measured gain;
- pruning directives for components whose recent edits stopped producing gains.

The result is a smaller evolve-set gain but a larger out-of-distribution gain and a cheaper final harness [code:rrsi/README.md][doc][sec:abstract].

---

## 2. What is improved, what is frozen, who grades

**Improved: the harness `H`.** It is a directory of source files (`Domain.harness_path`) that the proposer edits in place [code:rrsi/rrsi/domain.py:Domain.harness_dir][code:rrsi/rrsi/propose.py:Workspace]. Every edit is tagged with one component from a fixed vocabulary [code:rrsi/rrsi/components.py:K]:
- `K = [prompt, control_flow, config, output_plumbing, context_mgmt, client_tool, skill, memory, subagent]`
- the structural subset `K_str = [client_tool, skill, memory, subagent]`, meaning "components that add machinery (a tool, a skill file, a memory store, an extra policy call) rather than changing text or constants" [code:rrsi/rrsi/components.py:module docstring].
- In the paper's words the editable space is "prompts, control flow, configuration, context management, tools, skills, memory and sub-agents … the constraints act on how the search moves, not on what the harness may contain" [code:rrsi/README.md]. See also [doc].
- The starting harnesses H_0 are Terminus-2 (harbor) for coding and the archipelago `react_toolbelt` ReAct agent for workspace and engineering. The latter has ReSum context compression, head/tail tool-result truncation (`MAX_RESULT_TOKENS = 24000`, `HEAD_CHARS = 20000`) and a todo-gated `final_answer` [code:rrsi/README.md][code:rrsi/third_party/archipelago/harness_workspace/README.md][code:rrsi/third_party/archipelago/harness_workspace/tool_result.py]. The engineering H_0 is the same agent "with a bare system prompt and task wrapper" (`harness_eng/`) [code:rrsi/domains/eng/README.md].
- Each harness ships an inert `mechanisms.py` "substrate" that the proposer can wire in to activate a structural lever [code:rrsi/third_party/harbor_terminus2/mechanisms.py][code:rrsi/third_party/archipelago/harness_{workspace,eng}/mechanisms.py]:
  - `Memory` (fcntl-locked JSONL, semantic and episodic, `digest()`);
  - `ToolRegistry` (host-side callables);
  - `subcall()` (one bounded extra policy call);
  - skills: `upload_skills()` (progressive-disclosure `skills/<name>/SKILL.md`) exists only in the harbor substrate. The archipelago substrates instead tell the proposer to author `harness/skills/<name>/SKILL.md` and wire a `skill_use` tool.

**Frozen:**
- The policy model π, meaning both its weights and the model id: "The policy LLM is frozen and is a DIFFERENT model from you" [code:rrsi/rrsi/propose.py:SYSTEM_TMPL]. See also [doc].
- "the tool environment, the grader and the task set are frozen" [code:rrsi/rrsi/propose.py:SYSTEM_TMPL].
- Model name, temperature, step budget, timeouts and concurrency, which are injected externally. The constitution says "Model name, temperature, task timeouts and concurrency are injected externally — editing them is a wasted edit" [code:rrsi/domains/coding/SKILL.md]. The proposer system prompt says "model name, step budget and timeouts are injected externally so editing them has no effect" [code:rrsi/rrsi/propose.py:SYSTEM_TMPL].
- The eng instance fixes the trial timeout: a trial that exceeds it "comes back with no payload and SCORES ZERO". Refilling only failed trials is forbidden because it "would launder the score upward, and asymmetrically, since a candidate that issues more model calls hits more rate limits and more timeouts" [code:rrsi/domains/eng/config.py:TIMEOUT_PER_TASK]. The engineering instance additionally pins concurrency and the quota-route list after the baseline, so that "champion and candidate must draw the same route" [code:rrsi/domains/eng/config.py].
- The harness interface contract: entry points, class names and trajectory schema [code:rrsi/rrsi/propose.py:SYSTEM_TMPL].
- The RRSI hyperparameters: "β0 … β1 … Both are set once and then frozen for all transfer tests" [doc].

**Who grades:**
- *Score:* each benchmark's own grader.
  - Terminal-Bench 2.1 and SWE-bench Verified: hidden unit tests, reward 1.0 iff the tests pass [code:rrsi/domains/coding/adapter.py:module docstring].
  - Harvey LAB: its own per-criterion rubric judge (`score_rubric`) run with `gemini-3.5-flash`. The trial reward is criteria passed ÷ criteria total, weighted by the total [code:rrsi/domains/workspace/adapter.py][code:rrsi/domains/workspace/workspace_judge.py][code:rrsi/domains/workspace/rrsi.json:judge_model].
  - EngDesign: each task's frozen code verifier returning a binary `passed`, a continuous `combined_score` and a `valid` flag, "with no LLM anywhere in the loop" [code:rrsi/domains/eng/config.py][code:rrsi/domains/eng/adapter.py].
- *Keep or discard:* the deterministic Algorithm 2 rules (floor, cost rule, within-band rule, domain guards, argmax) [code:rrsi/rrsi/selection.py].
- *Pre-evaluation screen:* the LLM leakage critic plus a regex denylist [code:rrsi/rrsi/critic.py].
- *Liveness:* the domain smoke test ("not a selection rule") [code:rrsi/rrsi/loop.py:_draft].
- *Transfer verdict:* held-out and OOD benchmarks that never enter selection, each graded by its own judge [code:rrsi/README.md][code:rrsi/domains/eng/scripts/final_eval.sh].
- *Who proposes:* an LLM proposer, an LLM analyst with digester sub-agents, and an LLM critic, all Claude Opus 4.8 [code:rrsi/rrsi/config.py:proposer_model/analyst_model/critic_model][doc].

---

## 3. The loop, step by step (precise pseudocode)

The code's own summary is: "Each round drafts two candidates in their own git worktrees, screens them, evaluates both on the full evolve set and fast-forwards `evolve/<name>` to the winner" [code:rrsi/README.md]. Algorithm 1 (proposal side) corresponds to `rrsi.loop` (`Run.round` / `Run._draft`) and `rrsi.propose`; Algorithm 2 (selection side) corresponds to `rrsi.selection.select_round`. The package docstring says Algorithm 1 is `rrsi.loop.propose_round`, a function that does not exist. Its `__all__` likewise lists a non-existent `select` module; the real one is `selection` [code:rrsi/rrsi/__init__.py]. Both are stale names and harmless.

```text
# ============ Setup (rrsi.py baseline | calibrate) ============
BASELINE(domain, cfg):                                   [code:rrsi/rrsi/loop.py:Run.baseline]
  ensure branch evolve/<d> exists (created at HEAD)
  wt   <- worktree "incumbent" at evolve/<d>             # H_0
  ev0  <- EVALUATE(wt, job="base", ids=domain.evolve_ids(), k=cfg.k)
  if ev0.missing > cfg.invalid_missing_frac * ev0.n_expected: ABORT "baseline invalid"
  frontier <- { incumbent:{t:0, commit, harness_tree, job:"base", S:ev0.S, C:ev0.C, extra},
                S_star: ev0.S,
                trajectory:[{t:0, S:ev0.S, C:ev0.C, commit, job:"base"}],
                config: cfg }
  history.append({t:0, variant:"-", outcome:"BASELINE", S, C, accepted:true, delta_S:null, bundle:0})
  CALIBRATE(["base"])  -> runs/<d>/calibration.json       [code:rrsi/rrsi.py:main "baseline"][code:rrsi/rrsi/calibrate.py:calibrate]
  # (the CLI `baseline` command runs Run.baseline and then Run.calibrate; Run.baseline itself does not calibrate)

DELTA(): cfg.delta if not null else calibration.json["delta"]      [code:rrsi/rrsi/loop.py:Run.delta]

EVALUATE(root, job, ids, k):                              [code:rrsi/rrsi/evaluate.py:evaluate]
  domain.run(root, runs_dir, job, ids, k)                 # "Must be resume-safe" (Domain.run). eng fills missing
                                                          # trials; coding (harbor cannot resume) moves an incomplete
                                                          # job dir aside and re-runs the whole job [code:rrsi/domains/coding/adapter.py:_harbor]
  per_task, extra <- domain.score(runs_dir, job, ids, k)  # missing/infra trial -> reward 0, full denominator
  return AGGREGATE(per_task, extra)                       # S_hat, C_hat

# ============ Driver (rrsi.py run) ============
DRIVE(T, start):                                          [code:rrsi/rrsi/driver.py:drive]
  if no frontier.json: BASELINE
  if no calibration.json: CALIBRATE
  infra <- 0
  for t in start..T-1:
    if file runs/<d>/STOP exists: return
    if len(trajectory)-1 >= t+1: continue                 # round already settled
    run ROUND(t) as a subprocess
    if it failed or did not settle: infra += 1; if infra >= 3: exit     # MAX_CONSECUTIVE_INFRA = 3
    else infra <- 0

# ============ One round t = Algorithm 1 then Algorithm 2 ============
ROUND(t):                                                 [code:rrsi/rrsi/loop.py:Run.round]
  fr <- frontier;  assert tree_hash(evolve/<d>:harness) == fr.incumbent.harness_tree
  assert len(fr.trajectory) >= t+1
  δ <- DELTA();  inc_ev <- eval.json of fr.incumbent.job;  D <- domain.evolve_ids()

  # (1) F_t <- Analyze(H_t, D_evolve), using the incumbent's OWN stored evaluation (no re-run)
  traces <- BUILD_TRACES(inc_ev.per_task):
      rank tasks by mean reward ascending
      fails <- first n_fail_traces tasks; wins <- last n_success_traces tasks not in fails
      for each: load the WORST trial (fails) or the BEST trial (wins) via domain.load_trial
  require |traces| >= 0.5 * min(|D|, n_fail_traces + n_success_traces)
  F_t <- r<t>/analysis_report.json if cached else ANALYZE(traces, prior names from global_analysis.json)
  write global_analysis.json <- {failure_modes, success_habits} of F_t

  # (2-3) directives
  b_t   <- EDIT_BUDGET(t, T, b_min, b_max)
  σ_t   <- STALL([x.S for x in trajectory], t, w, δ)
  T_t   <- history.tried();   U_t <- [c in K if c not in T_t]
  E_t   <- EXPLORATION(t, σ_t, T_t, m_draft)             # {sigma, untried, m_draft, text}
  B_t   <- history.prune_set(t, n_prune)
  L_t   <- history.render(n=40)
  SKILL.md, PATTERNS.md <- domain.constitution()
  write r<t>/directives.json {t, b_t, sigma_t, tried, explore, prune_set, delta, S_star}

  # (4) draw and screen m candidates, each from the SAME incumbent, each in its own worktree
  for v in 0..m-1 (labels A,B,...):
     reserved <- σ_t==1 and U_t non-empty and v >= m - m_draft
     cand[v]  <- DRAFT(t, v, reserved)

  # (5) Evaluate(H', D_evolve, k) for the screened set (ThreadPool, eval_parallel workers)
  for c in cand with c.gate_failure == None: EVAL_CANDIDATE(t, c)

  # (6) Algorithm 2
  counts <- history.incumbent_component_counts()         # accepted-edit counts per component
  winner, decisions <- SELECT(cand, inc_ev, fr.S_star, δ, cfg, counts, domain.guards)
  write r<t>/decisions.json
  for (c, dec) in zip(cand, decisions):                   # skip if already recorded (resume)
     if c.ev is None: history.append_candidate(t, c, outcome=c.gate_failure, delta_S=None, ...)
     else:
        outcome <- "ACCEPTED" if c is winner else ("LOST" if dec.admissible else "REJECTED")
        history.append_candidate(t, c, outcome, dec.delta_S, dec.delta_C, accepted=(c is winner),
                                 S=dec.S, C=dec.C, diff=c.diff_path, detail=dec.reason)
        ATTRIBUTE(t, c)                                   # scoreboard row per edit

  # (7) H_{t+1}
  if winner: fast-forward evolve/<d> to winner.commit (must be a descendant)
             fr.incumbent <- {t:t+1, commit, harness_tree, job, S:winner.S, C:winner.C, variant}
             fr.S_star <- max(fr.S_star, winner.S)
  else:      H_{t+1} <- H_t (incumbent unchanged)
  fr.trajectory <- [x for x in trajectory if x.t <= t] + [{t:t+1, S:incumbent.S, C:incumbent.C, commit, job}]
  remove candidate worktrees

DRAFT(t, v, reserved):                                    [code:rrsi/rrsi/loop.py:Run._draft]
  (resume: if r<t>/<V>/prep.json exists, return the stored gate failure or the committed candidate)
  branch <- "<d>/r<t><V>";  wt <- new worktree on branch from evolve/<d>
  brief  <- "You are variant V of round t. m variants are drafted independently from the same
            incumbent this round and each is evaluated on the full evolve set; the best admissible
            one becomes H_{t+1}."
  prop <- PROPOSE(harness_dir(wt), F_t, L_t, SKILL.md, PATTERNS.md, b_t, E_t, reserved, B_t,
                  traces, inc per_task, digests, scoreboard[-20:], brief)
  if prop.status != "done" or prop.n_edits == 0: FINISH("no_proposal")
  for attempt in 0..repair_rounds:                        # bounded repair
     diff <- git diff of harness path (untracked files included via `git add -N`)
     verdict <- CRITIC(diff, prop.mechanism, prop.targets_mode, prop.edits)
     if verdict == accept:
        tags <- [NORMALIZE(e.component, diff, domain.component_signals) for e in prop.edits]
        if reserved and U_t and no tag in U_t:
           verdict <- reject("RESERVED EXPLORATION SLOT: at least one edit must be on a
                              never-exercised component … judged by the DIFF, and none is")
     save critic_a<attempt>.json
     if verdict == accept or attempt == repair_rounds: break
     prop <- PROPOSE(..., repair_brief={reasons, risk_notes, your_declared_edits})   # tree keeps prior edits
     if prop.status != "done": break
  if verdict != accept: FINISH("critic_reject", reasons)
  diff <- git diff;  for e in edits: e.component <- NORMALIZE(e.component, diff, signals)
  commit <- git commit (harness path only) "r<t><V>: <mechanism>"
  ok, detail <- domain.smoke(wt, runs, "r<t><V>_smoke", domain.smoke_ids(inc per_task))
  if not ok: FINISH("smoke_fail")
  write prep.json {commit, branch, edits, diff_path, mechanism}
  return Candidate(V, edits, diff_path, branch, commit)
  FINISH(g): write prep.json {gate_failure:g, ...}; remove worktree and branch; return Candidate(gate_failure=g)

EVAL_CANDIDATE(t, c):                                     [code:rrsi/rrsi/loop.py:Run._evaluate]
  reuse r<t>/<V>/eval.json if it exists
  for attempt in 0..1:
     try ev <- EVALUATE(worktree r<t><V>, job "r<t><V>", D, k) except -> c.gate_failure <- "eval_invalid"; return
     if ev.missing <= invalid_missing_frac * ev.n_expected: break      # else retry once
  if still too many missing: c.gate_failure <- "eval_invalid"; return
  save eval.json; c.ev <- ev

SELECT(cands, inc, S_star, δ, cfg, counts, guard_fn):     [code:rrsi/rrsi/selection.py:select_round/judge]
  for c in cands:
     if c.ev is None: decision <- inadmissible(reason = c.gate_failure)
     ΔS <- c.S - inc.S;  ΔC <- (c.C - inc.C)/inc.C  (0 if either C missing);  ν <- NOVELTY(c.components, counts)
     if c.S < S_star - δ:             inadmissible "below noise-adjusted floor"
     elif not COST_RULE(ΔS, ΔC, ν, δ): inadmissible "cost rule failed"
     elif guard_fn(inc, c.ev) != []:  inadmissible "domain guard violated"
     else admissible
  winner <- argmax_{admissible} c.S   (first on ties; None if the admissible set is empty)

PROPOSE(...):                                             [code:rrsi/rrsi/propose.py:propose]
  system  <- SYSTEM_TMPL(domain proposer brief, K)
  stable  <- "=== CONSTITUTION (SKILL.md) ===" + SKILL.md + "=== PATTERN LIBRARY (PATTERNS.md) ===" + PATTERNS.md   # prompt-cached prefix
  context <- variant brief | EDIT HISTORY L_t | ATTRIBUTION SCOREBOARD | EXPLORATION DIRECTIVES E_t
             | COMPONENTS TO PRUNE B_t | THREE-LENS ANALYSIS REPORT F_t | PER-TASK DIGESTS
             | CURRENT HARNESS SOURCE H_t (full dump) | THIS ROUND'S EDIT BUDGET b_t | TASK (or REPAIR ROUND)
  for turn in 1..40:                                      # MAX_TURNS = 40
     act <- LLM(json_only) given context + transcript
     list_files | read_file | list_traces | read_trace(task_id, from_step, to_step, detail)
     | edit_file(path, old, new)   # `old` must occur exactly once
     | write_file(path, content)   # new files only, allowed extensions
       (at most MAX_EDITS = 80 successful file edits)
     | done(summary, edits[]):
          reject back to the model if: file changes == 0 while edits are declared;
            edits empty while files changed; len(edits) > b_t; any edit is missing
            id/component/hypothesis/targets_mode/predicted_affected/retroactive_check;
            component not in K; a reserved slot with no edit whose component is in U_t
          else return {status:"done", edits, mechanism, targets_mode, n_edits}
     | abort: bounced up to 3 times ("there is no abort action"); the 4th returns status "abort"
  return {status:"max_turns"}

CRITIC(diff, summary, targets, edits):                    [code:rrsi/rrsi/critic.py:review]
  hits <- regexes in GENERIC_PATTERNS + domain.critic_patterns that match the diff
  if hits: return reject("precheck: …")
  if diff is empty: return reject("empty diff")
  payload <- CANDIDATE SUMMARY | TARGETS | DECLARED EDITS (≤20k chars) | DIFF (≤120k chars)
             | STATE FILES (≤20k chars; review() has a state_files argument but loop.py never passes it,
               so this section is always empty in the released code)
  up to 3 LLM calls (json): return the first {"verdict": accept|reject, reasons, risk_notes} that parses
  (on a JSON decode error, the first {...} span found by regex is returned without checking its verdict field)
  else reject("critic output unparseable after 3 attempts")

ANALYZE(traces):                                          [code:rrsi/rrsi/analyst.py:analyze]
  render every trace (detail=True) to r<t>/analysis/rendered/<task>.txt
  for turn in 1..30:
     act <- LLM: digest_many(requests ≤ 8 per call; lens ∈ {failure, capability_gap, success}, optional questions)
            -> DIGEST each request (6 in parallel), save r<t>/analysis/digests/<task>_<lens>.json
          | report(failure_modes, capability_gaps, success_habits) -> sort failure_modes by -n_tasks; return
            (the prompt asks the LLM to rank by "total impact (number of tasks weighted by how much score
             the mode costs on each)", but the code then re-sorts by raw n_tasks)
  on max turns: return empty report with error

DIGEST(task, lens):                                       [code:rrsi/rrsi/digester.py:digest_task]
  ≤15 turns of read_file | glob | grep | bash (allowlist grep/egrep/head/tail/wc/cat/ls/find/cut/sort/uniq/awk/jq/sed/tr/paste;
  denied: redirection, backticks, $(...), sed -i, rm, mv, cp, tee, touch, mkdir, chmod, python; 30 s timeout)
  analyst questions are passed through, capped at 5
  return a digest following the lens schema, ≤ 6000 chars (bounced if longer)

ATTRIBUTE(t, c):                                          [code:rrsi/rrsi/loop.py:Run.attribute]
  thr <- domain.regression_threshold(k)   (default 1/k; workspace 0.05)
  per edit: pred <- predicted_affected ∩ tasks; hits <- {p in pred : cand mean(p) > inc mean(p)}
            drops <- {x : cand mean(x) - inc mean(x) <= -thr}
            row {t, variant, edit_id, component, hypothesis[:160], n_predicted, predicted_hit=hits,
                 hit_rate=|hits|/|pred|, unpredicted_regressions=(drops \ pred)[:12]} -> attribution.jsonl
```

Maintenance commands [code:rrsi/rrsi.py][code:rrsi/rrsi/loop.py]:
- `readjudicate --t t`: re-runs Algorithm 2 on round t's **stored** measurements after a δ or weight change. It recomputes `S* = max(trajectory[:t+1].S)` and counts accepted components only from rounds `< t`, rewrites that round's history records, and moves the branch with `update-ref`. Later rounds must be removed first.
- `reevaluate --t t`: re-measures the committed candidates after an infrastructure failure, then readjudicates.
- `heldout --label L [--ref R] [--set heldout|evolve]`: evaluates any git ref on the held-out split.
- `smoke` and `status`.

---

## 4. Formulas, objectives, acceptance rules, schedules and defaults

### 4.1 Setup and estimator

- Agent `A = (π, H)` with frozen π. The verifier gives `r(x, τ) ∈ [0,1]`; `c(τ)` is the policy tokens of a trajectory [code:rrsi/rrsi/evaluate.py:module docstring][sec:inkeast "Eq. 1"].
  - `S(H;D) = E_{x~D} E_{τ~A(·|x)} [ r(x,τ) ]`
  - `C(H;D) = E_{x~D} E_{τ~A(·|x)} [ c(τ) ]`  [sec:inkeast]
- Empirical estimator, Eq. (estimate) [code:rrsi/rrsi/evaluate.py:aggregate]:
  - `Ŝ(H) = 1/(k|D|) Σ_x Σ_j r(x, τ_x^(j))`
  - `Ĉ(H) = 1/(k|D|) Σ_x Σ_j c(τ_x^(j))`
  - As implemented: weighted rewards, `Ŝ = Σ r·w / Σ w`. Weights are 1 except in Harvey LAB, where `r` = criteria passed ÷ total and `w` = total, so Ŝ is the overall criterion pass rate.
  - **A missing trial (crash, timeout, infrastructure) counts `r = 0` with the full denominator**, "so a candidate cannot look better by destroying the trials it finds hard."
  - Per-domain details of "missing":
    - Coding: only harbor/docker environment errors count as missing. `AgentTimeoutError` and `VerifierTimeoutError` are scored normally as failures [code:rrsi/domains/coding/adapter.py:infra_failure].
    - Workspace: a missing trial gets weight = the task's criteria count read from `task.json` [code:rrsi/domains/workspace/adapter.py:score].
    - Eng: a missing trial also counts `valid = 0` and `no_payload = 1`, so it feeds the domain guards [code:rrsi/domains/eng/adapter.py:score].
  - `Ĉ` is the mean over trials that have a positive token count (missing trials are excluded from the cost mean).
- Invalid evaluation: a candidate is `eval_invalid` if `missing > invalid_missing_frac · n_expected` after one retry [code:rrsi/rrsi/loop.py:_evaluate]. The baseline aborts under the same condition [code:rrsi/rrsi/loop.py:baseline].
- Generic (unregularized) harness-evolution loop. inkeast reports the loop as Eq. 2 and "harness evolution treats H as the optimization variable, selecting by Ŝ and Ĉ from k trials per task" as Eq. 3 [sec:inkeast]. The name "unconstrained proposer P_0" comes from [sec:roacher]. The loop:
  1. run H_t on D_evolve;
  2. summarize the traces into feedback F_t;
  3. an unconstrained proposer P_0 drafts candidates;
  4. score them on the same D_evolve;
  5. promote the best.

  Whether "promote the best" is unconditional or requires beating H_t is not stated in any accessible source [unverified].
- RRSI transition rule [sec:shibing][code:rrsi/rrsi/propose.py:module docstring]:
  - `𝓗_t ~ P_reg(· | H_t, F_t, L_t, b_t, E_t, B_t)`
  - `H_{t+1} = argmax_{H' ∈ 𝓗_t ∩ 𝒜_t} Ŝ(H')`, or `H_t` if `𝓗_t ∩ 𝒜_t = ∅`.

### 4.2 Proposal side (Algorithm 1)

**Annealed L0 edit budget, Eq. (anneal)** [code:rrsi/rrsi/schedule.py:edit_budget]:

```
b_t = ceil( b_min + (b_max − b_min) · ½ · (1 + cos(π t / T)) ),   t = 0..T−1
constraint: ||z_t||_0 ≤ b_t   (number of independent edits active in one candidate)
```

- The implementation clamps t to [0, T] and computes `ceil(round(v, 9))` to avoid a floating-point `1.0000000002 → 2` error. If T ≤ 0 it returns b_max.
- The constraint "bounds ||z_t||_0 … and nothing else: the set of mechanisms the harness may eventually contain is not restricted" [code:rrsi/rrsi/schedule.py][sec:harry].
- It is enforced in the proposer's `done()`: `len(edits) > b_t` is bounced [code:rrsi/rrsi/propose.py:propose].
- An "edit" is "ONE independent, attributable change (it works on its own and can answer 'which tasks will it move' by itself). Dependent parts are ONE edit" [code:rrsi/rrsi/propose.py:SYSTEM_TMPL].

Computed schedules. These are [inferred] outputs of the code function using the rrsi.json values:

| instance | T | b_min..b_max | b_t for t = 0..T−1 |
|---|---|---|---|
| coding | 20 | 1..4 | 4×8, 3×5, 2×7 |
| workspace | 20 | 1..3 | 3×10, 2×10 |
| eng | 40 | 1..4 | 4×16, 3×9, 2×15 |
| overview's illustrative values [doc] | 10 | 1..4 | 4,4,4,4,3,3,3,2,2,2 |

⚠ **Discrepancy.** Because of the `ceil` and because `t` only reaches `T−1`, **b_t never reaches b_min = 1 inside a run under the code**. The last round gets 2. For example, at T = 20, t = 19 the value is `1 + 3·½·(1+cos(19π/20)) = 1.018 → 2`. The unit test only checks `edit_budget(T, T) == b_min` [code:rrsi/tests/test_core.py:test_schedule_matches_eq_anneal].
- A WebSearch snippet of the paper says the budget "begins at three or four independent edits, depending on domain, and ends at one" [sec:search].
- inkeast, roacher and tony reproduce Eq. 4 *without* the ceil [sec:inkeast][sec:roacher][sec:tony]; shibing and the overview show it *with* the ceil [sec:shibing][doc]. inkeast additionally states the paper's settings as b_max = 4, b_min = 1 for coding and engineering and b_max = 3, b_min = 1 for workspace [sec:inkeast]. That matches the rrsi.json files.
- The overview's prose says "later rounds allow only one or two" [doc].
- A faithful re-implementation should make this choice explicit (a flag `budget_rounding ∈ {ceil, floor_at_bmin_last}`) [inferred].

**Edit history L_t, Eq. (history)** [code:rrsi/rrsi/history.py]:

```
L_t = {(t_i, l_i, h_i, d_i, ΔS_i, ΔC_i, a_i) : i ≤ n_t}
```

- There is one record per edit. `a_i = 1` only for the edits of the candidate that became H_{t+1} [code:rrsi/README.md].
- A bundle of n edits receives one measurement, which every edit in the bundle carries.
- Candidates dropped before measurement (critic reject, smoke fail, invalid evaluation) are recorded with `ΔS = None` and "do not enter T_t or g_t".

**Tried set, recent yield, untried set, stall flag and prune set** [code:rrsi/rrsi/history.py]:

```
T_t   = { l_i : i ≤ n_t, ΔS_i measured, l_i ∈ K }                           (tried components)
g_t(l) = max{ ΔS_i : l_i = l, t − t_i ≤ n_prune },   max(∅) = −∞            Eq. (yield)
U_t   = K \ T_t                                                             (untried)
σ_t   = 1[ S_t − S_{t−w} ≤ δ ]   (0 while t < w)                             Eq. (explore)
B_t   = { l ∈ T_t : g_t(l) ≤ 0 }                                             Eq. (prune)
E_t   = (σ_t, U_t, m_draft)  (+ text handed to the proposer)
```

- `S_t` is the trajectory entry for round t, i.e. the incumbent's measured Ŝ at the start of round t [code:rrsi/rrsi/loop.py:round].
- The `history.py` docstring writes the paper's form `T_t = {l_i : i ≤ n_t}` with no "measured" condition. The implementation (`History.tried`) counts only records with a non-null ΔS and a component in K, so critic-rejected, smoke-failed and eval-invalid edits do not make a component "tried" [code:rrsi/rrsi/history.py:tried,measured][code:rrsi/tests/test_core.py:test_history_summaries_prune_and_explore]. The paper text may differ [unverified].
- The analogous paper statements are:
  - "RRSI tracks whether recently exercised components produce strictly positive measured gain within a fixed pruning window; persistently unproductive components are reported to the proposer as deletion targets" [sec:inkeast][sec:shibing];
  - during a stall, "a small fraction of the proposal budget is reserved for components not yet exercised in this run (U_t = K \ T_t)" [sec:inkeast].
- The window is inclusive: at t = 5 with n_prune = 4, records from rounds t_i ≥ 1 count [code:rrsi/tests/test_core.py:test_history_summaries_prune_and_explore].
- **Reserved exploration slots.** If σ_t = 1 and U_t ≠ ∅, the last `m_draft` variants, i.e. those with index `v ≥ m − m_draft` (variant B when m = 2 and m_draft = 1), must ship at least one edit whose component is in U_t. The rule is checked on the declared tags in `done()` and again on the diff-normalized tags after the critic accepts [code:rrsi/rrsi/loop.py:round,_draft][code:rrsi/rrsi/propose.py].
- Paper phrasing: "When progress over a three-round stall window stays within the noise band, one proposal slot is reserved for an editable component not previously exercised" [sec:search].
- **Pruning is a directive, not an automatic deletion.** B_t is handed to the proposer together with "the accepted machinery to remove" (accepted edits in that component). "An edit that removes that machinery is a legitimate edit (component = the pruned component, hypothesis = "prune: …")" [code:rrsi/rrsi/propose.py:SYSTEM_TMPL]. A prune edit then goes through the same critic and selection rules [inferred from code].

**Component tagging and novelty** [code:rrsi/rrsi/components.py]:
- `normalize(declared, diff, signals)` keeps a declared tag only if it is in K *and* the diff contains a regex signal for it (`has_evidence`). Otherwise the tag is recovered by `classify_diff`:
  1. `text_only(diff)`: every changed line is a string literal or comment → `prompt`;
  2. otherwise the first matching component among the domain signals, then the generic signals (`memory: \bMemory\(, \.remember\(, \.recall\(, _STATE_DIR`; `skill: skills/, SkillRegistry, skill_use, skill_catalog, upload_skills`; `client_tool: ToolRegistry, register_tool, tool_spec, CLIENT_TOOLS`; `subagent: \bsubcall\(, sub_agent, subagent`);
  3. the default is `prompt`.
- **Domain signals are checked before the generic ones, and `has_evidence` also uses them.** A declared tag, including a non-structural one such as `control_flow` or `prompt`, survives only if the diff matches that component's domain or generic regex. The domain signal lists are as follows [code:rrsi/domains/*/adapter.py:component_signals]:
  - coding: `memory: TBMH_STATE_DIR`; `subagent: generate_response`; `context_mgmt: _summarize|summariz|proactive_summarization|unwind|free_tokens`; `output_plumbing: _limit_output_length|terminal_output|truncat|_collapse|paste.?buffer|load-?buffer`; `control_flow: _run_agent_loop|episode|is_task_complete|_pending_completion|keystrokes`; `config: "= *\d+ *#"|threshold|max_bytes|duration`; `prompt: templates/.*\.txt|_VERIFICATION|_get_.*hint|prompt|system`.
  - workspace and eng: `memory: DRMH_STATE_DIR|digest\(`; `context_mgmt: resum\.py|ReSum|summariz|compact|_should_compress|keep_last`; `output_plumbing: tool_result\.py|truncat|HEAD_CHARS|TAIL_CHARS|MAX_RESULT_TOKENS|_process_result`; `control_flow: _handle_tool_calls|def step|final_answer|has_incomplete_todos|max_steps|_execute_mcp_tool`; `config: "= *\d+ *#"|threshold|_CAP\b|_LIMIT\b`; `prompt:` workspace `SYSTEM_PROMPT|WRAPPER_TEMPLATE|prompt|description`, eng `prompts\.py|SYSTEM_PROMPT|TASK_TEMPLATE|prompt|description`.
  - `normalize` is called once per edit but always on the **whole candidate diff**, not on a per-edit hunk [code:rrsi/rrsi/loop.py:_draft]. Two consequences follow:
    - a declared tag is kept if evidence for it appears *anywhere* in the bundle;
    - every edit whose tag lacks evidence is re-tagged to the same `classify_diff(whole diff)` result, i.e. the *first* matching component in list order. Some patterns are very broad (`prompt`, `system`, `description`, `episode`) [inferred from code].
- Novelty `ν(l') = |{ c ∈ set(edit components) : c ∈ K_str and incumbent_counts[c] = 0 }|`. Here `incumbent_counts[c]` is the number of accepted edit records on component c. ν "only ever tie-breaks a candidate inside the noise band."

### 4.3 Selection side (Algorithm 2)

For every screened candidate H' with measurement (S', C') against the incumbent (S_t, C_t) [code:rrsi/rrsi/selection.py:module docstring]:

```
ΔS = S' − S_t
ΔC = (C' − C_t) / C_t                   (relative; 0 if either token count is missing)

c  = [ ΔC ≤ β0 + β1·ΔS ]                         if ΔS > δ          Eq. (tokenbudget)
     [ w_s·ΔS − w_c·ΔC + w_n·ν(l') > 0 ]          otherwise          (Alg. 2 line 5, "shaped rule")

admissible  ⇔  S' ≥ S* − δ   ∧   c   ∧   every domain guard holds     Eq. (floor)
H_{t+1} = argmax_{H' admissible} S'   (else H_t)
S*      = max(S*, S_{t+1})
```

- **Order of checks and reason strings** [code:rrsi/rrsi/selection.py:judge]:
  1. gate failure ("critic_reject", "smoke_fail", "eval_invalid", "no_proposal");
  2. "below noise-adjusted floor";
  3. "cost rule failed";
  4. "domain guard violated".
- **The winner is argmax of S' among admissible candidates, not argmax of the shaped score.**
- **Wording mismatch with the paper figure.** The repository figure says "Set H_{t+1} to the best admissible candidate *if it improves robustly*; otherwise keep H_t" [code:rrsi/assets/rrsi_overview.png]. In code, H_t is never itself a candidate in the argmax. A within-band candidate with ΔS ≤ 0 that saves tokens (or earns a novelty bonus) and clears the floor is admissible and replaces H_t, even though it does not "improve" S [code:rrsi/rrsi/selection.py][code:rrsi/tests/test_core.py:test_cost_rule_both_branches]. Treat the code as normative.
- **S*** is the best *incumbent* score seen, i.e. the maximum over the trajectory. ΔS, by contrast, is measured against the *current* incumbent, which can be below S* after a within-band acceptance [code:rrsi/rrsi/loop.py:round].
- **The noise floor.** The constitution words it as: "A candidate can never walk the line downhill through regressions small enough to look like noise" [code:rrsi/domains/coding/SKILL.md]. The paper's own sentence is available only through inkeast's Chinese paraphrase, which gives the floor as Eq. 5 ("防止搜索顺着一串各自小到可被误判为噪声的回退往下走", i.e. it prevents the search from walking downhill through a series of regressions, each small enough to be mistaken for noise) [sec:inkeast]. The project page says "A gain must clear the variance measured on the unchanged base harness" [sec:projectpage].
- **Cost rule numbering.** inkeast gives the cost rule as **Eq. 7** of the paper, with the β values in **Table 5** [sec:inkeast].
- **Cost rule** (overview's wording): "relative cost increase ≤ β0 + β1 × score gain … β0 is the cost increase allowed for a negligible gain; β1 is how much extra cost each point of gain may buy" [doc]. Code wording: "a gain that also SAVES tokens always passes" [code:rrsi/domains/coding/SKILL.md].
- **Within-band rule**, as the constitution explains it to the proposer: "a neutral candidate survives by cutting tokens or by landing a working, non-regressing structural mechanism, never by a coin-flip gain" [code:rrsi/domains/coding/SKILL.md]. Note that the within-band branch also covers **negative** ΔS above the floor.
- **Units** [code:rrsi/rrsi/config.py:module docstring]: "scores S are fractions in [0,1] …, cost C is mean policy tokens per trial, ΔC is RELATIVE …, so beta0 = 0.10 means '10% more tokens for free' and beta1 = 40 means 'each +1pp of S buys +40%'."
- **Domain guards** are non-compensatory [code:rrsi/rrsi/domain.py:Domain.guards]. They are used only by the eng instance [code:rrsi/domains/eng/adapter.py:EngDomain.guards]:

  ```
  violate if  valid_rate(inc) − valid_rate(cand) > max_valid_rate_drop (0.03)
  violate if  no_payload_rate(cand) − no_payload_rate(inc) > max_no_payload_rise (0.02)
  ```

  These catch cases where "a scaffold can lift the average by pushing designs into aggressive territory that violates a hard constraint outright" [code:rrsi/domains/eng/SKILL.md].

Worked implications of the per-domain constants. These are [inferred] arithmetic on the code values:
- Coding (`w_s = 0, w_c = 15, w_n = 0.5`): inside the band a candidate is admitted iff `ΔC < ν/30`. So with ν = 0 it needs a strict token saving, and with ν = 1 it may add less than 3.3% tokens. This matches the note "inside the band a candidate is admitted only for a token saving or a new structural component, never for a within-band score gain" [code:rrsi/domains/coding/rrsi.json:_doc].
- Workspace (`w_s = 1414`): 10 extra criteria out of about 14,100 contribute +1.0, which offsets `ΔC = 1/15 ≈ 6.7%`.
- Eng (`w_s = 244, w_c = 2`): one extra pass contributes +1.0, which offsets `ΔC = 0.5`.

### 4.4 Noise band δ (calibration)

`δ = z · sd(null ΔS)` with `z = delta_z = 2.0`, "so an unchanged harness clears the floor S* − δ about 97.5% of the time" [code:rrsi/rrsi/calibrate.py:module docstring].
- **R ≥ 2 independent base evaluations:** `sd_null = stdev({Ŝ_r}) · √2` (the difference of two independent evaluations). This is preferred unless it is degenerate (0). The code also records `max_abs_diff` [code:rrsi/rrsi/calibrate.py:calibrate].
- **A single base evaluation:** bootstrap Ŝ by resampling trials *within each task*, respecting weights (`reps = 2000`, `seed = 7`). This gives `se = pstdev(bootstrap Ŝ)` and `sd_boot = √2 · se · √(k_pooled / k_single)`, where the last factor rescales a pooled multi-evaluation back to a single evaluation's k [code:rrsi/rrsi/calibrate.py:bootstrap_se,pooled,calibrate].
- **In the paper runs δ was fixed per instance** [code:rrsi/domains/*/rrsi.json:_doc]:
  - coding `δ = 0.017` ("3 passes of 178");
  - workspace `δ = 0.004` ("60 criteria of about 14,100");
  - eng `δ = 0.020` ("5 passes of 244").
  Setting `"delta": null` triggers re-estimation with `calibrate`. The README describes this as "bootstrap over trials of the base evaluation, or repeated base evaluations" [code:rrsi/README.md].
- The eng instance also ships a richer, domain-level calibrator that writes `data/metric.json` [code:rrsi/domains/eng/bench/calibrate.py]. It computes:
  - a headroom analysis (tasks pinned at 0 or 1);
  - a 2-vs-2 half-split null on both the continuous and the binary axis, which requires k ≥ 4;
  - a pass-count null obtained by resampling each task from its own baseline pass rate (2000 reps), giving `noise_band_passes = max(2, round(|p01|))`;
  - a null distribution of hard drops and gains, with `hard_drop = max(0.25, p99(|half-split per-task move|) + 0.03)` and `max_net_hard_drops = max(1, net_p99)`.
  - It also writes `max_hard_drops = max(2, drops_p99)`. `noise_band_passes` is set at the null's 1st percentile "so an unchanged harness clears it ~99% of the time". It writes the file only with `--write` [code:rrsi/domains/eng/bench/calibrate.py:main].
  - The core RRSI loop does not read `metric.json`; selection uses the fixed δ in `rrsi.json`. The file's docstring says it is "consumed by bench/score.py and loop.py", but there is no eng `loop.py`, and `final_eval.sh` still greps for a process named `rloop.py`.
  - `bench/score.py` does read `metric.json`, for `HARD_DROP_DELTA`, default 0.50. `scripts/final_eval.sh` calls it to report the final paired evaluations [code:rrsi/domains/eng/bench/score.py][code:rrsi/domains/eng/scripts/final_eval.sh].
  - So the calibrator appears to be a leftover of an earlier stand-alone eng loop. Today it only feeds reporting, not selection [inferred].

### 4.5 Hyperparameters

Defaults from `RRSIConfig`, described there as "Hyperparameters of RRSI (paper Appendix 'Hyperparameters')" [code:rrsi/rrsi/config.py], next to the per-instance values [code:rrsi/domains/{coding,workspace,eng}/rrsi.json]:

| symbol | meaning | default | coding | workspace | eng |
|---|---|---|---|---|---|
| T | rounds | 20 | 20 | 20 | 40 |
| k | trials per task | 2 | 2 | 2 | 4 |
| m | candidates per round | 2 | 2 | 2 | 2 |
| b_min / b_max | edit-budget bounds | 1 / 4 | 1 / 4 | 1 / 3 | 1 / 4 |
| w | stall window | 3 | 3 | 3 | 3 |
| m_draft | reserved exploratory slots | 1 | 1 | 1 | 1 |
| δ | noise band | null (calibrate) | 0.017 | 0.004 | 0.020 |
| delta_z | z for calibration | 2.0 | 2.0 | 2.0 | 2.0 |
| β0 | free relative cost | 0.10 | 0.10 | 0.10 | 0.15 |
| β1 | relative cost per unit ΔS | 40 | 44.5 ("25% tokens per additional pass") | 35.4 ("25% tokens per 100 additional criteria") | 24.4 ("10% tokens per additional pass") |
| w_s | shaped: score weight | 100 | 0.0 | 1414 ("0.1 per criterion") | 244 ("1 per pass") |
| w_c | shaped: cost weight | 15 | 15 | 15 | 2 |
| w_n | shaped: novelty weight | 0.5 | 0.5 | 0.5 | 0.5 |
| n_prune | yield window | 4 | 4 | 4 | 5 |
| repair_rounds | critic → proposer repairs | 5 | 5 | 5 | 5 |
| invalid_missing_frac | invalid-evaluation threshold | 0.15 | 0.2 | 0.1 | 0.15 |
| n_fail_traces / n_success_traces | analyst evidence | 22 / 6 | 30 / 6 | 60 / 6 | 22 / 6 |
| eval_parallel | concurrent candidate evaluations | 1 | 1 | 1 | 1 |
| guards | non-compensatory | – | – | – | max_valid_rate_drop 0.03, max_no_payload_rise 0.02 |
| smoke | liveness tasks | – | `extract-elf`, `fix-git` (1 trial; passes if both trials exist with no exception, test outcome ignored) | first 2 evolve ids (produce only, no judge; passes if both trajectories exist) | first `smoke_n` = 4 tasks solved on *every* incumbent trial, else the first 4 evolve ids; 1 trial; passes only if no trial is missing, no-payload rate = 0 and mean combined_score > 0 |

- β values per domain are also reported as "Table 5" of the paper [sec:inkeast]. The b_max values (4, 3, 4) agree [sec:inkeast].
- The smoke checks are listed from [code:rrsi/domains/{coding,workspace,eng}/adapter.py:smoke,smoke_ids] and [code:rrsi/domains/workspace/split_workspace.py]. Every domain first runs `compileall` on the harness. Coding and eng also run a constructor or template check.
- The overview's illustrative values are `b_min = 1, b_max = 4, T = 10`, with the note "the paper's own values are in its appendix" [doc].
- Hyperparameter selection: shibing reports that the paper chose these hyperparameters on the evolve set and then froze them [sec:shibing]. The overview says the β values are "set once and then frozen for all transfer tests" [doc].
- Fixed internal constants [code]:

  | where | constants |
  |---|---|
  | proposer | `MAX_TURNS = 40`, `MAX_EDITS = 80`, `TRACE_READ_CAP = 60,000` chars; tool-result caps 150,000 chars (read actions) and 4,000 chars (others) |
  | analyst | `MAX_TURNS = 30`, `DIGEST_PARALLELISM = 6`, ≤ 8 requests per `digest_many` |
  | digester | `MAX_TURNS = 15`, `DIGEST_MAX_CHARS = 6000`, `TOOL_OUT_CAP = 25,000`, `BASH_TIMEOUT = 30` s |
  | critic | ≤ 3 parse attempts; diff capped at 120,000 chars; declared edits at 20,000 chars |
  | llm | `MAX_TOKENS = 20,000`, `max_retries = 6` with backoff `min(2^attempt, 30)` s, round-robin over GCP projects, ephemeral prompt cache on the constitution prefix |
  | driver | `MAX_CONSECUTIVE_INFRA = 3` |
  | history.render | last 40 records; at most the 4 most recent unmeasured records |
  | scoreboard | last 20 rows |

### 4.6 Regularization analogies (not literal penalties)

**The sources disagree** on which Lp norm maps to which mechanism:

| mechanism | overview [doc] | secondary read-throughs [sec:shibing][sec:inkeast][sec:roacher][sec:awesome] | repository figure `assets/rrsi_overview.png` [code] | code docstrings [code] |
|---|---|---|---|---|
| annealed edit budget | L0 | L0 cardinality | "A. Annealed update sparsity" (no norm label) | "Annealed L0 update budget" (`schedule.py`, `config.py`) |
| pruning | L1 (lasso) | Lasso / L1 | "G. Structural pruning (**L0-style**)" | – |
| cost / complexity acceptance | L2 (ridge) | Ridge / L2 in the method sections of shibing, inkeast, roacher and tony. inkeast's Fig. 4 paraphrase, however, calls it an "L1-style budget", and awesome does not label it | "F. Complexity-aware acceptance (**L1-style**)" | "L1 cost rule" (`config.py`) |
| stall exploration | entropy bonus | diversity / entropy | "C. Structured exploration" | – |
| floor + falsified-idea ledger | holdout-reuse theory (Dwork et al. 2015) | Dwork et al. 2015 | "E. Noise-adjusted performance floor", "B. Evidence-aware credit assignment" | – |

Both the overview and the secondary sources stress that "these are analogies, not literal penalties on an objective" [doc][sec:inkeast]. A re-implementation should not depend on the labels [inferred].

**Which side each mechanism belongs to (the paper's own grouping).** Every paper-level source puts **pruning on the selection side**:
- The figure groups A–C (annealed sparsity, evidence-aware credit assignment, structured exploration) as "Proposal-side regularization: controls how search capacity is used". It groups D–G (leakage screening, noise-adjusted floor, complexity-aware acceptance, structural pruning) as "Selection-side regularization: controls which gains may become permanent state" [code:rrsi/assets/rrsi_overview.png].
- The abstract: "The selector is equipped with a critic and a pruner: the critic screens benchmark-specific proposals, while the pruner removes changes that are too small, too expensive, or no longer useful" [sec:abstract]. In the abstract's wording, "pruner" therefore covers the floor ("too small"), the cost rule ("too expensive") and pruning proper ("no longer useful").
- The README, the overview's "When deciding what to keep" list, and shibing agree [code:rrsi/README.md][doc][sec:shibing].

In code, however, pruning acts through the *proposer* (the B_t directive). This matters for how the ablation groups are reproduced (§7.4, §9.1).

---

## 5. Data structures and artifacts

All paths are under `runs/<domain>/` [code:rrsi/rrsi/loop.py:Run.__init__].

| artifact | fields / content |
|---|---|
| `frontier.json` | `domain`; `incumbent{t, commit, harness_tree (12-char git tree hash of the harness path), job, S, C, extra, variant}`; `S_star`; `trajectory[{t, S, C, commit, job}]` (one entry per settled round; `trajectory[0]` is H_0); `config` (a dump of RRSIConfig) [code:rrsi/rrsi/loop.py:baseline,round] |
| `calibration.json` | `delta, z, sd_null, sd_null_bootstrap, se_bootstrap, method, n_evals, k, n_tasks, S_base, C_base, [S_per_eval, max_abs_diff], jobs` [code:rrsi/rrsi/calibrate.py:calibrate] |
| `history.jsonl` (L_t, one line per EDIT) | `t, variant, edit_id, component, hypothesis (or mechanism), targets_mode, predicted_affected, diff (path to diff.patch), delta_S (6 dp or null), delta_C (6 dp or null), accepted, outcome ∈ {ACCEPTED, LOST, REJECTED, critic_reject, smoke_fail, eval_invalid, no_proposal, not_evaluated, BASELINE}, S (6 dp), C (1 dp), bundle (edits in the candidate), detail (≤ 600 chars: the decision reason), ts` [code:rrsi/rrsi/history.py:append_candidate]. Rendered for the proposer with the keys `t, variant, edit_id, component, hypothesis, targets_mode, delta_S, delta_C, accepted, outcome, bundle, detail` [code:rrsi/rrsi/history.py:render] |
| `attribution.jsonl` (scoreboard) | `t, variant, edit_id, component, hypothesis[:160], n_predicted, predicted_hit[], hit_rate, unpredicted_regressions[:12]` [code:rrsi/rrsi/loop.py:attribute] |
| `global_analysis.json` | `failure_modes`, `success_habits` of the latest report, used for stable naming across rounds [code:rrsi/rrsi/loop.py:round] |
| `r<t>/analysis_report.json` (F_t) | `failure_modes[{mode, n_tasks, affected_tasks[], description, needed_instead, representative_evidence[{task_id, where, quote}]}]`, `capability_gaps[{gap, n_tasks, affected_tasks, description, representative_evidence}]`, `success_habits[{habit, n_tasks, description, risk_if_broken}]`, `n_digests`, optional `error` [code:rrsi/rrsi/analyst.py:SYSTEM_TMPL,analyze] |
| `r<t>/analysis/rendered/<task>.txt`, `r<t>/analysis/digests/<task>_<lens>.json` | rendered traces. Digest schemas: **failure** `{task_id, lens, blocker, narrative, evidence[{where, quote}], verifier_evidence, capability_note, needed_instead}`; **capability_gap** `{task_id, lens, wanted, why_couldnt, evidence[], workaround_seen}`; **success** `{task_id, lens, habits[{habit, where_shown}], risk_if_removed}` [code:rrsi/rrsi/digester.py:SCHEMAS] |
| `r<t>/directives.json` | `t, b_t, sigma_t, tried[], explore{sigma, untried[], m_draft, text}, prune_set[{component, recent_best_gain, accepted_edits_in_incumbent[{t, edit_id, hypothesis, delta_S}]}], delta, S_star` [code:rrsi/rrsi/loop.py:round][code:rrsi/rrsi/history.py:prune_set] |
| `r<t>/<V>/proposal.json`, `proposal_r<n>.json` | `status ∈ {done, abort, max_turns}, summary, edits[], mechanism, targets_mode, n_edits, log[]` (actions, truncated) [code:rrsi/rrsi/propose.py:propose] |
| edit record (declared in `done`) | `id, component ∈ K, hypothesis, targets_mode, why_not_lower_lever, trigger_condition, predicted_affected[task ids], retroactive_check (corrective / preservative / transfer), regression_risk`; `mechanism` defaults to `hypothesis`. Required: `id, component, hypothesis, targets_mode, predicted_affected, retroactive_check` [code:rrsi/rrsi/propose.py] |
| `r<t>/<V>/diff.patch`, `critic_a<n>.json`, `critic.json` | a git diff of the harness path including new files; the verdict `{verdict: accept or reject, reasons[], risk_notes[]}` [code:rrsi/rrsi/loop.py:_draft][code:rrsi/rrsi/critic.py] |
| `r<t>/<V>/smoke.json` | `{ok, stage ∈ {compile, ctor, smoke_run, run}, …details}` [code:rrsi/domains/*/adapter.py:smoke] |
| `r<t>/<V>/prep.json` | success: `{commit, branch, edits, diff_path, mechanism}`; failure: `{gate_failure, detail, edits, diff_path}` [code:rrsi/rrsi/loop.py:_draft] |
| `r<t>/<V>/eval.json`, `jobs/<job>/eval.json` (EvalResult) | `job, k, S, C, n_expected, missing, extra{domain aggregates}, per_task{task_id: TaskResult{rewards[], weights[], tokens[], missing, extra{}}}` [code:rrsi/rrsi/evaluate.py] |
| `r<t>/decisions.json` (Decision) | `variant, admissible, reason, S, C, delta_S, delta_C, novelty, guards[]` [code:rrsi/rrsi/selection.py:Decision] |
| `Candidate` (in memory) | `variant, edits[], ev, diff_path, branch, commit, gate_failure, detail`; `components` = the edits' tags [code:rrsi/rrsi/selection.py:Candidate] |
| git layout | incumbent branch `evolve/<domain>`; candidate branches `<domain>/r<t><V>`; worktrees `runs/<domain>/wt/r<t><V>` (plus `incumbent`, `smoke`, `heldout_<label>`); commits touch only the harness path. Accepting a candidate is `merge-base --is-ancestor` + `update-ref` (a fast-forward). The frontier compares tree hashes "so commits outside the harness never look like a change" [code:rrsi/rrsi/gitops.py] |
| `STOP` file, `logs/r<t>.log`, `baseline.log`, `calibrate.log` | driver control and logs [code:rrsi/rrsi/driver.py] |
| Domain trial layouts | coding `jobs/<job>/<task>__<hash>/{result.json, agent/trajectory.json, verifier/ctrf.json, verifier/test-stdout.txt}`; workspace `jobs/<job>/s<s>/<task>/{trajectory.json, output/, scores.json}`; eng `jobs/<job>/<task>/t<i>/{payload.py, traj.json, meta.json, verdict.json}` [code:rrsi/domains/*/adapter.py] |

---

## 6. LLM roles and the gist of their prompts

All roles are Claude Opus 4.8 through AnthropicVertex. JSON-only mode appends "Output ONLY a single valid JSON object. No prose before or after, no markdown fences." and extracts JSON from fences or prose [code:rrsi/rrsi/llm.py].

### 6.1 Proposer: the harness engineer agent [code:rrsi/rrsi/propose.py:SYSTEM_TMPL]

- **Framing:** "You are a harness engineer agent. You directly modify the source code of an LLM-agent scaffold (the "harness") to fix recurring failure modes observed on an evolve set of tasks. The policy LLM is frozen and is a DIFFERENT model from you: do not assume it shares your capabilities, habits or judgment. Improve the harness from ITS perspective, using the trajectories as evidence of how it actually behaves."
- **No abort:** "THERE IS NO ABORT ACTION. You must ship a candidate. A round that ships nothing tests nothing: the history records what was MEASURED … If your best idea violates a hard rule, it is not your best idea; construct a different one, preferably on a component the history shows was never exercised."
- **done() contract:** "Ship at most THIS ROUND'S EDIT BUDGET b_t … Ship fewer if the evidence supports fewer." "The tag is validated against the diff; a mislabelled edit is re-tagged from the diff." "predicted_affected lists CONCRETE task ids … Predictions are checked against the evaluation and your hit/miss record (scoreboard) is shown back to you; over-claiming counts against you."
- **Required per-edit fields:**
  - `why_not_lower_lever` ("why a plain instruction edit would NOT fix this");
  - `trigger_condition` ("'always' is almost never right");
  - a three-part `retroactive_check`: "(corrective) which cited failing tasks would have moved … (preservative) which success habits could this disrupt … (transfer) why it generalizes beyond this evolve set".
- **Context section headers**, which carry the regularizers:
  - "EDIT HISTORY L_t … A rejected mechanism is negative evidence; do not redraw it unchanged. An accepted one carries the gain it produced; refine what has known credit, not what merely preceded a rise."
  - "COMPONENTS TO PRUNE B_t (exercised, no strictly improving edit in the recent window; remove the accepted machinery listed, it has stopped earning its place)."
  - "THREE-LENS ANALYSIS REPORT F_t (… success_habits are behaviors your change MUST NOT break)."
  - "You may ship AT MOST {b_t} independent edit(s) … (the budget anneals over the run: early rounds explore, late rounds make single attributable changes)."
  - Reserved slot: "RESERVED EXPLORATION SLOT: this variant holds one. At least one of your edits MUST be on a never-exercised component from: {U_t}."
  - Repair: "REPAIR ROUND: your previous edits … are already in the working tree … Fix ONLY what the objections require (remove leaked content, split or re-declare edits, wire up dead code, or delete the offending part)."
- **The constitution** (`SKILL.md`, per domain) explains the reward, meaning the exact floor, cost and within-band rules and the two proposal regularizers, and adds nine hard rules [code:rrsi/domains/coding/SKILL.md]:
  - "Edits, counted by independence, not by line count … Dependent parts are ONE edit … No same-round dependency chains between edits. Larger subsystems may be built ACROSS rounds: declare the plan ('phase 1 of N') in the hypothesis" (the same rule is in all three constitutions);
  - "No task-specific content";
  - "Never touch the verifier";
  - "Mechanism over wording. Prefer changing control flow, output plumbing, information routing, or state over rewording prompts";
  - "Don't break the contract" (entry class, BaseAgent API and trajectory schema unchanged; model, temperature, timeouts and concurrency are injected);
  - "Don't disable safety without replacement";
  - "Unattended robustness";
  - "Never jeopardize termination … must be bounded (e.g. 'at most one targeted verification pass')";
  - "English only".

  It lists the levers 1–10 (config, control flow, prompt, extra model calls, context management, new modules, and the structural skill, memory, client_tool and subagent), with priors such as "purely textual reference skills tend to wash out" and "sub-calls tend to HURT on small policies". Its litmus test is "would this help a competent human operator working on MANY unfamiliar terminal tasks?"
- **`PATTERNS.md`** is a "Pattern Library (reference, not an allowlist)" of mechanism families with generic traps. Examples: waiting and polling, output plumbing, workflow state machines, parse-error recovery, context budget and bounded sub-calls for coding; ACE-style playbooks, executable skills and episodic memory for workspace; submission-contract compliance and "a mechanism that is refuted at one cost is not thereby refuted at another" for eng [code:rrsi/domains/*/PATTERNS.md].
- **Domain proposer brief:** benchmark description, out-of-bounds rules "enforced before measurement", and the fact that the real outcome is judged on an unseen OOD benchmark "so memorized task knowledge is worthless" [code:rrsi/domains/coding/briefs.py:PROPOSER].

### 6.2 Critic: the leakage screen [code:rrsi/rrsi/critic.py:SYSTEM_TMPL]

"You are a strict reviewer of harness (agent scaffold) code changes in an evolution loop. The harness is evolved against the very tasks it is measured on, so anti-overfitting review is critical." It must REJECT if ANY of the following hold:

1. **LEAKAGE / TASK-SPECIALIZATION**: task names or ids, entity, company or case names, task-specific file names, expected outputs or answers, "magic constants that only make sense for one task, per-task templates, a list of the suite's domains, or branching on task-identifying features". The litmus test is "would this change still make sense, and still help, on an unfamiliar task from a different suite in the same kind of work?"
2. **DEGENERATE**: an effective no-op (dead code, unused flag, comment-only) while claiming a mechanism, or deleting or disabling an existing safety mechanism without a working replacement.
3. **GRADER GAMING**: reading or influencing the verifier, judge or rubric; hard-coding grader-derived thresholds; gaming the completion or output format.
4. **UNDECLARED BUNDLING**: diff changes not covered by any declared edit, or one declared edit hiding several independent mechanisms.
5. **RUNTIME MEMORY / SKILL LEAKAGE**: persisting or injecting task-specific runtime data across trials.
6. **UNBOUNDED WORK**: retry or "keep improving" loops with no give-up path.

"Otherwise ACCEPT. You are reviewing intent and content, not style. Runtime correctness … is NOT your job." The output is `{"verdict": "accept" | "reject", "reasons": [...], "risk_notes": [...]}`.

**Deterministic prechecks run before the LLM** [code:rrsi/domains/*/adapter.py:critic_patterns]:
- Generic: credential patterns `AIza…`, `sk-…`, `api_key="…"`.
- Coding: `/tests`, `test_outputs`, `reward.(txt|json)`, `ctrf`, `/solution`, `task.toml`, `/logs/verifier`, **and every one of the 89 Terminal-Bench task names** (`data/tb21_tasks.txt`), each as the regex `(?<![\w-])<name>(?![\w-])`.
- Workspace: `task_[0-9a-f]{32}`, `world_[0-9a-f]{32}`, `llm_judge|score_rubric|criteria_results`, `task.json`.
- Eng: grader artefact names (`evaluate.py`, `solution.txt`, `rubrics.txt`, …), paths into the pristine benchmark tree, absolute or upward path escapes, verifier internals (`pass_threshold`, `combined_score`, …), the benchmark name, the task-id pattern, toolchain names of specific tasks (`iverilog`, `octave`, …), and network or package installs.

**Domain critic briefs** set the line between general practice and task knowledge. For example, "poll long-running builds instead of guessing a fixed wait" is OK, while "when extracting an ELF, segments are at offset 32" is REJECT. For the legal domain, general method is legitimate but "the acquirer is Vantage Partners" is REJECT [code:rrsi/domains/{coding,workspace}/briefs.py:CRITIC].

### 6.3 Analyst: the batch analyst, F_t [code:rrsi/rrsi/analyst.py:SYSTEM_TMPL]

"You do NOT read traces yourself. You dispatch read-only digester subagents … digest FAILED / low-scoring tasks first (failure lens), prioritising coverage of every suspected failure cluster …; digest 3-5 representative SUCCESSFUL tasks (success lens …); use the capability_gap lens … where a failure digest hints the agent was blocked by the environment or the scaffold's plumbing."

Aggregation rules:
- "MERGE digests describing the same underlying mechanism … SPLIT a label that covers two distinct mechanisms";
- "RANK failure_modes by total impact";
- "Descriptions must be entity-free and task-agnostic";
- "Keep prior mode names when the same mechanism recurs";
- "Do NOT prescribe code changes and do NOT attribute blame to model vs scaffold."

### 6.4 Digester: a read-only per-trace sub-agent [code:rrsi/rrsi/digester.py:SYSTEM_TMPL]

"a read-only investigator that inspects ONE agent trajectory in depth and returns a compact structured digest … read the grading / verifier section first (what actually failed), grep for anchors …, then read the relevant slices. Do not read whole files top to bottom." Its rules are "quotes must be exact and short; every claim needs a 'where'; do not speculate beyond what the trace shows."

The domain briefs tell it where the ground truth sits: the VERIFIER section for coding, the RUBRIC GRADING block for Harvey, and the "VERIFIER DETAILS" and "FAILURE CLASS" blocks for eng, whose failure classes are NO SUBMISSION, MALFORMED, INVALID DESIGN and UNDER TARGET [code:rrsi/domains/*/briefs.py].

---

## 7. Experimental protocol and headline results (as reported)

### 7.1 Protocol

- **Evolve and freeze.** "In each domain the harness evolves against one benchmark, then runs unchanged on others it never saw. Every method starts from the same base harness with the same model, tasks and budget" [doc][sec:inkeast]. "Every number measured against the unevolved harness H_0 in the same window" [code:rrsi/README.md].
- **Paired OOD evaluation.** H_0 (`trajectory[0].commit`) and the incumbent are each evaluated from their own worktrees.
  - SWE-bench Verified: one attempt per instance over the full 500-instance denominator [code:rrsi/domains/coding/scripts/swe_eval.sh].
  - EngDesign v1 (the same 61 tasks with "nine grading exploits closed"): both arms are run with K = 4. The script reports the pass-rate delta and a paired combined_score delta with a t statistic. It adds the note "if the champion's advantage is smaller here, part of what it learned was the closed exploits" [code:rrsi/domains/eng/scripts/final_eval.sh].
  - Frontier-Eng minus its EngDesign domain ("those seven tasks are all in the evolve set"): each arm is run **once per task** (`--n 1`). The script compares within-task on the tuple (valid, combined_score) and reports win/tie/loss and the win rate over decided tasks. Tasks ungradeable in either arm are excluded from both arms [code:rrsi/domains/eng/scripts/final_eval.sh]. How the paper's "Medal points" were derived from these runs is not in the repository.
  - JobBench, GDPval and APEX-Agents: the harness is exported into each benchmark's own archipelago runner, and the report is pass@1 over the full task set [code:rrsi/domains/workspace/README.md].
  - The Medal-point computation for Frontier-Eng is **not** in the repository.
- **Harvey LAB split.** Generated deterministically from Harvey LAB commit `1da4750`. Eligible tasks have ≤ 100 criteria and ≤ 20 MB of documents. The split is stratified proportionally by practice area, ordered by criteria count, and picked by even stride, "held-out first" [code:rrsi/domains/workspace/split_workspace.py].
- **Baselines.** H_0, Meta-Harness, AHE, TTHE and HarnessX, plus the ablations "no proposal regularizers", "no acceptance regularizers" and "unregularized evolution" [doc][sec:inkeast][sec:harry]. All baselines share "the same starting point, same frozen policy, same evolve set, same candidate budget" [sec:inkeast][sec:shibing]. Harry adds "round/candidate budget" for the ablation arms [sec:harry].

| Domain | Evolve set | ID held-out | OOD | Grader |
|---|---|---|---|---|
| Coding | Terminal-Bench 2.1, 89 tasks × k = 2 = 178 trials | – | SWE-bench Verified (500) | hidden unit tests |
| Agentic workspace | Harvey LAB, 120 tasks × k = 2 (about 14,100 criterion verdicts; 20 to 100 criteria per task) | Harvey LAB 40 | JobBench, GDPval, APEX-Agents | rubric judged by an LLM (Gemini 3.5 Flash for Harvey) |
| Engineering design | EngDesign-Open, the 61 tasks "that run without proprietary simulators", × k = 4 = 244 trials | – (the set "is too small to spend tasks on") | Frontier-Eng (Medal points; "26 domains" per [sec:inkeast]); EngDesign v1 (hardened) | frozen code verifiers, deterministic |

Sources: [doc][code:rrsi/domains/*/rrsi.json][code:rrsi/domains/eng/config.py][code:rrsi/domains/{workspace,eng}/README.md].

### 7.2 Main results, Claude Opus 4.8 policy [code:rrsi/README.md][doc]

| Domain | Benchmark | Role | H_0 | RRSI | Δ |
|---|---|---|---|---|---|
| Coding | Terminal-Bench 2.1 | evolve | 74.2 | 80.2 | +6.0 |
| Coding | SWE-bench Verified | OOD | 82.0 | 83.8 | +1.8 |
| Workspace | Harvey LAB | evolve | 89.4 | 90.5 | +1.1 |
| Workspace | Harvey LAB | ID held-out | 86.9 | 89.2 | +2.3 |
| Workspace | JobBench | OOD | 36.0 | 40.7 | +4.7 |
| Workspace | GDPval | OOD | 48.8 | 52.3 | +3.5 |
| Workspace | APEX-Agents | OOD | 34.2 | 37.9 | +3.7 |
| Engineering | EngDesign | evolve | 50.0 | 54.9 | +4.9 |
| Engineering | Frontier-Eng | OOD | 17.7 | 22.0 | +4.3 (+24.3% relative [sec:shibing]) |

"No held-out split got worse in any domain" [doc]. Note that one secondary source mis-transcribes the EngDesign row as 17.7 → 22.0 [sec:inkeast], so trust the README.

### 7.3 Against prior harness-evolution methods, workspace domain (Table 1 per [sec:harry][sec:shibing]; values also in [doc])

| Method | Harvey (evolve) | Harvey (ID held-out) | JobBench | GDPval | APEX-Agents | OOD avg [inferred: mean of 3] |
|---|---|---|---|---|---|---|
| H_0 | 89.4 | 86.9 | 36.0 | 48.8 | 34.2 | 39.7 |
| Meta-Harness | **93.0** | 89.2 | 37.1 | 49.1 | 35.7 | 40.6 |
| AHE | 90.7 | 88.7 | 37.2 | 47.2 | 33.1 | 39.2 |
| TTHE | 91.1 | 88.5 | 35.2 | 47.0 | 31.7 | 38.0 |
| HarnessX | 91.8 | 89.1 | 36.3 | 48.5 | 34.3 | 39.7 |
| RRSI | 90.5 | **89.2** | **40.7** | **52.3** | **37.9** | **43.6** |

- The ranking flips out of distribution: "Meta-Harness … OOD only +0.9; AHE and TTHE below H_0 (TTHE by 1.7)" [sec:awesome][sec:shibing][sec:inkeast]. From the per-benchmark numbers the unrounded gaps are Meta-Harness +0.97, HarnessX +0.03, AHE −0.50 and TTHE −1.70 [inferred]. The "+0.9" is the difference of the rounded averages (40.6 − 39.7), and the overview's "added under a point" [doc] agrees.
- RRSI is "the only OOD average clearing H_0 by more than a point (43.6 vs 39.7)" [sec:awesome][doc].
- Figure 1(a) plots evolve gain against OOD gain with a 1:1 line. The prior methods fall in the "gains don't transfer" region [sec:shibing].

### 7.4 Ablation, workspace (Table 2 per [sec:inkeast][sec:harry]; also [doc])

| Variant | Evolve | ID held-out | OOD avg | Policy tokens / trial |
|---|---|---|---|---|
| H_0 | 89.4 | 86.9 | 39.7 | 1.56M |
| Unregularized evolution | **92.8** | 88.9 | 40.3 | 3.80M |
| − proposal regularizers | 90.7 | 88.8 | 41.9 | 2.69M |
| − acceptance regularizers | 91.5 | 88.7 | 41.0 | 3.59M |
| Full RRSI | 90.5 | **89.2** | **43.6** | 2.42M |

"Removing guards always raises the practised score and lowers the unseen one: the signature of overfitting" [doc]. Removing the acceptance regularizers adds roughly half the token cost (3.59 / 2.42 − 1 = +48% [inferred]; harry also gives +48% and OOD −2.6 [sec:harry]). Removing the proposal regularizers **raises** the evolve score by only 0.2 (90.5 → 90.7) but lowers OOD by 1.7 (43.6 → 41.9) [doc][inferred arithmetic]. awesome says this "costs 0.2 evolve" and shibing says evolve "drops 0.2" [sec:awesome][sec:shibing], but those phrasings have the evolve direction backwards; the table numbers show a rise. awesome's gloss is "steering where the search looks matters even when nothing is rejected" [sec:awesome].

**Which mechanisms belong to each ablation group is not stated in any source I could access.** The paper's own grouping (§4.6) suggests the following [inferred]:
- "proposal regularizers" = {annealed budget, evidence-aware history conditioning, structured exploration};
- "acceptance regularizers" = {leakage critic, noise floor, complexity-aware acceptance, pruning}. The paper's figure, abstract and README all place pruning on the selection side.

In the code, pruning is delivered as a proposer directive (B_t), so "removing acceptance regularizers" in a faithful reproduction should also drop the B_t directive [inferred]. Also not stated: whether the within-band shaped rule and the domain guards belong to the acceptance group, and what replaces the critic in the no-acceptance arm (smoke only?) [unverified].

### 7.5 Policy dependence (Tables 3 and 4 per [sec:harry][sec:inkeast]; also [doc][code:rrsi/README.md])

| Policy | Benchmark | Before | After | Δ |
|---|---|---|---|---|
| Claude Opus 4.8 | Terminal-Bench 2.1 (evolve) | 74.2 | 80.2 | +6.0 |
| Claude Opus 4.8 | SWE-bench Verified (OOD) | 82.0 | 83.8 | +1.8 |
| Gemini 3.5 Flash (evolved separately) | Terminal-Bench 2.1 | 64.6 | 78.7 | +14.1 |
| Gemini 3.5 Flash | SWE-bench Verified | 76.8 | 79.0 | +2.2 |
| Gemini 3.1 Flash Lite (never used in the search; runs the Gemini-3.5-Flash-evolved harness unchanged) | Terminal-Bench 2.1 | 11.2 | 14.6 | +3.4 (+30.4% relative) |

### 7.6 Cost and steps (Figure 4 per [sec:shibing][sec:roacher]; also [doc])

- RRSI's final harness uses 2.42M policy tokens per trial, against 3.80M for unregularized evolution and 3.82M for AHE ("58% more for 4.4 points less out of distribution").
- It takes 26.3 steps per trial, against 27.3–34.6 for the prior methods and 21.2 for H_0.
- Every evolved harness costs more than H_0 (1.56M), so "transfer gain [is] still partly bought with extra inference compute" [sec:harry].
- The abstract says "30% fewer policy tokens than the unregularized evolution" [sec:abstract]. The ablation table implies 1 − 2.42/3.80 = 36% [inferred][doc].

### 7.7 Qualitative evidence

"Table 6's round-by-round accept/reject case studies … a candidate rejected by the cost rule, another by the noise floor, a small reusable fix retained" [sec:awesome]. The table content itself was not accessible. The project page has an interactive section, "Watch the harness evolve, round by round: Four real runs, every candidate: what it proposed, what the critic said, why the gate kept or dropped it, and the exact diff" [sec:projectpage]; only its heading survives in the mirror. The project page also stresses: "The cost rule refuses growth that is not paid for when it is proposed; pruning removes growth that stopped paying for itself since. No prior method carries either" [sec:projectpage].

---

## 8. Known weaknesses and critiques

**Reported by the overview and secondary sources:**
- Single numbers, no error bars or repeated runs [doc]. harry separately notes that component credit "is still based on noisy empirical deltas" and that δ may fail if the evaluation noise is non-stationary [sec:harry].
- The agent, proposer, analyst and critic are the same model family, which may share blind spots [doc][sec:shibing].
- Extra tuning knobs: edit budget (b_min, b_max), noise margin δ, cost limits (β0, β1), pruning window, stall window and T [doc][sec:shibing][sec:tony]. shibing reports that these are chosen on the evolve set and then fixed, which is itself an adaptive decision that could overfit; the paper does not examine this [sec:shibing].
- The abstract's 30% token saving versus the ablation's 36% [doc].
- Frozen weights only. Longer horizons, different architectures and different tool ecosystems are untested [doc][sec:inkeast][sec:tony]. shibing asks whether the constraints become too tight over "hundreds of rounds" [sec:shibing]. harry asks whether longer runs produce "second-order overfit" [sec:harry].
- A third-party speculative take argues that most of the OOD benefit may come from the annealed edit budget alone (capacity control), with the critic being "a much noisier LLM-judged version of the same regularization" [sec:jjak-take]. It proposes a compute-matched four-arm factorial: unregularized, budget-only, selector-only, full. It warns that matching on tokens and matching on candidate count can give different answers. The paper's two-group ablation cannot settle this. This is an untested hypothesis, not a finding.
- Regularization narrows the evolve-to-OOD gap (up to +14.1 on the evolve set versus up to +4.7 OOD) but does not close it [sec:awesome].
- The critic is not a guarantee of non-leakage. It may miss subtle leaks or reject useful domain-specific mechanisms [sec:awesome][sec:harry].
- Workspace grading relies on an LLM judge. The simulator-graded engineering domain mitigates "learning to please the judge" [doc][sec:harry].
- Token count is a cost proxy only. It excludes latency and tool or API cost [sec:harry].
- Each domain needs a hand-built adapter and per-domain constants [sec:awesome].
- Adaptive benchmark-poisoning attacks, e.g. edits slightly above δ that satisfy the cost rule, are unexplored [sec:harry].

**Observed in the code** [inferred from reading it]:
1. **Winner's curse and ratcheting S*.**
   - The incumbent is never re-measured. The next round's ΔS reference S_t is the same noisy measurement that won the argmax, so it is biased upward.
   - S* is a running maximum of noisy measurements, so the floor `S* − δ` drifts upward by selection noise. Later genuine improvements have to beat an inflated bar.
   - The fixed δ (for example "3 passes of 178") does not account for this max-of-noisy bias.
2. **Budget never reaches b_min** in-run under ceil and t ≤ T−1 (§4.2).
3. **Pruning is advisory.** B_t only lists components. Removal happens only if the proposer chooses a prune edit, and that edit must still pass the critic, smoke and Algorithm 2. B_t also includes components with no accepted machinery (for example a tried and rejected `skill`), which then appear with an empty list. It also includes components whose only measurements are older than the window (g = −∞ ≤ 0). Once a component has been tried, it keeps landing in B_t unless an edit to it achieves ΔS > 0 within every n_prune window [code:rrsi/rrsi/history.py:yield_g,prune_set].
4. **Credit is shared across a bundle.** g_t(l) uses ΔS shared by every edit in the bundle, so early (b_t = 4) credit is coarse. The code's own justification is that "as b_t anneals to 1 the record becomes evidence about a single component" [code:rrsi/rrsi/history.py] — but by item 2 it anneals only to 2.
5. **Negative ΔS can be admitted within the band** (above the floor) if it saves tokens or adds novel structure. By design this trades a small score for cost, but combined with item 1 it can walk S_t below S* repeatedly; the floor bounds this.
6. **The novelty bonus rewards neutral structural additions.** When the cost term is small this can grow the harness, which the complexity rule is meant to prevent. The cost term w_c·ΔC and pruning counteract it.
7. **Component tags are regex heuristics** over diffs and are domain-ordered. Mislabels feed T_t, U_t and ν.
8. **The critic sees only the diff** (capped at 120k chars), not the full files. It is told not to judge runtime correctness. Its prompt has a "STATE FILES" section for inspecting persisted memory, but the loop never fills it. So check 5 (runtime memory or skill leakage) can only be judged from code, never from what was actually persisted. The deterministic denylist is strong for coding (every task name) but domain-specific.
9. **Evaluation is expensive.** Every surviving candidate is run on the full evolve set with k trials: m × |D| × k trials per round, for example 2 × 178 for coding. Held-out data is never used in-loop, which is by design, and only the workspace domain has an ID held-out split.
10. **The δ estimate assumes trial exchangeability within a task** (bootstrap). With only 2 base evaluations, the repeated-evaluation estimate uses a 2-sample stdev, which is itself very noisy.
11. The engineering `metric.json` calibrator is not wired into selection; it only feeds `bench/score.py` reporting (§4.4). That makes it a potential source of confusion.
12. **No per-round held-out monitoring.** Held-out and OOD suites are evaluated only at the end (`heldout`, `swe_eval.sh`, `final_eval.sh`, `ood/*.sh`). Nothing in the loop would reveal an evolve/held-out drift while the run is in progress. The overview's lesson "Score every round on data it didn't tune against" [doc] goes beyond what the released code does [code:rrsi/rrsi/loop.py][code:rrsi/rrsi.py].
13. **Frontier-Eng transfer is measured with a single trial per task** (`--n 1`) [code:rrsi/domains/eng/scripts/final_eval.sh], whereas the evolve set uses k = 4.

---

## 9. Reproduction blueprint (domain-agnostic, CPU-only)

### 9.1 Components and interfaces

These are the minimal framework components for a faithful, generic RRSI. Names are proposals [inferred]; the behaviour of each is specified in §§3–5 with code references.

```python
# --- task environment (the only domain-specific part) ---
class Domain(Protocol):                                # mirrors rrsi.domain.Domain
    name: str
    def evolve_ids(self) -> list[str]; def heldout_ids(self) -> list[str]
    def ood_suites(self) -> dict[str, list[str]]      # extension: named transfer suites
    def smoke_ids(self, incumbent_per_task=None) -> list[str]
    def run(self, harness_root: Path, job: str, ids: list[str], k: int) -> None   # resume-safe
    def score(self, job, ids, k) -> tuple[dict[str, TaskResult], dict]           # missing -> 0
    def load_trial(self, job, task_id, trial) -> Any
    def render_trace(self, rec, detail=False) -> str; def task_row(self, task_id, rec, tr) -> str
    def smoke(self, harness_root, job, ids) -> tuple[bool, dict]
    def guards(self, inc: EvalResult, cand: EvalResult) -> list[str]             # non-compensatory
    def regression_threshold(self, k) -> float                                    # default 1/k
    critic_patterns: list[tuple[str, str]]; component_signals: list[tuple[str, list[str]]]
    briefs: dict[str, str]  # analyst / digester / proposer / critic
    constitution: tuple[str, str]  # SKILL.md, PATTERNS.md
    harness_path: str; source_exts: set[str]

# --- core method components ---
class HarnessStore:        # git-worktree backend (faithful) + plain-directory snapshot backend (mock/CI)
    def ensure_incumbent_branch(); def checkout(name, ref, new_branch=None) -> Path
    def diff(worktree) -> str      # includes untracked files
    def commit(worktree, msg) -> str; def fast_forward(commit); def tree_hash(ref) -> str
class Evaluator:           def evaluate(domain, root, job, ids, k) -> EvalResult   # aggregate(): weighted S, mean C
class NoiseCalibrator:     def calibrate(evals: list[EvalResult], z=2.0, reps=2000, seed=7) -> dict
class EditBudget:          def b(t, T, b_min, b_max, rounding="ceil") -> int
class ComponentTaxonomy:   K, K_STR; def normalize(declared, diff, signals) -> str; def novelty(components, counts) -> int
class EditHistory:         # JSONL ledger, one record per edit
    def append_candidate(...); def tried(); def yield_g(t, n_prune); def prune_set(t, n_prune)
    def incumbent_component_counts(); def render(n=40); def replace_round(t)
class Exploration:         def stall_flag(traj, t, w, delta) -> int; def directives(t, sigma, tried, m_draft) -> dict
class Analyst:             def analyze(traces, per_task, round_dir, prior_modes, prior_habits) -> Report   # + Digester
class Proposer:            def propose(harness_dir, report, history_rows, constitution, b_t, explore,
                                       reserved, prune_set, traces, per_task, digests, scoreboard,
                                       variant_brief, repair_brief=None) -> Proposal
class LeakageCritic:       def review(diff, summary, targets, edits) -> Verdict   # regex precheck → LLM (3 parses)
class Selector:            # Algorithm 2 as pure functions
    def cost_rule(dS, dC, nu, delta, cfg) -> (bool, str)
    def judge(cand, inc, S_star, delta, cfg, counts, guards) -> Decision
    def select_round(cands, inc, S_star, delta, cfg, counts, guard_fn) -> (winner | None, decisions)
class Scoreboard:          def attribute(t, variant, edits, inc_ev, cand_ev) -> rows
class Frontier:            # incumbent, S_star, trajectory (JSON)
class RRSILoop:            def baseline(); def calibrate(); def round(t); def readjudicate(t); def reevaluate(t)
class Driver:              def drive(T, start)   # STOP file, settled-round detection, 3-infra-failure stop
class LLMClient(Protocol): def generate(prompt, system=None, json_only=False, cache_prefix=None) -> str
    # implementations: ClaudeCLI (`claude -p`, JSON extraction as in rrsi.llm.extract_json), MockLLM (scripted/deterministic)
class TransferEvaluator:   def paired(domain, ref_a="H_0", ref_b="incumbent", suites) -> table
    # paired per-task deltas; for continuous graders also within-task win/tie/loss on
    # (valid, score) as in domains/eng/scripts/final_eval.sh
class HeldoutMonitor:      # extension beyond released code: scores each new incumbent on held-out
    def on_accept(t, commit) -> None   # logged only; must never feed Selector or Proposer context
class RegularizerSwitches: # ablations: budget_anneal, history_conditioning, stall_exploration, critic,
                           # floor, cost_rule, within_band_rule, prune_directives, domain_guards
```

Three design notes [inferred]:
- Keep `Selector`, `EditBudget`, `EditHistory` summaries, `ComponentTaxonomy` and `NoiseCalibrator` as pure and deterministic, and unit-test them against the RRSI test vectors in [code:rrsi/tests/test_core.py]. The `Selector` can then be reused as a generic "regularized keep rule" by the other methods in the framework: a Dream-RSI replay-world selector, or EvoMap asset admission [doc "They also need each other"].
- The unregularized baseline must be explicit, because no source I accessed defines it precisely. Proposed definition: b_t = ∞ (or b_max), a history of accepted edits only, no stall exploration, no critic (smoke only), and a winner of `argmax_c Ŝ(c)` accepted iff `Ŝ(c) > Ŝ(H_t)`. A second variant accepts the argmax unconditionally.
- For the ablation groups, follow the paper's own proposal/selection split (§4.6): "proposal = {budget anneal, history negative evidence, stall exploration}" and "acceptance = {critic, floor, cost rule, within-band rule, guards, prune directives}". Pruning belongs to the selection side in the figure, abstract and README, even though the code delivers it through the proposer. The exact membership used in Table 2 is unknown, so also report sensitivity to moving pruning to the proposal group [inferred].
- Add single-mechanism switches as well as the two groups, and a compute-matched budget-only arm. That lets the framework test the third-party claim that the budget alone explains most of the OOD gain [sec:jjak-take]. Report both matchings (equal tokens and equal candidate count) [inferred].

### 9.2 CPU-only experiment design

The constraints are: no GPU, an LLM available through `claude -p`, and a deterministic mock LLM. The strategy [inferred] is to use cheap simulated policies with **known ground-truth expected scores**, so that noise chasing, leakage and transfer can be measured exactly.

**Tier 0: the overview's toy, ported.** Port the overview's "overfitting trap" simulator as a smoke test. The doc calls it "a toy model … not the paper's actual method", and its numbers are the demo author's, not RRSI's [doc][sec:demo]. Its exact mechanics:
- Scale: `ROUNDS = 60`, `RUNS = 200`, seeded mulberry32 streams per run.
- Knob defaults: trick share `pTrick = 0.35` (slider 0–0.6), noise `σ = 0.02` (0–0.04), critic catch rate `0.8` (0–1).
- Each round one proposal is drawn:
  - a trick (probability pTrick) has practice effect `e = 0.03 + 0.01·N(0,1)` and unseen effect `g = −0.004 + 0.006·N(0,1)`;
  - otherwise the effect is genuine, `e = −0.004 + 0.02·N(0,1)` with `g = e`.
- Plain loop: "keep if the new measurement beats a fresh measurement of the current version", i.e. keep iff `(e + σN₁) − σN₂ > 0`.
- Guarded loop: the critic drops a trick with probability catchRate. The loop then keeps iff `(e + σN₁) − σN₂ > band`, with `band = 0.5·σ·√2`, i.e. half a standard deviation of the null difference.
- Kept changes accumulate e (practice) and g (unseen).

Expected outcome: plain loop practice ↑ and unseen ≈ flat; guarded loop practice lower and unseen higher. The demo's band is *not* RRSI's δ = 2·sd; the port should expose both.

**Tier 1: "HarnessWorld", fully synthetic with a mock proposer and critic** (seconds per run, so 50+ seeds are feasible) [inferred design].
- *Tasks.* Families f ∈ F with latent difficulty. The evolve set, an ID held-out set from the same families, and an OOD suite with shifted family mix and "tool-surface" features.
- *Harness.* A set of mechanisms. Each has a component ∈ K, a text body, a true effect vector per family and surface, a token-cost multiplier, and optional `leak_keys` (evolve task ids). A leaky mechanism helps only the listed evolve tasks and is 0 or slightly harmful elsewhere.
- *Frozen policy.* `P(success | x, H) = σ(a_π − d_x + Σ_{m∈H} e_m(x))`, with Bernoulli trials and tokens `= base · Π cost_m · lognormal noise`. Use two policies, strong and weak, for the cross-policy test.
- *Mock proposer.* Samples bundles of at most b_t edits from a pool with configurable shares: generic-helpful, leaky tricks (text contains task ids and entity names), null edits, costly-small-gain edits, hitchhiker (harmful) edits, structural mechanisms, and prune edits. The draw is biased toward the analyst's top failure families. It has a "prompt-collapse" bias, respects the reserved slot and B_t, and (switchable) avoids hypotheses whose history record is REJECTED.
- *Mock critic.* Uses the regex denylist (task ids, entity names) with a configurable catch rate for obfuscated leaks.
- *Ground truth* is `E[S]` on the evolve, ID and OOD sets, computed analytically for every harness.

**Tier 2: a real code harness with `claude -p` as proposer, analyst and critic, and a mock policy** [inferred design]. The harness is a small Python package: `solve(task)` pipelines for text-extraction or normalization tasks (dates, amounts, units) around a **fixed noisy "policy" function**. That function mimics an LLM extractor: seeded, it fails on certain surface formats and has a token meter equal to characters processed times the number of calls. Deterministic exact-match graders score the output.
- The proposer really edits the code through the JSON action protocol. Leakage then appears as literal task strings or hard-coded answers, which the regex plus LLM critic must catch.
- Cost is real, through extra policy calls, retries and verification passes.
- The OOD suite uses unseen formats and locales.
- Settings: 3–5 seeds, T ≈ 8–10, m = 2, k = 2, 30–60 evolve tasks. Everything runs on CPU in minutes, apart from LLM latency.

**Tier 3 (optional): a real LLM policy**, `claude -p` with a small model, on 20–40 tasks for one qualitative run. This is only a sanity check of end-to-end plumbing.

For each key claim, what to measure, the baseline or ablation, and what outcome confirms it (confirmation is qualitative: the direction must be consistent across seeds and the paired bootstrap CI must exclude 0):

| # | Claim | Measure | Arms | Confirming outcome |
|---|---|---|---|---|
| E1 | Unregularized evolution overfits: evolve ↑, transfer ≈ H_0 (Tables 1–2) | final Ŝ_evolve; true E[S] on ID and OOD; gap = evolve gain − OOD gain | H_0, unregularized, full RRSI (Tier 1 with 50 seeds; Tier 2 with 3–5) | unregularized has the highest evolve Ŝ; RRSI has a lower evolve Ŝ but higher true OOD and ID; the unregularized OOD gain is near 0 |
| E2 | Both regularizer groups matter (ablation ordering) | same metrics plus tokens per trial | −proposal, −acceptance, none, full | removing either group raises evolve and lowers OOD; none is worst on OOD and most expensive |
| E2b | (Open question, not a paper claim) How much of the OOD gain the edit budget alone explains [sec:jjak-take] | OOD gain per arm; tokens; candidates evaluated | unregularized, budget-only, selector-only, full, each at matched tokens and at matched candidate count (Tier 1, ≥ 50 seeds) | if budget-only ≈ full, the selector is secondary; if the arms are additive, the paper's two-group decomposition holds. Report whichever comes out |
| E3 | The leakage critic blocks benchmark-specific fitting before evaluation | number of leaky mechanisms in the final harness; evaluations spent on leaky candidates; OOD harm | critic on or off (everything else on); sweep leak share and catch rate | the critic-on arm retains ≈ 0 leaky mechanisms and spends no evaluations on caught leaks; OOD higher |
| E4 | The noise floor prevents noise chasing and downhill walks | false-accept rate of null candidates; drift of the true incumbent E[S] under a pool of slightly negative, cheaper edits | floor relative to S* versus relative to S_t versus none; keep-if-better baseline | with S* − δ, the true score stays within about δ of its best; the S_t-relative or no floor slides down; the null false-accept rate is near the calibrated level (z = 2 gives about 2.5% one-sided) |
| E5 | The cost rule makes extra tokens earn their keep; lighter harness (Fig. 4) | tokens per trial of the final harness; OOD-versus-tokens Pareto | cost rule on or off; β1 sweep | cost-rule-on gives fewer tokens and equal or higher OOD; the unregularized arm sits in the "more tokens, lower OOD" region |
| E6 | Pruning removes machinery that stopped helping | count of dead or zero-effect mechanisms in the final harness; number of prune edits accepted | prune directives on or off; pool with mechanisms whose effect decays to 0 after acceptance | on: dead mechanisms removed and tokens lower; off: they accumulate |
| E7 | The annealed budget improves attribution | hitchhiker rate (accepted bundles containing ≥ 1 harmful edit); correlation of recorded per-component ΔS with true effect; early-round progress | anneal (b_max → b_min) versus constant b_max versus constant 1 | anneal has a lower hitchhiker rate than constant b_max and faster early progress than constant 1 |
| E8 | History conditioning avoids redrawing falsified hypotheses | re-proposal rate of REJECTED hypotheses; evaluations wasted | history with negative evidence versus accepted-only or none (Tier 2 with the real LLM; in Tier 1 the switch is mechanical) | re-proposal rate is significantly lower with the full history |
| E9 | Stall exploration escapes prompt-only collapse | component coverage \|T_t\|/\|K\|; score after a stall; accepted structural mechanisms | reserved slots on or off, with a proposer biased toward `prompt` and the remaining real gains only in structural components | on: broader coverage and escape from the plateau |
| E10 | δ calibration is sound | δ from bootstrap versus from R repeated base evaluations; empirical fraction of unchanged-harness re-evaluations clearing S* − δ | z ∈ {1, 2, 3} | about 97.5% clearance at z = 2; bootstrap and repeated-evaluation estimates agree within their spread |
| E11 | Domain guards are non-compensatory | acceptance of candidates that raise pass rate but lower the validity or no-payload rate | guards on or off | on: such candidates are never accepted |
| E12 | Mechanisms transfer across policies (Tables 3–4) | gain of the evolved harness on a weaker policy never used in the search | RRSI versus unregularized harness, both run on the weak policy | positive transfer for RRSI; smaller or no gain for unregularized |
| E13 | Evidence is auditable and resumable | a killed-and-resumed run yields the same decisions; `readjudicate` with a changed δ changes only Algorithm 2 outcomes | Tier 1 and Tier 2 | bit-identical ledgers under the mock; ledger fields as in §5 |

Report every table with paired bootstrap CIs over seeds. This fixes the "no error bars" critique [doc]. Also log every candidate as a tree node (parent commit, edits, ΔS, ΔC, verdict), so that RRSI histories can later serve as Dream-RSI replay worlds [doc "Log experiments as a tree"].

---

## 10. Capability checklist: overview claims mapped to components

| # | Claim the overview [doc] attributes to RRSI | Component(s) that satisfy it |
|---|---|---|
| 1 | Improves the agent's harness (system and task prompts, control flow, tools and their descriptions, memory and skill files, context rules) around a frozen model | `Domain.harness_path` + `HarnessStore` + `Proposer` (edit or write files) + `ComponentTaxonomy.K`; policy frozen inside `Domain.run` |
| 2 | Every part stays editable; it limits how the search moves, not what it can touch | `Proposer` with an unrestricted workspace (path-jailed only) + regularizers only in `EditBudget`, `Exploration`, `Selector` and `LeakageCritic` |
| 3 | Names and targets three failure modes: benchmark-specific fitting, noise chasing, complexity accumulation | `LeakageCritic`; `NoiseCalibrator` + `Selector.floor` / band rule; `Selector.cost_rule` + prune directives (`EditHistory.prune_set`) |
| 4 | Keep rule: within the noise band of the best score so far, passes the leakage critic, justifies extra cost | `Selector.judge` (floor, cost rule, within-band rule, guards) + `LeakageCritic` gate |
| 5 | Shrinking edit budget: early rounds bundle several coordinated edits, later rounds one or two, so gains trace to a single change | `EditBudget.b` (cosine, ceil) enforced by `Proposer` `done()` validation |
| 6 | Memory of what failed: each tried change recorded with hypothesis, diff, score change and cost change; rejected ideas stay rejected | `EditHistory` (JSONL per edit) + `EditHistory.render` fed to `Proposer` with negative-evidence instructions + `Scoreboard` |
| 7 | Forced variety when stuck: a stall inside the noise band for several rounds sends part of the budget to untouched parts | `Exploration.stall_flag` (w, δ) + `Exploration.directives` (U_t, m_draft) + reserved-slot checks in `Proposer` and `RRSILoop._draft` |
| 8 | Leakage critic: before scoring, rejects hard-coded task names, entities, specific values or answers; generic improvements pass | `LeakageCritic` (regex precheck from `Domain.critic_patterns` → LLM review → bounded repair loop `repair_rounds`) |
| 9 | Noise floor: the unchanged agent is run several times to measure variation; no change may fall more than that below the best so far, stopping a slow slide | `NoiseCalibrator` (repeated evaluations or bootstrap, z) + `Frontier.S_star` + `Selector` floor `S' ≥ S* − δ` |
| 10 | Cost must be earned: a more expensive harness is kept only if the gain pays for it | `Evaluator` (Ĉ = mean policy tokens) + `Selector.cost_rule` (ΔC ≤ β0 + β1·ΔS; within-band shaped rule) |
| 11 | Pruning: parts that stopped producing gains are flagged for deletion (listed by the overview under "When deciding what to keep", i.e. the selection side) | `EditHistory.yield_g` / `prune_set` (n_prune) → `Proposer` context "COMPONENTS TO PRUNE"; prune edits go through `LeakageCritic` and `Selector`; the ablation switch belongs to the acceptance group |
| 12 | Regularization analogies: L0 edit count, L1 deletion, L2 token-cost growth, entropy exploration, holdout-reuse theory | documentation mapping in the method page (§4.6); implemented by `EditBudget`, prune directives, `cost_rule`, `Exploration`, floor + `EditHistory` |
| 13 | b_t = ceil(b_min + (b_max − b_min)·½(1 + cos(πt/T))) with illustrative b_min = 1, b_max = 4, T = 10 | `EditBudget.b` + `RRSIConfig` (defaults and per-domain JSON), overridable from the CLI |
| 14 | score(candidate) ≥ best − δ | `Selector.judge` floor |
| 15 | For gains above noise: relative cost increase ≤ β0 + β1 × gain; β0 and β1 set once and frozen for transfer tests | `Selector.cost_rule` + frozen `RRSIConfig` snapshot in `Frontier.config`; `TransferEvaluator` never mutates config |
| 16 | Evolve on one benchmark, then run unchanged on unseen ones; same base harness, model, tasks and budget for every method | `Domain.evolve_ids`, `heldout_ids`, `ood_suites` + `TransferEvaluator.paired(H_0, incumbent)` + `RegularizerSwitches` / baseline arms sharing the same budget |
| 17 | Three domains and eight benchmarks, graded by hidden unit tests, rubric LLM judges or frozen simulators | pluggable `Domain` adapters with each grader inside `Domain.score`; CPU analogues: Tier 1 and Tier 2 domains |
| 18 | Compared against Meta-Harness, AHE, TTHE and HarnessX | baseline arms in the framework (unregularized loop and ablations here; the other methods are specified in their own specs) sharing `Evaluator` and `TransferEvaluator` |
| 19 | Ranking flips: the unguarded method practises best but transfers worst; RRSI lifts the unseen average most | experiments E1 and E2 (§9.2) with `TransferEvaluator` |
| 20 | Ablation: removing guards raises the practised score, lowers the unseen score and increases tokens (3.80M vs 2.42M) | `RegularizerSwitches` (proposal and acceptance groups) + E2 and E5 |
| 21 | Model independence: works with Gemini 3.5 Flash; the harness transfers to Gemini 3.1 Flash Lite, never used in the search | `Domain.run` parameterized by policy + E12 (a weak policy never used in the search) |
| 22 | Engineering design +4.9 on the practised set, +4.3 medal points (24%) on Frontier-Eng; no held-out split worse | `TransferEvaluator` over all held-out suites with a non-regression report; `Domain.guards` for simulator-graded domains |
| 23 | Final harness takes 26.3 steps per task vs 27.3–34.6 for rivals and 21.2 for the start | `Evaluator.extra` (steps per trial) + cost reporting in `TransferEvaluator` |
| 24 | Treats self-improvement as a statistics problem (test-set reuse) rather than pure search | `NoiseCalibrator`, floor, ledger of falsified ideas, evolve/held-out/OOD separation |
| 25 | Guards both proposing and keeping, while leaving the edit space open | the proposal-side modules (`EditBudget`, `EditHistory`, `Exploration`) and the selection-side modules (`LeakageCritic`, `Selector`) |
| 26 | Cost is part of the keep decision, giving a lighter harness than every rival | `Selector.cost_rule` + within-band shaped rule rewarding savings + prune directives |
| 27 | Checks transfer on simulator-graded tasks to avoid pleasing an AI judge | deterministic graders in `Domain.score` (Tier 1 and Tier 2 use exact or analytic graders) |
| 28 | Caveats: no error bars; the same model family in every role; many tuning knobs; 30% vs 36%; weights frozen | multi-seed paired-CI reporting in experiments; `LLMClient` per role (allowing a different critic model or the mock); `RRSIConfig` sweep utilities; token accounting in `TransferEvaluator` |
| 29 | Side-by-side: "several candidates per round, filtered"; history is "a record of what was tried and failed"; cost control is "cost rule and pruning"; the strategy is improved by "an AI, within human-set rules" | `RRSILoop` (m variants in isolated worktrees) + `EditHistory` + `Selector` / prune + human-authored `constitution` / `rrsi.json` |
| 30 | "Measure your noise first" (re-run 3–5 times); "Keep a set the loop never sees" ("Score every round on data it didn't tune against. If practice and held-out scores drift apart, it's memorizing"); "Ban test-specific shortcuts"; "Change one thing at a time, eventually"; "Make cost part of the decision" ("A change that costs 20% more must buy a measurable gain"); "Log experiments as a tree"; "Never let the loop grade itself" | `NoiseCalibrator` (R repeated evaluations); `Domain.heldout_ids` / `ood_suites` never read by the proposer, plus `HeldoutMonitor` for per-round drift (an extension: the released code scores held-out only at the end); `LeakageCritic`; `EditBudget`; `Selector.cost_rule`; `EditHistory` + git commit parents (tree); grader inside `Domain.score`, outside the harness path, with critic denylists on grader paths |
| 31 | Cross-method synergy: Dream-RSI would benefit from RRSI's guards; RRSI candidates could be replayed Dream-RSI style; EvoMap assets should pass an RRSI-style independent check | `Selector` + `NoiseCalibrator` exposed as reusable "regularized acceptance gate" APIs; the `EditHistory` / worktree tree exported as replayable search logs (optional `ReplayEvaluator` hook) |
| 32 | Meta-Harness "has no explicit check for test-specific edits; RRSI's leakage critic was added partly to address this" | `LeakageCritic` (and experiment E3) |
| 33 | "RRSI and Dream-RSI both have open code, but their experiments used frontier models and many evaluations, so reproducing them at full scale is costly"; side-by-side "Scale to try it: Frontier models and full benchmark suites" | CPU-only Tier 0–2 designs (§9.2) with the `MockLLM` / `claude -p` `LLMClient`; `Evaluator` supports small k and small task sets |
| 34 | RRSI "regularizes what may be proposed and kept", unlike GEPA (diverse archive), Meta-Harness (full history) and SoL-Pi (survive across environments) | the proposal-side modules + `Selector`, exposed so the other method specs can reuse them as an optional gate |
