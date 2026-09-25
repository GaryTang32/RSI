"""Applying autoresearch to a NEW problem (copy-paste template, runs offline in seconds).

The "user problem": fit a noisy 1-D function with a polynomial ridge regressor. The
user owns a locked ``evaluate.py`` (data, the held-out score, and the entry point that
imports the agent's code and calls ``report``) and an editable ``train.py`` (degree,
regularisation, feature scaling; it must expose ``predict(x)``). We wrap them in a
ScriptResearchTask and run the loop with the scripted agent - swap in
``llm_propose=get_llm("claude:haiku", cache_dir=...)`` for a live research agent.

Hardened-mode rules of thumb for your own problem: the locked file is the entry point
and the only caller of ``report`` (so editable code cannot report a number itself), and
``tamper_patterns`` names what the editable code must never touch (here the validation
arrays and ``report``; the framework adds its own patterns for the record file and for
monkeypatching the locked module).

Usage: python experiments/autoresearch/example_new_problem.py [--llm sim|claude:haiku] [--quick]
"""
from __future__ import annotations

from _common import suffix, SCRATCH, parser, propose_llm, write  # noqa: I001

from rsi.autoresearch import Config, MockResearchAgent, RunBudget, ScriptResearchTask, knob_edit, run
from rsi.autoresearch.task import RECORD_HELPER

EVALUATE_PY = RECORD_HELPER + '''
import numpy as np

rng = np.random.default_rng(0)
x = np.sort(rng.uniform(-3, 3, 400))
y = np.sin(1.5 * x) + 0.3 * x + rng.normal(0, 0.3, 400)
X_TRAIN, Y_TRAIN, X_VAL, Y_VAL = x[::2], y[::2], x[1::2], y[1::2]


def score(predict):
    """Locked metric: validation R^2 of predict(x_val) (higher is better)."""
    pred = np.asarray(predict(X_VAL), float)
    return float(1 - np.mean((pred - Y_VAL) ** 2) / np.var(Y_VAL))


if __name__ == "__main__":                 # the framework runs `python evaluate.py`
    import train                           # the agent's code: fits at import, exposes predict(x)
    report(r2=score(train.predict))
'''

TRAIN_PY = '''import numpy as np
from evaluate import X_TRAIN, Y_TRAIN

DEGREE = 1
RIDGE = 1.0
SCALE = 1.0


def features(x):
    z = np.asarray(x, float) / SCALE
    return np.vstack([z ** d for d in range(DEGREE + 1)]).T


F = features(X_TRAIN)
w = np.linalg.solve(F.T @ F + RIDGE * np.eye(F.shape[1]), F.T @ Y_TRAIN)


def predict(x):
    return features(x) @ w
'''


def make_task() -> ScriptResearchTask:
    return ScriptResearchTask(
        "poly-fit", {"evaluate.py": EVALUATE_PY, "train.py": TRAIN_PY}, metric="r2", direction="max",
        editable_paths=["train.py"], locked_paths=["evaluate.py"], run_cmd=["python", "evaluate.py"],
        budget=RunBudget(kind="ceiling", amount=10.0, kill_after=20.0, mem_mb=1024),
        tamper_patterns=[r"\bX_VAL\b", r"\bY_VAL\b", r"\breport\b"],
        description="Fit a noisy 1-D curve; train.py sets polynomial features and ridge regularisation; "
                    "the metric is validation R^2 (higher is better).")


def edit_pool():
    return [knob_edit("DEGREE", lambda v: v + 2, hi=15, name="deg_up"),
            knob_edit("DEGREE", lambda v: v - 2, lo=1, name="deg_down"),
            knob_edit("RIDGE", lambda v: v * 10, hi=1e4, name="ridge_up"),
            knob_edit("RIDGE", lambda v: v / 10, lo=1e-6, name="ridge_down"),
            knob_edit("SCALE", lambda v: v * 3, hi=100, name="scale_up"),
            knob_edit("SCALE", lambda v: v / 3, lo=0.01, name="scale_down")]


def main():
    a = parser(__doc__.splitlines()[0], seeds=1).parse_args()
    task = make_task()
    llm = propose_llm(a.llm, pool=edit_pool())
    agent = None if llm is not None else MockResearchAgent(edit_pool(), seed=0)
    res = run(task, llm_propose=llm, agent=agent,
              config=Config(max_experiments=6 if a.quick else 20, mode="hardened", overwrite=True),
              out_dir=SCRATCH / "example_new_problem")
    s = res.meta["analysis"]
    print(open(res.meta["results_tsv"]).read())
    write("example_new_problem" + suffix(a.llm, a.quick),
          {"analysis": {k: v for k, v in s.items() if k != "top_hits"}, "top_hits": s["top_hits"],
           "best_train_py": res.best["train.py"], "usage": res.usage.get("_total")})


if __name__ == "__main__":
    main()
