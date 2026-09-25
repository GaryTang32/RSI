"""GEPA engine end-to-end (offline, deterministic): budget identity, acceptance, merge,
determinism, kill/resume, saturation, sealed splits, AgentQA + quick-start API."""
import json

import pytest

from rsi.core import Artifact, SealedSplitError
from rsi.domains.ruleworld import RuleWorldReflectionLM, make_domain
from rsi.gepa import Config, DomainAdapter, GenericReflectionLM, optimize, run, tree_metrics


def _run(seed=0, B=600, **kw):
    d = make_domain(seed=seed)
    cfg_kw = {k: v for k, v in kw.items() if k in Config.__dataclass_fields__}
    other = {k: v for k, v in kw.items() if k not in Config.__dataclass_fields__}
    res = run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world),
              config=Config(max_metric_calls=B, seed=seed, **cfg_kw), **other)
    return d, res


def test_budget_identity_and_overshoot():
    d, res = _run(B=700)
    st, b, V = res.state, 3, len(d.tasks.splits["val"])
    ev = [e.get("event") for e in st.trace]
    parent_evals = sum(1 for e in st.trace if "subsample_scores" in e)
    child_evals = sum(1 for e in st.trace if "new_subsample_scores" in e)
    accepted = ev.count("accepted")
    assert st.counter.total == V + b * parent_evals + b * child_evals + V * accepted
    assert st.counter.by_phase["val_reflective"] == V * accepted
    assert 700 <= st.counter.total < 700 + 2 * b + V                      # soft cap: boundary-check overshoot
    assert res.meta["rollouts_by_phase"]["seed_val"] == V


def test_hard_budget_never_exceeds():
    d, res = _run(B=701, budget_mode="hard")
    assert res.state.counter.total <= 701 and res.stop_reason.startswith("max_metric_calls")


def test_strict_acceptance_and_best_is_argmax_val():
    d, res = _run(B=900)
    st = res.state
    for e in st.trace:
        if e.get("event") == "accepted":
            assert sum(e["new_subsample_scores"]) > sum(e["subsample_scores"])
            assert st.parents[e["new_program_idx"]] == [e["selected_program_candidate"]]
        if e.get("event") == "rejected":
            assert sum(e["new_subsample_scores"]) <= sum(e["subsample_scores"])
    agg = st.agg_scores()
    assert res.best == st.candidates[agg.index(max(agg))]
    assert res.meta["best_val"] > res.meta["seed_val"]
    assert d.expected(res.best, "test") > d.expected(res.baseline, "test") + 0.3
    # ledger: one node per candidate + one per rejected proposal, lineage intact
    nodes = res.ledger.nodes()
    assert len([n for n in nodes if n.status in ("accepted", "keep")]) == len(st.candidates)
    assert len([n for n in nodes if n.status == "rejected"]) == [e.get("event") for e in st.trace].count("rejected")
    lin = res.ledger.lineage(f"c{st.best_idx()}")
    assert lin[0].id == "c0"
    tm = tree_metrics(st)
    assert tm["n_candidates"] == len(st.candidates) and tm["max_depth"] >= 1


def test_skip_perfect_and_saturation_returns_seed():
    d = make_domain(seed=1, slip=0.0)
    oracle = d.oracle_artifact()
    res = run(d, oracle, llm_propose=RuleWorldReflectionLM(d.world), config=Config(max_metric_calls=300))
    assert res.best == oracle and res.meta["n_accepted"] == 0
    assert all(e["event"] == "skip_perfect" for e in res.state.trace)
    res2 = run(d, oracle, llm_propose=RuleWorldReflectionLM(d.world),
               config=Config(max_metric_calls=300, skip_perfect_score=False))
    assert res2.best == oracle and res2.meta["n_accepted"] == 0 and res2.meta["n_proposals"] > 0


def test_merge_soft_cap_can_exceed_and_hard_cap_holds():
    soft_max, found = 0, False
    for s in range(6):
        d, res = _run(seed=s, B=2500, use_merge=True, max_merge_invocations=1)
        st = res.state
        merged = [k for k, kind in enumerate(st.kinds) if kind == "merge"]
        for k in merged:
            assert len(st.parents[k]) == 2
            e = next(x for x in st.trace if x.get("new_program_idx") == k)
            assert sum(e["new_program_subsample_scores"]) >= max(e["id1_subsample_score"], e["id2_subsample_score"])
            assert len(e["subsample_ids"]) == 5
        soft_max = max(soft_max, len(merged))
        found = found or len(merged) > 0
        dh, resh = _run(seed=s, B=2500, use_merge=True, max_merge_invocations=1, merge_cap_mode="hard")
        assert resh.meta["n_merges_accepted"] <= 1
    assert found and soft_max > 1          # reference soft cap: accepted merges can exceed max_merge_invocations


def test_determinism_same_seed_identical_runs(tmp_path):
    _, a = _run(seed=3, B=600, out_dir=tmp_path / "a")
    _, b = _run(seed=3, B=600, out_dir=tmp_path / "b")
    assert [c.id for c in a.state.candidates] == [c.id for c in b.state.candidates]
    assert (tmp_path / "a" / "run_log.jsonl").read_text() == (tmp_path / "b" / "run_log.jsonl").read_text()
    strip = lambda L: [{k: v for k, v in n.to_json().items() if k != "t"} for n in L.nodes()]
    assert strip(a.ledger) == strip(b.ledger)


class _Kill(Exception):
    pass


def test_kill_and_resume_matches_uninterrupted(tmp_path):
    _, full = _run(seed=4, B=800, use_merge=True, out_dir=tmp_path / "full")

    def killer(event, payload):
        if event == "iteration_start" and payload["i"] == 11:
            raise _Kill()
    with pytest.raises(_Kill):
        _run(seed=4, B=800, use_merge=True, out_dir=tmp_path / "part", callbacks=[killer])
    _, resumed = _run(seed=4, B=800, use_merge=True, out_dir=tmp_path / "part")
    assert resumed.meta["resumed_at"] == 10
    assert [c.id for c in resumed.state.candidates] == [c.id for c in full.state.candidates]
    assert resumed.state.val_scores == full.state.val_scores
    assert (tmp_path / "part" / "run_log.jsonl").read_text() == (tmp_path / "full" / "run_log.jsonl").read_text()
    assert resumed.state.counter.by_phase == full.state.counter.by_phase
    s_full = json.loads((tmp_path / "full" / "state.json").read_text())
    s_part = json.loads((tmp_path / "part" / "state.json").read_text())
    s_full["extra"].pop("usage"), s_part["extra"].pop("usage")
    assert s_full == s_part


def test_sealed_splits_never_reach_the_optimizer():
    d = make_domain(seed=0)
    ad = DomainAdapter(d)
    with pytest.raises(SealedSplitError):
        ad.ids("test")
    with pytest.raises(PermissionError):
        ad.evaluate(d.tasks.splits["test"][:2], d.seed_artifact(), False, [0, 0])
    _, res = _run(B=300, report_splits=("test",))
    rep = res.meta["report"]["splits"]["test"]
    assert set(rep) == {"seed", "best"} and rep["best"]["S"] > rep["seed"]["S"]


def test_score_only_strips_feedback_text():
    d = make_domain(seed=0, feedback="rich")
    ad = DomainAdapter(d, feedback="score_only")
    ids = d.tasks.splits["evolve"][:3]
    eb = ad.evaluate(ids, d.seed_artifact(), True, [1, 2, 3])
    ds = ad.make_reflective_dataset(d.seed_artifact(), eb, ["prompts/triage.md"])
    for rec in ds["prompts/triage.md"]:
        assert rec["Feedback"].startswith("Score:") and "Rule" not in rec["Feedback"]


def test_quick_start_optimize_on_new_problem():
    # a formatter whose prompt must state conventions; the metric explains failures
    rules = {"date": "Write dates as YYYY-MM-DD.", "money": "Write money with two decimals.",
             "name": "Write names in Title Case."}

    def metric(cand, ex):
        text = cand["instructions.md"]
        ok = [k for k in ex["needs"] if rules[k] in text]
        miss = [k for k in ex["needs"] if k not in ok]
        fb = " ".join(f"{rules[k]}" for k in miss) or "Correct."
        return len(ok) / len(ex["needs"]), fb, f"formatted with {len(ok)} of {len(ex['needs'])} conventions"

    data = [{"needs": ["date", "money"]}, {"needs": ["name"]}, {"needs": ["money", "name"]}, {"needs": ["date"]}] * 3
    res = optimize({"instructions.md": "Format the record."}, data[:6], data[6:], metric=metric,
                   llm_propose=GenericReflectionLM(), config=Config(max_metric_calls=200), testset=data[:4])
    assert res.meta["best_val"] == 1.0
    assert res.meta["report"]["splits"]["test"]["best"]["S"] == 1.0


def test_agentqa_two_module_harness_improves():
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.gepa import AgentQAReflectionLM, two_module_harness
    suite = make_suite(n_evolve=6, n_val=6, n_holdout=6, n_ood_per_family=2, seed=0)
    dom = AgentQADomain(suite)
    res = run(dom, two_module_harness(), llm_task=SimModel(suite), llm_propose=AgentQAReflectionLM(),
              config=Config(max_metric_calls=70, workers=4))
    assert res.meta["components"] == ["prompts/reporter.md", "prompts/solver.md"]
    assert "harness.py" not in res.meta["components"]
    assert res.best["harness.py"] == two_module_harness()["harness.py"]         # control flow stays frozen
    assert res.meta["best_val"] >= res.meta["seed_val"]
    assert res.usage["reflection"]["calls"] >= 1 and res.usage["task"]["calls"] > 0


def test_optional_leakage_critic_blocks_ticket_copies_before_evaluation():
    from rsi.core import LeakageCritic
    from rsi.domains.ruleworld import ReflectionProfile
    d = make_domain(seed=0)
    critic = LeakageCritic(d.leakage_terms("evolve"))
    res = run(d, d.seed_artifact(), llm_propose=RuleWorldReflectionLM(d.world, ReflectionProfile(q_copy=0.8)),
              config=Config(max_metric_calls=600), critic=critic)
    ev = [e.get("event") for e in res.state.trace]
    assert ev.count("critic_rejected") > 0
    assert d.world.count_facts(res.best.files) == 0          # no ticket-specific line survived
    assert all(n.status == "critic_rejected" for n in res.ledger.nodes() if n.status == "critic_rejected")
