"""S5c - "Keep only the changes that survive everywhere", literal arm (claims audit L2, retry round 2, P-L2b).

Claim [doc; blog]: "Keep only the changes that survive everywhere": searching across diverse environments makes
the survivors transfer (to held-out tasks and an unseen backend).

P-L2 (s5b) compared ``single_env_dual`` with ``multi_aggregate`` (dual gate on the pooled screen). S5's own
docstring calls ``multi_per_family`` (dual gate in EVERY training family) the literal "survives everywhere" arm,
so this follow-up (preregistered as P-L2b in claims-audit-solpi.md, after P-L2's result was seen) runs that arm
on the same fresh seeds, with P-L2's primary endpoint and pass rule unchanged: the held-out-family (``ood``)
capability ratio of the stack admitted at the training gate WITHOUT the firewall, paired per_family - single,
averaged over backends A and B per seed. ``single_env_dual`` is re-run and checked against s5b's rows.

    python experiments/metaharness-solpi/s5c_per_family_power.py [--seeds 20] [--seed-offset 10] [--workers 2]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import s5_survive as s5  # noqa: E402
from _common import RESULTS, fmt, paired, parse_args, pool_map, save, summarize, table  # noqa: E402

from scipy import stats  # noqa: E402

PROTOS = ("single_env_dual", "multi_per_family")
ARGS = None


def pf(d: dict, p: int = 4) -> str:
    return f"{d['mean_diff']:+.{p}f} [{d['lo']:+.{p}f}, {d['hi']:+.{p}f}]"


def job(spec):
    s5.ARGS = argparse.Namespace(quick=False, live=False, llm="sim")
    return s5.job(spec)


def extra(ap):
    ap.add_argument("--seed-offset", type=int, default=10)


def pvals(diffs):
    nz = [d for d in diffs if d != 0]
    t = float(stats.ttest_1samp(diffs, 0).pvalue) if any(diffs) else 1.0
    w = float(stats.wilcoxon(nz).pvalue) if nz else 1.0
    return {"t_p": t, "wilcoxon_p": w, "nonzero_seeds": len(nz)}


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=20, extra=extra)
    seeds = list(range(ARGS.seed_offset, ARGS.seed_offset + ARGS.seeds))
    rows = pool_map(job, [(p, s) for s in seeds for p in PROTOS], ARGS.workers)
    by = {p: {r["seed"]: r for r in rows if r["protocol"] == p} for p in PROTOS}
    single, multi = by["single_env_dual"], by["multi_per_family"]
    keys = ("A_nofw_capability", "B_nofw_capability", "A_fw_capability", "B_fw_capability", "A_nofw_eta_saving",
            "B_nofw_eta_saving", "A_fw_eta_saving", "B_fw_eta_saving")
    cmp = {k: paired([single[s][k] for s in seeds], [multi[s][k] for s in seeds]) for k in keys}

    def pooled_of(tag):
        a = [(single[s][f"A_{tag}_capability"] + single[s][f"B_{tag}_capability"]) / 2 for s in seeds]
        b = [(multi[s][f"A_{tag}_capability"] + multi[s][f"B_{tag}_capability"]) / 2 for s in seeds]
        return paired(a, b), pvals([y - x for x, y in zip(a, b)])

    pooled, pooled_p = pooled_of("nofw")
    pooled_fw, pooled_fw_p = pooled_of("fw")
    tricks = {p: summarize([by[p][s]["admitted_train"]["trick"] for s in seeds]) for p in PROTOS}
    no_harm = all(cmp[k]["hi"] >= 0 for k in ("A_nofw_capability", "B_nofw_capability"))
    passed = pooled["lo"] > 0 and no_harm
    # determinism / identity checks against s5b (same seeds)
    ident = {}
    s5b_path = RESULTS / "solpi_retry2" / "s5b_diversity_power.json"
    if s5b_path.exists():
        s5b = json.loads(s5b_path.read_text())
        old = {(r["protocol"], r["seed"]): r for r in s5b["per_seed"]}
        ident["single_env_dual_equals_s5b"] = sum(
            all(abs(single[s][k] - old[("single_env_dual", s)][k]) < 1e-12 for k in keys) for s in seeds)
        ident["per_family_equals_s5b_multi_aggregate"] = sum(
            all(abs(multi[s][k] - old[("multi_aggregate", s)][k]) < 1e-12 for k in keys)
            and multi[s]["frozen"] == old[("multi_aggregate", s)]["frozen"] for s in seeds)
    summ = {p: {k: summarize([by[p][s][k] for s in seeds]) for k in keys} for p in PROTOS}
    verdict = (f"P-L2b {'PASS' if passed else 'FAIL'}: held-out capability without firewall, per_family - single "
               f"(mean of backends A and B) {pf(pooled)} (t p={pooled_p['t_p']:.3f}); "
               f"A {pf(cmp['A_nofw_capability'])}, B {pf(cmp['B_nofw_capability'])}; with firewall {pf(pooled_fw)} "
               f"(t p={pooled_fw_p['t_p']:.3f}, Wilcoxon p={pooled_fw_p['wilcoxon_p']:.3f}, "
               f"{pooled_fw_p['nonzero_seeds']} nonzero seeds); tricks admitted single "
               f"{fmt(tricks['single_env_dual'])} vs per_family {fmt(tricks['multi_per_family'])}; identity {ident}")
    print(table([[p] + [fmt(summ[p][k], 3) for k in keys] for p in PROTOS], ["protocol"] + list(keys)))
    print("verdict:", verdict)
    save("s5c_per_family_power", {
        "claim": "L2 (literal arm): keep only the changes that survive everywhere [doc; blog]",
        "config": {"seeds": seeds, "protocols": PROTOS, "prereg": "claims-audit-solpi.md P-L2b"},
        "per_seed": rows, "summary": summ, "paired_per_family_minus_single": cmp, "pooled_nofw": pooled,
        "pooled_nofw_p": pooled_p, "pooled_fw": pooled_fw, "pooled_fw_p": pooled_fw_p, "tricks_admitted": tricks,
        "identity_checks": ident, "p_l2b_pass": passed, "verdict": verdict},
        ARGS.out or str(RESULTS / "solpi_retry2" / "s5c_per_family_power.json"))


if __name__ == "__main__":
    main()
