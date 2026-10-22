"""The proposer's history view must not depend on wall-clock time or the hash seed (so cached runs replay)."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rsi.domains.memoclassify import make_domain
from rsi.metaharness import Config, run
from rsi.metaharness.store import VOLATILE_KEYS, ExperienceStore, strip_volatile_fields

ROOT = Path(__file__).resolve().parents[2]

_VIEW_SCRIPT = """
import json, sys
from rsi.domains.memoclassify import make_domain
from rsi.metaharness import Config, run
from rsi.metaharness.store import ExperienceStore
out = sys.argv[1]
dom = make_domain(seed=0)
run(dom, dom.seed_artifact('fewshot_all'), llm_task=dom.make_model('A'), llm_propose=None,
    config=Config(iterations=1, k=2, seed=0), out_dir=out, baselines=dom.baselines())
json.dump(ExperienceStore(out + '/store').view('full', strip_volatile=True), open(out + '/view.json', 'w'))
"""


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory) -> list[ExperienceStore]:
    """Two identical offline runs, started a moment apart."""
    stores = []
    for k in (1, 2):
        dom = make_domain(seed=0)
        d = tmp_path_factory.mktemp(f"run{k}")
        run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), llm_propose=None,
            config=Config(iterations=2, k=2, seed=0), out_dir=d, baselines=dom.baselines())
        stores.append(ExperienceStore(d / "store"))
        time.sleep(0.05)
    return stores


def test_two_identical_runs_render_the_same_proposer_view(two_runs):
    a, b = (st.view("full", strip_volatile=True) for st in two_runs)
    assert a.keys() == b.keys()
    assert [p for p in a if a[p] != b[p]] == []


def test_raw_view_still_carries_wall_clock_fields(two_runs):
    a, b = (st.view("full") for st in two_runs)
    assert any(a[p] != b[p] for p in a)
    assert any('"created_at"' in t for t in a.values())


def test_strip_is_byte_identical_without_volatile_keys():
    text = '{\n "score": 0.5,\n "nested": {"n": 1}\n}'
    assert strip_volatile_fields("candidates/x/meta.json", text) is text
    assert strip_volatile_fields("reports/x.md", '"created_at": 1') == '"created_at": 1'
    got = strip_volatile_fields("evolution_summary.jsonl", '{"a": 1, "latency_s": 0.2}\nnot json\n')
    assert got == '{"a": 1}\nnot json\n'
    assert {"created_at", "latency_s", "timing_s"} <= VOLATILE_KEYS
    # a cached replay meters the proposer's bill as $0, so a session's usage is dropped there only
    sess = '{"iteration": 1, "usage": {"calls": 1, "cost_usd": 0.07}, "n_files_read": 3}'
    assert '"usage"' not in strip_volatile_fields("sessions/iter001/meta.json", sess)
    kept = '{"usage": {"calls": 1}}'
    assert strip_volatile_fields("candidates/x/eval/search/scores.json", kept) is kept


def test_config_default_is_deterministic():
    assert Config().deterministic_view is True


def test_view_is_identical_across_processes_with_different_hash_seeds(tmp_path):
    views = []
    for seed in ("1", "987"):
        out = tmp_path / f"h{seed}"
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(ROOT)}
        subprocess.run([sys.executable, "-c", _VIEW_SCRIPT, str(out)], env=env, check=True, capture_output=True,
                       timeout=300)
        views.append(json.loads((out / "view.json").read_text()))
    assert views[0] == views[1]


def test_strip_leaves_traces_and_sources_alone():
    # model replies and tool output are raw data, kept as written
    reply = '{"hours": 2, "seconds": 7200}\n{"created_at": "2024-01-02", "total": 5}\n'
    assert strip_volatile_fields("candidates/x/eval/search/traces/u.jsonl", reply) == reply
    src = '{"seconds": 30, "name": "café"}'
    assert strip_volatile_fields("candidates/x/src/config.json", src) == src
    assert strip_volatile_fields("candidates/x/meta.json", '{"name": "x", "created_at": 1.5}') == '{\n "name": "x"\n}'
    assert strip_volatile_fields("evolution_summary.jsonl", '{"a": 1, "latency_s": 2}\n') == '{"a": 1}\n'
