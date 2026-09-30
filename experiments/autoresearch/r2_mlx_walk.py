"""R2-X3 - the MLX port's first night on a compute-starved CPU analogue (claims O16b; O16a, O17 secondary).

Claim [doc:110]: the Apple Silicon port's first night went 2.667 -> 1.808 by "halving the batch
size, raising the learning rate and cutting model depth from 8 to 4" [code:mlx/results.tsv:
2.667 -> 2.589 (halve total batch to 2^16) -> 2.534 (matrix LR 0.04) -> 1.808 (depth 8 -> 4)].

The first round's analogue started from DEPTH 2 and cut to DEPTH 1 at a fixed width; the depth cut
lost. Two differences from the source: the source starts from depth 8, and in upstream and MLX
``model_dim = DEPTH * ASPECT_RATIO`` (``ar/train.py:470``, ``mlx/train.py:401``), so "reduce depth from
8 to 4" also halves the width. This analogue (preregistered, claims-audit.md section 5, X3) starts
from DEPTH 8 with HIDDEN = 16 * DEPTH = 128 and applies the three moves in the source's order:

  M1 BATCH_SIZE 32 -> 16;  M2 LR 0.003 -> 0.006;  M3 DEPTH 8 -> 4 with HIDDEN 128 -> 64.

Secondary: M3' (DEPTH 8 -> 4 at HIDDEN 128), the auditor's DEPTH-2 chain re-run with 5 seeds, and
the new chain at 2 s and 24 s (a 4x slower and a 3x faster "machine", claim O17).

Every config of a run seed runs back to back (paired design); run seeds 0-4; hardened mode.

Usage: python experiments/autoresearch/r2_mlx_walk.py [--seeds 5] [--quick]
"""
from __future__ import annotations

from _common import ci, write  # noqa: I001

import argparse
import json
import math
import re

import numpy as np

from rsi.core.artifact import Artifact
from rsi.domains.tinylm import TinyLMTask


def setk(text: str, k: str, v) -> str:
    out, n = re.subn(rf"^{k}(\s*=\s*)[^#\n]+?(\s*(#.*)?)$", lambda m: f"{k}{m.group(1)}{v!r}{m.group(2)}", text,
                     count=1, flags=re.M)
    assert n == 1, k
    return out


def chains(seed_train: str) -> dict:
    new = {}
    t = setk(setk(seed_train, "DEPTH", 8), "HIDDEN", 128)
    new["base_d8"] = t
    t = setk(t, "BATCH_SIZE", 16); new["m1_batch16"] = t
    t = setk(t, "LR", 0.006); new["m2_lr0.006"] = t
    new["m3_depth4_width64"] = setk(setk(t, "DEPTH", 4), "HIDDEN", 64)
    new["m3alt_depth4_width128"] = setk(t, "DEPTH", 4)
    old = {}
    t = setk(seed_train, "DEPTH", 2)
    old["old_base_d2"] = t
    t = setk(t, "BATCH_SIZE", 16); old["old_m1_batch16"] = t
    t = setk(t, "LR", 0.006); old["old_m2_lr0.006"] = t
    old["old_m3_depth1"] = setk(t, "DEPTH", 1)
    return {"new": new, "old": old}


MOVES = [("M1 halve batch", "base_d8", "m1_batch16"), ("M2 LR x2", "m1_batch16", "m2_lr0.006"),
         ("M3 depth 8->4 (width follows)", "m2_lr0.006", "m3_depth4_width64"),
         ("M3' depth 8->4 (width kept)", "m2_lr0.006", "m3alt_depth4_width128")]
OLD_MOVES = [("M1 halve batch", "old_base_d2", "old_m1_batch16"), ("M2 LR x2", "old_m1_batch16", "old_m2_lr0.006"),
             ("M3 depth 2->1", "old_m2_lr0.006", "old_m3_depth1")]


def paired_test(before: list, after: list) -> dict:
    g = np.asarray(before) - np.asarray(after)
    n = len(g)
    m, sd = float(g.mean()), float(g.std(ddof=1)) if n > 1 else float("nan")
    t = m / (sd / math.sqrt(n)) if sd and sd > 0 else float("inf") * np.sign(m)
    from scipy import stats

    p = float(stats.t.sf(t, n - 1)) if math.isfinite(t) else (0.0 if t > 0 else 1.0)
    return {"gains": g.tolist(), "mean_gain": m, "sd": sd, "t": float(t), "p_one_sided": p,
            "pass": bool(m > 0 and p < 0.05), "ci": ci(g.tolist())}


def old_chain_budgets(budgets, seeds) -> None:
    """X3c (claims-audit.md section 5): the 25 Sep DEPTH-2 chain at other budgets, 5 paired seeds."""
    base_files = dict(TinyLMTask(budget_s=8.0).seed_artifact().files)
    old = chains(base_files["train.py"])["old"]
    out = {"config": {"seeds": seeds, "budgets": budgets, "chain": "DEPTH 2 (25 Sep auditor design)"}, "runs": {}}
    for budget in budgets:
        task = TinyLMTask(budget_s=budget)
        task.prepare()
        vals = {k: [] for k in old}
        for s in seeds:
            for name, src in old.items():
                o = task.run(Artifact({**base_files, "train.py": src}), seed=s, mode="hardened")
                vals[name].append(o.metric if o.metric is not None else float("nan"))
                print(f"{budget:g}s seed {s} {name}: {vals[name][-1]:.4f}", flush=True)
        out["runs"][f"{budget:g}s"] = {"vals": vals, "mean": {k: float(np.nanmean(v)) for k, v in vals.items()},
                                       "old_moves": {m: paired_test(vals[b], vals[c]) for m, b, c in OLD_MOVES}}
    write("r2_mlx_walk_old_chain_budgets", out)
    print(json.dumps({b: {m: (round(t["mean_gain"], 4), round(t["p_one_sided"], 4), t["pass"])
                          for m, t in r["old_moves"].items()} for b, r in out["runs"].items()}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--old-budgets", type=str, default="",
                    help="X3c: run only the DEPTH-2 chain at these budgets (comma-separated seconds)")
    a = ap.parse_args()
    if a.old_budgets:
        return old_chain_budgets([float(x) for x in a.old_budgets.split(",")], list(range(a.seeds)))
    seeds = list(range(2 if a.quick else a.seeds))
    budgets = (8.0,) if a.quick else (8.0, 2.0, 24.0)
    seed_train = TinyLMTask(budget_s=8.0).seed_artifact()
    base_files = dict(seed_train.files)
    ch = chains(base_files["train.py"])
    out = {"config": {"seeds": seeds, "budgets": budgets, "mode": "hardened", "task": "tinylm",
                      "aspect": "HIDDEN = 16 x DEPTH (upstream/MLX: model_dim = DEPTH x ASPECT_RATIO)",
                      "configs": {k: {kk: re.search(rf"^{kk}\s*=\s*([^#\n]+)", v, re.M).group(1).strip()
                                      for kk in ("DEPTH", "HIDDEN", "BATCH_SIZE", "LR")}
                                  for grp in ch.values() for k, v in grp.items()}},
           "runs": {}}
    for budget in budgets:
        task = TinyLMTask(budget_s=budget)
        task.prepare()
        cfgs = dict(ch["new"])
        if budget == 8.0:
            cfgs.update(ch["old"])
        vals = {k: [] for k in cfgs}
        extra = {k: [] for k in cfgs}
        for s in seeds:
            for name, src in cfgs.items():
                o = task.run(Artifact({**base_files, "train.py": src}), seed=s, mode="hardened")
                vals[name].append(o.metric if o.metric is not None else float("nan"))
                extra[name].append({"steps": (o.record or {}).get("num_steps"), "tokens": (o.record or {}).get("tokens"),
                                    "crash": o.crash_reason, "mem_gb": o.memory_gb})
                print(f"{budget:g}s seed {s} {name}: {vals[name][-1]:.4f} steps={extra[name][-1]['steps']}", flush=True)
        res = {"vals": vals, "extra": extra, "mean": {k: float(np.nanmean(v)) for k, v in vals.items()},
               "moves": {m: paired_test(vals[b], vals[c]) for m, b, c in MOVES}}
        if budget == 8.0:
            res["old_moves"] = {m: paired_test(vals[b], vals[c]) for m, b, c in OLD_MOVES}
        res["walk_ratio_final_over_base"] = res["mean"]["m3_depth4_width64"] / res["mean"]["base_d8"]
        out["runs"][f"{budget:g}s"] = res
    prim = out["runs"]["8s"]["moves"]
    n_pass = sum(prim[m]["pass"] for m, _, _ in MOVES[:3])
    verdict = {"primary_8s_moves_passing": n_pass,
               "primary_8s": {m: {"mean_gain": prim[m]["mean_gain"], "p": prim[m]["p_one_sided"], "pass": prim[m]["pass"]}
                              for m, _, _ in MOVES},
               "o16b": f"PARTIAL ({n_pass} of 3 moves pass on the CPU analogue; the claim is an MLX night on a Mac)",
               "o16b_note": ("the DEPTH-8 baseline is compute-starved to about the byte-unigram level (val window 0 "
                             "unigram 4.70 bpb vs base_d8 4.80 / 4.71 / 4.04 at 2 / 8 / 24 s), so the batch and LR "
                             "moves (M1, M2) are not informative in this analogue")}
    if "old_moves" in out["runs"]["8s"]:
        verdict["old_design_8s"] = {m: {"mean_gain": v["mean_gain"], "p": v["p_one_sided"], "pass": v["pass"]}
                                    for m, v in out["runs"]["8s"]["old_moves"].items()}
    if len(budgets) == 3:
        tr = {}
        for m, _, _ in MOVES[:3]:
            tr[m] = {b: {"mean_gain": out["runs"][b]["moves"][m]["mean_gain"], "pass": out["runs"][b]["moves"][m]["pass"]}
                     for b in ("2s", "8s", "24s")}
        verdict["transfer_across_budgets"] = tr
        verdict["o17_flag_for_review"] = all(
            tr[m]["2s"]["pass"] == tr[m]["8s"]["pass"] == tr[m]["24s"]["pass"] and
            np.sign(tr[m]["2s"]["mean_gain"]) == np.sign(tr[m]["24s"]["mean_gain"]) for m in tr)
    out["verdict"] = verdict
    write("r2_mlx_walk" + ("_quick" if a.quick else ""), out)
    print(json.dumps(verdict, indent=1))


if __name__ == "__main__":
    main()
