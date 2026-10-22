"""R2-X4 - validation reuse over hundreds of real-training decisions (claim O26; retry round 2).

Usage: python experiments/autoresearch/r2_val_reuse_tinylm.py [--seeds 8] [--experiments 300] [--workers 2] [--quick]
"""
from _common import (SCRATCH, cached_json, ci, claim, hardened_audit, insert_audit_code, pool_map,  # noqa: I001
                     scored_keeps, write)

import argparse
import json
import math

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, MockResearchAgent
from rsi.domains.tinylm import TinyLMTask
from rsi.domains.tinylm.task import DOMAIN_DIR

CHECKPOINTS = (50, 100, 150, 200, 250, 300)
OUT = SCRATCH / "r2_val_reuse"

_ALT = '''    if _CONST["MODE"] == "audit" and os.environ.get("RSI_AR_VAL_ALT_EPOCH", "") != "":
        # measurement-only (retry round 2, X4): score a disjoint window of the val split on the same weights
        _alt = int(os.environ["RSI_AR_VAL_ALT_EPOCH"])
        _d, _seq, _ev = _read_split("val"), _CONST["SEQ_LEN"], _CONST["EVAL_BYTES"]
        _n = _ev // _seq
        _need = _n * _seq + 1
        _off = (_alt * _ev) % max(1, len(_d) - _need + 1)
        _st = _off + _LIB["np.arange"](_n) * _seq
        _ch = _d[_st[:, None] + _LIB["np.arange"](_seq + 1)[None, :]]
        _tn, _tb = 0.0, 0
        for _s in range(0, _n, _CONST["HARDENED_EVAL_ROWS"]):
            _x, _y = _ch[_s:_s + _CONST["HARDENED_EVAL_ROWS"], :-1], _ch[_s:_s + _CONST["HARDENED_EVAL_ROWS"], 1:]
            _lp = _logprobs(model, _x)
            _tn += float(-_LIB["np.take_along_axis"](_lp, _y[..., None].astype(np.int64), axis=-1)[..., 0].sum())
            _tb += int(_y.size)
        record.setdefault("audit", {})["val_alt"] = _tn / (_LIB["math.log"](2) * max(_tb, 1))
        record["audit"]["val_alt_offset"] = int(_off)
'''


def variant_prepare() -> str:
    return insert_audit_code(_ALT)


class ValReuseTask(TinyLMTask):
    """tinylm selecting on val window ``sel_epoch``; audit mode also scores the other window and test_iid on the
    same weights (kept aside, never shown to the agent)."""

    def __init__(self, sel_epoch: int, budget_s: float = 2.0):
        files = {"prepare.py": variant_prepare(), "train.py": (DOMAIN_DIR / "train.py").read_text(),
                 "README.md": (DOMAIN_DIR / "README.md").read_text()}
        super().__init__(budget_s=budget_s, files=files)
        self.sel_epoch = sel_epoch
        self.env = {"RSI_AR_VAL_ALT_EPOCH": str(1 - sel_epoch)}
        self.held_out: list[dict] = []

    def run(self, artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0):
        m = "audit" if mode == "hardened" else mode
        out = super().run(artifact, seed=seed, mode=m, log_path=log_path, val_epoch=self.sel_epoch)
        if m == "audit":
            aud = hardened_audit(out)
            self.held_out.append({"artifact_id": artifact.id, "metric": out.metric, "val_alt": aud.get("val_alt"),
                                  "test_iid": aud.get("test_iid"), "val_alt_offset": aud.get("val_alt_offset")})
        return out


def night(args) -> dict:
    """One night, cached per seed so a split or interrupted series can be resumed or merged."""
    seed, n_exp, checkpoints = args
    out = cached_json(OUT / f"night_{seed}_{n_exp}.json", lambda: _night(seed, n_exp, checkpoints))
    out["checkpoints"] = {int(k): v for k, v in out["checkpoints"].items()}
    return out


def _night(seed: int, n_exp: int, checkpoints) -> dict:
    """Even seeds select on val window 0 and hold out window 1; odd seeds the reverse."""
    sel = seed % 2
    task = ValReuseTask(sel_epoch=sel)
    cfg = Config(max_experiments=n_exp, seed=seed, shadow_monitor=False, hidden_audit=False, plot=False,
                 overwrite=True, tag=f"r2-valreuse-{seed}")
    loop = AutoresearchLoop(task, MockResearchAgent(task.mock_edit_pool(), seed=seed), cfg, out_dir=OUT / f"seed{seed}")
    nodes, matched = scored_keeps(loop.run(), task.held_out)
    keeps = [{"exp": nd.round, "sel": nd.score, "alt": h and h["val_alt"], "iid": h and h["test_iid"],
              "change": nd.change} for nd, h in matched]
    base = keeps[0]
    rows = {}
    for k in checkpoints:  # O(k) = [sel(base) - sel(inc_k)] - [alt(base) - alt(inc_k)]
        inc = [kk for kk in keeps if kk["exp"] <= k][-1]
        rows[k] = {"exp_of_incumbent": inc["exp"],
                   "O_alt": (base["sel"] - inc["sel"]) - (base["alt"] - inc["alt"]),
                   "O_iid": (base["sel"] - inc["sel"]) - (base["iid"] - inc["iid"]),
                   "sel_gain": base["sel"] - inc["sel"], "alt_gain": base["alt"] - inc["alt"],
                   "iid_gain": base["iid"] - inc["iid"]}
    return {"seed": seed, "sel_window": sel, "n_turns": loop.n_rounds, "n_runs": loop.n_runs,
            "n_experiments_run": sum(1 for nd in nodes if nd.status in ("keep", "discard", "crash")),
            "n_keeps": len(keeps) - 1, "counters": dict(loop.counters), "keeps": keeps, "checkpoints": rows,
            "unmatched_keeps": sum(1 for kk in keeps if kk["alt"] is None)}


def one_sided_t(x) -> dict:
    from scipy import stats

    x = np.asarray(x, dtype=float)
    n, m, sd = len(x), float(x.mean()), float(x.std(ddof=1))
    t = m / (sd / math.sqrt(n)) if sd > 0 else float("inf")
    p = float(stats.t.sf(t, n - 1))
    # minimum mean effect detectable at 80% power (one-sided alpha 0.05) with this sd and n
    mde = (stats.t.ppf(0.95, n - 1) + stats.t.ppf(0.80, n - 1)) * sd / math.sqrt(n)
    return {"n": n, "mean": m, "sd": sd, "t": t, "p_one_sided": p, "ci": ci(x.tolist()), "mde_80pct_power": float(mde)}


def crossover(values_even, values_odd) -> dict:
    """X4b: the window term flips sign between even and odd seeds, so opt = (mean_even + mean_odd) / 2 (Welch SE)."""
    from scipy import stats

    e, o = np.asarray(values_even, float), np.asarray(values_odd, float)
    ve, vo = e.var(ddof=1) / len(e), o.var(ddof=1) / len(o)
    opt, se = float((e.mean() + o.mean()) / 2), 0.5 * math.sqrt(ve + vo)
    df = (ve + vo) ** 2 / (ve ** 2 / (len(e) - 1) + vo ** 2 / (len(o) - 1)) if (ve + vo) > 0 else float("inf")
    t = opt / se if se > 0 else float("inf")
    return {"opt": opt, "se": se, "df": float(df), "t": t, "p_one_sided": float(stats.t.sf(t, df)),
            "window_term": float((o.mean() - e.mean()) / 2), "mean_even": float(e.mean()), "mean_odd": float(o.mean()),
            "pass": bool(opt > 0 and stats.t.sf(t, df) < 0.05)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--experiments", type=int, default=300)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", type=str, default="", help="comma-separated seeds to run now (no analysis written)")
    a = ap.parse_args()
    if a.only:
        OUT.mkdir(parents=True, exist_ok=True)
        for s in [int(x) for x in a.only.split(",")]:
            if claim(OUT / f"claim_{s}_{a.experiments}"):  # slots sharing one seed list never run a seed twice
                night((s, a.experiments, CHECKPOINTS))
        return
    n_exp = 20 if a.quick else a.experiments
    seeds = list(range(2 if a.quick else a.seeds))
    checkpoints = (10, 20) if a.quick else CHECKPOINTS
    nights = pool_map(night, [(s, n_exp, checkpoints) for s in seeds], a.workers)
    k0, k1 = checkpoints[0], checkpoints[-1]

    def o_at(group: list[dict], k: int, key: str = "O_alt") -> list[float]:
        return [nt["checkpoints"][k][key] for nt in group]

    def growth(group: list[dict], key: str = "O_alt") -> list[float]:
        return [nt["checkpoints"][k1][key] - nt["checkpoints"][k0][key] for nt in group]

    h1, h2 = one_sided_t(o_at(nights, k1)), one_sided_t(growth(nights))
    verdict = {"H1_O_end_gt_0": {**h1, "pass": bool(h1["mean"] > 0 and h1["p_one_sided"] < 0.05)},
               "H2_growth_gt_0": {**h2, "pass": bool(h2["mean"] > 0 and h2["p_one_sided"] < 0.05)},
               "secondary_test_iid": {"O_end": one_sided_t(o_at(nights, k1, "O_iid")),
                                      "growth": one_sided_t(growth(nights, "O_iid"))},
               "O_alt_by_checkpoint": {k: ci(o_at(nights, k)) for k in checkpoints},
               "O_iid_by_checkpoint": {k: ci(o_at(nights, k, "O_iid")) for k in checkpoints},
               "mean_keeps": float(np.mean([nt["n_keeps"] for nt in nights])),
               "mean_runs": float(np.mean([nt["n_runs"] for nt in nights]))}
    ev = [nt for nt in nights if nt["sel_window"] == 0]
    od = [nt for nt in nights if nt["sel_window"] == 1]
    if len(ev) > 1 and len(od) > 1:
        verdict["X4b_crossover"] = {
            "H1b_opt_end": crossover(o_at(ev, k1), o_at(od, k1)), "H2b_opt_growth": crossover(growth(ev), growth(od)),
            "opt_by_checkpoint": {k: crossover(o_at(ev, k), o_at(od, k))["opt"] for k in checkpoints}}
    both = verdict["H1_O_end_gt_0"]["pass"] and verdict["H2_growth_gt_0"]["pass"]
    one = verdict["H1_O_end_gt_0"]["pass"] or verdict["H2_growth_gt_0"]["pass"]
    verdict["o26"] = "REPRODUCED" if both else "PARTIAL" if one else "NOT REPRODUCED"
    out = {"config": {"task": "tinylm", "budget_s": 2.0, "budget_kind": "wallclock", "mode": "hardened (runs scored "
                      "in audit mode; agent sees val_bpb only)", "agent": "MockResearchAgent greedy",
                      "keep_rule": "upstream", "experiments": n_exp, "seeds": seeds, "checkpoints": checkpoints,
                      "counterbalanced": "even seeds select on val window 0, odd seeds on window 1"},
           "nights": nights, "verdict": verdict}
    write("r2_val_reuse_tinylm" + ("_quick" if a.quick else ""), out)
    print(json.dumps({k: v for k, v in verdict.items() if k not in ("O_alt_by_checkpoint", "O_iid_by_checkpoint")},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
