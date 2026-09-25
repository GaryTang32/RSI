"""Mechanism registry: harness config -> ordered list of extensions.

A SoL-Pi *harness artifact* is an :class:`rsi.core.Artifact` with

* ``harness.json`` - ``{"extensions": {name: params, ...}, "auto_compact": bool}``;
* optional ``extensions/<name>.py`` - code mechanisms (e.g. written by an LLM
  implementer) defining ``MECHANISM`` (an :class:`Extension` subclass or instance)
  or ``register(rt, config)``;
* optional ``system_prompt.md``.

Built-ins register in SoL-Pi's order (Action Fusion, ObservationPack, EPR, Online
Context Compact - so OCC observes the context after the others), then any other
mechanism by name. :func:`parse_solpi_config` validates the release's
``sol-pi.json`` schema (unknown keys, wrong version, non-boolean flags, invalid
ratio or empty model strings are fatal) and maps it to a harness config.
"""
from __future__ import annotations

import json
import math
from typing import Any, Callable, Optional

from .fusion import ActionFusion
from .obspack import ObservationPack
from .occ import OnlineContextCompact
from .reducer import DeterministicReducer, EvidencePreservingReducer, LLMReducer, MockReducer
from .runtime import AgentRuntime, Extension, Message, ToolError, ToolResult, ToolSpec
from .tricks import HeadTrim, NoVerify, PromptSlim, PytestQuiet, TailTrim, TurnCap

ORDER = ("action_fusion", "observation_pack", "evidence_preserving_reducer", "online_context_compact")
GENERAL = ORDER
TRICKS = ("tail_trim", "head_trim", "pytest_quiet")
DO_LESS = ("no_verify", "turn_cap")
DUDS = ("prompt_slim",)


def _reducer(cfg: dict, ctx: dict):
    kind = cfg.get("reducer", "deterministic")
    if kind == "mock":
        return MockReducer(h=float(cfg.get("h", 0.0)), seed=int(ctx.get("seed", 0)))
    if kind == "llm":
        llm = ctx.get("reducer_llm")
        if llm is None:
            raise ValueError("EPR reducer 'llm' needs ctx['reducer_llm']")
        return LLMReducer(llm)
    return DeterministicReducer()


def _obspack(c: dict) -> ObservationPack:
    op = ObservationPack(full_sends=int(c.get("full_sends", 2)), excerpt_bytes=int(c.get("excerpt_bytes", 1024)),
                         head_frac=float(c.get("head_frac", 0.5)))
    op.fail_store = bool(c.get("fail_store", False))       # fault injection (experiments/tests)
    return op


MECHANISMS: dict[str, Callable[[dict, dict], Extension]] = {
    "action_fusion": lambda c, x: ActionFusion(),
    "observation_pack": lambda c, x: _obspack(c),
    "evidence_preserving_reducer": lambda c, x: EvidencePreservingReducer(_reducer(c, x)),
    "online_context_compact": lambda c, x: OnlineContextCompact(
        cache_write_read_ratio=c.get("cache_write_read_ratio", 12.5), policy=c.get("policy", "economic")),
    "tail_trim": lambda c, x: TailTrim(int(c.get("lines", 40))),
    "head_trim": lambda c, x: HeadTrim(int(c.get("lines", 60))),
    "pytest_quiet": lambda c, x: PytestQuiet(),
    "no_verify": lambda c, x: NoVerify(),
    "turn_cap": lambda c, x: TurnCap(int(c.get("max_turns", 24))),
    "prompt_slim": lambda c, x: PromptSlim(),
}


class FunctionExtension(Extension):
    def __init__(self, name: str, fn: Callable[[AgentRuntime, dict], None], config: dict) -> None:
        super().__init__()
        self.name, self.fn, self.config = name, fn, config

    def register(self, rt: AgentRuntime) -> None:
        self.fn(rt, self.config)


def load_code_extension(name: str, source: str, config: dict) -> Extension:
    """Exec an ``extensions/<name>.py`` mechanism with the runtime API pre-imported."""
    ns: dict[str, Any] = {"Extension": Extension, "ToolSpec": ToolSpec, "ToolResult": ToolResult,
                          "ToolError": ToolError, "Message": Message, "AgentRuntime": AgentRuntime,
                          "__name__": f"solpi_ext_{name}"}
    exec(compile(source, f"extensions/{name}.py", "exec"), ns)  # noqa: S102 - mechanism code is the artifact
    mech = ns.get("MECHANISM")
    if isinstance(mech, type) and issubclass(mech, Extension):
        ext = mech(**config) if config else mech()
    elif isinstance(mech, Extension):
        ext = mech
    elif callable(ns.get("register")):
        ext = FunctionExtension(name, ns["register"], config)
    else:
        raise TypeError(f"extensions/{name}.py defines neither MECHANISM nor register(rt, config)")
    ext.name = getattr(ext, "name", name) or name
    return ext


def harness_config(files: dict[str, str]) -> dict:
    raw = files.get("harness.json")
    return json.loads(raw) if raw else {"extensions": {}}


def build_extensions(files: dict[str, str], ctx: Optional[dict] = None) -> list[Extension]:
    ctx = ctx or {}
    cfg = harness_config(files)
    exts = cfg.get("extensions", {}) or {}
    names = [n for n in ORDER if n in exts] + sorted(n for n in exts if n not in ORDER)
    out = []
    for n in names:
        params = exts[n] or {}
        if n in MECHANISMS:
            out.append(MECHANISMS[n](params, ctx))
        elif f"extensions/{n}.py" in files:
            out.append(load_code_extension(n, files[f"extensions/{n}.py"], params))
        else:
            raise KeyError(f"unknown mechanism {n!r} (no built-in and no extensions/{n}.py)")
    return out


def with_mechanism(files: dict[str, str], name: str, params: Optional[dict] = None,
                   code: Optional[str] = None) -> dict[str, str]:
    """Files of a harness with one more mechanism enabled."""
    cfg = harness_config(files)
    cfg.setdefault("extensions", {})[name] = params or {}
    out = dict(files)
    out["harness.json"] = json.dumps(cfg, indent=1, sort_keys=True)
    if code is not None:
        out[f"extensions/{name}.py"] = code
    return out


# ------------------------------------------------------------------ sol-pi.json (config.ts)
SOLPI_KEYS = {"version", "actionFusion", "observationPack", "evidencePreservingReducer",
              "evidencePreservingReducerProvider", "evidencePreservingReducerModel", "onlineContextCompact",
              "cacheWriteReadRatio"}
SOLPI_DEFAULTS = {"version": 1, "actionFusion": False, "observationPack": False, "evidencePreservingReducer": False,
                  "evidencePreservingReducerProvider": "openai-codex", "evidencePreservingReducerModel": "gpt-5.6-luna",
                  "onlineContextCompact": False, "cacheWriteReadRatio": 12.5}


def parse_solpi_config(d: dict) -> dict:
    """Validate a ``sol-pi.json`` document and return a harness config."""
    unknown = set(d) - SOLPI_KEYS
    if unknown:
        raise ValueError(f"unknown sol-pi config keys: {sorted(unknown)}")
    c = {**SOLPI_DEFAULTS, **d}
    if c["version"] != 1:
        raise ValueError("unsupported sol-pi config version")
    for k in ("actionFusion", "observationPack", "evidencePreservingReducer", "onlineContextCompact"):
        if not isinstance(c[k], bool):
            raise ValueError(f"{k} must be a boolean")
    r = c["cacheWriteReadRatio"]
    if not isinstance(r, (int, float)) or isinstance(r, bool) or not math.isfinite(r) or r < 0:
        raise ValueError("cacheWriteReadRatio must be a finite non-negative number")
    for k in ("evidencePreservingReducerProvider", "evidencePreservingReducerModel"):
        if not isinstance(c[k], str) or not c[k].strip():
            raise ValueError(f"{k} must be a non-empty string")
    exts: dict[str, dict] = {}
    if c["actionFusion"]:
        exts["action_fusion"] = {}
    if c["observationPack"]:
        exts["observation_pack"] = {}
    if c["evidencePreservingReducer"]:
        exts["evidence_preserving_reducer"] = {"provider": c["evidencePreservingReducerProvider"],
                                               "model": c["evidencePreservingReducerModel"]}
    if c["onlineContextCompact"]:
        exts["online_context_compact"] = {"cache_write_read_ratio": c["cacheWriteReadRatio"]}
    return {"extensions": exts}
