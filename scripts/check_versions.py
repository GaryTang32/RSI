"""Check that every place that states the release version agrees with pyproject.toml.

python scripts/check_versions.py
"""

import json
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    want = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    found = {
        ".claude-plugin/plugin.json": json.loads((REPO / ".claude-plugin/plugin.json").read_text()).get("version"),
        "CITATION.cff": (
            re.search(r'^version:\s*"?([^"\n]+)"?', (REPO / "CITATION.cff").read_text(), re.M) or [None, None]
        )[1],
    }
    changelog = (REPO / "CHANGELOG.md").read_text()
    found["CHANGELOG.md"] = want if re.search(rf"^## \[{re.escape(want)}\]", changelog, re.M) else None
    bad = {f: v for f, v in found.items() if v != want}
    for f, v in bad.items():
        print(f"{f}: {v!r}, expected {want!r}")
    print(f"version {want}: {'ok' if not bad else 'MISMATCH'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
