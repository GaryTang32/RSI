"""Editors: how an LLM turns "here is the artifact and what went wrong" into a
changed artifact.

Two interchangeable implementations of :class:`Editor`:

* :class:`RewriteEditor` - one completion; the model returns whole changed files
  in ``=== FILE: name ===`` blocks plus a JSON header. Works with any
  :class:`~rsi.core.llm.LLM` including :class:`~rsi.core.llm.MockLLM`.
* :class:`AgentEditor` - a headless coding agent (``claude -p`` with Read/Edit/
  Write tools) working in a scratch copy of the artifact; context files (history,
  traces, scores) are placed read-only beside it. This is the Meta-Harness /
  autoresearch style "coding agent edits files" proposer.

Both apply a scope guard: edits to files outside ``editable`` are discarded and
reported (autoresearch's "the agent can't change how it is graded").
"""
from __future__ import annotations

import fnmatch
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional, Sequence

from .artifact import Artifact, is_safe_relpath, parse_file_blocks
from .llm import LLM, ClaudeCLI, Usage, extract_json

EDIT_FORMAT = """\
Respond in exactly this format:
1. A JSON object in a ```json fence with keys:
   "change": one-line description of the edit,
   "hypothesis": why it should improve the score on UNSEEN tasks of the same kind,
   "components": list of component names you touched (e.g. ["prompt", "control_flow", "tool"]).
2. Then, for every file you change or create, the COMPLETE new file content:
=== FILE: <path> ===
<entire file content>
Files you do not list stay unchanged. To delete a file, emit `=== FILE: <path> ===` followed by the single line `<<DELETE>>`.
"""


@dataclass
class Proposal:
    artifact: Optional[Artifact]
    change: str = ""
    hypothesis: str = ""
    components: list[str] = field(default_factory=list)
    raw: str = ""
    usage: Usage = field(default_factory=Usage)
    error: Optional[str] = None
    blocked_files: list[str] = field(default_factory=list)   # scope-guard rejections
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None and self.artifact is not None


def _allowed(name: str, editable: Optional[Sequence[str]]) -> bool:
    return editable is None or any(fnmatch.fnmatch(name, pat) for pat in editable)


def apply_scope(base: Artifact, updates: Mapping[str, Optional[str]], editable: Optional[Sequence[str]]):
    """Apply ``updates`` that match ``editable`` (glob patterns; None = all).
    Returns ``(new_artifact, blocked_names)``. Names that are absolute or escape
    the artifact root (``..``) are always blocked."""
    allowed, blocked = {}, []
    for name, text in updates.items():
        if is_safe_relpath(name) and _allowed(name, editable):
            allowed[name] = text
        else:
            blocked.append(name)
    return base.with_files(allowed), blocked


def _header_fields(header: object) -> tuple[str, str, list[str]]:
    """(change, hypothesis, components) from a proposal's JSON header."""
    h = header if isinstance(header, dict) else {}
    comps = h.get("components") or []
    if isinstance(comps, str):
        comps = [comps]
    elif not isinstance(comps, (list, tuple)):
        comps = [comps]
    return str(h.get("change", ""))[:500], str(h.get("hypothesis", ""))[:2000], [str(c) for c in comps]


class Editor:
    def edit(
        self,
        artifact: Artifact,
        instructions: str,
        *,
        context: Optional[Mapping[str, str]] = None,
        editable: Optional[Sequence[str]] = None,
        system: Optional[str] = None,
        seed: Optional[int] = None,
        role: str = "proposer",
    ) -> Proposal:  # pragma: no cover
        raise NotImplementedError


class RewriteEditor(Editor):
    def __init__(self, llm: LLM, max_context_chars: int = 60000) -> None:
        self.llm = llm
        self.max_context_chars = max_context_chars

    def build_prompt(self, artifact, instructions, context, editable) -> str:
        parts = [instructions.strip(), ""]
        if context:
            budget = self.max_context_chars
            for name, text in context.items():
                chunk = text if len(text) <= budget else text[: max(0, budget)] + "\n...[truncated]"
                budget -= len(chunk)
                parts.append(f"--- CONTEXT: {name} ---\n{chunk}\n")
        parts.append("--- CURRENT ARTIFACT ---")
        parts.append(artifact.render())
        if editable is not None:
            parts.append(f"\nEditable files (glob patterns): {list(editable)}. Other files are locked.")
        parts.append("\n" + EDIT_FORMAT)
        return "\n".join(parts)

    def edit(self, artifact, instructions, *, context=None, editable=None, system=None, seed=None, role="proposer"):
        prompt = self.build_prompt(artifact, instructions, context, editable)
        resp = self.llm.complete(prompt, system=system, seed=seed, role=role)
        if not resp.ok:
            return Proposal(None, raw=resp.text, usage=resp.usage, error=f"llm error: {resp.error}")
        return parse_proposal(artifact, resp.text, editable, resp.usage)


def parse_proposal(artifact: Artifact, text: str, editable=None, usage: Optional[Usage] = None) -> Proposal:
    """Turn a reply in :data:`EDIT_FORMAT` into a :class:`Proposal`: the JSON header
    before the first ``=== FILE:`` block gives change/hypothesis/components; file
    blocks replace whole files (``<<DELETE>>`` deletes); edits outside
    ``editable`` are dropped into ``blocked_files``. ``error`` is set when there
    are no file blocks or the result equals ``artifact``."""
    text = text or ""
    header: dict = {}
    try:
        h = extract_json(text.split("=== FILE:")[0]) if "=== FILE:" in text else extract_json(text)
        if isinstance(h, dict):
            header = h
    except ValueError:
        pass
    files = parse_file_blocks(text)
    updates = {k: (None if v.strip() == "<<DELETE>>" else v) for k, v in files.items()}
    if not updates:
        return Proposal(None, raw=text, usage=usage or Usage(), error="no file blocks in reply",
                        change=str(header.get("change", "")))
    new, blocked = apply_scope(artifact, updates, editable)
    change, hypothesis, comps = _header_fields(header)
    prop = Proposal(new, change=change, hypothesis=hypothesis, components=comps, raw=text, usage=usage or Usage(),
                    blocked_files=blocked)
    if new == artifact:
        prop.error = "no effective change" + (f" (blocked edits to {blocked})" if blocked else "")
    return prop


AGENT_SUFFIX = """
When you are done, write a file named `_proposal.json` in the working directory with keys
"change" (one line), "hypothesis" (why it helps on unseen tasks) and "components" (list).
Read-only context (history, traces, scores) is in the `_context/` directory; do not edit it.
"""


class AgentEditor(Editor):
    """Headless coding agent in a scratch directory.

    ``context`` files go under ``_context/`` (removed before the result is read
    back); a context name that is absolute or escapes that directory is skipped
    and listed in ``Proposal.meta["skipped_context"]``."""

    def __init__(self, cli: ClaudeCLI, tools: Sequence[str] = ("Read", "Edit", "Write", "Glob", "Grep"),
                 timeout_s: float = 900.0, keep_dirs: bool = False) -> None:
        self.cli = cli
        self.tools = tuple(tools)
        self.timeout_s = timeout_s
        self.keep_dirs = keep_dirs

    def edit(self, artifact, instructions, *, context=None, editable=None, system=None, seed=None, role="proposer"):
        work = Path(tempfile.mkdtemp(prefix="rsi_agent_"))
        skipped_context = []
        try:
            artifact.to_dir(work)
            ctx_dir = work / "_context"
            for name, text in (context or {}).items():
                if not is_safe_relpath(str(name)):   # "../harness.py" would overwrite the artifact itself
                    skipped_context.append(str(name))
                    continue
                p = ctx_dir / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            prompt = instructions.strip() + "\n" + (
                f"\nYou may only edit files matching {list(editable)}.\n" if editable is not None else "") + AGENT_SUFFIX
            resp = self.cli.run_agent(prompt, cwd=str(work), system=system, tools=self.tools,
                                      timeout_s=self.timeout_s, role=role)
            header: object = {}
            hp = work / "_proposal.json"
            if hp.exists():
                try:
                    header = json.loads(hp.read_text())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    header = {}
                hp.unlink()
            if ctx_dir.exists():
                shutil.rmtree(ctx_dir)
            after = Artifact.from_dir(work)
            updates = {n: after.get(n) for n in artifact.changed_files(after)}
            new, blocked = apply_scope(artifact, updates, editable)
            change, hypothesis, comps = _header_fields(header)
            prop = Proposal(new, change=change, hypothesis=hypothesis, components=comps, raw=resp.text,
                            usage=resp.usage, blocked_files=blocked,
                            error=None if resp.ok else f"agent error: {resp.error}",
                            meta={"workdir": str(work)} if self.keep_dirs else {})
            if skipped_context:
                prop.meta["skipped_context"] = skipped_context
            if prop.error is None and new == artifact:
                prop.error = "no effective change"
            return prop
        finally:
            if not self.keep_dirs:
                shutil.rmtree(work, ignore_errors=True)
