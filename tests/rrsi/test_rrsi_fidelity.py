"""Defaults that follow the released code; each older behaviour sits only behind its own Config option."""
import collections

import pytest

from rsi.core import Artifact, Budget, CachedLLM, Execution, GateContext, MockLLM, Scored, Task
from rsi.domains.harnessworld import HarnessWorldMockLLM
from rsi.rrsi import (Candidate, Config, History, Killed, Measurer, Proposer, RRSICritic, RRSIRewriteEditor, RRSIRun,
                      Taxonomy, aggregate, build_gates, default_constitution, parse_sections, run, select_round)
from rsi.rrsi.evaluate import TaskResult
from rsi.rrsi.mocks import AgentQAMockLLM

from rrsi_helpers import Paid, aq_run, edit, edit_rows, fn_domain, hw_domain, mechanism, read_json, reply


def _meas(job, S_tasks, tokens=1000):
    per = {f"t{i}": TaskResult([s, s], tokens=[tokens, tokens]) for i, s in enumerate(S_tasks)}
    return aggregate(job, 2, per)


def _evolve_domain(execute, n: int):
    return fn_domain([Task(f"q{i:02d}", i, "x", "f") for i in range(n)], execute)


def test_band_edge_follows_the_code_raw_float_comparison():
    """0.55 - 0.50 = 0.05000000000000004 > delta, so the cost-rule branch admits dC = 1.0 <= 0.1 + 40 * 0.05."""
    inc, cand = _meas("inc", [0.5] * 20, tokens=1000), _meas("c", [0.55] * 20, tokens=2000)
    assert cand.S - inc.S > 0.05
    win, decs = select_round([Candidate("A", [], ev=cand)], inc, 0.5, 0.05, Config(), {})
    assert decs[0].admissible and win is not None and "beta0+beta1*dS" in decs[0].reason
    legacy = Config(tie_eps=1e-9)                                     # reads the gain as inside the band
    decs = select_round([Candidate("A", [], ev=cand)], inc, 0.5, 0.05, legacy, {}, build_gates(legacy))[1]
    assert not decs[0].admissible and "shaped" in decs[0].reason


@pytest.mark.parametrize("gate_i,cand,inc,ctx,raw_ok", [
    (0, Scored(0.5), Scored(0.6), GateContext(best_score=0.5 + 1e-12, delta=0.0), False),   # S' < floor by 1e-12
    (1, Scored(0.5 + 1e-12, cost=1.0), Scored(0.5, cost=1.0), GateContext(delta=0.0), True),  # dS > 0 = delta
])
def test_floor_and_band_have_no_hidden_tolerance(gate_i, cand, inc, ctx, raw_ok):
    assert bool(build_gates(Config())[gate_i].check(cand, inc, ctx)) is raw_ok
    assert bool(build_gates(Config(tie_eps=1e-9))[gate_i].check(cand, inc, ctx)) is (not raw_ok)


def _first_proposer_prompt(out, **cfg):
    dom = hw_domain()
    llm = HarnessWorldMockLLM(dom.world)
    run(dom, dom.seed_artifact(), llm_propose=llm, config=Config(T=1, workers=1, **cfg), out_dir=out)
    prompt = next(c["prompt"] for c in llm.calls if (c.get("system") or "").startswith("You are a harness engineer"))
    return prompt, parse_sections(prompt)["directives"]


def test_proposer_is_never_shown_delta_s_star_or_numeric_weights(tmp_path):
    prompt, d = _first_proposer_prompt(tmp_path / "a")
    assert {"b_t", "reserved_slot", "untried", "prune_components", "trace_task_ids"} <= set(d)
    assert not {"delta", "S_star", "S_incumbent", "T"} & set(d)
    assert "beta0 + beta1 x (gain)" in prompt and "w_s x (gain) - w_c x (relative cost change)" in prompt
    assert "0.1 + 40.0 x" not in prompt and "100.0 x (gain)" not in prompt
    prompt, d = _first_proposer_prompt(tmp_path / "b", proposer_numbers=True)
    assert {"delta", "S_star", "S_incumbent", "T"} <= set(d)
    assert "0.1 + 40.0 x (gain)" in prompt


def test_numeric_constitution_only_on_request():
    faithful, _ = default_constitution(Config(), Taxonomy())
    numeric, _ = default_constitution(Config(proposer_numbers=True), Taxonomy())
    assert "beta0 + beta1 x (gain)" in faithful and "40.0" not in faithful
    assert "0.1 + 40.0 x (gain)" in numeric and "100.0 x (gain) - 15.0 x" in numeric


def test_precheck_denylist_is_ids_and_patterns_not_answers():
    dom = hw_domain()
    ev = dom.tasks.split("evolve", allow_sealed=True)
    crit = RRSICritic(dom, None)
    assert {t.id for t in ev} <= set(crit.denylist.terms)
    assert not {t.target for t in ev} & set(crit.denylist.terms)
    base = dom.seed_artifact()
    answer_only = base.with_files({"memory/cache.py": f'ANS = ["{ev[0].target}", "{ev[1].target}"]\n'})
    assert crit.precheck(base.diff(answer_only)) == []                    # left to the LLM review (rule 1)
    ids = base.with_files({"memory/cache.py": f'ANS = {{"{ev[0].id}": 1}}\n'})
    assert crit.precheck(base.diff(ids))
    assert RRSICritic(dom, None, precheck_answers=True).precheck(base.diff(answer_only))


def test_precheck_scope_added_vs_the_codes_whole_diff_grep():
    dom = hw_domain()
    leak, base = mechanism(dom, "leak"), dom.seed_artifact()
    removal = base.with_files({leak.path: leak.file_text()}).diff(base)    # deletes the leaked table
    assert RRSICritic(dom, None).precheck(removal) == []
    assert RRSICritic(dom, None, scope="diff").precheck(removal)           # the code greps the whole diff
    with pytest.raises(ValueError):
        RRSICritic(dom, None, scope="lines")


def test_missing_trial_carries_the_task_weight():
    def execute(art, task, seed, llm):
        if task.id == "big" and seed == 1:
            raise RuntimeError("infra: judge timed out")
        return Execution(output=0.2 if task.id == "big" else 1.0, meta={"weight": task.meta["weight"]})
    dom = fn_domain([Task("big", 0, None, "f", {"weight": 50.0}), Task("small", 0, None, "f", {"weight": 1.0})],
                    execute, lambda t, o: float(o))
    m = Measurer(dom, None, workers=1).measure(Artifact({"a": "a"}), "job", 2, seeds=[0, 1])
    assert m.per_task["big"].weights == [50.0, 50.0] and m.missing == 1
    assert m.S == pytest.approx((0.2 * 50 + 1 + 1) / 102)


def test_render_keeps_the_baseline_row_like_the_code(tmp_path):
    h = History(tmp_path / "h.jsonl", timestamps=False)
    h.append({"t": 0, "variant": "-", "edit_id": None, "component": None, "hypothesis": "H_0 baseline",
              "outcome": "BASELINE", "S": 0.5, "C": 1.0, "accepted": True, "delta_S": None, "delta_C": None,
              "bundle": 0})
    h.append_candidate(0, "A", edit_rows(prompt="x"), "REJECTED", -0.1, 0.0, False, 0.4, 1.0, "d")
    rows = h.render()
    assert rows[0]["outcome"] == "BASELINE" and rows[0]["hypothesis"] == "H_0 baseline" and len(rows) == 2
    for t in range(1, 5):                                             # 4 newer unmeasured records push it out
        h.append_candidate(t, "A", edit_rows(prompt="y"), "critic_reject", None, None, False, None, None, "d")
    assert [r["outcome"] for r in h.render()] == ["REJECTED"] + ["critic_reject"] * 4
    assert h.render(mode="accepted_only")[0]["outcome"] == "BASELINE"


def _tool_reply(comp):
    return reply({"tools/t.py": "T = 1\n"}, [edit(comp)])


def test_declared_tag_outside_k_is_bounced_not_aliased():
    base = Artifact({"harness.md": "h\n"})
    tax = Taxonomy.from_domain(hw_domain())                               # K has client_tool, not tool
    llm = MockLLM(lambda p, s, seed, i: [_tool_reply("tool"), _tool_reply("client_tool")][min(i, 1)])
    out = Proposer(RRSIRewriteEditor(llm), tax, Config()).propose(base, directives={}, budget=1)
    assert out["status"] == "done" and len(out["log"]) == 2               # "tool" bounced by done()
    assert "not in" in llm.calls[1]["prompt"]
    assert tax.normalize("tool", "+++ b/prompts/p.md\n+x\n") == "prompt"
    tax_alias = Taxonomy.from_domain(hw_domain(), aliases=True)
    assert tax_alias.canonical("tool") == "client_tool"
    llm2 = MockLLM(lambda p, s, seed, i: _tool_reply("tool"))
    out2 = Proposer(RRSIRewriteEditor(llm2), tax_alias, Config()).propose(base, directives={}, budget=1)
    assert out2["status"] == "done" and len(out2["log"]) == 1 and out2["edits"][0]["component"] == "client_tool"


def test_eval_invalid_retry_reruns_only_the_missing_trials(tmp_path):
    calls = collections.Counter()

    def execute(art, task, seed, llm):
        calls[(task.id, seed)] += 1
        if calls[(task.id, seed)] == 1 and int(task.id[1:]) % 2 == 0:
            raise RuntimeError("infra: backend down")                        # half the slots fail once
        return "x"
    r = RRSIRun(_evolve_domain(execute, 20), Artifact({"a.txt": "a\n"}), out_dir=tmp_path,
                config=Config(k=2, workers=1, delta=0.05))
    ev, why = r._measure_valid(Artifact({"a.txt": "a\n"}), "job")
    assert ev is not None and ev.missing == 0 and why == ""
    assert sum(calls.values()) == 40 + 20                                  # 40 slots + 20 refills
    assert collections.Counter(calls.values()) == {1: 20, 2: 20}


def test_smoke_runs_the_codes_number_of_tasks(tmp_path):
    ok, broken = Artifact({"solve.py": "ok\n"}), Artifact({"solve.py": "broken\n"})

    def execute(art, task, seed, llm):
        if "broken" in art["solve.py"] and task.id == "q01":
            raise ValueError("crashes on the second smoke task")
        return "x"
    dom = _evolve_domain(execute, 10)
    r = RRSIRun(dom, ok, out_dir=tmp_path / "a", config=Config(delta=0.05))
    assert r.smoke_ids() == ["q00", "q01"]                                  # coding / workspace: 2 tasks
    assert r.smoke(broken) is not None
    eng = RRSIRun(dom, ok, out_dir=tmp_path / "b", config=Config.preset("eng"))
    assert eng.smoke_ids() == ["q00", "q01", "q02", "q03"]                  # eng: 4 tasks ...
    zero = _evolve_domain(lambda *a: "y", 10)
    assert RRSIRun(zero, ok, out_dir=tmp_path / "c", config=Config.preset("eng")).smoke(ok)   # ... and mean score > 0
    hw = hw_domain()
    assert RRSIRun(hw, hw.seed_artifact(), out_dir=tmp_path / "d", config=Config()).smoke_ids() == \
        hw.tasks.splits["smoke"]


def _paid_run(out, cache, hooks=None):
    prop = CachedLLM(Paid(MockLLM(AgentQAMockLLM()._respond), 0.01), cache)    # $0.01 per search call
    _, res = aq_run(out, T=8, propose=prop, budget=Budget(max_usd=0.05), hooks=hooks, shadow_monitor=False)
    return prop, res


def test_resumed_run_budget_includes_the_killed_process_spend(tmp_path):
    def kill(ev, t=None, **kw):
        if t == 0:
            raise Killed()
    _, full = _paid_run(tmp_path / "full", tmp_path / "c_full")
    assert full.stop_reason == "max_usd"
    with pytest.raises(Killed):
        _paid_run(tmp_path / "k", tmp_path / "c_k", hooks={"settled": kill})
    spent = read_json(tmp_path / "k" / "spend.json")["segments"]
    assert len(spent) == 1 and spent[0]["usd"] > 0
    _, resumed = _paid_run(tmp_path / "k", tmp_path / "c_k")
    assert resumed.stop_reason == "max_usd"
    assert len(resumed.trajectory) == len(full.trajectory)
    assert resumed.meta["spend"]["prior_segments_usd"] == pytest.approx(spent[0]["usd"])


def test_cache_replay_is_charged_at_the_original_price(tmp_path):
    _, orig = _paid_run(tmp_path / "orig", tmp_path / "cache")
    prop, replay = _paid_run(tmp_path / "replay", tmp_path / "cache")
    assert prop.meter.total().cost_usd == 0 and prop.misses == 0
    assert [r["S"] for r in orig.trajectory] == [r["S"] for r in replay.trajectory]
    assert replay.stop_reason == orig.stop_reason == "max_usd"
