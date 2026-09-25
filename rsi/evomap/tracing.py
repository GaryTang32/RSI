"""Per-cycle trace helpers for the gene loop (``rsi.trace`` event schema).

EvoMap is not a harness-editing method, so the uniform event kinds are mapped
onto one agent cycle (``round`` = cycle number):

==============  =====================================================================
``round_start`` loop state before the cycle: library, capsules, failed capsules, event
                chain, memory-graph sizes, pending outcome, personality, rollouts, hub
``analysis``    signals (raw -> deduped -> final), plateau / drift, memory advice
                (preferred, bans), the selector's decision with its scores
``proposal``    the gene the cycle will try: reused local gene, adopted hub asset, a
                scratch attempt, or a newly written gene (writer prompt + reply);
                ``diff`` = the ACTUAL change of the solver's input (harness vs harness
                with the gene injected)
``critic``      the consumer quarantine verdict on a hub asset (lint, policy, A/B)
``eval``        every graded rollout the cycle paid for (solve attempts, rsi-taskcheck
                A/B arms, quarantine A/B arms, hub-bank verification arms)
``gate``        solidify (the keep rule) with every number it used; hub verification
                of a published bundle; the quarantine A/B arithmetic
``decision``    library version before / after and why
``state``       loop state after the cycle plus the memory-graph outcome written
==============  =====================================================================

Nothing here is read back by the loop: it only formats what the loop computed.
"""
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
    """What a routed evaluation of the library depends on (card text + routing patterns)."""
    rows = sorted((g.id, render_gene(g), json.dumps(list(g.signals_match))) for g in genes)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def gene_brief(g: Optional[Gene]) -> Optional[dict]:
    if g is None:
        return None
    return {"id": g.id, "category": g.category, "signals_match": list(g.signals_match), "summary": g.summary,
            "strategy": list(g.strategy), "avoid": list(g.avoid), "validation": list(g.validation),
            "provenance": g.provenance}


def trial_eval(candidate: str, split: str, task, trial, **extra: Any) -> dict:
    """``eval`` payload for a single graded rollout (same fields as ``RunTracer.evaluation``)."""
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
    out = []
    for row in (report.commands or []):
        out.append({k: (str(v)[-400:] if k in ("stdout", "stderr") else v) for k, v in row.items()})
    return out
