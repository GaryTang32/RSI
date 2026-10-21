"""A second new domain (JSON config, noisy, no seed_artifact hook) and regressions for metering, git and the hub."""
import json
import random

import pytest

from autoresearch_helpers import kept, needs_git, quiet, rows
from rsi.autoresearch import (Config, DomainResearchTask, LandscapeTask, LLMResearchAgent, MockResearchAgent,
                              ScriptedEdit, Workspace, agent_llms, landscape_edit_pool, make_agent, run, scripted_llm)
from rsi.autoresearch.hub import CollaborativeLoop, SharedHub
from rsi.core import Artifact, ArtifactStore, CachedLLM, ClaudeCLI, FunctionDomain, RewriteEditor, Task, TaskSuite


def _router_domain():
    """Route integers to "small"/"big" (true boundary 60) by router.json's threshold; 10% of labels flip per seed."""
    tasks = [Task(f"n{i}", i, "big" if i >= 60 else "small", family="mid" if i < 90 else "tail") for i in range(100)]
    ids = [t.id for t in tasks if t.family == "mid"]
    random.Random(0).shuffle(ids)
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

    pool = [ScriptedEdit(name, "unknown", move(delta), group="threshold")
            for name, delta in [("up10", 10), ("down10", -10), ("up5", 5), ("down5", -5)]]
    seed_art = Artifact({"router.json": json.dumps({"threshold": 20, "labels": ["small", "big"]})})
    return FunctionDomain(suite, execute, grade, name="router"), seed_art, pool


def test_second_new_domain_llm_path_and_variants(tmp_path):
    dom, seed_art, pool = _router_domain()
    with pytest.raises(ValueError, match="seed_artifact"):
        run(dom, agent=MockResearchAgent(pool), config=Config(max_experiments=1, plot=False, persist=False))
    with pytest.raises(ValueError, match="mock edit pool"):  # neither an agent nor an LLM
        run(dom, seed_art, config=Config(max_experiments=1, plot=False, persist=False))
    llm = scripted_llm(pool, seed=3)
    res = run(dom, seed_art, llm_propose=llm, out_dir=tmp_path / "llm", task_kwargs={"k": 2},
              config=Config(max_experiments=8, keep_rule="rigor", plot=False, reeval_seeds=2))
    log = rows(res)
    assert log[0].description == "baseline" and {r.status for r in log} <= {"keep", "discard"}
    assert res.meta["analysis"]["best"] > res.meta["analysis"]["baseline"] + 0.2  # 20 -> ~60 is learnt
    assert json.loads(res.best["router.json"])["threshold"] > 30
    assert all(len(n.metrics["samples"]) == 3 for n in kept(res))  # rigor: 3 runs per version
    aud = res.meta["audit"]
    assert {"holdout", "ood"} <= set(aud[-1]) and aud[-1]["holdout"] > aud[0]["holdout"]
    assert dom.tasks.is_sealed("holdout") and dom.tasks.is_sealed("ood")  # the audit never unsealed them
    assert res.usage["_total"]["calls"] >= 8 and "scripted-researcher:researcher" in res.usage
    assert "router.json" in llm.calls[0]["prompt"] and "results.tsv" in llm.calls[0]["prompt"]
    for name, kw in {"parallel": dict(workers=2), "resample": dict(val_resample_every=3)}.items():
        r = run(dom, seed_art, agent=MockResearchAgent(pool, seed=1), out_dir=tmp_path / name,
                config=quiet(max_experiments=6, **kw))
        assert r.meta["analysis"]["best"] >= r.meta["analysis"]["baseline"], name
    assert r.meta["val_epochs"] == 1  # the resample run


def test_second_new_domain_deterministic():
    dom, seed_art, pool = _router_domain()

    def once():
        r = run(dom, seed_art, agent=MockResearchAgent(pool, seed=5), config=quiet(max_experiments=6, persist=False))
        return [(n.status, n.score, n.change) for n in r.ledger.nodes()]

    assert once() == once()


def test_user_supplied_and_agent_editor_llms_are_metered(tmp_path):
    dom, seed_art, pool = _router_domain()
    res = run(dom, seed_art, agent=LLMResearchAgent(RewriteEditor(scripted_llm(pool, seed=0))),
              config=quiet(max_experiments=3, persist=False))
    assert res.usage["_total"]["calls"] >= 3
    cli = ClaudeCLI("haiku")  # constructed, never called
    agent = make_agent(DomainResearchTask(dom, seed_artifact=seed_art), CachedLLM(cli, tmp_path / "c"), editor="agent")
    assert agent_llms(agent) == [cli]  # AgentEditor meters on the inner CLI


@needs_git
def test_git_workspace_mirrors_nested_files(tmp_path):
    repo = tmp_path / "repo"
    ws = Workspace(ArtifactStore(tmp_path / "store"), backend="git", root=repo)
    s0 = ws.init_run("t", Artifact({"harness.py": "x\n", "prompts/system.md": "A\n"}))
    ws.commit(Artifact({"harness.py": "y\n"}), "drop the prompt file")
    assert not (repo / "prompts" / "system.md").exists()
    assert (repo / ".gitignore").read_text().startswith("results.tsv")
    assert ws._git("ls-files").split() == [".gitignore", "harness.py"]
    ws.reset_to(s0)
    assert (repo / "prompts" / "system.md").read_text() == "A\n"


def test_collaborative_loop_publishes_a_hypothesis_every_time(tmp_path):
    hub = SharedHub(tmp_path / "hub", metric="val_loss", verify_delay=0)
    loop = CollaborativeLoop(LandscapeTask(), MockResearchAgent(landscape_edit_pool(), seed=0, crash_rate=0.3),
                             quiet(max_experiments=6), out_dir=tmp_path / "a", hub=hub, agent_id="a0")
    loop.run()
    results = [v for _, v in hub.items("results")]
    titles = [v["title"] for _, v in hub.items("hypotheses")]
    assert len(results) == loop.hub_stats["published"] >= 6
    for r in results:
        assert any(r["description"] in t for t in titles), r["description"]
    assert {r["status"] for r in results} >= {"discard"}
