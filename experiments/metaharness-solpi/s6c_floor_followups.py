"""S6c - Follow-ups to S6b (claims audit R7 and L4, retry round 2; preregistered as P-R7b / P-L4b).

Usage: python experiments/metaharness-solpi/s6c_floor_followups.py [--seeds 20] [--seed-offset 10] [--workers 2]
"""
from __future__ import annotations

import json
import time

from scipy import stats

from _agentworld import IDEAS, frozen_variants, kind_counts, metrics, parse_seeded
from _common import RESULTS, fmt, fresh_dir, pool_map, save, summarize, table
from rsi.domains.agentworld import MockAgentLLM, make_domain
from rsi.solpi import Config, GateSpec, run
from s6b_floor_fresh_seeds import TOL, is_union, score_change, stack_vs_base

EXPS = {  # name: (make_domain kwargs, firewall)
    "r7b_firewall": (dict(n_train=8, n_final=8, n_accept=6, n_test=8), True),
    "l4b_big_screen": (dict(n_train=24, n_final=24, n_accept=2, n_test=8), False),
}
WIDE_DO_LESS = {"P14", "P20", "T3", "T7"}  # blog: stopping early, skipping verification, removing evidence


def job(spec):
    exp, seed = spec
    t0 = time.time()
    kw, fw = EXPS[exp]
    dom = make_domain(seed=seed, **kw)
    res = run(dom, dom.seed_artifact(), llm_task=MockAgentLLM("A"),
              config=Config(gate=GateSpec(mode="aggregate"), firewall=fw, n_lineages=10),
              out_dir=fresh_dir("s6c", f"{exp}_s{seed}"))
    r = res.meta["rounds"][0]
    llm = MockAgentLLM("A")
    base = dom.seed_artifact()
    surv = list(r["survivor_ideas"])
    variants = frozen_variants(dom, r)
    out = {"exp": exp, "seed": seed, "firewall": fw, "domain": kw, "frozen": list(r["frozen_ideas"]),
           "survivors": surv, "variants": {k: variants[k][1] for k in surv},
           "do_less_admitted": kind_counts(surv)["do_less"],
           "wide_do_less_admitted": sum(i in WIDE_DO_LESS for i in surv),
           "admitted_kinds": {i: IDEAS[i].kind for i in surv}}
    b = {sp: metrics(dom, llm, base, splits=(sp,)) for sp in ("evolve", "test")}
    c = {sp: metrics(dom, llm, res.best, splits=(sp,)) for sp in ("evolve", "test")}
    out |= stack_vs_base(b, c, token_saving=False)
    out["composed_is_union"] = is_union(res.best, surv)
    out["standalone"] = {i: score_change(dom, llm, variants[i][0], b) for i in surv}
    out["seconds"] = time.time() - t0
    return out


def mcnemar_exact(a: list, b: list) -> float:
    """Exact two-sided McNemar test on paired booleans."""
    n01 = sum((not x) and y for x, y in zip(a, b))
    n10 = sum(x and (not y) for x, y in zip(a, b))
    n = n01 + n10
    return 1.0 if n == 0 else float(stats.binomtest(n01, n, 0.5).pvalue)


def survivor_counts(dg: list[dict]) -> dict:
    return {i: sum(i in r["survivors"] for r in dg) for i in IDEAS}


def r7b(dg: list[dict], n: int) -> tuple[dict, str]:
    small = [r for r in dg if any(-TOL <= v["evolve"] < 0 for v in r["standalone"].values())]
    accum = [r for r in small
             if r["evolve_stack_change"] < min(v["evolve"] for v in r["standalone"].values()) - 0.005]
    nonzero_pairs = [(r["seed"], i, v["evolve"]) for r in dg for i, v in r["standalone"].items() if v["evolve"] != 0]
    union = sum(r["composed_is_union"] for r in dg)
    retention = summarize([r["evolve_stack_score"] / r["evolve_base_score"] for r in dg])
    tricks = summarize([sum(IDEAS[i].kind == "trick" for i in r["survivors"]) for r in dg])
    passed = len(small) >= 3 and len(accum) >= 3
    report = {"seeds_with_permitted_small_loss": [r["seed"] for r in small],
              "accumulation_seeds": [r["seed"] for r in accum],
              "nonzero_standalone_evolve_pairs": nonzero_pairs,
              "union": f"{union}/{n}", "retention_evolve": retention, "tricks_admitted": tricks,
              "composed_evolve_change": summarize([r["evolve_stack_change"] for r in dg]),
              "composed_test_change": summarize([r["test_stack_change"] for r in dg]),
              "composed_cost_saving": summarize([r["evolve_stack_cost_saving"] for r in dg]),
              "survivor_counts": survivor_counts(dg), "pass": passed}
    return report, (f"P-R7b {'PASS' if passed else 'FAIL'}: seeds with a permitted nonzero standalone loss "
                    f"{len(small)}/{n}; accumulation {len(accum)}/{n}; union {union}/{n}; retention "
                    f"{fmt(retention)}; tricks admitted {fmt(tricks)}")


def l4b(dg: list[dict], seeds: list) -> tuple[dict, str]:
    n = len(seeds)
    l4_1 = sum(r["do_less_admitted"] == 0 for r in dg)
    l4_1w = sum(r["wide_do_less_admitted"] == 0 for r in dg)
    pairs = [(r["seed"], i, v["test"]) for r in dg for i, v in r["standalone"].items()]
    l4_3 = sum(v >= -TOL for _, _, v in pairs)
    fail_seed = {r["seed"]: any(v["test"] < -TOL for v in r["standalone"].values()) for r in dg}
    passed = l4_1 == n and l4_3 >= 0.95 * len(pairs)
    cmp = {}
    old_p = RESULTS / "solpi_retry2" / "s6b_floor_fresh_seeds.json"
    if old_p.exists():
        old = {r["seed"]: r for r in json.loads(old_p.read_text())["per_seed"] if r["arm"] == "dual_gate"}
        old_fail = {s: any(v["test"] < -TOL for v in old[s]["standalone"].values()) for s in seeds if s in old}
        ss = [s for s in seeds if s in old_fail]
        cmp = {"p_l4_failing_seeds": sum(old_fail[s] for s in ss),
               "p_l4b_failing_seeds": sum(fail_seed[s] for s in ss),
               "mcnemar_exact_p": mcnemar_exact([old_fail[s] for s in ss], [fail_seed[s] for s in ss]),
               "p_l4_wide_do_less_free_seeds": sum(not any(i in WIDE_DO_LESS for i in old[s]["survivors"])
                                                   for s in ss)}
    report = {"L4-1 no P14/P20 admitted (seeds)": f"{l4_1}/{n}",
              "L4-1' no P14/P20/T3/T7 admitted (seeds)": f"{l4_1w}/{n}",
              "L4-3 admitted survivors within 2 pts standalone on test": f"{l4_3}/{len(pairs)}",
              "L4-3 failures": [(s, i, round(v, 4)) for s, i, v in pairs if v < -TOL],
              "seeds_with_failing_survivor": [s for s, f in fail_seed.items() if f],
              "vs_p_l4": cmp,
              "composed_evolve_change": summarize([r["evolve_stack_change"] for r in dg]),
              "composed_test_change": summarize([r["test_stack_change"] for r in dg]),
              "survivor_counts": survivor_counts(dg), "pass": passed}
    return report, (f"P-L4b {'PASS' if passed else 'FAIL'}: L4-1 {l4_1}/{n}; L4-1' {l4_1w}/{n}; L4-3 "
                    f"{l4_3}/{len(pairs)}; failing seeds {sum(fail_seed.values())}/{n}; vs P-L4 {cmp}")


def main():
    def only(ap):
        ap.add_argument("--only", default=None, help="run one experiment only (r7b_firewall | l4b_big_screen)")

    args, seeds = parse_seeded(__doc__.splitlines()[0], default_seeds=20, extra=only)
    exps = [args.only] if args.only else list(EXPS)
    rows = pool_map(job, [(e, s) for e in exps for s in seeds], args.workers)
    by = {e: sorted([r for r in rows if r["exp"] == e], key=lambda r: r["seed"]) for e in exps}
    payload = {"claim": "R7 (accumulation of permitted small losses, firewall on) and L4 (floor with a larger "
                        "screen) [blog Capability floors]",
               "config": {"seeds": seeds, "tolerance": TOL, "experiments": {e: EXPS[e] for e in exps},
                          "prereg": "claims-audit-solpi.md P-R7b, P-L4b"},
               "per_seed": rows}
    verdicts = []
    if "r7b_firewall" in by:
        payload["p_r7b"], v = r7b(by["r7b_firewall"], len(seeds))
        verdicts.append(v)
    if "l4b_big_screen" in by:
        payload["p_l4b"], v = l4b(by["l4b_big_screen"], seeds)
        verdicts.append(v)
    for e in exps:
        print(table([[r["seed"], ",".join(r["survivors"]), f"{r['evolve_stack_change']:+.3f}",
                      f"{r['test_stack_change']:+.3f}",
                      " ".join(f"{i}:{v['evolve']:+.3f}/{v['test']:+.3f}" for i, v in r["standalone"].items())]
                     for r in by[e]], ["seed", "survivors", "evolve", "test", "standalone evolve/test"]))
    payload["verdict"] = "; ".join(verdicts)
    print("verdict:", payload["verdict"])
    save("s6c_floor_followups", payload, args.out or str(RESULTS / "solpi_retry2" / "s6c_floor_followups.json"))


if __name__ == "__main__":
    main()
