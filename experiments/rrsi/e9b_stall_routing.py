"""E9b (retry round 2, preregistered P9 in docs/methods/rrsi/claims-audit.md section 6): claim L20.

"Forced variety when stuck: a stall inside the band sends budget to untouched components."
Measured directly: in every round with sigma_t = 1 and U_t non-empty (sigma_t recomputed with the
loop's own formula for the exploration-off arm, whose directives never set it), did at least one
EVALUATED candidate carry an edit on a component of U_t? Per run: the share of such stalled rounds.
Worlds and proposers are E9's two setups (strict collapse = the spec's premise; mild collapse),
exploration on vs off, worlds 0-59, T = 20. Also coverage and the true evolve gain (plateau escape).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import paired, pmap, run_hw, save, summarize  # noqa: E402
from e9_exploration import SETUPS  # noqa: E402

from rsi.domains.harnessworld.world import K  # noqa: E402
from rsi.rrsi import RegularizerSwitches  # noqa: E402
from rsi.rrsi.history import stall_flag  # noqa: E402

ARMS = {"on": RegularizerSwitches.full(),
        "off": RegularizerSwitches.full().but(stall_exploration=False, name="no_explore")}


def stall_routing(out: Path, w: int = 3) -> dict:
    fr = json.loads((out / "frontier.json").read_text())
    traj = [x["S"] for x in fr["trajectory"]]
    recs = [json.loads(l) for l in (out / "history.jsonl").read_text().splitlines() if l.strip()]
    n_stall = n_routed = 0
    for rdir in sorted(out.glob("r*/directives.json"), key=lambda p: int(p.parent.name[1:])):
        d = json.loads(rdir.read_text())
        t = d["t"]
        sigma = stall_flag(traj[: t + 1], t, w, d["delta"])
        untried = [c for c in K if c not in set(d["tried"])]
        if not (sigma and untried):
            continue
        n_stall += 1
        comps = {r.get("component") for r in recs if r.get("t") == t and r.get("edit_id")
                 and r.get("delta_S") is not None}
        n_routed += bool(comps & set(untried))
    return {"stalled_rounds": n_stall, "routed_rounds": n_routed,
            "routed_share": (n_routed / n_stall) if n_stall else None}


def one(job: dict) -> dict:
    m = run_hw({**job, "keep": True})
    out = Path(m["out_dir"])
    m.update(stall_routing(out))
    shutil.rmtree(out, ignore_errors=True)
    m.pop("curve", None)
    m.pop("out_dir", None)
    return m


def main():
    ap = argparse.ArgumentParser(description="E9b: where does budget go when the run stalls?")
    ap.add_argument("--seeds", type=int, default=60)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--T", type=int, default=20)
    a = ap.parse_args()
    jobs = [{"seed": s, "arm": sw, "label": f"{setup}|{lab}", "cfg": {"T": a.T}, "world": world, "proposer": prop}
            for setup, (world, prop) in SETUPS.items() for s in range(a.seeds) for lab, sw in ARMS.items()]
    rows = pmap(one, jobs, a.workers)
    metrics = ("routed_share", "stalled_rounds", "coverage", "accepted_structural", "evolve_gain", "ood_gain")
    summ = summarize(rows, metrics=metrics)
    pd, checks = {}, {}
    for setup in SETUPS:
        # the share is defined only for runs with >= 1 stalled round; pair the seeds where both arms have one
        on = {r["seed"]: r for r in rows if r["label"] == f"{setup}|on"}
        off = {r["seed"]: r for r in rows if r["label"] == f"{setup}|off"}
        both = [s for s in on if on[s]["routed_share"] is not None and off[s]["routed_share"] is not None]
        sub = [dict(on[s], label="on") for s in both] + [dict(off[s], label="off") for s in both]
        pd[setup] = {"routed_share": {**paired(sub, "off", "on", "routed_share"), "n_paired_seeds": len(both)},
                     **{m: paired(rows, f"{setup}|off", f"{setup}|on", m)
                        for m in ("coverage", "evolve_gain", "ood_gain", "accepted_structural")}}
        checks[f"{setup}: on-arm routed share >= 80%"] = summ[f"{setup}|on"]["routed_share"]["mean"] >= 0.80
        checks[f"{setup}: routed share on - off CI > 0"] = pd[setup]["routed_share"]["lo"] > 0
    checks["strict (the spec's premise): true evolve on - off CI > 0"] = pd["strict"]["evolve_gain"]["lo"] > 0
    routing = all(v for k, v in checks.items() if "routed" in k)
    verdict = ("REPRODUCED" if routing and checks["strict (the spec's premise): true evolve on - off CI > 0"]
               else "PARTIAL" if routing else "NOT REPRODUCED")
    out = {"experiment": "E9b stall routing (retry round 2: P9)", "config": {"seeds": a.seeds, "T": a.T,
                                                                          "setups": SETUPS},
           "summary": summ, "paired_on_minus_off": pd, "checks": checks, "verdict": verdict, "rows": rows}
    save("e9b_stall_routing", out)
    for lab, s in summ.items():
        print(lab, {m: round(s[m]["mean"], 3) for m in metrics if s[m]["n"]})
    for setup, v in pd.items():
        print(setup, {m: (round(x["mean_diff"], 3), round(x["lo"], 3), round(x["hi"], 3)) for m, x in v.items()})
    print(checks, verdict)


if __name__ == "__main__":
    main()
