"""E12 - Saturation sanity check: a seed that already solves the training examples comes back.

Claim [skill gotchas 9]: "If the seed already solves the training minibatches, every
proposal is rejected and the seed comes back unchanged." Spec E12: ~0 accepted, the seed
is returned.

RuleWorld: (a) seed = the oracle prompt set (noise-free and with slip 0.05), with
skip_perfect_score on (default) and off; (b) a *train-saturated but not general* seed that
memorises every training ticket (ticket-specific facts) but states no rule - it is
perfect on D_train and poor on the test split, so GEPA with D_pareto = D_train has nothing
to learn from (the gotcha), while a separate validation set exposes it (the fix).

    python experiments/gepa/e12_saturation.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import fmt, parse_args, pool_map, reflection_llm, save, summarize  # noqa: E402

from rsi.core import Artifact  # noqa: E402
from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import Config, run  # noqa: E402

ARMS = ["oracle,slip0,skip", "oracle,slip0,noskip", "oracle,slip0.05,skip", "memorizer,slip0,val=train",
        "memorizer,slip0,separate_val"]
ARGS = None


def memorizer(d) -> Artifact:
    files = d.world.seed_files()
    w = d.world
    for tid in d.tasks.splits["evolve"]:
        ex = w.examples[tid]
        for m in w.cfg.modules:
            pairs = [f"{a}: {c}" for a, c in ex.target.items() if w.aspects[a].module == m]
            if pairs:
                files[w.module_path(m)] += f"Ticket {ex.ticket}: " + "; ".join(pairs) + "\n"
    return Artifact(files)


def job(spec):
    arm, seed, B = spec
    kind = arm.split(",", 1)[0]
    slip = 0.0 if "slip0," in arm else 0.05
    d = make_domain(seed=seed, slip=slip, val_is_train=arm.endswith("val=train"),
                    capacity=200 if kind == "memorizer" else 16)
    seed_art = d.oracle_artifact() if kind == "oracle" else memorizer(d)
    res = run(d, seed_art, llm_propose=reflection_llm(ARGS.llm, d.world),
              config=Config(max_metric_calls=B, seed=seed, skip_perfect_score="noskip" not in arm))
    ev = [e.get("event") for e in res.state.trace]
    return {"arm": arm, "seed": seed, "accepted": ev.count("accepted"), "proposed": res.meta["n_proposals"],
            "skipped_perfect": ev.count("skip_perfect"), "iterations": len(ev),
            "returned_seed": res.best == seed_art, "seed_test": d.expected(seed_art, "test"),
            "measured_val_of_returned": res.meta["best_val"],
            "best_test": d.expected(res.best, "test"), "seed_train": d.expected(seed_art, "evolve")}


def main():
    global ARGS
    ARGS = a = parse_args("E12: saturation sanity check", default_seeds=10)
    B = 300 if a.live else 600
    arms = ARMS if not a.live else ["oracle,slip0.05,skip"]
    rows = pool_map(job, [(arm, s, B) for arm in arms for s in range(a.seeds)], a.workers)
    summ = {}
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        summ[arm] = {k: summarize([float(r[k]) for r in rs]) for k in
                     ("accepted", "proposed", "skipped_perfect", "iterations", "returned_seed", "seed_test",
                      "best_test", "seed_train", "measured_val_of_returned")}
    o = summ.get("oracle,slip0,skip")        # the noise-free arm decides the claim (absent in live mode)
    out = {"experiment": "E12 saturation", "llm": a.llm, "config": {"budget": B, "seeds": a.seeds}, "summary": summ,
           "raw": rows,
           "verdict": ("Oracle seed: accepted children per run " + ", ".join(
               f"{k} {summ[k]['accepted']['mean']:.2f} (seed returned {summ[k]['returned_seed']['mean']:.0%}, true test "
               f"{summ[k]['seed_test']['mean']:.3f} -> {summ[k]['best_test']['mean']:.3f})"
               for k in arms if k.startswith("oracle"))
               + (f". Train-memorising seed (noise-free, perfect on D_train, true test "
                  f"{fmt(summ['memorizer,slip0,val=train']['seed_test'])}): with D_pareto = D_train GEPA accepts "
                  f"{summ['memorizer,slip0,val=train']['accepted']['mean']:.1f} children, returns the seed "
                  f"{summ['memorizer,slip0,val=train']['returned_seed']['mean']:.0%} of the time and reports a validation "
                  f"score of {fmt(summ['memorizer,slip0,val=train']['measured_val_of_returned'])}; with a separate "
                  f"validation set it also returns the seed ({summ['memorizer,slip0,separate_val']['returned_seed']['mean']:.0%}) "
                  f"- GEPA never reflects on D_pareto - but the reported validation score "
                  f"{fmt(summ['memorizer,slip0,separate_val']['measured_val_of_returned'])} exposes the problem"
                  if "memorizer,slip0,val=train" in summ else "")
               + (f". Claim '~0 accepted, the seed is returned': "
                  f"{'REPRODUCED' if o['accepted']['mean'] < 0.5 and o['returned_seed']['mean'] > 0.9 else 'NOT reproduced'} "
                  f"with noise-free evaluation" if o else ". The noise-free arm was not run (live mode), so the claim "
                  "is not assessed")
               + "; with 5% rollout noise the strict minibatch gate admits noise-driven children (no noise margin), "
                 "so the returned candidate can differ from the seed without being better.")}
    save("e12_saturation", out, a.out)
    print(out["verdict"])


if __name__ == "__main__":
    main()
