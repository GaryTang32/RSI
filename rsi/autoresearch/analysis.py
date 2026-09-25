"""Morning report and honest measurement.

* :class:`Analyzer` - the quantities of upstream's ``analysis.ipynb``: keep rate
  n_keep / (n_keep + n_discard) (crashes excluded), running best over KEEP rows,
  per-keep contribution Delta_i = metric(previous keep) - metric(keep i) (sign
  adjusted so positive = better; the TOTAL telescopes to first keep - last keep),
  total improvement %, top hits, experiments/hour, and the ``progress.png`` plot.
* :class:`HiddenAudit` - xgboost-port ``run_groundtruth_all.sh`` analogue: every
  kept version is re-scored post hoc on splits the agent never saw (iid and
  shifted test), reported separately so overfitting and distribution shift are
  not conflated. A version whose audit run crashes gets a row with ``audit_error``
  (``CRASH`` in the TSV), as in the port's script, instead of disappearing. Results
  go to ``groundtruth_all.tsv`` and the ledger, never into the agent's context.
* :class:`Reeval` - honest re-evaluation with fresh run seeds (the MLX
  maintainers: "the recorded curve is an optimistic running-minimum that
  regresses on honest re-eval").
"""
from __future__ import annotations

import csv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from ..core.artifact import Artifact
from ..core.ledger import ArtifactStore, Ledger
from ..core.stats import summarize_runs


class Analyzer:
    """Rows are dicts with ``exp, status (keep|discard|crash), metric, description, commit, t``."""

    def __init__(self, rows: list[dict], direction: str = "min", metric: str = "val_bpb") -> None:
        self.rows = rows
        self.direction = direction
        self.metric = metric

    @classmethod
    def from_ledger(cls, ledger: Ledger, direction: str = "min", metric: str = "val_bpb") -> "Analyzer":
        rows = []
        for n in ledger.nodes():
            if n.status not in ("keep", "discard", "crash", "rejected"):
                continue
            rows.append({"exp": n.round, "status": "discard" if n.status == "rejected" else n.status,
                         "metric": n.score, "description": n.change, "commit": n.meta.get("commit", ""),
                         "t": n.t, "truth": n.metrics.get("truth")})
        return cls(rows, direction, metric)

    @classmethod
    def from_results(cls, path: str | Path, direction: str = "min") -> "Analyzer":
        from .results import ResultsLog

        log = ResultsLog.read(path)
        rows = [{"exp": i, "status": r.status, "metric": r.metric if r.status != "crash" else None,
                 "description": r.description, "commit": r.commit, "t": None} for i, r in enumerate(log.rows())]
        return cls(rows, direction, log.metric_name)

    def _gain(self, prev: float, cur: float) -> float:
        return prev - cur if self.direction == "min" else cur - prev

    def running_best(self) -> list[Optional[float]]:
        out, best = [], None
        for r in self.rows:
            if r["status"] == "keep" and r["metric"] is not None:
                if best is None or self._gain(best, r["metric"]) > 0:
                    best = r["metric"]
            out.append(best)
        return out

    def keep_deltas(self) -> list[dict]:
        out, prev = [], None
        for r in self.rows:
            if r["status"] != "keep" or r["metric"] is None:
                continue
            if prev is not None:
                out.append({"exp": r["exp"], "commit": r["commit"], "description": r["description"],
                            "delta": self._gain(prev, r["metric"]), "metric": r["metric"]})
            prev = r["metric"]
        return out

    def summary(self, wall_s: Optional[float] = None, top: int = 10) -> dict:
        st = [r["status"] for r in self.rows]
        n_keep, n_discard, n_crash = st.count("keep"), st.count("discard"), st.count("crash")
        base = self.rows[0]["metric"] if self.rows else None
        rb = self.running_best()
        best = rb[-1] if rb else None
        deltas = self.keep_deltas()
        out = {
            "n_rows": len(self.rows), "n_experiments": max(len(self.rows) - 1, 0), "n_keep": n_keep,
            "n_discard": n_discard, "n_crash": n_crash,
            "keep_rate": n_keep / (n_keep + n_discard) if (n_keep + n_discard) else float("nan"),
            "baseline": base, "best": best,
            "improvement": self._gain(base, best) if base is not None and best is not None else None,
            "total_keep_delta": float(sum(d["delta"] for d in deltas)),
            "top_hits": sorted(deltas, key=lambda d: -d["delta"])[:top],
        }
        if base and best is not None:
            out["improvement_pct"] = 100.0 * out["improvement"] / abs(base)
        if wall_s:
            out["experiments_per_hour"] = out["n_experiments"] / (wall_s / 3600.0)
        return out

    def report(self, wall_s: Optional[float] = None) -> str:
        s = self.summary(wall_s)
        lines = [f"experiments: {s['n_experiments']}  keep {s['n_keep']}  discard {s['n_discard']}  crash {s['n_crash']}"
                 f"  keep rate {s['keep_rate']:.2f}",
                 f"{self.metric}: baseline {s['baseline']}  best {s['best']}  improvement {s['improvement']}"]
        for d in s["top_hits"]:
            lines.append(f"  {d['delta']:+.6f}  {d['commit']}  {d['description']}")
        return "\n".join(lines)

    def plot(self, path: str | Path, title: str = "autoresearch progress", margin: Optional[float] = None) -> Path:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        rows = [r for r in self.rows if r["metric"] is not None and r["status"] != "crash"]
        base = self.rows[0]["metric"] if self.rows else None
        fig, ax = plt.subplots(figsize=(11, 5))
        xs = [r["exp"] for r in rows if r["status"] == "discard"]
        ys = [r["metric"] for r in rows if r["status"] == "discard"]
        if base is not None and ys:
            span = np.ptp([r["metric"] for r in rows if r["status"] == "keep"] + [base]) or abs(base) * 0.05 or 1.0
            lim = margin if margin is not None else 1.5 * span
            keep = [(x, y) for x, y in zip(xs, ys) if abs(self._gain(base, y)) <= lim or self._gain(base, y) > 0]
            xs, ys = [k[0] for k in keep], [k[1] for k in keep]
        ax.scatter(xs, ys, s=12, color="#9aa0a6", label="discarded", zorder=2)
        kx = [r["exp"] for r in rows if r["status"] == "keep"]
        ky = [r["metric"] for r in rows if r["status"] == "keep"]
        ax.scatter(kx, ky, s=36, color="#1e8e3e", label="kept", zorder=3)
        rb = self.running_best()
        ax.step([r["exp"] for r in self.rows], [b if b is not None else np.nan for b in rb], where="post",
                color="#1e8e3e", lw=1.5, label="running best", zorder=1)
        for r in rows:
            if r["status"] == "keep" and r["exp"] > 0:
                ax.annotate(str(r["description"])[:38], (r["exp"], r["metric"]), fontsize=7, rotation=30,
                            xytext=(3, 4), textcoords="offset points", color="#1e5e2e")
        ax.set_xlabel("experiment #")
        ax.set_ylabel(f"{self.metric} ({'lower' if self.direction == 'min' else 'higher'} is better)")
        n_keep = len(kx)
        ax.set_title(f"{title}: {len(self.rows) - 1} experiments, {max(n_keep - 1, 0)} kept improvements")
        ax.legend(loc="best", fontsize=8)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(p, dpi=110)
        plt.close(fig)
        return p


class HiddenAudit:
    """Post-hoc scoring of versions on hidden splits (the agent never sees these)."""

    def __init__(self, task, workers: int = 1) -> None:
        self.task = task
        self.workers = workers

    def audit(self, artifact: Artifact, seed: int = 0) -> dict[str, float]:
        out = dict(self.task.audit(artifact, seed=seed) or {})
        truth = self.task.truth(artifact)
        if truth is not None:
            out["truth"] = truth
        return out

    def audit_ledger(self, ledger: Ledger, store: ArtifactStore, statuses: Sequence[str] = ("keep",),
                     seed: int = 0) -> list[dict]:
        nodes = [n for n in ledger.nodes() if n.status in statuses and n.artifact_id]

        def one(n):
            return n, self.audit(store.get(n.artifact_id), seed=seed)

        if self.workers > 1:
            with ThreadPoolExecutor(self.workers) as ex:
                pairs = list(ex.map(one, nodes))
        else:
            pairs = [one(n) for n in nodes]
        rows = []
        for n, aud in pairs:
            if not aud:
                continue
            ledger.update(n.id, meta={"audit": aud})
            rows.append({"exp": n.round, "commit": n.meta.get("commit", ""), "status": n.status,
                         "description": n.change, "metric": n.score, **aud})
        return rows

    @staticmethod
    def write_tsv(rows: list[dict], path: str | Path, metric: str) -> Path:
        cols = ["commit", "status", "description", metric]
        extra = sorted({k for r in rows for k in r} - {"exp", "commit", "status", "description", "metric"})
        p = Path(path)
        with p.open("w", newline="") as f:
            w = csv.writer(f, delimiter="\t", lineterminator="\n")
            w.writerow(cols + extra)
            for r in rows:
                w.writerow([r["commit"], r["status"], r["description"],
                            f"{r['metric']:.6f}" if r["metric"] is not None else "N/A"]
                           + [f"{r[k]:.6f}" if isinstance(r.get(k), float) else
                              r.get(k, "CRASH" if r.get("audit_error") else "N/A") for k in extra])
        return p


class Reeval:
    """Re-run a version with fresh run seeds and summarise (mean, 95% bootstrap CI)."""

    def __init__(self, task, mode: str = "hardened", workers: int = 1) -> None:
        self.task = task
        self.mode = mode
        self.workers = workers

    def values(self, artifact: Artifact, seeds: Sequence[int]) -> tuple[list[float], int]:
        def one(s):
            return self.task.run(artifact, seed=s, mode=self.mode).metric

        if self.workers > 1:
            with ThreadPoolExecutor(self.workers) as ex:
                vals = list(ex.map(one, seeds))
        else:
            vals = [one(s) for s in seeds]
        ok = [float(v) for v in vals if v is not None]
        return ok, len(vals) - len(ok)

    def run(self, artifact: Artifact, seeds: Sequence[int]) -> dict:
        vals, n_crash = self.values(artifact, seeds)
        out = summarize_runs(vals) if vals else {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}
        out.update({"values": vals, "n_crash": n_crash, "seeds": list(seeds)})
        return out
