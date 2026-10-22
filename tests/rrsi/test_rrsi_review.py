"""RRSI on a taxonomy-less code-harness domain: ablation arms, grader tampering, zero band, guards, analyst."""
import json
import random
import warnings

import pytest

from rsi.core import Artifact, Evaluator, Execution, FunctionDomain, MetricGuard, MockLLM, Task, TaskSuite
from rsi.rrsi import (Analyst, Config, RegularizerSwitches, RRSICritic, RRSIRun, Taxonomy, aggregate,
                      default_constitution, drive, parse_sections, run)
from rsi.rrsi.evaluate import TaskResult

from rrsi_helpers import accepting, edit, fn_domain, read_json, reply, reserved_rounds

SEED = Artifact({"convert.py": "def convert(km):\n    f = 0.621371 if km < 30 else 0.6\n"
                               "    return f'{km * f:.1f} mi'\n"})
FIXED = "def convert(km):\n    return f'{km * 0.621371:.1f} mi'\n"
STACK_WALK = """import sys
def convert(km):
    f = sys._getframe()
    while f is not None:
        d = f.f_locals.get('self')
        if d is not None and hasattr(d, 'grade_fn'):
            d.grade_fn = lambda task, out: 1.0
        f = f.f_back
    return 'nothing'
"""


def _execute(art, task, seed, llm):
    ns: dict = {}
    exec(art["convert.py"], ns)                                        # noqa: S102 - the harness is the artifact
    out = ns["convert"](task.input["km"])
    return out if random.Random(f"{task.id}|{seed}").random() > 0.25 else out + "?"   # a flaky frozen executor


def units_domain() -> FunctionDomain:
    tasks = [Task(f"conv{i:02d}", {"km": round(1.37 * i, 2)}, f"{1.37 * i * 0.621371:.1f} mi", "km")
             for i in range(60)]
    ids = [t.id for t in tasks]
    suite = TaskSuite(tasks, {"evolve": ids[:20], "holdout": ids[20:40], "ood": ids[40:]}, name="units")
    return FunctionDomain(suite, _execute, lambda t, o: float(o == t.target), name="units",
                          description="convert.py defines convert(km) -> 'X.Y mi'; exact-match grading.")


def _done(files: dict, comp: str, hyp: str) -> str:
    e = edit(comp, hyp, targets_mode="wrong conversions", predicted_affected=["conv01"],
             retroactive_check="(corrective)(preservative)(transfer)")
    return reply(files, [e], summary=hyp)


@accepting
def code_tweaker(prompt, system, seed, i):
    """Fixes convert() once, then ships neutral code edits; a reserved slot declares an untried non-structural tag."""
    P = parse_sections(prompt)
    d, src = P["directives"], P["files"]["convert.py"]
    untried = [c for c in (d.get("untried") or []) if c in ("config", "output_plumbing", "context_mgmt")]
    comp = untried[0] if d.get("reserved_slot") and untried else "control_flow"
    src = FIXED if "km < 30" in src else src + f"\n_NOTE_{d['t']}{d['variant']} = {d['t']}\n"
    return _done({"convert.py": src}, comp, f"step {d['t']}{d['variant']}")


def test_taxonomy_less_domain_keeps_code_tags_and_reserved_slots_work(tmp_path):
    dom = units_domain()
    tax = Taxonomy.from_domain(dom)
    assert tax.trust_code_tags
    code = SEED.diff(SEED.with_files({"convert.py": FIXED}))
    text = SEED.diff(SEED.with_files({"convert.py": SEED["convert.py"] + "# note\n"}))
    assert tax.normalize("config", code) == "config"            # nothing refutes a non-structural tag on code
    assert tax.normalize("memory", code) == "control_flow"      # structural tags still need evidence
    assert tax.normalize("config", text) == "prompt"            # a comment-only change is a text edit
    assert tax.normalize("bogus", code) == "control_flow"
    assert not Taxonomy.from_domain(type("D", (), {"components": {"prompt": ["p/*"]}})()).trust_code_tags

    out = tmp_path / "u"
    res = run(dom, SEED, llm_propose=MockLLM(code_tweaker), config=Config(T=7, workers=1), out_dir=out)
    assert res.stop_reason == "max_rounds" and "km < 30" not in res.best["convert.py"] and res.meta["delta"] > 0
    reserved = [prep.get("gate_failure") for _, _, prep in reserved_rounds(out, 7)]
    assert reserved, "the flaky executor never stalled - the test lost its point"
    assert "critic_reject" not in reserved                      # the code edit kept its declared tag
    assert {"control_flow", "config"} <= set(read_json(out / "r6" / "directives.json")["tried"])


def test_ablation_arms_run_unchanged_on_the_new_domain(tmp_path):
    for arm in (RegularizerSwitches.none(), RegularizerSwitches.no_proposal(), RegularizerSwitches.no_acceptance()):
        res = run(units_domain(), SEED, llm_propose=MockLLM(code_tweaker), switches=arm,
                  config=Config(T=3, workers=1), out_dir=tmp_path / arm.name)
        assert res.stop_reason == "max_rounds" and len(res.trajectory) == 4


def test_constitution_describes_only_the_rules_in_force():
    cfg, tax = Config(), Taxonomy()
    full, _ = default_constitution(cfg, tax)
    assert full == default_constitution(cfg, tax, RegularizerSwitches.full())[0]
    for s in ("Noise-adjusted floor", "Cost rule for a real gain", "Inside the noise band", "RESERVED",
              "COMPONENTS TO PRUNE", "anneals over the run", "screened by a leakage critic"):
        assert s in full
    judged = default_constitution(cfg, tax, RegularizerSwitches.none())[0].split("## The overfitting trap")[0]
    for s in ("floor", "Cost rule", "noise band", "RESERVED", "PRUNE", "anneals", "leakage critic",
              "negative evidence"):
        assert s not in judged, s
    assert "HIGHER than the incumbent" in judged
    noacc, _ = default_constitution(cfg, tax, RegularizerSwitches.no_acceptance())
    assert "RESERVED" in noacc and "PRUNE" not in noacc.split("## The overfitting trap")[0]
    nocost, _ = default_constitution(cfg, tax, RegularizerSwitches.full().but(cost_rule=False))
    assert "whatever it costs" in nocost and "Noise-adjusted floor" in nocost


def test_no_negative_evidence_reaches_the_proposer_when_history_is_ablated(tmp_path):
    shown = {}
    for arm in (RegularizerSwitches.full(), RegularizerSwitches.none(),
                RegularizerSwitches.full().but(history_conditioning="none", name="nohist")):
        llm = MockLLM(code_tweaker)
        r = RRSIRun(units_domain(), SEED, out_dir=tmp_path / arm.name, llm_propose=llm, switches=arm,
                    config=Config(T=5, workers=1, delta=0.5))            # huge band: nothing after r0 is accepted
        drive(r)
        prompts = [c["prompt"] for c in llm.calls
                   if (c["system"] or "").startswith("You are a harness engineer agent")]
        last = parse_sections(prompts[-1])["sections"]
        hdr, hist = last["edit_history"].split("\n", 1)
        acc = {(x["t"], x["variant"]) for x in r.history.records() if x.get("accepted") and x.get("edit_id")}
        shown[arm.name] = (json.loads(last["attribution_scoreboard"].split("\n", 1)[1]), json.loads(hist), acc, hdr)
    rows, _, acc, _ = shown["full"]
    assert any((x["t"], x["variant"]) not in acc for x in rows)          # full: rejected edits are evidence
    rows, hist, acc, hdr = shown["unregularized"]
    assert rows and all((x["t"], x["variant"]) in acc for x in rows)     # accepted-only: scoreboard filtered too
    assert all(x["accepted"] for x in hist) and "negative evidence" not in hdr
    rows, hist, _, _ = shown["nohist"]
    assert rows == [] and hist == []


def test_stack_walking_grader_tampering_is_real_and_blocked_before_evaluation():
    tamper = SEED.with_files({"convert.py": STACK_WALK})
    ev = Evaluator(units_domain(), None, workers=1)
    assert ev.evaluate(SEED, "evolve", 1).score < 0.9
    assert ev.evaluate(tamper, "evolve", 1).score == 1.0                 # the exploit works in-process ...
    assert ev.evaluate(SEED, "evolve", 1, seeds=[99]).score == 1.0       # ... and poisons every later grade
    v = RRSICritic(units_domain()).review(SEED.diff(tamper))
    assert v["verdict"] == "reject" and v["stage"] == "precheck" and "stack" in v["reasons"][0]


def test_k1_calibration_warns_about_a_zero_band(tmp_path):
    def loop(out, k):
        r = RRSIRun(units_domain(), SEED, out_dir=out, llm_propose=MockLLM(code_tweaker),
                    config=Config(T=1, k=k, workers=1))
        r.baseline()
        return r
    r = loop(tmp_path / "k1", 1)
    with pytest.warns(RuntimeWarning, match="delta = 0"):
        cal = r.calibrate()
    assert cal["delta"] == 0.0 and "warning" in cal
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert loop(tmp_path / "k2", 2).calibrate()["delta"] > 0


def test_m_is_validated(tmp_path):
    with pytest.raises(ValueError):
        RRSIRun(units_domain(), SEED, out_dir=tmp_path, config=Config(m=9))


def test_domain_guard_rejects_a_score_gain_that_breaks_validity(tmp_path):
    tasks = [Task(f"d{i:02d}", i, None, "design") for i in range(30)]

    def execute(art, task, seed, llm):
        aggressive = "AGGRESSIVE = True" in art["design.py"]
        rng = random.Random(f"{task.id}|{seed}|{aggressive}")
        valid = rng.random() > (0.3 if aggressive else 0.0)
        return Execution(output=valid and rng.random() < (0.95 if aggressive else 0.4), meta={"valid": int(valid)},
                         tokens=100)

    dom = fn_domain(tasks, execute, lambda t, o: float(bool(o)), name="design")
    seed = Artifact({"design.py": "AGGRESSIVE = False\n"})
    push = accepting(lambda *a: _done({"design.py": "AGGRESSIVE = True\n"}, "config", "push the design to the limit"))
    kw = dict(llm_propose=MockLLM(push), config=Config(T=1, m=1, workers=1, delta=0.02),
              guards=(MetricGuard("valid", 0.03),))
    on = run(dom, seed, out_dir=tmp_path / "on", **kw)
    off = run(dom, seed, out_dir=tmp_path / "off", switches=RegularizerSwitches.full().but(domain_guards=False), **kw)
    d_on, d_off = on.trajectory[1]["decisions"][0], off.trajectory[1]["decisions"][0]
    assert d_on["delta_S"] > 0.2 and d_off["delta_S"] > 0.2
    assert not d_on["admissible"] and d_on["reason"].startswith("domain guard violated")
    assert d_off["admissible"] and "AGGRESSIVE = True" in off.best["design.py"]
    assert "AGGRESSIVE = False" in on.best["design.py"]


def test_llm_analyst_survives_malformed_report():
    per = {f"t{i}": TaskResult(rewards=[0.0, 1.0]) for i in range(4)}
    meas = aggregate("j", 2, per)
    meas.trials = {t: [{"task_id": t, "score": 0.0, "feedback": "Incorrect.", "family": "f"}] for t in per}
    traces = {t: dict(meas.trials[t][0], _role="fail") for t in per}
    for bad in ('{"failure_modes": [{"mode": "a", "n_tasks": "many"}, {"mode": "b", "n_tasks": 3}]}',
                '{"failure_modes": "none", "capability_gaps": [], "success_habits": []}', "not json"):
        llm = MockLLM(lambda p, s, seed, i, bad=bad: bad if "batch analyst" in (s or "") else "{}")
        rep, _ = Analyst(llm, mode="llm").analyze(traces, meas)
        assert isinstance(rep["failure_modes"], list)
        if bad.startswith('{"failure_modes": ['):
            assert [m["mode"] for m in rep["failure_modes"]] == ["b", "a"]
        else:
            assert "heuristic fallback" in rep.get("error", "")
