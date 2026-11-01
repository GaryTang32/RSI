"""Print the CHANGELOG.md section for one version (the release notes).

python scripts/release_notes.py 0.1.0
"""

import re
import sys
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parents[1] / "CHANGELOG.md"


def section(version: str, text: str) -> str:
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"no '## [{version}]' section in {CHANGELOG.name}")
    return m.group(1).strip() + "\n"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip())
    print(section(sys.argv[1], CHANGELOG.read_text()), end="")
