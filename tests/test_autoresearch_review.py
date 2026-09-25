"""Review-round tests for rsi.autoresearch.

* Genericity: a second brand-new Domain, deliberately unlike the others (the artifact is a JSON
  config rather than Python, the domain has no ``seed_artifact`` hook, grading is noisy per seed,
  higher is better), driven through the LLM-agent code path (scripted MockLLM), a repeat-run keep
  rule, the parallel loop and validation re-sampling - with no change to the method.
* Regressions for bugs fixed in review: LLM usage of user-supplied agents and of the
  ``editor="agent"`` path is metered; the git workspace mirrors nested files exactly; the
  collaborative loop publishes a hypothesis with every result (at-home: "mandatory every time").
"""
import json
import random
import shutil

import pytest

from rsi.autoresearch import (Config, LLMResearchAgent, MockResearchAgent, ResultsLog, ScriptedEdit, Workspace, run,
                              scripted_llm)
from rsi.autoresearch.loop import agent_llms, make_agent
from rsi.core import Artifact, ArtifactStore, CachedLLM, ClaudeCLI, FunctionDomain, RewriteEditor, Task, TaskSuite


def _router_domain():
    """Route integers to "small"/"big" (true boundary 60). The artifact is router.json with a
    threshold; each rollout mislabels 10% of inputs at random (seeded), so scores are noisy."""
    tasks = [Task(f"n{i}", i, "big" if i >= 60 else "small", family="mid" if i < 90 else "tail") for i in range(100)]
    rng = random.Random(0)
    ids = [t.id for t in tasks if t.family == "mid"]
    rng.shuffle(ids)
    suite = TaskSuite(tasks, {"evolve": ids[:30], "holdout": ids[30:], "ood": [f"n{i}" for i in range(90, 100)]},
                      name="router")

    def execute(art, task, seed, llm):
        cfg = json.loads(art["router.json"])
        label = cfg["labels"][int(task.input >= cfg["threshold"])]
        if random.Random(f"{task.id}|{seed}").random() < 0.1:
            label = cfg["labels"][int(task.input < cfg["threshold"])]
        return label

    def grade(task, out):
        return float(out == task.target)

    def move(delta):
        def apply(files):
            cfg = json.loads(files["router.json"])
            new = cfg["threshold"] + delta
            if not 0 <= new <= 100:
                return None
            old, cfg["threshold"] = cfg["threshold"], new
            return {"router.json": json.dumps(cfg)}, f"threshold {old} -> {new}"
        return apply

    pool = [ScriptedEdit("up10", "unknown", move(10), group="threshold"),
            ScriptedEdit("down10", "unknown", move(-10), group="threshold"),
            ScriptedEdit("up5", "unknown", move(5), group="threshold"),
            ScriptedEdit("down5", "unknown", move(-5), group="threshold")]
    seed_art = Artifact({"router.json": json.dumps({"threshold": 20, "labels": ["small", "big"]})})
    return FunctionDomain(suite, execute, grade, name="router"), seed_art, pool


def test_second_new_domain_llm_path_and_variants(tmp_path):
    dom, seed_art, pool = _router_domain()
    with pytest.raises(ValueError, match="seed_artifact"):
        run(dom, agent=MockResearchAgent(pool), config=Config(max_experiments=1, plot=False, persist=False))
    with pytest.raises(ValueError, match="mock edit pool"):                        # no agent and no LLM: clear error
        run(dom, seed_art, config=Config(max_experiments=1, plot=False, persist=False))
    llm = scripted_llm(pool, seed=3)
    res = run(dom, seed_art, llm_propose=llm, out_dir=tmp_path / "llm", task_kwargs={"k": 2},
              config=Config(max_experiments=8, keep_rule="rigor", plot=False, reeval_seeds=2))
    rows = ResultsLog.read(res.meta["results_tsv"]).rows()
    assert rows[0].description == "baseline" and {r.status for r in rows} <= {"keep", "discard"}
    assert res.meta["analysis"]["best"] > res.meta["analysis"]["baseline"] + 0.2     # 20 -> ~60 is learnt
    assert json.loads(res.best["router.json"])["threshold"] > 30
    kept = [n for n in res.ledger.nodes() if n.status == "keep"]
    assert all(len(n.metrics["samples"]) == 3 for n in kept)                         # rigor: 3 runs per version
    aud = res.meta["audit"]
    assert {"holdout", "ood"} <= set(aud[-1]) and aud[-1]["holdout"] > aud[0]["holdout"]
    assert dom.tasks.is_sealed("holdout") and dom.tasks.is_sealed("ood")             # audit never unsealed them
    assert res.usage["_total"]["calls"] >= 8 and "scripted-researcher:researcher" in res.usage
    assert "router.json" in llm.calls[0]["prompt"] and "results.tsv" in llm.calls[0]["prompt"]
    # parallel "don't wait" loop and validation re-sampling on the same new domain, scripted agent
    for name, kw in {"parallel": dict(workers=2), "resample": dict(val_resample_every=3)}.items():
        r = run(dom, seed_art, agent=MockResearchAgent(pool, seed=1), out_dir=tmp_path / name,
                config=Config(max_experiments=6, plot=False, hidden_audit=False, **kw))
        assert r.meta["analysis"]["best"] >= r.meta["analysis"]["baseline"], name
    assert r.meta["val_epochs"] == 1


def test_second_new_domain_deterministic():
    dom, seed_art, pool = _router_domain()

    def once():
        r = run(dom, seed_art, agent=MockResearchAgent(pool, seed=5),
                config=Config(max_experiments=6, plot=False, hidden_audit=False, persist=False))
        return [(n.status, n.score, n.change) for n in r.ledger.nodes()]

    assert once() == once()


def test_user_supplied_and_agent_editor_llms_are_metered(tmp_path):
    dom, seed_art, pool = _router_domain()
    llm = scripted_llm(pool, seed=0)
    res = run(dom, seed_art, agent=LLMResearchAgent(RewriteEditor(llm)),        # no llm_propose= given
              config=Config(max_experiments=3, plot=False, hidden_audit=False, persist=False))
    assert res.usage["_total"]["calls"] >= 3                                    # was 0 before the fix
    cli = ClaudeCLI("haiku")                                                    # constructed, never called
    from rsi.autoresearch.task import DomainResearchTask

    ag = make_agent(DomainResearchTask(dom, seed_artifact=seed_art), CachedLLM(cli, tmp_path / "c"), editor="agent")
    assert agent_llms(ag) == [cli]            # AgentEditor meters on the inner CLI, so the loop must sum that meter


def test_git_workspace_mirrors_nested_files(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    repo = tmp_path / "repo"
    ws = Workspace(ArtifactStore(tmp_path / "store"), backend="git", root=repo)
    s0 = ws.init_run("t", Artifact({"harness.py": "x\n", "prompts/system.md": "A\n"}))
    ws.commit(Artifact({"harness.py": "y\n"}), "drop the prompt file")
    assert not (repo / "prompts" / "system.md").exists()          # a stale nested file used to survive
    assert (repo / ".gitignore").read_text().startswith("results.tsv")
    assert ws._git("ls-files").split() == [".gitignore", "harness.py"]
    ws.reset_to(s0)
    assert (repo / "prompts" / "system.md").read_text() == "A\n"


def test_collaborative_loop_publishes_a_hypothesis_every_time(tmp_path):
    from rsi.autoresearch import LandscapeTask, landscape_edit_pool
    from rsi.autoresearch.hub import CollaborativeLoop, SharedHub

    hub = SharedHub(tmp_path / "hub", metric="val_loss", verify_delay=0)
    lp = CollaborativeLoop(LandscapeTask(), MockResearchAgent(landscape_edit_pool(), seed=0, crash_rate=0.3),
                           Config(max_experiments=6, plot=False, hidden_audit=False), out_dir=tmp_path / "a",
                           hub=hub, agent_id="a0")
    lp.run()
    results = [v for _, v in hub.items("results")]
    titles = [v["title"] for _, v in hub.items("hypotheses")]
    assert len(results) == lp.hub_stats["published"] >= 6
    for r in results:
        assert any(r["description"] in t for t in titles), r["description"]
    assert {r["status"] for r in results} >= {"discard"}
