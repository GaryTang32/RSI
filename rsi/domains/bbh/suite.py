"""Deterministic BBH task suites: practice families (evolve/val/holdout), unseen families (ood) and a pilot pool.

Default families (overridable, e.g. from ``experiments/bbh/families.json`` after a pilot):

- practice: ``multistep_arithmetic_two``, ``word_sorting``, ``dyck_languages``, ``logical_deduction_five_objects``,
  ``tracking_shuffled_objects_five_objects``, ``date_understanding``. Each needs several exact intermediate steps
  (nested arithmetic, long alphabetical sorts, bracket stacks, 5-object constraint or swap tracking, calendar
  arithmetic), which is where a small model answering in one call without a reasoning scaffold loses most, so a
  Haiku-class model is unlikely to be at ceiling and generic harness changes (scratch work, tools, checking) can help.
- ood: ``navigate``, ``object_counting``, ``temporal_sequences``, ``geometric_shapes``, ``penguins_in_a_table``,
  ``web_of_lies``: different skills (spatial bookkeeping, counting, interval reasoning, SVG paths, table lookup,
  truth chains), none a sibling of a practice family (no logical_deduction_* or tracking_shuffled_objects_* variant),
  so they test transfer rather than a re-sized copy of a practised task.

Split discipline, per family: one fixed permutation (seed ``PILOT_SEED``, independent of the suite seed) reserves its
first ``pilot_reserve`` examples for the pilot pool; the rest is shuffled with the suite seed and cut into
evolve / val / holdout (practice families) or ood (unseen families). So pilot, evolve, val, holdout and ood never
share an example, whatever the seed, and a pilot that re-picks families never sees a later evaluation example.
"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Mapping, Optional, Sequence

from ...core.tasks import Task, TaskSuite
from .data import BBH, TASKS, Benchmark, load_examples

DEFAULT_PRACTICE: tuple[str, ...] = BBH.default_practice
DEFAULT_OOD: tuple[str, ...] = BBH.default_ood
#: candidate families of the pilot (all 27 tasks; trim in families.json to make the pilot cheaper)
PILOT_CANDIDATES: tuple[str, ...] = TASKS
#: examples per family: evolve 6 (36), holdout 10 (60), ood 10 (60); val is off by default
DEFAULT_PER_FAMILY: dict[str, int] = dict(BBH.default_per_family)
PILOT_PER_FAMILY = BBH.pilot_per_family
PILOT_SEED = BBH.pilot_seed
#: families that are re-sized copies of one task; a pilot should not put siblings on both sides
SIBLINGS: dict[str, str] = {t: t.rsplit("_", 2)[0] for t in TASKS
                            if t.startswith(("logical_deduction_", "tracking_shuffled_objects_"))}


def sibling_group(task: str) -> str:
    return SIBLINGS.get(task, task)


def load_families(path: str | Path, benchmark: Optional[Benchmark] = None) -> dict:
    """``{"practice": [...], "ood": [...], "pilot_candidates": [...]}`` from a JSON file, validated."""
    b = benchmark or BBH
    d = json.loads(Path(path).read_text())
    fam = {"practice": list(d["practice"]), "ood": list(d["ood"]),
           "pilot_candidates": list(d.get("pilot_candidates") or b.tasks)}
    check_families(fam["practice"], fam["ood"], b)
    if bad := [t for t in fam["pilot_candidates"] if t not in b.tasks]:
        raise ValueError(f"unknown pilot candidates {bad}")
    return fam


def check_families(practice: Sequence[str], ood: Sequence[str], benchmark: Optional[Benchmark] = None) -> None:
    b = benchmark or BBH
    if bad := [t for t in [*practice, *ood] if t not in b.tasks]:
        raise ValueError(f"unknown {b.name.upper()} tasks {bad}")
    if len(set(practice)) != len(practice) or len(set(ood)) != len(ood):
        raise ValueError("duplicate family in practice or ood")
    if both := sorted(set(practice) & set(ood)):
        raise ValueError(f"families in both practice and ood: {both}")


def _family_order(examples: list[dict], family: str, pilot_reserve: int, seed,
                  pilot_seed: str = PILOT_SEED, name: str = "bbh") -> tuple[list[int], list[int]]:
    """(pilot indices, the remaining indices shuffled by ``seed``); duplicate inputs keep their first index."""
    seen: set = set()
    idx = []
    for i, e in enumerate(examples):
        if e["input"] not in seen:
            seen.add(e["input"])
            idx.append(i)
    random.Random(f"{pilot_seed}:{family}").shuffle(idx)
    pilot, rest = idx[:pilot_reserve], idx[pilot_reserve:]
    random.Random(f"{name}:{seed}:{family}").shuffle(rest)
    return pilot, rest


def _task(split: str, family: str, i: int, ex: dict) -> Task:
    return Task(f"{split}-{family}-{i:03d}", ex["input"], ex["target"], family, {"bbh_index": i})


def make_suite(practice: Optional[Sequence[str]] = None, ood: Optional[Sequence[str]] = None, *,
               per_family: Optional[Mapping[str, int]] = None, seed: int = 0,
               pilot_reserve: Optional[int] = None, families_file: Optional[str | Path] = None,
               benchmark: Optional[Benchmark] = None, **load_kw) -> TaskSuite:
    """evolve / (val) / holdout from ``practice`` and ood from ``ood``, per-family counts from ``per_family``.

    ``families_file`` supplies ``practice``/``ood`` when they are not given; ``load_kw`` (``cache_dir``,
    ``manifest``, ``fetch``, ``download``) go to the loader. Task ids are ``<split>-<family>-<index in file>``.
    ``benchmark`` (default BBH) supplies the tasks, the default families/sizes and the pilot reserve."""
    b = benchmark or BBH
    if families_file is not None and (practice is None or ood is None):
        fam = load_families(families_file, b)
        practice = fam["practice"] if practice is None else practice
        ood = fam["ood"] if ood is None else ood
    practice = list(b.default_practice if practice is None else practice)
    ood = list(b.default_ood if ood is None else ood)
    check_families(practice, ood, b)
    pilot_reserve = b.pilot_per_family if pilot_reserve is None else pilot_reserve
    n = {**b.default_per_family, **(per_family or {})}
    if unknown := sorted(set(n) - set(b.default_per_family)):
        raise ValueError(f"unknown per_family keys {unknown}; valid: {sorted(b.default_per_family)}")
    order = [s for s in ("evolve", "val", "holdout") if n[s]]
    splits: dict[str, list[str]] = {s: [] for s in order}
    splits.setdefault("evolve", [])
    splits.setdefault("holdout", [])
    splits["ood"] = []
    tasks: list[Task] = []
    for fam in practice + ood:
        ex = load_examples(fam, benchmark=b, **load_kw)
        _, rest = _family_order(ex, fam, pilot_reserve, seed, b.pilot_seed, b.name)
        plan = [(s, n[s]) for s in order] if fam in practice else [("ood", n["ood"])]
        need = sum(c for _, c in plan)
        if need > len(rest):
            raise ValueError(f"{fam}: {need} examples requested but only {len(rest)} remain after the pilot reserve")
        pos = 0
        for split, count in plan:
            for i in rest[pos:pos + count]:
                t = _task(split, fam, i, ex[i])
                tasks.append(t)
                splits[split].append(t.id)
            pos += count
    splits["smoke"] = splits["evolve"][:2]
    return TaskSuite(tasks, splits, name=f"{b.name}-s{seed}")


def make_pilot_suite(families: Optional[Sequence[str]] = None, *, n_per_family: Optional[int] = None,
                     benchmark: Optional[Benchmark] = None, **load_kw) -> TaskSuite:
    """The pilot pool: the first ``n_per_family`` reserved examples of each candidate family (split ``pilot``).

    Never overlaps :func:`make_suite` with the same ``pilot_reserve >= n_per_family`` (the default)."""
    b = benchmark or BBH
    n_per_family = b.pilot_per_family if n_per_family is None else n_per_family
    families = list(b.tasks if families is None else families)
    if bad := [t for t in families if t not in b.tasks]:
        raise ValueError(f"unknown {b.name.upper()} tasks {bad}")
    tasks: list[Task] = []
    for fam in families:
        ex = load_examples(fam, benchmark=b, **load_kw)
        pilot, _ = _family_order(ex, fam, n_per_family, 0, b.pilot_seed, b.name)
        tasks += [_task("pilot", fam, i, ex[i]) for i in pilot]
    return TaskSuite(tasks, {"pilot": [t.id for t in tasks], "evolve": []}, name=f"{b.name}-pilot")


def suggest_families(acc: Mapping[str, float], *, n_practice: int = 6, n_ood: int = 6, lo: float = 0.15,
                     hi: float = 0.85) -> dict:
    """Pick practice/ood families from pilot accuracies: drop floor/ceiling families, prefer accuracy near 0.5,
    alternate between the two sides, and keep sibling families on one side."""
    ok = sorted((f for f, a in acc.items() if lo <= a <= hi), key=lambda f: (abs(acc[f] - 0.5), f))
    practice: list[str] = []
    ood: list[str] = []
    side_of: dict[str, str] = {}
    for f in ok:
        g = sibling_group(f)
        want = side_of.get(g) or ("practice" if len(practice) <= len(ood) else "ood")
        side = practice if want == "practice" else ood
        cap = n_practice if want == "practice" else n_ood
        if len(side) >= cap or g in {sibling_group(x) for x in side}:
            continue                    # at most one family per sibling group, on one side only
        side.append(f)
        side_of[g] = want
        if len(practice) >= n_practice and len(ood) >= n_ood:
            break
    return {"practice": practice, "ood": ood, "excluded_floor_or_ceiling": sorted(set(acc) - set(ok)),
            "rule": f"accuracy in [{lo}, {hi}], nearest 0.5 first, alternating sides, one per sibling group"}
