"""Turn the live demo traces into per-iteration data and graphs.

Reads ``demo/runs/<method>/trace.jsonl`` (+ ``summary.json``) written by ``live_demo.py`` and writes

- ``demo/data.json``: per-iteration rows for every run (candidates tried, the gate's verdict,
  the incumbent's practice score, and the sealed held-out / ood scores from the shadow monitor);
- ``demo/figures/*.png``: one iteration chart per method plus a seed-vs-final chart.

Everything plotted comes straight from the trace; nothing is re-computed or smoothed::

    python experiments/demo/plot_demo.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo"
RUNS = DEMO / "runs"

# reference categorical palette (dataviz skill), fixed order: evolve, holdout, ood
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
DISCARD = "#9a9994"


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def extract(name: str) -> dict:
    """Per-iteration rows from one trace, in the order the loop produced them."""
    ev = load(RUNS / name / "trace.jsonl")
    base = next(e["data"] for e in ev if e["kind"] == "baseline")
    base_name = base.get("candidate") or "seed"
    base_S = base["summary"]["S"]
    sealed: dict[str, dict] = {}
    for e in ev:
        if e["kind"] == "monitor":
            sealed[e["data"]["version"]] = {k: v.get("S") for k, v in e["data"]["sealed"].items()}
    rounds: dict = {}
    order: list = []
    for e in ev:
        r = e["round"]
        if e["kind"] == "eval" and e["data"].get("candidate") not in (base_name, "baseline_0") and \
                e["data"].get("role") != "baseline":
            if r not in rounds:
                rounds[r] = {"candidates": [], "decision": None}
                order.append(r)
            d = e["data"]
            rounds[r]["candidates"].append({"name": d["candidate"], "S": d["summary"].get("S"),
                                            "C": d["summary"].get("C")})
        if e["kind"] == "gate" and r in rounds:
            for c in rounds[r]["candidates"]:
                if c["name"] == e["data"]["candidate"]:
                    c["admissible"] = e["data"].get("accept")
                    c["reason"] = e["data"].get("reason")
        if e["kind"] == "proposal" and r is not None:
            d = e["data"]
            rounds.setdefault(r, {"candidates": [], "decision": None})
            if r not in order:
                order.append(r)
            rounds[r].setdefault("proposals", {})[d.get("candidate")] = d.get("change") or d.get("hypothesis") or d.get("description")
        if e["kind"] == "decision" and r in rounds:
            rounds[r]["decision"] = e["data"]
    rows = [{"iter": 0, "incumbent": base_name, "S": base_S, "sealed": sealed.get(base_name, {}), "candidates": []}]
    inc, inc_S = base_name, base_S
    for i, r in enumerate(order, start=1):
        R = rounds[r]
        dec = R["decision"] or {}
        # the kept candidate: named directly (Meta-Harness, RRSI), or the round's single experiment when
        # autoresearch's status is "keep" (its decision names commits, not candidates)
        kept = next((c for c in R["candidates"] if c["name"] in (dec.get("kept"), dec.get("incumbent_after"))
                     and dec.get("incumbent_after") != dec.get("incumbent_before")), None)
        if kept is None and dec.get("status") == "keep" and len(R["candidates"]) == 1:
            kept = R["candidates"][0]
        if kept is not None:
            inc, inc_S = kept["name"], kept["S"]
        props = R.get("proposals", {})
        for c in R["candidates"]:
            c["kept"] = c is kept
            c["change"] = props.get(c["name"])
        rows.append({"iter": i, "incumbent": inc, "S": inc_S, "sealed": sealed.get(inc, rows[-1]["sealed"]),
                     "candidates": R["candidates"], "why": dec.get("why")})
    summ_p = RUNS / name / "summary.json"
    summ = json.loads(summ_p.read_text()) if summ_p.exists() else {}
    out = {"run": name, "rows": rows, "usd_total": summ.get("usd_total"), "wall_s": summ.get("wall_s"),
           "stop_reason": summ.get("stop_reason")}
    if "transfer" in summ:
        out["transfer"] = {s: {a: {"S": v["S"], "vs": v.get("vs_reference")} for a, v in row.items()}
                           for s, row in summ["transfer"]["splits"].items()}
    return out


def _style(ax, title: str, ylabel: str) -> None:
    ax.set_facecolor(SURF)
    ax.set_title(title, loc="left", fontsize=12, color=INK, fontweight="bold")
    ax.set_ylabel(ylabel, color=INK2, fontsize=10)
    ax.tick_params(colors=INK2, labelsize=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_harness(d: dict, title: str, path: Path) -> None:
    import matplotlib.pyplot as plt
    rows = d["rows"]
    xs = [r["iter"] for r in rows]
    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=150)
    fig.patch.set_facecolor(SURF)
    _style(ax, title, "accuracy (exact match)")
    # every candidate the proposer produced, at its practice score
    for r in rows[1:]:
        for c in r["candidates"]:
            if c["S"] is None:
                continue
            ax.scatter(r["iter"], c["S"], s=46, zorder=3, facecolor=BLUE if c.get("kept") else "white",
                       edgecolor=BLUE if c.get("kept") else DISCARD, linewidth=1.5)
    series = [("incumbent, practice tasks", [r["S"] for r in rows], BLUE),
              ("incumbent, sealed held-out", [r["sealed"].get("holdout") for r in rows], ORANGE),
              ("incumbent, sealed OOD (unseen task types)", [r["sealed"].get("ood") for r in rows], AQUA)]
    for label, ys, col in series:
        pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
        if pts:
            ax.step([p[0] for p in pts], [p[1] for p in pts], where="post", color=col, linewidth=2, label=label)
            ax.annotate(f"{pts[-1][1]:.2f}", (pts[-1][0], pts[-1][1]), xytext=(6, 0), textcoords="offset points",
                        va="center", fontsize=9, color=INK2)
    ax.scatter([], [], s=46, facecolor=BLUE, edgecolor=BLUE, label="candidate kept")
    ax.scatter([], [], s=46, facecolor="white", edgecolor=DISCARD, label="candidate discarded")
    ax.set_xticks(xs, ["seed"] + [str(x) for x in xs[1:]])
    ax.set_xlabel("iteration", color=INK2, fontsize=10)
    ax.set_ylim(-0.03, 1.08)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=3, fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURF)
    plt.close(fig)


def plot_autoresearch(d: dict, path: Path) -> None:
    import matplotlib.pyplot as plt
    rows = d["rows"]
    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=150)
    fig.patch.set_facecolor(SURF)
    _style(ax, "autoresearch: Haiku edits train.py, one experiment at a time", "val_bpb (lower is better)")
    for r in rows[1:]:
        for c in r["candidates"]:
            if c["S"] is None:
                ax.scatter(r["iter"], rows[0]["S"], marker="x", color=DISCARD, s=40, zorder=3)
                continue
            ax.scatter(r["iter"], c["S"], s=46, zorder=3, facecolor=BLUE if c.get("kept") else "white",
                       edgecolor=BLUE if c.get("kept") else DISCARD, linewidth=1.5)
    xs = [r["iter"] for r in rows]
    ax.step(xs, [r["S"] for r in rows], where="post", color=BLUE, linewidth=2, label="running best, validation")
    hid = [(r["iter"], r["sealed"].get("test_iid")) for r in rows if r["sealed"].get("test_iid") is not None]
    if hid:
        ax.step([h[0] for h in hid] + [xs[-1]], [h[1] for h in hid] + [hid[-1][1]], where="post", color=ORANGE,
                linewidth=2, label="same model on a hidden test split")
    ax.scatter([], [], s=46, facecolor=BLUE, edgecolor=BLUE, label="experiment kept")
    ax.scatter([], [], s=46, facecolor="white", edgecolor=DISCARD, label="experiment discarded")
    ax.set_xticks(xs, ["baseline"] + [str(x) for x in xs[1:]])
    ax.set_xlabel("experiment", color=INK2, fontsize=10)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURF)
    plt.close(fig)


def plot_seed_vs_final(runs: list[dict], path: Path) -> None:
    import matplotlib.pyplot as plt
    have = [d for d in runs if d.get("transfer")]
    if not have:
        return
    fig, axes = plt.subplots(1, len(have), figsize=(4.2 * len(have), 3.8), dpi=150, squeeze=False)
    fig.patch.set_facecolor(SURF)
    for ax, d in zip(axes[0], have):
        _style(ax, d["run"], "accuracy" if ax is axes[0][0] else "")
        splits = ["evolve", "holdout", "ood"]
        labels = ["practice", "held-out", "OOD"]
        seed = [d["transfer"][s]["seed"]["S"] for s in splits]
        fin = [d["transfer"][s]["final"]["S"] for s in splits]
        x = range(len(splits))
        ax.bar([i - 0.19 for i in x], seed, width=0.36, color=DISCARD, label="seed harness")
        ax.bar([i + 0.19 for i in x], fin, width=0.36, color=BLUE, label="improved harness")
        for i, (a, b) in enumerate(zip(seed, fin)):
            ax.annotate(f"{a:.2f}", (i - 0.19, a), xytext=(0, 3), textcoords="offset points", ha="center",
                        fontsize=8, color=INK2)
            ax.annotate(f"{b:.2f}", (i + 0.19, b), xytext=(0, 3), textcoords="offset points", ha="center",
                        fontsize=8, color=INK)
        ax.set_xticks(list(x), labels)
        ax.set_ylim(0, 1.15)
    axes[0][0].legend(loc="upper left", fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURF)
    plt.close(fig)


def main() -> None:
    names = [n for n in ("metaharness", "rrsi", "autoresearch") if (RUNS / n / "trace.jsonl").exists()]
    runs = [extract(n) for n in names]
    (DEMO / "data.json").write_text(json.dumps(runs, indent=1, default=str))
    figs = DEMO / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    titles = {"metaharness": "Meta-Harness: Haiku rewrites its own AgentQA harness",
              "rrsi": "RRSI: Haiku edits the AgentQA harness under guards"}
    for d in runs:
        if d["run"] == "autoresearch":
            plot_autoresearch(d, figs / "autoresearch_iterations.png")
        else:
            plot_harness(d, titles[d["run"]], figs / f"{d['run']}_iterations.png")
    plot_seed_vs_final(runs, figs / "seed_vs_final.png")
    print(json.dumps([{"run": d["run"], "iters": len(d["rows"]) - 1, "usd": d["usd_total"]} for d in runs]))


if __name__ == "__main__":
    sys.exit(main())
