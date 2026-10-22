"""The overview's replay demo (3 branches x 5 attempts, W = 3) on the real replay machinery (spec §9.1 g)."""
from __future__ import annotations

from dataclasses import dataclass

from .evaluator import ReplayEvaluator
from .objectives import Eq1Objective
from .policy import code_of, parallel_refine, rules
from .tree import ROOT_ID, DiscoveryNode, DiscoveryTree, cell_id

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
    """Root (score 0) + three chains, created in round-robin order."""
    names = list(DEMO_BRANCHES)
    nodes = [DiscoveryNode(cell_id(b, a), ROOT_ID if a == 0 else cell_id(b, a - 1), b, a, a * len(names) + b + 1,
                           DEMO_BRANCHES[name][a], round=a + 1) for a in range(5) for b, name in enumerate(names)]
    return DiscoveryTree(DiscoveryNode(ROOT_ID, None, score=0.0), nodes,
                         branch_tags={b: {"name": n} for b, n in enumerate(names)}, world_id="demo")


def demo_policies() -> dict[str, str]:
    return {
        "all": code_of(parallel_refine()),
        "focus": code_of(rules(open=-1, gain_stop=0.025)),  # stop a branch once its gains shrink
        "stop": code_of(rules(open=-1, max_rounds=2)),  # refine each once, then stop
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
    ev = ReplayEvaluator(Eq1Objective(beta1=beta1, beta2=beta2, normalize=False), W=DEMO_W, fallback=(3, 4),
                         runner=runner)
    rows = []
    for key, code in demo_policies().items():
        rep = ev.evaluate(code, [tree], label=key)
        e = rep.episodes[0]
        rows.append(DemoRow(key, LABELS[key], float(e.best), e.N, e.k, float(rep.value), dict(e.reveal_round)))
    return rows


def render_demo(rows: list[DemoRow], beta1: float = 0.010, beta2: float = 0.005) -> str:
    tree = demo_tree()
    out = [f"Replay demo: 3 branches x 5 attempts, W={DEMO_W}, beta1={beta1:.3f}, beta2={beta2:.3f}", "",
           f"{'Strategy':<22}{'Best':>6}{'Attempts':>10}{'Rounds':>8}{'Score':>8}"]
    out += [f"{r.label:<22}{r.best:>6.2f}{r.attempts:>10d}{r.rounds:>8d}{r.score:>8.3f}" for r in rows]
    out.append(f"\nWith these settings, “{max(rows, key=lambda r: r.score).label}” scores highest.")
    for r in rows:
        out.append(f"\n{r.label}: reveal-round map (R = round the cell was revealed, -- = never visited)")
        out.append(tree.render_grid(r.reveal_round, labels={0: "A", 1: "B", 2: "C"}))
    return "\n".join(out)
