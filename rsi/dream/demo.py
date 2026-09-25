"""The overview's interactive replay demo, reproduced on the real machinery.

"A made-up search with three branches and five attempts each, run with three
parallel workers. Every score already exists in the record, so testing a strategy
costs nothing" [doc, ``replayDemo()``]. Three strategies are written as policy
*code* and replayed by :class:`ReplayEvaluator` with Eq. 1 on raw scores
(beta1 = 0.010, beta2 = 0.005; the demo's best starts at 0 and the root is not an
attempt). Expected scores: refine everything 0.575, drop the flat branch 0.611,
stop early 0.475 (spec §9.1 g).
"""
from __future__ import annotations

from dataclasses import dataclass

from .evaluator import ReplayEvaluator
from .objectives import Eq1Objective
from .policy import code_of, parallel_refine, rules
from .tree import ROOT_ID, DiscoveryNode, DiscoveryTree, cell_id

#: the demo's recorded search (branches A, B, C x attempts 1..5)
DEMO_BRANCHES = {
    "A": [0.40, 0.52, 0.61, 0.63, 0.64],
    "B": [0.35, 0.37, 0.36, 0.38, 0.37],
    "C": [0.30, 0.45, 0.58, 0.70, 0.71],
}
DEMO_W = 3
DEMO_EXPECTED = {"all": 0.575, "focus": 0.611, "stop": 0.475}
DEMO_ROUNDS = {  # the demo's hard-coded reveal schedules, [branch, attempt] per round
    "all": [[[0, 0], [1, 0], [2, 0]], [[0, 1], [1, 1], [2, 1]], [[0, 2], [1, 2], [2, 2]], [[0, 3], [1, 3], [2, 3]],
            [[0, 4], [1, 4], [2, 4]]],
    "focus": [[[0, 0], [1, 0], [2, 0]], [[0, 1], [1, 1], [2, 1]], [[0, 2], [2, 2]], [[0, 3], [2, 3]], [[2, 4]]],
    "stop": [[[0, 0], [1, 0], [2, 0]], [[0, 1], [1, 1], [2, 1]]],
}
LABELS = {"all": "Refine everything", "focus": "Drop the flat branch", "stop": "Stop early"}


def demo_tree() -> DiscoveryTree:
    """Root (score 0) + three chains, created in round-robin order as the fixed strategy would."""
    root = DiscoveryNode(ROOT_ID, None, score=0.0)
    nodes, seq = [], 0
    names = list(DEMO_BRANCHES)
    for a in range(5):
        for b, name in enumerate(names):
            seq += 1
            nodes.append(DiscoveryNode(cell_id(b, a), ROOT_ID if a == 0 else cell_id(b, a - 1), b, a, seq,
                                       DEMO_BRANCHES[name][a], round=a + 1))
    return DiscoveryTree(root, nodes, branch_tags={b: {"name": n} for b, n in enumerate(names)}, world_id="demo")


def demo_policies() -> dict[str, str]:
    """The three strategies as policy code."""
    return {
        "all": code_of(parallel_refine()),
        "focus": code_of(rules(open=-1, gain_stop=0.025)),   # "stop once its gains shrink to a couple of points"
        "stop": code_of(rules(open=-1, max_rounds=2)),        # "refine each once, then stop the whole search"
    }


@dataclass
class DemoRow:
    key: str
    label: str
    best: float
    attempts: int
    rounds: int
    score: float
    reveal_round: dict

    def matches_schedule(self) -> bool:
        want = {cell_id(b, a): r + 1 for r, cells in enumerate(DEMO_ROUNDS[self.key]) for b, a in cells}
        return want == self.reveal_round


def run_demo(beta1: float = 0.010, beta2: float = 0.005, runner="inprocess") -> list[DemoRow]:
    tree = demo_tree()
    obj = Eq1Objective(beta1=beta1, beta2=beta2, normalize=False)
    ev = ReplayEvaluator(obj, W=DEMO_W, fallback=(3, 4), runner=runner)
    rows = []
    for key, code in demo_policies().items():
        rep = ev.evaluate(code, [tree], label=key)
        e = rep.episodes[0]
        rows.append(DemoRow(key, LABELS[key], float(e.best), e.N, e.k, float(rep.value), dict(e.reveal_round)))
    return rows


def render_demo(rows: list[DemoRow], beta1: float = 0.010, beta2: float = 0.005) -> str:
    tree = demo_tree()
    out = [f"Replay demo: 3 branches x 5 attempts, W={DEMO_W}, beta1={beta1:.3f}, beta2={beta2:.3f}", ""]
    out.append(f"{'Strategy':<22}{'Best':>6}{'Attempts':>10}{'Rounds':>8}{'Score':>8}")
    for r in rows:
        out.append(f"{r.label:<22}{r.best:>6.2f}{r.attempts:>10d}{r.rounds:>8d}{r.score:>8.3f}")
    win = max(rows, key=lambda r: r.score)
    out.append(f"\nWith these settings, “{win.label}” scores highest.")
    for r in rows:
        out.append(f"\n{r.label}: reveal-round map (R = round the cell was revealed, -- = never visited)")
        out.append(tree.render_grid(r.reveal_round, labels={0: "A", 1: "B", 2: "C"}))
    return "\n".join(out)
