"""Policy scaffolding: the API every exploration policy is written against.

This is the framework's version of the paper's ``see.policy.api`` /
``see.policy.observation_signal`` [paper:App.B.2 L2:28-45, L2:110-127]. Policy
code imports it as ``from policy_api import ...``.

The module is **standalone** (standard library only): the policy sandbox copies
this file verbatim next to the policy's ``method.py`` in a scratch directory, so
a policy running in a subprocess sees exactly the same classes as one running
in-process. Nothing in here can reach a hidden tree: the :class:`QuestionProxy`
only holds the revealed prefix it was sent by the parent process.

Contents
--------
* :class:`Observation`, :class:`CellMeta` - what a revealed cell / a legal cell looks like;
* :class:`GridPlan`, :class:`GridPlanningContext` - the pre-episode width x depth plan;
* :class:`LLMDesignedMethod` - base class (``self.config``; ``plan_grid`` stub returns None,
  which the runner rejects: policies must override it);
* :class:`SimResult`, :func:`_budget_done`, :func:`_record_curve`, :func:`finalize_result`;
* helper signals ``branch_promising``, ``branch_failed_hard``, ``probe_improved_vs_parent``,
  ``probe_improved_vs_baseline`` plus ``branch_trajectories``, ``is_success``, ``is_repairable``;
* :class:`QuestionProxy` - the policy-facing Question (same object online and in replay).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

#: evaluation outcome classes. "ok" = successful evaluation (paper success semantics).
FAIL_CLASSES = ("ok", "compile_other", "correctness", "constraint", "timeout", "resource", "env_error",
                "dependency", "rejected", "hard")
#: failures that are normally NOT repairable by the agent (environment / dependency / declared hard).
HARD_FAIL_CLASSES = ("env_error", "dependency", "hard")


class GuardViolation(RuntimeError):
    """Raised when a policy touches something outside the revealed prefix
    (hidden tree, ``best_so_far``, ``budget_spent``, unknown attributes)."""


class BatchError(ValueError):
    """Raised when a selected batch is illegal (duplicates, > W cells, illegal cell,
    parent together with its child)."""


# --------------------------------------------------------------------------- data
@dataclass
class Observation:
    cell_id: str
    branch: int
    attempt: int
    score: Optional[float]
    evaluated: bool = True
    valid: bool = True
    fail_class: str = "ok"
    error: Optional[str] = None
    delta_vs_baseline: Optional[float] = None
    delta_vs_parent: Optional[float] = None
    n_valid: Optional[int] = None
    n_total: Optional[int] = None
    parent_id: Optional[str] = None

    @property
    def success(self) -> bool:
        """Paper success semantics: evaluated, ``error is None`` and ``fail_class == "ok"``,
        even when ``valid`` is False [paper:App.B.2 L2:47-51]."""
        return bool(self.evaluated) and self.error is None and self.fail_class == "ok"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Observation":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class CellMeta:
    cell_id: str
    branch: int
    attempt: int
    parent_id: Optional[str] = None
    seq: Optional[int] = None
    tags: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CellMeta":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class GridPlan:
    """``branch_count`` branches (0..W-1) and attempts 0..``refine_count`` per branch
    [paper:App.B.2 L2:206-212]. ``branch_count`` is a number of *branches*, not workers."""

    branch_count: int
    refine_count: int
    reason: str = ""

    @property
    def cells(self) -> int:
        return int(self.branch_count) * (int(self.refine_count) + 1)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GridPlan":
        return cls(int(d["branch_count"]), int(d["refine_count"]), str(d.get("reason", "")))


@dataclass
class GridPlanningContext:
    """Prefix-safe facts for ``plan_grid`` [paper:App.B.2 L2:214-221]: completed earlier
    live manifests, fallback / hard caps, the worker cap, and (replay only) the frozen
    trace's structural support."""

    history: List[dict] = field(default_factory=list)
    fallback_branch_count: int = 4
    fallback_refine_count: int = 4
    hard_max_branch_count: int = 16
    hard_max_refine_count: int = 16
    worker_cap: int = 4
    trace_branch_count: Optional[int] = None
    trace_refine_count: Optional[int] = None
    mode: str = "live"                  # "live" | "replay"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GridPlanningContext":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class SimResult:
    curve: List[List[float]] = field(default_factory=list)   # [probes revealed, best successful score]
    n_probes: int = 0
    best: Optional[float] = None
    stopped: str = ""
    info: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SimResult":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


class LLMDesignedMethod:
    """Base class of every exploration policy.

    Subclasses read exactly one behavioural scalar, ``beta = float(self.config.get("beta", d))``,
    and implement ``solve(question, budget=None)`` and ``plan_grid(context)``.
    """

    NAME = "LLMDesignedMethod"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = dict(config or {})

    def plan_grid(self, context: GridPlanningContext) -> Optional[GridPlan]:  # template stub
        return None

    def solve(self, question: "QuestionProxy", budget: Optional[int] = None) -> SimResult:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------- scaffolding
def _budget_done(question, budget: Optional[int] = None) -> bool:
    """True when the episode is over (round limit / everything revealed / online budget)
    or when an explicit probe ``budget`` is used up. Replay calls with ``budget=None``."""
    return bool(question._scaffold_budget_done(budget))


def _best_success(prefix: Dict[str, Observation]) -> Optional[float]:
    vals = [o.score for o in prefix.values() if o.success and o.score is not None]
    return max(vals) if vals else None


def _record_curve(res: SimResult, question) -> None:
    prefix = question.observed()
    res.curve.append([len(prefix), _best_success(prefix)])


def finalize_result(question, res: SimResult) -> SimResult:
    prefix = question.observed()
    res.n_probes = len(prefix)
    res.best = _best_success(prefix)
    return res


# ------------------------------------------------------------------ helper signals
def is_success(obs: Observation) -> bool:
    return obs.success


def is_repairable(obs: Observation) -> bool:
    """A failed evaluation that is normally fixable by the agent (correctness mismatch,
    compile/runtime error, constraint violation, timeout, resource limit). Never
    "repairable" merely because ``valid`` is False [paper:App.B.2 L2:49-50, L2:63-71]."""
    if obs.success:
        return False
    return obs.fail_class not in HARD_FAIL_CLASSES


def branch_trajectories(prefix: Dict[str, Observation]) -> Dict[int, List[Observation]]:
    """Ordered prefix trajectory of every opened branch (attempt order)."""
    out: Dict[int, List[Observation]] = {}
    for o in prefix.values():
        out.setdefault(o.branch, []).append(o)
    for b in out:
        out[b].sort(key=lambda o: o.attempt)
    return out


def successful_anchor(traj: List[Observation]) -> Optional[float]:
    vals = [o.score for o in traj if o.success and o.score is not None]
    return max(vals) if vals else None


def probe_improved_vs_parent(obs: Observation) -> bool:
    return obs.success and obs.delta_vs_parent is not None and obs.delta_vs_parent > 0


def probe_improved_vs_baseline(obs: Observation) -> bool:
    return obs.success and obs.delta_vs_baseline is not None and obs.delta_vs_baseline > 0


def branch_promising(traj: List[Observation], baseline: Optional[float] = None) -> bool:
    """Has a successful anchor above the baseline and its latest success still improved."""
    succ = [o for o in traj if o.success and o.score is not None]
    if not succ:
        return False
    anchor = max(o.score for o in succ)
    if baseline is not None and anchor <= baseline:
        return False
    return probe_improved_vs_parent(succ[-1]) or len(succ) == 1


def branch_failed_hard(traj: List[Observation], window: int = 2) -> bool:
    """Signal (not an unconditional closure): the latest ``window`` attempts all failed and
    at least one of them is a hard (environment/dependency) failure, or the branch has
    never produced a successful evaluation and its latest failure is hard."""
    if not traj:
        return False
    tail = traj[-window:]
    if all(not o.success for o in tail) and any(o.fail_class in HARD_FAIL_CLASSES for o in tail):
        return True
    return not any(o.success for o in traj) and traj[-1].fail_class in HARD_FAIL_CLASSES


# ------------------------------------------------------------------- the question
_ALLOWED = frozenset({
    "reset", "observed", "legal_actions", "legal_roots", "opened_branches", "meta", "probe_batch",
    "baseline_score", "max_parallelism", "is_done", "_scaffold_budget_done",
})


class QuestionProxy:
    """The only Question a policy ever holds (online and in replay).

    It keeps a *mirror* of the revealed prefix, filled exclusively from what the
    parent-side guard sends back after ``reset``/``probe_batch``. Any other attribute
    raises :class:`GuardViolation` and the violation is reported to the parent, which
    disqualifies the episode. ``best_so_far`` / ``budget_spent`` are bookkeeping only
    and forbidden as decision inputs [paper:App.B.2 L2:53-55].
    """

    __slots__ = ("_t", "_st", "_obs")

    def __init__(self, transport: Callable[..., dict]) -> None:
        object.__setattr__(self, "_t", transport)
        object.__setattr__(self, "_st", {})
        object.__setattr__(self, "_obs", {})

    # -- internal
    def _apply(self, state: dict, revealed: Optional[list] = None) -> List[Observation]:
        if state.get("reset"):
            self._obs.clear()
        out = []
        for d in revealed or state.get("revealed", []):
            o = Observation.from_dict(d)
            self._obs[o.cell_id] = o
            out.append(o)
        self._st.update({k: v for k, v in state.items() if k not in ("revealed", "reset", "reset_denied")})
        return out

    def _violation(self, what: str):
        try:
            self._t("violation", what=what)
        finally:
            raise GuardViolation(f"policy accessed {what!r}: only the revealed prefix, baseline_score, legal sets, "
                                 "structural meta and helper signals may be used")

    # -- API
    def reset(self) -> None:
        state = self._t("reset")
        if state.get("reset_denied"):   # already recorded parent-side as a violation
            raise GuardViolation("reset() after probing: an episode may only be reset before its first probe")
        self._apply(state)

    def observed(self) -> Dict[str, Observation]:
        return dict(self._obs)

    def legal_actions(self) -> List[str]:
        return list(self._st.get("legal_actions", []))

    def legal_roots(self) -> List[str]:
        return list(self._st.get("legal_roots", []))

    def opened_branches(self) -> List[int]:
        return list(self._st.get("opened", []))

    def meta(self, cell_id: str) -> CellMeta:
        m = self._st.get("meta", {}).get(cell_id)
        if m is None:
            o = self._obs.get(cell_id)
            if o is None:
                self._violation(f"meta({cell_id}) of a cell that is neither revealed nor legal")
            return CellMeta(o.cell_id, o.branch, o.attempt, o.parent_id, None, {})
        return CellMeta.from_dict(m)

    @property
    def baseline_score(self) -> Optional[float]:
        return self._st.get("baseline_score")

    @property
    def max_parallelism(self) -> int:
        return int(self._st.get("max_parallelism", 1))

    def is_done(self) -> bool:
        return bool(self._st.get("done", False))

    def _scaffold_budget_done(self, budget: Optional[int]) -> bool:
        if self._st.get("done"):
            return True
        return budget is not None and len(self._obs) >= int(budget)

    def probe_batch(self, cells, on_reveal: Optional[Callable[[Observation], Any]] = None) -> List[Observation]:
        cells = [str(c) for c in cells]
        resp = self._t("probe", cells=cells)
        if resp.get("batch_error"):
            raise BatchError(resp["batch_error"])
        out = self._apply(resp)
        if on_reveal is not None:
            for o in out:
                on_reveal(o)
        return out

    @property
    def best_so_far(self):
        self._violation("best_so_far")

    @property
    def budget_spent(self):
        self._violation("budget_spent")

    def __getattr__(self, name: str):
        if name in _ALLOWED:  # pragma: no cover - handled by real attributes
            return object.__getattribute__(self, name)
        self._violation(name)

    def __setattr__(self, name, value):
        self._violation(f"set {name}")


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def lerp(lo: float, hi: float, beta: float) -> float:
    """Linear schedule used by ``_schedule(beta)`` templates."""
    return lo + (hi - lo) * clamp(float(beta), 0.0, 1.0)


def ceil_div(a: int, b: int) -> int:
    return int(math.ceil(a / max(1, b)))
