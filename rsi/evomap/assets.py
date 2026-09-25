"""Gene / Capsule / EvolutionEvent / Mutation / ValidationReport (spec §5, §9.1).

The dataclasses mirror the GEP 1.14.0 objects plus the extra fields the engine
really writes (``avoid``, ``provenance`` (= engine ``_source``), ``parent``,
object-valued ``epigenetic_marks``, string ``diff`` / ``content``). Unknown keys
read from JSON are kept in ``extra`` so a round trip preserves the asset id.

* ``to_dict()`` - the engine form (``None`` values and empty optional
  collections are *omitted*, never serialized as ``null``, because ``null``
  changes the hash);
* ``stamp()``   - set ``asset_id`` from the canonical form;
* ``verify()``  - recompute and compare (tamper check);
* ``to_gep()``  - only the fields the strict schema allows (for export to other
  GEP-compatible runtimes).

A :class:`Clock` makes ids and timestamps deterministic in simulations
(``evt_<node>_<n>`` rather than wall-clock milliseconds).
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as _dt
from dataclasses import dataclass, field
from typing import Any, Optional

from .hashing import SCHEMA_VERSION, asset_id, verify_asset_id
from .schema import load_schema

CATEGORIES = ("repair", "optimize", "innovate", "explore")
DEFAULT_FORBIDDEN = [".git", "node_modules"]


class Clock:
    """Logical clock: ``now()`` in seconds since an epoch, advanced explicitly
    (``tick``) in simulations or following wall time (``real=True``)."""

    def __init__(self, start_s: float = 1_780_000_000.0, step_s: float = 3600.0, real: bool = False) -> None:
        self.t = float(start_s)
        self.step_s = step_s
        self.real = real

    def now(self) -> float:
        if self.real:
            import time
            return time.time()
        return self.t

    def tick(self, n: float = 1.0) -> float:
        self.t += n * self.step_s
        return self.t

    def iso(self, t: Optional[float] = None) -> str:
        return _dt.datetime.fromtimestamp(self.now() if t is None else t, _dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")


def _clean(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items() if x is not None}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


class _Asset:
    """Shared plumbing for the asset dataclasses."""

    TYPE = "Asset"
    #: optional collection fields omitted from the dict when empty
    _OMIT_EMPTY: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"type": self.TYPE}
        for f in dataclasses.fields(self):  # type: ignore[arg-type]
            if f.name == "extra":
                continue
            v = getattr(self, f.name)
            if v is None:
                continue
            if f.name in self._OMIT_EMPTY and v in ([], {}, ""):
                continue
            out[f.name] = _clean(copy.deepcopy(v))
        for k, v in (getattr(self, "extra", None) or {}).items():
            if v is not None and k not in out:
                out[k] = copy.deepcopy(v)
        return out

    @classmethod
    def from_dict(cls, d: dict):
        names = {f.name for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
        kw = {k: copy.deepcopy(v) for k, v in d.items() if k in names and k != "extra"}
        extra = {k: copy.deepcopy(v) for k, v in d.items() if k not in names and k != "type"}
        obj = cls(**kw)  # type: ignore[call-arg]
        obj.extra = extra  # type: ignore[attr-defined]
        return obj

    def compute_id(self) -> str:
        return asset_id(self.to_dict())

    def stamp(self):
        self.asset_id = self.compute_id()  # type: ignore[attr-defined]
        return self

    def verify(self) -> bool:
        return verify_asset_id(self.to_dict())

    def to_gep(self) -> dict:
        """Strict-schema projection (drops engine-only fields; re-stamps asset_id)."""
        kind = self.TYPE
        schema = load_schema(kind)
        allowed = set(schema["properties"])
        d = {k: v for k, v in self.to_dict().items() if k in allowed}
        if kind == "Gene" and "epigenetic_marks" in d:
            d["epigenetic_marks"] = [f"{m.get('context')}:{m.get('boost')}" if isinstance(m, dict) else str(m)
                                     for m in d["epigenetic_marks"]]
        if kind == "Capsule":
            for k in ("content", "diff"):
                if isinstance(d.get(k), str):
                    d[k] = {"text": d[k]}
        d.pop("asset_id", None)
        d["asset_id"] = asset_id(d)
        return d

    def copy(self):
        return copy.deepcopy(self)


@dataclass
class Gene(_Asset):
    """A compact strategy card: trigger signals, a few steps, AVOID warnings,
    constraints and validation commands (paper form g = (m, u, pi, alpha, c, v, eta))."""

    id: str
    category: str = "repair"
    signals_match: list[str] = field(default_factory=list)
    strategy: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    summary: str = ""
    preconditions: list[str] = field(default_factory=list)
    constraints: dict = field(default_factory=lambda: {"max_files": 12, "forbidden_paths": list(DEFAULT_FORBIDDEN)})
    validation: list[str] = field(default_factory=list)
    epigenetic_marks: list[dict] = field(default_factory=list)
    learning_history: list[dict] = field(default_factory=list)
    anti_patterns: list[dict] = field(default_factory=list)
    scope: Optional[dict] = None
    claims: Optional[list] = None
    provenance: Optional[dict] = None
    parent: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    asset_id: Optional[str] = None
    extra: dict = field(default_factory=dict)

    TYPE = "Gene"
    _OMIT_EMPTY = ("avoid", "preconditions", "epigenetic_marks", "learning_history", "anti_patterns", "summary")

    @property
    def max_files(self) -> int:
        return int((self.constraints or {}).get("max_files") or 20)

    @property
    def forbidden_paths(self) -> list[str]:
        return list((self.constraints or {}).get("forbidden_paths") or [])

    def text(self) -> str:
        """All human-readable content (for token counts, lints and world parsers)."""
        return "\n".join([self.summary, *self.signals_match, *self.strategy, *self.avoid, *self.preconditions])

    def content_key(self) -> tuple:
        """Identity of the *content* (not metadata): used for dedup and lineage."""
        return (tuple(sorted(s.lower() for s in self.signals_match)), tuple(self.strategy), tuple(self.avoid))


@dataclass
class Capsule(_Asset):
    """Record of one successful application of a gene (worked case trace)."""

    id: str
    gene: str
    trigger: list[str] = field(default_factory=list)
    summary: str = ""
    confidence: float = 0.0
    blast_radius: dict = field(default_factory=lambda: {"files": 0, "lines": 0})
    outcome: dict = field(default_factory=lambda: {"status": "success", "score": 0.0})
    success_streak: Optional[int] = None
    success_reason: Optional[str] = None
    env_fingerprint: Optional[dict] = None
    source_type: Optional[str] = None
    reused_asset_id: Optional[str] = None
    content: Optional[str] = None
    diff: Optional[str] = None
    strategy: list[str] = field(default_factory=list)
    execution_trace: list[dict] = field(default_factory=list)
    a2a: Optional[dict] = None
    derivation_tokens: Optional[dict] = None
    proof_of_work: Optional[dict] = None
    gene_library_version: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    asset_id: Optional[str] = None
    extra: dict = field(default_factory=dict)

    TYPE = "Capsule"
    _OMIT_EMPTY = ("strategy", "execution_trace")


@dataclass
class EvolutionEvent(_Asset):
    """Append-only audit record of one cycle (success or failure)."""

    id: str
    intent: str = "repair"
    signals: list[str] = field(default_factory=list)
    genes_used: list[str] = field(default_factory=list)
    mutation_id: str = ""
    blast_radius: dict = field(default_factory=lambda: {"files": 0, "lines": 0})
    outcome: dict = field(default_factory=lambda: {"status": "failed", "score": 0.0})
    source_type: str = "generated"
    parent: Optional[str] = None
    personality_state: Optional[dict] = None
    capsule_id: Optional[str] = None
    reused_asset_id: Optional[str] = None
    env_fingerprint: Optional[dict] = None
    validation_report_id: Optional[str] = None
    meta: Optional[dict] = None
    schema_version: str = SCHEMA_VERSION
    asset_id: Optional[str] = None
    extra: dict = field(default_factory=dict)

    TYPE = "EvolutionEvent"


@dataclass
class Mutation(_Asset):
    """Transient description of the intended change (not persisted as an asset)."""

    id: str
    category: str = "repair"
    trigger_signals: list[str] = field(default_factory=list)
    target: str = "behavior:protocol"
    expected_effect: str = ""
    risk_level: str = "low"
    rationale: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    asset_id: Optional[str] = None
    extra: dict = field(default_factory=dict)

    TYPE = "Mutation"


@dataclass
class ValidationReport(_Asset):
    """Result of running a gene's validation specs; ``signer`` says who ran them
    (``local`` = the publisher, i.e. self-reported; ``hub`` = hub-run)."""

    id: str
    gene_id: str = ""
    commands: list[dict] = field(default_factory=list)   # {command, ok, exit, stdout, stderr, skipped?, blocked?}
    overall_ok: bool = False
    discriminative: Optional[dict] = None
    signer: str = "local"
    env_fingerprint: Optional[dict] = None
    duration_ms: int = 0
    created_at: Optional[str] = None
    meta: Optional[dict] = None
    schema_version: str = SCHEMA_VERSION
    asset_id: Optional[str] = None
    extra: dict = field(default_factory=dict)

    TYPE = "ValidationReport"

    @property
    def n_run(self) -> int:
        return sum(1 for c in self.commands if not c.get("skipped"))

    @property
    def n_passed(self) -> int:
        return sum(1 for c in self.commands if c.get("ok") and not c.get("skipped"))


PERSONALITY_TRAITS = ("rigor", "creativity", "verbosity", "risk_tolerance", "obedience")


@dataclass
class PersonalityState(_Asset):
    """Run-level meta-parameters (spec §3.4, §5.5)."""

    rigor: float = 0.7
    creativity: float = 0.35
    verbosity: float = 0.25
    risk_tolerance: float = 0.4
    obedience: float = 0.85
    extra: dict = field(default_factory=dict)

    TYPE = "PersonalityState"

    def key(self) -> str:
        return "|".join(f"{t}={round(getattr(self, t), 1):.1f}" for t in PERSONALITY_TRAITS)

    def clamp(self) -> "PersonalityState":
        for t in PERSONALITY_TRAITS:
            setattr(self, t, float(min(1.0, max(0.0, getattr(self, t)))))
        return self


ASSET_CLASSES = {"Gene": Gene, "Capsule": Capsule, "EvolutionEvent": EvolutionEvent, "Mutation": Mutation,
                 "ValidationReport": ValidationReport, "PersonalityState": PersonalityState}


def from_dict(d: dict):
    """Build the right asset class from a dict with a ``type`` key."""
    cls = ASSET_CLASSES.get(d.get("type", ""))
    if cls is None:
        raise ValueError(f"unknown asset type {d.get('type')!r}")
    return cls.from_dict(d)
