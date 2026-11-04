"""Autoresearch review fixes: shadow-audit spend and time never absorb concurrent loop spend (M13)."""

import re
import threading
import time

import rsi.autoresearch.loop as L
from rsi.autoresearch import Config, MockResearchAgent, ResearchAgent, ResearchTask, RunBudget, RunOutcome, run
from rsi.autoresearch.parallel import ParallelAutoresearchLoop
from rsi.autoresearch.pools import harness_edit_pool
from rsi.core import Artifact
from rsi.core.editors import Proposal
from rsi.core.llm import Usage
from rsi.domains.agentqa import AgentQADomain, SimModel


class _Costed(SimModel):
    """SimModel that bills $0.001 per call and takes a little time, so parallel runs overlap the audit."""

    def _complete(self, prompt, **kw):
        time.sleep(0.002)
        r = super()._complete(prompt, **kw)
        r.usage = Usage(1, r.usage.input_tokens, r.usage.output_tokens, 0.001, 0.0)
        return r


def test_parallel_monitor_spend_excludes_concurrent_loop_spend(tmp_path, monkeypatch):
    """M13: with workers=3 the loop LLM's whole spend counts against ``max_usd``; none of it is booked as monitor.

    The monitor runs on its own LLM here, so every cent on ``loop_llm`` is loop spend; before the fix the audit's
    ``usd()`` delta swallowed what in-flight runs spent on ``loop_llm`` meanwhile (reviewer: $0.180 vs $0.154).
    """
    dom = AgentQADomain()
    loop_llm, mon_llm = _Costed(dom.tasks), _Costed(dom.tasks)
    orig = L.make_monitor

    def mk(task, **kw):
        m = orig(task, **kw)
        if m is not None:
            m.ev.llm = mon_llm
        return m

    monkeypatch.setattr(L, "make_monitor", mk)
    seen = {}
    orig_finish = L.AutoresearchLoop.finish

    def fin(self):
        seen.update(budget_usd=self.budget_usd(), usd=self.usd(), monitor_usd=self._monitor_usd)
        return orig_finish(self)

    monkeypatch.setattr(L.AutoresearchLoop, "finish", fin)
    run(
        dom,
        dom.seed_artifact(),
        llm_task=loop_llm,
        agent=MockResearchAgent(harness_edit_pool(), seed=0),
        config=Config(max_experiments=8, plot=False, workers=3, hidden_audit=False),
        out_dir=str(tmp_path / "par"),
        task_kwargs={"k": 1},
    )
    loop_spend = loop_llm.meter.total().cost_usd
    assert mon_llm.meter.total().cost_usd > 0  # the monitor did audit
    assert loop_spend > 0
    assert abs(seen["budget_usd"] - loop_spend) < 1e-9, seen
    assert abs(seen["monitor_usd"]) < 1e-9


class _SlowTask(ResearchTask):
    """metric = ``X`` in train.py (min), no wall-clock run budget; ``audit`` logs how many runs were in flight."""

    name, metric, direction = "slow", "loss", "min"
    editable_paths, locked_paths = ("train.py",), ()
    audit_splits = ("test",)

    def __init__(self):
        self.budget = RunBudget(kind="none", amount=1.0, mem_mb=None)
        self.active, self.lock, self.audit_log, self.n = 0, threading.Lock(), [], 0

    def seed_artifact(self):
        return Artifact({"train.py": "X = 5\n"})

    def run(self, artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0):
        with self.lock:
            self.active += 1
            self.n += 1
            dt = 0.05 * (1 + self.n % 3)  # staggered, so a keep settles while other runs still train
        try:
            time.sleep(dt)
            x = float(re.search(r"^X = (\S+)", artifact["train.py"], re.M).group(1))
            return RunOutcome(x, summary={"loss": x}, memory_gb=0.1)
        finally:
            with self.lock:
                self.active -= 1

    def audit(self, artifact, *, seed=0):
        with self.lock:
            self.audit_log.append(self.active)
        return {"test": 0.0}


class _Lower(ResearchAgent):
    name = "lower"

    def __init__(self):
        self.i = 0

    def propose(self, ctx):
        self.i += 1
        src = re.sub(r"^X = \S+", f"X = {5 - 0.1 * self.i}", ctx.artifact["train.py"], flags=re.M)
        return Proposal(ctx.artifact.with_files({"train.py": src}), change=f"X -> {5 - 0.1 * self.i}")


def test_parallel_audit_never_overlaps_inflight_runs_without_a_wallclock_task_budget(tmp_path):
    """M13 (wall time): the audit's time is credited back to ``max_wall_s``, so it must not overlap loop runs."""
    task = _SlowTask()
    loop = ParallelAutoresearchLoop(
        task,
        _Lower(),
        Config(max_experiments=9, workers=3, shadow_monitor=True, plot=False, hidden_audit=False, max_wall_s=600),
        out_dir=tmp_path / "par",
    )
    res = loop.run()
    assert loop.monitor is not None and len(task.audit_log) >= 2
    assert max(task.audit_log) == 0, task.audit_log
    assert res.meta["parallel"]["deferred_audits"] >= 1
