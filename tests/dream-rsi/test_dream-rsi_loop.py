"""Dream-RSI loop: replay fidelity to the recorded rollout, logging, selection, developers and baselines."""
import json

import numpy as np
import pytest

from rsi.core import Ledger, MockLLM, RewriteEditor
from rsi.domains.discovery import SumDiffDomain, SyntheticConfig, SyntheticDomain
from rsi.dream import (Config, DevContext, DiscoveryTree, GuardedSelector, LLMPolicyDeveloper, MockGuidanceSummarizer,
                       ParametricMutator, PolicyReport, ReplayEvaluator, Selector, VersionRecord, choose_default_beta,
                       code_of, mock_developer_llm, parallel_refine, run, template_code)


def small_cfg(**kw) -> Config:
    return Config(**{"rounds": 3, "W": 3, "branch_count": 4, "refine_count": 3, "M": 3, "sandbox": "inprocess",
                     "seed": 3, **kw})


def report(vals: list[float]) -> PolicyReport:
    return PolicyReport("p", "", "eq1", float(np.mean(vals)), list(vals), [])


@pytest.mark.parametrize("policy_name,root_mode", [("parallel_refine", "earliest"), ("adaptive", "addressable"),
                                                   ("adaptive", "earliest")])
def test_replay_of_recording_policy_reproduces_online_rollout(policy_name, root_mode):
    """Invariant (a): same revealed set, N, k, per-round batches and best score."""
    code = template_code(policy_name)
    res = run(SyntheticDomain(SyntheticConfig(seed=11)), config=small_cfg(dream=False), initial_policy=code)
    ev = ReplayEvaluator(W=3, fallback=(4, 3), runner="inprocess", root_mode=root_mode)
    for world, man in zip(res.meta["worlds"], res.meta["manifests"]):
        e = ev.evaluate(code, [world], manifests=res.meta["manifests"]).episodes[0]
        assert e.reveal_round == {n.id: n.round for n in world.non_root()}
        assert (e.N, e.k) == (man["probe_work"], man["decision_rounds"])
        assert e.batch_sizes == man["batch_sizes"]
        assert e.best == pytest.approx(man["round_best"])


def test_dream_run_logs_everything_and_selection_is_safe(tmp_path):
    out = tmp_path / "run"
    res = run(SyntheticDomain(SyntheticConfig(seed=5)), config=small_cfg(M=4), out_dir=str(out))
    rows = [r["dream"] for r in res.trajectory if "dream" in r]
    assert len(rows) == 2
    for r in rows:  # invariant (c): V^{m*} >= V^0 on replay
        assert r["delta_vs_incumbent"] >= -1e-12 and len(r["values"]) == 4
    for rel in ("discovery.jsonl", "policies.jsonl", "trajectory.json", "final_policy/method.py",
                "trace_pool/iter01/tree.json", "trace_pool/iter01/live_cycle_manifest.json",
                "history/baseline/method.py", "history/r0000_initial/method.py"):
        assert (out / rel).exists(), rel
    traces = list(out.glob("history/r*/proposal_results/policy_execution_traces.jsonl"))
    assert list(out.glob("history/r*/proposal_results/beta_sweep.json")) and traces
    row = json.loads(traces[0].read_text().splitlines()[0])
    assert {"world", "beta", "rounds"} <= set(row) and row["rounds"][0]["batch"]
    assert len(Ledger(out / "discovery.jsonl")) == sum(len(w) for w in res.meta["worlds"])
    assert DiscoveryTree.load(out / "trace_pool/iter01/tree.json").size == res.trajectory[0]["calls"]
    cost = res.usage["_cost"]
    assert cost["agent_calls"] == sum(r["calls"] for r in res.trajectory)
    assert cost["replay_episodes"] > 0 and cost["developer_calls"] == 0
    assert res.best != res.baseline and res.meta["best_score"] > res.meta["seed_score"]


def test_fixed_baseline_never_changes_policy_and_shares_round_one():
    dom = SyntheticDomain(SyntheticConfig(seed=7))
    fixed, dream = run(dom, config=small_cfg(dream=False)), run(dom, config=small_cfg(dream=True))
    assert len({r["policy"] for r in fixed.trajectory}) == 1
    assert fixed.trajectory[0]["calls"] == dream.trajectory[0]["calls"] == 4 * 4
    assert fixed.trajectory[0]["round_best"] == dream.trajectory[0]["round_best"]
    assert fixed.usage["_cost"]["replay_episodes"] == 0


def test_llm_developer_path_offline():
    llm = mock_developer_llm()
    res = run(SyntheticDomain(SyntheticConfig(seed=2)), config=small_cfg(rounds=2), developer=LLMPolicyDeveloper(llm))
    values = res.trajectory[0]["dream"]["values"]
    assert len(values) == 3 and all(v > -1 for v in values)
    assert res.usage["_cost"]["developer_calls"] == 2
    prompt = llm.calls[0]["prompt"]
    for phrase in ("prefix-only exploration policy", "history/baseline/method.py", "policy_execution_traces.jsonl",
                   "live_cycle_manifest.json"):
        assert phrase in prompt


def test_llm_developer_rejects_leaky_or_invalid_code():
    leaky = template_code("adaptive").replace('NAME = "OptimalPolicy"', 'NAME = "OptimalPolicy"\nBEST = "b3.a2"')
    llm = MockLLM(lambda *a: '```json\n{"change": "x"}\n```\n=== FILE: method.py ===\n' + leaky)
    rec = VersionRecord(0, code_of(parallel_refine()))
    ctx = DevContext(1, [rec], [], [], code_of(parallel_refine()), "eq1", 3, forbidden_terms=["b3.a2"])
    rev = LLMPolicyDeveloper(RewriteEditor(llm), repair_rounds=1).revise(ctx)
    assert not rev.ok and "b3.a2" in rev.error and len(llm.calls) == 2


def test_mutator_rewrites_params_and_default_beta_rule():
    ctx = DevContext(1, [VersionRecord(0, code_of(parallel_refine()))], [], [], code_of(parallel_refine()), "eq1", 3)
    rev = ParametricMutator().revise(ctx, seed=1)
    assert rev.ok and "PARAMS" in rev.code and "rewrite" in rev.change
    sweep = {"points": [{"beta": 0.6, "attainment": 0.7, "probes_frac": 0.4, "V_eq1": 0.5},
                        {"beta": 0.8, "attainment": 0.9, "probes_frac": 0.5, "V_eq1": 0.55}]}
    plateau, improving = [{"final_best": 1.0}, {"final_best": 1.0}], [{"final_best": 1.0}, {"final_best": 1.2}]
    b, why = choose_default_beta(0.6, plateau, sweep)
    assert b == pytest.approx(0.75) and "raise" in why
    assert choose_default_beta(0.6, improving, sweep)[0] == pytest.approx(0.8)  # a clearly better nearby beta
    b, why = choose_default_beta(0.6, improving, {"points": [dict(p, V_eq1=0.5) for p in sweep["points"]]})
    assert b == pytest.approx(0.6) and "keep" in why
    assert choose_default_beta(None, [], None)[0] == 0.6


def test_selectors():
    reps = [report([0.5, 0.5, 0.5]), report([0.6, 0.6, 0.4]), report([0.4, 0.4, 0.4])]
    assert Selector().select(reps).index == 1
    assert Selector().select([report([0.5]), report([0.4])]).index == 0  # incumbent kept
    assert Selector(include_incumbent=False).select([report([0.5]), report([0.4])]).index == 1
    g = GuardedSelector(holdout_frac=0.34, margin_floor=0.01)
    assert g.holdout_worlds(3) == [2]
    sel = g.select(reps)  # best on dev worlds 0 and 1, but loses on held-out world 2
    assert sel.index == 0 and sel.details["top_dev"] == 1
    assert g.select([report([0.5, 0.5, 0.5]), report([0.6, 0.6, 0.6])]).index == 1


def test_guarded_loop_and_guidance_variant():
    dom = SyntheticDomain(SyntheticConfig(seed=9))
    assert "dream" in run(dom, config=small_cfg(selector="guarded")).trajectory[-2]
    g = run(dom, config=small_cfg(guidance=True, dream=False), summarizer=MockGuidanceSummarizer())
    assert any(t.get("advised") for w in g.meta["worlds"][1:] for t in w.branch_tags.values())
    assert g.method == "fixed+guidance"


def test_max_calls_budget_and_real_domain():
    res = run(SumDiffDomain(sandboxed=False), config=small_cfg(rounds=5, max_calls=25))
    assert res.usage["_cost"]["agent_calls"] <= 25
    assert res.meta["best_score"] >= res.meta["seed_score"]


def test_subprocess_sandbox_loop():
    res = run(SyntheticDomain(SyntheticConfig(seed=4)), config=small_cfg(rounds=2, M=2, sandbox="subprocess"))
    assert "dream" in res.trajectory[0] and res.trajectory[1]["calls"] > 0
