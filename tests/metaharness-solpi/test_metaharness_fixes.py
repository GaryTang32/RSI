"""Regression tests for the Meta-Harness claim-audit findings (docs/methods/metaharness-solpi/claims-audit-metaharness.md, "Fix log").

Each test fails on the code before the fix (commit 5fd1c06) and passes after it.
"""
import importlib.util
import json
import os
import stat
import subprocess
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

ROOT = Path(__file__).resolve().parents[2]


def _run_inner(code: str, *args: str) -> subprocess.CompletedProcess:
    """Run ``code`` in a fresh interpreter that imports this checkout's ``rsi`` from any working directory."""
    path = os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p)
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PYTHONPATH": path})

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


def test_a_query_answered_without_a_model_call_costs_no_context():
    """Found while fixing N2: the per-query prompt was the domain's last recorded prompt, never reset, so a
    harness that answers from a lookup table (no model call) was charged the PREVIOUS query's prompt (a stale
    train-phase prompt: 1.5-4.5k chars per unit). Now each query counts only its own calls."""
    d = make_domain(seed=0, scale=0.3, leaky=True)
    ev = Evaluator(d, d.make_model("A"), workers=1)
    g0 = programs.apply_move(programs.DEFAULT_GENOME, "retrieve_topk")
    base = ev.evaluate(Artifact({"memory.py": programs.render(g0)}), "evolve")
    g = programs.leaky_genome(g0, {t: trs[0].trace for t, trs in base.trials.items()})
    r = ev.evaluate(Artifact({"memory.py": programs.render(g)}), "evolve")
    assert r.score == 1.0                                          # every search query answered from the table
    assert all(trs[0].meta["context_chars"] == 0 for trs in r.trials.values())
    assert all(trs[0].meta["context_chars_last_call"] == 0 for trs in r.trials.values())


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
    assert pm["prototype"]["python_runs"] == 1
    assert (lp.store.reports_dir() / "iter000.md").exists()
    assert lp.store.scores("cand_a") is not None                             # the candidate was evaluated
    assert cli.meter.total().cost_usd == pytest.approx(0.0123)


def test_transcript_accounting_sees_reads_inside_scripts_and_prototypes_anywhere():
    from rsi.metaharness.proposer import files_touched, prototype_activity
    view = {"candidates/a/eval/search/traces/u.jsonl": "x", "evolution_summary.jsonl": "y"}
    calls = [{"name": "Bash", "input": {"command": "python3 << 'EOF'\nf = '/w/_context/candidates/a/eval/search/"
                                                  "traces/u.jsonl'\nEOF"}},
             {"name": "Bash", "input": {"command": "ls -la /w/_context/candidates/"}},
             {"name": "Write", "input": {"file_path": "/tmp/elsewhere/test_retrieval.py"}},
             {"name": "Write", "input": {"file_path": "/tmp/rsi_agent_x/agents/c/memory.py"}},
             {"name": "Bash", "input": {"command": "cd /tmp/elsewhere && python3 test_retrieval.py"}}]
    read, searched = files_touched(calls, view)
    assert read == ["candidates/a/eval/search/traces/u.jsonl"] and "candidates" in searched
    act = prototype_activity(calls)
    assert act["python_runs"] == 2 and act["scripts_written"] == ["/tmp/elsewhere/test_retrieval.py"]


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


def test_forked_smoke_timeout_kills_its_sandboxed_process(tmp_path):
    """The forked smoke child is stopped with SIGTERM first, so the sandboxed process it started is killed by
    run_cmd's own cleanup; before, the child was SIGKILLed and its CPU-bound sandbox process kept running."""
    from rsi.core.sandbox import run_cmd
    pidfile = tmp_path / "pid"

    class Spins:
        def smoke(self, artifact, llm):
            run_cmd([sys.executable, "-c", f"import os; open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                                           "while True: pass"], timeout_s=60)

    ok, msg = InterfaceValidator(timeout_s=1.5, isolate=True).validate(Spins(), Artifact({"h.py": "x = 1\n"}), None)
    assert not ok and "timed out" in msg
    pid = int(pidfile.read_text())
    for _ in range(100):                       # the group kill lands within moments
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        os.kill(pid, 9)
        pytest.fail(f"sandboxed process {pid} outlived the timed-out smoke")


def _gone(pid: int, tries: int = 100) -> bool:
    for _ in range(tries):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    os.kill(pid, 9)
    return False


def test_forked_smoke_timeout_kills_a_sandbox_started_from_a_harness_thread(tmp_path):
    """As in AgentQA, the harness runs in a thread and the smoke's main thread only joins it: a signal handler runs
    in the main thread, so the child closes the sandboxes it started on its way out (process and scratch directory).
    A sandboxed process the parent itself is running at the fork is left alone."""
    import threading
    from rsi.core.sandbox import run_cmd, run_python
    spin = "import os, sys; open(sys.argv[1], 'w').write(f'{os.getpid()} {os.getcwd()}')\nwhile True: pass"
    mine, theirs = tmp_path / "smoke_pid", tmp_path / "parent_pid"

    class Threaded:
        def smoke(self, artifact, llm):
            th = threading.Thread(target=run_python, args=(spin,), kwargs={"args": [str(mine)], "timeout_s": 60},
                                  daemon=True)
            th.start()
            while th.is_alive():
                th.join(0.05)

    parent = threading.Thread(target=run_cmd, args=([sys.executable, "-c", spin, str(theirs)],),
                              kwargs={"timeout_s": 20}, daemon=True)
    parent.start()
    for _ in range(100):
        if theirs.exists() and theirs.read_text():
            break
        time.sleep(0.05)
    ok, msg = InterfaceValidator(timeout_s=1.5, isolate=True).validate(Threaded(), Artifact({"h.py": "x = 1\n"}), None)
    assert not ok and "timed out" in msg
    pid, scratch = mine.read_text().split(" ", 1)
    parent_pid = int(theirs.read_text().split()[0])
    try:
        assert _gone(int(pid)), f"sandboxed process {pid} outlived the timed-out smoke"
        assert not os.path.exists(scratch), f"scratch directory {scratch} was left behind"
        os.kill(parent_pid, 0)                 # the parent's own sandboxed process is still running
    finally:
        try:
            os.kill(parent_pid, 9)
        except ProcessLookupError:
            pass


def test_forked_smoke_child_never_runs_the_parents_code(tmp_path, monkeypatch):
    """A signal landing while the child closes its sandboxes (simulated: the close raises SystemExit once) must not
    let the child unwind out of its exit path into the caller's code."""
    from rsi.core import sandbox
    real, calls = sandbox.close_sandboxes, []

    def interrupted_once():
        calls.append(1)
        if len(calls) == 1:
            raise SystemExit(143)
        return real()

    monkeypatch.setattr(sandbox, "close_sandboxes", interrupted_once)

    class Quick:
        def smoke(self, artifact, llm):
            return None

    parent = os.getpid()
    try:
        ok, msg = InterfaceValidator(timeout_s=10, isolate=True).validate(Quick(), Artifact({"h.py": "x = 1\n"}), None)
    finally:
        if os.getpid() != parent:              # the child escaped: record it and stop it here
            (tmp_path / f"escaped_{os.getpid()}").write_text("x")
            os._exit(0)
    assert ok, msg
    assert not list(tmp_path.glob("escaped_*"))


def test_forked_smoke_child_ignores_the_parents_sigterm_once_leaving(tmp_path):
    """The smoke finishes, and the child is held at the entry of its exit path while the parent's cleanup SIGTERM
    arrives: the child's handlers are already no-ops there, so it does not unwind into the caller's code."""

    class HeldAtExit:
        def smoke(self, artifact, llm):
            def hold(frame, event, arg):
                if event == "call" and frame.f_code.co_name == "_child_exit":
                    time.sleep(0.5)            # the parent got "done" and sends SIGTERM meanwhile
            sys.setprofile(hold)
            return None

    parent = os.getpid()
    try:
        ok, msg = InterfaceValidator(timeout_s=10, isolate=True).validate(HeldAtExit(), Artifact({"h.py": "x = 1\n"}),
                                                                          None)
    finally:
        if os.getpid() != parent:              # the child escaped: record it and stop it here
            (tmp_path / f"escaped_{os.getpid()}").write_text("x")
            os._exit(0)
    assert ok, msg
    assert not list(tmp_path.glob("escaped_*"))


def test_a_stop_signal_during_the_fork_reaches_the_childs_own_handler(tmp_path):
    """A SIGTERM landing in the fork's after-fork hooks, under a parent that turns SIGTERM into SystemExit (as the
    replay check does), is held until the child's own handler is in place: the smoke never runs and the child
    leaves through its exit path (before, the parent's handler ran in the hook, the error was ignored and the
    smoke went on)."""
    marker = tmp_path / "smoke_ran"
    code = f"""
import os, signal, sys, time
from rsi.core import Artifact
from rsi.metaharness import InterfaceValidator
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
os.register_at_fork(after_in_child=lambda: os.kill(os.getpid(), signal.SIGTERM))

class Smoke:
    def smoke(self, artifact, llm):
        open({str(marker)!r}, "w").write("x")
        time.sleep(30)

t0 = time.monotonic()
ok, msg = InterfaceValidator(timeout_s=8, isolate=True).validate(Smoke(), Artifact({{"h.py": "x = 1\\n"}}), None)
print(os.getpid(), round(time.monotonic() - t0, 1), msg[:40])
"""
    out = _run_inner(code)
    lines = out.stdout.strip().splitlines()
    assert len(lines) == 1, (out.stdout, out.stderr[-1500:])          # one process printed: no child escaped
    assert not marker.exists(), "the smoke ran although a stop signal arrived during the fork"
    assert float(lines[0].split()[1]) < 5, lines


def test_the_signal_mask_is_restored_when_a_stop_signal_surfaces_right_after_the_fork(tmp_path):
    """Blocking SIGINT/SIGTERM across the fork covers only the validating thread: another thread may take a stop
    signal meanwhile, and the handler then runs in the validating thread at its next chance, which can be the
    parent's first call after the fork. Simulated deterministically: that call (closing the pipe's write end)
    raises KeyboardInterrupt while the signals are still blocked. The mask must come back unblocked and the child
    must still be stopped."""
    code = """
import os, signal, sys, time
from rsi.core import Artifact
from rsi.metaharness import InterfaceValidator
parent, real_close, raised = os.getpid(), os.close, []
real_fork, kids = os.fork, []
def fork():
    pid = real_fork()
    if pid:
        kids.append(pid)
    return pid
os.fork = fork
def close(fd):
    real_close(fd)
    blocked = signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, [])
    if os.getpid() == parent and blocked and not raised:   # the parent's first call after the fork
        raised.append(fd)
        raise KeyboardInterrupt
os.close = close

class Smoke:
    def smoke(self, artifact, llm):
        time.sleep(30)

t0 = time.monotonic()
try:
    InterfaceValidator(timeout_s=8, isolate=True).validate(Smoke(), Artifact({"h.py": "x = 1\\n"}), None)
    print("no interrupt", end=" ")
except KeyboardInterrupt:
    print("interrupted", end=" ")
print(sorted(signal.pthread_sigmask(signal.SIG_BLOCK, [])), time.monotonic() - t0 < 5, end=" ")
try:
    os.waitpid(kids[0], os.WNOHANG)
    os.kill(kids[0], 9)
    print("child left running")
except ChildProcessError:
    print("child reaped")
"""
    out = _run_inner(code)
    assert out.stdout.strip() == "interrupted [] True child reaped", (out.stdout, out.stderr[-1500:])


_REAPED = """
try:
    os.waitpid(kids[0], os.WNOHANG)
    os.kill(kids[0], 9)
    reaped = "child left running"
except ChildProcessError:
    reaped = "child reaped"
except IndexError:
    reaped = "no child"
"""

_FORK_SPY = """
real_fork, kids = os.fork, []
def fork():
    pid = real_fork()
    if pid:
        kids.append(pid)
    return pid
os.fork = fork
"""


def test_a_failed_fork_restores_the_mask_and_closes_the_pipe():
    """A fork that fails (or a stop signal surfacing at it) leaves no pipe end open and the signals unblocked."""
    code = """
import os, signal, sys
from rsi.core import Artifact
from rsi.metaharness import InterfaceValidator
def fork():
    raise KeyboardInterrupt
os.fork = fork
fds_before = len(os.listdir("/proc/self/fd"))
class Smoke:
    def smoke(self, artifact, llm):
        return None
try:
    InterfaceValidator(timeout_s=8, isolate=True).validate(Smoke(), Artifact({"h.py": "x = 1\\n"}), None)
    print("no interrupt", end=" ")
except KeyboardInterrupt:
    print("interrupted", end=" ")
print(sorted(signal.pthread_sigmask(signal.SIG_BLOCK, [])), len(os.listdir("/proc/self/fd")) - fds_before)
"""
    out = _run_inner(code)
    assert out.stdout.strip() == "interrupted [] 0", (out.stdout, out.stderr[-1500:])


def test_two_stop_signals_in_a_row_still_stop_the_smoke_child(tmp_path):
    """Ctrl-C then a kill, back to back, while the smoke runs: the first interrupts the wait, the second must not
    interrupt the cleanup (it is deferred and raised once the child is stopped and reaped)."""
    code = """
import os, signal, sys, threading, time
from rsi.core import Artifact
from rsi.metaharness import InterfaceValidator
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))       # as the demo's replay check does
""" + _FORK_SPY + """
marker = sys.argv[1]
class Smoke:
    def smoke(self, artifact, llm):
        open(marker, "w").write("x")
        time.sleep(30)
main = threading.main_thread().ident
def stopper():
    while not os.path.exists(marker):
        time.sleep(0.01)
    time.sleep(0.2)
    signal.pthread_kill(main, signal.SIGINT)
    signal.pthread_kill(main, signal.SIGTERM)
threading.Thread(target=stopper, daemon=True).start()
try:
    InterfaceValidator(timeout_s=20, isolate=True).validate(Smoke(), Artifact({"h.py": "x = 1\\n"}), None)
    res = "returned"
except BaseException as e:
    res = type(e).__name__
""" + _REAPED + """
print(res, reaped, sorted(signal.pthread_sigmask(signal.SIG_BLOCK, [])))
"""
    out = _run_inner(code, str(tmp_path / "smoke_started"))
    assert out.stdout.strip() in ("SystemExit child reaped []", "KeyboardInterrupt child reaped []"), (
        out.stdout, out.stderr[-1500:])


def test_a_ctrl_c_during_an_after_fork_hook_is_not_lost():
    """Another thread takes a SIGINT while the parent runs its after-fork hooks (logging's, say): the handler
    used to run inside the hook, where Python reports and drops the exception, so the Ctrl-C was lost and
    validation went on. Deferred, it is raised once the fork is done."""
    code = """
import os, signal, sys, threading, time
from rsi.core import Artifact
from rsi.metaharness import InterfaceValidator
""" + _FORK_SPY + """
go, sent = threading.Event(), threading.Event()
def sender():          # started before the validation, so SIGINT is not blocked in it: it takes the signal
    go.wait()
    os.kill(os.getpid(), signal.SIGINT)
    sent.set()
threading.Thread(target=sender, daemon=True).start()
def hook():
    go.set()
    sent.wait(5)
    # a check for pending signals in the main thread (a GIL switch to a busy thread does the same): the handler
    # runs here, inside the hook
    signal.pthread_sigmask(signal.SIG_BLOCK, [])
os.register_at_fork(after_in_parent=hook)
class Smoke:
    def smoke(self, artifact, llm):
        return None
try:
    InterfaceValidator(timeout_s=8, isolate=True).validate(Smoke(), Artifact({"h.py": "x = 1\\n"}), None)
    res = "returned"
except KeyboardInterrupt:
    res = "interrupted"
""" + _REAPED + """
print(res, reaped)
"""
    out = _run_inner(code)
    assert out.stdout.strip() == "interrupted child reaped", (out.stdout, out.stderr[-1500:])


def test_forked_smoke_stopped_while_its_harness_thread_spawns(tmp_path):
    """A sandbox the harness thread is just starting when the smoke is stopped is closed too (spawn and
    registration happen under one lock)."""
    import threading
    from rsi.core.sandbox import run_cmd
    tag = f"rsi_spawn_race_{os.getpid()}"
    spin = f"import time\n# {tag}\nwhile True: pass"

    class Spawner:
        def smoke(self, artifact, llm):
            def loop():
                while True:
                    run_cmd([sys.executable, "-S", "-c", spin], timeout_s=0.1)
            th = threading.Thread(target=loop, daemon=True)
            th.start()
            while th.is_alive():
                th.join(0.05)

    for i in range(8):
        InterfaceValidator(timeout_s=0.6 + 0.03 * i, isolate=True).validate(Spawner(), Artifact({"h.py": "x = 1\n"}),
                                                                            None)
    time.sleep(0.3)
    left = [d for d in os.listdir("/proc") if d.isdigit()
            and tag in open(f"/proc/{d}/cmdline", "rb").read().decode(errors="replace")] if os.path.isdir("/proc") else []
    for d in left:
        os.kill(int(d), 9)
    assert not left, f"{len(left)} sandboxes outlived their stopped smokes"


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
    # experiments/<method>/_common.py share a module name: another suite's _common may already be cached
    saved = {k: sys.modules.pop(k) for k in ("_common", "mh_common") if k in sys.modules}
    try:
        spec.loader.exec_module(m2)
    finally:
        for k in ("_common", "mh_common"):
            sys.modules.pop(k, None)
        sys.modules.update(saved)
    s = m2.reach_stats({"opro": [4, 7, 8, 10, None, None]}, budget=20)["opro"]
    assert s["median_when_reached"] == 7.5
    assert s["median_never_as_budget_plus_1"] == 9.0
    assert s["n_seeds_never_reached"] == 2 and s["n_seeds"] == 6
