"""Shared tinylm and tabular fixtures; each dataset is built once per session."""
import pytest

from rsi.domains.tabular import TabularTask
from rsi.domains.tinylm import TinyLMTask


@pytest.fixture(scope="session")
def tinylm_data(tmp_path_factory):
    root = tmp_path_factory.mktemp("tinylm_data")
    TinyLMTask(data_root=root).prepare()
    return root


@pytest.fixture(scope="session")
def tinylm(tinylm_data):
    return TinyLMTask(budget_s=0.6, kill_after=8.0, data_root=tinylm_data)


@pytest.fixture(scope="session")
def tinylm_tokens(tinylm_data):
    return TinyLMTask(budget_s=30_000, budget_kind="tokens", data_root=tinylm_data)


@pytest.fixture(scope="session")
def tinylm_tokens_6k(tinylm_data):
    return TinyLMTask(budget_s=6000, budget_kind="tokens", kill_after=30.0, data_root=tinylm_data)


@pytest.fixture(scope="session")
def tabular(tmp_path_factory):
    task = TabularTask(data_root=tmp_path_factory.mktemp("tabular_data"))
    task.prepare()
    return task
