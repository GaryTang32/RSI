"""Genericity audit: the shared core runs a complete propose -> run -> score -> keep
loop on NEW problems without any change to rsi.core.

``mini_loop`` below uses only the public API (RewriteEditor + MockLLM proposer,
LeakageCritic, Evaluator, noise_from_trials, RRSIGate + select, Ledger, Budget,
UsageMeter roles, ImprovementResult, transfer_report). It is applied unchanged to
two domains defined here and nowhere else:

* ``curve``   - FunctionDomain; the artifact is a JSON file of polynomial
  coefficients, scores are noisy (seeded) closeness to a hidden curve;
* ``sorter``  - a Domain subclass whose artifact is a Python program run in the
  sandbox (``call_function``); the score is ``-loss`` in ``score_range=(-5, 0)``,
  so a crash scores -5 instead of a (best-possible) 0.
"""
from __future__ import annotations

import json
import random
import re

import pytest

from rsi.core import (Artifact, Budget, Domain, Evaluator, Execution, FunctionDomain, GateContext, ImprovementResult,
                      LeakageCritic, Ledger, MockLLM, Node, RewriteEditor, RRSIGate, Scored, SealedSplitError, Task,
                      TaskSuite, call_function, noise_from_trials, select, transfer_report)


# ------------------------------------------------------------------ a method written only against rsi.core
def mini_loop(domain: Domain, seed_artifact: Artifact, *, llm_propose, llm_task=None, rounds: int = 4, k: int = 3,
              out_dir=None) -> ImprovementResult:
    ev = Evaluator(domain, llm_task, workers=2)
    ledger = Ledger(None if out_dir is None else f"{out_dir}/ledger.jsonl")
    editor = RewriteEditor(llm_propose)
    critic = LeakageCritic(domain.leakage_terms("evolve"))
    budget = Budget(max_rounds=rounds)
    inc, inc_ev = seed_artifact, ev.evaluate(seed_artifact, "evolve", k)
    delta = noise_from_trials(inc_ev.trial_matrix()).delta
    best_score = inc_ev.score
    node = ledger.add(Node("n0", None, 0, "baseline", "keep", inc_ev.score, inc_ev.cost, "seed", inc.id))
    traj, t = [{"round": 0, "S": inc_ev.score}], 0
    while not budget.exhausted(rounds=t):
        t += 1
        prop = editor.edit(inc, f"Improve this artifact.\n{domain.describe()}\nLedger:\n{ledger.render()}",
                           seed=t, role="proposer")
        if not prop.ok:
            ledger.add(Node(f"n{t}", node.id, t, status="crash", change=prop.error or ""))
            continue
        verdict = critic.screen(inc.diff(prop.artifact), prop.change)
        if not verdict.accept:
            ledger.add(Node(f"n{t}", node.id, t, status="rejected", change=prop.change, meta={"hits": verdict.hits}))
            continue
        cand_ev = ev.evaluate(prop.artifact, "evolve", k)
        win, verdicts = select([("cand", Scored.from_eval(cand_ev))], Scored.from_eval(inc_ev), RRSIGate(),
                               GateContext(best_score=best_score, delta=delta, round=t))
        keep = win is not None
        n = ledger.add(Node(f"n{t}", node.id, t, status="keep" if keep else "discard", score=cand_ev.score,
                            cost=cand_ev.cost, change=prop.change, artifact_id=prop.artifact.id,
                            diff=inc.diff(prop.artifact), meta={"reason": verdicts[0][1].reason}))
        if keep:
            inc, inc_ev, node = prop.artifact, cand_ev, n
            best_score = max(best_score, cand_ev.score)
        traj.append({"round": t, "S": inc_ev.score})
    usage = llm_propose.meter.snapshot()
    return ImprovementResult("mini", seed_artifact, inc, ledger, traj, usage, "max_rounds", out_dir,
                             meta={"delta": delta})


# ------------------------------------------------------------------ problem 1: FunctionDomain, noisy curve fit
TRUE = [0.5, -1.0, 2.0]


def _curve_domain() -> FunctionDomain:
    tasks = [Task(f"pt{i:03d}", i / 10, sum(c * (i / 10) ** j for j, c in enumerate(TRUE)), "curve") for i in range(30)]
    suite = TaskSuite.from_tasks(tasks, fractions={"evolve": 0.5, "holdout": 0.5}, seed=1)

    def execute(art, task, seed, llm):
        coeffs = json.loads(art["model.json"])["coeffs"]
        noise = random.Random(f"{task.id}|{seed}").gauss(0, 0.05)
        return sum(c * task.input ** j for j, c in enumerate(coeffs)) + noise

    return FunctionDomain(suite, execute, lambda t, out: (1 / (1 + abs(out - t.target)), f"err={out - t.target:+.3f}"),
                          name="curve", description="model.json holds polynomial coefficients; closer is better")


def _curve_proposer(leak_id: str) -> MockLLM:
    """Moves each coefficient halfway to the truth; round 2 tries to leak an evolve task id."""
    def respond(prompt, system, seed, i):
        cur = json.loads(re.search(r"=== FILE: model\.json ===\n(.*?)\n", prompt).group(1))["coeffs"]
        new = [round(c + (t - c) / 2, 4) for c, t in zip(cur, TRUE)]
        extra = {"note": f"special-case {leak_id}"} if seed == 2 else {}
        head = json.dumps({"change": "halve the error of each coefficient", "hypothesis": "general",
                           "components": ["model"]})
        return f"```json\n{head}\n```\n=== FILE: model.json ===\n{json.dumps({'coeffs': new, **extra})}\n"
    return MockLLM(respond, name="curve-proposer")


# ------------------------------------------------------------------ problem 2: sandboxed program, negative loss
class SorterDomain(Domain):
    name = "sorter"
    score_range = (-5.0, 0.0)

    def execute(self, artifact, task, *, seed, llm):
        out, rr = call_function(artifact["solver.py"], "solve", {"xs": task.input}, timeout_s=10)
        if out is None:
            return Execution(error=rr.tail(3) or "no result")
        return Execution(output=out, steps=1)

    def grade(self, task, execution):
        loss = sum(a != b for a, b in zip(execution.output, task.target)) + abs(len(execution.output) - len(task.target))
        return -min(5.0, float(loss)), f"{loss} positions wrong"


def _sorter_domain() -> SorterDomain:
    rng = random.Random(3)
    tasks = []
    for i in range(10):
        xs = [rng.randint(0, 99) for _ in range(5)]
        tasks.append(Task(f"s{i}", xs, sorted(xs), "sort"))
    return SorterDomain(TaskSuite(tasks, {"evolve": [f"s{i}" for i in range(5)],
                                          "holdout": [f"s{i}" for i in range(5, 10)]}))


SORTERS = [
    "def solve(xs):\n    raise RuntimeError('not yet')\n",           # a crash must score -5, not 0
    "def solve(xs):\n    return sorted(xs)\n",
    "def solve(xs):\n    return sorted(xs, reverse=True)\n",
]


def _sorter_proposer() -> MockLLM:
    def respond(prompt, system, seed, i):
        code = SORTERS[i % len(SORTERS)]
        return f'{{"change": "solver v{i}", "components": ["control_flow"]}}\n=== FILE: solver.py ===\n{code}'
    return MockLLM(respond, name="sorter-proposer")


# ------------------------------------------------------------------ the audit
@pytest.mark.parametrize("make", ["curve", "sorter"])
def test_core_loop_runs_unchanged_on_new_domains(make, tmp_path):
    if make == "curve":
        dom = _curve_domain()
        leak = dom.tasks.splits["evolve"][0]
        seed, prop = Artifact({"model.json": json.dumps({"coeffs": [0.0, 0.0, 0.0]})}), _curve_proposer(leak)
    else:
        dom, seed, prop = _sorter_domain(), Artifact({"solver.py": "def solve(xs):\n    return list(xs)\n"}), \
            _sorter_proposer()
    res = mini_loop(dom, seed, llm_propose=prop, rounds=4, out_dir=str(tmp_path))
    assert isinstance(res, ImprovementResult) and len(res.trajectory) >= 2
    statuses = [n.status for n in res.ledger.nodes()]
    assert statuses[0] == "keep" and "keep" in statuses[1:]
    assert res.best != res.baseline
    # lineage from the final incumbent back to the seed
    best_node = [n for n in res.ledger.nodes() if n.artifact_id == res.best.id][-1]
    assert [n.id for n in res.ledger.lineage(best_node.id)][0] == "n0"
    assert res.usage["proposer"]["calls"] == 4 and res.usage["_total"]["input_tokens"] > 0
    # the loop never touched a sealed split; the report step unseals explicitly
    with pytest.raises(SealedSplitError):
        dom.tasks.split("holdout")
    rep = transfer_report(dom, None, {"seed": res.baseline, "best": res.best}, splits=("evolve", "holdout"), k=2)
    assert rep["splits"]["holdout"]["best"]["S"] > rep["splits"]["holdout"]["seed"]["S"]
    assert rep["splits"]["holdout"]["best"]["vs_reference"]["mean_diff"] > 0
    res.save(tmp_path)
    assert (tmp_path / "best_artifact").is_dir() and len(Ledger(tmp_path / "ledger.jsonl")) == len(res.ledger)
    if make == "curve":
        assert "rejected" in statuses                                   # the leaked task id was caught
        assert [h for n in res.ledger.nodes(status="rejected") for h in n.meta["hits"]] == [leak]
    else:
        crash = [n for n in res.ledger.nodes() if n.round == 1][0]
        assert crash.status == "discard" and crash.score == -5.0          # failure = bottom of score_range
        assert res.best["solver.py"] == SORTERS[1]
