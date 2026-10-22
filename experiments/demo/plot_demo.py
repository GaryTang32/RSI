"""Turn the live demo traces into per-iteration data (demo/data.json, demo/replicates.json) and demo/figures/*.png.

    python experiments/demo/plot_demo.py   # reads demo/runs/<run>/ and demo/spend.json; nothing is smoothed
"""
from __future__ import annotations

import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import matplotlib.pyplot as plt
from _demo_common import DEMO, RUNS, load_jsonl

# reference categorical palette (dataviz skill), fixed order: evolve, holdout, ood
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID, SURF = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
DISCARD = "#9a9994"
SUBTITLE = {"metaharness": "in-loop practice scores and shadow-monitor readings, one trial per task (trial seed 0)",
            "rrsi": "in-loop practice scores: one trial per task, a hashed trial seed per evaluation\n"
                    "shadow-monitor readings: one trial per task, trial seed 0"}
#: every complete Meta-Harness run on the standard AgentQA suite (same settings, seed harness and model):
#: label, run directory, note
REPLICATES = [
    ("Run 1", "_archive/metaharness_before_fix", "before the replay fix"),
    ("Run 2", "_archive/metaharness_fix_v1", "after part 1 of the fix"),
    ("Run 3", "_archive/metaharness_fix_v2", "after part 2 of the fix"),
    ("Run 4", "metaharness", "final code; cut off by the usage cap after iteration 3, then resumed from its cache"),
]


def r2(v) -> str:
    """Two decimals, exact halves rounded up: the digits JavaScript's toFixed(2) gives the page."""
    return str(Decimal(repr(float(v))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def spend() -> dict:
    p = DEMO / "spend.json"
    return json.loads(p.read_text()) if p.exists() else {}


def fresh_transfer(rel: str) -> dict | None:
    """The run's independent re-score (``rescore.json``) as {split: {arm: {S, vs, missing}}} plus its settings."""
    p = RUNS / rel / "rescore.json"
    if not p.exists():
        return None
    r = json.loads(p.read_text())
    out = {sp: {a: {"S": v["S"], "vs": v.get("vs_reference"), "missing": v.get("missing")} for a, v in row.items()}
           for sp, row in r["transfer"]["splits"].items()}
    return {"splits": out, "k": r["k"], "seed_offset": r["seed_offset"], "usd": r["usd"], "clean": r["clean"]}


def _rounds(ev: list[dict], base_name: str) -> tuple[dict, list]:
    """Each loop round's candidates (with gate verdicts), proposals and decision, and the rounds in loop order."""
    rounds: dict = {}
    order: list = []
    for e in ev:
        r, d = e["round"], e["data"]
        if e["kind"] == "eval" and d.get("candidate") not in (base_name, "baseline_0") and d.get("role") != "baseline":
            if r not in rounds:
                rounds[r] = {"candidates": [], "decision": None}
                order.append(r)
            rounds[r]["candidates"].append({"name": d["candidate"], "S": d["summary"].get("S"),
                                            "C": d["summary"].get("C")})
        if e["kind"] == "gate" and r in rounds:
            for c in rounds[r]["candidates"]:
                if c["name"] == d["candidate"]:
                    c["admissible"] = d.get("accept")
                    c["reason"] = d.get("reason")
        if e["kind"] == "proposal" and r is not None:
            rounds.setdefault(r, {"candidates": [], "decision": None})
            if r not in order:
                order.append(r)
            props = rounds[r].setdefault("proposals", {})
            text = d.get("change") or d.get("hypothesis") or d.get("description")
            # critic repairs and failed retries add proposal events: describe a candidate by its last one that worked
            if text and (not d.get("error") or d.get("candidate") not in props):
                props[d.get("candidate")] = text
        if e["kind"] == "decision" and r in rounds:
            rounds[r]["decision"] = d
    return rounds, order


def _harness_doc(run: str, candidate: str) -> str | None:
    """The docstring a Meta-Harness proposer wrote atop the candidate's harness.py."""
    src = RUNS / run / "store" / "candidates" / candidate / "src" / "harness.py"
    if src.exists() and (m := re.match(r'\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', src.read_text(), re.S)):
        return " ".join(m.group(1).split())
    return None


def extract(name: str) -> dict:
    """Per-iteration rows from one trace, in the order the loop produced them."""
    ev = load_jsonl(RUNS / name / "trace.jsonl")
    base = next(e["data"] for e in ev if e["kind"] == "baseline")
    base_name = base.get("candidate") or "seed"
    base_S = base["summary"]["S"]
    sealed = {e["data"]["version"]: {k: v.get("S") for k, v in e["data"]["sealed"].items()}
              for e in ev if e["kind"] == "monitor"}
    rounds, order = _rounds(ev, base_name)
    rows = [{"iter": 0, "incumbent": base_name, "S": base_S, "sealed": sealed.get(base_name, {}), "candidates": []}]
    inc, inc_S = base_name, base_S
    for i, r in enumerate(order, start=1):
        R = rounds[r]
        dec = R["decision"] or {}
        # the kept candidate: named by Meta-Harness and RRSI, or autoresearch's single experiment with status "keep"
        kept = next((c for c in R["candidates"] if c["name"] in (dec.get("kept"), dec.get("incumbent_after"))
                     and dec.get("incumbent_after") != dec.get("incumbent_before")), None)
        if kept is None and dec.get("status") == "keep" and len(R["candidates"]) == 1:
            kept = R["candidates"][0]
        if kept is not None:
            inc, inc_S = kept["name"], kept["S"]
        props = R.get("proposals", {})
        per = dec.get("per_candidate") or {}
        for c in R["candidates"]:
            if not props.get(c["name"]) and (doc := _harness_doc(name, c["name"])) is not None:
                props[c["name"]] = doc
            c["kept"] = c is kept
            c["change"] = props.get(c["name"])
            if per:   # Meta-Harness keeps every candidate in its population and ranks the Pareto frontier
                c["state"] = ("incumbent" if c["kept"] else
                              "frontier" if (per.get(c["name"]) or {}).get("on_frontier") else "population")
            else:     # RRSI and autoresearch keep one version and discard the rest
                c["state"] = "incumbent" if c["kept"] else "discarded"
        rows.append({"iter": i, "incumbent": inc, "S": inc_S, "sealed": sealed.get(inc, rows[-1]["sealed"]),
                     "candidates": R["candidates"], "why": dec.get("why")})
    summ_p = RUNS / name / "summary.json"
    summ = json.loads(summ_p.read_text()) if summ_p.exists() else {}
    end = next((e["data"] for e in reversed(ev) if e["kind"] == "run_end"), {})
    sp = (spend().get("reported") or {}).get(name) or {}
    out = {"run": name, "rows": rows, "suite": (summ.get("setup") or {}).get("suite") or "easy",
           "usd_attempt": (sp.get("attempts") or {}).get(name, summ.get("usd_total")), "usd_full": sp.get("usd_full"),
           "attempts": sp.get("attempts"), "usd_original_report": sp.get("usd_original_report"),
           "kept_seed": bool(summ.get("seed_files")) and summ.get("seed_files") == summ.get("final_files"),
           "stop_reason": summ.get("stop_reason") or end.get("stop_reason"), "has_summary": bool(summ),
           "method": next(m for m in ("metaharness", "rrsi", "autoresearch") if name.startswith(m))}
    fresh = fresh_transfer(name)
    if fresh:
        out["transfer"] = fresh["splits"]
        out["transfer_k"], out["transfer_seed_offset"], out["transfer_fresh"] = fresh["k"], fresh["seed_offset"], True
        out["usd_rescore"] = sp.get("usd_rescore") or fresh["usd"]   # the spend record survives a $0 replay
    elif "transfer" in summ:
        # live_demo._finish's report: fresh with a seed offset; older ones used trial seeds from 0 (the loop's own)
        out["transfer"] = {s: {a: {"S": v["S"], "vs": v.get("vs_reference")} for a, v in row.items()}
                           for s, row in summ["transfer"]["splits"].items()}
        out["transfer_k"] = summ["transfer"].get("k") or (1 if out["suite"] == "hard" else 2)
        out["transfer_seed_offset"] = summ["transfer"].get("seed_offset", 0)
        out["transfer_fresh"] = out["transfer_seed_offset"] > 0
        if out["transfer_fresh"]:
            out["usd_rescore"] = summ.get("usd_transfer_report")
    return out


def _style(ax, title: str, ylabel: str, pad: float | None = None) -> None:
    ax.set_facecolor(SURF)
    ax.set_title(title, loc="left", fontsize=12, color=INK, fontweight="bold", pad=pad)
    ax.set_ylabel(ylabel, color=INK2, fontsize=10)
    ax.tick_params(colors=INK2, labelsize=9)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_harness(d: dict, title: str, path: Path) -> None:
    rows = d["rows"]
    xs = [r["iter"] for r in rows]
    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=150)
    fig.patch.set_facecolor(SURF)
    sub = SUBTITLE[d["method"]]
    _style(ax, title, "accuracy (exact match)", pad=20 + 11 * sub.count("\n"))
    ax.text(0, 1.015, sub, transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom")
    # filled = became the new incumbent; Meta-Harness keeps the rest (ring = joined the Pareto frontier, hollow =
    # dominated), RRSI discards it (hollow gray)
    style = {"incumbent": dict(facecolor=BLUE, edgecolor=BLUE), "frontier": dict(facecolor="#cfe0f5", edgecolor=BLUE),
             "population": dict(facecolor="white", edgecolor=DISCARD),
             "discarded": dict(facecolor="white", edgecolor=DISCARD)}
    for r in rows[1:]:
        n = len(r["candidates"])
        # spread same-iteration candidates sideways; draw the new incumbent last so it stays on top
        for j, c in sorted(enumerate(r["candidates"]), key=lambda p: bool(p[1].get("kept"))):
            if c["S"] is None:
                continue
            ax.scatter(r["iter"] + (j - (n - 1) / 2) * 0.09, c["S"], s=46, zorder=4 if c.get("kept") else 3,
                       linewidth=1.5, **style[c.get("state", "incumbent" if c.get("kept") else "discarded")])
    series = [("incumbent, practice tasks", [r["S"] for r in rows], BLUE, 3.4),
              ("incumbent, sealed held-out", [r["sealed"].get("holdout") for r in rows], ORANGE, 2),
              ("incumbent, sealed OOD (unseen task types)", [r["sealed"].get("ood") for r in rows], AQUA, 2)]
    # practice underneath and wider, sealed lines on top: where they coincide both stay visible
    ends = []
    for label, ys, col, lw in series:
        pts = [(x, y) for x, y in zip(xs, ys) if y is not None]
        if pts:
            ax.step([p[0] for p in pts], [p[1] for p in pts], where="post", color=col, linewidth=lw, label=label,
                    zorder=2 if col == BLUE else 2.5, linestyle=(0, (5, 3)) if col == AQUA else "-")
            ends.append([pts[-1][1], pts[-1][1], col])
    ends.sort(key=lambda e: e[0])
    for i in range(1, len(ends)):   # keep the end labels of coincident values apart
        ends[i][1] = max(ends[i][1], ends[i - 1][1] + 0.06)
    for v, ypos, col in ends:
        ax.annotate(r2(v), (xs[-1], ypos), xytext=(14, 0), textcoords="offset points", va="center", fontsize=9,
                    color=col, annotation_clip=False)
    keep_lab = "candidate became the incumbent"
    ax.scatter([], [], s=46, facecolor=BLUE, edgecolor=BLUE, label=keep_lab)
    if d.get("method") == "metaharness":
        ax.scatter([], [], s=46, facecolor="#cfe0f5", edgecolor=BLUE, label="kept, joined the Pareto frontier")
        ax.scatter([], [], s=46, facecolor="white", edgecolor=DISCARD, label="kept in the population, dominated")
    else:
        ax.scatter([], [], s=46, facecolor="white", edgecolor=DISCARD, label="candidate discarded")
    ax.set_xticks(xs, ["seed"] + [str(x) for x in xs[1:]])
    ax.set_xlabel("iteration", color=INK2, fontsize=10)
    ax.set_ylim(-0.03, 1.08)
    h, lab = ax.get_legend_handles_labels()
    want = [s[0] for s in series] + [keep_lab, "kept, joined the Pareto frontier", "kept in the population, dominated",
                                     "candidate discarded"]
    pairs = sorted(zip(h, lab), key=lambda p: want.index(p[1]) if p[1] in want else 99)
    ax.legend([p[0] for p in pairs], [p[1] for p in pairs], loc="upper center", bbox_to_anchor=(0.5, -0.2),
              ncol=3, fontsize=8, frameon=False)
    fig.tight_layout()
    _check_inside(fig, path)
    fig.savefig(path, facecolor=SURF)
    plt.close(fig)


def _check_inside(fig, path: Path) -> None:
    """Fail the build rather than ship a figure whose title, subtitle or legend runs off the canvas."""
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    W, H = fig.bbox.width, fig.bbox.height
    for t in [*fig.texts, *(t for ax in fig.axes for t in (ax.title, ax._left_title, *ax.texts)),
              *(ax.get_legend() for ax in fig.axes if ax.get_legend())]:
        if t is None or (hasattr(t, "get_text") and not t.get_text()):
            continue
        b = t.get_window_extent(r)
        if b.x0 < -1 or b.x1 > W + 1 or b.y0 < -1 or b.y1 > H + 1:
            raise SystemExit(f"{path.name}: {getattr(t, 'get_text', lambda: 'legend')()[:40]!r} runs off the figure")


def plot_autoresearch(d: dict, path: Path) -> None:
    rows = d["rows"]
    fig, ax = plt.subplots(figsize=(8, 4.8), dpi=150)
    fig.patch.set_facecolor(SURF)
    _style(ax, "autoresearch: Haiku edits train.py, one experiment at a time", "val_bpb (lower is better)", pad=20)
    ax.text(0, 1.015, "values the loop selected on (one training run each); hidden test from the shadow audit "
            "(one audit run per kept version)", transform=ax.transAxes, fontsize=8.5, color=INK2, va="bottom")
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
                linewidth=2, label="hidden test split (the shadow audit's own training run of the same train.py)")
    ax.scatter([], [], s=46, facecolor=BLUE, edgecolor=BLUE, label="experiment kept")
    ax.scatter([], [], s=46, facecolor="white", edgecolor=DISCARD, label="experiment discarded")
    ax.set_xticks(xs, ["baseline"] + [str(x) for x in xs[1:]])
    ax.set_xlabel("experiment", color=INK2, fontsize=10)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=2, fontsize=8, frameon=False)
    fig.tight_layout()
    fig.savefig(path, facecolor=SURF)
    plt.close(fig)


def plot_seed_vs_final(runs: list[dict], path: Path) -> None:
    have = sorted((d for d in runs if d.get("transfer")), key=lambda d: ("hard" in d["run"], d["run"]))
    if not have:
        return
    fig, axes = plt.subplots(1, len(have), figsize=(4.2 * len(have), 4.1), dpi=150, squeeze=False)
    fig.patch.set_facecolor(SURF)
    titles = {"metaharness": "Meta-Harness (Run 4)", "rrsi": "RRSI", "metaharness_hard": "Meta-Harness, hard",
              "rrsi_hard": "RRSI, hard"}
    splits = ["evolve", "holdout", "ood"]
    for ax, d in zip(axes[0], have):
        _style(ax, titles.get(d["run"], d["run"]), "accuracy" if ax is axes[0][0] else "")
        seed = [d["transfer"][s]["seed"]["S"] for s in splits]
        fin = [d["transfer"][s]["final"]["S"] for s in splits]
        x = range(len(splits))
        ax.bar([i - 0.19 for i in x], seed, width=0.36, color=DISCARD, label="seed harness")
        ax.bar([i + 0.19 for i in x], fin, width=0.36, color=BLUE, label="harness after the run")
        for i, (a, b) in enumerate(zip(seed, fin)):
            ax.annotate(r2(a), (i - 0.19, a), xytext=(0, 3), textcoords="offset points", ha="center",
                        fontsize=8, color=INK2)
            ax.annotate(r2(b), (i + 0.19, b), xytext=(0, 3), textcoords="offset points", ha="center",
                        fontsize=8, color=INK)
        ax.set_xticks(list(x), ["practice", "held-out", "OOD"])
        ax.set_ylim(0, 1.15)
    fig.legend(*axes[0][0].get_legend_handles_labels(), loc="lower center", ncol=2, fontsize=8, frameon=False)
    ks = sorted({(d["transfer_k"], d["transfer_seed_offset"]) for d in have})
    fig.suptitle("Seed vs final harness, re-scored after each run with fresh trials ("
                 + "; ".join(f"{k} per task, seeds {o}-{o + k - 1}" for k, o in ks) + ")",
                 x=0.01, ha="left", fontsize=11, color=INK, fontweight="bold")
    fig.tight_layout(rect=(0, 0.07, 1, 0.93))
    fig.savefig(path, facecolor=SURF)
    plt.close(fig)


def replicates() -> list[dict]:
    out = []
    rep = spend().get("reported") or {}
    for label, rel, note in REPLICATES:
        fresh = fresh_transfer(rel)
        if fresh is None:
            continue
        sp = fresh["splits"]
        out.append({"label": label, "dir": rel, "note": note, "usd": (rep.get(rel) or {}).get("usd_full"),
                    "k": fresh["k"], "seed_offset": fresh["seed_offset"],
                    "scores": {k: {"seed": sp[k]["seed"]["S"], "final": sp[k]["final"]["S"],
                                   "vs": sp[k]["final"].get("vs")} for k in sp}})
    return out


def plot_replicates(reps: list[dict], path: Path) -> None:
    """Dumbbell chart: one row per run, seed (gray) to final (blue), one panel per split."""
    if not reps:
        return
    splits = [("evolve", "practice"), ("holdout", "held-out"), ("ood", "OOD")]
    fig, axes = plt.subplots(1, 3, figsize=(11, 0.55 * len(reps) + 1.9), dpi=150, sharey=True)
    fig.patch.set_facecolor(SURF)
    ys = list(range(len(reps)))[::-1]
    for ax, (k, lab) in zip(axes, splits):
        _style(ax, lab, "")
        ax.grid(axis="y", visible=False)
        ax.grid(axis="x", color=GRID, linewidth=0.8)
        for y, r in zip(ys, reps):
            a, b = r["scores"][k]["seed"], r["scores"][k]["final"]
            ax.plot([a, b], [y, y], color=GRID, linewidth=3, zorder=1, solid_capstyle="round")
            ax.scatter([a], [y], s=46, color=DISCARD, zorder=2)
            ax.scatter([b], [y], s=46, color=BLUE, zorder=3)
            if abs(a - b) < 1e-9:   # unchanged: show the seed as a ring around the final point
                ax.scatter([a], [y], s=130, facecolor="none", edgecolor=DISCARD, linewidth=1.8, zorder=4)
            ax.annotate(r2(b), (b, y), xytext=(0, 7), textcoords="offset points", ha="center", fontsize=8,
                        color=INK)
        ax.set_xlim(-0.05, 1.1)
        ax.set_xticks([0, 0.5, 1])
    axes[0].set_yticks(ys, [r["label"] for r in reps])
    axes[0].set_ylim(-0.7, len(reps) - 0.3)
    axes[0].scatter([], [], s=46, color=DISCARD, label="seed harness")
    axes[0].scatter([], [], s=46, color=BLUE, label="harness after the run")
    fig.legend(*axes[0].get_legend_handles_labels(), loc="lower center", ncol=2, fontsize=8, frameon=False)
    fig.suptitle("Meta-Harness, four complete runs, same configuration (re-scored with fresh trials, two per task)",
                 x=0.01, ha="left", fontsize=11, color=INK, fontweight="bold")
    fig.tight_layout(rect=(0, 0.08, 1, 0.95))
    fig.savefig(path, facecolor=SURF)
    plt.close(fig)


def main() -> None:
    names = [n for n in ("metaharness_hard", "rrsi_hard", "metaharness", "metaharness_a", "rrsi", "autoresearch")
             if (RUNS / n / "trace.jsonl").exists()]
    runs = [extract(n) for n in names]
    (DEMO / "data.json").write_text(json.dumps(runs, indent=1, default=str))
    figs = DEMO / "figures"
    figs.mkdir(parents=True, exist_ok=True)
    titles = {"metaharness": "Meta-Harness Run 4: Haiku rewrites its own AgentQA harness",
              "metaharness_a": "Meta-Harness, interrupted replicate (before the replay fix)",
              "metaharness_hard": "Meta-Harness on the hard tasks (logic grids + ledgers)",
              "rrsi_hard": "RRSI on the hard tasks (logic grids + ledgers)",
              "rrsi": "RRSI: Haiku edits the AgentQA harness under guards"}
    for d in runs:
        if d["run"] == "autoresearch":
            plot_autoresearch(d, figs / "autoresearch_iterations.png")
        else:
            plot_harness(d, titles[d["run"]], figs / f"{d['run']}_iterations.png")
    plot_seed_vs_final(runs, figs / "seed_vs_final.png")
    reps = replicates()
    plot_replicates(reps, figs / "metaharness_replicates.png")
    (DEMO / "replicates.json").write_text(json.dumps(reps, indent=1))
    print(json.dumps([{"run": d["run"], "iters": len(d["rows"]) - 1, "usd_full": d["usd_full"]} for d in runs]))


if __name__ == "__main__":
    main()
