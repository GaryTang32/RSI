"""E11 - collaborative autoresearch: claim before running, publish results with full code, shared best.

Usage: python experiments/autoresearch/e11_hub.py [--seeds N] [--quick] [--llm sim|claude:haiku]
"""
from _common import (SCRATCH, ci, is_live, parser, plt, report, research_agent, save_fig, suffix, time_to,  # noqa: I001
                     usage_of)

import json
import shutil
import threading
import time

import numpy as np

from rsi.autoresearch import Config, LandscapeTask, Reeval, landscape_edit_pool
from rsi.autoresearch.hub import CollaborativeLoop, SanityRule, SharedHub

SLEEP = 0.15
LIARS = {"blatant": lambda v: 0.30, "subtle": lambda v: v - 0.03}
FILTERS = {"no_filters": (False, False), "sanity_filters": (True, False), "filters+verify": (True, True)}


def cross_agent_duplicates(started: list[dict], field: str) -> int:
    """Starts whose ``field`` another agent had already started."""
    first, dup = {}, 0
    for s in started:
        dup += s[field] in first and first[s[field]] != s["agent"]
        first.setdefault(s[field], s["agent"])
    return dup


def swarm(seed: int, *, claiming=True, n_agents=4, n_exp=20, liar=None, sanity=True, verify=False, tag="",
          llm_spec: str = "sim") -> dict:
    root = SCRATCH / "e11" / f"{tag}_s{seed}"
    shutil.rmtree(root, ignore_errors=True)
    task = LandscapeTask(sleep_s=SLEEP, seed=seed)

    def verifier(src):
        return Reeval(task).run(task.seed_artifact().with_files(src), [70_000, 70_001])["mean"]

    hub = SharedHub(root / "hub", metric="val_loss", verify_delay=0.02, sanity=SanityRule() if sanity else None,
                    verifier=verifier if verify else None, verify_tol=0.008)
    loops, llms = [], []
    for i in range(n_agents):
        agent, agent_llms = research_agent(llm_spec, landscape_edit_pool(), seed=1000 * seed + i, p_repeat=0.5)
        llms += agent_llms
        cfg = Config(max_experiments=n_exp, plot=False, hidden_audit=False, seed=1000 * seed + i, overwrite=True,
                     tag=f"{tag}-a{i}")
        loops.append(CollaborativeLoop(task, agent, cfg, out_dir=root / f"agent{i}", hub=hub, agent_id=f"agent{i}",
                                       claiming=claiming, report=LIARS[liar] if liar and i == n_agents - 1 else None,
                                       llms=agent_llms))
    t0 = time.time()
    threads = [threading.Thread(target=lambda lp=lp: setattr(lp, "result", lp.run())) for lp in loops]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0
    started = sorted((s for lp in loops for s in lp.started), key=lambda s: s["t"])
    gb = hub.get("best", "metadata") or {}
    src = (hub.get("best", "train_py") or {}).get("source")
    best_art = task.seed_artifact().with_files(src) if src else task.seed_artifact()
    lb = json.loads((root / "hub" / "leaderboard.json").read_text())["entries"]
    survive = []  # does each global-best update hold up under an independent re-evaluation?
    for e in lb:
        rec = [v for _, v in hub.items("results") if v.get("agent_id") == e["agent_id"] and
               v.get("description") == e["description"]]
        if rec:
            honest = Reeval(task).run(task.seed_artifact().with_files(rec[0]["source"]), [80_000, 80_001, 80_002])
            survive.append(abs(honest["mean"] - e["val_loss"]) <= 0.01)
    n_started = max(1, len(started))
    return {"seed": seed, "claiming": claiming, "liar": liar, "sanity": sanity, "verify": verify,
            "n_started": len(started), "dup_desc_rate": cross_agent_duplicates(started, "description") / n_started,
            "dup_code_rate": cross_agent_duplicates(started, "code") / n_started, "wall_s": wall,
            "claims_refused": sum(lp.hub_stats["claims_refused"] for lp in loops),
            "ran_unclaimed": sum(lp.hub_stats["ran_unclaimed"] for lp in loops),
            "adoptions": sum(lp.hub_stats["adoptions"] for lp in loops),
            "global_best_claimed": gb.get("val_loss"), "global_best_by": gb.get("agent_id"),
            "liar_captured": bool(liar) and gb.get("agent_id") == f"agent{n_agents - 1}",
            "global_best_true": task.truth(best_art), "baseline_true": task.truth(task.seed_artifact()),
            "survive_rate": float(np.mean(survive)) if survive else float("nan"), "n_best_updates": len(lb),
            "best_trajectory": [(e["t"] - t0, e["val_loss"]) for e in lb],
            "rejections": [m for m in hub.log if m.startswith("REJECTED")][:5], "usage": usage_of(llms)}


def main() -> None:
    a = parser(__doc__.splitlines()[0], seeds=5).parse_args()
    live = is_live(a.llm)
    seeds = list(range(min(a.seeds, 1) if (a.quick and live) else 2 if a.quick else a.seeds))
    n_agents = 3 if live else 4
    n_exp = (3 if a.quick else 5) if live else (8 if a.quick else 20)
    out = {"config": {"agents": n_agents, "experiments_per_agent": n_exp, "seconds_per_run": SLEEP, "seeds": seeds,
                      "claim_ttl_s": 900, "semantic_threshold": 0.92, "similarity": "char-3gram cosine",
                      "llm": a.llm}}
    kw = {"n_agents": n_agents, "n_exp": n_exp, "llm_spec": a.llm}
    A = {c: [swarm(s, claiming=c, tag=f"claim{int(c)}", **kw) for s in seeds] for c in (True, False)}
    out["claiming"] = {("on" if c else "off"): {k: ci([r[k] for r in rs]) for k in (
        "dup_desc_rate", "dup_code_rate", "claims_refused", "ran_unclaimed", "global_best_true", "wall_s")}
        for c, rs in A.items()}
    swarms = A[True]
    single = [swarm(s, claiming=True, tag="single", **{**kw, "n_agents": 1, "n_exp": n_agents * n_exp})
              for s in seeds]
    t_sw, t_si = [], []
    for sw, si in zip(swarms, single):  # same seed: a target both arms reached
        target = max(sw["global_best_claimed"], si["global_best_claimed"])
        t_sw.append(time_to(sw["best_trajectory"], target))
        t_si.append(time_to(si["best_trajectory"], target))
    out["swarm_vs_single"] = {
        "swarm_true_best": ci([r["global_best_true"] for r in swarms]),
        "single_true_best": ci([r["global_best_true"] for r in single]),
        "swarm_minus_single_true_best": ci([sw["global_best_true"] - si["global_best_true"]
                                            for sw, si in zip(swarms, single)]),
        "swarm_time_to_common_best_s": ci(t_sw), "single_time_to_common_best_s": ci(t_si),
        "swarm_time_to_common_best_values": t_sw, "single_time_to_common_best_values": t_si,
        "swarm_wall_s": ci([r["wall_s"] for r in swarms]), "single_wall_s": ci([r["wall_s"] for r in single]),
        "swarm_traj": [r["best_trajectory"] for r in swarms], "single_traj": [r["best_trajectory"] for r in single]}
    C = {}
    for liar in (() if live else LIARS):
        for cond, (san, ver) in FILTERS.items():
            rs = [swarm(s, liar=liar, sanity=san, verify=ver, n_exp=n_exp, tag=f"{liar}_{cond}") for s in seeds]
            C[f"{liar}/{cond}"] = {"liar_captured_rate": float(np.mean([r["liar_captured"] for r in rs])),
                                   **{k: ci([r[k] for r in rs]) for k in ("survive_rate", "global_best_true",
                                                                          "global_best_claimed")},
                                   "rejections_example": rs[0]["rejections"]}
    C["honest/sanity_filters"] = {"liar_captured_rate": 0.0,  # no liar in this arm
                                  "survive_rate": ci([r["survive_rate"] for r in swarms]),
                                  "global_best_true": ci([r["global_best_true"] for r in swarms])}
    out["bogus"] = C
    on, off = out["claiming"]["on"], out["claiming"]["off"]
    S = out["swarm_vs_single"]
    verdict = {
        "dup_code_rate_on_vs_off": [on["dup_code_rate"]["mean"], off["dup_code_rate"]["mean"]],
        "dup_desc_rate_on_vs_off": [on["dup_desc_rate"]["mean"], off["dup_desc_rate"]["mean"]],
        "claiming_cuts_duplicates": bool(on["dup_code_rate"]["mean"] < off["dup_code_rate"]["mean"]),
        "time_to_common_best_s_swarm_vs_single": [S["swarm_time_to_common_best_s"]["mean"],
                                                  S["single_time_to_common_best_s"]["mean"]],
        "swarm_reaches_common_best_sooner": bool(S["swarm_time_to_common_best_s"]["mean"] <
                                                 S["single_time_to_common_best_s"]["mean"]),
        "total_wall_s_swarm_vs_single": [S["swarm_wall_s"]["mean"], S["single_wall_s"]["mean"]],
        "swarm_vs_single_true_best": [S["swarm_true_best"]["mean"], S["single_true_best"]["mean"]],
        "swarm_minus_single_true_best_ci": [S["swarm_minus_single_true_best"][k] for k in ("mean", "lo", "hi")],
    }
    if live:
        verdict["usage"] = {"claiming_on": [r["usage"] for r in swarms], "single": [r["usage"] for r in single]}
    else:
        verdict.update({
            "liar_captures_without_filters": C["blatant/no_filters"]["liar_captured_rate"],
            "blatant_liar_with_filters": C["blatant/sanity_filters"]["liar_captured_rate"],
            "subtle_liar_with_filters": C["subtle/sanity_filters"]["liar_captured_rate"],
            "subtle_liar_with_verify": C["subtle/filters+verify"]["liar_captured_rate"]})
        verdict["claim_reproduced"] = bool(verdict["claiming_cuts_duplicates"] and
                                           verdict["swarm_reaches_common_best_sooner"] and
                                           verdict["liar_captures_without_filters"] > 0.5 and
                                           verdict["subtle_liar_with_filters"] > 0.5 and
                                           verdict["subtle_liar_with_verify"] == 0.0)
    out["verdict"] = verdict
    name = "e11_hub" + suffix(a.llm, a.quick)
    out["figure"] = str(figure(out, name))
    report(name, out)


def figure(out: dict, name: str):
    p = plt()
    fig, axes = p.subplots(1, 2, figsize=(13, 4.5))
    ax = axes[0]
    keys = [k for k in out["bogus"] if k != "honest/sanity_filters"] or ["honest/sanity_filters"]
    ax.bar(range(len(keys)), [out["bogus"][k]["liar_captured_rate"] for k in keys],
           color=["#d93025" if "no_filters" in k else "#fbbc04" if "sanity" in k else "#1e8e3e" for k in keys])
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels(keys, rotation=25, fontsize=8)
    ax.set_ylabel("share of seeds where the liar owns the global best")
    ax.set_title("self-reported global best vs filters / re-verification")
    ax = axes[1]
    for key, color in (("swarm_traj", "#1e8e3e"), ("single_traj", "#5f6368")):
        for tr in out["swarm_vs_single"][key]:
            ax.step([t for t, _ in tr], [v for _, v in tr], where="post", color=color, alpha=0.6)
    cfg = out["config"]
    ax.plot([], [], color="#1e8e3e", label=f"swarm {cfg['agents']} x {cfg['experiments_per_agent']}")
    ax.plot([], [], color="#5f6368", label=f"single agent {cfg['agents'] * cfg['experiments_per_agent']}")
    ax.set_xlabel("wall-clock seconds")
    ax.set_ylabel("hub global best (claimed val_loss)")
    ax.legend(fontsize=8)
    ax.set_title("equal total compute")
    return save_fig(fig, name)


if __name__ == "__main__":
    main()
