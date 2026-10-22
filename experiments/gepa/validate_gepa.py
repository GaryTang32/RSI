"""From-scratch GEPA validation run with the per-iteration trace, a transfer report and an independent step audit.

Run: python experiments/gepa/validate_gepa.py {ruleworld_offline|ruleworld_merge_offline|agentqa_offline|agentqa_live}
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from _common import ROOT, pareto_survivors
from rsi.core import Budget, CachedLLM, ClaudeCLI, transfer_report
from rsi.core.artifact import Artifact
from rsi.core.evaluate import Evaluator
from rsi.core.ledger import ArtifactStore, Ledger
from rsi.gepa import Config, run
from rsi.trace import inspect, load_trace

OUT = ROOT / "validation" / "gepa"
VAL_PHASES = ("seed_val", "val_reflective", "val_merge")
CHECKS = ("gate_arithmetic", "diff_matches_store", "decision_matches_ledger", "val_eval_matches_ledger",
          "pareto_weights_recomputed", "round_robin_recomputed", "minibatch_epoch_disjoint", "val_eval_covers_D_pareto",
          "returned_best_is_argmax_val", "child_minibatch_same_ids_as_parent")


def setup(name: str, reuse_cache: bool = False) -> dict:
    if name in ("ruleworld_offline", "ruleworld_merge_offline"):
        from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
        dom = make_domain(seed=0, feedback="rich")
        if name == "ruleworld_merge_offline":
            config = Config(max_metric_calls=1500, seed=0, use_merge=True)
            text = ("Same RuleWorld world, seed artifact and mock reflection LM as ruleworld_offline, but "
                    "GEPA+Merge (use_merge=True, reference soft cap 5, 5-id subsample, overlap floor 5) and "
                    "max_metric_calls = 1500, to exercise rejections, skips and the merge path.")
        else:
            config = Config(max_metric_calls=300, seed=0)
            text = ("RuleWorld seed 0 (2 modules triage -> reply, 16 aspects, 4 customer families, 2 conflicting "
                    "aspects, slip 5%), rich feedback; |D_train| = 30 (evolve), |D_pareto| = 30 (val), sealed "
                    "test = 300. Task model = RuleWorld's simulated rule-following model (no LLM). Reflection LM "
                    "= RuleWorldReflectionLM (offline mock, default ReflectionProfile). GEPA defaults (b = 3, "
                    "Pareto, round-robin, strict acceptance, no merge), max_metric_calls = 300, seed 0.")
        return dict(domain=dom, seed=dom.seed_artifact(), llm_task=None, llm_propose=RuleWorldReflectionLM(dom.world),
                    config=config, budget=None, cache=None, report_splits=("evolve", "val", "test"), report_k=1,
                    setup=text)
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.gepa import AgentQAReflectionLM, two_module_harness
    suite = make_suite(n_evolve=10, n_val=8, n_holdout=6, n_ood_per_family=1, seed=0)
    dom = AgentQADomain(suite)
    seed = two_module_harness()
    common = ("AgentQA two-module harness (rsi.gepa.two_module_harness: solver prompt -> optional Python tool -> "
              "reporter prompt; harness.py frozen, components prompts/solver.md + prompts/reporter.md). Suite seed 0: "
              "|D_train| = 10 evolve + |D_pareto| = 8 val (numeric family), sealed holdout 6 (numeric) + ood 4 "
              "(dates / numbertheory / strings / lists, 1 each). GEPA defaults (b = 3, Pareto, round-robin, strict "
              "acceptance, no merge), seed 0.")
    if name == "agentqa_offline":
        return dict(domain=dom, seed=seed, llm_task=SimModel(suite), llm_propose=AgentQAReflectionLM(),
                    config=Config(max_metric_calls=120, seed=0, workers=4, shadow_workers=4), budget=None, cache=None,
                    report_splits=("evolve", "val", "holdout", "ood"), report_k=1,
                    setup=common + " Task model = SimModel (offline simulated model), reflection LM = "
                                   "AgentQAReflectionLM (offline mock). max_metric_calls = 120, workers 4.")
    if name == "agentqa_live":
        cache = OUT / ".cache_agentqa_live"
        if cache.exists() and not reuse_cache:
            shutil.rmtree(cache)
        llm = CachedLLM(ClaudeCLI("haiku", timeout_s=240), cache)
        return dict(domain=dom, seed=seed, llm_task=llm, llm_propose=llm,
                    config=Config(max_metric_calls=100, seed=0, workers=4, shadow_workers=4),
                    budget=Budget(max_usd=1.2, max_wall_s=1800), cache=cache,
                    # evolve is the only split not already cached, so the live report leaves it out (cost)
                    report_splits=("val", "holdout", "ood"), report_k=1,
                    setup=common + " Task model AND reflection LM = claude haiku (ClaudeCLI('haiku') behind a fresh "
                                   "CachedLLM). max_metric_calls = 100 (not 120: haiku's long computations cost "
                                   "~$0.007 per task call, so 120 would not fit the $3 / 40 min stage limits), "
                                   "workers 4, safety Budget(max_usd=1.2 loop spend, max_wall_s=1800).")
    raise SystemExit(f"unknown run {name!r}")


def truth_fn(name: str, s: dict):
    """Ground truth (or a close estimate) of an artifact's quality, for the post-hoc audit only."""
    dom = s["domain"]
    if name.startswith("ruleworld"):
        def f(a: Artifact) -> dict:
            return {"val": dom.expected(a, "val"), "test": dom.expected(a, "test"), "evolve": dom.expected(a, "evolve")}
        return f, "exact analytic expected score (RuleWorld.expected)"
    if name == "agentqa_offline":
        from rsi.domains.agentqa import SimModel
        ev = Evaluator(dom, SimModel(dom.tasks), workers=3, allow_sealed=True, seed_offset=10_000)
        cache: dict = {}

        def g(a: Artifact) -> dict:
            if a.id not in cache:
                cache[a.id] = {sp: ev.evaluate(a, sp, 6).score for sp in ("evolve", "val", "holdout", "ood")}
            return cache[a.id]
        return g, "SimModel estimate: mean over 6 fresh seeds (offset 10000) per task on each split"
    return None, "none (live model: no ground truth; see the shadow monitor and the transfer report)"


def independent_pareto_weights(scores: dict[str, dict[str, float]], key_order: list[str]) -> dict[str, int]:
    """Alg. 2 SELECT_CANDIDATE weights, independent of rsi.gepa.frontier: per-instance fronts (ties kept),
    set-cover pruning, weight = number of fronts a survivor is in."""
    front = {}
    for v in key_order:
        vals = {int(c[1:]): sc[v] for c, sc in scores.items() if v in sc}
        if vals:
            b = max(vals.values())
            front[v] = {c for c, x in vals.items() if x == b}
    agg = {int(c[1:]): sum(sc.values()) / len(sc) for c, sc in scores.items()}
    return {f"c{c}": sum(1 for v in front if c in front[v]) for c in pareto_survivors(front, agg)}


def _first(events: list[dict], kind: str, cond=lambda d: True):
    return next((e["data"] for e in events if e["kind"] == kind and cond(e["data"])), None)


def audit(run_dir: Path, name: str, s: dict) -> dict:
    ev = load_trace(run_dir)
    store = ArtifactStore(run_dir / "artifacts")
    ledger = {n.id: n for n in Ledger(run_dir / "ledger.jsonl").nodes()}
    truth, truth_desc = truth_fn(name, s)
    checks = {k: [0, 0] for k in CHECKS}
    start_ev = next(e["data"] for e in ev if e["kind"] == "run_start")
    comps = start_ev["components"]
    n_pareto, n_train = start_ev["splits"]["n_pareto"], start_ev["splits"]["n_train"]
    b = start_ev["config"]["minibatch_size"]
    val_scores: dict[str, dict[str, float]] = {}     # cN -> per-task D_pareto scores, as the trace recorded them
    key_order: list[str] = []
    rr: dict[str, int] = {}
    epoch_seen: dict[int, set] = {}
    it_parent: dict = {}
    problems: list[str] = []

    def ok(key, cond):
        checks[key][0] += int(bool(cond))
        checks[key][1] += 1
        return bool(cond)

    for e in ev:
        kind, d = e["kind"], e["data"]
        if kind in ("baseline", "eval") and d.get("phase") in VAL_PHASES:
            val_scores[d["candidate"]] = dict(d["per_task"])
            if not key_order:
                key_order = list(d["per_task"])
            ok("val_eval_covers_D_pareto", len(d["per_task"]) == n_pareto)
            if kind == "baseline":
                rr[d["candidate"]] = 0
        elif kind == "note" and d.get("what", "").startswith("parent selection"):
            if d.get("selector") == "pareto":
                w = independent_pareto_weights(val_scores, key_order)
                if not ok("pareto_weights_recomputed", w == d["sampling_weights"]):
                    problems.append(f"it {e['round']}: weights {d['sampling_weights']} vs independent {w}")
            it_parent[e["round"]] = d["chosen"]
            seen = epoch_seen.setdefault(d.get("sampler_epoch"), set())
            ids = d["minibatch_ids"]
            # within one epoch every id appears once, except the least-frequent padding (L = ceil(n/b)*b)
            ok("minibatch_epoch_disjoint", len(seen & set(ids)) <= (-n_train) % b)
            seen.update(ids)
        elif kind == "analysis":
            par = it_parent.get(e["round"])
            if par is not None:
                expect = comps[rr[par] % len(comps)]
                if not ok("round_robin_recomputed", d["components"] == [expect]):
                    problems.append(f"it {e['round']}: components {d['components']} vs round-robin {expect}")
                rr[par] = (rr[par] + 1) % len(comps)
        elif kind == "decision" and d.get("kept"):
            # a new pool member inherits max(pointer of its parents), already advanced (reference state.py)
            rr[d["kept"]] = max((rr[p] for p in d.get("new_parents", [])), default=0)
    end = next((e["data"] for e in ev if e["kind"] == "run_end"), {})
    if end and val_scores:
        agg = {c: sum(v.values()) / len(v) for c, v in val_scores.items()}
        best = max(agg.values())
        first_best = min((c for c in agg if agg[c] == best), key=lambda c: int(c[1:]))
        ok("returned_best_is_argmax_val", end["best"] == first_best)
    by_round: dict = {}
    for e in ev:
        by_round.setdefault(e["round"], []).append(e)
    pool = {n.id: n.artifact_id for n in ledger.values() if n.id.startswith("c")}     # cN -> artifact id
    charged = sum(int(e["data"].get("rollouts_charged", 0)) for e in ev if e["kind"] in ("eval", "baseline"))
    rows = []
    for r in sorted(k for k in by_round if k is not None):
        evs = by_round[r]
        evals = [e["data"] for e in evs if e["kind"] == "eval"]
        par_mb = [d for d in evals if d.get("phase") == "minibatch_parent"]
        ch_mb = [d for d in evals if d.get("phase") == "minibatch_child"]
        if par_mb and ch_mb:              # paper Alg. 1: the child is scored on the parent's minibatch M
            ok("child_minibatch_same_ids_as_parent", par_mb[0]["ids"] == ch_mb[0]["ids"])
        row: dict = {"round": r}
        prop, gate, dec = _first(evs, "proposal"), _first(evs, "gate"), _first(evs, "decision")
        sel = _first(evs, "note", lambda d: d.get("what", "").startswith("parent"))
        mons = [e["data"] for e in evs if e["kind"] == "monitor"]
        row["rollouts_at_start"] = (_first(evs, "round_start") or {}).get("rollouts_used")
        if sel:
            row.update(parent=sel["chosen"], p_parent=sel.get("p_chosen"), minibatch=sel["minibatch_ids"])
        if dec:
            row.update(event=dec.get("event"), kept=dec.get("kept"), incumbent=f"{dec['incumbent_before']}->"
                       f"{dec['incumbent_after']}", new_val=dec.get("new_val_mean"))
        if prop:
            row.update(proposal=prop["candidate"], kind=prop.get("proposal_kind"), components=prop.get("components"),
                       diff_lines=sum(1 for line in (prop.get("diff") or "").splitlines()
                                      if line[:1] in "+-" and not line.startswith(("+++", "---"))),
                       unchanged_rewrite=prop.get("unchanged_rewrite"), error=prop.get("error"))
            parent_id, child_id = pool.get(prop.get("parent")), prop.get("child_artifact")
            if parent_id and child_id and prop.get("proposal_kind") == "reflective":
                ok("diff_matches_store", store.get(parent_id).diff(store.get(child_id)) == prop.get("diff", ""))
        if gate:
            m = gate["math"]
            if gate.get("gate") == "minibatch acceptance":
                if m["rule"] == "sum(after) > sum(before)":
                    ok("gate_arithmetic", (m["sum_after"] > m["sum_before"]) == gate["accept"]
                       and abs(sum(m["before"]) - m["sum_before"]) < 1e-9
                       and abs(sum(m["after"]) - m["sum_after"]) < 1e-9)
                row.update(mb_before=m["sum_before"], mb_after=m["sum_after"], gate=gate["accept"])
            else:
                ok("gate_arithmetic", (m["sum_sub_after"] >= m["threshold"]) == gate["accept"])
                row.update(merge_sub_after=m["sum_sub_after"], merge_threshold=m["threshold"], gate=gate["accept"])
        if dec:
            if dec.get("kept"):
                node = ledger.get(dec["kept"])
                ok("decision_matches_ledger", node is not None and node.status == "accepted")
                vals = [d for d in evals if d.get("candidate") == dec["kept"]]
                if node is not None and vals:
                    ok("val_eval_matches_ledger", abs(vals[-1]["summary"]["S"] - float(node.score)) < 1e-9)
            elif dec.get("proposal") and dec.get("event") in ("rejected", "merge_rejected", "critic_rejected"):
                node = ledger.get(dec["proposal"])
                ok("decision_matches_ledger", node is not None and node.status in ("rejected", "critic_rejected"))
        if mons:
            row["monitor"] = {m_["version"]: {sp: round(v["S"], 4) for sp, v in m_["sealed"].items()} for m_ in mons}
        if truth and prop and prop.get("child_artifact") and pool.get(prop.get("parent")):
            row.update(step_truth(name, s, truth, store, pool, prop, row))
        rows.append(row)
    out = {"run": name, "truth": truth_desc, "checks": {k: {"passed": v[0], "of": v[1]} for k, v in checks.items()},
           "problems": problems, "rollouts_charged_in_trace": charged, "rollouts_counted": end.get("rollouts"),
           "rows": rows}
    if truth:
        out["incumbent_changes_vs_truth"] = incumbent_changes(ev, truth, store, pool)
    gated = [r for r in rows if "gate" in r and "true_gain" in r]
    if gated:
        acc = [r for r in gated if r["gate"]]
        rej = [r for r in gated if not r["gate"]]
        out["gate_vs_truth"] = {
            "n": len(gated), "accepted": len(acc), "rejected": len(rej),
            "false_accepts (accepted, true gain <= 0)": sum(1 for r in acc if r["true_gain"] <= 0),
            "false_rejects (rejected, true gain > 0)": sum(1 for r in rej if r["true_gain"] > 0),
            "split": gated[0]["true_gain_split"]}
    return out


def step_truth(name: str, s: dict, truth, store, pool: dict, prop: dict, row: dict) -> dict:
    """Ground truth of one proposal: parent and child scores, true gain, expected minibatch sums (RuleWorld)."""
    parent, child = store.get(pool[prop["parent"]]), store.get(prop["child_artifact"])
    tp, tc = truth(parent), truth(child)
    out = {"truth_parent": {k: round(v, 4) for k, v in tp.items()},
           "truth_child": {k: round(v, 4) for k, v in tc.items()}}
    key = "test" if "test" in tc else "holdout"
    ref = tp[key]
    if prop.get("proposal_kind") == "merge" and all(pool.get(p) for p in prop.get("parents", [])):
        # the merge gate compares against the better parent, so the truth does too
        ref = max(truth(store.get(pool[p]))[key] for p in prop["parents"])
        out["true_gain_ref"] = "better parent (merge)"
    out["true_gain"] = round(tc[key] - ref, 4)
    out["true_gain_split"] = key
    if name.startswith("ruleworld") and row.get("minibatch") and prop.get("proposal_kind") == "reflective":
        world, mb = s["domain"].world, row["minibatch"]
        out["expected_mb_sum_parent"] = round(world.expected(parent.files, mb) * len(mb), 4)
        out["expected_mb_sum_child"] = round(world.expected(child.files, mb) * len(mb), 4)
        out["ticket_facts_added"] = world.count_facts(child.files) - world.count_facts(parent.files)
    return out


def incumbent_changes(ev: list, truth, store, pool: dict) -> list[dict]:
    out = []
    for e in ev:
        d = e["data"]
        if e["kind"] != "decision" or d["incumbent_before"] == d["incumbent_after"]:
            continue
        bef, aft = d["incumbent_before"], d["incumbent_after"]
        tb, ta = truth(store.get(pool[bef])), truth(store.get(pool[aft]))
        key = "test" if "test" in ta else "holdout"
        out.append({"round": e["round"], "from": bef, "to": aft, "val_before": d["incumbent_val_before"],
                    "val_after": d["incumbent_val_after"], f"true_{key}_before": round(tb[key], 4),
                    f"true_{key}_after": round(ta[key], 4), "true_gain": round(ta[key] - tb[key], 4)})
    return out


def audit_md(a: dict) -> str:
    L = [f"# Audit of `{a['run']}`", "", f"Ground truth: {a['truth']}.", "",
         "Independent checks re-derived from `trace.jsonl`, the ledger and the artifact store:", ""]
    L += [f"- {k}: {v['passed']}/{v['of']}" for k, v in a["checks"].items()]
    L.append(f"- rollouts charged in eval events = {a['rollouts_charged_in_trace']}, engine counter = "
             f"{a['rollouts_counted']}")
    if "gate_vs_truth" in a:
        L.append(f"- gate vs truth: `{json.dumps(a['gate_vs_truth'])}`")
    L += [f"- PROBLEM: {pr}" for pr in a.get("problems", [])]
    if a.get("incumbent_changes_vs_truth"):
        L += ["", "Incumbent (argmax mean D_pareto) changes against the truth:", ""]
        L += [f"- it {x['round']}: {x['from']} -> {x['to']}  "
              + json.dumps({k: v for k, v in x.items() if k not in ("round", "from", "to")})
              for x in a["incumbent_changes_vs_truth"]]
    L += ["", "| it | parent (p) | component | minibatch sum before -> after | gate | decision | new val | incumbent "
              "| true gain | sealed (monitor) |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in a["rows"]:
        comp = ",".join(c.split("/")[-1] for c in (r.get("components") or [])) or "-"
        mb = (f"{r['mb_before']:.3g} -> {r['mb_after']:.3g}" if "mb_before" in r else
              (f"merge {r['merge_sub_after']:.3g} vs {r['merge_threshold']:.3g}" if "merge_sub_after" in r else "-"))
        if "expected_mb_sum_parent" in r:
            mb += f" (expected {r['expected_mb_sum_parent']:.3g} -> {r['expected_mb_sum_child']:.3g})"
        p = f"{r.get('parent', '-')} ({r['p_parent']:.2f})" if r.get("p_parent") is not None else r.get("parent", "-")
        mon = "; ".join(f"{v}: " + ", ".join(f"{sp} {x}" for sp, x in d.items())
                        for v, d in r.get("monitor", {}).items())
        tg = f"{r['true_gain']:+.3f}" if "true_gain" in r else "-"
        nv = f"{r['new_val']:.3f}" if r.get("new_val") is not None else "-"
        L.append(f"| {r['round']} | {p} | {comp} | {mb} | {r.get('gate', '-')} | {r.get('event', '-')} "
                 f"{r.get('kept') or ''} | {nv} | {r.get('incumbent', '-')} | {tg} | {mon or '-'} |")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run", choices=["ruleworld_offline", "ruleworld_merge_offline", "agentqa_offline", "agentqa_live"])
    ap.add_argument("--reuse-cache", action="store_true",
                    help="live run: replay from the existing cache (NOT a from-scratch run; for re-rendering only)")
    a = ap.parse_args()
    run_dir = OUT / a.run
    if run_dir.exists():
        shutil.rmtree(run_dir)
    s = setup(a.run, a.reuse_cache)
    t0 = time.time()
    res = run(s["domain"], s["seed"], llm_task=s["llm_task"], llm_propose=s["llm_propose"], config=s["config"],
              out_dir=run_dir, budget=s["budget"], verbose=True)
    wall_loop = time.time() - t0
    loop_usage, shadow_usage = res.usage, res.meta["shadow_usage"]
    llms = {id(x): x for x in (s["llm_task"], s["llm_propose"]) if x is not None}
    usd = lambda llm: llm.meter.snapshot()["_total"]["cost_usd"]
    before = {k: usd(llm) for k, llm in llms.items()}
    t1 = time.time()
    rep = transfer_report(s["domain"], s["llm_task"], {"seed": res.baseline, "best": res.best},
                          splits=s["report_splits"], k=s["report_k"], workers=3)
    report_usd = sum(usd(llm) - before[k] for k, llm in llms.items())
    total_usd = sum(usd(llm) for llm in llms.values())
    inspect(run_dir)
    au = audit(run_dir, a.run, s)
    (run_dir / "audit.json").write_text(json.dumps(au, indent=1, default=str))
    (run_dir / "audit.md").write_text(audit_md(au))
    meta = res.meta
    summary = {
        "run": a.run, "setup": s["setup"], "stop_reason": res.stop_reason, "iterations": meta["iterations"],
        "rollouts": meta["rollouts"], "rollouts_by_phase": meta["rollouts_by_phase"],
        "n_candidates": meta["n_candidates"], "n_proposals": meta["n_proposals"],
        "n_accepted": meta["n_accepted"], "seed_val": meta["seed_val"], "best_val": meta["best_val"],
        "best_idx": meta["best_idx"], "transfer_report": {sp: {arm: {"S": v["S"], "vs_seed": v["vs_reference"]}
                                                               for arm, v in row.items()}
                                                          for sp, row in rep["splits"].items()},
        "spend_usd": {"loop": loop_usage.get("_total", {}).get("cost_usd", 0.0),
                      "shadow_monitor": shadow_usage.get("_total", {}).get("cost_usd", 0.0),
                      "report": report_usd, "total_from_meters": total_usd},
        "loop_usage": loop_usage, "shadow_usage": shadow_usage,
        "wall_s": {"loop_incl_monitor": wall_loop, "report": time.time() - t1},
        "cache": str(s["cache"]) if s["cache"] else None,
        "exec_errors": meta["n_exec_errors"], "reflection_failed": meta["n_reflection_failed"],
        "reflection_unparsed": meta["n_reflection_unparsed"], "infra_errors": meta["n_infra_errors"],
    }
    if a.run.startswith("ruleworld"):
        dom = s["domain"]
        summary["truth"] = {"seed_test": dom.expected(res.baseline, "test"),
                            "best_test": dom.expected(res.best, "test"),
                            "oracle_test": dom.expected(dom.oracle_artifact(), "test"),
                            "best_ticket_facts": dom.world.count_facts(res.best.files)}
    (run_dir / "report.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: summary[k] for k in ("stop_reason", "rollouts", "seed_val", "best_val", "spend_usd")},
                     default=str))
    print(json.dumps(summary["transfer_report"], default=str)[:1500])
    print(json.dumps(au["checks"]), au.get("gate_vs_truth"))


if __name__ == "__main__":
    main()
