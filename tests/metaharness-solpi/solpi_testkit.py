"""Shared helpers for the SoL-Pi tests: a scripted runtime and a tiny "verbosity" FunctionDomain."""
import json
import random

from rsi.core import Artifact, Execution, FunctionDomain, Task, TaskSuite
from rsi.solpi import PRICES, AgentRuntime, Idea, Message, TokenMeter, ToolCall, builtin_tools
from rsi.solpi.reducer import REDUCER_RECEIPT_SCHEMA
from rsi.solpi.runtime import ToolResult


class Script:
    """Backend that replays tool-call batches, then finishes."""

    name = "script"

    def __init__(self, steps, pad_tokens: int = 0):
        self.steps, self.pad_tokens, self.seen = list(steps), pad_tokens, []

    def act(self, msgs, tools, rt):
        self.seen.append(msgs)
        if not self.steps:
            return Message("assistant", "done")
        calls = tuple(ToolCall(rt.next_call_id(), n, a) for n, a in self.steps.pop(0))
        return Message("assistant", "step", tool_calls=calls, pad_tokens=self.pad_tokens)


class Env:
    """In-memory files; ``outputs`` maps a bash command to ``(output, is_error)``."""

    def __init__(self, files=None, outputs=None):
        self.files = dict(files or {})
        self.outputs = outputs or {}

    def read_file(self, p):
        return self.files.get(p)

    def tool_read(self, p, rt=None):
        return ToolResult(self.files[p]) if p in self.files else ToolResult("ENOENT", True)

    def tool_write(self, p, c, rt=None):
        self.files[p] = c
        return ToolResult(f"wrote {p}")

    def tool_edit(self, p, old, new, rt=None):
        t = self.files.get(p)
        if t is None or t.count(old) != 1:
            return ToolResult("Could not find the exact text", True)
        self.files[p] = t.replace(old, new)
        return ToolResult(f"edited {p}")

    def tool_bash(self, cmd, rt=None):
        return ToolResult(*self.outputs.get(cmd, (f"ran {cmd}", False)))


def runtime(env=None, steps=(), exts=(), *, prices: str = "sim-a", system_prompt: str = "sys", pad_tokens: int = 0,
            **kw) -> AgentRuntime:
    """Runtime with the built-in tools and ``exts``, driven by a :class:`Script` of ``steps``."""
    env = Env() if env is None else env
    price = PRICES[prices]
    meter = TokenMeter({"main": price, "reducer": PRICES["reducer"], "compaction": price})
    rt = AgentRuntime(Script(steps, pad_tokens), system_prompt=system_prompt, meter=meter, env=env, **kw)
    for spec in builtin_tools(env):
        rt.register_tool(spec)
    for ext in exts:
        rt.add_extension(ext)
    return rt


def bash_steps(*commands: str) -> list:
    return [[("bash", {"command": c})] for c in commands]


def tool_messages(rt: AgentRuntime) -> list[Message]:
    return [m for m in rt.history if m.role == "tool"]


def failure_receipt(source_sha256: str, quote: str) -> dict:
    return {"schema": REDUCER_RECEIPT_SCHEMA, "source_sha256": source_sha256, "status": "failure", "uncertain": False,
            "evidence": [{"kind": "failure", "quote": quote}]}


def verbosity_domain() -> FunctionDomain:
    """Tokens grow with ``verbosity``; the ``fast`` flag fails the held-out family ``gamma``."""
    rng = random.Random(1)
    tasks, splits = [], {"evolve": [], "holdout": [], "ood": []}
    for fam, split, n in (("alpha", "evolve", 6), ("beta", "evolve", 6), ("gamma", "holdout", 4), ("gamma", "ood", 4)):
        for i in range(n):
            t = Task(f"{split}-{fam}-{i}", {"need": rng.randint(1, 3)}, None, fam)
            tasks.append(t)
            splits[split].append(t.id)
    runs: list[tuple[str, str]] = []

    def execute(artifact, task, seed, llm):
        cfg = json.loads(artifact["config.json"])
        runs.append((task.id, artifact.id))
        v = cfg["verbosity"]
        tokens = 200 * v + 500 - (400 if cfg.get("fast") else 0)
        ok = v >= task.input["need"] and not (cfg.get("fast") and task.family == "gamma")
        return Execution(output=ok, tokens=tokens, cost_usd=tokens * 1e-6, steps=v + 1)

    dom = FunctionDomain(TaskSuite(tasks, splits), execute, lambda t, o: 1.0 if o else 0.0, name="verbosity",
                         description="config.json sets verbosity (tokens) and an optional 'fast' flag")
    dom.runs = runs
    return dom


VERBOSITY_IDEAS = [Idea("G1", "C", "Trim one verbosity level", "trim1"),
                   Idea("D1", "P", "Answer with minimal verbosity", "trim_all", kind="do_less"),
                   Idea("T1", "T", "Fast path that assumes the familiar output format", "fast", kind="trick")]
VERBOSITY_SEED = Artifact({"config.json": json.dumps({"verbosity": 4}, sort_keys=True)})
