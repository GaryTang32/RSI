"""From-scratch validation runs of Meta-Harness and SoL-Pi with the per-iteration audit trace.

Each run starts from the untouched seed artifact(s) of its domain, in a fresh run directory
``validation/metaharness-solpi/<run>/`` (deleted first) and - for live runs - a fresh LLM cache
``validation/metaharness-solpi/.cache_<run>/`` (deleted first; nothing from ``.rsi_cache``). The loop
writes ``trace.jsonl`` (rsi.trace format) and the shadow monitor scores every new incumbent on the
sealed splits (never shown to the loop). This script then writes, per run:

* ``TRACE.md``      - :func:`rsi.trace.inspect`;
* ``report.json``   - seed vs final on every split via :func:`rsi.core.transfer_report` (report-only
                      unsealing), spend from the meters and from the fresh cache's entries;
* ``audit.json`` / ``audit.md`` - one row per iteration re-derived from trace + ledger + store, with
                      independent checks (see ``audit_mh`` / ``audit_sp``).

Runs::

    python experiments/metaharness-solpi/validate_metaharness_solpi.py mh_memoclassify_offline
    python experiments/metaharness-solpi/validate_metaharness_solpi.py solpi_agentworld_offline
    python experiments/metaharness-solpi/validate_metaharness_solpi.py mh_agentqa_live
    python experiments/metaharness-solpi/validate_metaharness_solpi.py solpi_agentworld_live
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi.core import CachedLLM, ClaudeCLI, transfer_report  # noqa: E402
from rsi.core.ledger import Ledger  # noqa: E402
from rsi.trace import inspect, load_trace  # noqa: E402

OUT = ROOT / "validation" / "metaharness-solpi"


def fresh(name: str) -> Path:
    d = OUT / name
    if d.exists():
        shutil.rmtree(d)
    return d


def fresh_llm(name: str, timeout_s: float = 240.0) -> tuple[CachedLLM, Path]:
    cache = OUT / f".cache_{name}"
    if cache.exists():
        shutil.rmtree(cache)
    return CachedLLM(ClaudeCLI("haiku", timeout_s=timeout_s), cache), cache


def cache_spend(cache: Path) -> dict:
    """Ground truth of money spent: every cache miss wrote one entry with its real usage (this also
    covers calls made in forked children - e.g. the interface validator's smoke - which the parent's
    meters cannot see)."""
    n, usd, tin, tout = 0, 0.0, 0, 0
    for p in cache.rglob("*.json"):
        try:
            u = json.loads(p.read_text())["usage"]
        except (OSError, ValueError, KeyError):
            continue
        n += 1
        usd += float(u.get("cost_usd", 0.0))
        tin += int(u.get("input_tokens", 0))
        tout += int(u.get("output_tokens", 0))
    return {"entries": n, "usd": round(usd, 4), "input_tokens": tin, "output_tokens": tout}


def split_meter(meter_snapshot: dict) -> dict:
    """Loop vs shadow-monitor spend from a CachedLLM meter snapshot (roles ``shadow:*`` = monitor)."""
    loop = {"usd": 0.0, "calls": 0}
    shadow = {"usd": 0.0, "calls": 0}
    for role, u in meter_snapshot.items():
        if role.startswith("_") or role.endswith(":cached"):
            continue
        tgt = shadow if role.startswith("shadow:") else loop
        tgt["usd"] += u.get("cost_usd", 0.0)
        tgt["calls"] += u.get("calls", 0)
    return {"loop": {k: round(v, 4) if isinstance(v, float) else v for k, v in loop.items()},
            "shadow_monitor": {k: round(v, 4) if isinstance(v, float) else v for k, v in shadow.items()}}


# ============================================================================ Meta-Harness
def run_mh(name: str) -> dict:
    from rsi.metaharness import Config, run
    out = fresh(name)
    t0 = time.time()
    if name == "mh_memoclassify_offline":
        from rsi.domains.memoclassify import make_domain
        dom = make_domain(seed=0)
        seed = dom.seed_artifact("fewshot_all")
        baselines = dom.baselines()                       # H0 = untouched no_memory + fewshot_all (release)
        llm_task = dom.make_model("A")
        cfg = Config(iterations=6, k=2, history_mode="full", seed=0)
        res = run(dom, seed, llm_task=llm_task, llm_propose=None, config=cfg, out_dir=out, baselines=baselines)
        rep_llm, report_splits, cache = llm_task, ("evolve", "test", "ood"), None
        setup = ("MemoClassify seed 0 (scale 1.0): 3 search datasets (evolve = their val parts), sealed test = "
                 "their test parts, sealed ood = 4 unseen datasets. Frozen model MemoLM-A (deterministic). "
                 "H0 = the two untouched release baselines no_memory + fewshot_all (seed = fewshot_all). "
                 "Proposer = MockProposer (offline, reads only what the view exposes). Config: N = 6 iterations, "
                 "k = 2, history_mode = full, objectives (score, context_cost), validate on, no leakage screen "
                 "(faithful), test evaluated once by finalize(). Shadow monitor on the sealed ood split.")
    elif name in ("mh_agentqa_live", "mh_agentqa_live_r2"):
        from rsi.domains.agentqa import AgentQADomain, make_suite
        dom = AgentQADomain(make_suite(n_evolve=10, n_holdout=6, n_ood_per_family=1, seed=0))
        seed = AgentQADomain.seed_artifact()
        seed.meta["name"] = "seed"
        llm, cache = fresh_llm(name)
        cfg = Config(iterations=3, k=2, history_mode="full", seed=0, test_splits=("holdout", "ood"),
                     cost_metric="tokens", workers=4, validate_timeout_s=240.0, shadow_workers=4)
        res = run(dom, seed, llm_task=llm, llm_propose=llm, config=cfg, out_dir=out, proposer_kind="rewrite")
        rep_llm, report_splits = llm, ("evolve", "holdout", "ood")
        setup = ("AgentQA suite seed 0: evolve 10 (numeric), sealed holdout 6 (numeric), sealed ood 4 (dates / "
                 "numbertheory / strings / lists, 1 each). Seed = AgentQADomain.seed_artifact() (one direct model "
                 "call, 'You are a helpful assistant.'), H0 = {seed}. Task model AND proposer = claude haiku "
                 "(ClaudeCLI('haiku') behind a fresh CachedLLM). Proposer = RewriteProposer (one completion over "
                 "the rendered full history, 60k-char budget) - NOT the paper's coding agent. Config: N = 3, k = 2, "
                 "history full, objectives (score, context_cost) with cost = tokens per trial, validation smoke "
                 "timeout 240 s (it calls the model), finalize on holdout + ood. Shadow monitor on holdout + ood.")
    else:
        raise SystemExit(name)
    wall = time.time() - t0
    loop = res.loop
    best = res.meta["best_system"]
    arms = {"seed": seed, f"best={best}": res.best}
    if name == "mh_memoclassify_offline":
        arms = {"seed(fewshot_all)": seed, "no_memory": baselines["no_memory"], f"best={best}": res.best}
    before = rep_llm.meter.total().cost_usd if hasattr(rep_llm, "meter") else 0.0
    tr = transfer_report(dom, rep_llm, arms, splits=report_splits, k=1, workers=4)
    report_usd = (rep_llm.meter.total().cost_usd if hasattr(rep_llm, "meter") else 0.0) - before
    report = {"run": name, "setup": setup, "wall_s": round(wall, 1), "best_system": best,
              "frontier": res.meta["frontier"]["_pareto"], "final": res.meta["final"], "transfer": tr,
              "n_evaluated": res.meta["n_evaluated"], "n_proposed": res.meta["n_proposed"],
              "usage": res.usage, "report_usd": round(report_usd, 4)}
    if cache is not None:
        report["spend"] = {**split_meter(rep_llm.meter.snapshot()), "report": round(report_usd, 4),
                           "cache_ground_truth": cache_spend(cache)}
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    inspect(out)
    audit = audit_mh(out, loop)
    (out / "audit.json").write_text(json.dumps(audit, indent=1, default=str))
    (out / "audit.md").write_text(render_audit(name, audit))
    return report


def audit_mh(out: Path, loop) -> dict:
    """Independent re-derivation of every Meta-Harness iteration from trace + ledger + store."""
    from rsi.metaharness.store import safe_name
    ev = load_trace(out)
    store = loop.store
    nodes = {n.meta.get("system"): n for n in Ledger(out / "ledger.jsonl").nodes()}
    checks: dict[str, list[int]] = {k: [0, 0] for k in (
        "diff_matches_ledger_and_store", "eval_only_on_search_split", "frontier_recomputed_independently",
        "incumbent_is_top_pareto_point", "delta_arithmetic", "evaluated_iff_admissible",
        "proposal_changes_code", "changes_a_file_the_base_harness_has", "claimed_base_exists_and_visible", "test_results_only_after_finalize",
        "view_never_exposes_results", "monitor_only_new_incumbents")}
    problems: list[str] = []

    def ok(k, c, msg=""):
        checks[k][0] += int(bool(c))
        checks[k][1] += 1
        if not c and msg:
            problems.append(f"{k}: {msg}")

    def indep_front(pts):
        return {n for n, s, c in pts if not any(s2 >= s and c2 <= c and (s2 > s or c2 < c) for _, s2, c2 in pts)}

    rows, cur = [], None
    visible: dict[int, set] = {}
    admissible: dict[str, bool] = {}
    evaluated: set = set()
    last_best = None
    finalize_seen = False
    for e in ev:
        k_, d, r = e["kind"], e["data"], e["round"]
        if k_ == "round_start":
            cur = {"iteration": r, "incumbent_before": (d["frontier"]["best"] or {}).get("system"),
                   "view_files": d["view"]["n_files"], "view_by_kind": d["view"]["by_kind"], "proposals": []}
            visible[r] = set(d["view"]["visible_systems"])
            rows.append(cur)
        elif k_ == "analysis" and cur is not None:
            cur["files_read"] = d.get("files_read")
            cur["files_read_by_kind"] = d.get("files_read_by_kind")
            cur["proposer_usd"] = (d.get("proposer_usage") or {}).get("cost_usd")
            cur["proposer_error"] = d.get("error")
        elif k_ == "proposal" and cur is not None:
            c = d["candidate"]
            p = {"name": c, "base": d.get("parent"), "claim": (d.get("hypothesis") or "")[:160],
                 "files_changed": d.get("files_changed"), "error": d.get("error")}
            cur["proposals"].append(p)
            if c in nodes:
                n = nodes[c]
                ok("diff_matches_ledger_and_store", d["diff"][:5000] == (n.diff or "")[:5000] and (
                    not d.get("base_known") or
                    store.artifact(d["parent"]).diff(store.artifact(c))[:5000] == d["diff"][:5000]), c)
                ok("proposal_changes_code", not d.get("identical_to_base"), f"{c} identical to its base")
                if d.get("base_known"):
                    bfiles = set(store.artifact(d["parent"]).files)
                    ok("changes_a_file_the_base_harness_has", any(f in bfiles for f in d.get("files_changed") or []),
                       f"{c} only ADDS files {d.get('files_changed')} - the harness it runs is unchanged")
                ok("claimed_base_exists_and_visible", d.get("parent") in visible.get(r, set()),
                   f"{c}: base {d.get('parent')} not visible in iteration {r}")
        elif k_ == "gate":
            admissible[d["candidate"]] = d["accept"]
            for p in (cur or {}).get("proposals", []):
                if p["name"] == d["candidate"]:
                    p["admissible"] = d["accept"]
                    p["gate_reason"] = d["reason"][:200]
        elif k_ == "eval":
            ok("eval_only_on_search_split", d["summary"]["split"] == loop.cfg.search_split and not finalize_seen,
               d["candidate"])
            evaluated.add(d["candidate"])
        elif k_ == "decision":
            pts = [(n, s["score"], loop._cost_value(s)) for n in store.names()
                   if (s := store.scores(n)) and (store.meta(n).get("iteration") or 0) <= r]
            front = indep_front(pts)
            best = sorted([p for p in pts if p[0] in front], key=lambda x: (-x[1], x[2], x[0]))[0][0]
            ok("incumbent_is_top_pareto_point", d["incumbent_after"] == best, f"it {r}: {d['incumbent_after']} vs {best}")
            pre = d["pre_best_score"]
            post = d["post_best_score"]
            for n, row in d["per_candidate"].items():
                if "on_frontier" not in row:
                    continue
                ok("frontier_recomputed_independently", row["on_frontier"] == (n in front), f"it {r} {n}")
                sc = store.scores(n)
                ok("delta_arithmetic", abs(row["delta_vs_pre_best_pts"] - round(100 * (sc["score"] - pre), 2)) < 1e-6
                   and abs(row["delta_logged_release"] - round(sc["avg_val"] - 100 * post, 1)) < 1e-6, n)
                for p in cur["proposals"]:
                    if p["name"] == n:
                        p.update(score=round(sc["score"], 4), context_cost=round(row["context_cost"], 1),
                                 on_frontier=row["on_frontier"], dominated_by=row["dominated_by"][:3])
            cur["incumbent_after"] = d["incumbent_after"]
            cur["new_frontier_members"] = d["kept"]
        elif k_ == "monitor":
            ok("monitor_only_new_incumbents", d["version"] != last_best, d["version"])
            last_best = d["version"]
            tgt = cur if cur is not None and r == cur["iteration"] else None
            if tgt is not None:
                tgt["monitor"] = {s: round(v["S"], 4) for s, v in d["sealed"].items()}
            else:
                rows.insert(0, {"iteration": 0, "baseline_incumbent": d["version"],
                                "monitor": {s: round(v["S"], 4) for s, v in d["sealed"].items()}})
        elif k_ == "note" and "finalize" in d.get("what", ""):
            finalize_seen = True
    for c, a in admissible.items():
        ok("evaluated_iff_admissible", a == (c in evaluated), c)
    # the one-time test evaluation: exactly one result per (split, finalized system), written by finalize()
    fin = json.loads((store.root / "finalized.json").read_text()) if (store.root / "finalized.json").exists() else {}
    for split in loop.cfg.test_splits:
        for s in fin.get("systems", []):
            ok("test_results_only_after_finalize", store.test_result(split, s) is not None, f"{split}/{s}")
    view = store.view("full")
    ok("view_never_exposes_results", not any(p.startswith("results/") or "finalized" in p or p == "frontier.json"
                                             for p in view))
    _ = safe_name
    return {"checks": {k: f"{a}/{b}" for k, (a, b) in checks.items()},
            "all_passed": all(a == b for a, b in checks.values()), "problems": problems, "rows": rows}


# ============================================================================ SoL-Pi
def run_sp(name: str) -> dict:
    from rsi.domains.agentworld import MockAgentLLM, make_domain
    from rsi.solpi import Config, GateSpec, Idea, run
    out = fresh(name)
    t0 = time.time()
    agent = MockAgentLLM("A")
    cache = llm = None
    if name == "solpi_agentworld_offline":
        dom = make_domain(seed=0, n_train=8, n_accept=6, n_final=8, n_test=0)
        cfg = Config(gate=GateSpec(mode="aggregate"), n_lineages=10, max_iters=4, workers=2, shadow_monitor=True)
        res = run(dom, dom.seed_artifact(), llm_task=agent, llm_propose=None, config=cfg, out_dir=out)
        setup = ("AgentWorld seed 0: training screen (evolve) = 8 tasks x 3 families (repofix, buildfix, logtriage); "
                 "sealed holdout = 6 x 2 held-out families (configfix, datalookup) used only by the firewall; sealed "
                 "ood = 8 x 2 held-out families (final, never touched by the protocol). Base = untouched Pi-like "
                 "harness (no extensions). Agent backend = MockAgentLLM('A') (offline). Idea pool = all 10 "
                 "AGENTWORLD_IDEAS (families C, P, D, T, R: 4 released mechanisms, 3 tricks, 2 do-less shortcuts, "
                 "1 dud). Proposer = LibraryProposer, reviewer = SmokeReviewer. Predeclared aggregate dual gate: "
                 "score within 2% (relative) AND tokens or cost saving > 2%; firewall on holdout; compose survivors. "
                 "n_lineages 10, max_iters 4, ralph_max 3, k 1. Shadow monitor on holdout + ood.")
    elif name in ("solpi_agentworld_live", "solpi_agentworld_live_r2",
                  "solpi_agentworld_live_r3"):
        from rsi.solpi import LLMReviewer
        dom = make_domain(seed=0, n_train=3, n_accept=3, n_final=3, n_test=0)
        llm, cache = fresh_llm(name)
        ideas = [Idea("L1", "C", "Stop replaying large successful tool outputs in every later request; keep them "
                                 "recallable", oracle="replayed_large_outputs"),
                 Idea("L2", "D", "Condense long failing command logs to the lines that carry the failure, keeping "
                                 "the full log recallable", oracle="diagnostic_log_tokens")]
        cfg = Config(gate=GateSpec(mode="aggregate"), n_lineages=2, max_iters=2, ralph_max=2, workers=2,
                     shadow_monitor=True)
        res = run(dom, dom.seed_artifact(), llm_task=agent, llm_propose=llm, config=cfg, out_dir=out, ideas=ideas,
                  reviewer=LLMReviewer(llm, dom, agent))
        setup = ("AgentWorld seed 0, small: evolve 3 x 3 training families, holdout 3 x 2, ood 3 x 2 held-out "
                 "families. Base = untouched harness. Agent backend = MockAgentLLM('A') (offline). Two free-form "
                 "ideas (no registry mechanism): the mechanism proposer/implementer (LLMMechanismProposer) AND the "
                 "independent reviewer (LLMReviewer: smoke + LLM contract review) are claude haiku behind a fresh "
                 "CachedLLM; haiku writes each mechanism as a Python extension against the runtime hook API. "
                 "Aggregate dual gate (2% / 2%), firewall on holdout. n_lineages 2, max_iters 2, ralph_max 2.")
    else:
        raise SystemExit(name)
    wall = time.time() - t0
    rnd = res.meta["rounds"][0]
    tr = transfer_report(dom, agent, {"base": dom.seed_artifact(), "composed": res.best},
                         splits=("evolve", "holdout", "ood"), k=1, workers=2)
    report = {"run": name, "setup": setup, "wall_s": round(wall, 1), "transfer": tr,
              "rounds": [{k: v for k, v in r.items() if k not in ("heldout",)} for r in res.meta["rounds"]],
              "usage": res.usage}
    if llm is not None:
        report["spend"] = {**split_meter(llm.meter.snapshot()), "cache_ground_truth": cache_spend(cache)}
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    inspect(out)
    audit = audit_sp(out, res, name)
    (out / "audit.json").write_text(json.dumps(audit, indent=1, default=str))
    (out / "audit.md").write_text(render_audit(name, audit))
    _ = rnd
    return report


def indep_gate(spec: dict, base: dict, cand: dict) -> bool:
    cap_ok = all((cand[m] >= base[m] * (1 - tol) - 1e-12) if spec["tolerance_kind"] == "relative"
                 else (cand[m] >= base[m] - tol - 1e-12) for m, tol in spec["capability"])
    eff_ok = any(base[m] > 0 and (base[m] - cand[m]) / base[m] > spec["min_gain"] for m in spec["efficiency"])
    return cap_ok and eff_ok


def audit_sp(out: Path, res, name: str) -> dict:
    from rsi.solpi.registry import harness_config
    ev = load_trace(out)
    start = next(e["data"] for e in ev if e["kind"] == "run_start")
    spec = start["config"]["gate"]
    checks: dict[str, list[int]] = {k: [0, 0] for k in (
        "gate_arithmetic_recomputed", "gate_used_base_metrics_of_round", "no_sealed_eval_in_lineages",
        "oracle_selection_recomputed", "variant_walk_rule", "firewall_trace_eq_sink_eq_driver",
        "firewall_verdict_recomputed", "only_firewall_survivors_composed", "composition_is_union",
        "diff_is_actual")}
    problems: list[str] = []

    def ok(k, c, msg=""):
        checks[k][0] += int(bool(c))
        checks[k][1] += 1
        if not c and msg:
            problems.append(f"{k}: {msg}")

    rows = []
    base_metrics = None
    cur = None
    last_by_idea: dict[str, dict] = {}
    for e in ev:
        k_, d, r = e["kind"], e["data"], e["round"]
        if k_ == "baseline":
            base_metrics = d["metrics"]["agg"]
        elif k_ == "analysis" and "oracle" in d:
            est = d["oracle"]
            n = start["config"]["n_lineages"]
            want = [i for i, _ in sorted(est.items(), key=lambda kv: (-kv[1], kv[0]))][:n]
            ok("oracle_selection_recomputed", want == d["chosen"], f"{want} vs {d['chosen']}")
        elif k_ == "round_start" and d.get("phase") == "lineage iteration":
            cur = {"trace_round": r, "idea": d["idea"]["id"], "kind_truth": d["idea_kind_ground_truth"],
                   "iteration": d["lineage_iteration"]}
            rows.append(cur)
            ok("gate_used_base_metrics_of_round", d["base_metrics"] == base_metrics)
        elif k_ == "proposal" and cur is not None:
            cur.update(change=d.get("change"), variant=d.get("variant"), error=d.get("error"),
                       ralph_repairs=d.get("ralph_repairs"), files_changed=d.get("files_changed"))
            # variant walk (LibraryProposer): first = 0; after a capability failure / rejection -> +1
            # (more conservative); after "no efficiency gain" -> -1 (more aggressive)
            if name == "solpi_agentworld_offline":
                prev = last_by_idea.get(cur["idea"])
                if prev is None:
                    ok("variant_walk_rule", d.get("variant") == 0, cur["idea"])
                elif prev.get("outcome") in ("gate_failed", "rejected"):
                    exp = prev["variant"] + (1 if prev.get("cap_failed") or prev["outcome"] != "gate_failed" else -1)
                    ok("variant_walk_rule", d.get("variant") == exp, f"{cur['idea']}: {d.get('variant')} vs {exp}")
            if d.get("diff") and cur["iteration"] is not None:
                ok("diff_is_actual", bool(d.get("files_changed")))
        elif k_ == "critic" and cur is not None:
            cur["review"] = "pass" if d["accept"] else f"reject: {'; '.join(d.get('objections', []))[:160]}"
        elif k_ == "eval" and "summary" in d:
            ok("no_sealed_eval_in_lineages", d["summary"]["split"] not in ("holdout", "ood", "test"), d["candidate"])
        elif k_ == "gate" and "capability" in d.get("math", {}) and cur is not None:
            m = d["math"]
            b = {k: v["base"] for k, v in {**m["capability"], **m["efficiency"]}.items()}
            c = {k: v["cand"] for k, v in {**m["capability"], **m["efficiency"]}.items()}
            ok("gate_arithmetic_recomputed", indep_gate(spec, b, c) == d["accept"], d["candidate"])
            cur.update(gate=d["accept"], reason=d["reason"], score=round(c["score"], 4),
                       saving_tokens=round(m["efficiency"]["tokens"]["saving"], 4),
                       saving_cost=round(m["efficiency"]["cost"]["saving"], 4),
                       cap_failed=not all(v["pass"] for v in m["capability"].values()))
        elif k_ == "decision" and cur is not None and d.get("stage") is not None:
            cur["outcome"] = d.get("outcome")
            cur["next"] = d["why"].split("next: ")[-1]
            last_by_idea[cur["idea"]] = {"variant": cur.get("variant"), "outcome": d.get("outcome"),
                                         "cap_failed": cur.get("cap_failed")}
    # firewall: trace == sink == driver, verdict recomputed from the sink's metrics
    fw_rows = []
    for i, rnd in enumerate(res.meta["rounds"], start=1):
        sink_p = out / f"firewall_r{i}" / "heldout.jsonl"
        sink = [json.loads(line) for line in sink_p.read_text().splitlines()] if sink_p.exists() else []
        trace_fw = {e["data"]["candidate"]: e["data"]["accept"] for e in ev
                    if e["kind"] == "gate" and "firewall" in e["data"].get("stage", "")}
        ok("firewall_trace_eq_sink_eq_driver", trace_fw == rnd["heldout_passed"] ==
           {s["candidate"]: s["passed"] for s in sink})
        for s in sink:
            ok("firewall_verdict_recomputed", indep_gate(spec, s["base_metrics"]["agg"], s["metrics"]["agg"]) ==
               s["passed"], s["candidate"])
            fw_rows.append({"candidate": s["candidate"], "passed": s["passed"], "reason": s["reason"],
                            "heldout_score": round(s["metrics"]["agg"]["score"], 4),
                            "base_heldout_score": round(s["base_metrics"]["agg"]["score"], 4),
                            "tokens_saving": round(1 - s["metrics"]["agg"]["tokens"] /
                                                   s["base_metrics"]["agg"]["tokens"], 4),
                            "cost_saving": round(1 - s["metrics"]["agg"]["cost"] / s["base_metrics"]["agg"]["cost"],
                                                 4)})
        ok("only_firewall_survivors_composed", set(rnd["survivors"]) ==
           {n for n, p in rnd["heldout_passed"].items() if p})
    # composition = union of the survivors' harness.json extension entries (+ their extension files)
    exts = set(harness_config(res.best.files).get("extensions", {}))
    if name == "solpi_agentworld_offline":        # registry mechanisms: frozen name = "<idea>:<mechanism>{params}"
        want = {n.change.split(":", 1)[1].split("{")[0] for n in Ledger(out / "ledger.jsonl").nodes(kind="frozen")
                if n.status == "survivor"}
        ok("composition_is_union", exts == want, f"{exts} vs {want}")
    kinds = {}
    for rnd in res.meta["rounds"]:
        for lin in rnd["lineages"]:
            kinds[lin["idea"]] = {"kind_truth": lin["kind"], "frozen": lin["frozen"],
                                  "survived": lin["idea"] in rnd["survivor_ideas"]}
    return {"checks": {k: f"{a}/{b}" for k, (a, b) in checks.items()},
            "all_passed": all(a == b for a, b in checks.values()), "problems": problems, "rows": rows,
            "firewall": fw_rows, "ideas": kinds, "composed_extensions": sorted(exts)}


# ============================================================================ rendering
def render_audit(name: str, audit: dict) -> str:
    out = [f"# Audit: {name}", "", f"All independent checks passed: **{audit['all_passed']}**", "",
           "| check | passed / total |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in audit["checks"].items()]
    if audit["problems"]:
        out += ["", "## Problems", ""] + [f"- {p}" for p in audit["problems"]]
    out += ["", "## Rows", ""]
    for r in audit["rows"]:
        out.append("- `" + json.dumps(r, default=str)[:1500] + "`")
    for key in ("firewall", "ideas", "composed_extensions"):
        if key in audit:
            out += ["", f"## {key}", "", "```json", json.dumps(audit[key], indent=1, default=str), "```"]
    return "\n".join(out) + "\n"


def reaudit(name: str) -> dict:
    """Re-run the audit of a finished Meta-Harness run from its files (no model calls)."""
    from types import SimpleNamespace
    from rsi.metaharness.store import ExperienceStore
    cfg = SimpleNamespace(**json.loads(next(l for l in (OUT / name / "trace.jsonl").read_text().splitlines()
                                             if '"run_start"' in l))["data"]["config"])
    loop = SimpleNamespace(store=ExperienceStore(OUT / name / "store"), cfg=cfg,
                           _cost_value=lambda s: float(s["context_cost"]) if "context_cost" in cfg.objectives else 0.0)
    audit = audit_mh(OUT / name, loop)
    (OUT / name / "audit.json").write_text(json.dumps(audit, indent=1, default=str))
    (OUT / name / "audit.md").write_text(render_audit(name, audit))
    return audit


RUNS = {"mh_memoclassify_offline": run_mh, "mh_agentqa_live": run_mh, "mh_agentqa_live_r2": run_mh, "solpi_agentworld_offline": run_sp,
        "solpi_agentworld_live": run_sp, "solpi_agentworld_live_r2": run_sp,
        "solpi_agentworld_live_r3": run_sp}

if __name__ == "__main__":
    if sys.argv[1:2] == ["reaudit"]:
        for n in sys.argv[2:]:
            a = reaudit(n)
            print(n, a["all_passed"], a["checks"], a["problems"])
        sys.exit(0)
    for n in sys.argv[1:] or ["mh_memoclassify_offline", "solpi_agentworld_offline"]:
        t = time.time()
        rep = RUNS[n](n)
        a = json.loads((OUT / n / "audit.json").read_text())
        print(f"[{n}] {time.time() - t:.1f}s audit_passed={a['all_passed']} checks={a['checks']}")
        if a["problems"]:
            print("  problems:", a["problems"][:10])
        if "spend" in rep:
            print("  spend:", rep["spend"])
