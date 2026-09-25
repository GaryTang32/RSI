"""ExperienceStore: Meta-Harness's filesystem ``D`` of every earlier candidate.

"Each evaluated harness contributes a directory containing its source code,
scores, and execution traces" [paper:MH §3]. Layout (release names kept where
they exist, spec A5)::

    <root>/
      candidates/<name>/
        src/<artifact files>                  # the harness source
        meta.json                             # iteration, base_system, hypothesis, axis, components, status
        eval/search/scores.json               # aggregate + per-unit scores, context cost, tokens
        eval/search/per_task/<unit>.json      # graded trial records (score, feedback, cost), no trace
        eval/search/traces/<unit>.jsonl       # raw execution traces (prompts, predictions, tool calls ...)
        eval/search/summary.md                # trace summary (only for the scores_summary ablation)
      evolution_summary.jsonl                 # one row per candidate (all iterations)
      frontier_val.json                       # per-unit best + "_pareto" + "_best" (search split)
      pending_eval.json                       # proposer -> loop handoff of the current iteration
      reports/                                # post-eval reports written by proposers
      sessions/iter<N>/{prompt.md, response.md, meta.json}   # proposer transcripts + accounting
      results/<split>/<name>/scores.json      # TEST results - written only by finalize(), never in a view
      frontier.json, finalized.json           # written only by finalize()

:meth:`ExperienceStore.view` projects the store for the proposer: ``full``
(everything except ``results/`` and ``frontier.json``), ``scores_summary`` (code +
scores + summaries, no traces), ``scores_only`` (code + scores), ``window``
(last-w candidates' code + scores), ``last_only`` (the current best candidate's
code, scores and traces), ``seed_only`` (the run's seed harness code only - Best-of-N). Views never
include test results - the operational isolation the release implements.
"""
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
    """A system's context cost = mean over units of the NON-ZERO per-unit context (0 if all are zero),
    as ``benchmark.py:print_frontier`` averages ``memory_context_chars`` over datasets."""
    nz = [float(v) for v in cost.values() if v]
    return float(sum(nz) / len(nz)) if nz else 0.0


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", str(s)).strip("_") or "x"


class FinalizedError(RuntimeError):
    """Raised when evolution is attempted after a complete finalisation."""


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

    # ------------------------------------------------------------ candidates
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
        m = json.loads(p.read_text()) if p.exists() else {"name": name}
        m.update(fields)
        p.write_text(json.dumps(m, indent=1, default=str))
        return m

    def meta(self, name: str) -> dict:
        p = self.cand_dir(name) / "meta.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def artifact(self, name: str) -> Artifact:
        return Artifact.from_dir(self.cand_dir(name) / "src")

    def names(self, status: Optional[str] = None) -> list[str]:
        out = []
        for d in sorted((self.root / "candidates").iterdir(), key=lambda p: (p.stat().st_mtime_ns, p.name)):
            mp = d / "meta.json"
            if mp.exists():
                m = json.loads(mp.read_text())
                if status is None or m.get("status") == status:
                    out.append(m["name"])
        out.sort(key=lambda n: (self.meta(n).get("order", 0), n))
        return out

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
        p = self.cand_dir(name) / "eval" / SEARCH / "scores.json"
        return json.loads(p.read_text()) if p.exists() else None

    def traces(self, name: str) -> dict[str, str]:
        d = self.cand_dir(name) / "eval" / SEARCH / "traces"
        return {p.stem: p.read_text() for p in sorted(d.glob("*.jsonl"))} if d.exists() else {}

    def write_summary_text(self, name: str, text: str) -> None:
        p = self.cand_dir(name) / "eval" / SEARCH / "summary.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    # ------------------------------------------------------------ run files
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
        return json.loads(self.frontier_path.read_text()) if self.frontier_path.exists() else {}

    def write_pending(self, iteration: int, candidates: list[dict]) -> None:
        self.pending_path.write_text(json.dumps({"iteration": iteration, "candidates": candidates}, indent=1))

    def clear_pending(self) -> None:
        if self.pending_path.exists():
            self.pending_path.unlink()

    def reports_dir(self) -> Path:
        return self.root / "reports"

    def write_reports(self, reports: dict[str, str]) -> list[str]:
        """Store proposer-written post-eval reports (``{"reports/<file>": text}``, release Step 0). A report
        that already exists is kept (Step 0 writes only missing reports). Returns the paths written."""
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

    # ------------------------------------------------------------ finalisation
    def is_finalized(self) -> bool:
        if not self.finalized_path.exists():
            return False
        return json.loads(self.finalized_path.read_text()).get("status") == "complete"

    def write_finalized(self, status: str, systems: list[str], **extra: Any) -> None:
        d = json.loads(self.finalized_path.read_text()) if self.finalized_path.exists() else {}
        d.update({"status": status, "systems": systems, **extra})
        d.setdefault("started_at", time.time())
        if status == "complete":
            d["completed_at"] = time.time()
        self.finalized_path.write_text(json.dumps(d, indent=1, default=str))

    def write_test_result(self, split: str, name: str, ev: EvalResult, cost: dict[str, float]) -> dict:
        d = self.results_dir / safe_name(split) / safe_name(name)
        d.mkdir(parents=True, exist_ok=True)
        res = {"split": split, "score": ev.score, "per_unit": ev.task_scores(), "per_unit_cost": cost,
               "context_cost": context_mean(cost), "tokens": ev.cost,
               "families": ev.family_scores()}
        (d / "test.json").write_text(json.dumps(res, indent=1, default=float))
        return res

    def test_result(self, split: str, name: str) -> Optional[dict]:
        p = self.results_dir / safe_name(split) / safe_name(name) / "test.json"
        return json.loads(p.read_text()) if p.exists() else None

    # ------------------------------------------------------------ views
    def view(self, mode: str = "full", *, window: int = 5, focus: Optional[str] = None,
             seeds: Iterable[str] = ()) -> dict[str, str]:
        """Read-only projection ``{relative path: text}`` of the store for the proposer.

        ``focus`` = the candidate shown by ``last_only`` (default: current best);
        ``seeds`` = the baseline names shown by ``seed_only``."""
        files: dict[str, str] = {}
        names = self.names()
        evaluated = [n for n in names if self.scores(n) is not None]

        def add_cand(n: str, *, src=True, meta=True, scores=True, traces=False, per_task=False, summary=False):
            d = self.cand_dir(n)
            rel = f"candidates/{safe_name(n)}"
            if src and (d / "src").exists():
                for p in sorted((d / "src").rglob("*")):
                    if p.is_file():
                        files[f"{rel}/src/{p.relative_to(d / 'src')}"] = p.read_text()
            if meta and (d / "meta.json").exists():
                files[f"{rel}/meta.json"] = (d / "meta.json").read_text()
            ev = d / "eval" / SEARCH
            if scores and (ev / "scores.json").exists():
                files[f"{rel}/eval/search/scores.json"] = (ev / "scores.json").read_text()
            if per_task and (ev / "per_task").exists():
                for p in sorted((ev / "per_task").glob("*.json")):
                    files[f"{rel}/eval/search/per_task/{p.name}"] = p.read_text()
            if traces and (ev / "traces").exists():
                for p in sorted((ev / "traces").glob("*.jsonl")):
                    files[f"{rel}/eval/search/traces/{p.name}"] = p.read_text()
            if summary and (ev / "summary.md").exists():
                files[f"{rel}/eval/search/summary.md"] = (ev / "summary.md").read_text()

        if mode == "full":
            for n in names:
                add_cand(n, traces=True, per_task=True)
            self._add_run_files(files, reports=True, sessions=True)
        elif mode == "scores_summary":
            for n in names:
                add_cand(n, summary=True)
            self._add_run_files(files)
        elif mode == "scores_only":
            for n in names:
                add_cand(n)
            self._add_run_files(files)
        elif mode == "window":
            for n in evaluated[-window:]:
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
        """Write a view to ``dest`` (e.g. for a coding agent's working directory)."""
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


# ------------------------------------------------------------------ history CLI
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
