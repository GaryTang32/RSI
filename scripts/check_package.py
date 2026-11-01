"""Check that a built wheel contains every non-Python file tracked under rsi/ (prompts, manifests, schemas).

python scripts/check_package.py dist/rsi-*.whl
"""

import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    names = set(zipfile.ZipFile(argv[0]).namelist())
    tracked = subprocess.run(["git", "ls-files", "rsi"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    data = [f for f in tracked.split() if not f.endswith(".py")]
    missing = [f for f in data if f not in names]
    print(f"{len(data)} data files under rsi/, {len(missing)} missing from {Path(argv[0]).name}")
    for f in missing:
        print("  missing:", f)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
