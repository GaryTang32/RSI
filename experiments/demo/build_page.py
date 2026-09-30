"""Build demo/index.html: an interactive page of the live demo runs (data from plot_demo.py).

    python experiments/demo/plot_demo.py && python experiments/demo/build_page.py
"""
from __future__ import annotations

import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo"


def _files(run: str) -> dict:
    p = DEMO / "runs" / run / "summary.json"
    if not p.exists():
        return {}
    s = json.loads(p.read_text())
    cut = lambda fs: {k: (v if len(v) < 7000 else v[:7000] + "\n... (truncated)") for k, v in (fs or {}).items()}
    return {"seed": cut(s.get("seed_files")), "final": cut(s.get("final_files"))}


def main() -> None:
    runs = json.loads((DEMO / "data.json").read_text())
    for d in runs:
        d["files"] = _files(d["run"])
        if d["run"] == "autoresearch":
            tsv = (DEMO / "runs" / "autoresearch" / "results.tsv").read_text().strip().splitlines()
            d["results_tsv"] = [r.split("\t") for r in tsv]
    tpl = (Path(__file__).parent / "page_template.html").read_text()
    data = json.dumps(runs, default=str).replace("</", "<\\/")
    (DEMO / "index.html").write_text(tpl.replace("/*__DATA__*/null", data))
    print("wrote", DEMO / "index.html", round(len(data) / 1024), "KB of data")


if __name__ == "__main__":
    main()
