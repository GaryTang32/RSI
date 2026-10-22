"""GEPA pure components against the reference probe vectors (spec 4.7) and reference semantics."""
import random
from collections import Counter
from pathlib import Path

import pytest

from rsi.core import Artifact, MockLLM
from rsi.gepa import (DEFAULT_TEMPLATE, OPTIMIZE_ANYTHING_TEMPLATE, BeamSearchSelector, EpochShuffledBatchSampler,
                      FrontierTracker, ImprovementOrEqual, NoiseMargin, ReflectionProposer, StrictImprovement,
                      build_prompt, find_dominator_programs, parse_fenced, remove_dominated_programs,
                      render_samples, sample_and_attempt_merge, select_eval_subsample, select_from_pareto_front)
from rsi.gepa.merge import merge_texts
from rsi.gepa.state import SearchState
from rsi.gepa.strategies import CurrentBestSelector, EpsilonGreedySelector, RoundRobinComponents, TopKParetoSelector

SPEC = Path(__file__).resolve().parents[2] / "docs" / "methods" / "gepa" / "paper-spec.md"
RECORD = {"Inputs": "x", "Generated Outputs": "y", "Feedback": "z"}


def test_pareto_probe_vector_1():
    front, agg = {0: {0, 1}, 1: {1}, 2: {2, 3}, 3: {3}}, [0.4, 0.6, 0.5, 0.7]
    assert remove_dominated_programs(front, agg) == {0: {1}, 1: {1}, 2: {3}, 3: {3}}
    rng = random.Random(0)
    c = Counter(select_from_pareto_front(front, agg, rng) for _ in range(10000))
    assert set(c) == {1, 3} and abs(c[1] / 10000 - 0.5) < 0.03


def test_pareto_probe_vector_2_set_cover_prunes_generalist():
    front = {1: {0, 1}, 2: {0, 2}}
    assert remove_dominated_programs(front, [0.1, 0.5, 0.6]) == {1: {1}, 2: {2}}
    assert remove_dominated_programs(front, [0.9, 0.5, 0.6]) == {1: {0}, 2: {0}}
    assert find_dominator_programs(front, [0.1, 0.5, 0.6]) == [1, 2]


def test_frontier_tracker_ties_and_replacement():
    f = FrontierTracker("instance")
    f.update(0, {"a": 0.5, "b": 1.0})
    d = f.update(1, {"a": 0.7, "b": 1.0})
    assert f.mapping() == {"i:a": {1}, "i:b": {0, 1}}
    assert d["won"] == ["i:a"] and d["tied"] == ["i:b"] and d["displaced"] == {"i:a": [0]}
    h = FrontierTracker("hybrid")
    h.update(0, {"a": 1.0}, {"a": {"acc": 1.0, "len": 0.2}})
    h.update(1, {"a": 0.0}, {"a": {"acc": 0.0, "len": 0.9}})
    assert h.mapping()["o:len"] == {1} and h.mapping()["i:a"] == {0}
    assert FrontierTracker.from_json(h.to_json()).mapping() == h.mapping()


def test_selectors(selector_state):
    st = selector_state([0.1, 0.9, 0.5, 0.8], {"a": {1}, "b": {2}, "c": {3}})
    assert CurrentBestSelector().select(st) == 1
    beam = BeamSearchSelector(n=2)
    assert [beam.select(st) for _ in range(4)] == [1, 3, 1, 3]
    top = TopKParetoSelector(random.Random(0), k=2)
    assert {top.select(st) for _ in range(50)} == {1, 3}
    eps = EpsilonGreedySelector(random.Random(0), epsilon=1.0)
    assert {eps.select(st) for _ in range(200)} == {0, 1, 2, 3}


def test_round_robin_pointer_inherited_by_children():
    st = SearchState(["A", "B", "C"])
    st.add_candidate(Artifact({"A": "a", "B": "b", "C": "c"}), [None], {"v": 0.0}, None, "baseline", 0)
    rr = RoundRobinComponents()
    assert rr(st, 0) == ["A"] and st.rr[0] == 1
    st.add_candidate(Artifact({"A": "a2", "B": "b", "C": "c"}), [0], {"v": 0.5}, None, "reflective", 0)
    assert st.rr[1] == 1 and rr(st, 1) == ["B"]
    assert [rr(st, 0) for _ in range(3)] == [["B"], ["C"], ["A"]]


def test_epoch_shuffled_sampler_padding_and_epochs():
    ids = [f"t{i}" for i in range(10)]
    s = EpochShuffledBatchSampler(3, random.Random(0))
    batches = [s.next_ids(ids, i) for i in range(4)]
    flat = [x for b in batches for x in b]
    assert all(len(b) == 3 for b in batches) and len(s.shuffled_ids) == 12
    assert set(flat) == set(ids)
    assert sorted(Counter(flat).values()) == [1] * 8 + [2] * 2
    assert s.shuffled_ids[10:] == [s.shuffled_ids[9], s.shuffled_ids[8]]   # reference pads with the last ids, reversed
    epoch0 = list(s.shuffled_ids)
    s.next_ids(ids, 4)
    assert s.epoch == 1 and s.shuffled_ids != epoch0
    s2 = EpochShuffledBatchSampler(3, random.Random(0))
    s2.set_state(s.get_state())
    assert s2.next_ids(ids, 5) == s.next_ids(ids, 5)


def _probe_cands() -> list[Artifact]:
    return [Artifact({"A": "s", "B": "s"}), Artifact({"A": "x", "B": "s"}), Artifact({"A": "s", "B": "y"})]


def test_merge_zero_weight_probe():
    cands, parents = _probe_cands(), [[None], [0], [0]]

    def merge(agg, **kw):
        return sample_and_attempt_merge(agg, random.Random(0), [1, 2], ([], []), cands, parents, ["A", "B"], **kw)

    with pytest.raises(ValueError):
        merge([0.0, 0.5, 0.5], zero_weight="raise")
    assert dict(merge([0.0, 0.5, 0.5])[0]) == {"A": "x", "B": "y"}
    out = merge([0.1, 0.5, 0.5], zero_weight="raise")
    assert dict(out[0]) == {"A": "x", "B": "y"} and out[1:4] == (1, 2, 0)


def test_merge_rules_and_ancestry_guard():
    cands = _probe_cands() + [Artifact({"A": "z", "B": "s"})]
    new, desc = merge_texts(random.Random(0), cands, ["A", "B"], [0.1, 0.5, 0.4, 0.9], 1, 3, 0)
    assert new == {"A": "z", "B": "s"} and desc == (3, 1)        # both changed A: the higher aggregate wins
    parents = [[None], [0], [0], [1]]                            # 1 is the parent of 3: no merge between them
    assert sample_and_attempt_merge([0.1, 0.5, 0.5, 0.6], random.Random(0), [1, 3], ([], []), cands, parents,
                                    ["A", "B"]) is None


def test_merge_subsample_buckets():
    s1 = {f"v{i}": v for i, v in enumerate([1, 1, 0, 0, 1, 0, 1])}
    s2 = {f"v{i}": v for i, v in enumerate([0, 0, 1, 1, 1, 0, 1])}
    sub = select_eval_subsample(random.Random(0), s1, s2, 5)
    assert len(sub) == len(set(sub)) == 5
    assert sum(s1[k] > s2[k] for k in sub) == 2 and sum(s2[k] > s1[k] for k in sub) == 2


def _spec_block(after: str) -> str:
    text = SPEC.read_text()
    return text[text.index(after):].split("````text\n", 1)[1].split("\n````", 1)[0]


def test_meta_prompts_are_verbatim():
    assert DEFAULT_TEMPLATE == _spec_block("**6.1 The default reflection meta-prompt.**")
    assert OPTIMIZE_ANYTHING_TEMPLATE == _spec_block("**6.2 `optimize_anything` default template.**")


def test_render_samples_markdown():
    txt = render_samples([{"Inputs": {"q": "2+2", "ctx": ["a", "b"]}, "Generated Outputs": "5", "Feedback": "wrong "}])
    assert txt == ("# Example 1\n## Inputs\n### q\n2+2\n\n### ctx\n#### Item 1\na\n\n#### Item 2\nb\n\n"
                   "## Generated Outputs\n5\n\n## Feedback\nwrong\n\n")
    p = build_prompt(DEFAULT_TEMPLATE, "Be good.", [RECORD])
    assert "```\nBe good.\n```" in p and "# Example 1\n## Inputs\nx" in p and "<side_info>" not in p


def test_parse_fenced():
    assert parse_fenced("Sure!\n```markdown\nNew instr\nline2\n```\nthanks") == ("New instr\nline2", None)
    assert parse_fenced("plain text instruction") == ("plain text instruction", None)
    assert parse_fenced("```\nonly opening") == ("only opening", None)
    txt, err = parse_fenced("<think> still thinking ```")
    assert txt is None and "incomplete" in err
    assert parse_fenced("x", finish_reason="length")[0] is None


def test_reflection_proposer_one_call_per_component_with_records():
    llm = MockLLM(lambda p, s, seed, i: "```\nNEW\n```")
    art = Artifact({"a.md": "old a\n", "b.md": "old b\n"})
    res = ReflectionProposer(llm).propose(art, {"a.md": [RECORD], "b.md": []}, ["a.md", "b.md"], seed_fn=lambda c: 1)
    assert res.new_texts == {"a.md": "NEW\n"} and res.calls == 1
    assert llm.meter.by_role["reflection"].calls == 1
    with pytest.raises(ValueError):
        ReflectionProposer(llm, template="no placeholders")


def test_acceptance_rules():
    assert StrictImprovement().accept([0, 1, 0], [1, 1, 0]) and not StrictImprovement().accept([1, 0, 0], [0, 1, 0])
    assert ImprovementOrEqual().accept([1, 0, 0], [0, 1, 0])
    assert not NoiseMargin(0.2).accept([0, 0, 0], [0.5, 0, 0]) and NoiseMargin(0.1).accept([0, 0, 0], [0.5, 0, 0])
