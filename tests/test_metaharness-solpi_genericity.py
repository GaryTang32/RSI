"""Genericity: both methods run unchanged on NEW tiny FunctionDomains (offline, deterministic).

* Meta-Harness on a keyword-sentiment harness, driven by (a) a scripted LLM through the generic
  RewriteProposer path and (b) a user Proposer subclass; the sealed test split is executed only by
  finalize(), and no proposer prompt ever contains a test input.
* SoL-Pi's research protocol on a "verbosity" harness whose ideas are a general saving, a do-less
  shortcut and an environment-specific trick: the dual gate rejects the shortcut, the held-out
  firewall rejects the trick, the general idea survives; also with an LLM (scripted) implementer.
"""
import json
import random

from rsi.core import Artifact, Execution, FunctionDomain, MockLLM, Task, TaskSuite
from rsi.metaharness import CandidateSpec, Config, ProposalBatch, Proposer, RewriteProposer, make_proposer, run
from rsi.solpi import Config as SPConfig
from rsi.solpi import GateSpec, Idea, MechanismProposal
from rsi.solpi import run as sp_run

# ------------------------------------------------------------------ a new Meta-Harness problem
POS, NEG = ["great", "good", "love", "fine", "nice"], ["bad", "awful", "hate", "poor", "sad"]
FILLER = ["the", "movie", "was", "really", "quite", "and", "plot", "actors", "today"]


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


EXECUTED: list[str] = []


def sent_execute(artifact, task, seed, llm):
    EXECUTED.append(task.id)
    ns: dict = {}
    exec(artifact["harness.py"], ns)
    out = ns["run"](task.input)
    return Execution(output=out, trace=f"input={task.input!r} output={out!r}", tokens=len(artifact["harness.py"]),
                     meta={"context_chars": len(artifact["harness.py"])})


def sent_grade(task, output):
    return (1.0, "ok") if output == task.target else (0.0, f"got {output!r}, expected {task.target!r}")


SEED_HARNESS = 'def run(text):\n    return "neu"\n'
GOOD_HARNESS = ('POS = {"great", "good", "love", "fine", "nice"}\nNEG = {"bad", "awful", "hate", "poor", "sad"}\n\n\n'
                'def run(text):\n    w = set(text.split())\n    return "pos" if w & POS else "neg" if w & NEG else "neu"\n')
HALF_HARNESS = 'POS = {"great", "good", "love", "fine", "nice"}\n\n\ndef run(text):\n    return "pos" if set(text.split()) & POS else "neu"\n'


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
    n_sys = len(rep["systems"])
    assert len(EXECUTED) == n_sys * len(test_ids)            # every finalised system tested exactly once
    EXECUTED.clear()
    res.loop.finalize()                                      # idempotent: no second test evaluation
    assert EXECUTED == []


class LexiconProposer(Proposer):
    """A user-written proposer: reads failing per-task records of the best visible candidate."""

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
        # per-task feedback lives in the trace/per-task records: only the full view lets the proposer fix 'neg'
        assert res.meta["frontier"]["_best"]["score"] == (1.0 if mode == "full" else res.meta["curve"][0]["score"])
        assert "test" in res.meta["final"]["splits"]


# ------------------------------------------------------------------ a new SoL-Pi problem
def verbosity_domain():
    rng = random.Random(1)
    tasks, splits = [], {"evolve": [], "holdout": [], "ood": []}
    for fam, split, n in (("alpha", "evolve", 6), ("beta", "evolve", 6), ("gamma", "holdout", 4), ("gamma", "ood", 4)):
        for i in range(n):
            t = Task(f"{split}-{fam}-{i}", {"need": rng.randint(1, 3)}, None, fam)
            tasks.append(t)
            splits[split].append(t.id)
    runs: list[tuple[str, str]] = []

    def execute(artifact, task, seed, llm):
        cfg = json.loads(artifact["config.json"])
        runs.append((task.id, artifact.id))
        v = cfg["verbosity"]
        tokens = 200 * v + 500 - (400 if cfg.get("fast") else 0)
        ok = v >= task.input["need"] and not (cfg.get("fast") and task.family == "gamma")
        return Execution(output=ok, tokens=tokens, cost_usd=tokens * 1e-6, steps=v + 1)

    dom = FunctionDomain(TaskSuite(tasks, splits), execute, lambda t, o: 1.0 if o else 0.0, name="verbosity",
                         description="config.json sets verbosity (tokens) and an optional 'fast' flag")
    dom.runs = runs
    return dom


IDEAS = [Idea("G1", "C", "Trim one verbosity level", "trim1"),
         Idea("D1", "P", "Answer with minimal verbosity", "trim_all", kind="do_less"),
         Idea("T1", "T", "Fast path that assumes the familiar output format", "fast", kind="trick")]


class ConfigProposer:
    """A user MechanismProposer for the new domain: one edit per idea, no repair."""

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


SP_SEED = Artifact({"config.json": json.dumps({"verbosity": 4}, sort_keys=True)})


def test_solpi_protocol_on_new_function_domain(tmp_path):
    dom = verbosity_domain()
    res = sp_run(dom, SP_SEED, ideas=IDEAS, proposer=ConfigProposer(),
                 config=SPConfig(gate=GateSpec(capability=(("score", 0.02),), efficiency=("tokens", "cost")),
                                 n_lineages=3, max_iters=2), out_dir=tmp_path)
    r = res.meta["rounds"][0]
    assert set(r["frozen_ideas"]) == {"G1", "T1"}           # the capability floor rejects the do-less idea
    assert r["heldout_passed"] == {n: n.startswith("G1") for n in r["frozen"]}   # the firewall rejects the trick
    assert r["survivor_ideas"] == ["G1"]
    assert json.loads(res.best["config.json"]) == {"verbosity": 3}
    # the held-out split was only ever run by the firewall, on the base and the frozen candidates
    holdout = set(dom.tasks.splits["holdout"])
    ood = set(dom.tasks.splits["ood"])
    touched = {aid for tid, aid in dom.runs if tid in holdout}
    assert len(touched) == 3 and SP_SEED.id in touched           # base + 2 frozen candidates, once each
    assert not any(tid in ood for tid, _ in dom.runs)             # the final split is never touched by the protocol


def llm_implementer(prompt, system, seed, i):
    return ('{"change": "trim one level", "hypothesis": "shorter outputs", "components": ["verbosity"]}\n'
            '=== FILE: config.json ===\n{"verbosity": 3}\n')


def test_solpi_protocol_on_new_domain_with_scripted_llm_implementer(tmp_path):
    dom = verbosity_domain()
    llm = MockLLM(llm_implementer)
    res = sp_run(dom, SP_SEED, llm_propose=llm, ideas=IDEAS[:1],
                 config=SPConfig(n_lineages=1, max_iters=1), out_dir=tmp_path)
    r = res.meta["rounds"][0]
    assert r["survivor_ideas"] == ["G1"] and json.loads(res.best["config.json"]) == {"verbosity": 3}
    assert llm.meter.by_role["proposer"].calls == 1                # LLM usage metered per role
