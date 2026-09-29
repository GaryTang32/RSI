"""A1 - Is dreaming "almost free" at the paper's scale? (claims audit L2, retry round 2; analysis, $0)

The developer's share of the LLM bill was 14-30% in our live runs, but those runs spent only 6-9 agent calls per
round, while the paper's Dream arm spends 9-87 (Pro) and 254-350 (Flash) agent calls per round after round 1
(exact Fig. 3b data). Preregistered computation (claims-audit §6): measured $ per agent call and $ per developer
request from the four recorded live runs (pooled), one request per revision (measured in the runs that record
revisions), M - 1 = 3 revisions per dreaming phase, and the paper's mean Dream agent calls per round after
round 1: Pro (317 - 110) / 4, Flash (1879 - 640) / 4. "Almost free" iff the developer's share of a phase is
<= 10% for both models. Also reported: the per-run spread of the cost ratio, the smallest paper round, the whole
run, and 1000 LLM-written candidates per phase ("a massive pool").

    python experiments/dream-rsi/a1_developer_share.py            # A1 (constant cost per request)
    python experiments/dream-rsi/a1_developer_share.py --growth   # A1b (review round, claims-audit §6.1)

A1b drops A1's constant-cost assumption. Our developer prompt carries every trajectory and trace, so its input
grows with the replay pool. Token prices a ($/input token) and b ($/output token) are fitted by least squares
on the 8 measured (run, role) rows. Model (i): constant cost (A1). Model (ii, preregistered): developer input
tokens per request proportional to the recorded nodes in the pool, anchored on each measured run's pool size at
its dreaming phases; output tokens and the agent's cost constant. Model (iii): the agent's input tokens also
proportional to the pool. Not preregistered, reported as a sensitivity: model (ii') with an affine fit
in = c0 + c1 * pool over the 4 runs instead of a pure proportion. The pool at the phase after paper round r is the
paper's cumulative Dream calls through r; that phase's agent calls are round r + 1's.
"""
import argparse
import json
from pathlib import Path

from _common import RESULTS, ROOT, save

RUNS = {"sumdiff_live": ROOT / "validation/dream-rsi/sumdiff_live/summary.json",
        "sumdiff_live_b": ROOT / "validation/dream-rsi/sumdiff_live_b/summary.json",
        "sumdiff_live_c": ROOT / "validation/dream-rsi/sumdiff_live_c/summary.json"}
PAPER_ROUNDS = {"pro": [110, 119 - 110, 147 - 119, 234 - 147, 317 - 234],
                "flash": [640, 976 - 640, 1230 - 976, 1529 - 1230, 1879 - 1529]}


def share(dev_usd_per_rev, agent_usd_per_call, revisions, agent_calls):
    d = revisions * dev_usd_per_rev
    return d / (d + agent_calls * agent_usd_per_call)


# recorded nodes in the replay pool at each run's dreaming phases (trace.jsonl "state" world_sizes / the smoke's
# per-round N): sumdiff_live phases at pools 12 and 24 (mean 18); _b and _c one phase at 9; the smoke one at 6.
POOL_AT_DREAMING = {"sumdiff_live": 18.0, "sumdiff_live_b": 9.0, "sumdiff_live_c": 9.0, "live_smoke": 6.0}


def usage_rows():
    out = {}
    for name, p in RUNS.items():
        u = json.loads(Path(p).read_text())["usage"]
        out[name] = {r: u[r] for r in ("agent", "developer")}
    u = json.loads((RESULTS / "live_smoke.json").read_text())["summary"]["usage_by_role"]
    out["live_smoke"] = {r: u[r] for r in ("agent", "developer")}
    return out


def growth():
    import numpy as np

    rows = usage_rows()
    A = np.array([[rows[n][r]["input_tokens"], rows[n][r]["output_tokens"]] for n in rows for r in ("agent", "developer")],
                 dtype=float)
    c = np.array([rows[n][r]["cost_usd"] for n in rows for r in ("agent", "developer")])
    (pa, pb), *_ = np.linalg.lstsq(A, c, rcond=None)
    fit_err = float(np.max(np.abs(A @ [pa, pb] - c) / c))
    per = {}
    for n, r in rows.items():
        d, g = r["developer"], r["agent"]
        per[n] = {"pool": POOL_AT_DREAMING[n], "dev_in_per_req": d["input_tokens"] / d["calls"],
                  "dev_out_per_req": d["output_tokens"] / d["calls"], "agent_in_per_call": g["input_tokens"] / g["calls"],
                  "agent_out_per_call": g["output_tokens"] / g["calls"]}
    tot_dev = sum(r["developer"]["calls"] for r in rows.values())
    tot_ag = sum(r["agent"]["calls"] for r in rows.values())
    dev_in_per_node = sum(per[n]["dev_in_per_req"] / per[n]["pool"] * rows[n]["developer"]["calls"] for n in rows) / tot_dev
    ag_in_per_node = sum(per[n]["agent_in_per_call"] / per[n]["pool"] * rows[n]["agent"]["calls"] for n in rows) / tot_ag
    dev_out = sum(r["developer"]["output_tokens"] for r in rows.values()) / tot_dev
    ag_out = sum(r["agent"]["output_tokens"] for r in rows.values()) / tot_ag
    dev_in0 = sum(r["developer"]["input_tokens"] for r in rows.values()) / tot_dev
    ag_in0 = sum(r["agent"]["input_tokens"] for r in rows.values()) / tot_ag
    pools = np.array([per[n]["pool"] for n in rows])
    c1, c0 = np.polyfit(pools, np.array([per[n]["dev_in_per_req"] for n in rows]), 1)
    res = {}
    for model, calls in PAPER_ROUNDS.items():
        cum = np.cumsum(calls)
        phases = []
        for r in range(1, len(calls)):
            P, nxt = float(cum[r - 1]), calls[r]
            dev_const = pa * dev_in0 + pb * dev_out
            ag_const = pa * ag_in0 + pb * ag_out
            dev_prop = pa * dev_in_per_node * P + pb * dev_out
            dev_aff = pa * max(c0 + c1 * P, 0.0) + pb * dev_out
            ag_prop = pa * ag_in_per_node * P + pb * ag_out
            sh = lambda dv, ag: 3 * dv / (3 * dv + nxt * ag)  # noqa: E731
            phases.append({"after_round": r, "pool_nodes": P, "next_round_agent_calls": nxt,
                           "i_constant": sh(dev_const, ag_const), "ii_dev_grows": sh(dev_prop, ag_const),
                           "iii_both_grow": sh(dev_prop, ag_prop), "ii_affine_sensitivity": sh(dev_aff, ag_const),
                           "dev_input_tokens_per_request_ii": dev_in_per_node * P})
        res[model] = {"phases": phases,
                      "phases_above_10pct": {k: sum(ph[k] > 0.10 for ph in phases) for k in
                                             ("i_constant", "ii_dev_grows", "iii_both_grow", "ii_affine_sensitivity")},
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
    rows = {}
    for name, p in RUNS.items():
        c = json.loads(Path(p).read_text())["usage"]["_cost"]
        rows[name] = {"agent_calls": c["agent_calls"], "agent_usd": c["agent_usd"],
                      "developer_calls": c["developer_calls"], "developer_usd": c["developer_usd"],
                      "developer_revisions": c.get("developer_revisions")}
    sm = json.loads((RESULTS / "live_smoke.json").read_text())["summary"]["cost"]
    rows["live_smoke"] = {"agent_calls": sm["agent_calls"], "agent_usd": sm["agent_usd"],
                          "developer_calls": sm["developer_calls"], "developer_usd": sm["developer_usd"],
                          "developer_revisions": sm.get("developer_revisions")}
    a = sum(r["agent_usd"] for r in rows.values()) / sum(r["agent_calls"] for r in rows.values())
    d = sum(r["developer_usd"] for r in rows.values()) / sum(r["developer_calls"] for r in rows.values())
    per_run_ratio = {n: (r["developer_usd"] / r["developer_calls"]) / (r["agent_usd"] / r["agent_calls"])
                     for n, r in rows.items()}
    out = {"usd_per_agent_call": a, "usd_per_developer_request": d, "developer_over_agent_cost_ratio": d / a,
           "per_run_ratio": per_run_ratio, "requests_per_revision_where_recorded":
               {n: r["developer_calls"] / r["developer_revisions"] for n, r in rows.items() if r["developer_revisions"]}}
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
        print(f"[{m}] per-phase developer share {100 * v['share_per_phase_M4']:.1f}% (cost-ratio range "
              f"{100 * v['share_per_phase_M4_ratio_range'][0]:.1f}-{100 * v['share_per_phase_M4_ratio_range'][1]:.1f}%),"
              f" smallest round {100 * v['share_smallest_round_M4']:.1f}%, whole run {100 * v['share_whole_run_M4']:.1f}%,"
              f" 1000 candidates {100 * v['share_per_phase_1000_candidates']:.1f}%")
    save("a1_developer_share", {"config": {"runs": list(rows), "revisions_per_phase": 3,
                                           "paper_rounds": PAPER_ROUNDS}, "runs": rows, "results": out})


if __name__ == "__main__":
    main()
