"""R2-X7 - a backend port under the same loop and rules (claim O18; the spec's E9, retry round 2).

Usage: python experiments/autoresearch/r2_backend_port.py [--experiments 20] [--seeds 2] [--reanalyze]
"""
from _common import RESULTS, SCRATCH, ci, constants, pool_map, report, with_constants  # noqa: I001

import argparse
import json

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, MockResearchAgent
from rsi.core.artifact import Artifact
from rsi.domains.tinylm import TinyLMTask

BACKENDS = ("float64", "float32")
PORT_ANCHOR = "model = MLPLM(rng)\n"
PORT_LINE = "model.p = {k: v.astype(np.float32) for k, v in model.p.items()}   # float32 backend port\n"
NOTE = ("transfer_ratios / transferred_gain use np.nanmean and drop diverged runs; see "
        "divergence_aware (a diverged run counts as a failed transfer, zero gain)")


def port(train_py: str) -> str:
    """The float32 backend: parameters (and so activations, gradients and Adam state) cast once after build."""
    assert train_py.count(PORT_ANCHOR) == 1
    return train_py.replace(PORT_ANCHOR, PORT_ANCHOR + PORT_LINE)


def night(args) -> dict:
    backend, seed, n = args
    task = TinyLMTask(budget_s=2.0)
    files = dict(task.seed_artifact().files)
    if backend == "float32":
        files["train.py"] = port(files["train.py"])
    cfg = Config(max_experiments=n, seed=seed, shadow_monitor=False, hidden_audit=False, plot=False, overwrite=True,
                 tag=f"r2-port-{backend}-{seed}")
    res = AutoresearchLoop(task, MockResearchAgent(task.mock_edit_pool(), seed=seed), cfg,
                           out_dir=SCRATCH / "r2_backend_port" / f"{backend}_{seed}",
                           seed_artifact=Artifact(files)).run()
    a = res.meta["analysis"]
    return {"backend": backend, "seed": seed, "baseline": a["baseline"], "best": a["best"], "n_keep": a["n_keep"],
            "keeps": [d["description"] for d in a["top_hits"]], "final_constants": constants(res.best["train.py"]),
            "base_constants": constants(files["train.py"])}


def score(args) -> list[float]:
    """Hardened runs of ``backend``'s baseline with ``consts`` applied, one per run seed (NaN when no metric)."""
    backend, consts, seeds = args
    task = TinyLMTask(budget_s=2.0)
    files = dict(task.seed_artifact().files)
    t = files["train.py"] if backend == "float64" else port(files["train.py"])
    if consts:
        t = with_constants(t, consts)
    vals = [task.run(Artifact({**files, "train.py": t}), seed=s, mode="hardened").metric for s in seeds]
    return [v if v is not None else float("nan") for v in vals]


def divergence_aware(rows: list[dict]) -> dict:
    """Transfer statistics with each diverged run (NaN / no metric) scored as the base mean (zero gain)."""
    out = []
    for r in rows:
        ob, of = r["transferred"]["base"], r["transferred"]["final"]
        bm = float(np.nanmean(ob))
        failed = [v is None or v != v for v in of]
        gain = bm - float(np.mean([bm if f else v for v, f in zip(of, failed)]))
        native = r["native"]["gain"]
        out.append({"backend": r["backend"], "seed": r["seed"], "failed_transferred_runs": int(sum(failed)),
                    "n_transferred_runs": len(of),
                    "failed_native_runs": int(sum(v is None or v != v for v in r["native"]["final"])),
                    "transferred_gain_failures_as_zero": gain,
                    "transfer_ratio_failures_as_zero": gain / native if native else None,
                    "transfer_ratio_nanmean": r["transfer_ratio"], "final_LR": r["final_constants"].get("LR")})
    return {"per_night": out, "nights_with_failed_transfer_runs": sum(o["failed_transferred_runs"] > 0 for o in out),
            "failed_transferred_runs": sum(o["failed_transferred_runs"] for o in out),
            "transferred_runs": sum(o["n_transferred_runs"] for o in out)}


def reanalyze(path) -> dict:
    """Add the divergence-aware statistics to an existing result file (no re-run)."""
    d = json.loads(path.read_text())
    d["verdict"]["divergence_aware"] = divergence_aware(d["nights"])
    d["verdict"]["note"] = NOTE
    path.write_text(json.dumps(d, indent=1, default=str))
    return d["verdict"]["divergence_aware"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--experiments", type=int, default=20)
    ap.add_argument("--seeds", type=int, default=2)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--reanalyze", action="store_true", help="add divergence-aware stats to the existing result file")
    a = ap.parse_args()
    if a.reanalyze:
        print(json.dumps(reanalyze(RESULTS / "r2_backend_port.json"), indent=1, default=str))
        return
    nights = pool_map(night, [(b, s, a.experiments) for b in BACKENDS for s in range(a.seeds)], a.workers)
    rseeds = [20_000 + i for i in range(5)]
    jobs = []
    for nt in nights:  # native base, native final, other-backend base, transferred final
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

    def baseline(b: str) -> dict:
        return ci([v for r in rows if r["backend"] == b for v in r["native"]["base"]])

    def gain(key: str) -> dict:
        return {b: ci([r[key]["gain"] for r in rows if r["backend"] == b]) for b in BACKENDS}

    verdict = {"loop_code_changed": False, "baseline_float64": baseline("float64"),
               "baseline_float32": baseline("float32"), "native_gain": gain("native"),
               "transferred_gain": gain("transferred"),
               "transfer_ratios": [r["transfer_ratio"] for r in rows],
               "divergence_aware": divergence_aware(rows), "note": NOTE}
    report("r2_backend_port",
           {"config": {"experiments": a.experiments, "seeds": list(range(a.seeds)), "budget_s": 2.0,
                       "port": PORT_LINE.strip(), "rescore_seeds": rseeds}, "nights": rows, "verdict": verdict},
           default=str)


if __name__ == "__main__":
    main()
