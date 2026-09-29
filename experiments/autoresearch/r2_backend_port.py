"""R2-X7 - a backend port under the same loop and rules (claim O18; the spec's E9, retry round 2).

Claim [doc:112]: "Apple Silicon port using MLX, with the same rules and no CUDA dependency."
MLX's own program.md adds: "Run train.py once to establish YOUR baseline on this hardware. Do NOT use
baseline numbers from other platforms." and its README reports that findings "did not carry cleanly"
between machines.

CPU analogue (spec section 10, E9: "Domain A implemented twice (numpy vs torch-CPU, or float64 vs
float32)"): backend A = tinylm's train.py (numpy float64); backend B = the same file ported to float32
(parameters, activations, gradients and Adam state; one added line after the model is built). The
loop, program.md, prepare.py and the rules are unchanged. Scripted greedy agent, 2 s wall clock,
20 experiments, seeds 0 and 1 per backend. Transfer: each night's final constants are applied to the
other backend's baseline and scored with 5 fresh run seeds, next to that backend's own final.
Preregistered (descriptive) in claims-audit.md section 5, X7.

Usage: python experiments/autoresearch/r2_backend_port.py [--experiments 20] [--seeds 2]
"""
from __future__ import annotations

from _common import SCRATCH, ci, pool_map, write  # noqa: I001

import argparse
import json
import re

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, MockResearchAgent
from rsi.core.artifact import Artifact
from rsi.domains.tinylm import TinyLMTask

PORT_ANCHOR = "model = MLPLM(rng)\n"
PORT_LINE = "model.p = {k: v.astype(np.float32) for k, v in model.p.items()}   # float32 backend port\n"
CONST_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)(\s*=\s*)([^#\n]+?)(\s*(#.*)?)$", re.M)


def port(train_py: str) -> str:
    assert train_py.count(PORT_ANCHOR) == 1
    return train_py.replace(PORT_ANCHOR, PORT_ANCHOR + PORT_LINE)


def constants(src: str) -> dict:
    return {m.group(1): m.group(3).strip() for m in CONST_RE.finditer(src)}


def with_constants(src: str, consts: dict) -> str:
    def sub(m):
        k = m.group(1)
        return f"{k}{m.group(2)}{consts[k]}{m.group(4)}" if k in consts else m.group(0)

    return CONST_RE.sub(sub, src)


def night(args):
    backend, seed, n = args
    task = TinyLMTask(budget_s=2.0)
    files = dict(task.seed_artifact().files)
    if backend == "float32":
        files["train.py"] = port(files["train.py"])
    agent = MockResearchAgent(task.mock_edit_pool(), seed=seed)
    loop = AutoresearchLoop(task, agent, Config(max_experiments=n, seed=seed, shadow_monitor=False, hidden_audit=False,
                                                plot=False, overwrite=True, tag=f"r2-port-{backend}-{seed}"),
                            out_dir=SCRATCH / "r2_backend_port" / f"{backend}_{seed}", seed_artifact=Artifact(files))
    res = loop.run()
    a = res.meta["analysis"]
    return {"backend": backend, "seed": seed, "baseline": a["baseline"], "best": a["best"], "n_keep": a["n_keep"],
            "keeps": [d["description"] for d in a["top_hits"]], "final_constants": constants(res.best["train.py"]),
            "base_constants": constants(files["train.py"])}


def score(args):
    backend, consts, seeds = args
    task = TinyLMTask(budget_s=2.0)
    files = dict(task.seed_artifact().files)
    t = files["train.py"] if backend == "float64" else port(files["train.py"])
    if consts:
        t = with_constants(t, consts)
    vals = [task.run(Artifact({**files, "train.py": t}), seed=s, mode="hardened").metric for s in seeds]
    return [v if v is not None else float("nan") for v in vals]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--experiments", type=int, default=20)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    nights = pool_map(night, [(b, s, a.experiments) for b in ("float64", "float32") for s in range(a.seeds)], a.workers)
    rseeds = [20_000 + i for i in range(5)]
    jobs = []
    for nt in nights:
        other = "float32" if nt["backend"] == "float64" else "float64"
        jobs += [(nt["backend"], None, rseeds), (nt["backend"], nt["final_constants"], rseeds),
                 (other, None, rseeds), (other, nt["final_constants"], rseeds)]
    scores = pool_map(score, jobs, a.workers)
    rows = []
    for i, nt in enumerate(nights):
        nb, nf, ob, of = scores[4 * i: 4 * i + 4]
        native_gain = float(np.nanmean(nb) - np.nanmean(nf))
        transfer_gain = float(np.nanmean(ob) - np.nanmean(of))
        rows.append({**nt, "native": {"base": nb, "final": nf, "gain": native_gain},
                     "transferred": {"base": ob, "final": of, "gain": transfer_gain},
                     "transfer_ratio": transfer_gain / native_gain if native_gain else None})
    base64 = [v for r in rows if r["backend"] == "float64" for v in r["native"]["base"]]
    base32 = [v for r in rows if r["backend"] == "float32" for v in r["native"]["base"]]
    verdict = {"loop_code_changed": False,
               "baseline_float64": ci(base64), "baseline_float32": ci(base32),
               "native_gain": {b: ci([r["native"]["gain"] for r in rows if r["backend"] == b]) for b in ("float64", "float32")},
               "transferred_gain": {b: ci([r["transferred"]["gain"] for r in rows if r["backend"] == b])
                                    for b in ("float64", "float32")},
               "transfer_ratios": [r["transfer_ratio"] for r in rows]}
    write("r2_backend_port", {"config": {"experiments": a.experiments, "seeds": list(range(a.seeds)), "budget_s": 2.0,
                                         "port": PORT_LINE.strip(), "rescore_seeds": rseeds}, "nights": rows,
                              "verdict": verdict})
    print(json.dumps(verdict, indent=1, default=str))


if __name__ == "__main__":
    main()
