"""Regression tests for the claims-audit fixes (docs/methods/autoresearch/claims-audit.md, section 3a)."""
import importlib.util
import re
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from autoresearch_helpers import context, quiet
from rsi.autoresearch import (AutoresearchLoop, Config, KeepContext, LLMResearchAgent, MockResearchAgent, ProgramSpec,
                              ResearchAgent, ResearchTask, ResultsLog, RunBudget, RunOutcome, Samples, ScopeGuard,
                              StrictKeep, UpstreamKeep, make_keep_rule, text_edit)
from rsi.autoresearch.guard import AGENT_FIX_KINDS
from rsi.autoresearch.parallel import ParallelAutoresearchLoop
from rsi.core import Artifact
from rsi.core.editors import Proposal, RewriteEditor
from rsi.core.llm import MockLLM
from rsi.domains.tinylm.task import DOMAIN_DIR, TinyLMTask, tinylm_edit_pool


class ToyTask(ResearchTask):
    """metric = the value of ``X`` in train.py (min); ``BROKEN`` and ``OOM`` crash."""

    name, metric, direction = "toy", "loss", "min"
    editable_paths, locked_paths = ("train.py",), ()

    def __init__(self, seed_text="X = 5\n", budget_kind="none", sleep=0.0, audit_splits=()):
        self.seed_text, self.sleep, self.audit_splits = seed_text, sleep, tuple(audit_splits)
        self.budget = RunBudget(kind=budget_kind, amount=1.0, mem_mb=None)
        self.active, self.lock, self.audit_log = 0, threading.Lock(), []

    def seed_artifact(self):
        return Artifact({"train.py": self.seed_text})

    def run(self, artifact, *, seed=0, mode="hardened", log_path=None, val_epoch=0):
        with self.lock:
            self.active += 1
        try:
            if self.sleep:
                time.sleep(self.sleep)
            src = artifact["train.py"]
            if "BROKEN" in src:
                return RunOutcome(None, log="Traceback\nSyntaxError: invalid syntax", crash_reason="SyntaxError")
            if "OOM" in src:
                return RunOutcome(None, log="Traceback\nMemoryError", crash_reason="MemoryError")
            x = float(re.search(r"^X = (\S+)", src, re.M).group(1))
            return RunOutcome(x, summary={"loss": x}, memory_gb=0.1)
        finally:
            with self.lock:
                self.active -= 1

    def audit(self, artifact, *, seed=0):
        with self.lock:
            self.audit_log.append(self.active)  # training runs in flight during this audit
        return {"test": 0.0}


def set_x(v):
    def propose(ctx):
        src = re.sub(r"^X = \S+", f"X = {v}", ctx.artifact["train.py"], flags=re.M)
        return Proposal(ctx.artifact.with_files({"train.py": src}), change=f"X -> {v}")

    return propose


class ListAgent(ResearchAgent):
    name = "list-agent"

    def __init__(self, props=(), fixer=None):
        self.props, self.fixer = list(props), fixer
        self.fix_calls, self.contexts = [], []

    def propose(self, ctx):
        self.contexts.append(ctx)
        return self.props.pop(0)(ctx) if self.props else Proposal(ctx.artifact, change="no-op")

    def fix_crash(self, ctx, candidate, description, log_tail):
        self.fix_calls.append(log_tail)
        return self.fixer(candidate) if self.fixer else None


def cfg(**kw) -> Config:
    return quiet(**{"persist": False, "shadow_monitor": False, "trace": False, **kw})


def test_upstream_keep_is_the_default_and_weighs_code_size():
    assert Config().keep_rule == "upstream" and isinstance(make_keep_rule("upstream"), UpstreamKeep)
    keep, inc = UpstreamKeep(), Samples([1.0], memory_gb=10)

    def ctx(added, removed):
        return KeepContext("min", lines_added=added, lines_removed=removed)

    def accept(value, added, removed):
        return keep.decide(Samples([value], memory_gb=10), inc, ctx(added, removed)).accept

    assert accept(1.0, 0, 3) and accept(1.0005, 0, 3)  # ~0 or slightly worse, but simpler
    assert not accept(1.01, 0, 3)  # clearly worse
    assert not accept(1.0, 0, 0)  # equal at the same size
    assert not accept(0.9995, 40, 0) and accept(0.99, 40, 0)  # only a real gain pays for 40 lines
    assert accept(0.9999, 0, 0)  # knob edit: strict
    assert not StrictKeep().decide(Samples([1.0]), inc, ctx(0, 3)).accept


def test_simplification_at_equal_bpb_is_kept_by_default(tinylm_tokens, tmp_path):
    simp = text_edit("simplify", "neutral", "train.py", "    smooth_loss = 0.9 * smooth_loss + 0.1 * loss\n", "",
                     "delete unused smooth_loss EMA (simplification)")
    AutoresearchLoop(tinylm_tokens, MockResearchAgent([simp], schedule=["simplify"]),
                     cfg(max_experiments=1, persist=True), out_dir=tmp_path / "simp").run()
    log = ResultsLog.read(tmp_path / "simp" / "results.tsv").rows()
    assert log[1].metric == log[0].metric and log[1].status == "keep"


def test_upstream_keep_memory_soft_constraint():
    keep, inc, ctx = UpstreamKeep(), Samples([1.0], memory_gb=0.1), KeepContext("min")

    def accept(value, memory_gb):
        return keep.decide(Samples([value], memory_gb=memory_gb), inc, ctx).accept

    assert not accept(0.5, 1.2)  # 12x memory
    assert not accept(0.9995, 0.15)  # +50% for a tiny gain
    assert accept(0.99, 0.15)  # +50% for a meaningful gain
    assert accept(0.9995, 0.105)  # +5% is within tolerance
    assert accept(0.5, 0.0)  # unknown memory is ignored


def test_memory_blowup_is_discarded_in_a_real_run(tinylm_tokens, tmp_path):
    ballast = text_edit("ballast", "helpful", "train.py", "LR = 0.003\n",
                        "LR = 0.006\n_ballast = np.ones((5000, 5000))  # 0.2 GB, unused\n_ballast += 1.0\n",
                        "LR x2 (+ unused buffer)")
    loop = AutoresearchLoop(tinylm_tokens, MockResearchAgent([ballast], schedule=["ballast"]),
                            cfg(max_experiments=1), out_dir=tmp_path / "ballast")
    loop.run()
    log = loop.results.rows()
    assert log[1].metric < log[0].metric  # a real gain ...
    assert log[1].memory_gb > 2 * log[0].memory_gb  # ... but memory blew up
    assert log[1].status == "discard"


def test_agent_sees_the_whole_results_tsv():
    log = ResultsLog(None)
    log.append("base000", 2.8, 0.1, "keep", "baseline")
    for i in range(60):
        log.append(f"c{i:06d}", 2.8 + i / 1000, 0.1, "discard", f"idea {i}")
    assert "baseline" in log.text() and "idea 0" in log.text()
    short = log.text(last=10)
    assert "baseline" in short and "idea 5\t" not in short and "idea 5 |" in short  # summarised, not dropped
    assert short.count("\tdiscard\t") == 10
    agent = ListAgent([set_x(9 - 0.01 * i) for i in range(45)])
    AutoresearchLoop(ToyTask(), agent, cfg(max_experiments=46)).run()
    last = agent.contexts[-1].results_tsv
    assert Config().history_rows is None
    assert "baseline" in last and len(last.strip().splitlines()) == 1 + 46  # header + every row


def test_never_stop_after_empty_proposals():
    agent = ListAgent()
    res = AutoresearchLoop(ToyTask(), agent, cfg(max_experiments=12)).run()
    assert res.stop_reason == "max_rounds" and len(agent.contexts) >= 12
    assert "think harder" in agent.contexts[-1].notes
    res = AutoresearchLoop(ToyTask(), ListAgent(), cfg(max_experiments=12, max_consecutive_invalid=5)).run()
    assert res.stop_reason == "agent_failed"  # the guard is opt-in


def test_crashing_baseline_goes_to_the_fix_path():
    agent = ListAgent(fixer=lambda c: Proposal(c.with_files({"train.py": "X = 5\n"}), change="fix typo"))
    loop = AutoresearchLoop(ToyTask("X = 5\nBROKEN\n"), agent, cfg(max_experiments=1))
    res = loop.run()
    assert len(agent.fix_calls) == 1 and "SyntaxError" in agent.fix_calls[0]
    assert loop.results.rows()[0].status == "keep" and loop.results.rows()[0].metric == 5.0
    assert res.baseline["train.py"] == "X = 5\n" and loop.counters["fixed"] == 1
    with pytest.raises(RuntimeError):  # unfixable: still stops
        AutoresearchLoop(ToyTask("X = 5\nBROKEN\n"), ListAgent(), cfg(max_experiments=1)).run()


def test_tinylm_summary_block_matches_upstream(tinylm_data):
    task = TinyLMTask(budget_s=2.0, data_root=tinylm_data)
    o = task.run(task.seed_artifact(), seed=0, mode="faithful")
    keys = [line.split(":")[0] for line in o.log.split("---\n", 1)[1].strip().splitlines()]
    assert keys == ["val_bpb", "training_seconds", "total_seconds", "peak_vram_mb", "mfu_percent", "total_tokens_M",
                    "num_steps", "num_params_M", "depth"]
    assert abs(o.summary["training_seconds"] - 2.0) <= 0.1  # the budgeted tau, warm-up excluded
    assert o.summary["mfu_percent"] > 0 and o.summary["total_tokens_M"] > 0


def test_next_batch_fetch_is_inside_the_timed_step():
    src = (DOMAIN_DIR / "train.py").read_text()
    loop = src[src.index("while batch is not None:"):]
    t0, fetch = loop.index("t0 = time.time()"), loop.index("batch = next(batches, None)")
    assert t0 < fetch < loop.index("dt = time.time() - t0")


def test_locked_loader_clock_budgets_the_fetch(tmp_path, monkeypatch):
    """The locked loader's budget clock includes the time spent producing each batch."""
    d = tmp_path / "data"
    d.mkdir()
    np.random.default_rng(0).integers(0, 256, 2_000_000).astype(np.uint8).tofile(d / "train.bin")
    for k, v in {"RSI_AR_MODE": "hardened", "RSI_AR_DATA": str(d), "RSI_AR_BUDGET": "1e9",
                 "RSI_AR_BUDGET_KIND": "wallclock"}.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("tl_prep_clock", DOMAIN_DIR / "prepare.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    batches = prep.make_dataloader(2048, 128, "train")
    for _ in range(12):  # warm-up batches are not budgeted
        next(batches)
    t = time.time()
    for _ in range(40):
        next(batches)
    wall = time.time() - t
    assert prep._CLOCK["tau"] >= 0.7 * wall


def test_rejected_rows_reference_a_real_commit(tinylm_tokens, tmp_path):
    loop = AutoresearchLoop(tinylm_tokens, MockResearchAgent(tinylm_edit_pool(), schedule=["exploit_grader"]),
                            cfg(max_experiments=1, workspace="git", persist=True), out_dir=tmp_path / "rej")
    loop.run()
    row = loop.results.rows()[-1]
    assert row.description.startswith("REJECTED") and row.metric == 0.0
    assert any(sha.startswith(row.commit) for sha in loop.ws.commits)
    subprocess.run(["git", "cat-file", "-e", f"{row.commit}^{{commit}}"], cwd=tmp_path / "rej" / "workspace",
                   check=True)
    assert loop.ws.head() == loop.inc["sha"]  # reset away at once


def test_tinylm_memory_column_is_informative(tinylm_tokens):
    assert tinylm_tokens.memory_decimals == 3
    o = tinylm_tokens.run(tinylm_tokens.seed_artifact(), seed=0, mode="hardened")
    assert 0 < o.memory_gb < 1 and round(o.memory_gb, 1) != o.memory_gb or o.memory_gb * 1000 % 100 != 0
    log = ResultsLog(None, memory_decimals=tinylm_tokens.memory_decimals)
    log.append("abc1234", o.metric, o.memory_gb, "keep", "baseline")
    assert re.match(r"^abc1234\t\d\.\d{6}\t0\.\d{3}\tkeep\tbaseline$", log.text().splitlines()[1])
    assert ResultsLog(None).memory_decimals == 1  # upstream's .1f stays the default


@pytest.mark.skipif(not Path("/proc/self/status").exists(), reason="needs Linux /proc")
def test_run_memory_is_not_the_launchers_peak(tinylm_tokens):
    """A run reports its own peak memory, not the high-water mark it inherits from the launcher."""
    ballast = np.ones((9000, 9000))  # the launcher holds ~0.65 GB
    ballast += 1.0
    o = tinylm_tokens.run(tinylm_tokens.seed_artifact(), seed=0, mode="hardened")
    del ballast
    assert 0 < o.memory_gb < 0.4, o.memory_gb


def test_readme_is_an_in_scope_file(tinylm_tokens):
    art = tinylm_tokens.seed_artifact()
    assert "README.md" in art.files and "prepare.py" in art["README.md"]
    assert "# tinylm autoresearch" in RewriteEditor(MockLLM()).build_prompt(art, "propose", {"results.tsv": "x"}, None)
    g = ScopeGuard(tinylm_tokens.editable_paths, tinylm_tokens.locked_paths)
    assert g.check(art, art.with_files({"README.md": "edited"}))  # read-only for the agent


def test_upstream_preset_keeps_program_md_lines():
    flat = " ".join(ProgramSpec.preset("upstream").render(TinyLMTask(2.0), "hardened").split())
    for line in ["When evaluating whether to keep a change, weigh the complexity cost against the improvement "
                 "magnitude.",
                 "A 0.001 val_bpb improvement that adds 20 lines of hacky code? Probably not worth it.",
                 "A 0.001 val_bpb improvement from deleting code? Definitely keep.",
                 "An improvement of ~0 but much simpler code? Keep.",
                 "is a soft constraint. Some increase is acceptable for meaningful val_bpb gains, but it should not "
                 "blow up dramatically.",
                 "If you feel like you're getting stuck in some way, you can rewind but you should probably do this "
                 "very very sparingly (if ever).",
                 "think harder - read papers referenced in the code, re-read the in-scope files for new angles, try "
                 "combining previous near-misses, try more radical architectural changes.",
                 'Do NOT ask "should I keep going?" or "is this a good stopping point?".',
                 "Each experiment runs on a single CPU machine",
                 "mfu_percent:", "total_tokens_M:", "peak_vram_mb:"]:
        assert line in flat, line


def test_agent_can_request_a_rewind():
    def rewind(ctx):
        return Proposal(None, error="no file blocks", meta={"rewind": ctx.git_log.splitlines()[-1][:7]})

    loop = AutoresearchLoop(ToyTask(), ListAgent([set_x(4), set_x(3), rewind]), cfg(max_experiments=3))
    loop.run()
    assert loop.ws.n_rewinds == 1 and loop.inc["artifact"]["train.py"] == "X = 5\n"
    assert any(n.status == "rewind" for n in loop.ledger.nodes())
    agent = LLMResearchAgent(RewriteEditor(MockLLM(lambda p, s, seed, i: "I am stuck.\nREWIND abcdef1\n")))
    prop = agent.propose(context(Artifact({"train.py": "X = 1\n"}), program="prog", git_log="abcdef1 baseline"))
    assert prop.meta.get("rewind") == "abcdef1"


def test_confirm_hook():
    seen = []
    res = AutoresearchLoop(ToyTask(), ListAgent([set_x(4)]),
                           cfg(max_experiments=1, confirm=lambda s: seen.append(s) or False)).run()
    assert res.stop_reason == "not_confirmed" and seen[0]["baseline"] == 5.0 and "baseline" in seen[0]["results_tsv"]
    res = AutoresearchLoop(ToyTask(), ListAgent([set_x(4)]), cfg(max_experiments=1, confirm=lambda s: True)).run()
    assert res.stop_reason == "max_rounds"


def test_every_crash_but_timeouts_goes_to_the_agent():
    def huge(ctx):
        return Proposal(ctx.artifact.with_files({"train.py": "X = 1\nOOM\n"}), change="huge")

    agent = ListAgent([huge])
    AutoresearchLoop(ToyTask(), agent, cfg(max_experiments=1)).run()
    assert len(agent.fix_calls) == 1 and "MemoryError" in agent.fix_calls[0]
    agent = ListAgent([huge])
    AutoresearchLoop(ToyTask(), agent, cfg(max_experiments=1, crash_fix="trivial")).run()
    assert agent.fix_calls == []  # the mechanical variant
    assert "timeout" not in AGENT_FIX_KINDS and "violation" not in AGENT_FIX_KINDS


def test_parallel_monitor_audits_never_compete_with_inflight_runs(tmp_path):
    task = ToyTask(budget_kind="wallclock", sleep=0.15, audit_splits=("test",))
    agent = ListAgent([set_x(5 - 0.1 * (i + 1)) for i in range(9)])
    loop = ParallelAutoresearchLoop(task, agent, quiet(max_experiments=9, workers=3, shadow_monitor=True),
                                    out_dir=tmp_path / "par")
    res = loop.run()
    assert loop.monitor is not None and len(task.audit_log) >= 2
    assert max(task.audit_log) == 0  # never while runs were training
    assert res.meta["parallel"]["deferred_audits"] >= 1
