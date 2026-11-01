"""Improve a classification prompt on your own labelled CSV with GEPA (support-ticket intents).

Offline (default, ~5 s, no API key):  python examples/01_prompt_from_csv/improve_prompt.py
Live (claude CLI, uses your plan):    python examples/01_prompt_from_csv/improve_prompt.py --llm claude:haiku
Your data: --csv my.csv (columns text,label); edit prompt.md and LABEL_GUIDE below to match your labels.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

if importlib.util.find_spec("rsi") is None:  # not pip-installed: use the clone this example lives in
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import rsi
from rsi.core import (
    Artifact,
    Evaluator,
    Execution,
    FunctionDomain,
    MockLLM,
    SealedSplitError,
    Task,
    TaskSuite,
    noise_from_trials,
    usd_of,
)
from rsi.gepa import GenericReflectionLM

HERE = Path(__file__).resolve().parent
OFFLINE_NOTE = "offline simulated model: numbers show the loop working, not real gains"
# The grader's annotation guide: one sentence per label, returned as feedback when a ticket is misclassified.
LABEL_GUIDE = {
    "billing": "Tickets about a refund, charge, invoice, payment or subscription price are billing.",
    "technical": "Tickets about a crash, error, bug, freeze or slowness are technical.",
    "account": "Tickets about a password, login, username or profile are account.",
    "shipping": "Tickets about a delivery, parcel, tracking number, courier or package are shipping.",
}


def offline_classifier(prompt: str, system, seed, i) -> str:
    """OFFLINE STAND-IN FOR A REAL MODEL (written for this example; no LLM involved).

    It follows only the rules written in the prompt ("Tickets about A, B or C are <label>."), falls back to
    'technical', and answers at random 20% of the time (seeded), so the noise band is non-zero."""
    ticket = prompt.rsplit("Ticket:", 1)[-1].lower()
    h = int(hashlib.sha256(f"{seed}|{ticket}".encode()).hexdigest()[:8], 16)
    if h % 100 < 20:
        return sorted(LABEL_GUIDE)[h % len(LABEL_GUIDE)]
    hits = {}
    for words, label in re.findall(r"about (.+?) are (\w+)\.", prompt.rsplit("Ticket:", 1)[0]):
        stems = [w[:5] for w in re.findall(r"[a-z]+", words.lower()) if w not in ("a", "an", "the", "or", "and")]
        hits[label] = sum(any(t.startswith(s) for s in stems) for t in re.findall(r"[a-z]+", ticket))
    best = max(hits, key=hits.get, default=None)
    return best if best and hits[best] > 0 else "technical"


def load_suite(path: Path) -> TaskSuite:
    rows = list(csv.DictReader(path.read_text().splitlines()))
    tasks = [Task(f"t{i:02d}", r["text"], r["label"].strip()) for i, r in enumerate(rows)]
    if unknown := {t.target for t in tasks} - set(LABEL_GUIDE):
        raise SystemExit(f"labels {sorted(unknown)} have no LABEL_GUIDE entry: add one sentence for each")
    # evolve: the loop learns from it; val: GEPA's Pareto/selection set; holdout: SEALED, read only at the end
    return TaskSuite.from_tasks(tasks, fractions={"evolve": 0.4, "val": 0.27, "holdout": 0.33}, seed=0)


def execute(artifact: Artifact, task: Task, seed: int, llm) -> Execution:
    """Run the prompt on one ticket; returning an Execution (not just the label) records tokens and the reply."""
    r = llm.complete(f"{artifact['prompt.md'].strip()}\n\nTicket: {task.input}", seed=seed, role="task")
    label = next((w for w in re.findall(r"[a-z]+", r.text.lower()) if w in LABEL_GUIDE), "?")
    return Execution(output=label, trace=r.text, tokens=r.usage.total_tokens, cost_usd=r.usage.cost_usd)


def grade(task: Task, label: str) -> tuple[float, str]:
    if label == task.target:
        return 1.0, "correct"
    return 0.0, f"{LABEL_GUIDE[task.target]} (expected {task.target!r}, got {label!r})"


def main(argv: list[str] | None = None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--llm", default="offline", help="offline (simulated) | claude:haiku | api:sonnet | ...")
    ap.add_argument("--out", default=None, help="run directory (default: a fresh temp dir)")
    ap.add_argument("--csv", default=str(HERE / "tickets.csv"), help="labelled examples: columns text,label")
    ap.add_argument("--budget", type=int, default=150, help="GEPA max_metric_calls (task rollouts)")
    a = ap.parse_args(argv)
    out = Path(a.out or tempfile.mkdtemp(prefix="rsi-ex01-"))
    offline = a.llm == "offline"

    # 1. Artifact (the prompt file), tasks (the CSV, split 3 ways) and a locked grader -> a FunctionDomain.
    suite = load_suite(Path(a.csv))
    dom = FunctionDomain(suite, execute, grade, name="ticket-intent", description="Classify support tickets.")
    seed = Artifact({"prompt.md": (HERE / "prompt.md").read_text()})
    print("splits:", {k: v["n"] for k, v in suite.summary().items()})

    if offline:  # task model: the stand-in above; proposer: GEPA's generic reflection mock (feedback -> "Lesson:")
        llm_task, llm_propose = MockLLM(offline_classifier, name="offline-classifier"), GenericReflectionLM()
    else:  # ---- LIVE: real model calls, cached so a rerun replays for free ----
        llm_task = llm_propose = rsi.get_llm(a.llm, cache_dir=str(Path(".rsi_cache") / "example-01"))

    # 2. Measure before improving: k=3 trials of the seed on evolve -> the noise band delta.
    ev = Evaluator(dom, llm_task, workers=4)
    base = ev.evaluate(seed, "evolve", k=3)
    delta = noise_from_trials(base.trial_matrix(), small_k_correction=True).delta
    print(f"seed on evolve: {base.score:.3f}   noise band delta: {delta:.3f}")
    try:
        ev.evaluate(seed, "holdout")
    except SealedSplitError:
        print("holdout is sealed: the loop's evaluator refuses it")

    # 3. Run GEPA; a child prompt is kept only if it beats its parent's minibatch mean by more than delta.
    cfg = {"max_metric_calls": a.budget, "acceptance": "noise_margin", "noise_margin": delta}
    res = rsi.improve(dom, seed, method="gepa", llm_task=llm_task, llm_propose=llm_propose, config=cfg, out_dir=out)

    # 4. Report: seed vs best on every split, paired 95% CI over tasks. The loop chose the best prompt using
    #    evolve and val, so only the sealed holdout (unsealed here, after the run) is an unbiased estimate.
    rep = rsi.transfer_report(dom, llm_task, {"seed": res.baseline, "gepa": res.best}, splits=tuple(suite.splits), k=2)
    print(f"\n{'split':8s} {'seed':>6s} {'gepa':>6s}  paired diff [95% CI]   tokens/task seed -> gepa")
    for split, row in rep["splits"].items():
        d = row["gepa"]["vs_reference"]
        print(
            f"{split:8s} {row['seed']['S']:6.3f} {row['gepa']['S']:6.3f}  "
            f"{d['mean_diff']:+.3f} [{d['lo']:+.3f}, {d['hi']:+.3f}]   {row['seed']['C']:.0f} -> {row['gepa']['C']:.0f}"
        )
    n = res.meta["n_candidates"]
    print(f"\nbest prompt (best on val of {n} candidates, {res.meta['rollouts']} rollouts):")
    print("  " + res.best["prompt.md"].strip().replace("\n", "\n  "))
    hold = rep["splits"]["holdout"]
    usd = usd_of(llm_task) + (usd_of(llm_propose) if llm_propose is not llm_task else 0.0)  # every call, all roles
    print(f"\nfiles: {out / 'best_artifact'}   audit trail: python -m rsi.cli inspect {out}")
    print(
        f"\nSUMMARY gepa on {Path(a.csv).name}: holdout {hold['seed']['S']:.3f} -> {hold['gepa']['S']:.3f}, "
        f"delta {delta:.3f}, spend ${usd:.2f}"
    )
    if offline:
        print(f"NOTE {OFFLINE_NOTE} (the classifier is a keyword stand-in defined in this script)")
    return {"report": rep, "best_prompt": res.best["prompt.md"], "delta": delta, "out": str(out)}


if __name__ == "__main__":
    main()
