"""BIG-Bench Hard files: download once into a cache, verify against the committed sha256 manifest, parse as data.

Nothing here executes downloaded content: task files are parsed with :func:`json.loads` and checked for the
``{"examples": [{"input": str, "target": str}, ...]}`` shape; CoT prompt files are read as text.

Cache directory: ``cache_dir=`` argument, else ``$RSI_BBH_CACHE``, else ``~/.cache/rsi/bbh``. Layout mirrors the
upstream repository: ``<cache>/bbh/<task>.json`` and ``<cache>/cot-prompts/<task>.txt``.

The machinery is shared with other benchmarks of the same shape (BBEH, :mod:`rsi.domains.bbeh`) through a
:class:`Benchmark` spec: task names, file layout, manifest, cache location and suite defaults. Every function takes
``benchmark=`` and defaults to :data:`BBH`.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

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


@dataclass(frozen=True)
class Benchmark:
    """What differs between BBH-shaped benchmarks: files, manifest, cache, and the defaults of their suites."""

    name: str
    tasks: tuple[str, ...]
    examples_path: str                          # relpath template with {task}
    manifest: Callable[[], dict]                # the committed manifest (looked up at call time)
    cache_env: str
    cache_subdir: str
    cot_path: Optional[str] = None              # relpath template of official few-shot CoT prompts, if published
    default_practice: tuple[str, ...] = ()
    default_ood: tuple[str, ...] = ()
    default_per_family: dict = field(default_factory=dict)
    pilot_per_family: int = 10
    pilot_seed: str = ""
    pilot_band: tuple[float, float] = (0.15, 0.85)
    meta: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"Benchmark({self.name})"


class BBHDataError(RuntimeError):
    """A BBH file is unavailable (offline with an empty cache) or malformed."""


class BBHChecksumError(BBHDataError):
    """A BBH file does not match the sha256 in the manifest."""


@lru_cache(maxsize=1)
def _default_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def _bbh_manifest() -> dict:
    return _default_manifest()          # module-level lookup at call time (tests patch _default_manifest)


def load_manifest(benchmark: Optional[Benchmark] = None) -> dict:
    """The committed manifest: ``{"commit", "url_template", "files": {relpath: sha256}, ...}`` (a copy)."""
    m = (benchmark or BBH).manifest()
    return {**m, "files": dict(m["files"])}


def default_cache_dir(benchmark: Optional[Benchmark] = None) -> Path:
    b = benchmark or BBH
    env = os.environ.get(b.cache_env)
    return Path(env).expanduser() if env else Path.home() / ".cache" / "rsi" / b.cache_subdir


def resolve_cache_dir(cache_dir: Optional[str | Path] = None, benchmark: Optional[Benchmark] = None) -> Path:
    return Path(cache_dir).expanduser() if cache_dir else default_cache_dir(benchmark)


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
               fetch: Optional[Fetch] = None, download: bool = True, benchmark: Optional[Benchmark] = None) -> bytes:
    """Verified bytes of ``rel`` (e.g. ``"bbh/navigate.json"``), downloading it once into the cache if needed.

    A cached file with the wrong hash is re-downloaded (when ``download``); a download with the wrong hash is never
    written to the cache. Raises :class:`BBHDataError` when the file is neither cached nor downloadable."""
    b = benchmark or BBH
    man = manifest if manifest is not None else b.manifest()
    want = man["files"].get(rel)
    if want is None:
        raise BBHDataError(f"{rel!r} is not in the {b.name.upper()} manifest")
    root = resolve_cache_dir(cache_dir, b)
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
        raise BBHDataError(f"{b.name.upper()} file {rel} is not cached in {root} and downloads are disabled")
    url = _url(rel, man)
    try:
        data = (fetch or urllib_fetch)(url)
    except Exception as e:  # noqa: BLE001 - any transport failure means "offline" for the caller
        raise BBHDataError(
            f"{b.name.upper()} file {rel} is not cached in {root} and could not be downloaded from {url} "
            f"({type(e).__name__}: {e}). Connect to the network once, or download it there by hand (the sha256 must "
            f"match rsi/domains/{b.name}/manifest.json), or point {b.cache_env} / cache_dir at a directory that has it."
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


def _check_task(name: str, b: Benchmark) -> None:
    if name not in b.tasks:
        raise KeyError(f"unknown {b.name.upper()} task {name!r}; choose from {list(b.tasks)}")


def load_examples(task: str, *, benchmark: Optional[Benchmark] = None, **kw) -> list[dict]:
    """``[{"input": str, "target": str}, ...]`` of one task, in file order (``kw`` go to :func:`fetch_file`)."""
    b = benchmark or BBH
    _check_task(task, b)
    rel = b.examples_path.format(task=task)
    raw = fetch_file(rel, benchmark=b, **kw)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise BBHDataError(f"{rel} is not valid JSON: {e}") from e
    ex = doc.get("examples") if isinstance(doc, dict) else None
    if not isinstance(ex, list) or not all(isinstance(e, dict) and isinstance(e.get("input"), str)
                                           and isinstance(e.get("target"), str) for e in ex):
        raise BBHDataError(f"{rel} does not hold examples of {{'input': str, 'target': str}}")
    return [{"input": e["input"], "target": e["target"]} for e in ex]


def load_cot_prompt(task: str, *, benchmark: Optional[Benchmark] = None, **kw) -> str:
    """The official 3-shot CoT prompt of ``task``: description + exemplars, without the canary header."""
    b = benchmark or BBH
    if not b.cot_path:
        raise BBHDataError(f"{b.name.upper()} publishes no official CoT prompts: the cot3 reference is unavailable "
                           f"for {b.name}")
    _check_task(task, b)
    text = fetch_file(b.cot_path.format(task=task), benchmark=b, **kw).decode("utf-8")
    head, sep, rest = text.partition("\n-----\n")
    return (rest if sep else text).strip()


def ensure_all(*, include_cot: bool = True, benchmark: Optional[Benchmark] = None, **kw) -> list[str]:
    """Download (once) and verify every file of the manifest; returns the relpaths checked."""
    b = benchmark or BBH
    man = kw.get("manifest") if kw.get("manifest") is not None else b.manifest()
    rels = [r for r in man["files"] if include_cot or not r.startswith("cot-prompts/")]
    for rel in rels:
        fetch_file(rel, benchmark=b, **kw)
    return rels


#: BIG-Bench Hard (Suzgun et al. 2022): defaults justified in :mod:`.suite`
BBH = Benchmark(
    name="bbh", tasks=TASKS, examples_path="bbh/{task}.json", manifest=_bbh_manifest, cache_env="RSI_BBH_CACHE",
    cache_subdir="bbh", cot_path="cot-prompts/{task}.txt",
    default_practice=("multistep_arithmetic_two", "word_sorting", "dyck_languages", "logical_deduction_five_objects",
                      "tracking_shuffled_objects_five_objects", "date_understanding"),
    default_ood=("navigate", "object_counting", "temporal_sequences", "geometric_shapes", "penguins_in_a_table",
                 "web_of_lies"),
    default_per_family={"evolve": 6, "val": 0, "holdout": 10, "ood": 10}, pilot_per_family=10,
    pilot_seed="bbh-pilot-v1", pilot_band=(0.15, 0.85))
