"""Core calibration: do rsi.core's noise band and gates behave as the specs claim?

Claims checked (all on UNCHANGED artifacts, i.e. under the null of "no real change"):

C1  RRSI calibrate.py: with delta = z * sd(null dS) (z = 2) estimated by the
    within-task bootstrap of ONE k-trial evaluation (``stats.noise_from_trials``),
    an unchanged artifact clears the floor S* - delta "about 97.5% of the time"
    (``gates.NoiseFloor``).
C2  The bootstrap delta agrees with the delta from repeated evaluations
    (``stats.noise_from_repeats``: sqrt(2) * stdev(S_r)).
C3  autoresearch-mlx rigor.py: "keep iff strictly better" chases noise - it keeps
    about half of the re-evaluations of the SAME artifact, while a noise-aware
    rule (``gates.MinGain`` with the same delta) keeps only a few %.
C4  (core bug fix) float summation order makes mathematically tied scores differ
    in the last bit; the pre-fix strict rule (gain > 0) accepted some ties, the
    fixed rule (gain > TIE_EPS) never does.

Diagnosis arm: resampling k trials within a task underestimates the per-task
variance by (k-1)/k, so the bootstrap delta should be sqrt((k-1)/k) of the true
one and clearance Phi(z * sqrt((k-1)/k)) instead of Phi(z). We sweep k and also
report the delta corrected by sqrt(k/(k-1)).

Domains: AgentQA (seed harness + SimModel, or a live model via --llm) and a
Bernoulli toy FunctionDomain (tasks with latent success probabilities), both
through ``rsi.core.Evaluator``. Each (domain, k, seed) builds a fresh suite,
evaluates the artifact R times with disjoint trial seeds, and forms R/2
independent pairs.

Verdicts test two hypotheses against the pooled 95% bootstrap CI of each cell
(and of both domains pooled per k): the claim (clearance 0.975, delta ratio 1)
and the small-k prediction (Phi(z*sqrt((k-1)/k)), ratio sqrt((k-1)/k)). A claim
is "reproduced" only when the CI contains the claim and excludes the
prediction; "inconclusive" when it contains both.

    python experiments/core-qa/core_calibration.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core import (Artifact, CachedLLM, Evaluator, FunctionDomain, GateContext, MinGain, NoiseFloor, Scored,  # noqa: E402
                      StrictImprovement, Task, TaskSuite, get_llm, noise_from_repeats, noise_from_trials,
                      summarize_runs)
from rsi.core.stats import bootstrap_ci  # noqa: E402

OUT = ROOT / "results" / "core-qa"
CACHE = ROOT / ".rsi_cache" / "core-qa"
Z = 2.0
CLAIMED = 0.975


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def predicted_clearance(k: int) -> float:
    return _phi(Z * math.sqrt((k - 1) / k))


# ------------------------------------------------------------------- domains
def bernoulli_domain(seed: int, n_tasks: int):
    """Toy domain: task i succeeds with latent probability p_i (spread over [0.05, 0.95])."""
    rng = random.Random(f"bern-{seed}")
    tasks = [Task(f"b{i:03d}", i, None, "toy", {"p": rng.uniform(0.05, 0.95)}) for i in range(n_tasks)]
    suite = TaskSuite(tasks, {"evolve": [t.id for t in tasks]}, name=f"bernoulli-s{seed}")

    def execute(art, task, s, llm):
        r = random.Random(f"{seed}|{task.id}|{s}|{art.id}")
        return float(r.random() < task.meta["p"])

    return FunctionDomain(suite, execute, lambda t, out: out, name="bernoulli"), Artifact({"policy.txt": "fixed\n"}), None


def agentqa_domain(seed: int, n_tasks: int, llm_spec: str):
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite

    suite = make_suite(n_evolve=n_tasks, n_holdout=2, n_ood_per_family=1, seed=seed)
    llm = SimModel(suite) if llm_spec == "sim" else CachedLLM(get_llm(llm_spec), CACHE)
    return AgentQADomain(suite), AgentQADomain.seed_artifact(), llm


# ------------------------------------------------------------------ one job
def run_job(job: dict) -> dict:
    kind, seed, R, k, n_tasks = job["kind"], job["seed"], job["R"], job["k"], job["n_tasks"]
    dom, art, llm = bernoulli_domain(seed, n_tasks) if kind == "bernoulli" else \
        agentqa_domain(seed, n_tasks, job["llm"])
    ev = Evaluator(dom, llm, workers=job["workers"])
    evals = [ev.evaluate(art, "evolve", seeds=[10_000 * seed + 100 * r + j for j in range(k)]) for r in range(R)]
    S = np.array([e.score for e in evals])
    sums = [sum(t.score for trs in e.trials.values() for t in trs) for e in evals]   # exact integer totals
    deltas = np.array([noise_from_trials(e.trial_matrix(), z=Z).delta for e in evals])
    corr = math.sqrt(k / (k - 1))
    delta_rep = noise_from_repeats(S, z=Z).delta
    rows = []
    for a, b in zip(range(0, R - 1, 2), range(1, R, 2)):      # disjoint pairs = independent null comparisons
        inc, cand = Scored(S[a]), Scored(S[b])
        ctx = GateContext(best_score=S[a], delta=float(deltas[a]))
        ctx_c = GateContext(best_score=S[a], delta=float(deltas[a] * corr))
        tie = abs(sums[a] - sums[b]) < 1e-9
        rows.append({
            "dS": float(S[b] - S[a]), "delta": float(deltas[a]),
            "floor_clear": bool(NoiseFloor().check(cand, inc, ctx)),
            "floor_clear_corrected": bool(NoiseFloor().check(cand, inc, ctx_c)),
            "strict": bool(StrictImprovement().check(cand, inc, ctx)),
            "strict_prefix": bool(S[b] - S[a] > 0),               # the rule before the TIE_EPS fix
            "min_gain": bool(MinGain().check(cand, inc, ctx)),
            "min_gain_corrected": bool(MinGain().check(cand, inc, ctx_c)),
            "tie": bool(tie), "float_tie": bool(tie and S[a] != S[b]),
        })
    sd_true = float(np.std(S, ddof=1) * math.sqrt(2))
    mean = lambda key: float(np.mean([r[key] for r in rows]))  # noqa: E731
    return {
        "kind": kind, "seed": seed, "R": R, "k": k, "n_tasks": len(dom.tasks.splits["evolve"]),
        "S_mean": float(S.mean()), "sd_null_repeat": sd_true, "delta_boot_mean": float(deltas.mean()),
        "delta_repeat": float(delta_rep), "delta_ratio": float(deltas.mean() / delta_rep) if delta_rep else float("nan"),
        "clearance": mean("floor_clear"), "clearance_corrected": mean("floor_clear_corrected"),
        "clearance_analytic": float(_phi(deltas.mean() / sd_true)) if sd_true else 1.0,
        "strict_accept": mean("strict"), "strict_prefix_accept": mean("strict_prefix"),
        "min_gain_accept": mean("min_gain"), "min_gain_corrected_accept": mean("min_gain_corrected"),
        "ties": int(sum(r["tie"] for r in rows)),
        "float_ties": int(sum(r["float_tie"] for r in rows)),
        "float_ties_accepted_prefix": int(sum(r["float_tie"] and r["strict_prefix"] for r in rows)),
        "float_ties_accepted_fixed": int(sum(r["float_tie"] and r["strict"] for r in rows)),
        "pairs": rows, "usage": llm.meter.snapshot()["_total"] if llm is not None else {}, "rollouts": ev.n_rollouts,
    }


# ------------------------------------------------------------------- report
def summarize(rs: list[dict]) -> dict:
    keys = ("clearance", "clearance_corrected", "clearance_analytic", "delta_ratio", "strict_accept",
            "strict_prefix_accept", "min_gain_accept", "min_gain_corrected_accept", "S_mean", "delta_boot_mean",
            "delta_repeat")
    out = {k: summarize_runs([r[k] for r in rs]) for k in keys}
    for key in ("ties", "float_ties", "float_ties_accepted_prefix", "float_ties_accepted_fixed"):
        out[key] = sum(r[key] for r in rs)
    out["n_pairs"] = sum(len(r["pairs"]) for r in rs)
    for key in ("floor_clear", "floor_clear_corrected", "strict", "min_gain", "min_gain_corrected"):
        pooled = [p[key] for r in rs for p in r["pairs"]]
        out[f"{key}_pooled"] = dict(zip(("mean", "lo", "hi"), bootstrap_ci(pooled)))
    k = rs[0]["k"]
    out["k"] = k
    out["predicted_ratio"] = math.sqrt((k - 1) / k)
    out["predicted_clearance"] = predicted_clearance(k)
    return out


def _two_way(ci: dict, claim: float, pred: float, *, enough: bool) -> str:
    """Which of claim / small-k prediction the 95% CI is compatible with."""
    if not enough:
        return "inconclusive (too few pairs)"
    has_claim, has_pred = ci["lo"] <= claim <= ci["hi"], ci["lo"] <= pred <= ci["hi"]
    if has_claim and not has_pred:
        return "reproduced"
    if has_pred and not has_claim:
        return "NOT reproduced (matches the small-k bias prediction instead)"
    if has_claim and has_pred:
        return "inconclusive (the CI contains both the claim and the small-k prediction)"
    return "NOT reproduced (matches neither the claim nor the small-k prediction)"


def verdicts(summ: dict, per_job: list[dict]) -> dict:
    """Per cell and pooled over both domains per k (see the module docstring)."""
    v = {}
    for key, s in summ.items():
        enough = s["n_pairs"] >= 100
        c, cc = s["floor_clear_pooled"], s["floor_clear_corrected_pooled"]
        tag = _two_way(c, CLAIMED, s["predicted_clearance"], enough=enough)
        v[f"C1[{key}]"] = (f"{tag}: clearance {c['mean']:.3f} [{c['lo']:.3f}, {c['hi']:.3f}] vs {CLAIMED} claimed "
                           f"and {s['predicted_clearance']:.3f} predicted by the small-k bias; with the "
                           f"sqrt(k/(k-1)) correction {cc['mean']:.3f} [{cc['lo']:.3f}, {cc['hi']:.3f}] "
                           f"({s['n_pairs']} pairs)")
        r = s["delta_ratio"]
        tag = _two_way(r, 1.0, s["predicted_ratio"], enough=enough)
        v[f"C2[{key}]"] = (f"{tag}: delta_boot / delta_repeat = {r['mean']:.3f} [{r['lo']:.3f}, {r['hi']:.3f}] "
                           f"(claim 1.0, sqrt((k-1)/k) = {s['predicted_ratio']:.3f}; per-seed ratios)")
        st, mg, mgc = s["strict_pooled"], s["min_gain_pooled"], s["min_gain_corrected_pooled"]
        tag = "inconclusive (too few pairs)" if not enough else \
            ("reproduced" if st["lo"] >= 0.3 and mg["hi"] <= 0.08 else
             "partly: strict chases noise, but MinGain(delta) keeps more than a few % (delta biased low)"
             if st["lo"] >= 0.3 and mgc["hi"] <= 0.08 else "NOT reproduced")
        v[f"C3[{key}]"] = (f"{tag}: strict keeps {st['mean']:.1%} [{st['lo']:.1%}, {st['hi']:.1%}] of null "
                           f"re-evaluations, MinGain(delta) {mg['mean']:.1%} [{mg['lo']:.1%}, {mg['hi']:.1%}], "
                           f"MinGain(corrected delta) {mgc['mean']:.1%} (one-sided z=2 target: 2.3%)")
        v[f"C4[{key}]"] = (f"{s['float_ties']} of {s['ties']} exact ties differed in float; the pre-fix strict rule "
                           f"accepted {s['float_ties_accepted_prefix']}, the fixed rule {s['float_ties_accepted_fixed']}")
    for k in sorted({r["k"] for r in per_job}):
        pairs = [p for r in per_job if r["k"] == k for p in r["pairs"]]
        for key, claim_name in (("floor_clear", "C1"), ("floor_clear_corrected", "C1-corrected")):
            ci = dict(zip(("mean", "lo", "hi"), bootstrap_ci([p[key] for p in pairs])))
            tag = _two_way(ci, CLAIMED, predicted_clearance(k), enough=len(pairs) >= 200) \
                if key == "floor_clear" else \
                ("inconclusive (too few pairs)" if len(pairs) < 200 else
                 "consistent with the claim" if ci["lo"] <= CLAIMED <= ci["hi"] else "differs from the claim")
            v[f"{claim_name}[pooled/k{k}]"] = (f"{tag}: {ci['mean']:.3f} [{ci['lo']:.3f}, {ci['hi']:.3f}] "
                                               f"over {len(pairs)} pairs of both domains (claim {CLAIMED}, "
                                               f"small-k prediction {predicted_clearance(k):.3f})")
    return v


def figure(per_job: list[dict], summ: dict, ks: list[int], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    surf, ink, muted, grid = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
    col = {"agentqa": "#2a78d6", "bernoulli": "#eb6834"}
    kinds = [k for k in ("agentqa", "bernoulli") if any(key.startswith(k) for key in summ)]
    k_hist = 3 if 3 in ks else ks[0]
    fig, axes = plt.subplots(2, 2, figsize=(10, 7.2), facecolor=surf)
    axes = axes.ravel()
    for ax, kind in zip(axes[:2], kinds):
        z = [p["dS"] / p["delta"] for r in per_job if r["kind"] == kind and r["k"] == k_hist
             for p in r["pairs"] if p["delta"] > 0]
        ax.hist(z, bins=np.linspace(-3, 3, 31), color=col[kind], edgecolor=surf, linewidth=2)
        top = ax.get_ylim()[1]
        ax.axvline(-1, color=ink, linestyle="--", linewidth=1.5)
        ax.axvline(1, color=muted, linestyle=":", linewidth=1.5)
        ax.text(-1.08, top * 0.92, "floor S* - δ", ha="right", color=ink, fontsize=8)
        ax.text(1.08, top * 0.92, "MinGain: ΔS > δ", ha="left", color=muted, fontsize=8)
        c = summ[f"{kind}/k{k_hist}"]["floor_clear_pooled"]["mean"]
        ax.set_title(f"{kind}, k={k_hist}: null ΔS in units of bootstrap δ (clears floor {c:.1%})", fontsize=9,
                     color=ink)
        ax.set_xlabel("ΔS between two evaluations of the same artifact / δ", fontsize=8, color=muted)
        ax.set_ylabel("pairs", fontsize=8, color=muted)
    ax = axes[2]
    xs = np.array(ks)
    ax.plot(xs, [CLAIMED] * len(xs), color=muted, linestyle=":", linewidth=1.5)
    ax.text(xs[-2] + 0.5 * (xs[-1] - xs[-2]) if len(xs) > 1 else xs[0], CLAIMED - 0.002, "claimed 97.5%",
            ha="center", va="top", fontsize=8, color=muted)
    ax.plot(xs, [predicted_clearance(k) for k in ks], color=grid, linewidth=6, solid_capstyle="round",
            label="predicted Φ(z·√((k-1)/k))")
    for kind in kinds:
        raw = [summ[f"{kind}/k{k}"]["floor_clear_pooled"]["mean"] for k in ks]
        cor = [summ[f"{kind}/k{k}"]["floor_clear_corrected_pooled"]["mean"] for k in ks]
        ax.plot(xs, raw, color=col[kind], linewidth=2, marker="o", markersize=8, label=f"{kind}: bootstrap δ")
        ax.plot(xs, cor, color=col[kind], linewidth=2, linestyle="--", marker="s", markersize=8,
                label=f"{kind}: δ·√(k/(k-1))")
    ax.set_xticks(xs, [f"k={k}" for k in ks])
    ax.set_ylim(0.85, 1.0)
    ax.set_title("floor clearance of an unchanged artifact vs trials per task", fontsize=9, color=ink)
    ax.legend(frameon=False, fontsize=7, loc="lower right")
    ax = axes[3]
    rules = [("strict_prefix_accept", "strict (pre-fix)"), ("strict_accept", "strict"), ("min_gain_accept", "MinGain δ"),
             ("min_gain_corrected_accept", "MinGain δ·√(k/(k-1))")]
    x = np.arange(len(rules))
    w = 0.38
    for j, kind in enumerate(kinds):
        vals = [summ[f"{kind}/k{k_hist}"][key]["mean"] for key, _ in rules]
        bars = ax.bar(x + (j - 0.5) * w, vals, w - 0.03, color=col[kind], label=kind)
        for b, val in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, val + 0.01, f"{val:.0%}", ha="center", fontsize=7, color=muted)
    ax.set_xticks(x, [lab for _, lab in rules], fontsize=8)
    ax.set_ylim(0, 0.7)
    ax.set_title(f"share of null re-evaluations kept (k={k_hist})", fontsize=9, color=ink)
    ax.legend(frameon=False, fontsize=8)
    for a in axes:
        a.set_facecolor(surf)
        a.grid(axis="y", color=grid, linewidth=0.8)
        a.set_axisbelow(True)
        for sp in ("top", "right"):
            a.spines[sp].set_visible(False)
        a.tick_params(labelsize=8, colors=muted)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--llm", default="sim", help="sim | claude:haiku (task model for AgentQA)")
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--replicates", type=int, default=None, help="evaluations per (domain, k, seed); even")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--replot", default=None, help="redraw the figure from an existing results JSON and exit")
    args = ap.parse_args()
    if args.replot:
        res = json.loads(Path(args.replot).read_text())
        per_job = [{**r, "pairs": res["pairs"][f"{r['kind']}-k{r['k']}-{r['seed']}"]} for r in res["per_seed"]]
        figure(per_job, res["summary"], res["config"]["ks"], Path(args.replot).with_suffix(".png"))
        print(f"redrew {Path(args.replot).with_suffix('.png')}")
        return
    live = args.llm != "sim"
    if args.quick or live:
        seeds, R, ks, n_tasks = args.seeds or 2, args.replicates or 4, [2], 6
    else:
        seeds, R, ks, n_tasks = args.seeds or 30, args.replicates or 80, [2, 3, 5], 20
    kinds = ("agentqa", "bernoulli")
    jobs = [{"kind": kind, "seed": s, "R": R, "k": k, "n_tasks": n_tasks if kind == "agentqa" else 3 * n_tasks,
             "llm": args.llm, "workers": 4 if live else 1}
            for kind in kinds for k in ks for s in range(seeds)]
    t0 = time.time()
    if live:
        per_job = [run_job(j) for j in jobs]           # one process: the CLI calls are the bottleneck
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            per_job = list(ex.map(run_job, jobs))
    summ = {f"{kind}/k{k}": summarize([r for r in per_job if r["kind"] == kind and r["k"] == k])
            for kind in kinds for k in ks}
    v = verdicts(summ, per_job)
    OUT.mkdir(parents=True, exist_ok=True)
    name = "core_calibration" + ("_quick" if args.quick else "") + ("_" + args.llm.replace(":", "-") if live else "")
    figure(per_job, summ, ks, OUT / f"{name}.png")
    res = {"config": {"llm": args.llm, "seeds": seeds, "replicates": R, "ks": ks, "n_tasks_agentqa": n_tasks,
                      "n_tasks_bernoulli": 3 * n_tasks, "z": Z, "quick": args.quick},
           "summary": summ, "verdicts": v, "wall_s": round(time.time() - t0, 1),
           "per_seed": [{k2: v2 for k2, v2 in r.items() if k2 != "pairs"} for r in per_job],
           "pairs": {f"{r['kind']}-k{r['k']}-{r['seed']}": r["pairs"] for r in per_job}}
    (OUT / f"{name}.json").write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(v, indent=1))
    print(f"wrote {OUT / name}.json/.png in {res['wall_s']} s")


if __name__ == "__main__":
    main()
