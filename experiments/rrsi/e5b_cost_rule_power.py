"""E5b (preregistered P7, claim L16): E5's complexity term on 1,200 worlds, powered for a 1-pt OOD margin.

Run in chunks, then pool: python experiments/rrsi/e5b_cost_rule_power.py --start A --stop B; ... --aggregate
"""
import argparse
import json

from _common import SCRATCH, ci_pts, pairs, pmap, run_hw, save, strip_curves, summarize

from rsi.rrsi import RegularizerSwitches

ON, OFF = "beta1=40 (full)", "no complexity term (cost rule off, w_c=0)"
ARMS = {ON: ("full", {"beta1": 40.0}),
        OFF: (RegularizerSwitches.full().but(cost_rule=False, name="no_cost"), {"w_c": 0.0})}
CHUNKS = SCRATCH.parent / "e5b_chunks"


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
    rows = [r for p in sorted(CHUNKS.glob("rows_*.json")) for r in json.loads(p.read_text())]
    seeds = sorted({r["seed"] for r in rows})
    assert seeds == list(range(a.n)), f"expected worlds 0..{a.n - 1}, found {len(seeds)}"
    by = {(r["seed"], r["label"]) for r in rows}
    assert all((s, lab) in by for s in seeds for lab in ARMS), "a chunk is missing an arm"
    metrics = ("token_ratio", "ood_gain", "holdout_gain", "unseen_gain", "evolve_gain", "measured_gain", "n_mechanisms")
    summ = summarize(rows, metrics=metrics)
    pd = pairs(rows, OFF, ON, metrics)
    pd50 = pairs([r for r in rows if r["seed"] < 50], OFF, ON, ("token_ratio", "ood_gain"))
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
    print({m: ci_pts(v, 1 if m in ("token_ratio", "n_mechanisms") else 100, 3) for m, v in pd.items()})
    print(checks, verdict)


if __name__ == "__main__":
    main()
