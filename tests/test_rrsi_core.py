"""RRSI pure components against the released code's test vectors (google-research/rrsi
``tests/test_core.py``) plus the extensions of this implementation."""
import json
import math

import numpy as np
import pytest

from rsi.core import Artifact, MetricGuard
from rsi.rrsi import (Candidate, Config, History, K, Measurement, RegularizerSwitches, Taxonomy, aggregate,
                      budget_table, build_gates, cost_rule, edit_budget, exploration, select_round, stall_flag)
from rsi.rrsi.calibrate import calibrate
from rsi.rrsi.components import normalize, novelty
from rsi.rrsi.evaluate import TaskResult, job_seeds, relative_cost_change


def _ev(job, rewards_by_task, k, tokens=1000.0):
    per = {t: TaskResult(rewards=list(r), tokens=[tokens] * len(r)) for t, r in rewards_by_task.items()}
    return aggregate(job, k, per)


# ---------------------------------------------------------------- schedule (Eq. anneal)
def test_schedule_matches_eq_anneal():
    T, bmin, bmax = 20, 1, 4
    for t in range(T):
        expect = math.ceil(bmin + (bmax - bmin) * 0.5 * (1 + math.cos(math.pi * t / T)))
        assert edit_budget(t, T, bmin, bmax) == expect
    tab = budget_table(T, bmin, bmax)
    assert tab[0] == bmax and tab == sorted(tab, reverse=True)
    assert edit_budget(T, T, bmin, bmax) == bmin


def test_schedule_tables_from_spec():
    assert budget_table(20, 1, 4) == [4] * 8 + [3] * 5 + [2] * 7            # coding
    assert budget_table(20, 1, 3) == [3] * 10 + [2] * 10                    # workspace
    assert budget_table(40, 1, 4) == [4] * 16 + [3] * 9 + [2] * 15          # eng
    assert budget_table(10, 1, 4) == [4, 4, 4, 4, 3, 3, 3, 2, 2, 2]          # overview
    assert min(budget_table(20, 1, 4)) == 2                                 # never reaches b_min under ceil
    assert budget_table(10, 1, 4, "floor_at_bmin_last")[-1] == 1
    assert edit_budget(3, 0, 1, 4) == 4


# -------------------------------------------------------------- estimator (Eq. estimate)
def test_estimator_weights_and_missing():
    ev = _ev("j", {"a": [1, 0], "b": [1, 1]}, 2)
    assert abs(ev.S - 0.75) < 1e-9 and ev.n_expected == 4 and ev.missing == 0
    per = {"a": TaskResult(rewards=[0.5, 1.0], weights=[10, 10]), "b": TaskResult(rewards=[0.0, 0.0], weights=[90, 90])}
    assert abs(aggregate("w", 2, per).S - 15 / 200) < 1e-9
    per = {"a": TaskResult(rewards=[1.0, 0.0], tokens=[100, None], missing=1)}   # missing -> 0, full denominator
    ev = aggregate("m", 2, per)
    assert ev.S == 0.5 and ev.C == 100 and ev.missing == 1
    assert relative_cost_change(None, 10) == 0.0 and relative_cost_change(12, 10) == pytest.approx(0.2)


def test_measurement_roundtrip(tmp_path):
    ev = _ev("j", {"a": [1, 0]}, 2)
    ev.save(tmp_path / "e.json")
    ev2 = Measurement.load(tmp_path / "e.json")
    assert ev2.S == ev.S and ev2.per_task["a"].rewards == [1, 0]
    assert job_seeds("r3A", 2, 0) == job_seeds("r3A", 2, 0) != job_seeds("r3B", 2, 0)


# ------------------------------------------------------------------------ calibration
def test_calibration_bootstrap_and_repeats():
    ev = _ev("base", {f"t{i}": [i % 2, (i + 1) % 2] for i in range(40)}, 2)
    cal = calibrate([ev], z=2.0, reps=300)
    assert cal["delta"] > 0 and cal["method"].startswith("bootstrap")
    ev2 = _ev("base2", {f"t{i}": [1, 1 if i % 3 else 0] for i in range(40)}, 2)
    cal2 = calibrate([ev, ev2], z=2.0, reps=100)
    assert cal2["n_evals"] == 2 and cal2["delta"] >= 0 and "max_abs_diff" in cal2
    corr = calibrate([ev], z=2.0, reps=300, small_k_correction=True)
    assert corr["delta"] == pytest.approx(cal["delta"] * math.sqrt(2), rel=1e-3)


def test_bootstrap_matches_theory_for_bernoulli():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.1, 0.9, 200)
    m = {f"t{i}": list((rng.random(8) < p[i]).astype(float)) for i in range(200)}
    cal = calibrate([_ev("b", m, 8)], z=2.0, reps=2000)
    theory = math.sqrt(2 * np.mean(p * (1 - p)) / (200 * 8))
    # plug-in bootstrap underestimates by sqrt((k-1)/k); at k=8 that is ~6%
    assert 0.8 * theory < cal["sd_null"] < 1.1 * theory


# ------------------------------------------------------------------ selection (Alg. 2)
def test_cost_rule_both_branches():
    cfg = Config(beta0=0.1, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5)
    assert cost_rule(0.05, 0.10 + 40 * 0.05 - 0.01, 0, 0.02, cfg)[0]          # within budget
    assert not cost_rule(0.05, 0.10 + 40 * 0.05 + 0.01, 0, 0.02, cfg)[0]      # over budget
    assert cost_rule(0.0, -0.10, 0, 0.02, cfg)[0]                             # neutral, cheaper
    assert not cost_rule(0.0, 0.10, 0, 0.02, cfg)[0]                          # neutral, costlier
    assert cost_rule(0.0, 0.0, 1, 0.02, cfg)[0]                               # neutral, new structural
    coding = Config.preset("coding")                                          # w_s = 0: dC < nu/30
    assert not cost_rule(0.015, 0.0, 0, coding.delta, coding)[0]
    assert cost_rule(0.0, 0.03, 1, coding.delta, coding)[0] and not cost_rule(0.0, 0.034, 1, coding.delta, coding)[0]


def test_selection_floor_argmax_and_sstar():
    cfg = Config(beta0=0.1, beta1=40.0, w_s=100.0, w_c=15.0, w_n=0.5)
    inc = _ev("inc", {f"t{i}": [1, 1] if i < 5 else [0, 0] for i in range(10)}, 2)                 # S = 0.5
    a = Candidate("A", [{"id": "C1", "component": "prompt"}],
                  ev=_ev("A", {f"t{i}": [1, 1] if i < 7 else [0, 0] for i in range(10)}, 2))         # 0.7
    b = Candidate("B", [{"id": "C1", "component": "skill"}],
                  ev=_ev("B", {f"t{i}": [1, 1] if i < 6 else [0, 0] for i in range(10)}, 2))         # 0.6
    c = Candidate("C", [{"id": "C1", "component": "config"}],
                  ev=_ev("C", {f"t{i}": [1, 1] if i < 3 else [0, 0] for i in range(10)}, 2))         # 0.3: floor
    d = Candidate("D", [], gate_failure="critic_reject")
    win, decs = select_round([a, b, c, d], inc, 0.55, 0.05, cfg, {})
    assert win is a
    by = {x.variant: x for x in decs}
    assert by["A"].admissible and by["B"].admissible
    assert not by["C"].admissible and "floor" in by["C"].reason
    assert not by["D"].admissible and by["D"].reason == "critic_reject"
    exp = Candidate("E", [{"id": "C1", "component": "prompt"}],
                    ev=_ev("E", {f"t{i}": [1, 1] if i < 7 else [0, 0] for i in range(10)}, 2,
                           tokens=1000.0 * (1 + 0.1 + 40 * 0.2 + 0.5)))
    win2, decs2 = select_round([exp], inc, 0.55, 0.05, cfg, {})
    assert win2 is None and "cost rule" in decs2[0].reason
    # a domain guard is non-compensatory (core MetricGuard)
    a.ev.extra["valid_rate"], inc.extra["valid_rate"] = 0.80, 0.95
    gates = build_gates(cfg, RegularizerSwitches.full(), [MetricGuard("valid_rate", 0.03)])
    win3, decs3 = select_round([a], inc, 0.55, 0.05, cfg, {}, gates)
    assert win3 is None and "domain guard" in decs3[0].reason
    # ties -> first; within-band negative dS that saves tokens is admissible (code semantics)
    cheap = Candidate("F", [{"id": "C1", "component": "prompt"}],
                      ev=_ev("F", {f"t{i}": [1, 1] if i < 5 else ([1, 0] if i == 5 else [0, 0]) for i in range(10)}, 2,
                             tokens=500.0))
    worse = _ev("G", {f"t{i}": [1, 1] if i < 5 else [0, 0] for i in range(10)}, 2)
    worse.S -= 0.01
    cheap.ev = worse
    cheap.ev.C = 500.0
    win4, _ = select_round([cheap], inc, 0.5, 0.05, cfg, {})
    assert win4 is cheap


def test_ablation_gates():
    cfg = Config()
    inc = _ev("inc", {f"t{i}": [1, 1] if i < 5 else [0, 0] for i in range(10)}, 2)
    lower = Candidate("A", [{"id": "C1", "component": "prompt"}],
                      ev=_ev("A", {f"t{i}": [1, 1] if i < 4 else [0, 0] for i in range(10)}, 2, tokens=100.0))
    # full: S*=0.6 floor 0.55 blocks 0.4; S_t floor (0.45) also blocks; no floor admits the cheaper candidate
    assert select_round([lower], inc, 0.6, 0.05, cfg, {}, build_gates(cfg, RegularizerSwitches.full()))[0] is None
    assert select_round([lower], inc, 0.6, 0.15, cfg, {},
                        build_gates(cfg, RegularizerSwitches.full().but(floor="S_t")))[0] is lower
    assert select_round([lower], inc, 0.6, 0.05, cfg, {},
                        build_gates(cfg, RegularizerSwitches.full().but(floor="none")))[0] is lower
    # greedy (unregularized): only strictly better
    assert select_round([lower], inc, 0.6, 0.05, cfg, {}, build_gates(cfg, RegularizerSwitches.none()))[0] is None


# ------------------------------------------------------------------- history summaries
def test_history_summaries_prune_and_explore(tmp_path):
    h = History(tmp_path / "history.jsonl")
    h.append_candidate(0, "A", [{"id": "C1", "component": "prompt", "hypothesis": "h1"}], "ACCEPTED", 0.03, 0.05,
                       True, 0.53, 1000, None)
    h.append_candidate(1, "A", [{"id": "C1", "component": "prompt", "hypothesis": "h2"}], "REJECTED", -0.01, 0.0,
                       False, 0.52, 1000, None)
    h.append_candidate(1, "B", [{"id": "C1", "component": "skill", "hypothesis": "h3"},
                                {"id": "C2", "component": "memory", "hypothesis": "h4"}], "REJECTED", -0.02, 0.3,
                       False, 0.51, 1300, None)
    h.append_candidate(2, "A", [{"id": "C1", "component": "config", "hypothesis": "h5"}], "critic_reject", None,
                       None, False, None, None, None, "leak")
    assert h.tried() == {"prompt", "skill", "memory"}
    g = h.yield_g(t=3, n_prune=4)
    assert g["prompt"] == 0.03 and g["skill"] == -0.02
    assert h.yield_g(t=5, n_prune=4)["prompt"] == -0.01
    assert h.yield_g(t=6, n_prune=4)["prompt"] == -math.inf
    prune = {p["component"]: p for p in h.prune_set(t=5, n_prune=4)}
    assert set(prune) == {"prompt", "skill", "memory"}
    assert prune["prompt"]["accepted_edits_in_incumbent"][0]["hypothesis"] == "h1"
    assert h.incumbent_component_counts()["prompt"] == 1
    assert h.incumbent_component_counts(before_t=0)["prompt"] == 0
    assert novelty(["client_tool", "prompt"], h.incumbent_component_counts()) == 1
    assert novelty(["skill"], {"skill": 2}) == 0
    assert h.has(1, "B") and not h.has(5, "A")
    assert len(h.render(mode="accepted_only")) == 1 and h.render(mode="none") == []
    assert [r["outcome"] for r in h.render()][-1] == "critic_reject"
    traj = [0.50, 0.53, 0.53, 0.535, 0.60]
    assert stall_flag(traj, 3, 3, 0.02) == 0
    assert stall_flag(traj, 3, 2, 0.02) == 1
    assert stall_flag(traj, 4, 2, 0.02) == 0
    assert stall_flag(traj, 1, 3, 0.02) == 0
    e = exploration(3, 1, {"prompt"}, 1)
    assert e["sigma"] == 1 and "prompt" not in e["untried"] and "RESERVED" in e["text"]
    h.truncate_after(0)
    assert {r["t"] for r in h.records()} == {0}


def test_render_keeps_at_most_four_unmeasured(tmp_path):
    h = History(tmp_path / "h.jsonl", timestamps=False)
    for t in range(8):
        h.append_candidate(t, "A", [{"id": "C1", "component": "prompt", "hypothesis": f"x{t}"}], "critic_reject",
                           None, None, False, None, None, None)
    assert len(h.render()) == 4
    assert "ts" not in h.records()[0]


# --------------------------------------------------------------------- components
def test_component_normalization_reference_vectors():
    assert normalize("Skill", "", []) == "prompt"
    assert normalize("Skill", "+++ b/x/skills/y/SKILL.md", []) == "skill"
    assert normalize("bogus", "+++ b/harness/skills/x/SKILL.md", []) == "skill"
    assert normalize(None, "+++ b/harness/resum.py\n+ keep_last = 5", [("context_mgmt", [r"resum\.py"])]) == "context_mgmt"
    assert normalize(None, "nothing structural", []) == "prompt"


def test_taxonomy_from_domain_paths_and_retagging():
    class D:
        components = {"prompt": ["prompts/*"], "control_flow": ["harness.py"], "tool": ["tools/*"],
                      "memory": ["memory/*"]}
        structural_components = ("tool", "memory")
    tax = Taxonomy.from_domain(D())
    assert tax.K == ["prompt", "control_flow", "tool", "memory"] and tax.K_str == ["tool", "memory"]
    a = Artifact({"harness.py": "def solve():\n    return 1\n", "prompts/s.md": "hi\n"})
    b = a.with_files({"prompts/s.md": "hi\nThink step by step.\n"})
    d = a.diff(b)
    assert tax.normalize("memory", d) == "prompt"                 # mislabelled: no memory evidence -> re-tagged
    assert tax.normalize("client_tool", a.diff(a.with_files({"tools/calc.py": "x = 1\n"}))) == "tool"   # alias
    c = a.with_files({"harness.py": "def solve():\n    return 2\n", "memory/m.py": "M = {}\n"})
    assert tax.normalize("memory", a.diff(c)) == "memory"
    assert tax.classify(a.diff(c)) == "memory"                    # structural paths first
    assert tax.novelty(["tool", "prompt"], {}) == 1


def test_config_presets_and_roundtrip(tmp_path):
    p = tmp_path / "rrsi.json"
    p.write_text(json.dumps({"T": 7, "k": 3, "beta1": 12.5, "custom": "x"}))
    cfg = Config.load(p, T=None, k=5)
    assert cfg.T == 7 and cfg.k == 5 and cfg.beta1 == 12.5 and cfg.notes["custom"] == "x"
    for name, (delta, beta1, w_s) in {"coding": (0.017, 44.5, 0.0), "workspace": (0.004, 35.4, 1414.0),
                                      "eng": (0.020, 24.4, 244.0)}.items():
        c = Config.preset(name)
        assert (c.delta, c.beta1, c.w_s) == (delta, beta1, w_s)
    d = Config()
    assert (d.T, d.k, d.m, d.b_min, d.b_max, d.w, d.m_draft, d.delta, d.delta_z, d.beta0, d.beta1, d.w_s, d.w_c,
            d.w_n, d.n_prune, d.repair_rounds, d.invalid_missing_frac) == (20, 2, 2, 1, 4, 3, 1, None, 2.0, 0.10,
                                                                           40.0, 100.0, 15.0, 0.5, 4, 5, 0.15)
    assert Config.from_dict(d.dump()) == d and K[0] == "prompt"
