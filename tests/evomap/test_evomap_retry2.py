"""Retry round 2: GDI formula, faithful selector rule, hub signal layer, bounties and the git workspace."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from rsi.core import Artifact, FunctionDomain, MockLLM, Task, TaskSuite
from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer
from rsi.domains.geneworld.forge import GeneWorldForge
from rsi.evomap import AgentNode, Config, Gene, LocalStore, NaiveEvoMapHub, PopulationSimulator
from rsi.evomap.economy import (BountyBoard, BountyDriver, BountyTask, dormant_nodes, estimate_capability_match,
                                rank_tasks, refer)
from rsi.evomap.gitws import GitWorkspace
from rsi.evomap.hub import AssetRecord, Bundle, CreditLedger, GDIRanker
from rsi.evomap.mutation import MutationBuilder, PersonalityState
from rsi.evomap.population import AgentSpec
from rsi.evomap.selector import GeneScorer
from rsi.evomap.signals import OPPORTUNITY_SIGNALS, HubSignalLayer, merge_signals
from rsi.evomap.solidify import RunState, Solidifier, blast_radius
from rsi.evomap.validation import CommandPolicy, InProcessExecutor, ValidationRunner

DATA = Path(__file__).parent / "data"


def record(conf=0.9, streak=4, files=2, lines=100, trig=("a", "b", "c"), summary="x" * 100, epoch=0, author="n1"):
    cap = {"confidence": conf, "success_streak": streak, "blast_radius": {"files": files, "lines": lines},
           "outcome": {"status": "success", "score": 0.9}, "trigger": list(trig), "summary": summary}
    b = Bundle(gene={"id": "gene_x", "signals_match": ["a"], "summary": "short", "strategy": ["s"]}, capsule=cap,
               event=None, report={"overall_ok": True})
    return AssetRecord("sha256:x", b, author, "promoted", epoch)


def test_gdi_intrinsic_is_the_behind_evomap_formula():
    r = GDIRanker()
    intrinsic, parts = r.intrinsic(record())
    # 1/6 [clip(C) + min(S/10,1) + max(0, 1 - F*L/1000) + min(T/5,1) + min(l/200,1) + clip(R/100)], R = 50
    assert intrinsic == pytest.approx((0.9 + 0.4 + 0.8 + 0.6 + 0.5 + 0.5) / 6)
    assert set(parts) == {"confidence", "streak", "blast", "trigger", "summary", "reputation"}
    assert r.intrinsic(record(files=5, lines=200))[1]["blast"] == 0.0
    assert GDIRanker(reputation={"n1": 90}).intrinsic(record())[1]["reputation"] == 0.9
    legacy = GDIRanker(variant="legacy")
    assert set(legacy.intrinsic(record())[1]) == {"blast", "confidence", "streak", "score", "report_ok", "substance"}
    assert isinstance(NaiveEvoMapHub().ranker, GDIRanker) and NaiveEvoMapHub().ranker.variant == "be2026"


def test_gdi_freshness_decays_from_the_last_activity():
    rec = record(epoch=0)
    rec.fetches.append(("n2", 8))
    r = GDIRanker()
    assert r.freshness(rec, 10) == pytest.approx(0.5 ** (2 / 10))
    assert GDIRanker(variant="legacy").freshness(rec, 10) == pytest.approx(0.5)
    assert r.freshness(rec, 10, half_life=30) == pytest.approx(0.5 ** (2 / 30))


def test_faithful_mode_uses_the_engine_selector_rule(gw):
    assert Config(mode="faithful").resolved().require_match is False
    assert Config(mode="safe").resolved().require_match is True
    assert Config(mode="faithful", require_match=True).resolved().require_match is True
    ag = AgentNode("a", gw, gw.seed_artifact(), llm_task=GeneWorldModel(0.0), config=Config(mode="faithful"))
    assert ag.selector.scorer.require_match is False
    g = Gene(id="gene_unrelated", signals_match=["zzzqqq"], summary="nothing in common", strategy=["x"],
             learning_history=[{"outcome": "success"}] * 3)
    # no pattern hit and no token overlap: > 0 under the engine rule (history +0.36), 0 under the safe deviation
    assert ag.selector.scorer.score(g, ["task:c01"]) > 0
    ag_safe = AgentNode("b", gw, gw.seed_artifact(), llm_task=GeneWorldModel(0.0), config=Config(mode="safe"))
    assert ag_safe.selector.scorer.score(g, ["task:c01"]) == 0


def test_hub_signal_layer_calls_every_fifth_cycle_with_a_truncated_corpus():
    seen = []

    def analyzer(payload):
        seen.append(payload)
        return {"signals": ["capability_gap", "", "x" * 200, 7] + [f"s{i}" for i in range(20)]}

    layer = HubSignalLayer(analyzer, sender_id="node_a")
    outs = [layer.extract("E" * 5000) for _ in range(11)]
    assert [c["count"] for c in layer.calls] == [1, 6, 11]
    assert all(o == [] for i, o in enumerate(outs) if i not in (0, 5, 10))
    assert outs[0] == ["capability_gap"] + [f"s{i}" for i in range(9)]  # 1..199 chars, at most 10
    assert len(seen[0]["corpus_summary"]) == 2000 and seen[0]["signal_types"] == list(OPPORTUNITY_SIGNALS)
    assert seen[0]["sender_id"] == "node_a"
    assert HubSignalLayer(None).extract("x") == []
    assert HubSignalLayer(lambda p: 1 / 0).extract("x") == []
    assert merge_signals(["a", "b"], ["b", "c"]) == ["a", "b", "c"]


def test_faithful_agent_merges_hub_llm_signals_on_its_first_cycle(gw):
    hub = NaiveEvoMapHub(signal_analyzer=lambda p: {"signals": ["external_opportunity"]})
    ag = AgentNode("a", gw, gw.seed_artifact(), llm_task=GeneWorldModel(0.0), hub=hub,
                   config=Config(mode="faithful", propose=False, distill=False, publish=False))
    t = gw.tasks.split("evolve")[0]
    cr1, cr2 = ag.cycle(t), ag.cycle(t)
    assert "external_opportunity" in cr1.signals and "external_opportunity" not in cr2.signals
    assert ag.llm_signals.count == 2 and len(ag.llm_signals.calls) == 1
    safe = AgentNode("b", gw, gw.seed_artifact(), llm_task=GeneWorldModel(0.0), hub=hub, config=Config(mode="safe"))
    assert safe.llm_signals is None


def test_task_ranking_matches_the_v2_javascript():
    d = json.loads((DATA / "taskreceiver_vectors.json").read_text())
    for c, js in zip(d["cases"], d["js"]):
        ts = [BountyTask(t["task_id"], "p", t.get("bounty_amount"), t["signals"], t["title"], status=t["status"],
                         claimed_by=t.get("claimed_by"), complexity_score=t.get("complexity_score"),
                         historical_completion_rate=t.get("historical_completion_rate"), bounty_id=t.get("bounty_id"))
              for t in c["tasks"]]
        got = rank_tasks(ts, "me", c["mem"], c["strategy"])
        assert [(e["task"].task_id, e["reason"]) for e in got] == [(e["id"], e["reason"]) for e in js["ranked"]]
        for e, j in zip(got, js["ranked"]):
            if j["score"] is not None:
                assert e["score"]["composite"] == pytest.approx(j["score"]["composite"], abs=1e-12)
                assert e["score"]["factors"] == j["score"]["factors"]
        for t, cap in zip(ts, js["caps"]):
            assert estimate_capability_match(t.signals, c["mem"]) == pytest.approx(cap, abs=1e-12)


def test_bounty_escrow_self_reported_completion_expiry_swarm_referral_dormancy():
    cr = CreditLedger()
    board = BountyBoard(cr, reputation={"agg": 70, "low": 40})
    t = board.post("poster", 100, ["task:c01"], epoch=0, expires_in=2)
    assert cr.balance["poster"] == 400 and t.status == "open" and t.bounty_id
    assert board.claim(t.task_id, "w1") and not board.claim(t.task_id, "w2")
    assert not board.complete(t.task_id, "w2", "sha256:a")  # only the claimer
    assert board.complete(t.task_id, "w1", "sha256:any")  # nothing checks the asset solves it
    assert cr.earned("w1") == 100 and t.status == "completed"
    t2 = board.post("poster", 50, ["x"], epoch=0, expires_in=2)
    assert cr.balance["poster"] == 350
    assert board.expire(2) == [t2.task_id] and cr.balance["poster"] == 400
    assert t2.status == "expired" and cr.by_reason()["bounty_refund"] == 50
    t3 = board.post("prop", 1000, ["y"], swarm=True)
    with pytest.raises(ValueError):
        board.complete_swarm(t3.task_id, {"s1": 1}, "low")
    pay = board.complete_swarm(t3.task_id, {"s1": 3, "s2": 1}, "agg")
    assert pay == {"prop": pytest.approx(50), "s1": pytest.approx(637.5), "s2": pytest.approx(212.5),
                   "agg": pytest.approx(100)}
    refer(cr, "old", "new")
    assert cr.earned("old") == 50 and cr.earned("new") == 100
    cr.balance["idle"] = 0.0
    cr.balance["busy"] = 0.0
    assert dormant_nodes(cr, {"idle": 0, "busy": 25, "prop": 30}, 30) == {"idle"}
    assert cr.balance["prop"] < 0 and "prop" in dormant_nodes(cr, {"idle": 0, "busy": 25, "prop": 0}, 30)


def _bounty_run(gw, specs, epochs, forge=None, **driver_kw):
    hub = NaiveEvoMapHub()
    drv = BountyDriver(BountyBoard(hub.credits), per_epoch=3, **driver_kw)
    PopulationSimulator(gw, gw.seed_artifact(), hub, specs, config=Config(mode="faithful", seed=0),
                        model_factory=lambda sp: GeneWorldModel(sp.ability),
                        proposer_factory=lambda sp: GeneWorldProposer(gw.world, sp.insight, 1.0),
                        forge=forge, seed=0, bounties=drv).run(epochs)
    return hub, drv


def test_population_bounty_driver_pays_self_reported_completions(gw):
    hub, drv = _bounty_run(gw, [AgentSpec(f"a{i}_honest", "honest", ability=0.5, insight=0.9) for i in range(4)], 6)
    assert drv.log, "no bounty was completed"
    assert hub.credits.by_reason().get("bounty", 0) == 100 * len(drv.log)
    assert all(drv.board.tasks[e["task"]].status == "completed" for e in drv.log)


def test_bounty_driver_can_let_farmers_self_report_completions(gw):
    """Farmer claimants complete with a fresh capsule id; the default driver never pays a farmer."""
    def farm(**driver_kw):
        specs = [AgentSpec("a0_farmer", "farmer", farm_rate=2),
                 AgentSpec("a1_honest", "honest", ability=0.5, insight=0.9)]
        return _bounty_run(gw, specs, 4, forge=GeneWorldForge(gw.world), **driver_kw)

    _, drv = farm()
    assert not any(e["agent"] == "a0_farmer" for e in drv.log)
    hub, drv = farm(claimant_kinds=("honest", "inflator", "farmer"))
    farmed = [e for e in drv.log if e["agent"] == "a0_farmer"]
    assert len(farmed) == 4  # one self-reported completion per epoch
    assert all(e["asset"] and e["asset"].startswith("sha256:") for e in farmed)
    assert sum(a for _, ag, a, r in hub.credits.history if ag == "a0_farmer" and r == "bounty") == 400


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True).stdout


def test_git_workspace_blast_rollback_and_untracked_cleanup(tmp_path):
    before = {"mod.py": "a = 1\nb = 2\n", "README.md": "hi\n"}
    ws = GitWorkspace.init(tmp_path / "repo", before)
    assert ws.is_git_repo() and ws.changed_files() == []
    (tmp_path / "repo" / "old_untracked.txt").write_text("user file\n")
    after = {**before, "mod.py": "a = 1\nb = 3\nc = 4\n", "pkg/sub/new.py": "x = 1\n", "package.json": "{}\n"}
    runner = ValidationRunner(CommandPolicy.safe(), InProcessExecutor(), mode="safe")
    sol = Solidifier(LocalStore(node_id="n"), runner, mode="safe", require_task_success=True, workspace=ws)
    g = Gene(id="gene_g", signals_match=["x"], summary="s", strategy=["a", "b"], validation=[])
    mut = MutationBuilder().build(["x"], g, innovate_mode=False, personality=PersonalityState())
    rs = RunState("r1", ["x"], g, mut, PersonalityState(), before, after, task_success=False, intent=mut.category)
    res = sol.solidify(rs)
    assert not res.success and res.rolled_back and res.git_rollback == "stash"
    assert "evolver-rollback-" in _git(ws.root, "stash", "list")
    assert (ws.root / "mod.py").read_text() == before["mod.py"]
    assert not (ws.root / "pkg" / "sub" / "new.py").exists()
    # as in Evolver, `stash push --include-untracked` also stashes pre-existing untracked files (recoverable)
    assert not (ws.root / "old_untracked.txt").exists()
    assert _git(ws.root, "show", "stash@{0}^3:old_untracked.txt") == "user file\n"

    ws2 = GitWorkspace.init(tmp_path / "repo2", before)
    ws2.write(after)
    b_git, b_map = ws2.blast_radius(), blast_radius(before, after)
    assert (b_git["files"], b_git["lines"]) == (b_map["files"], b_map["lines"])
    assert "b = 3" in ws2.diff_snapshot()

    ws3 = GitWorkspace.init(tmp_path / "repo3", before)
    base_untracked = ws3.untracked_files()
    ws3.write({"a/b/c.py": "1\n", ".env": "SECRET=1\n", "top.py": "2\n"})
    assert ws3.rollback("hard") == "hard"
    removed = ws3.remove_new_untracked(base_untracked)
    assert sorted(removed["deleted"]) == ["a/b/c.py", "top.py"] and removed["skipped"] == [".env"]
    assert (ws3.root / ".env").exists() and not (ws3.root / "a" / "b").exists() and (ws3.root / "a").exists()

    ws4 = GitWorkspace.init(tmp_path / "repo4", before)
    sol4 = Solidifier(LocalStore(node_id="m"), runner, mode="faithful", require_task_success=False, workspace=ws4)
    rs4 = RunState("r2", ["x"], None, mut, PersonalityState(), before, after, task_success=True, intent=mut.category)
    assert sol4.solidify(rs4).git_rollback is None and (ws4.root / "mod.py").read_text() == after["mod.py"]


def test_agent_runs_inside_a_git_repo_without_a_hub(tmp_path):
    tasks = [Task(f"t{i}", {"x": i}, float(i), "fam") for i in range(6)]
    dom = FunctionDomain(TaskSuite(tasks, {"evolve": [t.id for t in tasks]}),
                         lambda art, task, seed, llm: float(task.input["x"]),
                         lambda task, out: (float(out == task.target), ""), name="echo")
    ws = GitWorkspace.init(tmp_path / "repo", {"README.md": "repo\n"})
    ag = AgentNode("a", dom, Artifact({"agent.md": "echo\n"}), llm_task=MockLLM(lambda *a: "0", name="m"),
                   hub=None, config=Config(mode="faithful", propose=False, distill=False, publish=False),
                   workspace=ws)
    crs = [ag.cycle(t) for t in tasks[:3]]
    assert ag.solidifier.workspace is ws and ws.is_git_repo()
    failed = [cr for cr in crs if not cr.solidified]
    stashes = _git(ws.root, "stash", "list").splitlines()
    assert failed and len(stashes) == len(failed)  # every failed solidify is stashed
    assert all("evolver-rollback-" in s for s in stashes)
    assert _git(ws.root, "show", "stash@{0}^3:task.json")
    assert ws.changed_files() == []


def test_selector_history_penalty_applies_to_soft_mode_only_as_selector_js():
    """selector.js:130-138: success +0.12, mode 'hard' -0.22, mode 'soft' -0.08, any other mode 0."""
    sel = GeneScorer(mode="current")
    base = dict(id="gene_h", signals_match=["x"], summary="s", strategy=["a"])
    g = Gene(**base, learning_history=[{"outcome": "failed", "mode": "none"}, {"outcome": "failed"},
                                       {"outcome": "unknown", "mode": "x"}])
    assert sel.adjustment(g, ["x"], None) == pytest.approx(0.0)
    g2 = Gene(**base, learning_history=[{"outcome": "failed", "mode": "soft"}, {"outcome": "failed", "mode": "hard"},
                                        {"outcome": "success", "mode": "none"}])
    assert sel.adjustment(g2, ["x"], None) == pytest.approx(-0.08 - 0.22 + 0.12)
