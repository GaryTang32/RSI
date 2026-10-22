"""R2-X3 - the MLX port's first night on a compute-starved CPU analogue (claims O16b; O16a, O17 secondary).

Usage: python experiments/autoresearch/r2_mlx_walk.py [--seeds 5] [--quick] [--old-budgets 2,24]
"""
from _common import ci, setk, write  # noqa: I001

import argparse
import json
import math
import re

import numpy as np

from rsi.core.artifact import Artifact
from rsi.domains.tinylm import TinyLMTask

# (move, before, after); M3 follows upstream/MLX, where model_dim = DEPTH * ASPECT_RATIO, so width follows depth
MOVES = [("M1 halve batch", "base_d8", "m1_batch16"), ("M2 LR x2", "m1_batch16", "m2_lr0.006"),
         ("M3 depth 8->4 (width follows)", "m2_lr0.006", "m3_depth4_width64"),
         ("M3' depth 8->4 (width kept)", "m2_lr0.006", "m3alt_depth4_width128")]
OLD_MOVES = [("M1 halve batch", "old_base_d2", "old_m1_batch16"), ("M2 LR x2", "old_m1_batch16", "old_m2_lr0.006"),
             ("M3 depth 2->1", "old_m2_lr0.006", "old_m3_depth1")]
KNOBS = ("DEPTH", "HIDDEN", "BATCH_SIZE", "LR")


def chains(seed_train: str) -> dict:
    """The preregistered DEPTH-8 chain (HIDDEN = 16 x DEPTH) and the first round's DEPTH-2 chain."""
    base = setk(setk(seed_train, "DEPTH", 8), "HIDDEN", 128)
    m1 = setk(base, "BATCH_SIZE", 16)
    m2 = setk(m1, "LR", 0.006)
    new = {"base_d8": base, "m1_batch16": m1, "m2_lr0.006": m2,
           "m3_depth4_width64": setk(setk(m2, "DEPTH", 4), "HIDDEN", 64),
           "m3alt_depth4_width128": setk(m2, "DEPTH", 4)}
    old_base = setk(seed_train, "DEPTH", 2)
    old_m1 = setk(old_base, "BATCH_SIZE", 16)
    old_m2 = setk(old_m1, "LR", 0.006)
    old = {"old_base_d2": old_base, "old_m1_batch16": old_m1, "old_m2_lr0.006": old_m2,
           "old_m3_depth1": setk(old_m2, "DEPTH", 1)}
    return {"new": new, "old": old}


def paired_test(before: list, after: list) -> dict:
    """One-sided paired t-test of the gain before - after > 0."""
    from scipy import stats

    g = np.asarray(before) - np.asarray(after)
    n = len(g)
    m, sd = float(g.mean()), float(g.std(ddof=1)) if n > 1 else float("nan")
    t = m / (sd / math.sqrt(n)) if sd and sd > 0 else float("inf") * np.sign(m)
    p = float(stats.t.sf(t, n - 1)) if math.isfinite(t) else (0.0 if t > 0 else 1.0)
    return {"gains": g.tolist(), "mean_gain": m, "sd": sd, "t": float(t), "p_one_sided": p,
            "pass": bool(m > 0 and p < 0.05), "ci": ci(g.tolist())}


def run_configs(task: TinyLMTask, base_files: dict, cfgs: dict, seeds, budget: float,
                show_steps: bool = True) -> tuple[dict, dict]:
    """Every config of a run seed back to back (paired design), hardened mode; NaN for no metric."""
    vals, extra = {k: [] for k in cfgs}, {k: [] for k in cfgs}
    for s in seeds:
        for name, src in cfgs.items():
            o = task.run(Artifact({**base_files, "train.py": src}), seed=s, mode="hardened")
            vals[name].append(o.metric if o.metric is not None else float("nan"))
            rec = o.record or {}
            extra[name].append({"steps": rec.get("num_steps"), "tokens": rec.get("tokens"), "crash": o.crash_reason,
                                "mem_gb": o.memory_gb})
            line = f"{budget:g}s seed {s} {name}: {vals[name][-1]:.4f}"
            print(f"{line} steps={extra[name][-1]['steps']}" if show_steps else line, flush=True)
    return vals, extra


def old_chain_budgets(budgets, seeds) -> None:
    """X3c: the DEPTH-2 chain at other budgets, paired seeds."""
    base_files = dict(TinyLMTask(budget_s=8.0).seed_artifact().files)
    old = chains(base_files["train.py"])["old"]
    out = {"config": {"seeds": seeds, "budgets": budgets, "chain": "DEPTH 2 (25 Sep auditor design)"}, "runs": {}}
    for budget in budgets:
        task = TinyLMTask(budget_s=budget)
        task.prepare()
        vals, _ = run_configs(task, base_files, old, seeds, budget, show_steps=False)
        out["runs"][f"{budget:g}s"] = {"vals": vals, "mean": {k: float(np.nanmean(v)) for k, v in vals.items()},
                                       "old_moves": {m: paired_test(vals[b], vals[c]) for m, b, c in OLD_MOVES}}
    write("r2_mlx_walk_old_chain_budgets", out)
    print(json.dumps({b: {m: (round(t["mean_gain"], 4), round(t["p_one_sided"], 4), t["pass"])
                          for m, t in r["old_moves"].items()} for b, r in out["runs"].items()}, indent=1))


def summary(tests: dict) -> dict:
    return {m: {"mean_gain": t["mean_gain"], "p": t["p_one_sided"], "pass": t["pass"]} for m, t in tests.items()}


def main() -> None:
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
    base_files = dict(TinyLMTask(budget_s=8.0).seed_artifact().files)
    ch = chains(base_files["train.py"])
    configs = {k: {kk: re.search(rf"^{kk}\s*=\s*([^#\n]+)", v, re.M).group(1).strip() for kk in KNOBS}
               for grp in ch.values() for k, v in grp.items()}
    out = {"config": {"seeds": seeds, "budgets": budgets, "mode": "hardened", "task": "tinylm",
                      "aspect": "HIDDEN = 16 x DEPTH (upstream/MLX: model_dim = DEPTH x ASPECT_RATIO)",
                      "configs": configs}, "runs": {}}
    for budget in budgets:
        task = TinyLMTask(budget_s=budget)
        task.prepare()
        cfgs = {**ch["new"], **(ch["old"] if budget == 8.0 else {})}
        vals, extra = run_configs(task, base_files, cfgs, seeds, budget)
        res = {"vals": vals, "extra": extra, "mean": {k: float(np.nanmean(v)) for k, v in vals.items()},
               "moves": {m: paired_test(vals[b], vals[c]) for m, b, c in MOVES}}
        if budget == 8.0:
            res["old_moves"] = {m: paired_test(vals[b], vals[c]) for m, b, c in OLD_MOVES}
        res["walk_ratio_final_over_base"] = res["mean"]["m3_depth4_width64"] / res["mean"]["base_d8"]
        out["runs"][f"{budget:g}s"] = res
    prim = out["runs"]["8s"]["moves"]
    n_pass = sum(prim[m]["pass"] for m, _, _ in MOVES[:3])
    verdict = {"primary_8s_moves_passing": n_pass,
               "primary_8s": summary({m: prim[m] for m, _, _ in MOVES}),
               "o16b": f"PARTIAL ({n_pass} of 3 moves pass on the CPU analogue; the claim is an MLX night on a Mac)",
               "o16b_note": ("the DEPTH-8 baseline is compute-starved to about the byte-unigram level (val window 0 "
                             "unigram 4.70 bpb vs base_d8 4.80 / 4.71 / 4.04 at 2 / 8 / 24 s), so the batch and LR "
                             "moves (M1, M2) are not informative in this analogue")}
    if "old_moves" in out["runs"]["8s"]:
        verdict["old_design_8s"] = summary(out["runs"]["8s"]["old_moves"])
    if len(budgets) == 3:
        tr = {m: {b: {"mean_gain": out["runs"][b]["moves"][m]["mean_gain"], "pass": out["runs"][b]["moves"][m]["pass"]}
                  for b in ("2s", "8s", "24s")} for m, _, _ in MOVES[:3]}
        verdict["transfer_across_budgets"] = tr
        verdict["o17_flag_for_review"] = all(
            tr[m]["2s"]["pass"] == tr[m]["8s"]["pass"] == tr[m]["24s"]["pass"] and
            np.sign(tr[m]["2s"]["mean_gain"]) == np.sign(tr[m]["24s"]["mean_gain"]) for m in tr)
    out["verdict"] = verdict
    write("r2_mlx_walk" + ("_quick" if a.quick else ""), out)
    print(json.dumps(verdict, indent=1))


if __name__ == "__main__":
    main()
