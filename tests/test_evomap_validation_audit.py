"""Stage-B audit regressions for the gene loop (validation/evomap/AUDIT.md).

* B1  SafeHub spot-check: an outcome-0 adoption report whose own A/B had no headroom (baseline at ceiling) is
      uninformative - neither counted nor slashed; a report with headroom is still spot-checked and slashed;
* B2  safe-mode keep-rule failures (task check, vacuous validation) are SOFT failures, not hard constraint ones;
* B3  the gene-writer prompt carries the §6.1 evolution history of earlier genes for the same signals (and is
      byte-identical to the old prompt when there is none);
* B4  safe mode never re-publishes a gene adopted from the hub under the adopter's name;
* B5  every gate in a traced run recomputes from the raw trial scores recorded in the trace.
"""
from __future__ import annotations

import json
import random

import numpy as np
import pytest

from rsi.core import MockLLM, noise_from_trials
from rsi.domains.geneworld import GeneWorldModel, make_domain
from rsi.domains.geneworld.forge import GeneWorldForge
from rsi.domains.katas import KataMockProposer, KatasDomain, KataSimSolver, seed_harness
from rsi.evomap import AgentNode, Config, Gene, SafeHub, TaskBank, run
from rsi.evomap.prompts import gene_writer_prompt
from rsi.evomap.solidify import classify_failure_mode
from rsi.trace import load_trace

HINT = " (it MUST fail before the fix and pass after it, e.g. the public check script)"


# ----------------------------------------------------------------------------- B1
def _honest_bundle(dom, cl="c03", author="alice"):
    w = dom.world
    key = w.class_keys(cl, "best")[0]
    g = Gene(id=f"gene_{cl}_best", signals_match=w.keywords[cl][:2],
             strategy=[f"Read the failing {cl} handler.", w.strategy_step(key), "Re-run the public check."],
             summary=f"Fix {cl} handler failures", validation=["python check.py"])
    before = {"mod.py": w.module(cl, "buggy"), "check.py": w.check_script(cl)}
    after = {**before, "mod.py": w.module(cl, "fixed")}
    return GeneWorldForge(w)._bundle(g, author, cl, before, after,
                                     claims={"confidence": 0.9, "files": 1, "lines": 2, "score": 0.9, "streak": 2})


def test_spot_check_spares_an_uninformative_ceiling_report():
    dom = make_domain()
    hub = SafeHub(TaskBank(dom, dom.seed_artifact(), GeneWorldModel(0.0, "ref"), split="test", n=24, n_off=12, k=4),
                  seed=0, rho=1.0)
    for a in ("honest", "liar"):
        hub.register(a, cluster=a)
    d = hub.publish(_honest_bundle(dom), "alice")
    assert d.status == "verified"
    ceiling = {"tasks_hash": "x", "dS": 0.0, "delta": 0.0, "n": 1, "k": 2, "S_base": 1.0, "S_gene": 1.0}
    row = hub.report_outcome(d.asset_id, "honest", 0, ceiling)
    assert not row["counted"] and "no headroom" in row["reason"] and "spot_check" not in row
    assert hub.n_slashed == 0 and hub.credits.balance["honest"] == 500
    # with headroom a "no gain" claim is still spot-checked: the hub measures a real uplift -> slashed
    headroom = {**ceiling, "S_base": 0.5, "S_gene": 0.5}
    row = hub.report_outcome(d.asset_id, "liar", 0, headroom)
    assert not row["counted"] and row["spot_check"]["agree"] is False and hub.n_slashed == 1


def test_quarantine_proof_carries_the_consumer_baseline():
    dom = KatasDomain(scheme="audit")
    h = seed_harness()
    from rsi.evomap import QuarantineGate
    from rsi.domains.katas.genes import CLASS_GENES
    q = QuarantineGate(dom, h, KataSimSolver(0.0, name="q"), dom.tasks.split("val"), k=2, min_tasks=1)
    res = q.test(CLASS_GENES["dates"])
    assert {"S_base", "S_gene", "dS", "tasks_hash"} <= set(res.proof)
    assert res.proof["S_base"] == pytest.approx(res.S_base) and res.proof["dS"] == pytest.approx(res.dS)


# ----------------------------------------------------------------------------- B2
def test_safe_mode_keep_rule_failures_are_soft():
    assert classify_failure_mode([], [], True, extra=["task_check_failed"]) == ("soft", "task_check", True)
    assert classify_failure_mode([], [], True, extra=["vacuous_validation:empty_list"]) == ("soft", "validation", True)
    assert classify_failure_mode([], [], False, extra=["task_check_failed"]) == ("soft", "validation", True)
    # real constraint violations stay hard
    assert classify_failure_mode(["max_files exceeded: 9 > 5"], [], True, extra=["task_check_failed"])[0] == "hard"
    assert classify_failure_mode(["forbidden_path touched: .git/x"], [], True)[1] == "constraint_destructive"


# ----------------------------------------------------------------------------- B3
def test_writer_prompt_history_block():
    base = gene_writer_prompt(["task:dates", "dates"], "def f(): ...", "trace", validation_hint=HINT)
    assert gene_writer_prompt(["task:dates", "dates"], "def f(): ...", "trace", validation_hint=HINT,
                              history=()) == base                       # no history -> byte-identical prompt
    hist = [{"id": "gene_old", "outcome": "FAILED", "why": "task_check_failed", "summary": "use lower()",
             "strategy": ["Lowercase both strings."], "avoid": ["casefold"]}]
    p = gene_writer_prompt(["task:dates", "dates"], "def f(): ...", "trace", validation_hint=HINT, history=hist)
    assert "do NOT repeat a card that failed" in p and "gene_old: FAILED (task_check_failed)" in p
    assert "Lowercase both strings" in p and p.index("gene_old") < p.index("Rules:")


class _RecordingWriter(MockLLM):
    """Always writes the same shallow card (it never solves the task), recording every prompt."""

    def __init__(self):
        self.prompts = []
        super().__init__(self._respond, name="rec-writer")

    def _respond(self, prompt, system, seed, i):
        self.prompts.append(prompt)
        n = len(self.prompts)
        return "```json\n" + json.dumps({"id": f"gene_dates_shallow_{n}", "category": "repair",
                                         "signals_match": ["dates", "calendar"], "summary": "Be careful with dates.",
                                         "strategy": ["Read the spec.", "Write code.", "Check examples."],
                                         "avoid": ["Overthinking."], "validation": ["python smoke_test.py"]}) + "\n```"


def test_writer_sees_its_earlier_failed_gene_for_the_same_signals():
    dom = KatasDomain(scheme="audit")
    writer = _RecordingWriter()
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
    if not first.solidified:                       # the shallow card failed its retry -> the writer is told so
        assert f"{first.gene_id}: FAILED" in writer.prompts[1]
    assert "Recent evolution history" in writer.prompts[1]


# ----------------------------------------------------------------------------- B4 + B5
@pytest.fixture(scope="module")
def population(tmp_path_factory):
    out = tmp_path_factory.mktemp("pop")
    dom = KatasDomain(scheme="audit")
    h = seed_harness()
    hub = SafeHub(TaskBank(dom, h, KataSimSolver(0.0, name="hub-ref"), split="test", n=24, n_off=4, k=4), seed=0)
    calls = []
    orig = hub.publish

    def publish(b, author):
        calls.append((author, b.gene.get("id"), (b.gene.get("provenance") or {}).get("kind")))
        return orig(b, author)

    hub.publish = publish
    kw = dict(mode="safe", quarantine_min_tasks=1, trace_baseline_k=2, monitor_k=2)
    run(dom, h, llm_task=KataSimSolver(0.0, name="sim-a"), llm_propose=KataMockProposer(0.7),
        config=Config(cycles=12, seed=0, validation_hint=HINT, **kw), hub=hub, name="agent0", out_dir=out / "agent0")
    b = run(dom, h, llm_task=KataSimSolver(0.0, name="sim-b"), llm_propose=None, config=Config(cycles=8, seed=1, **kw),
            hub=hub, name="agent1", out_dir=out / "agent1")
    return out, hub, calls, b


def test_adopted_gene_is_not_republished_in_safe_mode(population):
    out, hub, calls, b = population
    adopted = [g for g in b.meta["agent"].store.genes.values() if (g.provenance or {}).get("kind") == "external"]
    assert adopted, "agent1 adopted nothing: the scenario did not exercise the rule"
    assert all(kind != "external" for _, _, kind in calls)
    assert not [c for c in calls if c[0] == "agent1"]


def _lcb(d, alpha=0.10, reps=20000):
    d = np.asarray(d, float)
    rng = np.random.default_rng(0)
    return float(np.quantile(d[rng.integers(0, len(d), (reps, len(d)))].mean(1), alpha / 2))


def test_every_gate_recomputes_from_the_traced_trials(population):
    out = population[0]
    n_checked = 0
    for agent in ("agent0", "agent1"):
        last, sig = {}, {}
        for e in load_trace(out / agent):
            k, d = e["kind"], e["data"]
            if k == "analysis":
                sig[e["round"]] = d["signals"]
            if k == "eval":
                last[d["candidate"]] = d
            if k != "gate":
                continue
            c, m = d["candidate"], d.get("math") or {}
            if c.startswith("hub-publish:") and "U" in m:
                g = c.split(":", 1)[1]
                bi, gi = last[f"hub-bank:base_in:{g}"], last[f"hub-bank:gene_in:{g}"]
                diffs = [gi["per_task"][t] - bi["per_task"][t] for t in bi["per_task"]]
                assert np.mean(diffs) == pytest.approx(m["U"])
                assert _lcb(diffs) == pytest.approx(m["U_LCB"], abs=0.02)
                assert noise_from_trials(np.array(list(bi["trials"].values())), z=2).delta == pytest.approx(m["delta"])
                acc = m["U_LCB"] >= m["delta"] and m["U_LCB"] > 0 and m["R"] >= -m["delta"]
                assert acc == d["accept"]
                n_checked += 1
            elif c.startswith("quarantine:") and "S_no_gene" in m:
                g = c.split(":", 1)[1]
                b0, b1 = last["quarantine:no_gene"], last[f"quarantine:{g}"]
                delta = noise_from_trials(np.array(list(b0["trials"].values())), z=2).delta
                dS = b1["summary"]["S"] - b0["summary"]["S"]
                dC = (b1["summary"]["C"] - b0["summary"]["C"]) / b0["summary"]["C"]
                cost = (dC <= 0.1 + 40 * dS) if dS > delta + 1e-9 else (100 * dS - 15 * dC > 1e-9)
                assert delta == pytest.approx(m["delta"]) and dS == pytest.approx(m["dS"])
                assert ((dS >= -delta) and cost and dS > 0) == d["accept"]
                n_checked += 1
            elif d.get("stage") == "solidify":
                cons, val = m["constraints"], m["validation"]
                vac = (m.get("vacuity") or {}).get("vacuous", False)
                ok = cons["ok"] and val["ok"] and not m["protocol_violations"] and not vac and \
                    m["task_score"] >= m["success_threshold"]
                assert ok == d["accept"]
                n_sig = len(sig[e["round"]])
                sel = 0.3 if c == "no_gene" else 0.9
                files = cons["blast"]["files"]
                bc = 0.4 if files == 0 else (1.0 if files <= 6 else 0.7)
                v = (val["n_passed"] / val["n_run"]) if val["n_run"] else (0.5 if val["ok"] else 0.0)
                comp = round(.05 * min(1, .4 + .1 * n_sig) + .1 * sel + .05 * .5 + .15 * bc
                             + .25 * max(0, 1 - .25 * len(cons["violations"])) + .25 * v + .1 + .05, 2)
                assert comp == m["composite_score"]
                n_checked += 1
    assert n_checked >= 10
