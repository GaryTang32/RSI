"""Hyperparameters of RRSI (paper Appendix "Hyperparameters") plus the engineering
knobs a run needs that are not part of the method (mirrors ``rrsi/config.py``).

Symbols follow the paper:

=============  =================================================================
``T``          number of rounds, t = 0..T-1
``k``          trials per task inside Evaluate (Eq. estimate)
``m``          candidate harnesses drawn per round, |C_t| before screening
``b_min/max``  bounds of the annealed L0 edit budget b_t (Eq. anneal)
``w``          stall window of the exploration flag sigma_t (Eq. explore)
``m_draft``    candidate slots reserved for exploratory edits when stalled
``delta``      empirical noise band (None -> calibrate from the base evaluation)
``beta0/1``    cost rule for gaining candidates (Eq. tokenbudget)
``w_s/c/n``    shaped rule for candidates inside the noise band (Alg. 2 line 5)
``n_prune``    window of the recent-yield summary g_t (Eq. yield / prune)
=============  =================================================================

Units: scores S are fractions in [0, 1], cost C is mean policy tokens per trial,
dC is RELATIVE, so ``beta0 = 0.10`` means "10% more tokens for free" and
``beta1 = 40`` means "each +1pp of S buys +40%".

Per-instance presets reproduce the released ``domains/*/rrsi.json`` values
(``Config.preset("coding" | "workspace" | "eng" | "overview")``).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

#: Values from ``domains/{coding,workspace,eng}/rrsi.json`` and the overview's illustrative settings.
PRESETS: dict[str, dict[str, Any]] = {
    "default": {},
    # Terminal-Bench 2.1: delta = 3 passes of 178; beta1 = 25% tokens per extra pass; w_s = 0.
    "coding": dict(T=20, k=2, m=2, b_min=1, b_max=4, w=3, m_draft=1, delta=0.017, delta_z=2.0, beta0=0.10,
                   beta1=44.5, w_s=0.0, w_c=15.0, w_n=0.5, n_prune=4, repair_rounds=5, invalid_missing_frac=0.2,
                   n_fail_traces=30, n_success_traces=6, eval_parallel=1),
    # Harvey LAB: delta = 60 criteria of ~14,100; beta1 = 25% per 100 criteria; w_s = 0.1 per criterion.
    "workspace": dict(T=20, k=2, m=2, b_min=1, b_max=3, w=3, m_draft=1, delta=0.004, delta_z=2.0, beta0=0.10,
                      beta1=35.4, w_s=1414.0, w_c=15.0, w_n=0.5, n_prune=4, repair_rounds=5,
                      invalid_missing_frac=0.1, n_fail_traces=60, n_success_traces=6, eval_parallel=1),
    # EngDesign: delta = 5 passes of 244; beta1 = 10% per pass; w_s = 1 per pass; guards on valid/no-payload.
    "eng": dict(T=40, k=4, m=2, b_min=1, b_max=4, w=3, m_draft=1, delta=0.020, delta_z=2.0, beta0=0.15,
                beta1=24.4, w_s=244.0, w_c=2.0, w_n=0.5, n_prune=5, repair_rounds=5, invalid_missing_frac=0.15,
                n_fail_traces=22, n_success_traces=6, eval_parallel=1,
                notes={"max_valid_rate_drop": 0.03, "max_no_payload_rise": 0.02, "smoke_n": 4}),
    # Overview page: "Illustrative settings: b_min = 1, b_max = 4, T = 10."
    "overview": dict(T=10, b_min=1, b_max=4),
}


@dataclass
class Config:
    """RRSI hyperparameters (defaults = ``RRSIConfig`` of google-research/rrsi)."""

    # ---- horizon and estimator -------------------------------------------
    T: int = 20
    k: int = 2
    m: int = 2
    # ---- proposal side (Algorithm 1) -------------------------------------
    b_min: int = 1
    b_max: int = 4
    budget_rounding: str = "ceil"        # "ceil" (released code) | "floor_at_bmin_last" (reaches b_min at t=T-1)
    w: int = 3
    m_draft: int = 1
    # ---- selection side (Algorithm 2) ------------------------------------
    delta: Optional[float] = None        # None -> calibrate (calibration.json)
    delta_z: float = 2.0                 # delta = z * sd(null dS)
    beta0: float = 0.10
    beta1: float = 40.0
    w_s: float = 100.0
    w_c: float = 15.0
    w_n: float = 0.5
    n_prune: int = 4
    # ---- engineering knobs (not part of the method) ----------------------
    repair_rounds: int = 5               # critic -> proposer repair attempts
    invalid_missing_frac: float = 0.15
    n_fail_traces: int = 22
    n_success_traces: int = 6
    eval_parallel: int = 1               # candidates evaluated concurrently
    # ---- calibration -----------------------------------------------------
    calibration_repeats: int = 1         # R base evaluations; 1 = within-task bootstrap of one evaluation
    calibration_reps: int = 2000         # bootstrap resamples (code: 2000)
    calibration_seed: int = 7            # bootstrap seed (code: 7)
    bootstrap_small_k_correction: bool = False   # extension: rescale se by sqrt(k/(k-1)) (off = faithful)
    # ---- proposer protocol ----------------------------------------------
    max_done_bounces: int = 3            # done() contract violations bounced back before giving up
    max_abort_bounces: int = 3           # "there is no abort action" (code: 3)
    history_render_n: int = 40           # History.render(n=40)
    scoreboard_n: int = 20               # last 20 attribution rows
    trace_chars: int = 3000              # per-trace cap in stored evaluations / proposer context
    max_digests: int = 8                 # LLM analyst: digests per round (<= 8 per digest_many in the code)
    analyst: str = "auto"                # "auto" | "llm" | "heuristic"
    # ---- plumbing ---------------------------------------------------------
    workers: int = 4                     # rollouts in parallel inside one evaluation
    trial_cache: bool = False            # persist every trial under out_dir/trials (resume mid-evaluation)
    smoke: bool = True                   # liveness smoke before evaluation (not a selection rule)
    heldout_monitor: bool = False        # extension: score each new incumbent on holdout (logged only)
    record_timestamps: bool = True       # history "ts" field (off -> byte-identical resumed ledgers)
    editable: Optional[list[str]] = None  # glob patterns the proposer may edit (None = every file)
    seed: int = 0
    notes: dict = field(default_factory=dict)

    # ---- construction ---------------------------------------------------------
    @classmethod
    def preset(cls, name: str = "default", **overrides: Any) -> "Config":
        """Config from a named preset (``default``, ``coding``, ``workspace``, ``eng``, ``overview``)."""
        if name not in PRESETS:
            raise KeyError(f"unknown preset {name!r}; choose from {sorted(PRESETS)}")
        kw = dict(PRESETS[name])
        kw.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**kw)

    @classmethod
    def load(cls, path: str | Path, **overrides: Any) -> "Config":
        """Load an ``rrsi.json``-style file; unknown keys go to ``notes``."""
        raw = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in raw.items() if k in known}
        kw["notes"] = {**raw.get("notes", {}), **{k: v for k, v in raw.items() if k not in known}}
        kw.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**kw)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})

    def dump(self) -> dict:
        return asdict(self)

    def replace(self, **kw: Any) -> "Config":
        d = self.dump()
        d.update(kw)
        return Config.from_dict(d)
