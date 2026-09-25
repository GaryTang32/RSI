"""Katas: small Python coding tasks with hidden unit-test graders (EvoMap spec §9.3 Tier 2).

The artifact is a solver harness (``prompts/system.md`` + ``prompts/task.md``).
At solve time every file under ``genes/`` or ``skills/`` (the strategy cards an
agent injected) is appended to the system prompt, each wrapped in a
``--- guidance file: <path> ---`` delimiter. The model writes the function; the
**hidden** asserts (never shown to agents; kept inside the domain object) grade
it in a sandboxed subprocess: score 1 iff all hidden asserts pass.

Workspace hooks for validation: W0 = ``solution.py`` stub + ``smoke_test.py``
(the public asserts), W1 = the model's solution + the same smoke test, so the
gene validation ``python smoke_test.py`` is discriminative (fails on the stub)
though weak (public tests do not exercise the pitfalls).

Offline stand-ins: :class:`KataSimSolver` (chooses the correct or the pitfall
implementation with a probability driven by the guidance it reads - hint
phrases, AVOID warnings, contradictory advice, dilution inside long skill
documents, composition and length penalties; all *knobs*) and
:class:`KataMockProposer` (writes a gene card after a failure).
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import threading
from typing import Optional, Sequence

from rsi.core import Artifact, Domain, Execution, MockLLM, Task, TaskSuite
from rsi.core.llm import estimate_tokens, extract_code
from rsi.core.sandbox import run_python

from .katas import CLASS_KEYWORDS, CLASSES, KATA_BY_ID, KATAS, Kata

SYSTEM = ("You are an expert Python programmer. Write one correct, self-contained function that satisfies the "
          "specification exactly, including edge cases.\n")
TASK = ("Implement this function:\n\n{signature}\n    \"\"\"{description}\"\"\"\n\nExamples that must hold:\n"
        "{public_tests}\n\nReturn only the complete function (with any imports it needs) in one ```python block.\n")
GUIDANCE_HEADER = "\n# Strategy guidance from your team's shared library\n"


def seed_harness() -> Artifact:
    return Artifact({"prompts/system.md": SYSTEM, "prompts/task.md": TASK})


def make_suite(scheme: str = "default") -> TaskSuite:
    """``default``: evolve = katas 0-2 of each class, holdout = 3-4.
    ``hub``: evolve = 0-1, val = 2, test (hub bank) = 3-4."""
    tasks, splits = [], {}
    by_cls = {c: [k for k in KATAS if k.cls == c] for c in CLASSES}
    layout = {"default": {"evolve": (0, 3), "holdout": (3, 5)},
              "hub": {"evolve": (0, 2), "val": (2, 3), "test": (3, 5)}}[scheme]
    for k in KATAS:
        tasks.append(Task(k.id, {"fn": k.fn, "signature": k.signature, "description": k.description,
                                 "public_tests": list(k.public)}, None, k.cls,
                          {"signals": [*CLASS_KEYWORDS[k.cls], *k.keywords], "public": list(k.public),
                           "signature": k.signature}))
    for split, (a, b) in layout.items():
        splits[split] = [k.id for c in CLASSES for k in by_cls[c][a:b]]
    splits["smoke"] = splits["evolve"][:1]
    return TaskSuite(tasks, splits, name=f"katas-{scheme}")


def _assert_runner(code: str, asserts: Sequence[str], module: bool = False) -> str:
    lines = [code, "", "_ok = 0"]
    for a in asserts:
        lines += ["try:", f"    if ({a}):", "        _ok += 1", "except Exception:", "    pass"]
    lines.append("print('__RSI_OK__', _ok)")
    return "\n".join(lines) + "\n"


class KatasDomain(Domain):
    name = "katas"
    components = {"prompt": ["prompts/*"], "gene": ["genes/*"], "skill": ["skills/*"]}

    def __init__(self, suite: Optional[TaskSuite] = None, *, scheme: str = "default", timeout_s: float = 10.0) -> None:
        super().__init__(suite or make_suite(scheme))
        self.timeout_s = timeout_s
        self._cache: dict = {}
        self._lock = threading.Lock()

    seed_artifact = staticmethod(seed_harness)

    def describe(self) -> str:
        return ("Katas: a solver harness prompts a model to write one small Python function; hidden unit tests grade "
                "it. Strategy genes are injected as guidance files under genes/ (or skills/).")

    # ------------------------------------------------------------------ prompts
    @staticmethod
    def guidance(artifact: Artifact) -> str:
        parts = [f"--- guidance file: {p} ---\n{t.strip()}\n" for p, t in artifact.items()
                 if p.startswith(("genes/", "skills/")) and p != "genes/library.json"]
        return (GUIDANCE_HEADER + "\n".join(parts)) if parts else ""

    def public_text(self, task: Task) -> str:
        i = task.input
        return f"{i['signature']}\n    \"\"\"{i['description']}\"\"\"\nExamples: " + "; ".join(i["public_tests"])

    def build_prompt(self, artifact: Artifact, task: Task) -> tuple[str, str]:
        i = task.input
        system = artifact.get("prompts/system.md", SYSTEM) + self.guidance(artifact)
        prompt = artifact.get("prompts/task.md", TASK).format(signature=i["signature"], description=i["description"],
                                                              public_tests="\n".join(i["public_tests"]))
        return system, prompt

    # ------------------------------------------------------------------ execute / grade
    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm) -> Execution:
        if llm is None:
            raise ValueError("katas needs a task LLM")
        system, prompt = self.build_prompt(artifact, task)
        resp = llm.complete(prompt, system=system, seed=seed, role="task")
        if not resp.ok:
            return Execution(error=f"infra: llm backend error: {resp.error}")
        code = extract_code(resp.text) or ""
        return Execution(output=code, trace=f"[prompt] {prompt[:400]}\n[reply] {resp.text[:1500]}",
                         tokens=resp.usage.total_tokens, cost_usd=resp.usage.cost_usd, steps=1,
                         error=None if code.strip() else "no python code block in reply")

    def _run_asserts(self, code: str, asserts: Sequence[str]) -> tuple[int, str]:
        key = hashlib.sha256((code + "\x00" + "\x00".join(asserts)).encode()).hexdigest()
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        rr = run_python(_assert_runner(code, asserts), timeout_s=self.timeout_s, mem_mb=512)
        m = re.search(r"__RSI_OK__ (\d+)", rr.stdout)
        res = (int(m.group(1)) if m else 0, "" if m else (rr.stderr.strip().splitlines() or ["crashed"])[-1][:300])
        with self._lock:
            self._cache[key] = res
        return res

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        k = KATA_BY_ID[task.id]
        ok, err = self._run_asserts(str(execution.output or ""), k.hidden)
        n = len(k.hidden)
        return (1.0 if ok == n else 0.0), f"{ok}/{n} hidden tests passed" + (f" ({err})" if err else "")

    def leakage_terms(self, split: str = "evolve") -> list[str]:
        terms = []
        for t in self.tasks.split(split, allow_sealed=True):
            terms.append(t.id)
            for a in KATA_BY_ID[t.id].hidden:
                terms += [x for x in re.findall(r"'([^']{3,})'", a)]
        return sorted(set(terms))

    # ------------------------------------------------------------------ hooks for rsi.evomap
    @staticmethod
    def smoke_script(k: Kata) -> str:
        return "from solution import *\n\n" + "".join(f"assert {a}, {a!r}\n" for a in k.public) + \
            "print('public tests passed')\n"

    def pre_workspace(self, task: Task) -> dict:
        k = KATA_BY_ID[task.id]
        stub = f"{k.signature}\n    raise NotImplementedError\n"
        return {"solution.py": stub, "smoke_test.py": self.smoke_script(k)}

    def workspace(self, task: Task, execution: Execution) -> dict:
        w = self.pre_workspace(task)
        w["solution.py"] = str(execution.output or "")
        return w

    def public_feedback(self, task: Task, execution: Execution) -> str:
        k = KATA_BY_ID[task.id]
        ok, err = self._run_asserts(str(execution.output or ""), k.public)
        return f"public tests: {ok}/{len(k.public)} passed" + (f"; error: {err}" if err else "")


# ----------------------------------------------------------------------------- offline solver
HINTS = {
    "boundaries": [r"inclusive", r"b \+ 1|<=", r"ceil|-\(-", r"n == 0|edges?|empty input", r"max\(0"],
    "unicode": [r"normaliz|nfc|nfd", r"casefold", r"combining"],
    "dates": [r"fromisoformat|strptime", r"timedelta|datetime", r"leap", r"weekday\(\)|monday == 0", r"month/day"],
    "rounding": [r"decimal", r"half.?up", r"divmod|remainder", r"integer cents"],
    "retry": [r"min\(cap|cap every|each delay", r"min\(success_at|number of attempts", r"429", r"max\(0\.0|non-negative"],
}
PITFALLS = {
    "boundaries": [r"lst\[-n:\]", r"floor division"], "unicode": [r"ascii', 'ignore'|encode\(", r"code points|raw"],
    "dates": [r"365|30-day", r"day/month"], "rounding": [r"round\(\)|banker", r"%\.2f|float sums"],
    "retry": [r"uncapped", r">= 400"],
}
ANTI = {
    "boundaries": [r"range\(a, b\) and", r"use floor division", r"slice with lst\[-n:\]"],
    "unicode": [r"with lower\(\)", r"drop non-ascii"],
    "dates": [r"365 days", r"to the day field", r"every fourth year"],
    "rounding": [r"use round\(", r"sum floats"],
    "retry": [r"every status >= 400", r"without a cap", r"allow negative"],
}
CLASS_BASE = {"boundaries": -0.2, "unicode": -0.6, "dates": -0.1, "rounding": -0.8, "retry": 0.0}


def _u(*parts) -> float:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:8], "big") / 2 ** 64


class KataSimSolver(MockLLM):
    """Simulated coder (see module doc). All effects are knobs, not facts."""

    def __init__(self, ability: float = 0.0, *, hint_gain: float = 0.9, max_hints: float = 2.5,
                 avoid_gain: float = 0.5, log_weight: float = 0.5, anti_gain: float = 1.0, skill_dilution: float = 0.5,
                 lambda_comp: float = 0.4, lambda_len: float = 0.2, name: str = "kata-sim") -> None:
        self.ability = ability
        self.kw = dict(hint_gain=hint_gain, max_hints=max_hints, avoid_gain=avoid_gain, log_weight=log_weight,
                       anti_gain=anti_gain, dil=skill_dilution, lc=lambda_comp, ll=lambda_len)
        super().__init__(self._respond, name=name)

    def _units(self, system: str) -> list[tuple[str, str]]:
        parts = re.split(r"^--- guidance file: (.+?) ---$", system or "", flags=re.M)
        return [(parts[i].strip(), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]

    def logit(self, kata: Kata, system: str) -> float:
        kw = self.kw
        units = self._units(system)
        hint_w = avoid_w = 0.0
        anti = 0
        toks = 0
        for path, text in units:
            toks += estimate_tokens(text)
            w = kw["dil"] if path.startswith("skills/") else 1.0
            low = text.lower()
            log_part = ""
            if "previous failure log" in low:
                low, log_part = low.split("previous failure log", 1)
            avoid_lines = [ln for ln in low.splitlines() if ln.strip().startswith("- ")]
            strat = "\n".join(ln for ln in low.splitlines() if not ln.strip().startswith("- "))
            hint_w += w * sum(1 for rx in HINTS[kata.cls] if re.search(rx, strat))
            if any(re.search(rx, "\n".join(avoid_lines)) for rx in PITFALLS[kata.cls]):
                avoid_w += w
            if log_part and any(re.search(rx, log_part) for rx in PITFALLS[kata.cls]):
                avoid_w += kw["log_weight"]
            anti += sum(1 for rx in ANTI[kata.cls] if re.search(rx, strat))
        z = (self.ability + CLASS_BASE[kata.cls] + kw["hint_gain"] * min(kw["max_hints"], hint_w)
             + kw["avoid_gain"] * min(1.0, avoid_w) - kw["anti_gain"] * min(2, anti)
             - kw["lc"] * max(0, len(units) - 1) - kw["ll"] * toks / 1000.0)
        return z

    def _respond(self, prompt: str, system: Optional[str], seed, i) -> str:
        k = next((k for k in KATAS if f"def {k.fn}(" in prompt), None)
        if k is None:
            return "I cannot find the function to implement."
        p = 1 / (1 + math.exp(-self.logit(k, system or "")))
        code = k.correct if _u(self.name, k.id, seed) < p else k.buggy[0]
        return f"Here is the implementation.\n```python\n{code}```\n"


class KataMockProposer(MockLLM):
    """Writes a gene card after a failure: the class's real lesson with probability
    ``insight`` (else a shallow generic card); validation per the prompt's hint."""

    def __init__(self, insight: float = 0.7, p_real_validation: float = 1.0, name: str = "kata-proposer") -> None:
        self.insight = insight
        self.p_real = p_real_validation
        super().__init__(self._respond, name=name)

    def _respond(self, prompt: str, system, seed, i) -> str:
        from .genes import CLASS_GENES
        m = re.search(r"Signals[^:]*:\s*(.*)", prompt)
        sig = [s.strip() for s in (m.group(1) if m else "").split(",")]
        cls = next((s.split(":", 1)[1] for s in sig if s.startswith("task:")), None)
        if cls is None:
            cls = next((c for c, kws in CLASS_KEYWORDS.items() if any(k in sig for k in kws)), None)
        if cls not in CLASS_GENES:
            return "{}"
        rng = random.Random(f"{self.name}|{seed}|{hashlib.sha256(prompt.encode()).hexdigest()[:12]}")
        g = CLASS_GENES[cls]
        real = "MUST fail before" in prompt or rng.random() < self.p_real
        val = ["python smoke_test.py"] if real else ["python --version"]
        if rng.random() < self.insight:
            d = {"id": g.id, "category": "repair", "signals_match": list(g.signals_match), "summary": g.summary,
                 "strategy": list(g.strategy), "avoid": list(g.avoid), "validation": val}
        else:
            d = {"id": f"gene_{cls}_generic_{rng.randrange(1000):03d}", "category": "repair",
                 "signals_match": list(CLASS_KEYWORDS[cls]), "summary": f"Be careful with {cls} problems.",
                 "strategy": ["Read the specification twice.", "Write the simplest implementation.",
                              "Check the visible examples."], "avoid": ["Overcomplicating the solution."],
                 "validation": val}
        return "```json\n" + json.dumps(d) + "\n```"
