"""Task-level recursion: Propose-Probe-Compose per task, parallel and sequential, validation and selection."""
from __future__ import annotations

import pytest

from rsi.core.artifact import Artifact
from rsi.core.llm import MockLLM
from rsi.harness2 import MockEditor, OpenWorld
from rsi.harness2 import context as C
from rsi.harness2.roles import Session, anon_ids, parse_experience, run_stage
from rsi.harness2.tree import validate

from harness2_helpers import RecordingImprover


def _labels(rec):
    return [s.label for s in rec.stages]


@pytest.mark.parametrize("k", [1, 2, 3])
def test_parallel_stage_structure_and_identity(go, k):
    res, _ = go(k=k, max_tasks=2)
    for rec in res.meta["records"]:
        editors = [f"{d}{i}" for i in range(1, k + 1) for d in ("inv", "dom")]
        expected = editors + [f"agg{j}" for j in range(1, k + 1)] + (["select"] if k > 1 else []) + ["reflect"]
        assert _labels(rec) == expected
        assert sorted(rec.rollouts) == sorted([f"base{i}" for i in range(1, k + 1)] + editors +
                                              [f"agg{j}" for j in range(1, k + 1)])
        c = rec.counts()
        assert (c["rollouts"], c["sessions"]) == (4 * k, 3 * k)
        assert c["selector"] == (1 if k > 1 else 0) and c["reflector"] == 1
        assert rec.adopted in [f"agg{j}" for j in range(1, k + 1)]     # always a composed re-execution
        assert rec.submitted is rec.rollouts[rec.adopted]


@pytest.mark.parametrize("k", [1, 3])
def test_sequential_stage_structure_and_checkpoints(go, k):
    res, imp = go(k=k, mode="sequential", max_tasks=1)
    rec = res.meta["records"][0]
    rounds = [lab for t in range(1, k + 1) for lab in (f"inv{t}", f"dom{t}", f"c{t}")]
    assert _labels(rec) == rounds + (["select"] if k > 1 else []) + ["reflect"]
    assert (rec.counts()["rollouts"], rec.counts()["sessions"]) == (4 * k, 3 * k)
    assert rec.adopted in [f"c{t}" for t in range(1, k + 1)]
    editors = [p for role, p in imp.prompts if role == "editor"]
    for t in range(2, k + 1):           # round t's editors edit checkpoint c_{t-1} and see it as on-policy evidence
        ctx = C.parse(editors[2 * (t - 1)])
        shown = C.subtree(ctx, "context/harness")
        assert shown == {p: v.rstrip("\n") for p, v in rec.trees[f"c{t - 1}"].items()}   # rendered text blocks
        assert f"context/pool/c{t - 1}/rollout.md" in ctx and "This is round" in editors[2 * (t - 1)]


def test_editors_see_references_not_each_other_composers_see_all_candidates(go):
    res, imp = go(k=2, max_tasks=1)
    eds = [p for role, p in imp.prompts if role == "editor"]
    comps = [p for role, p in imp.prompts if role == "composer"]
    assert len(eds) == 4 and len(comps) == 2
    for p in eds:
        ctx = C.parse(p)
        assert {"context/rollouts/base1.md", "context/rollouts/base2.md"} <= set(ctx)
        assert not any(k.startswith("context/pool/") for k in ctx)
    for p in comps:
        ctx = C.parse(p)
        pool = {k.split("/")[2] for k in ctx if k.startswith("context/pool/")}
        assert pool == {"inv1", "dom1", "inv2", "dom2"}
        assert all(f"context/pool/{lab}/harness.diff" in ctx and f"context/decisions/{lab}.md" in ctx for lab in pool)
        assert not any("agg" in k for k in ctx)                  # composers never see each other
    assert "# YOUR DIMENSION: INV" in eds[0] and "# YOUR DIMENSION: DOM" in eds[1]
    assert "You are candidate 2 of 2" in eds[2]


def test_composed_harness_merges_scoped_edits_from_evidence(go):
    res, _ = go(k=1, max_tasks=1)
    rec = res.meta["records"][0]
    inv, dom, agg = (rec.trees[x]["prompts/task.md"] for x in ("inv1", "dom1", "agg1"))
    base = rec.base_tree["prompts/task.md"]
    new = lambda t: [ln for ln in t.splitlines() if ln not in base.splitlines()]   # noqa: E731
    assert new(inv) and new(dom) and set(new(inv)) | set(new(dom)) == set(new(agg))
    assert "ANSWER" in new(inv)[0] and "step by step" in new(dom)[0]      # INV = procedure, DOM = practitioner
    assert not rec.kept_base() and rec.submitted.delivered


def test_keep_base_still_submits_a_fresh_reexecution(go):
    """An editor with nothing to fix replies KEEP_BASE; the composed tree is H0 and its fresh re-run is submitted."""
    keep = RecordingImprover()
    keep.editor = lambda prompt: "<notes>\nKEEP_BASE -- nothing to fix\n</notes>"
    res, _ = go(k=1, max_tasks=1, improver=keep)
    rec = res.meta["records"][0]
    assert all(s.kept_base for s in rec.stages if s.role in ("inv", "dom", "composer"))
    assert rec.kept_base() and rec.adopted == "agg1" and rec.submitted is not rec.rollouts["base1"]


def test_invalid_proposal_retries_once_then_inherits(go):
    res, _ = go(k=1, max_tasks=1, improver=RecordingImprover(editor=MockEditor(invalid="first")))
    eds = [s for s in res.meta["records"][0].stages if s.role in ("inv", "dom")]
    assert all(s.attempts == 2 and not s.fell_back for s in eds)          # repaired on the retry
    res, imp = go(k=1, max_tasks=1, improver=RecordingImprover(editor=MockEditor(invalid="always")), out="always")
    rec = res.meta["records"][0]
    eds = [s for s in rec.stages if s.role in ("inv", "dom")]
    assert all(s.attempts == 2 and s.fell_back and s.tree == rec.base_tree for s in eds)
    assert all("template braces" in s.error for s in eds)
    retry = [p for role, p in imp.prompts if role == "editor"][1]
    assert "# YOUR PREVIOUS ATTEMPT WAS REJECTED" in retry
    assert rec.counts()["rollouts"] == 4                    # a fallen-back candidate is still probed
    assert "inherited its starting harness" in C.parse([p for r, p in imp.prompts if r == "composer"][0])[
        "context/decisions/inv1.md"]


def test_off_surface_and_unknown_adopt_are_rejected(dom):
    base = dom.seed_artifact()
    replies = iter(["<notes>x</notes>\n=== FILE: secrets/key.md ===\nhi\n",
                    "<notes>y</notes>\nADOPT: nobody\n"])
    llm = MockLLM(lambda p, s, seed, i: next(replies))
    with OpenWorld(dom) as g:
        s = Session(llm, g, dom, editable=["prompts/*", "harness.py"], retry_invalid=1)
        st = run_stage(s, label="inv1", role="inv", prompt="You are adapting an autonomous agent's harness.\n",
                       ctx={"context/task.md": "q"}, start=base, fallback=base)
    assert st.fell_back and st.attempts == 2 and "unknown candidate" in st.error and st.tree == base


def test_validation_rules(dom):
    base = dom.seed_artifact()
    ok = base.with_files({"prompts/task.md": base["prompts/task.md"] + "Be exact.\n"})
    assert validate(ok, base, domain=dom) == []
    bad = {"template": base.with_files({"prompts/system.md": "{{ x }}"}),
           "lost placeholders": base.with_files({"prompts/task.md": "no question here"}),
           "adds placeholders": base.with_files({"prompts/system.md": "{nope}"}),
           "does not compile": base.with_files({"harness.py": "def solve(:\n"}),
           "no code file loads": base.with_files({"prompts/extra.md": "unused"}),
           "over the cap": base.with_files({"prompts/system.md": "x" * 30000}),
           "must define a top-level solve": base.with_files({"harness.py": "def other():\n    pass\n"})}
    for needle, tree in bad.items():
        errs = " | ".join(validate(tree, base, domain=dom))
        assert needle in errs, (needle, errs)
    loaded = base.with_files({"prompts/extra.md": "x", "harness.py": base["harness.py"] + "\n# prompts/extra.md\n"})
    assert validate(loaded, base, domain=dom) == []


def test_selector_is_anonymised_and_deliverable_only(go):
    res, imp = go(k=3, max_tasks=1)
    rec = res.meta["records"][0]
    sel_prompt = next(p for role, p in imp.prompts if role == "selector")
    ctx = C.parse(sel_prompt)
    assert set(C.subtree(ctx, "context/candidates")) == {f"{c}.md" for c in "ABC"}
    assert not any(k.startswith(("context/harness", "context/rollouts", "context/pool", "context/library",
                                 "context/decisions")) for k in ctx)
    assert not any(lab in sel_prompt for lab in ("agg1", "agg2", "agg3", "inv1", "base1"))
    assert rec.selection["ids"] == anon_ids(rec.task_id, 3, "agg", ["agg1", "agg2", "agg3"])
    assert rec.adopted in ("agg1", "agg2", "agg3") and rec.selection["ok"] and not rec.selection["fallback"]
    chosen_id = next(c for c, lab in rec.selection["ids"].items() if lab == rec.adopted)
    assert f"```choice\n{chosen_id}\n```" in rec.stages[-2].reply


def test_selector_failure_falls_back_to_first_final(go):
    bad = RecordingImprover()
    bad.selector = lambda prompt: "I cannot decide."
    res, _ = go(k=2, max_tasks=1, improver=bad)
    rec = res.meta["records"][0]
    assert rec.selection["fallback"] and rec.adopted == "agg1" and rec.status == "ok"
    assert rec.stages[-2].role == "selector" and rec.stages[-2].attempts == 1


def test_parse_experience_needs_cue_lesson_evidence():
    full = "<experience>\ncue: a\nlesson: b\nevidence: c\nconfidence: low\n</experience>"
    assert parse_experience(full) == {"cue": "a", "lesson": "b", "evidence": "c", "confidence": "low"}
    assert parse_experience("<experience>\ncue: a\nlesson: b\n</experience>") == {}
    assert parse_experience("<edits>..</edits>\nNO_ENTRY") == {}


def test_base_failed_task_stops_without_a_deliverable(dom):
    from rsi.domains.agentqa import SimModel
    from rsi.harness2 import Config, run

    broken = Artifact({"harness.py": "def solve(q, llm, tools, files):\n    raise RuntimeError('boom')\n",
                       "prompts/task.md": "{question}\n"})
    res = run(dom, broken, llm_task=SimModel(dom.tasks), llm_propose=RecordingImprover(),
              config=Config(split="holdout", max_tasks=2))
    assert all(r.status == "base-failed" and r.submitted is None for r in res.meta["records"])
    assert res.meta["report"]["submitted"][0] == 0.0 and res.meta["library_entries"] == 0


def test_transport_failure_is_retried_then_the_stage_inherits(go):
    from rsi.core.llm import LLMResponse, Usage

    class Flaky(RecordingImprover):
        """Every first call of a prompt fails in transport; with ``always`` every call fails."""

        def __init__(self, always=False):
            super().__init__()
            self.always, self.seen = always, set()

        def _complete(self, prompt, *, system, max_tokens, seed):
            if self.always or prompt not in self.seen:
                self.seen.add(prompt)
                return LLMResponse(text="", usage=Usage(1), model="flaky", error="503")
            return super()._complete(prompt, system=system, max_tokens=max_tokens, seed=seed)

    res, imp = go(k=1, max_tasks=1, improver=Flaky())
    rec = res.meta["records"][0]
    assert not any(s.fell_back for s in rec.stages) and imp.meter.total().calls == 2 * len(rec.stages)
    res, _ = go(k=1, max_tasks=1, improver=Flaky(always=True), out="dead")
    rec = res.meta["records"][0]
    eds = [s for s in rec.stages if s.role in ("inv", "dom", "composer")]
    assert all(s.fell_back and s.error.startswith("transport") and s.attempts == 1 for s in eds)
    assert rec.kept_base() and rec.submitted.delivered and rec.reflection.get("error", "").startswith("transport")
