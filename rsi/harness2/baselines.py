"""Best-of-n with the same anonymised selector: the paper's Best-of-k baseline [spec §7.1, paper App. C].

The base harness H0 is rolled ``n`` times on each open task of the same stream and the Harness² selector picks one
deliverable; nothing is edited and no library is kept. With ``n = 4k`` it spends exactly the rollouts of Harness² at
budget k, so the two are compared at an equal number of agent calls per task.
"""
from __future__ import annotations

from typing import Optional, Sequence

from ..core.artifact import Artifact
from ..core.domain import Domain
from ..core.llm import LLM
from ..core.tasks import Task
from .api import TaskStream, domain_of, posthoc_report
from .guard import OpenWorld
from .loop import _seed, task_source, task_text
from .records import Rollout, TaskRecord
from .roles import Session, select


def best_of_n(domain: Domain, seed_artifact: Artifact, *, llm_task: LLM, llm_select: LLM, n: int,
              tasks: Sequence[Task], seed: int = 0, selector_attempts: int = 2, obs_trunc: int = 50000,
              slots: Optional[dict] = None, report: bool = True) -> dict:
    """``{"records": [...], "report": posthoc_report(...)}``; rollout seeds match Harness²'s ``base{i}`` labels."""
    if n < 1:
        raise ValueError("n >= 1")
    stream = TaskStream(tasks)
    records: list[TaskRecord] = []
    with OpenWorld(domain) as guard:
        s = Session(llm_select, guard, domain, editable=(), retry_invalid=0, seed=seed)
        for task in stream:
            rec = TaskRecord(task_id=task.id, domain_key=domain_of(domain, task), mode="best-of-n", k=n,
                             base_tree=seed_artifact)
            for i in range(1, n + 1):
                stream.assert_current(task.id)
                lab, sd = f"base{i}", _seed(seed, task.id, f"base{i}")
                rec.rollouts[lab] = Rollout(lab, seed_artifact.id, sd,
                                            domain.rollout(seed_artifact, task, seed=sd, llm=llm_task))
                rec.trees[lab] = seed_artifact
            sel, st = select(s, rec, list(rec.rollouts), task_text=task_text(domain, task),
                             source=task_source(domain, task), attempts=selector_attempts, slots=slots or {},
                             obs_trunc=obs_trunc, mode_token="bon")
            if st is not None:
                rec.stages.append(st)
            rec.selection, rec.adopted, rec.status = sel, sel["chosen"], ("ok" if sel["ok"] else "partial")
            records.append(rec)
    return {"records": records, "report": posthoc_report(domain, records, seed=seed) if report else None}
