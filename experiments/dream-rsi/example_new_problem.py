"""Apply Dream-RSI to a NEW problem in ~40 lines (the pattern to copy).

Problem: a program ``approx.py`` defines ``coeffs()`` - polynomial coefficients that
should approximate exp(x) on [0, 1]; the locked grader scores 1 / (1 + 1000 * max error)
on a fixed grid. Any rsi.core Domain works the same way (here a FunctionDomain).

Offline the discovery agent is a tiny ParametricAgent (perturb one coefficient per
attempt, the branch direction says which); with ``--llm claude:haiku`` Claude rewrites
approx.py (EditorAgent, Listing-1 prompt) and also acts as policy developer.

    python experiments/dream-rsi/example_new_problem.py [--llm sim|claude:haiku]
"""
import ast

import numpy as np
from _common import llm_of, parse_args

from rsi.core import Artifact, FunctionDomain, Task, TaskSuite
from rsi.dream import Config, EditorAgent, LLMPolicyDeveloper, ParametricAgent, run

GRID = np.linspace(0.0, 1.0, 201)


def execute(artifact, task, seed, llm):          # run the candidate program
    ns = {}
    exec(artifact["approx.py"], ns)               # (use rsi.core.sandbox.call_function for untrusted code)
    return [float(c) for c in ns["coeffs"]()]


def grade(task, coeffs):                          # the locked grader
    err = np.max(np.abs(np.polyval(coeffs[::-1], GRID) - np.exp(GRID)))
    return 1.0 / (1.0 + 1000.0 * err), f"max error {err:.2e}"


def mutate(parent, rng, ctx):                      # offline stand-in for a coding agent
    src = parent["approx.py"]
    c = ast.literal_eval(src.split("C = ", 1)[1].split("\n", 1)[0])
    k = int(ctx.direction.get("direction", "c0")[1:]) if ctx and ctx.direction else rng.randrange(len(c))
    c[k] += rng.gauss(0.0, 0.05 / (1 + k))
    return Artifact({"approx.py": f"C = {c!r}\n\ndef coeffs():\n    return C\n"}), f"# nudge coefficient {k}"


def main():
    a = parse_args("Dream-RSI on a new problem", default_seeds=1)
    domain = FunctionDomain(TaskSuite([Task("exp01", None)], {"evolve": ["exp01"]}), execute, grade,
                            name="poly-exp", description="Write approx.py whose coeffs() returns polynomial "
                                                         "coefficients (c0 + c1 x + ...) approximating exp(x) on [0, 1].")
    seed = Artifact({"approx.py": "C = [1.0, 1.0, 0.5, 0.0]\n\ndef coeffs():\n    return C\n"})
    llm = llm_of(a.llm)
    from rsi.dream import DomainTask
    task = DomainTask(domain, seed, directions=["c0", "c1", "c2", "c3"])   # direction = which coefficient to work on
    agent = EditorAgent(llm, editable=["approx.py"]) if llm else ParametricAgent(mutate)
    dev = LLMPolicyDeveloper(llm) if llm else None                        # None -> offline ParametricMutator
    res = run(task, config=Config(rounds=3 if llm else 6, W=4, branch_count=4, refine_count=3, M=4,
                                  sandbox="subprocess"), agent=agent, developer=dev)
    print("seed score", round(res.meta["seed_score"], 4), "-> best", round(res.meta["best_score"], 4))
    print("calls per live search:", [r["calls"] for r in res.trajectory])
    print(res.best["approx.py"])
    print("deployed exploration policy (first lines):\n" + "\n".join(res.meta["policy"].splitlines()[:6]))


if __name__ == "__main__":
    main()
