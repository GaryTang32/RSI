"""Meta-Harness + MemoClassify tests (offline, deterministic)."""
import json
import time

import pytest

from rsi.core import Artifact, Evaluator, MockLLM, SealedSplitError
from rsi.domains.memoclassify import (MemoClassifyDomain, extract_json_field, make_domain, programs, validate_memory)
from rsi.metaharness import (Config, ExperienceStore, FinalizedError, InterfaceValidator, LeakageScreen,
                             MetaHarnessLoop, MockProposer, MemoClassifyLibrary, RewriteProposer, hypervolume,
                             pareto_frontier, per_unit_best, run)


@pytest.fixture(scope="module")
def dom():
    return make_domain(seed=0, scale=0.35)


# ------------------------------------------------------------------ MemoClassify
def test_datasets_deterministic_and_disjoint():
    a, b = make_domain(seed=3, scale=0.3), make_domain(seed=3, scale=0.3)
    assert [e.text for e in a.search[0].val] == [e.text for e in b.search[0].val]
    d = a.search[0]
    assert set(e.id for e in d.val).isdisjoint(e.id for e in d.test)
    assert {e.label for e in d.train} == set(d.labels)          # every label appears in the stream
    assert set(a.search[0].common).isdisjoint(a.ood[0].common)  # OOD uses other vocabularies


def test_model_only_answers_labels_in_prompt(dom):
    lm = dom.make_model("A")
    ex = dom.search[0].val[0]
    r = lm.complete(f"Answer this.\n{ex.text}\nfinal_answer please")
    ans = extract_json_field(r.text, "final_answer")
    assert ans not in dom.search[0].labels                     # no demonstrations, no label list -> cannot be right
    demo = "\n".join(f"Q: {e.text}\nA: {e.label}" for e in dom.search[0].train)
    r2 = lm.complete(f"{demo}\n\n**Problem:**\n{ex.text}\nfinal_answer")
    assert extract_json_field(r2.text, "final_answer") in dom.search[0].labels


def test_model_degrades_past_budget(dom):
    lm = dom.make_model("A")
    d = dom.search[2]
    ok_short = ok_long = 0
    for ex in d.val[:12]:
        near = sorted(d.train, key=lambda e: -len(set(e.text.split()) & set(ex.text.split())))[:8]
        short = "\n\n".join(f"Q: {e.text}\nA: {e.label}" for e in near)
        pad = "\n".join(f"Note {i}: background material that is not a demonstration." for i in range(400))
        long = pad + "\n\n" + short + "\n\n" + pad            # demonstrations lost in the middle
        for prompt, key in ((short, "s"), (long, "l")):
            a = lm.classify(prompt + f"\n\n**Problem:**\n{ex.text}")[0]
            if a == ex.label:
                if key == "s":
                    ok_short += 1
                else:
                    ok_long += 1
    assert ok_short > ok_long


def test_domain_execute_grade_and_context_cost(dom):
    ev = Evaluator(dom, dom.make_model("A"), workers=1)
    r_all = ev.evaluate(dom.seed_artifact("fewshot_all"), "evolve")
    r_zero = ev.evaluate(dom.seed_artifact("no_memory"), "evolve")
    assert r_all.score > r_zero.score
    t = next(iter(r_all.trials.values()))[0]
    assert t.meta["context_chars"] > 1000 and t.meta["llm_calls"] > 0
    recs = [json.loads(l) for l in t.trace.splitlines()]
    assert {r["type"] for r in recs} >= {"meta", "step", "checkpoint", "eval_step", "done"}
    assert all("prompt_len" in r and "context_chars" in r for r in recs if r["type"] == "eval_step")
    with pytest.raises(SealedSplitError):
        ev.evaluate(dom.seed_artifact(), "test")


def test_program_render_parse_roundtrip_and_validation(dom):
    for mv in programs.MOVES:
        g = programs.apply_move(programs.DEFAULT_GENOME, mv)
        src = programs.render(g)
        assert programs.parse(src) == g
        assert validate_memory(Artifact({"memory.py": src}), dom) is None
    assert validate_memory(Artifact({"memory.py": "class Memory: pass"}), dom) is not None


# ------------------------------------------------------------------ frontier
def test_pareto_and_hypervolume():
    pts = [("a", 0.5, 100.0), ("b", 0.6, 300.0), ("c", 0.4, 200.0), ("d", 0.6, 300.0)]
    front = pareto_frontier(pts)
    assert [p[0] for p in front] == ["b", "d", "a"]
    assert hypervolume([("a", 0.5, 100.0)], ref_cost=400) == pytest.approx(0.5 * 300)
    assert hypervolume(pts, ref_cost=400) == pytest.approx(0.5 * 300 + 0.1 * 100)
    best = per_unit_best({"x": {"u": (0.5, 10.0)}, "y": {"u": (0.5, 5.0)}})
    assert best["u"]["best_system"] == "y"


# ------------------------------------------------------------------ store + views
def _tiny_loop(dom, tmp_path, mode="full", **kw):
    cfg = Config(iterations=2, k=2, history_mode=mode, validate_in_subprocess=False, **kw)
    return MetaHarnessLoop(dom, llm_task=dom.make_model("A"), proposer=MockProposer(MemoClassifyLibrary()),
                           config=cfg, out_dir=tmp_path, baselines=dom.baselines())


def test_views_expose_exactly_what_the_mode_allows(dom, tmp_path):
    loop = _tiny_loop(dom, tmp_path)
    loop.run()
    st = loop.store
    full = st.view("full")
    so = st.view("scores_only")
    assert any("/traces/" in p for p in full) and not any("/traces/" in p for p in so)
    assert any(p.endswith("scores.json") for p in so) and any("/src/" in p for p in so)
    last = st.view("last_only")
    names = {p.split("/")[1] for p in last}
    assert len(names) == 1 and any("/traces/" in p for p in last)
    seed = st.view("seed_only", seeds=["no_memory"])
    assert all(p.startswith("candidates/no_memory/") for p in seed) and not any("scores" in p for p in seed)
    win = st.view("window", window=2)
    assert len({p.split("/")[1] for p in win}) == 2
    rows = st.summary_rows()
    assert rows[0]["iteration"] == 0 and any(r["iteration"] == 2 for r in rows)
    fr = st.frontier()
    assert fr["_pareto"] and fr["_best"]["system"] == fr["_pareto"][0]["system"]
    meta = json.loads((st.sessions_dir() / "iter001" / "meta.json").read_text())
    assert meta["n_files_read"] > 0 and meta["files_read_by_kind"]["traces"] > 0


def test_scores_only_proposer_never_reads_traces(dom, tmp_path):
    loop = _tiny_loop(dom, tmp_path, mode="scores_only")
    loop.run()
    for d in loop.store.sessions_dir().glob("iter*/meta.json"):
        m = json.loads(d.read_text())
        assert m["files_read_by_kind"]["traces"] == 0


def test_test_split_never_touched_during_evolution_and_finalize_locks(dom, tmp_path):
    loop = _tiny_loop(dom, tmp_path)
    loop.run()
    st = loop.store
    assert not st.results_dir.exists() and not st.finalized_path.exists()
    assert not any("results/" in p or p.endswith("test.json") for p in st.view("full"))
    rep = loop.finalize()
    assert st.is_finalized() and rep["splits"]["test"]["results"]
    assert set(rep["systems"]) >= set(dom.baselines())
    with pytest.raises(FinalizedError):
        loop.iterate(99)
    rep2 = loop.finalize()                      # idempotent: no re-evaluation
    assert rep2["splits"]["test"]["results"].keys() == rep["splits"]["test"]["results"].keys()
    assert not any("results/" in p for p in st.view("full"))


def test_leakage_screen_rejects_lookup_candidates(dom, tmp_path):
    lib = MemoClassifyLibrary()
    loop = MetaHarnessLoop(dom, llm_task=dom.make_model("A"), proposer=MockProposer(lib, leak_rate=1.0),
                           config=Config(iterations=2, k=2, leakage_screen=True, validate_in_subprocess=False),
                           out_dir=tmp_path, baselines=dom.baselines())
    loop.run()
    statuses = [loop.store.meta(n).get("status") for n in loop.store.names()]
    assert "rejected_leakage" in statuses
    screen = LeakageScreen.for_domain(dom)
    g = programs.leaky_genome(programs.DEFAULT_GENOME, loop.store.traces("fewshot_all"))
    assert screen.check(Artifact({"memory.py": programs.render(g)}), dom.seed_artifact()) is not None
    assert screen.check(Artifact({"memory.py": programs.render(programs.apply_move(programs.DEFAULT_GENOME,
                                                                                   "coverage"))}),
                        dom.seed_artifact()) is None


def test_interface_validator_catches_errors_and_hangs(dom):
    v = InterfaceValidator(timeout_s=3.0, isolate=True)
    ok, _ = v.validate(dom, dom.seed_artifact("fewshot_all"))
    assert ok
    bad, msg = v.validate(dom, Artifact({"memory.py": "def broken(:\n"}))
    assert not bad and "syntax" in msg
    hang = Artifact({"memory.py": dom.seed_artifact("no_memory")["memory.py"].replace(
        "response = self.call_llm", "while True:\n            pass\n        response = self.call_llm")})
    t0 = time.time()
    ok3, msg3 = v.validate(dom, hang)
    assert not ok3 and "timed out" in msg3 and time.time() - t0 < 10


def test_rewrite_proposer_parses_k_candidates(dom):
    src = programs.render(programs.apply_move(programs.DEFAULT_GENOME, "retrieve_topk"))
    reply = ('```json\n{"iteration": 1, "candidates": [{"name": "topk_a", "base_system": "fewshot_all", '
             '"hypothesis": "h", "axis": "exploitation", "components": ["retrieval"]}]}\n```\n'
             f'=== FILE: agents/topk_a/memory.py ===\n{src}\n')
    llm = MockLLM(lambda p, s, seed, i: reply)
    prop = RewriteProposer(llm)
    b = prop.propose(iteration=1, view={"evolution_summary.jsonl": "{}"}, k=2, brief="b",
                     artifacts={"fewshot_all": dom.seed_artifact()})
    assert len(b.candidates) == 1 and b.candidates[0].base_system == "fewshot_all"
    assert "SCALE" not in b.candidates[0].artifact["memory.py"] and b.candidates[0].artifact["memory.py"] == \
        src.rstrip() + "\n"
    assert "Write exactly 2 new candidate" in llm.calls[0]["system"]


def test_run_end_to_end_is_deterministic(dom, tmp_path):
    cfg = Config(iterations=3, k=2, validate_in_subprocess=False)
    r1 = run(dom, dom.seed_artifact(), llm_task=dom.make_model("A"), config=cfg, out_dir=tmp_path / "a",
             baselines=dom.baselines())
    r2 = run(dom, dom.seed_artifact(), llm_task=dom.make_model("A"), config=cfg, out_dir=tmp_path / "b",
             baselines=dom.baselines())
    assert [c["score"] for c in r1.meta["curve"]] == [c["score"] for c in r2.meta["curve"]]
    assert r1.meta["frontier"]["_best"]["score"] >= max(
        r1.meta["frontier"][u]["score"] for u in [k for k in r1.meta["frontier"] if not k.startswith("_")]) - 1
    assert r1.meta["final"]["splits"]["test"]["results"]
    assert len(r1.ledger) >= 2 + 3


def test_eval_budget_is_respected(dom, tmp_path):
    res = run(dom, dom.seed_artifact(), llm_task=dom.make_model("A"),
              config=Config(iterations=10, k=2, eval_budget=3, validate_in_subprocess=False, finalize=False),
              out_dir=tmp_path, baselines=dom.baselines())
    assert res.meta["n_evaluated"] == 3 and res.stop_reason == "eval_budget"


def test_runs_on_agentqa_second_domain(tmp_path):
    from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
    suite = make_suite(n_evolve=6, n_holdout=4, n_ood_per_family=2, seed=0)
    d = AgentQADomain(suite)
    res = run(d, d.seed_artifact(), llm_task=SimModel(suite),
              config=Config(iterations=2, k=2, objectives=("score", "context_cost"), cost_metric="tokens",
                            test_splits=("holdout", "ood"), validate_in_subprocess=False),
              out_dir=tmp_path)
    assert res.meta["n_evaluated"] >= 2
    assert res.meta["frontier"]["_best"]["score"] >= res.meta["curve"][0]["best_so_far"] - 1e-9
    assert "ood" in res.meta["final"]["splits"]
