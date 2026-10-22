"""Shared plumbing for the EvoMap experiments: CLI, worlds, populations, stats, printing and JSON output."""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from multiprocessing import Pool
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core import CachedLLM, ClaudeCLI, paired_diff_ci, summarize_runs  # noqa: E402
from rsi.evomap import (AgentSpec, Config, Gene, NaiveEvoMapHub, PopulationSimulator, RunContext, SafeHub,  # noqa: E402
                        TaskBank, TaskSignalExtractor, render_gene)
from rsi.evomap.signals import pattern_hits  # noqa: E402

RESULTS = ROOT / "results" / "evomap"
# a fresh EVOMAP_CACHE_DIR keeps a live re-run after a behaviour change from replaying old replies
CACHE = Path(os.environ["EVOMAP_CACHE_DIR"]) if os.environ.get("EVOMAP_CACHE_DIR") else ROOT / ".rsi_cache" / "evomap"
FAITHFUL_HINT = ' (keep it LIGHT: prefer "python --version" as the distiller prompt advises)'
SAFE_HINT = " (it MUST fail before the fix and pass after it, e.g. the public check script)"
KATA_SAFE_HINT = SAFE_HINT + (". The workspace contains only solution.py and smoke_test.py. Allowed "
                              "commands: `python <existing_script>.py` or `pytest -q <existing_file>.py`; inline code "
                              "(python -c) and shell syntax are BLOCKED by the command policy")
NAN = float("nan")


def parse_args(desc: str, default_seeds: int = 20, extra: Optional[Callable] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--llm", default="sim", help="sim (GeneWorld / mocks) | claude:<model> (live katas, cached)")
    ap.add_argument("--seeds", type=int, default=None, help=f"number of seeds (default {default_seeds}; quick: 3)")
    ap.add_argument("--quick", action="store_true", help="few seeds / epochs for a fast check")
    ap.add_argument("--workers", type=int, default=3, help="parallel processes (seeds)")
    ap.add_argument("--out", default=None, help="output JSON path")
    if extra:
        extra(ap)
    a = ap.parse_args()
    if a.seeds is None:
        a.seeds = 3 if a.quick else (1 if live(a) else default_seeds)
    if live(a):
        a.workers = 1
    return a


def live(args) -> bool:
    return args.llm != "sim"


def live_stand_in():
    """``--llm mock-live``: the live (katas) code paths offline, with the kata simulator and the mock gene writer."""
    from rsi.core import MockLLM
    from rsi.domains.katas import KataMockProposer, KataSimSolver
    solver, writer = KataSimSolver(0.0), KataMockProposer(0.7)

    def respond(prompt, system, seed, i):
        target = writer if "STRATEGY GENE" in prompt or "Gene synthesis" in (system or "") else solver
        text = target.complete(prompt, system=system, seed=seed).text
        if target is solver and '"type": "EvolutionEvent"' in (system or ""):   # X15: an over-confident executor
            text += '\n{"type": "EvolutionEvent", "outcome": {"status": "success", "score": 0.9}}'
        return text
    return MockLLM(respond, name="mock-live")


def cached_llm(spec: str):
    if spec == "mock-live":
        return live_stand_in()
    kind, _, model = spec.partition(":")
    if kind != "claude":
        raise SystemExit(f"unsupported --llm {spec!r}")
    CACHE.mkdir(parents=True, exist_ok=True)
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=240), CACHE)


@dataclass
class World:
    domain: object
    harness: object
    model_factory: Callable
    proposer_factory: Callable
    forge: object
    truth: Optional[Callable]
    reference_llm: object


def geneworld_world(seed: int = 0, p_real_validation: float = 0.3) -> World:
    """GeneWorld with simulated models and proposers; ``truth`` is a gene's true uplift on the test tasks it matches."""
    from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, WorldConfig, make_domain
    from rsi.domains.geneworld.forge import GeneWorldForge
    dom = make_domain(WorldConfig(seed=seed))
    w = dom.world
    bank = dom.tasks.split("test", allow_sealed=True)
    ext = TaskSignalExtractor()
    sig = {t.id: ext.extract(RunContext(task=t)) for t in bank}
    memo: dict = {}

    def truth(g: Gene) -> float:
        key = (tuple(g.signals_match), render_gene(g))
        if key not in memo:
            ins = [t for t in bank if pattern_hits(g.signals_match, sig[t.id]) > 0]
            memo[key] = w.true_uplift(key[1], ins, 0.0) if ins else 0.0
        return memo[key]

    return World(dom, dom.seed_artifact(), lambda sp: GeneWorldModel(sp.ability, sp.name),
                 lambda sp: GeneWorldProposer(w, sp.insight, p_real_validation, sp.name + "-proposer"),
                 GeneWorldForge(w), truth, GeneWorldModel(0.0, "reference"))


def katas_world(args) -> World:
    """Katas with one cached live model as solver and gene writer; no ground truth."""
    from rsi.domains.katas import KatasDomain, seed_harness
    from rsi.domains.katas.forge import KatasForge
    dom = KatasDomain(scheme="hub")
    llm = cached_llm(args.llm)
    return World(dom, seed_harness(), lambda sp: llm, lambda sp: llm, KatasForge(dom), None, llm)


def make_world(args, seed: int = 0, **kw) -> World:
    return katas_world(args) if live(args) else geneworld_world(seed, **kw)


DEFAULT_MIX = {"honest": 0.60, "farmer": 0.15, "inflator": 0.10, "freerider": 0.10, "poisoner": 0.05}


def population_specs(n: int, mix: dict, seed: int, *, farm_rate: int = 8, poison_rate: int = 2,
                     classes: Optional[list] = None) -> list[AgentSpec]:
    """``n`` agents of the given kind mix; each works on 4 random task families."""
    rng = random.Random(f"specs-{seed}")
    kinds = [k for k, f in mix.items() for _ in range(int(round(f * n)))]
    kinds = (kinds + ["honest"] * n)[:n]
    rng.shuffle(kinds)
    out = []
    for i, k in enumerate(kinds):
        fams = rng.sample(classes, min(4, len(classes))) if classes else None
        out.append(AgentSpec(f"a{i:02d}_{k}", k, ability=rng.gauss(0.0, 0.7), insight=rng.random(),
                             farm_rate=farm_rate if k == "farmer" else poison_rate, classes=fams))
    return out


def make_hub(kind: str, world: World, *, eps: float = 0.2, seed: int = 0):
    if kind == "none":
        return None
    if kind == "naive":
        return NaiveEvoMapHub()
    bank = TaskBank(world.domain, world.harness, world.reference_llm, split="test", n=24, n_off=12, k=4)
    return SafeHub(bank, eps=eps, seed=seed)


def agent_config(regime: str, seed: int, **kw) -> Config:
    """naive: faithful Evolver agents ('prefer --version'); safe: quarantine, local re-test, discriminative checks."""
    if regime == "naive":
        return Config(mode="faithful", seed=seed, validation_hint=FAITHFUL_HINT, **kw)
    return Config(mode="safe", seed=seed, validation_hint=SAFE_HINT, **kw)


def simulator(world: World, hub, specs: list[AgentSpec], config: Config, seed: int, **kw) -> PopulationSimulator:
    return PopulationSimulator(world.domain, world.harness, hub, specs, config=config,
                               model_factory=world.model_factory, proposer_factory=world.proposer_factory,
                               forge=world.forge, truth=world.truth, seed=seed, **kw)


def summarize(values) -> dict:
    vals = [float(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return summarize_runs(vals) if vals else {"mean": NAN, "lo": NAN, "hi": NAN, "n": 0}


def mean_or_nan(xs) -> float:
    return float(np.mean(xs)) if xs else NAN


def paired(a, b) -> dict:
    pairs = [(x, y) for x, y in zip(a, b) if not (math.isnan(x) or math.isnan(y))]
    if len(pairs) < 2:
        return {"mean_diff": NAN, "lo": NAN, "hi": NAN, "n": len(pairs)}
    return paired_diff_ci([p[0] for p in pairs], [p[1] for p in pairs])


def by_arm(rows: list[dict], arms) -> dict[str, list[dict]]:
    return {a: [r for r in rows if r["arm"] == a] for a in arms}


def summarize_groups(groups: dict, metrics) -> dict:
    return {g: {m: summarize([r[m] for r in rs]) for m in metrics} for g, rs in groups.items()}


def summarize_columns(rows: list[dict]) -> dict:
    """Per-column summary over seeds, skipping ``seed`` and the categorical ``*_largest`` columns."""
    return {k: summarize([r[k] for r in rows]) for k in rows[0] if k != "seed" and not k.endswith("_largest")}


def _clean(o):
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.floating):
        return _clean(float(o))
    if isinstance(o, np.integer):
        return int(o)
    return o


def save(name: str, payload: dict, out: Optional[str] = None) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = Path(out) if out else RESULTS / f"{name}.json"
    payload = {"experiment": name, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), **payload}
    p.write_text(json.dumps(_clean(payload), indent=1, default=str))
    print(f"[saved] {p}")
    return p


def save_figure(name: str, draw: Callable) -> None:
    """``draw(plt)`` returns a figure, saved as results/evomap/<name>.png; plotting errors are reported, not raised."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig = draw(plt)
        fig.tight_layout()
        RESULTS.mkdir(parents=True, exist_ok=True)
        fig.savefig(RESULTS / f"{name}.png", dpi=120)
    except Exception as e:  # noqa: BLE001
        print("figure skipped:", e)


def pmap(fn: Callable, items: list, workers: int) -> list:
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with Pool(min(workers, len(items))) as pool:
        return pool.map(fn, items)


def fmt(s: dict, digits: int = 3) -> str:
    if s.get("n", 0) == 0 or s.get("mean") is None or (isinstance(s.get("mean"), float) and math.isnan(s["mean"])):
        return "n/a"
    return f"{s['mean']:.{digits}f} [{s['lo']:.{digits}f}, {s['hi']:.{digits}f}]"


def print_stats(summ: dict, width: int, keys=None) -> None:
    for k in keys or summ:
        print(f"  {k:{width}s} {fmt(summ[k])}")


def print_groups(summ: dict, metrics, width: int) -> None:
    for g, s in summ.items():
        print(f"== {g}")
        print_stats(s, width, metrics)
