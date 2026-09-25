"""The policy developer ("dreaming"): rewrite the exploration-policy code from replay feedback.

"A separate AI 'policy developer' reads how the current strategy behaved in replay,
rewrites the strategy code, and repeats many times" [doc]; the prompt is Listing 2
[paper:App.B.2]. Implementations:

* :class:`LLMPolicyDeveloper` - any :class:`rsi.core.Editor` (``RewriteEditor`` over
  an LLM, or ``AgentEditor`` = ``claude -p`` with file tools) edits ``method.py``
  with the Listing-2 prompt (:mod:`rsi.dream.prompts`). Context files mirror the paper's layout:
  ``history/baseline/method.py`` (the parallel-refine floor), ``history/r####_*/``
  (earlier versions' code + ``proposal_results/beta_sweep.json`` + report),
  ``proposal_results/policy_execution_traces.jsonl`` of the base version
  (between-round feedback only), ``trace_pool/iter*/live_cycle_manifest.json`` and
  the ``policy_api.py`` source. Every revision passes :func:`static_check` and a
  :class:`rsi.core.LeakageCritic` screen (no trace cell ids / scores copied into the
  code); a failed check gets one repair round.
* :class:`ParametricMutator` - the offline mock developer: starts from the strongest
  version so far, applies feedback-directed moves to the ``PARAMS`` block of the
  adaptive template (premature stops -> more patience/width; wasted probes -> earlier
  stops; serial batches -> wider opening) plus random perturbations, and sets the
  baked-in default beta with the cross-cycle rule.
* :func:`choose_default_beta` - Listing 2's cross-cycle default-beta rule.
* :func:`mock_developer_llm` - a MockLLM that answers the Listing-2 prompt with a valid
  ``=== FILE: method.py ===`` edit, so the LLM path runs offline.
"""
from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..core.critic import LeakageCritic
from ..core.editors import Editor, RewriteEditor
from ..core.llm import LLM, MockLLM, Usage
from .guard import CheckResult, static_check
from .policy import POLICY_FILE, get_params, policy_artifact, set_params, template_code
from .policy_api import clamp
# Listing 2 and the framework notes live in rsi.dream.prompts (re-exported here)
from .prompts import (DEVELOPER_PROMPT, EQ1_OBJECTIVE, FRAMEWORK_NOTES,  # noqa: F401
                      PARETO_OBJECTIVE, developer_prompt, objective_text)

# ------------------------------------------------------------------------------- data


@dataclass
class VersionRecord:
    index: int                 # global revision number (r####)
    code: str
    report: object = None      # PolicyReport on the current worlds (None until evaluated)
    change: str = ""
    parent: Optional[int] = None
    iteration: int = 0
    label: str = ""

    @property
    def value(self) -> float:
        return float(self.report.value) if self.report is not None else float("-inf")


@dataclass
class DevContext:
    iteration: int
    versions: list[VersionRecord]          # this phase's versions so far (incumbent first)
    history: list[VersionRecord]           # every earlier evaluated version (all phases)
    manifests: list[dict]                  # live_cycle_manifest.json of completed live iterations
    baseline_code: str
    objective: str
    W: int
    forbidden_terms: list[str] = field(default_factory=list)
    first_in_phase: bool = True


@dataclass
class Revision:
    code: Optional[str]
    change: str = ""
    parent: Optional[int] = None
    usage: Usage = field(default_factory=Usage)
    error: Optional[str] = None
    check: Optional[CheckResult] = None
    meta: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.code is not None and self.error is None


def strongest(versions: Sequence[VersionRecord]) -> VersionRecord:
    """"Start from a strong recent policy": best value, most recent on ties."""
    return max(versions, key=lambda v: (v.value, v.index))


def default_beta_of(code: str, fallback: float = 0.6) -> float:
    """The policy's baked-in default ``beta``: ``PARAMS["default_beta"]`` (templates), else the
    literal default of ``self.config.get("beta", <literal>)`` (the Listing-2 idiom an LLM-written
    policy uses), else ``fallback``. Recorded as the live manifest's actual beta."""
    p = get_params(code)
    if "default_beta" in p:
        return float(p["default_beta"])
    try:
        import ast

        for node in ast.walk(ast.parse(code)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                    and len(node.args) == 2 and isinstance(node.args[0], ast.Constant) and node.args[0].value == "beta"
                    and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, (int, float))):
                return float(node.args[1].value)
    except SyntaxError:
        pass
    return float(fallback)


def choose_default_beta(prior: Optional[float], manifests: Sequence[dict], sweep: Optional[dict]) -> tuple[float, str]:
    """Cross-cycle default-beta rule [paper:App.B.2 L2:169-188]."""
    recent = [m for m in manifests if m.get("final_best") is not None][-3:]
    if prior is None or len(recent) < 2 or not sweep or not sweep.get("points"):
        return 0.6, "history insufficient: moderately exploratory default 0.6"
    pts = {round(p["beta"], 6): p for p in sweep["points"]}
    near = min(pts, key=lambda b: abs(b - prior))
    cur = pts[near]
    improving = recent[-1]["final_best"] > recent[-2]["final_best"]
    if improving:
        better = [p for b, p in pts.items() if 0 < abs(b - prior) <= 0.21
                  and p.get("V_eq1", -9) > cur.get("V_eq1", -9) + 0.02]
        if better:
            b = max(better, key=lambda p: p.get("V_eq1", -9))["beta"]
            return float(b), f"live best improving; sweep shows a clearly better nearby beta {b:g}"
        return float(prior), "live best still improving: keep the prior default"
    higher = [p for b, p in pts.items() if b > prior + 1e-9]
    if higher and max(p["attainment"] for p in higher) > cur["attainment"] + 0.02 and \
            min(p["probes_frac"] for p in higher) - cur["probes_frac"] < 0.35:
        return float(clamp(prior + 0.15, 0.0, 1.0)), "live best plateaued and higher beta reaches higher attainment: raise"
    if prior >= 0.7:
        # "a high default has already been tried through a plateau and high-beta points add work
        # without attainment": judged against the points above it, or - when the default sits at the
        # top of the grid and nothing lies above - against the nearest point below it
        no_gain_above = not higher or max(p["attainment"] for p in higher) <= cur["attainment"] + 1e-9
        lower = [p for b, p in pts.items() if b < prior - 1e-9]
        nb = max(lower, key=lambda p: p["beta"]) if lower else None
        work_without_gain = bool(higher) or (nb is not None and cur["probes_frac"] > nb["probes_frac"] + 1e-9
                                             and cur["attainment"] <= nb["attainment"] + 0.02)
        if no_gain_above and work_without_gain:
            return float(clamp(prior - 0.1, 0.0, 1.0)), "high default already tried through a plateau: lower"
    return 0.6, "evidence conflicts: moderately exploratory default 0.6"


class PolicyDeveloper:
    name = "developer"

    def revise(self, ctx: DevContext, *, seed: int = 0) -> Revision:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------- mutator (mock)
#: search space of the adaptive template's PARAMS (lo, hi, integer?)
SPACE = {
    "open_lo": (0.1, 1.0, False), "open_hi": (0.2, 1.0, False), "max_width_lo": (0.2, 1.0, False),
    "max_width_hi": (0.3, 1.0, False), "patience_lo": (0.5, 4.0, False), "patience_hi": (1.0, 6.0, False),
    "gain_lo": (0.0, 0.3, False), "gain_hi": (0.0, 0.2, False), "prune_lo": (0.05, 1.5, False),
    "prune_hi": (0.1, 2.0, False), "evidence_lo": (1.0, 3.0, False), "evidence_hi": (1.0, 4.0, False),
    "repairs_lo": (0.0, 3.0, False), "repairs_hi": (0.0, 4.0, False), "explore_on_stall": (0.0, 1.0, False),
    "w_anchor": (0.2, 2.0, False), "w_trend": (0.0, 2.0, False), "w_depth": (0.0, 0.3, False),
    "plan_width_step": (0, 3, True), "plan_depth_step": (0, 3, True),
}


class ParametricMutator(PolicyDeveloper):
    """Offline mock policy developer (feedback-directed + random edits of ``PARAMS``)."""

    name = "mutator"

    def __init__(self, *, sigma: float = 0.2, n_random: int = 2, directed: bool = True, beta_rule: bool = True,
                 template: str = "adaptive") -> None:
        self.sigma, self.n_random, self.directed, self.beta_rule = sigma, n_random, directed, beta_rule
        self.template = template

    def revise(self, ctx: DevContext, *, seed: int = 0) -> Revision:
        rng = random.Random(seed)
        base = strongest(ctx.versions)
        params = get_params(base.code)
        moves: list[str] = []
        if not all(k in params for k in SPACE):
            code = template_code(self.template)
            params = get_params(code)
            moves.append("rewrite: adaptive portfolio policy (prefix trajectories, dynamic batches, beta schedule, "
                         "plan_grid) replaces the fixed widen/deepen schedule")
        else:
            code = base.code
        diag = (base.report.diagnostics if base.report is not None else {}) or {}
        p = dict(params)

        def bump(k, delta):
            lo, hi, is_int = SPACE[k]
            v = clamp(p[k] + delta, lo, hi)
            p[k] = int(round(v)) if is_int else round(v, 4)

        if self.directed and diag:
            step = rng.choice((0.5, 1.0))
            if diag.get("missed_ceiling_rate", 0) > 0.5 and diag.get("attainment", 1) < 0.9:
                for k in ("patience_lo", "patience_hi"):
                    bump(k, 0.5 * step)
                for k in ("prune_lo", "prune_hi", "max_width_lo", "max_width_hi"):
                    bump(k, 0.15 * step)
                moves.append("premature stops (ceiling missed): more patience, weaker pruning, wider")
            elif diag.get("wasted_probe_frac", 0) > 0.3:
                for k in ("patience_lo", "patience_hi"):
                    bump(k, -0.5 * step)
                for k in ("prune_lo", "prune_hi"):
                    bump(k, -0.15 * step)
                bump("gain_lo", 0.03 * step)
                moves.append("wasted probes after the final best: stop stagnating branches earlier")
            elif diag.get("missed_ceiling_rate", 1) <= 0.34 and diag.get("probes_frac", 0) > 0.3:
                k = rng.choice(("width", "prune", "patience"))
                if k == "width":
                    for key in ("max_width_lo", "max_width_hi", "open_lo", "open_hi"):
                        bump(key, -0.15 * step)
                elif k == "prune":
                    for key in ("prune_lo", "prune_hi"):
                        bump(key, -0.2 * step)
                else:
                    for key in ("patience_lo", "patience_hi"):
                        bump(key, -0.5 * step)
                moves.append(f"ceiling reached with many probes: try a cheaper search ({k})")
            if diag.get("batch_fill", 1) < 0.6:
                for k in ("open_lo", "open_hi"):
                    bump(k, 0.15)
                moves.append("serial / under-filled batches: open more roots per round")
        keys = sorted(SPACE)
        for k in rng.sample(keys, k=min(self.n_random, len(keys))):
            lo, hi, is_int = SPACE[k]
            old = p[k]
            bump(k, rng.gauss(0.0, self.sigma) * (hi - lo))
            # say so when clamping / integer rounding left the value unchanged (a no-op move)
            moves.append(f"perturb {k} -> {p[k]}" if p[k] != old else f"perturb {k}: unchanged at {p[k]} (clamp/round)")
        if self.beta_rule and ctx.first_in_phase:
            prior = float(params.get("default_beta", 0.6))
            sweep = getattr(base.report, "sweep", None) if base.report is not None else None
            if sweep is None:
                for h in reversed(ctx.history):
                    if h.report is not None and getattr(h.report, "sweep", None):
                        sweep = h.report.sweep
                        break
            b, why = choose_default_beta(prior, ctx.manifests, sweep)
            if abs(b - prior) > 1e-9:
                p["default_beta"] = round(b, 3)
                moves.append(f"default beta {prior:g} -> {b:g} ({why})")
        new = set_params(code, {k: p[k] for k in p})
        # audit only (rsi.trace): the feedback the mutator acted on and the parameter changes
        changed = {k: [params.get(k), p[k]] for k in p if params.get(k) != p[k]}
        return Revision(new, "; ".join(moves) or "no-op", base.index,
                        meta={"base_label": base.label, "base_value": base.value, "feedback": dict(diag),
                              "moves": moves, "params_changed": changed})


# ---------------------------------------------------------------------------- LLM path
DEVELOPER_SYSTEM = ("You are a careful controller-development engineer. You write deterministic, prefix-only "
                    "exploration policies in Python. You never solve the scientific task itself.")


class LLMPolicyDeveloper(PolicyDeveloper):
    """Listing-2 policy developer over an LLM or any :class:`rsi.core.Editor`.

    Context files mirror the paper's layout, and by default nothing is capped: every earlier
    version's ``history/r####_*/`` (code, report, ``beta_sweep.json`` and
    ``policy_execution_traces.jsonl``) and every live manifest. ``max_history`` (versions) and
    ``max_trace_rows`` (episodes per version) are non-default caps to bound prompt length."""

    name = "llm"

    def __init__(self, editor: Editor | LLM, *, beta1: float = 0.01, beta2: float = 0.005, lam: float = 0.1,
                 max_history: Optional[int] = None, max_trace_rows: Optional[int] = None, repair_rounds: int = 1,
                 role: str = "developer", leakage_check: bool = True, max_context_chars: int = 400_000) -> None:
        # a single-completion editor gets every context file inline; the core editor's 60k-char default
        # would silently cut the oldest history (and the API source) once every version's traces are in
        self.editor = RewriteEditor(editor, max_context_chars=max_context_chars) if isinstance(editor, LLM) \
            else editor
        self.beta1, self.beta2, self.lam = beta1, beta2, lam
        self.max_history, self.max_trace_rows = max_history, max_trace_rows
        self.repair_rounds, self.role = repair_rounds, role
        self.leakage_check = leakage_check

    def context_files(self, ctx: DevContext, base: VersionRecord) -> dict[str, str]:
        from . import policy_api

        # order = what survives an editor's context budget: API, live manifests, baseline, then the
        # version history newest first
        files = {}
        with open(policy_api.__file__) as f:
            files["api/policy_api.py"] = f.read()
        for m in ctx.manifests:
            files[f"trace_pool/iter{int(m.get('iteration', 0)):02d}/live_cycle_manifest.json"] = json.dumps(m, default=float)
        files["history/baseline/method.py"] = ctx.baseline_code
        latest: dict[int, VersionRecord] = {}
        for v in list(ctx.history) + list(ctx.versions):      # later entries (this phase's reports) win
            latest[v.index] = v
        versions = sorted(latest.values(), key=lambda v: v.index, reverse=True)
        if self.max_history is not None:
            versions = versions[:self.max_history] + ([base] if base.index not in
                                                      {v.index for v in versions[:self.max_history]} else [])
        for v in versions:
            d = f"history/r{v.index:04d}_{v.label or 'v'}"
            files[f"{d}/method.py"] = v.code
            if v.report is not None:
                files[f"{d}/report.json"] = json.dumps(v.report.summary(), default=float, indent=1)
                if getattr(v.report, "sweep", None):
                    files[f"{d}/proposal_results/beta_sweep.json"] = json.dumps(v.report.sweep, default=float)
                rows = v.report.traces_jsonl(self.max_trace_rows)
                if rows:
                    files[f"{d}/proposal_results/policy_execution_traces.jsonl"] = rows
        return files

    def _screen(self, code: str, ctx: DevContext) -> tuple[CheckResult, list[str]]:
        chk = static_check(code)
        hits: list[str] = []
        if self.leakage_check and ctx.forbidden_terms:
            import difflib

            diff = "".join(difflib.unified_diff([], code.splitlines(keepends=True)))
            v = LeakageCritic(ctx.forbidden_terms, min_term_len=4).screen(diff)
            hits = v.hits
        return chk, hits

    def revise(self, ctx: DevContext, *, seed: int = 0) -> Revision:
        base = strongest(ctx.versions)
        instructions = developer_prompt(ctx.objective, ctx.W, beta1=self.beta1, beta2=self.beta2, lam=self.lam,
                                        trace_rows=self.max_trace_rows)
        files = self.context_files(ctx, base)
        art = policy_artifact(base.code)
        usage = Usage()
        errors: list[str] = []
        seen: list[str] = []
        calls: list[dict] = []          # audit only (rsi.trace): every prompt / reply / screen verdict
        for attempt in range(self.repair_rounds + 1):
            prompt = instructions
            if errors:
                prompt += ("\n\n## Your previous edit was rejected\n" + "\n".join(f"- {e}" for e in errors)
                           + "\nFix these problems and return the complete corrected method.py.")
            prop = self.editor.edit(art, prompt, context=files, editable=[POLICY_FILE], system=DEVELOPER_SYSTEM,
                                    seed=seed * 10 + attempt, role=self.role)
            usage = usage + prop.usage
            try:
                shown = self.editor.build_prompt(art, prompt, files, [POLICY_FILE]) \
                    if hasattr(self.editor, "build_prompt") else prompt
            except Exception:  # noqa: BLE001 - auditing must never break a revision
                shown = prompt
            call = {"attempt": attempt, "prompt": shown, "reply": prop.raw, "change": prop.change,
                    "hypothesis": prop.hypothesis, "error": prop.error, "base_code": art[POLICY_FILE]}
            calls.append(call)
            if not prop.ok:
                errors = [prop.error or "no effective change"]
                seen.extend(errors)
                call["screen"] = {"ok": False, "errors": list(errors), "leak_hits": []}
                continue
            from .agent import strip_reply_terminators    # local import: agent.py is a sibling module

            fixed_art, cleaned = strip_reply_terminators(prop.artifact, [POLICY_FILE])
            code = fixed_art[POLICY_FILE]
            call["sanitized"] = cleaned
            chk, hits = self._screen(code, ctx)
            call["screen"] = {"ok": bool(chk.ok and not hits), "errors": list(chk.errors), "leak_hits": list(hits)}
            if chk.ok and not hits:
                # a repaired revision keeps the substantive claim of its first attempt: the repair
                # reply's header only describes the fix, not the change against the base version
                first = next((c.get("change") for c in calls if c.get("change")), "") if attempt else ""
                change = (f"{first} [repaired: {prop.change}]" if first and first != prop.change
                          else (prop.change or "llm revision"))
                return Revision(code, change, base.index, usage, None, chk,
                                {"hypothesis": prop.hypothesis[:1000], "repairs": attempt, "calls": calls,
                                 "context_files": {k: len(v) for k, v in files.items()}})
            errors = chk.errors + [f"code copies trace-specific data: {h!r}" for h in hits]
            seen.extend(errors)
            art = policy_artifact(code)
        uniq = list(dict.fromkeys(seen))
        return Revision(None, "rejected", base.index, usage, "; ".join(uniq)[:800], None,
                        {"calls": calls, "context_files": {k: len(v) for k, v in files.items()}})


def mock_developer_llm(seed: int = 0) -> MockLLM:
    """Offline stand-in for the developer LLM: parses the current ``method.py`` out of the
    Listing-2 prompt and answers with a valid edited file (a PARAMS perturbation of the
    adaptive template, or the adaptive template itself when the current code has no PARAMS)."""
    mut = ParametricMutator(directed=False, beta_rule=False, n_random=2)

    def respond(prompt: str, system, s, i) -> str:
        m = re.search(r"=== FILE: method\.py ===\n(.*?)(?=\n=== FILE:|\nEditable files|\Z)", prompt, re.S)
        code = m.group(1) if m else template_code("adaptive")
        rec = VersionRecord(0, code)
        rev = mut.revise(DevContext(0, [rec], [], [], "", "eq1", 4, first_in_phase=False), seed=(s or 0) + i + seed)
        header = json.dumps({"change": rev.change[:200], "hypothesis": "retune thresholds from replay feedback",
                             "components": ["policy"]})
        return f"```json\n{header}\n```\n=== FILE: method.py ===\n{rev.code}"

    return MockLLM(respond, name="mock-developer")
