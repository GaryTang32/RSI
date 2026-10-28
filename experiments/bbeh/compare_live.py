"""BBEH comparison: ``experiments/bbh/compare_live.py --benchmark bbeh`` (arms: pilot, seed, rrsi, gepa, metaharness,
harness2, bestofn; no cot3). Results in results/bbeh; families in experiments/bbeh/families.json.

Run:  python experiments/bbeh/compare_live.py --arm all --offline       # dry run with mocks, no spend
      python experiments/bbeh/compare_live.py --arm seed --smoke        # 2 tasks live
      python experiments/bbeh/compare_live.py --arm pilot --cap 3 [--apply]
      python experiments/bbeh/compare_live.py --arm rrsi --cap 8
      python experiments/bbeh/merge.py
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "bbh" / "compare_live.py"


def _load():
    spec = importlib.util.spec_from_file_location("bbh_compare_live", _SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bbh_compare_live"] = mod
    spec.loader.exec_module(mod)
    return mod


_impl = _load()
ARMS = _impl.PROFILES["bbeh"].arms
SEARCHED = _impl.SEARCHED
subsample, run_arm, build_suite = _impl.subsample, _impl.run_arm, _impl.build_suite


def parse_args(argv=None):
    return _impl.parse_args(["--benchmark", "bbeh", *(sys.argv[1:] if argv is None else argv)])


def main(argv=None) -> int:
    return _impl.main(["--benchmark", "bbeh", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
