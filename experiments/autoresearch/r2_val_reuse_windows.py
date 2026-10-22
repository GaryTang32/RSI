"""R2-X4c - validation reuse with four rotating, equally sized held-out windows (claim O26; retry round 2).

Usage: python experiments/autoresearch/r2_val_reuse_windows.py --only 8,9,... (resumable nights) | --analyze
"""
from _common import SCRATCH, cached_json, claim, hardened_audit, insert_audit_code, scored_keeps, write  # noqa: I001

import argparse
import json
import math

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, MockResearchAgent
from rsi.domains.tinylm import TinyLMTask
from rsi.domains.tinylm.task import DOMAIN_DIR

CHECKPOINTS = (50, 100, 150, 200, 250, 300)
WINDOWS = ("V0", "V1", "T0", "T1")  # disjoint 32768-byte windows: val[0:], val[32768:], test_iid[0:], test_iid[32768:]
SEEDS = tuple(range(8, 20))  # WINDOWS[seed % 4]: each window selects in 3 nights (balanced rotation)
OUT = SCRATCH / "r2_val_reuse_windows"

_WIN = '''    if _CONST["MODE"] == "audit" and os.environ.get("RSI_AR_SEL_WINDOW", "") != "":
        # measurement-only (retry round 2, X4c): score four disjoint, equally sized windows on the same
        # weights; the loop's val_bpb is the selected one. TOKEN_BYTES mask as in _bpb_locked.
        _seq, _ev, _rows = _CONST["SEQ_LEN"], _CONST["EVAL_BYTES"], _CONST["HARDENED_EVAL_ROWS"]
        _n = _ev // _seq
        _wins = {}
        for _name, _split, _off in (("V0", "val", 0), ("V1", "val", _ev), ("T0", "test_iid", 0), ("T1", "test_iid", _ev)):
            _d = _read_split(_split)
            assert len(_d) >= _off + _n * _seq + 1, (_name, len(_d))
            _st = _off + _LIB["np.arange"](_n) * _seq
            _ch = _d[_st[:, None] + _LIB["np.arange"](_seq + 1)[None, :]]
            _tn, _tb = 0.0, 0
            for _s in range(0, _n, _rows):
                _x, _y = _ch[_s:_s + _rows, :-1], _ch[_s:_s + _rows, 1:]
                _lp = _logprobs(model, _x)
                _nb = _TOKEN_BYTES[_y]
                _nats = -_LIB["np.take_along_axis"](_lp, _y[..., None].astype(np.int64), axis=-1)[..., 0]
                _tn += float((_nats * (_nb > 0)).sum())
                _tb += int(_nb.sum())
            _wins[_name] = _tn / (_LIB["math.log"](2) * max(_tb, 1))
        record.setdefault("audit", {})["windows"] = _wins
        val_bpb = _wins[os.environ["RSI_AR_SEL_WINDOW"]]
        record["val_bpb"] = val_bpb
'''


def variant_prepare() -> str:
    return insert_audit_code(_WIN)


class WindowsTask(TinyLMTask):
    """tinylm scored in audit mode on four windows; the loop sees only the selected window's bpb."""

    def __init__(self, sel: str, budget_s: float = 2.0):
        files = {"prepare.py": variant_prepare(), "train.py": (DOMAIN_DIR / "train.py").read_text(),
                 "README.md": (DOMAIN_DIR / "README.md").read_text()}
        super().__init__(budget_s=budget_s, files=files)
        assert sel in WINDOWS
        self.sel = sel
        self.env = {"RSI_AR_SEL_WINDOW": sel}
        self.scored: list[dict] = []

    def run(self, artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0):
        m = "audit" if mode == "hardened" else mode
        out = super().run(artifact, seed=seed, mode=m, log_path=log_path, val_epoch=0)
        if m == "audit":
            wins = dict(hardened_audit(out).get("windows") or {})
            self.scored.append({"artifact_id": artifact.id, "metric": out.metric, "windows": wins})
        return out


def o_value(base: dict, inc: dict, sel: str) -> dict:
    """Optimism of the selected window against the mean of the other three (gains = base - incumbent)."""
    gains = {w: base[w] - inc[w] for w in WINDOWS}
    others = [gains[w] for w in WINDOWS if w != sel]
    return {"O": gains[sel] - float(np.mean(others)), "gains": gains}


def _night(seed: int, n_exp: int) -> dict:
    sel = WINDOWS[seed % 4]
    task = WindowsTask(sel)
    cfg = Config(max_experiments=n_exp, seed=seed, shadow_monitor=False, hidden_audit=False, plot=False,
                 overwrite=True, tag=f"r2-valwin-{seed}")
    loop = AutoresearchLoop(task, MockResearchAgent(task.mock_edit_pool(), seed=seed), cfg, out_dir=OUT / f"seed{seed}")
    nodes, matched = scored_keeps(loop.run(), task.scored)
    keeps = [{"exp": nd.round, "sel": nd.score, "windows": h and h["windows"], "change": nd.change}
             for nd, h in matched]
    base = keeps[0]
    assert abs(base["windows"][sel] - base["sel"]) < 1e-9
    rows = {}
    for k in CHECKPOINTS:
        inc = [kk for kk in keeps if kk["exp"] <= k][-1]
        rows[str(k)] = {"exp_of_incumbent": inc["exp"], **o_value(base["windows"], inc["windows"], sel)}
    return {"seed": seed, "sel_window": sel, "n_turns": loop.n_rounds, "n_runs": loop.n_runs,
            "n_experiments_run": sum(1 for nd in nodes if nd.status in ("keep", "discard", "crash")),
            "n_keeps": len(keeps) - 1, "counters": dict(loop.counters), "keeps": keeps, "checkpoints": rows,
            "unmatched_keeps": sum(1 for kk in keeps if kk["windows"] is None)}


def night(seed: int, n_exp: int = 300) -> dict:
    return cached_json(OUT / f"night_{seed}_{n_exp}.json", lambda: _night(seed, n_exp))


def balanced_test(values: list[float], windows: list[str]) -> dict:
    """One-sided t-test of tau > 0, tau = mean over windows of the per-window mean, SE from the pooled
    within-window variance (df = n - n_windows); also the plain one-sample t."""
    from scipy import stats

    x = np.asarray(values, float)
    groups = {w: x[[i for i, ww in enumerate(windows) if ww == w]] for w in sorted(set(windows))}
    means = {w: float(g.mean()) for w, g in groups.items()}
    tau = float(np.mean(list(means.values())))
    df = len(x) - len(groups)
    ss = sum(float(((g - g.mean()) ** 2).sum()) for g in groups.values())
    sp2 = ss / df
    se = math.sqrt(sp2 * sum(1.0 / len(g) for g in groups.values()) / len(groups) ** 2)
    t = tau / se if se > 0 else float("inf")
    p = float(stats.t.sf(t, df))
    h = float(stats.t.ppf(0.975, df)) * se
    mde = (stats.t.ppf(0.95, df) + stats.t.ppf(0.80, df)) * se
    n, m, sd = len(x), float(x.mean()), float(x.std(ddof=1))
    t1 = m / (sd / math.sqrt(n))
    h1 = float(stats.t.ppf(0.975, n - 1)) * sd / math.sqrt(n)
    return {"tau": tau, "se": se, "df": df, "t": t, "p_one_sided": p, "t_ci95": [tau - h, tau + h],
            "mde_80pct_power": float(mde), "per_window_mean": means, "pass": bool(tau > 0 and p < 0.05),
            "plain_one_sample": {"mean": m, "sd": sd, "p_one_sided": float(stats.t.sf(t1, n - 1)),
                                 "t_ci95": [m - h1, m + h1]}}


def analyze(nights: list[dict]) -> dict:
    """H1c: tau(300) > 0; H2c: tau(300) - tau(50) > 0."""
    k0, k1 = str(CHECKPOINTS[0]), str(CHECKPOINTS[-1])
    wins = [nt["sel_window"] for nt in nights]
    h1 = balanced_test([nt["checkpoints"][k1]["O"] for nt in nights], wins)
    h2 = balanced_test([nt["checkpoints"][k1]["O"] - nt["checkpoints"][k0]["O"] for nt in nights], wins)
    curve = {k: balanced_test([nt["checkpoints"][str(k)]["O"] for nt in nights], wins)["tau"] for k in CHECKPOINTS}
    same_window = {}  # descriptive: a window's gain when selected minus its gain when held out
    for w in WINDOWS:
        sel = [nt["checkpoints"][k1]["gains"][w] for nt in nights if nt["sel_window"] == w]
        held = [nt["checkpoints"][k1]["gains"][w] for nt in nights if nt["sel_window"] != w]
        same_window[w] = {"gain_when_selected": float(np.mean(sel)), "gain_when_held_out": float(np.mean(held)),
                          "diff": float(np.mean(sel) - np.mean(held))}
    keeps = [nt["n_keeps"] for nt in nights]
    return {"H1c_tau_end_gt_0": h1, "H2c_growth_gt_0": h2, "tau_by_checkpoint": curve,
            "same_window_contrast": same_window, "mean_keeps": float(np.mean(keeps)),
            "keeps_range": [min(keeps), max(keeps)],
            "unmatched_keeps": int(sum(nt["unmatched_keeps"] for nt in nights)),
            "o26_rule": ("REPRODUCED" if h1["pass"] and h2["pass"] else "PARTIAL")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", type=str, default="", help="comma-separated seeds to run now (claim files)")
    ap.add_argument("--experiments", type=int, default=300)
    ap.add_argument("--analyze", action="store_true")
    a = ap.parse_args()
    if a.only:
        OUT.mkdir(parents=True, exist_ok=True)
        for s in [int(x) for x in a.only.split(",")]:
            if claim(OUT / f"claim_{s}_{a.experiments}"):  # slots sharing one seed list never run a seed twice
                night(s, a.experiments)
                print(f"seed {s} done", flush=True)
    if a.analyze:
        nights = [night(s, a.experiments) for s in SEEDS]
        verdict = analyze(nights)
        out = {"config": {"task": "tinylm", "budget_s": 2.0, "budget_kind": "wallclock", "mode": "hardened (runs "
                          "scored in audit mode; agent sees the selected window's val_bpb only)",
                          "agent": "MockResearchAgent greedy", "keep_rule": "upstream", "experiments": a.experiments,
                          "seeds": list(SEEDS), "windows": WINDOWS, "selection": "WINDOWS[seed % 4]",
                          "checkpoints": CHECKPOINTS},
               "nights": nights, "verdict": verdict}
        write("r2_val_reuse_windows", out)
        print(json.dumps({k: v for k, v in verdict.items() if k != "tau_by_checkpoint"}, indent=1, default=str))


if __name__ == "__main__":
    main()
