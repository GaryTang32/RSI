"""Research roles for SoL-Pi lineages: default idea pools, offline proposers, LLM proposers/reviewers.

Offline (deterministic):

* :data:`AGENTWORLD_IDEAS` - a small idea pool over the six proposal families: the
  four released mechanisms under their blog idea ids (C23 ObservationPack, P8 Action
  Fusion, D1 EPR, C6 OCC - the last three mappings are the spec's guess), plus
  environment-specific tricks, do-less shortcuts and a dud, so the protocol has
  something to reject;
* :class:`LibraryProposer` - implements an idea as a registry mechanism and walks
  its parameter grid from aggressive to conservative using only in-lineage gate
  feedback (capability failure -> more conservative; no efficiency gain -> more
  aggressive; revisiting a variant -> the lineage is exhausted), like the
  ObservationPack V0..V7 sweep;
* :data:`AGENTQA_IDEAS` + :class:`AgentQAEditProposer` - the same protocol on
  AgentQA harness code (second domain).

LLM-backed: :class:`LLMMechanismProposer` writes a new extension against the
runtime hook API (:data:`RUNTIME_API_DOC`) for SoL-Pi harnesses, or edits any other
artifact with :class:`rsi.core.RewriteEditor`; :class:`LLMReviewer` checks the
behavioural contract.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..core.artifact import Artifact, parse_file_blocks
from ..core.editors import RewriteEditor
from ..core.llm import LLM, Usage, extract_json
from .registry import harness_config, with_mechanism
from .research import Idea, MechanismProposal

AGENTWORLD_IDEAS = [
    Idea("C23", "C", "Build observation packs before paying for full bodies", "observation_pack",
         [{"full_sends": 1, "excerpt_bytes": 0}, {"full_sends": 1, "excerpt_bytes": 1024},
          {"full_sends": 2, "excerpt_bytes": 1024}, {"full_sends": 2, "excerpt_bytes": 2048}],
         "replayed_large_outputs"),
    Idea("P8", "P", "Couple successful mutations to their verifier", "action_fusion", [{}], "adjacent_edit_command"),
    Idea("D1", "D", "Delegate oversized result compression with deterministic fallback",
         "evidence_preserving_reducer", [{"reducer": "deterministic"}], "diagnostic_log_tokens"),
    Idea("C6", "C", "Use layered context epochs and event-driven compaction", "online_context_compact",
         [{"cache_write_read_ratio": 12.5}], "archivable_context"),
    Idea("T3", "T", "Keep only the last lines of long command outputs", "tail_trim",
         [{"lines": 20}, {"lines": 40}, {"lines": 120}], "tail_of_outputs", kind="trick"),
    Idea("T7", "T", "Keep only the first lines of long command outputs", "head_trim",
         [{"lines": 40}, {"lines": 120}], "tail_of_outputs", kind="trick"),
    Idea("T11", "T", "Run the test runner in quiet mode by default", "pytest_quiet", [{}], "diagnostic_log_tokens",
         kind="trick"),
    Idea("P14", "P", "Skip redundant re-verification after edits", "no_verify", [{}], "adjacent_edit_command",
         kind="do_less"),
    Idea("P20", "P", "Cap the turn budget", "turn_cap", [{"max_turns": 10}, {"max_turns": 16}, {"max_turns": 24}],
         "prompt_tokens", kind="do_less"),
    Idea("R5", "R", "Slim the system prompt", "prompt_slim", [{}], "prompt_tokens", kind="dud"),
]


class LibraryProposer:
    """Maps an idea to its registry mechanism; walks the variant grid on gate feedback."""

    def propose(self, idea: Idea, base: Artifact, evidence: dict, history: list[dict]) -> MechanismProposal:
        grid = idea.grid or [{}]
        visited = [h["variant"] for h in history if "variant" in h]
        if not history:
            v = 0
        else:
            last = history[-1]
            g = last.get("gate") or {}
            cap_failed = any(not c.get("pass", True) for c in (g.get("capability") or {}).values()) or \
                any(not pf.get("cap_ok", True) for pf in (g.get("per_family") or {}).values())
            v = last.get("variant", 0) + (1 if cap_failed or last.get("outcome") != "gate_failed" else -1)
            if last.get("outcome") == "frozen":          # sweep mode: continue through the grid
                v = max(visited) + 1
            if v < 0 or v >= len(grid) or v in visited:
                return MechanismProposal(None, error="variant grid exhausted", variant=v, meta={"exhausted": True})
        params = grid[v]
        files = with_mechanism(base.files, idea.mechanism, params)
        return MechanismProposal(Artifact(files), change=f"{idea.mechanism}{json.dumps(params, sort_keys=True)}",
                                 variant=v)

    def fix(self, idea, prop, error):
        return MechanismProposal(None, error=f"cannot repair: {error}", variant=prop.variant,
                                 meta={"exhausted": True})


# ------------------------------------------------------------------ AgentQA (second domain)
AGENTQA_IDEAS = [
    Idea("T1", "T", "Compute with the Python tool instead of long free-text reasoning", "tool", [{}], "none"),
    Idea("R2", "R", "Drop step-by-step reasoning from the prompt", "no_cot", [{}], "none", kind="do_less"),
    Idea("R3", "R", "Ask for a compact 'ANSWER:' line", "format", [{}], "none"),
    Idea("P4", "P", "Fall back to a direct answer when the tool fails", "fallback", [{}], "none", kind="dud"),
]


class AgentQAEditProposer:
    """Offline proposer for AgentQA harnesses (uses the Meta-Harness program library)."""

    def __init__(self) -> None:
        from ..metaharness.mock import AgentQALibrary
        self.lib = AgentQALibrary()

    def propose(self, idea, base, evidence, history):
        if history:
            return MechanismProposal(None, error="single variant", meta={"exhausted": True})
        g = self.lib.parse(base.files, "base") or {}
        g = {**g, "cot": False} if idea.mechanism == "no_cot" else self.lib.apply(g, idea.mechanism)
        return MechanismProposal(self.lib.render(g, base), change=idea.mechanism)

    def fix(self, idea, prop, error):
        return MechanismProposal(None, error=error, meta={"exhausted": True})


# ------------------------------------------------------------------ LLM roles
RUNTIME_API_DOC = """\
Runtime hook API (Python). An extension is a class `MECHANISM(Extension)` with `name` and `register(self, rt)`:
- rt.register_tool(ToolSpec(name, description, parameters: dict, execute(args, rt, call_id) -> ToolResult),
  replaces=None | "edit" | "write" | "bash" | "read"); rt.builtin(name) returns the original tool it replaced.
- rt.on("context", fn(messages, rt) -> new list | None)   # projection of what is sent; never mutate history
- rt.on("before_provider_request", fn(messages, rt)); rt.on("tool_result", fn(event, rt) -> ToolResult | None)
  (event.call.name, event.call.args, event.result.content, event.result.is_error)
- rt.on("turn_end", fn(reply, results, rt)); rt.on("agent_settled", fn(rt)); rt.on("session_compact", fn(ev, rt))
- rt.compact(instructions) -> summary | None; rt.abort(); rt.send_hidden(text, trigger_turn=True)
- rt.store: dict[path, text] private object store (bash can read paths under /.solpi/); rt.meter: TokenMeter
- Message(role, content, tool_calls, tool_call_id, tool_name, is_error); m.with_content(text) copies a message.
- ToolResult(content, is_error=False, details={}); raise ToolError(msg) for tool errors.
Mechanisms must fail open (on any exception keep the original behaviour) and must not hide information the agent
needs to succeed; they must not mention particular tasks, files or environments."""

MECH_PROMPT = """You are the mechanism proposer and implementer of one auto-research lineage.
Idea: {title} (family {family}).
Goal: reduce token traffic / API cost of the agent harness WITHOUT reducing task success, across many different
environments (the same mechanism will be judged on environments you never see).

Evidence from trajectory analysis (map-reduce over rollouts):
{evidence}

Earlier attempts in this lineage (gate feedback on the training screen):
{history}

{api}

Reply with a ```json fence {{"name": "<snake_case_mechanism_name>", "params": {{}}, "change": "<one line>"}}
followed by the complete code:
=== FILE: extensions/<name>.py ===
<code defining MECHANISM>"""


class LLMMechanismProposer:
    """Writes one mechanism per iteration (code extension or generic artifact edit)."""

    def __init__(self, llm: LLM) -> None:
        self.llm = llm
        self.editor = RewriteEditor(llm)

    def _history(self, history):
        return "\n".join(f"- iter {h['iteration']}: {h.get('change', '')} -> {h.get('outcome')} "
                         f"({(h.get('gate') or {}).get('reason', h.get('error', ''))})" for h in history) or "(none)"

    def propose(self, idea, base, evidence, history):
        ev = json.dumps({k: v for k, v in evidence.items() if k != "worst"}, default=float)
        if "harness.json" in base:
            prompt = MECH_PROMPT.format(title=idea.title, family=idea.family, evidence=ev,
                                        history=self._history(history), api=RUNTIME_API_DOC)
            resp = self.llm.complete(prompt, role="proposer", seed=len(history))
            if not resp.ok:
                return MechanismProposal(None, error=f"llm error: {resp.error}", usage=resp.usage)
            return self._parse(resp.text, base, resp.usage, len(history))
        instr = (f"Implement ONE efficiency mechanism in this harness: {idea.title}. Goal: fewer tokens / lower cost "
                 f"at unchanged accuracy on unseen tasks. Evidence: {ev}. Earlier attempts:\n{self._history(history)}")
        p = self.editor.edit(base, instr, seed=len(history), role="proposer")
        return MechanismProposal(p.artifact if p.ok else None, change=p.change, usage=p.usage, error=p.error,
                                 variant=len(history))

    def _parse(self, text, base, usage, variant) -> MechanismProposal:
        try:
            h = extract_json(text.split("=== FILE:")[0])
        except ValueError:
            h = {}
        files = parse_file_blocks(text)
        code_path = next((p for p in files if p.startswith("extensions/")), None)
        if not code_path:
            return MechanismProposal(None, error="no extensions/<name>.py in reply", usage=usage, variant=variant)
        from ..metaharness.proposer import clean_code_block
        files[code_path] = clean_code_block(files[code_path])
        name = re.sub(r"\W", "_", str(h.get("name") or code_path[11:-3]))
        art = Artifact(with_mechanism(base.files, name, h.get("params") or {}, files[code_path]))
        if code_path != f"extensions/{name}.py":
            art = Artifact({**{k: v for k, v in art.files.items() if k != f"extensions/{name}.py"},
                            f"extensions/{name}.py": files[code_path]})
        return MechanismProposal(art, change=str(h.get("change", name))[:200], usage=usage, variant=variant,
                                 meta={"code": files[code_path]})

    def fix(self, idea, prop, error):
        if prop.artifact is None or "code" not in prop.meta:
            return MechanismProposal(None, error=error, meta={"exhausted": True})
        prompt = (f"This mechanism failed its exit check with:\n{error[:2000]}\n\nCode:\n```python\n{prop.meta['code']}"
                  f"\n```\n{RUNTIME_API_DOC}\nReturn the same JSON header and the corrected complete file in the same "
                  f"format (=== FILE: extensions/<name>.py ===).")
        resp = self.llm.complete(prompt, role="implementer", seed=prop.variant)
        if not resp.ok:
            return MechanismProposal(None, error=f"llm error: {resp.error}", usage=resp.usage)
        base = Artifact({k: v for k, v in prop.artifact.files.items() if not k.startswith("extensions/")})
        cfg = harness_config(base.files)
        return self._parse(resp.text, base, resp.usage, prop.variant)


REVIEW_PROMPT = """You are an independent reviewer in an auto-research loop. Check the implementation of the idea
"{title}" against its behavioural contract:
1. it only changes harness mechanisms (no task-specific content, no references to evaluation data or environments);
2. it fails open: on any error the original behaviour is kept;
3. it never removes information the agent needs without a way to get it back (e.g. a handle to recall it);
4. it does not make the agent do less work (skipping verification, capping effort) to save tokens.
Diff:
```diff
{diff}
```
Reply with JSON {{"verdict": "pass" | "fail", "reasons": ["..."]}}."""


class LLMReviewer:
    def __init__(self, llm: LLM, domain=None, task_llm=None) -> None:
        self.llm, self.domain, self.task_llm = llm, domain, task_llm

    def review(self, idea, base, cand):
        if self.domain is not None:
            err = self.domain.smoke(cand, self.task_llm)
            if err:
                return False, f"smoke failed: {err}"
        resp = self.llm.complete(REVIEW_PROMPT.format(title=idea.title, diff=base.diff(cand)[:20000]),
                                 role="reviewer", seed=0)
        try:
            d = extract_json(resp.text)
            ok = str(d.get("verdict", "")).lower().startswith("pass")
            return ok, "; ".join(map(str, d.get("reasons", [])))[:500] or ("pass" if ok else "fail")
        except (ValueError, AttributeError):
            return False, "review unparseable (fails closed)"
