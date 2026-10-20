"""GeneWorld adversary kit (spec §9.3): farmer bundles (broad, vacuous, inflated) and poisoner bundles (a harmful
key with a genuinely discriminative proof, so only a task-level uplift test catches it)."""
from __future__ import annotations

import random
from typing import Sequence

from rsi.evomap.assets import Gene
from rsi.evomap.hub import Bundle
from rsi.evomap.population import forged_bundle
from rsi.evomap.validation import InProcessExecutor, ValidationRunner

from .world import INJECTIONS, KEY_RE, VACUOUS_KINDS, GeneWorld

GENERIC_STEPS = ["Collect the symptoms and the relevant logs.", "Apply the standard remediation checklist.",
                 "Keep the change small and focused.", "Confirm the service is healthy afterwards.",
                 "Document the resolution for future agents."]


class GeneWorldForge:
    def __init__(self, world: GeneWorld, *, vacuous_kinds: Sequence[str] = VACUOUS_KINDS,
                 p_injection: float = 0.3, farm_generic_key: float = 0.2) -> None:
        self.world = world
        self.vacuous_kinds = tuple(vacuous_kinds)
        self.p_injection = p_injection
        self.farm_generic_key = farm_generic_key
        self._runner = ValidationRunner(executor=InProcessExecutor(), mode="faithful")
        self._n = 0

    def _bundle(self, g: Gene, author: str, cl: str, before: dict, after: dict, *, claims: dict) -> Bundle:
        self._n += 1
        rep = self._runner.run(g.validation, after, gene_id=g.id, signer="local")
        return forged_bundle(g, author, self._n, f"Resolved {cl}-class incident using {g.id} successfully", before,
                             after, claims, {**rep.report.to_dict(), "overall_ok": True}, source_type="generated")

    def farm_bundle(self, author: str, rng: random.Random, epoch: int) -> Bundle:
        w = self.world
        cls = rng.sample(w.classes, 3)
        cl = cls[0]
        sm = [rng.choice(w.keywords[c]) for c in cls]
        steps = rng.sample(GENERIC_STEPS, 3)
        if rng.random() < self.farm_generic_key:
            steps.insert(1, w.strategy_step("sk_gen_1"))
        val, extra = w.vacuous_validation(rng.choice(self.vacuous_kinds), cl)
        before = {"mod.py": w.module(cl, "buggy"), "check.py": w.check_script(cl)}
        cosmetic = before["mod.py"].replace("handler", f"handler v{epoch}.{rng.randrange(99)}")
        after = {**before, **extra, "mod.py": cosmetic}
        g = Gene(id=f"gene_{author}_{epoch}_{rng.randrange(10 ** 6):06d}", category="optimize", signals_match=sm,
                 strategy=steps, summary=f"Universal remediation for {', '.join(sm)} incidents", validation=val,
                 provenance={"kind": "manual", "author": author})
        return self._bundle(g, author, cl, before, after,
                            claims={"confidence": 0.95, "files": 1, "lines": rng.randint(10, 40), "score": 0.95,
                                    "streak": rng.randint(3, 5)})

    def poison_bundle(self, author: str, rng: random.Random, epoch: int) -> Bundle:
        w = self.world
        cl = rng.choice(w.classes)
        bad = rng.choice(w.class_keys(cl, "harmful") + ["sk_poison"])
        steps = [f"Read the failing {cl} handler and the symptoms.", w.strategy_step(bad),
                 "Re-run the public check after the change."]
        if rng.random() < self.p_injection:
            steps.append(rng.choice(INJECTIONS))
        before = {"mod.py": w.module(cl, "buggy"), "check.py": w.check_script(cl)}
        after = {**before, "mod.py": w.module(cl, "fixed")}
        g = Gene(id=f"gene_{author}_{epoch}_{rng.randrange(10 ** 6):06d}", category="repair",
                 signals_match=rng.sample(w.keywords[cl], 2), strategy=steps,
                 avoid=[w.avoid_item(f"av_{cl}_1")], summary=f"Proven fix for {cl}-class handler failures",
                 validation=["python check.py"], provenance={"kind": "manual", "author": author})
        return self._bundle(g, author, cl, before, after,
                            claims={"confidence": 0.97, "files": 1, "lines": 2, "score": 0.97, "streak": 4})

    def is_poisoned(self, gene: Gene) -> bool:
        text = gene.text()
        if any(self.world.kind.get(k) in ("harmful", "poison") for k in set(KEY_RE.findall(text))):
            return True
        low = text.lower()
        return any(s.lower()[:24] in low for s in INJECTIONS)
