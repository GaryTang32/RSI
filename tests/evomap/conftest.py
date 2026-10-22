"""Shared fixtures for the evomap tests."""
from types import SimpleNamespace

import pytest

from evomap_helpers import katas_population
from rsi.domains.geneworld import make_domain


@pytest.fixture(scope="session")
def gw():
    return make_domain()


@pytest.fixture(scope="session")
def traced_population(tmp_path_factory):
    """A traced, monitored katas population: run dir, both results, the hub and its publish calls."""
    out = tmp_path_factory.mktemp("traced")
    calls = []
    a, b, hub = katas_population(out, publish_log=calls)
    return SimpleNamespace(out=out, a=a, b=b, hub=hub, calls=calls)
