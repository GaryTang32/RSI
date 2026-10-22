"""Exploration policies as content-addressed code artifacts holding one ``method.py`` [paper:App.B.2]."""
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
    return next((node for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "PARAMS" for t in node.targets)), None)


def get_params(code: str) -> dict:
    """The literal ``PARAMS = {...}`` dict of a policy module ({} when absent or not literal)."""
    try:
        node = _params_node(ast.parse(code))
        return dict(ast.literal_eval(node.value)) if node is not None else {}
    except (SyntaxError, ValueError):
        return {}


def set_params(code: str, updates: dict) -> str:
    """Rewrite the ``PARAMS`` block with ``updates`` merged in."""
    node = _params_node(ast.parse(code))
    if node is None:
        raise ValueError("policy has no PARAMS block")
    params = {**dict(ast.literal_eval(node.value)), **updates}
    lines = code.splitlines(keepends=True)
    items = "".join(f"    {k!r}: {round(v, 6) if isinstance(v, float) else v!r},\n" for k, v in params.items())
    return "".join(lines[:node.lineno - 1]) + "PARAMS = {\n" + items + "}\n" + "".join(lines[node.end_lineno:])


def make_policy(name: str, **params) -> Artifact:
    """Template ``name`` with its ``PARAMS`` overridden by ``params``."""
    code = template_code(name)
    return policy_artifact(set_params(code, params) if params else code, template=name)


def parallel_refine() -> Artifact:
    return make_policy("parallel_refine")


def adaptive(**params) -> Artifact:
    return make_policy("adaptive", **params)


def rules(**params) -> Artifact:
    return make_policy("rules", **params)
