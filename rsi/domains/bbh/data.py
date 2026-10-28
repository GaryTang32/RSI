"""BIG-Bench Hard files: download once into a cache, verify against the committed sha256 manifest, parse as data.

Nothing here executes downloaded content: task files are parsed with :func:`json.loads` and checked for the
``{"examples": [{"input": str, "target": str}, ...]}`` shape; CoT prompt files are read as text.

Cache directory: ``cache_dir=`` argument, else ``$RSI_BBH_CACHE``, else ``~/.cache/rsi/bbh``. Layout mirrors the
upstream repository: ``<cache>/bbh/<task>.json`` and ``<cache>/cot-prompts/<task>.txt``.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Callable, Mapping, Optional

MANIFEST_PATH = Path(__file__).parent / "manifest.json"

#: the 27 BBH tasks (Suzgun et al. 2022)
TASKS: tuple[str, ...] = (
    "boolean_expressions", "causal_judgement", "date_understanding", "disambiguation_qa", "dyck_languages",
    "formal_fallacies", "geometric_shapes", "hyperbaton", "logical_deduction_three_objects",
    "logical_deduction_five_objects", "logical_deduction_seven_objects", "movie_recommendation",
    "multistep_arithmetic_two", "navigate", "object_counting", "penguins_in_a_table",
    "reasoning_about_colored_objects", "ruin_names", "salient_translation_error_detection", "snarks",
    "sports_understanding", "temporal_sequences", "tracking_shuffled_objects_three_objects",
    "tracking_shuffled_objects_five_objects", "tracking_shuffled_objects_seven_objects", "web_of_lies",
    "word_sorting")

Fetch = Callable[[str], bytes]


class BBHDataError(RuntimeError):
    """A BBH file is unavailable (offline with an empty cache) or malformed."""


class BBHChecksumError(BBHDataError):
    """A BBH file does not match the sha256 in the manifest."""


@lru_cache(maxsize=1)
def _default_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def load_manifest() -> dict:
    """The committed manifest: ``{"commit", "url_template", "files": {relpath: sha256}, ...}`` (a copy)."""
    m = _default_manifest()
    return {**m, "files": dict(m["files"])}


def default_cache_dir() -> Path:
    env = os.environ.get("RSI_BBH_CACHE")
    return Path(env).expanduser() if env else Path.home() / ".cache" / "rsi" / "bbh"


def resolve_cache_dir(cache_dir: Optional[str | Path] = None) -> Path:
    return Path(cache_dir).expanduser() if cache_dir else default_cache_dir()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _url(rel: str, manifest: Mapping) -> str:
    return manifest["url_template"].format(commit=manifest["commit"], path=rel)


def urllib_fetch(url: str, timeout_s: float = 60.0) -> bytes:
    """Plain HTTPS GET (honours HTTPS_PROXY); raises OSError on network failure."""
    req = urllib.request.Request(url, headers={"User-Agent": "rsi-bbh-loader"})
    with urllib.request.urlopen(req, timeout=timeout_s) as r:  # noqa: S310 - fixed https URL from the manifest
        return r.read()


def fetch_file(rel: str, *, cache_dir: Optional[str | Path] = None, manifest: Optional[Mapping] = None,
               fetch: Optional[Fetch] = None, download: bool = True) -> bytes:
    """Verified bytes of ``rel`` (e.g. ``"bbh/navigate.json"``), downloading it once into the cache if needed.

    A cached file with the wrong hash is re-downloaded (when ``download``); a download with the wrong hash is never
    written to the cache. Raises :class:`BBHDataError` when the file is neither cached nor downloadable."""
    man = manifest if manifest is not None else _default_manifest()
    want = man["files"].get(rel)
    if want is None:
        raise BBHDataError(f"{rel!r} is not in the BBH manifest")
    root = resolve_cache_dir(cache_dir)
    path = root / rel
    stale = None
    if path.exists():
        data = path.read_bytes()
        got = sha256_bytes(data)
        if got == want:
            return data
        stale = got
        if not download:
            raise BBHChecksumError(f"cached {path} has sha256 {got[:16]}..., manifest says {want[:16]}...; delete it "
                                   "or allow a download to replace it")
    if not download:
        raise BBHDataError(f"BBH file {rel} is not cached in {root} and downloads are disabled")
    url = _url(rel, man)
    try:
        data = (fetch or urllib_fetch)(url)
    except Exception as e:  # noqa: BLE001 - any transport failure means "offline" for the caller
        raise BBHDataError(
            f"BBH file {rel} is not cached in {root} and could not be downloaded from {url} ({type(e).__name__}: "
            f"{e}). Connect to the network once, or download it there by hand (the sha256 must match "
            f"rsi/domains/bbh/manifest.json), or point RSI_BBH_CACHE / cache_dir at a directory that has it."
        ) from e
    got = sha256_bytes(data)
    if got != want:
        raise BBHChecksumError(f"downloaded {url} has sha256 {got[:16]}..., manifest says {want[:16]}... "
                               f"(cached copy {'also wrong' if stale else 'absent'}); refusing to use it")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return data


def _check_task(name: str) -> None:
    if name not in TASKS:
        raise KeyError(f"unknown BBH task {name!r}; choose from {list(TASKS)}")


def load_examples(task: str, **kw) -> list[dict]:
    """``[{"input": str, "target": str}, ...]`` of one task, in file order (``kw`` go to :func:`fetch_file`)."""
    _check_task(task)
    raw = fetch_file(f"bbh/{task}.json", **kw)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BBHDataError(f"bbh/{task}.json is not valid JSON: {e}") from e
    ex = doc.get("examples") if isinstance(doc, dict) else None
    if not isinstance(ex, list) or not all(isinstance(e, dict) and isinstance(e.get("input"), str)
                                           and isinstance(e.get("target"), str) for e in ex):
        raise BBHDataError(f"bbh/{task}.json does not hold examples of {{'input': str, 'target': str}}")
    return [{"input": e["input"], "target": e["target"]} for e in ex]


def load_cot_prompt(task: str, **kw) -> str:
    """The official 3-shot CoT prompt of ``task``: description + exemplars, without the canary header."""
    _check_task(task)
    text = fetch_file(f"cot-prompts/{task}.txt", **kw).decode("utf-8")
    head, sep, rest = text.partition("\n-----\n")
    return (rest if sep else text).strip()


def ensure_all(*, include_cot: bool = True, **kw) -> list[str]:
    """Download (once) and verify every file of the manifest; returns the relpaths checked."""
    man = kw.get("manifest") if kw.get("manifest") is not None else _default_manifest()
    rels = [r for r in man["files"] if include_cot or not r.startswith("cot-prompts/")]
    for rel in rels:
        fetch_file(rel, **kw)
    return rels
