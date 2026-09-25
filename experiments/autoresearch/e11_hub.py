"""E11 - collaborative autoresearch: claim before running, publish results with full
code, shared leaderboard and shared best.

Claim [doc]: "agents claim an experiment before running it to avoid duplicate work,
publish results and full code, and track a shared leaderboard and a shared best
version." Spec critique: the global best is self-reported (heuristic filters only).

Setup: 4 agents (threads) on one file-backed SharedHub, known-truth landscape where
each run takes 0.15 s so experiments overlap in time; each agent does 20 experiments.
A. claiming on vs off: cross-agent duplicate rate (same description / same code
   already started by another agent), claims refused, "ran anyway after 5 tries".
B. swarm (4 x 20) vs one agent (80) at equal total compute: true quality reached vs time.
C. one agent lies (blatant: reports 0.30; subtle: reports its value - 0.03) under
   {no filters, at-home sanity filters, filters + independent re-verification}: does the
   liar capture the global best, what share of global-best updates survive an independent
   re-evaluation, and what is the true quality of the swarm's final best?

Usage: python experiments/autoresearch/e11_hub.py [--seeds N] [--quick] [--llm ...]
"""
from __future__ import annotations

from _common import SCRATCH, ci, parser, plt, write  # noqa: I001

import json
import shutil
import threading
import time

import numpy as np

from rsi.autoresearch import Config, LandscapeTask, MockResearchAgent, Reeval, landscape_edit_pool
from rsi.autoresearch.hub import CollaborativeLoop, SanityRule, SharedHub

SLEEP = 0.15


def swarm(seed: int, *, claiming=True, n_agents=4, n_exp=20, liar=None, sanity=True, verify=False, tag="") -> dict:
    root = SCRATCH / "e11" / f"{tag}_s{seed}"
    shutil.rmtree(root, ignore_errors=True)
    task = LandscapeTask(sleep_s=SLEEP, seed=seed)
    verifier = None
    if verify:
        def verifier(src):
            return Reeval(task).run(task.seed_artifact().with_files(src), [70_000, 70_001])["mean"]
    hub = SharedHub(root / "hub", metric="val_loss", verify_delay=0.02, sanity=SanityRule() if sanity else None,
                    verifier=verifier, verify_tol=0.008)
    loops = []
    for i in range(n_agents):
        rep = None
        if liar == "blatant" and i == n_agents - 1:
            rep = (lambda v: 0.30)
        elif liar == "subtle" and i == n_agents - 1:
            rep = (lambda v: v - 0.03)
        ag = MockResearchAgent(landscape_edit_pool(), seed=1000 * seed + i, p_repeat=0.5)
        cfg = Config(max_experiments=n_exp, plot=False, hidden_audit=False, seed=1000 * seed + i, overwrite=True,
                     tag=f"{tag}-a{i}")
        loops.append(CollaborativeLoop(task, ag, cfg, out_dir=root / f"agent{i}", hub=hub, agent_id=f"agent{i}",
                                       claiming=claiming, report=rep))
    t0 = time.time()
    ths = [threading.Thread(target=lambda lp=lp: setattr(lp, "result", lp.run())) for lp in loops]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = time.time() - t0
    started = sorted((s for lp in loops for s in lp.started), key=lambda s: s["t"])
    seen_d, seen_c, dup_d, dup_c = {}, {}, 0, 0
    for s in started:
        if s["description"] in seen_d and seen_d[s["description"]] != s["agent"]:
            dup_d += 1
        if s["code"] in seen_c and seen_c[s["code"]] != s["agent"]:
            dup_c += 1
        seen_d.setdefault(s["description"], s["agent"])
        seen_c.setdefault(s["code"], s["agent"])
    gb = hub.get("best", "metadata") or {}
    src = (hub.get("best", "train_py") or {}).get("source")
    best_art = task.seed_artifact().with_files(src) if src else task.seed_artifact()
    lb = json.loads((root / "hub" / "leaderboard.json").read_text())["entries"]
    # independent re-evaluation of every global-best update: does the claimed number hold up?
    survive = []
    for e in lb:
        rec = [v for _, v in hub.items("results") if v.get("agent_id") == e["agent_id"] and
               v.get("description") == e["description"]]
        if not rec:
            continue
        honest = Reeval(task).run(task.seed_artifact().with_files(rec[0]["source"]), [80_000, 80_001, 80_002])["mean"]
        survive.append(abs(honest - e["val_loss"]) <= 0.01)
    # best-so-far (true quality of the hub best) over time
    traj = [(e["t"] - t0, e["val_loss"]) for e in lb]
    return {"seed": seed, "claiming": claiming, "liar": liar, "sanity": sanity, "verify": verify,
            "n_started": len(started), "dup_desc_rate": dup_d / max(1, len(started)),
            "dup_code_rate": dup_c / max(1, len(started)), "wall_s": wall,
            "claims_refused": sum(lp.hub_stats["claims_refused"] for lp in loops),
            "ran_unclaimed": sum(lp.hub_stats["ran_unclaimed"] for lp in loops),
            "adoptions": sum(lp.hub_stats["adoptions"] for lp in loops),
            "global_best_claimed": gb.get("val_loss"), "global_best_by": gb.get("agent_id"),
            "liar_captured": bool(liar) and gb.get("agent_id") == f"agent{n_agents - 1}",
            "global_best_true": task.truth(best_art), "baseline_true": task.truth(task.seed_artifact()),
            "survive_rate": float(np.mean(survive)) if survive else float("nan"), "n_best_updates": len(lb),
            "best_trajectory": traj, "rejections": [m for m in hub.log if m.startswith("REJECTED")][:5]}


def main():
    ap = parser(__doc__.splitlines()[0], seeds=5)
    a = ap.parse_args()
    seeds = list(range(2 if a.quick else a.seeds))
    n_exp = 8 if a.quick else 20
    out = {"config": {"agents": 4, "experiments_per_agent": n_exp, "seconds_per_run": SLEEP, "seeds": seeds,
                      "claim_ttl_s": 900, "semantic_threshold": 0.92, "similarity": "char-3gram cosine"}}
    A = {c: [swarm(s, claiming=c, n_exp=n_exp, tag=f"claim{int(c)}") for s in seeds] for c in (True, False)}
    out["claiming"] = {("on" if c else "off"): {
        "dup_desc_rate": ci([r["dup_desc_rate"] for r in rs]), "dup_code_rate": ci([r["dup_code_rate"] for r in rs]),
        "claims_refused": ci([r["claims_refused"] for r in rs]), "ran_unclaimed": ci([r["ran_unclaimed"] for r in rs]),
        "global_best_true": ci([r["global_best_true"] for r in rs]), "wall_s": ci([r["wall_s"] for r in rs])}
        for c, rs in A.items()}
    single = [swarm(s, n_agents=1, n_exp=4 * n_exp, claiming=True, tag="single") for s in seeds]
    out["swarm_vs_single"] = {
        "swarm_true_best": ci([r["global_best_true"] for r in A[True]]),
        "single_true_best": ci([r["global_best_true"] for r in single]),
        "swarm_wall_s": ci([r["wall_s"] for r in A[True]]), "single_wall_s": ci([r["wall_s"] for r in single]),
        "swarm_traj": [r["best_trajectory"] for r in A[True]], "single_traj": [r["best_trajectory"] for r in single]}
    C = {}
    for liar in ("blatant", "subtle"):
        for cond, (san, ver) in {"no_filters": (False, False), "sanity_filters": (True, False),
                                 "filters+verify": (True, True)}.items():
            rs = [swarm(s, liar=liar, sanity=san, verify=ver, n_exp=n_exp, tag=f"{liar}_{cond}") for s in seeds]
            C[f"{liar}/{cond}"] = {"liar_captured_rate": float(np.mean([r["liar_captured"] for r in rs])),
                                   "survive_rate": ci([r["survive_rate"] for r in rs]),
                                   "global_best_true": ci([r["global_best_true"] for r in rs]),
                                   "global_best_claimed": ci([r["global_best_claimed"] for r in rs]),
                                   "rejections_example": rs[0]["rejections"]}
    C["honest/sanity_filters"] = {"liar_captured_rate": 0.0,
                                  "survive_rate": ci([r["survive_rate"] for r in A[True]]),
                                  "global_best_true": ci([r["global_best_true"] for r in A[True]])}
    out["bogus"] = C
    on, off = out["claiming"]["on"], out["claiming"]["off"]
    verdict = {
        "dup_code_rate_on_vs_off": [on["dup_code_rate"]["mean"], off["dup_code_rate"]["mean"]],
        "dup_desc_rate_on_vs_off": [on["dup_desc_rate"]["mean"], off["dup_desc_rate"]["mean"]],
        "claiming_cuts_duplicates": bool(on["dup_code_rate"]["mean"] < off["dup_code_rate"]["mean"]),
        "swarm_faster_same_compute": bool(out["swarm_vs_single"]["swarm_wall_s"]["mean"] <
                                          out["swarm_vs_single"]["single_wall_s"]["mean"]),
        "swarm_vs_single_true_best": [out["swarm_vs_single"]["swarm_true_best"]["mean"],
                                      out["swarm_vs_single"]["single_true_best"]["mean"]],
        "liar_captures_without_filters": C["blatant/no_filters"]["liar_captured_rate"],
        "blatant_liar_with_filters": C["blatant/sanity_filters"]["liar_captured_rate"],
        "subtle_liar_with_filters": C["subtle/sanity_filters"]["liar_captured_rate"],
        "subtle_liar_with_verify": C["subtle/filters+verify"]["liar_captured_rate"],
    }
    verdict["claim_reproduced"] = bool(verdict["claiming_cuts_duplicates"] and
                                       verdict["liar_captures_without_filters"] > 0.5 and
                                       verdict["subtle_liar_with_filters"] > 0.5 and
                                       verdict["subtle_liar_with_verify"] == 0.0)
    out["verdict"] = verdict
    name = "e11_hub" + ("_quick" if a.quick else "")
    out["figure"] = str(figure(out, name))
    write(name, out)
    print(json.dumps(verdict, indent=1))


def figure(out, name):
    from _common import RESULTS

    p = plt()
    fig, axes = p.subplots(1, 2, figsize=(13, 4.5))
    ax = axes[0]
    keys = [k for k in out["bogus"] if k != "honest/sanity_filters"]
    ax.bar(range(len(keys)), [out["bogus"][k]["liar_captured_rate"] for k in keys],
           color=["#d93025" if "no_filters" in k else "#fbbc04" if "sanity" in k else "#1e8e3e" for k in keys])
    ax.set_xticks(range(len(keys)))
    ax.set_xticklabels(keys, rotation=25, fontsize=8)
    ax.set_ylabel("share of seeds where the liar owns the global best")
    ax.set_title("self-reported global best vs filters / re-verification")
    ax = axes[1]
    for tr in out["swarm_vs_single"]["swarm_traj"]:
        ax.step([t for t, _ in tr], [v for _, v in tr], where="post", color="#1e8e3e", alpha=0.6)
    for tr in out["swarm_vs_single"]["single_traj"]:
        ax.step([t for t, _ in tr], [v for _, v in tr], where="post", color="#5f6368", alpha=0.6)
    ax.plot([], [], color="#1e8e3e", label="swarm 4 x 20")
    ax.plot([], [], color="#5f6368", label="single agent 80")
    ax.set_xlabel("wall-clock seconds")
    ax.set_ylabel("hub global best (claimed val_loss)")
    ax.legend(fontsize=8)
    ax.set_title("equal total compute")
    fig.tight_layout()
    path = RESULTS / f"{name}.png"
    RESULTS.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    return path


if __name__ == "__main__":
    main()
