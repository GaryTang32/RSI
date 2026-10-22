"""Meta-Harness LLM summariser and the structured comparison optimisers of paper Table 4."""
import json

import pytest

from rsi.core.llm import MockLLM
from rsi.domains.memoclassify import make_domain
from rsi.metaharness import Config, LLMSummarizer, MemoClassifyLibrary, MockProposer, StructuredOptimizerProposer, run
from rsi.metaharness.baselines import OE_ARTIFACT_BYTES, _Prog, programs_in


def _trace(unit: str) -> str:
    train = [{"type": "step", "phase": "train", "step": i, "ok": i % 3 == 0, "calls": [{"prompt": "x" * 400}]}
             for i in range(400)]
    evals = [{"type": "eval_step", "step": i, "ok": i % 2 == 0, "pred": f"p{i}", "tgt": f"{unit}-gold-{i}",
              "prompt_len": 900} for i in range(40)]
    return "\n".join(json.dumps(r) for r in [{"type": "meta", "unit": unit}, *train, *evals])


def _artifacts(store) -> dict:
    return {n: store.artifact(n) for n in store.names()}


def _traces(sub: dict[str, str]) -> dict[str, str]:
    return {p: t for p, t in sub.items() if "/traces/" in p}


@pytest.fixture(scope="module")
def small_run(tmp_path_factory):
    dom = make_domain(seed=3, scale=0.3)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              config=Config(iterations=2, k=2, seed=3, shadow_monitor=False, finalize=False),
              out_dir=tmp_path_factory.mktemp("mh_r2"), baselines=dom.baselines())
    return dom, res


def test_llm_summarizer_reads_every_unit_and_its_evaluation_failures():
    """Not just the head of the first unit: every unit and its evaluation failures, within the budget."""
    traces = {u: _trace(u) for u in ("ds_a__val", "ds_b__val", "ds_c__val")}
    assert all(len(t) > 40000 for t in traces.values())
    prompts = []
    llm = MockLLM(lambda prompt, system, seed, i: prompts.append(prompt) or "summary")
    assert LLMSummarizer(llm, max_chars=30000)(traces) == "summary"
    prompt = prompts[0]
    for u in traces:
        assert f"--- {u} ---" in prompt and f"{u}-gold-" in prompt
    assert prompt.count('"eval_step"') >= 15
    assert len(prompt) < 30000 + 2000  # budget plus the fixed instruction text


@pytest.mark.parametrize("policy", ["openevolve", "ttt_discover", "gepa"])
def test_structured_optimiser_restricts_the_view_and_names_the_parent(small_run, policy):
    _, res = small_run
    view = res.loop.store.view("full")
    wrapper = StructuredOptimizerProposer(MockProposer(MemoClassifyLibrary(), seed=0), policy, seed=0)
    parent, sub = wrapper.select(view, iteration=3, slot=0, seed=0)
    assert parent in {p.name for p in programs_in(view)}
    assert json.loads(sub["parent.json"])["parent"] == parent
    assert not any(p.startswith(("evolution_summary", "frontier_val", "reports/")) for p in sub)
    traces = _traces(sub)
    if policy == "ttt_discover":
        assert not traces and {p.split("/")[1] for p in sub if p.startswith("candidates/")} == {parent}
    elif policy == "gepa":
        assert traces and all(p.split("/")[1] == parent for p in traces)
        assert all(t == view[p] for p, t in traces.items())  # the parent's full traces
    else:
        assert all(p.split("/")[1] == parent for p in traces)
        assert sum(len(t) for t in traces.values()) <= OE_ARTIFACT_BYTES + 2000
    batch = wrapper.propose(iteration=3, view=view, k=2, brief="b", artifacts=_artifacts(res.loop.store), seed=0)
    assert len(batch.candidates) == 2
    assert batch.candidates[0].base_system == parent  # the mock mutates the selected parent
    ids = {c.artifact.id for c in batch.candidates}
    existing = {res.loop.store.artifact(n).id for n in res.loop.store.names()}
    assert len(ids) == 2 and not ids & existing  # no exact copies


def test_puct_prefers_unvisited_high_rank_programs():
    w = StructuredOptimizerProposer(MockProposer(MemoClassifyLibrary()), "ttt_discover")
    a = _Prog("a", 0.5, {"kind": "baseline", "order": 1}, {})
    b = _Prog("b", 0.6, {"kind": "candidate", "order": 2, "base_system": "a"}, {})
    assert w._puct([a, b], {}).name == "b"
    # 8 children that did no better shrink b's exploration bonus; a keeps Q = 0.6 through b
    kids = [_Prog(f"k{i}", 0.1, {"kind": "candidate", "order": 3 + i, "base_system": "b"}, {}) for i in range(8)]
    assert w._puct([a, b] + kids, {}).name == "a"


def test_gepa_minibatch_reflects_on_three_examples_of_the_parent(small_run):
    """GEPA's reflective dataset is a minibatch of 3 examples (``reflection_minibatch_size``), not every trace."""
    _, res = small_run
    view = res.loop.store.view("full")
    wrapper = StructuredOptimizerProposer(MockProposer(MemoClassifyLibrary(), seed=0), "gepa_minibatch", seed=0)
    parent, sub = wrapper.select(view, iteration=3, slot=0, seed=0)
    assert parent in {p.name for p in programs_in(view)}
    traces = _traces(sub)
    assert traces and all(p.split("/")[1] == parent for p in traces)
    evals = [json.loads(line) for t in traces.values() for line in t.splitlines() if '"eval_step"' in line]
    assert len(evals) == 3
    parent_traces = [t for p, t in view.items() if p.startswith(f"candidates/{parent}/eval/search/traces/")]
    assert sum(len(t) for t in traces.values()) < sum(len(t) for t in parent_traces) / 10
    assert {p.split("/")[1] for p in sub if p.startswith("candidates/")} == {parent}
    _, sub2 = wrapper.select(view, iteration=3, slot=1, seed=0)  # the epoch-shuffled sampler moves on
    assert len([line for t in _traces(sub2).values() for line in t.splitlines() if '"eval_step"' in line]) == 3
    batch = wrapper.propose(iteration=3, view=view, k=2, brief="b", artifacts=_artifacts(res.loop.store), seed=0)
    assert len(batch.candidates) == 2 and batch.candidates[0].base_system == parent


def test_gepa_minibatch_front_is_per_instance_and_drops_dominated_programs():
    """GEPA ``remove_dominated_programs``: a single-instance leader stays; a program always shadowed goes."""
    rd = StructuredOptimizerProposer.remove_dominated
    out = rd({0: {"a", "b"}, 1: {"a", "b"}, 2: {"c"}}, {"a": 0.9, "b": 0.5, "c": 0.1})
    assert out == {0: {"a"}, 1: {"a"}, 2: {"c"}}  # b (dominated by a) removed; low-score c kept
    assert rd({0: {"x", "y"}}, {"x": 0.2, "y": 0.3}) == {0: {"y"}}  # exact tie: the lower-scoring one goes
