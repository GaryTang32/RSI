"""X17: Evolver's failure distiller pollutes a faithful agent's gene library (found in the adversarial review).
Run: python experiments/evomap/x17_failure_distiller.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import FAITHFUL_HINT, cached_llm, live, paired, parse_args, pmap, print_groups, save, summarize_groups

from rsi.evomap import Config, evaluate_library, run

REPAIR = "gene_repair_distilled_"
METRICS = ["holdout_no_genes", "holdout_library", "n_genes", "n_repair_genes", "cycle_share_on_repair_genes",
           "repair_cycles_solidified", "repair_cycles_task_solved", "loop_solve_rate"]


def setup(domain: str, seed: int, args):
    """(domain, harness, task model, proposer, cycles, eval k)."""
    if domain == "geneworld":
        from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, WorldConfig, make_domain
        dom = make_domain(WorldConfig(seed=seed))
        return (dom, dom.seed_artifact(), GeneWorldModel(0.0), GeneWorldProposer(dom.world, 0.6, 0.3),
                40 if args.quick else 96, 2)
    from rsi.domains.katas import KataMockProposer, KataSimSolver, KatasDomain, seed_harness
    dom = KatasDomain()
    if live(args):
        llm = cached_llm(args.llm)
        return dom, seed_harness(), llm, llm, 12, 1
    return (dom, seed_harness(), KataSimSolver(0.0, name=f"kata-sim-{seed}"), KataMockProposer(0.7, 0.3),
            15 if args.quick else 30, 3)


def _share(part: int, whole: int) -> float:
    return part / max(1, whole)


def one(job) -> dict:
    domain, fd, seed, args = job
    dom, harness, model, prop, cycles, k = setup(domain, seed, args)
    res = run(dom, harness, llm_task=model, llm_propose=prop,
              config=Config(cycles=cycles, mode="faithful", seed=seed, validation_hint=FAITHFUL_HINT,
                            failure_distill=fd))
    rep = evaluate_library(dom, model, res, splits=("holdout",), k=k, workers=2)
    traj = res.trajectory
    on_repair = [r for r in traj if (r["gene_id"] or "").startswith(REPAIR)]
    return {"domain": domain, "failure_distill": fd, "seed": seed,
            "holdout_no_genes": rep["splits"]["holdout"]["no_genes"]["S"],
            "holdout_library": rep["splits"]["holdout"]["gene_library"]["S"],
            "n_genes": res.meta["n_genes"],
            "n_repair_genes": sum(1 for g in res.meta["genes"] if g.startswith(REPAIR)),
            "cycle_share_on_repair_genes": _share(len(on_repair), len(traj)),
            "repair_cycles_solidified": _share(sum(r["solidified"] for r in on_repair), len(on_repair)),
            "repair_cycles_task_solved": _share(sum(r["task_success"] for r in on_repair), len(on_repair)),
            "loop_solve_rate": _share(sum(r["task_success"] for r in traj), len(traj))}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=10)
    domains = ["katas"] if live(args) else ["geneworld", "katas"]
    rows = pmap(one, [(d, fd, s, args) for d in domains for fd in (True, False) for s in range(args.seeds)],
                args.workers)
    groups, pd, verdict = {}, {}, {}
    for d in domains:
        for fd in (True, False):
            groups[f"{d}/failure_distill={fd}"] = [r for r in rows if r["domain"] == d and r["failure_distill"] == fd]
        on, off = (sorted(groups[f"{d}/failure_distill={fd}"], key=lambda r: r["seed"]) for fd in (True, False))
        pd[d] = paired([r["holdout_library"] for r in off], [r["holdout_library"] for r in on])
        verdict[f"{d}_failure_distiller_lowers_holdout"] = pd[d]["hi"] < 0 if pd[d]["n"] >= 2 else None
    summ = summarize_groups(groups, METRICS)
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "domains": domains, "mode": "faithful",
                      "clock": "1 logical hour per cycle; failure-distiller interval 12 h"},
           "raw": rows, "summary": summ, "paired_on_minus_off_holdout": pd, "verdict": verdict}
    save("x17_failure_distiller" + ("_live" if live(args) else ""), out, args.out)
    print_groups(summ, METRICS, 30)
    for d, p in pd.items():
        print(f"  {d}: holdout (on - off) {p['mean_diff']:+.3f} [{p['lo']:+.3f}, {p['hi']:+.3f}]")
    print("verdict:", verdict)


if __name__ == "__main__":
    main()
