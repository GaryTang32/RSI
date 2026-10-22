"""Worked example: GEPA's quick-start ``optimize()`` on a new problem, a one-prompt customer-record formatter.

Run: python experiments/gepa/example_new_problem.py [--llm sim|claude:haiku] [--task-llm sim|claude:haiku]
"""
from __future__ import annotations

import random

from _common import CACHE, parse_args, reflection_llm, save
from rsi.core import CachedLLM, ClaudeCLI
from rsi.gepa import Config, optimize

FIRST = ["ada", "alan", "grace", "edsger", "barbara", "donald", "margaret", "john", "frances", "ken"]
LAST = ["lovelace", "turing", "hopper", "dijkstra", "liskov", "knuth", "hamilton", "mccarthy", "allen", "thompson"]
CUR = ["gbp", "usd", "eur", "chf"]
CONVENTIONS = {  # field -> (keyword the simulated formatter looks for, plain-language rule used as feedback)
    "name": ("title case", "Names must be written in Title Case"),
    "date": ("yyyy-mm-dd", "Dates must be written as YYYY-MM-DD"),
    "amount": ("two decimals", "Amounts must use a thousands separator and two decimals"),
    "currency": ("uppercase currency", "Put the uppercase currency code before the amount"),
}
RULES = {"name": CONVENTIONS["name"][1], "date": CONVENTIONS["date"][1],
         "money": f"{CONVENTIONS['currency'][1]} and {CONVENTIONS['amount'][1]}"}


def make_records(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        d, m, y = rng.randint(1, 28), rng.randint(1, 12), rng.randint(1900, 2025)
        out.append({"name": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "date": f"{d:02d}/{m:02d}/{y}",
                    "amount": round(rng.uniform(10, 99999), 2), "currency": rng.choice(CUR)})
    return out


def gold(r: dict) -> dict:
    d, m, y = r["date"].split("/")
    return {"name": r["name"].title(), "date": f"{y}-{m}-{d}", "money": f"{r['currency'].upper()} {r['amount']:,.2f}"}


def simulated_formatter(prompt: str, r: dict) -> dict:
    """Applies exactly the conventions whose keyword the prompt mentions."""
    p = prompt.lower()
    has = lambda field: CONVENTIONS[field][0] in p
    d, m, y = r["date"].split("/")
    amount = f"{r['amount']:,.2f}" if has("amount") else str(r["amount"])
    cur = r["currency"].upper() if has("currency") else r["currency"]
    return {"name": r["name"].title() if has("name") else r["name"],
            "date": f"{y}-{m}-{d}" if has("date") else r["date"],
            "money": f"{cur} {amount}" if has("currency") else f"{amount} {cur}"}


def llm_formatter(llm):
    def fmt(prompt: str, r: dict) -> dict:
        resp = llm.complete(f"{prompt}\n\nRecord: {r}\nReply with exactly three lines: name, date, money.",
                            seed=0, role="task")
        lines = [line.strip() for line in resp.text.strip().splitlines() if line.strip()] + ["", "", ""]
        return {"name": lines[0], "date": lines[1], "money": lines[2]}
    return fmt


def make_metric(formatter):
    def metric(candidate: dict, record: dict):
        out = formatter(candidate["formatter.md"], record)
        g = gold(record)
        wrong = [k for k in g if out.get(k, "").strip() != g[k]]
        fb = " ".join(f"{RULES[k]}. (field {k}: got {out.get(k)!r}, expected {g[k]!r})" for k in wrong) \
            or "All fields correct."
        return 1.0 - len(wrong) / len(g), fb, " | ".join(f"{k}={out.get(k)}" for k in g)
    return metric


def main():
    a = parse_args("GEPA on a new problem via optimize()", default_seeds=1,
                   extra=lambda ap: ap.add_argument("--task-llm", default="sim"))
    formatter = simulated_formatter if a.task_llm == "sim" else llm_formatter(
        CachedLLM(ClaudeCLI(a.task_llm.partition(":")[2] or "haiku"), CACHE))
    data = make_records(40, seed=0)
    res = optimize({"formatter.md": "Format the customer record as three lines: name, date, money."},
                   trainset=data[:10], valset=data[10:20], testset=data[20:],
                   metric=make_metric(formatter), llm_propose=reflection_llm(a.llm, kind="generic"),
                   config=Config(max_metric_calls=150 if a.live else 300))
    rep = res.meta["report"]["splits"]["test"]
    out = {"experiment": "example: new problem via optimize()", "llm": a.llm, "task_llm": a.task_llm,
           "seed_val": res.meta["seed_val"], "best_val": res.meta["best_val"],
           "test": {k: v["S"] for k, v in rep.items()}, "rollouts": res.meta["rollouts"],
           "best_prompt": res.best["formatter.md"], "usage": res.usage}
    out["verdict"] = f"sealed test: seed {out['test']['seed']:.2f} -> best {out['test']['best']:.2f}"
    save("example_new_problem", out, a.out)
    print(res.best["formatter.md"])
    print(out["verdict"])


if __name__ == "__main__":
    main()
