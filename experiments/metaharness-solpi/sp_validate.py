"""SoL-Pi from-scratch validation runs under the post-claims-audit protocol defaults, with the stage-A audit.

Usage: python experiments/metaharness-solpi/sp_validate.py [solpi_agentworld_offline | solpi_agentworld_live_r5]
"""
from __future__ import annotations

import json
import sys
import time

import validate_metaharness_solpi as V
from rsi.domains.agentworld import MockAgentLLM, make_domain
from rsi.solpi import Config, GateSpec, LLMReviewer, run as sp_run
from rsi.trace import inspect

OFFLINE_SETUP = (
    "AgentWorld seed 0: training screen (evolve) = 8 tasks x 3 families (repofix, buildfix, logtriage); sealed "
    "holdout = 6 x 2 held-out families (configfix, datalookup) used only by the firewall; sealed ood = 8 x 2 "
    "held-out families (final, never touched by the protocol). Base = untouched Pi-like harness (no extensions). "
    "Agent backend = MockAgentLLM('A') (offline). Idea pool = all 12 AGENTWORLD_IDEAS over the six families C, P, "
    "T, D, R, M (4 released mechanisms, 3 tricks, 2 do-less shortcuts, 1 dud, 2 evaluation-family ideas); the "
    "Oracle Analysis ranks them and the top n_lineages = 10 get rollouts. Proposer = LibraryProposer, reviewer = "
    "SmokeReviewer. Predeclared aggregate dual gate: score AND solved rate within 2% (relative) AND tokens or cost "
    "saving > 2%; lineages sweep their variants and freeze the nondominated best-eta one; firewall on holdout; "
    "compose survivors. n_lineages 10, max_iters 4, ralph_max 3, k 1. Shadow monitor on holdout + ood.")
LIVE_SETUP = (
    "AgentWorld seed 0, small: evolve 3 x 3 training families, holdout 3 x 2, ood 3 x 2 held-out families. Base = "
    "untouched harness. Agent backend = MockAgentLLM('A') (offline). Two free-form ideas (no registry mechanism): "
    "the mechanism proposer/implementer (LLMMechanismProposer) AND the independent reviewer (LLMReviewer: smoke + "
    "LLM contract review) are claude haiku behind a FRESH CachedLLM; haiku writes each mechanism as a Python "
    "extension against the runtime hook API. Aggregate dual gate (score AND solved within 2%, efficiency > 2%), "
    "sweep (a lineage keeps iterating after a pass and freezes its nondominated best-eta variant), firewall on "
    "holdout. n_lineages 2, max_iters 2, ralph_max 2, review_max 2 (r4's configuration under the fixed defaults).")


def sweep_walk_check(rows: list[dict]) -> tuple[str, list[str]]:
    """``LibraryProposer`` with sweep: variant 0 first; before any pass, +1 after a capability failure or a
    rejection and -1 after "no efficiency gain"; after a pass, the smallest variant not yet evaluated."""
    good = total = 0
    problems = []
    seq: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("variant") is None or r.get("error"):
            continue
        prev = seq.setdefault(r["idea"], [])
        if not prev:
            exp = 0
        elif any(p.get("outcome") == "frozen" for p in prev):
            seen = {p["variant"] for p in prev}
            exp = min(i for i in range(len(seen) + 1) if i not in seen)
        else:
            p = prev[-1]
            exp = p["variant"] + (1 if p.get("cap_failed") or p.get("outcome") != "gate_failed" else -1)
        total += 1
        good += int(r["variant"] == exp)
        if r["variant"] != exp:
            problems.append(f"variant_walk_rule: {r['idea']}: {r['variant']} vs {exp}")
        prev.append(r)
    return f"{good}/{total}", problems


def run(name: str) -> dict:
    out = V.fresh(name)
    t0 = time.time()
    agent = MockAgentLLM("A")
    llm = cache = None
    if name == "solpi_agentworld_offline":
        dom = make_domain(seed=0, n_train=8, n_accept=6, n_final=8, n_test=0)
        cfg = Config(gate=GateSpec(mode="aggregate"), n_lineages=10, max_iters=4, workers=2, shadow_monitor=True)
        res = sp_run(dom, dom.seed_artifact(), llm_task=agent, llm_propose=None, config=cfg, out_dir=out)
        setup = OFFLINE_SETUP
    elif name == "solpi_agentworld_live_r5":
        dom = make_domain(seed=0, n_train=3, n_accept=3, n_final=3, n_test=0)
        llm, cache = V.fresh_llm(name)
        cfg = Config(gate=GateSpec(mode="aggregate"), n_lineages=2, max_iters=2, ralph_max=2, workers=2,
                     shadow_monitor=True)
        res = sp_run(dom, dom.seed_artifact(), llm_task=agent, llm_propose=llm, config=cfg, out_dir=out,
                     ideas=V.live_ideas(), reviewer=LLMReviewer(llm, dom, agent))
        setup = LIVE_SETUP
    else:
        raise SystemExit(f"unknown run {name!r}")
    report = V.sp_report(name, setup, time.time() - t0, dom, agent, res, llm, cache,
                         generated_by="experiments/metaharness-solpi/sp_validate.py (post claims-audit fixes)")
    V.write_json(out / "report.json", report)
    inspect(out)
    audit = V.audit_sp(out, res, name)
    if name == "solpi_agentworld_offline":          # the stage-A walk rule predates the sweep default
        audit["checks"]["variant_walk_rule"], walk_problems = sweep_walk_check(audit["rows"])
        audit["problems"] = [p for p in audit["problems"] if not p.startswith("variant_walk_rule")] + walk_problems
        audit["all_passed"] = all(a == b for a, b in (c.split("/") for c in audit["checks"].values()))
        audit["notes"] = ["variant_walk_rule: sweep-aware rule (sp_validate.sweep_walk_check)"]
    V.write_audit(out, name, audit)
    return report


if __name__ == "__main__":
    for n in sys.argv[1:] or ["solpi_agentworld_offline"]:
        t = time.time()
        rep = run(n)
        a = json.loads((V.OUT / n / "audit.json").read_text())
        print(f"[{n}] {time.time() - t:.1f}s audit_passed={a['all_passed']} checks={a['checks']}")
        if a["problems"]:
            print("  problems:", a["problems"][:10])
        if "spend" in rep:
            print("  spend:", rep["spend"])
        r0 = rep["rounds"][0]
        print("  chosen", r0["chosen"], "frozen", r0["frozen_ideas"], "survivors", r0["survivor_ideas"])
