"""S5b - Does search across diverse environments make the survivors transfer? Powered re-test (claims audit L2,
retry round 2).

Usage: python experiments/metaharness-solpi/s5b_diversity_power.py [--seeds 20] [--seed-offset 10] [--workers 2]
"""
from __future__ import annotations

from functools import partial

from _agentworld import POWER_KEYS, parse_seeded, pf, pooled_capability, power_compare
from _common import fmt, paired, pool_map, save, table
from s5_survive import OFFLINE, job

PROTOS = ("single_env_dual", "multi_aggregate")


def main():
    args, seeds = parse_seeded(__doc__.splitlines()[0], default_seeds=20)
    rows = pool_map(partial(job, OFFLINE), [(p, s) for s in seeds for p in PROTOS], args.workers)
    by, cmp, summ, tricks = power_compare(rows, seeds, PROTOS)
    single, multi = by["single_env_dual"], by["multi_aggregate"]
    pooled = paired(*pooled_capability(single, multi, seeds, "nofw"))
    pooled_fw = paired(*pooled_capability(single, multi, seeds, "fw"))
    no_harm = all(cmp[k]["hi"] >= 0 for k in ("A_nofw_capability", "B_nofw_capability"))
    passed = pooled["lo"] > 0 and no_harm
    verdict = (f"P-L2 {'PASS' if passed else 'FAIL'}: held-out capability without firewall, multi - single "
               f"(mean of backends A and B) {pf(pooled)}; A {pf(cmp['A_nofw_capability'])}, "
               f"B {pf(cmp['B_nofw_capability'])}; with firewall {pf(pooled_fw)}; tricks admitted at the "
               f"training gate single {fmt(tricks['single_env_dual'])} vs multi {fmt(tricks['multi_aggregate'])}")
    print(table([[p] + [fmt(summ[p][k], 3) for k in POWER_KEYS] for p in PROTOS], ["protocol"] + list(POWER_KEYS)))
    print("verdict:", verdict)
    save("s5b_diversity_power", {
        "claim": "L2: search across diverse environments makes the survivors transfer [doc; blog]",
        "config": {"seeds": seeds, "protocols": PROTOS, "prereg": "claims-audit-solpi.md P-L2"},
        "per_seed": rows, "summary": summ, "paired_multi_minus_single": cmp, "pooled_nofw": pooled,
        "pooled_fw": pooled_fw, "tricks_admitted": tricks, "p_l2_pass": passed, "verdict": verdict}, args.out)


if __name__ == "__main__":
    main()
