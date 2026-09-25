"""Content addressing compatible with GEP schema 1.14.0 (spec §4.1).

``asset_id(x) = "sha256:" + hex(SHA256_utf8(canonicalize(x without "asset_id")))``

:func:`canonicalize` reproduces the JavaScript reference (``@evomap/gep-sdk``
``src/contentHash.js``, Apache-2.0) byte for byte, including JavaScript's number
formatting (``1.0 -> "1"``, ``1e-7 -> "1e-7"``, ``1e21 -> "1e+21"``), so ids
computed here agree with ids computed by other GEP-compatible runtimes.

Keys whose value is ``None`` *do* change the hash (they canonicalize to
``null``); producers must omit absent fields rather than send ``None``.
"""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any, Iterable

SCHEMA_VERSION = "1.14.0"
ASSET_ID_PREFIX = "sha256:"


def _js_number(x: float) -> str:
    """``String(x)`` for a JavaScript number (ECMA-262 Number::toString)."""
    if isinstance(x, bool):  # pragma: no cover - handled by caller
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if not math.isfinite(x):
        return "null"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    d = Decimal(repr(abs(x)))
    tup = d.normalize().as_tuple()
    digits = "".join(str(t) for t in tup.digits)
    k = len(digits)
    n = tup.exponent + k            # position of the decimal point
    if k <= n <= 21:
        return sign + digits + "0" * (n - k)
    if 0 < n <= 21:
        return sign + digits[:n] + "." + digits[n:]
    if -6 < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    es = ("+" if e >= 0 else "-") + str(abs(e))
    if k == 1:
        return sign + digits + "e" + es
    return sign + digits[0] + "." + digits[1:] + "e" + es


def canonicalize(obj: Any) -> str:
    """Deterministic JSON text: sorted keys, no whitespace, JS number/string rules."""
    if obj is None:
        return "null"
    if isinstance(obj, bool):
        return "true" if obj else "false"
    if isinstance(obj, (int, float)):
        return _js_number(obj)
    if isinstance(obj, str):
        return json.dumps(obj, ensure_ascii=False)
    if isinstance(obj, (list, tuple)):
        return "[" + ",".join(canonicalize(v) for v in obj) + "]"
    if isinstance(obj, dict):
        keys = sorted(str(k) for k in obj)
        lookup = {str(k): v for k, v in obj.items()}
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonicalize(lookup[k]) for k in keys) + "}"
    if hasattr(obj, "to_dict"):
        return canonicalize(obj.to_dict())
    return "null"


def asset_id(obj: Any, exclude: Iterable[str] = ("asset_id",)) -> str:
    """``sha256:<hex>`` over the canonical form of ``obj`` minus ``exclude`` keys."""
    d = obj.to_dict() if hasattr(obj, "to_dict") else obj
    if not isinstance(d, dict):
        raise TypeError("asset_id needs a dict-like asset")
    ex = set(exclude)
    clean = {k: v for k, v in d.items() if k not in ex}
    return ASSET_ID_PREFIX + hashlib.sha256(canonicalize(clean).encode("utf-8")).hexdigest()


def verify_asset_id(obj: Any) -> bool:
    """True iff ``obj["asset_id"]`` equals the recomputed id."""
    d = obj.to_dict() if hasattr(obj, "to_dict") else obj
    claimed = d.get("asset_id") if isinstance(d, dict) else None
    return isinstance(claimed, str) and claimed == asset_id(d)


def hub_capsule_asset_id(capsule: dict) -> str:
    """Hub-side capsule id: only ``outcome.status`` and ``outcome.score`` take part
    in the hash (``outcome.notes`` / ``outcome.details`` are stripped) [spec §2.2]."""
    c = dict(capsule)
    if isinstance(c.get("outcome"), dict):
        o = c["outcome"]
        c["outcome"] = {k: o[k] for k in ("status", "score") if k in o}
    return asset_id(c)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
