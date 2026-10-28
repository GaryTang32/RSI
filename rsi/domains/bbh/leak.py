"""Static leak check of a final harness against the practice (evolve) examples it was searched on.

A harness fails if any file contains

- a ``window``-character (default 40) substring of an evolve question that is *specific* to that question, i.e. does
  not also occur in another evolve question (BBH questions of one family share instruction boilerplate such as
  "The following paragraphs each describe a set of five objects..."; copying that is not a leak of an example), or
- an evolve target that is not a generic token (see :func:`~.grading.is_generic_target`: option letters, yes/no
  words, numbers and sequences shorter than 3 tokens are generic), as a standalone string: not glued to further
  letters, digits or brackets, so e.g. a full Dyck closing sequence or a full sorted word list.
"""
from __future__ import annotations

from typing import Iterable, Mapping

from ...core.tasks import Task, TaskSuite
from .grading import is_generic_target

_GLUE = set("()[]{}<>")


def _standalone(text: str, needle: str) -> bool:
    start = 0
    while (i := text.find(needle, start)) >= 0:
        j = i + len(needle)
        before = text[:i].rstrip()[-1:]
        after = text[j:].lstrip()[:1]
        if not any(c and (c.isalnum() or c in _GLUE) for c in (before, after)):
            return True
        start = i + 1
    return False


def leak_check(files: Mapping[str, str], tasks: Iterable[Task] | TaskSuite, *, split: str = "evolve",
               window: int = 40, max_hits: int = 20) -> dict:
    """``{"passed": bool, "hits": [...], "checked": {...}}`` for harness ``files`` (an Artifact works)."""
    ts = tasks.split(split, allow_sealed=True) if isinstance(tasks, TaskSuite) else list(tasks)
    texts = {p: str(files[p] or "") for p in files}
    questions = [(t.id, str(t.input)) for t in ts]
    hits: list[dict] = []
    for tid, q in questions:
        others = [o for oid, o in questions if oid != tid]
        for path, text in texts.items():
            for i in range(0, max(0, len(q) - window + 1)):
                w = q[i:i + window]
                if w in text and not any(w in o for o in others):
                    hits.append({"kind": "question", "task": tid, "file": path, "snippet": w})
                    break
    for t in ts:
        tgt = str(t.target).strip()
        if is_generic_target(tgt):
            continue
        for path, text in texts.items():
            if _standalone(text, tgt):
                hits.append({"kind": "target", "task": t.id, "file": path, "snippet": tgt[:80]})
    return {"passed": not hits, "hits": hits[:max_hits], "n_hits": len(hits),
            "checked": {"split": split, "tasks": len(ts), "window": window, "files": sorted(texts),
                        "non_generic_targets": sum(not is_generic_target(str(t.target)) for t in ts)}}
