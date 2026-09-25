"""X17: Evolver's failure distiller pollutes a faithful agent's gene library (found in the adversarial review).

Mechanism (spec §3.7, §4.14; Evolver ``autoDistillFromFailures``): after a successful solidify, once >= 5 failed
capsules exist and 12 h have passed, a heuristic synthesizes ``gene_repair_distilled_*`` - generic GUARD / APPLY /
VERIFY / ROLLBACK steps, ``signals_match`` = the most frequent trigger tokens of the failed capsules, validation
``node --test`` (ported: ``python --test``), which the allowlist filters out, so the gene ships with an EMPTY
validation list. Under the faithful runner an empty list passes, so the gene solidifies as a "success" whatever
the task outcome and spreads over every task its broad triggers match.

Arms (faithful mode, same seeds and task order): ``failure_distill=True`` (Evolver default) vs ``False``.
Domains: GeneWorld (96 cycles; mock gene writer) and katas (30 cycles; mock coder, real hidden tests). Metrics:
holdout pass rate of the routed library, number of repair genes, share of loop cycles run on a repair gene.
Caveat: our logical clock advances 1 h per cycle, so the 12 h interval lets the distiller fire at most every 12
cycles; a real agent that cycles faster would get fewer repair genes per cycle.

Run: python experiments/evomap/x17_failure_distiller.py [--llm sim|claude:haiku] [--seeds N] [--quick]
(``--llm claude:haiku``: katas only, live solver + gene writer, 1 seed, 12 cycles.)
"""
from __future__ import annotations

from _common import FAITHFUL_HINT, cached_llm, fmt, live, paired, parse_args, pmap, save, summarize

from rsi.evomap import Config, evaluate_library, run


def setup(domain, seed, args):
    if domain == "geneworld":
        from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, WorldConfig, make_domain
        dom = make_domain(WorldConfig(seed=seed))
        return dom, dom.seed_artifact(), GeneWorldModel(0.0), GeneWorldProposer(dom.world, 0.6, 0.3), \
            (40 if args.quick else 96), 2
    from rsi.domains.katas import KataMockProposer, KataSimSolver, KatasDomain, seed_harness
    dom = KatasDomain()
    if live(args):
        llm = cached_llm(args.llm)
        return dom, seed_harness(), llm, llm, 12, 1
    return dom, seed_harness(), KataSimSolver(0.0, name=f"kata-sim-{seed}"), KataMockProposer(0.7, 0.3), \
        (15 if args.quick else 30), 3


def one(job):
    domain, fd, seed, args = job
    dom, harness, model, prop, cycles, k = setup(domain, seed, args)
    res = run(dom, harness, llm_task=model, llm_propose=prop,
              config=Config(cycles=cycles, mode="faithful", seed=seed, validation_hint=FAITHFUL_HINT,
                            failure_distill=fd))
    rep = evaluate_library(dom, model, res, splits=("holdout",), k=k, workers=2)
    on_repair = [r for r in res.trajectory if (r["gene_id"] or "").startswith("gene_repair_distilled_")]
    return {"domain": domain, "failure_distill": fd, "seed": seed,
            "holdout_no_genes": rep["splits"]["holdout"]["no_genes"]["S"],
            "holdout_library": rep["splits"]["holdout"]["gene_library"]["S"],
            "n_genes": res.meta["n_genes"],
            "n_repair_genes": sum(1 for g in res.meta["genes"] if g.startswith("gene_repair_distilled_")),
            "cycle_share_on_repair_genes": len(on_repair) / max(1, len(res.trajectory)),
            "repair_cycles_solidified": sum(r["solidified"] for r in on_repair) / max(1, len(on_repair)),
            "repair_cycles_task_solved": sum(r["task_success"] for r in on_repair) / max(1, len(on_repair)),
            "loop_solve_rate": sum(r["task_success"] for r in res.trajectory) / max(1, len(res.trajectory))}


METRICS = ["holdout_no_genes", "holdout_library", "n_genes", "n_repair_genes", "cycle_share_on_repair_genes",
           "repair_cycles_solidified", "repair_cycles_task_solved", "loop_solve_rate"]


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=10)
    domains = ["katas"] if live(args) else ["geneworld", "katas"]
    rows = pmap(one, [(d, fd, s, args) for d in domains for fd in (True, False) for s in range(args.seeds)],
                args.workers)
    summ, pd, verdict = {}, {}, {}
    for d in domains:
        for fd in (True, False):
            rs = [r for r in rows if r["domain"] == d and r["failure_distill"] == fd]
            summ[f"{d}/failure_distill={fd}"] = {m: summarize([r[m] for r in rs]) for m in METRICS}
        on = sorted((r for r in rows if r["domain"] == d and r["failure_distill"]), key=lambda r: r["seed"])
        off = sorted((r for r in rows if r["domain"] == d and not r["failure_distill"]), key=lambda r: r["seed"])
        pd[d] = paired([r["holdout_library"] for r in off], [r["holdout_library"] for r in on])
        verdict[f"{d}_failure_distiller_lowers_holdout"] = pd[d]["hi"] < 0 if pd[d]["n"] >= 2 else None
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "domains": domains, "mode": "faithful",
                      "clock": "1 logical hour per cycle; failure-distiller interval 12 h"},
           "raw": rows, "summary": summ, "paired_on_minus_off_holdout": pd, "verdict": verdict}
    save("x17_failure_distiller" + ("_live" if live(args) else ""), out, args.out)
    for key, s in summ.items():
        print(f"== {key}")
        for m in METRICS:
            print(f"  {m:30s} {fmt(s[m])}")
    for d, p in pd.items():
        print(f"  {d}: holdout (on - off) {p['mean_diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]")
    print("verdict:", verdict)


if __name__ == "__main__":
    main()
