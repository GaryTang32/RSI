"""Offline BBH fixtures: a synthetic cache + manifest; ``bbh_offline`` makes every default loader call use them."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bbh_fixture import build
from rsi.domains.bbh import data as bbh_data


@pytest.fixture(scope="session")
def bbh_env(tmp_path_factory):
    root = tmp_path_factory.mktemp("bbh_cache")
    return SimpleNamespace(cache_dir=root, manifest=build(root))


@pytest.fixture
def bbh_offline(bbh_env, monkeypatch):
    """Default cache dir and manifest point at the synthetic fixture; any download attempt fails the test."""
    monkeypatch.setenv("RSI_BBH_CACHE", str(bbh_env.cache_dir))
    monkeypatch.setattr(bbh_data, "_default_manifest", lambda: bbh_env.manifest)

    def no_network(url, timeout_s=60.0):
        raise AssertionError(f"network access attempted: {url}")
    monkeypatch.setattr(bbh_data, "urllib_fetch", no_network)
    return bbh_env


@pytest.fixture
def kw(bbh_env):
    """Loader keywords for the fixture (explicit, without monkeypatching)."""
    return {"cache_dir": bbh_env.cache_dir, "manifest": bbh_env.manifest, "download": False}
