"""The policy developer ("dreaming"): rewrite the exploration-policy code from replay feedback.

"A separate AI 'policy developer' reads how the current strategy behaved in replay,
rewrites the strategy code, and repeats many times" [doc]; the prompt is Listing 2
[paper:App.B.2]. Implementations:

* :class:`LLMPolicyDeveloper` - any :class:`rsi.core.Editor` (``RewriteEditor`` over
  an LLM, or ``AgentEditor`` = ``claude -p`` with file tools) edits ``method.py``
  with the Listing-2 prompt. Context files mirror the paper's layout:
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

#: Listing 2 [paper:App.B.2 L2:1-273], verbatim. Runtime substitutions: ``{method_file}``,
#: ``{history_dir}`` and ``{trace_pool}`` (the context-file paths), ``see.policy.api`` /
#: ``see.policy.observation_signal`` -> this framework's ``policy_api`` module, and the objective
#: paragraph L2:12-22 (``{objective_text}``), which is L2's own text under ``objective="pareto"`` and
#: states the Eq. 1 selection score otherwise (the paper never says which of the two produced its
#: results, spec §8.15). Everything this reimplementation adds is in ``FRAMEWORK_NOTES`` below it.
DEVELOPER_PROMPT = """\
You are improving one **prefix-only exploration policy**. Edit only
``{method_file}`` and implement ``OptimalPolicy.solve(self, question, budget=None)``.
Do not solve the scientific task and do not edit any other program.

## Objective: quality, work, and parallelism

The environment is a frozen, irregular branch×attempt grid. A policy opens a root
or refines the next cell of an already-open branch. Each revealed cell costs one
probe. The policy sees only the cells it has revealed so far; unrevealed scores are
unknown.

{objective_text}

A local implementation failure does not by itself prove that its parent direction
is poor. Weigh recovery value against new roots and ordinary refinements while
keeping batches parallel.

## API

question.reset()
question.observed() -> dict[str, Observation] # revealed prefix only
question.legal_actions() -> list[str] # roots + opened-branch frontiers
question.legal_roots() -> list[str] # unopened roots only
question.opened_branches() -> list[int]
question.meta(cell_id) -> CellMeta # .branch .attempt .parent_id .seq .tags
question.probe_batch(cells, on_reveal=...) -> list[Observation]
question.baseline_score
question.max_parallelism

``Observation`` supplies ``branch``, ``attempt``, ``score``, ``evaluated``, ``valid``,
``fail_class``, ``error``, ``delta_vs_baseline``, ``delta_vs_parent``, ``n_valid``, and
``n_total``.
Use the helpers in ``policy_api`` (the paper's ``see.policy.observation_signal``) when useful:
``branch_promising``, ``branch_failed_hard``, ``probe_improved_vs_parent``, and
``probe_improved_vs_baseline``.

**Success semantics:** an evaluated observation with ``error is None`` and
``fail_class == "ok"`` is a successful evaluation, even when ``valid == False`` or
``n_valid``/``n_total`` are unavailable. Never label it repairable solely because
``valid`` is false. A *successful anchor* below means the best historical score
from such a successful evaluation.

Do **not** use ``question.best_so_far`` or ``question.budget_spent`` to decide what
to explore; they are bookkeeping only. Derive any decision statistic from
``question.observed()`` instead.

## Required branch trajectory and failure interpretation

For each opened branch, reconstruct its ordered prefix trajectory, not only its
latest observation or best score: successful anchor, score trend, regressions,
failure/repair sequence, and explored versus remaining depth.

Before closing or deprioritizing a failed frontier, classify it as
hard-unrecoverable, repairable implementation failure, weak-but-underexplored, or
repeatedly unpromising after sufficient valid evidence. Output/correctness mismatch,
shared-memory/resource limits, and variable/code, mask/layout/shape errors are
normally repairable. Do not infer algorithmic failure from one such error.
``n_valid == 0`` and ``branch_failed_hard(obs)`` are signals, not unconditional
closure: use ``fail_class`` and ``error`` to distinguish a repairable zero-valid
failure from an environment/dependency failure. ``compile_other`` alone is not
permanently hard. Classify the current failure episode: a later successful result
reopens the branch and cancels closure based only on an earlier failure.

## Required batch decision loop

At each decision round:

1. Read the prefix, reconstruct trajectories, and close only branches with
cumulative evidence of being hard-unrecoverable or repeatedly unpromising.
2. Rank legal roots and legal branch frontiers using only prefix-derived signals:
successful anchor, parent→child gain, complete branch trajectory, actual success
versus failure evidence,
failure recoverability, prior repair outcomes, remaining depth, and cross-branch
comparison.
3. Rank actual repairable failures and underexplored frontiers in deterministic
queues using trajectory, recoverability, remaining depth, repeated failures, and
beta. A repairable failure retains eligibility unless cumulative evidence lowers
its relative priority.
4. Build one **dynamic portfolio** batch of independent candidates, up to
``question.max_parallelism``: exploitation (strong normal refinements),
exploration (new roots or underexplored branches), and at most one recovery
(an actual repairable failure). When multiple roles are eligible, give
exploration and justified recovery representation before filling remaining slots
by priority; adapt this to prefix evidence rather than fixed quotas. Recovery
must not displace normal successful refinements or leave workers idle. Never
sample randomly, and do not default to a singleton merely because its top
candidate is clear.
5. Stop only after considering the whole revealed portfolio: active, underexplored,
recoverable, unopened, and remaining legal candidates. Do not stop while an
eligible high-priority recovery or underexplored candidate remains; every
remaining action needs an evidence-based decision to continue, reserve, or close.

A batch must contain distinct cells that are all legal *before* the call. It may
contain several roots and/or one frontier from each opened branch. It must never
contain a parent and its child together. Do not use a fixed widen-all / deepen-all
wave schedule: adapt batch composition after every revealed prefix.

Minimal structure:

from policy_api import (
    LLMDesignedMethod, SimResult, _budget_done, _record_curve, finalize_result,
)

def solve(self, question, budget=None):
    question.reset()
    res, closed = SimResult(), set()
    while not _budget_done(question, budget):
        prefix = question.observed()
        update_closed(closed, prefix, question)
        batch = select_batch(prefix, question, closed)
        if not batch:
            break
        question.probe_batch(
            batch,
            on_reveal=lambda _: _record_curve(res, question),
        )
    return finalize_result(question, res)

## Hard constraints

- Keep ``NAME = "OptimalPolicy"`` and implement
``class OptimalPolicy(LLMDesignedMethod)`` in ``{method_file}`` only.
- **Prefix-only:** decisions may use revealed observations, ``baseline_score``, legal
sets, structural ``meta``, and helper signals. Never use unrevealed scores, a true
optimum, hardcoded winning cell ids, absolute score targets, or internal trace data.
- Every prune, widen, deepen, batch, and stop decision must be explainable from the
current prefix. Shallow weak scores are not enough to discard a branch: deeper
attempts can recover. A repairable latest failure must not erase its historical
successful anchor or by itself cause permanent starvation.
- Replay calls with ``budget=None``. Always terminate when no batch is selected; do
not assume a budget cap exists.
- A selected batch must be legal, have no duplicate ids, and contain at most
``question.max_parallelism`` cells.

## Beta: fixed per run, adaptive across cycles

Read exactly one scalar in ``__init__``:

beta = float(self.config.get("beta", <sensible_default>))

Beta has three distinct roles. Do not conflate them:

1. **Within one replay or live episode:** beta is fixed. Route every behavioral
threshold through one ``_schedule(beta) -> dict``. High beta means more width,
deeper patience, and weaker pruning. Low beta means fewer probes, earlier
stagnation stops, and stronger pruning. Never change beta from observations inside
``solve()``. Route recovery eligibility, reserve threshold, and waiting through
the same schedule: high beta is more patient; low beta remains selective without
treating one repairable failure as automatic closure.
2. **During offline evaluation:** eval sweeps a fixed beta grid. This measures whether
the policy exposes a real attainment/work/parallelism trade-off; it is not online
beta adaptation.
3. **When proposing the next policy version:** choose the baked-in default beta once,
using evidence from earlier *live* cycles and their beta sweeps. That default will
remain fixed throughout the next live exploration episode.

Keep all thresholds relative to the prefix; never use absolute score cutoffs.

Use the following cross-cycle default-beta rule. Read the most recent 2–3
**live** ``{trace_pool}/iter*/live_cycle_manifest.json`` sidecars (and ``_current``
when present) for each iteration's final best score and actual baked-in beta. Read
the matching archived ``beta_sweep.json`` values (``pareto.reward``, AUC, parallel
penalty, and the per-beta frontier). Scores alone do not establish that beta caused a
change, so always use both sources:

- live best is still improving: keep the prior default beta unless its sweep clearly
shows a better nearby beta;
- live best has plateaued, and higher beta reaches higher attainment for a reasonable
work/parallelism cost in the sweep: raise the default by a small step (about
0.1–0.2, clamped to [0, 1]);
- a high default beta has already been tried through a plateau, and high-beta sweep
points add work without higher attainment: lower it by a small step;
- history is insufficient or evidence conflicts: use a moderately exploratory default
(about 0.6), rather than pretending the replay ceiling is a live stopping signal.

The beta sweep is non-degenerate only if beta changes the attainment/work trade-off.
It also reveals whether the policy batches. Do not select the default simply as the
smallest beta that reaches a frozen trace's known ceiling.

## Required next-cycle grid planning

Every proposed policy **must** implement this deterministic method:

from policy_api import GridPlan, GridPlanningContext

def plan_grid(self, context: GridPlanningContext) -> GridPlan:
    ...

This method runs **before** a new live grid is created. It does not make a
within-episode decision and must never inspect a current episode's outcomes.
It must always return a non-``None`` ``GridPlan``: do not inherit the template
stub and do not delegate grid choice to the runner's fallback. When history is
empty or insufficient, still return an explicit conservative bootstrap plan
derived from the context's fallback/hard-cap fields, with a factual reason.

``GridPlan(branch_count=W, refine_count=R)`` accepts arbitrary integers, not a
fixed set of presets. It creates branches ``0..W-1`` and attempts ``0..R``; ``R`` is
the number of refinements allowed after each root. The runner validates
``1 <= W <= context.hard_max_branch_count`` and
``0 <= R <= context.hard_max_refine_count``. In replay, a requested plan beyond the
frozen trace's ``context.trace_branch_count`` or ``context.trace_refine_count`` is
out of support and cannot earn replay reward.

Use only the prefix-safe facts in ``context``:

- ``history``: completed earlier live manifests, including prior planned/effective
grids, actual opened width/depth, probe work, decision rounds, scores, and beta;
- fallback/hard caps and worker cap;
- replay structural support fields. Do not read raw trace outcomes or a current
cycle result inside ``plan_grid``.

Choose width versus depth from evidence, not a default preference:

- many semantically distinct roots improve early while deeper refinements stall:
increase width and reduce/hold depth;
- high gains arrive late on a small, repeatable set of directions: reduce/hold width
and increase depth;
- all explored directions plateau after sufficient depth while meaningful direction
classes remain uncovered: increase width;
- repeated hard, unrecoverable failures or strongly redundant directions: reduce
width and depth conservatively;
- conflicting or insufficient history: return an explicit conservative bootstrap
plan derived from the context, and state that evidence is insufficient.

Include a short, factual ``reason`` in every plan. ``plan_grid`` answers
how many directions to make available; the direction provider assigns those new
roots their directions, and ``solve`` still decides which legal roots/frontiers to
open, refine, prune, or stop. Do not choose roots merely because their branch id is
small. The runtime grid is the hard bound: controller thresholds may use less, but
can never create branches or attempts beyond the effective plan. Before finishing,
verify that the edited ``method.py`` contains an override of ``plan_grid`` that
returns ``GridPlan(branch_count=..., refine_count=..., reason=...)`` on every path.

## Learn from history without leaking outcomes

Earlier rounds are in ``{history_dir}/r####_*/``. Read their policy code and
``proposal_results/beta_sweep.json``. Start from a strong recent policy, retain
mechanisms that raised ``pareto.reward``, and make a concrete change when progress
stalls. A legacy AUC-only sweep is useful code history but is not numerically
comparable to the current reward. The baseline under ``{history_dir}/baseline/`` is
a parallel-refine floor to beat.

Each current-objective round also archives
``proposal_results/policy_execution_traces.jsonl``: one replay episode per
``(frozen trace, beta)``. Use it to diagnose general behavior -- serial batches,
premature stops, over-pruning, or wasted probes -- from the prefix state, selected
batch, and revealed outcomes at each decision round. It is **between-round feedback
only**: never read it inside ``solve()``, and never copy a trace-specific branch,
cell id, score, or target into policy logic.

``{trace_pool}``, if present, may be read only outside ``solve()``. Prefer the
``live_cycle_manifest.json`` sidecars over raw replay outcomes for the per-iteration
live trend. Never copy trace scores, targets, or cell ids into policy logic.

## Deliverable

Write a complete adaptive policy in ``{method_file}``. Include a short module
docstring describing its prefix signals, batch rule, beta schedule, default-beta
rationale, grid-planning rule (if implemented), and safeguards against
over-pruning, over-stopping, permanent starvation after repairable failures, and
serial probes. Before finishing, verify trajectory-based ranking, the stated
success semantics, non-automatic zero-valid closure, deterministic recovery
competition, and portfolio-level stop.
"""

#: what this reimplementation adds to Listing 2 (none of it is in the paper's prompt): where the
#: context files are, the import allow-list and the sandbox rules its runner enforces
FRAMEWORK_NOTES = """\

---
# Framework notes (this reimplementation's runner; not part of the paper's prompt)

- Context files: ``{history_dir}/r####_<label>/`` holds each earlier version's ``method.py``, ``report.json`` and
  ``proposal_results/beta_sweep.json``; the version shown as the current artifact (the strongest so far) also has
  ``proposal_results/policy_execution_traces.jsonl`` (first {trace_rows} episodes). ``{history_dir}/baseline/method.py`` is
  parallel refine. Live manifests are ``{trace_pool}/iter<t>/live_cycle_manifest.json``. The API source is
  ``api/policy_api.py``: import it as ``from policy_api import ...`` (it also offers ``branch_trajectories``,
  ``is_repairable``, ``lerp`` and ``clamp``). There is no ``_current`` manifest.
- Workers: W = question.max_parallelism = {W}.
- Allowed imports: {imports}. No file access, no ``getattr``/``eval``/``exec``/``compile``, no introspection
  (``__class__``, ``__dict__``, ``globals()`` ...). Any other attribute of ``question`` is a guard violation.
- Call ``question.reset()`` once, at the start of ``solve()``, before the first probe: a reset after probing is a
  guard violation (it would let a policy explore, remember and replay the best path).
- Every episode (one frozen trace at one beta, or one live search) runs in a fresh policy process: module-level and
  class-level state does not survive between episodes. The static check therefore rejects ``global``/``nonlocal``,
  mutating module-level objects or shared class attributes from inside a method, and ``lru_cache``/``cache``.
- Live searches call ``solve(question, budget)`` with the round's agent-call budget; replay calls with
  ``budget=None``. A disqualified episode (violation, illegal batch, crash) scores below every honest one.
"""

#: L2:12-22, verbatim (``lambda`` is reported with its value)
PARETO_OBJECTIVE = """\
The evaluator sweeps your single ``beta`` knob and ranks the resulting curve by:

pareto.reward = pareto.auc - lambda * parallel_penalty      (lambda = {lam:g})

``pareto.auc`` rewards reaching high per-trace attainment with few **total probes**.
``parallel_penalty`` is the mean of
``effective_sequential_rounds / total_probes`` over the sweep. For a batch of size
``k`` with ``W = question.max_parallelism`` workers, it costs one decision round and
``ceil(k / W)`` effective sequential rounds. A serial policy has penalty near 1;
useful full batches approach ``1/W``. Therefore choose only promising probes, but
batch independent promising probes whenever possible."""

#: the same paragraph when versions are selected by the paper's Eq. 1 (framework default)
EQ1_OBJECTIVE = """\
The evaluator replays your policy at its baked-in default ``beta`` on every frozen trace and ranks it by
the mean replay score (the paper's Eq. 1):

V = best - {beta1:g} * N + {beta2:g} * N / max(1, k)

``best`` is the best revealed successful score normalized per trace (0 = the trace's root, 1 = the best
score recorded in that trace), ``N`` the number of revealed cells (probes) and ``k`` the number of decision
rounds (non-empty batches). It also sweeps your single ``beta`` knob for feedback (``beta_sweep.json``):
pareto.reward = pareto.auc - lambda * parallel_penalty (lambda = {lam:g}); ``pareto.auc`` rewards reaching
high per-trace attainment with few **total probes**; ``parallel_penalty`` is the mean of
``effective_sequential_rounds / total_probes`` over the sweep, where a batch of size ``k`` with
``W = question.max_parallelism`` workers costs one decision round and ``ceil(k / W)`` effective sequential
rounds (a serial policy has penalty near 1; useful full batches approach ``1/W``). Therefore choose only
promising probes, but batch independent promising probes whenever possible."""


def objective_text(objective: str, beta1: float = 0.01, beta2: float = 0.005, lam: float = 0.1) -> str:
    """The objective paragraph of Listing 2 (L2:12-22) for the objective actually used for selection."""
    if objective == "pareto":
        return PARETO_OBJECTIVE.format(lam=lam)
    return EQ1_OBJECTIVE.format(beta1=beta1, beta2=beta2, lam=lam)


def developer_prompt(objective: str, W: int, *, beta1: float = 0.01, beta2: float = 0.005, lam: float = 0.1,
                     trace_rows: Optional[int] = None, method_file: str = POLICY_FILE, history_dir: str = "history",
                     trace_pool: str = "trace_pool") -> str:
    """Listing 2 (verbatim, runtime paths and the objective paragraph filled in) + the framework notes."""
    from .guard import ALLOWED_IMPORTS

    body = DEVELOPER_PROMPT.format(method_file=method_file, history_dir=history_dir, trace_pool=trace_pool,
                                   objective_text=objective_text(objective, beta1, beta2, lam))
    notes = FRAMEWORK_NOTES.format(history_dir=history_dir, trace_pool=trace_pool, W=W,
                                   trace_rows="all" if trace_rows is None else f"at most {trace_rows}",
                                   imports=", ".join(sorted(i for i in ALLOWED_IMPORTS if i != "__future__")))
    return body + notes


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
