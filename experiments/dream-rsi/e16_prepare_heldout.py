"""Build the Lasso held-out datasets of the paper's Fig. 3a that can be obtained here (claims audit, retry round 2).

The paper evaluates on six LIBSVM datasets (Gisette, RCV1, DNA, Leukemia, Colon, Duke Breast), fetched by the
benchmark it follows with ``libsvmdata`` (``gisette`` / ``rcv1.binary`` with an 85% split, ``dna`` 85%,
``leukemia`` / ``colon-cancer`` / ``duke breast-cancer`` whole). The LIBSVM host is blocked from this
machine, so:

* **dna**, **leukemia**: the benchmark ships these two arrays itself (``eval_data/real_dna.npz`` 1700 x 180,
  ``real_leukemia.npz`` 38 x 7129): copied as they are.
* **colon**: Alon et al. (1999), 62 x 2000, from the ``datamicroarray`` R package (``alon.RData``) with LIBSVM's
  preprocessing, which this script first *verifies* on Leukemia: instance-wise standardisation (population std),
  then feature-wise standardisation (sample std, ddof = 1) over all samples. Reproducing the benchmark's
  Leukemia array from Golub et al.'s 72 samples this way matches it to the precision of LIBSVM's text format (max mean squared difference per row ~6e-12), so the
  same recipe on Alon's data should give LIBSVM's ``colon-cancer``.
* **duke** (approximation): West et al. (2001), 49 x 7129 (``west.RData``) with the same recipe. LIBSVM's
  ``duke breast-cancer`` has 44 of these samples; which 5 were dropped is unknown, so this is a Duke-like
  dataset of the same width, not the paper's exact data.
* **gisette**, **rcv1**: not available (not written; ``SimpleTESLassoDomain`` lists them as missing).

Labels are the class labels coded -1 / +1 (the benchmark regresses the LIBSVM labels). Output: one
``<name>.npz`` (arrays ``X``, ``y``) per dataset and ``manifest.json`` with shapes, sources and sha256.

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
        sys.path.append(a.pylib)          # appended: never shadows the environment's numpy
    import warnings

    warnings.filterwarnings("ignore")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ev = Path(a.simpletes) / "datasets" / "numerical_tasks" / "lasso_path" / "eval_data"
    dma = Path(a.datamicroarray) / "data"
    manifest = {}
    for name, f in (("dna", "real_dna.npz"), ("leukemia", "real_leukemia.npz")):
        d = np.load(ev / f)
        np.savez(out / f"{name}.npz", X=d["X"], y=d["y"])
        manifest[name] = {"shape": list(d["X"].shape), "source": f"SimpleTES eval_data/{f} (the benchmark's own file)",
                          "exact": True}
    # verify the LIBSVM recipe on Leukemia before trusting it for Colon / Duke
    Xg, _ = load_rdata(dma / "golub.RData", "golub")
    L = np.load(ev / "real_leukemia.npz")["X"]
    G = libsvm_recipe(Xg)
    dist = ((L[:, None, :] - G[None, :, :]) ** 2).mean(2)
    match = dist.argmin(1)
    recipe_check = {"max_min_sq_dist": float(dist.min(1).max()), "matched_rows": int(len(set(match.tolist()))),
                    "of": int(L.shape[0])}
    if not (recipe_check["max_min_sq_dist"] < 1e-8 and recipe_check["matched_rows"] == L.shape[0]):
        raise SystemExit(f"LIBSVM recipe does not reproduce the benchmark's Leukemia array: {recipe_check}")
    # colon: the recipe is verified on Leukemia but LIBSVM's colon-cancer file itself cannot be compared here
    for name, rname, pos, exact in (("colon", "alon", "t", "expected (recipe verified on Leukemia; file not compared)"),
                                    ("duke", "west", "positive", False)):
        X, y = load_rdata(dma / f"{rname}.RData", rname)
        Xn = libsvm_recipe(X)
        yy = np.where(y == pos, 1.0, -1.0)
        np.savez(out / f"{name}.npz", X=Xn, y=yy)
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
