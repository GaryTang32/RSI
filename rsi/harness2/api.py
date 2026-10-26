"""``run``: Harness² over a task stream (task-level recursion per task, harnessing-level recursion after it).

The loop never reads a grader, a label or a score [spec §1, §5]:

- tasks arrive through a :class:`TaskStream` one at a time, as *open* copies (no target, no answer-like meta keys);
  only the current task can be executed, completed tasks are never replayed and later tasks are not visible;
- the whole loop runs inside :class:`~.guard.OpenWorld`, so the domain's ``grade``/``run``/``grade_execution``/
  ``score`` raise if anything calls them, and every improver prompt, proposed tree and library entry is screened
  for grader-verdict markers;
- per task: Propose-Probe-Compose within R = 4k rollouts and P = 3k sessions, final selection, then (harnessing
  level) one reflector session and, only for a supported lesson, a library entry that later tasks of the same
  domain read;
- benchmark scores exist only in :func:`posthoc_report`, which grades the stored deliverables after the loop ended.
"""
from __future__ import annotations

import fnmatch
import json
import random
import tempfile
from pathlib import Path
from typing import Iterator, Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.ledger import Ledger, Node
from ..core.llm import LLM
from ..core.run import Budget, ImprovementResult
from ..core.stats import bootstrap_ci, paired_diff_ci
from ..core.tasks import SEALED_SPLITS, Task
from ..trace import RunTracer
from .config import Config
from .guard import OpenWorld, OpenWorldViolation, markers_for, open_task
from .library import Library
from .loop import MODE_TOKEN, TaskRunner
from .records import TaskRecord
from .roles import Session
from .tree import editable_globs, validate


class StreamViolation(OpenWorldViolation):
    """A rollout of a task other than the current one (a replay of a past task or a peek at a future one)."""


class TaskStream:
    """Tasks one at a time, as open copies; the current task may be re-run, past and future ones may not."""

    def __init__(self, tasks: Sequence[Task], *, strip: bool = True) -> None:
        self._tasks = list(tasks)
        self._strip = strip
        self._i = -1
        self.closed: list[str] = []

    def __len__(self) -> int:
        return len(self._tasks)

    @property
    def current_id(self) -> Optional[str]:
        return self._tasks[self._i].id if 0 <= self._i < len(self._tasks) else None

    def __iter__(self) -> Iterator[Task]:
        while self._i + 1 < len(self._tasks):
            if self.current_id is not None:
                self.closed.append(self.current_id)
            self._i += 1
            yield open_task(self._tasks[self._i], self._strip)
        if self.current_id is not None:
            self.closed.append(self.current_id)
        self._i = len(self._tasks)

    def assert_current(self, task_id: str) -> None:
        if task_id != self.current_id:
            what = "a completed task (replay)" if task_id in self.closed else "a task that has not arrived yet"
            raise StreamViolation(f"rollout of {task_id!r}, {what}; only the current task {self.current_id!r} may run")


def domain_of(domain: Domain, task: Task) -> str:
    """Library key: ``domain.domain_of(task)`` if the domain has one, else the task's family."""
    hook = getattr(domain, "domain_of", None)
    return str(hook(task)) if callable(hook) else str(task.family)


def eval_split(domain: Domain, split: Optional[str]) -> list[str]:
    """Split names of the stream: ``split`` ('a+b' joins several), else the sealed evaluation split."""
    if split:
        names = [s.strip() for s in split.split("+") if s.strip()]
        if missing := [s for s in names if s not in domain.tasks.splits]:
            raise ValueError(f"unknown split(s) {missing}; have {sorted(domain.tasks.splits)}")
        return names
    for s in ("holdout", "test", "ood"):
        if domain.tasks.splits.get(s):
            return [s]
    raise ValueError("no holdout/test/ood split to stream; pass config.split")


def stream_order(domain: Domain, tasks: Sequence[Task], order: str, seed: int) -> list[Task]:
    """Stream order [code schedule.stream_order]: grouped by domain, sorted, or seeded shuffles of the sorted order."""
    if order == "split":
        return list(tasks)
    by_dom: dict[str, list[Task]] = {}
    for t in sorted(tasks, key=lambda t: t.id):
        by_dom.setdefault(domain_of(domain, t), []).append(t)
    doms = sorted(by_dom)
    if order == "shuffle":
        random.Random(f"{seed}:__domains__").shuffle(doms)
        for d in doms:
            random.Random(f"{seed}:{d}").shuffle(by_dom[d])
    return [t for d in doms for t in by_dom[d]]


def stream_tasks(domain: Domain, cfg: Config) -> list[Task]:
    """The ordered task stream of a config (labels still attached; the loop only sees open copies)."""
    tasks: list[Task] = []
    for name in eval_split(domain, cfg.split):
        tasks += domain.tasks.split(name, allow_sealed=name in SEALED_SPLITS)
    tasks = stream_order(domain, list({t.id: t for t in tasks}.values()), cfg.order, cfg.seed)
    return tasks[:cfg.max_tasks] if cfg.max_tasks is not None else tasks


def editable_surface(domain: Domain, seed: Artifact, editable: Optional[Sequence[str]]) -> list[str]:
    """Editable globs: the config's, else the seed's files plus the domain's component globs."""
    if editable is not None:
        return list(editable)
    comps = getattr(domain, "components", None) or {}
    globs = [g for gs in comps.values() for g in gs] if isinstance(comps, dict) else []
    return sorted(set(editable_globs(seed, None)) | set(globs))


def _zero() -> dict:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0, "total_tokens": 0}


def merged_usage(*llms: Optional[LLM]) -> dict:
    """Per-role usage summed over distinct LLM objects, plus ``_total`` (cached roles excluded)."""
    out: dict = {}
    seen = set()
    for llm in llms:
        if llm is None or id(llm) in seen:
            continue
        seen.add(id(llm))
        for role, u in llm.meter.snapshot().items():
            if role != "_total":
                cur = out.setdefault(role, _zero())
                for k in cur:
                    cur[k] += u.get(k, 0)
    tot = _zero()
    for r, u in out.items():
        if not r.endswith(":cached"):
            for k in tot:
                tot[k] += u[k]
    out["_total"] = tot
    return out


def run(domain: Domain, seed_artifact: Artifact, *, llm_task: Optional[LLM], llm_propose: Optional[LLM] = None,
        config: Optional[Config] = None, out_dir: Optional[str | Path] = None, llm_select: Optional[LLM] = None,
        budget: Optional[Budget] = None, tasks: Optional[Sequence[Task]] = None) -> ImprovementResult:
    """Run Harness² over the stream; returns per-task deliverables, records and the per-domain library.

    ``llm_propose`` drives editors and composers (default: the task model, as in the reference code);
    ``llm_select`` the selector and reflector (default: ``llm_propose``). ``tasks`` overrides the stream (in order).
    ``budget`` (or ``config.max_usd``) is checked between tasks, never inside one: a started task always finishes
    its 4k rollouts and 3k sessions. ``res.best`` is H0, because every task starts from H0; what the method
    produces is ``res.meta['deliverables']`` and the library (``res.meta['library_dir']``)."""
    cfg = config or Config()
    cfg.validate()
    if llm_task is None:
        raise ValueError("Harness² needs a task model (llm_task)")
    improver = llm_propose or llm_task
    selector = llm_select or improver
    out = Path(out_dir) if out_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    tracer = RunTracer(out if (out and cfg.trace) else None, "harness2")
    ledger = Ledger(out / "ledger.jsonl" if out else None)
    stream_list = list(tasks) if tasks is not None else stream_tasks(domain, cfg)
    if not stream_list:
        raise ValueError("the task stream is empty")
    editable = editable_surface(domain, seed_artifact, cfg.editable)
    off = [p for p in seed_artifact if not any(fnmatch.fnmatch(p, g) for g in editable)]
    lib_root = Path(cfg.library_dir) if cfg.library_dir else (out / "library" if out else
                                                              Path(tempfile.mkdtemp(prefix="harness2-lib-")))
    markers = markers_for(domain)
    library = Library(lib_root / MODE_TOKEN[cfg.mode], markers)
    library.clear()                     # the library starts empty for every stream [spec §4.1]
    slots = dict(cfg.prompts)
    brief = getattr(domain, "harness_brief", None)
    if "work" not in slots and callable(brief):
        slots["work"] = brief()         # the harness interface without any grading description
    llms = list({id(x): x for x in (llm_task, improver, selector)}.values())
    budget = budget or Budget()

    def spent() -> float:
        return sum(x.meter.total().cost_usd for x in llms)

    seed_errs = validate(seed_artifact, seed_artifact, domain=domain, max_text_chars=cfg.max_text_chars)
    if tracer.enabled:
        tracer.event("run_start", None, seed=seed_artifact.short_id, domain=domain.name, config=cfg.to_json(),
                     n_tasks=len(stream_list), editable=editable, not_editable=off, library_root=str(library.root),
                     seed_files={p: seed_artifact[p][:600] for p in sorted(seed_artifact)},
                     seed_validation=seed_errs or "ok",
                     what_improves="per task: the execution harness (Propose-Probe-Compose from H0); across tasks: "
                                   "the per-domain library of adopted harnesses + EXPERIENCE.md")
        tracer.event("noise", None, delta=None, mode="none",
                     detail="Harness² has no score and no noise band: decisions come from behavioural contrasts read "
                            "by the composer and an anonymised deliverable-only selector")
    stream = TaskStream(stream_list, strip=cfg.strip_targets)
    records: list[TaskRecord] = []
    traj: list[dict] = []
    stop = "stream_end"
    with OpenWorld(domain) as guard:
        session = Session(improver, guard, domain, editable, retry_invalid=cfg.retry_invalid,
                          max_text_chars=cfg.max_text_chars, seed=cfg.seed)
        sel_session = Session(selector, guard, domain, editable, retry_invalid=0,
                              max_text_chars=cfg.max_text_chars, seed=cfg.seed)
        for i, task in enumerate(stream):
            n_roll = sum(len(r.rollouts) for r in records)
            reason = budget.exhausted(rounds=i, rollouts=n_roll, usd=spent())
            if reason is None and cfg.max_usd is not None and records:
                if spent() + spent() / len(records) > cfg.max_usd:
                    reason = "max_usd_projected"   # the next task would likely cross the cap
                elif spent() >= cfg.max_usd:
                    reason = "max_usd"
            if reason:
                stop = reason
                break
            dkey = domain_of(domain, task)
            lib_files = library.context(dkey) if cfg.library else {}
            if tracer.enabled:
                tracer.event("round_start", i, task=task.id, domain=dkey, position=f"{i + 1}/{len(stream)}",
                             library_entries=library.entries(dkey) if cfg.library else [],
                             task_text=str(task.input)[:2000])
            runner = TaskRunner(domain=domain, task=task, domain_key=dkey, base_tree=seed_artifact,
                                llm_task=llm_task, session=session, select_session=sel_session, cfg=cfg,
                                library=lib_files, slots=slots, editable=editable, tracer=tracer, round=i,
                                stream=stream)
            rec = runner.solve()
            entry = None
            if cfg.library and rec.status == "ok" and rec.adopted_tree is not None:
                reflection = runner.reflect()
                entry = library.add(dkey, task.id, rec.adopted, rec.adopted_tree, rec.selection.get("why", ""),
                                    reflection)
                rec.library_added = entry
                if tracer.enabled:
                    tracer.event("library", i, domain=dkey, task=task.id, added=entry is not None, entry=entry,
                                 entries=library.entries(dkey),
                                 why="a supported lesson: adopted harness + EXPERIENCE.md stored" if entry else
                                 "no valid experience (NO_ENTRY or invalid): nothing is stored, not even the harness")
            records.append(rec)
            _ledger(ledger, rec, i)
            row = {"task": task.id, "domain": dkey, "status": rec.status, "adopted": rec.adopted,
                   "adopted_tree": rec.adopted_tree.short_id if rec.adopted_tree else None,
                   "kept_base": rec.kept_base(), "library_added": entry is not None,
                   "library_size": library.size(), "delivered": bool(rec.submitted and rec.submitted.delivered),
                   **{("stages_kept_base" if k == "kept_base" else k): v for k, v in rec.counts().items()}}
            traj.append(row)
            if tracer.enabled:
                tracer.event("state", i, **row)
            if out:
                (out / "tasks").mkdir(exist_ok=True)
                (out / "tasks" / f"{_safe(task.id)}.json").write_text(json.dumps(rec.to_json(), indent=1,
                                                                                 default=str))
    deliverables = {r.task_id: (str(r.submitted.execution.output) if r.submitted else None) for r in records}
    usage = merged_usage(llm_task, improver, selector)
    meta = {"config": cfg.to_json(), "n_stream": len(stream_list), "n_done": len(records),
            "unprocessed": [t.id for t in stream_list[len(records):]], "deliverables": deliverables,
            "library_dir": str(library.root), "library_entries": library.size(),
            "status": {r.task_id: r.status for r in records}, "editable": editable,
            "best_is": "H0: every task starts from the common harness; adopted harnesses live in the records and "
                       "the library"}
    if tracer.enabled:
        tracer.event("run_end", None, stop_reason=stop, tasks=len(records), library_entries=library.size(),
                     usage=usage, statuses=meta["status"])
    res = ImprovementResult(method="harness2", baseline=seed_artifact, best=seed_artifact, ledger=ledger,
                            trajectory=traj, usage=usage, stop_reason=stop, out_dir=str(out) if out else None,
                            meta=meta)
    if cfg.report:                      # evaluation only: after the open-world loop has ended
        rep = posthoc_report(domain, records)
        res.meta["report"] = rep
        if tracer.enabled:
            tracer.event("report", None, note="evaluation only, after the loop ended; never an input to it", **rep)
    if out:
        res.save(out)
    res.meta["records"] = records       # live objects, after save (not JSON)
    return res


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def _ledger(ledger: Ledger, rec: TaskRecord, i: int) -> None:
    base_id = f"{rec.task_id}/H0"
    ledger.add(Node(id=base_id, parent=None, round=i, kind="baseline", status="reference",
                    artifact_id=rec.base_tree.id, change="common execution harness H0"))
    for st in rec.stages:
        if st.tree is None:
            continue
        status = "adopted" if st.label == rec.adopted else ("fell_back" if st.fell_back else "candidate")
        ledger.add(Node(id=f"{rec.task_id}/{st.label}", parent=base_id, round=i, kind=st.role, status=status,
                        artifact_id=st.tree.id, change=(st.notes or st.error or "")[:600],
                        diff=rec.base_tree.diff(st.tree)[:4000],
                        meta={"kept_base": st.kept_base, "attempts": st.attempts, "adopted_from": st.adopted_from}))


def _score(domain: Domain, task: Task, rollout) -> float:
    if rollout is None:
        return float(domain.failure_score)
    return float(domain.grade_execution(task, rollout.execution)[0])


def posthoc_report(domain: Domain, records: Sequence[TaskRecord], *, seed: int = 0) -> dict:
    """Evaluation only: grade each task's submitted deliverable and its first reference rollout (H0), after the loop.

    Raises if called while an :class:`OpenWorld` guard is active on ``domain`` (the refused method raises)."""
    rows: dict[str, dict] = {}
    for r in records:
        task = domain.tasks.get(r.task_id)          # the labelled task, outside the loop
        rows[r.task_id] = {"family": task.family, "status": r.status, "base": _score(domain, task, r.base_rollout),
                           "submitted": _score(domain, task, r.submitted), "kept_base": r.kept_base()}
    base = [v["base"] for v in rows.values()]
    sub = [v["submitted"] for v in rows.values()]
    fams = sorted({v["family"] for v in rows.values()})
    return {"n": len(rows), "base": bootstrap_ci(base, seed=seed), "submitted": bootstrap_ci(sub, seed=seed),
            "paired": paired_diff_ci(base, sub, seed=seed),
            "families": {f: {"n": sum(v["family"] == f for v in rows.values()),
                             "base": float(sum(v["base"] for v in rows.values() if v["family"] == f)
                                           / max(1, sum(v["family"] == f for v in rows.values()))),
                             "submitted": float(sum(v["submitted"] for v in rows.values() if v["family"] == f)
                                                / max(1, sum(v["family"] == f for v in rows.values())))}
                         for f in fams},
            "per_task": rows}
