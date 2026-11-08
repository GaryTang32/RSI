# Examples

Three short scripts, each a complete run of the workflow in the
[`rsi-improve` skill](../.claude/skills/rsi-improve/SKILL.md): define what may change, the tasks and a locked grader; seal
a held-out split; measure the noise; run one method with a trace; read the result on tasks the loop never saw.

| Example | Improves | Method | Offline run |
|---|---|---|---|
| [`01_prompt_from_csv`](01_prompt_from_csv/improve_prompt.py) | a prompt, on your own labelled CSV | GEPA | ~1 s |
| [`02_agent_harness`](02_agent_harness/improve_harness.py) | an agent harness (`harness.py` + prompts) | RRSI | ~5 s |
| [`03_training_script`](03_training_script/improve_training_script.py) | a training script (`train.py`) | autoresearch | ~25 s |

Each one runs offline by default: no API key, no network, nothing written outside its run directory (a fresh temp dir
unless you pass `--out`). Run them from the repo root or from anywhere else; a script uses the installed `rsi` package
(`pip install -e .`) and falls back to the clone it sits in.

```bash
python examples/01_prompt_from_csv/improve_prompt.py
python examples/02_agent_harness/improve_harness.py
python examples/03_training_script/improve_training_script.py
```

**Offline numbers are not results.** Offline, the task model is a simulator and the proposer is a script, so the scores
show the loop working (proposals, keep decisions, sealed splits, audit trail), not real gains. Every offline run
ends by saying so.

**Run live** by adding `--llm claude:haiku` (the `claude` CLI; uses your Claude plan) or `--llm api:haiku`
(`ANTHROPIC_API_KEY` and `pip install -e ".[api]"`; billed per token). The same model is then both the task model and
the proposer. Responses are cached under `.rsi_cache/example-0N` in the current directory, so a rerun replays at $0;
pass `--out runs/<name>` to keep the run. For `claude:*`, `export RSI_EXIT_ON_USAGE_LIMIT=1` makes a plan usage
limit stop the run instead of scoring empty replies. The costs below are rough guides from the number of calls;
the run prints its own spend at the end.

## 01: a prompt for your own labelled examples

[`improve_prompt.py`](01_prompt_from_csv/improve_prompt.py) routes the 94 support tickets in
[`tickets.csv`](01_prompt_from_csv/tickets.csv) to billing / technical / account / shipping, starting from the
one-line [`prompt.md`](01_prompt_from_csv/prompt.md). The labels follow three company rules that a model cannot guess
from the ticket: refunds for damaged, missing or lost items go to shipping (not billing), login and two-factor errors
go to account (not technical), and cancelling or pausing a subscription goes to account (not billing). The seed prompt
does not state them, so it misroutes those tickets and there is something real to learn.

- **Domain:** a `FunctionDomain` from two functions: `execute` (prompt + ticket -> model -> label) and `grade` (exact
  match; on a miss the feedback is the label's line from `LABEL_GUIDE`, the annotation guide, which GEPA's
  reflection reads).
- **Splits:** 36 evolve (the loop learns from these), 19 val (GEPA's Pareto/selection set), 39 holdout (sealed: the
  script shows the loop's evaluator refusing it; only `transfer_report` reads it, after the run).
- **Noise:** 3 trials of the seed prompt on evolve give the noise band `delta`; GEPA then keeps a child prompt only if
  it beats its parent by more than `delta` (`acceptance="noise_margin"`), not on any strict gain.
- **Headroom check:** if the seed already scores within `max(delta, 1/n)` of the top score (n = evolve tasks), no
  kept change could be told apart from noise, so the script prints `STOP no headroom` and exits before the loop
  spends anything. Make the tasks harder or use a weaker model; `--force` runs the loop anyway. (An earlier version
  of this example used 30 easy tickets that Claude Haiku already got 100% right, and still spent the full budget.)
- **Offline:** no existing simulator fits a custom CSV, so the script defines a small keyword classifier
  (`offline_classifier`) that follows only the rules written in the prompt and answers at random 20% of the time.
  The proposer is GEPA's `GenericReflectionLM`, which appends a failing example's feedback as a `Lesson:` line.
- **Your data:** `--csv my.csv` with columns `text,label`, then edit `prompt.md` and `LABEL_GUIDE` to your labels.
- **Live cost:** about 700 task calls (noise measurement, 300 GEPA rollouts, the report) and a few reflection calls.
  One run with `--llm claude:haiku` (8 November 2026) cost $1.52 and took about 50 minutes, since GEPA runs rollouts
  one at a time. The seed scored 0.917 on evolve (delta 0.038), so the headroom check passed. GEPA's best prompt
  wrote out all three house rules, and the sealed holdout went from 0.910 to 1.000 (paired 95% CI +0.013 to
  +0.179, 39 tickets). This is one run: an earlier three-seed trial on the same tickets with subtype-level feedback
  gave holdout gains of +0.04 to +0.09, with CIs that included 0.

## 02: an agent harness

[`improve_harness.py`](02_agent_harness/improve_harness.py) runs RRSI on the built-in AgentQA domain: short questions
with exact answers, a seed harness that makes one model call, and sealed `holdout` (same kind) and `ood` (unseen kinds)
splits.

- **Offline:** `SimModel` answers like a model whose accuracy depends on what the harness does (answer format line,
  Python tool, ...), and `AgentQAMockLLM` is RRSI's scripted proposer and critic: it proposes useful, costly, null and
  leaky edits, so you can watch the gate reject some.
- **Noise:** RRSI measures `delta` on the seed itself before round 1.
- **Output:** the kept edits (from `history.jsonl`; a kept candidate can bundle a useless edit with useful ones), seed
  vs final on evolve / holdout / ood with paired 95% intervals and tokens per task, and `TRACE.md`, the per-round
  audit trail (analysis, proposal, actual diff, critic verdict, per-task scores, gate arithmetic, decision). Re-render
  it with `python -m rsi.cli inspect <run dir>`.
- **Options:** `--rounds T` (default 6).
- **Live cost:** about 900 model calls and a million tokens for 6 rounds plus the report (the offline run's own
  count), so roughly $1-3 with Haiku; the README's live AgentQA RRSI run with Haiku cost $2.76.

## 03: a training script

[`improve_training_script.py`](03_training_script/improve_training_script.py) runs autoresearch on the built-in tinylm
task: `train.py` (editable) trains a tiny numpy byte-level language model on CPU; the locked `prepare.py` owns the data
and the metric, `val_bpb` (lower is better).

- **Budget:** a fixed number of training bytes per run (`--budget`, default 30,000), so runs are comparable across
  machines. The corpus is built under the run directory in about 2 s (`--data` to reuse one).
- **Keep rule:** not upstream's "strictly better on one run". The unchanged script is re-run 3 times with fresh seeds
  to measure `delta`, and `MinGain` keeps an experiment only if it beats the tip by more than `delta`. The table marks
  experiments that beat the tip but stayed within the noise: the upstream rule would have kept those.
- **Offline:** `MockResearchAgent` replays tinylm's scripted edit pool (knob changes, crashes, grader exploits). The
  ideas are scripted; the training runs are real.
- **Output:** the experiment table (`results.tsv`), each kept commit's score on two hidden splits the agent never saw
  (`test_iid`, `test_shift`), and the best `train.py`.
- **Live cost:** one proposer call per experiment (plus crash fixes): cents for the default 8. Training runs locally.
  The README's live tinylm run cost $0.70.

## Next steps

- Wrap your own problem: [SKILL.md](../.claude/skills/rsi-improve/SKILL.md) steps 1-7, or `/rsi-improve` in Claude Code.
- Choose a method: `rsi recommend --help`, or the method table in SKILL.md step 4.
- Templates for other methods: `experiments/{gepa,autoresearch,dream-rsi,metaharness-solpi}/example_new_problem.py`.
- Concepts (noise bands, sealed splits, keep rules): [RSI 101](../docs/guide/RSI-101.md).
