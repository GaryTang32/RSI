"""S5 - "Keep only what survives across environments" transfers better than single-environment selection.

Usage: python experiments/metaharness-solpi/s5_survive.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import argparse
from functools import partial

from _agentworld import frozen_variants, kind_counts, metrics
from _common import fmt, fresh_dir, live_llm, paired, parse_args, pool_map, save, summarize, table
from rsi.domains.agentworld import MockAgentLLM, make_domain
from rsi.solpi import AGENTWORLD_IDEAS, Config, GateSpec, compose, run

PROTOCOLS = {"single_env_eta": GateSpec(mode="eta_better", families=("repofix",)),
             "single_env_dual": GateSpec(mode="aggregate", families=("repofix",)),
             "multi_aggregate": GateSpec(mode="aggregate"),
             "multi_per_family": GateSpec(mode="per_family")}
MULTI = ("multi_aggregate", "multi_per_family")
OFFLINE = argparse.Namespace(quick=False, live=False, llm="sim")   # full offline settings (S5b, S5c)


def _eta(m: dict) -> float:
    return m["cost"] / max(1e-9, m["score"])


def job(args, spec):
    proto, seed = spec
    q = args.quick or args.live
    dom = make_domain(seed=seed, n_train=3 if q else 8, n_accept=3 if q else 6, n_final=3 if q else 8, n_test=0)
    cfg = Config(gate=PROTOCOLS[proto], n_lineages=3 if args.live else 10, max_iters=2 if args.live else 4)
    res = run(dom, dom.seed_artifact(), llm_task=MockAgentLLM("A"),
              llm_propose=live_llm(args.llm) if args.live else None, config=cfg,
              out_dir=fresh_dir("s5", f"{proto}_s{seed}_{args.llm.replace(':', '_')}"))
    r = res.meta["rounds"][0]
    base = dom.seed_artifact()
    # the training-admitted set composed WITHOUT the firewall (ablation)
    no_fw = [art for art, _ in frozen_variants(dom, r).values()]
    composed_no_fw = compose(base, no_fw)[0] if no_fw else base
    out = {"protocol": proto, "seed": seed, "frozen": r["frozen_ideas"], "survivors": r["survivor_ideas"],
           "heldout_passed": r["heldout_passed"], "admitted_train": kind_counts(r["frozen_ideas"]),
           "admitted_final": kind_counts(r["survivor_ideas"]), "research_usage": res.usage.get("research")}
    for bk in ("A", "B"):
        llm = MockAgentLLM(bk)
        b = metrics(dom, llm, base, splits=("ood",))
        for tag, art in (("fw", res.best), ("nofw", composed_no_fw)):
            c = metrics(dom, llm, art, splits=("ood",))
            out[f"{bk}_{tag}_capability"] = c["score"] / b["score"] if b["score"] else float("nan")
            out[f"{bk}_{tag}_cost_saving"] = 1 - c["cost"] / b["cost"]
            out[f"{bk}_{tag}_token_saving"] = 1 - c["tokens"] / b["tokens"]
            out[f"{bk}_{tag}_eta_saving"] = 1 - _eta(c) / _eta(b)
    return out


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=5)
    rows = pool_map(partial(job, args), [(p, s) for s in range(args.seeds) for p in PROTOCOLS], args.workers)
    summ = {}
    for p in PROTOCOLS:
        rs = [r for r in rows if r["protocol"] == p]
        summ[p] = {k: summarize([r[k] for r in rs]) for k in rs[0] if k[:2] in ("A_", "B_")}
        for stage in ("admitted_train", "admitted_final"):
            for kind in ("general", "trick", "do_less", "dud"):
                summ[p][f"{stage}_{kind}"] = summarize([r[stage][kind] for r in rs])
    mean = {p: {k: v["mean"] for k, v in d.items()} for p, d in summ.items()}
    ok_multi = all(mean[p]["admitted_train_trick"] < mean["single_env_dual"]["admitted_train_trick"] for p in MULTI)
    ok_general = all(mean[p]["admitted_final_general"] >= 2 for p in MULTI)
    ok_single_tricks = mean["single_env_eta"]["admitted_train_trick"] > 0
    transfer = {p: (mean[p]["A_fw_capability"], mean[p]["A_fw_eta_saving"], mean[p]["B_fw_capability"],
                    mean[p]["B_fw_eta_saving"]) for p in PROTOCOLS}
    nofw = {p: (mean[p]["A_nofw_capability"], mean[p]["A_nofw_eta_saving"]) for p in PROTOCOLS}
    better = all(transfer[m][0] >= transfer["single_env_eta"][0] - 0.01 and
                 transfer[m][1] >= transfer["single_env_eta"][1] - 0.02 for m in MULTI)
    # the environment-diversity effect proper: same capability floor, one family vs all training families
    by_seed = {p: {r["seed"]: r for r in rows if r["protocol"] == p} for p in PROTOCOLS}
    seeds = sorted(by_seed["single_env_dual"])
    env_cmp = {m: {k: paired([by_seed["single_env_dual"][s][k] for s in seeds], [by_seed[m][s][k] for s in seeds])
                   for k in ("A_fw_capability", "A_fw_eta_saving", "B_fw_capability", "B_fw_eta_saving",
                             "A_nofw_capability", "A_nofw_eta_saving")}
               for m in MULTI}
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
        "; ".join(f"{p}: {mean[p]['admitted_train_general']:.1f}/{mean[p]['admitted_train_trick']:.1f}/"
                  f"{mean[p]['admitted_train_do_less']:.1f}" for p in PROTOCOLS) + \
        ". Held-out family (backend A) capability ratio / eta saving WITH firewall: " + \
        "; ".join(f"{p}: {transfer[p][0]:.3f}/{transfer[p][1]:+.2f}" for p in PROTOCOLS) + \
        ". WITHOUT firewall: " + "; ".join(f"{p}: {nofw[p][0]:.3f}/{nofw[p][1]:+.2f}" for p in PROTOCOLS) + \
        ". Backend B (fw): " + "; ".join(f"{p}: {transfer[p][2]:.3f}/{transfer[p][3]:+.2f}" for p in PROTOCOLS)
    print(table([[p, f"{mean[p]['admitted_train_general']:.1f}", f"{mean[p]['admitted_train_trick']:.1f}",
                  f"{mean[p]['admitted_train_do_less']:.1f}", f"{mean[p]['admitted_final_trick']:.1f}",
                  fmt(summ[p]["A_nofw_capability"]), fmt(summ[p]["A_fw_capability"]),
                  fmt(summ[p]["A_fw_eta_saving"], 2), fmt(summ[p]["B_fw_capability"]),
                  fmt(summ[p]["B_fw_eta_saving"], 2)] for p in PROTOCOLS],
                ["protocol", "gen", "trick", "doless", "trick post-fw", "A cap (no fw)", "A cap (fw)", "A eta saving",
                 "B cap (fw)", "B eta saving"]))
    print("verdict:", verdict)
    save("s5_survive" + ("_live" if args.live else ""), {
        "claim": "Multi-environment dual gate + held-out firewall admits general mechanisms, rejects env-specific "
                 "tricks and transfers better than single-environment selection [doc; blog]",
        "config": {"llm": args.llm, "seeds": args.seeds, "protocols": {p: vars(g) for p, g in PROTOCOLS.items()},
                   "ideas": [(i.id, i.kind, i.title) for i in AGENTWORLD_IDEAS]},
        "per_seed": rows, "summary": summ, "paired_multi_minus_single_env_dual": env_cmp, "verdict": verdict,
        "caveat": "Environment families and the agent policy are simulations built for this test: which tricks break "
                  "where is a property of the stated environment model (evidence position in outputs)."}, args.out)


if __name__ == "__main__":
    main()
