# Autoresearch (Karpathy, March 2026) and community ports

Andrej Karpathy, open-source repository `karpathy/autoresearch`, first commit 6 Mar 2026 [code:karpathy__autoresearch git log b11d6f2]. This spec also covers the community ports named in the overview: autoresearch-mlx (Apple Silicon), SLURM/HPC forks, "autoresearch at home" (collaborative), and gradient-boosted-tree adaptations. It also covers one parallel-cluster variant (SkyPilot) that turned up while researching the ports.

## 0. Sources and how to read the tags

Autoresearch has **no paper**. The primary sources are the repository itself, the author's own follow-up notes, and the port repositories. Everything below was read in full unless stated otherwise.

| Tag | Meaning |
|---|---|
| `[code:ar/<path>:<symbol>]` | `karpathy/autoresearch` at `228791fb499a` (25 Mar 2026), clone in `scratchpad/src/karpathy__autoresearch`, full history fetched. I read all of `README.md`, `program.md`, `prepare.py`, `train.py`, `analysis.ipynb`, `pyproject.toml` and `.gitignore`, looked at `progress.png`, and read the 36-commit log. |
| `[code:ar@<commit>:<path>]` | A file as it existed at an earlier upstream commit. The fact-check pass also read `git log -p program.md` (8 revisions) and the deleted multi-agent launcher `spawn.sh` (248 lines, present only in the initial commit `b11d6f2`). |
| `[code:nus/…]` | `Chern30/autoresearch-NUSSLURM` at `eb7c97f` (program.md diff, `slurm/submit_job.sh`, `slurm/submit_and_wait.sh`). This is a second, minimal SLURM fork with no container. |
| `[code:macos/…]` | `miolini/autoresearch-macos` at `537c6e6`, the first "notable fork" in the upstream README. It is a PyTorch MPS port that swaps FA3 for `F.scaled_dot_product_attention` (README, program.md diff, train.py grep). |
| `[code:mlx/…]` | `trevin-creator/autoresearch-mlx` at `766a25ff22af` (README, program.md, results.tsv, rigor.py, prepare.py `evaluate_bpb`, train.py constants and loop, git log). |
| `[code:home/…]` | `mutable-state-inc/autoresearch-at-home` at `d195e2fea7cf` (README, collab.md, program.md diff vs upstream, coordinator.py, setup_hub.py). |
| `[code:slurm/…]` | `kcxain/autoresearch-slurm` at `4b5c81e6a10b` (README, submit.sh, program.md diff). |
| `[code:slurmc/…]` | `zzyuanyi/autoreserach_slurm` at `4c90fdb076f5` (containerized SLURM runner: `slurm/job_template.sbatch`, `slurm/slurm_runner.py`, `slurm/sync_manager.py`, `slurm/__main__.py`, `slurm/server_config.py`, `program_slurm.md`). |
| `[code:hpc/…]` | `HudoGriz/autoresearch-hpc` at `2321edbe1999`, first commit 8 Sep 2026 (README plus `docs/skills-and-token-budget.md`). It lists Karpathy's autoresearch as "Inspiration and attribution; no imported runtime", so it is not a fork. |
| `[code:xgb/…]` | `szilard/xgboost-autoresearch` at `ab30064aac0d` (write-up `docs/index.md`, `program.md`, `groundtruth_all.tsv`, `results.tsv`, `research-log.md`, `analysis/a03,a07,a08,a09,a10`). `[code:xgbmin/…]` is the template repo `szilard/xgboost-autoresearch-minimal` at `744c660643ba` (program.md, train.py, check_groundtruth.py, run_groundtruth_all.sh, score_by_row/score.py, git log). |
| `[code:sky/…]` | `skypilot-org/skypilot` `examples/autoresearch/{README.md,instructions.md}` (master, fetched via raw.githubusercontent). |
| `[nanochat:LEADERBOARD]` | `karpathy/nanochat` `dev/LEADERBOARD.md` (master, fetched via raw.githubusercontent). Karpathy's own record of transferring autoresearch findings. |
| `[gh-disc:#32]`, `[gh-disc:#43]` | GitHub Discussions on karpathy/autoresearch, both dated 8 Mar 2026. Each "session report" says: "This is an automated post from an autoresearch agent running on behalf of @karpathy" (#32 adds "Agent: Claude (Anthropic)"). #32 is branch `autoresearch/mar5` and #43 is branch `autoresearch/mar8`. Fetched with WebFetch, which returns a summarized rendering, so the numbers are exact and the prose is paraphrased; the fact-check pass re-fetched both and confirmed the numbers. |
| `[web-snippet:<site>]` | A WebSearch result snippet only. The page itself was blocked, so the claim is **unverified**. |
| `[doc]` | The user's overview page ("Four ways AI is learning to improve itself"), a secondary summary. |
| `[inferred]` | My own reconstruction, arithmetic or design proposal. |

**Could not access** (egress-blocked): ensue.dev (the at-home "first coordinated run" blog), blog.skypilot.co (full parallel-run write-up; the repo README summary was used instead), szilard.github.io (the XGBoost blog; its source `docs/index.md` was read instead), rywalker.com, nickoak.com (a "tennis XGBoost reward hacking" write-up), pith.science and arxiv.org. The last two host the "Rehearse / confidence cliff" critique, 2607.27687; the fact-check pass also tried export.arxiv.org and api.semanticscholar.org without success. I also could not reach x.com (Karpathy's tweets 2029701092347630069, 2031135152349524125 and 2030705271627284816). One tweet is quoted verbatim inside `[code:home/README.md]`.

---

## 1. The idea in one paragraph (101)

Give a coding agent (Claude Code, Codex, …) a **small but real** training setup and let it run experiments by itself overnight [code:ar/README.md]. The repository has three files that matter, each with one owner:

- `prepare.py` holds the data, the tokenizer, the dataloader and the **evaluation function**. Nobody edits it.
- `train.py` holds the model, the optimizer and the training loop. The agent edits it, and everything in it is fair game.
- `program.md` holds the research instructions. The human edits it, and Karpathy calls it a "super lightweight 'skill'".

Each experiment trains for a **fixed 5 minutes of wall-clock time**, regardless of what the agent changed, then reports one number: validation **bits per byte** (`val_bpb`, lower is better, independent of vocabulary size). If the number went down, the git commit stays and becomes the new baseline. If it stayed equal or went up, the agent `git reset`s. Every run is logged to `results.tsv` and the loop repeats "forever", which comes to about 12 experiments an hour or about 100 per night [code:ar/program.md; code:ar/README.md]. The human's job moves up a level: instead of editing Python, you edit `program.md`, the "research org code" [code:ar/README.md]. It works because experiments are made comparable (a fixed budget and a fixed metric) and the grader sits in a file the agent is told not to touch [doc]. Its weak points are also simple. It keeps anything that is "strictly better" on one noisy number, judges every experiment on the same validation shard, rewards whatever trains best in 5 minutes on *this* machine, and runs as one greedy chain whose strategy changes only when a person rewrites the instructions [doc]. Community ports kept the same three-file contract and changed only the substrate:

- MLX on Macs;
- SLURM job submission on clusters, optionally inside containers;
- a shared hub where many agents claim experiments and publish results and a global best;
- XGBoost/tabular models, graded against hidden held-out sets.

---

## 2. What is improved, what is frozen, who grades

| Aspect | Upstream autoresearch | Source |
|---|---|---|
| **Improved (object of search)** | `train.py` only: "the full GPT model, optimizer (Muon + AdamW), and training loop. Everything is fair game: architecture, hyperparameters, optimizer, batch size, etc." | [code:ar/README.md] |
| **Frozen (locked)** | `prepare.py`: `MAX_SEQ_LEN`, `TIME_BUDGET`, `EVAL_TOKENS`, data shards, BPE tokenizer (vocab 8192), dataloader, `evaluate_bpb`. Also the dependencies ("You can only use what's already in `pyproject.toml`") and the hardware. | [code:ar/program.md "What you CANNOT do"; code:ar/prepare.py] |
| **Frozen (the improver)** | The coding agent's own model and harness never change. What changes is the training code of a *separate* small model. | [doc FAQ] |
| **Who grades** | `prepare.evaluate_bpb`, "the ground truth metric", on a pinned validation shard (`shard_06542`). The agent runs it and reads `val_bpb` from the log with `grep`. | [code:ar/program.md; code:ar/prepare.py:VAL_SHARD] |
| **Who decides keep/discard** | The agent, following the rule in `program.md` (strictly lower `val_bpb` means keep) tempered by the "simplicity criterion" (a judgment call). | [code:ar/program.md] |
| **Who improves the strategy** | The human, by editing `program.md`. | [code:ar/README.md; doc] |
| **Cost control** | A fixed 5-minute training budget per experiment, plus a 10-minute kill rule. | [code:ar/program.md] |

**Grader isolation is by instruction, not by mechanism** [inferred from code]. Nothing in the repo technically stops the agent from editing `prepare.py`; `program.md` simply forbids it. The README's "(and disable all permissions)" [code:ar/README.md] almost certainly means **turning off the agent's permission prompts** so it can run unattended. That *widens* the agent's powers rather than sandboxing it [inferred, from the launcher evidence that follows]. The deleted launcher shows the intended invocation: `claude --dangerously-skip-permissions` and `codex --dangerously-bypass-approvals-and-sandbox` [code:ar@b11d6f2:spawn.sh:setup_worker]. The XGBoost port's run recipes also use `claude --dangerously-skip-permissions` and `codex --yolo` [code:xgb/analysis/a08-multi_runs/phase3/setup.txt; code:xgb/analysis/a09-codex/notes.txt]. The grader also reaches into agent-owned code in four places:

- `evaluate_bpb` sums the **per-token losses returned by the agent-editable `model.forward(x, y, reduction='none')`** [code:ar/prepare.py:evaluate_bpb; code:ar/train.py:GPT.forward].
- The **5-minute budget accounting lives in `train.py`**, which is editable (`if step > 10: total_training_time += dt`) [code:ar/train.py].
- The eval batch size is the agent-chosen `DEVICE_BATCH_SIZE` [code:ar/train.py `evaluate_bpb(model, tokenizer, DEVICE_BATCH_SIZE)`].
- Because `steps = EVAL_TOKENS // (B·MAX_SEQ_LEN)` uses floor division, **the number of evaluated rows depends on B**. The default B = 128 evaluates all 10,240 rows (80 steps). B = 96 would evaluate 106·96 = 10,176 rows, a slightly shorter prefix of the same deterministic row stream [code:ar/prepare.py:evaluate_bpb; arithmetic inferred]. Rows are packed one at a time from a single document buffer, so the row sequence itself does not depend on B [inferred from code:ar/prepare.py:make_dataloader].

See §8.

Port variants of the same triple:

| Port | Improved | Frozen | Grader | Source |
|---|---|---|---|---|
| autoresearch-mlx | `train.py` (MLX) | `prepare.py`; `EVAL_TOKENS` reduced to `3*524288` | `evaluate_bpb`; optional `rigor.py` repeated-run gate | [code:mlx/README.md; code:mlx/prepare.py] |
| autoresearch-macos | `train.py` (PyTorch on MPS; FA3 replaced by `F.scaled_dot_product_attention`) | `prepare.py` | `evaluate_bpb` | [code:macos/README.md; code:macos/train.py] |
| SLURM forks | `train.py` | also `submit.sh` ("agent submits this, not modify"); in the containerized fork, everything under `slurm/` | same, run as a batch job | [code:slurm/README.md; code:slurmc/program_slurm.md] |
| autoresearch-at-home | `train.py`, starting from the swarm's global or tier best | as upstream | local `evaluate_bpb`; the global best is **self-reported** by agents, with heuristic sanity checks | [code:home/collab.md; code:home/coordinator.py:maybe_update_best] |
| xgboost-autoresearch | `train.py`: feature engineering in `prepare(df)` plus XGBoost hyperparameters | `prepare.py` (data download); `check_groundtruth.py`; the evaluation protocol (5-fold CV or a fixed eval slice) | the agent sees CV AUC (or eval-slice AUC); the **human** runs `run_groundtruth_all.sh` post hoc on hidden test sets | [code:xgb/program.md; code:xgbmin/run_groundtruth_all.sh] |

---

## 3. The loop, step by step (precise pseudocode)

### 3.1 Setup (once per run) [code:ar/program.md "Setup"]

```text
SETUP(repo):
  tag    ← propose from today's date (e.g. "mar5"); assert branch "autoresearch/<tag>" does not exist
  git checkout -b autoresearch/<tag>   # from current master
  read README.md, prepare.py, train.py          # "in-scope files"
  assert ~/.cache/autoresearch/ has data shards + tokenizer   # else ask human to run `uv run prepare.py`
  create results.tsv with header only: "commit\tval_bpb\tmemory_gb\tstatus\tdescription"
  confirm with human → then fully autonomous
```

The one-time data preparation (`uv run prepare.py`) downloads training shards and the pinned validation shard, then trains the tokenizer [code:ar/prepare.py:__main__, download_data, train_tokenizer]:
- the default is `--num-shards 10` (`-1` means all 6,542), and the validation shard is always added. Downloads use `--download-workers 8` by default and 5 attempts per shard with 2^attempt s backoff;
- a BPE tokenizer is trained with `rustbpe` on up to 1e9 characters from the train shards (docs capped at 10,000 chars). The target regular vocabulary is `VOCAB_SIZE − 4` = 8,188 tokens; with the usual 256 byte base tokens that would be about 7,932 merges [inferred; rustbpe internals not read]. The 4 special tokens are appended after it. At least 2 shards (1 train + 1 val) are required;
- the tokenizer is saved as a tiktoken pickle, together with a `token_bytes` lookup (UTF-8 byte length per token id, 0 for special tokens). A round-trip sanity check on a fixed string, including Unicode, must pass.

### 3.2 The experiment loop (run by the agent) [code:ar/program.md "The experiment loop"]

```text
# first iteration: run train.py unmodified → baseline row with status "keep"   [program.md "The first run"]
best ← None
LOOP FOREVER:                                           # "NEVER STOP" … "until the human interrupts you, period"
  1. inspect git state (branch, HEAD)
  2. idea ← agent chooses an experimental idea (context: program.md, train.py, its own session history;
            re-reading results.tsv is not an explicit step upstream [inferred])
     edit train.py directly                                        # the only editable file
  3. git commit                                                    # commit hash identifies the experiment
  4. `uv run train.py > run.log 2>&1`                              # redirect ALL output; no tee
       (watchdog, agent-enforced: if wall time > 10 min → kill; treat as failure: discard + revert)
  5. out ← `grep "^val_bpb:\|^peak_vram_mb:" run.log`
  6. if out empty:                                                 # crash
        read `tail -n 50 run.log`; if trivial (typo, missing import) → fix & re-run (step 3/4)
        if "more than a few attempts" fail or idea fundamentally broken → status ← crash
  7. append row to results.tsv  (results.tsv is NOT committed; stays untracked)
  8. if status ≠ crash and val_bpb < best:   keep   → branch advances (commit stays); best ← val_bpb
  9. else:                                   discard (or crash) → `git reset` back to the pre-experiment commit
     (simplicity criterion may override 8/9 — see §4.2)
  (rare) if stuck: may rewind to an earlier commit, "very very sparingly (if ever)"
  if out of ideas: "think harder — read papers referenced in the code, re-read the in-scope files …,
                    try combining previous near-misses, try more radical architectural changes"
```

Notes on the rule as written [code:ar/program.md]:
- "Improved" is judged against the state "where you started", meaning the current branch tip. Because the tip only advances on keeps, the tip normally holds the best kept result. A rewind, or a simplicity-criterion keep that is not strictly better, breaks that equivalence [inferred].
- A run that exceeds 10 min is "a failure (discard and revert)". The prompt does not say whether its row gets status `discard` or `crash` [code:ar/program.md "Timeout"].

**Multi-agent launcher that shipped in the initial commit and was then deleted (`spawn.sh`)** [code:ar@b11d6f2:spawn.sh]. It was committed in `b11d6f2` and removed about 4.5 minutes later (21:58:52 → 22:03:27 UTC, 6 Mar 2026) in `1e207aa` ("dam, erase experimental file from before that snuck through in my purge"). Commits `ae81d55` and `69eb7f9` then removed references to it from the README and `program.md`.
```text
spawn.sh launch <tag> <agent:gpu> ...        # e.g. "claude:0 claude:1 codex:2 codex:3" or "opus:0 sonnet:1 ..."
  for each agent:gpu:
     branch   ← autoresearch/<tag>-gpu<N>      # must not exist (fresh run)
     worktree ← worktrees/gpu<N> (git worktree add), symlink .venv
     cmd ← cd worktree && CUDA_VISIBLE_DEVICES=<N> claude --dangerously-skip-permissions --model <m> "Read program.md and follow the instructions."
           (codex: --dangerously-bypass-approvals-and-sandbox --model gpt-5.3-codex-spark)
  tmux session "autoresearch-<tag>", one tiled pane per worker
  WATCHER every 120 s: capture each pane; if its content hash is unchanged since the last check AND the
           last line is an idle prompt (❯ or ›) → send "Keep going. Do not stop — continue your research loop."
spawn.sh stop <tag>: kill tmux, remove worktrees, keep branches for review;  spawn.sh status: list sessions/worktrees/branches
```
In this design the agents are **independent greedy chains on separate branches**, one per GPU, with no shared state. The watcher is a mechanical enforcement of "NEVER STOP". The initial `program.md` had a matching branch: "If launched via `spawn.sh` (multi-agent): The branch and worktree already exist … Skip to step 3", and "If launched via `spawn.sh`, proceed directly into the autonomous experiment loop" [code:ar@b11d6f2:program.md]. Traces remain in the released repo: the `-gpu0` branch example, the `.gitignore` entries `worktrees/`, `results/`, `queue/`, `dev/`, `CLAUDE.md` and `AGENTS.md` ("generated per-session by launchers"), and the `pyproject.toml` description "Autonomous pretraining research swarm" [code:ar/.gitignore; code:ar/pyproject.toml].

**`program.md` revision history (the human-edits-the-strategy lever, observed)** [code:ar git log -p program.md]:
- `b11d6f2` (initial): the baseline was *not* re-run. "The baseline results are already known … (val_bpb: 0.997900, peak_vram_mb: 45060.2). Do NOT re-run the baseline — just record it." It said "LOOP FOREVER (until I wake up and come back in the morning)", assumed "~7 min" per experiment, "~8/hour … approx. 80" over "10 or so" hours, and said to log "CRASH".
- `bdf0c0d` / `bd75534` (community PR, 7 Mar): added step 6, "If the grep output is empty, the run crashed. You MUST run `tail -n 50 run.log` …" (commit title "Fix agent crash blindspot by forcing it to read traceback").
- `ada84e5`: softened that step to "attempt a fix. If you can't get things to work after more than a few attempts, give up."
- `47ec1ad`: added "The first run" (establish the baseline by running as is), "~5 minutes total", "approx 12/hour … about 100", and lowercase "crash".
- `8a5c486`: `constants.py` was merged into `prepare.py`.
- `6fdefa7`: the agent is also told to read `README.md`.
- `f16ece4`: `results.tsv` starts "with just the header row. The baseline will be recorded after the first run." Between `47ec1ad` and `f16ece4` (about 30 h), the prompt contradicted itself: the setup step still said "Do NOT re-run the baseline" while "The first run" said to run it. The commit message says the old language "wasn't fully cleaned up".
- `068d93d`: "do not commit the results.tsv file, leave it untracked by git".

### 3.3 One fixed-budget training run (inside `train.py`) [code:ar/train.py]

```text
t_start ← now; seed torch/cuda with 42
tokenizer ← prepare.Tokenizer.from_directory(); V ← vocab size
config ← build_model_config(DEPTH)          # model_dim = ceil(DEPTH·ASPECT_RATIO / HEAD_DIM)·HEAD_DIM
model ← GPT(config) on meta device → to_empty(cuda) → init_weights()
optimizer ← MuonAdamW(param groups)         # AdamW for embeddings/lm_head/scalars, Muon for 2-D matrices
model ← torch.compile(model)
train_loader ← prepare.make_dataloader(tokenizer, DEVICE_BATCH_SIZE, MAX_SEQ_LEN, "train")
grad_accum ← TOTAL_BATCH_SIZE / (DEVICE_BATCH_SIZE·MAX_SEQ_LEN)       # must divide exactly (assert)
τ ← 0; step ← 0
while True:
   for micro in 1..grad_accum: loss ← model(x, y); (loss/grad_accum).backward(); x,y ← next(train_loader)
   p ← min(τ / TIME_BUDGET, 1)
   set lr ← initial_lr · lrm(p); Muon momentum ← μ(step); Muon weight decay ← λ(p)
   optimizer.step(); zero_grad
   if isnan(loss) or loss > 100: print "FAIL"; exit(1)                  # fast-fail
   dt ← step wall time (cuda-synchronized)
   if step > 10: τ ← τ + dt                                             # first 11 steps (compile warm-up) not counted
   step ← step + 1
   if step > 10 and τ ≥ TIME_BUDGET: break
val_bpb ← prepare.evaluate_bpb(model, tokenizer, DEVICE_BATCH_SIZE)    # under bf16 autocast
print summary block: val_bpb, training_seconds, total_seconds, peak_vram_mb, mfu_percent,
                     total_tokens_M, num_steps, num_params_M, depth
```

### 3.4 Evaluation (locked) [code:ar/prepare.py:evaluate_bpb, make_dataloader]

```text
evaluate_bpb(model, tokenizer, B):
  token_bytes ← load token_bytes.pt                 # bytes per token id; 0 for special tokens
  val_loader ← make_dataloader(tokenizer, B, MAX_SEQ_LEN, "val")   # fresh loader each call → same pinned shard
  steps ← EVAL_TOKENS // (B · MAX_SEQ_LEN)
  for s in 1..steps:
     x, y ← next(val_loader)
     ℓ ← model(x, y, reduction='none')              # per-token CE in nats, computed by agent-owned forward
     b ← token_bytes[y]; m ← (b > 0)
     N ← N + Σ ℓ·m;  Bytes ← Bytes + Σ b
  return N / (ln 2 · Bytes)
```

The dataloader is "BOS-aligned … with best-fit packing". Every row starts with BOS. Documents are packed by repeatedly taking the largest document that fits the remaining row. When none fits, the shortest document is cropped to fill the row exactly, so there is no padding (a doc buffer of 1000 documents; tokenization in batches of 128 docs) [code:ar/prepare.py:make_dataloader].

### 3.5 Port variants of the loop

**(a) MLX port.** The loop is the same, with these deviations [code:mlx/program.md]:
- git adds only the `autoresearch-mlx/` paths ("never `git add -A`");
- on keep, `results.tsv` **is committed** via `git commit --amend`; on discard, the agent records the discarded hash and runs `git reset --hard <previous kept commit>`;
- the timeout is 15 min (each experiment takes about 7 min);
- the agent must establish its own baseline on its own hardware ("Do NOT use baseline numbers from other platforms").
- Because `results.tsv` is tracked in this port, the literal step order (step 7 "Record the results in the tsv", then step 9 `git reset --hard <previous kept commit>`) would also revert the discard row just appended. Upstream made `results.tsv` untracked for exactly this reason (commit `068d93d`) [inferred from code:mlx/program.md]. The public `results.tsv` does contain a discard row, so the maintainers kept it somehow [code:mlx/results.tsv].
- `train.py` differences [code:mlx/train.py]: AdamW only ("v0.1: AdamW only. Muon port is future work."), `mx.random.seed(42)`, `STARTUP_EXCLUDE_STEPS = 1` (only step 0 is excluded from the budget, against 11 steps upstream), and `FINAL_EVAL_BATCH_SIZE = 256`, so the eval is 3·524288 / (256·2048) = 3 steps [arithmetic inferred]. The later fix `51add48` caps rows per forward pass at a 2 GiB logits budget [code:mlx/prepare.py:evaluate_bpb].

**Optional noise-aware keep gate: `rigor.py`** [code:mlx/rigor.py]:
```text
rigor run "<desc>" [--seeds S=3] [--confidence c=0.95]:
  h ← sha1(train.py)[:7]; if h already in ledger → skip ("never-repeat")
  best ← argmin_mean over ledger entries with status keep
  samples ← []
  for i in 1..S:
     v ← run `uv run train.py`; parse ^val_bpb; if crash → ledger(crash); return
     samples.append(v)
     if best and i == 1 and v ≥ best.mean → ledger(discard, p=0); return      # fail a clear loser fast
  if best is None → ledger(keep, p=1)  # baseline
  p ← P_boot(mean(cand) < mean(best)) with 20,000 resamples, rng seed 1234
  status ← keep if p ≥ c else discard; append {hash, desc, samples, mean, std, status, p_better} to rigor_ledger.jsonl
  # never edits train.py, touches git, or changes evaluate_bpb — "it only decides"
```
**The "seeds" are really repeated runs.** `run_once()` executes the identical `uv run train.py` each time, and `train.py` pins `mx.random.seed(42)`. So the S samples measure run-to-run nondeterminism ("nondeterministic GPU reductions, even with the seed pinned") rather than variance across seeds. `--seeds` is a repeat count [code:mlx/rigor.py:run_once, docstring; code:mlx/train.py]. Other details: a crash on any repeat records status `crash` with the samples so far. The baseline (first ledger entry) is kept with `p_better = 1.0`. The tool prints a TSV line with `-` in the memory column, and the agent still has to do the git keep or reset itself [code:mlx/rigor.py:score].

**(b) SLURM fork (kcxain).** The agent lives on the login node; compute nodes have no internet [code:slurm/README.md; code:slurm/program.md]:
```text
per experiment: edit train.py; git commit; `slurm-gpu-info` (pick partition with free GPUs)
  `sbatch --partition=<p> --gres=gpu:<N> submit.sh` → job_id
  poll `squeue -j <id> -h` every 30–60 s (empty ⇒ finished); state ← `sacct -j <id> --format=State`
  COMPLETED → grep ^val_bpb in ret-<id>.out; FAILED/CANCELLED/TIMEOUT → crash (read ret-<id>.err)
  PENDING > 10 min → report to user and wait (do not cancel/resubmit); RUNNING > 15 min → crash + revert
  state TIMEOUT (vs the generous `#SBATCH -t 0-12:00:00` in submit.sh) ⇒ "the training script hung; treat as a crash"
  record/keep/reset exactly as upstream
```
**Minimal SLURM fork (Chern30/NUS)** [code:nus/program.md; code:nus/slurm/*]: `bash slurm/submit_and_wait.sh` runs `sbatch --parsable` on a fixed `submit_job.sh` (`--gres=gpu:1 --time=00:20:00 --mem=32G --cpus-per-task=4`, output to `run.log`). It then polls `sacct` every 30 s until COMPLETED (exit 0) or FAILED/CANCELLED/TIMEOUT/NODE_FAIL/OUT_OF_MEMORY (exit 1). The prompt allows "up to 25 minutes total before treating a run as failed" and kills stuck jobs with `scancel`. There is no container.


**Containerized SLURM runner (zzyuanyi)** [code:slurmc/slurm/slurm_runner.py; job_template.sbatch; program_slurm.md]:
- the subcommands are `python -m slurm submit|status|wait|cancel|run|test|dry-run|pull|parse` [code:slurmc/slurm/__main__.py]. `run <tag>` = submit + wait + pull + parse. Submit writes `slurm/ar_<tag>.sbatch` from a template;
- when `container_image` is configured, the template gets `#SBATCH --container-image=… [--container-mounts=…] [--container-writable] [--container-remap-root]` lines, and the run command becomes `srun -l --container-image=… <shell> -c "<pre_cmd> && uv run train.py"`. These are the flags of the NVIDIA pyxis/enroot SLURM plugin [inferred; the repo does not name pyxis]. Alternatives are a conda block (`uv run train.py`) or `docker run --gpus all --rm -v $(pwd):/workspace …` [code:slurmc/slurm/slurm_runner.py:_build_run_command];
- the default `SLURM_TIME` is `"01:00:00"` [code:slurmc/slurm/server_config.py];
- in remote mode the runner syncs code over SSH (scp by default, rsync "as optional optimization" when available, with up to 3 SSH attempts and 1 s then 2 s backoff), submits `sbatch --parsable`, and checks status with `squeue`, falling back to `sacct -X` [code:slurmc/slurm/sync_manager.py; slurm_runner.py:status];
- `wait()` defaults to `timeout=600`, `poll_interval=30` and cancels on timeout. It warns when a job is still PENDING past timeout/2, and returns UNKNOWN after 120 s of unknown state [code:slurmc/slurm/slurm_runner.py:wait];
- `parse_results` returns `val_bpb = 0.0` with an error when the output is missing, starts with the `FAIL` marker, or has no `val_bpb` line [code:slurmc/slurm/slurm_runner.py:parse_results];
- the prompt adds a `timeout` status alongside `crash` ("log 'crash' or 'timeout' in the tsv and git reset") [code:slurmc/program_slurm.md];
- the agent may `submit` several tags and `wait` on each "when queue waits are long". **Caveat:** every tag syncs into the same `remote_project_root`, and the job runs `cd {{REMOTE_PROJECT_ROOT}} && uv run train.py` when it *starts*. A job still PENDING when a later tag is pushed would therefore train the later tag's `train.py`. SkyPilot's per-experiment `--workdir` snapshot avoids this race [inferred from code:slurmc/slurm/sync_manager.py:push_files, job_template.sbatch].
The overview's "research-cluster fork that runs the loop through a job scheduler inside a container" [doc] best matches this repo among true forks, since the other two SLURM forks read (kcxain, Chern30/NUS) have no container support. `HudoGriz/autoresearch-hpc` also fits the literal description: "Nextflow controller → Slurm / PBS / local → Singularity / Apptainer tasks", with pre-declared experiments, append-only records and a review by "a model from another family". But it is an independent protocol layer created in Sep 2026, which lists Karpathy's autoresearch only as "Inspiration and attribution; no imported runtime" [code:hpc/README.md; code:hpc/docs/skills-and-token-budget.md]. I could not determine which specific project the overview meant [inferred].

**(c) Parallel cloud variant (SkyPilot)** [code:sky/instructions.md]:
```text
LOOP FOREVER: check results.tsv, `sky status`, `sky queue`; pick an untried idea
  copy train.py/prepare.py/… to /tmp/autoresearch/<exp-id>/ (workdir isolation), edit there
  `sky launch gpu-0k experiment.yaml --workdir … --env EXPERIMENT_ID=… -d -y`  or `sky exec` (pipeline on same cluster)
  "Don't wait — move on to the next idea"; ≤ 4 clusters at a time
  periodically: `sky logs` / ssh; val_bpb improved → copy winning train.py back, commit; else log discard
  tear down idle clusters
```
The README reports that parallelism changed the search from "greedy sequential hill-climbing" to grid-like exploration. The agent also "independently discovered a two-tier strategy: screening hypotheses on cheaper H100s, then promoting winners to faster H200s" [code:sky/README.md].

**(d) Collaborative "autoresearch at home"** [code:home/program.md; code:home/collab.md; code:home/coordinator.py]:
```text
startup: `nvidia-smi` must find a GPU (else stop setup and point the human to a cloud-GPU guide);
         collaborative mode iff ENSUE_API_KEY or .autoresearch-key exists (else plain solo upstream loop);
         register agent (API key), pick a codename, join hub, announce();
         pull_best_config_for_tier() (fallback: global best); adopt if better → commit "adopt global best (val_bpb=X from Y)"
LOOP FOREVER:
 1. THINK: analyze_swarm(); get_swarm_insights(topic); get_unclaimed_hypotheses(); ask_swarm(q, namespace)
           every 5 runs: pull_best_config_for_tier() and adopt if someone beat you
 2. CLAIM: key ← claim_experiment(desc); if None → pick another idea (≤ 5 tries); if all fail "just run something"
 3–8. edit train.py; commit; run; grep val_bpb, peak_vram_mb, num_steps, total_tokens_M, mfu_percent; record tsv
 9. keep/discard: compare against GLOBAL best and TIER best, "not just your local branch"
10. PUBLISH (mandatory, "no exceptions"):
      publish_result(key, val_bpb, memory_gb, status, desc, full train.py source, extra_metrics)
         → if status == keep: update agent best, maybe_update_best (global), tier best
      post_insight("what I observed and why", evidence_keys)
      publish_hypothesis(title, hypothesis, suggested_config, evidence_keys, priority)
  on any hub error: log and continue solo ("Network is additive, never blocking")
```
Claim protocol [code:home/coordinator.py:claim_experiment, check_claimed, check_similar_claimed, _experiment_key]:
- key = `<slug(agent_id, ≤20 chars)>--<slug(desc, ≤40 chars)>--<sha256(desc.lower().strip())[:6]>`;
- skip if a result already exists for that key (or for a legacy hash-only key), or if a claim on it is less than `CLAIM_TTL` old;
- skip if a semantic search (`search_memories`, limit 5, prefix `claims/`) finds a claim younger than `CLAIM_TTL` with score ≥ `SEMANTIC_THRESHOLD`;
- otherwise write the claim (embedded on its description), sleep `VERIFY_DELAY`, re-read it, and keep it only if the stored `agent_id` is ours. `collab.md` instead says "Earliest `created_at` wins a race", but the code does not compare timestamps; it checks ownership after the wait, so the last writer effectively wins [code vs code:home/collab.md];
- on RPC error, return the key anyway so the agent can train locally. `check_claimed` also returns "not claimed" on error.
- **The exact-key check is per agent.** The key embeds the claimant's own name, so two *different* agents with the same description get different keys. Cross-agent deduplication therefore rests entirely on the semantic search [inferred from code].

**(e) XGBoost / tabular adaptation** [code:xgb/program.md; code:xgbmin/*]:
```text
same setup + loop (branch "<tag>", not "autoresearch/<tag>"); metric = CV AUC (StratifiedKFold 5, shuffle, random_state=42,
  on 2005-slice1-100k.csv) or Eval AUC on 2006-slice1-100k.csv (scenario 2); higher is better; keep iff strictly higher;
  per-run timeout 1 minute ("Research time does not count against the experiment timeout")
baseline train.py: XGBClassifier(n_estimators=30, max_depth=6, learning_rate=0.1, enable_categorical=True, random_state=42);
  also fits a "4/5 model" on fold-0's training split for the post-hoc audit          [code:xgbmin/train.py]
extra rules: before first non-baseline experiment and every 10 experiments → web research; plateau (≥3 consecutive discards
  with <0.001 movement) → research; before each experiment state a hypothesis and classify it as
  follow-up / ablation-simplification / exploration; no near-duplicates; every 10 experiments write a synthesis
  "as a short note in your context (not a file)"; maintain research-log.md linked to results.tsv and commits;
  "Do not access" check_groundtruth.py; do not peek at analysis/ or docs/ or at earlier results via git
feature engineering only inside prepare(df). xgb (May 2026): "Do not create any helper functions etc. outside prepare(df)";
  xgbmin (from commit 9d6ee8b, Sep 2026): per-row features only, and lookups may be fitted on `train` at module level
post hoc (HUMAN ONLY): run_groundtruth_all.sh [results.tsv] [out.tsv] [timeout=3000 s] → for every row that is not a discard,
  not a crash (CV 0.0000) and whose commit still exists: in a temporary git worktree, `git show <commit>:train.py`,
  run check_groundtruth.py (it exec's that train.py) → Test AUC on (full model, 2005-slice2-1m), (4/5 model, 2005-slice2-1m),
  (full model, 2006-slice2-1m); CRASH on non-zero exit, N/A for skipped rows
```
The **budget semantics differ from upstream**. Here 1 minute is a *ceiling*, not a fixed training duration, so the agent trades AUC against wall time. Several discards in the published run had higher CV AUC than the incumbent but were over budget, for example "add Dep20Min cat (huge AUC gain but 90s wall, over budget)" at 0.8287 against the then-best 0.8115. The agent then recovered the gain by compounding with fewer trees: "Dep20Min cat + n_est 1000->500 (compound to fit budget)" at 0.8268 was kept [code:xgb/groundtruth_all.tsv].

---

## 4. Formulas, objectives, acceptance rules, schedules and defaults

### 4.1 Objective: validation bits per byte [code:ar/prepare.py:evaluate_bpb]

Let the evaluation stream contain target tokens y_1…y_N (N = steps·B·T). Define:
- ℓ_t = −ln p_θ(y_t | y_<t), the per-token cross-entropy in **nats** as returned by the model;
- b_t = UTF-8 byte length of token y_t (b_t = 0 for special tokens);
- m_t = 1[b_t > 0].

**val_bpb = ( Σ_t m_t · ℓ_t ) / ( ln 2 · Σ_t b_t )**, lower is better. Because the denominator counts bytes of text rather than tokens, the metric is "vocab size-independent", so architecture or tokenizer changes are "fairly compared" [code:ar/README.md]. In upstream the tokenizer itself is locked in `prepare.py` [inferred]. The model applies a logit soft-cap before the CE: `logits ← 15·tanh(logits/15)` [code:ar/train.py:GPT.forward, softcap = 15].

- Evaluation volume: `EVAL_TOKENS = 40 × 524288 = 20,971,520` tokens. The step count is `EVAL_TOKENS // (B × MAX_SEQ_LEN)`, which is 80 steps at the default B = 128 and T = 2048 [code:ar/prepare.py; arithmetic inferred]. The MLX port uses `3 × 524288` [code:mlx/prepare.py].
- The validation data is the pinned `shard_06542.parquet` (`VAL_SHARD = MAX_SHARD = 6542`). Training uses all other downloaded shards [code:ar/prepare.py].

### 4.2 Acceptance rules

| Variant | Rule | Source |
|---|---|---|
| Upstream (mechanical part) | keep ⇔ `val_bpb_new < val_bpb_best` (strict). "If val_bpb is equal or worse, you git reset back." Crash ⇒ discard. | [code:ar/program.md steps 8–9] |
| Upstream (judgment part) | "Simplicity criterion: All else being equal, simpler is better … A 0.001 val_bpb improvement that adds 20 lines of hacky code? Probably not worth it. A 0.001 val_bpb improvement from deleting code? Definitely keep. An improvement of ~0 but much simpler code? Keep." "VRAM is a soft constraint. Some increase is acceptable for meaningful val_bpb gains, but it should not blow up dramatically." | [code:ar/program.md] |
| Overview's paraphrase | "Keep only if the score is strictly better; equal or worse is reset" | [doc] |
| MLX `rigor.py` | keep ⇔ P̂(mean(cand) < mean(best)) ≥ c, where P̂ = (1/R)·Σ_{r=1..R} 1[ mean(c*_r) < mean(b*_r) ] over bootstrap resamples c*_r, b*_r (with replacement, same sizes as the sample sets). "best" = the kept ledger entry with the lowest mean. Defaults: S = 3 repeated runs (`--seeds`), c = 0.95, R = 20,000, rng seed 1234. Early discard if the first sample ≥ best mean. Identical `train.py` (sha1[:7]) is never re-scored. | [code:mlx/rigor.py:prob_improvement, best_entry, score, main] |
| at-home global best | update best ⇔ status = keep ∧ v > 0 ∧ v ≥ 0.5 ∧ (no current best ∨ (v < current_best ∧ (current_best − v) ≤ 0.1)), re-checked by a second read right before writing (read-compare-write). The new record stores `previous_best_*` for recovery. | [code:home/coordinator.py:maybe_update_best] |
| at-home tier best | **weaker than the global rule**: rejects v ≤ 0 and v < 0.5, and requires v < tier_best. There is **no > 0.1 jump check and no second read** before writing. | [code:home/coordinator.py:_update_tier_best] |
| at-home agent best | v < own current best, with no sanity filters at all | [code:home/coordinator.py:_update_agent_best] |
| at-home local keep | the agent compares against "the **global best** … and your **tier best** …, not just your local branch. If val_bpb improved, keep the git commit. If equal or worse, git reset back." The prompt does not say whether "improved" must beat the global best, the tier best or the branch tip [code:home/program.md step 9; ambiguity inferred] | [code:home/program.md] |
| XGBoost | keep ⇔ AUC_new > AUC_best (strict); equal or worse ⇒ reset; plus the simplicity criterion with "0.001 AUC" wording. In practice the agent also discarded higher-AUC runs that exceeded the 1-minute limit (logged as `discard` with their AUC, not as `crash`) | [code:xgb/program.md; code:xgb/groundtruth_all.tsv] |

### 4.3 Budget accounting [code:ar/train.py]

- τ = Σ_{s > 10} dt_s, where dt_s is the synchronized wall time of optimizer step s, including its gradient-accumulation micro-steps. Steps 0–10 (11 steps) are excluded "so we don't count compilation". The run stops at the first step where step > 10 ∧ τ ≥ `TIME_BUDGET` (= 300 s).
- Progress p = min(τ / TIME_BUDGET, 1). **All schedules are functions of p (time), not of step count** (the exception is Muon momentum, which uses step).
- Evaluation, startup and compile time are *not* budgeted. `total_seconds` reports wall time end-to-end (example: 325.9 s for 300.1 s of training) [code:ar/program.md output example].
- Agent-side watchdog: a total run over 10 min is a failure (upstream). The MLX port allows 15 min total; the kcxain SLURM fork allows 15 min *in RUNNING state* (queue time excluded); the NUS SLURM fork allows 25 min including queue wait; the containerized runner's `wait` cancels after 600 s by default [code:ar/program.md; code:mlx/program.md; code:slurm/program.md; code:nus/program.md; code:slurmc/slurm/slurm_runner.py:wait]. The XGBoost port uses 1 minute [code:xgb/program.md].
- The budget loop never checks the clock *during* a step, so one very long step can overshoot the budget. The agent-side 10-min kill is the only backstop [inferred from code:ar/train.py].

### 4.4 Schedules and formulas inside the baseline `train.py` [code:ar/train.py]

With w = `WARMUP_RATIO`, d = `WARMDOWN_RATIO`, f = `FINAL_LR_FRAC`:
- **LR multiplier:** lrm(p) = p/w if p < w (and 1 if w = 0); 1 if p < 1 − d; otherwise c + (1 − c)·f with c = (1 − p)/d. This is linear warmdown to f.
- **Muon momentum:** μ(s) = (1 − φ)·0.85 + φ·0.95 with φ = min(s/300, 1).
- **Muon weight decay:** λ(p) = `WEIGHT_DECAY`·(1 − p). It is "cautious": elementwise, decay applies only where g·θ ≥ 0. The update is θ ← θ − η·g̃ − η·λ·θ·1[g̃·θ ≥ 0], so the decay is also scaled by the scheduled LR η = η₀·lrm(p) [code:ar/train.py:muon_step_fused].
- **AdamW LR scaling:** lr_group × (d_model/768)^(−0.5) for the lm_head, embedding and value-embedding groups. At d_model = 512 the factor is ≈ 1.2247 [arithmetic inferred]. The resid-lambda group uses `SCALAR_LR·0.01` and the x0-lambda group uses `SCALAR_LR` with betas (0.96, 0.95), both without the d_model factor. **Every AdamW group has `weight_decay = 0.0` and `eps = 1e-10`**, so the embedding and value-embedding weight decay that #43 found helpful was a new addition [code:ar/train.py:setup_optimizer; gh-disc:#43].
- **Muon per-shape LR:** lr × max(1, rows/cols)^0.5. Muon covers every parameter under `transformer.h`, grouped by shape (including the 2-D `ve_gate` weights). The steps are Nesterov momentum; normalization X/(‖X‖·1.02 + 1e-6); polar-express orthogonalization in bf16 with 5 fixed coefficient triples (`ns_steps = 5`); then "NorMuon" variance reduction (β₂ = 0.95). The group's initial `momentum=0.95` is overwritten every step by μ(s) [code:ar/train.py:muon_step_fused, setup_optimizer].
- **Baseline architecture** (the objects the agent's kept edits touch) [code:ar/train.py:GPT, Block, MLP, CausalSelfAttention]:
  - pre-norm blocks with parameter-free RMSNorm and a ReLU² MLP (4× width);
  - QK-norm after RoPE, with RoPE base 10000 and precomputed length 10·T;
  - untied `wte`/`lm_head`;
  - per-layer scalars: x ← λ_resid[i]·x + λ_x0[i]·x0, with λ_resid init 1.0 and λ_x0 init 0.1;
  - init: `wte` ~ N(0, 1), `lm_head` ~ N(0, 0.001²), Q/K/V/`c_fc` ~ U(±√3·d^(−1/2)), the two output projections zero-initialized, `ve_gate` zero-initialized (gate = 1);
  - embeddings are cast to bf16.
- **Model sizing:** model_dim = ⌈DEPTH·ASPECT_RATIO / HEAD_DIM⌉·HEAD_DIM; n_head = model_dim / HEAD_DIM; n_kv_head = n_head. The defaults give 8·64 = 512, so 4 heads of 128. Value embeddings sit on alternating layers (the last layer always included) with an input-dependent gate 2·σ(W·x[:32]). Windows: "S" = half context, "L" = full, and the last layer is always full.
- **Gradient accumulation:** TOTAL_BATCH_SIZE / (DEVICE_BATCH_SIZE·MAX_SEQ_LEN) = 2^19 / (128·2048) = 2 [arithmetic inferred].
- **Fast fail:** print "FAIL" and exit(1) if the last micro-step's training loss is NaN or > 100. The check runs after `optimizer.step()`. The explicit NaN check was added on 9–10 Mar (`b5ba8ac`/`0be1e4f`/`ebf3578`), because `train_loss_f > 100` "silently passes on NaN" and made the run waste the full budget [code:ar git log].
- **MFU (report only):** the per-step log line prints 100 · F · TOTAL_BATCH_SIZE / dt / 989.5e12 (H100 bf16 peak). The **summary** `mfu_percent` is steady-state: 100 · F · TOTAL_BATCH_SIZE · (steps − 10) / τ / 989.5e12. Here F = FLOPs/token = 6·(all params − `wte` − value embeddings − the two per-layer scalar vectors) + Σ_layers 12·h·q·min(window, T); the `lm_head` *is* counted [code:ar/train.py:estimate_flops, summary block]. Other summary fields: `total_tokens_M` = steps · TOTAL_BATCH_SIZE / 1e6, which includes the 11 unbudgeted warm-up steps; `num_params_M` includes all embeddings; `peak_vram_mb` = `torch.cuda.max_memory_allocated()`/1024².
- **Logging:** EMA of the train loss with β = 0.9, debiased by 1 − β^(s+1). At step 0 Python GC runs `collect()`, `freeze()` and `disable()` ("Python's GC causes ~500ms stalls"). After that it collects only when (s+1) % 5000 = 0.
- **Environment:** `PYTORCH_ALLOC_CONF=expandable_segments:True`; `torch.set_float32_matmul_precision("high")`; FA3 kernel `varunneal/flash-attention-3` on Hopper (capability (9, 0)), else `kernels-community/flash-attn3` (the fallback added in `17b480a`) [code:ar/train.py].

### 4.5 Default hyperparameters

`prepare.py` (locked) [code:ar/prepare.py]:

| Constant | Value |
|---|---|
| `MAX_SEQ_LEN` | 2048 |
| `TIME_BUDGET` | 300 s |
| `EVAL_TOKENS` | 40·524288 |
| `VOCAB_SIZE` | 8192 (4 special `<\|reserved_i\|>`; BOS = `<\|reserved_0\|>`) |
| Data | `karpathy/climbmix-400b-shuffle` parquet shards; `MAX_SHARD = 6542`; default 10 train shards |
| BPE split pattern | GPT-4 style with `\p{N}{1,2}` |

`train.py` (editable baseline) [code:ar/train.py]:

| Knob | Default | Knob | Default |
|---|---|---|---|
| `ASPECT_RATIO` | 64 | `EMBEDDING_LR` | 0.6 |
| `HEAD_DIM` | 128 | `UNEMBEDDING_LR` | 0.004 |
| `WINDOW_PATTERN` | "SSSL" | `MATRIX_LR` | 0.04 |
| `DEPTH` | 8 | `SCALAR_LR` | 0.5 |
| `DEVICE_BATCH_SIZE` | 128 | `WEIGHT_DECAY` | 0.2 |
| `TOTAL_BATCH_SIZE` | 2^19 | `ADAM_BETAS` | (0.8, 0.95) |
| seed | 42 | `WARMUP_RATIO` / `WARMDOWN_RATIO` / `FINAL_LR_FRAC` | 0.0 / 0.5 / 0.0 |

Note that `GPT.setup_optimizer`'s own keyword defaults (embedding_lr=0.2, matrix_lr=0.02, weight_decay=0.0) are overridden by the module constants above.

MLX public baseline differs: `DEPTH = 4`, `TOTAL_BATCH_SIZE = 2**16`, `DEVICE_BATCH_SIZE = 16`, AdamW-only [code:mlx/train.py; code:mlx/README.md]. These defaults (with `MATRIX_LR = 0.04`) are the *end state* of the published first walk, the 1.807902 config ("halve total batch size to 2^16", "increase matrix LR to 0.04", "reduce depth from 8 to 4"). They are not the configuration that scored the 2.667 baseline [code:mlx/results.tsv; code:mlx/train.py; inferred]. The README's advice for small computers is to use TinyStories, a vocab of 4096 down to 256 (byte-level), a much lower `MAX_SEQ_LEN` (as low as 256), a lower `EVAL_TOKENS`, `DEPTH` around 4, `WINDOW_PATTERN = "L"` and `TOTAL_BATCH_SIZE` around 2^14 [code:ar/README.md "Platform support"].

### 4.6 House-rule thresholds and throughput

- About 12 experiments/hour and about 100 per night ("over the duration of the average human sleep"; 100/12 ≈ 8.3 h is my arithmetic [inferred]) [code:ar/README.md; code:ar/program.md]. The initial `program.md` said "~7 min" per experiment, "~8/hour" and "approx. 80" over "10 or so" hours [code:ar@b11d6f2:program.md]. The MLX port gets about 8–9/hour, about 70/night [code:mlx/program.md]. XGBoost gets about 60/hour, about 480/night [code:xgb/program.md].
- Crash logging: `val_bpb = 0.000000` and `memory_gb = 0.0` [code:ar/program.md].
- `memory_gb = round(peak_vram_mb / 1024, 1)` [code:ar/program.md].

### 4.7 Port parameters

| Port | Parameter | Value | Source |
|---|---|---|---|
| at-home | `CLAIM_TTL` | 900 s ("3x expected 5-min experiment") | [code:home/coordinator.py] |
| at-home | `VERIFY_DELAY` | 2 s | same |
| at-home | `SEMANTIC_THRESHOLD` | 0.92 | same |
| at-home | `MAX_CLAIM_ATTEMPTS` | 5 | same |
| at-home | `SYNC_EVERY_N` | 5 experiments | same |
| at-home | VRAM tiers | small ≤ 16 GB, medium ≤ 24, large ≤ 48, xl > 48 | same |
| at-home | best sanity | global: reject v ≤ 0, v < 0.5, single-step improvement > 0.1; tier: only v ≤ 0 and v < 0.5; agent: none | same:maybe_update_best, _update_tier_best, _update_agent_best |
| at-home | search limits | claim similarity search top-5; `ask_swarm` top-20; `analyze_swarm` top-30 results and top-20 claims (semantic search, not time-ordered); `announce` counts ≤ 200 results and ≤ 50 claims | [code:home/coordinator.py] |
| upstream (deleted `spawn.sh`) | idle-nudge interval | 120 s | [code:ar@b11d6f2:spawn.sh] |
| MLX | startup steps excluded / final-eval batch | 1 / 256 | [code:mlx/train.py] |
| SLURM (zzyuanyi) | default job time limit | `SLURM_TIME = "01:00:00"` | [code:slurmc/slurm/server_config.py] |
| XGBoost | ground-truth audit timeout per commit | 3000 s | [code:xgbmin/run_groundtruth_all.sh] |
| MLX rigor | repeats (`--seeds`) / confidence / bootstrap trials / rng | 3 / 0.95 / 20,000 / 1234 | [code:mlx/rigor.py] |
| MLX | measured noise | "re-running the *same* `train.py` moves `val_bpb` by ~0.03" | [code:mlx/README.md; code:mlx/rigor.py docstring] |
| SLURM (kcxain) | poll / pending / running limits | 30–60 s / 10 min then report / 15 min then crash | [code:slurm/program.md] |
| SLURM (zzyuanyi) | wait timeout / poll | 600 s / 30 s | [code:slurmc/slurm/slurm_runner.py:wait] |
| SkyPilot | max concurrent clusters | 4 | [code:sky/instructions.md] |
| XGBoost | research cadence / synthesis cadence / plateau trigger | every 10 exps / every 10 exps / ≥ 3 consecutive discards with < 0.001 movement | [code:xgb/program.md] |

### 4.8 Analysis formulas [code:ar/analysis.ipynb]

- Keep rate = n_keep / (n_keep + n_discard). Crashes are excluded from the denominator.
- Running best = cumulative minimum of `val_bpb` over KEEP rows. The plot shows only non-crash points with `val_bpb ≤ baseline + 0.0005`.
- Per-keep contribution Δ_i = bpb_{previous KEEP} − bpb_i. The printed "TOTAL" = Σ Δ_i, which telescopes to bpb_{first KEEP} − bpb_{last KEEP}. That equals baseline − best only when every keep improved on the previous one; a simplicity-criterion keep can make a Δ_i negative [code:ar/analysis.ipynb "Top Hits"; telescoping inferred]. Baseline = first row of the TSV.
- Total improvement % = (baseline − best) / baseline × 100.

---

## 5. Data structures and artifacts

**`results.tsv`** (tab-separated because "commas break in descriptions"; upstream leaves it **untracked by git** so that `git reset` does not erase history) [code:ar/program.md; commit 068d93d]:

| Column | Type | Semantics |
|---|---|---|
| `commit` | str (7-char short hash) | the experiment's commit. After a reset, discarded commits are unreachable from the branch but stay in the object store until `git gc`; the XGBoost audit script tests existence with `git cat-file -e` [code:xgbmin/run_groundtruth_all.sh] |
| `val_bpb` | float, 6 dp | 0.000000 for crashes |
| `memory_gb` | float, 1 dp | peak VRAM GB; 0.0 for crashes |
| `status` | enum `keep \| discard \| crash` | first row (baseline) = keep |
| `description` | str | "short text description of what this experiment tried" |

The analysis notebook upper-cases `status` and coerces numerics [code:ar/analysis.ipynb]. at-home standardizes descriptions as `<param> <old> → <new>`, for example `LR 0.001 → 0.04` [code:home/collab.md]. The XGBoost port uses 4 columns, `commit  CV_AUC|Eval_AUC  status  description` [code:xgb/program.md]. The SkyPilot variant uses `experiment_id  status  val_bpb  memory_gb  description` [code:sky/instructions.md].

**`run.log` summary block** (key/value lines after `---`, parsed with `grep "^key:"`) [code:ar/train.py; code:ar/program.md]: `val_bpb, training_seconds, total_seconds, peak_vram_mb, mfu_percent, total_tokens_M, num_steps, num_params_M, depth`. On failure the log shows no `val_bpb:` line, and "FAIL" appears on the fast-fail path.

**Git state** [code:ar/program.md]: the branch `autoresearch/<tag>` (e.g. `autoresearch/mar5-gpu0` for a per-GPU branch) is a *linear chain of kept commits*. Discards live only as `results.tsv` rows. The `.gitignore` reserves `worktrees/`, `results/`, `queue/`, `dev/` and launcher-generated `CLAUDE.md`/`AGENTS.md` ("Agent prompt files (generated per-session by launchers)"). These are leftovers of the multi-agent launcher `spawn.sh`. It **was** in the initial public commit `b11d6f2`, was deleted in `1e207aa`, and its references were cleaned up in `ae81d55` and `69eb7f9`. It created one `autoresearch/<tag>-gpu<N>` branch and one `worktrees/gpu<N>` worktree per agent (§3.2) [code:ar@b11d6f2:spawn.sh; code:ar/.gitignore; git log].

**MLX rigor ledger** `rigor_ledger.jsonl` [code:mlx/rigor.py:record]: `{hash: sha1(train.py)[:7], desc, samples: [float], mean, std (population), status: keep|discard|crash, p_better}`.

**at-home shared store** (Ensue keys under `@autoresearch-at-home/`) [code:home/collab.md; code:home/coordinator.py]:

| Namespace / key | Fields (as written by `coordinator.py`) |
|---|---|
| `claims/<agent>--<slug>--<hash6>` | `agent_id, description, experiment_key, claimed_at (ISO UTC), expected_duration_seconds=300, status="claimed"`; embedded for semantic search on the description |
| `results/<key>` | `agent_id, val_bpb, memory_gb, vram_tier, vram_total_gb, status, commit, description, train_py (full source), repo_url, branch, commit_url, completed_at, delta_vs_best, global_best_at_publish, delta_vs_own_best, agent_best_at_publish`, plus the `extra_metrics` dict **merged in as top-level fields** (`num_steps, total_tokens_M, mfu_percent`). The entry is embedded for semantic search on a description string `"[<agent> <STATUS>] val_bpb=… (delta=…) \| <desc>"` |
| `best/train_py`, `best/metadata` | the result fields plus `best_val_bpb, achieved_by, achieved_at, previous_best_val_bpb, previous_best_by, previous_best_description, improvement_over_previous` |
| `best/tier/<tier>/{train_py,metadata}` | the result fields minus `train_py`, plus `vram_tier, best_val_bpb, achieved_by, achieved_at, previous_best_val_bpb, previous_best_by` |
| `best/agent/<name>` | `agent_id, val_bpb, description, memory_gb, vram_tier, vram_total_gb, achieved_at, previous_best_val_bpb` (no source code) |
| `hypotheses/<agent>--<slug(title)>--<sha256(title)[:6]>` | `agent_id, title, hypothesis, suggested_config (dict), evidence_keys, priority (default 3), created_at`. Nothing ever marks a hypothesis claimed or tested: `get_unclaimed_hypotheses` is just a semantic search over `hypotheses/` |
| `insights/<agent>--<slug(insight)>--<sha256(insight)[:6]>` | `agent_id, insight, evidence_keys, posted_at` |
| `leaderboard` | `{entries: [...]}`, seeded as `{"entries": [], "updated_at": null}`. The client only reads it; nothing in this repo writes it after seeding |

Permissions and seeding [code:home/setup_hub.py]:
- participants get read and create on `claims/`, `results/`, `hypotheses/`, `insights/`, `best/` and `leaderboard`, plus update on `best/` and `leaderboard`;
- `leaderboard`, `best/` and `results/` are made publicly readable;
- the hub is seeded with `best/train_py` = the baseline `train.py` and `best/metadata` = `{val_bpb: null, status: "baseline", agent_id: "hub-setup", …}`, so the first kept result with v ≥ 0.5 becomes the global best without a jump check.

**XGBoost post-hoc audit** `groundtruth_all.tsv` [code:xgbmin/run_groundtruth_all.sh]: `commit, status, description, cv_auc, test_auc_2005s2_full, test_auc_2005s2_4_5, test_auc_2006_full`. Discards and crashes are filled with N/A; a commit whose audit run fails gets CRASH. It is produced in a temporary git worktree so the main repo is never touched. There is also `research-log.md`: per-experiment hypothesis, change, classification, result, status and take-away [code:xgb/research-log.md].

**SLURM artifacts**: `ret-<jobid>.out` and `ret-<jobid>.err` [code:slurm/submit.sh]; generated `slurm/ar_<tag>.sbatch` and pulled logs `slurm_results/run_<tag>_<job_id>.{out,err}` [code:slurmc/slurm/slurm_runner.py:submit, parse_results; code:slurmc/program_slurm.md]; `run.log` written by SLURM itself in the NUS fork [code:nus/slurm/submit_job.sh].

**Deleted launcher state** [code:ar@b11d6f2:spawn.sh]: tmux session `autoresearch-<tag>` (one pane per agent), worktrees `worktrees/gpu<N>`, branches `autoresearch/<tag>-gpu<N>` ("Branches kept for review"), and the watcher's per-pane content hash (md5 of `tmux capture-pane`).

---

## 6. LLM roles and the gist of their prompts

Upstream has **one LLM role**: a general coding agent ("Claude/Codex or whatever you want … and disable all permissions") that acts as researcher, engineer, experiment runner and bookkeeper at once. Its "prompt" is `program.md` plus the in-scope files [code:ar/README.md]. The **human** is the meta-optimizer: they edit `program.md`. The README frames this as finding the "research org code" that "achieves the fastest research progress, how you'd add more agents to the mix, etc." [code:ar/README.md]. There is no separate critic, judge or proposer model. In the deleted `spawn.sh`, the only non-LLM "role" was a shell watcher that re-prompts idle agents with "Keep going. Do not stop — continue your research loop." Its initial prompt to each agent was "Read program.md and follow the instructions." [code:ar@b11d6f2:spawn.sh]. The published session reports (#32, #43) were themselves written and posted by the agent ("an automated post from an autoresearch agent running on behalf of @karpathy") [gh-disc:#32; gh-disc:#43].

**Kick-off message** (verbatim) [code:ar/README.md]: `Hi have a look at program.md and let's kick off a new experiment! let's do the setup first.`

**Key `program.md` fragments** (verbatim) [code:ar/program.md]:
- "This is an experiment to have the LLM do its own research."
- "**The goal is simple: get the lowest val_bpb.** Since the time budget is fixed, you don't need to worry about training time — it's always 5 minutes. Everything is fair game … The only constraint is that the code runs without crashing and finishes within the time budget."
- "Modify `prepare.py`. It is read-only. … Install new packages or add dependencies. … Modify the evaluation harness. The `evaluate_bpb` function in `prepare.py` is the ground truth metric." (the "CANNOT" list)
- "**The first run**: Your very first run should always be to establish the baseline, so you will run the training script as is."
- "Run the experiment: `uv run train.py > run.log 2>&1` (redirect everything — do NOT use tee or let output flood your context)"
- "If val_bpb improved (lower), you "advance" the branch, keeping the git commit … If val_bpb is equal or worse, you git reset back to where you started"
- "The idea is that you are a completely autonomous researcher trying things out. If they work, keep. If they don't, discard. … If you feel like you're getting stuck in some way, you can rewind but you should probably do this very very sparingly (if ever)."
- "**Crashes**: … If it's something dumb and easy to fix (e.g. a typo, a missing import), fix it and re-run. If the idea itself is fundamentally broken, just skip it, log "crash" as the status in the tsv, and move on."
- "**NEVER STOP**: … do NOT pause to ask the human if you should continue. … The human might be asleep … If you run out of ideas, think harder — read papers referenced in the code, re-read the in-scope files for new angles, try combining previous near-misses, try more radical architectural changes. The loop runs until the human interrupts you, period."

**Port-specific prompt additions**:
- *MLX*: "Run `uv run train.py` once to establish YOUR baseline on this hardware. Do NOT use baseline numbers from other platforms." and "Compare only against your own baseline on the same hardware." [code:mlx/program.md].
- *SLURM*: "Poll every 30–60 seconds. Do not busy-loop." and "If the job is stuck in `PENDING` for more than 10 minutes, report to the user and wait; do not cancel or resubmit unless instructed." [code:slurm/program.md]. The containerized variant lists under "What you CANNOT do": "Modify any file in `slurm/`. These files are cluster configuration." and "Call `ssh`, `sbatch`, `squeue`, `scp` directly. Always use `python -m slurm <command>`." [code:slurmc/program_slurm.md].
- *at-home* [code:home/collab.md]:
  - "**The goal is to improve the global best, not your local best.** Your baseline is whatever the swarm's current best is"
  - "THINK — decide what to try next. This is the most important step. Don't skip it."
  - "don't waste a run on something the swarm already answered"
  - "If you can't claim anything after 5 tries, just run something — a rare duplicate beats doing nothing."
  - on publishing: "You spent a full context window reasoning about this experiment … If you don't share it, every other agent has to redo that same thinking from scratch."
  - insights and hypotheses are "**mandatory every time**".
  - Its README quotes Karpathy: "The next step for autoresearch is that it has to be asynchronously massively collaborative for agents (think: SETI@home style). The goal is not to emulate a single PhD student, it's to emulate a research community of them." [code:home/README.md].
- *XGBoost* [code:xgb/program.md]:
  - "Everything is fair game that will lead to a model that generalizes on unseen data"
  - "Search the web and read external resources. This is not optional"
  - "State a short **hypothesis** … Classify the experiment as one of: *follow-up* …, *ablation/simplification* …, or *exploration*"
  - "**Do not** run near-duplicate experiments"
  - "Do not read, run, or reference `run_groundtruth_all.sh`. This is a human-only tool … If you find yourself wanting to use it, stop and tell the human immediately"
  - "Do not use git to peek at earlier results"
  - "Research time does not count against the experiment timeout."
  - the leakage test, **only in the later template** [code:xgbmin/program.md, added in commit `9d6ee8b` "forbid frame-dependent features in prepare(), which broke both sol5.6 runs"]: "`prepare(df)` must compute each row's features from that row alone, plus lookups fitted on `train`." Its quick test: "if you ran `prepare()` on a random half of the training data instead of all of it, would this column change for a given row? If yes, the feature is broken … A feature that fails this test can raise your CV AUC while making the model strictly worse on unseen data, and you will not notice from the CV number alone."
- *SkyPilot*: "Don't wait — move on to the next idea." and "Keep at most **4 clusters** running at a time." [code:sky/instructions.md].

---

## 7. Experimental protocol and headline results (as reported)

Autoresearch has no controlled study. Results are single runs posted by the author or port maintainers. The overview calls the evidence "open demo, anecdotal results" [doc].

### 7.1 Upstream, H100, default `program.md`

| Report | Experiments | Kept / discarded / crash | Wall time | val_bpb start → best | Source |
|---|---|---|---|---|---|
| Teaser `progress.png` | 83 | 15 kept (incl. baseline) | — | ≈0.998 → ≈0.977 (read from plot) | [code:ar/progress.png] |
| Session report #32 (branch `autoresearch/mar5`) | 89 | 15 / 74 / 0 | ~7.5 h | 0.997900 → 0.977287 (−0.0206) | [gh-disc:#32] |
| Session report #43 (branch `autoresearch/mar8`; started from the original baseline and "applied batch halving, depth 9, SSSSL, RoPE 200K immediately" from #32) | 126 | 23 / 102 / 1 | ~10.5 h, H100 80GB | 0.9979 → 0.9697 (−0.0282) | [gh-disc:#43] |

Kept changes annotated in `progress.png`, in order [code:ar/progress.png]:
1. baseline
2. halve total batch 524K→262K ("more steps")
3. warmdown 0.5→0.7
4. add 5% warmup
5. depth 9, aspect_ratio 57 (same dim 512 …)
6. x0_lambda init 0.1→0.05
7. unembedding LR 0.004→0.008
8. SSSSL window pattern
9. short window 1/4 context
10. short window 1/8 context (256 tokens)
11. embedding LR 0.6→0.8
12. RoPE base 10000→50000
13. RoPE base 50000→100000
14. RoPE base 100000→200000
15. **random seed 42→137**

The commit that added the figure has the message "bunch of small changes to docs and files, and a teaser figure with a blooper :)" [code:ar git log 8a5c486]. That the blooper is the seed change is my inference [inferred]. The 14 non-baseline keeps in the figure are exactly the 14 listed in #32, and the figure ends at the same best (≈ 0.977). So the teaser is probably the #32 (`mar5`) run plotted after 83 of its 89 experiments [inferred].

Per-keep deltas reported in #32 (in the order listed there) [gh-disc:#32]:
- halve batch −0.007179
- depth 9 / AR 57 −0.002919
- short window 1/4 −0.002234
- 5% warmup −0.002233
- SSSSL −0.001242
- RoPE 10k→50k −0.000650
- RoPE 50k→100k −0.000255
- RoPE 100k→200k −0.000292
- short window 1/8 −0.000275
- embedding LR 0.6→0.8 −0.000674
- x0_lambda init 0.1→0.05 −0.000566
- unembedding LR 0.004→0.008 −0.000576
- warmdown 0.5→0.7 −0.001079
- **random seed 42→137 −0.000439**

They sum to 0.020613 = 0.997900 − 0.977287 [arithmetic inferred]. A pure seed change therefore contributed about 2% of the reported total.

The largest single gain in #32 was the batch halving (−0.0072), summarised as "More steps > more params. Halving batch size was the single biggest win." [gh-disc:#32]. #43 found that weight decay on embeddings and value embeddings helped: "Adding tiny amounts (0.001 for embeddings, 0.001→0.002→0.003 for VEs) stacked for ~0.0028 total improvement", while "0.005 VE WD regressed". It also found that a transformer init-scale reduction helped: "0.8x→0.7x→0.68x, but 0.66x and 0.65x both regressed". #43 also reported that "5% warmup did NOT reproduce — actually hurt this time (+0.0008) … Seed 137 also didn't help here (+0.0007). These things are fragile." [gh-disc:#43].

### 7.2 Transfer to a real training run (author's own follow-up) [nanochat:LEADERBOARD]

| nanochat leaderboard entry | Change source | "Time to GPT-2" (8×H100) |
|---|---|---|
| Run 4 (before) | manual | 2.02 h (stated as the "from" value in Run 5's note) |
| Run 5, 9 Mar 2026, commit `6ed7d1d` | "all of the improvements … came from fully autonomous "research" done by a private version of autoresearch run on a d12 model … The changes easily translated from d12 to d24". `--target-param-data-ratio=8.7`. "I ran 5 identical runs, the average CORE was 0.2690" (threshold 0.2565), and val loss 0.71808 < Run 4's 0.71854 | 1.80 h |
| Run 6, 14 Mar 2026, commit `a825e63` | "autoresearch round 2, where I asked it to reference the modded-nanogpt repo for inspiration"; found a way to make "backout" and "smear" help; ratio 8 | 1.65 h per run (99 min); average CORE of 5 runs 0.262634 |

Note the contrast in acceptance rules: inside the loop a change is kept on **one** 5-minute run, but Karpathy's own acceptance of the transferred result on the leaderboard used **5 identical runs** plus a val-loss safety margin [nanochat:LEADERBOARD Run 5].

A search snippet of Karpathy's tweet adds "~20 changes that improved the validation loss … all of them were additive and transferred to larger (depth=24) models" [web-snippet: x.com/karpathy/status/2031135152349524125]. This is not verified.

### 7.3 Ports

| Port | Setting | Result | Source |
|---|---|---|---|
| autoresearch-mlx, first public walk | M-series Mac (repo history says M1 Mac Studio, "M4 Max results coming") | 2.667000 → 2.588904 (halve batch to 2^16) → 2.533728 (matrix LR 0.04) → **1.807902** (depth 8→4); one discard (WD 0.1: 2.695369) | [code:mlx/results.tsv; code:mlx/README.md; git log 54ad8a5] [doc] |
| autoresearch-mlx, longer runs | M4 Max #1 / M4 Max #2 / Mac Mini | 1.596971→1.294526 / 1.807902→1.330509 / 1.922472→1.353329; "Later transfer tests showed some of those Mac Mini findings did not carry cleanly onto the Max baseline" | [code:mlx/README.md] |
| SkyPilot parallel | 16 GPUs, 8 h | ~910 experiments (~90/h vs ~10/h sequential); "9x speedup to reach the same best validation loss"; 1.003 → 0.974 (2.87%); "$9 in Claude API calls + $300 in GPU compute" | [code:sky/README.md] |
| autoresearch-at-home | first coordinated run | "20+ agents completed over 1,000 experiments in 54 hours and improved validation performance by 3.2%" (**unverified**; re-checked 24 Sep 2026: still only a search summary, ensue.dev is egress-blocked). The same search summary notes no repo commits after 13 Mar 2026; the local clone's HEAD `d195e2f` is dated 12 Mar 2026 | [web-snippet: search summary of ensue.dev blog / rywalker.com; code:home git log] |
| Another collaborative version | — | "13 agents worldwide logging 472 experiments and 30 improvements" (**unverified**) | [web-snippet: search summary] |

### 7.4 XGBoost adaptation (airline delay, binary AUC) [code:xgb/*]

Protocol:
- The agent (Claude Code) optimizes on 5-fold CV over a 100k-row 2005 slice (scenario 1), or on a 2006 eval slice (scenario 2).
- The human post-hoc evaluates kept commits on a 1M-row 2005 slice ("full" and "4/5" models) and on 2006 data (a time-shifted holdout) [code:xgb/docs/index.md; code:xgbmin/check_groundtruth.py].
- Each run is capped at 1 minute [code:xgb/program.md].

Scenario 1 (`groundtruth_all.tsv`): 70 experiments, 19 keep, 51 discard, 0 crash. The final kept commit is `c1ad65c` ("sin/cos hour period 6h -> 4h (borderline noise)", CV 0.8319); the last two rows are discards [code:xgb/groundtruth_all.tsv].

| | baseline | final kept | Source |
|---|---|---|---|
| CV AUC (seen by agent) | 0.7445 | 0.8319 | [code:xgb/groundtruth_all.tsv] |
| Test AUC 2005 slice 2, full model (hidden) | 0.7479 | 0.8469 | same |
| Test AUC 2006, full model (hidden, time-shifted) | 0.7155 | 0.7436 | same |

**The first four keeps raised CV AUC but lowered the 2006 holdout.** n_estimators 30→200→500→1000 and adding DepHour moved CV 0.7445→0.7590 while 2006 AUC fell 0.7155→0.7056 [code:xgb/groundtruth_all.tsv]. Over the same four keeps, the *same-period* hidden test rose with CV: 2005-slice-2 full 0.7479→0.7679, and 4/5 0.7454→0.7602. So this is a failure to transfer across time, not adaptive overfitting to the CV folds. Across the whole chain the 4/5-model hidden AUC tracks CV closely: final CV 0.8319 against hidden 4/5 0.8341, with gains of +0.0874 and +0.0887 from the baseline [code:xgb/groundtruth_all.tsv; differences computed, inferred]. The agent also kept changes it itself called noise-level: two TSV descriptions end in "(borderline noise)" (+0.0001 CV each), and the research log calls a third keep (`max_cat_threshold 128 -> 256`, +0.0003) "borderline noise but kept". The CV fold std it printed ranged from about ±0.0024 to ±0.0059 (e.g. "0.7445 ± 0.0043", "0.7563 ± 0.0059", "0.8314 ± 0.0024"). It also kept a run with a 65.7 s wall time, reading the 1-minute limit's "+ a few seconds" as an allowance [code:xgb/groundtruth_all.tsv; code:xgb/research-log.md].

Comparison against other methods ("AUC" column; the multi-run files call it test_AUC) [code:xgb/analysis/a07-compare_all_methods/results-methods.csv]:

| Method | AUC |
|---|---|
| FE + AutoGluon ensemble | 0.8857 |
| Claude FE + XGB HPO (run 1, iteration 178) | 0.8431 |
| Claude XGB HPO only | 0.8319 |
| FE + Optuna XGB HPO | 0.8309 |
| AutoGluon ensemble | 0.8296 |
| Optuna XGB HPO | 0.8203 |
| XGB basic (tweaked) | 0.7926 |
| XGB super basic (starter) | 0.7479 |
| logistic regression | 0.7155 |

The feature-engineering vs hyperparameter-optimization decomposition attributes about 60% of the gain to FE and about 40% to HPO [code:xgb/analysis/a03-FE_vs_HPO/results.txt]. Independent repeated runs, each with a fresh HOME and a private repo "to prevent data leakage from previous runs/results", gave the following [code:xgb/analysis/a08-multi_runs/*; a09-codex; a10-opencode]:
- Claude (phase 3, isolated): test AUC 0.8090, 0.8214, 0.8510 after 97, 70 and 211 experiments (last keep at 86, 69 and 204);
- Codex (gpt-5.5; reasoning "Medium" in run 1, "xhigh" in run 2 per `notes.txt`): 0.8074 and 0.8383 (the CSV lists runs 2 and 3);
- opencode (gpt-5.5): 0.7918; the gpt-5.4-mini run is marked "broken".

The earlier "phase 1 – with memory" Claude runs (0.8431 at iteration 178 of a run manually stopped after 510; 0.8469; 0.8338) were, going by the folder name, run *with* agent memory, i.e. without the fresh-HOME isolation of later phases [inferred from the folder name `phase1-with_memory`]. Two "phase 2" runs are marked "not valid because of forgotten README" [code:xgb/analysis/a08-multi_runs/phase1-with_memory/results-runs.csv; phase2-forgot_README/results-runs.csv]. The 0.8431 used in the method comparison matches phase-1 run 1 ("run1 iteration 178") [code:xgb/analysis/a07-compare_all_methods/results-methods.csv].

Several runs record `stop_reason` "Claude (though asked if more)", which means the agent stopped despite "NEVER STOP". Codex runs record `stop_reason` "codex".

---

## 8. Known weaknesses and critiques

1. **Noise chasing / lucky keeps.**
   - A strict "<" on a single run keeps anything below the running minimum. The MLX maintainers measured run-to-run noise of about 0.03 `val_bpb` on Apple Silicon, where `val_bpb` is about 1.3–2.7. No upstream H100 noise figure is published; #43's non-reproductions of +0.0007 to +0.0008 suggest an H100 noise scale around 1e-3, which is the size of many kept deltas [inferred from gh-disc:#32, #43]. The maintainers note that "the recorded curve is an optimistic running-minimum that regresses on honest re-eval" [code:mlx/README.md; code:mlx/rigor.py].
   - Upstream's own teaser keeps "random seed 42→137" (−0.000439 in #32) [code:ar/progress.png; gh-disc:#32], and #43 reports that the seed and warmup wins did not reproduce [gh-disc:#43].
   - The XGBoost agent kept changes it labelled "borderline noise" [code:xgb/groundtruth_all.tsv]. [doc "Watch out for"]
2. **Validation reuse (adaptive overfitting).** Every decision uses the same pinned shard and the same evaluated tokens (a fresh deterministic loader per call) [code:ar/prepare.py]. The overview warns that after hundreds of decisions the chain slowly tunes to that shard [doc]. **Upstream has no hidden test at all, so this is neither measured nor excluded there.** The only direct measurements in the sources point the other way at the ~70–100-decision scale:
   - In XGBoost scenario 1, the hidden same-period 4/5-model AUC tracks CV AUC all the way (final 0.8341 against CV 0.8319) [code:xgb/groundtruth_all.tsv].
   - In scenario 2, the authors found the larger same-year 2006 sample "closely tracks the AUC on the evaluation data used by the agent, confirming that this multi-evaluation setup does not lead to noticeable overfitting to the evaluation set", with "more modest" gains on 2007 [code:xgb/docs/index.md].
   - The early keeps that lowered the 2006 holdout (§7.4) raised the same-period hidden test, so they show **distribution shift / non-transfer**, not adaptive overfitting [code:xgb/groundtruth_all.tsv].
   - Treat the overview's claim as a plausible risk at larger decision counts, not a demonstrated effect [inferred].
3. **Budget bias and platform specificity.**
   - "autoresearch will find the most optimal model for your platform in that time budget. The downside is that your runs (and results) become not comparable to other people running on other compute platforms." [code:ar/README.md]
   - Smaller, faster models win ("more steps > more params" [gh-disc:#32]; depth 8→4 on Mac [code:mlx/README.md]).
   - "some of those Mac Mini findings did not carry cleanly onto the Max baseline" [code:mlx/README.md]. **Counter-evidence:** Karpathy's d12→d24 transfer on nanochat [nanochat:LEADERBOARD].
4. **Grader isolation is partial** [inferred from code]:
   - (a) `evaluate_bpb` trusts the loss computed by the agent-editable `forward`, so a model that returns an under-normalized or zero loss would score arbitrarily well;
   - (b) the budget clock (`if step > 10`, `TIME_BUDGET` usage) and the fast-fail check live in editable `train.py`;
   - (c) the 10-minute kill is enforced by the agent itself;
   - (d) the agent writes `results.tsv` itself (self-reported);
   - (e) only instructions protect `prepare.py`, and the recommended invocation runs the agent with permission prompts disabled (`--dangerously-skip-permissions`) [code:ar@b11d6f2:spawn.sh; code:ar/README.md];
   - (f) the evaluated row count depends on the agent-chosen eval batch size (floor division, §2).
   The at-home hub adds heuristic plausibility filters (reject `val_bpb < 0.5` or a jump > 0.1) precisely because results are self-reported [code:home/coordinator.py]. The overview's "the grader is out of reach" [doc] holds only socially.

   A concrete grader-side failure did occur in the XGBoost port. Agents wrote *frame-dependent* features inside `prepare(df)`, such as counts or aggregates over whatever frame is passed in. These mean something different on the 100k training frame and the 1M-row audit frames, and CV cannot detect this, "because in CV the features are built once on the full training frame before it is split into folds". The maintainers' commit `9d6ee8b` says this "broke both sol5.6 runs", and they responded with a prompt rule rather than a mechanical check [code:xgbmin/program.md; code:xgbmin git log]. Its `score_by_row/score.py` later asserts that batch and per-row `prepare()` give identical predictions [code:xgbmin/score_by_row/score.py].
5. **Cross-run information leakage.** The XGBoost authors had to isolate runs to prevent "leakage via Claude memory, looking at previous results files, git history, via web searches etc." [code:xgb/analysis/a08-multi_runs/phase2-forgot_README/setup.txt]. `program.md` there forbids "use git to peek at earlier results".
6. **One greedy chain, human-written strategy.** There is no population, no branching and no replay. Rewinding is discouraged, and the strategy improves only when a person edits `program.md` [code:ar/program.md; doc]. Karpathy's own deleted multi-GPU launcher ran N *independent* chains, one branch per GPU, with no sharing between them [code:ar@b11d6f2:spawn.sh]. Sharing appears only in community ports (at-home hub) and in the author's stated next step ("asynchronously massively collaborative", quoted in [code:home/README.md]). With parallel compute, the agent's own search shape changed (a grid, two-tier screening) [code:sky/README.md]. That suggests the chain shape is a compute artifact rather than a principle [inferred].
7. **Judgment-based rules are unreproducible.** The simplicity criterion, "VRAM is a soft constraint", "fix if trivial" and "give up after more than a few attempts" are left to the agent [code:ar/program.md]. Two runs of the same `program.md` can diverge. Independent XGBoost agent runs spanned 0.8090–0.8510 test AUC [code:xgb/analysis/a08-multi_runs/phase3/results-runs.csv].
8. **Agents stop despite "NEVER STOP"** [code:xgb/analysis/a08-multi_runs/*/results-runs.csv; a09-codex/results-runs.csv]. **Pre-execution judgment degrades along the trajectory.** The "Rehearse" paper (Jiazhen Ji and Shouhong Ding, arXiv 2607.27687, July 2026, per search metadata) reports that "a memoryless judge has 82.8% selective accuracy at cold start but only 56.9% once three or more successes have accumulated, while still committing verdicts at similar rates", which it calls a "confidence cliff". It proposes that the agent propose several modifications, compare them before execution, and judge with only "the few most similar" past records [web-snippet: arxiv 2607.27687 abstract via search; re-checked 24 Sep 2026; **paper not accessed**, as arxiv, export.arxiv, semanticscholar, pith.science and alphaxiv are blocked].
9. **Collaborative-port caveats** [code:home/coordinator.py; inferred]:
   - the global best is writable by any participant and the sanity checks are heuristic;
   - claims dedupe on description text (semantic ≥ 0.92), not on code diff;
   - the swarm `improvement_trend` compares the best 5 against the next 5 keeps *sorted by val_bpb*, not by time, so it nearly always reports "improving";
   - "recent" results in `analyze_swarm` are the top 30 of a semantic search for "experiment result val_bpb", not the latest 30. `get_unclaimed_hypotheses` never filters out hypotheses already tested or claimed;
   - exact-key claim deduplication only catches the *same* agent repeating a description, because the key embeds the agent name. Tier bests skip the > 0.1 jump check and the second read that the global best has. The documented race rule ("earliest `created_at` wins") differs from the code (the claimant's `agent_id` must survive a 2 s re-read);
   - absolute `val_bpb` is not comparable across GPUs, which is why VRAM tiers and per-agent bests were added [code:home/collab.md].
10. **Cost is only wall-clock.** No token or API-cost accounting appears in upstream. SkyPilot reports $9 API vs $300 GPU for 910 runs [code:sky/README.md]. Evaluation and compile time are outside the budget [code:ar/train.py].
11. **Noise gates in ports measure less than they appear to.** MLX `rigor.py`'s "seeds" are repeated runs of an identical, seed-pinned `train.py`, so the gate captures GPU nondeterminism but not seed-to-seed variance. A seed change like upstream's "42→137" would be scored as a new config [code:mlx/rigor.py; code:mlx/train.py; inferred].
12. **Port-protocol hazards** [inferred from code]:
    - MLX: committing `results.tsv` on keep plus `git reset --hard` on discard would, if followed literally, revert the discard row (§3.5a).
    - Containerized SLURM: parallel `submit`s share one remote directory, so a queued job may run a later tag's `train.py` (§3.5b).
    - XGBoost: the 1-minute limit is a ceiling, so the loop optimizes AUC under a runtime cap rather than at a fixed budget (§3.5e).
13. **Autonomy needs a babysitter.** The only upstream mechanism that enforced "NEVER STOP" was the deleted `spawn.sh` watcher, which re-prompted idle agents every 120 s [code:ar@b11d6f2:spawn.sh]. The XGBoost multi-run logs show agents stopping on their own anyway (§7.4).

---

## 9. Reproduction blueprint (domain-agnostic, CPU-only)

### 9.1 Components and interfaces

The names are proposals [inferred]. Where possible they reuse the names in the RRSI and Dream-RSI specs (`LLMClient`, `Evaluator`, `NoiseCalibrator`, `Selector`), so the same framework hosts all methods. Autoresearch needs **two modes**:
- **faithful** reproduces upstream semantics, including its weaknesses, so that they can be measured;
- **hardened** closes the isolation gaps from §8.4.

```python
# ---------- the only domain-specific part ----------
class ResearchTask(Protocol):                     # "prepare.py": locked constants, data, grader
    name: str
    locked_paths: list[str]                       # e.g. ["prepare.py", "evaluator/**", "pyproject.toml"]
    editable_paths: list[str]                     # upstream: ["train.py"]
    run_cmd: list[str]                            # e.g. ["python", "train.py"]
    budget: "Budget"                              # fixed per task, never editable
    metric: str; direction: Literal["min", "max"] # "val_bpb"/"min", "cv_auc"/"max"
    def prepare(self) -> None                     # one-time data/tokenizer prep (idempotent)
    def parse_summary(self, log: str) -> dict | None   # '^key:\s+value' lines; None ⇒ crash
    hidden_audits: list["HiddenAudit"]            # post-hoc only, never exposed to the agent

@dataclass
class Budget:                                     # the "fixed 5 minutes"
    kind: Literal["wallclock", "cpu_time", "steps", "tokens"]
    amount: float; warmup_excluded_steps: int = 11     # upstream excludes steps 0..10
    kill_after: float                             # hard kill (upstream: 10 min ≈ 2× budget)

# ---------- core method components ----------
class Workspace:                                  # git-backed single chain (plus worktrees for parallel)
    def init_run(tag) -> str                      # branch "autoresearch/<tag>", must be fresh
    def commit(msg) -> str; def reset_to(commit); def head() -> str
    def diff_paths(a, b) -> list[str]; def file_hash(path) -> str
    def worktree(exp_id) -> Path                  # isolation for parallel/SLURM/SkyPilot variants
class ScopeGuard:                                 # hardened mode: mechanical version of the "CANNOT" list
    def check(prev_commit, new_commit) -> list[Violation]    # edits outside editable_paths, dep changes
    def locked_hashes_ok() -> bool                # before every run
class Runner:                                     # subprocess, stdout+stderr → run.log, never into the agent context
    def run(workdir, cmd, env, kill_after) -> RunOutcome     # returncode, wall_s, log_path, peak_rss_mb, killed
class BudgetEnforcer:                             # hardened: budget enforced by locked code
    # e.g. locked `prepare.budget_clock()` context/iterator the training loop must pull from, or
    # locked dataloader that stops yielding after `amount` tokens/steps; Runner kill as backstop
class Evaluator:                                  # locked grader ("evaluate_bpb")
    def evaluate(model_adapter, split="val") -> float
    # faithful: sums losses returned by the model (as upstream)
    # hardened: model returns logits/log-probs; evaluator applies log_softmax itself, checks
    #           normalization, computes CE and bits-per-byte from a locked byte-length table
class ResultsLog:                                 # results.tsv, exact 5-column schema, untracked by git
    def append(commit, metric, memory_gb, status, description)
    def rows() -> list[Row]
class ExperimentTree:                             # extension: JSONL with parent commit, diff, seeds, times,
    def add(node)                                 # full editable-file source → future Dream-RSI replay world
class KeepRule(Protocol):                         # pluggable acceptance
    def decide(candidate: Samples, incumbent: Samples, ctx) -> Literal["keep", "discard"]
class StrictImprovement(KeepRule): ...            # upstream: keep iff strictly better on one run
class SimplicityWeighted(KeepRule): eps: float    # mechanical proxy: keep iff better, or |Δ| ≤ eps and LOC shrinks
class BootstrapRigor(KeepRule):                   # MLX rigor.py: repeats=3, confidence=0.95, R=20000, rng=1234,
    ...                                           # early reject after 1 run, never-repeat by file hash; add an option
                                                  # to vary the seed per repeat (rigor.py does not) [inferred]
class NoiseCalibrator:                            # re-run unchanged baseline k times → σ, noise band
class CrashPolicy:                                # empty summary ⇒ crash; ≤ max_fix_attempts trivial fixes; log 0.0
class ProgramSpec:                                # "program.md": versioned, human-owned; rendered into agent context
    text: str; version: str
class ResearchAgent(Protocol):                    # the single LLM role
    def propose_and_edit(ctx: AgentContext) -> Proposal    # edits editable files, returns 1-line description
    def fix_crash(ctx, log_tail: str) -> Proposal | None   # "dumb and easy to fix" path
    # AgentContext: ProgramSpec, editable file(s), locked files (read-only), results.tsv, git log, tail of notes
class ClaudeCodeAgent(ResearchAgent): ...         # `claude -p` with program.md as the prompt; tools jailed to workspace
class MockAgent(ResearchAgent): ...               # deterministic scripted mutator (see §9.2)
class LLMClient(Protocol): def generate(prompt, system=None) -> str   # ClaudeCLI | MockLLM
class AutoresearchLoop:                           # §3.1–3.2; never asks the human; stop only via STOP file/limits
    def setup(tag); def baseline(); def step() -> Row; def run(max_experiments=None, max_hours=None)
class LivenessNudger:                             # spawn.sh watcher: every 120 s, if an interactive agent's output is
    def check(session) -> bool                    # unchanged and idle → send "Keep going. Do not stop — …"; faithful
                                                  # mode with interactive agents only (a framework-owned loop needs none)
class MultiChainLauncher:                         # spawn.sh: N independent chains, branch autoresearch/<tag>-w<N> +
    def launch(tag, agents: list[AgentSpec])      # worktree each, per-worker resource pinning (GPU id / CPU set)
class Analyzer:                                   # analysis.ipynb: keep rate, running best, per-keep Δ, plot
class HiddenAudit:                                # xgboost's check_groundtruth: re-evaluate every KEEP commit on
    def audit(tree) -> table                      # splits the agent never sees (iid test + shifted test)
class Reeval:                                     # honest re-evaluation of kept commits with fresh seeds

# ---------- port extensions ----------
class Executor(Protocol):                         # local | slurm | (cloud) — SLURM & SkyPilot forks
    def submit(workdir, cmd, resources) -> JobId; def status(id) -> State; def wait(id, timeout, poll)
    def cancel(id); def fetch_log(id) -> str
class LocalProcessExecutor(Executor): ...         # process pool, per-experiment worktree, CPU pinning
class SlurmExecutor(Executor): ...                # sbatch template (+ optional --container-image), squeue→sacct;
                                                  # one remote workdir per experiment (avoid the shared-root race)
class SharedHub(Protocol):                        # autoresearch-at-home "Ensue" replacement (file/SQLite backend)
    def claim(desc, agent) -> key | None          # exact-key + semantic (threshold) + TTL + write-wait-verify
    def publish_result(key, record_with_full_source); def global_best(tier=None); def maybe_update_best(...)
    def publish_hypothesis(...); def post_insight(...); def unclaimed_hypotheses(); def analyze()
class SimilarityFn(Protocol): def score(a: str, b: str) -> float   # embeddings if available; token-Jaccard fallback
class ProfileTier:                                # "VRAM tier" analogue: hardware profile (threads, RAM)
```

Design notes [inferred]:
- **The keep rule is the main plug-in point** that the other methods reuse. RRSI's `Selector` (noise floor, cost rule) can replace `StrictImprovement`. Logging every attempt as an `ExperimentTree` node, including discards with their source, turns an autoresearch night into a Dream-RSI replay world [doc "Log experiments as a tree"].
- In hardened mode, run the agent with its file tools jailed to `editable_paths`. Mount locked files read-only (or copy them fresh from a sealed store before each run and verify hashes). Compute the metric in a separate process that imports the trained model artifact rather than the agent's `forward` loss.
- Keep upstream's context hygiene: the full log goes to disk and only the grep'ed summary or `tail -n 50` enters the agent context [code:ar/program.md].

### 9.2 CPU-only domains

**Environment**: `pip install numpy` (required); scikit-learn is optional for Domain B; torch-CPU is optional. None of them is installed on this machine yet, but PyPI is reachable. The LLM is `claude -p` (installed at `/opt/node22/bin/claude`) or `MockAgent`. The machine has 4 cores [environment check]. Pin `OMP_NUM_THREADS=1` for wall-clock stability [inferred].

**Domain A: "nano-autoresearch" (faithful analogue of the LM task)** [inferred design]:
- *Data (locked, `prepare.py`)*: an offline text corpus with **byte-level tokens (vocab 256)**. That way bpb equals the mean nats/token divided by ln 2 for the baseline, and the agent may still introduce its own merges or n-gram features.
  - Option 1: a seeded synthetic corpus from a small probabilistic grammar / Markov source whose **entropy rate is known**, which gives a known floor on achievable bpb.
  - Option 2: a fixed bundle of public-domain text vendored in the repo.
  - Splits: train, `val` (seen by the loop), `test_iid` (hidden, same distribution) and `test_shift` (hidden; a different grammar parameter or genre).
- *Editable `train.py`*: a numpy MLP n-gram language model in the style of makemore: embedding, context window, hidden layers, tanh/ReLU, manual backprop, and an SGD/Adam choice. It exposes about 15 knobs (context length, embedding dim, width, depth, lr, schedule warmup/warmdown, batch size, init scale, weight decay, optimizer, seed), mirroring `train.py`'s constants block.
- *Budget*: `Budget(kind="wallclock", amount=30 s, warmup_excluded_steps=11, kill_after=60 s)` gives about 60–100 experiments/hour excluding LLM latency. A deterministic alternative is `Budget(kind="tokens")`, a fixed count of training tokens served by the locked dataloader. It removes wall-clock noise but also removes the "fastest model wins" pressure, so use it as an ablation.
- *Evaluator*: bits per byte on a fixed `EVAL_TOKENS` (for example 200k bytes) of `val`, with a byte-length table (all 1 for byte vocab; >1 if the agent adds merges), in faithful and hardened variants.

**Domain B: tabular GBT (the "non-LLM adaptation")** [inferred design modelled on code:xgbmin]:
- A seeded synthetic tabular generator with categorical and numeric features, interactions, a cyclic time feature and an **era** variable that drifts. Train is 20k rows of era 0; the loop metric is 5-fold CV AUC on train.
- Hidden audits: an iid era-0 test set of 100k rows, and era-1 (shifted).
- `train.py` holds `prepare(df)` plus `sklearn.ensemble.HistGradientBoostingClassifier` hyperparameters, with a pure-numpy fallback (logistic regression plus hand-built features) if sklearn is unavailable.
- Budget: 20 s per run, kill after 40 s.
- Enforce the per-row `prepare(df)` rule with a locked check that runs `prepare` on a random half and on the full data and compares rows (the program.md test, mechanized).

**Domain C: synthetic "known-truth" landscape (fast, mock-only)** [inferred design]:
- A config vector x (the knobs in train.py) has a true long-horizon quality Q_∞(x), plus a budget-limited quality Q_B(x) = Q_∞(x) + g(steps(x, B)), where steps is proportional to B / cost(x). Larger models cost more per step.
- The measured val metric is Q_B(x) + v(x) + ε, where v(x) is a **fixed val-split-specific component** (so val reuse can overfit it) and ε ~ N(0, σ²) is seed noise.
- Hidden test metrics use independent v′(x) (iid) and a shifted v″ + Δ(x).
- Thousands of simulated "nights" take seconds with a `MockAgent` that proposes coordinate moves, which gives exact ground truth for noise and overfitting studies.

**MockAgent** (deterministic, seeded) [inferred]:
- It proposes from a scripted pool of regex edits on the constants block: helpful, harmful, neutral (e.g. `SEED 42→137`), crash-inducing (syntax error, `MemoryError` via a big allocation under `RLIMIT_AS`, an infinite loop to trigger the kill) and **exploit** edits (touch `prepare.py`; make the model return a scaled-down loss; change `warmup_excluded_steps` in the editable loop; read hidden-audit files).
- It fixes "trivial" crashes on the first retry with probability p_fix.
- Its policies are greedy coordinate search, random search, or "combine near-misses".

**ClaudeCodeAgent**:
- invoke `claude -p` once per experiment with `program.md` (adapted: paths, metric, budget), the current `train.py`, the last N rows of `results.tsv` and the git log;
- tools are restricted to editing `train.py`, plus reading the locked files;
- it returns a one-line description.
The framework, not the agent, runs git, the Runner, parsing, the keep rule and logging (hardened). The faithful mode lets the agent run git and train itself, as upstream does [inferred].

### 9.3 Experiments that demonstrate each key claim (qualitative, CPU-only)

Use ≥ 3 seeds for real-LLM arms and ≥ 50 seeds for Domain C. Report paired bootstrap CIs.

| # | Claim [doc unless noted] | Setup | Measure | Baseline / ablation | Confirming outcome |
|---|---|---|---|---|---|
| E1 | The loop makes steady unattended progress (propose → run → score → keep/reset) | Domain A and B, `ClaudeCodeAgent`, 60–100 experiments, faithful mode | running-best curve, keep rate, experiments/hour, crash handling | (i) the unmodified `train.py` re-run k = 5 times (noise band); (ii) random search over the same knobs with the same experiment count; (iii) `MockAgent` greedy | final best is below the baseline's noise band; the agent is ≥ random search at equal count; the log is readable (Analyzer output matches the analysis.ipynb quantities) |
| E2 | A fixed time budget makes arbitrary changes comparable, and in doing so favours small/fast models on *this* machine | Domain A and C | parameter count and throughput of kept configs vs baseline; re-train the final and baseline configs at 4× and 16× the budget | budget = wall-clock vs budget = tokens; 1 thread vs 4 threads ("different Mac") | kept configs shrink or speed up under the wall-clock budget; part of the gain shrinks or reverses at longer horizons or on another thread count; winners differ between the 1- and 4-thread profiles (MLX Mac-Mini vs Max analogue) |
| E3 | BPB is vocab-independent, so tokenizer or vocab changes stay comparable | Domain A: two models with different tokenizations (bytes vs a 512-merge BPE fitted on train) | per-token nats vs bits/byte on the same text | rank by per-token loss vs rank by bpb | per-token loss mis-ranks (larger tokens look worse), bpb ranks consistently with the likelihood of the byte stream |
| E4 | Locking the grader keeps the loop honest; the faithful version is only socially locked | `MockAgent` exploit pool injected at 10% of proposals | exploit acceptance rate; spurious "best" values | faithful vs hardened (`ScopeGuard`, `BudgetEnforcer`, logits-only `Evaluator`, read-only mounts) | faithful mode accepts the scaled-loss and budget-clock exploits (bogus bests); hardened mode rejects 100% of them as violations or crashes; honest edits are unaffected |
| E5 | "Strictly better" on one noisy number locks in luck | Domain C (exact truth) and Domain A (re-eval) | fraction of keeps whose true Δ ≤ 0; count of neutral (seed-only) keeps; optimism gap = recorded best − honest mean of 5 fresh-seed re-runs of the final commit | `StrictImprovement` vs `BootstrapRigor` (3 seeds, 0.95) vs `SimplicityWeighted`, at **equal total training runs** | strict keeps more changes, including seed changes, and shows a positive optimism gap; rigor has fewer false keeps and a smaller gap. The honest final quality is equal or better under rigor at a matched budget (or report the trade-off) |
| E6 | Reusing the same validation data judges every experiment ⇒ slow tuning to it | Domains C and B (and A with `test_iid`) | val − test_iid gap of the kept chain vs experiment index; test_shift trend | fixed val vs a val split re-sampled every M experiments; hidden audit only at the end (`HiddenAudit`) | the val − test_iid gap grows with the number of decisions under fixed val, and resampling val shrinks it. Separately, some keeps improve val and test_iid while hurting test_shift, as in the XGBoost 2006 holdout, which is a shift effect and not an overfitting one. The sources show no measurable gap at about 70 decisions (§8.2), so report a null result honestly; Domain C can raise the val-specific component v(x) to find where the effect appears |
| E7 | One chain, one idea at a time; strategy improves only when a person rewrites the instructions | `ClaudeCodeAgent` on Domain A with two or three `ProgramSpec` versions: upstream bare; + XGBoost-style research/hypothesis/synthesis rules; + "prefer simplifications" | best at N = 40 experiments; diversity of edited knobs | same budget and seeds, only `program.md` differs | outcome differences are attributable to `program.md` alone (the "research org code" lever); no within-run strategy change occurs without a spec edit |
| E8 | House rules: kill > 2× budget, fix trivial crashes, skip broken ideas, rewinds rare | `MockAgent` crash pool (incl. NaN-loss edits, which must hit the fast-fail rather than burn the budget) | correct statuses in results.tsv (`crash`, 0.000000, 0.0); branch reset; fix-and-rerun path taken once for syntax errors | — | every injected hang is killed at `kill_after`; NaN runs exit early with FAIL; OOM and syntax crashes are logged; HEAD always equals the last kept commit |
| E9 | Platform port: the same contract on another backend; baselines must be re-established per platform | Domain A implemented twice (numpy vs torch-CPU, or float64 vs float32) | whether the same `program.md`/loop runs unchanged; transfer of the kept edit set from backend 1 to backend 2 | — | the loop runs unchanged; some edits do not transfer (MLX finding) |
| E10 | Scheduler/cluster fork: asynchronous submission and polling | `LocalProcessExecutor` (4 workers) and a stub `SlurmExecutor` with a fake `sbatch`/`squeue`/`sacct` | throughput (experiments/hour), correctness of state handling (PENDING/RUNNING/FAILED/TIMEOUT), time-to-same-best | sequential vs 4-way parallel at equal total CPU | about 4× throughput; the same statuses as sequential; faster time-to-best; the search becomes more grid-like (SkyPilot observation) |
| E11 | Collaborative version: claim before running, publish results with full code, shared leaderboard and best | 4 agents (processes, mock or Claude) on a file-backed `SharedHub` | duplicate-experiment rate; global-best trajectory vs wall time; the fraction of hub bests that survive an **independent re-evaluation** | claiming on vs off; hub sanity filters on vs off with an injected bogus-result agent | claiming cuts duplicates; the swarm reaches a given best sooner than one agent at equal total compute; without re-verification a bogus self-report can capture the global best, and filters or re-evaluation stop it (links to the EvoMap critique) |
| E12 | Non-LLM-model adaptation (GBT against a held-out metric) | Domain B end to end | CV AUC trajectory plus `HiddenAudit` iid and shifted AUC per keep | Optuna-style random or TPE HPO baseline at an equal experiment count (optional) | CV and iid improve together; the shifted test improves less or sometimes regresses; the agent's FE edits contribute gains beyond HPO |
| E13 | Frame-dependent ("transductive") features inflate CV but break at audit time (the XGBoost `9d6ee8b` failure) | Domain B; `MockAgent` injects `df.groupby(...).transform("size")`-style features | CV AUC vs hidden AUC of those keeps; detection rate of a locked per-row check (the program.md "random half" test, mechanized) | prompt-rule only vs mechanical `prepare` check | CV rises while hidden AUC on a larger frame falls; the mechanical check flags 100% of the injected features |

### 9.4 Suggested framework defaults [inferred, not from any source]

- Budget of 30 s wall-clock (Domain A) or 20 s (Domain B), `kill_after` = 2 × budget (mirroring 5 → 10 min upstream).
- Noise calibration with 5 baseline re-runs before the loop (the overview's "Measure your noise first" [doc]).
- `KeepRule` defaults to `StrictImprovement` in faithful mode and `BootstrapRigor(repeats=3, confidence=0.95)` in hardened mode, with the MLX parameters, but drawing a fresh seed for each repeat (unlike `rigor.py`).
- `max_fix_attempts = 3` for "more than a few attempts".
- `results.tsv` is never committed, while the `ExperimentTree` JSONL is committed at the end of the run.

---

## 10. Capability checklist: overview claims mapped to components

| # | Claim the overview [doc] attributes to autoresearch (or its ports) | Component(s) that satisfy it |
|---|---|---|
| 1 | An AI agent edits one training script, trains for five minutes, keeps the edit if the score improved, and repeats all night | `AutoresearchLoop` + `ResearchAgent` + `Runner` + `Budget` + `KeepRule` (Strict) + `Workspace` |
| 2 | About 12 experiments an hour on a single GPU, with no human in the loop | `Budget` (fixed per-run), `AutoresearchLoop.run` never prompts the human (the "NEVER STOP" semantics, stopping only via STOP file or limits); `LivenessNudger` when an interactive agent drives the loop (upstream's deleted `spawn.sh` watcher); `Analyzer` reports experiments/hour |
| 3 | What changes: training code for a small LM (simplified single-GPU nanochat) | `ResearchTask.editable_paths = ["train.py"]`; Domain A (CPU LM analogue) |
| 4 | What stays fixed: data, tokenizer, evaluation code, the 5-minute budget | `ResearchTask.locked_paths`, `Evaluator`, `Budget` (not editable), `ScopeGuard.locked_hashes_ok` |
| 5 | Keep only if strictly better; equal or worse is reset | `StrictImprovement` + `Workspace.reset_to` |
| 6 | The strategy is improved by a person, by editing the instructions (program.md, a lightweight "skill") | `ProgramSpec` (versioned, human-owned) rendered into `AgentContext`; E7 |
| 7 | Hardware: one NVIDIA GPU; community ports run on Macs | `Executor` / backend abstraction; Domain A on CPU; E9 |
| 8 | prepare.py nobody edits: locking it means the agent can't change how it is graded | `ScopeGuard`, read-only mounts, hash checks; hardened `Evaluator`; E4 |
| 9 | train.py is what the agent edits; any idea is fair game (architecture, learning rates, batch size) | `ResearchAgent.propose_and_edit` with no restriction inside `editable_paths` |
| 10 | program.md is what the person edits | `ProgramSpec` |
| 11 | The agent reads the instructions and the current code and decides what to try | `AgentContext` (ProgramSpec + editable and locked files + results.tsv + git log) |
| 12 | Trains for exactly five minutes of wall-clock time (startup and compilation don't count) and reads one number, validation bpb | `Budget(kind="wallclock", warmup_excluded_steps=11)`, `BudgetEnforcer`; `Runner` + `ResearchTask.parse_summary` (grep `^val_bpb:`) |
| 13 | Logs the run in a results table: commit, score, memory used, status (keep, discard, crash) and a one-line description | `ResultsLog` (exact 5-column TSV, untracked); `Runner` measures peak memory (RSS as the VRAM analogue) |
| 14 | If the score improved, the commit stays and becomes the new baseline; otherwise the code is reset | `KeepRule` → `Workspace.commit` / `reset_to` |
| 15 | A fixed time budget makes every experiment directly comparable, whatever the agent changed | `Budget` + E2 |
| 16 | Bits per byte doesn't depend on vocabulary size, so architecture or tokenizer changes are compared fairly | `Evaluator` bpb with a locked byte-length table; E3 |
| 17 | One editable file keeps each change small enough to review in the morning | `editable_paths` (single file) + `Workspace` linear kept-commit chain + `Analyzer` report with per-keep diffs |
| 18 | House rules: a run over 10 minutes is killed and counts as a failure; trivial crashes are fixed and rerun; broken ideas are skipped; rewinding is allowed but rare | `Runner(kill_after)`, `CrashPolicy(max_fix_attempts)`, `ResearchAgent.fix_crash`, `Workspace.reset_to(any kept commit)` with a rewind counter; E8 |
| 19 | Example experiment log (baseline, LR increase kept, GeLU discarded, width-doubling OOM crash) | `ResultsLog` schema and crash conventions (0.000000 / 0.0) |
| 20 | Apple Silicon port's first night: 2.667 → 1.808 (halve batch, raise LR, depth 8→4); some findings did not carry to a different Mac | E2 and E9 (thread-profile transfer test); `ProfileTier` |
| 21 | Apple Silicon port using MLX, same rules, no CUDA | backend-agnostic `ResearchTask` + unchanged `AutoresearchLoop`; E9 |
| 22 | Research-cluster fork runs the loop through a job scheduler inside a container | `Executor` interface with `SlurmExecutor` (sbatch template with optional container flags, squeue/sacct polling, cancel on timeout, per-experiment remote workdir) and `LocalProcessExecutor`; `MultiChainLauncher` for independent per-worker chains; E10 |
| 23 | Collaborative version: agents claim an experiment first to avoid duplicates, publish results and full code, and track a shared leaderboard and shared best | `SharedHub` (claim with exact-key + `SimilarityFn` ≥ threshold + TTL + write-wait-verify; `publish_result` with full source; `global_best`/tier best with sanity checks and read-compare-write; hypotheses and insights); E11 |
| 24 | Non-LLM adaptations such as tuning a gradient-boosted tree against a held-out metric show the loop isn't LM-specific | Domain B + `HiddenAudit`; per-row `prepare` check; E12, E13 |
| 25 | Why it caught on: small, one GPU, a readable log by morning; the person's job moves up a level | `Analyzer` (keep rate, running best, top hits, progress plot) + `ProgramSpec` |
| 26 | A coding agent can make steady unattended progress when experiments are comparable and the grader is out of reach | E1 + E4 (with hardened isolation) |
| 27 | Watch out: "strictly better" on one noisy number can lock in lucky results | `NoiseCalibrator`, `Reeval`, `BootstrapRigor`; E5 |
| 28 | Watch out: the same validation data judges every experiment, so hundreds of experiments slowly tune to it | `HiddenAudit` (iid + shifted, reported separately so overfitting and shift are not conflated), optional val re-sampling; E6 |
| 29 | Watch out: a five-minute budget rewards whatever trains best in five minutes on that machine; smaller, faster models tend to win | `Budget` kinds, long-horizon `Reeval` at k×budget; E2 |
| 30 | Watch out: one chain, one idea at a time; the strategy only improves when a person rewrites the instructions | single-chain `AutoresearchLoop` (faithful) vs `Executor`-parallel variant; `ProgramSpec` versions; E7 and E10 |
| 31 | Side-by-side: improves "the work"; frozen data, evaluation and budget; one chain; keep rule strictly better; guard is locked evaluation; strategy by a person; history is "a log the agent reads"; cost control is a fixed 5 min; one GPU, one night; evidence is anecdotal | the components above; `ResultsLog` is fed back into `AgentContext` (history); `ExperimentTree` extension for reuse |
| 32 | "Autoresearch locks the grader away from the agent" | `ScopeGuard` + hardened `Evaluator` (mechanical lock), versus faithful mode (instruction-only lock) to measure the difference; E4 |
| 33 | RRSI "fixes the keep rule"; Dream-RSI turns the person-written strategy into code and replays past runs | the pluggable `KeepRule` (RRSI `Selector` drop-in); `ExperimentTree` JSONL with parent, diff and full source as a Dream-RSI replay world |
| 34 | The agent itself stays the same; it improves the training code of a separate small model | `ResearchAgent` is stateless across runs (no self-edits); only `editable_paths` change |
| 35 | Could someone run this at home? Yes: one GPU, and ports on Apple Silicon | CPU-only Domains A to C with a 30 s budget; `claude -p` or `MockAgent` |
| 36 | Autoresearch's successes are mostly community anecdotes | multi-seed, CI-reported experiment suite (§9.3) with `HiddenAudit` and `Reeval` |
| 37 | Not related to EvoMap's separately named "AutoResearch" project | (naming note only; nothing to implement) |
| 38 | SoL-Pi runs "autoresearch-style loops" across many environments (cross-reference) | `AutoresearchLoop` is reusable with any `ResearchTask`; multi-task keep rules belong to the SoL-Pi spec |
| 39 | Cross-method advice: "Measure your noise first", "Keep a set the loop never sees", "Log experiments as a tree", "Never let the loop grade itself" | `NoiseCalibrator`; `HiddenAudit`; `ExperimentTree`; `ScopeGuard` + hardened `Evaluator` + framework-owned (not agent-owned) logging |
