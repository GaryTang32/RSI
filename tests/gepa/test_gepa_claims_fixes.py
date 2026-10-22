"""Regressions for the GEPA claim-audit fixes (docs/methods/gepa/claims-audit.md, "Fix log")."""
import json
import random
import time
from types import SimpleNamespace

import pytest

from rsi.core import Artifact, Budget
from rsi.core.llm import LLM, CachedLLM, LLMResponse, Usage
from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.gepa import Config, run
from rsi.gepa.frontier import find_dominator_programs, select_from_pareto_front
from rsi.gepa.merge import MergeProposer
from rsi.gepa.reflection import ReflectionProposer, response_finish_reason
from rsi.gepa.strategies import CurrentBestSelector, EpsilonGreedySelector, idxmax
from rsi.trace import ShadowMonitor

RECS = {"p.md": [{"Inputs": "q", "Generated Outputs": "a", "Feedback": "wrong"}]}
SOLVE = Artifact({"p.md": "Solve.\n"})


def _merge_counts(res) -> tuple[int, int]:
    """Accepted and rejected merges among the iterations that invoked merge."""
    events = [e.get("event") for e in res.state.trace if e.get("invoked_merge")]
    return events.count("merge_accepted"), events.count("merge_rejected")


def _due_proposer(cap_mode: str = "reference_soft", **kw) -> MergeProposer:
    """A proposer capped at 2 invocations, right after a new program was found."""
    return MergeProposer(random.Random(0), max_merge_invocations=2, cap_mode=cap_mode,
                         last_iter_found_new_program=True, **kw)


def test_merge_proposer_hard_cap_counts_invocations_not_acceptances():
    hard = _due_proposer("hard", merges_due=3)
    assert hard.should_attempt()
    hard.merges_performed[0].extend([(1, 2, 0), (1, 3, 0)])      # two merges built and scored, both rejected
    assert hard.n_invocations == 2 and hard.total_merges_tested == 0 and not hard.should_attempt()
    accepted = _due_proposer("accepted", merges_due=3)
    accepted.merges_performed[0].extend([(1, 2, 0), (1, 3, 0)])
    assert accepted.should_attempt()
    assert _due_proposer(merges_due=1, total_merges_tested=5).should_attempt()   # reference: the cap never gates it
    st = hard.get_state()
    resumed = MergeProposer(random.Random(0), max_merge_invocations=2, cap_mode="hard")
    resumed.set_state(st)
    assert resumed.n_invocations == st["n_invocations"] == 2 and not resumed.should_attempt()
    with pytest.raises(ValueError):
        MergeProposer(random.Random(0), cap_mode="bogus")
    with pytest.raises(ValueError):
        Config(merge_cap_mode="bogus").validate()


def test_hard_merge_cap_bounds_accepted_plus_rejected_end_to_end(merge_run):
    over_accepted_cap = False
    for seed in (0, 1):
        res = merge_run(seed, merge_cap_mode="hard")
        acc, rej = _merge_counts(res)
        assert acc + rej == res.meta["n_merge_invocations"] <= 5          # paper: "invoked a maximum of 5 times"
        assert res.meta["n_merges_accepted"] == acc
        assert res.state.counter.by_phase["merge_subsample"] <= 5 * 5
        acc_a, rej_a = _merge_counts(merge_run(seed, merge_cap_mode="accepted"))
        assert acc_a <= 5
        over_accepted_cap |= acc_a + rej_a > 5
    assert over_accepted_cap          # capping accepted merges lets rejected ones exceed the cap


def test_hard_merge_cap_one_invocation_and_soft_cap_unbounded(merge_run):
    assert sum(_merge_counts(merge_run(1, merge_cap_mode="hard", max_merge_invocations=1))) <= 1
    assert sum(_merge_counts(merge_run(1, max_merge_invocations=1))) > 1     # reference soft cap


class _Truncating(LLM):
    name = "trunc"

    def __init__(self, text: str, raw: dict) -> None:
        super().__init__()
        self.text, self.raw = text, raw

    def _complete(self, prompt, *, system, max_tokens, seed):
        return LLMResponse(text=self.text, usage=Usage(1, 10, 10, 0.0, 0.0), model="x", raw=dict(self.raw))


def test_truncated_reflection_is_rejected_on_cache_miss_and_hit(tmp_path):
    llm = CachedLLM(_Truncating("```\nNew instruction cut off mid", {"stop_reason": "max_tokens"}), tmp_path)
    prop = ReflectionProposer(llm)
    for k in range(2):
        r = prop.propose(SOLVE, RECS, ["p.md"], seed_fn=lambda c: 1)
        assert r.new_texts == {} and "incomplete" in r.rejected["p.md"], k
        assert r.finish["p.md"] == "max_tokens"
    assert llm.hits == 1


def test_finish_reason_key_and_complete_fence_semantics():
    def reflect(text: str, raw: dict):
        return ReflectionProposer(_Truncating(text, raw)).propose(SOLVE, RECS, ["p.md"])

    r = reflect("New instruction cut off", {"finish_reason": "length"})
    assert r.new_texts == {} and "finish_reason='length'" in r.rejected["p.md"]
    # reference: a complete fence pair is parsed even when the provider says max_tokens
    r = reflect("```\nFull text\n```\ntrailing", {"stop_reason": "max_tokens"})
    assert r.new_texts == {"p.md": "Full text\n"}
    assert response_finish_reason(LLMResponse("x", Usage(), "m", raw={"stop_reason": None})) is None
    assert response_finish_reason(LLMResponse("x", Usage(), "m", raw={"cached": True, "usage": {}})) is None


def _slow_monitor(domain, clock: SimpleNamespace) -> ShadowMonitor:
    """A shadow monitor whose every sealed-split evaluation takes 100 s of fake wall time."""
    mon = ShadowMonitor(domain, None, splits=("test",), workers=1)
    evaluate = mon.ev.evaluate

    def slow(*a, **k):
        clock.t += 100.0
        return evaluate(*a, **k)

    mon.ev.evaluate = slow
    return mon


@pytest.mark.parametrize("which", ["budget", "timeout"])
def test_monitor_wall_time_does_not_count_toward_wall_clock_stoppers(tmp_path, monkeypatch, which):
    clock = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(time, "time", lambda: clock.t)

    def tick(event, payload):              # the loop's own work: 10 s per iteration
        if event == "iteration_end":
            clock.t += 10.0

    def timed_run(name: str, monitor: bool):
        d = make_domain(seed=0)
        if which == "budget":              # Budget's default _t0 bound the real time.time at import
            stop = dict(budget=Budget(max_wall_s=35, _t0=clock.t), config=Config(max_metric_calls=2000))
        else:
            stop = dict(config=Config(max_metric_calls=2000, timeout_s=35))
        return run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world), out_dir=tmp_path / name,
                   monitor=_slow_monitor(d, clock) if monitor else False, callbacks=[tick], **stop)

    with_mon = timed_run("a", monitor=True)
    without = timed_run("b", monitor=False)
    assert with_mon.meta["iterations"] == without.meta["iterations"] == 4
    assert with_mon.stop_reason == without.stop_reason == ("max_wall_s" if which == "budget" else "timeout")
    assert with_mon.trajectory == without.trajectory
    end = json.loads((tmp_path / "a" / "trace.jsonl").read_text().splitlines()[-1])
    assert end["kind"] == "run_end" and end["data"]["monitor_wall_s_credited"] >= 100.0


def test_current_best_ties_go_to_oldest_like_reference_idxmax(selector_state):
    agg = [0.0, 0.5, 0.5, 0.5, 0.2]
    st = selector_state(agg, {"a": {1, 2, 3}})
    assert CurrentBestSelector().select(st) == agg.index(max(agg)) == idxmax(agg) == 1   # gepa_utils.idxmax
    assert EpsilonGreedySelector(random.Random(0), epsilon=0.0).select(st) == 1
    # set-cover pruning keeps the newest of fully tied candidates (the E3 plateau mechanism)
    tied = {"v1": {0, 1, 2}, "v2": {0, 1, 2}}
    assert find_dominator_programs(tied, [0.0, 0.0, 0.0]) == [2]
    assert {select_from_pareto_front(tied, [0.0, 0.0, 0.0], random.Random(s)) for s in range(20)} == {2}


def test_e1_results_report_the_matched_budget_ratio(result):
    d = result("e1_sample_efficiency.json")
    mr = d["matched_budget_ratio"]
    assert mr["budgets"]["ratio_rl_over_gepa"] == 4
    assert {"mean_diff", "lo", "hi"} <= set(mr["gepa_minus_rl_final"])
    assert "1:4" in d["verdict_matched_ratio"]
    assert "agentqa" in d and len(d["agentqa"]["raw"]) == 15


def test_e4_results_hard_cap_bounds_invocations(result):
    d = result("e4_merge.json")
    assert "merge_accepted5" in d["config"]["arms"]
    for r in d["raw"]:
        assert r["n_merge_invocations"] == r["n_merges"] + r["n_merge_rejected"]
        assert r["arm"] != "merge_hard5" or r["n_merge_invocations"] <= 5
    assert all(summ["merge_hard5"]["max_invocations_per_run"] <= 5 for summ in d["summary"].values())


def test_system_record_shows_every_module_and_misplaced_rules_are_counted():
    d = make_domain(seed=0)
    mods = d.world.cfg.modules
    tri, rep = (d.world.module_path(m) for m in mods)
    task = next(t for t in map(d.tasks.get, d.tasks.splits["evolve"])
                if {d.world.aspects[a].module for a in t.meta["aspects"]} == set(mods))
    trial = d.run(d.seed_artifact(), task, seed=1, llm=None)
    own = d.reflective_record(task, trial, tri)
    sys_rec = d.system_reflective_record(task, trial, tri)
    assert sys_rec == d.system_reflective_record(task, trial, rep)
    other = [a for a in task.meta["aspects"] if d.world.aspects[a].module != mods[0]]
    assert other and all(a not in own["Generated Outputs"] for a in other)
    assert all(a in sys_rec["Generated Outputs"] for a in task.meta["aspects"])
    seed = d.seed_artifact()
    rule = "\n" + d.world.rule_text(other[0], d.world.aspects[other[0]].codes[0])
    assert d.misplaced_rule_lines(seed) == 0
    assert d.misplaced_rule_lines(seed.with_files({tri: seed[tri] + rule})) == 1    # reply rule in the triage prompt
    assert d.misplaced_rule_lines(seed.with_files({rep: seed[rep] + rule})) == 0


def test_e9_results_module_specific_records_avoid_misplaced_rules(result):
    d = result("e9_credit_assignment.json")
    s = d["summary"]
    assert s["module/round_robin"]["misplaced_rule_lines"]["mean"] == 0.0
    assert s["system/round_robin"]["misplaced_rule_lines"]["mean"] > 0.0
    assert set(d["paired"]) >= {"module_minus_system (round_robin)", "module_minus_system (all)"}
