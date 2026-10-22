"""Append-only iteration trace (``trace.jsonl``), a write-only shadow monitor on sealed splits, and its renderer."""
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
#: fields an auditor needs verbatim (the exact prompt a proposer saw, its reply, the diff)
LONG_FIELDS = ("prompt", "reply", "diff", "code", "text")
MAX_LONG_TEXT = 60000


def _clip(x: Any, n: int = MAX_TEXT, long_n: int = MAX_LONG_TEXT, key: str = "") -> Any:
    if isinstance(x, str):
        lim = long_n if key in LONG_FIELDS else n
        return x if len(x) <= lim else x[:lim] + f"\n...[truncated {len(x) - lim} chars]"
    if isinstance(x, dict):
        return {k: _clip(v, n, long_n, str(k)) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clip(v, n, long_n, key) for v in x]
    return x


class RunTracer:
    """Append-only iteration trace; a no-op when ``out_dir`` is None."""

    def __init__(self, out_dir: Optional[str | Path], method: str, *, max_text: int = MAX_TEXT,
                 max_long_text: int = MAX_LONG_TEXT) -> None:
        self.method = method
        self.max_text = max_text
        self.max_long_text = max_long_text
        self.path = Path(out_dir) / "trace.jsonl" if out_dir else None
        self._seq = 0
        self._lock = threading.Lock()
        self.monitor: Optional["ShadowMonitor"] = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                with self.path.open() as f:
                    self._seq = sum(1 for _ in f)

    @property
    def enabled(self) -> bool:
        return self.path is not None

    def event(self, kind: str, round: Optional[int] = None, **data: Any) -> None:
        if not self.path:
            return
        with self._lock:
            rec = {"seq": self._seq, "t": time.time(), "method": self.method, "round": round, "kind": kind,
                   "data": _clip(data, self.max_text, self.max_long_text)}
            self._seq += 1
            with self.path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    def proposal(self, round: int, candidate: str, *, parent: Optional[str], prompt: str = "", reply: str = "",
                 change: str = "", hypothesis: str = "", components: Sequence[str] = (), diff: str = "",
                 error: Optional[str] = None, **extra: Any) -> None:
        self.event("proposal", round, candidate=candidate, parent=parent, prompt=prompt, reply=reply, change=change,
                   hypothesis=hypothesis, components=list(components), diff=diff, error=error, **extra)

    def evaluation(self, round: Optional[int], candidate: str, result: Any, **extra: Any) -> None:
        """Record an ``EvalResult`` (summary, per-task scores, trial scores) or any other ``result`` value."""
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
    """Scores each kept version on sealed splits into the trace only; the loop never sees the numbers."""

    def __init__(self, domain: Domain, llm: Optional[LLM], *, splits: Sequence[str] = ("holdout", "ood"),
                 k: int = 1, workers: int = 4) -> None:
        self.domain = domain
        self.splits = [s for s in splits if s in domain.tasks.splits]
        self.k = k
        self.ev = Evaluator(domain, llm, workers=workers, allow_sealed=True)
        self.seen: set[str] = set()
        # loops with wall-clock budgets credit this back, so auditing never shortens the run
        self.elapsed_s = 0.0
        self.last_elapsed_s = 0.0

    def observe(self, tracer: RunTracer, round: int, name: str, artifact: Artifact,
                decision_score: Optional[float] = None) -> None:
        if artifact.id in self.seen:
            self.last_elapsed_s = 0.0
            return
        self.seen.add(artifact.id)
        t0 = time.time()
        scores = {}
        for s in self.splits:
            r = self.ev.evaluate(artifact, s, self.k)
            scores[s] = {"S": r.score, "C": r.cost, "families": r.family_scores(), "missing": r.n_missing}
        self.last_elapsed_s = time.time() - t0
        self.elapsed_s += self.last_elapsed_s
        tracer.event("monitor", round, version=name, artifact=artifact.short_id, decision_score=decision_score,
                     sealed=scores, elapsed_s=float(f"{self.last_elapsed_s:.3f}"),
                     note="shadow evaluation - never shown to the loop")


def load_trace(run_dir: str | Path) -> list[dict]:
    p = Path(run_dir) / "trace.jsonl"
    if not p.exists():
        raise FileNotFoundError(f"no trace.jsonl in {run_dir}")
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def _fmt(v: Any) -> str:
    return f"{v:.4f}" if isinstance(v, float) else str(v)


def _block(text: str, lang: str, limit: int) -> str:
    if not text:
        return ""
    t = text if len(text) <= limit else text[:limit] + "\n...[truncated]"
    return f"```{lang}\n{t}\n```\n"


def _per_task(d: dict) -> list[str]:
    pt = d.get("per_task") or {}
    return ["  per-task: " + ", ".join(f"{t}={_fmt(v)}" for t, v in pt.items())] if pt and len(pt) <= 40 else []


def _dump(d: Any, n: int) -> str:
    return json.dumps(d, default=str)[:n]


_PROPOSAL_KEYS = ("candidate", "parent", "prompt", "reply", "change", "hypothesis", "components", "diff", "error")


def _render_proposal(d: dict, show_prompts: bool) -> list[str]:
    out = [f"### Proposal `{d.get('candidate')}` (parent `{d.get('parent')}`)"]
    if d.get("error"):
        out.append(f"- **error:** {d['error']}")
    out.append(f"- **claimed change:** {d.get('change') or '-'}")
    if d.get("hypothesis"):
        out.append(f"- **hypothesis:** {d['hypothesis']}")
    if d.get("components"):
        out.append(f"- **components:** {', '.join(d['components'])}")
    extra = {k: v for k, v in d.items() if k not in _PROPOSAL_KEYS}
    if extra:
        out.append(f"- **details:** `{_dump(extra, 800)}`")
    if show_prompts and d.get("prompt"):
        out.append("<details><summary>proposer prompt</summary>\n\n" + _block(d["prompt"], "", 4000) + "</details>")
    if show_prompts and d.get("reply"):
        out.append("<details><summary>proposer reply</summary>\n\n" + _block(d["reply"], "", 4000) + "</details>")
    if d.get("diff"):
        out += ["**Actual diff:**", _block(d["diff"], "diff", 4000)]
    return out


def _render_event(k: str, d: dict, show_prompts: bool) -> list[str]:
    if k == "run_start":
        return [f"**Run start.** seed `{d.get('seed', '?')}`; config: `{json.dumps(d.get('config', {}))[:800]}`"]
    if k == "baseline" or (k == "eval" and d.get("candidate") in ("baseline", "H0", "seed")):
        s = d.get("summary", d)
        return [f"**Baseline evaluation** `{d.get('candidate', 'H0')}`: S={_fmt(s.get('S'))}, "
                f"C={_fmt(s.get('C'))} tokens/trial, n_tasks={s.get('n_tasks')}, k={s.get('k')}"] + _per_task(d)
    if k == "noise":
        return [f"**Noise band.** delta={_fmt(d.get('delta'))} ({d.get('mode')}, z={d.get('z')}); "
                f"{d.get('detail', '')}"]
    if k == "round_start":
        return [f"**State at round start:** `{_dump(d, 1200)}`"]
    if k == "analysis":
        return ["**Analysis of the incumbent's failures/successes:**", _block(str(d.get("text", d)), "", 3000)]
    if k == "proposal":
        return _render_proposal(d, show_prompts)
    if k == "critic":
        verdict = "ACCEPT" if d.get("accept") else "REJECT"
        return [f"**Critic on `{d.get('candidate')}`: {verdict}** ({d.get('stage', '')}) "
                f"{'; '.join(map(str, d.get('objections', [])))[:600]}"]
    if k == "eval" and not d.get("summary"):
        rest = {kk: vv for kk, vv in d.items() if kk != "candidate"}
        return [f"**Eval `{d.get('candidate')}`:** `{_dump(rest, 900)}`"]
    if k == "eval":
        s = d.get("summary") or {}
        return [f"**Eval `{d.get('candidate')}`** on {s.get('split', '?')}: S={_fmt(s.get('S'))}, "
                f"C={_fmt(s.get('C'))}, errors={s.get('errors')}, missing={s.get('missing')}"] + _per_task(d)
    if k == "gate":
        verdict = "ADMISSIBLE" if d.get("accept") else "REJECTED"
        out = [f"**Gate on `{d.get('candidate')}`: {verdict}** - {d.get('reason')}"]
        return out + ([f"  arithmetic: `{_dump(d['math'], 900)}`"] if d.get("math") else [])
    if k == "decision":
        return [f"**Decision:** kept `{d.get('kept')}`; incumbent `{d.get('incumbent_before')}` -> "
                f"`{d.get('incumbent_after')}`. {d.get('why', '')}"]
    if k == "monitor":
        parts = [f"{s}: S={_fmt(v.get('S'))}" for s, v in d.get("sealed", {}).items()]
        return [f"**Shadow monitor (never shown to the loop)** `{d.get('version')}` "
                f"(decision score {_fmt(d.get('decision_score'))}): " + "; ".join(parts)]
    if k == "state":
        return [f"**State after round:** `{_dump(d, 1500)}`"]
    if k == "run_end":
        return [f"**Run end:** `{_dump(d, 2000)}`"]
    if k == "note" and isinstance(d.get("what"), str):
        rest = {kk: vv for kk, vv in d.items() if kk != "what"}
        return [f"- *{d['what']}*: `{_dump(rest, 700)}`"]
    return [f"**{k}:** `{_dump(d, 1500)}`"]


def render_markdown(events: Iterable[dict], *, title: Optional[str] = None, show_prompts: bool = True) -> str:
    """Iteration-by-iteration narrative of a traced run."""
    events = list(events)
    method = events[0]["method"] if events else "?"
    out = [f"# {title or 'Run trace'} ({method})", ""]
    starts = [e["seq"] for e in events if e["kind"] == "run_start"]
    if len(starts) > 1:
        out.append(f"> This trace has {len(starts)} segments (the run was resumed). Events are grouped by round; "
                   "a round that appears twice was interrupted and re-run - the later events are the settled ones.\n")
    by_round: dict[Any, list[dict]] = {}
    for e in events:
        if e["kind"] != "run_end":
            by_round.setdefault(e.get("round"), []).append(e)
    groups = [("## Setup", by_round.get(None, []))]
    groups += [(f"## Round {r}", by_round[r]) for r in sorted(k for k in by_round if k is not None)]
    groups.append(("## Summary", [e for e in events if e["kind"] == "run_end"]))
    for heading, evs in groups:
        if not evs:
            continue
        out.append(heading)
        for e in evs:
            out += _render_event(e["kind"], e["data"], show_prompts)
            out.append("")
    return "\n".join(out)


def monitor_table(events: Iterable[dict]) -> list[dict]:
    """Rows of (round, version, decision score, sealed split scores) for plots."""
    return [{"round": e["round"], "version": e["data"].get("version"),
             "decision_score": e["data"].get("decision_score"),
             **{s: v.get("S") for s, v in e["data"].get("sealed", {}).items()}}
            for e in events if e["kind"] == "monitor"]


def inspect(run_dir: str | Path, out: Optional[str | Path] = None, show_prompts: bool = True) -> str:
    """Render ``run_dir/trace.jsonl`` to markdown (written to ``out`` or ``run_dir/TRACE.md``)."""
    md = render_markdown(load_trace(run_dir), title=Path(run_dir).name, show_prompts=show_prompts)
    dest = Path(out) if out else Path(run_dir) / "TRACE.md"
    dest.write_text(md)
    return str(dest)
