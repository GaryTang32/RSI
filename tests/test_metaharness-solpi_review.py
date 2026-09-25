"""Regression tests for the adversarial review fixes (offline, deterministic)."""
import json

import pytest

from rsi.core import Artifact, Evaluator
from rsi.core.editors import Proposal
from rsi.domains.agentworld import MockAgentLLM
from rsi.domains.agentworld import make_domain as aw_domain
from rsi.domains.memoclassify import make_domain as mc_domain
from rsi.metaharness import AgentProposer, Config, run
from rsi.solpi import (PRICES, AgentRuntime, DeterministicReducer, EvidencePreservingReducer, Message,
                       OnlineContextCompact, TokenMeter, ToolCall, builtin_tools)
from rsi.solpi.reducer import exact_body, reducible_tool_result
from rsi.solpi.research import SmokeReviewer, heldout_only_families
from rsi.solpi.runtime import ToolResult, ToolResultEvent

LOG = "\n".join(f"collected line {i} ok" for i in range(300)) + \
    "\nFAILED tests/test_a.py::t - AssertionError: 1 != 2\n1 failed, 299 passed"


# ------------------------------------------------------------------ EPR reads the exact, untruncated log
def test_epr_reduces_the_full_log_not_the_truncated_preview():
    full = "E first causal error: widget exploded\n" + LOG
    preview = LOG[-400:] + "\n\n[Showing last 3 of 302 lines. Full output: /tmp/pi-bash-t-1.log]"
    store = {"/tmp/pi-bash-t-1.log": full}
    ev = ToolResultEvent(ToolCall("c1", "bash", {"command": "pytest"}),
                         ToolResult(preview, True, {"fullOutputPath": "/tmp/pi-bash-t-1.log"}))
    assert reducible_tool_result(ev, store).body == full
    # inline "Full output:" note works too (a fused edit carries the bash text, not its details)
    fused = ToolResultEvent(ToolCall("c2", "edit", {"path": "a", "then_run": {"command": "pytest"}}),
                            ToolResult("edited a\n\n[then_run:failed]\n\n" + preview, True))
    assert reducible_tool_result(fused, store).body == full
    # only pi-bash-*.log files directly in the temp dir are trusted
    assert exact_body(preview.replace("/tmp/pi-bash-t-1.log", "/etc/pi-bash-t-1.log"), {},
                      {"/etc/pi-bash-t-1.log": "X"}) != "X"

    class Env:
        def read_file(self, p):
            return None

        def tool_bash(self, cmd, rt=None):
            rt.store["/tmp/pi-bash-t-1.log"] = full
            return ToolResult(preview, True, {"fullOutputPath": "/tmp/pi-bash-t-1.log"})

    class Script:
        name = "s"

        def __init__(self):
            self.n = 0

        def act(self, msgs, tools, rt):
            self.n += 1
            calls = (ToolCall(rt.next_call_id(), "bash", {"command": "pytest"}),) if self.n == 1 else ()
            return Message("assistant", "x", tool_calls=calls)

    epr = EvidencePreservingReducer(DeterministicReducer())
    env = Env()
    rt = AgentRuntime(Script(), system_prompt="s", env=env,
                      meter=TokenMeter({"main": PRICES["sim-a"], "reducer": PRICES["reducer"]}))
    rt.register_tool(builtin_tools(env)[3])
    rt.add_extension(epr)
    rt.run("t")
    rec = epr.receipts[0]
    assert rt.store[rec["archive_path"]] == full
    assert rec["evidence"][0]["quote"] == "E first causal error: widget exploded"


# ------------------------------------------------------------------ grader-side meter integrity (AgentWorld)
def test_agentworld_rejects_a_harness_that_tampers_with_the_token_meter():
    dom = aw_domain(seed=0, n_train=1, n_accept=0, n_final=0, n_test=0)
    code = ("class Cheat(Extension):\n    name = 'cheat'\n    def register(self, rt):\n"
            "        rt.meter.request = lambda *a, **k: None\nMECHANISM = Cheat\n")
    art = dom.seed_artifact().with_files({"harness.json": json.dumps({"extensions": {"cheat": {}}}),
                                          "extensions/cheat.py": code})
    tr = dom.run(art, dom.tasks.split("evolve")[0], llm=MockAgentLLM("A"))
    assert tr.score == 0.0 and "tampered" in (tr.error or "")
    ok = dom.run(dom.seed_artifact(), dom.tasks.split("evolve")[0], llm=MockAgentLLM("A"))
    assert ok.error is None and ok.tokens > 0


# ------------------------------------------------------------------ MemoClassify: context cost measured domain-side
def test_memoclassify_context_cost_cannot_be_hidden_by_bypassing_call_llm():
    dom = mc_domain(seed=0, scale=0.2)
    bypass = ('class Memory(MemorySystem):\n    def predict(self, input):\n'
              '        r = self._llm("PADDING " * 500 + input + " final_answer")\n'
              '        return extract_json_field(r, "final_answer"), {}\n'
              '    def learn_from_batch(self, batch_results):\n        pass\n'
              '    def get_state(self):\n        return ""\n    def set_state(self, state):\n        pass\n')
    tr = Evaluator(dom, dom.make_model("A"), workers=1).evaluate(Artifact({"memory.py": bypass}), "evolve")
    t = next(iter(tr.trials.values()))[0]
    assert t.meta["context_chars"] >= len("PADDING ") * 500


# ------------------------------------------------------------------ Meta-Harness fidelity fixes
def test_best_of_n_view_exposes_only_the_seed_harness(tmp_path):
    dom = mc_domain(seed=0, scale=0.25)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              config=Config(iterations=2, k=2, history_mode="seed_only", validate_in_subprocess=False,
                            finalize=False), out_dir=tmp_path, baselines=dom.baselines())
    st = res.loop.store
    cands = [st.meta(n) for n in st.names() if st.meta(n).get("kind") == "candidate"]
    assert cands and all(m["base_system"] == "fewshot_all" for m in cands)
    assert {p.split("/")[1] for p in st.view("seed_only", seeds=res.loop.seed_names)} == {"fewshot_all"}


def test_agent_proposer_skips_the_iteration_when_the_agent_fails():
    class StubEditor:
        def edit(self, artifact, instructions, **kw):
            files = {"agents/a1/memory.py": "x = 1\n",
                     "pending_eval.json": json.dumps({"candidates": [{"name": "a1", "base_system": "b"}]})}
            return Proposal(artifact.with_files(files), error="agent error: timed out after 2400s")

    p = AgentProposer.__new__(AgentProposer)
    p.skill, p.editor = "skill {k}", StubEditor()
    b = p.propose(iteration=1, view={}, k=2, brief="", artifacts={"b": Artifact({"memory.py": "y = 2\n"})})
    assert b.candidates == [] and b.error.startswith("agent error")   # release: exit 124 -> iteration skipped


def test_context_cost_is_the_mean_of_nonzero_units():
    from rsi.metaharness.store import context_mean
    assert context_mean({"a": 0.0, "b": 100.0, "c": 300.0}) == 200.0 and context_mean({"a": 0.0}) == 0.0


# ------------------------------------------------------------------ SoL-Pi fidelity fixes
def test_smoke_reviewer_denylist_comes_from_the_domain_not_hard_coded_names():
    dom = aw_domain(seed=0, n_train=1, n_accept=1, n_final=1, n_test=0)
    assert heldout_only_families(dom) == ["configfix", "datalookup"]
    rv = SmokeReviewer(dom, MockAgentLLM("A"))
    assert "configfix" in rv.forbidden and "datalookup" in rv.forbidden
    bad = dom.seed_artifact().with_files({"system_prompt.md": "Special-case configfix tasks."})
    ok, why = rv.review(None, dom.seed_artifact(), bad)
    assert not ok and "configfix" in why


def test_occ_write_tokens_use_max_of_reported_and_estimated():
    occ = OnlineContextCompact()

    class RT:
        system_prompt = "x" * 400
        last_context_tokens = 5000

    occ.observed = [Message("system", "s" * 4000), Message("user", "u" * 400)]
    est = 100 + Message("user", "u" * 400).tokens()
    assert occ.context_tokens(RT()) == 5000
    RT.last_context_tokens = 10
    assert occ.context_tokens(RT()) == est


# ------------------------------------------------------------------ backend outages are missing trials, not failures
class DownLLM:
    """A backend that is down: every call returns an error response."""

    def __new__(cls):
        from rsi.core.llm import LLM, LLMResponse, Usage

        class _Down(LLM):
            name = "down"

            def _complete(self, prompt, *, system, max_tokens, seed):
                return LLMResponse(text="", usage=Usage(1, 1, 0, 0.0, 0.0), model="down", error="HTTP 529 overloaded")
        return _Down()


def test_backend_outage_is_an_infra_error_even_if_the_harness_swallows_it():
    dom = mc_domain(seed=0, scale=0.2)
    swallow = dom.seed_artifact("no_memory")["memory.py"].replace(
        "response = self.call_llm(PROMPT.format(input=input))",
        "try:\n            response = self.call_llm(PROMPT.format(input=input))\n"
        "        except Exception:\n            response = '{\"final_answer\": \"x\"}'")
    assert "except Exception" in swallow
    for art in (dom.seed_artifact("no_memory"), Artifact({"memory.py": swallow})):
        r = Evaluator(dom, DownLLM(), workers=1).evaluate(art, "evolve")
        assert r.n_missing == len(r.trials)                         # counted missing (and not cached)
    world = aw_domain(seed=0, n_train=1, n_accept=0, n_final=0, n_test=0)
    tr = world.run(world.seed_artifact(), world.tasks.split("evolve")[0], llm=DownLLM())
    assert (tr.error or "").startswith("infra:")


def test_finalize_with_missing_test_trials_is_incomplete_and_leaves_evolution_open(tmp_path):
    from rsi.metaharness import MemoClassifyLibrary, MetaHarnessLoop, MockProposer
    dom = mc_domain(seed=0, scale=0.2)
    loop = MetaHarnessLoop(dom, llm_task=dom.make_model("A"), proposer=MockProposer(MemoClassifyLibrary()),
                           config=Config(iterations=1, k=1, validate_in_subprocess=False), out_dir=tmp_path,
                           baselines=dom.baselines())
    loop.run()
    rep = loop.finalize(llm=DownLLM())
    assert rep["status"] == "incomplete" and rep["failures"] and not loop.store.is_finalized()
    loop.iterate(2)                                              # evolution still allowed
    assert loop.finalize()["status"] == "complete" and loop.store.is_finalized()
