"""R2-X4c - validation reuse with four rotating, equally sized held-out windows (claim O26; retry round 2,
review follow-up).

Why. X4 (``r2_val_reuse_tinylm.py``) selected on val window 0 (even seeds) or val window 1 (odd seeds)
and measured optimism against the other window. The reviewers showed that the two windows respond
differently to the same real improvement (O(300) = 0.070 when selecting on window 0 vs 0.013 when
selecting on window 1) and that the hidden test_iid window gave only 0.009, so the size of the tuning
effect was not identified by a two-window design.

Design (preregistered in claims-audit.md section 5, entry X4c, before any X4c night ran). Four
disjoint, equally sized (EVAL_BYTES = 32768) windows of the two iid splits (documents are assigned to
val / test_iid by a hash, so both are iid samples of the same corpus):

  V0 = val[0:], V1 = val[32768:], T0 = test_iid[0:], T1 = test_iid[32768:]

Every run is scored on all four windows on the same trained weights (audit mode, measurement only).
The loop's ``val_bpb`` (the only number the agent and the keep rule see) is the window
``WINDOWS[seed % 4]``; seeds 8-19 give each window 3 nights (balanced rotation), so any fixed
"window responsiveness" term sums to zero over the design:

  O_s(k) = gain_sel(k) - mean over the 3 other windows of gain_w(k),   gain_w(k) = w(base) - w(inc_k)

tau(k) = mean over windows of the per-window mean O(k). SE from the pooled within-window variance
(df = n - 4). H1c: tau(300) > 0, H2c: tau(300) - tau(50) > 0, each one-sided p < 0.05.

Usage: python experiments/autoresearch/r2_val_reuse_windows.py --only 8,9,...   (run nights; resumable)
       python experiments/autoresearch/r2_val_reuse_windows.py --analyze        (write the result file)
"""
from __future__ import annotations

from _common import SCRATCH, write  # noqa: I001

import argparse
import json
import math
import os

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, MockResearchAgent
from rsi.domains.tinylm import TinyLMTask
from rsi.domains.tinylm.task import DOMAIN_DIR

CHECKPOINTS = (50, 100, 150, 200, 250, 300)
WINDOWS = ("V0", "V1", "T0", "T1")
SEEDS = tuple(range(8, 20))
OUT = SCRATCH / "r2_val_reuse_windows"

_ANCHOR = '    record["eval_seconds"] = clock() - t0\n'
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
    src = (DOMAIN_DIR / "prepare.py").read_text()
    assert src.count(_ANCHOR) == 1
    return src.replace(_ANCHOR, _WIN + _ANCHOR)


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
            flags = out.meta.pop("audit_flags", None)
            if flags and out.metric is not None:        # hardened semantics: a flagged record is a crash
                out.crash_reason, out.metric = flags[0], None
            wins = dict(((out.record or {}).get("audit") or {}).get("windows") or {})
            self.scored.append({"artifact_id": artifact.id, "metric": out.metric, "windows": wins})
        return out


def o_value(base: dict, inc: dict, sel: str) -> dict:
    """Optimism of the selected window against the mean of the other three (gains = base - incumbent)."""
    gains = {w: base[w] - inc[w] for w in WINDOWS}
    others = [gains[w] for w in WINDOWS if w != sel]
    return {"O": gains[sel] - float(np.mean(others)), "gains": gains}


def night(seed: int, n_exp: int = 300) -> dict:
    done = OUT / f"night_{seed}_{n_exp}.json"
    if done.exists():
        return json.loads(done.read_text())
    sel = WINDOWS[seed % 4]
    task = WindowsTask(sel)
    agent = MockResearchAgent(task.mock_edit_pool(), seed=seed)
    cfg = Config(max_experiments=n_exp, seed=seed, shadow_monitor=False, hidden_audit=False, plot=False,
                 overwrite=True, tag=f"r2-valwin-{seed}")
    loop = AutoresearchLoop(task, agent, cfg, out_dir=OUT / f"seed{seed}")
    res = loop.run()
    by_key = {}
    for h in task.scored:
        if h["metric"] is not None:
            by_key.setdefault((h["artifact_id"], round(h["metric"], 12)), h)
    nodes = [nd for nd in res.ledger.nodes() if nd.status in ("keep", "discard", "crash", "rejected")]
    keeps = []
    for nd in nodes:
        if nd.status == "keep":
            h = by_key.get((nd.artifact_id, round(nd.score, 12)))
            keeps.append({"exp": nd.round, "sel": nd.score, "windows": h and h["windows"], "change": nd.change})
    base = keeps[0]
    assert abs(base["windows"][sel] - base["sel"]) < 1e-9
    rows = {}
    for k in CHECKPOINTS:
        inc = [kk for kk in keeps if kk["exp"] <= k][-1]
        rows[str(k)] = {"exp_of_incumbent": inc["exp"], **o_value(base["windows"], inc["windows"], sel)}
    out = {"seed": seed, "sel_window": sel, "n_turns": loop.n_rounds, "n_runs": loop.n_runs,
           "n_experiments_run": sum(1 for nd in nodes if nd.status in ("keep", "discard", "crash")),
           "n_keeps": len(keeps) - 1, "counters": dict(loop.counters), "keeps": keeps, "checkpoints": rows,
           "unmatched_keeps": sum(1 for kk in keeps if kk["windows"] is None)}
    OUT.mkdir(parents=True, exist_ok=True)
    done.write_text(json.dumps(out, default=str))
    return out


def balanced_test(values: list[float], windows: list[str]) -> dict:
    """tau = mean over windows of the per-window mean; SE from the pooled within-window variance
    (one-way layout, df = n - n_windows); one-sided t-test of tau > 0. Also the plain one-sample t."""
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
    k0, k1 = str(CHECKPOINTS[0]), str(CHECKPOINTS[-1])
    wins = [nt["sel_window"] for nt in nights]
    h1 = balanced_test([nt["checkpoints"][k1]["O"] for nt in nights], wins)
    h2 = balanced_test([nt["checkpoints"][k1]["O"] - nt["checkpoints"][k0]["O"] for nt in nights], wins)
    curve = {k: balanced_test([nt["checkpoints"][str(k)]["O"] for nt in nights], wins)["tau"] for k in CHECKPOINTS}
    # descriptive: for each window, its gain when selected minus its gain when held out (other nights)
    same_window = {}
    for w in WINDOWS:
        sel = [nt["checkpoints"][k1]["gains"][w] for nt in nights if nt["sel_window"] == w]
        held = [nt["checkpoints"][k1]["gains"][w] for nt in nights if nt["sel_window"] != w]
        same_window[w] = {"gain_when_selected": float(np.mean(sel)), "gain_when_held_out": float(np.mean(held)),
                          "diff": float(np.mean(sel) - np.mean(held))}
    return {"H1c_tau_end_gt_0": h1, "H2c_growth_gt_0": h2, "tau_by_checkpoint": curve,
            "same_window_contrast": same_window,
            "mean_keeps": float(np.mean([nt["n_keeps"] for nt in nights])),
            "keeps_range": [min(nt["n_keeps"] for nt in nights), max(nt["n_keeps"] for nt in nights)],
            "unmatched_keeps": int(sum(nt["unmatched_keeps"] for nt in nights)),
            "o26_rule": ("REPRODUCED" if h1["pass"] and h2["pass"] else "PARTIAL")}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", type=str, default="", help="comma-separated seeds to run now (claim files)")
    ap.add_argument("--experiments", type=int, default=300)
    ap.add_argument("--analyze", action="store_true")
    a = ap.parse_args()
    if a.only:
        OUT.mkdir(parents=True, exist_ok=True)
        for s in [int(x) for x in a.only.split(",")]:
            try:                                  # two slots can share one seed list without running a seed twice
                os.close(os.open(OUT / f"claim_{s}_{a.experiments}", os.O_CREAT | os.O_EXCL))
            except FileExistsError:
                continue
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
