"""Frontier state (the code's ``frontier.json``) and the run directory layout.

``frontier.json`` holds the incumbent ``{t, artifact_id, job, S, C, extra, variant, node}``,
``S_star`` (best incumbent score so far), the ``trajectory`` (one entry per settled
round; ``trajectory[0]`` is H_0) and a frozen copy of the config and switches. A round is
*settled* once the trajectory has an entry for t+1 - the driver's resume rule.

Artifacts are content-addressed (:class:`rsi.core.ArtifactStore`); the artifact id plays
the role of the code's git tree hash, so "commits outside the harness never look like a
change".

Run directory (``out_dir``)::

    config.json  frontier.json  calibration.json  history.jsonl  attribution.jsonl
    global_analysis.json  ledger.jsonl  heldout_monitor.jsonl  STOP
    artifacts/<id[:2]>/<id>.json            every harness version
    evals/<job>.json                         every stored Measurement ("eval.json")
    r<t>/directives.json  analysis_report.json  digests.json  decisions.json  summary.json
    r<t>/<V>/proposal.json  proposal_r<n>.json  critic_a<n>.json  critic.json  diff.patch
            smoke.json  prep.json  eval.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class Frontier:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict:
        if not self.path.exists():
            raise FileNotFoundError(f"no {self.path}; run baseline first")
        return json.loads(self.path.read_text())

    def save(self, fr: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(fr, indent=1, default=str))
        tmp.replace(self.path)

    def settled_rounds(self) -> int:
        """Number of settled rounds (-1 when no baseline yet)."""
        if not self.path.exists():
            return -1
        return len(self.load()["trajectory"]) - 1


def read_json(path: str | Path, default=None):
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text())


def write_json(path: str | Path, obj, indent: Optional[int] = 1) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=indent, ensure_ascii=False, default=str))
    tmp.replace(p)
