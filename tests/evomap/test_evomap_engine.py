"""rsi.evomap local engine: signals, memory graph, selection, validation, solidify, distillation."""
import math
import random

import pytest

from rsi.evomap import (Clock, CommandPolicy, Distiller, Gene, GeneScorer, GeneSelector, InProcessExecutor,
                        LeakageAuditor, LocalStore, MemoryGraph, Mutation, SignalDeduper, ValidationRunner,
                        VacuityDetector, composite_score, drift_intensity, make_mutants, match_pattern)
from rsi.evomap.memory import OutcomeInferrer
from rsi.evomap.signals import PlateauDetector, RegexSignalExtractor
from rsi.evomap.solidify import blast_radius, classify_failure_mode
from rsi.evomap.validation import ValidationResult


def test_pattern_matching():
    assert match_pattern("error|错误", ["log_error"])
    assert match_pattern("/time(out|d out)/i", ["TimedOut happened"]) is False
    assert match_pattern("/timeout/", ["request TIMEOUT"])
    assert match_pattern("Unicode", ["task:unicode"]) and not match_pattern("dates", ["x"])


def test_regex_extractor_and_dedup_rules():
    sig = RegexSignalExtractor().extract_text("Traceback\nValueError: bad value\nrequest timed out")
    assert "log_error" in sig and any(s.startswith("errsig:") for s in sig) and "perf_bottleneck" in sig
    ev = [{"signals": ["log_error", "task:x"], "intent": "repair", "genes_used": ["gene_bad"],
           "blast_radius": {"files": 1, "lines": 3}, "outcome": {"status": "failed", "score": 0.2}} for _ in range(6)]
    r = SignalDeduper().apply(["log_error", "task:x"], ev)
    assert "log_error" not in r.signals and "task:x" in r.signals            # suppressed; task descriptor protected
    assert "force_innovation_after_repair_loop" in r.signals
    assert "failure_loop_detected" in r.signals and "ban_gene:gene_bad" in r.signals
    assert "plateau_pivot_required" in r.signals and "high_failure_ratio" in r.signals
    empty = [{"signals": [], "blast_radius": {"files": 0, "lines": 0}, "outcome": {"status": "success", "score": .8}}] * 5
    r2 = SignalDeduper().apply(["task:y"], empty)
    assert {"force_steady_state", "evolution_saturation", "explore_opportunity", "empty_cycle_loop_detected"} <= set(r2.signals)
    pl = PlateauDetector().override([{"outcome": {"status": "failed"}}] * 10)
    assert pl.active and pl.severity == "required"


def _fail_n(mode, n, predictive=None, **kw):
    mg = MemoryGraph(clock=Clock())
    for _ in range(n):
        mg.record_outcome(signals=["a", "b"], gene_id="g", status="failed", score=0.1, predictive=predictive)
    return "g" in mg.advice(["a", "b"], mode=mode, **kw).banned_gene_ids


def test_ban_latency_matches_spec_arithmetic():
    # spec rule: n >= 2 and (s+1)/(n+2) < 0.18 -> banned at 4 failures, not at 3
    assert not _fail_n("spec", 3) and _fail_n("spec", 4)
    # current rule: per-key attempts >= 4 and best < 0.15 -> 5 failures (1/7); 6 with the x1.15 predictive factor
    assert not _fail_n("current", 4) and _fail_n("current", 5)
    assert not _fail_n("current", 5, predictive={"trend": 0}) and _fail_n("current", 6, predictive={"trend": 0})


def test_memory_preferred_and_inert():
    mg = MemoryGraph(clock=Clock())
    for _ in range(3):
        mg.record_outcome(signals=["a"], gene_id="good", status="success", score=0.9)
        mg.record_outcome(signals=["a"], gene_id="bad", status="failed", score=0.1)
    assert mg.advice(["a"]).preferred_gene_id == "good"
    mg2 = MemoryGraph(clock=Clock())
    for _ in range(8):
        mg2.record_outcome(signals=["a"], gene_id="inert", status="success", score=0.6, note="stable_no_error")
    adv = mg2.advice(["a"])
    assert "inert" in adv.banned_gene_ids and adv.preferred_gene_id is None
    # epochs: pre-epoch evidence x0.1
    mg3 = MemoryGraph(clock=Clock())
    for _ in range(3):
        mg3.record_outcome(signals=["a"], gene_id="g", status="success", score=0.9)
    before = mg3.advice(["a"]).scores["g"]
    mg3.clock.tick()
    mg3.epoch_boundary("reset")
    assert mg3.advice(["a"]).scores["g"] < before


def test_outcome_inferrer_faithful_vs_safe():
    tr = 'work...\n{"type": "EvolutionEvent", "outcome": {"status": "success", "score": 0.9}}'
    from rsi.evomap.memory import Outcome
    measured = Outcome("failed", 0.3, "solidify_measured", True)
    f = OutcomeInferrer("faithful").infer(prev_error=True, cur_error=True, transcript=tr, measured=measured)
    assert f.status == "success" and f.note == "evolutionevent_observed"
    s = OutcomeInferrer("safe").infer(prev_error=True, cur_error=True, transcript=tr, measured=measured)
    assert s.status == "failed"
    h = OutcomeInferrer("faithful").infer(prev_error=False, cur_error=False)
    assert h.note == "stable_no_error" and h.status == "success"


def test_drift_and_selector():
    assert drift_intensity(17, False) == pytest.approx(1 / math.sqrt(17))
    assert drift_intensity(1, True) == 0.7
    assert drift_intensity(4, True, 0) == pytest.approx(min(1, 0.5 + 0.3))
    assert drift_intensity(4, True, 400) == pytest.approx(0.5 + 0.02)
    g1 = Gene(id="gene_one", signals_match=["alpha", "beta"], strategy=["s"])
    g2 = Gene(id="gene_two", signals_match=["alpha"], strategy=["s"])
    g3 = Gene(id="gene_distilled_one", signals_match=["alpha", "beta"], strategy=["s"])
    sc = GeneScorer("spec")
    assert sc.score(g1, ["alpha", "beta"]) == 2 and sc.score(g3, ["alpha", "beta"]) == pytest.approx(1.6)
    sel = GeneSelector(GeneScorer("spec"))
    from rsi.evomap.memory import Advice
    d = sel.select([g1, g2], [], ["alpha", "beta"], advice=Advice(), rng=random.Random(0), drift_enabled=False)
    assert d.gene is not None
    d2 = sel.select([g1, g2], [], ["alpha", "beta"], advice=Advice(banned_gene_ids={"gene_one"}))
    assert d2.gene.id == "gene_two"
    d3 = sel.select([g1, g2], [], ["alpha", "beta", "ban_gene:gene_one"])
    assert d3.gene.id == "gene_two"
    fc = [{"gene": "gene_one", "trigger": ["alpha", "beta"]}] * 2
    assert sel.select([g1, g2], [], ["alpha", "beta"], failed_capsules=fc).gene.id == "gene_two"
    picks = {sel.select([g1, g2], [], ["alpha", "beta"], rng=random.Random(i), drift_enabled=True).gene.id
             for i in range(40)}
    assert picks == {"gene_one", "gene_two"}          # drift explores


def test_command_policy_and_runner_modes():
    pol = CommandPolicy.safe()
    assert pol.check("python check.py").ok and pol.check("python --version").info_only
    assert not pol.check("python -c 'print(1)'").ok and not pol.check("python a.py; rm -rf /").ok
    assert not pol.check("node x.js").ok and not pol.check("python ../x.py").ok and not pol.check("python").ok
    ex = InProcessExecutor()
    files = {"mod.py": "def f():\n    return 1\n", "check.py": "from mod import f\nassert f() == 1\n"}
    faithful = ValidationRunner(CommandPolicy.faithful(), ex, mode="faithful")
    safe = ValidationRunner(CommandPolicy.safe(), ex, mode="safe")
    assert faithful.run([], files).ok and not safe.run([], files).ok                   # empty list
    r = faithful.run(["python missing.py"], files)
    assert r.ok and r.n_skipped == 1 and r.component == 0.5                           # silent skip
    assert not safe.run(["python missing.py"], files).ok
    assert faithful.run(["python check.py"], files).ok and safe.run(["python check.py"], files).ok
    b = faithful.run(["echo ok"], files)
    assert not b.ok and b.blocked


def test_vacuity_detector_lint_and_discriminative():
    ex = InProcessExecutor()
    vd = VacuityDetector(ValidationRunner(CommandPolicy.safe(), ex, mode="safe"))
    before = {"mod.py": "def f(x):\n    return x + 1\n", "check.py": "from mod import f\nassert f(2) == 4\n"}
    after = {"mod.py": "def f(x):\n    return x + 2\n", "check.py": before["check.py"],
             "smoke.py": "print('ok')\n", "weak.py": "import mod\nassert hasattr(mod, 'f')\n",
             "const.py": "assert True\n"}
    assert not vd.verdict(["python check.py"], before, after).vacuous
    lint = vd.lint(["python --version", "python smoke.py", "python const.py", "python gone.py", "echo ok",
                    "python -c 'x'"], after)
    assert list(map(tuple, lint.values())) == [("info_only",), ("print_only",), ("constant_assert",),
                                                ("missing_script",), ("shell_noop",), ("eval_flag",)]
    assert vd.lint([], after) == {"": ["empty_list"]}
    w = vd.verdict(["python weak.py"], before, after)
    assert w.vacuous and "passes_before_change" in w.reasons and not w.lint["python weak.py"]
    clean_after = {k: after[k] for k in ("mod.py", "check.py")}
    d = vd.discriminate(["python check.py"], before, clean_after)
    assert d.discriminative and d.n_mutants == 2 and d.kill_rate == 1.0
    assert make_mutants(before, after)


def test_composite_score_spec_examples():
    g = Gene(id="gene_named", signals_match=["a"], strategy=["s"], constraints={"max_files": 12, "forbidden_paths": [".git"]})
    m = Mutation(id="m", category="repair")
    ok = ValidationResult(True, None, n_run=1, n_passed=1)
    skipped = ValidationResult(True, None, n_run=0)
    kw = dict(n_signals=1, gene=g, mutation=m, blast={"files": 1, "lines": 5}, max_files=12, estimate=None,
              n_violations=0, n_protocol=0)
    assert composite_score(validation=ok, **kw) == pytest.approx(0.94)          # spec §4.10 worked example
    assert composite_score(validation=skipped, **kw) == pytest.approx(0.815, abs=0.006)
    assert classify_failure_mode(["forbidden_path touched: .git/x"], [], True)[0] == "hard"
    assert classify_failure_mode([], [], False) == ("soft", "validation", True)
    br = blast_radius({"a.py": "x\n", "b.md": "y\n"}, {"a.py": "x\nz\n", "b.md": "y\n", "c.py": "1\n2\n"})
    assert br["files"] == 2 and br["lines"] == 3


def test_distiller_heuristic_every_fifth_and_leakage():
    from rsi.evomap import Capsule
    st = LocalStore(node_id="d")
    src = Gene(id="gene_src", signals_match=["x"], strategy=["a", "b", "c", "d", "e"], validation=["python c.py"])
    st.upsert_gene(src)
    from rsi.evomap import EvolutionEvent
    for i in range(10):
        st.upsert_capsule(Capsule(id=f"c{i}", gene="gene_src", trigger=["x", "y"], summary="fixed it", confidence=0.9,
                                  outcome={"status": "success", "score": 0.9}))
        st.append_event(EvolutionEvent(id=f"e{i}", mutation_id="m"))
    dist = Distiller(mode="faithful")
    assert dist.auto_trigger(st)                        # solidify_count == 10, a multiple of 5
    r = dist.maybe_distill(st)
    assert r.ok and r.gene.id.startswith("gene_distilled_") and r.gene.strategy == ["a", "b", "c", "d"]
    assert r.gene.validation == ["python c.py"] and r.gene.id in st.genes
    assert not dist.auto_distill(st).ok                 # data hash unchanged
    g = Gene(id="gene_leaky", signals_match=["x"], strategy=["return exactly 1234567 for id task-77"], summary="s")
    rep = LeakageAuditor().audit(g, public_text="compute the value", hidden_text="expected 1234567")
    assert rep.leaks and "1234567" not in g.strategy[0]
    rep2 = LeakageAuditor(deny_terms=["task-77"]).audit(g)
    assert rep2.critic_hits == ["task-77"]
