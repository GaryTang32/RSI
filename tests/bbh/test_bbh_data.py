"""BBH loader: download once into a cache, sha256 manifest checks, clear offline errors, parsing as data only."""
from __future__ import annotations

import json
import re

import pytest

from rsi.domains.bbh import (TASKS, BBHChecksumError, BBHDataError, default_cache_dir, ensure_all, fetch_file,
                             load_cot_prompt, load_examples, load_manifest)


def test_committed_manifest_pins_every_task_and_cot_prompt():
    m = load_manifest()
    assert re.fullmatch(r"[0-9a-f]{40}", m["commit"]) and "{commit}" in m["url_template"]
    assert set(m["files"]) == {f"bbh/{t}.json" for t in TASKS} | {f"cot-prompts/{t}.txt" for t in TASKS}
    assert len(TASKS) == 27 and all(re.fullmatch(r"[0-9a-f]{64}", h) for h in m["files"].values())
    assert "MIT" in m["license"]


def test_downloads_once_into_the_cache_then_reads_it(bbh_env, tmp_path):
    src = bbh_env.cache_dir
    calls = []

    def fetch(url):
        calls.append(url)
        return (src / url.split("/ffffffffffffffffffffffffffffffffffffffff/", 1)[1]).read_bytes()

    kw = {"cache_dir": tmp_path / "c", "manifest": bbh_env.manifest, "fetch": fetch}
    ex = load_examples("navigate", **kw)
    assert len(ex) == 32 and set(ex[0]) == {"input", "target"}
    assert (tmp_path / "c" / "bbh" / "navigate.json").exists()
    assert calls == ["https://bbh.invalid/ffffffffffffffffffffffffffffffffffffffff/bbh/navigate.json"]
    assert load_examples("navigate", **kw) == ex and len(calls) == 1          # cached: no second fetch
    assert len(ensure_all(**kw)) == 54 and len(calls) == 54


def test_wrong_hash_download_is_rejected_and_never_cached(bbh_env, tmp_path):
    kw = {"cache_dir": tmp_path, "manifest": bbh_env.manifest, "fetch": lambda url: b'{"examples": []}'}
    with pytest.raises(BBHChecksumError, match="refusing"):
        load_examples("navigate", **kw)
    assert not (tmp_path / "bbh" / "navigate.json").exists()


def test_tampered_cache_is_replaced_or_refused(bbh_env, tmp_path):
    good = (bbh_env.cache_dir / "bbh" / "snarks.json").read_bytes()
    p = tmp_path / "bbh" / "snarks.json"
    p.parent.mkdir(parents=True)
    p.write_bytes(good.replace(b"(A)", b"(B)", 1))
    with pytest.raises(BBHChecksumError, match="delete it"):
        fetch_file("bbh/snarks.json", cache_dir=tmp_path, manifest=bbh_env.manifest, download=False)
    data = fetch_file("bbh/snarks.json", cache_dir=tmp_path, manifest=bbh_env.manifest, fetch=lambda url: good)
    assert data == good and p.read_bytes() == good


def test_offline_with_empty_cache_raises_a_clear_error(bbh_env, tmp_path):
    def offline(url):
        raise OSError("Network is unreachable")
    with pytest.raises(BBHDataError) as e:
        load_examples("web_of_lies", cache_dir=tmp_path, manifest=bbh_env.manifest, fetch=offline)
    msg = str(e.value)
    assert "not cached" in msg and str(tmp_path) in msg and "RSI_BBH_CACHE" in msg and "unreachable" in msg
    with pytest.raises(BBHDataError, match="downloads are disabled"):
        load_examples("web_of_lies", cache_dir=tmp_path, manifest=bbh_env.manifest, download=False)
    with pytest.raises(BBHDataError, match="not in the BBH manifest"):
        fetch_file("bbh/nope.json", cache_dir=tmp_path, manifest=bbh_env.manifest)
    with pytest.raises(KeyError):
        load_examples("not_a_task", cache_dir=tmp_path, manifest=bbh_env.manifest)


def test_malformed_file_with_matching_hash_is_a_data_error(tmp_path):
    import hashlib
    bad = json.dumps({"examples": [{"input": 1, "target": "x"}]}).encode()
    (tmp_path / "bbh").mkdir()
    (tmp_path / "bbh" / "navigate.json").write_bytes(bad)
    man = {"commit": "0" * 40, "url_template": "x/{commit}/{path}",
           "files": {"bbh/navigate.json": hashlib.sha256(bad).hexdigest()}}
    with pytest.raises(BBHDataError, match="does not hold examples"):
        load_examples("navigate", cache_dir=tmp_path, manifest=man, download=False)


def test_cot_prompt_drops_the_canary_header(kw):
    text = load_cot_prompt("navigate", **kw)
    assert text.startswith("Synthetic description of navigate.") and "CANARY" not in text and "-----" not in text


def test_cache_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("RSI_BBH_CACHE", str(tmp_path / "x"))
    assert default_cache_dir() == tmp_path / "x"
    monkeypatch.delenv("RSI_BBH_CACHE")
    assert default_cache_dir().parts[-3:] == (".cache", "rsi", "bbh")


def test_default_loader_uses_patched_manifest_and_env(bbh_offline):
    assert len(load_examples("word_sorting")) == 32
