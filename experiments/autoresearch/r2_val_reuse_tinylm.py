"""R2-X4 - validation reuse over hundreds of real-training decisions (claim O26; retry round 2).

Claim [doc:121]: "The same validation data judges every experiment, so hundreds of experiments slowly
tune to it."

The first round measured this on the landscape (where the val-specific component is built in), on
tabular at 30 decisions (null) and not at all on tinylm. Here (preregistered, claims-audit.md
section 5, X4): tinylm with real training, a 2 s wall-clock budget, the scripted greedy agent, the
default keep rule and **300 experiments per night**, 8 seeds.

Measurement. The loop selects on one validation window ("sel"). A measurement-only variant of the
locked prepare.py also scores, in audit mode, a *disjoint* window of the same val split ("alt") and
the hidden test_iid split, **on the same trained weights in the same run**, so training luck cancels.
The agent and the keep rule see only ``val_bpb`` (the hardened record checks still turn a flagged run
into a crash). Even seeds select on window 0 (val bytes 0-32767) and hold out window 1
(32768-65535); odd seeds the reverse, so a systematic difference between the windows cancels.

  O(k) = [sel(base) - sel(inc_k)] - [alt(base) - alt(inc_k)]    (inc_k = incumbent after k experiments)

H1: mean O(300) > 0 (one-sided t, p < 0.05); H2: mean [O(300) - O(50)] > 0 (one-sided t, p < 0.05).

Usage: python experiments/autoresearch/r2_val_reuse_tinylm.py [--seeds 8] [--experiments 300] [--workers 2] [--quick]
"""
from __future__ import annotations

from _common import SCRATCH, ci, pool_map, write  # noqa: I001

import argparse
import json
import math

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, MockResearchAgent
from rsi.domains.tinylm import TinyLMTask
from rsi.domains.tinylm.task import DOMAIN_DIR

CHECKPOINTS = (50, 100, 150, 200, 250, 300)

_ANCHOR = '    record["eval_seconds"] = clock() - t0\n'
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
    src = (DOMAIN_DIR / "prepare.py").read_text()
    assert src.count(_ANCHOR) == 1
    return src.replace(_ANCHOR, _ALT + _ANCHOR)


class ValReuseTask(TinyLMTask):
    """tinylm whose runs are scored in audit mode (hidden numbers kept aside, never shown to the agent)."""

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
            flags = out.meta.pop("audit_flags", None)
            if flags and out.metric is not None:        # hardened semantics: a flagged record is a crash
                out.crash_reason, out.metric = flags[0], None
            aud = dict((out.record or {}).get("audit") or {})
            self.held_out.append({"artifact_id": artifact.id, "metric": out.metric, "val_alt": aud.get("val_alt"),
                                  "test_iid": aud.get("test_iid"), "val_alt_offset": aud.get("val_alt_offset")})
        return out


def night(args) -> dict:
    """One night; the result is also saved per seed so a split or interrupted series can be resumed/merged."""
    seed, n_exp = args
    done = SCRATCH / "r2_val_reuse" / f"night_{seed}_{n_exp}.json"
    if not done.exists():
        out = _night(seed, n_exp)
        done.parent.mkdir(parents=True, exist_ok=True)
        done.write_text(json.dumps(out, default=str))
    out = json.loads(done.read_text())
    out["checkpoints"] = {int(k): v for k, v in out["checkpoints"].items()}
    return out


def _night(seed: int, n_exp: int) -> dict:
    sel = seed % 2
    task = ValReuseTask(sel_epoch=sel)
    agent = MockResearchAgent(task.mock_edit_pool(), seed=seed)
    cfg = Config(max_experiments=n_exp, seed=seed, shadow_monitor=False, hidden_audit=False, plot=False,
                 overwrite=True, tag=f"r2-valreuse-{seed}")
    loop = AutoresearchLoop(task, agent, cfg, out_dir=SCRATCH / "r2_val_reuse" / f"seed{seed}")
    res = loop.run()
    by_key = {}
    for h in task.held_out:
        if h["metric"] is not None:
            by_key.setdefault((h["artifact_id"], round(h["metric"], 12)), h)
    nodes = [nd for nd in res.ledger.nodes() if nd.status in ("keep", "discard", "crash", "rejected")]
    keeps = []
    for nd in nodes:
        if nd.status == "keep":
            h = by_key.get((nd.artifact_id, round(nd.score, 12)))
            keeps.append({"exp": nd.round, "sel": nd.score, "alt": h and h["val_alt"], "iid": h and h["test_iid"],
                          "change": nd.change})
    base = keeps[0]
    rows = {}
    for k in CHECKPOINTS:
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
    x = np.asarray(x, dtype=float)
    n, m, sd = len(x), float(x.mean()), float(x.std(ddof=1))
    from scipy import stats

    t = m / (sd / math.sqrt(n)) if sd > 0 else float("inf")
    p = float(stats.t.sf(t, n - 1))
    # minimum mean effect detectable at 80% power (one-sided alpha 0.05) with this sd and n
    mde = (stats.t.ppf(0.95, n - 1) + stats.t.ppf(0.80, n - 1)) * sd / math.sqrt(n)
    return {"n": n, "mean": m, "sd": sd, "t": t, "p_one_sided": p, "ci": ci(x.tolist()), "mde_80pct_power": float(mde)}


def crossover(values_even, values_odd) -> dict:
    """X4b: the counterbalanced (crossover) estimate - the window term flips sign between even and odd
    seeds, so opt = (mean_even + mean_odd) / 2 with a Welch standard error."""
    e, o = np.asarray(values_even, float), np.asarray(values_odd, float)
    ve, vo = e.var(ddof=1) / len(e), o.var(ddof=1) / len(o)
    opt, se = float((e.mean() + o.mean()) / 2), 0.5 * math.sqrt(ve + vo)
    df = (ve + vo) ** 2 / (ve ** 2 / (len(e) - 1) + vo ** 2 / (len(o) - 1)) if (ve + vo) > 0 else float("inf")
    from scipy import stats

    t = opt / se if se > 0 else float("inf")
    return {"opt": opt, "se": se, "df": float(df), "t": t, "p_one_sided": float(stats.t.sf(t, df)),
            "window_term": float((o.mean() - e.mean()) / 2), "mean_even": float(e.mean()), "mean_odd": float(o.mean()),
            "pass": bool(opt > 0 and stats.t.sf(t, df) < 0.05)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--seeds", type=int, default=8)
    ap.add_argument("--experiments", type=int, default=300)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", type=str, default="", help="comma-separated seeds to run now (no analysis written)")
    a = ap.parse_args()
    if a.only:
        import os

        claims = SCRATCH / "r2_val_reuse"
        claims.mkdir(parents=True, exist_ok=True)
        for s in [int(x) for x in a.only.split(",")]:
            try:                                  # two slots can share one seed list without running a seed twice
                os.close(os.open(claims / f"claim_{s}_{a.experiments}", os.O_CREAT | os.O_EXCL))
            except FileExistsError:
                continue
            night((s, a.experiments))
        return
    n_exp = 20 if a.quick else a.experiments
    seeds = list(range(2 if a.quick else a.seeds))
    global CHECKPOINTS
    if a.quick:
        CHECKPOINTS = (10, 20)
    nights = pool_map(night, [(s, n_exp) for s in seeds], a.workers)
    k0, k1 = CHECKPOINTS[0], CHECKPOINTS[-1]
    O_end = [nt["checkpoints"][k1]["O_alt"] for nt in nights]
    growth = [nt["checkpoints"][k1]["O_alt"] - nt["checkpoints"][k0]["O_alt"] for nt in nights]
    h1, h2 = one_sided_t(O_end), one_sided_t(growth)
    curve = {k: ci([nt["checkpoints"][k]["O_alt"] for nt in nights]) for k in CHECKPOINTS}
    curve_iid = {k: ci([nt["checkpoints"][k]["O_iid"] for nt in nights]) for k in CHECKPOINTS}
    sec_end = one_sided_t([nt["checkpoints"][k1]["O_iid"] for nt in nights])
    sec_growth = one_sided_t([nt["checkpoints"][k1]["O_iid"] - nt["checkpoints"][k0]["O_iid"] for nt in nights])
    verdict = {"H1_O_end_gt_0": {**h1, "pass": bool(h1["mean"] > 0 and h1["p_one_sided"] < 0.05)},
               "H2_growth_gt_0": {**h2, "pass": bool(h2["mean"] > 0 and h2["p_one_sided"] < 0.05)},
               "secondary_test_iid": {"O_end": sec_end, "growth": sec_growth},
               "O_alt_by_checkpoint": curve, "O_iid_by_checkpoint": curve_iid,
               "mean_keeps": float(np.mean([nt["n_keeps"] for nt in nights])),
               "mean_runs": float(np.mean([nt["n_runs"] for nt in nights]))}
    ev = [nt for nt in nights if nt["sel_window"] == 0]
    od = [nt for nt in nights if nt["sel_window"] == 1]
    if len(ev) > 1 and len(od) > 1:
        verdict["X4b_crossover"] = {
            "H1b_opt_end": crossover([nt["checkpoints"][k1]["O_alt"] for nt in ev],
                                     [nt["checkpoints"][k1]["O_alt"] for nt in od]),
            "H2b_opt_growth": crossover([nt["checkpoints"][k1]["O_alt"] - nt["checkpoints"][k0]["O_alt"] for nt in ev],
                                        [nt["checkpoints"][k1]["O_alt"] - nt["checkpoints"][k0]["O_alt"] for nt in od]),
            "opt_by_checkpoint": {k: crossover([nt["checkpoints"][k]["O_alt"] for nt in ev],
                                               [nt["checkpoints"][k]["O_alt"] for nt in od])["opt"] for k in CHECKPOINTS}}
    both = verdict["H1_O_end_gt_0"]["pass"] and verdict["H2_growth_gt_0"]["pass"]
    one = verdict["H1_O_end_gt_0"]["pass"] or verdict["H2_growth_gt_0"]["pass"]
    verdict["o26"] = "REPRODUCED" if both else "PARTIAL" if one else "NOT REPRODUCED"
    out = {"config": {"task": "tinylm", "budget_s": 2.0, "budget_kind": "wallclock", "mode": "hardened (runs scored "
                      "in audit mode; agent sees val_bpb only)", "agent": "MockResearchAgent greedy",
                      "keep_rule": "upstream", "experiments": n_exp, "seeds": seeds, "checkpoints": CHECKPOINTS,
                      "counterbalanced": "even seeds select on val window 0, odd seeds on window 1"},
           "nights": nights, "verdict": verdict}
    write("r2_val_reuse_tinylm" + ("_quick" if a.quick else ""), out)
    print(json.dumps({k: v for k, v in verdict.items() if k not in ("O_alt_by_checkpoint", "O_iid_by_checkpoint")},
                     indent=1, default=str))


if __name__ == "__main__":
    main()
