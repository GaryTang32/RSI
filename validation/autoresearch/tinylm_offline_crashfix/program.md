# autoresearch

This is an experiment to have the LLM do its own research.

## Experimentation

Each experiment runs with a **fixed budget** ({{budget}}), regardless of what you change.
The framework launches it as: `{{run_cmd}}`.

**What you CAN do:**
- Modify {{editable}} - this is the only thing you edit. Everything is fair game: model architecture,
  optimizer, hyperparameters, training loop, batch size, model size, features, etc.

**What you CANNOT do:**
- Modify {{locked}}. It is read-only. It contains the fixed evaluation, data loading and the budget.
- Install new packages or add dependencies. You can only use what is already imported/available.
- Modify the evaluation harness. The locked evaluation is the ground truth metric.

**The goal is simple: get the {{goal}} {{metric}}.** Since the budget is fixed, you don't need to worry about
training time - it is always the same. Everything is fair game. The only constraint is that the code runs
without crashing and finishes within the budget.

**Memory** is a soft constraint. Some increase is acceptable for meaningful {{metric}} gains, but it should not
blow up dramatically.

**Simplicity criterion**: All else being equal, simpler is better. A small improvement that adds ugly complexity
is not worth it. Conversely, removing something and getting equal or better results is a great outcome - that's a
simplification win. A tiny improvement that adds 20 lines of hacky code? Probably not worth it. A tiny improvement
from deleting code? Definitely keep. An improvement of ~0 but much simpler code? Keep.

{{contract}}
## Output format

The script prints a summary block of `key: value` lines; the framework reads `{{metric}}:` from it.

## The experiment loop

The framework runs the loop and has already established the baseline. Each turn you propose ONE experiment:
1. Look at results.tsv (commit, {{metric}}, memory_gb, status, description) and the kept-commit log.
2. Tune {{editable}} with an experimental idea by directly hacking the code.
3. The framework commits it, runs it, records the row in results.tsv, and keeps the commit if {{metric}}
   improved ({{direction}}); if it is equal or worse the branch is reset to where you started.

**Crashes**: if a run crashes you will be shown `tail -n 50 run.log`. If it's something dumb and easy to fix
(e.g. a typo, a missing import), fix it. If the idea itself is fundamentally broken, give up on it.

**NEVER STOP**: do NOT ask the human anything and do not stop to check in. The human might be asleep. If you run
out of ideas, think harder - re-read the in-scope files for new angles, try combining previous near-misses, try
more radical changes. The loop runs until the human interrupts it, period.
