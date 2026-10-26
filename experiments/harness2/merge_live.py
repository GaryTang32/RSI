"""Combine per-arm live files (results/harness2/live_k*.json) into compare_live.json with paired comparisons."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from rsi.core.stats import bootstrap_ci, paired_diff_ci  # noqa: E402

RES = ROOT / "results" / "harness2"
arms = {}
for f in sorted(RES.glob("live_k*.json")):
    arms.update(json.loads(f.read_text())["arms"])
ids = sorted(set.intersection(*(set(v["per_task"]) for v in arms.values())))
first = next(iter(arms.values()))
seed = [first["per_task"][t]["base"] for t in ids]
summary = {"seed (H0 base1)": bootstrap_ci(seed)}
for name, v in arms.items():
    sub = [v["per_task"][t]["submitted"] for t in ids]
    summary[name] = {"acc": bootstrap_ci(sub), "vs_seed": paired_diff_ci(seed, sub)}
if {"harness2_k1", "harness2_k3"} <= set(arms):
    summary["k3_minus_k1"] = paired_diff_ci([arms["harness2_k1"]["per_task"][t]["submitted"] for t in ids],
                                            [arms["harness2_k3"]["per_task"][t]["submitted"] for t in ids])
out = {"experiment": "harness2_compare_live", "n_common": len(ids), "total_usd": round(sum(v["usd"] for v in arms.values()), 4),
       "summary": summary, "arms": arms}
(RES / "compare_live.json").write_text(json.dumps(out, indent=1, default=str))
print(json.dumps({"n_common": len(ids), "total_usd": out["total_usd"], "summary": summary}, indent=1, default=str))
