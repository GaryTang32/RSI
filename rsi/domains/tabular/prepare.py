"""prepare.py - LOCKED. Data and the evaluation protocol for the tabular
autoresearch task (the "non-LLM adaptation", modelled on szilard/xgboost-autoresearch).

Nobody edits this file. It holds

* a seeded synthetic binary-classification world ("will the shipment be late?")
  with numeric, categorical and cyclic-time features, interactions and an
  ``era`` that drifts: train = 20k rows of era 0; hidden test_iid = era 0;
  hidden test_shift = era 1 (a time-shifted holdout, like the 2006 audit set);
* ``cross_val_auc`` - the locked protocol the loop sees: 5-fold stratified CV
  (shuffle, random_state=42) ROC AUC on train;
* ``check_per_row`` - the mechanical version of the xgboost port's rule that
  ``featurize`` must compute each row's features from that row alone
  (plus lookups fitted on train);
* ``finish`` - prints the summary block and, in hardened/audit mode, writes the
  framework-owned result record (audit mode also scores the hidden sets).

Standalone (numpy + scikit-learn only): it is copied next to ``train.py``.
Columns are passed around as a dict ``{name: np.ndarray}`` (a "frame").
"""
from __future__ import annotations

import json
import os
import resource
import time

import numpy as np

# ---------------------------------------------------------------------------
# Constants (fixed, do not modify)
# ---------------------------------------------------------------------------
N_TRAIN = 20000
N_TEST = 50000
CV_FOLDS = 5
CV_SEED = 42 + int(os.environ.get("RSI_AR_VAL_EPOCH", "0"))   # fixed folds; a new epoch re-samples them (ablation)
MODE = os.environ.get("RSI_AR_MODE", "faithful")        # faithful | hardened | audit
RUN_SEED = int(os.environ.get("RSI_AR_RUN_SEED", "0"))
DATA_DIR = os.environ.get("RSI_AR_DATA", os.path.join(os.path.expanduser("~"), ".cache", "rsi_tabular", "all"))
RESULT_FILE = os.environ.get("RSI_AR_RESULT", "")
TARGET = "late"
NUMERIC = ["distance", "load", "temp", "rain", "age", "priority_score", "x7"]
CATEGORICAL = ["origin", "carrier", "dow"]
TIME_COLS = ["hour"]
ALL_COLS = NUMERIC + CATEGORICAL + TIME_COLS
N_LEVELS = {"origin": 30, "carrier": 8, "dow": 7}
_T0 = time.time()


# ---------------------------------------------------------------------------
# The synthetic world
# ---------------------------------------------------------------------------
def _world(seed: int = 20260306) -> dict:
    r = np.random.default_rng(seed)
    return {
        "origin": r.normal(0.0, 0.6, N_LEVELS["origin"]),
        "carrier": r.normal(0.0, 0.5, N_LEVELS["carrier"]),
        "carrier_shift": r.normal(0.0, 0.5, N_LEVELS["carrier"]),
        "origin_hour": r.normal(0.0, 0.5, N_LEVELS["origin"]),
    }


def generate(n: int, era: int, seed: int) -> dict:
    """``n`` rows of era ``era``. Era 1 differs from era 0 by covariate shift
    (more rain, longer distances), a later daily peak, re-ranked carriers and a
    reversed effect of ``x7`` (a feature that helps in era 0 and misleads in era 1)."""
    w = _world()
    rng = np.random.default_rng(seed)
    hour = rng.uniform(0.0, 24.0, n)
    dow = rng.integers(0, 7, n)
    origin = np.minimum(rng.zipf(1.6, n) - 1, N_LEVELS["origin"] - 1)
    carrier = rng.integers(0, N_LEVELS["carrier"], n)
    distance = rng.lognormal(6.0 + 0.3 * era, 0.6, n)
    load = rng.beta(2.0, 2.0, n)
    temp = rng.normal(15.0 - 3.0 * era, 8.0, n)
    rain = rng.exponential(1.0 + 0.8 * era, n)
    age = rng.gamma(2.0, 5.0, n)
    priority_score = rng.normal(0.0, 1.0, n)
    x7 = rng.normal(0.0, 1.0, n)
    peak = 17.0 + 3.0 * era
    carrier_eff = w["carrier"] + era * w["carrier_shift"]
    logit = (
        -1.1
        + 0.9 * np.cos(2 * np.pi * (hour - peak) / 24.0)
        + w["origin"][origin] + 0.6 * w["origin_hour"][origin] * np.sin(2 * np.pi * hour / 24.0)
        + carrier_eff[carrier]
        + 0.35 * np.log(distance / 400.0)
        + 1.2 * (load - 0.5) * (rain > 1.5)
        + 0.5 * np.tanh(rain - 1.0)
        + 0.03 * np.abs(temp - 12.0)
        + 0.25 * (dow >= 5)
        - 0.02 * age
        + 0.4 * priority_score * (load > 0.6)
        + (0.45 - 0.9 * era) * x7
    )
    p = 1.0 / (1.0 + np.exp(-logit))
    late = (rng.random(n) < p).astype(np.int64)
    return {"hour": hour, "dow": dow, "origin": origin, "carrier": carrier, "distance": distance, "load": load,
            "temp": temp, "rain": rain, "age": age, "priority_score": priority_score, "x7": x7, TARGET: late}


def build_data(root: str) -> dict:
    """Write ``<root>/all/{train,test_iid,test_shift}.npz`` and ``<root>/visible/train.npz`` (idempotent)."""
    man = os.path.join(root, "manifest.json")
    if os.path.exists(man):
        with open(man) as f:
            return json.load(f)
    specs = {"train": (N_TRAIN, 0, 1), "test_iid": (N_TEST, 0, 2), "test_shift": (N_TEST, 1, 3)}
    out = {}
    for d in ("all", "visible"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for name, (n, era, seed) in specs.items():
        frame = generate(n, era, seed)
        np.savez(os.path.join(root, "all", f"{name}.npz"), **frame)
        if name == "train":
            np.savez(os.path.join(root, "visible", f"{name}.npz"), **frame)
        out[name] = {"rows": n, "era": era, "positive_rate": float(frame[TARGET].mean())}
    tmp = man + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f, indent=1)
    os.replace(tmp, man)
    return out


def load_frame(name: str = "train") -> dict:
    with np.load(os.path.join(DATA_DIR, f"{name}.npz")) as z:
        return {k: z[k] for k in z.files}


def load_train() -> dict:
    return load_frame("train")


def subset(frame: dict, idx) -> dict:
    return {k: v[idx] for k, v in frame.items()}


# ---------------------------------------------------------------------------
# Locked evaluation protocol
# ---------------------------------------------------------------------------
def _auc(y, s) -> float:
    from sklearn.metrics import roc_auc_score

    return float(roc_auc_score(y, s))


def check_per_row(featurize, frame: dict, seed: int = 0) -> bool:
    """The xgboost port's quick test, mechanized: featurizing a random half of the
    frame must give the same rows as featurizing the whole frame."""
    n = len(frame[TARGET])
    idx = np.sort(np.random.default_rng(seed).choice(n, n // 2, replace=False))
    full = np.asarray(featurize(frame), dtype=np.float64)[idx]
    half = np.asarray(featurize(subset(frame, idx)), dtype=np.float64)
    return full.shape == half.shape and bool(np.allclose(full, half, equal_nan=True))


_RECORD: dict = {}


def cross_val_auc(make_model, featurize, frame: dict) -> tuple[float, float]:
    """Stratified 5-fold CV ROC AUC of ``make_model()`` on ``featurize(frame)``.
    As in the xgboost port, features are built once on the full frame before the
    split, so CV cannot see frame-dependent features (hardened mode checks them)."""
    from sklearn.model_selection import StratifiedKFold

    y = frame[TARGET]
    X = np.asarray(featurize(frame), dtype=np.float64)
    cv = StratifiedKFold(n_splits=CV_FOLDS, shuffle=True, random_state=CV_SEED)
    scores = []
    for tr, te in cv.split(X, y):
        m = make_model()
        m.fit(X[tr], y[tr])
        scores.append(_auc(y[te], m.predict_proba(X[te])[:, 1]))
    mean, std = float(np.mean(scores)), float(np.std(scores))
    if MODE in ("hardened", "audit"):
        _RECORD.update({"cv_auc": mean, "cv_std": std, "folds": scores,
                        "per_row_ok": check_per_row(featurize, frame), "n_features": int(X.shape[1])})
    return mean, std


def peak_mem_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def finish(model, featurize, cv_auc: float, cv_std: float = 0.0) -> None:
    """Print the summary block; in hardened/audit mode write the framework-owned record."""
    print("---")
    print(f"cv_auc:           {cv_auc:.6f}")
    print(f"cv_std:           {cv_std:.6f}")
    print(f"total_seconds:    {time.time() - _T0:.1f}")
    print(f"peak_mem_mb:      {peak_mem_mb():.1f}")
    if MODE not in ("hardened", "audit"):
        return
    rec = dict(_RECORD)
    rec["total_seconds"] = time.time() - _T0
    rec["peak_mem_mb"] = peak_mem_mb()
    if MODE == "audit":
        rec["audit"] = {}
        for split in ("test_iid", "test_shift"):
            if os.path.exists(os.path.join(DATA_DIR, f"{split}.npz")):
                fr = load_frame(split)
                rec["audit"][split] = _auc(fr[TARGET], model.predict_proba(np.asarray(featurize(fr), float))[:, 1])
    if RESULT_FILE:
        tmp = RESULT_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, RESULT_FILE)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Build the tabular data (one-time, offline, deterministic)")
    ap.add_argument("--root", default=os.path.join(os.path.expanduser("~"), ".cache", "rsi_tabular"))
    a = ap.parse_args()
    print(json.dumps(build_data(a.root), indent=1))
