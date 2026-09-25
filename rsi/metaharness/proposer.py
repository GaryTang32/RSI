"""Proposers: who writes new harnesses from the experience store.

* :class:`AgentProposer` - the paper's proposer: a headless coding agent
  (``claude -p`` via :class:`rsi.core.AgentEditor`) whose working directory holds the
  whole history view read-only under ``_context/`` and which writes ``k`` complete
  new harnesses under ``agents/<name>/`` plus ``pending_eval.json``.
* :class:`RewriteProposer` - one completion from any :class:`rsi.core.LLM`; the
  history view is *rendered* into the prompt (priority order, character budget)
  and the reply carries ``pending_eval`` JSON + ``=== FILE: agents/<name>/<path> ===``
  blocks.
* :class:`rsi.metaharness.mock.MockProposer` - deterministic offline proposer.

The skill text adapts the release's ``SKILL.md`` (spec A6) to any domain: you do
not run benchmarks; write exactly k candidates; change mechanisms, not
parameters; no dataset-specific hints; complete files only.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from ..core.artifact import Artifact, parse_file_blocks
from ..core.editors import AgentEditor
from ..core.llm import LLM, ClaudeCLI, Usage, extract_json
from .store import safe_name

SKILL_TEXT = """\
# Meta-Harness (harness evolution) - run ONE iteration

You improve the HARNESS around a frozen model: the code that decides what information to store, what to
retrieve and how to present it to the model. **You do NOT run benchmarks.** A separate evaluator scores every
candidate you write and stores its code, scores and raw execution traces in the history directory.

## Constraints
- Write exactly {k} new candidate harnesses this iteration. Never write "the frontier is optimal" or stop early.
- Mix exploitation (improve a top system) and exploration (a different mechanism).
- Good candidates change a fundamental MECHANISM (a new retrieval algorithm, a new prompt architecture, a new
  learning strategy, a new memory structure). Parameter sweeps (pool sizes, counts, budgets) almost always tie or
  regress. If your logic is identical to the base except for numbers, rewrite it. Combining systems is valid.
- Anti-overfitting: no dataset-specific hints, never mention dataset or task names, never hard-code answers,
  inputs or ids from the traces. General patterns are fine. Would this help on MANY unfamiliar tasks?
- Candidates must keep the harness interface (see the domain description) and work from a cold start.

## Workflow
1. Read evolution_summary.jsonl and frontier_val.json, then the code of top candidates and, most importantly,
   their raw traces (failed AND successful examples) under candidates/<name>/eval/search/traces/.
2. Formulate {k} falsifiable hypotheses, each targeting a different mechanism.
3. Implement each candidate as COMPLETE files (copy a strong base, then change the mechanism). Self-critique:
   is this a genuinely new mechanism?
"""

TASK_PROMPT = """\
Run iteration {iteration} of the evolution loop.

## Domain
{brief}

## Run directories
The full history of this run is under `{ctx}/` (read-only):
- `{ctx}/evolution_summary.jsonl` - past results (one row per candidate)
- `{ctx}/frontier_val.json` - Pareto frontier on the search set (score up, context cost down) and per-unit bests
- `{ctx}/candidates/<name>/src/` - every candidate's source; `.../eval/search/scores.json`, `.../traces/*.jsonl`
- `{ctx}/reports/` - post-eval reports
(Some of these may be absent: you see exactly what this run's history mode exposes.)

## Output
{output}
"""

AGENT_OUTPUT = """\
For each of the {k} candidates create a directory `agents/<new_name>/` containing the COMPLETE harness files
(same file layout as INSIDE the candidates' `src/` directories, e.g. `agents/<new_name>/harness.py`, not
`agents/<new_name>/src/harness.py`; unchanged files may be omitted and are copied from the base system). Then write `pending_eval.json`:
{{"iteration": {iteration}, "candidates": [{{"name": "<new_name>", "base_system": "<name of the system you started
from>", "hypothesis": "<falsifiable claim>", "axis": "exploitation|exploration", "components": ["<tags>"]}}]}}
Use new names (lowercase letters, digits, underscores) that do not exist yet."""

REWRITE_OUTPUT = """\
Reply with:
1. A ```json fence holding {{"iteration": {iteration}, "candidates": [{{"name": "<new_name>", "base_system":
   "<system you started from>", "hypothesis": "<falsifiable claim>", "axis": "exploitation|exploration",
   "components": ["<tags>"]}}, ...]}} with exactly {k} candidates.
2. For every candidate, the COMPLETE content of each file you change:
=== FILE: agents/<new_name>/<path> ===
<entire file content>
<path> is relative to the harness root, exactly as the files appear inside a candidate's src/ directory
(e.g. agents/<new_name>/harness.py, NOT agents/<new_name>/src/harness.py).
Files you omit are copied from the candidate's base_system. Use new names (lowercase, digits, underscores)."""


@dataclass
class CandidateSpec:
    name: str
    artifact: Artifact
    hypothesis: str = ""
    axis: str = ""
    components: list[str] = field(default_factory=list)
    base_system: str = ""
    parents_read: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def pending_row(self) -> dict:
        return {"name": self.name, "base_system": self.base_system, "hypothesis": self.hypothesis,
                "axis": self.axis, "components": self.components}


@dataclass
class ProposalBatch:
    candidates: list[CandidateSpec] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    files_read: list[str] = field(default_factory=list)
    transcript: str = ""
    prompt: str = ""
    error: Optional[str] = None
    meta: dict = field(default_factory=dict)


class Proposer:
    """Protocol: turn a history view into k candidate harnesses."""

    role = "proposer"

    def propose(self, *, iteration: int, view: dict[str, str], k: int, brief: str,
                artifacts: dict[str, Artifact], seed: int = 0) -> ProposalBatch:  # pragma: no cover
        """``view`` = what the proposer may read; ``artifacts`` = sources of visible systems
        (for copying unchanged files from ``base_system``)."""
        raise NotImplementedError


_FENCE_OPEN = re.compile(r"^\s*```[A-Za-z0-9_+.-]*[^\n]*\n")


def clean_code_block(text: str) -> str:
    """Unwrap a file body that is still inside a Markdown fence (``rsi.core.parse_file_blocks`` only
    unwraps it when nothing follows the closing fence) and drop prose after the closing fence."""
    if text is None:
        return text
    m = _FENCE_OPEN.match(text)
    if not m:
        return text
    body = text[m.end():]
    end = body.rfind("\n```")
    if end >= 0:
        body = body[:end + 1]
    elif body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.rstrip() + "\n"


def _collect(files: dict[str, str], header: dict, k: int, artifacts: dict[str, Artifact],
             taken: set[str], iteration: int) -> list[CandidateSpec]:
    """Group ``agents/<name>/<path>`` files into candidates, completing them from their base system."""
    groups: dict[str, dict[str, Optional[str]]] = {}
    for path, text in files.items():
        m = re.match(r"^agents/([^/]+)/(.+)$", path)
        if m:
            groups.setdefault(m.group(1), {})[m.group(2)] = clean_code_block(text)
    rows = header.get("candidates") if isinstance(header, dict) else None
    rows = rows if isinstance(rows, list) else []
    by_name = {str(r.get("name")): r for r in rows if isinstance(r, dict)}
    out = []
    for raw_name, updates in groups.items():
        row = by_name.get(raw_name, {})
        base_name = str(row.get("base_system") or "")
        base = artifacts.get(base_name) or (next(iter(artifacts.values())) if artifacts else Artifact({}))
        # the history shows sources under candidates/<name>/src/<path>; a proposer that copies that layout
        # (agents/<name>/src/harness.py) would add dead files and leave the real harness unchanged
        # (observed live with haiku, validation/metaharness-solpi/mh_agentqa_live) -> map src/<path> to <path>
        if not any(p.startswith("src/") for p in base.files):
            updates = {(p[4:] if p.startswith("src/") else p): t for p, t in updates.items()}
        art = base.with_files({p: (None if (t or "").strip() == "<<DELETE>>" else t) for p, t in updates.items()})
        name = safe_name(raw_name)
        if name in taken:
            name = safe_name(f"{name}_i{iteration}")
        taken.add(name)
        comps = row.get("components") or []
        out.append(CandidateSpec(name, art, str(row.get("hypothesis", ""))[:2000], str(row.get("axis", "")),
                                 [str(c) for c in (comps if isinstance(comps, list) else [comps])], base_name))
        if len(out) >= k:
            break
    return out


class RewriteProposer(Proposer):
    """Single-completion proposer over a rendered history view (works with any LLM)."""

    def __init__(self, llm: LLM, *, max_context_chars: int = 60000, skill: str = SKILL_TEXT) -> None:
        self.llm = llm
        self.max_context_chars = max_context_chars
        self.skill = skill

    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        rendered, read = render_view(view, self.max_context_chars)
        prompt = TASK_PROMPT.format(iteration=iteration, brief=brief, ctx="history",
                                    output=REWRITE_OUTPUT.format(iteration=iteration, k=k))
        prompt = prompt + "\n\n## History (rendered)\n" + rendered
        system = self.skill.format(k=k)
        resp = self.llm.complete(prompt, system=system, seed=seed * 1000 + iteration, role=self.role)
        batch = ProposalBatch(usage=resp.usage, files_read=read, transcript=resp.text, prompt=prompt)
        if not resp.ok:
            batch.error = f"llm error: {resp.error}"
            return batch
        header: dict = {}
        head_text = resp.text.split("=== FILE:")[0]
        try:
            h = extract_json(head_text)
            header = h if isinstance(h, dict) else {"candidates": h} if isinstance(h, list) else {}
        except ValueError:
            pass
        files = parse_file_blocks(resp.text)
        batch.candidates = _collect(files, header, k, artifacts, set(), iteration)
        if not batch.candidates:
            batch.error = "no candidate files in reply"
        return batch


class AgentProposer(Proposer):
    """The paper's proposer: a coding agent over the history directory (``claude -p``).

    Uses :class:`rsi.core.AgentEditor`: the view is written read-only under
    ``_context/``; the agent may only write ``agents/*`` and ``pending_eval.json``."""

    def __init__(self, cli: ClaudeCLI, *, timeout_s: float = 2400.0, skill: str = SKILL_TEXT,
                 tools: tuple[str, ...] = ("Read", "Edit", "Write", "Glob", "Grep")) -> None:
        self.cli = cli
        self.skill = skill
        self.editor = AgentEditor(cli, tools=tools, timeout_s=timeout_s)

    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        workspace = Artifact({"agents/README.md": "Write new candidates here: agents/<name>/<files>.\n"})
        prompt = TASK_PROMPT.format(iteration=iteration, brief=brief, ctx="_context",
                                    output=AGENT_OUTPUT.format(iteration=iteration, k=k))
        t0 = time.time()
        prop = self.editor.edit(workspace, prompt, context=view, editable=["agents/*", "pending_eval.json"],
                                system=self.skill.format(k=k), seed=seed, role=self.role)
        batch = ProposalBatch(usage=prop.usage, transcript=prop.raw, prompt=prompt,
                              files_read=sorted(view), meta={"seconds": time.time() - t0})
        if prop.artifact is None:
            batch.error = prop.error or "agent produced no files"
            return batch
        if prop.error and prop.error.startswith("agent error"):
            # release: a failed or timed-out proposer (claude_wrapper returns ok=False, e.g. exit 124) skips the
            # iteration even if pending_eval.json was already written [spec A3.2]
            batch.error = prop.error
            return batch
        files = {n: prop.artifact[n] for n in prop.artifact if n != "agents/README.md"}
        header: dict = {}
        if "pending_eval.json" in files:
            try:
                header = json.loads(files.pop("pending_eval.json"))
            except json.JSONDecodeError:
                header = {}
        batch.candidates = _collect(files, header, k, artifacts, set(), iteration)
        if not batch.candidates:
            batch.error = prop.error or "no candidates written under agents/"
        return batch


def render_view(view: dict[str, str], budget: int, per_file: int = 12000) -> tuple[str, list[str]]:
    """Render a view into prompt text: run files first, then candidates by score
    (source, scores, summaries, then traces), each file truncated, until ``budget``."""
    def cand_score(path: str) -> float:
        m = re.match(r"candidates/([^/]+)/", path)
        if not m:
            return 0.0
        s = view.get(f"candidates/{m.group(1)}/eval/search/scores.json")
        try:
            return float(json.loads(s)["score"]) if s else -1.0
        except (ValueError, KeyError):
            return -1.0

    def prio(path: str) -> tuple:
        if not path.startswith("candidates/"):
            return (0, 0.0, path)
        kind = 1 if "/src/" in path else 2 if path.endswith(("scores.json", "meta.json", "summary.md")) else 3
        return (kind, -cand_score(path), path)

    parts, read, used = [], [], 0
    for path in sorted(view, key=prio):
        text = view[path]
        if len(text) > per_file:
            text = text[:per_file] + "\n...[truncated]"
        chunk = f"=== HISTORY FILE: {path} ===\n{text}\n"
        if used + len(chunk) > budget:
            continue
        parts.append(chunk)
        read.append(path)
        used += len(chunk)
    return "".join(parts), read


# ------------------------------------------------------------------ summarisers
SUMMARY_PROMPT = """Summarise the execution traces of one harness candidate in at most 5 sentences for an engineer
who will improve the harness: overall accuracy, recurring failure patterns, anything notable. Do not quote
individual examples.

{traces}"""


class LLMSummarizer:
    """Trace summaries for the ``scores_summary`` ablation (paper Table 3, row 2)."""

    def __init__(self, llm: LLM, max_chars: int = 40000) -> None:
        self.llm = llm
        self.max_chars = max_chars

    def __call__(self, traces: dict[str, str]) -> str:
        text = "\n".join(f"--- {u} ---\n{t}" for u, t in traces.items())[: self.max_chars]
        resp = self.llm.complete(SUMMARY_PROMPT.format(traces=text), role="summarizer", seed=0)
        return resp.text.strip() if resp.ok else "(summary unavailable)"


def generic_summary(traces: dict[str, str]) -> str:
    n = sum(len(t.splitlines()) for t in traces.values())
    return f"{len(traces)} evaluation units, {n} trace lines. No further detail."
