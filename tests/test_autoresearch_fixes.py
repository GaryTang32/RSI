"""Regression tests for the claim-audit findings fixed on 2026-09-28
(docs/claims/autoresearch.md "Fix log", N1-N10 and the PARTIAL rows P5/P22/P24,
plus validation/autoresearch/AUDIT.md #23). Each test fails on the pre-fix code."""
import re
import subprocess
import threading
import time
from pathlib import Path

import pytest

from rsi.autoresearch import (AutoresearchLoop, Config, LandscapeTask, MockResearchAgent, ProgramSpec, ResearchTask,
                              ResultsLog, RunBudget, RunOutcome, Samples, StrictKeep, UpstreamKeep, landscape_edit_pool,
                              make_keep_rule)
from rsi.autoresearch.agent import AgentContext, LLMResearchAgent, ResearchAgent, text_edit
from rsi.autoresearch.keep import KeepContext
from rsi.autoresearch.parallel import ParallelAutoresearchLoop
from rsi.core import Artifact
from rsi.core.editors import Proposal, RewriteEditor
from rsi.core.llm import MockLLM


# ----------------------------------------------------------------------------- a tiny in-process task
class ToyTask(ResearchTask):
    """metric = the value of ``X`` in train.py (lower is better); ``MEM = g`` sets memory_gb;
    ``BROKEN`` -> SyntaxError crash, ``OOM`` -> MemoryError crash."""

    name, metric, direction = "toy", "loss", "min"
    editable_paths, locked_paths = ("train.py",), ()

    def __init__(self, seed_text="X = 5\n", budget_kind="none", sleep=0.0, audit_splits=()):
        self.seed_text = seed_text
        self.budget = RunBudget(kind=budget_kind, amount=1.0, mem_mb=None)
        self.sleep = sleep
        self.audit_splits = tuple(audit_splits)
        self.active = 0
        self.lock = threading.Lock()
        self.audit_log: list[int] = []

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
            m = re.search(r"^MEM = (\S+)", src, re.M)
            return RunOutcome(x, summary={"loss": x}, memory_gb=float(m.group(1)) if m else 0.1)
        finally:
            with self.lock:
                self.active -= 1

    def audit(self, artifact, *, seed=0):
        with self.lock:
            self.audit_log.append(self.active)          # runs training concurrently with this audit
        return {"test": 0.0}


def set_x(v):
    return lambda ctx: Proposal(ctx.artifact.with_files({"train.py": re.sub(r"^X = \S+", f"X = {v}",
                                                                              ctx.artifact["train.py"], flags=re.M)}),
                                change=f"X -> {v}")


class ListAgent(ResearchAgent):
    name = "list-agent"

    def __init__(self, props=(), fixer=None):
        self.props = list(props)
        self.fixer = fixer
        self.fix_calls: list[str] = []
        self.contexts: list[AgentContext] = []

    def propose(self, ctx):
        self.contexts.append(ctx)
        f = self.props.pop(0) if self.props else None
        return f(ctx) if f else Proposal(ctx.artifact, change="no-op")

    def fix_crash(self, ctx, candidate, description, log_tail):
        self.fix_calls.append(log_tail)
        return self.fixer(candidate) if self.fixer else None


def cfg(**kw):
    base = dict(plot=False, hidden_audit=False, persist=False, shadow_monitor=False, trace=False)
    base.update(kw)
    return Config(**base)


@pytest.fixture(scope="module")
def tinylm_tokens(tmp_path_factory):
    from rsi.domains.tinylm.task import TinyLMTask

    t = TinyLMTask(budget_s=30_000, budget_kind="tokens", data_root=tmp_path_factory.mktemp("tinylm_data"))
    t.prepare()
    return t


# ----------------------------------------------------------------------------- N2 simplicity criterion
def test_upstream_keep_is_the_default_and_weighs_code_size():
    assert Config().keep_rule == "upstream" and isinstance(make_keep_rule("upstream"), UpstreamKeep)
    k, inc = UpstreamKeep(), Samples([1.0], memory_gb=10)
    ctx = lambda a, r: KeepContext("min", lines_added=a, lines_removed=r)  # noqa: E731
    assert k.decide(Samples([1.0], memory_gb=10), inc, ctx(0, 3)).accept         # ~0 but simpler: keep
    assert k.decide(Samples([1.0005], memory_gb=10), inc, ctx(0, 3)).accept      # slightly worse, simpler: keep
    assert not k.decide(Samples([1.01], memory_gb=10), inc, ctx(0, 3)).accept    # clearly worse: discard
    assert not k.decide(Samples([1.0], memory_gb=10), inc, ctx(0, 0)).accept     # equal, same size: reset
    assert not k.decide(Samples([0.9995], memory_gb=10), inc, ctx(40, 0)).accept  # tiny gain + 40 lines: no
    assert k.decide(Samples([0.99], memory_gb=10), inc, ctx(40, 0)).accept       # real gain pays for 40 lines
    assert k.decide(Samples([0.9999], memory_gb=10), inc, ctx(0, 0)).accept      # knob edit: strict
    assert not StrictKeep().decide(Samples([1.0]), inc, ctx(0, 3)).accept        # the bare rule is unchanged


def test_simplification_at_equal_bpb_is_kept_by_default(tinylm_tokens, tmp_path):
    """Audit check B: deleting the dead smooth_loss EMA gives an identical val_bpb under a token
    budget; the old default (strict) discarded it, upstream says "Keep"."""
    simp = text_edit("simplify", "neutral", "train.py", "    smooth_loss = 0.9 * smooth_loss + 0.1 * loss\n", "",
                     "delete unused smooth_loss EMA (simplification)")
    loop = AutoresearchLoop(tinylm_tokens, MockResearchAgent([simp], schedule=["simplify"]),
                            cfg(max_experiments=1, persist=True), out_dir=tmp_path / "simp")
    loop.run()
    rows = ResultsLog.read(tmp_path / "simp" / "results.tsv").rows()
    assert rows[1].metric == rows[0].metric and rows[1].status == "keep"


# ----------------------------------------------------------------------------- N1 VRAM soft constraint
def test_upstream_keep_memory_soft_constraint():
    k, inc = UpstreamKeep(), Samples([1.0], memory_gb=0.1)
    ctx = KeepContext("min")
    assert not k.decide(Samples([0.5], memory_gb=1.2), inc, ctx).accept          # 12x: blew up, discard
    assert not k.decide(Samples([0.9995], memory_gb=0.15), inc, ctx).accept      # +50% for a tiny gain: no
    assert k.decide(Samples([0.99], memory_gb=0.15), inc, ctx).accept            # +50% for a meaningful gain
    assert k.decide(Samples([0.9995], memory_gb=0.105), inc, ctx).accept         # +5%: within tolerance
    assert k.decide(Samples([0.5], memory_gb=0.0), inc, ctx).accept              # unknown memory: ignored


def test_memory_blowup_is_discarded_in_a_real_run(tinylm_tokens, tmp_path):
    """Audit check C: a real gain plus a large unused buffer was kept (0.1 -> 1.2 GB)."""
    ballast = text_edit("ballast", "helpful", "train.py", "LR = 0.003\n",
                        "LR = 0.006\n_ballast = np.ones((5000, 5000))  # 0.2 GB, unused\n_ballast += 1.0\n",
                        "LR x2 (+ unused buffer)")
    loop = AutoresearchLoop(tinylm_tokens, MockResearchAgent([ballast], schedule=["ballast"]),
                            cfg(max_experiments=1), out_dir=tmp_path / "ballast")
    loop.run()
    rows = loop.results.rows()
    assert rows[1].metric < rows[0].metric                   # the LR change is a real gain ...
    assert rows[1].memory_gb > 2 * rows[0].memory_gb         # ... but memory blew up
    assert rows[1].status == "discard"


# ----------------------------------------------------------------------------- N3 full history
def test_agent_sees_the_whole_results_tsv(tmp_path):
    rl = ResultsLog(None)
    rl.append("base000", 2.8, 0.1, "keep", "baseline")
    for i in range(60):
        rl.append(f"c{i:06d}", 2.8 + i / 1000, 0.1, "discard", f"idea {i}")
    assert "baseline" in rl.text() and "idea 0" in rl.text()                     # whole file
    short = rl.text(last=10)
    assert "baseline" in short and "idea 5\t" not in short and "idea 5 |" in short   # summarised, not dropped
    assert short.count("\tdiscard\t") == 10
    task = ToyTask()
    agent = ListAgent([set_x(9 - 0.01 * i) for i in range(45)])
    AutoresearchLoop(task, agent, cfg(max_experiments=46)).run()
    last = agent.contexts[-1].results_tsv
    assert Config().history_rows is None
    assert "baseline" in last and len(last.strip().splitlines()) == 1 + 46          # header + every row


# ----------------------------------------------------------------------------- N4 NEVER STOP + baseline fix
def test_never_stop_after_empty_proposals():
    agent = ListAgent()                                                             # proposes nothing, ever
    res = AutoresearchLoop(ToyTask(), agent, cfg(max_experiments=12)).run()
    assert res.stop_reason == "max_rounds" and len(agent.contexts) >= 12            # old: agent_failed after 5
    assert "think harder" in agent.contexts[-1].notes
    res = AutoresearchLoop(ToyTask(), ListAgent(), cfg(max_experiments=12, max_consecutive_invalid=5)).run()
    assert res.stop_reason == "agent_failed"                                        # the guard is opt-in


def test_crashing_baseline_goes_to_the_fix_path():
    agent = ListAgent(fixer=lambda c: Proposal(c.with_files({"train.py": "X = 5\n"}), change="fix typo"))
    loop = AutoresearchLoop(ToyTask("X = 5\nBROKEN\n"), agent, cfg(max_experiments=1))
    res = loop.run()                                                                # old: RuntimeError
    assert len(agent.fix_calls) == 1 and "SyntaxError" in agent.fix_calls[0]
    assert loop.results.rows()[0].status == "keep" and loop.results.rows()[0].metric == 5.0
    assert res.baseline["train.py"] == "X = 5\n" and loop.counters["fixed"] == 1
    with pytest.raises(RuntimeError):                                               # unfixable: still stops
        AutoresearchLoop(ToyTask("X = 5\nBROKEN\n"), ListAgent(), cfg(max_experiments=1)).run()


# ----------------------------------------------------------------------------- N5 / N6 tinylm summary block + timing
def test_tinylm_summary_block_matches_upstream(tmp_path):
    from rsi.domains.tinylm.task import TinyLMTask

    t = TinyLMTask(budget_s=2.0, data_root=tmp_path / "d")
    t.prepare()
    o = t.run(t.seed_artifact(), seed=0, mode="faithful")
    keys = [l.split(":")[0] for l in o.log.split("---\n", 1)[1].strip().splitlines()]
    assert keys == ["val_bpb", "training_seconds", "total_seconds", "peak_vram_mb", "mfu_percent", "total_tokens_M",
                    "num_steps", "num_params_M", "depth"]
    # upstream prints the budgeted tau (warm-up excluded); the old code printed loop wall time (2.2-2.3 s)
    assert abs(o.summary["training_seconds"] - 2.0) <= 0.1
    assert o.summary["mfu_percent"] > 0 and o.summary["total_tokens_M"] > 0


def test_next_batch_fetch_is_inside_the_timed_step():
    src = (Path(__file__).parent.parent / "rsi/domains/tinylm/train.py").read_text()
    loop = src[src.index("while batch is not None:"):]
    t0, fetch, dt = (loop.index("t0 = time.time()"), loop.index("batch = next(batches, None)"),
                     loop.index("dt = time.time() - t0"))
    assert t0 < fetch < dt


def test_locked_loader_clock_budgets_the_fetch(tmp_path, monkeypatch):
    """With an instant consumer the old clock (t_last taken after the fetch) saw ~0 s; the
    budget must include the time spent producing each batch, as upstream's timed step does."""
    import importlib.util

    import numpy as np

    d = tmp_path / "data"
    d.mkdir()
    np.random.default_rng(0).integers(0, 256, 2_000_000).astype(np.uint8).tofile(d / "train.bin")
    for k, v in {"RSI_AR_MODE": "hardened", "RSI_AR_DATA": str(d), "RSI_AR_BUDGET": "1e9",
                 "RSI_AR_BUDGET_KIND": "wallclock"}.items():
        monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("tl_prep_clock",
                                                  Path(__file__).parent.parent / "rsi/domains/tinylm/prepare.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    it = prep.make_dataloader(2048, 128, "train")
    for _ in range(12):
        next(it)                                                  # warm-up batches (not budgeted)
    t = time.time()
    for _ in range(40):
        next(it)
    wall = time.time() - t
    assert prep._CLOCK["tau"] >= 0.7 * wall


# ----------------------------------------------------------------------------- N7 rejected rows reference commits
def test_rejected_rows_reference_a_real_commit(tinylm_tokens, tmp_path):
    from rsi.domains.tinylm.task import tinylm_edit_pool

    loop = AutoresearchLoop(tinylm_tokens, MockResearchAgent(tinylm_edit_pool(), schedule=["exploit_grader"]),
                            cfg(max_experiments=1, workspace="git", persist=True), out_dir=tmp_path / "rej")
    loop.run()
    row = loop.results.rows()[-1]
    assert row.description.startswith("REJECTED") and row.metric == 0.0
    assert any(sha.startswith(row.commit) for sha in loop.ws.commits)
    subprocess.run(["git", "cat-file", "-e", f"{row.commit}^{{commit}}"], cwd=tmp_path / "rej" / "workspace",
                   check=True)
    assert loop.ws.head() == loop.inc["sha"]                      # reset away at once


# ----------------------------------------------------------------------------- N8 memory column precision
def test_tinylm_memory_column_is_informative(tinylm_tokens):
    assert tinylm_tokens.memory_decimals == 3
    o = tinylm_tokens.run(tinylm_tokens.seed_artifact(), seed=0, mode="hardened")
    assert 0 < o.memory_gb < 1 and round(o.memory_gb, 1) != o.memory_gb or o.memory_gb * 1000 % 100 != 0
    rl = ResultsLog(None, memory_decimals=tinylm_tokens.memory_decimals)
    rl.append("abc1234", o.metric, o.memory_gb, "keep", "baseline")
    assert re.match(r"^abc1234\t\d\.\d{6}\t0\.\d{3}\tkeep\tbaseline$", rl.text().splitlines()[1])
    assert ResultsLog(None).memory_decimals == 1                  # upstream's .1f stays the default


def test_run_memory_is_not_the_launchers_peak(tinylm_tokens):
    """Found while fixing N1/N8: ``ru_maxrss`` of the run inherits the high-water mark of the
    process that launched it (folded in at exec), so a run started from a large loop or test
    process reported the launcher's memory. ``peak_mem_mb`` now reads the run's own VmHWM."""
    import numpy as np

    if not Path("/proc/self/status").exists():
        pytest.skip("needs Linux /proc")
    ballast = np.ones((9000, 9000))          # the launcher holds ~0.65 GB
    ballast += 1.0
    o = tinylm_tokens.run(tinylm_tokens.seed_artifact(), seed=0, mode="hardened")
    del ballast
    assert 0 < o.memory_gb < 0.4, o.memory_gb


# ----------------------------------------------------------------------------- N9 README in the agent's context
def test_readme_is_an_in_scope_file(tinylm_tokens):
    art = tinylm_tokens.seed_artifact()
    assert "README.md" in art.files and "prepare.py" in art["README.md"]
    prompt = RewriteEditor(MockLLM()).build_prompt(art, "propose", {"results.tsv": "x"}, None)
    assert "# tinylm autoresearch" in prompt
    from rsi.autoresearch.guard import ScopeGuard

    g = ScopeGuard(tinylm_tokens.editable_paths, tinylm_tokens.locked_paths)
    assert g.check(art, art.with_files({"README.md": "edited"}))  # read-only for the agent (hardened)


# ----------------------------------------------------------------------------- N10 program.md verbatim lines
def test_upstream_preset_keeps_program_md_lines():
    from rsi.domains.tinylm.task import TinyLMTask

    text = ProgramSpec.preset("upstream").render(TinyLMTask(2.0), "hardened")
    flat = " ".join(text.split())
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


# ----------------------------------------------------------------------------- P24 agent-requested rewind
def test_agent_can_request_a_rewind():
    agent = ListAgent([set_x(4), set_x(3), lambda ctx: Proposal(None, error="no file blocks",
                                                                meta={"rewind": ctx.git_log.splitlines()[-1][:7]})])
    loop = AutoresearchLoop(ToyTask(), agent, cfg(max_experiments=3))
    loop.run()
    assert loop.ws.n_rewinds == 1 and loop.inc["artifact"]["train.py"] == "X = 5\n"
    assert any(n.status == "rewind" for n in loop.ledger.nodes())
    llm = MockLLM(lambda p, s, seed, i: "I am stuck.\nREWIND abcdef1\n")
    prop = LLMResearchAgent(RewriteEditor(llm)).propose(AgentContext("prog", Artifact({"train.py": "X = 1\n"}),
                                                                     ("train.py",), (), "", "abcdef1 baseline"))
    assert prop.meta.get("rewind") == "abcdef1"


# ----------------------------------------------------------------------------- P5 confirm and go (opt-in)
def test_confirm_hook():
    seen = []
    res = AutoresearchLoop(ToyTask(), ListAgent([set_x(4)]),
                           cfg(max_experiments=1, confirm=lambda s: seen.append(s) or False)).run()
    assert res.stop_reason == "not_confirmed" and seen[0]["baseline"] == 5.0 and "baseline" in seen[0]["results_tsv"]
    res = AutoresearchLoop(ToyTask(), ListAgent([set_x(4)]), cfg(max_experiments=1, confirm=lambda s: True)).run()
    assert res.stop_reason == "max_rounds"


# ----------------------------------------------------------------------------- P22 the agent judges every crash
def test_every_crash_but_timeouts_goes_to_the_agent():
    agent = ListAgent([lambda ctx: Proposal(ctx.artifact.with_files({"train.py": "X = 1\nOOM\n"}), change="huge")])
    AutoresearchLoop(ToyTask(), agent, cfg(max_experiments=1)).run()
    assert len(agent.fix_calls) == 1 and "MemoryError" in agent.fix_calls[0]        # old: never asked
    agent = ListAgent([lambda ctx: Proposal(ctx.artifact.with_files({"train.py": "X = 1\nOOM\n"}), change="huge")])
    AutoresearchLoop(ToyTask(), agent, cfg(max_experiments=1, crash_fix="trivial")).run()
    assert agent.fix_calls == []                                                    # the mechanical variant
    from rsi.autoresearch.guard import AGENT_FIX_KINDS

    assert "timeout" not in AGENT_FIX_KINDS and "violation" not in AGENT_FIX_KINDS


# ----------------------------------------------------------------------------- AUDIT #23 monitor vs parallel workers
def test_parallel_monitor_audits_never_compete_with_inflight_runs(tmp_path):
    task = ToyTask(budget_kind="wallclock", sleep=0.15, audit_splits=("test",))
    agent = ListAgent([set_x(5 - 0.1 * (i + 1)) for i in range(9)])
    loop = ParallelAutoresearchLoop(task, agent, Config(max_experiments=9, workers=3, plot=False, hidden_audit=False,
                                                        shadow_monitor=True), out_dir=tmp_path / "par")
    res = loop.run()
    assert loop.monitor is not None and len(task.audit_log) >= 2                    # the monitor did audit keeps
    assert task.audit_log and max(task.audit_log) == 0                              # never while runs were training
    assert res.meta["parallel"]["deferred_audits"] >= 1
