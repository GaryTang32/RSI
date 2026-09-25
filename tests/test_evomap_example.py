"""The copy-pasteable 'apply EvoMap to a new problem' example from docs/methods/evomap-impl.md."""


def test_new_problem_example(tmp_path):
    import json
    from rsi.core import Artifact, FunctionDomain, MockLLM, Task, TaskSuite
    from rsi.evomap import Config, SafeHub, TaskBank, evaluate_library, run

    # 1. A new problem: format invoice amounts. Tasks carry public signals; targets stay with the grader.
    tasks = [Task(f"t{i}", {"amount": i * 1.5}, f"EUR {i * 1.5:.2f}", "money", {"signals": ["money", "format"]})
             for i in range(1, 61)]
    suite = TaskSuite(tasks, {"evolve": [f"t{i}" for i in range(1, 21)], "val": [f"t{i}" for i in range(21, 31)],
                              "holdout": [f"t{i}" for i in range(31, 46)], "test": [f"t{i}" for i in range(46, 61)]})

    def execute(artifact, task, seed, llm):          # the frozen harness: prompt = every artifact file + the task
        system = "\n".join(artifact[p] for p in sorted(artifact))
        return llm.complete(json.dumps(task.input), system=system, seed=seed, role="task").text

    dom = FunctionDomain(suite, execute, lambda task, out: float(out.strip() == task.target), name="invoices")

    # 2. A frozen model (mock): prints two decimals only when its guidance says so.
    def model_fn(prompt, system, seed, i):
        amt = json.loads(prompt)["amount"]
        return f"EUR {amt:.2f}" if "two decimals" in (system or "") else f"EUR {amt}"
    model = MockLLM(model_fn, name="invoice-model")

    # 3. A gene writer (use CachedLLM(ClaudeCLI("haiku"), ...) live). rsi-taskcheck = discriminative A/B check.
    gene = {"id": "gene_two_decimals", "category": "repair", "signals_match": ["money"],
            "summary": "Money amounts must be printed with two decimals.",
            "strategy": ["Read the amount.", "Format it with exactly two decimals.", "Prefix the currency code."],
            "avoid": ["Printing the raw float."], "validation": ["rsi-taskcheck --n 4"]}
    proposer = MockLLM(lambda p, s, seed, i: "```json\n" + json.dumps(gene) + "\n```", name="gene-writer")

    harness = Artifact({"prompt.md": "Format the invoice amount.\n"})
    res = run(dom, harness, llm_task=model, llm_propose=proposer, config=Config(cycles=8, mode="safe"),
              out_dir=tmp_path / "invoices")
    print(res.best["genes/library.json"][:200])
    rep = evaluate_library(dom, model, res, splits=("holdout",), workers=1)
    assert rep["splits"]["holdout"]["no_genes"]["S"] < 0.2
    assert rep["splits"]["holdout"]["gene_library"]["S"] == 1.0

    # 4. Optional: share it through a verified hub (hidden bank = the sealed 'test' split).
    hub = SafeHub(TaskBank(dom, harness, model, split="test", n=12, n_off=0, k=2))
    res2 = run(dom, harness, llm_task=model, llm_propose=proposer, config=Config(cycles=8, mode="safe"), hub=hub,
               name="alice")
    assert res2.meta["n_genes"] == 1
    rec = hub.published()[0]
    assert rec.status == "verified" and rec.hub_report["U_LCB"] >= rec.hub_report["delta"]   # strategy gene: uplift
