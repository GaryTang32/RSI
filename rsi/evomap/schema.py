"""JSON Schema validation against the GEP 1.14.0 schemas (spec §5).

``jsonschema`` is not a dependency, so :class:`JsonSchemaValidator` implements
the Draft-07 subset the four GEP schemas use: ``type`` (incl. unions), ``const``,
``enum``, ``required``, ``properties``, ``additionalProperties: false``,
``items``, ``min/maxItems``, ``min/maxLength``, ``pattern``, ``minimum`` /
``maximum`` and ``allOf`` with ``if``/``then``/``else``.

Two modes:

* ``strict=True`` - the schema as published (``additionalProperties: false``).
  Engine-written objects fail it in the same places the spec documents
  ("schema/engine drift": ``avoid``, object-valued ``epigenetic_marks``,
  string ``diff``/``content`` ...).
* ``strict=False`` - extra properties allowed and the known drift fields
  tolerated; used by hubs, which accept the engine's objects.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).parent / "schemas"
KIND_FILES = {"Gene": "gene.schema.json", "Capsule": "capsule.schema.json",
              "EvolutionEvent": "evolution-event.schema.json", "Mutation": "mutation.schema.json"}
#: fields the engine writes with a different type than the strict schema (spec §5 "schema/engine drift")
DRIFT_FIELDS = {"Gene": {"epigenetic_marks"}, "Capsule": {"content", "diff"}, "EvolutionEvent": set(), "Mutation": set()}

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


@lru_cache(maxsize=None)
def load_schema(kind: str) -> dict:
    return json.loads((SCHEMA_DIR / KIND_FILES[kind]).read_text())


def _check(value: Any, schema: dict, path: str, errors: list[str], strict: bool) -> None:
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
        return
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")
        return
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_TYPES[x](value) for x in types):
            errors.append(f"{path}: expected {t}, got {type(value).__name__}")
            return
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: < {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: > {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, v in enumerate(value):
                _check(v, schema["items"], f"{path}[{i}]", errors, strict)
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required {req!r}")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                _check(v, props[k], f"{path}.{k}", errors, strict)
            elif strict and schema.get("additionalProperties") is False:
                errors.append(f"{path}: additional property {k!r} not allowed")
    for sub in schema.get("allOf", []):
        if "if" in sub:
            probe: list[str] = []
            _check(value, sub["if"], path, probe, strict)
            branch = sub.get("then") if not probe else sub.get("else")
            if branch:
                _check(value, branch, path, errors, strict)
        else:
            _check(value, sub, path, errors, strict)


class JsonSchemaValidator:
    """Validate an asset dict against its GEP schema (kind from ``obj["type"]``)."""

    def __init__(self, strict: bool = True) -> None:
        self.strict = strict

    def validate(self, obj: Any, kind: str | None = None) -> list[str]:
        d = obj.to_dict() if hasattr(obj, "to_dict") else obj
        if not isinstance(d, dict):
            return ["asset is not an object"]
        kind = kind or d.get("type")
        if kind not in KIND_FILES:
            return [f"unknown asset type {kind!r}"]
        schema = load_schema(kind)
        if not self.strict:
            d = {k: v for k, v in d.items() if k not in DRIFT_FIELDS.get(kind, set())}
        errors: list[str] = []
        _check(d, schema, "$", errors, self.strict)
        return errors

    def is_valid(self, obj: Any, kind: str | None = None) -> bool:
        return not self.validate(obj, kind)
