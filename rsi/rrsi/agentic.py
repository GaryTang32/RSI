"""Faithful ports of the released code's agentic protocols (opt-in; claims M4 and M28).

The default RRSI plumbing is single-shot: one proposer call returns whole files plus the done()
object (:class:`rsi.rrsi.propose.Proposer`), and one digester call per trace (the trace inline)
plus one aggregation call build F_t (:class:`rsi.rrsi.analyst.Analyst`). The released code instead
runs multi-turn, strict-JSON tool loops. This module ports them line by line:

* :class:`JsonActionProposer` = ``rrsi/propose.py::propose``: the code's SYSTEM_TMPL, context sections
  and interaction log; actions ``list_files`` / ``read_file`` / ``list_traces`` / ``read_trace`` /
  ``edit_file`` / ``write_file`` / ``done`` (and the bounced ``abort``); ``MAX_TURNS = 40``,
  ``MAX_EDITS = 80``, ``TRACE_READ_CAP = 60,000``, result caps 150,000 / 4,000 characters; the done()
  contract checked exactly as the code does (zero edit actions, over budget, missing fields, component
  outside K, reserved slot), "there is no abort action" (3 bounces). Select it with
  ``Config(proposer_protocol="json_actions")``.
* :class:`AgenticAnalyst` = ``rrsi/analyst.py::analyze`` + ``rrsi/digester.py::digest_task``: the batch
  analyst never reads traces; it dispatches read-only digesters (``digest_many``, up to 8 per call, 6 in
  parallel, ``MAX_TURNS = 30``), each an agent over the round's rendered traces with ``read_file`` /
  ``glob`` / ``grep`` / an allow-listed read-only ``bash`` (``MAX_TURNS = 15``, digests capped at 6,000
  characters, tool output at 25,000). Select it with ``Config(analyst="agentic")``.

Backend adaptation (documented, not a protocol change): the code's ``llm.generate(json_only=True)`` appends
the same JSON-only suffix to the system prompt and returns ``extract_json(text)``; :func:`generate` does the
same over any :class:`rsi.core.LLM`. The code sends ``cache_prefix`` (constitution) as a separate cached
content block before the prompt; over a single-string backend it is prepended with a blank line. The
differential test ``tests/rrsi/test_rrsi_agentic.py`` runs the reference implementation and this port on the
same scripted replies and checks identical prompts, transcripts, file results and outcomes.
"""
from __future__ import annotations

import json
import posixpath
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from ..core.artifact import Artifact
from ..core.llm import LLM, Usage
from .analyst import render_trace, task_row
from .components import Taxonomy

# ------------------------------------------------------------------------------------------ generate
JSON_SUFFIX = ("\n\nOutput ONLY a single valid JSON object. No prose before or "
               "after, no markdown fences.")


def ref_extract_json(text: str) -> str:
    """The code's ``llm.extract_json``: the JSON-looking span of a reply, as a string."""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    if not t.startswith("{") and not t.startswith("["):
        start = min([i for i in (t.find("{"), t.find("[")) if i != -1], default=-1)
        if start != -1:
            t = t[start:]
    if t and t[0] == "{" and not t.endswith("}"):
        end = t.rfind("}")
        if end != -1:
            t = t[:end + 1]
    if t and t[0] == "[" and not t.endswith("]"):
        end = t.rfind("]")
        if end != -1:
            t = t[:end + 1]
    return t


class GenerateError(RuntimeError):
    """The backend failed (the code's ``generate`` raises after its retries)."""


def generate(llm: LLM, prompt: str, *, system: Optional[str] = None, json_only: bool = False,
             cache_prefix: Optional[str] = None, seed: int = 0, role: str = "default",
             usage: Optional[list] = None) -> str:
    """The code's ``llm.generate`` over an :class:`rsi.core.LLM`."""
    sys_prompt = (system or "") + (JSON_SUFFIX if json_only else "")
    full = (cache_prefix + "\n\n" + prompt) if cache_prefix else prompt
    resp = llm.complete(full, system=sys_prompt, seed=seed, role=role)
    if usage is not None:
        usage.append(resp.usage)
    if not resp.ok or not resp.text:
        raise GenerateError(resp.error or "empty response")
    return ref_extract_json(resp.text) if json_only else resp.text


def _sum(us: list) -> Usage:
    tot = Usage()
    for u in us:
        tot = tot + u
    return tot


# ------------------------------------------------------------------------------------------ proposer
MAX_TURNS = 40
MAX_EDITS = 80
TRACE_READ_CAP = 60_000
DEFAULT_SOURCE_EXTS = (".py", ".txt", ".md", ".json")

#: rrsi/propose.py::SYSTEM_TMPL, verbatim
PROPOSER_SYSTEM_TMPL = """You are a harness engineer agent. You directly modify the source
code of an LLM-agent scaffold (the "harness") to fix recurring failure modes
observed on an evolve set of tasks. The policy LLM is frozen and is a DIFFERENT
model from you: do not assume it shares your capabilities, habits or judgment.
Improve the harness from ITS perspective, using the trajectories as evidence of
how it actually behaves. The ONLY thing you can change is the scaffold code in
your working directory; the tool environment, the grader and the task set are
frozen.

{domain_brief}

You interact through a STRICT JSON protocol: reply with EXACTLY ONE action
object per turn, nothing else. Actions:
  {{"action": "list_files"}}
  {{"action": "read_file", "path": "relative/path"}}
  {{"action": "list_traces"}}
      (this round's traces: task_id, score, status, steps, modes hit)
  {{"action": "read_trace", "task_id": "<id from the task list>", "from_step": 30,
   "to_step": 60, "detail": true}}
      (READ-ONLY rendered trajectory segment: the raw evidence behind the
      failure modes. Step range optional. "detail": true expands per-message
      caps so you can see what tool results actually contained. The grading
      verdict is always included.)
  {{"action": "edit_file", "path": "p", "old": "exact substring", "new": "replacement"}}
      (old must occur EXACTLY ONCE in the file)
  {{"action": "write_file", "path": "p", "content": "full file content"}}
      (for NEW files only; never overwrite an existing file this way. Paths are
      relative to the harness package. A new module must be imported from the
      entry module to do anything, and it IS included in the reviewed diff.)
  {{"action": "done", "summary": "one-line summary of this candidate",
   "edits": [
     {{"id": "C1",
      "component": "one of: {components}",
      "hypothesis": "one sentence: the mechanism and WHY it should move the score",
      "targets_mode": "failure mode / capability gap / habit it targets",
      "why_not_lower_lever": "why a plain instruction edit would NOT fix this
          (or, for a prompt edit, why prose IS the right lever here)",
      "trigger_condition": "the exact, checkable condition under which the
          mechanism activates ('always' is almost never right)",
      "predicted_affected": ["<task id>", "..."],
      "retroactive_check": "three-part counterfactual: (corrective) which cited
          failing tasks would have moved had this existed, walking the actual
          trajectory; (preservative) which success habits could this disrupt
          and why it won't; (transfer) why it generalizes beyond this evolve set",
      "regression_risk": "what could break outside predicted_affected"}},
     ...]}}

THERE IS NO ABORT ACTION. You must ship a candidate. A round that ships
nothing tests nothing: the history records what was MEASURED, and a mechanism
you declined to build has no measurement behind it. If your best idea violates
a hard rule, it is not your best idea; construct a different one, preferably on
a component the history shows was never exercised.

done() contract:
- An edit = ONE independent, attributable change (it works on its own and can
  answer "which tasks will it move" by itself). Dependent parts are ONE edit.
  Ship at most THIS ROUND'S EDIT BUDGET b_t given in the context, never more.
  Ship fewer if the evidence supports fewer.
- Every edit names its `component` from the fixed vocabulary above. The tag
  is validated against the diff; a mislabelled edit is re-tagged from the diff.
- If the context says a RESERVED EXPLORATION SLOT applies to you, at least one
  edit must be on one of the listed never-exercised components.
- If the context lists COMPONENTS TO PRUNE that hold accepted machinery, an
  edit that removes that machinery is a legitimate edit (component = the
  pruned component, hypothesis = "prune: ..."). Prefer it when the evidence
  says the machinery stopped earning its place.
- predicted_affected lists CONCRETE task ids from this round's traces.
  Predictions are checked against the evaluation and your hit/miss record
  (scoreboard) is shown back to you; over-claiming counts against you.

You must follow the constitution (SKILL.md) in the context: no task-specific
entities, names or values anywhere in code, prompts or state; every edit
targets a reported failure mode, capability gap or habit; keep each edit's
diff scoped to its mechanism; the code runs unattended on every task in the
set, so an unhandled exception kills the whole candidate. The interface
contract (module entry points, class names, trajectory output schema) stays
unchanged; model name, step budget and timeouts are injected externally so
editing them has no effect. All code and comments in English."""


def _path_key(rel: str) -> tuple:
    """Sort key matching ``sorted(Path.rglob("*"))`` (by path parts, not by string)."""
    return tuple(rel.split("/"))


class MemWorkspace:
    """The code's path-jailed ``Workspace``, over an in-memory file map (artifact files)."""

    def __init__(self, files: dict[str, str], source_exts=DEFAULT_SOURCE_EXTS, root_name: str = "harness") -> None:
        self.files = dict(files)
        self.exts = set(source_exts)
        self.root = root_name

    def _safe(self, rel: str) -> str:
        rel = str(rel).lstrip("/")
        if rel == self.root or rel.startswith(self.root + "/"):
            rel = rel[len(self.root):].lstrip("/")
        norm = posixpath.normpath(rel) if rel else "."
        if norm == ".." or norm.startswith("../") or norm.startswith("/"):
            raise ValueError(f"path escapes harness dir: {rel}")
        return norm

    def list_files(self) -> str:
        return "\n".join(f"{p} ({len(self.files[p].encode())}B)" for p in sorted(self.files, key=_path_key)
                         if "__pycache__" not in p.split("/"))

    def read(self, rel: str) -> str:
        p = self._safe(rel)
        return self.files[p] if p in self.files else f"ERROR: no such file {rel}"

    def edit(self, rel: str, old: str, new: str) -> str:
        p = self._safe(rel)
        if p not in self.files:
            return f"ERROR: no such file {rel}"
        src = self.files[p]
        n = src.count(old)
        if n == 0:
            return "ERROR: old string not found (must match exactly, including whitespace)"
        if n > 1:
            return f"ERROR: old string occurs {n} times; provide a longer unique context"
        self.files[p] = src.replace(old, new, 1)
        return f"OK: edited {rel}"

    def write(self, rel: str, content: str) -> str:
        p = self._safe(rel)
        if posixpath.splitext(p)[1] not in self.exts:
            return f"ERROR: extension {posixpath.splitext(p)[1]} not allowed"
        if p in self.files:
            return f"ERROR: {rel} exists; use edit_file"
        self.files[p] = content
        return f"OK: created {rel}"

    def dump(self) -> str:
        parts = []
        for p in sorted(self.files, key=_path_key):
            if posixpath.splitext(p)[1] in self.exts and "__pycache__" not in p.split("/"):
                parts.append(f"===== FILE: {p} =====\n{self.files[p]}")
        return "\n\n".join(parts)


def list_traces_text(traces: dict, task_means: dict, findings: list,
                     row: Callable = None) -> str:
    """The code's ``_list_traces``: one row per trace, lowest mean first, with the modes the digests saw."""
    row = row or (lambda tid, rec, mean: task_row(tid, rec, mean))
    modes_by_task: dict = {}
    for f in findings or []:
        if not isinstance(f, dict):
            continue
        label = f.get("blocker") or f.get("wanted") or f.get("lens") or "?"
        modes_by_task.setdefault(str(f.get("task_id")), []).append(str(label)[:80])
    rows = []
    for tid, rec in traces.items():
        mean = task_means.get(tid)
        rows.append((mean if mean is not None else 0.0, row(tid, rec, mean) + f" | modes={modes_by_task.get(tid, [])}"))
    return "\n".join(r for _, r in sorted(rows, key=lambda x: x[0])) or "ERROR: no traces available"


def read_trace_text(traces: dict, task_id, from_step, to_step, cache: dict, detail: bool,
                    render: Callable) -> str:
    """The code's ``_read_trace``: a rendered trajectory, optionally a step range (grading always kept)."""
    task_id = str(task_id)
    rec = traces.get(task_id)
    if rec is None:
        return f"ERROR: no trace for {task_id} (use list_traces for valid ids)"
    key = (task_id, bool(detail))
    if key not in cache:
        cache[key] = render(rec, bool(detail))
    rendered = cache[key]
    if from_step is None and to_step is None:
        return rendered[:TRACE_READ_CAP] + (
            f"\n...[capped at {TRACE_READ_CAP} chars; use from_step/to_step]"
            if len(rendered) > TRACE_READ_CAP else "")
    lo, hi = int(from_step or 0), int(to_step or 10**9)
    keep, in_grades = [], False
    for line in rendered.splitlines():
        if line.startswith("=== ") and ("GRAD" in line or "VERIFIER" in line):
            in_grades = True
        m = re.match(r"\[step (\d+)\]", line)
        if in_grades or m is None or lo <= int(m.group(1)) <= hi:
            keep.append(line)
    out = "\n".join(keep)
    return out[:TRACE_READ_CAP] + (f"\n...[capped at {TRACE_READ_CAP} chars]" if len(out) > TRACE_READ_CAP else "")


def build_proposer_context(*, ws: MemWorkspace, report: dict, history_rows: list, budget: int, explore: dict,
                           reserved: bool, prune_set: list, findings: list, scoreboard: list, variant_brief: str,
                           repair_brief: Optional[dict]) -> str:
    """The code's ROUND CONTEXT, section by section."""
    explore_text = explore.get("text", "")
    if reserved:
        explore_text += ("\n\nRESERVED EXPLORATION SLOT: this variant holds one. At "
                         "least one of your edits MUST be on a never-exercised "
                         f"component from: {explore.get('untried')}.")
    prune_text = (json.dumps(prune_set, ensure_ascii=False, indent=1) if prune_set else "(none)")
    return "\n\n".join([
        *(["=== THIS VARIANT'S BRIEF ===", variant_brief] if variant_brief else []),
        "=== EDIT HISTORY L_t (every measured edit: component, hypothesis, "
        "Delta S, Delta C, accepted). A rejected mechanism is negative evidence; "
        "do not redraw it unchanged. An accepted one carries the gain it "
        "produced; refine what has known credit, not what merely preceded a "
        "rise. ===",
        json.dumps(history_rows, ensure_ascii=False, indent=1),
        "=== ATTRIBUTION SCOREBOARD (how past edits' predictions fared; "
        "unpredicted_regressions are tasks an edit likely broke) ===",
        json.dumps(scoreboard or [], ensure_ascii=False, indent=1),
        "=== EXPLORATION DIRECTIVES E_t ===", explore_text,
        "=== COMPONENTS TO PRUNE B_t (exercised, no strictly improving edit in "
        "the recent window; remove the accepted machinery listed, it has "
        "stopped earning its place) ===", prune_text,
        "=== THREE-LENS ANALYSIS REPORT F_t (failure modes ranked; capability "
        "gaps often need tool/plumbing fixes; success_habits are behaviors "
        "your change MUST NOT break) ===",
        json.dumps(report, ensure_ascii=False, indent=1),
        "=== PER-TASK DIGESTS (evidence anchors; use read_trace for raw evidence) ===",
        json.dumps(findings or [], ensure_ascii=False, indent=1),
        "=== CURRENT HARNESS SOURCE H_t ===", ws.dump(),
        "=== THIS ROUND'S EDIT BUDGET b_t ===",
        f"You may ship AT MOST {budget} independent edit(s) in this candidate "
        f"(the budget anneals over the run: early rounds explore, late rounds "
        f"make single attributable changes). Ship fewer if the evidence "
        f"supports fewer.",
        "=== TASK ===",
        ("REPAIR ROUND: your previous edits for this candidate are already in "
         "the working tree (reflected in CURRENT HARNESS SOURCE above). The "
         "reviewer raised the objections below. Fix ONLY what the objections "
         "require (remove leaked content, split or re-declare edits, wire up "
         "dead code, or delete the offending part) with minimal additional "
         "edits, then call done again with the corrected edits array.\n\n"
         "=== REVIEWER OBJECTIONS ===\n"
         + json.dumps(repair_brief, ensure_ascii=False, indent=1) + "\nFirst action:")
        if repair_brief else
        "Address the highest-impact failure modes / capability gaps within your "
        "edit budget. Implement via the JSON actions, then call done with the "
        "edits array. First action:",
    ])


def run_json_actions(llm: LLM, *, ws: MemWorkspace, system: str, stable: str, context: str, budget: int,
                     reserved: bool, explore: dict, K: list, traces: dict, task_means: dict, findings: list,
                     render: Callable, seed: int = 0, role: str = "proposer", usage: Optional[list] = None,
                     turns: Optional[list] = None) -> dict:
    """The code's proposer loop (``rrsi/propose.py::propose`` after the context is built)."""
    render_cache: dict = {}
    log, transcript, n_edits, aborts_left = [], "", 0, 3
    for turn in range(MAX_TURNS):
        prompt = ("=== ROUND CONTEXT ===\n" + context
                  + "\n\n=== INTERACTION LOG ===\n" + transcript
                  + "\nReply with exactly one JSON action object.")
        raw = generate(llm, prompt, system=system, json_only=True, cache_prefix=stable,
                       seed=seed * 1000 + turn, role=role, usage=usage)
        if turns is not None:
            turns.append({"turn": turn, "prompt": prompt, "reply": raw})
        try:
            act = json.loads(raw)
        except json.JSONDecodeError:
            transcript += f"\n[you] {raw[:500]}\n[result] ERROR: not valid JSON"
            continue
        if isinstance(act, list):
            act = next((x for x in act if isinstance(x, dict)), None)
        if not isinstance(act, dict):
            transcript += ("\n[you] (non-object)\n[result] ERROR: reply with "
                           "EXACTLY ONE JSON action object")
            continue
        a = act.get("action")
        log.append(act if a in ("done", "abort") else
                   {k: (v if len(str(v)) < 200 else str(v)[:200] + "...") for k, v in act.items()})
        if a == "done":
            edits = act.get("edits") or act.get("candidates") or []
            problems = []
            if n_edits == 0 and edits:
                transcript += ("\n[you] done\n[result] ERROR: you declared edits but "
                               "made ZERO file changes. Implement them with "
                               "edit_file/write_file, then call done.")
                continue
            if n_edits > 0 and not edits:
                problems.append("edits array is empty")
            if len(edits) > budget:
                problems.append(f"{len(edits)} edits exceed the budget b_t = {budget}")
            for e in edits:
                miss = [f for f in ("id", "component", "hypothesis", "targets_mode",
                                    "predicted_affected", "retroactive_check") if not e.get(f)]
                if miss:
                    problems.append(f"edit {e.get('id', '?')} missing {miss}")
                if e.get("component") and str(e["component"]).lower() not in K:
                    problems.append(f"edit {e.get('id')} component {e['component']!r} not in {K}")
            if reserved and explore.get("untried") and edits and not any(
                    str(e.get("component", "")).lower() in explore["untried"] for e in edits):
                problems.append("this variant holds a RESERVED EXPLORATION SLOT: at "
                                "least one edit must be on a never-exercised "
                                f"component from {explore['untried']}")
            if problems and n_edits > 0:
                transcript += (f"\n[you] done\n[result] ERROR: {problems}. Call done "
                               f"again fixed (drop or merge edits if over budget; "
                               f"add the required edit if a slot is reserved).")
                continue
            for e in edits:
                e["component"] = str(e.get("component", "")).lower()
                e.setdefault("mechanism", e.get("hypothesis"))
            return {"status": "done", "summary": act.get("summary"), "edits": edits,
                    "mechanism": act.get("summary") or "; ".join(str(e.get("hypothesis")) for e in edits)[:200],
                    "targets_mode": ", ".join(str(e.get("targets_mode")) for e in edits)[:200],
                    "n_edits": n_edits, "log": log}
        if a == "abort":
            if aborts_left > 0:
                aborts_left -= 1
                transcript += ("\n[you] abort\n[result] ERROR: there is no abort action. "
                               "Pick the most defensible mechanism you can build "
                               "within the hard rules, implement it, and call done. "
                               "A rejection is data; an abort is not.")
                continue
            return {"status": "abort", "reason": act.get("reason"), "edits": [], "n_edits": n_edits, "log": log}
        try:
            if a == "list_files":
                result = ws.list_files()
            elif a == "read_file":
                result = ws.read(act["path"])
            elif a == "list_traces":
                result = list_traces_text(traces, task_means, findings or [])
            elif a == "read_trace":
                result = read_trace_text(traces, act.get("task_id", ""), act.get("from_step"), act.get("to_step"),
                                         render_cache, act.get("detail", False), render)
            elif a == "edit_file":
                if n_edits >= MAX_EDITS:
                    result = "ERROR: edit budget exhausted; call done"
                else:
                    result = ws.edit(act["path"], act["old"], act["new"])
                    n_edits += result.startswith("OK")
            elif a == "write_file":
                if n_edits >= MAX_EDITS:
                    result = "ERROR: edit budget exhausted; call done"
                else:
                    result = ws.write(act["path"], act["content"])
                    n_edits += result.startswith("OK")
            else:
                result = f"ERROR: unknown action {a}"
        except Exception as e:  # noqa: BLE001
            result = f"ERROR: {e}"
        cap = 150_000 if a in ("read_file", "list_files", "read_trace", "list_traces") else 4000
        transcript += f"\n[you] {json.dumps(act)[:1500]}\n[result] {str(result)[:cap]}"
    return {"status": "max_turns", "edits": [], "n_edits": n_edits, "log": log}


class JsonActionProposer:
    """Drop-in for :class:`rsi.rrsi.propose.Proposer` that runs the code's JSON action protocol.

    ``propose`` takes the loop's keyword arguments plus ``traces`` (task id -> trace record),
    ``task_means`` and ``task_inputs`` (for ``list_traces`` / ``read_trace``); it edits a copy of the
    working artifact in memory and returns the Proposer's result dict (``artifact`` is the edited harness,
    or None when nothing changed relative to the incumbent)."""

    def __init__(self, llm: LLM, taxonomy: Taxonomy, cfg, *, domain_brief: str = "",
                 constitution: tuple[str, str] = ("", ""), source_exts=DEFAULT_SOURCE_EXTS) -> None:
        self.llm = llm
        self.tax = taxonomy
        self.cfg = cfg
        self.system = PROPOSER_SYSTEM_TMPL.format(domain_brief=domain_brief, components=" | ".join(taxonomy.K))
        skill_md, patterns_md = constitution
        self.stable = "\n\n".join(["=== CONSTITUTION (SKILL.md) ===", skill_md,
                                   "=== PATTERN LIBRARY (PATTERNS.md) ===", patterns_md])
        self.source_exts = tuple(source_exts)

    def propose(self, base: Artifact, *, working: Optional[Artifact] = None, directives: Optional[dict] = None,
                variant_brief: str = "", history_rows: Optional[list] = None, scoreboard: Optional[list] = None,
                explore: Optional[dict] = None, reserved: bool = False, prune_set: Optional[list] = None,
                report: Optional[dict] = None, budget: int = 1, digests: Optional[list] = None,
                traces_text: str = "", repair_brief: Optional[dict] = None, seed: int = 0, capture: bool = False,
                traces: Optional[dict] = None, task_means: Optional[dict] = None,
                task_inputs: Optional[dict] = None) -> dict:
        explore = explore or {}
        working = working or base
        ws = MemWorkspace(working.files, self.source_exts)
        traces = traces or {}
        task_inputs = task_inputs or {}
        context = build_proposer_context(ws=ws, report=report or {}, history_rows=history_rows or [], budget=budget,
                                         explore=explore, reserved=reserved, prune_set=prune_set or [],
                                         findings=digests or [], scoreboard=scoreboard or [],
                                         variant_brief=variant_brief, repair_brief=repair_brief)

        def render(rec, detail):
            return render_trace(rec, task_inputs.get(rec.get("task_id")), cap=TRACE_READ_CAP if detail else 6000)

        usage: list = []
        turns: list = []
        try:
            res = run_json_actions(self.llm, ws=ws, system=self.system, stable=self.stable, context=context,
                                   budget=budget, reserved=reserved, explore=explore, K=list(self.tax.K),
                                   traces=traces, task_means=task_means or {}, findings=digests or [],
                                   render=render, seed=seed, usage=usage, turns=turns)
        except GenerateError as e:
            return {"status": "error", "reason": f"llm error: {e}", "edits": [], "n_changes": 0, "artifact": None,
                    "log": [], "usage": _sum(usage), **({"turns": turns} if capture else {})}
        new_art = working.with_files({p: ws.files.get(p) for p in set(working.files) | set(ws.files)})
        n_changes = len(base.changed_files(new_art))
        res.update(n_changes=n_changes, artifact=new_art if (res["status"] == "done" and n_changes) else None,
                   usage=_sum(usage), protocol="json_actions")
        if res["status"] == "done":
            for e in res["edits"]:
                e["component"] = self.tax.canonical(e.get("component", ""), strip=False)
        if capture:
            for tr in turns:
                tr.update(artifact=None, outcome="json action", declared_edits=[], n_changes=None)
            if turns:
                turns[-1].update(artifact=new_art, n_changes=n_changes, outcome=res["status"],
                                 summary=res.get("summary"), declared_edits=[dict(e) for e in res["edits"]])
            res["turns"] = turns
        return res


# ------------------------------------------------------------------------------------------ analyst
ANALYST_MAX_TURNS = 30
DIGEST_PARALLELISM = 6
DIGESTER_MAX_TURNS = 15
DIGEST_MAX_CHARS = 6000
TOOL_OUT_CAP = 25_000
BASH_TIMEOUT = 30
#: rrsi/digester.py::SCHEMAS, verbatim (the single-shot digester's copy in analyst.py wraps the success schema's
#: "habits" line differently; whitespace only, kept there so recorded live caches still replay)
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
 "habits": [{"habit": "reusable behavior that made this run clean",
             "where_shown": "step range"}],
 "risk_if_removed": "which habit is load-bearing and what breaks without it"}""",
}
ALLOWED_BASH = {"grep", "egrep", "head", "tail", "wc", "cat", "ls", "find",
                "cut", "sort", "uniq", "awk", "jq", "sed", "tr", "paste"}
DENY_BASH = re.compile(r"(>>?|`|\$\(|sed\s+[^|]*-i|\brm\b|\bmv\b|\bcp\b|"
                       r"\btee\b|\btouch\b|\bmkdir\b|\bchmod\b|\bpython)")

#: rrsi/digester.py::SYSTEM_TMPL, verbatim
DIGESTER_SYSTEM_TMPL = """You are a trajectory digester: a read-only investigator that
inspects ONE agent trajectory in depth and returns a compact structured
digest. Another agent (the batch analyst) will rely on your digest without
reading the trace itself, so be precise and evidence-anchored.

{domain_brief}

The rendered trajectory files live in your working directory as <task_id>.txt.

Your lens for this assignment: {lens}
{focus}

You interact via STRICT JSON, one action per turn:
  {{"action": "read_file", "path": "<task_id>.txt", "offset": 1200, "limit": 300}}
      (line-based; omit offset/limit to read from the start)
  {{"action": "glob", "pattern": "*.txt"}}
  {{"action": "grep", "pattern": "regex", "path": "<task_id>.txt", "max_hits": 40}}
      (returns matching lines with line numbers)
  {{"action": "bash", "cmd": "grep -n 'FAILED' <task_id>.txt | head -30"}}
      (read-only shell: grep/head/tail/awk/jq/sed(no -i)/wc/...; any write
       operation is rejected)
  {{"action": "return", "digest": {{...}}}}

Investigate efficiently: read the grading / verifier section first (what
actually failed), grep for anchors (step numbers, tool names, key values,
error strings), then read the relevant slices. Do not read whole files top
to bottom.

Finish with action "return". The digest MUST follow this schema and MUST be
under {cap} characters total:
{schema}

Rules: quotes must be exact and short; every claim needs a "where"; do not
speculate beyond what the trace shows; no blame attribution to "model vs
harness"."""

#: rrsi/analyst.py::SYSTEM_TMPL, verbatim
ANALYST_SYSTEM_TMPL = """You are the batch analyst in a harness-evolution loop. A frozen
policy LLM, driven by an evolvable scaffold, ran the evolve set of a benchmark;
some trials scored well, some did not. Your job: produce a three-lens analysis
report that a harness engineer will act on.

{domain_brief}

You do NOT read traces yourself. You dispatch read-only digester subagents
that investigate one trace each and return structured digests. Budget their
use: digest FAILED / low-scoring tasks first (failure lens), prioritising
coverage of every suspected failure cluster over digesting every failure;
digest 3-5 representative SUCCESSFUL tasks (success lens, prefer ones that
resemble a big failure cluster); use the capability_gap lens or follow-up
questions where a failure digest hints the agent was blocked by the
environment or the scaffold's plumbing rather than by its own judgment.

Actions (STRICT JSON, one per turn):
  {{"action": "digest_many", "requests": [
      {{"task_id": "<id exactly as it appears in the task table>",
       "lens": "failure|capability_gap|success",
       "questions": ["optional targeted questions"]}}, ...]}}
      (up to 8 per call, they run in parallel; each returns a digest)
  {{"action": "report",
   "failure_modes": [
     {{"mode": "snake_case_label", "n_tasks": 0, "affected_tasks": [...],
      "description": "generalized mechanism, entity-free",
      "needed_instead": "...",
      "representative_evidence": [{{"task_id": "...", "where": "...", "quote": "..."}}]}}],
   "capability_gaps": [
     {{"gap": "snake_case_label", "n_tasks": 0, "affected_tasks": [...],
      "description": "what the agent could not do and why, entity-free",
      "representative_evidence": [...]}}],
   "success_habits": [
     {{"habit": "snake_case_label", "n_tasks": 0,
      "description": "the reusable behavior, entity-free",
      "risk_if_broken": "what regresses if a harness change disrupts it"}}]}}

Aggregation rules:
1. MERGE digests describing the same underlying mechanism even if worded
   differently; SPLIT a label that covers two distinct mechanisms.
2. RANK failure_modes by total impact (number of tasks weighted by how much
   score the mode costs on each).
3. Descriptions must be entity-free and task-agnostic (no task names, no
   subject-matter facts, no task-specific values); evidence quotes may
   contain them.
4. Keep prior mode names when the same mechanism recurs (stable naming);
   prior names are provided in the context.
5. Do NOT prescribe code changes and do NOT attribute blame to model vs
   scaffold.
6. If digests contradict or a cluster is unclear, dispatch follow-up digests
   with targeted questions before reporting. Report once, at the end."""


def _bash_ok(cmd: str) -> bool:
    if DENY_BASH.search(cmd):
        return False
    for seg in re.split(r"[|;&]+", cmd):
        seg = seg.strip()
        if seg and seg.split()[0] not in ALLOWED_BASH:
            return False
    return True


def _safe(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve())):
        raise ValueError("path escapes the traces dir")
    return p


def _read_file(root: Path, rel: str, offset, limit) -> str:
    p = _safe(root, rel)
    if not p.exists():
        return f"ERROR: no such file {rel}"
    lines = p.read_text().splitlines()
    lo = max(0, (offset or 1) - 1)
    hi = lo + (limit or 400)
    body = "\n".join(f"{i+1}: {l}" for i, l in enumerate(lines[lo:hi], start=lo))
    return body + (f"\n...[file has {len(lines)} lines]" if hi < len(lines) else "")


def _grep(root: Path, pattern: str, rel: str, max_hits: int) -> str:
    p = _safe(root, rel)
    if not p.exists():
        return f"ERROR: no such file {rel}"
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"ERROR: bad regex: {e}"
    out = []
    for i, line in enumerate(p.read_text().splitlines(), 1):
        if rx.search(line):
            out.append(f"{i}: {line[:400]}")
            if len(out) >= max_hits:
                out.append("...[max hits reached]")
                break
    return "\n".join(out) if out else "(no matches)"


def digest_task(llm: LLM, traces_dir: Path, task_id: str, lens: str, domain_brief: str,
                questions: Optional[list] = None, seed: int = 0, usage: Optional[list] = None,
                calls: Optional[list] = None) -> dict:
    """The code's ``digest_task``: one read-only digester agent over one rendered trace."""
    lens = lens if lens in SCHEMAS else "failure"
    focus = ""
    if questions:
        focus = "Specific questions from the analyst you MUST answer:\n" + "\n".join(f"- {q}" for q in questions[:5])
    system = DIGESTER_SYSTEM_TMPL.format(lens=lens, focus=focus, cap=DIGEST_MAX_CHARS, schema=SCHEMAS[lens],
                                         domain_brief=domain_brief)
    transcript = f"Assigned trace: {task_id}.txt\nFirst action:"
    for turn in range(DIGESTER_MAX_TURNS):
        raw = generate(llm, transcript, system=system, json_only=True, seed=seed * 100 + turn, role="digester",
                       usage=usage)
        if calls is not None:
            calls.append({"role": "digester", "task_id": task_id, "lens": lens, "turn": turn, "prompt": transcript,
                          "reply": raw})
        try:
            act = json.loads(raw)
        except json.JSONDecodeError:
            transcript += f"\n[you] {raw[:300]}\n[result] ERROR: invalid JSON"
            continue
        if isinstance(act, list):
            act = next((x for x in act if isinstance(x, dict)), None)
        if not isinstance(act, dict):
            transcript += ("\n[you] (non-object)\n[result] ERROR: reply with "
                           "EXACTLY ONE JSON action object, not a list or value")
            continue
        a = act.get("action")
        if a == "return":
            digest = act.get("digest") or {}
            blob = json.dumps(digest, ensure_ascii=False)
            if len(blob) > DIGEST_MAX_CHARS:
                transcript += (f"\n[you] return ({len(blob)} chars)\n[result] "
                               f"ERROR: digest is {len(blob)} chars, cap is "
                               f"{DIGEST_MAX_CHARS}. Shorten and return again.")
                continue
            digest.setdefault("task_id", task_id)
            digest.setdefault("lens", lens)
            return digest
        try:
            if a == "read_file":
                result = _read_file(traces_dir, act["path"], act.get("offset"), act.get("limit"))
            elif a == "glob":
                result = "\n".join(sorted(p.name for p in traces_dir.glob(act.get("pattern", "*"))))
            elif a == "grep":
                result = _grep(traces_dir, act.get("pattern", ""), act.get("path", ""), int(act.get("max_hits", 40)))
            elif a == "bash":
                cmd = act.get("cmd", "")
                if not _bash_ok(cmd):
                    result = ("ERROR: command rejected (read-only shell; allowed: "
                              "grep/head/tail/awk/jq/sed/wc/cat/ls/find/cut/sort/"
                              "uniq/tr; no redirection)")
                else:
                    r = subprocess.run(cmd, shell=True, cwd=traces_dir, capture_output=True, text=True,
                                       timeout=BASH_TIMEOUT)
                    result = (r.stdout or "") + (r.stderr or "")
            else:
                result = f"ERROR: unknown action {a}"
        except Exception as e:  # noqa: BLE001
            result = f"ERROR: {e}"
        transcript += (f"\n[you] {json.dumps(act)[:600]}\n"
                       f"[result] {str(result)[:TOOL_OUT_CAP]}")
    return {"task_id": task_id, "lens": lens, "error": "digester hit max turns without returning"}


class AgenticAnalyst:
    """Drop-in for :class:`rsi.rrsi.analyst.Analyst` running the code's analyst + digester agents.

    ``analyze(traces, meas, task_inputs, prior, seed, workdir)``: renders the traces to
    ``workdir/analysis/rendered/<task>.txt``, runs the batch-analyst loop (digests written to
    ``workdir/analysis/digests/<task>_<lens>.json``) and returns ``(report, digests)`` where ``digests`` is
    every digest file of the round, sorted by name (the code's ``load_digests``, which the proposer reads)."""

    mode = "agentic"

    def __init__(self, llm: LLM, *, domain_brief: str = "", digester_brief: Optional[str] = None,
                 workers: int = DIGEST_PARALLELISM) -> None:
        self.llm = llm
        self.domain_brief = domain_brief
        self.digester_brief = digester_brief if digester_brief is not None else domain_brief
        self.workers = workers
        self.last_calls: list[dict] = []

    def analyze(self, traces: dict, meas, task_inputs: Optional[dict] = None, prior: Optional[dict] = None,
                seed: int = 0, workdir: Optional[Path] = None) -> tuple[dict, list[dict]]:
        task_inputs = task_inputs or {}
        prior = prior or {}
        self.last_calls = []
        tmp = None
        if workdir is None:
            tmp = tempfile.TemporaryDirectory(prefix="rrsi_analysis_")
            workdir = Path(tmp.name)
        rendered_dir = Path(workdir) / "analysis" / "rendered"
        rendered_dir.mkdir(parents=True, exist_ok=True)
        for tid, rec in traces.items():
            out = rendered_dir / f"{tid}.txt"
            if not out.exists():
                out.write_text(render_trace(rec, task_inputs.get(tid), cap=TRACE_READ_CAP))
        digests_dir = Path(workdir) / "analysis" / "digests"
        digests_dir.mkdir(parents=True, exist_ok=True)
        means = meas.task_means() if hasattr(meas, "task_means") else dict(meas or {})
        table = "\n".join(r for _, r in sorted(((means.get(t, 0.0), task_row(t, rec, means.get(t)))
                                                for t, rec in traces.items()), key=lambda x: x[0]))
        system = ANALYST_SYSTEM_TMPL.format(domain_brief=self.domain_brief)
        context = "\n\n".join([
            "=== TASK TABLE (one rendered trace per task; lowest scores first) ===", table,
            "=== PRIOR FAILURE-MODE NAMES (for stable naming) ===",
            json.dumps([{"mode": m.get("mode"), "description": m.get("description")}
                        for m in (prior.get("failure_modes") or [])], ensure_ascii=False, indent=1),
            "=== PRIOR SUCCESS-HABIT NAMES (for stable naming) ===",
            json.dumps([{"habit": h.get("habit"), "description": h.get("description")}
                        for h in (prior.get("success_habits") or [])], ensure_ascii=False, indent=1),
            "=== TASK ===",
            "Dispatch digesters, then produce the report. First action:",
        ])
        usage: list = []
        transcript, n_digests, n_calls = "", 0, 0
        report = None
        for turn in range(ANALYST_MAX_TURNS):
            prompt = (context + "\n\n=== INTERACTION LOG ===\n" + transcript
                      + "\nReply with exactly one JSON action object.")
            raw = generate(self.llm, prompt, system=system, json_only=True, seed=seed * 1000 + turn, role="analyst",
                           usage=usage)
            self.last_calls.append({"role": "analyst", "turn": turn, "system": system, "prompt": prompt,
                                    "reply": raw})
            try:
                act = json.loads(raw)
            except json.JSONDecodeError:
                transcript += f"\n[you] {raw[:300]}\n[result] ERROR: invalid JSON"
                continue
            if isinstance(act, list):
                act = next((x for x in act if isinstance(x, dict)), None)
            if not isinstance(act, dict):
                transcript += "\n[you] (non-object)\n[result] ERROR: reply with EXACTLY ONE JSON action object"
                continue
            a = act.get("action")
            if a == "report":
                report = {k: act.get(k) or [] for k in ("failure_modes", "capability_gaps", "success_habits")}
                report["failure_modes"].sort(key=lambda m: -(m.get("n_tasks") or 0))
                report["n_digests"] = n_digests
                break
            if a == "digest_many":
                reqs = (act.get("requests") or [])[:8]
                for r in reqs:
                    if isinstance(r, dict) and r.get("task_id") is not None:
                        r["task_id"] = str(r["task_id"])
                valid = [r for r in reqs if isinstance(r, dict) and r.get("task_id") in traces]
                base_seed = seed * 1000 + 500 + n_calls * 10
                n_calls += 1

                def run(ir):
                    i, r = ir
                    calls: list = []
                    d = digest_task(self.llm, rendered_dir, r["task_id"], r.get("lens", "failure"),
                                    self.digester_brief, r.get("questions"), seed=base_seed + i, usage=usage,
                                    calls=calls)
                    (digests_dir / f"{r['task_id']}_{r.get('lens', 'failure')}.json").write_text(
                        json.dumps(d, ensure_ascii=False, indent=1))
                    return d, calls
                with ThreadPoolExecutor(max_workers=max(1, self.workers)) as ex:
                    outs = list(ex.map(run, list(enumerate(valid))))
                digests = [d for d, _ in outs]
                for _, c in outs:
                    self.last_calls.extend(c)
                n_digests += len(digests)
                result = json.dumps(digests, ensure_ascii=False)
                if len(reqs) - len(valid):
                    result += f"\n[{len(reqs) - len(valid)} requests skipped: unknown task_id]"
                transcript += f"\n[you] digest_many ({len(valid)} requests)\n[result] {result}"
            else:
                transcript += f"\n[you] {json.dumps(act)[:300]}\n[result] ERROR: unknown action {a}"
        if report is None:
            report = {"failure_modes": [], "capability_gaps": [], "success_habits": [],
                      "error": "analyst hit max turns", "n_digests": n_digests}
        report["analyst"] = "agentic"
        all_digests = [json.loads(p.read_text()) for p in sorted(digests_dir.glob("*.json"))]
        self.last_usage = _sum(usage)
        if tmp is not None:
            tmp.cleanup()
        return report, all_digests


__all__ = ["JsonActionProposer", "AgenticAnalyst", "MemWorkspace", "digest_task", "run_json_actions",
           "build_proposer_context", "generate", "ref_extract_json", "PROPOSER_SYSTEM_TMPL",
           "DIGESTER_SYSTEM_TMPL", "ANALYST_SYSTEM_TMPL", "JSON_SUFFIX", "GenerateError"]
