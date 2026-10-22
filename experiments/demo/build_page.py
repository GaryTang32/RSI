"""Build demo/index.html, the interactive page of the live demo runs, from plot_demo.py's and spend.py's output.

    python experiments/demo/spend.py && python experiments/demo/plot_demo.py && python experiments/demo/build_page.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from _demo_common import DEMO, RUNS, traces_identical


def _cut(files: dict | None) -> dict:
    return {k: (v if len(v) < 7000 else v[:7000] + "\n... (truncated)") for k, v in (files or {}).items()}


def _files(run: str) -> dict:
    p = RUNS / run / "summary.json"
    if not p.exists():
        return {}
    s = json.loads(p.read_text())
    return {"seed": _cut(s.get("seed_files")), "final": _cut(s.get("final_files"))}


def _tool_calls(run: Path) -> int:
    """Sandboxed tool calls the replayed run made (0: its replay never exercised the sandbox)."""
    return sum(int(n) for p in (run / "store" / "candidates").glob("*/eval/search/per_task/*.json")
               for n in re.findall(r'"tool_calls": (\d+)', p.read_text()))


def _trace_lines(run: Path) -> dict:
    """Practice-trace lines that show a sandbox timeout or name the stable sandbox path."""
    lines = [ln for p in (run / "store" / "candidates").glob("*/eval/search/traces/*")
             for ln in p.read_text(errors="replace").splitlines()]
    return {"timeouts": sum("[killed: timeout]" in ln for ln in lines),
            "sandbox_path": sum("/tmp/rsi_sandbox" in ln for ln in lines)}


def _replay(rc: Path) -> dict:
    replay = rc.parent
    t = traces_identical(replay.parent / replay.name.removesuffix("_replay"), replay)
    return json.loads(rc.read_text()) | {"tool_calls": _tool_calls(replay),
                                         "traces": {"files": t["files"], "identical": t["identical"]},
                                         "trace_lines": _trace_lines(replay)}


def main() -> None:
    runs = json.loads((DEMO / "data.json").read_text())
    for d in runs:
        d["files"] = _files(d["run"])
        if d["run"] == "autoresearch":
            tsv = (RUNS / "autoresearch" / "results.tsv").read_text().strip().splitlines()
            d["results_tsv"] = [r.split("\t") for r in tsv]
    tpl = (Path(__file__).parent / "page_template.html").read_text()
    data = json.dumps(runs, default=str).replace("</", "<\\/")
    extra = {"spend": json.loads((DEMO / "spend.json").read_text())}
    if (DEMO / "replicates.json").exists():
        extra["replicates"] = json.loads((DEMO / "replicates.json").read_text())
    extra["replay"] = [_replay(rc) for rc in (RUNS / "metaharness_replay" / "replay_check.json",
                                              RUNS / "metaharness_hard_replay" / "replay_check.json") if rc.exists()]
    tpl = tpl.replace("/*__EXTRA__*/null", json.dumps(extra).replace("</", "<\\/"))
    (DEMO / "index.html").write_text(tpl.replace("/*__DATA__*/null", data))
    print("wrote", DEMO / "index.html", round(len(data) / 1024), "KB of data")


if __name__ == "__main__":
    main()
