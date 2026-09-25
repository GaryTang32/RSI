"""The shared injection interface and workspace adapters.

EvoMap never edits the agent's harness: a gene is *injected* into the frozen
artifact at solve time ("genes are injected under a shared injection interface").
An :class:`Injector` maps ``(artifact, genes) -> artifact``:

* :class:`FileInjector` (default) writes each rendered gene card to
  ``genes/active/<i>.md`` (domains read that directory; ``GeneWorld`` and ``katas`` do);
* :class:`AppendInjector` appends the cards to an existing prompt file (e.g.
  ``prompts/system.md`` of the ``agentqa`` harness) - works for any harness
  that already reads that file;
* :class:`SkillInjector` writes the long Skill form instead (X1 baseline).

:func:`pre_workspace` / :func:`post_workspace` give the validation workspace of a
cycle (W0 before, W1 after). Domains may implement ``workspace(task, execution)``
and ``pre_workspace(task)`` hooks; otherwise W0 = ``task.json`` (public input)
and W1 = W0 + ``output.txt``.

:class:`GeneRoutedDomain` evaluates a *gene library* artifact: its artifact is
the harness plus ``genes/library.json``; for each task it extracts signals,
selects one gene deterministically (no drift, no memory) and injects it before
delegating to the wrapped domain (grading stays in the wrapped domain).
"""
from __future__ import annotations

import json
from typing import Optional, Sequence

from rsi.core import Artifact, Domain, Execution, Task

from .assets import Gene
from .prompts import render_gene, render_skill
from .selector import GeneScorer, GeneSelector
from .signals import RunContext, TaskSignalExtractor

LIBRARY_PATH = "genes/library.json"


class Injector:
    def inject(self, artifact: Artifact, genes: Sequence[Gene]) -> Artifact:  # pragma: no cover
        raise NotImplementedError


class FileInjector(Injector):
    def __init__(self, directory: str = "genes/active", parts: Sequence[str] = ("keywords", "summary", "strategy",
                                                                                "avoid")) -> None:
        self.directory = directory.rstrip("/")
        self.parts = tuple(parts)

    def inject(self, artifact: Artifact, genes: Sequence[Gene]) -> Artifact:
        if not genes:
            return artifact
        upd = {f"{self.directory}/{i}.md": render_gene(g, self.parts) for i, g in enumerate(genes)}
        return artifact.with_files(upd)


class AppendInjector(Injector):
    def __init__(self, target: str, header: str = "\n\n# Strategy guidance\n") -> None:
        self.target = target
        self.header = header

    def inject(self, artifact: Artifact, genes: Sequence[Gene]) -> Artifact:
        if not genes:
            return artifact
        base = artifact.get(self.target, "")
        return artifact.with_files({self.target: base + self.header + "\n".join(render_gene(g) for g in genes)})


class SkillInjector(Injector):
    def __init__(self, directory: str = "skills") -> None:
        self.directory = directory

    def inject(self, artifact: Artifact, genes: Sequence[Gene]) -> Artifact:
        if not genes:
            return artifact
        return artifact.with_files({f"{self.directory}/{g.id}/SKILL.md": render_skill(g) for g in genes})


class TextInjector(Injector):
    """Inject arbitrary pre-rendered texts (ablations: fragments, raw logs)."""

    def __init__(self, texts: Sequence[str], directory: str = "genes/active") -> None:
        self.texts = list(texts)
        self.directory = directory

    def inject(self, artifact: Artifact, genes: Sequence[Gene] = ()) -> Artifact:
        return artifact.with_files({f"{self.directory}/{i}.md": t for i, t in enumerate(self.texts)})


# ----------------------------------------------------------------------------- workspaces
def public_task(task: Task) -> dict:
    """What an agent may see about a task (never the target)."""
    meta = {k: v for k, v in (task.meta or {}).items() if k in ("signals", "keywords", "public", "signature",
                                                                   "description")}
    return {"id": task.id, "family": task.family, "input": task.input, "meta": meta}


def pre_workspace(domain: Domain, task: Task) -> dict:
    hook = getattr(domain, "pre_workspace", None)
    if callable(hook):
        return dict(hook(task))
    return {"task.json": json.dumps(public_task(task), sort_keys=True, default=str)}


def post_workspace(domain: Domain, task: Task, execution: Execution) -> dict:
    hook = getattr(domain, "workspace", None)
    if callable(hook):
        return dict(hook(task, execution))
    w = pre_workspace(domain, task)
    w["output.txt"] = str(execution.output if execution.output is not None else "")
    return w


def task_text(domain: Domain, task: Task) -> str:
    hook = getattr(domain, "public_text", None)
    if callable(hook):
        return str(hook(task))
    return json.dumps(public_task(task), default=str)[:3000]


# ----------------------------------------------------------------------------- routed evaluation
def library_artifact(harness: Artifact, genes: Sequence[Gene]) -> Artifact:
    lib = json.dumps({"genes": [g.to_dict() for g in genes]}, sort_keys=True, indent=1)
    return harness.with_files({LIBRARY_PATH: lib})


def read_library(artifact: Artifact) -> list[Gene]:
    txt = artifact.get(LIBRARY_PATH)
    if not txt:
        return []
    return [Gene.from_dict(d) for d in json.loads(txt).get("genes", [])]


class GeneRoutedDomain(Domain):
    """Evaluate ``harness + genes/library.json`` on any wrapped domain: per task, pick
    the best-matching gene (scorer only) and inject it."""

    def __init__(self, base: Domain, *, injector: Optional[Injector] = None, extractor=None,
                 scorer: Optional[GeneScorer] = None, max_genes: int = 1) -> None:
        super().__init__(base.tasks)
        self.base = base
        self.name = f"routed:{base.name}"
        self.injector = injector or FileInjector()
        self.extractor = extractor or TaskSignalExtractor()
        self.selector = GeneSelector(scorer or GeneScorer("current"), use_memory=False, distilled_fallback=False)
        self.max_genes = max_genes
        self.components = base.components
        self.score_range = base.score_range

    def route(self, artifact: Artifact, task: Task) -> tuple[Artifact, list[Gene]]:
        genes = read_library(artifact)
        harness = artifact.with_files({LIBRARY_PATH: None})
        if not genes:
            return harness, []
        sig = self.extractor.extract(RunContext(task=task))
        dec = self.selector.select(genes, [], sig, drift_enabled=False)
        chosen = [dec.gene] if dec.gene is not None else []
        return self.injector.inject(harness, chosen), chosen

    def execute(self, artifact: Artifact, task: Task, *, seed: int, llm) -> Execution:
        art, chosen = self.route(artifact, task)
        ex = self.base.execute(art, task, seed=seed, llm=llm)
        ex.meta = {**(ex.meta or {}), "routed_gene": chosen[0].id if chosen else None}
        return ex

    def grade(self, task: Task, execution: Execution) -> tuple[float, str]:
        return self.base.grade(task, execution)

    def describe(self) -> str:
        return self.base.describe()
