"""Regression tests for the GEPA claim-audit fixes (docs/claims/gepa.md, "Fix log").

Each test fails on the pre-fix behaviour:

* F1 - ``merge_cap_mode="hard"`` capped *accepted* merges, so rejected merges (5 rollouts each)
  were unlimited; the paper says merge "is invoked a maximum of 5 times";
* F3 - the reflection truncation check must fire on ``CachedLLM`` hits (the core now keeps
  ``stop_reason``) and on OpenAI/LiteLLM-style ``finish_reason`` raws;
* V1 - the shadow monitor's wall time counted toward ``Budget.max_wall_s`` / ``Timeout``;
* L3 - CurrentBest must break validation ties like the reference ``idxmax`` (oldest candidate),
  and set-cover pruning of fully tied candidates keeps the newest (the E3 mechanism);
* F5 / S1 - the regenerated E1 / E4 result files carry the matched-ratio comparison and the
  invocation counts (skipped when the result files are absent).
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import pytest

from rsi.core import Artifact, Budget
from rsi.core.llm import LLM, CachedLLM, LLMResponse, Usage
from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.gepa import Config, run
from rsi.gepa.frontier import FrontierTracker, find_dominator_programs, select_from_pareto_front
from rsi.gepa.merge import MergeProposer
from rsi.gepa.reflection import ReflectionProposer, response_finish_reason
from rsi.gepa.strategies import CurrentBestSelector, EpsilonGreedySelector, idxmax
from rsi.trace import ShadowMonitor

ROOT = Path(__file__).resolve().parents[1]


def _merge_run(seed: int, B: int = 1500, **kw):
    d = make_domain(seed=seed)
    res = run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world),
              config=Config(max_metric_calls=B, seed=seed, use_merge=True, **kw))
    ev = [e for e in res.state.trace if e.get("invoked_merge")]
    acc = sum(1 for e in ev if e.get("event") == "merge_accepted")
    rej = sum(1 for e in ev if e.get("event") == "merge_rejected")
    return res, acc, rej


# ------------------------------------------------------------------------------ F1: merge cap
def test_merge_proposer_hard_cap_counts_invocations_not_acceptances():
    m = MergeProposer(random.Random(0), max_merge_invocations=2, cap_mode="hard")
    m.merges_due, m.last_iter_found_new_program = 3, True
    assert m.should_attempt()
    m.merges_performed[0].extend([(1, 2, 0), (1, 3, 0)])      # two merges built and scored, both rejected
    assert m.n_invocations == 2 and m.total_merges_tested == 0
    assert not m.should_attempt()                              # pre-fix "hard" still attempted here
    acc = MergeProposer(random.Random(0), max_merge_invocations=2, cap_mode="accepted")
    acc.merges_due, acc.last_iter_found_new_program = 3, True
    acc.merges_performed[0].extend([(1, 2, 0), (1, 3, 0)])
    assert acc.should_attempt()                                # pre-audit semantics kept under its own name
    soft = MergeProposer(random.Random(0), max_merge_invocations=2)
    soft.merges_due, soft.last_iter_found_new_program, soft.total_merges_tested = 1, True, 5
    assert soft.should_attempt()                               # reference: the cap never gates an attempt
    # the invocation count is derived from the persisted log, so it survives resume
    st = m.get_state()
    m2 = MergeProposer(random.Random(0), max_merge_invocations=2, cap_mode="hard")
    m2.set_state(st)
    assert m2.n_invocations == 2 and st["n_invocations"] == 2 and not m2.should_attempt()
    with pytest.raises(ValueError):
        MergeProposer(random.Random(0), cap_mode="bogus")
    with pytest.raises(ValueError):
        Config(merge_cap_mode="bogus").validate()


def test_hard_merge_cap_bounds_accepted_plus_rejected_end_to_end():
    over_accepted_cap = False
    for seed in (0, 1):
        res, acc, rej = _merge_run(seed, merge_cap_mode="hard")
        assert acc + rej == res.meta["n_merge_invocations"] <= 5          # paper: "invoked a maximum of 5 times"
        assert res.meta["n_merges_accepted"] == acc
        assert res.state.counter.by_phase["merge_subsample"] <= 5 * 5     # at most 5 subsample evaluations
        _, acc_a, rej_a = _merge_run(seed, merge_cap_mode="accepted")
        assert acc_a <= 5
        over_accepted_cap |= acc_a + rej_a > 5
    assert over_accepted_cap          # the pre-fix "hard" semantics let rejected merges exceed the cap (8, 13)


def test_hard_merge_cap_one_invocation_and_soft_cap_unbounded():
    res, acc, rej = _merge_run(1, B=1500, merge_cap_mode="hard", max_merge_invocations=1)
    assert acc + rej <= 1
    res_s, acc_s, rej_s = _merge_run(1, B=1500, max_merge_invocations=1)
    assert acc_s + rej_s > 1          # reference soft cap: invocations are not bounded by the cap


# ------------------------------------------------------------------------ F3: truncation check
class _Truncating(LLM):
    name = "trunc"

    def __init__(self, text: str, raw: dict) -> None:
        super().__init__()
        self.text, self.raw = text, raw

    def _complete(self, prompt, *, system, max_tokens, seed):
        return LLMResponse(text=self.text, usage=Usage(1, 10, 10, 0.0, 0.0), model="x", raw=dict(self.raw))


RECS = {"p.md": [{"Inputs": "q", "Generated Outputs": "a", "Feedback": "wrong"}]}


def test_truncated_reflection_is_rejected_on_cache_miss_and_hit(tmp_path):
    llm = CachedLLM(_Truncating("```\nNew instruction cut off mid", {"stop_reason": "max_tokens"}), tmp_path)
    prop = ReflectionProposer(llm)
    art = Artifact({"p.md": "Solve.\n"})
    for k in range(2):
        r = prop.propose(art, RECS, ["p.md"], seed_fn=lambda c: 1)
        assert r.new_texts == {} and "incomplete" in r.rejected["p.md"], k
        assert r.finish["p.md"] == "max_tokens"
    assert llm.hits == 1                     # the second call was a cache hit and was still rejected


def test_finish_reason_key_and_complete_fence_semantics(tmp_path):
    art = Artifact({"p.md": "Solve.\n"})
    # OpenAI / LiteLLM style raw: the pre-fix reader only looked at "stop_reason" and accepted this
    r = ReflectionProposer(_Truncating("New instruction cut off", {"finish_reason": "length"})).propose(
        art, RECS, ["p.md"])
    assert r.new_texts == {} and "finish_reason='length'" in r.rejected["p.md"]
    # reference semantics: a complete fence pair is parsed even when the provider says max_tokens
    r = ReflectionProposer(_Truncating("```\nFull text\n```\ntrailing", {"stop_reason": "max_tokens"})).propose(
        art, RECS, ["p.md"])
    assert r.new_texts == {"p.md": "Full text\n"}
    assert response_finish_reason(LLMResponse("x", Usage(), "m", raw={"stop_reason": None})) is None
    assert response_finish_reason(LLMResponse("x", Usage(), "m", raw={"cached": True, "usage": {}})) is None


# ------------------------------------------------------------ V1: monitor wall time is credited
class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class _SlowMonitor(ShadowMonitor):
    """Every sealed-split evaluation takes 100 s of (fake) wall time."""

    clock: _Clock

    def observe(self, tracer, round, name, artifact, decision_score=None):
        orig = self.ev.evaluate

        def slow(*a, **k):
            self.clock.t += 100.0
            return orig(*a, **k)

        self.ev.evaluate = slow
        try:
            super().observe(tracer, round, name, artifact, decision_score)
        finally:
            self.ev.evaluate = orig


def _timed_run(tmp_path, name, clock, *, monitor: bool, **kw):
    d = make_domain(seed=0)
    mon = False
    if monitor:
        mon = _SlowMonitor(d, None, splits=("test",), workers=1)
        mon.clock = clock

    def tick(event, payload):                  # the loop's own work: 10 s per iteration
        if event == "iteration_end":
            clock.t += 10.0

    cfg = kw.pop("config", Config(max_metric_calls=2000))
    return run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world), config=cfg,
               out_dir=tmp_path / name, monitor=mon, callbacks=[tick], **kw)


@pytest.mark.parametrize("which", ["budget", "timeout"])
def test_monitor_wall_time_does_not_count_toward_wall_clock_stoppers(tmp_path, monkeypatch, which):
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)

    def kw():
        if which == "budget":        # _t0 explicitly: Budget's default_factory bound the real time.time at import
            return dict(budget=Budget(max_wall_s=35, _t0=clock()))
        return dict(config=Config(max_metric_calls=2000, timeout_s=35))

    with_mon = _timed_run(tmp_path, "a", clock, monitor=True, **kw())
    assert with_mon.meta["iterations"] > 1       # pre-fix: the seed's 100 s monitor call stopped it at iteration 0
    without = _timed_run(tmp_path, "b", clock, monitor=False, **kw())
    # only the loop's own 10 s per iteration counts: the same stop point with and without the monitor
    assert with_mon.meta["iterations"] == without.meta["iterations"] == 4
    assert with_mon.stop_reason == without.stop_reason == ("max_wall_s" if which == "budget" else "timeout")
    assert with_mon.trajectory == without.trajectory
    end = [json.loads(l) for l in (tmp_path / "a" / "trace.jsonl").read_text().splitlines()][-1]
    assert end["kind"] == "run_end" and end["data"]["monitor_wall_s_credited"] >= 100.0


# --------------------------------------------------------------- L3: tie-breaking vs the reference
class _S:
    def __init__(self, agg, front):
        self._agg = agg
        self.candidates = list(range(len(agg)))
        self.frontier = FrontierTracker("instance")
        self.frontier.progs = {k: set(v) for k, v in front.items()}

    def agg_scores(self):
        return self._agg


def test_current_best_ties_go_to_oldest_like_reference_idxmax():
    agg = [0.0, 0.5, 0.5, 0.5, 0.2]
    st = _S(agg, {"a": {1, 2, 3}})
    ref_idxmax = lambda lst: lst.index(max(lst))          # gepa_utils.idxmax, verbatim semantics
    assert CurrentBestSelector().select(st) == ref_idxmax(agg) == idxmax(agg) == 1
    assert EpsilonGreedySelector(random.Random(0), epsilon=0.0).select(st) == 1
    # Pareto: fully tied candidates are pruned down to the newest (ascending-score, then index order),
    # which is why Pareto keeps building on the latest child on a validation plateau (E3 mechanism)
    tied = {"v1": {0, 1, 2}, "v2": {0, 1, 2}}
    assert find_dominator_programs(tied, [0.0, 0.0, 0.0]) == [2]
    assert {select_from_pareto_front(tied, [0.0, 0.0, 0.0], random.Random(s)) for s in range(20)} == {2}


# ------------------------------------------------------------------- regenerated result files
def _load(name):
    p = ROOT / "results" / "gepa" / name
    if not p.exists():
        pytest.skip(f"{p} not generated")
    return json.loads(p.read_text())


def test_e1_results_report_the_matched_budget_ratio():
    d = _load("e1_sample_efficiency.json")
    mr = d["matched_budget_ratio"]                      # pre-fix JSON had only rollouts-to-target
    assert mr["budgets"]["ratio_rl_over_gepa"] == 4
    assert {"mean_diff", "lo", "hi"} <= set(mr["gepa_minus_rl_final"])
    assert "1:4" in d["verdict_matched_ratio"]
    assert "agentqa" in d and len(d["agentqa"]["raw"]) == 15


def test_e4_results_hard_cap_bounds_invocations():
    d = _load("e4_merge.json")
    assert "merge_accepted5" in d["config"]["arms"]
    for r in d["raw"]:
        assert r["n_merge_invocations"] == r["n_merges"] + r["n_merge_rejected"]
        if r["arm"] == "merge_hard5":
            assert r["n_merge_invocations"] <= 5
    for b, summ in d["summary"].items():
        assert summ["merge_hard5"]["max_invocations_per_run"] <= 5


# ------------------------------------------------------------------ M16: spec E9 (credit assignment)
def test_system_record_shows_every_module_and_misplaced_rules_are_counted():
    d = make_domain(seed=0)
    tri, rep = (d.world.module_path(m) for m in d.world.cfg.modules)
    mods = d.world.cfg.modules
    task = next(t for t in (d.tasks.get(i) for i in d.tasks.splits["evolve"])      # a task spanning both modules
                if {d.world.aspects[a].module for a in t.meta["aspects"]} == set(mods))
    trial = d.run(d.seed_artifact(), task, seed=1, llm=None)
    own = d.reflective_record(task, trial, tri)
    sys_rec = d.system_reflective_record(task, trial, tri)
    assert sys_rec == d.system_reflective_record(task, trial, rep)          # component is ignored
    other = [a for a in task.meta["aspects"] if d.world.aspects[a].module != d.world.cfg.modules[0]]
    assert other and all(a not in own["Generated Outputs"] for a in other)
    assert all(a in sys_rec["Generated Outputs"] for a in task.meta["aspects"])
    # a rule for a reply property written into the triage prompt is misplaced; in its own prompt it is not
    a = other[0]
    code = d.world.aspects[a].codes[0]
    wrong = d.seed_artifact().with_files({tri: d.seed_artifact()[tri] + "\n" + d.world.rule_text(a, code)})
    right = d.seed_artifact().with_files({rep: d.seed_artifact()[rep] + "\n" + d.world.rule_text(a, code)})
    assert d.misplaced_rule_lines(d.seed_artifact()) == 0
    assert d.misplaced_rule_lines(wrong) == 1 and d.misplaced_rule_lines(right) == 0


def test_e9_results_module_specific_records_avoid_misplaced_rules():
    d = _load("e9_credit_assignment.json")
    s = d["summary"]
    assert s["module/round_robin"]["misplaced_rule_lines"]["mean"] == 0.0
    assert s["system/round_robin"]["misplaced_rule_lines"]["mean"] > 0.0
    assert set(d["paired"]) >= {"module_minus_system (round_robin)", "module_minus_system (all)"}
