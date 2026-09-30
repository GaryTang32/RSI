"""X20 (retry round 2, P7 / P8): do the engine's control rules, the layer-3 signal client and the success
distiller fire as specified in long faithful runs? (AUDIT D1: "plateau / drift / bans / dedup never triggered in
<= 12 cycles"; "the success distiller never fired".)

Setup (preregistered): 6 faithful ``AgentNode`` runs (seeds 0-5) on GeneWorld, 150 cycles each, a gene-writing
proposer, the naive hub (one agent per hub) with a deterministic keyword stand-in for the hub's
``/a2a/signal/analyze`` (its real prompt is not public). Every call of the de-duplicator, the plateau detector,
the layer-3 client and the success distiller is logged with its inputs, and each firing is re-derived by an
independent check written here from the spec text (not by calling the engine's code):

* suppression: each suppressed signal's collapsed key appears in >= 3 of the last 8 events;
* failure streak: ``ban_gene:<id>`` only after >= 5 trailing non-empty failed events, and <id> is the most used
  gene of the last 8 events;
* plateau override: >= 5 (suggested) / >= 10 (required) trailing non-success outcomes among the last 10 events;
* repair loop: >= 3 trailing ``repair`` intents; empty-cycle loop: >= 4 empty events among the last 8;
* layer 3: a request exactly on cycles 1, 6, 11, ... with ``corpus_summary`` <= 2000 chars;
* success distiller (amendment A1): fired only when solidify_count % 5 == 0 or should_distill (>= 7 successes in the
  last 10 capsules, >= 10 good capsules, >= 24 h since the last distillation); >= 10 good capsules; source gene =
  argmax(2*count + mean score) over good capsules grouped by gene; validation = the source's first <= 4 commands
  (else the fallback), filtered by the faithful allowlist.

P7r (review response, preregistered 2026-09-30): the checks above measure precision only (every firing obeys the
rule). P7r adds recall: every dedup call where the suppression condition holds for a non-protected input signal
must suppress it, and every call with >= 5 trailing non-empty failures and a gene in the last 8 events must emit
``ban_gene``. It also recounts the distinct good capsules in each final store (M11).

Run: python experiments/evomap/x20_engine_coverage.py [--seeds 6] [--workers 2] [--quick]
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict

from _common import FAITHFUL_HINT, fmt, parse_args, pmap, save, summarize

from rsi.evomap import AgentNode, Config, LocalStore, NaiveEvoMapHub
from rsi.evomap.validation import CommandPolicy


def stand_in_analyzer(payload):
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


def is_empty(e):
    br = e.get("blast_radius") or {}
    return not br.get("files") and not br.get("lines")


def one(job):
    seed, args = job
    from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, WorldConfig, make_domain
    dom = make_domain(WorldConfig(seed=seed))
    hub = NaiveEvoMapHub(signal_analyzer=stand_in_analyzer)
    cfg = Config(mode="faithful", seed=seed, validation_hint=FAITHFUL_HINT)
    rng0 = random.Random(seed)
    ag = AgentNode(f"x20_{seed}", dom, dom.seed_artifact(), llm_task=GeneWorldModel(rng0.gauss(-0.3, 0.5)),
                   llm_propose=GeneWorldProposer(dom.world, 0.6, 0.3), config=cfg, hub=hub,
                   store=LocalStore(node_id=f"x20_{seed}"))
    log = defaultdict(list)
    cycle = {"t": 0}

    dd_apply = ag.deduper.apply

    def dd_wrap(signals, recent):
        ev = [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in recent]
        res = dd_apply(signals, recent)
        log["dedup"].append({"t": cycle["t"], "in": list(signals), "ev": ev[-10:], "out": list(res.signals),
                             "suppressed": list(res.suppressed), "ban": res.ban_gene})
        return res
    ag.deduper.apply = dd_wrap

    pl_override = ag.plateau.override

    def pl_wrap(recent, hub_directive=None):
        ev = [e.to_dict() if hasattr(e, "to_dict") else dict(e) for e in recent][-10:]
        res = pl_override(recent, hub_directive)
        log["plateau"].append({"t": cycle["t"], "ev": ev, "active": res.active, "severity": res.severity})
        return res
    ag.plateau.override = pl_wrap

    auto = ag.distiller.auto_distill
    policy = CommandPolicy.faithful()

    def auto_wrap(store):
        caps = list(store.capsules.values())
        good = [c for c in caps if c.outcome.get("status") == "success" and float(c.outcome.get("score", 1)) >= 0.7]
        now = store.clock.now()
        last_at = ag.distiller.last_at
        pre = {"t": cycle["t"], "solidify_count": store.solidify_count, "n_good": len(good),
               "succ_last10": sum(1 for c in caps[-10:] if c.outcome.get("status") == "success"),
               "hours_since_last": None if last_at is None else (now - last_at) / 3600,
               "genes": {g.id: list(g.validation) for g in store.genes.values()}}
        groups = defaultdict(list)
        for c in good:
            groups[c.gene].append(float(c.outcome.get("score", 0)))
        pre["expected_source"] = max(groups.items(), key=lambda kv: (2 * len(kv[1]) + sum(kv[1]) / len(kv[1]),
                                                                     kv[0]))[0] if groups else None
        res = auto(store)
        pre.update({"ok": bool(res and res.ok), "reason": getattr(res, "reason", ""),
                    "gene": res.gene.id if res and res.gene else None,
                    "source": (res.gene.provenance or {}).get("source_gene") if res and res.gene else None,
                    "validation": list(res.gene.validation) if res and res.gene else None})
        log["distill"].append(pre)
        return res
    ag.distiller.auto_distill = auto_wrap

    tasks = dom.tasks.split("evolve")
    rng = random.Random(f"x20-{seed}")
    T = 40 if args.quick else 150
    for _ in range(T):
        cycle["t"] += 1
        ag.cycle(rng.choice(tasks))
    return check(seed, log, ag, policy, T)


def check(seed, log, ag, policy, T):
    rows = {"seed": seed, "cycles": T}
    # --- suppression
    n_sup = ok_sup = 0
    for d in log["dedup"]:
        last8 = d["ev"][-8:]
        for s in d["suppressed"]:
            n_sup += 1
            ok_sup += sum(1 for e in last8 if collapse(s) in {collapse(x) for x in e.get("signals", [])}) >= 3
    # --- suppression RECALL (P7r, review response): every non-protected input signal whose collapsed key is in
    # >= 3 of the last 8 events must be suppressed ("task:" descriptors are protected, a documented deviation)
    n_sup_due = hit_sup_due = n_sup_protected = 0
    for d in log["dedup"]:
        last8 = d["ev"][-8:]
        for s in dict.fromkeys(d["in"]):
            if sum(1 for e in last8 if collapse(s) in {collapse(x) for x in e.get("signals", [])}) >= 3:
                if s.startswith("task:"):
                    n_sup_protected += 1
                    continue
                n_sup_due += 1
                hit_sup_due += s in d["suppressed"]
    # --- failure streak -> ban_gene
    n_ban = ok_ban = 0
    for d in log["dedup"]:
        if not d["ban"]:
            continue
        n_ban += 1
        k = 0
        for e in reversed(d["ev"]):
            if is_empty(e) or (e.get("outcome") or {}).get("status") != "failed":
                break
            k += 1
        most = Counter(g for e in d["ev"][-8:] for g in e.get("genes_used", [])).most_common(1)
        ok_ban += k >= 5 and most and most[0][0] == d["ban"]
    # --- ban_gene RECALL (P7r): >= 5 trailing non-empty failures in the last 10 events and a gene used in the
    # last 8 -> a ban must fire
    n_ban_due = hit_ban_due = 0
    for d in log["dedup"]:
        k = 0
        for e in reversed(d["ev"][-10:]):
            if is_empty(e) or (e.get("outcome") or {}).get("status") != "failed":
                break
            k += 1
        used = [g for e in d["ev"][-8:] for g in e.get("genes_used", [])]
        if k >= 5 and used:
            n_ban_due += 1
            hit_ban_due += bool(d["ban"])
    # --- repair loop / empty loop (fire iff the rule holds)
    n_rep = ok_rep = n_emp = ok_emp = 0
    for d in log["dedup"]:
        rep = 0
        for e in reversed(d["ev"]):
            if e.get("intent") == "repair":
                rep += 1
            else:
                break
        if rep >= 3:
            n_rep += 1
            ok_rep += "force_innovation_after_repair_loop" in d["out"]
        emp = sum(1 for e in d["ev"][-8:] if is_empty(e))
        if "empty_cycle_loop_detected" in d["out"] or emp >= 4:
            n_emp += 1
            ok_emp += ("empty_cycle_loop_detected" in d["out"]) == (emp >= 4)
    # --- plateau
    n_pl = ok_pl = 0
    for d in log["plateau"]:
        k = 0
        for e in reversed(d["ev"]):
            if (e.get("outcome") or {}).get("status") != "success":
                k += 1
            else:
                break
        expect = "required" if k >= 10 else ("suggested" if k >= 5 else "none")
        if d["active"] or expect != "none":
            n_pl += 1
            ok_pl += (d["severity"] if d["active"] else "none") == expect
    # --- layer 3
    calls = ag.llm_signals.calls
    l3_cycles = [c["count"] for c in calls]
    l3_expected = list(range(1, T + 1, 5))
    l3_ok = l3_cycles == l3_expected and all(len(c["payload"]["corpus_summary"]) <= 2000 for c in calls)
    # --- success distiller
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
    rows.update({"suppression_firings": n_sup, "suppression_ok": ok_sup, "ban_firings": n_ban, "ban_ok": ok_ban,
                 "repair_loop_cases": n_rep, "repair_loop_ok": ok_rep, "empty_loop_cases": n_emp,
                 "empty_loop_ok": ok_emp, "plateau_cases": n_pl, "plateau_ok": ok_pl,
                 "plateau_active": sum(d["active"] for d in log["plateau"]),
                 "l3_calls": len(calls), "l3_ok": bool(l3_ok),
                 "l3_signals_merged": sum(bool(c["signals"]) for c in calls),
                 "distill_calls": len(log["distill"]), "distill_firings": n_d, "distill_ok": ok_d,
                 "distill_validation_empty_literal": lit_empty,
                 "distill_not_ok_reasons": dict(Counter(d["reason"] for d in log["distill"] if not d["ok"])),
                 "failure_distilled_genes": sum(1 for g in ag.store.genes if g.startswith("gene_repair_distilled_")),
                 "solidify_count": ag.store.solidify_count, "n_genes": len(ag.store.genes),
                 "suppression_due": n_sup_due, "suppression_due_fired": hit_sup_due,
                 "suppression_due_protected": n_sup_protected,
                 "ban_due": n_ban_due, "ban_due_fired": hit_ban_due,
                 # M11 recount (P7r): distinct good capsules in the final store, recomputed from the store itself
                 "final_distinct_good_capsules": len({c.id for c in ag.store.capsules.values()
                                                      if c.outcome.get("status") == "success"
                                                      and float(c.outcome.get("score", 1)) >= 0.7}),
                 "final_distinct_capsules": len(ag.store.capsules),
                 "max_n_good_seen_by_distiller": max([d["n_good"] for d in log["distill"]] or [0])})
    return rows


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=6)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)
    tot = lambda k: int(sum(r[k] for r in rows))  # noqa: E731
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
    v["recall"] = {"suppression": {"due": tot("suppression_due"), "fired": tot("suppression_due_fired"),
                                   "protected_task_descriptors": tot("suppression_due_protected")},
                   "ban_gene": {"due": tot("ban_due"), "fired": tot("ban_due_fired")},
                   "distinct_good_capsules_per_run": [r["final_distinct_good_capsules"] for r in rows],
                   "distinct_capsules_per_run": [r["final_distinct_capsules"] for r in rows]}
    rc = v["recall"]
    v["P7r_pass"] = bool(rc["suppression"]["due"] > 0 and rc["suppression"]["fired"] == rc["suppression"]["due"] and
                         rc["ban_gene"]["due"] > 0 and rc["ban_gene"]["fired"] == rc["ban_gene"]["due"])
    full = lambda d, n="firings": d[n] > 0 and d["rederived"] == d[n]  # noqa: E731
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
