"""Synthetic discovery worlds with ground truth (spec §9.2 Track A).

A cheap, deterministic stand-in for "coding agent + evaluator" whose true dynamics
are known, so the *true online* value of any exploration policy can be measured -
something the paper cannot do - and replay estimates can be validated off-policy.

Model (all [inferred]; the paper gives no generative model):

* A program's quality ``q`` lies in [0, 1] (1 = ceiling). The "program" is a JSON
  state; the locked evaluator reads its quality (0 for failed attempts).
* ``n_directions`` direction classes ("mechanisms"), each with a hidden richness
  ``r_d`` drawn per world seed. A mechanism already in the root program's lineage is
  depleted: ``r_eff = r_d * depletion ** (times d is in the lineage)`` (applying the
  same idea again yields less - the local optimum Listing 1 warns about).
* Each new branch (round t, branch b) gets the direction tag assigned by the
  direction provider, a latent type drawn with richness-dependent weights -
  ``steady``, ``late`` (late bloomer), ``plateau``, ``flat``, ``hardfail``
  (environment failures), ``repairable`` (frequent repairable failures) - and a
  potential ``G = gain_scale * (1 - q_root) * r_eff * u``.
* Attempt ``a`` of that branch scores ``q_root + G * phi_type(a) + noise`` or fails
  with a typed ``fail_class``. All randomness is keyed by (world seed, round, branch,
  attempt) - common random numbers - so every policy faces the same potential
  outcomes, and replaying the recording policy reproduces its rollout exactly.
* ``context_coupling`` (default 0) makes outcomes depend on how many sibling
  attempts the agent had read, which biases replay (critique §8.3).

``ground_truth(state)`` gives a branch's expected best quality within a depth,
used for off-policy validation (E2) and support analysis (E10).
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass
from typing import Optional

from ...core.artifact import Artifact
from ...core.domain import Domain, Execution
from ...core.tasks import Task, TaskSuite
from ...dream.agent import AgentAttempt, AttemptContext, DiscoveryAgent, DiscoveryTask, EvalOutcome
from ...core.llm import Usage

STATE = "state.json"
TYPES = ("steady", "late", "plateau", "flat", "hardfail", "repairable")


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16))


def phi(kind: str, a: int) -> float:
    """Progress curve (fraction of the branch potential reached at attempt ``a``)."""
    if kind in ("steady", "repairable"):
        return 1.0 - 0.7 ** (a + 1)
    if kind == "late":
        return 0.05 * (a + 1) if a < 2 else 0.25 + 0.75 * (1.0 - 0.55 ** (a - 1))
    if kind == "plateau":
        return 0.75 * (1.0 - 0.3 ** (a + 1))
    if kind == "flat":
        return 0.06 + 0.01 * a
    return 0.02  # hardfail


@dataclass
class SyntheticConfig:
    seed: int = 0
    n_directions: int = 8
    q0: float = 0.2
    gain_scale: float = 0.55
    depletion: float = 0.55
    richness_lo: float = 0.1
    richness_hi: float = 1.0
    noise: float = 0.03
    p_fail_repairable: float = 0.45
    p_fail_hard: float = 0.85
    p_fail_other: float = 0.04
    context_coupling: float = 0.0

    def to_json(self) -> dict:
        return asdict(self)


class SyntheticWorld:
    """The generator: richness per direction, branch draws, attempt outcomes."""

    def __init__(self, cfg: SyntheticConfig) -> None:
        self.cfg = cfg
        self.directions = [f"d{i}" for i in range(cfg.n_directions)]
        self.richness = {d: _rng(cfg.seed, "rich", d).uniform(cfg.richness_lo, cfg.richness_hi)
                         for d in self.directions}

    def r_eff(self, d: str, lineage: dict) -> float:
        return self.richness.get(d, 0.3) * self.cfg.depletion ** int(lineage.get(d, 0))

    def branch(self, round_index: int, branch: int, direction: str, q_root: float, lineage: dict) -> dict:
        rng = _rng(self.cfg.seed, "branch", round_index, branch)
        r = self.r_eff(direction, lineage)
        w = {"steady": 0.30 * r + 0.05, "late": 0.15 * r + 0.05, "plateau": 0.20, "flat": 0.30 * (1 - r) + 0.05,
             "hardfail": 0.10, "repairable": 0.12}
        x, acc, kind = rng.random() * sum(w.values()), 0.0, "flat"
        for k in TYPES:
            acc += w[k]
            if x <= acc:
                kind = k
                break
        u = rng.uniform(0.4, 1.0)
        G = self.cfg.gain_scale * max(0.0, 1.0 - q_root) * r * u
        return {"type": kind, "G": G, "direction": direction, "r_eff": r}

    def outcome(self, round_index: int, branch: int, attempt: int, br: dict, q_root: float,
                n_siblings: int = 0) -> tuple[Optional[float], str]:
        rng = _rng(self.cfg.seed, "attempt", round_index, branch, attempt)
        kind = br["type"]
        p_fail = {"hardfail": self.cfg.p_fail_hard, "repairable": self.cfg.p_fail_repairable}.get(
            kind, self.cfg.p_fail_other)
        f, z = rng.random(), rng.gauss(0.0, 1.0)
        if f < p_fail:
            if kind == "hardfail":
                return None, "env_error"
            return None, "correctness" if rng.random() < 0.5 else "compile_other"
        q = q_root + br["G"] * phi(kind, attempt) + self.cfg.noise * br["G"] * z
        if self.cfg.context_coupling:
            q += self.cfg.context_coupling * br["G"] * (min(n_siblings, 24) / 24.0 - 0.5)
        return float(min(1.0, max(0.0, q))), "ok"

    def ground_truth(self, br: dict, depth: int) -> float:
        """Expected best gain of a branch within attempts 0..depth (noise-free, success-weighted)."""
        kind = br["type"]
        if kind == "hardfail":
            return 0.0
        return br["G"] * max(phi(kind, a) for a in range(depth + 1))


# --------------------------------------------------------------------------- agent
class SyntheticAgent(DiscoveryAgent):
    """The frozen 'coding agent' of a synthetic world (one call = one attempt)."""

    def __init__(self, world: SyntheticWorld, tokens_per_call: int = 0) -> None:
        self.world = world
        self.tokens_per_call = tokens_per_call

    def attempt(self, ctx: AttemptContext, *, seed: int) -> AgentAttempt:
        parent = json.loads(ctx.parent_workspace[STATE])
        if ctx.attempt == 0:
            q_root = float(parent["quality"])
            lineage = dict(parent.get("lineage", {}))
            direction = ctx.direction.get("direction") or self.world.directions[ctx.branch % len(self.world.directions)]
            br = self.world.branch(ctx.round, ctx.branch, direction, q_root, lineage)
        else:
            q_root = float(parent["root_quality"])
            lineage = dict(parent.get("root_lineage", {}))
            br = dict(parent["branch"])
            direction = br["direction"]
        q, fc = self.world.outcome(ctx.round, ctx.branch, ctx.attempt, br, q_root, n_siblings=len(ctx.siblings))
        child_lineage = dict(lineage)
        child_lineage[direction] = child_lineage.get(direction, 0) + 1
        state = {"quality": q if q is not None else float(parent.get("quality", q_root)), "ok": fc == "ok",
                 "fail_class": fc, "root_quality": q_root, "root_lineage": lineage, "lineage": child_lineage,
                 "branch": br, "round": ctx.round, "attempt": ctx.attempt}
        prop = f"apply mechanism {direction} (attempt {ctx.attempt})"
        if ctx.direction.get("advised"):
            prop += " (direction chosen following written guidance)"
        return AgentAttempt(Artifact({STATE: json.dumps(state, sort_keys=True)}), prop,
                            Usage(1, self.tokens_per_call, 0, 0.0, 0.0))


# -------------------------------------------------------------------------- domain
class SyntheticDomain(Domain):
    """rsi.core Domain view of a synthetic world (artifact = one ``state.json``)."""

    name = "synthetic"

    def __init__(self, cfg: Optional[SyntheticConfig] = None) -> None:
        t = Task("synthetic-instance", None, None, family="synthetic")
        super().__init__(TaskSuite([t], {"evolve": [t.id]}, name="synthetic"))
        self.cfg = cfg or SyntheticConfig()
        self.world = SyntheticWorld(self.cfg)

    def describe(self) -> str:
        return ("Synthetic discovery world: a program has a quality in [0,1]; mechanisms (directions) differ in "
                "hidden richness and deplete once used.")

    def seed_artifact(self) -> Artifact:
        return Artifact({STATE: json.dumps({"quality": self.cfg.q0, "ok": True, "fail_class": "ok",
                                            "lineage": {}}, sort_keys=True)})

    def directions(self) -> list[str]:
        return list(self.world.directions)

    def evaluate_program(self, artifact: Artifact, *, seed: int = 0) -> EvalOutcome:
        try:
            st = json.loads(artifact[STATE])
        except (KeyError, json.JSONDecodeError) as e:
            return EvalOutcome(0.0, True, False, "compile_other", f"bad state: {e}", 0, 1)
        fc = st.get("fail_class", "ok")
        if fc != "ok":
            return EvalOutcome(0.0, True, False, fc, f"simulated {fc} failure", 0, 1)
        return EvalOutcome(float(st["quality"]), True, True, "ok", None, 1, 1)

    def mock_agent(self, **kw) -> SyntheticAgent:
        return SyntheticAgent(self.world, **kw)

    def execute(self, artifact, task, *, seed, llm=None):
        ev = self.evaluate_program(artifact, seed=seed)
        return Execution(output={"score": ev.score, "fail_class": ev.fail_class, "error": ev.error})

    def grade(self, task, execution):
        o = execution.output or {}
        return (float(o.get("score") or 0.0), o.get("error") or "") if o.get("fail_class") == "ok" else \
            (0.0, str(o.get("error")))

    def as_task(self) -> DiscoveryTask:
        dom = self

        class _Task(DiscoveryTask):
            name = "synthetic"

            def describe(self):
                return dom.describe()

            def seed_artifact(self):
                return dom.seed_artifact()

            def evaluate(self, artifact, *, seed=0):
                return dom.evaluate_program(artifact, seed=seed)

            def directions(self):
                return dom.directions()

        return _Task()

    # ---- ground truth
    def branch_params(self, round_index: int, branch: int, direction: str, q_root: float,
                      lineage: Optional[dict] = None) -> dict:
        return self.world.branch(round_index, branch, direction, q_root, lineage or {})

    def truth_for_tree(self, tree, depth: Optional[int] = None, lineage: Optional[dict] = None) -> dict[int, float]:
        """Ground-truth branch quality (expected best score within ``depth``) for every branch of
        a tree recorded in this world, recomputed from the generator (the record does not
        contain the latent types)."""
        rnd = int(tree.meta.get("round", 1))
        d = tree.trace_refine_count if depth is None else depth
        out = {}
        for b in tree.branches():
            direction = tree.branch_tags.get(b, {}).get("direction") or self.world.directions[b % len(self.world.directions)]
            br = self.branch_params(rnd, b, direction, tree.root_score, lineage)
            out[b] = tree.root_score + self.world.ground_truth(br, d)
        return out


def expected_phi_best(kind: str, depth: int) -> float:
    return 0.0 if kind == "hardfail" else max(phi(kind, a) for a in range(depth + 1))

