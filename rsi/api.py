"""One entry point for every technique: ``rsi.improve(domain, seed, method=...)``."""
from __future__ import annotations

import dataclasses
import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from .core import LLM, Artifact, Domain, ImprovementResult, get_llm


@dataclass(frozen=True)
class MethodInfo:
    name: str
    module: str
    improves: str
    keep_rule: str
    guards_against_overfitting: str
    use_when: str


METHODS: dict[str, MethodInfo] = {
    "autoresearch": MethodInfo(
        "autoresearch", "rsi.autoresearch", "the work: one editable file (e.g. a training script) under a fixed budget",
        "strictly better on one number (or bootstrap rigor / RRSI gate)", "locked grader + hidden audits",
        "you have a runnable script with one metric and a fixed per-experiment budget"),
    "rrsi": MethodInfo(
        "rrsi", "rsi.rrsi", "the agent harness: prompts, control flow, tools, memory, skills",
        "within the noise floor of the best, passes the leakage critic, cost earned; argmax",
        "seven proposal- and selection-side regularizers; evaluated on unseen suites",
        "gains must transfer to tasks the loop never saw, and cost matters"),
    "dream": MethodInfo(
        "dream", "rsi.dream", "the search strategy (exploration policy code) of a discovery loop",
        "best mean replay score over past searches (incumbent included; optional held-out worlds)",
        "optional RRSI-style held-out worlds + noise margin",
        "you run many-attempt program/solution searches and want cheaper, better searching"),
    "evomap": MethodInfo(
        "evomap", "rsi.evomap", "a shared library of compact strategy genes across agents",
        "local validation; hub admission (naive = self-reported; SafeHub = verified uplift)",
        "SafeHub: discriminative validation, hidden-task uplift LCB, adoption ranking, quarantine",
        "several agents/models face related tasks and should inherit each other's lessons"),
    "gepa": MethodInfo(
        "gepa", "rsi.gepa", "named prompt components of a compound AI system",
        "strict improvement on a minibatch, then full validation; Pareto frontier + merge",
        "diverse per-instance Pareto frontier; separate validation set",
        "you want better prompts from few rollouts and have textual feedback"),
    "metaharness": MethodInfo(
        "metaharness", "rsi.metaharness", "harness code end to end (what to store, retrieve, present)",
        "none (every candidate evaluated; Pareto frontier of score vs context cost)",
        "full uncompressed history; optional leakage screen; finalize-once test",
        "you have a strong coding agent and want broad harness rewrites"),
    "solpi": MethodInfo(
        "solpi", "rsi.solpi", "harness mechanisms that cut cost (tokens) at equal quality",
        "dual gate: every capability metric within tolerance AND some efficiency metric improves",
        "keep only mechanisms that survive every environment family; one-way held-out firewall",
        "quality is already fine and tokens/cost are the bottleneck"),
    "harness2": MethodInfo(
        "harness2", "rsi.harness2", "the execution harness of each task at test time (Propose-Probe-Compose from "
        "H0), plus a per-domain library of adopted harnesses with lessons",
        "no score: composers merge/adopt/keep from behavioural contrasts; an anonymised selector picks among the "
        "k composed re-executions by deliverable",
        "open-world guard (no grader, label or score reaches the loop); validation retry-once-then-inherit; "
        "lessons stored only when execution contrasts support them",
        "tasks arrive one at a time with no grader or labels (open-ended work) and ~4k rollouts per task are "
        "affordable"),
}
ALIASES = {"dream-rsi": "dream", "harness-squared": "harness2", "harness²": "harness2", "meta-harness": "metaharness", "sol-pi": "solpi", "karpathy": "autoresearch"}


def method_module(name: str):
    """Import and return the package implementing ``name``."""
    key = ALIASES.get(name, name)
    if key not in METHODS:
        raise KeyError(f"unknown method {name!r}; choose from {sorted(METHODS)}")
    return importlib.import_module(METHODS[key].module)


def make_config(method: str, config: Union[None, Mapping[str, Any], Any] = None):
    """Build the method's ``Config`` from a dict of overrides (a Config passes through)."""
    cfg_cls = method_module(method).Config
    if config is None:
        return cfg_cls()
    if not isinstance(config, Mapping):
        return config
    if dataclasses.is_dataclass(cfg_cls):
        fields = {f.name for f in dataclasses.fields(cfg_cls)}
        if unknown := [k for k in config if k not in fields]:
            raise TypeError(f"{method} Config has no fields {unknown}; valid: {sorted(fields)}")
    return cfg_cls(**dict(config))


def _llm(x: Union[None, str, LLM], cache_dir: Optional[str] = None) -> Optional[LLM]:
    return x if x is None or isinstance(x, LLM) else get_llm(x, cache_dir=cache_dir)


def improve(domain: Domain, seed_artifact: Optional[Artifact] = None, *, method: str = "rrsi",
            llm_task: Union[None, str, LLM] = None, llm_propose: Union[None, str, LLM] = None,
            config: Union[None, Mapping[str, Any], Any] = None, out_dir: Optional[Union[str, Path]] = None,
            cache_dir: Optional[str] = None, **kwargs: Any) -> ImprovementResult:
    """Run one technique on ``domain``; LLMs may be spec strings, ``kwargs`` go to the method's ``run``."""
    mod = method_module(method)
    if seed_artifact is None:
        seed_fn = getattr(domain, "seed_artifact", None)
        if seed_fn is None:
            raise ValueError("pass seed_artifact (the domain has no seed_artifact())")
        seed_artifact = seed_fn()
    return mod.run(domain, seed_artifact, llm_task=_llm(llm_task, cache_dir), llm_propose=_llm(llm_propose, cache_dir),
                   config=make_config(method, config), out_dir=str(out_dir) if out_dir else None, **kwargs)


def recommend(*, what_changes: str = "harness", must_transfer: bool = True, cost_is_bottleneck: bool = False,
              many_agents: bool = False, search_heavy: bool = False, single_metric_script: bool = False,
              prompts_only: bool = False, no_grader: bool = False) -> list[tuple[str, str]]:
    """Rank methods for a problem description as ``[(method, reason), ...]``.

    ``no_grader``: open-ended tasks with no grader, labels or score; only Harness² applies (every other method
    keeps or ranks candidates by a grade)."""
    if no_grader:
        return [("harness2", "no grader or labels: per-task Propose-Probe-Compose from execution contrasts, an "
                             "anonymised deliverable selector and a per-domain library; never reads a score")]
    out: list[tuple[str, str]] = []
    if single_metric_script or what_changes in ("script", "training", "code-file"):
        out.append(("autoresearch", "one editable file, one metric, fixed budget: the simplest loop that works"))
    if prompts_only or what_changes == "prompts":
        out.append(("gepa", "reflective prompt evolution: few rollouts, uses textual feedback, Pareto diversity"))
    if what_changes in ("harness", "agent") and must_transfer:
        out.append(("rrsi", "regularized harness evolution: only changes that generalize and pay for their cost"))
    if cost_is_bottleneck:
        out.append(("solpi", "dual gate keeps quality within tolerance while cutting tokens, across environments"))
    if search_heavy or what_changes in ("strategy", "search"):
        out.append(("dream", "improves the search policy by replaying past searches, almost free between runs"))
    if many_agents:
        out.append(("evomap", "share compact, verified genes across agents; use SafeHub, never self-reported checks"))
    if what_changes in ("harness", "agent") and not must_transfer:
        out.append(("metaharness", "full-history coding-agent proposer: strongest on the practised tasks"))
    return out or [("rrsi", "safe default: guarded harness evolution with held-out evaluation")]


def describe_methods() -> str:
    return "\n\n".join(
        f"{m.name:13s} improves {m.improves}\n{'':13s} keep: {m.keep_rule}\n{'':13s} use when: {m.use_when}"
        for m in METHODS.values())
