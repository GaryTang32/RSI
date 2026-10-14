"""Build demo/index.html: an interactive page of the live demo runs (data from plot_demo.py).

    python experiments/demo/spend.py && python experiments/demo/plot_demo.py && python experiments/demo/build_page.py
"""
from __future__ import annotations

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


def _tool_calls(run: Path) -> int:
    """Sandboxed tool calls the replayed run made (0 means its replay never exercised the sandbox)."""
    import re
    return sum(int(n) for p in (run / "store" / "candidates").glob("*/eval/search/per_task/*.json")
               for n in re.findall(r'"tool_calls": (\d+)', p.read_text()))


def _traces_identical(replay: Path) -> dict:
    """Same comparison as live_demo.traces_identical, for replays recorded before it existed."""
    src = replay.parent / replay.name.removesuffix("_replay")
    rel = lambda d: {p.relative_to(d): p.read_bytes() for p in (d / "store" / "candidates").glob("*/eval/*/traces/*")}
    a, b = rel(src), rel(replay)
    return {"files": len(a), "identical": sum(1 for k in a if b.get(k) == a[k])}


def main() -> None:
    runs = json.loads((DEMO / "data.json").read_text())
    for d in runs:
        d["files"] = _files(d["run"])
        if d["run"] == "autoresearch":
            tsv = (DEMO / "runs" / "autoresearch" / "results.tsv").read_text().strip().splitlines()
            d["results_tsv"] = [r.split("\t") for r in tsv]
    tpl = (Path(__file__).parent / "page_template.html").read_text()
    data = json.dumps(runs, default=str).replace("</", "<\\/")
    # every live call is in an LLM cache; spend.py attributes each to the attempt that paid for it
    extra = {"spend": json.loads((DEMO / "spend.json").read_text())}
    if (DEMO / "replicates.json").exists():
        extra["replicates"] = json.loads((DEMO / "replicates.json").read_text())
    extra["replay"] = [json.loads(rc.read_text()) | {"tool_calls": _tool_calls(rc.parent),
                                                     "traces": _traces_identical(rc.parent)}
                       for rc in (DEMO / "runs" / "metaharness_replay" / "replay_check.json",
                                  DEMO / "runs" / "metaharness_hard_replay" / "replay_check.json") if rc.exists()]
    tpl = tpl.replace("/*__EXTRA__*/null", json.dumps(extra).replace("</", "<\\/"))
    (DEMO / "index.html").write_text(tpl.replace("/*__DATA__*/null", data))
    print("wrote", DEMO / "index.html", round(len(data) / 1024), "KB of data")


if __name__ == "__main__":
    main()
