"""RuleWorld as an :class:`rsi.core.Domain`: simulated execution, locked grader, GEPA hooks."""
from __future__ import annotations

from typing import Optional

from ...core.artifact import Artifact
from ...core.domain import Domain, Execution
from ...core.llm import LLM
from ...core.tasks import Task, TaskSuite
from .world import GARBLED, STANDARD, RuleWorld, WorldConfig

FEEDBACK_LEVELS = ("rich", "symptom", "score_only")


class RuleWorldDomain(Domain):
    """Feedback levels: ``rich`` names the violated rule, ``symptom`` the failing property, ``score_only`` nothing."""

    name = "ruleworld"
    components = {"prompt": ["prompts/*"]}

    def __init__(self, world: Optional[RuleWorld] = None, *, feedback: str = "rich", first_error_only: bool = False,
                 config: Optional[WorldConfig] = None, val_is_train: bool = False) -> None:
        if feedback not in FEEDBACK_LEVELS:
            raise ValueError(f"feedback must be one of {FEEDBACK_LEVELS}")
        self.world = w = world or RuleWorld(config)
        self.feedback = feedback
        self.first_error_only = first_error_only
        tasks = [Task(e.id, e.text, e.target_str, e.family, {"ticket": e.ticket, "aspects": dict(e.target),
                                                             "entities": [e.ticket]}) for e in w.examples.values()]
        splits = {s: list(w.splits[s]) for s in ("evolve", "val", "test")}
        if val_is_train:
            splits["val"] = list(w.splits["evolve"])
        splits["smoke"] = splits["evolve"][:1]
        super().__init__(TaskSuite(tasks, splits, name=f"ruleworld-s{w.cfg.seed}"))

    def with_feedback(self, feedback: str, first_error_only: Optional[bool] = None) -> "RuleWorldDomain":
        """Same world and splits, a different feedback function mu_f."""
        d = RuleWorldDomain(self.world, feedback=feedback,
                            first_error_only=self.first_error_only if first_error_only is None else first_error_only)
        d.tasks = self.tasks
        return d

    def seed_artifact(self) -> Artifact:
        return Artifact(self.world.seed_files(), {"domain": "ruleworld"})

    def oracle_artifact(self) -> Artifact:
        return Artifact(self.world.oracle_files(), {"domain": "ruleworld", "oracle": True})

    def component_paths(self) -> list[str]:
        return [self.world.module_path(m) for m in self.world.cfg.modules]

    def describe(self) -> str:
        mods = ", ".join(f"prompts/{m}.md" for m in self.world.cfg.modules)
        return ("The artifact is the set of module prompts of an order-fulfilment assistant (" + mods + "). "
                "Each ticket names a customer type and order properties; each module must output the correct "
                "handling protocol for the properties it handles. Protocols follow general business rules that the "
                "prompts must state; the grader checks each property's protocol.")

    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm: Optional[LLM]) -> Execution:
        ex = self.world.examples[task.id]
        out, trace = self.world.simulate(artifact.files, ex, seed, salt=artifact.id)
        n_lines = sum(v.n_lines for v in self.world.views(artifact.files).values())
        return Execution(output=out, trace=trace, tokens=12 * n_lines + 20 * len(ex.aspects),
                         steps=len(self.world.cfg.modules))

    def _owned(self, aspect: str, module: Optional[str]) -> bool:
        return module is None or self.world.aspects[aspect].module == module

    def feedback_text(self, task: Task, output: dict, module: Optional[str] = None) -> str:
        """mu_f at this domain's level, optionally restricted to one module's aspects."""
        if self.feedback == "score_only":
            return ""
        tgt = task.meta["aspects"]
        fails = [a for a in tgt if output.get(a) != tgt[a] and self._owned(a, module)]
        if not fails:
            return "All order properties handled here were given the correct protocol."
        parts = []
        for a in fails[:1] if self.first_error_only else fails:
            if self.feedback == "rich":
                parts.append(f"Wrong protocol for the {a} property (got '{output.get(a, STANDARD)}'). Rule: "
                             + self.world.rule_text(a, tgt[a]))
            else:
                parts.append(f"The {a} property was handled incorrectly.")
        ok = [a for a in tgt if output.get(a) == tgt[a] and self._owned(a, module)]
        if ok and self.feedback == "rich":
            parts.append("Correct: " + ", ".join(f"{a} ({tgt[a]})" for a in ok) + ".")
        return " ".join(parts)

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        out = execution.output or {}
        right = [out.get(a) == c for a, c in task.meta["aspects"].items()]
        score = (sum(right) / len(right)) if self.world.cfg.scoring == "partial" else float(all(right))
        return float(score), self.feedback_text(task, out)

    def module_of(self, component: str) -> Optional[str]:
        return next((m for m in self.world.cfg.modules if component == self.world.module_path(m)), None)

    @staticmethod
    def _error_record(task: Task, trial, outputs: str) -> dict:
        return {"Inputs": task.input, "Generated Outputs": outputs,
                "Feedback": f"Score: {trial.score:.3g}. {trial.feedback}".strip()}

    def reflective_record(self, task: Task, trial, component: str) -> dict:
        """Module-specific record: the module's own outputs and feedback, upstream notes as input."""
        m = self.module_of(component)
        if m is None or trial.error:
            return self._error_record(task, trial, str(trial.output if not trial.error else trial.error))
        out = trial.output if isinstance(trial.output, dict) else {}
        tgt = task.meta["aspects"]
        asp = [a for a in tgt if self.world.aspects[a].module == m]
        inputs = task.input
        if m != self.world.cfg.modules[0]:
            up = [f"{a}: {out.get(a, STANDARD)}" for a in tgt if self.world.aspects[a].module != m]
            inputs += "\nUpstream notes from earlier modules: " + ("; ".join(up) if up else "(none)")
        gen = "; ".join(f"{a}: {out.get(a, STANDARD)}" for a in asp) if asp else "(no properties for this module)"
        trace_lines = [l for l in (trial.trace or "").splitlines() if l.startswith(f"[{m}] decisions")]
        if trace_lines:
            gen += "\nTrace: " + trace_lines[0].split("decisions: ", 1)[-1]
        m_score = (sum(out.get(a) == tgt[a] for a in asp) / len(asp)) if asp else 1.0
        return {"Inputs": inputs, "Generated Outputs": gen,
                "Feedback": (f"Score: {m_score:.3g}. " + self.feedback_text(task, out, m)).strip()}

    def system_reflective_record(self, task: Task, trial, component: str) -> dict:
        """System-level record (spec E9): every module sees the whole system's outputs and feedback."""
        if trial.error:
            return self._error_record(task, trial, str(trial.error))
        out = trial.output if isinstance(trial.output, dict) else {}
        gen = "; ".join(f"{a}: {out.get(a, STANDARD)}" for a in task.meta["aspects"]) or "(no properties)"
        dec = [l.split("decisions: ", 1)[-1] for l in (trial.trace or "").splitlines() if "] decisions: " in l]
        if dec:
            gen += "\nTrace: " + "; ".join(dec)
        return {"Inputs": task.input, "Generated Outputs": gen,
                "Feedback": (f"Score: {trial.score:.3g}. " + self.feedback_text(task, out)).strip()}

    def misplaced_rule_lines(self, artifact) -> int:
        """Rule lines naming only properties another module handles (a credit-assignment error)."""
        n = 0
        for m in self.world.cfg.modules:
            for line in (artifact.get(self.world.module_path(m), "") or "").splitlines():
                asp = list(self.world.parse_module(line).rules)
                if asp and all(self.world.aspects[a].module != m for a in asp):
                    n += 1
        return n

    def rl_vocabulary(self, component: str) -> list[str]:
        m = self.module_of(component)
        return self.world.vocabulary(m) if m else []

    def gold_text(self, task: Task, component: str) -> str:
        """The correct output of one module for ``task``."""
        m = self.module_of(component)
        return "; ".join(f"{a}: {c}" for a, c in task.meta["aspects"].items() if self._owned(a, m)) \
            or "(nothing to handle)"

    def demo_text(self, task: Task, output, component: str) -> str:
        """One-line demo for the few-shot baseline; ``output`` is the demonstrated answer dict (gold if not a dict)."""
        m = self.module_of(component)
        out = output if isinstance(output, dict) else task.meta["aspects"]
        ans = "; ".join(f"{a}: {out.get(a, STANDARD)}" for a in task.meta["aspects"] if self._owned(a, m))
        return f"Example: {task.input} => {ans or '(nothing to handle)'}"

    def expected(self, artifact: Artifact | dict, split: str = "test") -> float:
        files = artifact.files if isinstance(artifact, Artifact) else dict(artifact)
        return self.world.expected(files, self.tasks.splits[split])

    def expected_per_task(self, artifact: Artifact | dict, split: str) -> dict[str, float]:
        files = artifact.files if isinstance(artifact, Artifact) else dict(artifact)
        vs = self.world.views(files)
        return {i: self.world.expected_example(vs, self.world.examples[i]) for i in self.tasks.splits[split]}


def make_domain(seed: int = 0, feedback: str = "rich", **cfg_overrides) -> RuleWorldDomain:
    """``make_domain(seed=3, feedback="symptom", n_train=15)``; also takes ``val_is_train`` and ``first_error_only``."""
    val_is_train = bool(cfg_overrides.pop("val_is_train", False))
    first_error_only = bool(cfg_overrides.pop("first_error_only", False))
    return RuleWorldDomain(RuleWorld(WorldConfig(seed=seed, **cfg_overrides)), feedback=feedback,
                           first_error_only=first_error_only, val_is_train=val_is_train)


__all__ = ["RuleWorldDomain", "make_domain", "FEEDBACK_LEVELS", "GARBLED", "STANDARD"]
