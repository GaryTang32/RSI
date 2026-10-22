"""Build the Lasso held-out datasets of Fig. 3a obtainable here (dna, leukemia, colon, a Duke-like set) for E16.

    python experiments/dream-rsi/e16_prepare_heldout.py --simpletes <clone of wq-will/SimpleTES> \
        --datamicroarray <clone of ramhiser/datamicroarray> --out <dir> [--pylib <dir with the `rdata` package>]
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np


def rowstd(A):
    return (A - A.mean(1, keepdims=True)) / A.std(1, keepdims=True)


def libsvm_recipe(A):
    """LIBSVM's preprocessing: instance-wise (population std), then feature-wise (ddof=1) standardisation."""
    R = rowstd(A)
    return (R - R.mean(0)) / R.std(0, ddof=1)


def load_rdata(path: Path, name: str):
    import rdata

    obj = rdata.conversion.convert(rdata.parser.parse_file(str(path)))[name]
    return np.asarray(obj["x"], dtype=float), np.asarray(obj["y"]).astype(str)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simpletes", required=True)
    ap.add_argument("--datamicroarray", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pylib", default=None)
    a = ap.parse_args()
    if a.pylib:
        sys.path.append(a.pylib)  # appended: never shadows the environment's numpy
    import warnings

    warnings.filterwarnings("ignore")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ev = Path(a.simpletes) / "datasets" / "numerical_tasks" / "lasso_path" / "eval_data"
    dma = Path(a.datamicroarray) / "data"
    manifest = {}
    for name, f in (("dna", "real_dna.npz"), ("leukemia", "real_leukemia.npz")):  # shipped by the benchmark itself
        d = np.load(ev / f)
        np.savez(out / f"{name}.npz", X=d["X"], y=d["y"])
        manifest[name] = {"shape": list(d["X"].shape), "source": f"SimpleTES eval_data/{f} (the benchmark's own file)",
                          "exact": True}
    # verify the LIBSVM recipe on Leukemia (Golub et al.'s 72 samples) before trusting it for Colon / Duke
    Xg, _ = load_rdata(dma / "golub.RData", "golub")
    L = np.load(ev / "real_leukemia.npz")["X"]
    dist = ((L[:, None, :] - libsvm_recipe(Xg)[None, :, :]) ** 2).mean(2)
    recipe_check = {"max_min_sq_dist": float(dist.min(1).max()), "matched_rows": int(len(set(dist.argmin(1).tolist()))),
                    "of": int(L.shape[0])}
    if not (recipe_check["max_min_sq_dist"] < 1e-8 and recipe_check["matched_rows"] == L.shape[0]):
        raise SystemExit(f"LIBSVM recipe does not reproduce the benchmark's Leukemia array: {recipe_check}")
    # LIBSVM's colon-cancer file itself cannot be compared here; duke keeps 44 of West et al.'s 49 samples
    for name, rname, pos, exact in (("colon", "alon", "t", "expected (recipe verified on Leukemia; file not compared)"),
                                    ("duke", "west", "positive", False)):
        X, y = load_rdata(dma / f"{rname}.RData", rname)
        Xn = libsvm_recipe(X)
        np.savez(out / f"{name}.npz", X=Xn, y=np.where(y == pos, 1.0, -1.0))
        manifest[name] = {"shape": list(Xn.shape), "source": f"datamicroarray {rname}.RData + LIBSVM recipe "
                          "(row standardisation, then column standardisation with ddof=1)", "exact": exact}
        if name == "duke":
            manifest[name]["note"] = "LIBSVM's duke breast-cancer keeps 44 of West et al.'s 49 samples: approximation"
    for name in manifest:
        manifest[name]["sha256"] = sha(out / f"{name}.npz")
    manifest["_missing"] = ["gisette", "rcv1"]
    manifest["_recipe_check_on_leukemia"] = recipe_check
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
