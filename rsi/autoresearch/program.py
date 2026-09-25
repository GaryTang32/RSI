"""``program.md``: the research strategy, owned and edited by a person.

Upstream calls it a "super lightweight skill" and the "research org code": the
agent reads it every experiment, the loop never edits it, and a person improves
the strategy by editing it. :class:`ProgramSpec` keeps that contract:

* ``text`` is rendered for a task (metric, budget, editable/locked files, the
  interface contract) and placed in the agent's context;
* every version is content-addressed (``version``) and remembers its parent, so
  each ledger node records which strategy produced it;
* only :meth:`ProgramSpec.edit` (a human action) creates a new version; a loop
  given ``program_path`` re-reads the file between experiments, so a person can
  change the strategy while the loop runs.

Presets: ``upstream`` (karpathy/autoresearch program.md adapted to a
framework-run loop), ``xgboost`` (+ hypothesis/classification/no-near-duplicate
rules of the xgboost port) and ``simplify`` (prefer simplifications).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

UPSTREAM = """\
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
"""

XGBOOST_RULES = """
## Research discipline (xgboost-autoresearch rules)
- Before each experiment state a short **hypothesis** and classify the experiment as one of:
  *follow-up* (builds on a recent keep), *ablation/simplification*, or *exploration*.
- **Do not** run near-duplicate experiments; check results.tsv first.
- Every 10 experiments write a two-line synthesis of what has worked (in your description, not a file).
- On a plateau (3 or more consecutive discards with tiny movement), switch to a different family of ideas.
- Everything is fair game that will lead to a model that generalizes on unseen data.
"""

SIMPLIFY_RULES = """
## Preference
Prefer simplifications and removals. Try deleting or shrinking things before adding new ones.
"""

PRESETS = {"upstream": UPSTREAM, "xgboost": UPSTREAM + XGBOOST_RULES, "simplify": UPSTREAM + SIMPLIFY_RULES}


@dataclass(frozen=True)
class ProgramSpec:
    """A versioned, human-owned research strategy."""

    text: str
    label: str = "upstream"
    parent: Optional[str] = None
    author: str = "human"

    @property
    def version(self) -> str:
        return hashlib.sha1(self.text.encode()).hexdigest()[:7]

    @classmethod
    def preset(cls, name: str = "upstream") -> "ProgramSpec":
        if name not in PRESETS:
            raise ValueError(f"unknown program preset {name!r}; choose from {sorted(PRESETS)}")
        return cls(PRESETS[name], label=name)

    @classmethod
    def load(cls, spec: str) -> "ProgramSpec":
        """A preset name or a path to a program.md file."""
        if spec in PRESETS:
            return cls.preset(spec)
        p = Path(spec)
        return cls(p.read_text(), label=p.name)

    def edit(self, new_text: str, *, author: str = "human", label: Optional[str] = None) -> "ProgramSpec":
        """A person rewrites the strategy (the only way the strategy changes)."""
        return ProgramSpec(new_text, label=label or self.label, parent=self.version, author=author)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.text)
        return p

    def render(self, task, mode: str = "hardened") -> str:
        """Fill the task-specific placeholders."""
        b = task.budget
        if b.kind == "wallclock":
            budget = f"{b.amount:g} seconds of training wall-clock time; the first {b.warmup_excluded_steps} steps are not counted"
        elif b.kind == "tokens":
            budget = f"{b.amount:g} training bytes/tokens served by the locked dataloader"
        elif b.kind == "ceiling":
            budget = f"a {b.amount:g} second ceiling on the whole run; slower runs are discarded"
        elif b.kind == "rollouts":
            budget = f"a fixed evaluation of {b.amount:g} rollouts"
        else:
            budget = "a fixed budget"
        if b.kind in ("wallclock", "ceiling", "tokens"):
            budget += f"; runs longer than {b.hard_timeout():g} s are killed and count as failures"
        editable = ", ".join(f"`{p}`" for p in task.editable_paths)
        locked = ", ".join(f"`{p}`" for p in task.locked_paths) or "the grader (it lives outside the artifact)"
        contract = (task.contract(mode) or "").strip()
        if mode != "faithful" and getattr(task, "tamper_patterns", ()):
            contract += ("\n- Do not reach into the grader: no environment variables starting with `RSI_AR_`, no "
                         "private names of the locked files, no assignments to their attributes, no hidden data. "
                         "Edits whose new lines do any of this are rejected without running.")
        if contract.strip():
            contract = "## Interface contract\n" + contract.strip() + "\n"
        repl = {
            "budget": budget, "run_cmd": getattr(task, "run_command", "") or "the task's run command",
            "editable": editable, "locked": locked, "metric": task.metric,
            "goal": "lowest" if task.direction == "min" else "highest",
            "direction": "lower is better" if task.direction == "min" else "higher is better",
            "contract": contract,
        }
        out = self.text
        for k, v in repl.items():
            out = out.replace("{{" + k + "}}", v)
        return out
