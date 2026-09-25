"""AgentWorld environment families: small multi-step agent tasks with large tool outputs.

Four families, all CPU-only and deterministic, each with a verifier returning a
score in [0, 1] (fraction of subtasks correctly completed). They mirror the
blueprint's MiniAgentWorld (spec B9.2) and differ where it matters for
harness-efficiency research - *where the decisive evidence sits in long outputs*:

=============  =========================================================  =================================
family         task                                                       decisive evidence
=============  =========================================================  =================================
``repofix``    fix seeded constant bugs so failing pytest tests pass      pytest short summary (tail)
               (issue -> fix style, regression tests)
``buildfix``   fix misspelled identifiers so ``make`` succeeds            FIRST compiler error (head/middle),
                                                                          followed by cascades
``logtriage``  write report.txt with counts / timestamps from a log,      the whole log (counts); checker
               validated by ``make check`` (verifier-first style)         lists failed items
``datalookup`` answer questions about JSONL records, checked by a         "line N is wrong" at the TOP of a
               pytest checker (held-out family: other formats)            long captured-output dump
=============  =========================================================  =================================

The workspace is virtual (``files``). ``tool_bash`` understands ``cat``, ``ls``,
``grep [-c] [-n] [-m N]``, ``head/tail -n N``, ``sed -n 'a,bp'``, ``wc -l``,
pipelines of those, plus family commands (``pytest`` [-q --tb=line] [target],
``make`` [target|check]). Paths under ``/.solpi/`` and ``/tmp/`` read the
runtime's private store (EPR archives, full bash outputs). Like Pi's bash tool,
outputs over 50 KB / 2,000 lines are cut to the tail with a note and the full
output saved under ``/tmp/pi-bash-<n>.log``.
"""
from __future__ import annotations

from .base import BASE_STATEMENT, PI_MAX_BYTES, PI_MAX_LINES, Env, Subtask, project_guide, validity_filter
from .code_envs import BuildFixEnv, ConfigFixEnv, RepoFixEnv
from .data_envs import DataLookupEnv, LogTriageEnv

FAMILIES = {"repofix": RepoFixEnv, "buildfix": BuildFixEnv, "logtriage": LogTriageEnv, "datalookup": DataLookupEnv,
            "configfix": ConfigFixEnv}

__all__ = ["Env", "Subtask", "RepoFixEnv", "BuildFixEnv", "ConfigFixEnv", "LogTriageEnv", "DataLookupEnv", "FAMILIES",
           "validity_filter", "project_guide", "BASE_STATEMENT", "PI_MAX_BYTES", "PI_MAX_LINES"]
