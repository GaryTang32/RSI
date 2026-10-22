"""X20 (retry round 2, P7 / P8 / P7r): do the engine's control rules, layer-3 client and success distiller fire?
Run: python experiments/evomap/x20_engine_coverage.py [--seeds 6] [--workers 2] [--quick]
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict

from _common import FAITHFUL_HINT, parse_args, pmap, save

from rsi.evomap import AgentNode, Config, LocalStore, NaiveEvoMapHub
from rsi.evomap.validation import CommandPolicy


def stand_in_analyzer(payload: dict) -> dict:
    """A deterministic keyword stand-in for the hub's /a2a/signal/analyze (its real prompt is not public)."""
    text = payload["corpus_summary"].lower()
    out = []
    if "wrong value" in text:
        out.append("recurring_error")
    if "check passed" in text:
        out.append("stable_success_plateau")
    return {"signals": out}


def collapse(s: str) -> str:
    for pre in ("errsig:", "recurring_errsig", "user_feature_request:", "user_improvement_suggestion:"):
        if s.startswith(pre):
            return pre.rstrip(":")
    return s


def is_empty(e: dict) -> bool:
    br = e.get("blast_radius") or {}
    return not br.get("files") and not br.get("lines")


def _good(c) -> bool:
    return c.outcome.get("status") == "success" and float(c.outcome.get("score", 1)) >= 0.7


def _dicts(events) -> list[dict]:
    return [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in events]


def _trailing(events: list[dict], pred) -> int:
    """Length of the run of events at the end of ``events`` that satisfy ``pred``."""
    k = 0
    for e in reversed(events):
        if not pred(e):
            break
        k += 1
    return k


def _recurrences(signal: str, events: list[dict]) -> int:
    return sum(1 for e in events if collapse(signal) in {collapse(x) for x in e.get("signals", [])})


def _failed(e: dict) -> bool:
    return not is_empty(e) and (e.get("outcome") or {}).get("status") == "failed"


def instrument(ag: AgentNode, log: dict, cycle: dict) -> None:
    """Log every call of the de-duplicator, the plateau detector and the success distiller with its inputs."""
    dd_apply, pl_override, auto = ag.deduper.apply, ag.plateau.override, ag.distiller.auto_distill

    def dd_wrap(signals, recent):
        ev = _dicts(recent)
        res = dd_apply(signals, recent)
        log["dedup"].append({"t": cycle["t"], "in": list(signals), "ev": ev[-10:], "out": list(res.signals),
                             "suppressed": list(res.suppressed), "ban": res.ban_gene})
        return res

    def pl_wrap(recent, hub_directive=None):
        ev = _dicts(recent)[-10:]
        res = pl_override(recent, hub_directive)
        log["plateau"].append({"t": cycle["t"], "ev": ev, "active": res.active, "severity": res.severity})
        return res

    def auto_wrap(store):
        caps = list(store.capsules.values())
        good = [c for c in caps if _good(c)]
        now = store.clock.now()
        last_at = ag.distiller.last_at
        pre = {"t": cycle["t"], "solidify_count": store.solidify_count, "n_good": len(good),
               "succ_last10": sum(1 for c in caps[-10:] if c.outcome.get("status") == "success"),
               "hours_since_last": None if last_at is None else (now - last_at) / 3600,
               "genes": {g.id: list(g.validation) for g in store.genes.values()}}
        groups = defaultdict(list)
        for c in good:
            groups[c.gene].append(float(c.outcome.get("score", 0)))
        # amendment A1: source gene = argmax(2 * count + mean score) over good capsules grouped by gene
        pre["expected_source"] = max(groups.items(), key=lambda kv: (2 * len(kv[1]) + sum(kv[1]) / len(kv[1]),
                                                                     kv[0]))[0] if groups else None
        res = auto(store)
        gene = res.gene if res else None
        pre.update({"ok": bool(res and res.ok), "reason": getattr(res, "reason", ""),
                    "gene": gene.id if gene else None,
                    "source": (gene.provenance or {}).get("source_gene") if gene else None,
                    "validation": list(gene.validation) if gene else None})
        log["distill"].append(pre)
        return res

    ag.deduper.apply, ag.plateau.override, ag.distiller.auto_distill = dd_wrap, pl_wrap, auto_wrap


def one(job) -> dict:
    seed, args = job
    from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, WorldConfig, make_domain
    dom = make_domain(WorldConfig(seed=seed))
    hub = NaiveEvoMapHub(signal_analyzer=stand_in_analyzer)
    cfg = Config(mode="faithful", seed=seed, validation_hint=FAITHFUL_HINT)
    rng0 = random.Random(seed)
    ag = AgentNode(f"x20_{seed}", dom, dom.seed_artifact(), llm_task=GeneWorldModel(rng0.gauss(-0.3, 0.5)),
                   llm_propose=GeneWorldProposer(dom.world, 0.6, 0.3), config=cfg, hub=hub,
                   store=LocalStore(node_id=f"x20_{seed}"))
    log, cycle = defaultdict(list), {"t": 0}
    instrument(ag, log, cycle)
    tasks = dom.tasks.split("evolve")
    rng = random.Random(f"x20-{seed}")
    T = 40 if args.quick else 150
    for _ in range(T):
        cycle["t"] += 1
        ag.cycle(rng.choice(tasks))
    return check(seed, log, ag, CommandPolicy.faithful(), T)


def check(seed: int, log: dict, ag: AgentNode, policy: CommandPolicy, T: int) -> dict:
    """Re-derive every logged firing from the spec text (precision) and every due firing (P7r recall)."""
    n_sup = ok_sup = n_sup_due = hit_sup_due = n_sup_protected = 0
    n_ban = ok_ban = n_ban_due = hit_ban_due = 0
    n_rep = ok_rep = n_emp = ok_emp = 0
    for d in log["dedup"]:
        last8 = d["ev"][-8:]
        for s in d["suppressed"]:               # suppressed => key in >= 3 of the last 8 events
            n_sup += 1
            ok_sup += _recurrences(s, last8) >= 3
        for s in dict.fromkeys(d["in"]):        # recall; "task:" descriptors are protected (documented deviation)
            if _recurrences(s, last8) >= 3:
                if s.startswith("task:"):
                    n_sup_protected += 1
                    continue
                n_sup_due += 1
                hit_sup_due += s in d["suppressed"]
        streak = _trailing(d["ev"][-10:], _failed)
        used = [g for e in last8 for g in e.get("genes_used", [])]
        if d["ban"]:                            # ban_gene => >= 5 trailing non-empty failures, most used gene
            n_ban += 1
            most = Counter(used).most_common(1)
            ok_ban += streak >= 5 and most and most[0][0] == d["ban"]
        if streak >= 5 and used:
            n_ban_due += 1
            hit_ban_due += bool(d["ban"])
        if _trailing(d["ev"], lambda e: e.get("intent") == "repair") >= 3:
            n_rep += 1
            ok_rep += "force_innovation_after_repair_loop" in d["out"]
        emp = sum(1 for e in last8 if is_empty(e))
        fired = "empty_cycle_loop_detected" in d["out"]
        if fired or emp >= 4:
            n_emp += 1
            ok_emp += fired == (emp >= 4)
    n_pl = ok_pl = 0
    for d in log["plateau"]:
        k = _trailing(d["ev"], lambda e: (e.get("outcome") or {}).get("status") != "success")
        expect = "required" if k >= 10 else ("suggested" if k >= 5 else "none")
        if d["active"] or expect != "none":
            n_pl += 1
            ok_pl += (d["severity"] if d["active"] else "none") == expect
    calls = ag.llm_signals.calls
    l3_ok = [c["count"] for c in calls] == list(range(1, T + 1, 5)) and all(
        len(c["payload"]["corpus_summary"]) <= 2000 for c in calls)
    n_d = ok_d = lit_empty = 0
    for d in log["distill"]:
        if not d["ok"]:
            continue
        n_d += 1
        trig = d["solidify_count"] % 5 == 0 or (d["succ_last10"] >= 7 and d["n_good"] >= 10 and
                                                (d["hours_since_last"] is None or d["hours_since_last"] >= 24))
        src = d["expected_source"]
        src_val = d["genes"].get(src, [])[:4] if src else []
        exp_val = [v for v in (src_val or ["python --test"]) if policy.check(v).ok]
        ok_d += trig and d["n_good"] >= 10 and d["source"] == src and d["validation"] == exp_val
        lit_empty += d["validation"] == []
    caps = ag.store.capsules
    return {"seed": seed, "cycles": T, "suppression_firings": n_sup, "suppression_ok": ok_sup,
            "ban_firings": n_ban, "ban_ok": ok_ban, "repair_loop_cases": n_rep, "repair_loop_ok": ok_rep,
            "empty_loop_cases": n_emp, "empty_loop_ok": ok_emp, "plateau_cases": n_pl, "plateau_ok": ok_pl,
            "plateau_active": sum(d["active"] for d in log["plateau"]),
            "l3_calls": len(calls), "l3_ok": bool(l3_ok), "l3_signals_merged": sum(bool(c["signals"]) for c in calls),
            "distill_calls": len(log["distill"]), "distill_firings": n_d, "distill_ok": ok_d,
            "distill_validation_empty_literal": lit_empty,
            "distill_not_ok_reasons": dict(Counter(d["reason"] for d in log["distill"] if not d["ok"])),
            "failure_distilled_genes": sum(1 for g in ag.store.genes if g.startswith("gene_repair_distilled_")),
            "solidify_count": ag.store.solidify_count, "n_genes": len(ag.store.genes),
            "suppression_due": n_sup_due, "suppression_due_fired": hit_sup_due,
            "suppression_due_protected": n_sup_protected, "ban_due": n_ban_due, "ban_due_fired": hit_ban_due,
            # M11 recount from the final store itself
            "final_distinct_good_capsules": len({c.id for c in caps.values() if _good(c)}),
            "final_distinct_capsules": len(caps),
            "max_n_good_seen_by_distiller": max([d["n_good"] for d in log["distill"]] or [0])}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=6)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)

    def tot(k):
        return int(sum(r[k] for r in rows))

    def full(d, n="firings"):
        return d[n] > 0 and d["rederived"] == d[n]
    v = {
        "suppression": {"firings": tot("suppression_firings"), "rederived": tot("suppression_ok")},
        "ban_gene": {"firings": tot("ban_firings"), "rederived": tot("ban_ok")},
        "plateau": {"cases": tot("plateau_cases"), "rederived": tot("plateau_ok"), "active": tot("plateau_active")},
        "repair_loop": {"cases": tot("repair_loop_cases"), "rederived": tot("repair_loop_ok")},
        "empty_loop": {"cases": tot("empty_loop_cases"), "rederived": tot("empty_loop_ok")},
        "layer3": {"calls": tot("l3_calls"), "all_runs_on_schedule": all(r["l3_ok"] for r in rows)},
        "distiller": {"firings": tot("distill_firings"), "rederived": tot("distill_ok"),
                      "runs_with_firing": sum(r["distill_firings"] > 0 for r in rows),
                      "literal_empty_validation": tot("distill_validation_empty_literal")},
    }
    rc = v["recall"] = {"suppression": {"due": tot("suppression_due"), "fired": tot("suppression_due_fired"),
                                        "protected_task_descriptors": tot("suppression_due_protected")},
                        "ban_gene": {"due": tot("ban_due"), "fired": tot("ban_due_fired")},
                        "distinct_good_capsules_per_run": [r["final_distinct_good_capsules"] for r in rows],
                        "distinct_capsules_per_run": [r["final_distinct_capsules"] for r in rows]}
    v["P7r_pass"] = bool(rc["suppression"]["due"] > 0 and rc["suppression"]["fired"] == rc["suppression"]["due"] and
                         rc["ban_gene"]["due"] > 0 and rc["ban_gene"]["fired"] == rc["ban_gene"]["due"])
    v["P7_pass"] = bool(full(v["suppression"]) and full(v["ban_gene"]) and
                        v["plateau"]["active"] > 0 and v["plateau"]["rederived"] == v["plateau"]["cases"] and
                        v["layer3"]["all_runs_on_schedule"])
    v["P8_pass"] = bool(v["distiller"]["runs_with_firing"] >= 4 and full(v["distiller"]))
    out = {"config": {"seeds": args.seeds, "cycles": rows[0]["cycles"], "mode": "faithful", "domain": "geneworld",
                      "preregistration": "docs/methods/evomap/claims-audit.md#retry-round-2-preregistration"},
           "raw": rows, "verdict": v}
    save("x20_engine_coverage", out, args.out)
    for r in rows:
        print({k: r[k] for k in r if k not in ("distill_not_ok_reasons",)})
    print("verdict:", v)


if __name__ == "__main__":
    main()
