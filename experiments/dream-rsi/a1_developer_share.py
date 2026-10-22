"""A1: is dreaming "almost free" at the paper's scale? The developer's share of the LLM bill from recorded live runs.

    python experiments/dream-rsi/a1_developer_share.py [--growth]   # --growth: A1b, developer input grows with the pool
"""
import argparse
import json

import numpy as np
from _common import RESULTS, ROOT, save

RUNS = {n: ROOT / f"validation/dream-rsi/{n}/summary.json"
        for n in ("sumdiff_live", "sumdiff_live_b", "sumdiff_live_c")}
PAPER_ROUNDS = {"pro": [110, 119 - 110, 147 - 119, 234 - 147, 317 - 234],  # exact Fig. 3b cumulative calls
                "flash": [640, 976 - 640, 1230 - 976, 1529 - 1230, 1879 - 1529]}
# recorded nodes in the replay pool at each run's dreaming phases (sumdiff_live: pools 12 and 24)
POOL_AT_DREAMING = {"sumdiff_live": 18.0, "sumdiff_live_b": 9.0, "sumdiff_live_c": 9.0, "live_smoke": 6.0}
ROLES = ("agent", "developer")
MODELS = ("i_constant", "ii_dev_grows", "iii_both_grow", "ii_affine_sensitivity")
COST_KEYS = ("agent_calls", "agent_usd", "developer_calls", "developer_usd")


def share(dev_usd_per_rev, agent_usd_per_call, revisions, agent_calls):
    d = revisions * dev_usd_per_rev
    return d / (d + agent_calls * agent_usd_per_call)


def usage_rows():
    out = {n: json.loads(p.read_text())["usage"] for n, p in RUNS.items()}
    out["live_smoke"] = json.loads((RESULTS / "live_smoke.json").read_text())["summary"]["usage_by_role"]
    return {n: {r: u[r] for r in ROLES} for n, u in out.items()}


def growth():
    """A1b: token prices fitted on the measured rows; input tokens constant or proportional to the replay pool."""
    rows = usage_rows()
    A = np.array([[rows[n][r]["input_tokens"], rows[n][r]["output_tokens"]] for n in rows for r in ROLES], dtype=float)
    c = np.array([rows[n][r]["cost_usd"] for n in rows for r in ROLES])
    (pa, pb), *_ = np.linalg.lstsq(A, c, rcond=None)
    fit_err = float(np.max(np.abs(A @ [pa, pb] - c) / c))
    per = {}
    for n, r in rows.items():
        d, g = r["developer"], r["agent"]
        per[n] = {"pool": POOL_AT_DREAMING[n], "dev_in_per_req": d["input_tokens"] / d["calls"],
                  "dev_out_per_req": d["output_tokens"] / d["calls"],
                  "agent_in_per_call": g["input_tokens"] / g["calls"],
                  "agent_out_per_call": g["output_tokens"] / g["calls"]}
    calls = {r: sum(x[r]["calls"] for x in rows.values()) for r in ROLES}

    def mean(role, key):
        return sum(x[role][key] for x in rows.values()) / calls[role]

    def in_per_node(role, key):
        return sum(per[n][key] / per[n]["pool"] * rows[n][role]["calls"] for n in rows) / calls[role]

    dev_in_per_node = in_per_node("developer", "dev_in_per_req")
    ag_in_per_node = in_per_node("agent", "agent_in_per_call")
    dev_out, ag_out = mean("developer", "output_tokens"), mean("agent", "output_tokens")
    dev_in0, ag_in0 = mean("developer", "input_tokens"), mean("agent", "input_tokens")
    c1, c0 = np.polyfit(np.array([per[n]["pool"] for n in rows]), np.array([per[n]["dev_in_per_req"] for n in rows]), 1)
    dev_const, ag_const = pa * dev_in0 + pb * dev_out, pa * ag_in0 + pb * ag_out
    res = {}
    for model, calls_r in PAPER_ROUNDS.items():
        cum = np.cumsum(calls_r)
        phases = []
        for r in range(1, len(calls_r)):  # the pool after paper round r; that phase's agent calls are round r + 1's
            P, nxt = float(cum[r - 1]), calls_r[r]
            dev_prop = pa * dev_in_per_node * P + pb * dev_out
            dev_aff = pa * max(c0 + c1 * P, 0.0) + pb * dev_out
            ag_prop = pa * ag_in_per_node * P + pb * ag_out
            phases.append({"after_round": r, "pool_nodes": P, "next_round_agent_calls": nxt,
                           "i_constant": share(dev_const, ag_const, 3, nxt),
                           "ii_dev_grows": share(dev_prop, ag_const, 3, nxt),
                           "iii_both_grow": share(dev_prop, ag_prop, 3, nxt),
                           "ii_affine_sensitivity": share(dev_aff, ag_const, 3, nxt),
                           "dev_input_tokens_per_request_ii": dev_in_per_node * P})
        res[model] = {"phases": phases, "phases_above_10pct": {k: sum(ph[k] > 0.10 for ph in phases) for k in MODELS},
                      "n_phases": len(phases)}
        for ph in phases:
            print(f"[{model} phase after round {ph['after_round']}] pool {ph['pool_nodes']:.0f}, next round "
                  f"{ph['next_round_agent_calls']} calls: (i) {100 * ph['i_constant']:.1f}%  (ii) "
                  f"{100 * ph['ii_dev_grows']:.1f}%  (iii) {100 * ph['iii_both_grow']:.1f}%  (ii') "
                  f"{100 * ph['ii_affine_sensitivity']:.1f}%")
    save("a1b_developer_share_growth", {
        "config": {"pool_at_dreaming": POOL_AT_DREAMING, "revisions_per_phase": 3, "paper_rounds": PAPER_ROUNDS},
        "prices": {"usd_per_input_token": pa, "usd_per_output_token": pb, "max_rel_fit_error": fit_err},
        "per_run": per, "anchors": {"dev_input_tokens_per_node": dev_in_per_node, "agent_input_tokens_per_node":
                                    ag_in_per_node, "dev_output_tokens": dev_out, "agent_output_tokens": ag_out,
                                    "affine_dev_input": {"c0": c0, "c1": c1}},
        "results": res})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--growth", action="store_true")
    if ap.parse_args().growth:
        return growth()
    costs = {n: json.loads(p.read_text())["usage"]["_cost"] for n, p in RUNS.items()}
    costs["live_smoke"] = json.loads((RESULTS / "live_smoke.json").read_text())["summary"]["cost"]
    rows = {n: {**{k: c[k] for k in COST_KEYS}, "developer_revisions": c.get("developer_revisions")}
            for n, c in costs.items()}
    a = sum(r["agent_usd"] for r in rows.values()) / sum(r["agent_calls"] for r in rows.values())
    d = sum(r["developer_usd"] for r in rows.values()) / sum(r["developer_calls"] for r in rows.values())
    per_run_ratio = {n: (r["developer_usd"] / r["developer_calls"]) / (r["agent_usd"] / r["agent_calls"])
                     for n, r in rows.items()}
    per_rev = {n: r["developer_calls"] / r["developer_revisions"] for n, r in rows.items() if r["developer_revisions"]}
    out = {"usd_per_agent_call": a, "usd_per_developer_request": d, "developer_over_agent_cost_ratio": d / a,
           "per_run_ratio": per_run_ratio, "requests_per_revision_where_recorded": per_rev}
    res = {}
    for model, calls in PAPER_ROUNDS.items():
        later = calls[1:]
        mean_calls = sum(later) / len(later)
        res[model] = {"dream_calls_per_round_after_1": later, "mean": mean_calls,
                      "share_per_phase_M4": share(d, a, 3, mean_calls),
                      "share_per_phase_M4_ratio_range": [share(r * a, a, 3, mean_calls) for r in
                                                         (min(per_run_ratio.values()), max(per_run_ratio.values()))],
                      "share_smallest_round_M4": share(d, a, 3, min(later)),
                      "share_whole_run_M4": share(d, a, 3 * len(later), sum(calls)),
                      "share_per_phase_1000_candidates": share(d, a, 1000, mean_calls)}
        res[model]["almost_free"] = res[model]["share_per_phase_M4"] <= 0.10
    out["paper_scale"] = res
    out["holds_for_both"] = all(v["almost_free"] for v in res.values())
    for m, v in res.items():
        lo, hi = v["share_per_phase_M4_ratio_range"]
        print(f"[{m}] per-phase developer share {100 * v['share_per_phase_M4']:.1f}% (cost-ratio range "
              f"{100 * lo:.1f}-{100 * hi:.1f}%), smallest round {100 * v['share_smallest_round_M4']:.1f}%, "
              f"whole run {100 * v['share_whole_run_M4']:.1f}%, 1000 candidates "
              f"{100 * v['share_per_phase_1000_candidates']:.1f}%")
    save("a1_developer_share", {"config": {"runs": list(rows), "revisions_per_phase": 3,
                                           "paper_rounds": PAPER_ROUNDS}, "runs": rows, "results": out})


if __name__ == "__main__":
    main()
