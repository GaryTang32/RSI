"""S4 - Online Context Compact compacts at subtask boundaries only when future savings repay the cache rewrite.

Claims [code: economics.ts; blog]: with cache writes costing rho = 12.5x cache reads, compacting at every
boundary is expensive; never compacting overflows long sessions; Pi's native "late" compaction happens only
near the window; OCC's economic gate should give the lowest or near-lowest cost across trajectory lengths,
never compact when the saving S <= 0, and pass the released economics test vectors exactly.

Arms: never (auto-compaction off: an overflow ends the run), late (Pi native auto-compaction at
window - 16,384), every boundary (compact at each completed plan step), OCC (economic gate). Task-length bins
of 4-8, 8-12, 12-16 and 16-20 subtasks on the long families (repofix, configfix).

    python experiments/metaharness-solpi/s4_occ.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agentworld import agg, backend, domain, row, trials_of  # noqa: E402
from _common import RESULTS, figure_path, fmt, parse_args, plt, pool_map, save, summarize, table  # noqa: E402

import numpy as np  # noqa: E402

from rsi.solpi import DEFAULT_COMPACTION_ECONOMICS, decide_compaction, estimate_remaining_requests  # noqa: E402

BINS = [(4, 8), (8, 12), (12, 16), (16, 20)]
ARGS = None


def arms(dom):
    return {"never": dom.seed_artifact().with_files({"harness.json": '{"auto_compact": false, "extensions": {}}'}),
            "late_pi": dom.seed_artifact(),
            "every_boundary": dom.harness("online_context_compact", online_context_compact={"policy": "always"}),
            "occ": dom.harness("online_context_compact")}


def job(spec):
    seed, b, prof = spec
    dom = domain(ARGS, seed, n_train=3 if ARGS.quick else 6, subtasks=b, train_families=("repofix", "configfix"),
                 heldout_families=(), n_accept=0, n_final=0, n_test=0, max_turns=200)
    llm = backend(ARGS, prof)
    out = {"seed": seed, "bin": list(b), "backend": prof}
    for name, art in arms(dom).items():
        rs = [row(t) for t in trials_of(dom, llm, art, splits=("evolve",))]
        dec = [d for r in rs for d in r["audit"].get("occ", [])]
        out[name] = {**agg(rs), "decisions": len(dec),
                     "compacted_with_nonpositive_saving": sum(1 for s, c, _ in dec if c and s <= 0),
                     "reasons": dict(zip(*np.unique([d[2] for d in dec], return_counts=True))) if dec else {}}
        out[name]["reasons"] = {str(k): int(v) for k, v in out[name]["reasons"].items()}
    return out


def vectors() -> dict:
    h = estimate_remaining_requests(completed_boundary_request_counts=[4, 6, 5], remaining_boundaries=3, scale=1,
                                    standard_deviation_k=0, context_tokens=100_000, context_window_tokens=200_000,
                                    average_context_token_increment=5_000)
    base = dict(write_tokens=80_000, archive_tokens=60_000, memo_tokens=1_000, context_tokens=80_000,
                completed_boundary_request_counts=[4, 6, 5], remaining_boundaries=4,
                average_context_token_increment=2_000, context_window_tokens=200_000, prior_compaction_count=0,
                carried_debt_tokens=0, cache_debt_repayment_tokens=0, cache_write_read_ratio=1,
                economics=DEFAULT_COMPACTION_ECONOMICS)
    d = lambda **o: decide_compaction(**{**base, **o})
    checks = {
        "horizon": (h["requestsPerBoundaryMean"], h["expectedRemainingRequests"], h["windowRequestUpperBound"]) ==
        (5, 16, 20),
        "economic": (d()["compact"], d()["reason"]) == (True, "economic"),
        "non_positive_saving": d(archive_tokens=500, memo_tokens=1000)["reason"] == "non_positive_saving",
        "window_protection": d(context_tokens=195_000, cache_write_read_ratio=100, economics=dataclasses.replace(
            DEFAULT_COMPACTION_ECONOMICS, window_reserve_tokens=10_000))["reason"] == "window_protection",
        "cache_ratio_unavailable": d(cache_write_read_ratio=None)["reason"] == "cache_ratio_unavailable",
        "deferred_carried_debt": d(prior_compaction_count=1, cache_write_read_ratio=2,
                                   carried_debt_tokens=2_000_000)["reason"] == "deferred_carried_debt"}
    checks["all_pass"] = all(checks.values())
    return checks


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=5)
    profs = ["A"] if ARGS.live else ["A", "B"]
    bins = BINS[:1] if ARGS.live else BINS
    rows = pool_map(job, [(s, b, p) for s in range(ARGS.seeds) for b in bins for p in profs], ARGS.workers)
    names = ["never", "late_pi", "every_boundary", "occ"]
    summ = {}
    for p in profs:
        for b in bins:
            rs = [r for r in rows if r["backend"] == p and tuple(r["bin"]) == b]
            summ[f"{p}:{b[0]}-{b[1]}"] = {n: {**{k: summarize([r[n][k] for r in rs]) for k in
                                                 ("cost", "tokens", "score", "compactions", "overflow_rate")},
                                              "eta": summarize([r[n]["cost"] / max(1e-9, r[n]["score"]) for r in rs])}
                                          for n in names}
    vec = vectors()
    bad = sum(r[n]["compacted_with_nonpositive_saving"] for r in rows for n in ("occ",))
    rank = {}
    for k, v in summ.items():
        # rank by token efficiency eta = cost / score (SoL-Pi's objective): an arm that overflows and fails
        # is not "cheap"
        etas = {n: v[n]["eta"]["mean"] for n in names}
        costs = {n: v[n]["cost"]["mean"] for n in names}
        best = min(etas.values())
        rank[k] = {"occ_eta_vs_best": etas["occ"] / best - 1, "best_eta_arm": min(etas, key=etas.get),
                   "occ_vs_late": costs["occ"] / costs["late_pi"] - 1, "occ_eta_vs_late": etas["occ"] /
                   etas["late_pi"] - 1, "every_vs_late": costs["every_boundary"] / costs["late_pi"] - 1,
                   "never_overflow": v["never"]["overflow_rate"]["mean"]}
    near = all(r["occ_eta_vs_best"] <= 0.05 for r in rank.values())
    verdict = ("REPRODUCED" if near and vec["all_pass"] and bad == 0 else "PARTIAL") + \
        f": economics test vectors {'pass' if vec['all_pass'] else 'FAIL'}; OCC never compacted with S<=0 " \
        f"({bad} violations); OCC cost-per-score within 5% of the best arm in {sum(r['occ_eta_vs_best'] <= 0.05 for r in rank.values())}/" \
        f"{len(rank)} (backend, length) cells; every-boundary costs {np.mean([r['every_vs_late'] for r in rank.values()]):+.0%} " \
        f"vs late on average; never-compact overflows up to {max(r['never_overflow'] for r in rank.values()):.0%} of runs"
    print(table([[k] + [f"{summ[k][n]['eta']['mean']:.3f}" for n in names] +
                 [f"{rank[k]['occ_vs_late']:+.1%}", f"{rank[k]['best_eta_arm']}",
                  f"{summ[k]['never']['overflow_rate']['mean']:.2f}"]
                 for k in summ], ["backend:subtasks"] + [f"eta {n}" for n in names] +
                ["occ cost vs late", "best eta", "overflow(never)"]))
    print("verdict:", verdict)
    fig = plt()
    f, axes = fig.subplots(1, 2, figsize=(10, 3.8))
    for ax, metric in zip(axes, ("cost", "tokens")):
        for n in names:
            ax.plot([f"{b[0]}-{b[1]}" for b in bins], [summ[f"A:{b[0]}-{b[1]}"][n][metric]["mean"] for b in bins],
                    "-o", label=n)
        ax.set_xlabel("subtasks per task")
        ax.set_ylabel(f"mean {metric} per task (backend A)")
        ax.legend(fontsize=8)
    f.suptitle("S4 compaction policies across trajectory lengths")
    f.tight_layout()
    png = figure_path("s4_occ", ARGS)
    RESULTS.mkdir(parents=True, exist_ok=True)
    f.savefig(png, dpi=120)
    save("s4_occ" + ("_live" if ARGS.live else ""), {
        "claim": "OCC compacts at boundaries only when savings repay the cache rewrite: lowest or near-lowest cost "
                 "across lengths; never compacts when S <= 0; economics vectors pass [SoL-Pi code]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "bins": bins, "rho": 12.5},
        "per_seed": rows, "summary": summ, "ranking": rank, "economics_vectors": vec, "verdict": verdict,
        "figure": str(png)}, ARGS.out)


if __name__ == "__main__":
    main()
