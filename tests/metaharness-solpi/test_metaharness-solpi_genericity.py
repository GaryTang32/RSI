"""Meta-Harness and SoL-Pi run unchanged on new tiny FunctionDomains (offline, deterministic)."""
import json
import random

from rsi.core import Artifact, Execution, FunctionDomain, MockLLM, Task, TaskSuite
from rsi.metaharness import CandidateSpec, Config, ProposalBatch, Proposer, RewriteProposer, make_proposer, run
from rsi.solpi import Config as SPConfig
from rsi.solpi import GateSpec, MechanismProposal
from rsi.solpi import run as sp_run
from solpi_testkit import VERBOSITY_IDEAS, VERBOSITY_SEED, verbosity_domain

POS, NEG = ["great", "good", "love", "fine", "nice"], ["bad", "awful", "hate", "poor", "sad"]
FILLER = ["the", "movie", "was", "really", "quite", "and", "plot", "actors", "today"]
SEED_HARNESS = 'def run(text):\n    return "neu"\n'
GOOD_HARNESS = ('POS = {"great", "good", "love", "fine", "nice"}\nNEG = {"bad", "awful", "hate", "poor", "sad"}\n\n\n'
                'def run(text):\n    w = set(text.split())\n'
                '    return "pos" if w & POS else "neg" if w & NEG else "neu"\n')
HALF_HARNESS = ('POS = {"great", "good", "love", "fine", "nice"}\n\n\n'
                'def run(text):\n    return "pos" if set(text.split()) & POS else "neu"\n')
EXECUTED: list[str] = []


def sentiment_suite(seed=0):
    rng = random.Random(seed)
    tasks, splits = [], {"evolve": [], "test": []}
    for i in range(40):
        lab = rng.choice(["pos", "neg", "neu"])
        words = rng.sample(FILLER, 4) + ([rng.choice(POS)] if lab == "pos" else [rng.choice(NEG)] if lab == "neg"
                                         else [])
        rng.shuffle(words)
        t = Task(f"s{i}", " ".join(words), lab, "sentiment")
        tasks.append(t)
        splits["evolve" if i < 24 else "test"].append(t.id)
    return TaskSuite(tasks, splits)


def sent_execute(artifact, task, seed, llm):
    EXECUTED.append(task.id)
    ns: dict = {}
    exec(artifact["harness.py"], ns)
    out = ns["run"](task.input)
    return Execution(output=out, trace=f"input={task.input!r} output={out!r}", tokens=len(artifact["harness.py"]),
                     meta={"context_chars": len(artifact["harness.py"])})


def sent_grade(task, output):
    return (1.0, "ok") if output == task.target else (0.0, f"got {output!r}, expected {task.target!r}")


def sentiment_domain():
    return FunctionDomain(sentiment_suite(), sent_execute, sent_grade, name="sentiment",
                          description="harness.py defines run(text) -> 'pos' | 'neg' | 'neu'")


def scripted_reply(prompt, system, seed, i):
    body = HALF_HARNESS if i == 0 else GOOD_HARNESS
    return ('```json\n{"iteration": 1, "candidates": [{"name": "kw_%d", "base_system": "seed", "hypothesis": '
            '"keyword lexicon", "axis": "exploration", "components": ["lexicon"]}]}\n```\n'
            '=== FILE: agents/kw_%d/harness.py ===\n%s' % (i, i, body))


def test_metaharness_on_new_function_domain_with_scripted_llm(tmp_path):
    dom = sentiment_domain()
    llm = MockLLM(scripted_reply)
    cfg = Config(iterations=2, k=1, objectives=("score",), validate_in_subprocess=False, finalize=False)
    assert isinstance(make_proposer(dom, llm, cfg), RewriteProposer)   # no mock library needed for a new domain
    EXECUTED.clear()
    res = run(dom, Artifact({"harness.py": SEED_HARNESS}, meta={"name": "seed"}), llm_propose=llm, config=cfg,
              out_dir=tmp_path)
    test_ids = set(dom.tasks.splits["test"])
    assert not test_ids & set(EXECUTED), "sealed test split executed during evolution"
    assert res.meta["n_evaluated"] == 2 and res.meta["frontier"]["_best"]["score"] == 1.0
    assert res.best["harness.py"] == GOOD_HARNESS
    test_inputs = [dom.tasks.get(t).input for t in test_ids]
    for call in llm.calls:                                   # the proposer never sees sealed inputs
        assert not any(x in call["prompt"] for x in test_inputs)
    assert "input=" in llm.calls[1]["prompt"]                # ... but does read the raw search traces (full view)
    EXECUTED.clear()
    rep = res.loop.finalize()
    assert set(EXECUTED) <= test_ids and rep["splits"]["test"]["results"]["kw_1"]["score"] == 1.0
    assert len(EXECUTED) == len(rep["systems"]) * len(test_ids)      # every finalised system tested exactly once
    EXECUTED.clear()
    res.loop.finalize()                                      # idempotent: no second test evaluation
    assert EXECUTED == []


class LexiconProposer(Proposer):
    """Reads the failing per-task records of the best visible candidate."""

    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        best = max((p for p in view if p.endswith("scores.json")), key=lambda p: json.loads(view[p])["score"])
        name = best.split("/")[1]
        fails = [r for p, t in view.items() if p.startswith(f"candidates/{name}/eval/search/per_task/")
                 for r in json.loads(t) if r["score"] < 1]
        body = GOOD_HARNESS if any("expected 'neg'" in r["feedback"] for r in fails) and iteration > 1 \
            else HALF_HARNESS
        return ProposalBatch(candidates=[CandidateSpec(f"lex{iteration}", Artifact({"harness.py": body}),
                                                       "lexicon", "exploitation", ["lexicon"], name)],
                             files_read=sorted(view))


def test_metaharness_on_new_domain_with_user_proposer_and_history_modes(tmp_path):
    dom = sentiment_domain()
    for mode in ("full", "scores_only"):
        res = run(dom, Artifact({"harness.py": SEED_HARNESS}, meta={"name": "seed"}), proposer=LexiconProposer(),
                  config=Config(iterations=2, k=1, history_mode=mode, validate_in_subprocess=True),
                  out_dir=tmp_path / mode)
        # per-task feedback is only in the full view, so only it lets the proposer fix 'neg'
        assert res.meta["frontier"]["_best"]["score"] == (1.0 if mode == "full" else res.meta["curve"][0]["score"])
        assert "test" in res.meta["final"]["splits"]


class ConfigProposer:
    """One edit per idea, no repair."""

    def propose(self, idea, base, evidence, history):
        if history:
            return MechanismProposal(None, error="single variant", meta={"exhausted": True})
        cfg = json.loads(base["config.json"])
        if idea.mechanism == "trim1":
            cfg["verbosity"] -= 1
        elif idea.mechanism == "trim_all":
            cfg["verbosity"] = 0
        else:
            cfg["fast"] = True
        return MechanismProposal(base.with_files({"config.json": json.dumps(cfg, sort_keys=True)}),
                                 change=idea.mechanism)

    def fix(self, idea, prop, error):
        return MechanismProposal(None, error=error, meta={"exhausted": True})


def test_solpi_protocol_on_new_function_domain(tmp_path):
    dom = verbosity_domain()
    res = sp_run(dom, VERBOSITY_SEED, ideas=VERBOSITY_IDEAS, proposer=ConfigProposer(),
                 config=SPConfig(gate=GateSpec(capability=(("score", 0.02),), efficiency=("tokens", "cost")),
                                 n_lineages=3, max_iters=2), out_dir=tmp_path)
    r = res.meta["rounds"][0]
    assert set(r["frozen_ideas"]) == {"G1", "T1"}           # the capability floor rejects the do-less idea
    assert r["heldout_passed"] == {n: n.startswith("G1") for n in r["frozen"]}   # the firewall rejects the trick
    assert r["survivor_ideas"] == ["G1"]
    assert json.loads(res.best["config.json"]) == {"verbosity": 3}
    holdout, ood = set(dom.tasks.splits["holdout"]), set(dom.tasks.splits["ood"])
    touched = {aid for tid, aid in dom.runs if tid in holdout}
    assert len(touched) == 3 and VERBOSITY_SEED.id in touched    # firewall only: base + 2 frozen, once each
    assert not any(tid in ood for tid, _ in dom.runs)             # the final split is never touched by the protocol


def llm_implementer(prompt, system, seed, i):
    return ('{"change": "trim one level", "hypothesis": "shorter outputs", "components": ["verbosity"]}\n'
            '=== FILE: config.json ===\n{"verbosity": 3}\n')


def test_solpi_protocol_on_new_domain_with_scripted_llm_implementer(tmp_path):
    llm = MockLLM(llm_implementer)
    res = sp_run(verbosity_domain(), VERBOSITY_SEED, llm_propose=llm, ideas=VERBOSITY_IDEAS[:1],
                 config=SPConfig(n_lineages=1, max_iters=1), out_dir=tmp_path)
    r = res.meta["rounds"][0]
    assert r["survivor_ideas"] == ["G1"] and json.loads(res.best["config.json"]) == {"verbosity": 3}
    assert llm.meter.by_role["proposer"].calls == 1                # LLM usage metered per role
