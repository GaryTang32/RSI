"""Helpers shared by the autoresearch tests."""
import shutil
import threading

import pytest

from rsi.autoresearch import AgentContext, Config, ResultsLog, ScopeGuard

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def quiet(**kw) -> Config:
    """Config without plots or the post-hoc hidden audit."""
    return Config(**{"plot": False, "hidden_audit": False, **kw})


def rows(res) -> list:
    return ResultsLog.read(res.meta["results_tsv"]).rows()


def candidates(res) -> list:
    return [n for n in res.ledger.nodes() if n.kind == "candidate"]


def kept(res) -> list:
    return [n for n in res.ledger.nodes() if n.status == "keep"]


def edited(task, name: str):
    """The task's seed artifact with the named mock edit applied."""
    pool = {e.name: e for e in task.mock_edit_pool()}
    base = task.seed_artifact()
    return base.with_files(pool[name].apply(base.files)[0])


def task_guard(task) -> ScopeGuard:
    """The hardened loop's guard for ``task``, tamper scan included."""
    return ScopeGuard(task.editable_paths, task.locked_paths, sealed=task.sealed_files(), tamper=task.tamper_patterns)


def context(artifact, program: str = "", git_log: str = "") -> AgentContext:
    return AgentContext(program=program, artifact=artifact, editable=("train.py",), locked=(), results_tsv="",
                        git_log=git_log)


def run_threads(loops) -> None:
    threads = [threading.Thread(target=lp.run) for lp in loops]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
