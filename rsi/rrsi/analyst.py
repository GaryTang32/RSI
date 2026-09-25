"""Analyze(H_t, D_evolve) -> F_t (mirrors ``rrsi/analyst.py`` + ``rrsi/digester.py``).

The analyst works on the incumbent's OWN stored evaluation (no re-run): the worst
trial of the ``n_fail_traces`` lowest-scoring tasks and the best trial of the
``n_success_traces`` highest-scoring ones (:func:`build_traces`). It produces the
three-lens report

* ``failure_modes``    what lost points, clustered across tasks (ranked by n_tasks),
* ``capability_gaps``  what the agent tried to do but could not,
* ``success_habits``   what let passing tasks finish cleanly (the proposer must not break them).

Two implementations share the report schema:

* ``mode="llm"``: read-only *digesters* (one LLM call per trace and lens, the trace
  rendered inline, the code's lens schemas) followed by one aggregation call with the
  code's analyst rules (merge/split, rank, entity-free, stable names, no prescriptions);
* ``mode="heuristic"``: a deterministic, LLM-free analyst that clusters failing traces by
  task family and a de-numbered grader-feedback signature (used offline and as the
  fallback when the LLM output is unusable).
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from ..core.llm import LLM, extract_json
from .evaluate import Measurement

DIGEST_MAX_CHARS = 6000
DIGEST_PARALLELISM = 6

SCHEMAS = {
    "failure": """{"task_id": "...", "lens": "failure",
 "blocker": "one sentence: what mechanism lost the points",
 "narrative": "2-5 sentences: how the failure unfolded, concrete",
 "evidence": [{"where": "step 42", "quote": "short exact quote"}],
 "verifier_evidence": "what the grader/verifier itself reported as missed",
 "capability_note": "optional: anything the agent tried but could not do",
 "needed_instead": "1-2 sentences: what the successful path required"}""",
    "capability_gap": """{"task_id": "...", "lens": "capability_gap",
 "wanted": "what the agent was trying to accomplish",
 "why_couldnt": "what stopped it (tool limits, missing info, dead ends)",
 "evidence": [{"where": "step 12", "quote": "..."}],
 "workaround_seen": "optional: any partial workaround it attempted"}""",
    "success": """{"task_id": "...", "lens": "success",
 "habits": [{"habit": "reusable behavior that made this run clean", "where_shown": "step range"}],
 "risk_if_removed": "which habit is load-bearing and what breaks without it"}""",
}

DIGESTER_SYSTEM = """You are a trajectory digester: a read-only investigator that
inspects ONE agent trajectory in depth and returns a compact structured
digest. Another agent (the batch analyst) will rely on your digest without
reading the trace itself, so be precise and evidence-anchored.

{domain_brief}

Your lens for this assignment: {lens}

Investigate efficiently: read the grading / verifier section first (what
actually failed), then the relevant slices of the trajectory.

Reply with ONE JSON object following this schema, under {cap} characters total:
{schema}

Rules: quotes must be exact and short; every claim needs a "where"; do not
speculate beyond what the trace shows; no blame attribution to "model vs
harness"."""

ANALYST_SYSTEM = """You are the batch analyst in a harness-evolution loop. A frozen
policy LLM, driven by an evolvable scaffold, ran the evolve set of a benchmark;
some trials scored well, some did not. Your job: produce a three-lens analysis
report that a harness engineer will act on. Read-only digester subagents have
already investigated one trace each; their digests are below.

{domain_brief}

Reply with ONE JSON object:
{{"failure_modes": [{{"mode": "snake_case_label", "n_tasks": 0, "affected_tasks": [...],
   "description": "generalized mechanism, entity-free", "needed_instead": "...",
   "representative_evidence": [{{"task_id": "...", "where": "...", "quote": "..."}}]}}],
 "capability_gaps": [{{"gap": "snake_case_label", "n_tasks": 0, "affected_tasks": [...],
   "description": "what the agent could not do and why, entity-free", "representative_evidence": [...]}}],
 "success_habits": [{{"habit": "snake_case_label", "n_tasks": 0,
   "description": "the reusable behavior, entity-free", "risk_if_broken": "what regresses if disrupted"}}]}}

Aggregation rules:
1. MERGE digests describing the same underlying mechanism even if worded
   differently; SPLIT a label that covers two distinct mechanisms.
2. RANK failure_modes by total impact (number of tasks weighted by how much
   score the mode costs on each).
3. Descriptions must be entity-free and task-agnostic (no task names, no
   subject-matter facts, no task-specific values); evidence quotes may contain them.
4. Keep prior mode names when the same mechanism recurs (stable naming).
5. Do NOT prescribe code changes and do NOT attribute blame to model vs scaffold."""


def build_traces(meas: Measurement, n_fail: int, n_success: int) -> dict[str, dict]:
    """Worst trial of the lowest-scoring tasks plus the best trial of the highest-scoring
    ones, from the incumbent's stored evaluation."""
    ranked = sorted(meas.per_task.items(), key=lambda kv: kv[1].mean)
    fails = [t for t, _ in ranked[:n_fail]]
    wins = [t for t, _ in ranked[-n_success:] if t not in fails] if n_success > 0 else []
    out: dict[str, dict] = {}
    for tid in fails + wins:
        trs = meas.trials.get(tid) or []
        if not trs:
            continue
        pick = min(trs, key=lambda d: d.get("score", 0.0)) if tid in fails else max(trs, key=lambda d: d.get("score", 0.0))
        out[tid] = dict(pick, _role="fail" if tid in fails else "win")
    return out


def render_trace(rec: dict, task_input=None, cap: int = 6000) -> str:
    """What digesters and the proposer read about one trial."""
    parts = [f"=== TASK {rec.get('task_id')} (family={rec.get('family')}, seed={rec.get('seed')}) ==="]
    if task_input is not None:
        parts.append(f"INPUT: {str(task_input)[:1500]}")
    tr = str(rec.get("trace") or "")
    if tr:
        parts.append("=== TRAJECTORY ===\n" + tr[: max(500, cap - 2500)])
    parts.append(f"=== OUTPUT ===\n{str(rec.get('output'))[:800]}")
    parts.append(f"=== GRADING / VERIFIER ===\nscore={rec.get('score')} tokens={rec.get('tokens')} "
                 f"steps={rec.get('steps')} error={rec.get('error')}\nfeedback: {str(rec.get('feedback'))[:800]}")
    return "\n".join(parts)[:cap]


def task_row(tid: str, rec: dict, mean: Optional[float]) -> str:
    return (f"{tid} | mean={mean if mean is None else round(mean, 3)} | family={rec.get('family')} | "
            f"score={rec.get('score')} | tokens={rec.get('tokens')} | {str(rec.get('feedback'))[:100]}")


_NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def signature(text: str) -> str:
    """De-numbered, de-quoted first line of grader feedback / error (entity-free)."""
    s = (text or "").strip().splitlines()[0] if (text or "").strip() else "no feedback"
    s = _QUOTED.sub("<v>", s)
    s = _NUM.sub("#", s)
    return re.sub(r"\s+", " ", s)[:160]


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:48] or "unknown"


class Analyst:
    """Builds F_t from the incumbent's traces."""

    def __init__(self, llm: Optional[LLM] = None, *, mode: str = "heuristic", domain_brief: str = "",
                 max_digests: int = 8, workers: int = DIGEST_PARALLELISM) -> None:
        self.llm = llm
        self.mode = mode if (mode != "llm" or llm is not None) else "heuristic"
        self.domain_brief = domain_brief
        self.max_digests = max_digests
        self.workers = workers
        #: LLM exchanges of the latest :meth:`analyze` call (role, task, lens, prompt, reply) - for the run trace
        self.last_calls: list[dict] = []

    # ------------------------------------------------------------------ heuristic
    def heuristic_digests(self, traces: dict[str, dict]) -> list[dict]:
        out = []
        for tid, rec in traces.items():
            if rec.get("_role") == "fail" and rec.get("error") and not str(rec.get("error")).startswith("infra:"):
                out.append({"task_id": tid, "lens": "capability_gap", "family": rec.get("family"),
                            "wanted": "complete the task", "why_couldnt": signature(str(rec.get("error"))),
                            "evidence": [{"where": "execution", "quote": str(rec.get("error"))[:160]}]})
            elif rec.get("_role") == "fail":
                out.append({"task_id": tid, "lens": "failure", "family": rec.get("family"),
                            "blocker": signature(str(rec.get("feedback"))),
                            "narrative": str(rec.get("feedback"))[:300],
                            "evidence": [{"where": "grader", "quote": str(rec.get("feedback"))[:160]}],
                            "needed_instead": ""})
            else:
                out.append({"task_id": tid, "lens": "success", "family": rec.get("family"),
                            "habits": [{"habit": f"clean_{rec.get('family')}", "where_shown": "final"}],
                            "risk_if_removed": "a change that disrupts this path regresses passing tasks"})
        return out

    @staticmethod
    def aggregate_heuristic(digests: list[dict], scores: dict[str, float]) -> dict:
        modes: dict[str, dict] = {}
        gaps: dict[str, dict] = {}
        habits: dict[str, dict] = {}
        for d in digests:
            fam = d.get("family") or "default"
            if d["lens"] == "failure":
                key = f"{_slug(str(fam))}__{_slug(d['blocker'])}"
                m = modes.setdefault(key, {"mode": key, "n_tasks": 0, "affected_tasks": [], "loss": 0.0,
                                           "description": f"tasks of kind '{fam}' end with: {d['blocker']}",
                                           "needed_instead": "a general mechanism that fixes this outcome",
                                           "representative_evidence": []})
                m["n_tasks"] += 1
                m["affected_tasks"].append(d["task_id"])
                m["loss"] += 1.0 - scores.get(d["task_id"], 0.0)
                if len(m["representative_evidence"]) < 3:
                    m["representative_evidence"].append({"task_id": d["task_id"], **d["evidence"][0]})
            elif d["lens"] == "capability_gap":
                key = _slug(d["why_couldnt"])
                g = gaps.setdefault(key, {"gap": key, "n_tasks": 0, "affected_tasks": [],
                                          "description": f"execution failed: {d['why_couldnt']}",
                                          "representative_evidence": []})
                g["n_tasks"] += 1
                g["affected_tasks"].append(d["task_id"])
                if len(g["representative_evidence"]) < 3:
                    g["representative_evidence"].append({"task_id": d["task_id"], **d["evidence"][0]})
            else:
                key = f"clean_{_slug(str(fam))}"
                h = habits.setdefault(key, {"habit": key, "n_tasks": 0,
                                            "description": f"passing tasks of kind '{fam}' finish cleanly",
                                            "risk_if_broken": "regressions on currently solved tasks"})
                h["n_tasks"] += 1
        fm = sorted(modes.values(), key=lambda m: (-m["n_tasks"], -m["loss"], m["mode"]))
        for m in fm:
            m["loss"] = round(m["loss"], 4)
        return {"failure_modes": fm, "capability_gaps": sorted(gaps.values(), key=lambda g: -g["n_tasks"]),
                "success_habits": sorted(habits.values(), key=lambda h: -h["n_tasks"]), "n_digests": len(digests),
                "analyst": "heuristic"}

    # ------------------------------------------------------------------------ LLM
    def _digest(self, tid: str, rec: dict, lens: str, task_input, seed: int) -> dict:
        system = DIGESTER_SYSTEM.format(domain_brief=self.domain_brief, lens=lens, cap=DIGEST_MAX_CHARS,
                                        schema=SCHEMAS[lens])
        prompt = f"Digest this trajectory ({lens} lens).\n\n{render_trace(rec, task_input)}\n\nReply with the JSON digest."
        resp = self.llm.complete(prompt, system=system, seed=seed, role="digester")
        self.last_calls.append({"role": "digester", "task_id": tid, "lens": lens, "system": system, "prompt": prompt,
                                "reply": resp.text, "error": resp.error})
        try:
            d = extract_json(resp.text)
            if isinstance(d, dict):
                d["task_id"], d["lens"] = tid, lens
                d.setdefault("family", rec.get("family"))
                s = json.dumps(d, default=str)
                if len(s) > DIGEST_MAX_CHARS:     # the code bounces over-long digests; we truncate
                    d = {"task_id": tid, "lens": lens, "family": rec.get("family"),
                         "blocker": s[: DIGEST_MAX_CHARS // 2], "truncated": True}
                return d
        except ValueError:
            pass
        return {"task_id": tid, "lens": lens, "family": rec.get("family"), "error": "unparseable digest"}

    def analyze(self, traces: dict[str, dict], meas: Measurement, task_inputs: Optional[dict] = None,
                prior: Optional[dict] = None, seed: int = 0) -> tuple[dict, list[dict]]:
        """Return (report F_t, digests)."""
        scores = meas.task_means()
        self.last_calls = []
        heur_digests = self.heuristic_digests(traces)
        if self.mode != "llm":
            return self.aggregate_heuristic(heur_digests, scores), heur_digests
        task_inputs = task_inputs or {}
        fails = [t for t, r in traces.items() if r.get("_role") == "fail"]
        wins = [t for t, r in traces.items() if r.get("_role") != "fail"]
        n_win = min(len(wins), max(1, self.max_digests // 4))
        reqs = [(t, "failure") for t in fails[: self.max_digests - n_win]] + [(t, "success") for t in wins[:n_win]]
        with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
            digests = list(ex.map(lambda tl: self._digest(tl[0], traces[tl[0]], tl[1], task_inputs.get(tl[0]),
                                                          seed * 100 + reqs.index(tl)), reqs))
        table = "\n".join(task_row(t, traces[t], scores.get(t)) for t in sorted(traces, key=lambda x: scores.get(x, 0)))
        prompt = "\n\n".join([
            "=== TASK TABLE (one trace per task; lowest scores first) ===", table,
            "=== PRIOR FAILURE-MODE NAMES (for stable naming) ===",
            json.dumps([{"mode": m.get("mode"), "description": m.get("description")}
                        for m in (prior or {}).get("failure_modes") or []], indent=1),
            "=== PRIOR SUCCESS-HABIT NAMES ===",
            json.dumps([{"habit": h.get("habit")} for h in (prior or {}).get("success_habits") or []], indent=1),
            "=== DIGESTS ===", json.dumps(digests, ensure_ascii=False, indent=1)[:60000],
            "Produce the report JSON now."])
        system = ANALYST_SYSTEM.format(domain_brief=self.domain_brief)
        resp = self.llm.complete(prompt, system=system, seed=seed, role="analyst")
        self.last_calls.append({"role": "analyst", "system": system, "prompt": prompt, "reply": resp.text,
                                "error": resp.error})
        try:
            rep = extract_json(resp.text)
            if not isinstance(rep, dict):
                raise ValueError("not an object")
            report = {k: rep.get(k) or [] for k in ("failure_modes", "capability_gaps", "success_habits")}
            if not all(isinstance(report[k], list) for k in ("failure_modes", "capability_gaps", "success_habits")):
                raise ValueError("report sections are not lists")
            report["failure_modes"] = [m for m in report["failure_modes"] if isinstance(m, dict)]

            def _n(m):
                try:
                    return -float(m.get("n_tasks") or 0)
                except (TypeError, ValueError):     # an LLM wrote "n_tasks": "many"
                    return 0.0
            report["failure_modes"].sort(key=_n)
            report["n_digests"] = len(digests)
            report["analyst"] = "llm"
            return report, digests
        except ValueError as e:
            report = self.aggregate_heuristic(heur_digests, scores)
            report["error"] = f"analyst output unusable ({e}); heuristic fallback"
            return report, digests
