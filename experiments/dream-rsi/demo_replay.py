"""The overview's interactive replay demo, on the real Dream-RSI machinery.

A made-up recorded search (3 branches x 5 attempts, 3 workers). Three strategies -
refine everything / drop the flat branch / stop early - are written as policy code,
replayed by ReplayEvaluator (in the subprocess sandbox) and scored with Eq. 1 at
beta1 = 0.010, beta2 = 0.005. Prints Best / Attempts / Rounds / Score and the
reveal-round map; also sweeps beta1/beta2 over the demo's slider ranges to show
which strategy wins where.

With ``--llm claude:haiku`` the LLM policy developer (Listing-2 prompt) gets one dreaming
step on this single world, starting from "refine everything", and its policy is replayed in
the subprocess sandbox as a fourth row: can the developer find the demo's winner by itself?

    python experiments/dream-rsi/demo_replay.py [--llm sim|claude:haiku] [--quick]
"""
from _common import llm_policies, parse_args, save, figure  # noqa: I001

from rsi.dream import (DEMO_EXPECTED, Eq1Objective, ReplayEvaluator, code_of, demo_tree, parallel_refine, render_demo,
                       run_demo)


def main():
    a = parse_args("Dream-RSI overview demo", default_seeds=1)
    rows = run_demo(0.010, 0.005, runner="subprocess")
    print(render_demo(rows))
    ok = {r.key: round(r.score, 3) for r in rows} == DEMO_EXPECTED and all(r.matches_schedule() for r in rows)
    print(f"\nmatches the overview demo (scores {DEMO_EXPECTED}, reveal schedules): {ok}")
    # slider sweep (demo ranges: beta1 in [0, 0.05], beta2 in [0, 0.03])
    b1s = [round(0.0025 * i, 4) for i in range(0, 21, 1 if not a.quick else 4)]
    b2s = [round(0.0025 * i, 4) for i in range(0, 13, 1 if not a.quick else 4)]
    grid = []
    for b1 in b1s:
        for b2 in b2s:
            rs = run_demo(b1, b2, runner="inprocess")
            win = max(rs, key=lambda r: r.score)
            grid.append({"beta1": b1, "beta2": b2, "winner": win.key, "scores": {r.key: r.score for r in rs}})
    wins = {k: sum(1 for g in grid if g["winner"] == k) for k in DEMO_EXPECTED}
    print(f"winner counts over the slider grid ({len(grid)} settings): {wins}")
    plt, png = figure("demo_replay")
    fig, ax = plt.subplots(figsize=(5.2, 3.6))
    code = {"all": 0, "focus": 1, "stop": 2}
    import numpy as np
    Z = np.array([[code[next(g for g in grid if g["beta1"] == b1 and g["beta2"] == b2)["winner"]] for b1 in b1s]
                  for b2 in b2s])
    from matplotlib.colors import ListedColormap
    ax.imshow(Z, origin="lower", aspect="auto", cmap=ListedColormap(["#4C72B0", "#55A868", "#C44E52"]), vmin=0, vmax=2,
              extent=[b1s[0], b1s[-1], b2s[0], b2s[-1]])
    ax.scatter([0.010], [0.005], c="k", marker="x", label="demo default")
    ax.set_xlabel("beta1 (cost per attempt)")
    ax.set_ylabel("beta2 (parallelism bonus)")
    ax.set_title("Winning strategy: blue=refine all, green=drop flat, red=stop early", fontsize=8)
    ax.legend(fontsize=7, loc="upper right")
    fig.tight_layout()
    fig.savefig(png, dpi=130)
    llm_rows = []
    if a.llm != "sim":   # one dreaming step by the LLM developer on the demo world (subprocess sandbox)
        obj = Eq1Objective(beta1=0.010, beta2=0.005, normalize=False)
        pols = llm_policies(a.llm, [demo_tree()], n=2, W=3, fallback=(3, 4), base_code=code_of(parallel_refine()),
                            objective=obj, root_mode="addressable")
        ev = ReplayEvaluator(obj, W=3, fallback=(3, 4), runner="subprocess", root_mode="addressable")
        for name, code in pols.items():
            rep = ev.evaluate(code, [demo_tree()], label=name)
            e = rep.episodes[0]
            llm_rows.append({"strategy": f"LLM-written ({name})", "key": name, "best": e.best, "attempts": e.N,
                             "rounds": e.k, "score": rep.value, "disqualified": e.disqualified,
                             "reveal_round": e.reveal_round, "code": code})
            print(f"{'LLM-written ' + name:<22}{e.best:>6.2f}{e.N:>10d}{e.k:>8d}{rep.value:>8.3f}"
                  f"{'  (disqualified)' if e.disqualified else ''}")
            print(demo_tree().render_grid(e.reveal_round, labels={0: "A", 1: "B", 2: "C"}))
    save("demo_replay", {"config": {"W": 3, "beta1": 0.010, "beta2": 0.005, "runner": "subprocess",
                                    "llm": a.llm if a.llm != "sim" else "not used"},
                         "llm_rows": llm_rows,
                         "rows": [{"strategy": r.label, "key": r.key, "best": r.best, "attempts": r.attempts,
                                   "rounds": r.rounds, "score": r.score, "reveal_round": r.reveal_round} for r in rows],
                         "expected": DEMO_EXPECTED, "matches_overview": ok, "slider_grid_winners": wins,
                         "slider_grid": grid, "figure": str(png),
                         "verdict": "REPRODUCED: identical Best/Attempts/Rounds/Score and reveal maps; 'Drop the flat "
                                    "branch' wins at the default settings" if ok else "NOT reproduced"}, a.out)


if __name__ == "__main__":
    main()
