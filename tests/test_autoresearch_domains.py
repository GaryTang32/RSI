"""tinylm / tabular / AgentQA integration tests (real subprocess runs with tiny budgets)."""
import json

import pytest

from rsi.autoresearch import (AutoresearchLoop, Config, DomainResearchTask, MockResearchAgent, ResultsLog, run)
from rsi.autoresearch.pools import harness_edit_pool


@pytest.fixture(scope="module")
def tinylm(tmp_path_factory):
    from rsi.domains.tinylm import TinyLMTask

    root = tmp_path_factory.mktemp("tinylm_data")
    task = TinyLMTask(budget_s=0.6, kill_after=4.0, data_root=root)
    task.prepare()
    return task


@pytest.fixture(scope="module")
def tabular(tmp_path_factory):
    from rsi.domains.tabular import TabularTask

    root = tmp_path_factory.mktemp("tabular_data")
    task = TabularTask(data_root=root)
    task.prepare()
    return task


def _edit(task, name):
    pool = {e.name: e for e in task.mock_edit_pool()}
    base = task.seed_artifact()
    return base.with_files(pool[name].apply(base.files)[0])


def test_tinylm_corpus_deterministic(tinylm, tmp_path):
    from rsi.domains.tinylm import build_corpus

    m1 = json.loads((tinylm.root / "manifest.json").read_text())
    m2 = build_corpus(tmp_path / "again")
    assert {k: v["sha256"] for k, v in m1["splits"].items()} == {k: v["sha256"] for k, v in m2["splits"].items()}
    assert not (tinylm.root / "visible" / "test_iid.bin").exists()         # hidden shards not mounted
    assert m1["splits"]["train"]["bytes"] > 500_000


def test_tinylm_modes_and_record(tinylm):
    base = tinylm.seed_artifact()
    f = tinylm.run(base, mode="faithful")
    h = tinylm.run(base, mode="hardened")
    assert 2.0 < f.metric < 6.0 and 2.0 < h.metric < 6.0 and f.memory_gb >= 0
    assert h.record["causal"] and not h.record["budget_overrun"]
    assert 0.7 * tinylm.budget.amount <= h.record["training_seconds"] <= 1.25 * tinylm.budget.amount + 1
    aud = tinylm.audit(base)
    assert {"test_iid", "test_shift"} <= set(aud)


def test_tinylm_exploits_faithful_vs_hardened(tinylm):
    fake = _edit(tinylm, "exploit_fake_report")
    assert tinylm.run(fake, mode="faithful").metric == 1.0                  # self-reported number accepted
    assert tinylm.run(fake, mode="hardened").crashed                        # no locked record
    peek = _edit(tinylm, "exploit_future_peek")
    assert tinylm.run(peek, mode="faithful").metric < 1.0                   # bogus: sees the next byte
    o = tinylm.run(peek, mode="hardened")
    assert o.crashed and o.crash_reason.startswith("non-causal")
    val = tinylm.run(_edit(tinylm, "exploit_train_on_val"), mode="hardened")
    assert val.crashed and "PermissionError" in val.crash_reason
    hid = tinylm.run(_edit(tinylm, "exploit_hidden_data"), mode="hardened")
    assert hid.crashed and "test_iid" in hid.crash_reason
    assert tinylm.run(_edit(tinylm, "exploit_hidden_data"), mode="faithful").metric is not None


def test_tinylm_house_rules(tinylm, tmp_path):
    ag = MockResearchAgent(tinylm.mock_edit_pool(),
                           schedule=["typo_lr_up", "hang_prefetch", "oom_width", "nan_sgd", "exploit_grader"])
    res = AutoresearchLoop(tinylm, ag, Config(max_experiments=5, plot=False, hidden_audit=False),
                           out_dir=tmp_path / "h").run()
    rows = ResultsLog.read(res.meta["results_tsv"]).rows()
    assert [r.status for r in rows[2:5]] == ["crash", "crash", "crash"]
    assert all(r.metric == 0.0 and r.memory_gb == 0.0 for r in rows[2:5])
    assert rows[1].status in ("keep", "discard")                             # typo fixed and re-run
    nodes = [n for n in res.ledger.nodes() if n.kind == "candidate"]
    assert nodes[0].meta["fix_attempts"] == 1
    assert [n.meta["crash_kind"] for n in nodes[1:4]] == ["timeout", "oom", "nan"]
    assert nodes[1].metrics["wall_s"] < tinylm.budget.hard_timeout() + 2    # killed at kill_after
    assert nodes[4].status == "rejected"
    kept = [n for n in res.ledger.nodes() if n.status == "keep"]
    assert res.best.id == kept[-1].artifact_id


def test_tabular_protocol_and_per_row_check(tabular):
    base = tabular.seed_artifact()
    h = tabular.run(base, mode="hardened")
    assert 0.6 < h.metric < 0.9 and h.record["per_row_ok"] and len(h.record["folds"]) == 5
    bad = _edit(tabular, "fe_origin_count")
    assert tabular.run(bad, mode="faithful").metric is not None
    o = tabular.run(bad, mode="hardened")
    assert o.crashed and o.crash_reason.startswith("frame-dependent")
    aud = tabular.audit(_edit(tabular, "col_x7"))
    base_aud = tabular.audit(base)
    assert aud["test_iid"] > base_aud["test_iid"] and aud["test_shift"] < base_aud["test_shift"]   # shift trap


def test_generic_domain_agentqa(tmp_path):
    from rsi.domains.agentqa import AgentQADomain, SimModel

    dom = AgentQADomain()
    res = run(dom, dom.seed_artifact(), llm_task=SimModel(dom.tasks),
              agent=MockResearchAgent(harness_edit_pool(), seed=0),
              config=Config(max_experiments=6, plot=False), out_dir=tmp_path / "aq", task_kwargs={"k": 1})
    rows = ResultsLog.read(res.meta["results_tsv"]).rows()
    n_invalid = res.meta["counters"]["invalid"]
    assert rows[0].description == "baseline" and len(rows) == 7 - n_invalid       # invalid turns log no row
    assert res.meta["audit"] and {"holdout", "ood"} <= set(res.meta["audit"][0])
    assert res.usage["_total"]["calls"] > 0 and any(k.endswith(":task") for k in res.usage)
    assert DomainResearchTask(dom, SimModel(dom.tasks)).audit_splits == ("holdout", "ood")


def test_generic_domain_trivial_crash_fixed():
    from rsi.domains.agentqa import AgentQADomain, SimModel

    dom = AgentQADomain()
    task = DomainResearchTask(dom, SimModel(dom.tasks), k=1)
    ag = MockResearchAgent(harness_edit_pool(), schedule=["broken_return", "python_tool"])
    res = AutoresearchLoop(task, ag, Config(max_experiments=2, plot=False, hidden_audit=False, persist=False)).run()
    n1, n2 = [n for n in res.ledger.nodes() if n.kind == "candidate"]
    assert n1.meta["fix_attempts"] == 1 and n1.status != "crash"
    assert n2.status == "keep" and n2.score > res.ledger.nodes()[0].score


@pytest.mark.live
def test_live_llm_agent_landscape(tmp_path):
    """Opt-in (`pytest -m live`): one real Claude Haiku research turn on the cheap landscape task."""
    from rsi.autoresearch import LandscapeTask
    from rsi.core import CachedLLM, ClaudeCLI

    llm = CachedLLM(ClaudeCLI("haiku"), tmp_path / "cache")
    res = run(LandscapeTask(), llm_propose=llm, config=Config(max_experiments=1, plot=False, hidden_audit=False),
              out_dir=tmp_path / "live")
    assert len(ResultsLog.read(res.meta["results_tsv"]).rows()) >= 1
    assert res.usage["_total"]["calls"] >= 1
