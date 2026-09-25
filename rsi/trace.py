"""Iteration tracing, a shadow held-out monitor, and a run inspector.

A self-improving loop is only trustworthy if every step can be checked. This
module records *what the loop did* in each iteration as an append-only
``trace.jsonl`` in the run directory and renders it as a readable narrative:

* what the analysis said, what the proposer was asked and replied,
* the claimed change next to the actual diff,
* the critic's verdict, per-task scores, the gate arithmetic, the decision,
* the loop state after the round (edit budget, failure memory, frontier ...),
* tokens and dollars spent.

:class:`ShadowMonitor` evaluates every newly kept version on sealed splits
(holdout / ood) and writes the numbers into the trace. Those numbers are
**never returned to the loop** - they exist so a human can see whether each kept
step generalizes. (This is the "keep a set the loop never sees" practice, used
as an audit instrument rather than a decision input.)

Event schema (one JSON object per line)::

    {"seq": 12, "t": 1727243000.1, "method": "rrsi", "round": 3, "kind": "gate",
     "data": {"candidate": "c3.0", "accept": false, "reason": "...", "math": {...}}}

Kinds used by the built-in methods: ``run_start``, ``baseline``, ``noise``,
``round_start``, ``analysis``, ``proposal``, ``critic``, ``eval``, ``gate``,
``decision``, ``state``, ``monitor``, ``note``, ``run_end``.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .core.artifact import Artifact
from .core.domain import Domain
from .core.evaluate import Evaluator
from .core.llm import LLM

MAX_TEXT = 6000


def _clip(x: Any, n: int = MAX_TEXT) -> Any:
    if isinstance(x, str) and len(x) > n:
        return x[:n] + f"\n...[truncated {len(x) - n} chars]"
    if isinstance(x, dict):
        return {k: _clip(v, n) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clip(v, n) for v in x]
    return x


class RunTracer:
    """Append-only iteration trace. Cheap no-op when ``out_dir`` is None."""

    def __init__(self, out_dir: Optional[str | Path], method: str, *, max_text: int = MAX_TEXT) -> None:
        self.method = method
        self.max_text = max_text
        self.path = Path(out_dir) / "trace.jsonl" if out_dir else None
        self._seq = 0
        self._lock = threading.Lock()
        self.monitor: Optional["ShadowMonitor"] = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self._seq = sum(1 for _ in self.path.open())

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def event(self, kind: str, round: Optional[int] = None, **data: Any) -> None:
        if not self.path:
            return
        with self._lock:
            rec = {"seq": self._seq, "t": time.time(), "method": self.method, "round": round, "kind": kind,
                   "data": _clip(data, self.max_text)}
            self._seq += 1
            with self.path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    # convenience wrappers - keep call sites short and field names uniform
    def proposal(self, round: int, candidate: str, *, parent: Optional[str], prompt: str = "", reply: str = "",
                 change: str = "", hypothesis: str = "", components: Sequence[str] = (), diff: str = "",
                 error: Optional[str] = None, **extra: Any) -> None:
        self.event("proposal", round, candidate=candidate, parent=parent, prompt=prompt, reply=reply, change=change,
                   hypothesis=hypothesis, components=list(components), diff=diff, error=error, **extra)

    def evaluation(self, round: Optional[int], candidate: str, result: Any, **extra: Any) -> None:
        """Record an :class:`~rsi.core.EvalResult` (summary + per-task scores + raw trial scores)."""
        data = {"candidate": candidate}
        if hasattr(result, "summary"):
            data.update(summary=result.summary(), per_task=result.task_scores(),
                        trials={tid: [t.score for t in trs] for tid, trs in result.trials.items()})
        else:
            data.update(result=result)
        data.update(extra)
        self.event("eval", round, **data)

    def gate(self, round: int, candidate: str, accept: bool, reason: str, math: Optional[dict] = None,
             **extra: Any) -> None:
        self.event("gate", round, candidate=candidate, accept=bool(accept), reason=reason, math=math or {}, **extra)

    def decision(self, round: int, *, kept: Optional[str], incumbent_before: Optional[str],
                 incumbent_after: Optional[str], why: str = "", **extra: Any) -> None:
        self.event("decision", round, kept=kept, incumbent_before=incumbent_before, incumbent_after=incumbent_after,
                   why=why, **extra)

    def kept(self, round: int, name: str, artifact: Artifact, decision_score: Optional[float] = None) -> None:
        """Call when a new version becomes the incumbent; triggers the shadow monitor."""
        if self.monitor is not None:
            self.monitor.observe(self, round, name, artifact, decision_score)


class ShadowMonitor:
    """Scores each kept version on sealed splits for the human audit only.

    The loop never receives these numbers: ``observe`` returns None and writes
    only to the trace. Uses its own :class:`Evaluator` (``allow_sealed=True``) so
    the loop's evaluator keeps refusing sealed splits.
    """

    def __init__(self, domain: Domain, llm: Optional[LLM], *, splits: Sequence[str] = ("holdout", "ood"),
                 k: int = 1, workers: int = 4) -> None:
        self.domain = domain
        self.splits = [s for s in splits if s in domain.tasks.splits]
        self.k = k
        self.ev = Evaluator(domain, llm, workers=workers, allow_sealed=True)
        self.seen: set[str] = set()

    def observe(self, tracer: RunTracer, round: int, name: str, artifact: Artifact,
                decision_score: Optional[float] = None) -> None:
        if artifact.id in self.seen:
            return
        self.seen.add(artifact.id)
        scores = {}
        for s in self.splits:
            r = self.ev.evaluate(artifact, s, self.k)
            scores[s] = {"S": r.score, "C": r.cost, "families": r.family_scores()}
        tracer.event("monitor", round, version=name, artifact=artifact.short_id, decision_score=decision_score,
                     sealed=scores, note="shadow evaluation - never shown to the loop")


# ---------------------------------------------------------------------------- reading
def load_trace(run_dir: str | Path) -> list[dict]:
    p = Path(run_dir) / "trace.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"no trace.jsonl in {run_dir}")
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _block(text: str, lang: str = "", limit: int = 2500) -> str:
    if not text:
        return ""
    t = text if len(text) <= limit else text[:limit] + "\n...[truncated]"
    return f"```{lang}\n{t}\n```\n"


def render_markdown(events: Iterable[dict], *, title: Optional[str] = None, show_prompts: bool = True) -> str:
    """Iteration-by-iteration narrative of a traced run."""
    events = list(events)
    method = events[0]["method"] if events else "?"
    out = [f"# {title or 'Run trace'} ({method})", ""]
    by_round: dict[Any, list[dict]] = {}
    for e in events:
        by_round.setdefault(e.get("round"), []).append(e)
    for r in [None] + sorted(k for k in by_round if k is not None):
        evs = by_round.get(r, [])
        if not evs:
            continue
        out.append("## Setup and summary" if r is None else f"## Round {r}")
        for e in evs:
            k, d = e["kind"], e["data"]
            if k == "run_start":
                out.append(f"**Run start.** seed `{d.get('seed', '?')}`; config: `{json.dumps(d.get('config', {}))[:800]}`")
            elif k == "baseline" or (k == "eval" and d.get("candidate") in ("baseline", "H0", "seed")):
                s = d.get("summary", d)
                out.append(f"**Baseline evaluation** `{d.get('candidate', 'H0')}`: S={_fmt(s.get('S'))}, "
                           f"C={_fmt(s.get('C'))} tokens/trial, n_tasks={s.get('n_tasks')}, k={s.get('k')}")
            elif k == "noise":
                out.append(f"**Noise band.** delta={_fmt(d.get('delta'))} ({d.get('mode')}, z={d.get('z')}); "
                           f"{d.get('detail', '')}")
            elif k == "round_start":
                state = {kk: vv for kk, vv in d.items()}
                out.append(f"**State at round start:** `{json.dumps(state, default=str)[:1200]}`")
            elif k == "analysis":
                out.append("**Analysis of the incumbent's failures/successes:**")
                out.append(_block(str(d.get("text", d)), "", 3000))
            elif k == "proposal":
                out.append(f"### Proposal `{d.get('candidate')}` (parent `{d.get('parent')}`)")
                if d.get("error"):
                    out.append(f"- **error:** {d['error']}")
                out.append(f"- **claimed change:** {d.get('change') or '-'}")
                if d.get("hypothesis"):
                    out.append(f"- **hypothesis:** {d['hypothesis']}")
                if d.get("components"):
                    out.append(f"- **components:** {', '.join(d['components'])}")
                extra = {kk: vv for kk, vv in d.items() if kk not in
                         ("candidate", "parent", "prompt", "reply", "change", "hypothesis", "components", "diff", "error")}
                if extra:
                    out.append(f"- **details:** `{json.dumps(extra, default=str)[:800]}`")
                if show_prompts and d.get("prompt"):
                    out.append("<details><summary>proposer prompt</summary>\n\n" + _block(d["prompt"], "", 4000) +
                               "</details>")
                if show_prompts and d.get("reply"):
                    out.append("<details><summary>proposer reply</summary>\n\n" + _block(d["reply"], "", 4000) +
                               "</details>")
                if d.get("diff"):
                    out.append("**Actual diff:**")
                    out.append(_block(d["diff"], "diff", 4000))
            elif k == "critic":
                verdict = "ACCEPT" if d.get("accept") else "REJECT"
                out.append(f"**Critic on `{d.get('candidate')}`: {verdict}** ({d.get('stage', '')}) "
                           f"{'; '.join(map(str, d.get('objections', [])))[:600]}")
            elif k == "eval":
                s = d.get("summary") or {}
                out.append(f"**Eval `{d.get('candidate')}`** on {s.get('split', '?')}: S={_fmt(s.get('S'))}, "
                           f"C={_fmt(s.get('C'))}, errors={s.get('errors')}, missing={s.get('missing')}")
                pt = d.get("per_task") or {}
                if pt and len(pt) <= 40:
                    out.append("  per-task: " + ", ".join(f"{t}={_fmt(v)}" for t, v in pt.items()))
            elif k == "gate":
                verdict = "ADMISSIBLE" if d.get("accept") else "REJECTED"
                out.append(f"**Gate on `{d.get('candidate')}`: {verdict}** - {d.get('reason')}")
                if d.get("math"):
                    out.append(f"  arithmetic: `{json.dumps(d['math'], default=str)[:900]}`")
            elif k == "decision":
                out.append(f"**Decision:** kept `{d.get('kept')}`; incumbent `{d.get('incumbent_before')}` -> "
                           f"`{d.get('incumbent_after')}`. {d.get('why', '')}")
            elif k == "monitor":
                sealed = d.get("sealed", {})
                parts = [f"{s}: S={_fmt(v.get('S'))}" for s, v in sealed.items()]
                out.append(f"**Shadow monitor (never shown to the loop)** `{d.get('version')}` "
                           f"(decision score {_fmt(d.get('decision_score'))}): " + "; ".join(parts))
            elif k == "state":
                out.append(f"**State after round:** `{json.dumps(d, default=str)[:1500]}`")
            elif k == "run_end":
                out.append(f"**Run end:** `{json.dumps(d, default=str)[:2000]}`")
            else:
                out.append(f"**{k}:** `{json.dumps(d, default=str)[:1500]}`")
            out.append("")
    return "\n".join(out)


def monitor_table(events: Iterable[dict]) -> list[dict]:
    """Rows of (round, version, decision score, sealed split scores) for plots."""
    rows = []
    for e in events:
        if e["kind"] == "monitor":
            d = e["data"]
            row = {"round": e["round"], "version": d.get("version"), "decision_score": d.get("decision_score")}
            for s, v in d.get("sealed", {}).items():
                row[s] = v.get("S")
            rows.append(row)
    return rows


def inspect(run_dir: str | Path, out: Optional[str | Path] = None, show_prompts: bool = True) -> str:
    """Render ``run_dir/trace.jsonl`` to markdown (written to ``out`` or ``run_dir/TRACE.md``)."""
    md = render_markdown(load_trace(run_dir), title=Path(run_dir).name, show_prompts=show_prompts)
    dest = Path(out) if out else Path(run_dir) / "TRACE.md"
    dest.write_text(md)
    return str(dest)
