"""Offline, deterministic tests of the autoresearch core (landscape task, no subprocesses)."""
import csv
import shutil
import threading

import pytest

from rsi.autoresearch import (Analyzer, AutoresearchLoop, BootstrapRigorKeep, Config, CrashPolicy, GateKeep,
                              KeepContext, LandscapeTask, MockResearchAgent, ProgramSpec,
                              ResultsLog, RunOutcome, Samples, ScopeGuard, SimplicityWeighted, StrictKeep, Workspace,
                              landscape_edit_pool, parse_summary, run, scripted_llm)
from rsi.autoresearch.agent import get_knob, knob_edit, set_knob
from rsi.core import Artifact, ArtifactStore, RRSIGate


# ----------------------------------------------------------------------------- small units
def test_parse_summary_crash_signal():
    log = "step 1\n---\nval_bpb:          0.997900\npeak_vram_mb:     45060.2\ndepth: 8\n"
    s = parse_summary(log, "val_bpb")
    assert s["val_bpb"] == pytest.approx(0.9979) and s["peak_vram_mb"] == pytest.approx(45060.2)
    assert parse_summary("Traceback ...\nValueError: x\n", "val_bpb") is None      # empty grep = crash


def test_results_tsv_exact_format(tmp_path):
    log = ResultsLog(tmp_path / "results.tsv", metric_name="val_bpb")
    log.init()
    log.append("a1b2c3d4e5", 0.9979, 44.0, "keep", "baseline")
    log.append("b2c3d4e", 0.9932, 44.2, "keep", "increase LR\tto 0.04")
    log.append("c3d4e5f", 1.005, 44.0, "discard", "switch to GeLU activation")
    log.append("d4e5f6g", 1.23, 99.0, "crash", "double model width (OOM)")
    with pytest.raises(ValueError):
        log.append("x", 1.0, 1.0, "rejected", "not a valid status")
    text = (tmp_path / "results.tsv").read_text().splitlines()
    assert text[0] == "commit\tval_bpb\tmemory_gb\tstatus\tdescription"
    assert text[1] == "a1b2c3d\t0.997900\t44.0\tkeep\tbaseline"
    assert text[2] == "b2c3d4e\t0.993200\t44.2\tkeep\tincrease LR to 0.04"
    assert text[4] == "d4e5f6g\t0.000000\t0.0\tcrash\tdouble model width (OOM)"
    rows = list(csv.reader(open(tmp_path / "results.tsv"), delimiter="\t"))
    assert all(len(r) == 5 for r in rows)
    assert ResultsLog.read(tmp_path / "results.tsv").rows()[2].status == "discard"


def test_workspace_chain_reset_rewind(tmp_path):
    ws = Workspace(ArtifactStore(tmp_path / "store"))
    a0, a1, a2 = Artifact({"train.py": "A=0\n"}), Artifact({"train.py": "A=1\n"}), Artifact({"train.py": "A=2\n"})
    s0 = ws.init_run("mar5", a0)
    assert ws.branch == "autoresearch/mar5"
    with pytest.raises(RuntimeError):
        ws.init_run("mar5", a0)                  # a run tag must be fresh
    s1 = ws.commit(a1, "keep me")
    s2 = ws.commit(a2, "discard me")
    ws.reset_to(s1)
    assert ws.head() == s1 and ws.artifact() == a1
    assert [c.message for c in ws.log()] == ["keep me", "baseline"]
    assert s2 in ws.commits                      # still in the object store
    ws.rewind(s0)
    assert ws.n_rewinds == 1 and ws.artifact() == a0


def test_workspace_git_backend(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    ws = Workspace(ArtifactStore(tmp_path / "store"), backend="git", root=tmp_path / "repo")
    s0 = ws.init_run("t1", Artifact({"train.py": "A=0\n"}))
    ws.commit(Artifact({"train.py": "A=1\n"}), "exp")
    ws.reset_to(s0)
    assert (tmp_path / "repo" / "train.py").read_text() == "A=0\n"
    assert len(s0) == 40


def test_scope_guard():
    sealed = {"prepare.py": "LOCKED\n"}
    g = ScopeGuard(("train.py",), ("prepare.py",), sealed=sealed)
    base = Artifact({"prepare.py": "LOCKED\n", "train.py": "x=1\n"})
    assert g.check(base, base.with_files({"train.py": "x=2\n"})) == []
    v = g.check(base, base.with_files({"prepare.py": "HACKED\n", "train.py": "x=2\n"}))
    assert [x.kind for x in v] == ["locked_edit"]
    v = g.check(base, base.with_files({"notes/new.py": "y\n"}))
    assert v and v[0].kind == "out_of_scope"
    bad = base.with_files({"prepare.py": "HACKED\n"})
    assert g.locked_hashes_ok(bad) == ["prepare.py"] and g.locked_hashes_ok(g.restore(bad)) == []


def test_crash_policy_kinds():
    p = CrashPolicy(max_fix_attempts=2)
    assert p.kind(RunOutcome(1.0)) == "ok"
    assert p.kind(RunOutcome(None, log='  File "train.py"\nSyntaxError: invalid syntax\n')) == "trivial"
    assert p.kind(RunOutcome(None, log="NameError: name 'Fraction' is not defined\n")) == "trivial"
    assert p.kind(RunOutcome(None, log="MemoryError: Unable to allocate\n")) == "oom"
    assert p.kind(RunOutcome(None, log="FAIL\n", returncode=1)) == "nan"
    assert p.kind(RunOutcome(None, killed=True, crash_reason="killed: timeout")) == "timeout"
    assert p.kind(RunOutcome(None, crash_reason="non-causal model: ...")) == "violation"
    assert p.should_fix("trivial", 1) and not p.should_fix("trivial", 2) and not p.should_fix("oom", 0)


def test_keep_rules():
    ctx = KeepContext(direction="min")
    inc = Samples([1.00])
    assert StrictKeep().decide(Samples([0.999]), inc, ctx).accept
    assert not StrictKeep().decide(Samples([1.00]), inc, ctx).accept          # equal => reset
    assert StrictKeep().decide(Samples([0.81]), Samples([0.80]), KeepContext(direction="max")).accept
    r = BootstrapRigorKeep()
    assert (r.repeats, r.confidence, r.gate.reps) == (3, 0.95, 20000)
    assert r.early_reject(1.01, Samples([1.0, 1.0, 1.0]), "min")               # clear loser after run 1
    assert r.decide(Samples([0.90, 0.91, 0.90]), Samples([1.0, 1.01, 0.99]), ctx).accept
    assert not r.decide(Samples([0.99, 1.02, 0.98]), Samples([1.0, 1.01, 0.99]), ctx).accept
    s = SimplicityWeighted(eps=0.001, lines_per_eps=20)
    assert not s.decide(Samples([0.999]), inc, KeepContext("min", lines_added=21, lines_removed=1)).accept
    assert s.decide(Samples([0.999]), inc, KeepContext("min", lines_added=0, lines_removed=10)).accept
    assert s.decide(Samples([1.0005]), inc, KeepContext("min", lines_added=0, lines_removed=10)).accept
    g = GateKeep(RRSIGate())
    v = g.decide(Samples([0.95], memory_gb=10), Samples([1.0], memory_gb=10), KeepContext("min", best=1.0, delta=0.01))
    assert v.accept
    v = g.decide(Samples([1.02], memory_gb=10), Samples([1.0], memory_gb=10), KeepContext("min", best=1.0, delta=0.01))
    assert not v.accept


def test_program_spec_versioning():
    p = ProgramSpec.preset("upstream")
    q = p.edit(p.text + "\nTry smaller models first.\n")
    assert q.version != p.version and q.parent == p.version and q.author == "human"
    text = q.render(LandscapeTask(), "hardened")
    assert "{{" not in text and "val_loss" in text and "`train.py`" in text and "NEVER STOP" in text
    assert "lowest val_loss" in text


def test_knob_helpers():
    t = "LR = 0.04  # peak\nBATCH = 256\nNAME = 'tanh'\n"
    assert get_knob(t, "LR") == 0.04 and get_knob(t, "NAME") == "tanh"
    assert set_knob(t, "BATCH", 128).splitlines()[1] == "BATCH = 128"
    e = knob_edit("LR", lambda v: v * 2, hi=0.05)
    assert e.apply({"train.py": t}) is None                    # out of bounds -> not applicable
    ups, desc = knob_edit("BATCH", lambda v: v // 2).apply({"train.py": t})
    assert desc == "BATCH 256 -> 128" and "BATCH = 128" in ups["train.py"]
    ups, desc = knob_edit("LEAVES", lambda v: v * 0.5).apply({"train.py": "LEAVES = 31\n"})
    assert ups["train.py"] == "LEAVES = 16\n"                   # integer knobs stay integers


# ----------------------------------------------------------------------------- the loop
def _loop_run(tmp_path, name, **cfg):
    kw = dict(max_experiments=25, plot=False)
    kw.update(cfg)
    return run(LandscapeTask(seed=1), config=Config(**kw), out_dir=tmp_path / name, mock={"crash_rate": 0.1})


def test_loop_end_to_end(tmp_path):
    res = _loop_run(tmp_path, "a", plot=True, reeval_seeds=3)
    rows = ResultsLog.read(res.meta["results_tsv"]).rows()
    assert rows[0].status == "keep" and rows[0].description == "baseline"
    assert len(rows) == 26 and {r.status for r in rows} <= {"keep", "discard", "crash"}
    for r in rows:
        if r.status == "crash":
            assert r.metric == 0.0 and r.memory_gb == 0.0
    kept = [n for n in res.ledger.nodes() if n.status == "keep"]
    assert res.best.id == kept[-1].artifact_id                     # HEAD = last kept commit
    assert res.meta["workspace"]["head"] == kept[-1].meta["commit"]
    # strict: every keep improves on the previous keep
    vals = [n.score for n in kept]
    assert all(b < a for a, b in zip(vals, vals[1:]))
    # tree: every candidate's parent is a kept node
    keep_ids = {n.id for n in kept}
    assert all(n.parent in keep_ids for n in res.ledger.nodes() if n.kind == "candidate")
    assert all(n.meta["program_version"] for n in res.ledger.nodes() if n.kind != "baseline" or True)
    a = res.meta["analysis"]
    assert a["keep_rate"] == pytest.approx(a["n_keep"] / (a["n_keep"] + a["n_discard"]))
    assert a["total_keep_delta"] == pytest.approx(a["improvement"])
    assert (tmp_path / "a" / "progress.png").exists() and (tmp_path / "a" / "groundtruth_all.tsv").exists()
    audit = res.meta["audit"]
    assert len(audit) == len(kept) and {"test_iid", "test_shift", "truth"} <= set(audit[0])
    assert res.meta["reeval"]["final"]["n"] == 3 and "optimism_gap" in res.meta["reeval"]


def test_loop_deterministic(tmp_path):
    r1 = _loop_run(tmp_path, "d1")
    r2 = _loop_run(tmp_path, "d2")
    t1 = [(r.metric, r.status, r.description) for r in ResultsLog.read(r1.meta["results_tsv"]).rows()]
    t2 = [(r.metric, r.status, r.description) for r in ResultsLog.read(r2.meta["results_tsv"]).rows()]
    assert t1 == t2


def test_stop_file_halts(tmp_path):
    class Stopper(MockResearchAgent):
        def propose(self, ctx):
            if self.n_proposals == 4:
                (tmp_path / "s" / "STOP").write_text("")
            return super().propose(ctx)

    task = LandscapeTask()
    loop = AutoresearchLoop(task, Stopper(landscape_edit_pool()), Config(max_experiments=None, plot=False,
                                                                          hidden_audit=False), out_dir=tmp_path / "s")
    res = loop.run()
    assert res.stop_reason == "stop_file" and loop.n_rounds == 5


def test_faithful_vs_hardened_grader_exploit(tmp_path):
    task = LandscapeTask()
    for mode in ("faithful", "hardened"):
        ag = MockResearchAgent(landscape_edit_pool(), schedule=["exploit_grader"])
        res = AutoresearchLoop(task, ag, Config(max_experiments=1, mode=mode, plot=False, hidden_audit=False),
                               out_dir=tmp_path / mode).run()
        node = res.ledger.nodes()[-1]
        if mode == "faithful":
            assert node.status == "keep" and node.score < node.metrics["truth"] - 0.03    # bogus best
        else:
            assert node.status == "rejected" and res.meta["scope"]["rejected"] == 1
            assert ResultsLog.read(res.meta["results_tsv"]).rows()[-1].status == "discard"


def test_trivial_crash_is_fixed_and_rerun(tmp_path):
    ag = MockResearchAgent(landscape_edit_pool(), schedule=["typo", "oom_width", "hang", "nan_lr"])
    res = AutoresearchLoop(LandscapeTask(), ag, Config(max_experiments=4, plot=False, hidden_audit=False),
                           out_dir=tmp_path / "c").run()
    nodes = [n for n in res.ledger.nodes() if n.kind == "candidate"]
    assert nodes[0].status != "crash" and nodes[0].meta["fix_attempts"] == 1
    assert [n.meta["crash_kind"] for n in nodes[1:]] == ["oom", "timeout", "nan"]
    assert res.meta["counters"]["fixed"] == 1


def test_rigor_keep_rule_runs_repeats_and_never_repeats(tmp_path):
    task = LandscapeTask()
    ag = MockResearchAgent(landscape_edit_pool(), schedule=["batch_down", "batch_up", "batch_down", "width_down"])
    loop = AutoresearchLoop(task, ag, Config(max_experiments=4, keep_rule="rigor", plot=False, hidden_audit=False),
                            out_dir=tmp_path / "r")
    res = loop.run()
    base = res.ledger.nodes()[0]
    assert len(base.metrics["samples"]) == 3
    for n in res.ledger.nodes()[1:]:
        if n.status == "keep":
            assert len(n.metrics["samples"]) == 3 and n.meta["verdict_details"]["p_better"] >= 0.95
    # batch_down/batch_up/batch_down always recreates an already-scored file -> never re-scored
    assert res.meta["counters"]["duplicate"] >= 1
    assert any(n.status == "duplicate" for n in res.ledger.nodes())


def test_program_md_edited_by_human_mid_run(tmp_path):
    prog = tmp_path / "program.md"
    ProgramSpec.preset().save(prog)

    class Human(MockResearchAgent):
        def propose(self, ctx):
            if self.n_proposals == 3:
                prog.write_text(prog.read_text() + "\nPrefer smaller models.\n")
            return super().propose(ctx)

    res = AutoresearchLoop(LandscapeTask(), Human(landscape_edit_pool()),
                           Config(max_experiments=6, program=str(prog), plot=False, hidden_audit=False),
                           out_dir=tmp_path / "p").run()
    versions = [n.meta["program_version"] for n in res.ledger.nodes() if n.kind == "candidate"]
    assert len(set(versions)) == 2 and len(res.meta["programs"]) == 2


def test_llm_agent_offline_and_metered(tmp_path):
    llm = scripted_llm(landscape_edit_pool(), seed=0)
    res = run(LandscapeTask(), llm_propose=llm, config=Config(max_experiments=5, plot=False, hidden_audit=False),
              out_dir=tmp_path / "l")
    assert len(ResultsLog.read(res.meta["results_tsv"]).rows()) == 6
    assert res.usage["_total"]["calls"] >= 5 and "scripted-researcher:researcher" in res.usage
    assert "NEVER STOP" in llm.calls[0]["prompt"] and "results.tsv" in llm.calls[0]["prompt"]


def test_analyzer_from_results(tmp_path):
    log = ResultsLog(tmp_path / "r.tsv", "val_bpb")
    log.init()
    for c, m, s in [("a", 1.0, "keep"), ("b", 0.99, "keep"), ("c", 1.2, "discard"), ("d", 0, "crash"),
                    ("e", 0.97, "keep")]:
        log.append(c, m, 1.0, s, f"exp {c}")
    a = Analyzer.from_results(tmp_path / "r.tsv", "min").summary()
    assert a["keep_rate"] == pytest.approx(3 / 4) and a["n_crash"] == 1
    assert a["best"] == pytest.approx(0.97) and a["total_keep_delta"] == pytest.approx(0.03)
    assert a["improvement_pct"] == pytest.approx(3.0)


# ----------------------------------------------------------------------------- parallel / executors
def test_parallel_local_and_fake_slurm(tmp_path):
    for ex in ("local", "slurm"):
        res = run(LandscapeTask(sleep_s=0.02), config=Config(max_experiments=8, workers=3, executor=ex, plot=False,
                                                             hidden_audit=False),
                  out_dir=tmp_path / ex, mock={"crash_rate": 0.2})
        rows = ResultsLog.read(res.meta["results_tsv"]).rows()
        assert len(rows) == 9 and res.meta["parallel"]["workers"] == 3
        kept = [n for n in res.ledger.nodes() if n.status == "keep"]
        assert res.best.id == kept[-1].artifact_id


def test_fake_slurm_states_and_sbatch(tmp_path):
    from rsi.autoresearch.executors import FakeSlurmExecutor

    task = LandscapeTask()
    pool = {e.name: e for e in landscape_edit_pool()}
    ex = FakeSlurmExecutor(nodes=1, spool_dir=tmp_path / "spool", container_image="img:1")
    base = task.seed_artifact()
    arts = {n: base.with_files(pool[n].apply(base.files)[0]) for n in ("oom_width", "nan_lr", "hang", "batch_down")}
    ids = {n: ex.submit(task, a, mode="hardened") for n, a in arts.items()}
    outs = {n: ex.wait(j) for n, j in ids.items()}
    assert ex.sacct(ids["oom_width"]) == "OUT_OF_MEMORY" and ex.sacct(ids["nan_lr"]) == "FAILED"
    assert ex.sacct(ids["hang"]) == "TIMEOUT" and ex.sacct(ids["batch_down"]) == "COMPLETED"
    assert outs["batch_down"].metric is not None and outs["hang"].crashed
    assert ex.squeue(ids["batch_down"]) == ""
    assert "--container-image=img:1" in ex.scripts[ids["hang"]]
    assert (tmp_path / "spool" / f"ret-{ids['batch_down']}.out").exists()
    ex.shutdown()


# ----------------------------------------------------------------------------- hub
def test_hub_claims_and_bests(tmp_path):
    from rsi.autoresearch.hub import SanityRule, SharedHub, experiment_key

    clock = [1000.0]
    hub = SharedHub(tmp_path / "hub", metric="val_bpb", verify_delay=0, now=lambda: clock[0])
    assert experiment_key("gpu0", "Increase LR to 0.04").startswith("gpu0--increase-lr-to-0-04--")
    k = hub.claim("increase LR to 0.04", "gpu0")
    assert k and hub.claim("increase LR to 0.04", "gpu0") is None            # exact key, same agent
    assert hub.claim("increase LR to 0.04", "gpu1") is None                  # semantic check across agents
    assert hub.claim("add 5% warmup", "gpu1")
    clock[0] += 901                                                          # CLAIM_TTL expired
    assert hub.claim("increase LR to 0.04", "gpu1")
    hub.seed({"train.py": "base"})
    src = {"train.py": "v1"}
    assert hub.publish_result(k, "gpu0", 0.99, 40.0, "keep", "increase LR", src, tier="xl")["global"]
    assert hub.global_best_value() == 0.99 and hub.pull_best_config()[0] == src
    assert hub.get("results", k)["source"] == src                            # full source published
    assert not hub.maybe_update_best(0.30, {"agent_id": "evil"}, {"train.py": "x"})     # v < 0.5
    assert not hub.maybe_update_best(0.85, {"agent_id": "evil"}, {"train.py": "x"})     # jump > 0.1
    assert hub._update_tier_best("xl", 0.85, {"agent_id": "evil"}, {"train.py": "x"})   # tier rule is weaker
    assert hub.maybe_update_best(0.95, {"agent_id": "ok"}, {"train.py": "v2"})
    assert SanityRule(direction="max", floor=0.99).check(0.995, 0.8) is not None
    ver = SharedHub(tmp_path / "hub2", metric="val_bpb", verify_delay=0, verifier=lambda s: 1.00, verify_tol=0.005)
    ver.seed({"train.py": "b"})
    assert ver.maybe_update_best(1.00, {"agent_id": "a"}, {"train.py": "a"})
    assert not ver.maybe_update_best(0.95, {"agent_id": "liar"}, {"train.py": "b"})    # re-run disagrees
    hub.post_insight("gpu0", "LR helps", [k])
    hub.publish_hypothesis("gpu0", "try LR 0.08", "more LR")
    assert hub.unclaimed_hypotheses("LR")[0]["title"] == "try LR 0.08"
    assert hub.analyze()["n_results"] == 1


def test_collaborative_swarm_bogus_agent(tmp_path):
    from rsi.autoresearch.hub import CollaborativeLoop, SanityRule, SharedHub

    def swarm(root, sanity):
        task = LandscapeTask()
        hub = SharedHub(root, metric="val_loss", verify_delay=0, sanity=sanity)
        loops = [CollaborativeLoop(task, MockResearchAgent(landscape_edit_pool(), seed=i),
                                   Config(max_experiments=6, plot=False, hidden_audit=False, seed=i),
                                   out_dir=root / f"a{i}", hub=hub, agent_id=f"a{i}",
                                   report=(lambda v: 0.3) if i == 2 else None) for i in range(3)]
        ths = [threading.Thread(target=l.run) for l in loops]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        return hub

    assert swarm(tmp_path / "nofilter", None).get("best", "metadata")["agent_id"] == "a2"
    hub = swarm(tmp_path / "filter", SanityRule())
    assert hub.get("best", "metadata")["agent_id"] != "a2"
    assert len(hub.items("results")) == 18 and hub.items("insights")


def test_fresh_run_required(tmp_path):
    cfg = dict(max_experiments=2, plot=False, hidden_audit=False)
    run(LandscapeTask(), config=Config(**cfg), out_dir=tmp_path / "f")
    with pytest.raises(FileExistsError):
        run(LandscapeTask(), config=Config(**cfg), out_dir=tmp_path / "f")
    res = run(LandscapeTask(), config=Config(overwrite=True, **cfg), out_dir=tmp_path / "f")
    assert len(res.ledger) == 3 and len(ResultsLog.read(tmp_path / "f" / "results.tsv").rows()) == 3


def test_mock_agent_combines_when_out_of_ideas():
    from rsi.autoresearch.agent import AgentContext

    task = LandscapeTask()
    ag = MockResearchAgent(landscape_edit_pool(), seed=0, p_repeat=0.0)
    art = task.seed_artifact()
    ctx = AgentContext(program="", artifact=art, editable=("train.py",), locked=(), results_tsv="", git_log="")
    descs = [ag.propose(ctx).change for _ in range(30)]
    assert len(set(descs[:17])) == 17                      # every single edit tried once on this incumbent
    assert any(d.startswith("combine:") for d in descs[17:])


def test_random_search_agent_samples_from_base():
    from rsi.autoresearch import RandomSearchAgent
    from rsi.autoresearch.agent import AgentContext

    task = LandscapeTask()
    base = task.seed_artifact()
    ag = RandomSearchAgent(landscape_edit_pool(), base, seed=1)
    other = base.with_files({"train.py": base["train.py"].replace("WIDTH = 512", "WIDTH = 64")})
    ctx = AgentContext(program="", artifact=other, editable=("train.py",), locked=(), results_tsv="", git_log="")
    for _ in range(10):
        p = ag.propose(ctx)
        assert p.ok and p.change.startswith("random:") and "WIDTH = 64" not in p.artifact["train.py"]


def test_rewind_and_multichain(tmp_path):
    from rsi.autoresearch.parallel import MultiChainLauncher

    task = LandscapeTask()
    loop = AutoresearchLoop(task, MockResearchAgent(landscape_edit_pool(), schedule=["width_down", "width_down",
                                                                                     "batch_down"]),
                            Config(max_experiments=3, plot=False, hidden_audit=False), out_dir=tmp_path / "rw")
    loop.run()
    kept = [n for n in loop.ledger.nodes() if n.status == "keep"]
    assert len(kept) >= 2
    loop.rewind(kept[0].meta["commit"])
    assert loop.inc["artifact"] == task.seed_artifact() and loop.ws.n_rewinds == 1
    loop.cfg.max_experiments = 5
    loop.step()
    assert loop.ledger.nodes()[-1].parent == kept[0].id

    def make(i, out):
        return AutoresearchLoop(LandscapeTask(), MockResearchAgent(landscape_edit_pool(), seed=i),
                                Config(max_experiments=4, plot=False, hidden_audit=False), out_dir=out)

    results = MultiChainLauncher(make, 3, tmp_path / "mc", tag="mar5").launch()
    assert len(results) == 3 and {r.meta["workspace"]["branch"] for r in results} == \
        {"autoresearch/mar5-w0", "autoresearch/mar5-w1", "autoresearch/mar5-w2"}
