"""rsi.domains.agentqa.domain: grading, harness execution and budgets, infra handling, SimModel, and the domain's
defining property (a generic tool-using harness transfers to unseen families, a practice-only trick does not)."""
from __future__ import annotations

import pytest

from rsi.core import Artifact, Evaluator, Execution, LLMResponse, MockLLM, Usage
from rsi.core.llm import estimate_tokens
from rsi.domains.agentqa import AgentQADomain, SimModel, Tools, extract_answer, is_correct, make_suite
from rsi.domains.agentqa.domain import BASE_ACC, MAX_LLM_CALLS, MAX_TOOL_CALLS, HarnessBudgetExceeded, normalize


@pytest.mark.parametrize("output, expected", [
    ("blah\nANSWER: 42", "42"),
    ("ANSWER: 1\nmore thinking\nANSWER: 2\ntrailing", "2"),          # last ANSWER line wins
    ("final answer = 17", "17"),
    ("**ANSWER:** 5", "5"),
    ("**ANSWER**: 6\nextra line", "6"),
    ("**Answer**: Monday", "Monday"),
    ("ANSWER:\n7", "7"),
    ("no label\n\nlast line  ", "last line"),
    ("", ""),
])
def test_extract_answer(output, expected):
    assert extract_answer(output) == expected


@pytest.mark.parametrize("raw, norm", [
    ("  Monday. ", "monday"), ("`abc`", "abc"), ("**Monday**.", "monday"), ("'Monday'.", "monday"),
    ("The answer is **Monday**", "the answer is monday"), ("$1,234", "1,234"), ("\\boxed{Tuesday}", "tuesday"),
    ("\u22125", "-5"), ("a   b\tc", "a b c"), ("2**10", "2**10"),
])
def test_normalize(raw, norm):
    assert normalize(raw) == norm


@pytest.mark.parametrize("target, output, ok", [
    ("1234", "ANSWER: 1,234", True), ("1234", "The total is 1234 crates.", True), ("1234", "ANSWER: 1234.0", True),
    ("-17", "ANSWER: -17", True), ("-17", "ANSWER: \u221217", True), ("17", "ANSWER: -17", False),
    ("250", "ANSWER: $250.", True), ("42", "ANSWER: **42**", True), ("42", "**ANSWER**: 42\nDone.", True),
    ("42", "ANSWER: 41", False), ("42", "ANSWER: forty-two", False), ("42", "I computed 42 then 43", False),
    ("42", "", False), ("3", "ANSWER: 3 (mod 7)", False),                # last number is used
    ("Monday", "ANSWER: monday", True), ("Monday", "ANSWER: **Monday**.", True),
    ("Monday", "ANSWER: The day is Monday", True), ("Monday", "ANSWER: Mondays", False),
    ("Monday", "ANSWER: `Monday`", True), ("Monday", "ANSWER: Tuesday", False),
    ("river arrow orbit", "ANSWER: river  arrow orbit", True),
    ("river arrow orbit", "ANSWER: orbit arrow river", False),
    ("aabcz", "ANSWER: 'aabcz'", True), ("aabcz", "ANSWER: aabc", False),
])
def test_is_correct(target, output, ok):
    assert is_correct(target, output) is ok


def test_grade_feedback():
    d = AgentQADomain(make_suite(n_evolve=2, n_holdout=1, n_ood_per_family=1))
    t = d.tasks.split("evolve")[0]
    assert d.grade(t, Execution(output=f"ANSWER: {t.target}")) == (1.0, f"Correct (answer {t.target!r}).")
    score, fb = d.grade(t, Execution(output="ANSWER: nope"))
    assert score == 0.0 and "expected" in fb and "'nope'" in fb


@pytest.fixture(scope="module")
def dom():
    return AgentQADomain(make_suite(n_evolve=6, n_holdout=2, n_ood_per_family=2))


@pytest.fixture(scope="module")
def sim(dom):
    return SimModel(dom.tasks)


def H(body: str) -> Artifact:
    return Artifact({"harness.py": body, "prompts/task.md": "Q: {question}\nReply with ANSWER: <value>.\n",
                     "prompts/system.md": "Be exact."})


def _task(dom):
    return dom.tasks.split("evolve")[0]


def _solve(body: str) -> Artifact:
    return H("def solve(q, llm, tools, files):\n" + body)


@pytest.mark.parametrize("harness, err_prefix", [
    (None, "harness.py missing"),
    ("x = 1\n", "harness.py defines no solve()"),
    ("def solve(q, llm, tools, files)\n    pass\n", "SyntaxError"),
    ("def solve(q, llm, tools, files):\n    return 1 / 0\n", "ZeroDivisionError"),
    ("import sys\ndef solve(q, llm, tools, files):\n    sys.exit(3)\n", "SystemExit"),
    ("def solve(q, llm, tools, files):\n    return files['missing.md']\n", "KeyError"),
])
def test_harness_failures_are_graded_zero(dom, sim, harness, err_prefix):
    art = Artifact({"prompts/task.md": "{question}"}) if harness is None else H(harness)
    tr = dom.run(art, _task(dom), llm=sim)
    assert tr.score == 0.0 and tr.error.startswith(err_prefix), tr.error


def test_missing_llm_is_an_error(dom):
    tr = dom.run(AgentQADomain.seed_artifact(), _task(dom), llm=None)
    assert tr.score == 0.0 and "needs a task LLM" in tr.error


def test_output_coercion_and_files_access(dom, sim):
    t = _task(dom)
    tr = dom.run(_solve(f"    return {int(t.target)}\n"), t, llm=sim)
    assert tr.output == t.target and tr.score == 1.0 and tr.steps == 0 and tr.tokens == 0
    tr = dom.run(_solve("    return None\n"), t, llm=sim)
    assert tr.output == "" and tr.score == 0.0 and tr.error is None
    assert dom.run(_solve("    return files['prompts/system.md']\n"), t, llm=sim).output == "Be exact."


def test_llm_calls_are_metered_seeded_and_traced(dom):
    llm = MockLLM(lambda p, s, seed, i: f"ANSWER: {seed}")
    t = _task(dom)
    art = _solve("    llm('first', system='S')\n    return llm(files['prompts/task.md'].replace('{question}', q))\n")
    tr = dom.run(art, t, seed=3, llm=llm)
    assert [c["seed"] for c in llm.calls] == [3001, 3002]            # seed * 1000 + call index
    assert tr.output == "ANSWER: 3002" and tr.steps == 2 and tr.meta == {"llm_calls": 2, "tool_calls": 0}
    assert tr.tokens == sum(estimate_tokens((c["system"] or "") + c["prompt"]) + estimate_tokens(c["reply"])
                            for c in llm.calls)
    assert "[llm call 1]" in tr.trace and "SYSTEM: S" in tr.trace and t.input[:40] in tr.trace
    assert llm.meter.snapshot()["task"]["calls"] == 2


def test_llm_call_budget(dom):
    llm = MockLLM(lambda p, s, seed, i: "x")
    tr = dom.run(_solve("    for _ in range(100):\n        llm(q)\n    return 'done'\n"), _task(dom), llm=llm)
    assert tr.error == "HarnessBudgetExceeded: too many LLM calls" and tr.score == 0.0
    assert len(llm.calls) == MAX_LLM_CALLS                           # the 13th call never reaches the model
    swallow = _solve("    out = ''\n"
                     "    for _ in range(20):\n"
                     "        try:\n            out = llm(q)\n"
                     "        except Exception as e:\n            out = type(e).__name__\n"
                     "    return out\n")
    llm2 = MockLLM(lambda p, s, seed, i: "x")
    tr = dom.run(swallow, _task(dom), llm=llm2)
    assert len(llm2.calls) == MAX_LLM_CALLS and tr.output == "HarnessBudgetExceeded"


def test_tool_call_budget_and_tool_output():
    tools = Tools(timeout_s=10)
    assert tools.python("print(6 * 7)") == "42"
    assert "ZeroDivisionError" in tools.python("1/0")
    assert "[python]" in tools.log[0] and "[output]\n42" in tools.log[0]
    for _ in range(MAX_TOOL_CALLS - 2):
        tools.python("pass")
    with pytest.raises(HarnessBudgetExceeded):
        tools.python("print(1)")


def test_tool_budget_inside_harness(dom, sim):
    tr = dom.run(_solve("    for i in range(20):\n        tools.python('pass')\n    return 'x'\n"), _task(dom), llm=sim)
    assert tr.error == "HarnessBudgetExceeded: too many tool calls"


class _Down(MockLLM):
    def _complete(self, prompt, *, system, max_tokens, seed):
        return LLMResponse("", Usage(1, 1, 0), "down", error="503 overloaded")


def test_backend_failure_is_a_missing_trial(dom):
    r = Evaluator(dom, _Down(), workers=1).evaluate(AgentQADomain.seed_artifact(), "evolve", k=1)
    assert r.n_missing == len(dom.tasks.splits["evolve"]) and r.score == 0.0
    assert all(t.error.startswith("infra:") for trs in r.trials.values() for t in trs)


def test_swallowed_backend_failure_is_still_missing(dom):
    """A harness that catches the backend error still leaves a retryable missing trial, not a cached wrong answer."""
    art = _solve("    try:\n        return llm(q)\n    except Exception:\n        return '0'\n")
    assert dom.run(art, _task(dom), llm=_Down()).error == "infra: llm backend error: 503 overloaded"
    assert Evaluator(dom, _Down(), workers=1).evaluate(art, "evolve").n_missing == len(dom.tasks.splits["evolve"])


@pytest.fixture(scope="module")
def big():
    s = make_suite(n_evolve=400, n_holdout=0, n_ood_per_family=300, seed=11)
    return s, SimModel(s)


def _family(s, *fams):
    return [t for t in s.tasks.values() if t.family in fams]


def _acc(model, tasks, prefix="", system=None, seed=0):
    return sum(is_correct(t.target, model.complete(prefix + t.input, system=system, seed=seed).text)
               for t in tasks) / len(tasks)


def test_sim_direct_accuracy_by_family(big):
    s, m = big
    for fam in ("numeric", "dates", "numbertheory", "strings", "lists"):
        assert abs(_acc(m, _family(s, fam)) - BASE_ACC[fam]) < 0.1, fam


def test_sim_step_by_step_and_verify_bonuses(big):
    s, m = big
    ts = _family(s, "dates", "numbertheory")                                         # far from the 0.95 cap
    base = _acc(m, ts)
    sbs = _acc(m, ts, "Think step by step.\n")                                      # +0.12
    both = _acc(m, ts, "Reason step by step, then verify your result.\n")           # +0.17
    assert sbs - base > 0.07 and both - sbs > 0.0
    assert _acc(m, ts, system="Always think step by step.") - base > 0.07           # the system prompt counts too


def test_sim_code_path_and_tool_echo(big):
    s, m = big
    ts = _family(s, "numbertheory")
    good = sum(f"print({t.target!r})" in m.complete("Write Python code for:\n" + t.input, seed=1).text for t in ts)
    assert 0.85 < good / len(ts) < 0.99                             # p = 0.93
    assert m.complete("Question: whatever\nTool output: 12345\n").text.endswith("ANSWER: 12345")
    assert m.complete("Result = abc def\nmore").text.endswith("ANSWER: abc def")


def test_sim_answer_format_verbosity_and_unknown(big):
    s, m = big
    t = _family(s, "strings")[0]
    plain = m.complete(t.input, seed=0).text
    asked = m.complete(t.input + "\nEnd with a line 'ANSWER: <value>'.", seed=0).text
    assert "ANSWER:" not in plain and "\nANSWER: " in asked
    verbose = m.complete("Think step by step.\n" + t.input, seed=0).text
    assert len(plain) > 10000 and len(verbose) < 2000                # direct strings answers burn tokens
    assert m.complete("What is the capital of France?").text == "I am not sure what you are asking.\nANSWER: unknown"


def test_sim_is_deterministic_and_seeded(big):
    s, m = big
    ts = _family(s, "numeric")[:60]
    a = [m.complete(t.input, seed=5).text for t in ts]
    assert a == [m.complete(t.input, seed=5).text for t in ts]
    m2 = SimModel(s)
    assert a == [m2.complete(t.input, seed=5).text for t in ts]
    assert a != [m.complete(t.input, seed=6).text for t in ts]


def test_sim_skill_scales_accuracy(big):
    s, _ = big
    ts = _family(s, "strings", "lists")
    strong, weak = _acc(SimModel(s), ts), _acc(SimModel(s, skill=0.5), ts)
    assert strong - weak > 0.25 and abs(weak - 0.4) < 0.1


def test_sim_fuzzy_question_match(big):
    s, m = big
    t = next(t for t in _family(s, "numeric") if len(t.input) > 70)
    assert "```python" in m.complete("Solve this: " + t.input[:65] + " [rest omitted]\nWrite python code.").text


def test_seed_artifact_describe_components(dom):
    seed = AgentQADomain.seed_artifact()
    assert set(seed) == {"harness.py", "prompts/system.md", "prompts/task.md"}
    desc = dom.describe()
    assert "solve(question, llm, tools, files)" in desc and "numeric" in desc and "ANSWER" in desc
    assert set(AgentQADomain.structural_components) <= set(AgentQADomain.components)
    assert set(dom.tasks.splits["evolve"]) <= set(dom.leakage_terms("evolve"))
    assert dom.smoke(seed, SimModel(dom.tasks)) is None


TOOL_HARNESS = '''
def solve(question, llm, tools, files):
    reply = llm("Write python code that prints the answer to this question.\\n" + question)
    if "```python" not in reply:
        return reply
    code = reply.split("```python", 1)[1].split("```", 1)[0]
    out = tools.python(code)
    return llm(question + "\\nTool output: " + out)
'''

LOOKUP_HARNESS = '''
ANSWERS = {ANSWERS!r}
def solve(question, llm, tools, files):
    if question in ANSWERS:
        return "ANSWER: " + ANSWERS[question]
    system = files.get("prompts/system.md", "")
    reply = llm(files["prompts/task.md"].replace("{question}", question), system=system)
    lines = [l for l in reply.strip().splitlines() if l.strip()]
    return lines[-1] if lines else ""
'''


def _mean_tokens(r, fams) -> float:
    toks = [t.tokens for trs in r.trials.values() for t in trs if t.family in fams]
    return sum(toks) / len(toks)


def test_generic_skill_transfers_and_memorisation_does_not():
    """A tool-using harness improves practice and unseen families (at lower token cost on strings/lists); an answer
    lookup table only inflates the practice split."""
    suite = make_suite(n_evolve=12, n_holdout=8, n_ood_per_family=4, seed=2)
    dom, sim, seed = AgentQADomain(suite), SimModel(suite), AgentQADomain.seed_artifact()
    tool = Artifact({**seed.files, "harness.py": TOOL_HARNESS})
    table = {suite.get(t).input: suite.get(t).target for t in suite.splits["evolve"]}
    lookup = Artifact({**seed.files, "harness.py": LOOKUP_HARNESS.replace("{ANSWERS!r}", repr(table))})
    ev = Evaluator(dom, sim, workers=4, allow_sealed=True)
    res = {name: {sp: ev.evaluate(a, sp) for sp in ("evolve", "holdout", "ood")}
           for name, a in (("seed", seed), ("tool", tool), ("lookup", lookup))}
    S = {n: {sp: r.score for sp, r in v.items()} for n, v in res.items()}
    assert res["tool"]["ood"].trials[suite.splits["ood"][0]][0].meta["tool_calls"] == 1
    assert S["tool"]["evolve"] >= S["seed"]["evolve"] + 0.2
    assert S["tool"]["ood"] >= S["seed"]["ood"] + 0.2 and S["tool"]["holdout"] >= S["seed"]["holdout"] + 0.2
    assert S["lookup"]["evolve"] == 1.0
    assert abs(S["lookup"]["holdout"] - S["seed"]["holdout"]) < 0.2
    assert abs(S["lookup"]["ood"] - S["seed"]["ood"]) < 1e-9
    cheap = {"strings", "lists"}
    assert _mean_tokens(res["tool"]["ood"], cheap) < 0.5 * _mean_tokens(res["seed"]["ood"], cheap)
