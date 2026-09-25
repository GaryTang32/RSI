"""Regression tests for the Meta-Harness claim-audit findings (docs/claims/metaharness.md, "Fix log").

Each test fails on the code before the fix (commit 5fd1c06) and passes after it.
"""
import importlib.util
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

from rsi.core import Artifact, Evaluator, Execution, FunctionDomain, Task, TaskSuite
from rsi.core.llm import LLM, ClaudeCLI, LLMResponse, Usage
from rsi.domains.memoclassify import COMPARATOR_PROGRAMS, make_domain, programs
from rsi.domains.memoclassify.memory import SEED_PROGRAMS
from rsi.metaharness import (SKILL_TEXT, AgentProposer, CandidateSpec, Config, ExperienceStore, InterfaceValidator,
                             MemoClassifyLibrary, MetaHarnessLoop, MockProposer, ProposalBatch, Proposer,
                             RewriteProposer, pareto_frontier, per_unit_best, render_view, run)
from rsi.metaharness.proposer import _collect

ROOT = Path(__file__).resolve().parents[1]

TWO_CALL = SEED_PROGRAMS["fewshot_all"].replace(
    '''        response = self.call_llm(PROMPT_TEMPLATE.format(examples_section=section, input=input))
        return extract_json_field(response, "final_answer"), {"num_examples": len(self.examples)}''',
    '''        response = self.call_llm(PROMPT_TEMPLATE.format(examples_section=section, input=input))
        draft = extract_json_field(response, "final_answer")
        if not draft:
            return draft, {}
        confirm = self.call_llm("Labels: " + draft + "\\n\\n" + input + "\\n\\nReply with final_answer.")
        return extract_json_field(confirm, "final_answer") or draft, {"num_examples": len(self.examples)}''')


@pytest.fixture(scope="module")
def dom():
    return make_domain(seed=0, scale=0.3)


@pytest.fixture(scope="module")
def mock_store(tmp_path_factory):
    """A 3-iteration offline MemoClassify run (full history, MockProposer)."""
    d = make_domain(seed=0, scale=0.3)
    out = tmp_path_factory.mktemp("mh_mock")
    res = run(d, d.seed_artifact("fewshot_all"), llm_task=d.make_model("A"),
              config=Config(iterations=3, k=2, validate_in_subprocess=False, finalize=False, trace=False),
              out_dir=out, baselines=d.baselines())
    return res.loop


# ------------------------------------------------------------------ N2: context cost over ALL model calls
def test_context_cost_counts_every_model_call_not_only_the_last(dom):
    """Audit N2 / c1: a big few-shot call followed by a tiny confirm call reported ~46 context chars (the
    release measures only the last call). The default metric now sums every call of a query."""
    ev = Evaluator(dom, dom.make_model("A"), workers=1)
    base = ev.evaluate(Artifact({"memory.py": SEED_PROGRAMS["fewshot_all"]}), "evolve")
    two = ev.evaluate(Artifact({"memory.py": TWO_CALL}), "evolve")
    ctx = lambda r, key: sum(t[0].meta[key] for t in r.trials.values()) / len(r.trials)  # noqa: E731
    assert ctx(two, "context_chars") >= ctx(base, "context_chars")              # the big call is counted
    assert ctx(two, "context_chars_last_call") < 0.05 * ctx(base, "context_chars")   # release metric: gamed
    assert ctx(base, "context_chars") == ctx(base, "context_chars_last_call")   # single call: identical
    rec = [json.loads(l) for l in next(iter(two.trials.values()))[0].trace.splitlines()]
    ev_steps = [r for r in rec if r["type"] == "eval_step" and r["n_calls"] == 2]
    assert ev_steps and all(r["context_chars"] > r["context_chars_last_call"] for r in ev_steps)


def test_loop_can_select_the_release_last_call_metric(dom, tmp_path):
    lp = MetaHarnessLoop(dom, llm_task=dom.make_model("A"), proposer=MockProposer(MemoClassifyLibrary()),
                         config=Config(iterations=0, cost_metric="context_chars_last_call",
                                       validate_in_subprocess=False, trace=False),
                         out_dir=tmp_path, baselines={"two": Artifact({"memory.py": TWO_CALL})})
    lp.run_baselines()
    assert lp.store.scores("two")["context_cost"] < 200


# ------------------------------------------------------------------ N7: traces hold prompts, outputs, state
def test_full_traces_hold_every_prompt_raw_reply_and_state_checkpoints(dom):
    ev = Evaluator(dom, dom.make_model("A"), workers=1)
    r = ev.evaluate(dom.seed_artifact("fewshot_all"), "evolve")
    t = next(iter(r.trials.values()))[0]
    rec = [json.loads(l) for l in t.trace.splitlines()]
    kinds = {x["type"] for x in rec}
    assert {"meta", "step", "learn", "checkpoint", "eval_step", "done"} <= kinds
    for x in rec:
        if x["type"] in ("step", "eval_step"):
            assert x["calls"] and all(c["prompt"] and c["response"] for c in x["calls"])
            assert x["calls"][-1]["prompt_len"] == x["prompt_len"]
    ev_rec = [x for x in rec if x["type"] == "eval_step"]
    assert all('"final_answer"' in x["calls"][-1]["response"] for x in ev_rec)      # raw model output
    ck = [x for x in rec if x["type"] == "checkpoint"]
    n_train = rec[0]["n_train"]
    assert {c["step"] for c in ck} == {0, n_train - 1}                               # release default steps
    assert all(len(c["memory_state"]) == c["memory_state_chars"] for c in ck)       # not truncated
    biggest = max(json.loads(l)["memory_state_chars"] for trs in r.trials.values() for l in trs[0].trace.splitlines()
                  if '"type": "checkpoint"' in l)
    assert biggest > 3000                                                            # old format cut at 3000
    compact = make_domain(seed=0, scale=0.3, trace_detail="compact")
    rc = Evaluator(compact, compact.make_model("A"), workers=1).evaluate(compact.seed_artifact("fewshot_all"), "evolve")
    assert abs(rc.score - r.score) < 1e-12                                            # detail never changes scores


# ------------------------------------------------------------------ N3: the renderer includes raw traces
def test_rendered_full_history_includes_trace_excerpts(mock_store):
    view = mock_store.store.view("full")
    n_traces = sum("/traces/" in p for p in view)
    assert n_traces >= 12
    text, read = render_view(view, 60000)
    rendered_traces = [p for p in read if "/traces/" in p]
    assert len(rendered_traces) >= 3                                   # before the fix: 0 of 42 on MemoClassify
    assert len({p.split("/")[1] for p in rendered_traces}) >= 2
    trace_chars = sum(len(c) for c in text.split("=== HISTORY FILE: ") if "/traces/" in c.split(" ===")[0])
    assert trace_chars >= 0.25 * len(text)
    assert '"ok": false' in text and '"type": "eval_step"' in text     # failing eval records are shown
    assert len(text) <= 60000


def test_rewrite_proposer_prompt_shows_traces_objective_and_reports(mock_store):
    class Echo(LLM):
        name = "echo"

        def _complete(self, prompt, *, system, max_tokens, seed):
            self.seen = (prompt, system)
            return LLMResponse(text='```json\n{"candidates": [{"name": "c1", "base_system": "fewshot_all"}]}\n```\n'
                                    "=== FILE: agents/c1/memory.py ===\n" + SEED_PROGRAMS["fewshot_all"] +
                                    "\n=== FILE: reports/iter001.md ===\n# iteration 1\nwhat changed\n",
                               usage=Usage(1, 10, 10, 0.0), model="echo")

    llm = Echo()
    brief = "domain\n\n" + mock_store.objective_text()
    b = RewriteProposer(llm).propose(iteration=4, view=mock_store.store.view("full"), k=1, brief=brief,
                                     artifacts={n: mock_store.store.artifact(n) for n in mock_store.store.names()})
    prompt, system = llm.seen
    assert "/traces/" in prompt and "## Objective" in prompt and "Pareto dominance" in prompt
    assert "Post-eval reports" in system and "axis" in system and "Prototype" in system
    assert b.reports == {"reports/iter001.md": "# iteration 1\nwhat changed\n"}
    assert any("/traces/" in p for p in b.files_read)


# ------------------------------------------------------------------ N8: reports / prototype / axis rotation
def test_skill_text_keeps_the_release_steps():
    s = SKILL_TEXT.format(k=2, prototype="PROTO")
    assert "Post-eval reports" in s and "reports/" in s and "at most 30 lines" in s      # Step 0
    assert "PROTO" in s                                                                   # Step 2 slot
    assert "last 3 iterations explored the same axis" in s                                # axis rotation


def test_mock_writes_post_eval_reports_into_the_store(mock_store):
    reps = sorted(p.name for p in mock_store.store.reports_dir().glob("*.md"))
    assert reps == ["iter001.md", "iter002.md"]                     # reports for iterations 1 and 2 (run of 3)
    txt = (mock_store.store.reports_dir() / "iter001.md").read_text()
    assert "search" in txt and "takeaway" in txt and len(txt.splitlines()) <= 30
    assert any(p.startswith("reports/") for p in mock_store.store.view("full"))


def _fake_claude(tmp_path: Path) -> Path:
    """A stand-in `claude` executable: records its argv, acts like a coding agent in its cwd and prints a
    --verbose JSON transcript (no network, no LLM)."""
    script = tmp_path / "fake_claude.py"
    script.write_text(f'''#!{sys.executable}
import json, os, sys
argv_path = {str(tmp_path / "argv.json")!r}
json.dump(sys.argv[1:], open(argv_path, "w"))
prompt = sys.stdin.read()
cwd = os.getcwd()
src = open(os.path.join(cwd, "_context/candidates/fewshot_all/src/memory.py")).read()
os.makedirs("agents/cand_a", exist_ok=True)
open("agents/cand_a/memory.py", "w").write(src.replace("MAX_CHARS = 30000", "MAX_CHARS = 3000"))
open("pending_eval.json", "w").write(json.dumps({{"iteration": 1, "candidates": [{{"name": "cand_a",
    "base_system": "fewshot_all", "hypothesis": "smaller cap", "axis": "exploitation", "components": ["axis:D"]}}]}}))
os.makedirs("reports", exist_ok=True)
open("reports/iter000.md", "w").write("# baselines\\nfewshot_all beats no_memory\\n")
os.makedirs("scratch", exist_ok=True)
open("scratch/proto.py", "w").write("print(1)\\n")
tr = os.path.join(cwd, "_context/candidates/fewshot_all/eval/search/traces/ds_alpha__val.jsonl")
msgs = [{{"type": "system", "subtype": "init"}},
        {{"type": "assistant", "message": {{"content": [
            {{"type": "tool_use", "name": "Read", "input": {{"file_path": os.path.join(cwd, "_context/evolution_summary.jsonl")}}}},
            {{"type": "tool_use", "name": "Bash", "input": {{"command": "head -n 5 " + tr}}}},
            {{"type": "tool_use", "name": "Bash", "input": {{"command": "python3 scratch/proto.py"}}}},
            {{"type": "tool_use", "name": "Grep", "input": {{"pattern": "ok", "path": os.path.join(cwd, "_context/candidates")}}}}]}}}},
        {{"type": "result", "result": "CANDIDATES: cand_a", "usage": {{"input_tokens": 1000, "output_tokens": 200}},
         "total_cost_usd": 0.0123}}]
print(json.dumps(msgs))
''')
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def test_agent_proposer_prototypes_writes_reports_and_logs_the_files_it_opened(tmp_path):
    d = make_domain(seed=0, scale=0.25)
    cli = ClaudeCLI("haiku", executable=str(_fake_claude(tmp_path)), retries=1)
    prop = AgentProposer(cli, timeout_s=60)
    lp = MetaHarnessLoop(d, llm_task=d.make_model("A"), proposer=prop,
                         config=Config(iterations=1, k=1, validate_in_subprocess=False, trace=False),
                         out_dir=tmp_path / "run", baselines=d.baselines())
    lp.run()
    argv = json.loads((tmp_path / "argv.json").read_text())
    tools = argv[argv.index("--tools") + 1].split(",")
    assert "Bash" in tools and "--verbose" in argv
    allowed = argv[argv.index("--allowedTools") + 1:]
    assert "Bash(python3 *)" in allowed and not any(a.startswith("Bash(rm") for a in allowed)
    meta = json.loads((lp.store.sessions_dir() / "iter001" / "meta.json").read_text())
    # files actually opened (Read + head), not the whole view (before: files_read = sorted(view))
    assert meta["files_read"] == ["candidates/fewshot_all/eval/search/traces/ds_alpha__val.jsonl",
                                  "evolution_summary.jsonl"]
    assert meta["view_files"] > 10
    pm = meta["proposer_meta"]
    assert pm["tool_summary"] == {"Read": 1, "Bash": 2, "Grep": 1}
    assert pm["prototype_files"] == ["scratch/proto.py"] and "candidates" in pm["files_searched"]
    assert (lp.store.reports_dir() / "iter000.md").exists()
    assert lp.store.scores("cand_a") is not None                             # the candidate was evaluated
    assert cli.meter.total().cost_usd == pytest.approx(0.0123)


def test_agent_proposer_without_prototype_has_no_bash(tmp_path):
    cli = ClaudeCLI("haiku", executable=str(_fake_claude(tmp_path)), retries=1)
    p = AgentProposer(cli, prototype=False)
    assert "Bash" not in p.editor.tools and p.agent_cli.allowed_tools == []


# ------------------------------------------------------------------ N4: honest read accounting of the mock
def test_mock_files_read_are_what_the_proposal_used_not_everything_it_parsed(mock_store):
    st = mock_store.store
    for it in (2, 3):
        m = json.loads((st.sessions_dir() / f"iter{it:03d}" / "meta.json").read_text())
        parents = {c["base_system"] for c in m["candidates"]}
        used = {p for n in st.names() if st.meta(n).get("iteration") == it for p in st.meta(n).get("parents_read", [])}
        read_cands = {p.split("/")[1] for p in m["files_read"] if p.startswith("candidates/")}
        # before the fix every parsed source and score counted as "read" (all candidates)
        assert read_cands <= used
        n_src_view = len([n for n in st.names() if (st.meta(n).get("iteration", 0) or 0) < it])
        assert m["files_read_by_kind"]["code"] < n_src_view
        assert m["n_files_scanned"] > 0 and not set(m["files_read"]) & set(m["files_scanned"])
        tr_cands = {p.split("/")[1] for p in m["files_read"] if "/traces/" in p}
        assert tr_cands <= parents                          # Markovian: only the parents' traces (documented)


# ------------------------------------------------------------------ N5 / K3: optional incumbent re-evaluation
def _noisy_domain():
    """Harness text "q=<x>": score per (task, seed) = x + noise. 'lucky' (x=0.45) draws 0.95 on seed 0."""
    tasks = [Task(f"t{i}", {}, None, "f") for i in range(4)]
    suite = TaskSuite(tasks, {"evolve": [t.id for t in tasks], "test": [t.id for t in tasks]})

    def execute(artifact, task, seed, llm):
        return Execution(output=f"{artifact.get('h.txt')}|seed={seed}", trace=f"seed {seed}")

    def grade(task, out):
        q = float(out.split("=")[1].split()[0].split("|")[0])
        return (q + 0.5 if "lucky" in out and out.endswith("|seed=0") else q), ""

    return FunctionDomain(suite, execute, grade, name="noisy", description="toy")


class _OneShot(Proposer):
    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        if iteration > 1:
            return ProposalBatch(error="done")
        return ProposalBatch(candidates=[CandidateSpec("lucky", Artifact({"h.txt": "q=0.45 lucky"}),
                                                       base_system="inc")])


@pytest.mark.parametrize("reeval", [0, 4])
def test_reeval_incumbent_stops_a_lucky_single_seed_from_taking_the_incumbent_slot(tmp_path, reeval):
    d = _noisy_domain()
    lp = MetaHarnessLoop(d, llm_task=None, proposer=_OneShot(),
                         config=Config(iterations=1, k=1, objectives=("score",), reeval_incumbent=reeval,
                                       validate=False, trace=False),
                         out_dir=tmp_path / f"r{reeval}", baselines={"inc": Artifact({"h.txt": "q=0.6"})})
    lp.run()
    best = lp.store.frontier()["_best"]["system"]
    if reeval == 0:
        assert best == "lucky" and lp.n_reevaluations == 0             # faithful default: winner's curse
    else:
        assert best == "inc"                                            # pooled 0.55 < 0.6
        s = lp.store.scores("lucky")
        assert s["k"] == 5 and s["single_seed_score"] == pytest.approx(0.95) and s["score"] == pytest.approx(0.55)
        assert lp.n_reevaluations >= 2


# ------------------------------------------------------------------ #10: exact-tie order
def test_exact_ties_keep_registration_order_on_the_frontier_and_per_unit():
    front = pareto_frontier([("z_old", 0.5, 10.0), ("a_copy", 0.5, 10.0)])
    assert [p[0] for p in front] == ["z_old", "a_copy"]                 # before: a_copy first (by name)
    pu = per_unit_best({"a_old": {"u": (0.5, 10.0)}, "z_copy": {"u": (0.5, 10.0)}})
    assert pu["u"]["best_system"] == "a_old"                             # before: z_copy (largest name)


def test_an_exact_copy_never_displaces_the_incumbent(tmp_path):
    d = _noisy_domain()

    class Copy(Proposer):
        def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
            return ProposalBatch(candidates=[CandidateSpec("aaa_copy", Artifact({"h.txt": "q=0.6"}),
                                                           base_system="zzz_incumbent")])

    lp = MetaHarnessLoop(d, llm_task=None, proposer=Copy(), config=Config(iterations=1, k=1, validate=False,
                                                                         trace=False),
                         out_dir=tmp_path, baselines={"zzz_incumbent": Artifact({"h.txt": "q=0.6"})})
    lp.run()
    fr = lp.store.frontier()
    assert fr["_best"]["system"] == "zzz_incumbent"
    assert all(v["best_system"] == "zzz_incumbent" for u, v in fr.items() if not u.startswith("_"))


# ------------------------------------------------------------------ #24: forked smoke usage on timeout
def test_forked_smoke_killed_on_timeout_still_reports_completed_calls():
    class Paid(LLM):
        def complete(self, prompt, **kw):
            u = Usage(1, 100, 20, 0.01)
            self.meter.add(kw.get("role", "default"), u)
            return LLMResponse(text="ok", usage=u, model="paid")

    class Hangs:
        def smoke(self, artifact, llm):
            llm.complete("hi", role="task")
            llm.complete("again", role="task")
            time.sleep(30)

    llm = Paid()
    ok, msg = InterfaceValidator(timeout_s=1.5, isolate=True).validate(Hangs(), Artifact({"h.py": "x = 1\n"}), llm)
    assert not ok and "timed out" in msg
    t = llm.meter.total()
    assert t.calls == 2 and t.cost_usd == pytest.approx(0.02)          # before: 0 (the delta died with the child)


# ------------------------------------------------------------------ N10: unknown base system
def test_unknown_base_system_is_recorded_not_silently_replaced(dom, tmp_path):
    arts = {"fewshot_all": dom.seed_artifact("fewshot_all"), "no_memory": dom.seed_artifact("no_memory")}
    files = {"agents/x/memory.py": SEED_PROGRAMS["no_memory"], "agents/y/memory.py": SEED_PROGRAMS["no_memory"]}
    hdr = {"candidates": [{"name": "x", "base_system": "does_not_exist"}, {"name": "y", "base_system": "No_Memory"}]}
    cs = {c.name: c for c in _collect(files, hdr, 2, arts, set(), 1)}
    assert cs["x"].meta == {"base_fallback": True, "claimed_base_system": "does_not_exist"}
    assert cs["x"].base_system == "fewshot_all"                          # completed from the first system
    assert cs["y"].base_system == "no_memory" and not cs["y"].meta       # case-insensitive match, no fallback

    class Unknown(Proposer):
        def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
            return ProposalBatch(candidates=_collect(files, hdr, 1, artifacts, set(), iteration))

    lp = MetaHarnessLoop(dom, llm_task=dom.make_model("A"), proposer=Unknown(),
                         config=Config(iterations=1, k=1, validate_in_subprocess=False), out_dir=tmp_path,
                         baselines=arts)
    from rsi.metaharness.tracing import make_tracer
    lp.tr = make_tracer(lp, tmp_path, enabled=True, monitor=False, llm_task=None)
    lp.run()
    m = lp.store.meta("x")
    assert m["base_fallback"] is True and m["claimed_base_system"] == "does_not_exist"
    notes = [json.loads(l) for l in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "note" and e["data"].get("what") == "unknown base_system" for e in notes)


# ------------------------------------------------------------------ N9: tuned hint explicit; fairer comparators
def test_budget_hint_is_an_explicit_library_parameter(dom):
    ev = Evaluator(dom, dom.make_model("A"), workers=1)
    r = ev.evaluate(dom.seed_artifact("fewshot_all"), "evolve")
    traces = {t: trs[0].trace for t, trs in r.trials.items()}
    long_prompts = max(json.loads(l).get("prompt_len") or 0 for t in traces.values() for l in t.splitlines())
    assert programs.DEFAULT_BUDGET_HINT == 11000
    lo = MemoClassifyLibrary(budget_hint=long_prompts // 2).diagnose(traces, {})
    hi = MemoClassifyLibrary(budget_hint=long_prompts + 1).diagnose(traces, {})
    assert "retrieve_topk" in [m for m, _, _ in lo] and "retrieve_topk" not in [m for m, _, _ in hi]


def test_fewshot_n_comparators_exist_and_are_valid(dom):
    from rsi.domains.memoclassify import validate_memory
    assert set(COMPARATOR_PROGRAMS) == {"fewshot_4", "fewshot_8", "fewshot_16", "fewshot_32", "fewshot_64"}
    ev = Evaluator(dom, dom.make_model("A"), workers=1)
    ctx = {}
    for n, a in dom.comparators().items():
        assert validate_memory(a, dom) is None
        r = ev.evaluate(a, "evolve")
        ctx[n] = sum(t[0].meta["context_chars"] for t in r.trials.values())
    assert ctx["fewshot_4"] < ctx["fewshot_16"] < ctx["fewshot_64"]


# ------------------------------------------------------------------ N11: M2 reports both "evaluations to match"
def test_m2_reports_both_evaluations_to_match_statistics():
    spec = importlib.util.spec_from_file_location("m2_equal_budget",
                                                  ROOT / "experiments" / "metaharness-solpi" / "m2_equal_budget.py")
    m2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m2)
    s = m2.reach_stats({"opro": [4, 7, 8, 10, None, None]}, budget=20)["opro"]
    assert s["median_when_reached"] == 7.5
    assert s["median_never_as_budget_plus_1"] == 9.0
    assert s["n_seeds_never_reached"] == 2 and s["n_seeds"] == 6
