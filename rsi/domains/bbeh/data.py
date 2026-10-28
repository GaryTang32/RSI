"""BIG-Bench Extra Hard files: the BBH loader (download once, sha256 manifest, data only) with the BBEH spec.

Source: google-deepmind/bbeh pinned at commit 80d12ca9 (``manifest.json``); data CC-BY 4.0, code Apache 2.0
(Google LLC). Cache: ``cache_dir=``, else ``$RSI_BBEH_CACHE``, else ``~/.cache/rsi/bbeh``; layout mirrors upstream
(``<cache>/bbeh/benchmark_tasks/bbeh_<task>/task.json``). BBEH publishes no CoT prompts, so there is no cot3 reference.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..bbh import data as _bbh
from ..bbh.data import BBHChecksumError, BBHDataError, Benchmark

MANIFEST_PATH = Path(__file__).parent / "manifest.json"

TASKS: tuple[str, ...] = (
    "boardgame_qa", "boolean_expressions", "buggy_tables", "causal_understanding", "disambiguation_qa",
    "dyck_languages", "geometric_shapes", "hyperbaton", "linguini", "movie_recommendation", "multistep_arithmetic",
    "nycc", "object_counting", "object_properties", "sarc_triples", "shuffled_objects", "spatial_reasoning",
    "sportqa", "temporal_sequence", "time_arithmetic", "web_of_lies", "word_sorting", "zebra_puzzles")


@lru_cache(maxsize=1)
def _default_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def _manifest() -> dict:
    return _default_manifest()          # looked up at call time (tests patch _default_manifest)


#: Defaults (experiments/bbeh/families.json can override): practice = multi-step computation and bookkeeping
#: (arithmetic, boolean expressions, time arithmetic, sorting, truth chains, causal judgement); ood = different skills
#: (counting, temporal and object tracking, bracket depth, SVG shapes, adjective order). A pilot re-picks them with
#: accuracy band [0.15, 0.75]. Sizes are cost-driven (median input ~2.5k chars): evolve 4, holdout 6, ood 6.
BBEH = Benchmark(
    name="bbeh", tasks=TASKS, examples_path="bbeh/benchmark_tasks/bbeh_{task}/task.json", manifest=_manifest,
    cache_env="RSI_BBEH_CACHE", cache_subdir="bbeh", cot_path=None,
    default_practice=("multistep_arithmetic", "boolean_expressions", "time_arithmetic", "word_sorting",
                      "web_of_lies", "causal_understanding"),
    default_ood=("object_counting", "temporal_sequence", "shuffled_objects", "dyck_languages", "geometric_shapes",
                 "hyperbaton"),
    default_per_family={"evolve": 4, "val": 0, "holdout": 6, "ood": 6}, pilot_per_family=5,
    pilot_seed="bbeh-pilot-v1", pilot_band=(0.15, 0.75))


def load_manifest() -> dict:
    return _bbh.load_manifest(BBEH)


def default_cache_dir() -> Path:
    return _bbh.default_cache_dir(BBEH)


def fetch_file(rel: str, **kw) -> bytes:
    return _bbh.fetch_file(rel, benchmark=BBEH, **kw)


def load_examples(task: str, **kw) -> list[dict]:
    return _bbh.load_examples(task, benchmark=BBEH, **kw)


def ensure_all(**kw) -> list[str]:
    return _bbh.ensure_all(benchmark=BBEH, include_cot=False, **kw)


__all__ = ["TASKS", "BBEH", "BBHChecksumError", "BBHDataError", "load_manifest", "default_cache_dir", "fetch_file",
           "load_examples", "ensure_all"]
