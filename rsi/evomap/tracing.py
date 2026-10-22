"""Write-only payload helpers for the per-cycle ``rsi.trace`` events of the gene loop."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, Sequence

from rsi.core import Artifact

from .assets import Gene
from .prompts import render_gene


def loop_genes(store) -> list[Gene]:
    return [g for g in store.genes.values() if not g.id.startswith("gene_auto_")]


def library_fingerprint(genes: Sequence[Gene]) -> str:
    """Hash of what a routed evaluation depends on: card text and routing patterns."""
    rows = sorted((g.id, render_gene(g), json.dumps(list(g.signals_match))) for g in genes)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def gene_brief(g: Optional[Gene]) -> Optional[dict]:
    if g is None:
        return None
    return {"id": g.id, "category": g.category, "signals_match": list(g.signals_match), "summary": g.summary,
            "strategy": list(g.strategy), "avoid": list(g.avoid), "validation": list(g.validation),
            "provenance": g.provenance}


def trial_eval(candidate: str, split: str, task, trial, **extra: Any) -> dict:
    """``eval`` payload for one graded rollout."""
    return {"candidate": candidate,
            "summary": {"split": split, "S": trial.score, "C": trial.tokens, "n_tasks": 1, "k": 1,
                        "errors": int(trial.error is not None), "missing": 0},
            "per_task": {task.id: trial.score}, "trials": {task.id: [trial.score]},
            "feedback": trial.feedback, "error": trial.error, **extra}


def eval_payload(candidate: str, result, **extra: Any) -> dict:
    return {"candidate": candidate, "summary": result.summary(), "per_task": result.task_scores(),
            "trials": {tid: [t.score for t in trs] for tid, trs in result.trials.items()}, **extra}


def injected_diff(harness: Artifact, injected: Artifact) -> str:
    return harness.diff(injected) if injected.id != harness.id else ""


def validation_rows(report) -> list[dict]:
    return [{k: (str(v)[-400:] if k in ("stdout", "stderr") else v) for k, v in row.items()}
            for row in (report.commands or [])]
