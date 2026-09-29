"""A1 - Is dreaming "almost free" at the paper's scale? (claims audit L2, retry round 2; analysis, $0)

The developer's share of the LLM bill was 14-30% in our live runs, but those runs spent only 6-9 agent calls per
round, while the paper's Dream arm spends 9-87 (Pro) and 254-350 (Flash) agent calls per round after round 1
(exact Fig. 3b data). Preregistered computation (claims-audit §6): measured $ per agent call and $ per developer
request from the four recorded live runs (pooled), one request per revision (measured in the runs that record
revisions), M - 1 = 3 revisions per dreaming phase, and the paper's mean Dream agent calls per round after
round 1: Pro (317 - 110) / 4, Flash (1879 - 640) / 4. "Almost free" iff the developer's share of a phase is
<= 10% for both models. Also reported: the per-run spread of the cost ratio, the smallest paper round, the whole
run, and 1000 LLM-written candidates per phase ("a massive pool").

    python experiments/dream-rsi/a1_developer_share.py
"""
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


def main():
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
