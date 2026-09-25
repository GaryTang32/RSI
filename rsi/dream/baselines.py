"""Baselines: Recursive Fixed Exploration and history-as-written-guidance.

* **ParallelRefine / Recursive Fixed Exploration** [paper:§4 p.6-7] - the same loop,
  agent, evaluator, initialization and per-round cap, but the starting policy (parallel
  refine) "is kept fixed across recursive discovery rounds": ``Config(dream=False)``.
  :func:`fixed_config` builds it from any Dream config.
* **Guidance** [paper:§5.1 p.11] - past trajectories are abstracted "into high-level
  directional insights, which are directly injected into the prompt as explicit
  semantic guidance for subsequent rounds". A :class:`GuidanceSummarizer` turns the
  history into advice ``{"text", "focus", "avoid"}``; the loop injects ``text`` as
  ``$direction_guidance`` and the direction provider steers new roots toward ``focus``.
  :class:`MockGuidanceSummarizer` is deterministic (focus = directions whose branches
  produced the largest gains); :class:`LLMGuidanceSummarizer` asks an LLM.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Optional, Sequence

from ..core.llm import LLM, extract_json


@dataclass
class Guidance:
    text: str = ""
    focus: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)

    def as_provider_hint(self, strength: float) -> dict:
        return {"focus": list(self.focus), "strength": strength} if self.focus else {}


class GuidanceSummarizer:
    def summarize(self, worlds: Sequence, directions: Sequence[str]) -> Guidance:  # pragma: no cover
        raise NotImplementedError


def direction_gains(worlds: Sequence) -> dict[str, float]:
    """Best gain over the round's root achieved by branches of each direction (all worlds)."""
    out: dict[str, float] = {}
    for w in worlds:
        root = w.root_score
        for b, cells in w.branches().items():
            d = (w.branch_tags.get(b) or {}).get("direction")
            if d is None:
                continue
            g = max([c.score - root for c in cells if c.success and c.score is not None] or [0.0])
            out[d] = max(out.get(d, float("-inf")), g)
    return out


class MockGuidanceSummarizer(GuidanceSummarizer):
    """Deterministic summarizer: focus on the ``top`` historically best directions."""

    def __init__(self, top: int = 2) -> None:
        self.top = top

    def summarize(self, worlds, directions) -> Guidance:
        gains = direction_gains(worlds)
        if not gains:
            return Guidance()
        ranked = sorted(gains, key=lambda d: (-gains[d], d))
        focus = ranked[: self.top]
        avoid = [d for d in ranked[self.top:] if gains[d] <= 0]
        text = ("Guidance distilled from earlier searches: the largest gains came from "
                + ", ".join(f"{d} (+{gains[d]:.3g})" for d in focus)
                + ". Concentrate new attempts on these directions"
                + (f"; avoid {', '.join(avoid)}, which did not improve." if avoid else "."))
        return Guidance(text, focus, avoid)


GUIDANCE_PROMPT = """\
You summarize past discovery searches into high-level directional guidance for the next search.
Directions available: {directions}

Past attempts (direction, score over the round's starting program, failure if any):
{records}

Reply with a JSON object: {{"advice": "<3-5 sentences of directional insight>",
"focus": [<directions to concentrate on>], "avoid": [<directions to avoid>]}}
"""


class LLMGuidanceSummarizer(GuidanceSummarizer):
    def __init__(self, llm: LLM, *, max_records: int = 120, role: str = "summarizer") -> None:
        self.llm, self.max_records, self.role = llm, max_records, role

    def summarize(self, worlds, directions) -> Guidance:
        rows = []
        for w in worlds:
            root = w.root_score
            for n in w.non_root():
                d = (w.branch_tags.get(n.branch) or {}).get("direction", "?")
                res = f"{n.score - root:+.4g}" if n.success and n.score is not None else f"failed ({n.fail_class})"
                rows.append(f"- round {w.meta.get('round', '?')} {n.id} dir={d}: {res}. {n.proposal[:120]}")
        prompt = GUIDANCE_PROMPT.format(directions=list(directions), records="\n".join(rows[-self.max_records:]))
        resp = self.llm.complete(prompt, role=self.role)
        try:
            d = extract_json(resp.text)
            focus = [str(x) for x in d.get("focus", []) if str(x) in set(directions)] if directions else \
                [str(x) for x in d.get("focus", [])]
            return Guidance(str(d.get("advice", ""))[:1500], focus, [str(x) for x in d.get("avoid", [])])
        except (ValueError, AttributeError):
            return Guidance(resp.text[:1500])


def fixed_config(cfg, guidance: bool = False):
    """Recursive Fixed Exploration with the same budget/agent/evaluator settings as ``cfg``."""
    return replace(cfg, dream=False, guidance=guidance)


def guidance_json(g: Optional[Guidance]) -> str:
    return json.dumps({"text": g.text, "focus": g.focus, "avoid": g.avoid} if g else {})
