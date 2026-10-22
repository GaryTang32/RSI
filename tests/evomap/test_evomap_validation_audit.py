"""Audit regressions: spot-check headroom, soft keep-rule failures, writer history, no re-publish, gate math."""
from __future__ import annotations

import numpy as np
import pytest

from evomap_helpers import HINT, ShallowWriter, honest_bundle, safe_hub
from rsi.core import noise_from_trials
from rsi.domains.katas import KatasDomain, KataSimSolver, seed_harness
from rsi.domains.katas.genes import CLASS_GENES
from rsi.evomap import AgentNode, Config, QuarantineGate
from rsi.evomap.prompts import gene_writer_prompt
from rsi.evomap.solidify import classify_failure_mode, js_round2
from rsi.trace import load_trace


def test_spot_check_spares_an_uninformative_ceiling_report(gw):
    hub = safe_hub(gw, rho=1.0)
    for a in ("honest", "liar"):
        hub.register(a, cluster=a)
    d = hub.publish(honest_bundle(gw), "alice")
    assert d.status == "verified"
    ceiling = {"tasks_hash": "x", "dS": 0.0, "delta": 0.0, "n": 1, "k": 2, "S_base": 1.0, "S_gene": 1.0}
    row = hub.report_outcome(d.asset_id, "honest", 0, ceiling)
    assert not row["counted"] and "no headroom" in row["reason"] and "spot_check" not in row
    assert hub.n_slashed == 0 and hub.credits.balance["honest"] == 500
    row = hub.report_outcome(d.asset_id, "liar", 0, {**ceiling, "S_base": 0.5, "S_gene": 0.5})
    assert not row["counted"] and row["spot_check"]["agree"] is False and hub.n_slashed == 1


def test_quarantine_proof_carries_the_consumer_baseline():
    dom = KatasDomain(scheme="audit")
    q = QuarantineGate(dom, seed_harness(), KataSimSolver(0.0, name="q"), dom.tasks.split("val"), k=2, min_tasks=1)
    res = q.test(CLASS_GENES["dates"])
    assert {"S_base", "S_gene", "dS", "tasks_hash"} <= set(res.proof)
    assert res.proof["S_base"] == pytest.approx(res.S_base) and res.proof["dS"] == pytest.approx(res.dS)


def test_safe_mode_keep_rule_failures_are_soft():
    assert classify_failure_mode([], [], True, extra=["task_check_failed"]) == ("soft", "task_check", True)
    assert classify_failure_mode([], [], True, extra=["vacuous_validation:empty_list"]) == ("soft", "validation", True)
    assert classify_failure_mode([], [], False, extra=["task_check_failed"]) == ("soft", "validation", True)
    assert classify_failure_mode(["max_files exceeded: 9 > 5"], [], True, extra=["task_check_failed"])[0] == "hard"
    assert classify_failure_mode(["forbidden_path touched: .git/x"], [], True)[1] == "constraint_destructive"


def test_writer_prompt_history_block():
    args = (["task:dates", "dates"], "def f(): ...", "trace")
    base = gene_writer_prompt(*args, validation_hint=HINT)
    assert gene_writer_prompt(*args, validation_hint=HINT, history=()) == base
    hist = [{"id": "gene_old", "outcome": "FAILED", "why": "task_check_failed", "summary": "use lower()",
             "strategy": ["Lowercase both strings."], "avoid": ["casefold"]}]
    p = gene_writer_prompt(*args, validation_hint=HINT, history=hist)
    assert "do NOT repeat a card that failed" in p and "gene_old: FAILED (task_check_failed)" in p
    assert "Lowercase both strings" in p and p.index("gene_old") < p.index("Rules:")


def test_writer_sees_its_earlier_failed_gene_for_the_same_signals():
    dom = KatasDomain(scheme="audit")
    writer = ShallowWriter(name="rec-writer")
    ag = AgentNode("a", dom, seed_harness(), llm_task=KataSimSolver(0.0, name="s"), llm_propose=writer,
                   config=Config(mode="safe", seed=0, validation_hint=HINT, trace=False))
    task = next(t for t in dom.tasks.split("evolve") if t.family == "dates")
    for _ in range(8):
        ag.cycle(task)
        if len(writer.prompts) >= 2:
            break
    assert len(writer.prompts) >= 2, "the scratch solve never failed twice"
    assert "Recent evolution history" not in writer.prompts[0]
    first = ag.results[0]
    if not first.solidified:
        assert f"{first.gene_id}: FAILED" in writer.prompts[1]
    assert "Recent evolution history" in writer.prompts[1]


def test_adopted_gene_is_not_republished_in_safe_mode(traced_population):
    calls, genes = traced_population.calls, traced_population.b.meta["agent"].store.genes.values()
    adopted = [g for g in genes if (g.provenance or {}).get("kind") == "external"]
    assert adopted, "agent1 adopted nothing: the scenario did not exercise the rule"
    assert all(kind != "external" for _, _, kind in calls)
    assert not [c for c in calls if c[0] == "agent1"]


def _lcb(d, alpha=0.10, reps=20000):
    d = np.asarray(d, float)
    rng = np.random.default_rng(0)
    return float(np.quantile(d[rng.integers(0, len(d), (reps, len(d)))].mean(1), alpha / 2))


def _check_publish(m, accept, last, g):
    bi, gi = last[f"hub-bank:base_in:{g}"], last[f"hub-bank:gene_in:{g}"]
    diffs = [gi["per_task"][t] - bi["per_task"][t] for t in bi["per_task"]]
    assert np.mean(diffs) == pytest.approx(m["U"])
    assert _lcb(diffs) == pytest.approx(m["U_LCB"], abs=0.02)
    assert noise_from_trials(np.array(list(bi["trials"].values())), z=2).delta == pytest.approx(m["delta"])
    assert (m["U_LCB"] >= m["delta"] and m["U_LCB"] > 0 and m["R"] >= -m["delta"]) == accept


def _check_quarantine(m, accept, last, g):
    b0, b1 = last["quarantine:no_gene"], last[f"quarantine:{g}"]
    delta = noise_from_trials(np.array(list(b0["trials"].values())), z=2).delta
    dS = b1["summary"]["S"] - b0["summary"]["S"]
    dC = (b1["summary"]["C"] - b0["summary"]["C"]) / b0["summary"]["C"]
    cost = (dC <= 0.1 + 40 * dS) if dS > delta + 1e-9 else (100 * dS - 15 * dC > 1e-9)
    assert delta == pytest.approx(m["delta"]) and dS == pytest.approx(m["dS"])
    assert ((dS >= -delta) and cost and dS > 0) == accept


def _check_solidify(m, accept, candidate, n_sig):
    cons, val = m["constraints"], m["validation"]
    vac = (m.get("vacuity") or {}).get("vacuous", False)
    thr = m["success_threshold"]
    smp = m.get("new_gene_sample")
    if smp:  # a new gene is judged on its retry sample, not on one trial
        w = sum(x >= thr for x in smp["with"])
        solved = w > len(smp["with"]) / 2 and (smp["check"] != "paired" or
                                              w > sum(x >= thr for x in smp["without"]))
        assert solved == m["task_solved"]
    else:
        solved = m["task_score"] >= thr
    assert (cons["ok"] and val["ok"] and not m["protocol_violations"] and not vac and solved) == accept
    sel = 0.3 if candidate == "no_gene" else 0.9
    files = cons["blast"]["files"]
    bc = 0.4 if files == 0 else (1.0 if files <= 6 else 0.7)
    v = (val["n_passed"] / val["n_run"]) if val["n_run"] else (0.5 if val["ok"] else 0.0)
    comp = js_round2(.05 * min(1, .4 + .1 * n_sig) + .1 * sel + .05 * .5 + .15 * bc
                     + .25 * max(0, 1 - .25 * len(cons["violations"])) + .25 * v + .1 + .05)
    assert comp == m["composite_score"]


def test_every_gate_recomputes_from_the_traced_trials(traced_population):
    n_checked = 0
    for agent in ("agent0", "agent1"):
        last, sig = {}, {}
        for e in load_trace(traced_population.out / agent):
            k, d = e["kind"], e["data"]
            if k == "analysis":
                sig[e["round"]] = d["signals"]
            if k == "eval":
                last[d["candidate"]] = d
            if k != "gate":
                continue
            c, m = d["candidate"], d.get("math") or {}
            if c.startswith("hub-publish:") and "U" in m:
                _check_publish(m, d["accept"], last, c.split(":", 1)[1])
            elif c.startswith("quarantine:") and "S_no_gene" in m:
                _check_quarantine(m, d["accept"], last, c.split(":", 1)[1])
            elif d.get("stage") == "solidify":
                _check_solidify(m, d["accept"], c, len(sig[e["round"]]))
            else:
                continue
            n_checked += 1
    assert n_checked >= 10
