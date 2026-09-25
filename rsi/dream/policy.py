"""Exploration policies as hot-swappable code artifacts.

A policy is an :class:`rsi.core.Artifact` holding one file, ``method.py``
(``NAME = "OptimalPolicy"``, ``class OptimalPolicy(LLMDesignedMethod)`` with
``solve`` / ``plan_grid`` / ``_schedule(beta)``), exactly the paper's ``{method_file}``
[paper:App.B.2]. Versions are content-addressed, so ledgers and the developer
history (``r####_*/``) refer to exact code.

Built-in templates (``rsi/dream/policy_templates/``):

* ``parallel_refine`` - pi_1 / the Recursive Fixed Exploration baseline;
* ``adaptive`` - the adaptive portfolio policy (Listing-2 decision loop) whose
  ``PARAMS`` block is what the mock developer mutates;
* ``rules`` - hand-written strategies (overview demo, E2 zoo);
* ``oracle`` - a deliberately cheating policy for the no-peeking experiment.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

from ..core.artifact import Artifact

POLICY_FILE = "method.py"
TEMPLATES = Path(__file__).parent / "policy_templates"


def template_code(name: str) -> str:
    p = TEMPLATES / f"{name}.py"
    if not p.exists():
        raise KeyError(f"unknown policy template {name!r}; have {sorted(x.stem for x in TEMPLATES.glob('*.py'))}")
    return p.read_text()


def policy_artifact(code: str, **meta) -> Artifact:
    return Artifact({POLICY_FILE: code}, meta=meta)


def code_of(policy: Artifact | str) -> str:
    return policy if isinstance(policy, str) else policy[POLICY_FILE]


def _params_node(tree: ast.Module) -> Optional[ast.Assign]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PARAMS" for t in node.targets):
            return node
    return None


def get_params(code: str) -> dict:
    """The literal ``PARAMS = {...}`` dict of a policy module ({} when absent / not literal)."""
    try:
        node = _params_node(ast.parse(code))
        return dict(ast.literal_eval(node.value)) if node is not None else {}
    except (SyntaxError, ValueError):
        return {}


def set_params(code: str, updates: dict) -> str:
    """Rewrite the ``PARAMS`` block with ``updates`` merged in (a real code edit)."""
    tree = ast.parse(code)
    node = _params_node(tree)
    if node is None:
        raise ValueError("policy has no PARAMS block")
    params = dict(ast.literal_eval(node.value))
    params.update(updates)
    lines = code.splitlines(keepends=True)
    items = "".join(f"    {k!r}: {_fmt(v)},\n" for k, v in params.items())
    new = "PARAMS = {\n" + items + "}\n"
    start, end = node.lineno - 1, node.end_lineno
    return "".join(lines[:start]) + new + "".join(lines[end:])


def _fmt(v) -> str:
    if isinstance(v, float):
        return repr(round(v, 6))
    return repr(v)


def make_policy(name: str, **params) -> Artifact:
    """Template ``name`` with its ``PARAMS`` overridden by ``params``."""
    code = template_code(name)
    if params:
        code = set_params(code, params)
    return policy_artifact(code, template=name)


def parallel_refine() -> Artifact:
    return make_policy("parallel_refine")


def adaptive(**params) -> Artifact:
    return make_policy("adaptive", **params)


def rules(**params) -> Artifact:
    return make_policy("rules", **params)
