"""E9 - Module-level credit assignment: module-specific vs system-level feedback records.

Claim [paper §3 p.5]: the feedback function mu_f returns module-level text, so the reflection
LM can do "implicit credit assignment": it rewrites the module that is actually at fault.
Spec E9: compare module-specific against system-level feedback, crossed with RoundRobin vs All
module selection; expect module-specific feedback to fix the faulty module faster, and All to
be more rollout-efficient when faults are spread across modules [docs:faq].

RuleWorld default world (2 modules). Arms (30 seeds, B = 1500 by default):

* ``module`` - GEPA's default: ``RuleWorldDomain.reflective_record`` gives each module only
  its own properties' outputs and feedback (plus upstream notes for the reply module);
* ``system`` - ``RuleWorldDomain.system_reflective_record``: every module sees the whole
  system's outputs, decisions and feedback (the same record format), so the reflection LM has
  to work out itself which failures the rewritten module owns;

each with ``module_selector`` in {round_robin, all}. The reflection LM is the RuleWorld mock,
which sees only its prompt and does not know which module it is rewriting, so a failure it
reads for another module's property becomes a rule in the wrong prompt. Measures: true test
of the returned prompt, curve at fixed budgets, rollouts to 80% / 90% of the oracle, accept
rate, and misplaced rule lines in the returned prompts (rules that only name properties
another module handles: the owning module never reads them, they only dilute).

    python experiments/gepa/e9_credit_assignment.py [--seeds N] [--quick] [--workers W]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import RESULTS, fmt, paired, parse_args, plot_curves, pool_map, reflection_llm, save, summarize  # noqa

import numpy as np  # noqa: E402

from rsi.domains.ruleworld import make_domain  # noqa: E402
from rsi.gepa import Config, DomainAdapter, curve_at, gepa_curve, rollouts_to_target, run  # noqa: E402

GRID = [100, 200, 400, 800, 1500, 3000]
ARMS = {"module/round_robin": ("module", "round_robin"), "system/round_robin": ("system", "round_robin"),
        "module/all": ("module", "all"), "system/all": ("system", "all")}
ARGS = None


def job(spec):
    arm, seed, B = spec
    level, sel = ARMS[arm]
    d = make_domain(seed=seed)
    cfg = Config(max_metric_calls=B, seed=seed, module_selector=sel)
    record = d.reflective_record if level == "module" else d.system_reflective_record
    adapter = DomainAdapter(d, None, workers=cfg.workers, feedback=cfg.feedback, record_fn=record)
    res = run(d, d.seed_artifact(), llm_propose=reflection_llm(ARGS.llm, d.world), config=cfg, adapter=adapter)
    truth = lambda a: d.expected(a, "test")
    curve = gepa_curve(res, truth)
    oracle = d.expected(d.oracle_artifact(), "test")
    props = res.meta["n_proposals"]
    return {"arm": arm, "seed": seed, "B": B, "final_test": curve[-1][1], "at": curve_at(curve, [g for g in GRID]),
            "to80": rollouts_to_target(curve, 0.8 * oracle), "to90": rollouts_to_target(curve, 0.9 * oracle),
            "accept_rate": res.meta["n_accepted"] / props if props else None, "n_proposals": props,
            "misplaced_rule_lines": d.misplaced_rule_lines(res.best),
            "prompt_lines": sum(len([l for l in res.best.get(c, "").splitlines() if l.strip()])
                                for c in res.meta["components"])}


def main():
    global ARGS
    ARGS = a = parse_args("E9: module-level credit assignment", default_seeds=30)
    if a.live:
        raise SystemExit("E9 is an offline mechanism experiment (the mock's module-blindness is the point)")
    B = 800 if a.quick else 1500
    rows = pool_map(job, [(arm, s, B) for arm in ARMS for s in range(a.seeds)], a.workers)
    by = {arm: sorted([r for r in rows if r["arm"] == arm], key=lambda r: r["seed"]) for arm in ARMS}
    grid = [g for g in GRID if g <= B]
    summary = {arm: {"final_test": summarize([r["final_test"] for r in rs]),
                     "at_budget": {str(g): summarize([r["at"][j] for r in rs]) for j, g in enumerate(GRID) if g <= B},
                     "rollouts_to_80pct": summarize([r["to80"] for r in rs]),
                     "reached_80pct": float(np.mean([r["to80"] is not None for r in rs])),
                     "rollouts_to_90pct": summarize([r["to90"] for r in rs]),
                     "reached_90pct": float(np.mean([r["to90"] is not None for r in rs])),
                     "accept_rate": summarize([r["accept_rate"] for r in rs]),
                     "misplaced_rule_lines": summarize([r["misplaced_rule_lines"] for r in rs]),
                     "prompt_lines": summarize([r["prompt_lines"] for r in rs])}
               for arm, rs in by.items()}
    ft = lambda arm: [r["final_test"] for r in by[arm]]
    at = lambda arm, g: [r["at"][GRID.index(g)] for r in by[arm]]
    contrasts = {
        "module_minus_system (round_robin)": paired(ft("system/round_robin"), ft("module/round_robin")),
        "module_minus_system (all)": paired(ft("system/all"), ft("module/all")),
        "all_minus_round_robin (module)": paired(ft("module/round_robin"), ft("module/all")),
        "all_minus_round_robin (system)": paired(ft("system/round_robin"), ft("system/all")),
        "module_minus_system at 400 (round_robin)": paired(at("system/round_robin", 400), at("module/round_robin", 400)),
        "module_minus_system at 800 (round_robin)": paired(at("system/round_robin", 800), at("module/round_robin", 800)),
    }
    out = {"experiment": "E9 module-level credit assignment", "llm": a.llm,
           "config": {"budget": B, "seeds": a.seeds, "arms": {k: list(v) for k, v in ARMS.items()}, "grid": grid,
                      "world": "RuleWorld default (2 modules, rich mu_f, partial scoring)"},
           "summary": summary, "paired": contrasts, "raw": rows}

    def pd(k):
        c = contrasts[k]
        return f"{c['mean_diff']:+.3f} [{c['lo']:+.3f}, {c['hi']:+.3f}]"

    s = summary
    out["verdict"] = (
        f"B={B}, {a.seeds} seeds. Final true test: module/round_robin {fmt(s['module/round_robin']['final_test'])}, "
        f"system/round_robin {fmt(s['system/round_robin']['final_test'])}, module/all {fmt(s['module/all']['final_test'])}, "
        f"system/all {fmt(s['system/all']['final_test'])}. Module-specific - system-level: "
        f"{pd('module_minus_system (round_robin)')} (round-robin), {pd('module_minus_system (all)')} (all); at 400 "
        f"rollouts {pd('module_minus_system at 400 (round_robin)')}, at 800 {pd('module_minus_system at 800 (round_robin)')}. "
        f"Misplaced rule lines in the returned prompts: module {s['module/round_robin']['misplaced_rule_lines']['mean']:.1f} "
        f"vs system {s['system/round_robin']['misplaced_rule_lines']['mean']:.1f} (round-robin). All - RoundRobin: "
        f"{pd('all_minus_round_robin (module)')} (module-specific), {pd('all_minus_round_robin (system)')} (system-level). "
        f"Rollouts to 90% of the oracle: module/round_robin {s['module/round_robin']['rollouts_to_90pct']['mean']:.0f} "
        f"(reached {s['module/round_robin']['reached_90pct']:.0%}) vs system/round_robin "
        f"{s['system/round_robin']['rollouts_to_90pct']['mean']:.0f} (reached {s['system/round_robin']['reached_90pct']:.0%}).")
    save("e9_credit_assignment", out, a.out)
    plot_curves(RESULTS / "e9_credit_assignment.png", grid, {arm: [r["at"][:len(grid)] for r in rs] for arm, rs in by.items()},
                f"E9: module-specific vs system-level feedback records (B={B})")
    print(out["verdict"])


if __name__ == "__main__":
    main()
