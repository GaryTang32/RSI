"""GEP 1.14.0 content addressing: ``asset_id = "sha256:" + hex(sha256(canonicalize(x without asset_id)))``."""
from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any, Iterable

SCHEMA_VERSION = "1.14.0"
ASSET_ID_PREFIX = "sha256:"


def _js_number(x: float) -> str:
    """JavaScript ``String(x)`` (ECMA-262 Number::toString)."""
    if isinstance(x, int):
        if abs(x) < 2 ** 53:
            return str(x)
        try:  # a JS number is a double: large ints round as JSON.parse would
            x = float(x)
        except OverflowError:
            return "null"
    if not math.isfinite(x):
        return "null"
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    tup = Decimal(repr(abs(x))).normalize().as_tuple()
    digits = "".join(str(t) for t in tup.digits)
    k = len(digits)
    n = tup.exponent + k  # decimal point position
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


def _utf16_key(s: str) -> bytes:
    # JS sorts keys by UTF-16 code units; big-endian UTF-16 bytes compare the same way
    return s.encode("utf-16-be", "surrogatepass")


def canonicalize(obj: Any) -> str:
    """Deterministic JSON text matching the reference ``contentHash.js`` byte for byte."""
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
        keys = sorted((str(k) for k in obj), key=_utf16_key)
        lookup = {str(k): v for k, v in obj.items()}
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonicalize(lookup[k]) for k in keys) + "}"
    if hasattr(obj, "to_dict"):
        return canonicalize(obj.to_dict())
    return "null"


def asset_id(obj: Any, exclude: Iterable[str] = ("asset_id",)) -> str:
    d = obj.to_dict() if hasattr(obj, "to_dict") else obj
    if not isinstance(d, dict):
        raise TypeError("asset_id needs a dict-like asset")
    ex = set(exclude)
    clean = {k: v for k, v in d.items() if k not in ex}
    return ASSET_ID_PREFIX + hashlib.sha256(canonicalize(clean).encode("utf-8")).hexdigest()


def verify_asset_id(obj: Any) -> bool:
    d = obj.to_dict() if hasattr(obj, "to_dict") else obj
    claimed = d.get("asset_id") if isinstance(d, dict) else None
    return isinstance(claimed, str) and claimed == asset_id(d)


def hub_capsule_asset_id(capsule: dict) -> str:
    """Hub-side capsule id: only ``outcome.status`` and ``outcome.score`` are hashed (spec §2.2)."""
    c = dict(capsule)
    if isinstance(c.get("outcome"), dict):
        o = c["outcome"]
        c["outcome"] = {k: o[k] for k in ("status", "score") if k in o}
    return asset_id(c)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
