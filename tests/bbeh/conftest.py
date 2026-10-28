"""Offline BBEH fixtures: a synthetic cache + manifest; ``bbeh_offline`` makes every default loader call use them."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bbeh_fixture import build
from rsi.domains.bbeh import data as bbeh_data
from rsi.domains.bbh import data as bbh_data


@pytest.fixture(scope="session")
def bbeh_env(tmp_path_factory):
    root = tmp_path_factory.mktemp("bbeh_cache")
    return SimpleNamespace(cache_dir=root, manifest=build(root))


@pytest.fixture
def bbeh_offline(bbeh_env, monkeypatch):
    """Default BBEH cache dir and manifest point at the synthetic fixture; any download attempt fails the test."""
    monkeypatch.setenv("RSI_BBEH_CACHE", str(bbeh_env.cache_dir))
    monkeypatch.setattr(bbeh_data, "_default_manifest", lambda: bbeh_env.manifest)

    def no_network(url, timeout_s=60.0):
        raise AssertionError(f"network access attempted: {url}")
    monkeypatch.setattr(bbh_data, "urllib_fetch", no_network)
    return bbeh_env


@pytest.fixture
def bkw(bbeh_env):
    """Loader keywords for the fixture (explicit, without monkeypatching)."""
    return {"cache_dir": bbeh_env.cache_dir, "manifest": bbeh_env.manifest, "download": False}
