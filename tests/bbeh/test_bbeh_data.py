"""BBEH loader: the BBH machinery with BBEH's pinned manifest, cache location and file layout."""
from __future__ import annotations

import re

import pytest

from rsi.domains.bbeh import (BBEH, TASKS, BBHDataError, default_cache_dir, ensure_all, load_examples,
                              load_manifest)
from rsi.domains.bbh import data as bbh_data
from rsi.domains.bbh.domain import cot3_artifact


def test_committed_manifest_pins_every_task():
    m = load_manifest()
    assert m["commit"] == "80d12ca916b7158f22293fcf3144f4d3d854d4be" and "google-deepmind/bbeh" in m["url_template"]
    assert set(m["files"]) == {f"bbeh/benchmark_tasks/bbeh_{t}/task.json" for t in TASKS} and len(TASKS) == 23
    assert all(re.fullmatch(r"[0-9a-f]{64}", h) for h in m["files"].values())
    assert "CC-BY 4.0" in m["license"] and "Apache 2.0" in m["license"]


def test_downloads_once_into_the_upstream_layout(bbeh_env, tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return (bbeh_env.cache_dir / url.split("/" + "e" * 40 + "/", 1)[1]).read_bytes()

    kw = {"cache_dir": tmp_path, "manifest": bbeh_env.manifest, "fetch": fetch}
    ex = load_examples("zebra_puzzles", **kw)
    assert len(ex) == 20 and (tmp_path / "bbeh/benchmark_tasks/bbeh_zebra_puzzles/task.json").exists()
    assert load_examples("zebra_puzzles", **kw) == ex and len(calls) == 1
    assert len(ensure_all(**kw)) == 23 and len(calls) == 23


def test_offline_with_empty_cache_names_the_bbeh_fix(bbeh_env, tmp_path):
    def offline(url):
        raise OSError("Network is unreachable")
    with pytest.raises(BBHDataError) as e:
        load_examples("linguini", cache_dir=tmp_path, manifest=bbeh_env.manifest, fetch=offline)
    assert "BBEH file" in str(e.value) and "RSI_BBEH_CACHE" in str(e.value) and "rsi/domains/bbeh" in str(e.value)
    with pytest.raises(KeyError, match="unknown BBEH task"):
        load_examples("navigate", cache_dir=tmp_path, manifest=bbeh_env.manifest)


def test_cache_dir_env_and_default(monkeypatch, tmp_path):
    monkeypatch.setenv("RSI_BBEH_CACHE", str(tmp_path / "b"))
    assert default_cache_dir() == tmp_path / "b"
    monkeypatch.delenv("RSI_BBEH_CACHE")
    assert default_cache_dir().parts[-3:] == (".cache", "rsi", "bbeh")
    assert bbh_data.default_cache_dir().parts[-1] == "bbh"          # BBH keeps its own


def test_no_cot_prompts_for_bbeh(bkw):
    with pytest.raises(BBHDataError, match="no official CoT prompts"):
        bbh_data.load_cot_prompt("word_sorting", benchmark=BBEH, **bkw)
    with pytest.raises(BBHDataError, match="cot3 reference is unavailable"):
        cot3_artifact(["word_sorting"], benchmark=BBEH, **bkw)


def test_default_loader_uses_patched_manifest_and_env(bbeh_offline):
    assert len(load_examples("sportqa")) == 20
