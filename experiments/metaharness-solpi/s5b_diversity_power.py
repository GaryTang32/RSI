"""S5b - Does search across diverse environments make the survivors transfer? Powered re-test (claims audit L2,
retry round 2).

Claim [doc; blog]: "Keep only the changes that survive everywhere": searching across diverse environments makes
the survivors transfer (to held-out tasks and an unseen backend).

S5 (seeds 0-4) compared the multi-family dual gate with a single-environment dual gate (same capability floor)
and found no significant held-out difference; a power analysis on S5's own per-seed data (claims file, Retry
round 2) shows 5 seeds had ~50% power for the backend-B effect it hinted at (+0.037, paired SD 0.040). Here:
the same two protocols (S5's ``job``, unchanged), FRESH seeds (default 10-29), and the preregistered primary
endpoint P-L2: the held-out-family (``ood``) capability ratio of the stack admitted at the training gate
WITHOUT the firewall (so the environments, not the firewall, do the filtering), paired multi - single, averaged
over the search backend A and the unseen backend B. Secondary: the same with the firewall; tricks admitted.

    python experiments/metaharness-solpi/s5b_diversity_power.py [--seeds 20] [--seed-offset 10] [--workers 2]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import s5_survive as s5  # noqa: E402
from _common import fmt, paired, parse_args, pool_map, save, summarize, table  # noqa: E402

PROTOS = ("single_env_dual", "multi_aggregate")
ARGS = None


def pf(d: dict, p: int = 4) -> str:
    """format a paired-difference dict (``mean_diff`` + CI)."""
    return f"{d['mean_diff']:+.{p}f} [{d['lo']:+.{p}f}, {d['hi']:+.{p}f}]"


def job(spec):
    s5.ARGS = argparse.Namespace(quick=False, live=False, llm="sim")
    return s5.job(spec)


def extra(ap):
    ap.add_argument("--seed-offset", type=int, default=10)


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=20, extra=extra)
    seeds = list(range(ARGS.seed_offset, ARGS.seed_offset + ARGS.seeds))
    rows = pool_map(job, [(p, s) for s in seeds for p in PROTOS], ARGS.workers)
    by = {p: {r["seed"]: r for r in rows if r["protocol"] == p} for p in PROTOS}
    single, multi = by["single_env_dual"], by["multi_aggregate"]
    keys = ("A_nofw_capability", "B_nofw_capability", "A_fw_capability", "B_fw_capability", "A_nofw_eta_saving",
            "B_nofw_eta_saving", "A_fw_eta_saving", "B_fw_eta_saving")
    cmp = {k: paired([single[s][k] for s in seeds], [multi[s][k] for s in seeds]) for k in keys}
    pooled = paired([(single[s]["A_nofw_capability"] + single[s]["B_nofw_capability"]) / 2 for s in seeds],
                    [(multi[s]["A_nofw_capability"] + multi[s]["B_nofw_capability"]) / 2 for s in seeds])
    pooled_fw = paired([(single[s]["A_fw_capability"] + single[s]["B_fw_capability"]) / 2 for s in seeds],
                       [(multi[s]["A_fw_capability"] + multi[s]["B_fw_capability"]) / 2 for s in seeds])
    tricks = {p: summarize([by[p][s]["admitted_train"]["trick"] for s in seeds]) for p in PROTOS}
    no_harm = all(cmp[k]["hi"] >= 0 for k in ("A_nofw_capability", "B_nofw_capability"))
    passed = pooled["lo"] > 0 and no_harm
    summ = {p: {k: summarize([by[p][s][k] for s in seeds]) for k in keys} for p in PROTOS}
    verdict = (f"P-L2 {'PASS' if passed else 'FAIL'}: held-out capability without firewall, multi - single "
               f"(mean of backends A and B) {pf(pooled)}; A {pf(cmp['A_nofw_capability'])}, "
               f"B {pf(cmp['B_nofw_capability'])}; with firewall {pf(pooled_fw)}; tricks admitted at the "
               f"training gate single {fmt(tricks['single_env_dual'])} vs multi {fmt(tricks['multi_aggregate'])}")
    print(table([[p] + [fmt(summ[p][k], 3) for k in keys] for p in PROTOS], ["protocol"] + list(keys)))
    print("verdict:", verdict)
    save("s5b_diversity_power", {
        "claim": "L2: search across diverse environments makes the survivors transfer [doc; blog]",
        "config": {"seeds": seeds, "protocols": PROTOS, "prereg": "claims-audit-solpi.md P-L2"},
        "per_seed": rows, "summary": summ, "paired_multi_minus_single": cmp, "pooled_nofw": pooled,
        "pooled_fw": pooled_fw, "tricks_admitted": tricks, "p_l2_pass": passed, "verdict": verdict}, ARGS.out)


if __name__ == "__main__":
    main()
