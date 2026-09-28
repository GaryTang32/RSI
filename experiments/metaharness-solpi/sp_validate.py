"""SoL-Pi from-scratch validation runs after the claims-audit fixes (docs/claims/solpi.md "Fix log").

Same runs, same helpers and the same stage-A audit as ``validate_metaharness_solpi.py`` (shared with
Meta-Harness), with the protocol defaults of the fixed code:

* the predeclared gate has two capability metrics (mean score AND the fully-solved rate, 2 % each);
* lineages sweep (``Config.sweep=True``) and freeze the nondominated best-eta passing variant, so the stage-A
  ``variant_walk_rule`` (+-1 grid walk only) is replaced by the sweep-aware rule of ``LibraryProposer``;
* the idea pool has 12 ideas over all six families, so the Oracle Analysis leaves two without rollouts.

Runs::

    python experiments/metaharness-solpi/sp_validate.py solpi_agentworld_offline      # $0, re-run from scratch
    python experiments/metaharness-solpi/sp_validate.py solpi_agentworld_live_r5      # haiku, FRESH cache

``solpi_agentworld_live_r5`` repeats r4's configuration (the r1-r4 run directories are kept as recorded).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import validate_metaharness_solpi as V  # noqa: E402  (shared helpers: fresh, fresh_llm, audit_sp, ...)

from rsi.core import transfer_report  # noqa: E402
from rsi.trace import inspect  # noqa: E402

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
    from rsi.domains.agentworld import MockAgentLLM, make_domain
    from rsi.solpi import Config, GateSpec, Idea, LLMReviewer, run as sp_run
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
        ideas = [Idea("L1", "C", "Stop replaying large successful tool outputs in every later request; keep them "
                                 "recallable", oracle="replayed_large_outputs"),
                 Idea("L2", "D", "Condense long failing command logs to the lines that carry the failure, keeping "
                                 "the full log recallable", oracle="diagnostic_log_tokens")]
        cfg = Config(gate=GateSpec(mode="aggregate"), n_lineages=2, max_iters=2, ralph_max=2, workers=2,
                     shadow_monitor=True)
        res = sp_run(dom, dom.seed_artifact(), llm_task=agent, llm_propose=llm, config=cfg, out_dir=out,
                     ideas=ideas, reviewer=LLMReviewer(llm, dom, agent))
        setup = LIVE_SETUP
    else:
        raise SystemExit(f"unknown run {name!r}")
    wall = time.time() - t0
    tr = transfer_report(dom, agent, {"base": dom.seed_artifact(), "composed": res.best},
                         splits=("evolve", "holdout", "ood"), k=1, workers=2)
    report = {"run": name, "setup": setup, "wall_s": round(wall, 1), "transfer": tr,
              "rounds": [{k: v for k, v in r.items() if k not in ("heldout",)} for r in res.meta["rounds"]],
              "usage": res.usage, "generated_by": "experiments/metaharness-solpi/sp_validate.py (post claims-audit "
                                                   "fixes)"}
    if llm is not None:
        report["spend"] = {**V.split_meter(llm.meter.snapshot()), "cache_ground_truth": V.cache_spend(cache)}
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    inspect(out)
    audit = V.audit_sp(out, res, name)
    if name == "solpi_agentworld_offline":          # the stage-A walk rule predates the sweep default
        audit["checks"]["variant_walk_rule"], walk_problems = sweep_walk_check(audit["rows"])
        audit["problems"] = [p for p in audit["problems"] if not p.startswith("variant_walk_rule")] + walk_problems
        audit["all_passed"] = all(a == b for a, b in (c.split("/") for c in audit["checks"].values()))
        audit["notes"] = ["variant_walk_rule: sweep-aware rule (sp_validate.sweep_walk_check)"]
    (out / "audit.json").write_text(json.dumps(audit, indent=1, default=str))
    (out / "audit.md").write_text(V.render_audit(name, audit))
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
