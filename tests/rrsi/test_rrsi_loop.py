"""End-to-end RRSI loop: determinism, resume after a kill, STOP file, infrastructure failures, readjudication,
the proposer's done() contract, critic + repair, and a second domain."""
import json

import pytest

from rsi.core import Artifact, Editor, MockLLM, Proposal
from rsi.domains.agentqa import AgentQADomain
from rsi.domains.harnessworld import CriticProfile, HarnessWorldMockLLM, ProposerProfile
from rsi.rrsi import (Config, Killed, Proposer, RegularizerSwitches, RRSIRun, RRSICritic, RRSIRewriteEditor, Taxonomy,
                      drive, result, run)

from rrsi_helpers import aq_run, edit, hw_domain, hw_loop, mechanism, read_json, read_jsonl, reply, snapshot

KILL_POINTS = (("evaluated", (2, "A")), ("drafted", (3, "B")), ("recorded", (1, "A")), ("recorded", (3, "A")),
               ("settled", (3, None)))
TAX = Taxonomy(["prompt", "memory"], ["memory"], {"prompt": ["prompts/*"], "memory": ["memory/*"]})


def test_end_to_end_result_and_ledger(tmp_path):
    out = tmp_path / "a"
    dom, r = hw_loop(out)
    stop = drive(r)
    res = result(r, stop)
    assert stop == "max_rounds" and len(res.trajectory) == 6
    assert res.meta["delta"] > 0 and res.meta["calibration"]["method"].startswith("bootstrap")
    assert "proposer" in res.usage and "critic" in res.usage and res.usage["_total"]["calls"] > 0
    nodes = res.ledger.nodes()
    assert nodes[0].id == "H0" and all(n.parent is not None for n in nodes[1:])
    assert {n.status for n in nodes[1:]} <= {"ACCEPTED", "LOST", "REJECTED", "critic_reject", "smoke_fail",
                                             "eval_invalid", "no_proposal"}
    recs = read_jsonl(out / "history.jsonl")
    assert recs[0]["outcome"] == "BASELINE"
    for rec in recs[1:]:
        assert {"t", "variant", "edit_id", "component", "hypothesis", "diff", "delta_S", "delta_C", "accepted",
                "outcome", "bundle", "detail"} <= set(rec)
        assert rec["component"] in dom.components
    for t in range(5):
        b_t = read_json(out / f"r{t}" / "directives.json")["b_t"]
        assert all(len(read_json(out / f"r{t}" / v / "prep.json")["edits"]) <= b_t for v in "AB")
    assert res.best.id == read_json(out / "frontier.json")["incumbent"]["artifact_id"]


def test_deterministic_and_kill_resume_identical(tmp_path):
    drive(hw_loop(tmp_path / "full")[1])
    ref = snapshot(tmp_path / "full")
    for event, when in KILL_POINTS:
        d = tmp_path / f"kill_{event}_{when[0]}"

        def hook(ev, t=None, variant=None, **kw):
            if (t, variant if when[1] else None) == when:
                raise Killed(f"killed at {ev} {t}{variant}")
        with pytest.raises(Killed):
            drive(hw_loop(d, hooks={event: hook})[1])
        assert drive(hw_loop(d)[1]) == "max_rounds"       # a fresh process: new objects, same directory
        assert snapshot(d) == ref, event


def test_stop_file_and_infra_limit(tmp_path):
    _, r = hw_loop(tmp_path / "s")
    (tmp_path / "s").mkdir(exist_ok=True)
    (tmp_path / "s" / "STOP").write_text("")
    assert drive(r) == "stop_file"
    assert r.frontier.settled_rounds() == 0

    class Broken(RRSIRewriteEditor):
        def edit(self, *a, **k):
            raise RuntimeError("backend down")
    dom = hw_domain()
    r = RRSIRun(dom, dom.seed_artifact(), out_dir=tmp_path / "i", editor=Broken(MockLLM()),
                config=Config(T=5, workers=1))
    assert drive(r) == "infra_failures"
    assert len(list((tmp_path / "i" / "logs").glob("r0.err"))) == 1


def test_readjudicate_changes_only_algorithm2(tmp_path):
    out, t = tmp_path / "r", 2
    _, r = hw_loop(out, T=4)
    drive(r)
    r.truncate(t)
    before = read_json(out / f"r{t}" / "decisions.json")
    rollouts = r.measurer.n_rollouts
    after = [d.to_json() for d in r.readjudicate(t, r.cfg.replace(delta=0.5))]   # a much wider band
    assert r.measurer.n_rollouts == rollouts
    keys = ("S", "C", "delta_S", "delta_C")
    assert all([b[k] for k in keys] == [a[k] for k in keys] for b, a in zip(before, after))
    assert (out / f"r{t}" / "decisions.orig.json").exists()
    fr = read_json(out / "frontier.json")
    assert len(fr["trajectory"]) == t + 2 and fr["readjudications"][-1]["delta"] == 0.5
    assert all("[re-adjudicated" in rec["detail"] for rec in read_jsonl(out / "history.jsonl")
               if rec.get("t") == t and rec.get("delta_S") is not None)
    drive(r, T=4)
    assert r.frontier.settled_rounds() == 4


def test_done_contract_bounces():
    prompt_file = {"prompts/a.md": "x\n"}
    replies = [
        '```json\n{"action": "abort", "reason": "nothing to do"}\n```',                    # no abort action
        reply(prompt_file, [edit(id="C1"), edit(id="C2"), edit(id="C3")], summary="s"),     # over budget
        reply(prompt_file, [edit(targets_mode="")], summary="s"),                           # missing field
        reply(prompt_file, [edit("bogus")], summary="s"),                                   # not in K
        reply(prompt_file, [edit()], summary="s"),                                          # reserved slot ignored
        reply({"memory/m.py": "M = 1\n"}, [edit("memory")], summary="s"),                   # valid
    ]
    llm = MockLLM(lambda p, s, seed, i: replies[min(i, len(replies) - 1)])
    base = Artifact({"prompts/a.md": "a\n"})
    out = Proposer(RRSIRewriteEditor(llm), TAX, Config(max_done_bounces=5)).propose(
        base, directives={}, budget=2, reserved=True, explore={"untried": ["memory"]})
    assert out["status"] == "done" and out["edits"][0]["component"] == "memory"
    assert out["log"][0].get("action") == "abort" and len(out["log"]) == 6
    assert "memory/m.py" in out["artifact"]
    llm2 = MockLLM(lambda p, s, seed, i: reply({}, [edit()]))                          # declared edits, no files
    out2 = Proposer(RRSIRewriteEditor(llm2), TAX, Config(max_done_bounces=2)).propose(base, directives={}, budget=2)
    assert out2["status"] == "max_turns" and "ZERO file changes" in llm2.calls[1]["prompt"]


def test_critic_denylist_llm_and_fail_closed():
    dom = hw_domain()
    base = dom.seed_artifact()

    def diff(kind):
        m = mechanism(dom, kind)
        return base.diff(base.with_files({m.path: m.file_text()}))
    crit = RRSICritic(dom, HarnessWorldMockLLM(dom.world, critic=CriticProfile(catch_rate=1.0)))
    v = crit.review(diff("leak"))
    assert v["verdict"] == "reject" and v["stage"] == "precheck"
    v = crit.review(diff("obfuscated_leak"))
    assert v["verdict"] == "reject" and v["stage"] == "llm"
    assert crit.review(diff("generic"))["verdict"] == "accept"
    assert crit.review("")["verdict"] == "reject"
    assert RRSICritic(dom, MockLLM(lambda *a: "no json here")).review(diff("generic"))["verdict"] == "reject"


def test_repair_loop_turns_leak_into_clean_candidate(tmp_path):
    dom = hw_domain(2)
    shares = {k: 0.0 for k in ("generic", "structural", "narrow", "null", "costly", "harmful", "decaying")}
    shares.update({"leak": 1.0, "obfuscated_leak": 0.0, "generic": 0.0001})
    llm = HarnessWorldMockLLM(dom.world, proposer=ProposerProfile(shares=shares, fill_budget_p=0.0))
    res = run(dom, dom.seed_artifact(), llm_propose=llm, config=Config(T=2, workers=1), out_dir=tmp_path / "rep")
    crit = [read_json(p) for p in sorted((tmp_path / "rep").glob("r*/*/critic_a0.json"))]
    assert crit and all(c["verdict"] == "reject" and c["stage"] == "precheck" for c in crit)
    assert any((tmp_path / "rep").glob("r*/*/proposal_r1.json"))
    assert "leak" not in [dom.world.kind_of(m) for m in dom.mechanisms(res.best)]


def test_unregularized_keeps_leaks_rrsi_blocks(tmp_path):
    dom = hw_domain(3)
    out = {}
    for arm, sw in (("un", RegularizerSwitches.none()), ("full", RegularizerSwitches.full())):
        res = run(dom, dom.seed_artifact(), llm_propose=HarnessWorldMockLLM(dom.world),
                  config=Config(T=6, workers=1), out_dir=tmp_path / arm, switches=sw)
        out[arm] = [dom.world.kind_of(m) for m in dom.mechanisms(res.best)]
        assert (res.method == "rrsi") == (arm == "full")
    assert out["un"].count("leak") >= 1 and out["full"].count("leak") == 0


def test_agentqa_second_domain(tmp_path):
    _, res = aq_run(tmp_path / "aq", heldout_monitor=True)
    assert len(res.trajectory) == 4 and res.usage["task"]["calls"] > 0
    assert "memory/answers.json" not in res.best                      # the leaky lookup table never survives
    if any(r.get("winner") for r in res.trajectory[1:]):
        mon = read_jsonl(tmp_path / "aq" / "heldout_monitor.jsonl")
        assert mon and "S_holdout" in mon[0]
    tags = {rec.get("component") for rec in read_jsonl(tmp_path / "aq" / "history.jsonl")}
    assert tags - {None} <= set(AgentQADomain.components)


def test_agent_editor_done_file():
    class FakeAgent(Editor):
        def edit(self, artifact, instructions, **kw):
            assert "_rrsi_done.json" in instructions
            done = json.dumps({"action": "done", "summary": "s", "edits": [edit("memory")]})
            return Proposal(artifact.with_files({"memory/m.py": "M = 1\n", "_rrsi_done.json": done}))
    out = Proposer(FakeAgent(), TAX, Config()).propose(Artifact({"prompts/a.md": "a\n"}), directives={}, budget=1)
    assert out["status"] == "done" and "_rrsi_done.json" not in out["artifact"]
    assert out["edits"][0]["component"] == "memory"
