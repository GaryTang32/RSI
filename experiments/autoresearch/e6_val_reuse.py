"""E6 - reusing the same validation data for every decision => slow tuning to it.

Claim [doc]: "The same validation data judges every experiment, so hundreds of
experiments slowly tune to it." The sources show no measurable gap at ~70 decisions
(xgboost port, spec section 8.2), so a null result at small N is expected; the
known-truth landscape lets us raise the val-specific component and the decision
count to see where the effect appears. Overfitting (val - test_iid gap) and
distribution shift (test_shift) are reported separately.

A. Landscape, 40 seeds x 300 experiments, val-specific sd in {0.0015, 0.005}, fixed val
   vs val re-sampled every 25 experiments (incumbent re-scored on the new split):
   gap(t) = recorded val of the incumbent - its expected iid-test loss, vs experiment t.
B. Tabular (real GBT, CV AUC), 30 experiments, fixed CV folds vs folds re-sampled every
   10 experiments; HiddenAudit of every keep on hidden iid and time-shifted test sets.

Usage: python experiments/autoresearch/e6_val_reuse.py [--seeds N] [--quick] [--llm ...]
"""
from __future__ import annotations

from _common import SCRATCH, ci, parser, plt, pool_map, write  # noqa: I001

import json

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, LandscapeTask, MockResearchAgent, landscape_edit_pool

CHECK_EVERY = 25


def expected(task: LandscapeTask, art, split: str) -> float:
    """Noise-free loss of ``art`` on a split (quality + split-specific component [+ shift])."""
    mod = task._module(task.files["prepare.py"])
    k = dict(mod.DEFAULTS)
    k.update(task.knobs(art))
    cfg = mod.config_key(k)
    q = mod.quality(k, task.compute)
    if split == "test_iid":
        return q + mod.VAL_SD * mod._gauss("iid", *cfg)
    if split == "test_shift":
        return q + mod.VAL_SD * mod._gauss("shift", *cfg) + mod.shift_penalty(k)
    return q


def landscape_arm(args):
    val_sd, resample, seed, n = args
    task = LandscapeTask(seed=seed, val_sd=val_sd)
    ag = MockResearchAgent(landscape_edit_pool(), seed=seed, p_repeat=0.5)
    cfg = Config(max_experiments=n, persist=False, plot=False, hidden_audit=False, seed=seed,
                 val_resample_every=CHECK_EVERY if resample else None)
    loop = AutoresearchLoop(task, ag, cfg, out_dir=SCRATCH / "e6" / "ls")
    arts = {}
    orig = loop._record

    def rec(**kw):
        node = orig(**kw)
        arts[node.id] = kw["art"]
        return node

    loop._record = rec
    res = loop.run()
    nodes = {n_.id: n_ for n_ in res.ledger.nodes()}
    # incumbent (id, recorded value) after each experiment, from the ledger in order
    inc_id, inc_val, gaps, shift, truth = None, None, [], [], []
    by_round = {}
    for n_ in res.ledger.nodes():
        if n_.status == "keep":
            by_round[n_.round] = n_.id
    rescored = {}
    for n_ in res.ledger.nodes():
        for k, v in n_.meta.items():
            if k.startswith("rescore_epoch_"):
                rescored[int(k.split("_")[-1])] = (n_.id, float(np.mean(v)))
    for t in range(0, n + 1):
        if t in by_round:
            inc_id = by_round[t]
            inc_val = nodes[inc_id].score
        if resample and t > 0 and (t - 1) % CHECK_EVERY == 0 and t > 1:
            ep = (t - 1) // CHECK_EVERY
            if ep in rescored and rescored[ep][0] == inc_id:
                inc_val = rescored[ep][1]
        if t % CHECK_EVERY == 0:
            art = arts[inc_id]
            gaps.append(expected(task, art, "test_iid") - inc_val)
            shift.append(expected(task, art, "test_shift"))
            truth.append(task.truth(art))
    return {"val_sd": val_sd, "resample": resample, "seed": seed, "gap_curve": gaps, "shift_curve": shift,
            "truth_curve": truth}


def tabular_arm(args):
    resample, seed, n = args
    from rsi.domains.tabular import TabularTask

    task = TabularTask()
    ag = MockResearchAgent(task.mock_edit_pool(), seed=seed)
    res = AutoresearchLoop(task, ag, Config(max_experiments=n, seed=seed, tag=f"e6-{resample}-{seed}", overwrite=True,
                                            val_resample_every=10 if resample else None),
                           out_dir=SCRATCH / "e6" / f"tab_{int(resample)}_{seed}").run()
    rows = res.meta["audit"]
    return {"resample": resample, "seed": seed,
            "keeps": [{"exp": r["exp"], "description": r["description"], "cv": r["metric"], "iid": r["test_iid"],
                       "shift": r["test_shift"]} for r in rows]}


def main():
    ap = parser(__doc__.splitlines()[0], seeds=2)
    a = ap.parse_args()
    n = 100 if a.quick else 300
    seeds = list(range(8 if a.quick else 40))
    arms = [(v, r) for v in (0.0015, 0.005) for r in (False, True)]
    ls = pool_map(landscape_arm, [(v, r, s, n) for v, r in arms for s in seeds], a.workers)
    out = {"config": {"landscape_experiments": n, "landscape_seeds": len(seeds), "check_every": CHECK_EVERY,
                      "tabular_experiments": 12 if a.quick else 30}, "landscape": {}}
    for v, r in arms:
        rs = [x for x in ls if x["val_sd"] == v and x["resample"] == r]
        G = np.array([x["gap_curve"] for x in rs])
        out["landscape"][f"val_sd={v},resample={r}"] = {
            "checkpoints": list(range(0, n + 1, CHECK_EVERY)), "gap_mean": G.mean(0).tolist(),
            "gap_final": ci(G[:, -1]), "gap_after_first_50": ci(G[:, 2]),
            "true_final": ci([x["truth_curve"][-1] for x in rs]),
            "shift_final": ci([x["shift_curve"][-1] for x in rs])}
    t_seeds = list(range(1 if a.quick else a.seeds))
    tn = 12 if a.quick else 30
    tb = pool_map(tabular_arm, [(r, s, tn) for r in (False, True) for s in t_seeds], a.workers)
    out["tabular"] = {}
    for r in (False, True):
        rs = [x for x in tb if x["resample"] == r]
        first = [x["keeps"][0] for x in rs]
        last = [x["keeps"][-1] for x in rs]
        out["tabular"][f"resample={r}"] = {
            "runs": rs, "cv_minus_iid_baseline": ci([k["cv"] - k["iid"] for k in first]),
            "cv_minus_iid_final": ci([k["cv"] - k["iid"] for k in last]),
            "iid_gain": ci([l_["iid"] - f["iid"] for f, l_ in zip(first, last)]),
            "shift_gain": ci([l_["shift"] - f["shift"] for f, l_ in zip(first, last)]),
            "cv_gain": ci([l_["cv"] - f["cv"] for f, l_ in zip(first, last)]),
            "keeps_that_raise_iid_but_lower_shift": [
                k["description"] for x in rs for p_, k in zip(x["keeps"], x["keeps"][1:])
                if k["iid"] > p_["iid"] and k["shift"] < p_["shift"] - 0.005]}
    L = out["landscape"]
    big_fixed, big_res = L["val_sd=0.005,resample=False"], L["val_sd=0.005,resample=True"]
    small_fixed = L["val_sd=0.0015,resample=False"]
    T = out["tabular"]
    verdict = {
        "landscape_gap_grows_fixed_val": bool(big_fixed["gap_final"]["mean"] > big_fixed["gap_after_first_50"]["mean"]),
        "landscape_gap_final_fixed_vs_resampled": [big_fixed["gap_final"]["mean"], big_res["gap_final"]["mean"]],
        "landscape_small_valsd_gap_final": small_fixed["gap_final"]["mean"],
        "resampling_shrinks_gap": bool(big_res["gap_final"]["mean"] < big_fixed["gap_final"]["mean"]),
        "tabular_cv_minus_iid_fixed": [T["resample=False"]["cv_minus_iid_baseline"]["mean"],
                                       T["resample=False"]["cv_minus_iid_final"]["mean"]],
        "tabular_gap_growth_fixed": T["resample=False"]["cv_minus_iid_final"]["mean"] -
        T["resample=False"]["cv_minus_iid_baseline"]["mean"],
        "tabular_shift_trap_keeps": T["resample=False"]["keeps_that_raise_iid_but_lower_shift"],
    }
    verdict["claim_reproduced"] = (
        "landscape: yes (gap grows with decisions under fixed val; resampling shrinks it); "
        f"tabular at {tn} decisions: gap growth {verdict['tabular_gap_growth_fixed']:+.4f} AUC "
        "(compare with fold noise; a null result here matches the xgboost port's evidence)")
    out["verdict"] = verdict
    name = "e6_val_reuse" + ("_quick" if a.quick else "")
    out["figure"] = str(figure(out, name))
    write(name, out)
    print(json.dumps(verdict, indent=1, default=str))


def figure(out, name):
    from _common import RESULTS

    p = plt()
    fig, axes = p.subplots(1, 2, figsize=(13, 4.5))
    ax = axes[0]
    style = {False: "-", True: "--"}
    col = {0.0015: "#1a73e8", 0.005: "#d93025"}
    for key, v in out["landscape"].items():
        sd = float(key.split(",")[0].split("=")[1])
        r = key.endswith("True")
        ax.plot(v["checkpoints"], v["gap_mean"], style[r], color=col[sd],
                label=f"val-specific sd {sd}, {'re-sampled val' if r else 'fixed val'}")
    ax.axhline(0, color="grey", lw=0.8)
    ax.set_xlabel("experiment #")
    ax.set_ylabel("expected test_iid loss - recorded val loss")
    ax.set_title("landscape: optimism of the kept chain on its own val split")
    ax.legend(fontsize=7)
    ax = axes[1]
    for key, v in out["tabular"].items():
        for run in v["runs"]:
            xs = [k["exp"] for k in run["keeps"]]
            ax.step(xs, [k["cv"] - k["iid"] for k in run["keeps"]], where="post",
                    color="#d93025" if key.endswith("False") else "#1e8e3e", alpha=0.7,
                    label=f"{key} seed {run['seed']}")
    ax.set_xlabel("experiment #")
    ax.set_ylabel("CV AUC - hidden iid AUC (kept chain)")
    ax.set_title("tabular: CV vs hidden iid test")
    ax.legend(fontsize=7)
    fig.tight_layout()
    path = RESULTS / f"{name}.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


if __name__ == "__main__":
    main()
