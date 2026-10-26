"""RRSI hyperparameters (paper symbols; S in [0, 1], dC relative), engineering knobs and per-domain presets."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

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
                n_fail_traces=22, n_success_traces=6, eval_parallel=1, smoke_n=4, smoke_require_score=True,
                notes={"max_valid_rate_drop": 0.03, "max_no_payload_rise": 0.02, "smoke_n": 4}),
    # Overview page: "Illustrative settings: b_min = 1, b_max = 4, T = 10."
    "overview": dict(T=10, b_min=1, b_max=4),
}


@dataclass
class Config:
    """Defaults = ``RRSIConfig`` of the released code; fields marked "extension" are off by default."""

    # horizon and estimator
    T: int = 20
    k: int = 2
    m: int = 2
    # proposal side (Alg. 1)
    b_min: int = 1
    b_max: int = 4
    budget_rounding: str = "ceil"        # "ceil" | "floor_at_bmin_last"
    w: int = 3
    m_draft: int = 1
    # selection side (Alg. 2)
    delta: Optional[float] = None        # None -> calibrate
    delta_z: float = 2.0
    beta0: float = 0.10
    beta1: float = 40.0
    w_s: float = 100.0
    w_c: float = 15.0
    w_n: float = 0.5
    n_prune: int = 4
    # engineering
    repair_rounds: int = 5
    invalid_missing_frac: float = 0.15
    n_fail_traces: int = 22
    n_success_traces: int = 6
    eval_parallel: int = 1
    # calibration
    calibration_repeats: int = 1         # 1 = within-task bootstrap of one evaluation
    calibration_reps: int = 2000
    calibration_seed: int = 7
    bootstrap_small_k_correction: bool = True    # se * sqrt(k/(k-1)); False = the paper code's plug-in estimate
    # proposer protocol
    max_done_bounces: int = 3
    max_abort_bounces: int = 3
    history_render_n: int = 40
    scoreboard_n: int = 20
    trace_chars: int = 3000
    max_digests: int = 8
    analyst: str = "auto"                # "auto" | "llm" | "heuristic" | "agentic"
    proposer_protocol: str = "rewrite"   # "rewrite" | "json_actions"
    # fidelity options
    tie_eps: float = 0.0                 # 0.0 = raw float comparisons, as in the code
    proposer_numbers: bool = False       # extension: show delta, S*, S_t, T and numeric weights to the proposer
    precheck_answers: bool = False       # extension: deny literal answers/entities in the precheck
    precheck_scope: str = "added"        # "added" (added lines) | "diff" (the code's whole-diff grep)
    component_aliases: bool = False      # extension: accept tag aliases ("tool" -> "client_tool")
    smoke_n: int = 2
    smoke_require_score: bool = False    # eng: smoke must also score above the failure score
    # plumbing
    workers: int = 4
    trial_cache: bool = False
    smoke: bool = True
    heldout_monitor: bool = False        # extension: log holdout score of each new incumbent
    trace: bool = True
    shadow_monitor: bool = True
    shadow_monitor_k: int = 1
    trace_max_text: int = 100_000
    record_timestamps: bool = True
    editable: Optional[list[str]] = None  # glob patterns the proposer may edit (None = every file)
    seed: int = 0
    notes: dict = field(default_factory=dict)

    @classmethod
    def preset(cls, name: str = "default", **overrides: Any) -> "Config":
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
        return Config.from_dict({**self.dump(), **kw})
