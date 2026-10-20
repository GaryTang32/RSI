"""ExperienceStore: the filesystem holding every candidate's code, scores and raw traces, and its history views."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from ..core.artifact import Artifact
from ..core.evaluate import EvalResult

SEARCH = "search"


def context_mean(cost: dict[str, float]) -> float:
    """Mean of the non-zero per-unit context costs (0 if all are zero), as the release's ``print_frontier``."""
    nz = [float(v) for v in cost.values() if v]
    return float(sum(nz) / len(nz)) if nz else 0.0


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", str(s)).strip("_") or "x"


#: wall-clock fields: they vary between runs and say nothing about a harness (see Config.deterministic_view)
VOLATILE_KEYS = frozenset({"created_at", "latency_s", "runtime_seconds", "timing_s", "seconds", "wall_s",
                           "elapsed_s"})
#: a proposer session's own bill, which a cached replay meters as $0
SESSION_VOLATILE_KEYS = frozenset({"usage"})
#: the files the store writes; sources and raw traces are always shown exactly as written
STORE_WRITTEN = re.compile(r"^(evolution_summary\.jsonl|frontier_val\.json|reports/[^/]+\.jsonl?"
                           r"|sessions/[^/]+/meta\.json"
                           r"|candidates/[^/]+/(meta\.json|eval/search/scores\.json"
                           r"|eval/search/per_task/[^/]+\.json))$")


def _drop_volatile(obj: Any) -> tuple[Any, bool]:
    if isinstance(obj, dict):
        out, hit = {}, False
        for k, v in obj.items():
            if k in VOLATILE_KEYS:
                hit = True
                continue
            out[k], h = _drop_volatile(v)
            hit = hit or h
        return out, hit
    if isinstance(obj, list):
        pairs = [_drop_volatile(v) for v in obj]
        return [p[0] for p in pairs], any(p[1] for p in pairs)
    return obj, False


def strip_volatile_fields(path: str, text: str) -> str:
    """``text`` without volatile keys when ``path`` is a JSON/JSONL file the store writes; byte-identical otherwise."""
    if not STORE_WRITTEN.match(path):
        return text
    if path.endswith(".json"):
        try:
            obj, hit = _drop_volatile(json.loads(text))
        except ValueError:
            return text
        if path.startswith("sessions/") and path.endswith("/meta.json") and isinstance(obj, dict):
            extra = SESSION_VOLATILE_KEYS & obj.keys()
            obj = {k: v for k, v in obj.items() if k not in extra}
            hit = hit or bool(extra)
        return json.dumps(obj, indent=1, default=str) if hit else text
    if path.endswith(".jsonl"):
        lines, changed = [], False
        for line in (text[:-1] if text.endswith("\n") else text).split("\n"):
            try:
                obj, hit = _drop_volatile(json.loads(line))
            except ValueError:
                lines.append(line)
                continue
            lines.append(json.dumps(obj, default=str) if hit else line)
            changed = changed or hit
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "") if changed else text
    return text


class FinalizedError(RuntimeError):
    """Raised when evolution is attempted after a complete finalisation."""


def _read_json(p: Path, default: Any = None) -> Any:
    return json.loads(p.read_text()) if p.exists() else default


class ExperienceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        (self.root / "candidates").mkdir(parents=True, exist_ok=True)
        (self.root / "reports").mkdir(exist_ok=True)
        (self.root / "sessions").mkdir(exist_ok=True)
        self.summary_path = self.root / "evolution_summary.jsonl"
        self.frontier_path = self.root / "frontier_val.json"
        self.pending_path = self.root / "pending_eval.json"
        self.results_dir = self.root / "results"
        self.finalized_path = self.root / "finalized.json"

    def cand_dir(self, name: str) -> Path:
        return self.root / "candidates" / safe_name(name)

    def has(self, name: str) -> bool:
        return (self.cand_dir(name) / "meta.json").exists()

    def add_candidate(self, name: str, artifact: Artifact, meta: dict) -> Path:
        d = self.cand_dir(name)
        if (d / "src").exists():
            shutil.rmtree(d / "src")
        artifact.to_dir(d / "src")
        m = {"name": name, "artifact_id": artifact.id, "created_at": time.time(), "status": "pending", **meta}
        (d / "meta.json").write_text(json.dumps(m, indent=1, default=str))
        return d

    def update_meta(self, name: str, **fields: Any) -> dict:
        p = self.cand_dir(name) / "meta.json"
        m = _read_json(p, {"name": name})
        m.update(fields)
        p.write_text(json.dumps(m, indent=1, default=str))
        return m

    def meta(self, name: str) -> dict:
        return _read_json(self.cand_dir(name) / "meta.json", {})

    def artifact(self, name: str) -> Artifact:
        return Artifact.from_dir(self.cand_dir(name) / "src")

    def names(self, status: Optional[str] = None) -> list[str]:
        """Candidate names in registration order."""
        dirs = sorted((self.root / "candidates").iterdir(), key=lambda p: (p.stat().st_mtime_ns, p.name))
        metas = [json.loads((d / "meta.json").read_text()) for d in dirs if (d / "meta.json").exists()]
        metas.sort(key=lambda m: (m.get("order", 0), m["name"]))
        return [m["name"] for m in metas if status is None or m.get("status") == status]

    def write_eval(self, name: str, ev: EvalResult, *, cost: dict[str, float], extra: Optional[dict] = None,
                   split: str = SEARCH) -> dict:
        """Write scores, per-unit trial records and raw traces of one evaluation."""
        base = self._eval_dir(name, split)
        (base / "per_task").mkdir(parents=True, exist_ok=True)
        (base / "traces").mkdir(parents=True, exist_ok=True)
        per_unit = ev.task_scores()
        scores = {"split": split, "score": ev.score, "avg_val": round(100 * ev.score, 1), "per_unit": per_unit,
                  "per_unit_cost": cost, "context_cost": context_mean(cost),
                  "tokens": ev.cost, "steps": ev.steps, "n_units": len(per_unit), "k": ev.k,
                  "error_rate": ev.error_rate, "n_missing": ev.n_missing, "families": ev.family_scores(),
                  **(extra or {})}
        (base / "scores.json").write_text(json.dumps(scores, indent=1, default=float))
        for tid, trs in ev.trials.items():
            u = safe_name(tid)
            (base / "per_task" / f"{u}.json").write_text(json.dumps(
                [{k: v for k, v in t.to_json(max_trace=0).items() if k != "trace"} for t in trs], indent=1,
                default=str))
            if len(trs) == 1:
                text = trs[0].trace or ""
            else:
                text = "\n".join(f"# --- trial seed={t.seed} score={t.score}\n{t.trace or ''}" for t in trs)
            (base / "traces" / f"{u}.jsonl").write_text(text)
        return scores

    def _eval_dir(self, name: str, split: str) -> Path:
        if split != SEARCH:
            raise ValueError("only the search split is written under candidates/; test goes to results/")
        return self.cand_dir(name) / "eval" / split

    def scores(self, name: str) -> Optional[dict]:
        return _read_json(self.cand_dir(name) / "eval" / SEARCH / "scores.json")

    def traces(self, name: str) -> dict[str, str]:
        d = self.cand_dir(name) / "eval" / SEARCH / "traces"
        return {p.stem: p.read_text() for p in sorted(d.glob("*.jsonl"))} if d.exists() else {}

    def write_summary_text(self, name: str, text: str) -> None:
        p = self.cand_dir(name) / "eval" / SEARCH / "summary.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def append_summary(self, row: dict) -> None:
        with self.summary_path.open("a") as f:
            f.write(json.dumps(row, default=float) + "\n")

    def summary_rows(self) -> list[dict]:
        if not self.summary_path.exists():
            return []
        return [json.loads(l) for l in self.summary_path.read_text().splitlines() if l.strip()]

    def last_iteration(self) -> int:
        return max([int(r.get("iteration", 0)) for r in self.summary_rows()] or [0])

    def write_frontier(self, frontier: dict) -> None:
        self.frontier_path.write_text(json.dumps(frontier, indent=1, default=float))

    def frontier(self) -> dict:
        return _read_json(self.frontier_path, {})

    def write_pending(self, iteration: int, candidates: list[dict]) -> None:
        self.pending_path.write_text(json.dumps({"iteration": iteration, "candidates": candidates}, indent=1))

    def clear_pending(self) -> None:
        if self.pending_path.exists():
            self.pending_path.unlink()

    def reports_dir(self) -> Path:
        return self.root / "reports"

    def write_reports(self, reports: dict[str, str]) -> list[str]:
        """Store post-eval reports ``{"reports/<file>": text}``, keeping existing ones; returns the paths written."""
        written = []
        for rel, text in (reports or {}).items():
            name = safe_name(str(rel).split("/", 1)[-1].replace("/", "_"))
            if not name.endswith((".md", ".txt")):
                name += ".md"
            p = self.reports_dir() / name
            if p.exists() or not str(text or "").strip():
                continue
            p.write_text(str(text))
            written.append(f"reports/{name}")
        return written

    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    def log_session(self, iteration: int, *, prompt: str, response: str, meta: dict) -> Path:
        d = self.sessions_dir() / f"iter{iteration:03d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "prompt.md").write_text(prompt or "")
        (d / "response.md").write_text(response or "")
        (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
        return d

    def is_finalized(self) -> bool:
        return _read_json(self.finalized_path, {}).get("status") == "complete"

    def write_finalized(self, status: str, systems: list[str], **extra: Any) -> None:
        d = _read_json(self.finalized_path, {})
        d.update({"status": status, "systems": systems, **extra})
        d.setdefault("started_at", time.time())
        if status == "complete":
            d["completed_at"] = time.time()
        self.finalized_path.write_text(json.dumps(d, indent=1, default=str))

    def write_test_result(self, split: str, name: str, ev: EvalResult, cost: dict[str, float]) -> dict:
        d = self.results_dir / safe_name(split) / safe_name(name)
        d.mkdir(parents=True, exist_ok=True)
        res = {"split": split, "score": ev.score, "per_unit": ev.task_scores(), "per_unit_cost": cost,
               "context_cost": context_mean(cost), "tokens": ev.cost, "families": ev.family_scores()}
        (d / "test.json").write_text(json.dumps(res, indent=1, default=float))
        return res

    def test_result(self, split: str, name: str) -> Optional[dict]:
        return _read_json(self.results_dir / safe_name(split) / safe_name(name) / "test.json")

    def view(self, mode: str = "full", *, window: int = 5, focus: Optional[str] = None,
             seeds: Iterable[str] = (), strip_volatile: bool = False) -> dict[str, str]:
        """Read-only ``{relative path: text}`` projection of the store for the proposer (see ``HISTORY_MODES``)."""
        files = self._view(mode, window=window, focus=focus, seeds=seeds)
        if strip_volatile:
            files = {p: strip_volatile_fields(p, text) for p, text in files.items()}
        return files

    def _view(self, mode: str, *, window: int, focus: Optional[str], seeds: Iterable[str]) -> dict[str, str]:
        files: dict[str, str] = {}
        names = self.names()

        def add_cand(n: str, *, src=True, meta=True, scores=True, traces=False, per_task=False, summary=False):
            d = self.cand_dir(n)
            rel = f"candidates/{safe_name(n)}"
            if src and (d / "src").exists():
                for p in sorted((d / "src").rglob("*")):
                    if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
                        files[f"{rel}/src/{p.relative_to(d / 'src')}"] = p.read_text()
            ev = d / "eval" / SEARCH
            for want, folder, pattern in ((meta, d, "meta.json"), (scores, ev, "scores.json"),
                                          (per_task, ev / "per_task", "*.json"), (traces, ev / "traces", "*.jsonl"),
                                          (summary, ev, "summary.md")):
                if want:
                    for p in sorted(folder.glob(pattern)):
                        files[f"{rel}/{p.relative_to(d).as_posix()}"] = p.read_text()

        if mode == "full":
            for n in names:
                add_cand(n, traces=True, per_task=True)
            self._add_run_files(files, reports=True, sessions=True)
        elif mode in ("scores_summary", "scores_only"):
            for n in names:
                add_cand(n, summary=mode == "scores_summary")
            self._add_run_files(files)
        elif mode == "window":
            for n in [n for n in names if self.scores(n) is not None][-window:]:
                add_cand(n, meta=False)
        elif mode == "last_only":
            target = focus or self._best_name()
            if target:
                add_cand(target, traces=True, per_task=True)
        elif mode == "seed_only":
            for n in seeds:
                if self.has(n):
                    add_cand(n, meta=False, scores=False)
        else:
            raise ValueError(f"unknown view mode {mode!r}")
        return files

    def _add_run_files(self, files: dict[str, str], reports: bool = False, sessions: bool = False) -> None:
        if self.summary_path.exists():
            files["evolution_summary.jsonl"] = self.summary_path.read_text()
        if self.frontier_path.exists():
            files["frontier_val.json"] = self.frontier_path.read_text()
        if reports:
            for p in sorted(self.reports_dir().glob("*")):
                if p.is_file():
                    files[f"reports/{p.name}"] = p.read_text()
        if sessions:
            for p in sorted(self.sessions_dir().glob("*/meta.json")):
                files[f"sessions/{p.parent.name}/meta.json"] = p.read_text()

    def _best_name(self) -> Optional[str]:
        fr = self.frontier()
        if fr.get("_best"):
            return fr["_best"]["system"]
        best, bs = None, -1.0
        for n in self.names():
            s = self.scores(n)
            if s and s["score"] > bs:
                best, bs = n, s["score"]
        return best

    @staticmethod
    def materialize(files: dict[str, str], dest: str | Path, read_only: bool = True) -> Path:
        """Write a view to ``dest`` (e.g. a coding agent's working directory)."""
        d = Path(dest)
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file():
                    os.chmod(p, stat.S_IWUSR | stat.S_IRUSR)
            shutil.rmtree(d)
        for rel, text in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
            if read_only:
                os.chmod(p, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return d


def cli_frontier(store: ExperienceStore) -> str:
    """Text listing of the Pareto frontier (paper App. D's "short CLI")."""
    fr = store.frontier()
    rows = [f"{p['system']:40s} score={p['score']:.4f} cost={p['context_cost']:.1f}" for p in fr.get("_pareto", [])]
    return "\n".join(rows) or "(empty frontier)"


def cli_top(store: ExperienceStore, k: int = 5) -> list[tuple[str, float]]:
    scored = [(n, store.scores(n)["score"]) for n in store.names() if store.scores(n)]
    return sorted(scored, key=lambda x: -x[1])[:k]


def cli_diff(store: ExperienceStore, a: str, b: str) -> str:
    """Code diff plus per-unit score deltas between two candidates."""
    diff = store.artifact(a).diff(store.artifact(b))
    sa, sb = store.scores(a) or {}, store.scores(b) or {}
    lines = [f"{u}: {sa.get('per_unit', {}).get(u, float('nan')):.3f} -> {v:.3f}"
             for u, v in (sb.get("per_unit") or {}).items()]
    return diff + "\n# per-unit scores\n" + "\n".join(lines)


def cli_show(store: ExperienceStore, name: str, unit: str, n_lines: int = 40) -> str:
    t = store.traces(name).get(safe_name(unit), "")
    return "\n".join(t.splitlines()[:n_lines])
