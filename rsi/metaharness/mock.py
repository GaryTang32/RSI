"""Deterministic offline proposer for Meta-Harness (spec A9.1 ``MockProposer``).

It "draws from a library of candidate programs; its choice rule may use only
what ``store_view`` exposes". The choice rule is a caricature of the skill's
workflow, and each step is gated by what the view contains:

1. parse every visible candidate: its program (from its *source code*), its
   search score and context cost (``scores.json``), its trace summary and its raw
   traces - whichever of these the history mode exposes;
2. pick parents from the visible scores (best, then a low-cost frontier member);
3. choose a *mechanism move* per candidate:
   * raw traces visible -> :meth:`Library.diagnose` reads per-example records and
     proposes the move that addresses the observed failure (overflow, missing
     labels, recurring confusions, heavy context ...);
   * only a summary visible -> :meth:`Library.diagnose_summary` (lossy);
   * only scores -> an untried random move (still avoiding exact repeats and
     moves whose visible history regressed), or a crossover of the two best;
4. optionally (``leak_rate``) hard-code answers read from traces - the
   overfitting that Meta-Harness's main loop does not guard against.

Offline results with this proposer validate the *machinery* (views really hide
information; frontier, finalisation, budgets); they are not evidence about how
an LLM proposer uses traces. Use ``--llm claude:haiku`` for that.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from ..core.artifact import Artifact
from .proposer import CandidateSpec, ProposalBatch, Proposer
from .store import safe_name


class Library(Protocol):
    main_file: str
    moves: dict[str, tuple[dict, str, str]]

    def parse(self, files: dict[str, str], name: str) -> Optional[dict]: ...
    def render(self, genome: dict, base: Optional[Artifact] = None) -> Artifact: ...
    def apply(self, genome: dict, move: str) -> dict: ...
    def crossover(self, a: dict, b: dict) -> dict: ...
    def diagnose(self, traces: dict[str, str], per_task: dict[str, str]) -> list[tuple[str, float, str]]: ...
    def summarize(self, traces: dict[str, str]) -> str: ...
    def diagnose_summary(self, summary: str) -> list[tuple[str, float, str]]: ...
    def leak(self, genome: dict, traces: dict[str, str], per_task: dict[str, str]) -> Optional[dict]: ...
    def signature(self, genome: dict) -> str: ...


# ------------------------------------------------------------------ MemoClassify
class MemoClassifyLibrary:
    """Program library for :mod:`rsi.domains.memoclassify` (``memory.py`` programs)."""

    main_file = "memory.py"

    def __init__(self) -> None:
        from ..domains.memoclassify import programs
        self.P = programs
        self.moves = programs.ALL_MOVES       # mechanism moves + parameter variants (uninformed proposals)

    def parse(self, files, name):
        src = files.get(self.main_file)
        if src is None:
            return None
        g = self.P.parse(src)
        if g is None:
            g = self.P.seed_genome("no_memory" if "NoMemory" in src or "no learning" in src else "fewshot_all")
        return g

    def render(self, genome, base=None):
        return Artifact({self.main_file: self.P.render(genome)})

    def apply(self, genome, move):
        return self.P.apply_move(genome, move)

    def crossover(self, a, b):
        return self.P.crossover(a, b)

    def diagnose(self, traces, per_task):
        return self.P.diagnose(traces)

    def summarize(self, traces):
        return self.P.summarize(traces)

    def diagnose_summary(self, summary):
        return self.P.diagnose_summary(summary)

    def leak(self, genome, traces, per_task):
        g = self.P.leaky_genome(genome, traces)
        return g if g.get("lookup") else None

    def signature(self, genome):
        return json.dumps({k: v for k, v in genome.items() if k != "lookup"}, sort_keys=True) + \
            ("+lookup" if genome.get("lookup") else "")


# ------------------------------------------------------------------ AgentQA
AGENTQA_DEFAULT = {"tool": False, "format": False, "cot": False, "fallback": False, "lookup": {}}
AGENTQA_MOVES: dict[str, tuple[dict, str, str]] = {
    "format": ({"format": True}, "A", "Asking for an explicit 'ANSWER: <value>' line and returning only it removes "
                                      "answer-extraction failures."),
    "tool": ({"tool": True}, "C", "Having the model write a short Python program and running it replaces error-prone "
                                  "mental arithmetic and long reasoning with exact computation."),
    "cot": ({"cot": True}, "A", "Step-by-step reasoning improves direct answers."),
    "fallback": ({"fallback": True}, "C", "When the program fails or prints nothing, fall back to a direct answer "
                                          "instead of returning an empty string."),
}

_AGENTQA_TEMPLATE = '''"""Harness: {doc}"""
import re

CONFIG = {config}


def _extract(reply):
    m = re.findall(r"ANSWER\\s*[:=]\\s*(.+)", reply or "", flags=re.I)
    if m:
        return m[-1].strip()
    lines = [l for l in (reply or "").strip().splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def solve(question, llm, tools, files):
    system = files.get("prompts/system.md", "")
    for key, ans in CONFIG["lookup"].items():
        if key in question:
            return "ANSWER: " + ans
    if CONFIG["tool"]:
        reply = llm("Write a Python program that prints only the final answer to this question.\\n\\n"
                    + question + "\\n\\nReply with the code in a ```python block.", system=system)
        code = re.findall(r"```python\\n(.*?)```", reply, re.S)
        if code:
            out = tools.python(code[-1]).strip()
            if out and "Traceback" not in out:
                return "ANSWER: " + out.splitlines()[-1].strip()
        if not CONFIG["fallback"]:
            return "ANSWER: "
    prompt = files.get("prompts/task.md", "{{question}}").replace("{{question}}", question)
    if CONFIG["cot"]:
        prompt += "\\n\\nThink step by step."
    if CONFIG["format"]:
        prompt += "\\n\\nEnd your reply with a line of the form 'ANSWER: <value>'."
    reply = llm(prompt, system=system)
    if CONFIG["format"]:
        return "ANSWER: " + _extract(reply)
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""
'''


class AgentQALibrary:
    """Program library for :mod:`rsi.domains.agentqa` (``harness.py`` + prompts)."""

    main_file = "harness.py"

    def __init__(self) -> None:
        self.moves = AGENTQA_MOVES

    def parse(self, files, name):
        src = files.get(self.main_file)
        if src is None:
            return None
        from ..domains.memoclassify.programs import read_config
        g = read_config(src)
        return {**AGENTQA_DEFAULT, **g} if g is not None else dict(AGENTQA_DEFAULT)

    def render(self, genome, base=None):
        g = {**AGENTQA_DEFAULT, **genome}
        doc = ", ".join(k for k in ("tool", "format", "cot", "fallback") if g[k]) or "direct answer"
        src = _AGENTQA_TEMPLATE.format(doc=doc, config=repr(g))
        files = dict(base.files) if base is not None else {"prompts/system.md": "You are a helpful assistant.\n",
                                                             "prompts/task.md": "{question}\n"}
        files[self.main_file] = src
        return Artifact(files)

    def apply(self, genome, move):
        return {**genome, **self.moves[move][0]}

    def crossover(self, a, b):
        return {k: (a.get(k) or b.get(k)) if isinstance(a.get(k), bool) else a.get(k) for k in AGENTQA_DEFAULT}

    def diagnose(self, traces, per_task):
        fb = " ".join(per_task.values())
        text = " ".join(traces.values())
        n_bad = fb.count("Incorrect")
        out = []
        if n_bad and "ANSWER:" not in text:
            out.append(("format", 1.0, f"{n_bad} incorrect; replies never contain an ANSWER line"))
        if n_bad and "[python]" not in text:
            out.append(("tool", 1.2, "no tool calls in traces; wrong answers to computational questions"))
        if "[python]" in text and ("Traceback" in text or "ANSWER: '" in fb or "answer ''" in fb):
            out.append(("fallback", 0.8, "tool runs fail or print nothing"))
        return sorted(out, key=lambda x: -x[1])

    def summarize(self, traces):
        text = " ".join(traces.values())
        return (f"{len(traces)} tasks. The harness made {text.count('[llm call')} model calls and "
                f"{text.count('[python]')} tool calls. Answers are often wrong.")

    def diagnose_summary(self, summary):
        m = re.search(r"made \d+ model calls and (\d+) tool calls", summary or "")
        return [("tool", 0.6, "summary: no tool calls")] if m and m.group(1) == "0" else []

    def leak(self, genome, traces, per_task):
        table = {}
        for unit, fb in per_task.items():
            m = re.search(r"expected '([^']+)'", fb)
            t = traces.get(unit, "")
            q = re.search(r"PROMPT: (.{40})", t)
            if m and q:
                table[q.group(1)] = m.group(1)
        return {**genome, "lookup": table} if table else None

    def signature(self, genome):
        return json.dumps({k: genome.get(k) for k in ("tool", "format", "cot", "fallback")}, sort_keys=True) + \
            ("+lookup" if genome.get("lookup") else "")


def library_for(domain) -> Library:
    name = getattr(domain, "name", "")
    if name == "memoclassify":
        return MemoClassifyLibrary()
    if name == "agentqa":
        return AgentQALibrary()
    raise ValueError(f"no MockProposer library for domain {name!r}; pass proposer=... or llm_propose=...")


# ------------------------------------------------------------------ the proposer
@dataclass
class _Seen:
    name: str
    genome: dict
    score: Optional[float]
    cost: Optional[float]
    traces: dict
    per_task: dict
    summary: Optional[str]
    meta: dict


class MockProposer(Proposer):
    """Deterministic proposer; see module docstring. ``leak_rate`` = probability per
    iteration of hard-coding answers read from traces (only possible when traces or
    per-task feedback are visible)."""

    def __init__(self, library: Library, *, leak_rate: float = 0.0, explore_frac: float = 0.5,
                 seed: int = 0) -> None:
        self.lib = library
        self.leak_rate = leak_rate
        self.explore_frac = explore_frac
        self.seed = seed

    # ---- reading the view
    def _read(self, view: dict[str, str]) -> tuple[list[_Seen], list[str]]:
        read: list[str] = []
        groups: dict[str, dict[str, dict[str, str]]] = {}
        for path, text in view.items():
            m = re.match(r"^candidates/([^/]+)/(.+)$", path)
            if m:
                groups.setdefault(m.group(1), {})[m.group(2)] = text
        seen = []
        for name, files in groups.items():
            src = {p[4:]: t for p, t in files.items() if p.startswith("src/")}
            genome = self.lib.parse(src, name)
            if genome is None:
                continue
            read.extend(f"candidates/{name}/{p}" for p in files if p.startswith("src/"))
            sc = files.get("eval/search/scores.json")
            score = cost = None
            if sc:
                d = json.loads(sc)
                score, cost = d.get("score"), d.get("context_cost")
                read.append(f"candidates/{name}/eval/search/scores.json")
            meta = json.loads(files["meta.json"]) if "meta.json" in files else {}
            traces = {p.split("/")[-1][:-6]: t for p, t in files.items() if p.startswith("eval/search/traces/")}
            per_task = {}
            for p, t in files.items():
                if p.startswith("eval/search/per_task/"):
                    try:
                        per_task[p.split("/")[-1][:-5]] = " ".join(r.get("feedback", "") for r in json.loads(t))
                    except (ValueError, AttributeError):
                        pass
            seen.append(_Seen(name, genome, score, cost, traces, per_task, files.get("eval/search/summary.md"),
                              meta))
        return seen, read

    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        rng = random.Random(f"mock-proposer|{self.seed}|{seed}|{iteration}")
        seen, read = self._read(view)
        batch = ProposalBatch(meta={"view_chars": sum(len(t) for t in view.values())})
        if not seen:
            batch.error = "no readable candidate in the view"
            return batch
        scored = sorted([s for s in seen if s.score is not None], key=lambda s: (-s.score, s.cost or 0, s.name))
        pool = scored or seen
        self._front = []
        if "frontier_val.json" in view:
            try:
                fr = json.loads(view["frontier_val.json"])
                names = {s.name for s in seen}
                self._front = [p["system"] for p in fr.get("_pareto", []) if p.get("system") in names]
                read.append("frontier_val.json")
            except (ValueError, TypeError):
                self._front = []
        tried = {self.lib.signature(s.genome) for s in seen}
        regressed = self._regressed_moves(seen)
        out: list[CandidateSpec] = []
        leak_done = False
        for slot in range(k):
            parent = self._parent(pool, slot, rng)
            if parent is None:
                break
            genome, move, evidence, parents = None, None, "", [parent.name]
            if self.leak_rate > 0 and not leak_done and rng.random() < self.leak_rate and \
                    (parent.traces or parent.per_task):
                g = self.lib.leak(parent.genome, parent.traces, parent.per_task)
                if g is not None:
                    genome, move, evidence = g, "lookup", "hard-coded answers read from search traces"
                    leak_done = True
            if genome is None and parent.traces:
                read.extend(f"candidates/{parent.name}/eval/search/traces/{u}.jsonl" for u in parent.traces)
                genome, move, evidence = self._informed(parent, self.lib.diagnose(parent.traces, parent.per_task),
                                                        tried, regressed)
            if genome is None and parent.summary:
                read.append(f"candidates/{parent.name}/eval/search/summary.md")
                genome, move, evidence = self._informed(parent, self.lib.diagnose_summary(parent.summary), tried,
                                                        regressed)
            if genome is None and slot % 2 == 1 and len(scored) >= 2:
                other = scored[1] if scored[0].name == parent.name else scored[0]
                g = self.lib.crossover(parent.genome, other.genome)
                if self.lib.signature(g) not in tried:
                    genome, move, evidence, parents = g, "crossover", f"combine {parent.name} + {other.name}", \
                        [parent.name, other.name]
            if genome is None:
                genome, move, evidence = self._random(parent, tried, regressed, rng)
            tried.add(self.lib.signature(genome))
            axis = self.lib.moves.get(move, ({}, "combo", ""))[1] if move in self.lib.moves else \
                ("leak" if move == "lookup" else "combo")
            hyp = self.lib.moves[move][2] if move in self.lib.moves else evidence
            name = safe_name(f"i{iteration:02d}_{slot}_{move}")
            base = artifacts.get(parent.name)
            out.append(CandidateSpec(name, self.lib.render(genome, base), hypothesis=hyp,
                                     axis="exploitation" if slot == 0 else "exploration",
                                     components=[move, f"axis:{axis}"], base_system=parent.name,
                                     parents_read=parents, meta={"move": move, "evidence": evidence}))
        batch.candidates = out
        batch.files_read = sorted(set(read))
        batch.transcript = "\n".join(f"{c.name}: {c.meta['move']} on {c.base_system} ({c.meta['evidence']})"
                                     for c in out)
        return batch

    def _parent(self, pool: list[_Seen], slot: int, rng: random.Random) -> Optional[_Seen]:
        if not pool:
            return None
        if slot == 0 or len(pool) == 1:
            return pool[0]
        front = [s for s in pool if s.name in getattr(self, "_front", [])]
        if slot % 2 == 1 and len(front) > 1 and rng.random() < self.explore_frac:
            return front[rng.randrange(1, len(front))]            # a cheaper frontier member
        with_cost = [s for s in pool[:6] if s.cost is not None]
        if slot % 2 == 1 and not front and with_cost and rng.random() < self.explore_frac:
            return min(with_cost, key=lambda s: (s.cost, -(s.score or 0)))
        return pool[min(slot, len(pool) - 1)] if rng.random() < 0.5 else pool[0]

    def _regressed_moves(self, seen: list[_Seen]) -> set[str]:
        """Moves whose visible application lowered the score of their base system."""
        by_name = {s.name: s for s in seen}
        bad = set()
        for s in seen:
            mv = (s.meta.get("move") or (s.meta.get("components") or [None])[0])
            base = by_name.get(s.meta.get("base_system", ""))
            if mv and base and s.score is not None and base.score is not None and s.score < base.score:
                bad.add(mv)
        return bad

    def _informed(self, parent: _Seen, suggestions, tried, regressed):
        for move, weight, evidence in sorted(suggestions, key=lambda x: -x[1] * (0.3 if x[0] in regressed else 1)):
            if move not in self.lib.moves:
                continue
            g = self.lib.apply(parent.genome, move)
            if self.lib.signature(g) not in tried:
                return g, move, evidence
        return None, None, ""

    def _random(self, parent: _Seen, tried, regressed, rng):
        moves = sorted(self.lib.moves)
        rng.shuffle(moves)
        moves.sort(key=lambda m: m in regressed)
        for m in moves:
            g = self.lib.apply(parent.genome, m)
            if self.lib.signature(g) not in tried:
                return g, m, "untried mechanism"
        for _ in range(20):
            a, b = rng.sample(sorted(self.lib.moves), 2)
            g = self.lib.apply(self.lib.apply(parent.genome, a), b)
            if self.lib.signature(g) not in tried:
                return g, f"{a}+{b}", "combination of mechanisms"
        m = rng.choice(sorted(self.lib.moves))
        return self.lib.apply(parent.genome, m), m, "repeat"
