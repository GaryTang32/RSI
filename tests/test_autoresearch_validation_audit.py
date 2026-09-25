"""Stage-B (independent audit) regression tests for autoresearch.

* The write-only shadow monitor must not consume the loop's own budgets: its audit
  time used to count against ``max_wall_s`` and its LLM spend against ``max_usd``,
  so a run with the monitor on stopped earlier than the same run with it off
  (the sealed-split audit changed the ledger). Found in the Stage-B audit
  (validation/autoresearch/AUDIT.md).
* The proposer's prompt carries the results.tsv history and never a sealed-split
  number (checked against the monitor's own values).
"""
import time

from rsi.autoresearch import AutoresearchLoop, Config, MockResearchAgent
from rsi.autoresearch.agent import LLMResearchAgent, scripted_llm
from rsi.autoresearch.landscape import LandscapeTask
from rsi.core import RewriteEditor
from rsi.core.llm import Usage
from rsi.trace import load_trace


class _SlowAuditLandscape(LandscapeTask):
    """Landscape whose hidden audit is slow and (optionally) bills an LLM meter."""

    def __init__(self, *a, audit_s=0.0, audit_usd=0.0, meter_llm=None, run_s=0.0, **k):
        super().__init__(*a, **k)
        self.audit_s, self.audit_usd, self.meter_llm, self.run_s = audit_s, audit_usd, meter_llm, run_s

    def run(self, *a, **k):
        if self.run_s:
            time.sleep(self.run_s)
        return super().run(*a, **k)

    def audit(self, artifact, *, seed=0):
        time.sleep(self.audit_s)
        if self.meter_llm is not None and self.audit_usd:
            self.meter_llm.meter.add("audit", Usage(1, 10, 10, self.audit_usd, 0.0))
        return super().audit(artifact, seed=seed)


def _loop(tmp_path, name, task, monitor, llms=(), **cfg):
    c = Config(plot=False, hidden_audit=False, shadow_monitor=monitor, **cfg)
    return AutoresearchLoop(task, MockResearchAgent(task.mock_edit_pool(), seed=1), c, out_dir=tmp_path / name,
                            llms=llms)


def test_monitor_time_does_not_consume_wall_budget(tmp_path):
    # each audit takes 0.6 s; the whole wall budget is 1.0 s. Before the fix the
    # baseline audit plus one keep's audit ate the budget and the monitored run
    # stopped after ~1 experiment, while the unmonitored run did ~15.
    mk = lambda: _SlowAuditLandscape(seed=3, audit_s=0.6, run_s=0.05)  # noqa: E731
    on = _loop(tmp_path, "on", mk(), True, max_experiments=None, max_wall_s=1.0)
    res_on = on.run()
    off = _loop(tmp_path, "off", mk(), False, max_experiments=None, max_wall_s=1.0)
    res_off = off.run()
    n_on, n_off = res_on.meta["n_experiments"], res_off.meta["n_experiments"]
    assert n_on >= 0.6 * n_off and n_on >= 5, (n_on, n_off)
    assert on._monitor_wall >= 0.6                      # the monitor did run (baseline at least)
    st = [e for e in load_trace(res_on.out_dir) if e["kind"] == "state"][-1]["data"]["budget"]
    assert st["monitor_wall_s"] >= 0.6


def test_monitor_spend_does_not_count_against_max_usd(tmp_path):
    from rsi.core import MockLLM

    meter = MockLLM(name="judge")
    task = _SlowAuditLandscape(seed=3, audit_usd=0.5, meter_llm=meter)
    loop = _loop(tmp_path, "usd", task, True, llms=[meter], max_experiments=6, max_usd=0.4)
    res = loop.run()
    # the baseline audit alone bills $0.5 > max_usd; the loop must still run its 6 experiments
    assert res.stop_reason == "max_rounds" and res.meta["n_experiments"] == 6
    assert loop.usd() >= 0.5 and loop.budget_usd() == 0.0     # honest total, monitor excluded from the budget


def test_proposer_prompt_has_history_and_no_sealed_numbers(tmp_path):
    task = LandscapeTask(seed=3)
    llm = scripted_llm(task.mock_edit_pool(), seed=0)
    seen = []
    inner = llm.responder

    def rec(prompt, system, seed, i):
        seen.append(prompt)
        return inner(prompt, system, seed, i)

    llm.responder = rec
    loop = AutoresearchLoop(task, LLMResearchAgent(RewriteEditor(llm)), Config(max_experiments=8, plot=False),
                            out_dir=tmp_path / "p", llms=[llm])
    res = loop.run()
    ev = load_trace(res.out_dir)
    sealed = [v["S"] for e in ev if e["kind"] == "monitor" for v in e["data"]["sealed"].values()]
    assert sealed and len(seen) >= 8
    for i, p in enumerate(seen):
        assert "--- CONTEXT: results.tsv ---" in p and "commit\t" in p
        for s in sealed:
            assert f"{s:.4f}" not in p and repr(s) not in p
    # the n-th prompt shows every earlier results.tsv row (history_rows=40 > 8)
    rows = (res.out_dir and (tmp_path / "p" / "results.tsv").read_text().splitlines())
    assert rows[-2] in seen[-1]


def test_sanitized_reply_debris_is_visible_in_the_trace(tmp_path):
    """Stage A said the sanitizer's removals are "visible in the trace"; they were only in
    Proposal.meta (the proposal event never carried them). Stage-B fix: the proposal event
    has a ``sanitized`` field."""
    from rsi.core import MockLLM

    task = LandscapeTask(seed=3)
    llm0 = scripted_llm(task.mock_edit_pool(), seed=0)

    def dirty(prompt, system, seed, i):
        return llm0.responder(prompt, system, seed, i).rstrip("\n") + "\n```\nCo-Authored-By: X <x@y>\n"

    llm = MockLLM(dirty)
    res = AutoresearchLoop(task, LLMResearchAgent(RewriteEditor(llm)), Config(max_experiments=3, plot=False),
                           out_dir=tmp_path / "s", llms=[llm]).run()
    props = [e["data"] for e in load_trace(res.out_dir) if e["kind"] == "proposal"]
    assert props and all(p["sanitized"] for p in props if not p["error"])
    assert all("Co-Authored-By" not in p["diff"] and "```" not in p["diff"] for p in props)
