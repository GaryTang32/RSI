"""E5b (retry round 2, preregistered P7 in docs/methods/rrsi/claims-audit.md section 6): claim L16.

"Cost must be earned ... the result is a lighter harness with >= OOD." E5's two decisive arms (full
RRSI at beta1 = 40 vs "no complexity term": cost rule off and w_c = 0), default config, T = 20, at
the sample size a power analysis gives for E5's own pre-declared 1-pt non-inferiority margin: E5's
paired OOD SD is 10.4 pts, so n = ((1.96 + 1.28) * 10.4 / 1)^2 ~ 1,140; we run 1,200 worlds.

Runs in chunks (``--start/--stop``; each chunk writes its rows to the scratch directory), then
``--aggregate`` pools every chunk. Pass: tokens x H_0 (on - off) CI upper < 0 AND OOD (on - off)
CI lower > -1.0 pt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import paired, pmap, run_hw, save, strip_curves, summarize  # noqa: E402

from rsi.rrsi import RegularizerSwitches  # noqa: E402

ON, OFF = "beta1=40 (full)", "no complexity term (cost rule off, w_c=0)"
ARMS = {ON: ("full", {"beta1": 40.0}),
        OFF: (RegularizerSwitches.full().but(cost_rule=False, name="no_cost"), {"w_c": 0.0})}
CHUNKS = Path(os.environ.get("RRSI_SCRATCH", tempfile.gettempdir())) / "e5b_chunks"


def main():
    ap = argparse.ArgumentParser(description="E5b: powered non-inferiority test of the complexity term")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--stop", type=int, default=1200)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--aggregate", action="store_true")
    ap.add_argument("--n", type=int, default=1200, help="preregistered number of worlds (aggregate)")
    a = ap.parse_args()
    CHUNKS.mkdir(parents=True, exist_ok=True)
    if not a.aggregate:
        jobs = [{"seed": s, "arm": sw, "label": lab, "cfg": {"T": 20, **cfg}}
                for s in range(a.start, a.stop) for lab, (sw, cfg) in ARMS.items()]
        rows = strip_curves(pmap(run_hw, jobs, a.workers))
        p = CHUNKS / f"rows_{a.start}_{a.stop}.json"
        p.write_text(json.dumps(rows))
        print(f"[chunk] {p} ({len(rows)} rows)")
        return
    rows = []
    for p in sorted(CHUNKS.glob("rows_*.json")):
        rows += json.loads(p.read_text())
    seeds = sorted({r["seed"] for r in rows})
    assert seeds == list(range(a.n)), f"expected worlds 0..{a.n - 1}, found {len(seeds)}"
    by = {(r["seed"], r["label"]) for r in rows}
    assert all((s, lab) in by for s in seeds for lab in ARMS), "a chunk is missing an arm"
    metrics = ("token_ratio", "ood_gain", "holdout_gain", "unseen_gain", "evolve_gain", "measured_gain", "n_mechanisms")
    summ = summarize(rows, metrics=metrics)
    pd = {m: paired(rows, OFF, ON, m) for m in metrics}
    first50 = [r for r in rows if r["seed"] < 50]
    pd50 = {m: paired(first50, OFF, ON, m) for m in ("token_ratio", "ood_gain")}
    checks = {"tokens lower (CI upper < 0)": pd["token_ratio"]["hi"] < 0,
              "OOD non-inferior at the 1-pt margin (CI lower > -0.01)": pd["ood_gain"]["lo"] > -0.01}
    verdict = ("REPRODUCED" if all(checks.values()) else
               "PARTIAL" if checks["tokens lower (CI upper < 0)"] else "NOT REPRODUCED")
    out = {"experiment": "E5b powered complexity-term test (retry round 2: P7)",
           "config": {"worlds": a.n, "T": 20, "arms": {k: [str(v[0]), v[1]] for k, v in ARMS.items()},
                      "margin_pts": 1.0, "power_basis": "E5 paired OOD SD 10.4 pts; n ~ 1,140 for 90% power"},
           "summary": summ, "paired_on_minus_off": pd, "paired_first_50_worlds (= E5's seeds)": pd50,
           "checks": checks, "verdict": verdict, "rows": rows}
    save("e5b_cost_rule_power", out)
    print({m: (round(v["mean_diff"] * (1 if m in ("token_ratio", "n_mechanisms") else 100), 3),
               round(v["lo"] * (1 if m in ("token_ratio", "n_mechanisms") else 100), 3),
               round(v["hi"] * (1 if m in ("token_ratio", "n_mechanisms") else 100), 3)) for m, v in pd.items()})
    print(checks, verdict)


if __name__ == "__main__":
    main()
