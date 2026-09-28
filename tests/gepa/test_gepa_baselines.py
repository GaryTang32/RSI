"""Baselines at equal rollout budget: ScalarRL (GRPO-style), ScoreOnly, BestOfN, FewShot (MIPRO-lite)."""
import numpy as np

from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.gepa import (Config, FewShotConfig, RLConfig, gepa_curve, rollouts_to_target, run, run_best_of_n,
                      run_fewshot, run_scalar_rl, run_score_only, trajectory_curve)


def test_scalar_rl_accounting_and_learning():
    d = make_domain(seed=0)
    res = run_scalar_rl(d, d.seed_artifact(), config=RLConfig(max_metric_calls=3000, lr=2.0, val_every=5))
    per_step = 12 * 4
    assert 3000 <= res.meta["rollouts"] < 3000 + per_step + 2 * len(d.tasks.splits["val"])
    assert res.trajectory[0]["rollouts"] == len(d.tasks.splits["val"])
    curve = trajectory_curve(res, lambda a: d.expected(a, "test"))
    assert curve[-1][1] >= curve[0][1] and res.meta["best_val"] > 0.2
    assert res.ledger.nodes(kind="policy_checkpoint")


def test_rl_brainstorms_vocabulary_without_domain_hook():
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.gepa import AgentQAReflectionLM
    suite = make_suite(n_evolve=4, n_val=4, n_holdout=2, n_ood_per_family=1, seed=0)
    dom = AgentQADomain(suite)
    llm = AgentQAReflectionLM()
    res = run_scalar_rl(dom, AgentQADomain.seed_artifact(), llm_task=SimModel(suite), llm_propose=llm,
                        config=RLConfig(max_metric_calls=60, group_size=4, instances_per_step=2, val_every=2,
                                        vocab_size=6), components=["prompts/system.md"])
    assert res.meta["vocab_sizes"] == {"prompts/system.md": 6}
    assert res.usage["brainstorm"]["calls"] == 1


def test_equal_budget_baselines_run_and_gepa_beats_score_only_on_average():
    gaps = []
    for s in range(4):
        d = make_domain(seed=s)
        L = RuleWorldReflectionLM(d.world)
        cfg = Config(max_metric_calls=1200, seed=s)
        g = run(d, d.seed_artifact(), llm_propose=L, config=cfg)
        so = run_score_only(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world), config=cfg)
        assert so.method == "score_only_reflection" and so.meta["config"]["feedback"] == "score_only"
        gaps.append(d.expected(g.best, "test") - d.expected(so.best, "test"))
        if s == 0:
            bon = run_best_of_n(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world), config=cfg)
            assert bon.meta["rollouts"] <= 1200 and bon.meta["n_rewrites"] >= 30
            fs = run_fewshot(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world),
                             config=FewShotConfig(max_metric_calls=1200))
            assert fs.meta["rollouts"] <= 1200 and fs.meta["trials"] > 10
            assert sum(len(v) for v in fs.best.files.values()) > 0
            c = gepa_curve(g, lambda a: d.expected(a, "test"))
            assert rollouts_to_target(c, 0.5) is not None
    assert np.mean(gaps) > 0.05
