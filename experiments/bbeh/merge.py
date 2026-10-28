"""BBEH merge: ``experiments/bbh/merge.py --benchmark bbeh`` (results/bbeh/merged.{json,md}; no cot3 column)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "bbh" / "merge.py"
_spec = importlib.util.spec_from_file_location("bbh_merge", _SRC)
_impl = importlib.util.module_from_spec(_spec)
sys.modules["bbh_merge"] = _impl
_spec.loader.exec_module(_impl)
merge = _impl.merge


def main(argv=None) -> int:
    return _impl.main(["--benchmark", "bbeh", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    raise SystemExit(main())
