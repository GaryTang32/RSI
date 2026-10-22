"""S5c - "Keep only the changes that survive everywhere", literal arm (claims audit L2, retry round 2, P-L2b).

Usage: python experiments/metaharness-solpi/s5c_per_family_power.py [--seeds 20] [--seed-offset 10] [--workers 2]
"""
from __future__ import annotations

import json
from functools import partial

from scipy import stats

from _agentworld import POWER_KEYS, parse_seeded, pf, pooled_capability, power_compare
from _common import RESULTS, fmt, paired, pool_map, save, table
from s5_survive import OFFLINE, job

PROTOS = ("single_env_dual", "multi_per_family")


def pvals(diffs: list) -> dict:
    nz = [d for d in diffs if d != 0]
    t = float(stats.ttest_1samp(diffs, 0).pvalue) if any(diffs) else 1.0
    w = float(stats.wilcoxon(nz).pvalue) if nz else 1.0
    return {"t_p": t, "wilcoxon_p": w, "nonzero_seeds": len(nz)}


def identity_checks(single: dict, multi: dict, seeds: list) -> dict:
    """Seeds whose rows equal s5b's (same seeds): single_env_dual, and multi_aggregate vs per_family."""
    p = RESULTS / "solpi_retry2" / "s5b_diversity_power.json"
    if not p.exists():
        return {}
    old = {(r["protocol"], r["seed"]): r for r in json.loads(p.read_text())["per_seed"]}
    return {"single_env_dual_equals_s5b": sum(
                all(abs(single[s][k] - old[("single_env_dual", s)][k]) < 1e-12 for k in POWER_KEYS) for s in seeds),
            "per_family_equals_s5b_multi_aggregate": sum(
                all(abs(multi[s][k] - old[("multi_aggregate", s)][k]) < 1e-12 for k in POWER_KEYS)
                and multi[s]["frozen"] == old[("multi_aggregate", s)]["frozen"] for s in seeds)}


def main():
    args, seeds = parse_seeded(__doc__.splitlines()[0], default_seeds=20)
    rows = pool_map(partial(job, OFFLINE), [(p, s) for s in seeds for p in PROTOS], args.workers)
    by, cmp, summ, tricks = power_compare(rows, seeds, PROTOS)
    single, multi = by["single_env_dual"], by["multi_per_family"]

    def pooled_of(tag):
        a, b = pooled_capability(single, multi, seeds, tag)
        return paired(a, b), pvals([y - x for x, y in zip(a, b)])

    pooled, pooled_p = pooled_of("nofw")
    pooled_fw, pooled_fw_p = pooled_of("fw")
    no_harm = all(cmp[k]["hi"] >= 0 for k in ("A_nofw_capability", "B_nofw_capability"))
    passed = pooled["lo"] > 0 and no_harm
    ident = identity_checks(single, multi, seeds)
    verdict = (f"P-L2b {'PASS' if passed else 'FAIL'}: held-out capability without firewall, per_family - single "
               f"(mean of backends A and B) {pf(pooled)} (t p={pooled_p['t_p']:.3f}); "
               f"A {pf(cmp['A_nofw_capability'])}, B {pf(cmp['B_nofw_capability'])}; with firewall {pf(pooled_fw)} "
               f"(t p={pooled_fw_p['t_p']:.3f}, Wilcoxon p={pooled_fw_p['wilcoxon_p']:.3f}, "
               f"{pooled_fw_p['nonzero_seeds']} nonzero seeds); tricks admitted single "
               f"{fmt(tricks['single_env_dual'])} vs per_family {fmt(tricks['multi_per_family'])}; identity {ident}")
    print(table([[p] + [fmt(summ[p][k], 3) for k in POWER_KEYS] for p in PROTOS], ["protocol"] + list(POWER_KEYS)))
    print("verdict:", verdict)
    save("s5c_per_family_power", {
        "claim": "L2 (literal arm): keep only the changes that survive everywhere [doc; blog]",
        "config": {"seeds": seeds, "protocols": PROTOS, "prereg": "claims-audit-solpi.md P-L2b"},
        "per_seed": rows, "summary": summ, "paired_per_family_minus_single": cmp, "pooled_nofw": pooled,
        "pooled_nofw_p": pooled_p, "pooled_fw": pooled_fw, "pooled_fw_p": pooled_fw_p, "tricks_admitted": tricks,
        "identity_checks": ident, "p_l2b_pass": passed, "verdict": verdict},
        args.out or str(RESULTS / "solpi_retry2" / "s5c_per_family_power.json"))


if __name__ == "__main__":
    main()
