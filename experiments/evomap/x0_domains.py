"""X0: the local gene loop is domain-agnostic (one ``run()`` on three rsi.core domains).

For each domain the same ``rsi.evomap.run(domain, harness, ...)`` evolves a gene library on the decision split,
then :func:`rsi.evomap.evaluate_library` compares the unchanged harness without genes vs with the library routed
per task (``GeneRoutedDomain``) on splits the loop never saw. Domains: GeneWorld (simulated), katas (mock coder,
real sandboxed hidden tests), agentqa (the shared harness domain with its SimModel; genes appended to
``prompts/system.md``). Modes: ``safe`` and ``faithful``.

Run: python experiments/evomap/x0_domains.py [--llm sim|claude:haiku] [--seeds N] [--quick]
(``--llm claude:haiku`` runs the katas domain with a live solver + gene writer.)
"""
from __future__ import annotations

import json
import random

from _common import FAITHFUL_HINT, SAFE_HINT, cached_llm, fmt, live, paired, parse_args, pmap, save, summarize

from rsi.core import MockLLM
from rsi.evomap import AppendInjector, Config, evaluate_library, run

AGENTQA_GENES = [
    {"id": "gene_reason_verify", "category": "optimize", "signals_match": ["task:numeric"],
     "summary": "Reason step by step and verify before answering.",
     "strategy": ["Think step by step before answering.", "Verify each intermediate result (double-check).",
                  "End with a line 'ANSWER: <value>'."], "avoid": ["Answering without working through the steps."]},
    {"id": "gene_be_brief", "category": "optimize", "signals_match": ["task:numeric"],
     "summary": "Keep answers short.", "strategy": ["Answer in one line.", "Do not explain.", "Stop early."],
     "avoid": ["Long answers."]},
    {"id": "gene_answer_format", "category": "optimize", "signals_match": ["task:numeric"],
     "summary": "State the answer in a fixed format.", "strategy": ["Compute the result.", "Double-check it.",
                                                                     "Write 'ANSWER: <value>' as the last line."],
     "avoid": ["Mixing words into the final number."]},
]


def agentqa_proposer(mode: str, seed: int):
    def respond(prompt, system, s, i):
        g = dict(random.Random(f"{seed}-{s}-{i}").choice(AGENTQA_GENES))
        g["validation"] = ["rsi-taskcheck --n 12"] if mode == "safe" else ["python --version"]
        return "```json\n" + json.dumps(g) + "\n```"
    return MockLLM(respond, name="agentqa-proposer")


def setup(domain: str, mode: str, seed: int, args):
    hint = SAFE_HINT if mode == "safe" else FAITHFUL_HINT
    if domain == "geneworld":
        from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, WorldConfig, make_domain
        dom = make_domain(WorldConfig(seed=seed))
        return (dom, dom.seed_artifact(), GeneWorldModel(0.0), GeneWorldProposer(dom.world, 0.6, 0.3), None,
                Config(cycles=40 if args.quick else 96, mode=mode, seed=seed, validation_hint=hint),
                ("holdout",), 2)
    if domain == "katas":
        from rsi.domains.katas import KataMockProposer, KataSimSolver, KatasDomain, seed_harness
        dom = KatasDomain()
        if live(args):
            llm = cached_llm(args.llm)
            return (dom, seed_harness(), llm, llm, None, Config(cycles=6, mode=mode, seed=seed, validation_hint=hint),
                    ("holdout",), 1)
        return (dom, seed_harness(), KataSimSolver(0.0, name=f"kata-sim-{seed}"), KataMockProposer(0.7, 0.3),
                None, Config(cycles=15 if args.quick else 30, mode=mode, seed=seed, validation_hint=hint),
                ("holdout",), 3)
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    dom = AgentQADomain(make_suite(seed=seed))
    return (dom, dom.seed_artifact(), SimModel(dom.tasks), agentqa_proposer(mode, seed),
            AppendInjector("prompts/system.md"),
            Config(cycles=10 if args.quick else 20, mode=mode, seed=seed, taskcheck_n=12, taskcheck_k=2),
            ("holdout", "ood"), 2)


def one(job):
    domain, mode, seed, args = job
    dom, harness, model, prop, inj, cfg, splits, k = setup(domain, mode, seed, args)
    res = run(dom, harness, llm_task=model, llm_propose=prop, config=cfg, injector=inj)
    rep = evaluate_library(dom, model, res, splits=splits, k=k, workers=2, injector=inj)
    row = {"domain": domain, "mode": mode, "seed": seed, "n_genes": res.meta["n_genes"],
           "audit_ok": res.meta["audit"]["ok"], "loop_solve_rate": sum(r["task_success"] for r in res.trajectory) /
           max(1, len(res.trajectory)), "usage_calls": res.usage["_total"]["calls"]}
    for sp in splits:
        row[f"{sp}_no_genes"] = rep["splits"][sp]["no_genes"]["S"]
        row[f"{sp}_library"] = rep["splits"][sp]["gene_library"]["S"]
    return row


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=10)
    domains = ["katas"] if live(args) else ["geneworld", "katas", "agentqa"]
    modes = ["safe"] if live(args) else ["safe", "faithful"]
    rows = pmap(one, [(d, m, s, args) for d in domains for m in modes for s in range(args.seeds)], args.workers)
    summ, verdict = {}, {}
    for d in domains:
        for m in modes:
            rs = [r for r in rows if r["domain"] == d and r["mode"] == m]
            keys = [k for k in rs[0] if k not in ("domain", "mode", "seed")]
            summ[f"{d}/{m}"] = {k: summarize([float(r[k]) for r in rs]) for k in keys}
            p = paired([r["holdout_no_genes"] for r in rs], [r["holdout_library"] for r in rs])
            summ[f"{d}/{m}"]["holdout_gain"] = p
            verdict[f"{d}/{m}_holdout_gain_ci_excludes_0"] = p["lo"] > 0 if p["n"] >= 2 else None
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "domains": domains, "modes": modes}, "raw": rows,
           "summary": summ, "verdict": verdict}
    save("x0_domains" + ("_live" if live(args) else ""), out, args.out)
    for key, s in summ.items():
        g = s["holdout_gain"]
        print(f"  {key:22s} holdout no-genes {fmt(s['holdout_no_genes'])} -> library {fmt(s['holdout_library'])} "
              f"(paired gain {g['mean_diff']:+.3f} [{g['lo']:+.3f}, {g['hi']:+.3f}]) genes {fmt(s['n_genes'], 1)}")
    print("verdict:", verdict)


if __name__ == "__main__":
    main()
