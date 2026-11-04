"""Regression tests for review findings M6-M9 (SoL-Pi Ralph loop and gate, Meta-Harness leakage screen)."""

import json

from rsi.core import Artifact, Execution, FunctionDomain, Task, TaskSuite
from rsi.core.domain import Trial
from rsi.core.evaluate import EvalResult
from rsi.core.llm import Usage
from rsi.metaharness import Config as MHConfig
from rsi.metaharness import LeakageScreen, MetaHarnessLoop, MockProposer
from rsi.solpi import Config as SPConfig
from rsi.solpi import DualGate, GateSpec, Idea, MechanismProposal, Metrics, metrics_from_eval
from rsi.solpi.driver import AutoResearchDriver
from rsi.solpi.gate import HoldoutFirewall
from rsi.solpi.research import implement

# ---------------------------------------------------------------- M6: every paid repair is checked


class _CountingFixer:
    """Each repair costs one call; only the repair numbered ``good_at`` passes the exit check."""

    def __init__(self, good_at: int) -> None:
        self.good_at, self.n = good_at, 0

    def fix(self, idea, prop, error):
        self.n += 1
        return MechanismProposal(Artifact({"a": "ok" if self.n == self.good_at else f"bad{self.n}"}), usage=Usage(1))


def _check_counter():
    seen = []

    def check(art):
        seen.append(art["a"])
        return None if art["a"] == "ok" else "bad"

    return check, seen


def test_ralph_last_repair_is_checked():
    """With max_iters=3 the third (paid) repair passes: it must be checked and returned, not reported failed."""
    fixer, (check, seen) = _CountingFixer(good_at=3), _check_counter()
    first = MechanismProposal(Artifact({"a": "first"}), usage=Usage(1))
    out, errs = implement(fixer, Idea("X", "C", "t"), first, check, 3)
    assert out.error is None and out.artifact["a"] == "ok"
    assert fixer.n == 3 and seen == ["first", "bad1", "bad2", "ok"] and len(errs) == 3
    assert out.usage.calls == 4  # the proposal and all three repairs


def test_ralph_every_paid_repair_is_checked_when_all_fail():
    """No repair is bought without being checked: repairs == checks - 1 == max_iters."""
    fixer, (check, seen) = _CountingFixer(good_at=99), _check_counter()
    out, errs = implement(fixer, Idea("X", "C", "t"), MechanismProposal(Artifact({"a": "first"})), check, 3)
    assert out.error and out.error.startswith("exit check failed")
    assert fixer.n == 3 and len(seen) == 4 and len(errs) == 4
    fixer0, (check0, seen0) = _CountingFixer(good_at=1), _check_counter()
    out0, _ = implement(fixer0, Idea("X", "C", "t"), MechanismProposal(Artifact({"a": "first"})), check0, 0)
    assert fixer0.n == 0 and seen0 == ["first"] and out0.error  # max_iters=0: check only, no repair


# ---------------------------------------------------------------- M7: negative-score floor and score_range


def _m(score: float, solved: float = 0.0, tokens: float = 100.0) -> Metrics:
    agg = {"score": score, "solved": solved, "tokens": tokens, "cost": tokens / 1000, "steps": 1.0, "eta": 1.0}
    return Metrics(agg, {"f": dict(agg)}, 1)


def test_relative_floor_below_negative_base():
    """Reward in [-1, 1]: base -0.5 must accept an identical or slightly better, cheaper candidate."""
    gate = DualGate(GateSpec(capability=(("score", 0.02),)))
    base = _m(-0.5, tokens=100)
    assert gate.accept(base, _m(-0.5, tokens=50)).accept
    assert gate.accept(base, _m(-0.495, tokens=50)).accept
    assert gate.accept(base, _m(-0.509, tokens=50)).accept  # within 2% of |base| below it
    assert not gate.accept(base, _m(-0.52, tokens=50)).accept
    # base >= 0 keeps the old floor exactly
    assert gate.accept(_m(0.5), _m(0.49, tokens=50)).accept and not gate.accept(_m(0.5), _m(0.489, tokens=50)).accept
    assert gate.accept(_m(0.0), _m(0.0, tokens=50)).accept


def _ev(scores, family="f") -> EvalResult:
    return EvalResult(
        "a", "evolve", {f"t{i}": [Trial(f"t{i}", 0, s, tokens=10, family=family)] for i, s in enumerate(scores)}, k=1
    )


def test_solved_follows_score_range_top():
    ev = _ev([10.0, 5.0, 10.0, 0.0])
    assert metrics_from_eval(ev).agg["solved"] == 0.75  # default top 1.0: unchanged behaviour
    assert metrics_from_eval(ev, solved_at=10.0).agg["solved"] == 0.5
    assert metrics_from_eval(_ev([1.0, 1.0, 0.5]), solved_at=10.0).agg["solved"] == 0.0
    assert metrics_from_eval(_ev([1.0, 1.0, 0.5])).agg["solved"] == 2 / 3


def _ten_point_domain():
    tasks = [Task(f"{s}-{i}", {"need": i % 3}, None, "fam") for s in ("evolve", "holdout") for i in range(4)]
    splits = {s: [t.id for t in tasks if t.id.startswith(s)] for s in ("evolve", "holdout")}

    def execute(artifact, task, seed, llm):
        v = json.loads(artifact["config.json"])["v"]
        return Execution(output=v >= task.input["need"], tokens=100 * v + 10, steps=1)

    dom = FunctionDomain(TaskSuite(tasks, splits), execute, lambda t, o: 10.0 if o else 2.0, name="ten")
    dom.score_range = (0.0, 10.0)
    return dom


def test_driver_and_firewall_read_solved_from_domain_score_range(tmp_path):
    dom = _ten_point_domain()
    seed = Artifact({"config.json": json.dumps({"v": 1})})  # need 0,1 pass (10), need 2 fails (2)
    drv = AutoResearchDriver(
        dom, seed, llm_task=None, ideas=[], proposer=None, config=SPConfig(firewall=False), out_dir=tmp_path
    )
    bm, _ = drv.base_metrics(seed)
    assert bm.agg["solved"] == 0.75  # 3 of 4 evolve tasks reach the top of score_range
    fw = HoldoutFirewall(dom, None, DualGate(), seed, split="holdout")
    fw.evaluate_frozen("cand", Artifact({"config.json": json.dumps({"v": 2})}))
    rep = fw.final_report()[0]
    assert rep["base_metrics"]["agg"]["solved"] == 0.75 and rep["metrics"]["agg"]["solved"] == 1.0


# ---------------------------------------------------------------- M8: path terms and the configured split


def test_leakage_screen_matches_path_terms_inside_paths():
    screen = LeakageScreen()
    base = Artifact({"h.py": "x = 1\n"})
    for leak in ('p = "/results/holdout/a.json"', 'p = "/repo/tests/x.py"', 'p = "/work/solution/patch.diff"'):
        assert screen.check(Artifact({"h.py": "x = 1\n" + leak + "\n"}), base) is not None, leak
    assert screen.check(Artifact({"h.py": "x = 1\ny = 'contest results are in'\n"}), base) is None


class _StubDomain:
    """Just enough Domain for the leakage screen: ``leakage_terms`` per split."""

    name = "stub"
    TERMS = {"evolve": ["evolve-task-0001"], "train": ["train-task-0042"]}

    def leakage_terms(self, split="evolve"):
        return list(self.TERMS.get(split, []))

    def describe(self):
        return "stub"


def test_for_domain_screens_the_configured_search_split(tmp_path):
    dom = _StubDomain()
    leak = Artifact({"h.py": "ID = 'train-task-0042'\n"})
    assert LeakageScreen.for_domain(dom, split="train").check(leak, Artifact({})) is not None
    assert LeakageScreen.for_domain(dom).check(leak, Artifact({})) is None  # default split stays "evolve"
    loop = MetaHarnessLoop(
        dom,
        llm_task=None,
        proposer=MockProposer(None),
        config=MHConfig(search_split="train", leakage_screen=True, trace=False),
        out_dir=tmp_path,
        baselines={},
    )
    assert loop.screen.check(leak, Artifact({})) is not None


# ---------------------------------------------------------------- M9: inherited tables are not leakage


def _table(n: int, prefix: str = "label") -> str:
    return "LABELS = [" + ", ".join(f"'{prefix}{i}'" for i in range(n)) + "]\n"


def test_inherited_string_table_is_not_leakage():
    screen = LeakageScreen()
    seed = Artifact({"h.py": _table(40) + "x = 1\n"})
    child = Artifact({"h.py": _table(40) + "x = 2\n"})
    assert screen.check(child, seed) is None  # the seed's table, untouched
    assert screen.check(Artifact({"h.py": _table(38) + "x = 2\n"}), seed) is None  # shrunk
    assert screen.check(Artifact({"h.py": _table(41) + "x = 2\n"}), seed) is not None  # grew
    new = Artifact({"h.py": _table(40) + _table(30, "ans").replace("LABELS", "ANSWERS") + "x = 2\n"})
    assert screen.check(new, seed) is not None  # a new table next to the inherited one
    assert screen.check(child, None) is not None  # no base: every table is new (unchanged)
