"""Command line: ``rsi methods | recommend | improve | inspect | experiments | experiment``."""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .api import describe_methods, improve, recommend
from .core import get_llm, transfer_report
from .trace import inspect

REPO = Path(__file__).resolve().parent.parent
EXPERIMENTS = REPO / "experiments"
EXP_DIRS = {"autoresearch": "autoresearch", "rrsi": "rrsi", "dream": "dream-rsi", "evomap": "evomap",
            "gepa": "gepa", "metaharness": "metaharness-solpi", "solpi": "metaharness-solpi", "core": "core-qa",
            "harness2": "harness2"}


def _parse_value(v: str) -> Any:
    try:
        return json.loads(v)
    except json.JSONDecodeError:
        return v


def load_problem(spec: str, *, seed: int = 0):
    """``(domain, seed_artifact, offline_task_llm)`` for ``agentqa`` or ``path/to/file.py:factory``."""
    if spec == "agentqa":
        from .domains.agentqa import AgentQADomain, SimModel, make_suite

        suite = make_suite(seed=seed)
        dom = AgentQADomain(suite)
        return dom, dom.seed_artifact(), SimModel(suite)
    if ":" not in spec:
        raise SystemExit(f"unknown problem {spec!r}: use 'agentqa' or 'path/to/file.py:factory'")
    path, _, fn = spec.rpartition(":")
    mod_spec = importlib.util.spec_from_file_location("rsi_user_problem", path)
    if mod_spec is None or mod_spec.loader is None:
        raise SystemExit(f"cannot import {path}")
    mod = importlib.util.module_from_spec(mod_spec)
    sys.modules["rsi_user_problem"] = mod
    mod_spec.loader.exec_module(mod)
    made = getattr(mod, fn)()
    dom, art = (made[0], made[1]) if isinstance(made, tuple) else (made, made.seed_artifact())
    return dom, art, getattr(mod, "OFFLINE_TASK_LLM", None)


def offline_proposer(method: str, problem: str):
    """Scripted offline proposer for a built-in problem, or None for the method's own default."""
    if problem != "agentqa":
        return None
    if method == "rrsi":
        from .rrsi.mocks import AgentQAMockLLM

        return AgentQAMockLLM()
    if method == "gepa":
        from .gepa.mocks import AgentQAReflectionLM

        return AgentQAReflectionLM()
    if method in ("harness2", "harness-squared"):
        from .harness2.mocks import MockImprover

        return MockImprover()
    return None


def cmd_methods(a) -> int:
    print(describe_methods())
    return 0


def cmd_recommend(a) -> int:
    for m, why in recommend(what_changes=a.what, must_transfer=not a.no_transfer, cost_is_bottleneck=a.cost,
                            many_agents=a.many_agents, search_heavy=a.search, single_metric_script=a.script,
                            prompts_only=a.prompts_only, no_grader=a.no_grader):
        print(f"{m:13s} {why}")
    return 0


def cmd_improve(a) -> int:
    dom, art, offline = load_problem(a.problem, seed=a.seed)
    llm_task = offline if a.llm == "sim" else get_llm(a.llm, cache_dir=a.cache)
    prop_spec = a.propose_llm or a.llm
    offline_mode = prop_spec in ("offline", "none", "sim")
    llm_prop = offline_proposer(a.method, a.problem) if offline_mode else get_llm(prop_spec, cache_dir=a.cache)
    cfg = {k: _parse_value(v) for k, v in (kv.split("=", 1) for kv in a.set)} if a.set else None
    try:
        res = improve(dom, art, method=a.method, llm_task=llm_task, llm_propose=llm_prop, config=cfg, out_dir=a.out)
    except ValueError as e:
        if offline_mode:
            raise SystemExit(f"{a.method} has no offline proposer for {a.problem!r} ({e}). Use --propose-llm "
                             f"claude:haiku, or run its offline experiments: rsi experiments {a.method}")
        raise
    out = Path(a.out)
    res.save(out)
    print(json.dumps(res.summary(), indent=1, default=str))
    if res.method == "harness2":   # test-time method: its evaluation is the post-hoc grade of the deliverables
        rep = res.meta.get("report")
        if rep:
            (out / "report.json").write_text(json.dumps(rep, indent=1, default=float))
            print(f"stream accuracy: H0 base rollout {rep['base'][0]:.3f}  submitted {rep['submitted'][0]:.3f}  "
                  f"(paired diff {rep['paired']['mean_diff']:+.3f} [{rep['paired']['lo']:+.3f}, "
                  f"{rep['paired']['hi']:+.3f}], n={rep['n']})")
    elif not a.no_transfer:
        rep = transfer_report(dom, llm_task, {"seed": res.baseline, a.method: res.best}, k=a.k)
        (out / "transfer.json").write_text(json.dumps(rep, indent=1, default=float))
        for split, row in rep["splits"].items():
            print(f"{split:8s} " + "  ".join(f"{n}={v['S']:.3f}" for n, v in row.items()))
    if (out / "trace.jsonl").exists():
        print("trace:", inspect(out))
    return 0


def cmd_inspect(a) -> int:
    print(inspect(a.run_dir, a.out, show_prompts=not a.no_prompts))
    return 0


def cmd_experiments(a) -> int:
    for k in [a.method] if a.method else sorted(set(EXP_DIRS.values())):
        d = EXPERIMENTS / EXP_DIRS.get(k, k)
        names = sorted(p.stem for p in d.glob("*.py") if not p.stem.startswith("_"))
        print(f"{d.name}: {' '.join(names)}")
    return 0


def cmd_experiment(a) -> int:
    script = EXPERIMENTS / EXP_DIRS.get(a.method, a.method) / f"{a.name}.py"
    if not script.exists():
        raise SystemExit(f"no such experiment {script}")
    return subprocess.call([sys.executable, str(script), *a.rest], cwd=str(REPO))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rsi", description="Recursive self-improvement toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("methods").set_defaults(fn=cmd_methods)

    r = sub.add_parser("recommend")
    r.add_argument("--what", default="harness", help="harness|prompts|script|strategy|agent")
    r.add_argument("--no-transfer", action="store_true", help="gains need not carry over to unseen tasks")
    r.add_argument("--cost", action="store_true", help="tokens/cost are the bottleneck")
    r.add_argument("--many-agents", action="store_true")
    r.add_argument("--search", action="store_true", help="long many-attempt searches")
    r.add_argument("--script", action="store_true", help="one editable script + one metric")
    r.add_argument("--prompts-only", action="store_true")
    r.add_argument("--no-grader", action="store_true", help="open-ended tasks: no grader, labels or score")
    r.set_defaults(fn=cmd_recommend)

    i = sub.add_parser("improve")
    i.add_argument("--method", default="rrsi")
    i.add_argument("--problem", default="agentqa")
    i.add_argument("--llm", default="sim", help="task model: sim (offline) | claude:haiku | api:sonnet | ...")
    i.add_argument("--propose-llm", default=None, help="proposer model (default: same as --llm; 'offline' = mocks)")
    i.add_argument("--set", nargs="*", default=[], help="Config overrides key=value (JSON values)")
    i.add_argument("--out", required=True)
    i.add_argument("--seed", type=int, default=0)
    i.add_argument("--k", type=int, default=1, help="trials per task in the transfer report")
    i.add_argument("--cache", default=None, help="LLM response cache dir")
    i.add_argument("--no-transfer", action="store_true")
    i.set_defaults(fn=cmd_improve)

    n = sub.add_parser("inspect")
    n.add_argument("run_dir")
    n.add_argument("--out", default=None)
    n.add_argument("--no-prompts", action="store_true")
    n.set_defaults(fn=cmd_inspect)

    e = sub.add_parser("experiments")
    e.add_argument("method", nargs="?")
    e.set_defaults(fn=cmd_experiments)

    x = sub.add_parser("experiment")
    x.add_argument("method")
    x.add_argument("name")
    x.add_argument("rest", nargs=argparse.REMAINDER)
    x.set_defaults(fn=cmd_experiment)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
