"""End-to-end RRSI loop: determinism, resume after a kill, STOP file, infrastructure-failure
limit, readjudication, the proposer's done() contract, critic + repair, and a second domain."""
import json

import pytest

from rsi.core import Artifact, MockLLM
from rsi.domains.harnessworld import CriticProfile, HarnessWorldMockLLM, ProposerProfile, make_domain
from rsi.rrsi import (Config, Killed, Proposer, RegularizerSwitches, RRSIRun, RRSICritic, RRSIRewriteEditor, Taxonomy,
                      drive, result, run)

SMALL = dict(n_evolve=24, n_holdout=24, n_ood_per_family=6)


def _run(tmp, seed=0, T=5, sw=None, hooks=None, **cfg):
    dom = make_domain(seed=seed, **SMALL)
    llm = HarnessWorldMockLLM(dom.world)
    c = Config(T=T, workers=1, seed=seed, record_timestamps=False, **cfg)
    r = RRSIRun(dom, dom.seed_artifact(), out_dir=tmp, llm_propose=llm, config=c,
                switches=sw or RegularizerSwitches.full(), hooks=hooks)
    return dom, r


def _state(d):
    from rsi.core import Ledger
    fr = json.loads((d / "frontier.json").read_text())
    nodes = {n.id: {k: v for k, v in n.to_json().items() if k != "t"} for n in Ledger(d / "ledger.jsonl").nodes()}
    return {"history": (d / "history.jsonl").read_text(), "attribution": (d / "attribution.jsonl").read_text(),
            "ledger": nodes,
            "trajectory": fr["trajectory"], "S_star": fr["S_star"],
            "decisions": {p.parent.name: p.read_text() for p in sorted(d.glob("r*/decisions.json"))}}


def test_end_to_end_result_and_ledger(tmp_path):
    dom, r = _run(tmp_path / "a")
    stop = drive(r)
    res = result(r, stop)
    assert stop == "max_rounds" and len(res.trajectory) == 6
    assert res.meta["delta"] > 0 and res.meta["calibration"]["method"].startswith("bootstrap")
    assert "proposer" in res.usage and "critic" in res.usage and res.usage["_total"]["calls"] > 0
    nodes = res.ledger.nodes()
    assert nodes[0].id == "H0" and all(n.parent is not None for n in nodes[1:])
    statuses = {n.status for n in nodes[1:]}
    assert statuses <= {"ACCEPTED", "LOST", "REJECTED", "critic_reject", "smoke_fail", "eval_invalid", "no_proposal"}
    recs = [json.loads(l) for l in (tmp_path / "a" / "history.jsonl").read_text().splitlines()]
    assert recs[0]["outcome"] == "BASELINE"
    for rec in recs[1:]:
        assert {"t", "variant", "edit_id", "component", "hypothesis", "diff", "delta_S", "delta_C", "accepted",
                "outcome", "bundle", "detail"} <= set(rec)
        assert rec["component"] in dom.components
    # budget respected
    for t in range(5):
        dirs = json.loads((tmp_path / "a" / f"r{t}" / "directives.json").read_text())
        for v in "AB":
            prep = json.loads((tmp_path / "a" / f"r{t}" / v / "prep.json").read_text())
            assert len(prep["edits"]) <= dirs["b_t"]
    assert res.best.id == json.loads((tmp_path / "a" / "frontier.json").read_text())["incumbent"]["artifact_id"]


def test_deterministic_and_kill_resume_identical(tmp_path):
    _, r1 = _run(tmp_path / "full")
    drive(r1)
    ref = _state(tmp_path / "full")

    for event, when in (("evaluated", (2, "A")), ("drafted", (3, "B")), ("recorded", (1, "A")), ("recorded", (3, "A")),
                        ("settled", (3, None))):
        d = tmp_path / f"kill_{event}_{when[0]}"

        def hook(ev, t=None, variant=None, **kw):
            if (t, variant if when[1] else None) == when:
                raise Killed(f"killed at {ev} {t}{variant}")
        _, r2 = _run(d, hooks={event: hook})
        with pytest.raises(Killed):
            drive(r2)
        _, r3 = _run(d)                       # a fresh process: new objects, same directory
        assert drive(r3) == "max_rounds"
        assert _state(d) == ref, event


def test_stop_file_and_infra_limit(tmp_path):
    dom, r = _run(tmp_path / "s")
    (tmp_path / "s").mkdir(exist_ok=True)
    (tmp_path / "s" / "STOP").write_text("")
    assert drive(r) == "stop_file"
    assert r.frontier.settled_rounds() == 0          # baseline done, no round run

    class Broken(RRSIRewriteEditor):
        def edit(self, *a, **k):
            raise RuntimeError("backend down")
    dom = make_domain(seed=0, **SMALL)
    r = RRSIRun(dom, dom.seed_artifact(), out_dir=tmp_path / "i", editor=Broken(MockLLM()), config=Config(T=5, workers=1))
    assert drive(r) == "infra_failures"
    assert len(list((tmp_path / "i" / "logs").glob("r0.err"))) == 1


def test_readjudicate_changes_only_algorithm2(tmp_path):
    dom, r = _run(tmp_path / "r", T=4)
    drive(r)
    t = 2
    r.truncate(t)
    before = json.loads((tmp_path / "r" / f"r{t}" / "decisions.json").read_text())
    rollouts = r.measurer.n_rollouts
    decs = r.readjudicate(t, r.cfg.replace(delta=0.5))          # a much wider band
    after = [d.to_json() for d in decs]
    assert r.measurer.n_rollouts == rollouts                    # no new evaluation spent
    for b, a in zip(before, after):
        assert (b["S"], b["C"], b["delta_S"], b["delta_C"]) == (a["S"], a["C"], a["delta_S"], a["delta_C"])
    assert (tmp_path / "r" / f"r{t}" / "decisions.orig.json").exists()
    fr = json.loads((tmp_path / "r" / "frontier.json").read_text())
    assert len(fr["trajectory"]) == t + 2 and fr["readjudications"][-1]["delta"] == 0.5
    assert all("[re-adjudicated" in rec["detail"] for rec in map(json.loads, (tmp_path / "r" / "history.jsonl")
               .read_text().splitlines()) if rec.get("t") == t and rec.get("delta_S") is not None)
    drive(r, T=4)                                               # the run can continue afterwards
    assert r.frontier.settled_rounds() == 4


# --------------------------------------------------------------------- done() contract
def _prop_reply(edits, files):
    return "```json\n" + json.dumps({"action": "done", "summary": "s", "edits": edits}) + "\n```\n" + \
        "\n".join(f"=== FILE: {p} ===\n{t}" for p, t in files.items())


def _edit(i, comp="prompt", **kw):
    e = {"id": f"C{i}", "component": comp, "hypothesis": "h", "targets_mode": "m", "predicted_affected": ["t1"],
         "retroactive_check": "(corrective) (preservative) (transfer)"}
    e.update(kw)
    return e


def test_done_contract_bounces():
    replies = [
        '```json\n{"action": "abort", "reason": "nothing to do"}\n```',                   # bounced: no abort
        _prop_reply([_edit(1), _edit(2), _edit(3)], {"prompts/a.md": "x\n"}),              # over budget
        _prop_reply([_edit(1, targets_mode="")], {"prompts/a.md": "x\n"}),                 # missing field
        _prop_reply([_edit(1, comp="bogus")], {"prompts/a.md": "x\n"}),                    # not in K
        _prop_reply([_edit(1)], {"prompts/a.md": "x\n"}),                                  # reserved slot ignored
        _prop_reply([_edit(1, comp="memory")], {"memory/m.py": "M = 1\n"}),                # valid
    ]
    llm = MockLLM(lambda p, s, seed, i: replies[min(i, len(replies) - 1)])
    tax = Taxonomy(["prompt", "memory"], ["memory"], {"prompt": ["prompts/*"], "memory": ["memory/*"]})
    prop = Proposer(RRSIRewriteEditor(llm), tax, Config(max_done_bounces=5))
    base = Artifact({"prompts/a.md": "a\n"})
    out = prop.propose(base, directives={}, budget=2, reserved=True, explore={"untried": ["memory"]})
    assert out["status"] == "done" and out["edits"][0]["component"] == "memory"
    errs = [l.get("action") for l in out["log"]]
    assert errs[0] == "abort" and len(out["log"]) == 6
    assert "memory/m.py" in out["artifact"]
    # zero file changes with declared edits is bounced; exhausted bounces -> max_turns
    llm2 = MockLLM(lambda p, s, seed, i: '```json\n' + json.dumps({"action": "done", "edits": [_edit(1)]}) + '\n```')
    out2 = Proposer(RRSIRewriteEditor(llm2), tax, Config(max_done_bounces=2)).propose(base, directives={}, budget=2)
    assert out2["status"] == "max_turns" and "ZERO file changes" in llm2.calls[1]["prompt"]


def test_critic_denylist_llm_and_fail_closed():
    dom = make_domain(seed=0, **SMALL)
    base = dom.seed_artifact()
    leak = next(m for m in dom.world.catalog.values() if m.kind == "leak")
    obf = next(m for m in dom.world.catalog.values() if m.kind == "obfuscated_leak")
    gen = next(m for m in dom.world.catalog.values() if m.kind == "generic" and m.id not in dom.world.seed_ids)
    crit = RRSICritic(dom, HarnessWorldMockLLM(dom.world, critic=CriticProfile(catch_rate=1.0)))
    v = crit.review(base.diff(base.with_files({leak.path: leak.file_text()})))
    assert v["verdict"] == "reject" and v["stage"] == "precheck"
    assert crit.review(base.diff(base.with_files({obf.path: obf.file_text()})))["stage"] == "llm"
    assert crit.review(base.diff(base.with_files({obf.path: obf.file_text()})))["verdict"] == "reject"
    assert crit.review(base.diff(base.with_files({gen.path: gen.file_text()})))["verdict"] == "accept"
    # removing leaked lines is not a leak (denylist scans added lines only)
    leaky = base.with_files({leak.path: leak.file_text()})
    assert crit.precheck(leaky.diff(base)) == []
    assert crit.review("")["verdict"] == "reject"
    bad = RRSICritic(dom, MockLLM(lambda *a: "no json here"))
    assert bad.review(base.diff(base.with_files({gen.path: gen.file_text()})))["verdict"] == "reject"   # fails closed


def test_repair_loop_turns_leak_into_clean_candidate(tmp_path):
    dom = make_domain(seed=2, **SMALL)
    shares = {k: 0.0 for k in ("generic", "structural", "narrow", "null", "costly", "harmful", "decaying")}
    shares.update({"leak": 1.0, "obfuscated_leak": 0.0, "generic": 0.0001})
    llm = HarnessWorldMockLLM(dom.world, proposer=ProposerProfile(shares=shares, fill_budget_p=0.0))
    res = run(dom, dom.seed_artifact(), llm_propose=llm, config=Config(T=2, workers=1), out_dir=tmp_path / "rep")
    crit = [json.loads(p.read_text()) for p in sorted((tmp_path / "rep").glob("r*/*/critic_a0.json"))]
    assert crit and all(c["verdict"] == "reject" and c["stage"] == "precheck" for c in crit)
    assert any((tmp_path / "rep").glob("r*/*/proposal_r1.json"))
    kinds = [dom.world.kind_of(m) for m in dom.mechanisms(res.best)]
    assert "leak" not in kinds


def test_unregularized_keeps_leaks_rrsi_blocks(tmp_path):
    dom = make_domain(seed=3, **SMALL)
    out = {}
    for arm, sw in (("un", RegularizerSwitches.none()), ("full", RegularizerSwitches.full())):
        res = run(dom, dom.seed_artifact(), llm_propose=HarnessWorldMockLLM(dom.world),
                  config=Config(T=6, workers=1), out_dir=tmp_path / arm, switches=sw)
        out[arm] = [dom.world.kind_of(m) for m in dom.mechanisms(res.best)]
        assert (res.method == "rrsi") == (arm == "full")
    assert out["un"].count("leak") >= 1 and out["full"].count("leak") == 0


def test_agentqa_second_domain(tmp_path):
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    from rsi.rrsi.mocks import AgentQAMockLLM
    suite = make_suite(n_evolve=8, n_holdout=6, n_ood_per_family=2)
    dom = AgentQADomain(suite)
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=SimModel(suite), llm_propose=AgentQAMockLLM(),
              config=Config(T=3, workers=2, heldout_monitor=True), out_dir=tmp_path / "aq")
    assert len(res.trajectory) == 4 and res.usage["task"]["calls"] > 0
    assert "memory/answers.json" not in res.best                      # the leaky lookup table never survives
    if any(r.get("winner") for r in res.trajectory[1:]):
        mon = (tmp_path / "aq" / "heldout_monitor.jsonl").read_text().splitlines()
        assert mon and "S_holdout" in json.loads(mon[0])
    tags = {json.loads(l).get("component") for l in (tmp_path / "aq" / "history.jsonl").read_text().splitlines()}
    assert tags - {None} <= set(AgentQADomain.components)


def test_agent_editor_done_file():
    from rsi.core import Editor, Proposal

    class FakeAgent(Editor):
        def edit(self, artifact, instructions, **kw):
            assert "_rrsi_done.json" in instructions
            done = json.dumps({"action": "done", "summary": "s", "edits": [_edit(1, comp="memory")]})
            return Proposal(artifact.with_files({"memory/m.py": "M = 1\n", "_rrsi_done.json": done}))
    tax = Taxonomy(["prompt", "memory"], ["memory"], {"prompt": ["prompts/*"], "memory": ["memory/*"]})
    out = Proposer(FakeAgent(), tax, Config()).propose(Artifact({"prompts/a.md": "a\n"}), directives={}, budget=1)
    assert out["status"] == "done" and "_rrsi_done.json" not in out["artifact"]
    assert out["edits"][0]["component"] == "memory"
