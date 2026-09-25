"""Regression tests for the claims-audit findings (docs/claims/rrsi.md section 3 and "Fix log"): each test
fails on the pre-fix behaviour and pins the reference-faithful default, with the old behaviour available
only behind its explicit, documented Config option."""
import collections
import json

import pytest

from rsi.core import (Artifact, Budget, CachedLLM, Execution, FunctionDomain, GateContext, LLMResponse, MockLLM,
                      Scored, Task, TaskSuite, Usage)
from rsi.domains.harnessworld import HarnessWorldMockLLM, make_domain
from rsi.rrsi import (Config, Killed, Measurer, Proposer, RRSICritic, RRSIRewriteEditor, RRSIRun, Taxonomy,
                      build_gates, default_constitution, parse_sections, run, select_round)
from rsi.rrsi.evaluate import TaskResult, aggregate
from rsi.rrsi.history import History
from rsi.rrsi.selection import Candidate

SMALL = dict(n_evolve=24, n_holdout=24, n_ood_per_family=6)


# ------------------------------------------------------------------ N1: Algorithm 2 on raw floats
def _meas(job, S_tasks, tokens=1000):
    per = {f"t{i}": TaskResult([s, s], tokens=[tokens, tokens]) for i, s in enumerate(S_tasks)}
    return aggregate(job, 2, per)


def test_band_edge_follows_the_code_raw_float_comparison():
    """0.55 - 0.50 = 0.05000000000000004 > delta = 0.05, so the code takes the COST-RULE branch: dC = +1.0 <=
    0.10 + 40 * 0.05 = 2.1, admissible. The pre-fix 1e-9 tolerance read the gain as inside the band, where the
    shaped rule 100 * 0.05 - 15 * 1.0 = -10 < 0 rejected it (claims audit N1: 18 of 6,000 random rounds)."""
    inc = _meas("inc", [0.5] * 20, tokens=1000)
    cand = _meas("c", [0.55] * 20, tokens=2000)                       # dC = +1.0
    assert cand.S - inc.S > 0.05                                      # 0.05000000000000004
    faithful = select_round([Candidate("A", [], ev=cand)], inc, 0.5, 0.05, Config(), {})
    assert faithful[1][0].admissible and faithful[0] is not None     # code: gain > delta, 1.0 <= 2.1
    assert "beta0+beta1*dS" in faithful[1][0].reason
    legacy_cfg = Config(tie_eps=1e-9)                                 # the documented non-default reading
    legacy = select_round([Candidate("A", [], ev=cand)], inc, 0.5, 0.05, legacy_cfg, {},
                          build_gates(legacy_cfg))
    assert not legacy[1][0].admissible and "shaped" in legacy[1][0].reason


@pytest.mark.parametrize("gate_i,cand,inc,ctx,raw_ok", [
    (0, Scored(0.5), Scored(0.6), GateContext(best_score=0.5 + 1e-12, delta=0.0), False),   # S' < floor by 1e-12
    (1, Scored(0.5 + 1e-12, cost=1.0), Scored(0.5, cost=1.0), GateContext(delta=0.0), True),  # dS > 0 = delta
])
def test_floor_and_band_have_no_hidden_tolerance(gate_i, cand, inc, ctx, raw_ok):
    """A 1e-12 difference decides as the code's raw comparison does; the tie_eps=1e-9 option flips it."""
    g = build_gates(Config())[gate_i]
    assert bool(g.check(cand, inc, ctx)) is raw_ok
    g_eps = build_gates(Config(tie_eps=1e-9))[gate_i]
    assert bool(g_eps.check(cand, inc, ctx)) is (not raw_ok)


# ------------------------------------------------------------------ N2: no numeric thresholds for the proposer
def _first_proposer_prompt(tmp, **cfg):
    dom = make_domain(seed=0, **SMALL)
    llm = HarnessWorldMockLLM(dom.world)
    run(dom, dom.seed_artifact(), llm_propose=llm, config=Config(T=1, workers=1, **cfg), out_dir=tmp)
    prompt = next(c["prompt"] for c in llm.calls if (c.get("system") or "").startswith("You are a harness engineer"))
    return prompt, parse_sections(prompt)


def test_proposer_is_never_shown_delta_s_star_or_numeric_weights(tmp_path):
    prompt, P = _first_proposer_prompt(tmp_path / "a")
    d = P["directives"]
    assert {"b_t", "reserved_slot", "untried", "prune_components", "trace_task_ids"} <= set(d)
    assert not {"delta", "S_star", "S_incumbent", "T"} & set(d)
    assert "beta0 + beta1 x (gain)" in prompt and "w_s x (gain) - w_c x (relative cost change)" in prompt
    assert "0.1 + 40.0 x" not in prompt and "100.0 x (gain)" not in prompt
    prompt2, P2 = _first_proposer_prompt(tmp_path / "b", proposer_numbers=True)       # documented extension
    assert {"delta", "S_star", "S_incumbent", "T"} <= set(P2["directives"])
    assert "0.1 + 40.0 x (gain)" in prompt2


def test_numeric_constitution_only_on_request():
    tax = Taxonomy()
    faithful, _ = default_constitution(Config(), tax)
    numeric, _ = default_constitution(Config(proposer_numbers=True), tax)
    assert "beta0 + beta1 x (gain)" in faithful and "40.0" not in faithful
    assert "0.1 + 40.0 x (gain)" in numeric and "100.0 x (gain) - 15.0 x" in numeric


# ------------------------------------------------------------------ N3: the precheck holds ids + patterns only
def test_precheck_denylist_is_ids_and_patterns_not_answers():
    dom = make_domain(seed=0, **SMALL)
    ev = dom.tasks.split("evolve", allow_sealed=True)
    crit = RRSICritic(dom, None)
    assert {t.id for t in ev} <= set(crit.denylist.terms)
    assert not {t.target for t in ev} & set(crit.denylist.terms)          # no answer key (the code has none)
    base = dom.seed_artifact()
    answer_only = base.with_files({"memory/cache.py": f'ANS = ["{ev[0].target}", "{ev[1].target}"]\n'})
    assert crit.precheck(base.diff(answer_only)) == []                    # left to the LLM review (rule 1)
    ids = base.with_files({"memory/cache.py": f'ANS = {{"{ev[0].id}": 1}}\n'})
    assert crit.precheck(base.diff(ids))                                  # task ids / id patterns: precheck
    oracle = RRSICritic(dom, None, precheck_answers=True)                 # documented extension
    assert oracle.precheck(base.diff(answer_only))


def test_precheck_scope_added_vs_the_codes_whole_diff_grep():
    dom = make_domain(seed=0, **SMALL)
    leak = next(m for m in dom.world.catalog.values() if m.kind == "leak")
    base = dom.seed_artifact()
    leaky = base.with_files({leak.path: leak.file_text()})
    removal = leaky.diff(base)                                           # an edit that DELETES the leaked table
    assert RRSICritic(dom, None).precheck(removal) == []                 # default: a removal encodes nothing
    assert RRSICritic(dom, None, scope="diff").precheck(removal)          # the code's grep of the whole diff
    with pytest.raises(ValueError):
        RRSICritic(dom, None, scope="lines")


# ------------------------------------------------------------------ N4: a missing trial carries its task's weight
def test_missing_trial_carries_the_task_weight():
    def execute(art, task, seed, llm):
        if task.id == "big" and seed == 1:
            raise RuntimeError("infra: judge timed out")
        return Execution(output=0.2 if task.id == "big" else 1.0, meta={"weight": task.meta["weight"]})
    tasks = [Task("big", 0, None, "f", {"weight": 50.0}), Task("small", 0, None, "f", {"weight": 1.0})]
    dom = FunctionDomain(TaskSuite(tasks, {"evolve": ["big", "small"]}), execute, lambda t, o: float(o))
    m = Measurer(dom, None, workers=1).measure(Artifact({"a": "a"}), "job", 2, seeds=[0, 1])
    assert m.per_task["big"].weights == [50.0, 50.0] and m.missing == 1
    assert m.S == pytest.approx((0.2 * 50 + 1 + 1) / 102)               # code: 0.1176; pre-fix padding: 0.2264


# ------------------------------------------------------------------ N5: the rendered history keeps BASELINE
def test_render_keeps_the_baseline_row_like_the_code(tmp_path):
    h = History(tmp_path / "h.jsonl", timestamps=False)
    h.append({"t": 0, "variant": "-", "edit_id": None, "component": None, "hypothesis": "H_0 baseline",
              "outcome": "BASELINE", "S": 0.5, "C": 1.0, "accepted": True, "delta_S": None, "delta_C": None,
              "bundle": 0})
    h.append_candidate(0, "A", [{"id": "C1", "component": "prompt", "hypothesis": "x"}], "REJECTED", -0.1, 0.0,
                       False, 0.4, 1.0, "d")
    rows = h.render()
    assert rows[0]["outcome"] == "BASELINE" and rows[0]["hypothesis"] == "H_0 baseline" and len(rows) == 2
    for t in range(1, 5):                                             # 4 newer unmeasured records push it out
        h.append_candidate(t, "A", [{"id": "C1", "component": "prompt", "hypothesis": "y"}], "critic_reject",
                           None, None, False, None, None, "d")
    assert [r["outcome"] for r in h.render()] == ["REJECTED"] + ["critic_reject"] * 4
    assert h.render(mode="accepted_only")[0]["outcome"] == "BASELINE"


# ------------------------------------------------------------------ N6: a declared "tool" is not aliased
def _reply(comp):
    edit = {"id": "C1", "component": comp, "hypothesis": "h", "targets_mode": "m", "predicted_affected": ["t1"],
            "retroactive_check": "(corrective) (preservative) (transfer)"}
    return "```json\n" + json.dumps({"action": "done", "edits": [edit]}) + "\n```\n=== FILE: tools/t.py ===\nT = 1\n"


def test_declared_tag_outside_k_is_bounced_not_aliased():
    tax = Taxonomy.from_domain(make_domain(seed=0, **SMALL))                    # K has client_tool, not tool
    replies = [_reply("tool"), _reply("client_tool")]
    llm = MockLLM(lambda p, s, seed, i: replies[min(i, 1)])
    out = Proposer(RRSIRewriteEditor(llm), tax, Config()).propose(Artifact({"harness.md": "h\n"}), directives={},
                                                                  budget=1)
    assert out["status"] == "done" and len(out["log"]) == 2                   # "tool" bounced by done()
    assert "not in" in llm.calls[1]["prompt"]
    assert tax.normalize("tool", "+++ b/prompts/p.md\n+x\n") == "prompt"       # re-tagged from the diff
    tax_alias = Taxonomy.from_domain(make_domain(seed=0, **SMALL), aliases=True)
    assert tax_alias.canonical("tool") == "client_tool"                      # documented extension
    llm2 = MockLLM(lambda p, s, seed, i: _reply("tool"))
    out2 = Proposer(RRSIRewriteEditor(llm2), tax_alias, Config()).propose(Artifact({"harness.md": "h\n"}),
                                                                          directives={}, budget=1)
    assert out2["status"] == "done" and len(out2["log"]) == 1 and out2["edits"][0]["component"] == "client_tool"


# ------------------------------------------------------------------ N7: the retry refills only missing trials
def test_eval_invalid_retry_reruns_only_the_missing_trials(tmp_path):
    calls = collections.Counter()

    def execute(art, task, seed, llm):
        calls[(task.id, seed)] += 1
        if calls[(task.id, seed)] == 1 and int(task.id[1:]) % 2 == 0:
            raise RuntimeError("infra: backend down")                        # half the slots fail once
        return "x"
    tasks = [Task(f"q{i:02d}", i, "x", "f") for i in range(20)]
    dom = FunctionDomain(TaskSuite(tasks, {"evolve": [t.id for t in tasks]}), execute,
                         lambda t, o: float(o == t.target))
    r = RRSIRun(dom, Artifact({"a.txt": "a\n"}), out_dir=tmp_path, config=Config(k=2, workers=1, delta=0.05))
    ev, why = r._measure_valid(Artifact({"a.txt": "a\n"}), "job")
    assert ev is not None and ev.missing == 0 and why == ""
    assert sum(calls.values()) == 40 + 20                                  # 40 slots + 20 refills, not 80
    assert collections.Counter(calls.values()) == {1: 20, 2: 20}


# ------------------------------------------------------------------ N9: the liveness smoke runs 2-4 tasks
def test_smoke_runs_the_codes_number_of_tasks(tmp_path):
    seen = []

    def execute(art, task, seed, llm):
        seen.append(task.id)
        if "broken" in art["solve.py"] and task.id == "q01":
            raise ValueError("crashes on the second smoke task")
        return "x"
    tasks = [Task(f"q{i:02d}", i, "x", "f") for i in range(10)]
    dom = FunctionDomain(TaskSuite(tasks, {"evolve": [t.id for t in tasks]}), execute,
                         lambda t, o: float(o == t.target))
    r = RRSIRun(dom, Artifact({"solve.py": "ok\n"}), out_dir=tmp_path / "a", config=Config(delta=0.05))
    assert r.smoke_ids() == ["q00", "q01"]                                  # code: 2 tasks (coding, workspace)
    assert r.smoke(Artifact({"solve.py": "broken\n"})) is not None         # the pre-fix 1-task smoke passed it
    eng = RRSIRun(dom, Artifact({"solve.py": "ok\n"}), out_dir=tmp_path / "b", config=Config.preset("eng"))
    assert eng.smoke_ids() == ["q00", "q01", "q02", "q03"]                  # eng: 4 tasks ...
    zero = FunctionDomain(dom.tasks, lambda a, t, s, l: "y", lambda t, o: float(o == t.target))
    assert RRSIRun(zero, Artifact({"solve.py": "ok\n"}), out_dir=tmp_path / "c",
                   config=Config.preset("eng")).smoke(Artifact({"solve.py": "ok\n"}))   # ... and mean score > 0
    hw = make_domain(seed=0, **SMALL)                                      # a domain's own smoke split
    assert RRSIRun(hw, hw.seed_artifact(), out_dir=tmp_path / "d", config=Config()).smoke_ids() == \
        hw.tasks.splits["smoke"]


# ------------------------------------------------------------------ resumed run: the budget counts pre-kill spend
class _Paid(MockLLM):
    def __init__(self, fn):
        super().__init__(fn, name="paid-mock")

    def _complete(self, prompt, *, system, max_tokens, seed):
        r = super()._complete(prompt, system=system, max_tokens=max_tokens, seed=seed)
        return LLMResponse(r.text, Usage(1, r.usage.input_tokens, r.usage.output_tokens, 0.01, 0.0), r.model)


def test_resumed_run_budget_includes_the_killed_process_spend(tmp_path):
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.rrsi.mocks import AgentQAMockLLM
    suite = make_suite(n_evolve=8, n_holdout=6, n_ood_per_family=2, seed=0)

    def go(out, cache, kill_after=None):
        prop = CachedLLM(_Paid(AgentQAMockLLM()._respond), cache)            # $0.01 per search call
        hooks = {}
        if kill_after is not None:
            def h(ev, **kw):
                if ev == "settled" and kw["t"] == kill_after:
                    raise Killed()
            hooks = {"settled": h}
        try:
            return run(AgentQADomain(suite), AgentQADomain.seed_artifact(), llm_task=SimModel(suite),
                       llm_propose=prop, budget=Budget(max_usd=0.05), hooks=hooks, out_dir=out,
                       config=Config(T=8, k=2, workers=2, seed=0, record_timestamps=False, shadow_monitor=False))
        except Killed:
            return None
    full = go(tmp_path / "full", tmp_path / "c_full")
    assert full.stop_reason == "max_usd"
    assert go(tmp_path / "k", tmp_path / "c_k", kill_after=0) is None
    spent = json.loads((tmp_path / "k" / "spend.json").read_text())["segments"]
    assert len(spent) == 1 and spent[0]["usd"] > 0
    resumed = go(tmp_path / "k", tmp_path / "c_k")
    assert resumed.stop_reason == "max_usd"
    assert len(resumed.trajectory) == len(full.trajectory)                  # pre-fix: ran on (budget reset to 0)
    assert resumed.meta["spend"]["prior_segments_usd"] == pytest.approx(spent[0]["usd"])


def test_cache_replay_is_charged_at_the_original_price(tmp_path):
    """A $0 replay of a recorded run binds its USD budget where the original bound it."""
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.rrsi.mocks import AgentQAMockLLM
    suite = make_suite(n_evolve=8, n_holdout=6, n_ood_per_family=2, seed=0)
    res = []
    for name in ("orig", "replay"):
        prop = CachedLLM(_Paid(AgentQAMockLLM()._respond), tmp_path / "cache")
        res.append(run(AgentQADomain(suite), AgentQADomain.seed_artifact(), llm_task=SimModel(suite),
                       llm_propose=prop, budget=Budget(max_usd=0.05), out_dir=tmp_path / name,
                       config=Config(T=8, k=2, workers=2, seed=0, record_timestamps=False, shadow_monitor=False)))
        if name == "replay":
            assert prop.meter.total().cost_usd == 0 and prop.misses == 0
    assert [r["S"] for r in res[0].trajectory] == [r["S"] for r in res[1].trajectory]
    assert res[1].stop_reason == res[0].stop_reason == "max_usd"
