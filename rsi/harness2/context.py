"""Evidence as files: the ``context/`` tree of a session, rendered as ``=== context/<path> ===`` text blocks."""
from __future__ import annotations

import json
import re
from typing import Iterable, Mapping, Optional, Sequence

from ..core.artifact import Artifact
from .records import Rollout

Ctx = dict[str, str]
#: closes the rendered evidence in a mission (nothing after it is a context file)
END_MARK = "=== END OF EVIDENCE ==="
BLOCK = re.compile(r"^=== (context/\S[^\n]*?) ===[ \t]*$", re.M)
DESCRIBE = {
    "task.md": "the task as the agent received it",
    "source": "task source files",
    "rollouts": "FULL trajectories",
    "deliverables": "what each rollout produced",
    "harness": "the harness being edited (the editing baseline)",
    "base_harness": "the unmodified base harness",
    "pool": "candidates: harness/, harness.diff (vs context/harness), rollout.md, produced.md",
    "decisions": "the <notes> (or error) of earlier stages",
    "library": "finished harnesses of earlier tasks in this domain (read INDEX.md first)",
    "candidates": "one rendered deliverable per anonymous candidate",
    "adoption.json": "the adopted label, the anonymous ids and the selection reason",
}


def clip(text: str, n: int) -> str:
    """Keep head and tail of an over-long observation, with an explicit renderer marker."""
    text = str(text or "")
    if n <= 0 or len(text) <= n:
        return text
    h = n // 2
    return text[:h] + f"\n[renderer-truncated: {len(text) - 2 * h} chars omitted by the renderer]\n" + text[-h:]


def rollout_md(r: Rollout, obs_trunc: int) -> str:
    ex = r.execution
    head = (f"# rollout {r.label}\nharness: {r.tree_id[:10]}\ndelivered: {'yes' if r.delivered else 'no'}\n"
            f"steps: {ex.steps}  tokens: {ex.tokens}\n")
    err = f"\n## error\n{ex.error}\n" if ex.error else ""
    return head + err + "\n## trajectory\n" + clip(ex.trace or "(no events recorded)", obs_trunc) + "\n"


def produced_md(r: Rollout, obs_trunc: int) -> str:
    return clip(str(r.execution.output or ""), obs_trunc) if r.delivered else "(nothing produced)"


def _put_tree(ctx: Ctx, prefix: str, tree: Artifact) -> None:
    for name, text in tree.items():
        ctx[f"{prefix}/{name}"] = text


def build(task_text: str, *, harness: Artifact, rollouts: Sequence[Rollout] = (),
          pool: Sequence[tuple[str, Artifact, Optional[Rollout]]] = (), decisions: Mapping[str, str] = {},
          library: Mapping[str, str] = {}, source: Mapping[str, str] = {}, obs_trunc: int = 50000) -> Ctx:
    """The context tree of an editor or composer session."""
    ctx: Ctx = {"context/task.md": task_text}
    for name, text in source.items():
        ctx[f"context/source/{name}"] = text
    for r in rollouts:
        ctx[f"context/rollouts/{r.label}.md"] = rollout_md(r, obs_trunc)
        ctx[f"context/deliverables/{r.label}.md"] = produced_md(r, obs_trunc)
    _put_tree(ctx, "context/harness", harness)
    for label, tree, r in pool:
        _put_tree(ctx, f"context/pool/{label}/harness", tree)
        ctx[f"context/pool/{label}/harness.diff"] = harness.diff(tree) or "(no change)"
        if r is not None:
            ctx[f"context/pool/{label}/rollout.md"] = rollout_md(r, obs_trunc)
            ctx[f"context/pool/{label}/produced.md"] = produced_md(r, obs_trunc)
    for label, notes in decisions.items():
        ctx[f"context/decisions/{label}.md"] = notes or "(no notes)"
    for name, text in library.items():
        ctx[f"context/library/{name}"] = text
    return ctx


def selector_context(task_text: str, candidates: Mapping[str, str], source: Mapping[str, str] = {}) -> Ctx:
    ctx: Ctx = {"context/task.md": task_text}
    for name, text in source.items():
        ctx[f"context/source/{name}"] = text
    for cid, text in candidates.items():
        lines = text.count("\n") + 1
        ctx[f"context/candidates/{cid}.md"] = (f"<!-- candidate {cid}: {len(text)} chars, {lines} lines. "
                                               f"COMPLETE -- nothing trimmed. -->\n{text}")
    return ctx


def reflector_context(task_text: str, *, adopted: Artifact, base: Artifact, rollouts: Sequence[Rollout],
                      pool: Sequence[tuple[str, Artifact, Optional[Rollout]]], decisions: Mapping[str, str],
                      adoption: dict, obs_trunc: int = 50000) -> Ctx:
    """The complete improvement record D_t (no library)."""
    ctx = build(task_text, harness=adopted, rollouts=rollouts, pool=pool, decisions=decisions, obs_trunc=obs_trunc)
    _put_tree(ctx, "context/base_harness", base)
    ctx["context/adoption.json"] = json.dumps(adoption, indent=1, sort_keys=True)
    return ctx


def _group(path: str) -> tuple[str, str]:
    rest = path[len("context/"):]
    top, _, sub = rest.partition("/")
    return top, sub.split("/")[0].removesuffix(".md") if sub else ""


def layout(ctx: Iterable[str]) -> str:
    """Listing of the context tree, one line per top-level entry."""
    groups: dict[str, list[str]] = {}
    for path in ctx:
        top, item = _group(path)
        groups.setdefault(top, [])
        if item and item not in groups[top]:
            groups[top].append(item)
    lines = []
    for top, items in groups.items():
        name = f"context/{top}" + ("" if "." in top else "/")
        shown = f" [{', '.join(items)}]" if top in ("rollouts", "deliverables", "pool", "decisions", "candidates") \
            else (f" [{len(items)} entries]" if items else "")
        lines.append(f"{name:<24} {DESCRIBE.get(top, '')}{shown}")
    return "\n".join(lines)


def render(ctx: Mapping[str, str]) -> str:
    return "\n".join(f"=== {path} ===\n{text.rstrip(chr(10))}\n" for path, text in ctx.items())


def parse(text: str) -> Ctx:
    """Inverse of :func:`render` (the mocks read their evidence back this way)."""
    pieces = BLOCK.split((text or "").split("\n" + END_MARK, 1)[0])
    return {pieces[i]: pieces[i + 1].strip("\n") for i in range(1, len(pieces) - 1, 2)}


def subtree(ctx: Mapping[str, str], prefix: str) -> dict[str, str]:
    """Files under ``prefix/`` with the prefix removed."""
    p = prefix.rstrip("/") + "/"
    return {k[len(p):]: v for k, v in ctx.items() if k.startswith(p)}
