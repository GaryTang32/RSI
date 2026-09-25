"""HarnessWorld as an :class:`rsi.core.Domain` (execute + locked grader + split discipline).

The artifact is a directory of mechanism files (``harness.md`` + one file per
mechanism); ``execute`` runs the simulated frozen policy on one task and returns the
number of rubric criteria passed and the tokens used; ``grade`` turns that into the
reward ``passed / total`` with textual feedback. ``expected`` gives the noise-free
ground truth used by experiments (never by the loop).
"""
from __future__ import annotations

import hashlib
import math
import random
import threading
from typing import Optional

from ...core.artifact import Artifact
from ...core.domain import Domain, Execution
from ...core.llm import LLM
from ...core.tasks import Task, TaskSuite
from .world import COMPONENT_PATH, STRONG, STRUCTURAL, WEAK, Policy, World, WorldConfig


class HarnessWorldDomain(Domain):
    name = "harnessworld"
    components = {"prompt": ["prompts/*"], "control_flow": ["control/*", "harness.md"], "config": ["config/*"],
                  "output_plumbing": ["output/*"], "context_mgmt": ["context/*"], "client_tool": ["tools/*"],
                  "skill": ["skills/*"], "memory": ["memory/*"], "subagent": ["agents/*"]}
    structural_components = STRUCTURAL
    critic_patterns = [(r"hw-e-\d{3}", "evolve task id pattern")]

    def __init__(self, world: Optional[World] = None, *, policy: Policy = STRONG, config: Optional[WorldConfig] = None,
                 show_catalog: bool = True) -> None:
        self.world = world or World(config)
        self.policy = policy
        self.show_catalog = show_catalog
        tasks = [Task(t.id, t.text, t.target, t.family, {"surface": t.surface, "entities": []}) for t in self.world.tasks]
        splits = {s: self.world.split_ids(s) for s in ("evolve", "holdout", "ood")}
        splits["smoke"] = splits["evolve"][:2]
        super().__init__(TaskSuite(tasks, splits, name=f"harnessworld-s{self.world.cfg.seed}"))
        self._cache: dict[str, tuple] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ helpers --
    def with_policy(self, policy: Policy) -> "HarnessWorldDomain":
        """Same world and tasks, a different frozen policy (cross-policy transfer)."""
        return HarnessWorldDomain(self.world, policy=policy, show_catalog=self.show_catalog)

    def seed_artifact(self) -> Artifact:
        return Artifact(self.world.seed_files(), {"domain": "harnessworld"})

    def _state(self, artifact: Artifact):
        key = artifact.id
        with self._lock:
            st = self._cache.get(key)
        if st is None:
            files = artifact.files
            err = None
            for path, text in files.items():                       # compile once per artifact version
                if path.endswith(".py"):
                    try:
                        compile(text, path, "exec")
                    except SyntaxError as e:
                        err = f"SyntaxError in {path}: {e.msg} (line {e.lineno})"
                        break
            lg, cost, mechs = self.world.logits(files, self.policy)
            p = {tid: 1.0 / (1.0 + math.exp(-z)) for tid, z in lg.items()}
            st = (p, cost, [m.id for m in mechs], err)
            with self._lock:
                if len(self._cache) > 5000:
                    self._cache.clear()
                self._cache[key] = st
        return st

    def expected(self, artifact: Artifact, split: str, policy: Optional[Policy] = None) -> dict:
        """Noise-free E[S], E[C] (ground truth; report-only)."""
        return self.world.expected(artifact.files, split, policy or self.policy)

    def mechanisms(self, artifact: Artifact) -> list[str]:
        return [m.id for m in self.world.active(artifact.files)[0]]

    def regression_threshold(self, k: int) -> float:
        return 0.25

    # ---------------------------------------------------------------- Domain API --
    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm: Optional[LLM]) -> Execution:
        if "harness.md" not in artifact:
            return Execution(error="harness.md missing: the harness entry point was deleted")
        p_map, cost, ids, syntax_error = self._state(artifact)
        if syntax_error:
            return Execution(error=syntax_error)
        p = p_map[task.id]
        rng = random.Random(int(hashlib.sha256(f"{artifact.id}|{task.id}|{seed}|{self.policy.name}".encode())
                                .hexdigest()[:16], 16))
        n = self.world.cfg.n_criteria
        passed = sum(1 for _ in range(n) if rng.random() < p)
        s = self.world.cfg.token_noise_sd
        tokens = self.world.cfg.base_tokens * cost * math.exp(rng.gauss(0.0, s) - 0.5 * s * s)
        fam, surf = task.family, task.meta.get("surface")
        trace = (f"[harness] {len(ids)} mechanisms active: {', '.join(ids) or '(none)'}\n"
                 f"[policy:{self.policy.name}] worked on a {fam} task ({surf} surface) for {int(tokens)} tokens\n"
                 f"[rubric] passed {passed}/{n} criteria")
        return Execution(output={"passed": passed, "total": n}, trace=trace, tokens=int(tokens), steps=1 + len(ids),
                         meta={"n_mechanisms": len(ids)})

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        out = execution.output or {}
        passed, total = int(out.get("passed", 0)), int(out.get("total", 1)) or 1
        fam, surf = task.family, task.meta.get("surface")
        if passed == total:
            return 1.0, f"Passed all {total} rubric criteria (family={fam}, surface={surf})."
        return passed / total, f"Missed {total - passed}/{total} rubric criteria (family={fam}, surface={surf})."

    def describe(self) -> str:
        w = self.world
        text = (
            "HarnessWorld: the artifact is an agent harness made of mechanism files (harness.md plus one file per "
            "mechanism). A frozen policy solves rubric-graded tasks; the reward of a trial is the fraction of rubric "
            f"criteria passed. Practice (evolve) task families: {list(w.cfg.practice_families)}; the harness is later "
            f"run unchanged on held-out tasks of the same families and on never-seen families "
            f"({len(w.cfg.ood_families)} of them), so improvements must be general. Every mechanism costs policy "
            "tokens. To add a mechanism, create its file at the listed path with the header lines "
            "`# mechanism: <id>`, `# component: <component>`, `# title: <title>`, a docstring line, and the line "
            "`enable(\"<id>\")`; to remove one, delete its file. Python files must compile.")
        if self.show_catalog:
            rows = "\n".join(f"- {m.id} [{m.component}] {m.path}: {m.title} - {m.description}"
                             for m in w.catalog.values() if m.kind not in ("leak", "obfuscated_leak"))
            text += "\n\nMechanism catalog (true effects unknown):\n" + rows
        return text


def make_domain(seed: int = 0, policy: Policy = STRONG, **world_kw) -> HarnessWorldDomain:
    """Convenience constructor: ``make_domain(seed=3, n_leak=12)``."""
    return HarnessWorldDomain(World(WorldConfig(seed=seed, **world_kw)), policy=policy)


__all__ = ["HarnessWorldDomain", "make_domain", "COMPONENT_PATH", "STRONG", "WEAK"]
