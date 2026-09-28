"""Dream-RSI loop: fidelity of replay to the recorded rollout, selection, developer paths, baselines."""
import json

import pytest

from rsi.core import Ledger
from rsi.domains.discovery import SumDiffDomain, SyntheticConfig, SyntheticDomain
from rsi.dream import (Config, DevContext, DiscoveryTree, GuardedSelector, LLMPolicyDeveloper, MockGuidanceSummarizer,
                       ParametricMutator, ReplayEvaluator, Selector, VersionRecord, adaptive, choose_default_beta,
                       code_of, mock_developer_llm, parallel_refine, run, template_code)
from rsi.dream.evaluator import PolicyReport


def small_cfg(**kw):
    base = dict(rounds=3, W=3, branch_count=4, refine_count=3, M=3, sandbox="inprocess", seed=3)
    base.update(kw)
    return Config(**base)


@pytest.mark.parametrize("policy_name,root_mode", [("parallel_refine", "earliest"), ("adaptive", "addressable"),
                                                   ("adaptive", "earliest")])
def test_replay_of_recording_policy_reproduces_online_rollout(policy_name, root_mode):
    """Invariant (a): same revealed set, N, k, per-round batches and best score."""
    dom = SyntheticDomain(SyntheticConfig(seed=11))
    code = template_code(policy_name)
    res = run(dom, config=small_cfg(dream=False), initial_policy=code)
    ev = ReplayEvaluator(W=3, fallback=(4, 3), runner="inprocess", root_mode=root_mode)
    for world, man in zip(res.meta["worlds"], res.meta["manifests"]):
        rep = ev.evaluate(code, [world], manifests=res.meta["manifests"])
        e = rep.episodes[0]
        online = {n.id: n.round for n in world.non_root()}
        assert e.reveal_round == online
        assert (e.N, e.k) == (man["probe_work"], man["decision_rounds"])
        assert e.batch_sizes == man["batch_sizes"]
        assert e.best == pytest.approx(man["round_best"])


def test_dream_run_logs_everything_and_selection_is_safe(tmp_path):
    dom = SyntheticDomain(SyntheticConfig(seed=5))
    res = run(dom, config=small_cfg(rounds=3, M=4), out_dir=str(tmp_path / "run"))
    rows = [r for r in res.trajectory if "dream" in r]
    assert len(rows) == 2
    for r in rows:                              # invariant (c): V^{m*} >= V^0 on replay
        assert r["dream"]["delta_vs_incumbent"] >= -1e-12
        assert len(r["dream"]["values"]) == 4
    out = tmp_path / "run"
    for rel in ("discovery.jsonl", "policies.jsonl", "trajectory.json", "final_policy/method.py",
                "trace_pool/iter01/tree.json", "trace_pool/iter01/live_cycle_manifest.json",
                "history/baseline/method.py", "history/r0000_initial/method.py"):
        assert (out / rel).exists(), rel
    sweeps = list(out.glob("history/r*/proposal_results/beta_sweep.json"))
    traces = list(out.glob("history/r*/proposal_results/policy_execution_traces.jsonl"))
    assert sweeps and traces
    row = json.loads(traces[0].read_text().splitlines()[0])
    assert {"world", "beta", "rounds"} <= set(row) and row["rounds"][0]["batch"]
    led = Ledger(out / "discovery.jsonl")
    assert len(led) == sum(len(w) for w in res.meta["worlds"])
    world = DiscoveryTree.load(out / "trace_pool/iter01/tree.json")
    assert world.size == res.trajectory[0]["calls"]
    cost = res.usage["_cost"]
    assert cost["agent_calls"] == sum(r["calls"] for r in res.trajectory)
    assert cost["replay_episodes"] > 0 and cost["developer_calls"] == 0
    assert res.best != res.baseline and res.meta["best_score"] > res.meta["seed_score"]


def test_fixed_baseline_never_changes_policy_and_shares_round_one():
    dom = SyntheticDomain(SyntheticConfig(seed=7))
    fixed = run(dom, config=small_cfg(dream=False))
    dream = run(dom, config=small_cfg(dream=True))
    assert len({r["policy"] for r in fixed.trajectory}) == 1
    assert fixed.trajectory[0]["calls"] == dream.trajectory[0]["calls"] == 4 * 4
    assert fixed.trajectory[0]["round_best"] == dream.trajectory[0]["round_best"]
    assert fixed.usage["_cost"]["replay_episodes"] == 0


def test_llm_developer_path_offline():
    dom = SyntheticDomain(SyntheticConfig(seed=2))
    llm = mock_developer_llm()
    res = run(dom, config=small_cfg(rounds=2, M=3), developer=LLMPolicyDeveloper(llm))
    d = res.trajectory[0]["dream"]
    assert len(d["values"]) == 3 and all(v > -1 for v in d["values"])
    assert res.usage["_cost"]["developer_calls"] == 2
    prompt = llm.calls[0]["prompt"]
    assert "prefix-only exploration policy" in prompt and "history/baseline/method.py" in prompt
    assert "policy_execution_traces.jsonl" in prompt and "live_cycle_manifest.json" in prompt


def test_llm_developer_rejects_leaky_or_invalid_code():
    rec = VersionRecord(0, code_of(parallel_refine()))
    rec.report = None

    class Leaky:
        calls = 0

        def complete(self, prompt, **kw):
            from rsi.core.llm import LLMResponse, Usage
            Leaky.calls += 1
            code = template_code("adaptive").replace('NAME = "OptimalPolicy"',
                                                     'NAME = "OptimalPolicy"\nBEST = "b3.a2"')
            return LLMResponse('```json\n{"change": "x"}\n```\n=== FILE: method.py ===\n' + code, Usage(1), "m")

    from rsi.core import RewriteEditor
    dev = LLMPolicyDeveloper(RewriteEditor(Leaky()), repair_rounds=1)
    ctx = DevContext(1, [rec], [], [], code_of(parallel_refine()), "eq1", 3, forbidden_terms=["b3.a2"])
    rev = dev.revise(ctx)
    assert not rev.ok and "b3.a2" in rev.error and Leaky.calls == 2


def test_mutator_rewrites_params_and_default_beta_rule():
    rec = VersionRecord(0, code_of(parallel_refine()))
    ctx = DevContext(1, [rec], [], [], code_of(parallel_refine()), "eq1", 3)
    rev = ParametricMutator().revise(ctx, seed=1)
    assert rev.ok and "PARAMS" in rev.code and "rewrite" in rev.change
    sweep = {"points": [{"beta": 0.6, "attainment": 0.7, "probes_frac": 0.4, "V_eq1": 0.5},
                        {"beta": 0.8, "attainment": 0.9, "probes_frac": 0.5, "V_eq1": 0.55}]}
    b, why = choose_default_beta(0.6, [{"final_best": 1.0}, {"final_best": 1.0}], sweep)
    assert b == pytest.approx(0.75) and "raise" in why
    b, _ = choose_default_beta(0.6, [{"final_best": 1.0}, {"final_best": 1.2}], sweep)
    assert b == pytest.approx(0.8)              # still improving, but a clearly better nearby beta
    flat = {"points": [dict(p, V_eq1=0.5) for p in sweep["points"]]}
    b, why = choose_default_beta(0.6, [{"final_best": 1.0}, {"final_best": 1.2}], flat)
    assert b == pytest.approx(0.6) and "keep" in why
    assert choose_default_beta(None, [], None)[0] == 0.6


def _report(vals):
    import numpy as np
    return PolicyReport("p", "", "eq1", float(np.mean(vals)), list(vals), [])


def test_selectors():
    reps = [_report([0.5, 0.5, 0.5]), _report([0.6, 0.6, 0.4]), _report([0.4, 0.4, 0.4])]
    assert Selector().select(reps).index == 1
    assert Selector().select([_report([0.5]), _report([0.4])]).index == 0          # incumbent kept
    assert Selector(include_incumbent=False).select([_report([0.5]), _report([0.4])]).index == 1
    g = GuardedSelector(holdout_frac=0.34, margin_floor=0.01)
    assert g.holdout_worlds(3) == [2]
    sel = g.select(reps)             # best on dev worlds (0, 1) but loses on held-out world 2
    assert sel.index == 0 and sel.details["top_dev"] == 1
    reps2 = [_report([0.5, 0.5, 0.5]), _report([0.6, 0.6, 0.6])]
    assert g.select(reps2).index == 1


def test_guarded_loop_and_guidance_variant():
    dom = SyntheticDomain(SyntheticConfig(seed=9))
    res = run(dom, config=small_cfg(rounds=3, selector="guarded"))
    assert "dream" in res.trajectory[-2]
    g = run(dom, config=small_cfg(rounds=3, guidance=True, dream=False), summarizer=MockGuidanceSummarizer())
    tags = [w.branch_tags for w in g.meta["worlds"]]
    assert any(t.get("advised") for tt in tags[1:] for t in tt.values())
    assert g.method == "fixed+guidance"


def test_max_calls_budget_and_real_domain():
    dom = SumDiffDomain(sandboxed=False)
    res = run(dom, config=small_cfg(rounds=5, max_calls=25))
    assert res.usage["_cost"]["agent_calls"] <= 25
    assert res.meta["best_score"] >= res.meta["seed_score"]


def test_subprocess_sandbox_loop():
    dom = SyntheticDomain(SyntheticConfig(seed=4))
    res = run(dom, config=small_cfg(rounds=2, M=2, sandbox="subprocess"))
    assert "dream" in res.trajectory[0] and res.trajectory[1]["calls"] > 0
