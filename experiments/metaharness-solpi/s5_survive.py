"""S5 - "Keep only what survives across environments" transfers better than single-environment selection.

Claim [doc; blog:Method; ye-blog]: searching for efficiency under a capability floor across varied environment
families, with a one-way held-out firewall, admits general mechanisms and rejects environment-specific
tricks, so the surviving stack transfers to a held-out family and a second backend.

Idea pool (10): the 4 released mechanisms (ActionFusion, ObservationPack, EPR, OCC), 3 environment-specific
tricks (tail trim, head trim, hard-coded quiet test runner), 2 do-less shortcuts (skip verification, turn
cap) and a dud. Protocols, each with the HoldoutFirewall on held-out-family acceptance tasks:
  (a) single_env_eta   - repofix only, keep if cost/score improves (no capability floor);
  (b) single_env_dual  - repofix only, dual gate (2% capability tolerance, >= 2% efficiency gain);
  (c) multi_aggregate  - repofix + buildfix + logtriage, dual gate on the pooled screen (faithful);
  (d) multi_per_family - same families, dual gate in every family (the literal "survives everywhere").
Composed stacks (with and without the firewall) are evaluated once on the FINAL held-out tasks (configfix +
datalookup) with the search backend A and the unseen backend B.

    python experiments/metaharness-solpi/s5_survive.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _agentworld import agg, row, trials_of  # noqa: E402
from _common import fmt, fresh_dir, live_llm, paired, parse_args, pool_map, save, summarize, table  # noqa: E402

import numpy as np  # noqa: E402

from rsi.domains.agentworld import MockAgentLLM, make_domain  # noqa: E402
from rsi.solpi import AGENTWORLD_IDEAS, Config, GateSpec, compose, run  # noqa: E402

PROTOCOLS = {"single_env_eta": GateSpec(mode="eta_better", families=("repofix",)),
             "single_env_dual": GateSpec(mode="aggregate", families=("repofix",)),
             "multi_aggregate": GateSpec(mode="aggregate"),
             "multi_per_family": GateSpec(mode="per_family")}
KIND = {i.id: i.kind for i in AGENTWORLD_IDEAS}
ARGS = None


def job(spec):
    proto, seed = spec
    q = ARGS.quick or ARGS.live
    dom = make_domain(seed=seed, n_train=3 if q else 8, n_accept=3 if q else 6, n_final=3 if q else 8, n_test=0)
    cfg = Config(gate=PROTOCOLS[proto], n_lineages=3 if ARGS.live else 10, max_iters=2 if ARGS.live else 4)
    res = run(dom, dom.seed_artifact(), llm_task=MockAgentLLM("A"),
              llm_propose=live_llm(ARGS.llm) if ARGS.live else None, config=cfg,
              out_dir=fresh_dir("s5", f"{proto}_s{seed}_{ARGS.llm.replace(':', '_')}"))
    r = res.meta["rounds"][0]
    base = dom.seed_artifact()
    frozen_ids = r["frozen_ideas"]
    surv_ids = r["survivor_ideas"]
    # the training-admitted set composed WITHOUT the firewall (ablation)
    lin = {l["idea"]: l for l in r["lineages"]}
    no_fw_arts = []
    for iid in frozen_ids:
        it = [x for x in lin[iid]["iterations"] if x.get("outcome") == "frozen"][-1]
        idea = next(i for i in AGENTWORLD_IDEAS if i.id == iid)
        params = idea.grid[it["variant"]] if idea.grid else {}
        no_fw_arts.append(dom.harness(idea.mechanism, **{idea.mechanism: params}))
    composed_no_fw, _ = compose(base, no_fw_arts) if no_fw_arts else (base, [])
    out = {"protocol": proto, "seed": seed, "frozen": frozen_ids, "survivors": surv_ids,
           "heldout_passed": r["heldout_passed"],
           "admitted_train": {k: sum(KIND.get(i) == k for i in frozen_ids) for k in ("general", "trick", "do_less",
                                                                                      "dud")},
           "admitted_final": {k: sum(KIND.get(i) == k for i in surv_ids) for k in ("general", "trick", "do_less",
                                                                                    "dud")},
           "research_usage": res.usage.get("research")}
    for bk in ("A", "B"):
        llm = MockAgentLLM(bk)
        b = agg([row(t) for t in trials_of(dom, llm, base, splits=("ood",))])
        for tag, art in (("fw", res.best), ("nofw", composed_no_fw)):
            c = agg([row(t) for t in trials_of(dom, llm, art, splits=("ood",))])
            out[f"{bk}_{tag}_capability"] = c["score"] / b["score"] if b["score"] else float("nan")
            out[f"{bk}_{tag}_cost_saving"] = 1 - c["cost"] / b["cost"]
            out[f"{bk}_{tag}_token_saving"] = 1 - c["tokens"] / b["tokens"]
            out[f"{bk}_{tag}_eta_saving"] = 1 - (c["cost"] / max(1e-9, c["score"])) / (b["cost"] / max(1e-9, b["score"]))
    return out


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0], default_seeds=5)
    rows = pool_map(job, [(p, s) for s in range(ARGS.seeds) for p in PROTOCOLS], ARGS.workers)
    summ = {}
    for p in PROTOCOLS:
        rs = [r for r in rows if r["protocol"] == p]
        d = {k: summarize([r[k] for r in rs]) for k in rs[0] if k[:2] in ("A_", "B_")}
        for stage in ("admitted_train", "admitted_final"):
            for kind in ("general", "trick", "do_less", "dud"):
                d[f"{stage}_{kind}"] = summarize([r[stage][kind] for r in rs])
        summ[p] = d
    ok_multi = all(summ[p]["admitted_train_trick"]["mean"] < summ["single_env_dual"]["admitted_train_trick"]["mean"]
                   for p in ("multi_aggregate", "multi_per_family"))
    ok_general = all(summ[p]["admitted_final_general"]["mean"] >= 2 for p in ("multi_aggregate", "multi_per_family"))
    ok_single_tricks = summ["single_env_eta"]["admitted_train_trick"]["mean"] > 0
    transfer = {p: (summ[p]["A_fw_capability"]["mean"], summ[p]["A_fw_eta_saving"]["mean"],
                    summ[p]["B_fw_capability"]["mean"], summ[p]["B_fw_eta_saving"]["mean"]) for p in PROTOCOLS}
    nofw = {p: (summ[p]["A_nofw_capability"]["mean"], summ[p]["A_nofw_eta_saving"]["mean"]) for p in PROTOCOLS}
    better = all(transfer[m][0] >= transfer["single_env_eta"][0] - 0.01 and
                 transfer[m][1] >= transfer["single_env_eta"][1] - 0.02 for m in ("multi_aggregate",
                                                                                   "multi_per_family"))
    # the environment-diversity effect proper: same capability floor, one family vs all training families
    by_seed = {p: {r["seed"]: r for r in rows if r["protocol"] == p} for p in PROTOCOLS}
    seeds = sorted(by_seed["single_env_dual"])
    env_cmp = {m: {k: paired([by_seed["single_env_dual"][s_][k] for s_ in seeds], [by_seed[m][s_][k] for s_ in seeds])
                   for k in ("A_fw_capability", "A_fw_eta_saving", "B_fw_capability", "B_fw_eta_saving",
                             "A_nofw_capability", "A_nofw_eta_saving")}
               for m in ("multi_aggregate", "multi_per_family")}
    enough = len(seeds) >= 3                     # a bootstrap CI over fewer seeds is not evidence
    env_better = enough and any(env_cmp[m][k].get("lo", -1) > 0 for m in env_cmp for k in env_cmp[m])
    env_worse = enough and any(env_cmp[m][k].get("hi", 1) < 0 for m in env_cmp for k in env_cmp[m])
    vs_naive = ok_multi and ok_general and ok_single_tricks and better
    verdict = ("REPRODUCED" if vs_naive and env_better and not env_worse else "PARTIAL" if vs_naive else
               "NOT REPRODUCED") + \
        (": multi-family dual gate + firewall beats single-environment efficiency-only selection" if vs_naive else
         ": multi-family protocols do not beat single-environment efficiency-only selection") + \
        ("; vs the single-environment DUAL gate (same capability floor) the extra training environments give a "
         "significant held-out gain" if env_better and not env_worse else
         "; vs the single-environment DUAL gate (same capability floor) the extra training environments give NO "
         "significant held-out gain (paired CIs include 0)" + (" and a significant loss somewhere" if env_worse
                                                                 else "")) + \
        ". Training-gate admissions (general/trick/do-less) = " + \
        "; ".join(f"{p}: {summ[p]['admitted_train_general']['mean']:.1f}/{summ[p]['admitted_train_trick']['mean']:.1f}/"
                  f"{summ[p]['admitted_train_do_less']['mean']:.1f}" for p in PROTOCOLS) + \
        ". Held-out family (backend A) capability ratio / eta saving WITH firewall: " + \
        "; ".join(f"{p}: {transfer[p][0]:.3f}/{transfer[p][1]:+.2f}" for p in PROTOCOLS) + \
        ". WITHOUT firewall: " + "; ".join(f"{p}: {nofw[p][0]:.3f}/{nofw[p][1]:+.2f}" for p in PROTOCOLS) + \
        ". Backend B (fw): " + "; ".join(f"{p}: {transfer[p][2]:.3f}/{transfer[p][3]:+.2f}" for p in PROTOCOLS)
    print(table([[p, f"{summ[p]['admitted_train_general']['mean']:.1f}", f"{summ[p]['admitted_train_trick']['mean']:.1f}",
                  f"{summ[p]['admitted_train_do_less']['mean']:.1f}", f"{summ[p]['admitted_final_trick']['mean']:.1f}",
                  fmt(summ[p]["A_nofw_capability"]), fmt(summ[p]["A_fw_capability"]), fmt(summ[p]["A_fw_eta_saving"], 2),
                  fmt(summ[p]["B_fw_capability"]), fmt(summ[p]["B_fw_eta_saving"], 2)] for p in PROTOCOLS],
                ["protocol", "gen", "trick", "doless", "trick post-fw", "A cap (no fw)", "A cap (fw)", "A eta saving",
                 "B cap (fw)", "B eta saving"]))
    print("verdict:", verdict)
    save("s5_survive" + ("_live" if ARGS.live else ""), {
        "claim": "Multi-environment dual gate + held-out firewall admits general mechanisms, rejects env-specific "
                 "tricks and transfers better than single-environment selection [doc; blog]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "protocols": {p: vars(g) for p, g in PROTOCOLS.items()},
                   "ideas": [(i.id, i.kind, i.title) for i in AGENTWORLD_IDEAS]},
        "per_seed": rows, "summary": summ, "paired_multi_minus_single_env_dual": env_cmp, "verdict": verdict,
        "caveat": "Environment families and the agent policy are simulations built for this test: which tricks break "
                  "where is a property of the stated environment model (evidence position in outputs)."}, ARGS.out)


if __name__ == "__main__":
    main()
