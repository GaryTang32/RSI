"""GEPA audit trace (``rsi.trace`` format): write-only, complete and internally consistent.

* the trace and the shadow monitor never change what the loop decides (same ledger, same
  run log, same result) - with and without ``out_dir``, with the monitor on sealed splits;
* the monitor's model spend is kept out of the loop's usage, so a USD stopper is unaffected;
* every iteration records selection, evaluations, the proposal with its actual diff, the gate
  arithmetic and the decision, and those records agree with the ledger / artifact store.
"""
from __future__ import annotations

import json
from pathlib import Path

from rsi.core import Budget, LeakageCritic
from rsi.core.ledger import ArtifactStore
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.gepa import AgentQAReflectionLM, Config, run, two_module_harness
from rsi.gepa.tracing import ShadowLLM, default_shadow_splits
from rsi.trace import inspect, load_trace

KINDS = {"run_start", "noise", "baseline", "round_start", "note", "analysis", "proposal", "eval", "gate", "decision",
         "state", "monitor", "run_end"}


def decisions(res) -> list:
    return [(n.id, n.parent, n.kind, n.status, n.score, n.artifact_id) for n in res.ledger.nodes()]


def run_log(path: Path) -> str:
    return (path / "run_log.jsonl").read_text()


def rw(out_dir=None, **cfg):
    d = make_domain(seed=0)
    return d, run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world),
                  config=Config(**{"max_metric_calls": 700, "use_merge": True, **cfg}), out_dir=out_dir)


def test_trace_and_monitor_are_write_only_ruleworld(tmp_path):
    _, a = rw(tmp_path / "a")                                  # trace + shadow monitor (sealed test split)
    _, b = rw(tmp_path / "b", shadow_monitor=False)            # trace, no monitor
    _, c = rw(tmp_path / "c", trace=False)                     # no trace at all
    _, e = rw(None)                                            # no run directory
    ev = load_trace(tmp_path / "a")
    assert any(x["kind"] == "monitor" for x in ev)
    assert not any(x["kind"] == "monitor" for x in load_trace(tmp_path / "b"))
    assert not (tmp_path / "c" / "trace.jsonl").exists()
    assert any(x["kind"] == "decision" and x["data"]["event"] == "merge_accepted" for x in ev)
    for other in (b, c, e):
        assert decisions(other) == decisions(a)
        assert other.trajectory == a.trajectory
        assert other.best.id == a.best.id and other.meta["rollouts"] == a.meta["rollouts"]
    assert run_log(tmp_path / "a") == run_log(tmp_path / "b") == run_log(tmp_path / "c")


def _agentqa(out_dir, monitor: bool, budget=None, llm_cls=SimModel):
    suite = make_suite(n_evolve=6, n_val=6, n_holdout=6, n_ood_per_family=2, seed=0)
    dom = AgentQADomain(suite)
    return run(dom, two_module_harness(), llm_task=llm_cls(suite), llm_propose=AgentQAReflectionLM(),
               config=Config(max_metric_calls=90, workers=2, shadow_monitor=monitor, shadow_workers=2),
               out_dir=out_dir, budget=budget)


def test_trace_and_monitor_are_write_only_agentqa_and_spend_is_separate(tmp_path):
    a = _agentqa(tmp_path / "a", True)
    b = _agentqa(tmp_path / "b", False)
    assert decisions(a) == decisions(b) and a.trajectory == b.trajectory
    mon = [x for x in load_trace(tmp_path / "a") if x["kind"] == "monitor"]
    assert mon and set(mon[0]["data"]["sealed"]) == {"holdout", "ood"}
    assert not any(r.startswith("shadow") for r in a.usage)            # loop usage excludes the monitor
    assert a.meta["shadow_usage"]["_total"]["calls"] > 0
    assert b.meta["shadow_usage"]["_total"]["calls"] == 0
    assert a.usage["task"] == b.usage["task"]


class CostlySim(SimModel):
    """SimModel that charges $0.001 per call, so a USD stopper is live."""

    def _complete(self, prompt, *, system, max_tokens, seed):
        r = super()._complete(prompt, system=system, max_tokens=max_tokens, seed=seed)
        r.usage.cost_usd = 0.001
        return r


def test_usd_stopper_never_sees_monitor_spend(tmp_path):
    a = _agentqa(tmp_path / "a", True, Budget(max_usd=0.12), CostlySim)
    b = _agentqa(tmp_path / "b", False, Budget(max_usd=0.12), CostlySim)
    assert a.stop_reason == b.stop_reason == "max_usd"
    assert decisions(a) == decisions(b)
    assert a.meta["shadow_usage"]["_total"]["cost_usd"] > 0.01       # the monitor did spend, off the books


def test_trace_is_complete_and_consistent(tmp_path):
    d, res = rw(tmp_path / "r")
    ev = load_trace(tmp_path / "r")
    assert KINDS <= {x["kind"] for x in ev}
    n_it = res.meta["iterations"]
    assert sum(x["kind"] == "round_start" for x in ev) == n_it == sum(x["kind"] == "state" for x in ev)
    assert sum(x["kind"] == "decision" for x in ev) == n_it
    # every rollout the engine counted appears in exactly one evaluation event
    charged = sum(x["data"]["rollouts_charged"] for x in ev if x["kind"] in ("eval", "baseline"))
    end = next(x["data"] for x in ev if x["kind"] == "run_end")
    assert charged == res.meta["rollouts"] == end["rollouts"]
    store = ArtifactStore(tmp_path / "r" / "artifacts")
    pool = {n.id: n.artifact_id for n in res.ledger.nodes() if n.id.startswith("c")}
    ledger = {n.id: n for n in res.ledger.nodes()}
    for x in ev:
        dd = x["data"]
        if x["kind"] == "gate" and dd["gate"] == "minibatch acceptance":
            m = dd["math"]
            assert dd["accept"] == (sum(m["after"]) > sum(m["before"]))
        if x["kind"] == "gate" and dd["gate"] == "merge acceptance":
            assert dd["accept"] == (dd["math"]["sum_sub_after"] >= dd["math"]["threshold"])
        if x["kind"] == "proposal" and dd.get("proposal_kind") == "reflective" and dd.get("child_artifact"):
            assert store.get(pool[dd["parent"]]).diff(store.get(dd["child_artifact"])) == dd["diff"]
            assert "<curr_param>" not in dd["prompt"] and "I provided an assistant" in dd["prompt"]
        if x["kind"] == "decision":
            if dd["kept"]:
                assert ledger[dd["kept"]].status == "accepted"
            elif dd["event"] in ("rejected", "merge_rejected"):
                assert ledger[dd["proposal"]].status == "rejected"
    # the monitor observed the seed and every change of incumbent, nothing else
    changes = ["c0"] + [x["data"]["incumbent_after"] for x in ev if x["kind"] == "decision"
                        and x["data"]["incumbent_after"] != x["data"]["incumbent_before"]]
    assert [x["data"]["version"] for x in ev if x["kind"] == "monitor"] == changes
    assert changes[-1] == f"c{res.meta['best_idx']}"
    md = Path(inspect(tmp_path / "r")).read_text()
    assert "## Round 0" in md and "Shadow monitor" in md and "Gate on" in md


def test_trace_records_critic_and_hard_budget(tmp_path):
    d = make_domain(seed=0)
    from rsi.domains.ruleworld import ReflectionProfile
    res = run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world, ReflectionProfile(q_copy=0.8)),
              config=Config(max_metric_calls=301, budget_mode="hard"), out_dir=tmp_path,
              critic=LeakageCritic(d.leakage_terms("evolve")))
    ev = load_trace(tmp_path)
    assert any(x["kind"] == "critic" and not x["data"]["accept"] for x in ev)
    assert any(x["kind"] == "decision" and x["data"]["event"] == "critic_rejected" for x in ev)
    assert res.stop_reason == "max_metric_calls(hard)"
    assert any(x["kind"] == "note" and x["data"].get("what") == "hard budget cap" for x in ev)
    assert res.meta["rollouts"] <= 301


def test_resume_appends_to_the_trace(tmp_path):
    d = make_domain(seed=0)
    lm = RuleWorldReflectionLM(d.world)
    run(d, d.seed_artifact(), llm_propose=lm, config=Config(max_metric_calls=None, max_iterations=3), out_dir=tmp_path)
    r2 = run(d, d.seed_artifact(), llm_propose=lm, config=Config(max_metric_calls=None, max_iterations=6),
             out_dir=tmp_path)
    straight = run(d, d.seed_artifact(), llm_propose=lm, config=Config(max_metric_calls=None, max_iterations=6))
    assert decisions(r2) == decisions(straight)
    ev = load_trace(tmp_path)
    assert any(x["kind"] == "note" and x["data"].get("what") == "resume" for x in ev)
    assert sorted({x["round"] for x in ev if x["kind"] == "round_start"}) == list(range(6))


def test_shadow_llm_and_default_splits():
    suite = make_suite(n_evolve=3, n_val=3, n_holdout=3, n_ood_per_family=1, seed=0)
    sim = SimModel(suite)
    sh = ShadowLLM(sim)
    sh.complete("Question: 1+1", role="task")
    assert set(sim.meter.snapshot()) == {"shadow:task", "_total"}
    assert default_shadow_splits(AgentQADomain(suite)) == ["holdout", "ood"]
    assert default_shadow_splits(make_domain(seed=0)) == ["test"]
