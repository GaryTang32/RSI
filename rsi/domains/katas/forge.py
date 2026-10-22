"""Katas adversary kit: farmer bundles (generic, vacuous, inflated) and poisoner bundles (a conflicting gene with
a real discriminative proof)."""
from __future__ import annotations

import random

from rsi.evomap.assets import Gene
from rsi.evomap.hub import Bundle
from rsi.evomap.population import forged_bundle

from .domain import KatasDomain
from .genes import CONFLICTING_GENES
from .katas import CLASS_KEYWORDS, CLASSES, KATAS

VACUOUS = {"version": (["python --version"], {}), "print_only": (["python smoke.py"], {"smoke.py": "print('ok')\n"}),
           "constant_assert": (["python sanity.py"], {"sanity.py": "assert True\nprint('ok')\n"}),
           "weak_assert": (["python exists.py"],
                           {"exists.py": "import solution\nassert hasattr(solution, '__name__')\n"}),
           "missing_script": (["python scripts/validate_all.py"], {}), "empty": ([], {})}


class KatasForge:
    def __init__(self, domain: KatasDomain) -> None:
        self.domain = domain
        self._n = 0

    def _bundle(self, g: Gene, author: str, before: dict, after: dict, claims: dict) -> Bundle:
        self._n += 1
        return forged_bundle(g, author, self._n, f"Applied {g.id} successfully to a kata", before, after, claims,
                             {"type": "ValidationReport", "overall_ok": True})

    def _states(self, rng: random.Random, correct: bool = True) -> tuple[dict, dict, str]:
        k = rng.choice(KATAS)
        t = self.domain.tasks.get(k.id)
        before = self.domain.pre_workspace(t)
        after = {**before, "solution.py": k.correct if correct else before["solution.py"] + "# touched\n"}
        return before, after, k.cls

    def farm_bundle(self, author: str, rng: random.Random, epoch: int) -> Bundle:
        before, _, cls = self._states(rng)
        kind = rng.choice(sorted(VACUOUS))
        val, extra = VACUOUS[kind]
        after = {**before, **extra, "solution.py": before["solution.py"] + f"# v{epoch}.{rng.randrange(99)}\n"}
        sm = [rng.choice(CLASS_KEYWORDS[c]) for c in rng.sample(CLASSES, 3)]
        g = Gene(id=f"gene_{author}_{epoch}_{rng.randrange(10 ** 6):06d}", category="optimize", signals_match=sm,
                 strategy=["Read the task.", "Write clean code.", "Check the examples."],
                 summary=f"Universal coding recipe for {', '.join(sm)}", validation=list(val))
        return self._bundle(g, author, before, after, {"confidence": 0.95, "files": 1, "lines": 20, "score": 0.95,
                                                      "streak": rng.randint(3, 5)})

    def poison_bundle(self, author: str, rng: random.Random, epoch: int) -> Bundle:
        before, after, cls = self._states(rng)
        src = CONFLICTING_GENES[cls]
        g = Gene(id=f"gene_{author}_{epoch}_{rng.randrange(10 ** 6):06d}", category="repair",
                 signals_match=list(src.signals_match), strategy=list(src.strategy), avoid=list(src.avoid),
                 summary=f"Proven fix for {cls} katas", validation=["python smoke_test.py"])
        return self._bundle(g, author, before, after, {"confidence": 0.97, "files": 1, "lines": 3, "score": 0.97,
                                                      "streak": 4})

    def is_poisoned(self, gene: Gene) -> bool:
        return any(gene.strategy == c.strategy for c in CONFLICTING_GENES.values())
