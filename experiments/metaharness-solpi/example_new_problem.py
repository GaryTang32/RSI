"""Applying Meta-Harness and SoL-Pi to a NEW problem (template; runs offline in seconds).

    python experiments/metaharness-solpi/example_new_problem.py
"""
from __future__ import annotations

import json
import random
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rsi.core import Artifact, Execution, FunctionDomain, Task, TaskSuite  # noqa: E402
from rsi.metaharness import CandidateSpec, Config, ProposalBatch, Proposer, run  # noqa: E402
from rsi.solpi import (PRICES, ActionFusion, AgentRuntime, EvidencePreservingReducer, Message,  # noqa: E402
                       ObservationPack, OnlineContextCompact, TokenMeter, ToolCall, builtin_tools)
from rsi.solpi.runtime import ToolResult  # noqa: E402

# Part 1 - Meta-Harness on your own Domain: a date-normalisation harness (``normalize.py`` defines
# ``normalize(text) -> 'YYYY-MM-DD'``) graded by exact match. With a real LLM pass ``llm_propose=`` instead.
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def make_tasks() -> TaskSuite:
    rng = random.Random(0)
    tasks, splits = [], {"evolve": [], "test": []}
    for i in range(60):
        y, m, d = rng.randint(1990, 2030), rng.randint(1, 12), rng.randint(1, 28)
        fmt = rng.choice(["iso", "slash", "text", "dots"])
        s = {"iso": f"{y}-{m:02d}-{d:02d}", "slash": f"{m}/{d}/{y}", "text": f"{d} {MONTHS[m - 1].title()} {y}",
             "dots": f"{d}.{m}.{y}"}[fmt]
        t = Task(f"t{i}", s, f"{y}-{m:02d}-{d:02d}", fmt)
        tasks.append(t)
        splits["evolve" if i < 40 else "test"].append(t.id)
    return TaskSuite(tasks, splits)


def execute(artifact, task, seed, llm) -> Execution:
    ns: dict = {}
    exec(artifact["normalize.py"], ns)
    out = ns["normalize"](task.input)
    return Execution(output=out, trace=f"input={task.input!r} output={out!r}", tokens=len(artifact["normalize.py"]))


def grade(task, output) -> tuple[float, str]:
    return (1.0, "ok") if output == task.target else (0.0, f"got {output!r} expected {task.target!r}")


SEED = '''RULES = [
]   # (regex, order) where order maps groups -> (y, m, d)


def normalize(text):
    import re
    for rx, order in RULES:
        m = re.fullmatch(rx, text.strip())
        if m:
            g = m.groups()
            y, mo, d = (g[i] for i in order)
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    return text.strip()
'''

KNOWN = {  # what a careful reader of the traces would write for each surface pattern
    "slash": (r"(\d{1,2})/(\d{1,2})/(\d{4})", (2, 0, 1)),
    "dots": (r"(\d{1,2})\.(\d{1,2})\.(\d{4})", (2, 1, 0)),
    "iso": (r"(\d{4})-(\d{2})-(\d{2})", (0, 1, 2)),
}


class TraceRuleProposer(Proposer):
    """Toy offline proposer: adds one parsing rule per surface pattern seen in the best candidate's failures."""

    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        best = max((p for p in view if p.endswith("scores.json")), key=lambda p: json.loads(view[p])["score"])
        name = best.split("/")[1]
        src = view[f"candidates/{name}/src/normalize.py"]
        bad = [r for p, t in view.items() if p.startswith(f"candidates/{name}/eval/search/per_task/")
               for r in json.loads(t) if r["score"] < 1]
        out = []
        for kind, (rx, order) in KNOWN.items():
            if repr(rx) in src or len(out) >= k:
                continue
            if any(re.fullmatch(rx, str(r["output"]).strip()) for r in bad):
                new = src.replace("RULES = [", f"RULES = [({rx!r}, {order}), ", 1)
                out.append(CandidateSpec(f"i{iteration}_{kind}", Artifact({"normalize.py": new}),
                                         f"add a {kind} rule", "exploitation", ["rule"], name))
        return ProposalBatch(candidates=out, files_read=sorted(view), transcript=f"{len(bad)} failures read")


def part1() -> None:
    dom = FunctionDomain(make_tasks(), execute, grade, name="dates", description="date normalisation harness")
    res = run(dom, Artifact({"normalize.py": SEED}, meta={"name": "seed"}), proposer=TraceRuleProposer(),
              config=Config(iterations=3, k=2, objectives=("score",), cost_metric="tokens",
                            validate_in_subprocess=False), out_dir=tempfile.mkdtemp(prefix="mh_new_"))
    print("[meta-harness] best:", res.meta["best_system"], "search:", round(res.meta["frontier"]["_best"]["score"], 3),
          "test:", {k: round(v["score"], 3) for k, v in res.meta["final"]["splits"]["test"]["results"].items()})


# Part 2 - SoL-Pi's four opt-in extensions and the prefix-cache token meter in your own long-running agent.
class MyEnv:
    """Your environment: files + a shell whose test command prints a long log."""

    def __init__(self):
        self.files = {"app.py": "LIMIT = 3\n", "notes.md": "background " * 3000}

    def read_file(self, p):
        return self.files.get(p)

    def tool_read(self, p, rt=None):
        return ToolResult(self.files.get(p, "missing"), p not in self.files)

    def tool_write(self, p, c, rt=None):
        self.files[p] = c
        return ToolResult("written")

    def tool_edit(self, p, o, n, rt=None):
        self.files[p] = self.files[p].replace(o, n)
        return ToolResult("edited")

    def tool_bash(self, cmd, rt=None):
        ok = "LIMIT = 5" in self.files["app.py"]
        log = "\n".join(f"test_{i} PASSED" for i in range(400))
        return ToolResult(log + ("\n1 passed" if ok else "\nFAILED test_limit - AssertionError: LIMIT must be 5"),
                          not ok)


class MyAgent:
    """Your backend: anything with act(messages, tools, runtime) -> assistant Message."""

    name = "my-agent"

    def __init__(self):
        self.step = 0

    def act(self, msgs, tools, rt):
        self.step += 1
        fused = "then_run" in next(t for t in tools if t.name == "edit").parameters
        plan = [
            [("read", {"path": "notes.md"})],
            [("bash", {"command": "pytest"})],
            [("edit", {"path": "app.py", "old": "LIMIT = 3", "new": "LIMIT = 5",
                       **({"then_run": {"command": "pytest"}} if fused else {})})],
            [] if fused else [("bash", {"command": "pytest"})],
        ]
        calls = plan[self.step - 1] if self.step <= len(plan) else []
        return Message("assistant", "working", tool_calls=tuple(ToolCall(rt.next_call_id(), n, a) for n, a in calls))


def part2() -> None:
    for label, exts in (("base", []), ("sol-pi", [ActionFusion(), ObservationPack(), EvidencePreservingReducer(),
                                                   OnlineContextCompact()])):
        env = MyEnv()
        rt = AgentRuntime(MyAgent(), system_prompt="You fix bugs.", env=env,
                          meter=TokenMeter({"main": PRICES["sim-a"], "reducer": PRICES["reducer"],
                                            "compaction": PRICES["sim-a"]}))
        for s in builtin_tools(env):
            rt.register_tool(s)
        for e in exts:
            rt.add_extension(e)
        rt.run("Make the tests pass.")
        tot = rt.meter.total()
        print(f"[sol-pi] {label:7s} requests={rt.provider_requests} tokens={tot.total_tokens} cost=${tot.cost:.4f} "
              f"fixed={'LIMIT = 5' in env.files['app.py']}")


if __name__ == "__main__":
    part1()
    part2()
