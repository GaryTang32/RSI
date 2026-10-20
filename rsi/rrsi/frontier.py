"""Frontier state (``frontier.json``: incumbent, S*, trajectory) and atomic JSON helpers."""
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
        """Rounds whose trajectory entry t+1 exists (-1 before the baseline)."""
        return len(self.load()["trajectory"]) - 1 if self.path.exists() else -1


def read_json(path: str | Path, default=None):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else default


def write_json(path: str | Path, obj, indent: Optional[int] = 1) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=indent, ensure_ascii=False, default=str))
    tmp.replace(p)
