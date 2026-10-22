"""E6 - reusing the same validation data for every decision => slow tuning to it.

Usage: python experiments/autoresearch/e6_val_reuse.py [--seeds N] [--quick] [--llm sim|claude:haiku]
"""
from _common import (SCRATCH, ci, is_live, parser, plt, pool_map, report, research_agent, save_fig,  # noqa: I001
                     slug, suffix, usage_of)

import numpy as np

from rsi.autoresearch import AutoresearchLoop, Config, LandscapeTask, MockResearchAgent, landscape_edit_pool
from rsi.domains.tabular import TabularTask

CHECK_EVERY = 25


def expected(task: LandscapeTask, art, split: str) -> float:
    """Noise-free loss of ``art`` on a split (quality + split-specific component [+ shift])."""
    mod = task._module(task.files["prepare.py"])
    k = {**mod.DEFAULTS, **task.knobs(art)}
    cfg = mod.config_key(k)
    q = mod.quality(k, task.compute)
    if split == "test_iid":
        return q + mod.VAL_SD * mod._gauss("iid", *cfg)
    if split == "test_shift":
        return q + mod.VAL_SD * mod._gauss("shift", *cfg) + mod.shift_penalty(k)
    if split.startswith("val"):  # "val:<epoch>": the val split in use at that epoch
        return q + mod.VAL_SD * mod._gauss("val", int(split.split(":")[1]), *cfg)
    return q


def landscape_arm(args) -> dict:
    val_sd, resample, seed, n = args
    task = LandscapeTask(seed=seed, val_sd=val_sd)
    agent = MockResearchAgent(landscape_edit_pool(), seed=seed, p_repeat=0.5)
    cfg = Config(max_experiments=n, persist=False, plot=False, hidden_audit=False, seed=seed,
                 val_resample_every=CHECK_EVERY if resample else None)
    loop = AutoresearchLoop(task, agent, cfg, out_dir=SCRATCH / "e6" / "ls")
    arts, record = {}, loop._record

    def record_art(**kw):  # keep every recorded artifact, keyed by its ledger node
        node = record(**kw)
        arts[node.id] = kw["art"]
        return node

    loop._record = record_art
    res = loop.run()
    nodes = res.ledger.nodes()
    score = {nd.id: nd.score for nd in nodes}
    keep_by_round = {nd.round: nd.id for nd in nodes if nd.status == "keep"}
    rescored = {int(k.split("_")[-1]): (nd.id, float(np.mean(v)))
                for nd in nodes for k, v in nd.meta.items() if k.startswith("rescore_epoch_")}
    inc_id = inc_val = None
    gaps, shift, truth, valspec = [], [], [], []
    for t in range(n + 1):
        if t in keep_by_round:
            inc_id = keep_by_round[t]
            inc_val = score[inc_id]
        if resample and t > 1 and (t - 1) % CHECK_EVERY == 0:
            ep = (t - 1) // CHECK_EVERY
            if ep in rescored and rescored[ep][0] == inc_id:  # incumbent re-scored on the new val split
                inc_val = rescored[ep][1]
        if t % CHECK_EVERY == 0:
            art = arts[inc_id]
            epoch = (t - 1) // CHECK_EVERY if (resample and t >= 1) else 0
            iid = expected(task, art, "test_iid")
            gaps.append(iid - inc_val)
            valspec.append(iid - expected(task, art, f"val:{epoch}"))
            shift.append(expected(task, art, "test_shift"))
            truth.append(task.truth(art))
    return {"val_sd": val_sd, "resample": resample, "seed": seed, "gap_curve": gaps, "shift_curve": shift,
            "truth_curve": truth, "valspec_curve": valspec}


def tabular_arm(args) -> dict:
    resample, seed, n, llm_spec = args
    task = TabularTask()
    agent, llms = research_agent(llm_spec, task.mock_edit_pool(), seed=seed)
    every = (max(2, n // 3) if is_live(llm_spec) else 10) if resample else None
    cfg = Config(max_experiments=n, seed=seed, tag=f"e6-{resample}-{seed}", overwrite=True, val_resample_every=every)
    res = AutoresearchLoop(task, agent, cfg, out_dir=SCRATCH / "e6" / f"tab_{int(resample)}_{seed}_{slug(llm_spec)}",
                           llms=llms).run()
    rows = [r for r in res.meta["audit"] if "audit_error" not in r]
    return {"resample": resample, "seed": seed, "usage": usage_of(llms),
            "keeps": [{"exp": r["exp"], "description": r["description"], "cv": r["metric"], "iid": r["test_iid"],
                       "shift": r["test_shift"]} for r in rows]}


def tabular_summary(rs: list[dict]) -> dict:
    pairs = [(x["keeps"][0], x["keeps"][-1]) for x in rs]
    return {
        "runs": rs, "cv_minus_iid_baseline": ci([f["cv"] - f["iid"] for f, _ in pairs]),
        "cv_minus_iid_final": ci([last["cv"] - last["iid"] for _, last in pairs]),
        "iid_gain": ci([last["iid"] - f["iid"] for f, last in pairs]),
        "shift_gain": ci([last["shift"] - f["shift"] for f, last in pairs]),
        "cv_gain": ci([last["cv"] - f["cv"] for f, last in pairs]),
        "cv_minus_iid_growth": ci([(last["cv"] - last["iid"]) - (f["cv"] - f["iid"]) for f, last in pairs]),
        "keeps_that_raise_iid_but_lower_shift": [
            k["description"] for x in rs for prev, k in zip(x["keeps"], x["keeps"][1:])
            if k["iid"] > prev["iid"] and k["shift"] < prev["shift"] - 0.005]}


def main() -> None:
    a = parser(__doc__.splitlines()[0], seeds=2).parse_args()
    live = is_live(a.llm)
    n = 100 if a.quick else 300
    seeds = list(range(8 if a.quick else 40))
    arms = [] if live else [(v, r) for v in (0.0015, 0.005) for r in (False, True)]
    ls = pool_map(landscape_arm, [(v, r, s, n) for v, r in arms for s in seeds], a.workers) if arms else []
    tn = (4 if a.quick else 10) if live else (12 if a.quick else 30)
    out = {"config": {"landscape_experiments": 0 if live else n, "landscape_seeds": 0 if live else len(seeds),
                      "check_every": CHECK_EVERY, "tabular_experiments": tn, "llm": a.llm}, "landscape": {}}
    for v, r in arms:
        rs = [x for x in ls if x["val_sd"] == v and x["resample"] == r]
        G = np.array([x["gap_curve"] for x in rs])
        V = np.array([x["valspec_curve"] for x in rs])
        out["landscape"][f"val_sd={v},resample={r}"] = {
            "checkpoints": list(range(0, n + 1, CHECK_EVERY)), "gap_mean": G.mean(0).tolist(),
            "gap_final": ci(G[:, -1]), "gap_after_first_50": ci(G[:, 2]),
            "valspec_mean": V.mean(0).tolist(), "valspec_final": ci(V[:, -1]),
            "valspec_after_first_50": ci(V[:, 2]), "valspec_growth": ci(V[:, -1] - V[:, 2]),
            "true_final": ci([x["truth_curve"][-1] for x in rs]),
            "shift_final": ci([x["shift_curve"][-1] for x in rs])}
    t_seeds = list(range(1 if (a.quick and not live) else a.seeds))
    tb = pool_map(tabular_arm, [(r, s, tn, a.llm) for r in (False, True) for s in t_seeds], 1 if live else a.workers)
    out["tabular"] = {f"resample={r}": tabular_summary([x for x in tb if x["resample"] == r]) for r in (False, True)}
    L, fixed = out["landscape"], out["tabular"]["resample=False"]
    verdict = {
        "tabular_cv_minus_iid_fixed": [fixed["cv_minus_iid_baseline"]["mean"], fixed["cv_minus_iid_final"]["mean"]],
        "tabular_gap_growth_fixed": fixed["cv_minus_iid_final"]["mean"] - fixed["cv_minus_iid_baseline"]["mean"],
        "tabular_gap_growth_fixed_ci": fixed["cv_minus_iid_growth"],
        "tabular_shift_trap_keeps": fixed["keeps_that_raise_iid_but_lower_shift"],
    }
    if not live:
        big_fixed, big_res = L["val_sd=0.005,resample=False"], L["val_sd=0.005,resample=True"]
        small_fixed, small_res = L["val_sd=0.0015,resample=False"], L["val_sd=0.0015,resample=True"]
        verdict.update({
            "landscape_gap_grows_fixed_val": bool(big_fixed["gap_final"]["mean"] >
                                                  big_fixed["gap_after_first_50"]["mean"]),
            "landscape_gap_final_fixed_vs_resampled": [big_fixed["gap_final"]["mean"], big_res["gap_final"]["mean"]],
            "landscape_small_valsd_gap_final": small_fixed["gap_final"]["mean"],
            "resampling_shrinks_gap": bool(big_res["gap_final"]["mean"] < big_fixed["gap_final"]["mean"]),
            "landscape_valspec_50_vs_300_fixed": [big_fixed["valspec_after_first_50"]["mean"],
                                                  big_fixed["valspec_final"]["mean"]],
            "landscape_valspec_growth_fixed_ci": big_fixed["valspec_growth"],
            "landscape_valspec_final_fixed_vs_resampled": [big_fixed["valspec_final"]["mean"],
                                                           big_res["valspec_final"]["mean"]],
            "landscape_valspec_final_small_sd_fixed_vs_resampled": [small_fixed["valspec_final"]["mean"],
                                                                    small_res["valspec_final"]["mean"]],
        })
        verdict["valspec_grows_with_decisions"] = bool(big_fixed["valspec_growth"]["lo"] > 0)
        vs50, vs300 = verdict["landscape_valspec_50_vs_300_fixed"]
        vfix, vres = verdict["landscape_valspec_final_fixed_vs_resampled"]
        verdict["claim_reproduced"] = (
            f"landscape (val-specific sd 0.005): the selected val-specific component of the incumbent goes "
            f"{vs50:.4f} -> {vs300:.4f} from 50 to {n} decisions under a fixed val split "
            f"({'grows, CI excludes 0' if verdict['valspec_grows_with_decisions'] else 'no significant growth'}); "
            f"{vfix:.4f} fixed vs {vres:.4f} with val re-sampled every {CHECK_EVERY}. "
            f"Tabular at {tn} decisions: CV-minus-iid gap growth {verdict['tabular_gap_growth_fixed']:+.4f} AUC "
            "(a null result, as in the xgboost port's evidence)")
    else:
        verdict["usage"] = [r["usage"] for r in tb]
    out["verdict"] = verdict
    name = "e6_val_reuse" + suffix(a.llm, a.quick)
    out["figure"] = str(figure(out, name))
    report(name, out, default=str)


def figure(out: dict, name: str):
    p = plt()
    fig, axes = p.subplots(1, 3, figsize=(18, 4.5))
    style = {False: "-", True: "--"}
    col = {0.0015: "#1a73e8", 0.005: "#d93025"}
    for ax, key_c, ylab, title in ((axes[0], "gap_mean", "expected test_iid loss - recorded val loss",
                                    "landscape: optimism of the kept chain (incl. noise luck)"),
                                   (axes[1], "valspec_mean", "expected test_iid - expected val (noise-free)",
                                    "landscape: val-specific overfitting only")):
        for key, v in out["landscape"].items():
            sd = float(key.split(",")[0].split("=")[1])
            r = key.endswith("True")
            ax.plot(v["checkpoints"], v[key_c], style[r], color=col[sd],
                    label=f"val-specific sd {sd}, {'re-sampled val' if r else 'fixed val'}")
        ax.axhline(0, color="grey", lw=0.8)
        ax.set_xlabel("experiment #")
        ax.set_ylabel(ylab, fontsize=8)
        ax.set_title(title, fontsize=9)
        if out["landscape"]:
            ax.legend(fontsize=7)
    ax = axes[2]
    for key, v in out["tabular"].items():
        for run in v["runs"]:
            ax.step([k["exp"] for k in run["keeps"]], [k["cv"] - k["iid"] for k in run["keeps"]], where="post",
                    color="#d93025" if key.endswith("False") else "#1e8e3e", alpha=0.7,
                    label=f"{key} seed {run['seed']}")
    ax.set_xlabel("experiment #")
    ax.set_ylabel("CV AUC - hidden iid AUC (kept chain)")
    ax.set_title("tabular: CV vs hidden iid test")
    ax.legend(fontsize=7)
    return save_fig(fig, name)


if __name__ == "__main__":
    main()
