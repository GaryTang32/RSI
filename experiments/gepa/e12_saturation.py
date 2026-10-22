"""E12: a seed that already solves the training examples (oracle or train-memorising) comes back unchanged (RuleWorld).

Run: python experiments/gepa/e12_saturation.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

from _common import by_seed, fmt, parse_args, pool_map, reflection_llm, save, summarize
from rsi.core import Artifact
from rsi.domains.ruleworld import make_domain
from rsi.gepa import Config, run

ARMS = ["oracle,slip0,skip", "oracle,slip0,noskip", "oracle,slip0.05,skip", "memorizer,slip0,val=train",
        "memorizer,slip0,separate_val"]
METRICS = ("accepted", "proposed", "skipped_perfect", "iterations", "returned_seed", "seed_test", "best_test",
           "seed_train", "measured_val_of_returned")


def memorizer(d) -> Artifact:
    """Seed prompts listing every training ticket's answers and no rule: perfect on D_train only."""
    w = d.world
    files = w.seed_files()
    for tid in d.tasks.splits["evolve"]:
        ex = w.examples[tid]
        for m in w.cfg.modules:
            pairs = [f"{a}: {c}" for a, c in ex.target.items() if w.aspects[a].module == m]
            if pairs:
                files[w.module_path(m)] += f"Ticket {ex.ticket}: " + "; ".join(pairs) + "\n"
    return Artifact(files)


def job(spec):
    arm, seed, B, llm = spec
    kind = arm.split(",", 1)[0]
    d = make_domain(seed=seed, slip=0.0 if "slip0," in arm else 0.05, val_is_train=arm.endswith("val=train"),
                    capacity=200 if kind == "memorizer" else 16)
    seed_art = d.oracle_artifact() if kind == "oracle" else memorizer(d)
    res = run(d, seed_art, llm_propose=reflection_llm(llm, d.world),
              config=Config(max_metric_calls=B, seed=seed, skip_perfect_score="noskip" not in arm))
    ev = [e.get("event") for e in res.state.trace]
    return {"arm": arm, "seed": seed, "accepted": ev.count("accepted"), "proposed": res.meta["n_proposals"],
            "skipped_perfect": ev.count("skip_perfect"), "iterations": len(ev),
            "returned_seed": res.best == seed_art, "seed_test": d.expected(seed_art, "test"),
            "measured_val_of_returned": res.meta["best_val"],
            "best_test": d.expected(res.best, "test"), "seed_train": d.expected(seed_art, "evolve")}


def verdict(summ: dict, arms: list[str]) -> str:
    oracle = ", ".join(
        f"{k} {summ[k]['accepted']['mean']:.2f} (seed returned {summ[k]['returned_seed']['mean']:.0%}, true test "
        f"{summ[k]['seed_test']['mean']:.3f} -> {summ[k]['best_test']['mean']:.3f})"
        for k in arms if k.startswith("oracle"))
    vt, sep = summ.get("memorizer,slip0,val=train"), summ.get("memorizer,slip0,separate_val")
    memo = (f". Train-memorising seed (noise-free, perfect on D_train, true test "
            f"{fmt(vt['seed_test'])}): with D_pareto = D_train GEPA accepts "
            f"{vt['accepted']['mean']:.1f} children, returns the seed "
            f"{vt['returned_seed']['mean']:.0%} of the time and reports a validation "
            f"score of {fmt(vt['measured_val_of_returned'])}; with a separate "
            f"validation set it also returns the seed ({sep['returned_seed']['mean']:.0%}) "
            f"- GEPA never reflects on D_pareto - but the reported validation score "
            f"{fmt(sep['measured_val_of_returned'])} exposes the problem" if vt else "")
    o = summ.get("oracle,slip0,skip")        # the noise-free arm decides the claim (absent in live mode)
    holds = o and o["accepted"]["mean"] < 0.5 and o["returned_seed"]["mean"] > 0.9
    claim = (f". Claim '~0 accepted, the seed is returned': {'REPRODUCED' if holds else 'NOT reproduced'} "
             f"with noise-free evaluation" if o else ". The noise-free arm was not run (live mode), so the claim "
             "is not assessed")
    return ("Oracle seed: accepted children per run " + oracle + memo + claim
            + "; with 5% rollout noise the strict minibatch gate admits noise-driven children (no noise margin), "
              "so the returned candidate can differ from the seed without being better.")


def main():
    a = parse_args("E12: saturation sanity check", default_seeds=10)
    B = 300 if a.live else 600
    arms = ARMS if not a.live else ["oracle,slip0.05,skip"]
    rows = pool_map(job, [(arm, s, B, a.llm) for arm in arms for s in range(a.seeds)], a.workers)
    summ = {arm: {k: summarize([float(r[k]) for r in by_seed(rows, arm=arm)]) for k in METRICS} for arm in arms}
    out = {"experiment": "E12 saturation", "llm": a.llm, "config": {"budget": B, "seeds": a.seeds}, "summary": summ,
           "raw": rows, "verdict": verdict(summ, arms)}
    save("e12_saturation", out, a.out)
    print(out["verdict"])


if __name__ == "__main__":
    main()
