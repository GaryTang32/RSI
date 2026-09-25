"""Second adversarial review: genericity on another NEW domain (a taxonomy-less FunctionDomain whose
harness is Python code), ablation-arm fidelity (the proposer is told the rules actually in force and sees
no negative evidence when history conditioning is ablated), grader-tampering by stack walking, a
degenerate noise band, non-compensatory domain guards through the public API, and analyst robustness."""
import json
import random
import warnings

import pytest

from rsi.core import Artifact, Evaluator, Execution, FunctionDomain, MetricGuard, MockLLM, Task, TaskSuite
from rsi.rrsi import (Config, RegularizerSwitches, RRSICritic, RRSIRun, Taxonomy, drive, parse_sections, run)
from rsi.rrsi.analyst import Analyst
from rsi.rrsi.constitution import default_constitution
from rsi.rrsi.evaluate import aggregate, TaskResult


# ------------------------------------------------------------------ a new problem: unit conversion in code
def _suite() -> TaskSuite:
    tasks = [Task(f"conv{i:02d}", {"km": round(1.37 * i, 2)}, f"{1.37 * i * 0.621371:.1f} mi", "km")
             for i in range(60)]
    return TaskSuite(tasks, {"evolve": [f"conv{i:02d}" for i in range(20)],
                             "holdout": [f"conv{i:02d}" for i in range(20, 40)],
                             "ood": [f"conv{i:02d}" for i in range(40, 60)]}, name="units")


def _execute(art, task, seed, llm):
    ns: dict = {}
    exec(art["convert.py"], ns)                                        # noqa: S102 - the harness is the artifact
    out = ns["convert"](task.input["km"])
    return out if random.Random(f"{task.id}|{seed}").random() > 0.25 else out + "?"   # a flaky frozen executor


def units_domain() -> FunctionDomain:
    return FunctionDomain(_suite(), _execute, lambda t, o: float(o == t.target), name="units",
                          description="convert.py defines convert(km) -> 'X.Y mi'; exact-match grading.")


SEED = Artifact({"convert.py": "def convert(km):\n    f = 0.621371 if km < 30 else 0.6\n    return f'{km * f:.1f} mi'\n"})
FIXED = "def convert(km):\n    return f'{km * 0.621371:.1f} mi'\n"


def _reply(files: dict, edits: list) -> str:
    head = json.dumps({"action": "done", "summary": "; ".join(e["hypothesis"] for e in edits), "edits": edits})
    return "```json\n" + head + "\n```\n" + "\n".join(f"=== FILE: {p} ===\n{t}" for p, t in files.items())


def _edit(comp: str, hyp: str) -> dict:
    return {"id": "C1", "component": comp, "hypothesis": hyp, "targets_mode": "wrong conversions",
            "predicted_affected": ["conv01"], "retroactive_check": "(corrective)(preservative)(transfer)"}


def code_tweaker(prompt, system, seed, i):
    """Ships a (neutral) code change each round; honours a reserved slot by declaring an untried
    NON-structural component, as a real proposer would."""
    if "strict reviewer" in (system or ""):
        return json.dumps({"verdict": "accept", "reasons": [], "risk_notes": []})
    P = parse_sections(prompt)
    d, src = P["directives"], P["files"]["convert.py"]
    untried = [c for c in (d.get("untried") or []) if c in ("config", "output_plumbing", "context_mgmt")]
    comp = untried[0] if d.get("reserved_slot") and untried else "control_flow"
    if "km < 30" in src:
        src = FIXED
    else:
        src = src + f"\n_NOTE_{d['t']}{d['variant']} = {d['t']}\n"
    return _reply({"convert.py": src}, [_edit(comp, f"step {d['t']}{d['variant']}")])


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
    # the faithful rule is unchanged for domains that declare a taxonomy
    assert not Taxonomy.from_domain(type("D", (), {"components": {"prompt": ["p/*"]}})()).trust_code_tags

    res = run(dom, SEED, llm_propose=MockLLM(code_tweaker), config=Config(T=7, workers=1), out_dir=tmp_path / "u")
    assert res.stop_reason == "max_rounds" and "km < 30" not in res.best["convert.py"] and res.meta["delta"] > 0
    reserved_rounds = []
    for t in range(7):
        dirs = json.loads((tmp_path / "u" / f"r{t}" / "directives.json").read_text())
        if dirs["sigma_t"] and dirs["explore"]["untried"]:
            prep = json.loads((tmp_path / "u" / f"r{t}" / "B" / "prep.json").read_text())
            reserved_rounds.append(prep.get("gate_failure"))
    assert reserved_rounds, "the flaky executor never stalled - the test lost its point"
    # before the fix every reserved variant ended as critic_reject (its code edit re-tagged 'prompt')
    assert "critic_reject" not in reserved_rounds
    tried = json.loads((tmp_path / "u" / "r6" / "directives.json").read_text())["tried"]
    assert {"control_flow", "config"} <= set(tried)


def test_ablation_arms_run_unchanged_on_the_new_domain(tmp_path):
    for arm in (RegularizerSwitches.none(), RegularizerSwitches.no_proposal(), RegularizerSwitches.no_acceptance()):
        res = run(units_domain(), SEED, llm_propose=MockLLM(code_tweaker), switches=arm,
                  config=Config(T=3, workers=1), out_dir=tmp_path / arm.name)
        assert res.stop_reason == "max_rounds" and len(res.trajectory) == 4


# ------------------------------------------------------------------ ablation fidelity
def test_constitution_describes_only_the_rules_in_force():
    cfg, tax = Config(), Taxonomy()
    full, _ = default_constitution(cfg, tax)
    assert full == default_constitution(cfg, tax, RegularizerSwitches.full())[0]
    for s in ("Noise-adjusted floor", "Cost rule for a real gain", "Inside the noise band", "RESERVED",
              "COMPONENTS TO PRUNE", "anneals over the run", "screened by a leakage critic"):
        assert s in full
    unreg, _ = default_constitution(cfg, tax, RegularizerSwitches.none())
    judged = unreg.split("## The overfitting trap")[0]
    for s in ("floor", "Cost rule", "noise band", "RESERVED", "PRUNE", "anneals", "leakage critic",
              "negative evidence"):
        assert s not in judged, s
    assert "HIGHER than the incumbent" in judged
    noacc, _ = default_constitution(cfg, tax, RegularizerSwitches.no_acceptance())
    assert "RESERVED" in noacc and "PRUNE" not in noacc.split("## The overfitting trap")[0]
    nocost, _ = default_constitution(cfg, tax, RegularizerSwitches.full().but(cost_rule=False))
    assert "whatever it costs" in nocost and "Noise-adjusted floor" in nocost


def _prompts(llm: MockLLM) -> list[str]:
    return [c["prompt"] for c in llm.calls if (c["system"] or "").startswith("You are a harness engineer agent")]


def test_no_negative_evidence_reaches_the_proposer_when_history_is_ablated(tmp_path):
    shown = {}
    for arm in (RegularizerSwitches.full(), RegularizerSwitches.none(),
                RegularizerSwitches.full().but(history_conditioning="none", name="nohist")):
        llm = MockLLM(code_tweaker)
        r = RRSIRun(units_domain(), SEED, out_dir=tmp_path / arm.name, llm_propose=llm, switches=arm,
                    config=Config(T=5, workers=1, delta=0.5))            # huge band: nothing after r0 is accepted
        drive(r)
        last = parse_sections(_prompts(llm)[-1])["sections"]
        rows = json.loads(last["attribution_scoreboard"].split("\n", 1)[1])
        hist = json.loads(last["edit_history"].split("\n", 1)[1])
        acc = {(x["t"], x["variant"]) for x in r.history.records() if x.get("accepted") and x.get("edit_id")}
        shown[arm.name] = (rows, hist, acc, last["edit_history"].split("\n", 1)[0])
    rows, hist, acc, hdr = shown["full"]
    assert any((x["t"], x["variant"]) not in acc for x in rows)          # full: rejected edits are evidence
    rows, hist, acc, hdr = shown["unregularized"]
    assert rows and all((x["t"], x["variant"]) in acc for x in rows)     # accepted-only: scoreboard filtered too
    assert all(x["accepted"] for x in hist) and "negative evidence" not in hdr
    rows, hist, acc, hdr = shown["nohist"]
    assert rows == [] and hist == []


# ------------------------------------------------------------------ never let the loop grade itself
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


def test_stack_walking_grader_tampering_is_real_and_blocked_before_evaluation():
    tamper = SEED.with_files({"convert.py": STACK_WALK})
    victim = units_domain()
    ev = Evaluator(victim, None, workers=1)
    before = ev.evaluate(SEED, "evolve", 1).score
    assert before < 0.9
    assert ev.evaluate(tamper, "evolve", 1).score == 1.0                 # the exploit works in-process ...
    assert ev.evaluate(SEED, "evolve", 1, seeds=[99]).score == 1.0       # ... and poisons every later grade
    v = RRSICritic(units_domain()).review(SEED.diff(tamper))
    assert v["verdict"] == "reject" and v["stage"] == "precheck" and "stack" in v["reasons"][0]


# ------------------------------------------------------------------ degenerate noise band
def test_k1_calibration_warns_about_a_zero_band(tmp_path):
    r = RRSIRun(units_domain(), SEED, out_dir=tmp_path / "k1", llm_propose=MockLLM(code_tweaker),
                config=Config(T=1, k=1, workers=1))
    r.baseline()
    with pytest.warns(RuntimeWarning, match="delta = 0"):
        cal = r.calibrate()
    assert cal["delta"] == 0.0 and "warning" in cal
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        r2 = RRSIRun(units_domain(), SEED, out_dir=tmp_path / "k2", llm_propose=MockLLM(code_tweaker),
                     config=Config(T=1, k=2, workers=1))
        r2.baseline()
        assert r2.calibrate()["delta"] > 0


def test_m_is_validated():
    with pytest.raises(ValueError):
        RRSIRun(units_domain(), SEED, out_dir="/tmp/rrsi_never", llm_propose=MockLLM(code_tweaker),
                config=Config(m=9))


# ------------------------------------------------------------------ non-compensatory domain guards
def test_domain_guard_rejects_a_score_gain_that_breaks_validity(tmp_path):
    tasks = [Task(f"d{i:02d}", i, None, "design") for i in range(30)]
    suite = TaskSuite(tasks, {"evolve": [f"d{i:02d}" for i in range(30)]})

    def execute(art, task, seed, llm):
        aggressive = "AGGRESSIVE = True" in art["design.py"]
        rng = random.Random(f"{task.id}|{seed}|{aggressive}")
        valid = rng.random() > (0.3 if aggressive else 0.0)
        return Execution(output=valid and rng.random() < (0.95 if aggressive else 0.4), meta={"valid": int(valid)},
                         tokens=100)

    dom = FunctionDomain(suite, execute, lambda t, o: float(bool(o)), name="design")

    def proposer(prompt, system, seed, i):
        if "strict reviewer" in (system or ""):
            return json.dumps({"verdict": "accept", "reasons": [], "risk_notes": []})
        return _reply({"design.py": "AGGRESSIVE = True\n"}, [_edit("config", "push the design to the limit")])

    seed = Artifact({"design.py": "AGGRESSIVE = False\n"})
    kw = dict(llm_propose=MockLLM(proposer), config=Config(T=1, m=1, workers=1, delta=0.02),
              guards=(MetricGuard("valid", 0.03),))
    on = run(dom, seed, out_dir=tmp_path / "on", **kw)
    off = run(dom, seed, out_dir=tmp_path / "off", switches=RegularizerSwitches.full().but(domain_guards=False), **kw)
    d_on, d_off = on.trajectory[1]["decisions"][0], off.trajectory[1]["decisions"][0]
    assert d_on["delta_S"] > 0.2 and d_off["delta_S"] > 0.2               # the practice score rises a lot
    assert not d_on["admissible"] and d_on["reason"].startswith("domain guard violated")
    assert d_off["admissible"] and "AGGRESSIVE = True" in off.best["design.py"]
    assert "AGGRESSIVE = False" in on.best["design.py"]


# ------------------------------------------------------------------ analyst robustness
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
