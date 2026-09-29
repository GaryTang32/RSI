"""Regression tests for the Meta-Harness retry round 2 (docs/methods/metaharness-solpi/claims-audit-metaharness.md,
"Retry round 2"). Each test fails on the code before the round and passes after it."""
import json

from rsi.core.llm import MockLLM
from rsi.metaharness import LLMSummarizer


def _trace(unit: str, n_train: int = 400, n_eval: int = 40) -> str:
    lines = [json.dumps({"type": "meta", "unit": unit})]
    for i in range(n_train):
        lines.append(json.dumps({"type": "step", "phase": "train", "step": i, "ok": i % 3 == 0,
                                 "calls": [{"prompt": "x" * 400}]}))
    for i in range(n_eval):
        lines.append(json.dumps({"type": "eval_step", "step": i, "ok": i % 2 == 0, "pred": f"p{i}",
                                 "tgt": f"{unit}-gold-{i}", "prompt_len": 900}))
    return "\n".join(lines)


def test_llm_summarizer_reads_every_unit_and_its_evaluation_failures():
    """Claim L2 (retry 2): the summariser used to read ``join(traces)[:max_chars]`` = the head of the FIRST unit only
    (on MemoClassify: train-phase records, 0 of 144 evaluation records). It must see every unit and the
    evaluation-phase failures, within its character budget."""
    traces = {u: _trace(u) for u in ("ds_a__val", "ds_b__val", "ds_c__val")}
    assert all(len(t) > 40000 for t in traces.values())
    prompts = []
    llm = MockLLM(lambda prompt, system, seed, i: prompts.append(prompt) or "summary")
    assert LLMSummarizer(llm, max_chars=30000)(traces) == "summary"
    prompt = prompts[0]
    for u in traces:
        assert f"--- {u} ---" in prompt
        assert f"{u}-gold-" in prompt                   # evaluation-phase failure records of this unit
    assert prompt.count('"eval_step"') >= 15
    assert len(prompt) < 30000 + 2000                  # budget respected (plus the fixed instruction text)


# ---------------------------------------------------------------- structured comparison optimisers (Q4/Q5)
import pytest  # noqa: E402

from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import (Config, MemoClassifyLibrary, MockProposer, StructuredOptimizerProposer,  # noqa: E402
                             run)
from rsi.metaharness.baselines import OE_ARTIFACT_BYTES, programs_in  # noqa: E402


@pytest.fixture(scope="module")
def small_run(tmp_path_factory):
    dom = make_domain(seed=3, scale=0.3)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              config=Config(iterations=2, k=2, seed=3, shadow_monitor=False, finalize=False),
              out_dir=tmp_path_factory.mktemp("mh_r2"), baselines=dom.baselines())
    return dom, res


@pytest.mark.parametrize("policy", ["openevolve", "ttt_discover", "gepa"])
def test_structured_optimiser_restricts_the_view_and_names_the_parent(small_run, policy):
    """Q4/Q5 (retry 2): the comparison arms of paper Table 4 apply their own selection rule and context around
    the SAME proposer; before this round only view projections of the full history existed."""
    dom, res = small_run
    view = res.loop.store.view("full")
    wrapper = StructuredOptimizerProposer(MockProposer(MemoClassifyLibrary(), seed=0), policy, seed=0)
    parent, sub = wrapper.select(view, iteration=3, slot=0, seed=0)
    assert parent in {p.name for p in programs_in(view)}
    assert json.loads(sub["parent.json"])["parent"] == parent
    assert not any(p.startswith(("evolution_summary", "frontier_val", "reports/")) for p in sub)
    traces = {p: t for p, t in sub.items() if "/traces/" in p}
    if policy == "ttt_discover":
        assert not traces and {p.split("/")[1] for p in sub if p.startswith("candidates/")} == {parent}
    elif policy == "gepa":
        assert traces and all(p.split("/")[1] == parent for p in traces)
        assert all(t == view[p] for p, t in traces.items())             # the parent's full traces
    else:
        assert all(p.split("/")[1] == parent for p in traces)            # artifacts of the parent only
        assert sum(len(t) for t in traces.values()) <= OE_ARTIFACT_BYTES + 2000
    batch = wrapper.propose(iteration=3, view=view, k=2, brief="b",
                            artifacts={n: res.loop.store.artifact(n) for n in res.loop.store.names()}, seed=0)
    assert len(batch.candidates) == 2
    assert batch.candidates[0].base_system == parent                     # the mock mutates the selected parent
    ids = [c.artifact.id for c in batch.candidates]
    existing = {res.loop.store.artifact(n).id for n in res.loop.store.names()}
    assert len(set(ids)) == 2 and not set(ids) & existing                # no exact copies


def test_puct_prefers_unvisited_high_rank_programs():
    from rsi.metaharness.baselines import _Prog
    w = StructuredOptimizerProposer(MockProposer(MemoClassifyLibrary()), "ttt_discover")
    a = _Prog("a", 0.5, {"kind": "baseline", "order": 1}, {})
    b = _Prog("b", 0.6, {"kind": "candidate", "order": 2, "base_system": "a"}, {})
    assert w._puct([a, b], {}).name == "b"
    # once b has 8 children that did no better, its exploration bonus shrinks; a keeps Q = 0.6 through b
    kids = [_Prog(f"k{i}", 0.1, {"kind": "candidate", "order": 3 + i, "base_system": "b"}, {}) for i in range(8)]
    assert w._puct([a, b] + kids, {}).name == "a"
