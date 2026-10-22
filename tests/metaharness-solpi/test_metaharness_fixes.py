"""Regression tests for the Meta-Harness claims-audit fixes (each failed on the code before its fix)."""
import importlib.util
import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from rsi.core import Artifact, Evaluator, Execution, FunctionDomain, Task, TaskSuite, sandbox
from rsi.core.llm import LLM, ClaudeCLI, LLMResponse, Usage
from rsi.core.sandbox import run_cmd, run_python
from rsi.domains.memoclassify import COMPARATOR_PROGRAMS, make_domain, programs, validate_memory
from rsi.domains.memoclassify.memory import SEED_PROGRAMS
from rsi.metaharness import (SKILL_TEXT, AgentProposer, CandidateSpec, Config, InterfaceValidator, MemoClassifyLibrary,
                             MetaHarnessLoop, MockProposer, ProposalBatch, Proposer, RewriteProposer, pareto_frontier,
                             per_unit_best, render_view, run)
from rsi.metaharness.proposer import _collect, files_touched, prototype_activity
from rsi.metaharness.tracing import make_tracer

ROOT = Path(__file__).resolve().parents[2]

TWO_CALL = SEED_PROGRAMS["fewshot_all"].replace(
    '''        response = self.call_llm(PROMPT_TEMPLATE.format(examples_section=section, input=input))
        return extract_json_field(response, "final_answer"), {"num_examples": len(self.examples)}''',
    '''        response = self.call_llm(PROMPT_TEMPLATE.format(examples_section=section, input=input))
        draft = extract_json_field(response, "final_answer")
        if not draft:
            return draft, {}
        confirm = self.call_llm("Labels: " + draft + "\\n\\n" + input + "\\n\\nReply with final_answer.")
        return extract_json_field(confirm, "final_answer") or draft, {"num_examples": len(self.examples)}''')

# A stand-in `claude` executable: records its argv next to itself, acts like a coding agent in its cwd and prints
# a --verbose JSON transcript.
_FAKE_CLAUDE = '''
import json, os, sys
json.dump(sys.argv[1:], open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "argv.json"), "w"))
sys.stdin.read()
cwd = os.getcwd()
src = open(os.path.join(cwd, "_context/candidates/fewshot_all/src/memory.py")).read()
os.makedirs("agents/cand_a", exist_ok=True)
open("agents/cand_a/memory.py", "w").write(src.replace("MAX_CHARS = 30000", "MAX_CHARS = 3000"))
open("pending_eval.json", "w").write(json.dumps({"iteration": 1, "candidates": [{"name": "cand_a",
    "base_system": "fewshot_all", "hypothesis": "smaller cap", "axis": "exploitation", "components": ["axis:D"]}]}))
os.makedirs("reports", exist_ok=True)
open("reports/iter000.md", "w").write("# baselines\\nfewshot_all beats no_memory\\n")
os.makedirs("scratch", exist_ok=True)
open("scratch/proto.py", "w").write("print(1)\\n")
tr = os.path.join(cwd, "_context/candidates/fewshot_all/eval/search/traces/ds_alpha__val.jsonl")
def use(name, **inp):
    return {"type": "tool_use", "name": name, "input": inp}
calls = [use("Read", file_path=os.path.join(cwd, "_context/evolution_summary.jsonl")),
         use("Bash", command="head -n 5 " + tr), use("Bash", command="python3 scratch/proto.py"),
         use("Grep", pattern="ok", path=os.path.join(cwd, "_context/candidates"))]
result = {"type": "result", "result": "CANDIDATES: cand_a", "usage": {"input_tokens": 1000, "output_tokens": 200},
          "total_cost_usd": 0.0123}
print(json.dumps([{"type": "system", "subtype": "init"}, {"type": "assistant", "message": {"content": calls}}, result]))
'''

# Prelude of the fresh-interpreter smoke scripts.
_PRELUDE = """
import os, signal, sys, threading, time
from rsi.core import Artifact
from rsi.metaharness import InterfaceValidator
class Quick:
    def smoke(self, artifact, llm):
        return None
class Sleepy:
    def smoke(self, artifact, llm):
        time.sleep(30)
def validate(smoke, timeout_s):
    return InterfaceValidator(timeout_s=timeout_s, isolate=True).validate(smoke, Artifact({"h.py": "x = 1\\n"}), None)
"""

# Records the forked smoke children; reaped() says whether the first one was reaped.
_CHILDREN = """
real_fork, kids = os.fork, []
def fork():
    pid = real_fork()
    if pid:
        kids.append(pid)
    return pid
os.fork = fork
def reaped():
    try:
        os.waitpid(kids[0], os.WNOHANG)
        os.kill(kids[0], 9)
        return "child left running"
    except ChildProcessError:
        return "child reaped"
    except IndexError:
        return "no child"
"""

# A smoke that runs until stopped (SIGTERM exits, as in the demo's replay check); stop_after(s, f) runs f in another
# thread s seconds after the smoke started; outcome(t) validates and names the exception it raised.
_STOPPED = _PRELUDE + _CHILDREN + """
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
marker, main = sys.argv[1], threading.main_thread().ident
class Smoke:
    ignore_term = False
    def smoke(self, artifact, llm):
        if self.ignore_term:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        open(marker, "w").write("x")
        time.sleep(30)
def stop_after(seconds, then):
    def stopper():
        while not os.path.exists(marker):
            time.sleep(0.01)
        time.sleep(seconds)
        then()
    threading.Thread(target=stopper, daemon=True).start()
def interrupt():
    signal.pthread_kill(main, signal.SIGINT)
    signal.pthread_kill(main, signal.SIGTERM)
def outcome(timeout_s):
    try:
        validate(Smoke(), timeout_s)
        return "returned"
    except BaseException as e:
        return type(e).__name__
"""


def _run_inner(code: str, *args: str) -> subprocess.CompletedProcess:
    """Run ``code`` in a fresh interpreter, started in this checkout so its ``rsi`` is the one imported."""
    path = os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p)
    return subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PYTHONPATH": path}, cwd=ROOT)


def _check_inner(code: str, *expected: str, args: tuple[str, ...] = ()) -> None:
    out = _run_inner(code, *args)
    assert out.stdout.strip() in expected, (out.stdout, out.stderr[-1500:])


def _smoke(smoke, timeout_s: float, llm=None) -> tuple[bool, str]:
    """Validate a domain whose ``smoke(artifact, llm)`` is the given function, forked."""
    domain = SimpleNamespace(smoke=smoke)
    return InterfaceValidator(timeout_s=timeout_s, isolate=True).validate(domain, Artifact({"h.py": "x = 1\n"}), llm)


def _assert_child_stays_put(smoke, tmp_path) -> None:
    parent = os.getpid()
    try:
        ok, msg = _smoke(smoke, 10)
    finally:
        if os.getpid() != parent:  # the child escaped: record it and stop it here
            (tmp_path / f"escaped_{os.getpid()}").write_text("x")
            os._exit(0)
    assert ok, msg
    assert not list(tmp_path.glob("escaped_*"))


def _gone(pid: int) -> bool:
    """Wait for ``pid`` to exit; kill it and return False if it outlives the wait."""
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    os.kill(pid, 9)
    return False


def _cmdline(pid: str) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:  # the process exited after /proc was listed
        return ""


def _records(result) -> list[dict]:
    return [json.loads(line) for line in next(iter(result.trials.values()))[0].trace.splitlines()]


def _mean_meta(result, key: str) -> float:
    return sum(t[0].meta[key] for t in result.trials.values()) / len(result.trials)


def _fake_claude(tmp_path: Path) -> Path:
    script = tmp_path / "fake_claude.py"
    script.write_text(f"#!{sys.executable}{_FAKE_CLAUDE}")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _noisy_domain() -> FunctionDomain:
    """Harness text "q=<x>": score per (task, seed) = x + noise; 'lucky' (x=0.45) draws 0.95 on seed 0."""
    tasks = [Task(f"t{i}", {}, None, "f") for i in range(4)]
    suite = TaskSuite(tasks, {"evolve": [t.id for t in tasks], "test": [t.id for t in tasks]})

    def execute(artifact, task, seed, llm):
        return Execution(output=f"{artifact.get('h.txt')}|seed={seed}", trace=f"seed {seed}")

    def grade(task, out):
        q = float(out.split("=")[1].split()[0].split("|")[0])
        return (q + 0.5 if "lucky" in out and out.endswith("|seed=0") else q), ""

    return FunctionDomain(suite, execute, grade, name="noisy", description="toy")


def _noisy_loop(proposer, out_dir, incumbent: str, **cfg) -> MetaHarnessLoop:
    return MetaHarnessLoop(_noisy_domain(), llm_task=None, proposer=proposer,
                           config=Config(iterations=1, k=1, validate=False, trace=False, **cfg), out_dir=out_dir,
                           baselines={incumbent: Artifact({"h.txt": "q=0.6"})})


class _OneShot(Proposer):
    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        if iteration > 1:
            return ProposalBatch(error="done")
        return ProposalBatch(candidates=[CandidateSpec("lucky", Artifact({"h.txt": "q=0.45 lucky"}),
                                                       base_system="inc")])


@pytest.fixture(scope="module")
def dom():
    return make_domain(seed=0, scale=0.3)


@pytest.fixture(scope="module")
def mock_store(tmp_path_factory):
    """A 3-iteration offline MemoClassify run (full history, MockProposer)."""
    d = make_domain(seed=0, scale=0.3)
    res = run(d, d.seed_artifact("fewshot_all"), llm_task=d.make_model("A"),
              config=Config(iterations=3, k=2, validate_in_subprocess=False, finalize=False, trace=False),
              out_dir=tmp_path_factory.mktemp("mh_mock"), baselines=d.baselines())
    return res.loop


def test_context_cost_counts_every_model_call_not_only_the_last(dom):
    ev = Evaluator(dom, dom.make_model("A"), workers=1)
    base = ev.evaluate(Artifact({"memory.py": SEED_PROGRAMS["fewshot_all"]}), "evolve")
    two = ev.evaluate(Artifact({"memory.py": TWO_CALL}), "evolve")
    assert _mean_meta(two, "context_chars") >= _mean_meta(base, "context_chars")  # the big call is counted
    assert _mean_meta(two, "context_chars_last_call") < 0.05 * _mean_meta(base, "context_chars")  # release: gamed
    assert _mean_meta(base, "context_chars") == _mean_meta(base, "context_chars_last_call")  # single call
    ev_steps = [r for r in _records(two) if r["type"] == "eval_step" and r["n_calls"] == 2]
    assert ev_steps and all(r["context_chars"] > r["context_chars_last_call"] for r in ev_steps)


def test_a_query_answered_without_a_model_call_costs_no_context():
    """A lookup-table answer is not charged the previous query's prompt."""
    d = make_domain(seed=0, scale=0.3, leaky=True)
    ev = Evaluator(d, d.make_model("A"), workers=1)
    g0 = programs.apply_move(programs.DEFAULT_GENOME, "retrieve_topk")
    base = ev.evaluate(Artifact({"memory.py": programs.render(g0)}), "evolve")
    g = programs.leaky_genome(g0, {t: trs[0].trace for t, trs in base.trials.items()})
    r = ev.evaluate(Artifact({"memory.py": programs.render(g)}), "evolve")
    assert r.score == 1.0  # every search query answered from the table
    assert all(trs[0].meta["context_chars"] == 0 for trs in r.trials.values())
    assert all(trs[0].meta["context_chars_last_call"] == 0 for trs in r.trials.values())


def test_loop_can_select_the_release_last_call_metric(dom, tmp_path):
    lp = MetaHarnessLoop(dom, llm_task=dom.make_model("A"), proposer=MockProposer(MemoClassifyLibrary()),
                         config=Config(iterations=0, cost_metric="context_chars_last_call",
                                       validate_in_subprocess=False, trace=False),
                         out_dir=tmp_path, baselines={"two": Artifact({"memory.py": TWO_CALL})})
    lp.run_baselines()
    assert lp.store.scores("two")["context_cost"] < 200


def test_full_traces_hold_every_prompt_raw_reply_and_state_checkpoints(dom):
    r = Evaluator(dom, dom.make_model("A"), workers=1).evaluate(dom.seed_artifact("fewshot_all"), "evolve")
    rec = _records(r)
    assert {"meta", "step", "learn", "checkpoint", "eval_step", "done"} <= {x["type"] for x in rec}
    for x in rec:
        if x["type"] in ("step", "eval_step"):
            assert x["calls"] and all(c["prompt"] and c["response"] for c in x["calls"])
            assert x["calls"][-1]["prompt_len"] == x["prompt_len"]
    assert all('"final_answer"' in x["calls"][-1]["response"] for x in rec if x["type"] == "eval_step")  # raw output
    ck = [x for x in rec if x["type"] == "checkpoint"]
    assert {c["step"] for c in ck} == {0, rec[0]["n_train"] - 1}  # release default steps
    assert all(len(c["memory_state"]) == c["memory_state_chars"] for c in ck)  # not truncated
    biggest = max(json.loads(line)["memory_state_chars"] for trs in r.trials.values()
                  for line in trs[0].trace.splitlines() if '"type": "checkpoint"' in line)
    assert biggest > 3000  # the old format cut at 3000
    compact = make_domain(seed=0, scale=0.3, trace_detail="compact")
    rc = Evaluator(compact, compact.make_model("A"), workers=1).evaluate(compact.seed_artifact("fewshot_all"), "evolve")
    assert abs(rc.score - r.score) < 1e-12  # trace detail never changes scores


def test_rendered_full_history_includes_trace_excerpts(mock_store):
    view = mock_store.store.view("full")
    assert sum("/traces/" in p for p in view) >= 12
    text, read = render_view(view, 60000)
    rendered_traces = [p for p in read if "/traces/" in p]
    assert len(rendered_traces) >= 3 and len({p.split("/")[1] for p in rendered_traces}) >= 2
    trace_chars = sum(len(c) for c in text.split("=== HISTORY FILE: ") if "/traces/" in c.split(" ===")[0])
    assert trace_chars >= 0.25 * len(text)
    assert '"ok": false' in text and '"type": "eval_step"' in text  # failing eval records are shown
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

    llm, st = Echo(), mock_store.store
    b = RewriteProposer(llm).propose(iteration=4, view=st.view("full"), k=1,
                                     brief="domain\n\n" + mock_store.objective_text(),
                                     artifacts={n: st.artifact(n) for n in st.names()})
    prompt, system = llm.seen
    assert "/traces/" in prompt and "## Objective" in prompt and "Pareto dominance" in prompt
    assert "Post-eval reports" in system and "axis" in system and "Prototype" in system
    assert b.reports == {"reports/iter001.md": "# iteration 1\nwhat changed\n"}
    assert any("/traces/" in p for p in b.files_read)


def test_skill_text_keeps_the_release_steps():
    s = SKILL_TEXT.format(k=2, prototype="PROTO")
    assert "Post-eval reports" in s and "reports/" in s and "at most 30 lines" in s  # Step 0
    assert "PROTO" in s  # Step 2 slot
    assert "last 3 iterations explored the same axis" in s  # axis rotation


def test_mock_writes_post_eval_reports_into_the_store(mock_store):
    reports = mock_store.store.reports_dir()
    assert sorted(p.name for p in reports.glob("*.md")) == ["iter001.md", "iter002.md"]  # iterations 1, 2 of 3
    txt = (reports / "iter001.md").read_text()
    assert "search" in txt and "takeaway" in txt and len(txt.splitlines()) <= 30
    assert any(p.startswith("reports/") for p in mock_store.store.view("full"))


def test_agent_proposer_prototypes_writes_reports_and_logs_the_files_it_opened(tmp_path):
    d = make_domain(seed=0, scale=0.25)
    cli = ClaudeCLI("haiku", executable=str(_fake_claude(tmp_path)), retries=1)
    lp = MetaHarnessLoop(d, llm_task=d.make_model("A"), proposer=AgentProposer(cli, timeout_s=60),
                         config=Config(iterations=1, k=1, validate_in_subprocess=False, trace=False),
                         out_dir=tmp_path / "run", baselines=d.baselines())
    lp.run()
    argv = json.loads((tmp_path / "argv.json").read_text())
    assert "Bash" in argv[argv.index("--tools") + 1].split(",") and "--verbose" in argv
    allowed = argv[argv.index("--allowedTools") + 1:]
    assert "Bash(python3 *)" in allowed and not any(a.startswith("Bash(rm") for a in allowed)
    meta = json.loads((lp.store.sessions_dir() / "iter001" / "meta.json").read_text())
    # the files actually opened (Read + head), not the whole view
    assert meta["files_read"] == ["candidates/fewshot_all/eval/search/traces/ds_alpha__val.jsonl",
                                  "evolution_summary.jsonl"]
    assert meta["view_files"] > 10
    pm = meta["proposer_meta"]
    assert pm["tool_summary"] == {"Read": 1, "Bash": 2, "Grep": 1}
    assert pm["prototype_files"] == ["scratch/proto.py"] and "candidates" in pm["files_searched"]
    assert pm["prototype"]["python_runs"] == 1
    assert (lp.store.reports_dir() / "iter000.md").exists()
    assert lp.store.scores("cand_a") is not None  # the candidate was evaluated
    assert cli.meter.total().cost_usd == pytest.approx(0.0123)


def test_transcript_accounting_sees_reads_inside_scripts_and_prototypes_anywhere():
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
    p = AgentProposer(ClaudeCLI("haiku", executable=str(_fake_claude(tmp_path)), retries=1), prototype=False)
    assert "Bash" not in p.editor.tools and p.agent_cli.allowed_tools == []


def test_mock_files_read_are_what_the_proposal_used_not_everything_it_parsed(mock_store):
    st = mock_store.store
    for it in (2, 3):
        m = json.loads((st.sessions_dir() / f"iter{it:03d}" / "meta.json").read_text())
        parents = {c["base_system"] for c in m["candidates"]}
        used = {p for n in st.names() if st.meta(n).get("iteration") == it for p in st.meta(n).get("parents_read", [])}
        assert {p.split("/")[1] for p in m["files_read"] if p.startswith("candidates/")} <= used
        n_src_view = len([n for n in st.names() if (st.meta(n).get("iteration", 0) or 0) < it])
        assert m["files_read_by_kind"]["code"] < n_src_view
        assert m["n_files_scanned"] > 0 and not set(m["files_read"]) & set(m["files_scanned"])
        # Markovian: only the parents' traces
        assert {p.split("/")[1] for p in m["files_read"] if "/traces/" in p} <= parents


@pytest.mark.parametrize("reeval", [0, 4])
def test_reeval_incumbent_stops_a_lucky_single_seed_from_taking_the_incumbent_slot(tmp_path, reeval):
    lp = _noisy_loop(_OneShot(), tmp_path / f"r{reeval}", "inc", objectives=("score",), reeval_incumbent=reeval)
    lp.run()
    best = lp.store.frontier()["_best"]["system"]
    if reeval == 0:
        assert best == "lucky" and lp.n_reevaluations == 0  # faithful default: the winner's curse
    else:
        assert best == "inc"  # pooled 0.55 < 0.6
        s = lp.store.scores("lucky")
        assert s["k"] == 5 and s["single_seed_score"] == pytest.approx(0.95) and s["score"] == pytest.approx(0.55)
        assert lp.n_reevaluations >= 2


def test_exact_ties_keep_registration_order_on_the_frontier_and_per_unit():
    assert [p[0] for p in pareto_frontier([("z_old", 0.5, 10.0), ("a_copy", 0.5, 10.0)])] == ["z_old", "a_copy"]
    assert per_unit_best({"a_old": {"u": (0.5, 10.0)}, "z_copy": {"u": (0.5, 10.0)}})["u"]["best_system"] == "a_old"


def test_an_exact_copy_never_displaces_the_incumbent(tmp_path):
    class Copy(Proposer):
        def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
            return ProposalBatch(candidates=[CandidateSpec("aaa_copy", Artifact({"h.txt": "q=0.6"}),
                                                           base_system="zzz_incumbent")])

    lp = _noisy_loop(Copy(), tmp_path, "zzz_incumbent")
    lp.run()
    fr = lp.store.frontier()
    assert fr["_best"]["system"] == "zzz_incumbent"
    assert all(v["best_system"] == "zzz_incumbent" for u, v in fr.items() if not u.startswith("_"))


def test_forked_smoke_killed_on_timeout_still_reports_completed_calls():
    class Paid(LLM):
        def complete(self, prompt, **kw):
            u = Usage(1, 100, 20, 0.01)
            self.meter.add(kw.get("role", "default"), u)
            return LLMResponse(text="ok", usage=u, model="paid")

    def hangs(artifact, llm):
        llm.complete("hi", role="task")
        llm.complete("again", role="task")
        time.sleep(30)

    llm = Paid()
    ok, msg = _smoke(hangs, 1.5, llm)
    assert not ok and "timed out" in msg
    t = llm.meter.total()
    assert t.calls == 2 and t.cost_usd == pytest.approx(0.02)  # the usage delta did not die with the child


def test_forked_smoke_timeout_kills_its_sandboxed_process(tmp_path):
    """The child is stopped with SIGTERM first, so run_cmd's own cleanup kills the sandboxed process it started."""
    pidfile = tmp_path / "pid"

    def spins(artifact, llm):
        run_cmd([sys.executable, "-c", f"import os; open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                                       "while True: pass"], timeout_s=60)

    ok, msg = _smoke(spins, 1.5)
    assert not ok and "timed out" in msg
    pid = int(pidfile.read_text())
    assert _gone(pid), f"sandboxed process {pid} outlived the timed-out smoke"


def test_forked_smoke_timeout_kills_a_sandbox_started_from_a_harness_thread(tmp_path):
    """A child whose harness runs in a thread (as AgentQA's) closes its sandboxes; the parent's own is left alone."""
    spin = "import os, sys; open(sys.argv[1], 'w').write(f'{os.getpid()} {os.getcwd()}')\nwhile True: pass"
    mine, theirs = tmp_path / "smoke_pid", tmp_path / "parent_pid"

    def threaded(artifact, llm):
        th = threading.Thread(target=run_python, args=(spin,), kwargs={"args": [str(mine)], "timeout_s": 60},
                              daemon=True)
        th.start()
        while th.is_alive():
            th.join(0.05)

    threading.Thread(target=run_cmd, args=([sys.executable, "-c", spin, str(theirs)],), kwargs={"timeout_s": 20},
                     daemon=True).start()
    for _ in range(100):
        if theirs.exists() and theirs.read_text():
            break
        time.sleep(0.05)
    ok, msg = _smoke(threaded, 1.5)
    assert not ok and "timed out" in msg
    pid, scratch = mine.read_text().split(" ", 1)
    parent_pid = int(theirs.read_text().split()[0])
    try:
        assert _gone(int(pid)), f"sandboxed process {pid} outlived the timed-out smoke"
        assert not os.path.exists(scratch), f"scratch directory {scratch} was left behind"
        os.kill(parent_pid, 0)  # the parent's own sandboxed process is still running
    finally:
        try:
            os.kill(parent_pid, 9)
        except ProcessLookupError:
            pass


def test_forked_smoke_child_never_runs_the_parents_code(tmp_path, monkeypatch):
    """A signal while the child closes its sandboxes (simulated: the close raises once) cannot unwind it."""
    real, calls = sandbox.close_sandboxes, []

    def interrupted_once():
        calls.append(1)
        if len(calls) == 1:
            raise SystemExit(143)
        return real()

    monkeypatch.setattr(sandbox, "close_sandboxes", interrupted_once)
    _assert_child_stays_put(lambda artifact, llm: None, tmp_path)


def test_forked_smoke_child_ignores_the_parents_sigterm_once_leaving(tmp_path):
    """The parent's cleanup SIGTERM, arriving while the child enters its exit path, cannot unwind it."""

    def hold(frame, event, arg):
        if event == "call" and frame.f_code.co_name == "_child_exit":
            time.sleep(0.5)  # the parent got "done" and sends SIGTERM meanwhile

    _assert_child_stays_put(lambda artifact, llm: sys.setprofile(hold), tmp_path)


def test_a_stop_signal_during_the_fork_reaches_the_childs_own_handler(tmp_path):
    """A SIGTERM in the after-fork hooks is held until the child's handler is in place: the smoke never runs."""
    marker = tmp_path / "smoke_ran"
    out = _run_inner(_PRELUDE + """
signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
os.register_at_fork(after_in_child=lambda: os.kill(os.getpid(), signal.SIGTERM))
class Smoke:
    def smoke(self, artifact, llm):
        open(sys.argv[1], "w").write("x")
        time.sleep(30)
t0 = time.monotonic()
ok, msg = validate(Smoke(), 8)
print(os.getpid(), round(time.monotonic() - t0, 1), msg[:40])
""", str(marker))
    lines = out.stdout.strip().splitlines()
    assert len(lines) == 1, (out.stdout, out.stderr[-1500:])  # one process printed: no child escaped
    assert not marker.exists(), "the smoke ran although a stop signal arrived during the fork"
    assert float(lines[0].split()[1]) < 5, lines


def test_the_signal_mask_is_restored_when_a_stop_signal_surfaces_right_after_the_fork():
    """Simulated: the parent's first call after the fork (closing the write end) raises while signals are blocked."""
    _check_inner(_PRELUDE + _CHILDREN + """
parent, real_close, raised = os.getpid(), os.close, []
def close(fd):
    real_close(fd)
    blocked = signal.SIGINT in signal.pthread_sigmask(signal.SIG_BLOCK, [])
    if os.getpid() == parent and blocked and not raised:
        raised.append(fd)
        raise KeyboardInterrupt
os.close = close
t0 = time.monotonic()
try:
    validate(Sleepy(), 8)
    print("no interrupt", end=" ")
except KeyboardInterrupt:
    print("interrupted", end=" ")
print(sorted(signal.pthread_sigmask(signal.SIG_BLOCK, [])), time.monotonic() - t0 < 5, reaped())
""", "interrupted [] True child reaped")


def test_a_failed_fork_restores_the_mask_and_closes_the_pipe():
    _check_inner(_PRELUDE + """
def fork():
    raise KeyboardInterrupt
os.fork = fork
fds_before = len(os.listdir("/proc/self/fd"))
try:
    validate(Quick(), 8)
    print("no interrupt", end=" ")
except KeyboardInterrupt:
    print("interrupted", end=" ")
print(sorted(signal.pthread_sigmask(signal.SIG_BLOCK, [])), len(os.listdir("/proc/self/fd")) - fds_before)
""", "interrupted [] 0")


def test_two_stop_signals_in_a_row_still_stop_the_smoke_child(tmp_path):
    """Ctrl-C then a kill, back to back: the second surfaces as the cleanup starts, which runs again."""
    _check_inner(_STOPPED + """
stop_after(0.2, interrupt)
print(outcome(20), reaped(), sorted(signal.pthread_sigmask(signal.SIG_BLOCK, [])))
""", "SystemExit child reaped []", "KeyboardInterrupt child reaped []", args=(str(tmp_path / "smoke_started"),))


def test_a_stop_signal_during_the_childs_grace_period_still_reaps_it(tmp_path):
    """A second stop signal in the grace period of a child ignoring SIGTERM: the retry's grace is shorter (0.5 s)."""
    _check_inner(_STOPPED + """
second = []
def stop():
    signal.pthread_kill(main, signal.SIGINT)  # ends the wait; the cleanup's grace starts
    time.sleep(0.5)
    second.append(time.monotonic())
    signal.pthread_kill(main, signal.SIGTERM)  # lands inside that grace period
Smoke.ignore_term = True
stop_after(0.2, stop)
print(outcome(20), reaped(), time.monotonic() - second[0] < 1.5)  # not the first 2 s grace again
""", "SystemExit child reaped True", "KeyboardInterrupt child reaped True", args=(str(tmp_path / "smoke_started"),))


def test_two_stop_signals_pending_together_during_the_cleanup_still_reap_the_child(tmp_path):
    """Both land while the cleanup waits for a child slow to stop; the nested retries still kill and reap it."""
    _check_inner(_STOPPED + """
Smoke.ignore_term = True
stop_after(1.0, interrupt)  # past the 0.5 s timeout: inside the cleanup
print(outcome(0.5), reaped())
""", "KeyboardInterrupt child reaped", "SystemExit child reaped", args=(str(tmp_path / "smoke_started"),))


def test_an_interrupt_between_popping_and_closing_the_write_end_cannot_leak_it():
    """The write end is popped and closed with no bytecode in between for an interrupt to land on."""
    _check_inner(_PRELUDE + """
parent, fired = os.getpid(), []
def prof(frame, event, arg):
    if (event == "c_return" and getattr(arg, "__name__", "") == "pop" and os.getpid() == parent and not fired
            and frame.f_code.co_filename.endswith("validate.py")):
        fired.append(frame.f_code.co_name)
        raise KeyboardInterrupt
before = len(os.listdir("/proc/self/fd"))
sys.setprofile(prof)
try:
    validate(Quick(), 8)
except KeyboardInterrupt:
    pass
sys.setprofile(None)
print(len(os.listdir("/proc/self/fd")) - before, sorted(signal.pthread_sigmask(signal.SIG_BLOCK, [])))
""", "0 []")


def test_a_process_another_thread_forks_during_validation_still_stops_on_ctrl_c():
    """Validation changes no process-wide signal handler, so a worker forked meanwhile still stops on SIGINT."""
    _check_inner(_PRELUDE + """
import multiprocessing as mp
import rsi.metaharness.validate as v
workers, real_stop = [], v._stop_child
def stop(pid, *a, **k):  # fork a worker from another thread right now
    p = mp.get_context("fork").Process(target=time.sleep, args=(30,))
    t = threading.Thread(target=p.start)
    t.start()
    t.join()
    workers.append(p)
    return real_stop(pid, *a, **k)
v._stop_child = stop
validate(Sleepy(), 0.5)
p = workers[0]
time.sleep(0.3)
os.kill(p.pid, signal.SIGINT)
p.join(5)
print("stopped" if p.exitcode is not None else "still running")
if p.exitcode is None:
    p.kill()
""", "stopped")


def test_a_raising_handler_of_another_signal_cannot_make_the_child_escape():
    """Every signal stays blocked in the child until it is inside its own try (which ends in os._exit)."""
    out = _run_inner(_PRELUDE + """
import functools, hashlib
def alarm(*_):
    raise RuntimeError("alarm")
signal.signal(signal.SIGALRM, alarm)
# in the child, from C: arm a short timer, then stay busy in C past it (no bytecode, so no handler runs there)
os.register_at_fork(after_in_child=functools.partial(signal.setitimer, signal.ITIMER_REAL, 0.005))
os.register_at_fork(after_in_child=functools.partial(hashlib.pbkdf2_hmac, "sha256", b"x", b"y", 200000))
try:
    validate(Quick(), 8)
except RuntimeError:
    pass
os.write(1, f"{os.getpid()}\\n".encode())  # unbuffered: an escaped child may be killed before it flushes
""")
    assert len(out.stdout.split()) == 1, (out.stdout, out.stderr[-1500:])  # only the parent wrote


def test_forked_smoke_stopped_while_its_harness_thread_spawns():
    """A sandbox the harness thread is just starting is closed too (spawn and registration share one lock)."""
    tag = f"rsi_spawn_race_{os.getpid()}"
    spin = f"import time\n# {tag}\nwhile True: pass"

    def spawner(artifact, llm):
        def loop():
            while True:
                run_cmd([sys.executable, "-S", "-c", spin], timeout_s=0.1)
        th = threading.Thread(target=loop, daemon=True)
        th.start()
        while th.is_alive():
            th.join(0.05)

    for i in range(8):
        _smoke(spawner, 0.6 + 0.03 * i)
    time.sleep(0.3)
    left = [d for d in os.listdir("/proc") if d.isdigit() and tag in _cmdline(d)] if os.path.isdir("/proc") else []
    for d in left:
        os.kill(int(d), 9)
    assert not left, f"{len(left)} sandboxes outlived their stopped smokes"


def test_unknown_base_system_is_recorded_not_silently_replaced(dom, tmp_path):
    arts = {"fewshot_all": dom.seed_artifact("fewshot_all"), "no_memory": dom.seed_artifact("no_memory")}
    files = {"agents/x/memory.py": SEED_PROGRAMS["no_memory"], "agents/y/memory.py": SEED_PROGRAMS["no_memory"]}
    hdr = {"candidates": [{"name": "x", "base_system": "does_not_exist"}, {"name": "y", "base_system": "No_Memory"}]}
    cs = {c.name: c for c in _collect(files, hdr, 2, arts, set(), 1)}
    assert cs["x"].meta == {"base_fallback": True, "claimed_base_system": "does_not_exist"}
    assert cs["x"].base_system == "fewshot_all"  # completed from the first system
    assert cs["y"].base_system == "no_memory" and not cs["y"].meta  # case-insensitive match, no fallback

    class Unknown(Proposer):
        def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
            return ProposalBatch(candidates=_collect(files, hdr, 1, artifacts, set(), iteration))

    lp = MetaHarnessLoop(dom, llm_task=dom.make_model("A"), proposer=Unknown(),
                         config=Config(iterations=1, k=1, validate_in_subprocess=False), out_dir=tmp_path,
                         baselines=arts)
    lp.tr = make_tracer(lp, tmp_path, enabled=True, monitor=False, llm_task=None)
    lp.run()
    m = lp.store.meta("x")
    assert m["base_fallback"] is True and m["claimed_base_system"] == "does_not_exist"
    notes = [json.loads(line) for line in (tmp_path / "trace.jsonl").read_text().splitlines()]
    assert any(e["kind"] == "note" and e["data"].get("what") == "unknown base_system" for e in notes)


def test_budget_hint_is_an_explicit_library_parameter(dom):
    r = Evaluator(dom, dom.make_model("A"), workers=1).evaluate(dom.seed_artifact("fewshot_all"), "evolve")
    traces = {t: trs[0].trace for t, trs in r.trials.items()}
    longest = max(json.loads(line).get("prompt_len") or 0 for t in traces.values() for line in t.splitlines())
    assert programs.DEFAULT_BUDGET_HINT == 11000
    lo = MemoClassifyLibrary(budget_hint=longest // 2).diagnose(traces, {})
    hi = MemoClassifyLibrary(budget_hint=longest + 1).diagnose(traces, {})
    assert "retrieve_topk" in [m for m, _, _ in lo] and "retrieve_topk" not in [m for m, _, _ in hi]


def test_fewshot_n_comparators_exist_and_are_valid(dom):
    assert set(COMPARATOR_PROGRAMS) == {"fewshot_4", "fewshot_8", "fewshot_16", "fewshot_32", "fewshot_64"}
    ev = Evaluator(dom, dom.make_model("A"), workers=1)
    ctx = {}
    for n, a in dom.comparators().items():
        assert validate_memory(a, dom) is None
        ctx[n] = sum(t[0].meta["context_chars"] for t in ev.evaluate(a, "evolve").trials.values())
    assert ctx["fewshot_4"] < ctx["fewshot_16"] < ctx["fewshot_64"]


def test_m2_reports_both_evaluations_to_match_statistics():
    # tests/conftest.py drops cached experiment modules, so m2's own _common is the one imported
    spec = importlib.util.spec_from_file_location("m2_equal_budget",
                                                  ROOT / "experiments" / "metaharness-solpi" / "m2_equal_budget.py")
    m2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m2)
    s = m2.reach_stats({"opro": [4, 7, 8, 10, None, None]}, budget=20)["opro"]
    assert s["median_when_reached"] == 7.5
    assert s["median_never_as_budget_plus_1"] == 9.0
    assert s["n_seeds_never_reached"] == 2 and s["n_seeds"] == 6
