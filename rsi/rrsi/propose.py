"""The regularized proposer P_reg(. | H_t, F_t, L_t, b_t, E_t, B_t) of Algorithm 1
(mirrors ``rrsi/propose.py``), built on :class:`rsi.core.Editor`.

An LLM "harness engineer" edits the harness and finishes with a ``done()`` object that
declares at most b_t independent, tagged edits. It is conditioned on

* F_t  the analyst's three-lens report, per-task digests and rendered traces,
* L_t  the edit history (component, hypothesis, dS, dC, accepted, outcome),
* b_t  the annealed edit budget (||z_t||_0 <= b_t),
* E_t  exploration directives (stall flag, untried components, reserved slot),
* B_t  components to prune with the accepted machinery to remove,
* the attribution scoreboard and the constitution (SKILL.md / PATTERNS.md).

The released code drives a multi-turn JSON action protocol (list/read/edit/write/done).
Here any :class:`rsi.core.Editor` does the editing: :class:`RRSIRewriteEditor` (one
completion returning whole files + the done() JSON; works with MockLLM, ClaudeCLI and
the API) or :class:`rsi.core.AgentEditor` (a ``claude -p`` coding agent that writes the
done() object to ``_rrsi_done.json``). The done() contract is enforced exactly as in
the code: a submission with file changes but an invalid edit list (over budget, missing
required fields, component not in K, reserved slot not honoured) is BOUNCED back with
the problems; "there is no abort action" (aborts are bounced up to 3 times); declaring
edits with zero file changes is bounced. Repair mode keeps the previous edits in the
working tree and asks to fix only what the critic objected to.
"""
from __future__ import annotations

import json
import re
from typing import Optional, Sequence

from ..core.artifact import Artifact, parse_file_blocks
from ..core.editors import Editor, RewriteEditor
from ..core.llm import Usage, extract_json
from .components import Taxonomy

REQUIRED_FIELDS = ("id", "component", "hypothesis", "targets_mode", "predicted_affected", "retroactive_check")
DONE_FILE = "_rrsi_done.json"

SYSTEM_TMPL = """You are a harness engineer agent. You directly modify the source
code of an LLM-agent scaffold (the "harness") to fix recurring failure modes
observed on an evolve set of tasks. The policy LLM is frozen and is a DIFFERENT
model from you: do not assume it shares your capabilities, habits or judgment.
Improve the harness from ITS perspective, using the trajectories as evidence of
how it actually behaves. The ONLY thing you can change is the harness files;
the tool environment, the grader and the task set are frozen.

{domain_brief}

You finish by submitting a done() object together with the changed files:
  {{"action": "done", "summary": "one-line summary of this candidate",
   "edits": [
     {{"id": "C1",
      "component": "one of: {components}",
      "hypothesis": "one sentence: the mechanism and WHY it should move the score",
      "targets_mode": "failure mode / capability gap / habit it targets",
      "why_not_lower_lever": "why a plain instruction edit would NOT fix this (or, for a prompt edit, why prose IS the right lever here)",
      "trigger_condition": "the exact, checkable condition under which the mechanism activates ('always' is almost never right)",
      "predicted_affected": ["<task id>", "..."],
      "retroactive_check": "three-part counterfactual: (corrective) which cited failing tasks would have moved had this existed; (preservative) which success habits could this disrupt and why it won't; (transfer) why it generalizes beyond this evolve set",
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
contract (entry points, signatures, output schema) stays unchanged; the model
name, step budget and timeouts are injected externally so editing them has no
effect. All code and comments in English."""

RRSI_EDIT_FORMAT = """--- OUTPUT FORMAT ---
Respond in exactly this format:
1. A ```json fenced block holding the done() object described above
   ({"action": "done", "summary": ..., "edits": [...]}).
2. Then, for every file you change or create, the COMPLETE new file content:
=== FILE: <path> ===
<entire file content>
Files you do not list stay unchanged. To delete a file, emit `=== FILE: <path> ===`
followed by the single line `<<DELETE>>`.
(If you are a file-editing agent instead, edit the files in place and write the done()
object as JSON to `_rrsi_done.json`.)
"""

HISTORY_HDR = ("EDIT HISTORY L_t (every measured edit: component, hypothesis, Delta S, Delta C, accepted). "
               "A rejected mechanism is negative evidence; do not redraw it unchanged. An accepted one carries "
               "the gain it produced; refine what has known credit, not what merely preceded a rise.")
SCOREBOARD_HDR = ("ATTRIBUTION SCOREBOARD (how past edits' predictions fared; unpredicted_regressions are tasks an "
                  "edit likely broke)")
PRUNE_HDR = ("COMPONENTS TO PRUNE B_t (exercised, no strictly improving edit in the recent window; remove the "
             "accepted machinery listed, it has stopped earning its place)")
REPORT_HDR = ("THREE-LENS ANALYSIS REPORT F_t (failure modes ranked; capability gaps often need tool/plumbing "
              "fixes; success_habits are behaviors your change MUST NOT break)")
TASK_TEXT = ("Address the highest-impact failure modes / capability gaps within your edit budget. Implement the "
             "change and submit the done() object with the edits array.")
REPAIR_TEXT = ("REPAIR ROUND: your previous edits for this candidate are already in the working tree (reflected in "
               "CURRENT HARNESS SOURCE above). The reviewer raised the objections below. Fix ONLY what the objections "
               "require (remove leaked content, split or re-declare edits, wire up dead code, or delete the offending "
               "part) with minimal additional edits, then submit done() again with the corrected edits array.")


AGENT_NOTE = """
You are working as a file-editing agent: edit the harness files in place, read the round context
(directives, edit budget, exploration, prune set, history, scoreboard, analysis, traces, TASK) from
the `_context/` directory, and when finished write the done() object as JSON to `_rrsi_done.json`
in the working directory (it is removed before review)."""


class RRSIRewriteEditor(RewriteEditor):
    """:class:`rsi.core.RewriteEditor` with the RRSI prompt layout: constitution (stable
    prefix) -> context sections -> current harness -> TASK -> output format."""

    def build_prompt(self, artifact, instructions, context, editable) -> str:
        parts = [instructions.strip(), ""]
        ctx = dict(context or {})
        task = ctx.pop("task", None)
        budget = self.max_context_chars
        for name, text in ctx.items():
            chunk = text if len(text) <= budget else text[: max(0, budget)] + "\n...[truncated]"
            budget -= len(chunk)
            parts.append(f"--- CONTEXT: {name} ---\n{chunk}\n")
        parts.append("--- CURRENT HARNESS SOURCE H_t ---")
        parts.append(artifact.render())
        if editable is not None:
            parts.append(f"\nEditable files (glob patterns): {list(editable)}. Other files are locked.")
        if task:
            parts.append(f"\n--- TASK ---\n{task}")
        parts.append("\n" + RRSI_EDIT_FORMAT)
        return "\n".join(parts)


_SECTION = re.compile(r"^--- (?:CONTEXT: (.+?)|(CURRENT HARNESS SOURCE H_t)|(TASK)|(OUTPUT FORMAT)) ---[ \t]*$", re.M)


def parse_sections(prompt: str) -> dict:
    """Split an :class:`RRSIRewriteEditor` prompt back into its parts (a helper for
    scripted mock proposers): ``{"sections": {name: text}, "files": {path: text},
    "task": str, "directives": dict}``."""
    out: dict = {"sections": {}, "files": {}, "task": "", "directives": {}}
    marks = list(_SECTION.finditer(prompt or ""))
    for i, m in enumerate(marks):
        body = prompt[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(prompt)].strip("\n")
        if m.group(1):
            out["sections"][m.group(1).strip()] = body
        elif m.group(2):
            out["files"] = parse_file_blocks(body.split("\nEditable files (glob patterns):")[0])
        elif m.group(3):
            out["task"] = body
    try:
        out["directives"] = json.loads(out["sections"].get("round_directives", "{}"))
    except json.JSONDecodeError:
        out["directives"] = {}
    return out


def parse_done(text: str) -> Optional[dict]:
    """The done()/abort object of a reply (JSON before the first file block)."""
    head = (text or "").split("=== FILE:")[0]
    try:
        d = extract_json(head)
    except ValueError:
        return None
    if isinstance(d, list):
        d = next((x for x in d if isinstance(x, dict)), None)
    if not isinstance(d, dict):
        return None
    if d.get("action") in ("done", "abort") or "edits" in d:
        d.setdefault("action", "done")
        return d
    return None


class Proposer:
    """Draft one candidate from the incumbent under the RRSI directives."""

    def __init__(self, editor: Editor, taxonomy: Taxonomy, cfg, *, domain_brief: str = "",
                 constitution: tuple[str, str] = ("", ""), editable: Optional[Sequence[str]] = None) -> None:
        self.editor = editor
        self.tax = taxonomy
        self.cfg = cfg
        self.system = SYSTEM_TMPL.format(domain_brief=domain_brief, components=" | ".join(taxonomy.K))
        skill_md, patterns_md = constitution
        self.stable = "\n\n".join(["=== CONSTITUTION (SKILL.md) ===", skill_md,
                                   "=== PATTERN LIBRARY (PATTERNS.md) ===", patterns_md])
        self.editable = None if editable is None else list(editable) + [DONE_FILE]

    # ------------------------------------------------------------------ context
    def build_context(self, *, directives: dict, variant_brief: str, history_rows: list, scoreboard: list,
                      explore: dict, reserved: bool, prune_set: list, report: dict, budget: int, digests: list,
                      traces_text: str, task_text: str) -> dict[str, str]:
        explore_text = explore.get("text", "") or "(no exploration directives this run)"
        if reserved:
            explore_text += ("\n\nRESERVED EXPLORATION SLOT: this variant holds one. At least one of your edits MUST "
                             f"be on a never-exercised component from: {explore.get('untried')}.")
        return {
            "round_directives": json.dumps(directives, default=str),
            "variant_brief": variant_brief,
            "edit_budget": (f"THIS ROUND'S EDIT BUDGET b_t: You may ship AT MOST {budget} independent edit(s) in this "
                            f"candidate (the budget anneals over the run: early rounds explore, late rounds make "
                            f"single attributable changes). Ship fewer if the evidence supports fewer."),
            "exploration_directives": "EXPLORATION DIRECTIVES E_t\n" + explore_text,
            "components_to_prune": PRUNE_HDR + "\n" + (json.dumps(prune_set, ensure_ascii=False, indent=1)
                                                       if prune_set else "(none)"),
            "edit_history": HISTORY_HDR + "\n" + json.dumps(history_rows, ensure_ascii=False, indent=1, default=str),
            "attribution_scoreboard": SCOREBOARD_HDR + "\n" + json.dumps(scoreboard or [], ensure_ascii=False, indent=1),
            "analysis_report": REPORT_HDR + "\n" + json.dumps(report, ensure_ascii=False, indent=1, default=str),
            "per_task_digests": json.dumps(digests or [], ensure_ascii=False, indent=1, default=str),
            "traces": traces_text or "(none)",
            "task": task_text,
        }

    # --------------------------------------------------------------- validation
    def validate(self, edits: list, n_changes: int, budget: int, reserved: bool, explore: dict) -> list[str]:
        problems = []
        if n_changes > 0 and not edits:
            problems.append("edits array is empty")
        if len(edits) > budget:
            problems.append(f"{len(edits)} edits exceed the budget b_t = {budget}")
        for e in edits:
            if not isinstance(e, dict):
                problems.append(f"edit {e!r} is not an object")
                continue
            miss = [f for f in REQUIRED_FIELDS if not e.get(f)]
            if miss:
                problems.append(f"edit {e.get('id', '?')} missing {miss}")
            comp = self.tax.canonical(str(e.get("component", "")))
            if e.get("component") and comp not in self.tax.K:
                problems.append(f"edit {e.get('id')} component {e['component']!r} not in {self.tax.K}")
        untried = explore.get("untried") or []
        if reserved and untried and edits and not any(
                isinstance(e, dict) and self.tax.canonical(str(e.get("component", ""))) in untried for e in edits):
            problems.append("this variant holds a RESERVED EXPLORATION SLOT: at least one edit must be on a "
                            f"never-exercised component from {untried}")
        return problems

    # ------------------------------------------------------------------ propose
    def propose(self, base: Artifact, *, working: Optional[Artifact] = None, directives: dict,
                variant_brief: str = "", history_rows: Optional[list] = None, scoreboard: Optional[list] = None,
                explore: Optional[dict] = None, reserved: bool = False, prune_set: Optional[list] = None,
                report: Optional[dict] = None, budget: int = 1, digests: Optional[list] = None,
                traces_text: str = "", repair_brief: Optional[dict] = None, seed: int = 0) -> dict:
        """Return ``{status, summary, edits, mechanism, targets_mode, n_changes, artifact, log, usage}``.
        ``status`` is ``done`` | ``abort`` | ``max_turns`` | ``error``."""
        explore = explore or {}
        working = working or base
        transcript: list[str] = []
        log: list[dict] = []
        usage = Usage()
        aborts_left = self.cfg.max_abort_bounces
        bounces_left = self.cfg.max_done_bounces
        base_task = (REPAIR_TEXT + "\n\n=== REVIEWER OBJECTIONS ===\n" +
                     json.dumps(repair_brief, ensure_ascii=False, indent=1, default=str)) if repair_brief else TASK_TEXT
        for turn in range(1 + self.cfg.max_done_bounces + self.cfg.max_abort_bounces):
            task_text = base_task + ("\n\n=== INTERACTION LOG ===\n" + "\n".join(transcript) if transcript else "")
            ctx = self.build_context(directives=directives, variant_brief=variant_brief,
                                     history_rows=history_rows or [], scoreboard=scoreboard or [], explore=explore,
                                     reserved=reserved, prune_set=prune_set or [], report=report or {},
                                     budget=budget, digests=digests or [], traces_text=traces_text,
                                     task_text=task_text)
            instructions = self.stable if isinstance(self.editor, RewriteEditor) else self.stable + AGENT_NOTE
            prop = self.editor.edit(working, instructions, context=ctx, editable=self.editable, system=self.system,
                                    seed=seed * 100 + turn, role="proposer")
            usage = usage + prop.usage
            if prop.error and prop.error.startswith(("llm error", "agent error")) and prop.artifact is None:
                return {"status": "error", "reason": prop.error, "edits": [], "n_changes": 0, "artifact": None,
                        "log": log, "usage": usage}
            new_art = prop.artifact if prop.artifact is not None else working
            header = None
            if DONE_FILE in new_art:
                try:
                    header = json.loads(new_art[DONE_FILE])
                except json.JSONDecodeError:
                    header = None
                new_art = new_art.with_files({DONE_FILE: None})
            if header is None:
                header = parse_done(prop.raw)
            n_changes = len(base.changed_files(new_art))
            log.append({"turn": turn, "action": (header or {}).get("action"), "n_changes": n_changes,
                        "blocked": prop.blocked_files, "error": prop.error})
            working = new_art
            if header is None:
                transcript.append("[you] (no done() object)\n[result] ERROR: submit the done() JSON object "
                                  "together with the changed files.")
                bounces_left -= 1
                if bounces_left < 0:
                    break
                continue
            if header.get("action") == "abort":
                if aborts_left > 0:
                    aborts_left -= 1
                    transcript.append("[you] abort\n[result] ERROR: there is no abort action. Pick the most "
                                      "defensible mechanism you can build within the hard rules, implement it, and "
                                      "call done. A rejection is data; an abort is not.")
                    continue
                return {"status": "abort", "reason": header.get("reason"), "edits": [], "n_changes": n_changes,
                        "artifact": None, "log": log, "usage": usage}
            edits = [e for e in (header.get("edits") or header.get("candidates") or [])]
            if n_changes == 0 and edits:
                transcript.append("[you] done\n[result] ERROR: you declared edits but made ZERO file changes. "
                                  "Implement them (emit the changed files), then call done.")
                bounces_left -= 1
                if bounces_left < 0:
                    break
                continue
            problems = self.validate(edits, n_changes, budget, reserved, explore)
            if problems and n_changes > 0:
                transcript.append(f"[you] done\n[result] ERROR: {problems}. Call done again fixed (drop or merge "
                                  f"edits if over budget; add the required edit if a slot is reserved).")
                bounces_left -= 1
                if bounces_left < 0:
                    break
                continue
            for e in edits:
                e["component"] = self.tax.canonical(str(e.get("component", "")))
                e.setdefault("mechanism", e.get("hypothesis"))
            return {"status": "done", "summary": header.get("summary"), "edits": edits,
                    "mechanism": header.get("summary") or "; ".join(str(e.get("hypothesis")) for e in edits)[:200],
                    "targets_mode": ", ".join(str(e.get("targets_mode")) for e in edits)[:200],
                    "n_changes": n_changes, "artifact": new_art if n_changes else None, "log": log, "usage": usage}
        return {"status": "max_turns", "edits": [], "n_changes": 0, "artifact": None, "log": log, "usage": usage}
