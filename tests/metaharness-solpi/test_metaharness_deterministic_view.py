"""The proposer's history view must not depend on wall-clock time.

Before the fix, every candidate's ``meta.json`` carried ``created_at`` (and per-task results, traces, the
evolution summary and session accounting carried latencies), so the same run history rendered to a different
proposer prompt on every run and a Meta-Harness run could never replay from its LLM cache.
"""
from __future__ import annotations

import time

from rsi.domains.memoclassify import make_domain
from rsi.metaharness import Config, run
from rsi.metaharness.store import VOLATILE_KEYS, ExperienceStore, strip_volatile_fields


def _views(tmp_path, strip: bool) -> list[dict[str, str]]:
    out = []
    for k in (1, 2):
        dom = make_domain(seed=0)
        d = tmp_path / f"run{k}"
        run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"), llm_propose=None,
            config=Config(iterations=2, k=2, seed=0), out_dir=d, baselines=dom.baselines())
        out.append(ExperienceStore(d / "store").view("full", strip_volatile=strip))
        time.sleep(0.05)
    return out


def test_two_identical_runs_render_the_same_proposer_view(tmp_path):
    a, b = _views(tmp_path, strip=True)
    assert a.keys() == b.keys()
    diff = [p for p in a if a[p] != b[p]]
    assert diff == []


def test_raw_view_still_carries_wall_clock_fields(tmp_path):
    a, b = _views(tmp_path, strip=False)
    assert any(a[p] != b[p] for p in a)                      # the bug this guards against
    assert any('"created_at"' in t for t in a.values())


def test_strip_is_byte_identical_without_volatile_keys():
    text = '{\n "score": 0.5,\n "nested": {"n": 1}\n}'
    assert strip_volatile_fields("x.json", text) is text
    assert strip_volatile_fields("x.md", '"created_at": 1') == '"created_at": 1'
    got = strip_volatile_fields("t.jsonl", '{"a": 1, "latency_s": 0.2}\nnot json\n')
    assert got == '{"a": 1}\nnot json\n'
    assert {"created_at", "latency_s", "timing_s"} <= VOLATILE_KEYS
    # a session's usage is the proposer's bill: a cached replay meters it as $0, so it is dropped there only
    sess = '{"iteration": 1, "usage": {"calls": 1, "cost_usd": 0.07}, "n_files_read": 3}'
    assert '"usage"' not in strip_volatile_fields("sessions/iter001/meta.json", sess)
    kept = '{"usage": {"calls": 1}}'
    assert strip_volatile_fields("candidates/x/eval/search/scores.json", kept) is kept


def test_config_default_is_deterministic():
    assert Config().deterministic_view is True


def test_view_is_identical_across_processes_with_different_hash_seeds(tmp_path):
    """A cached replay runs in a new process; set/dict orders that depend on PYTHONHASHSEED must not leak."""
    import json
    import os
    import subprocess
    import sys
    script = (
        "import json, sys\n"
        "from rsi.domains.memoclassify import make_domain\n"
        "from rsi.metaharness import Config, run\n"
        "from rsi.metaharness.store import ExperienceStore\n"
        "out = sys.argv[1]\n"
        "dom = make_domain(seed=0)\n"
        "run(dom, dom.seed_artifact('fewshot_all'), llm_task=dom.make_model('A'), llm_propose=None,\n"
        "    config=Config(iterations=1, k=2, seed=0), out_dir=out, baselines=dom.baselines())\n"
        "json.dump(ExperienceStore(out + '/store').view('full', strip_volatile=True), open(out + '/view.json', 'w'))\n")
    views = []
    for seed in ("1", "987"):
        out = tmp_path / f"h{seed}"
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[2])}
        subprocess.run([sys.executable, "-c", script, str(out)], env=env, check=True, capture_output=True, timeout=300)
        views.append(json.loads((out / "view.json").read_text()))
    assert views[0] == views[1]
