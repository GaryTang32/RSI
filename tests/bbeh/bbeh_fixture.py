"""A tiny synthetic BBEH: 20 examples per task in the upstream layout, with BBEH-style targets, and its manifest."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from rsi.domains.bbeh.data import TASKS

N_PER_TASK = 20
NAMES = ["emma", "lily", "noah", "liam", "olivia", "ava", "mia", "zoe"]


def target(task: str, i: int, rng: random.Random) -> str:
    kind = i % 9
    if kind == 0:
        return f"({rng.choice('ABCDEFG')})"
    if kind == 1:
        return rng.choice(["Yes", "No"])
    if kind == 2:
        return str(rng.randint(-500, 2000))
    if kind == 3:
        return ", ".join(rng.choice(["yes", "no"]) for _ in range(3))
    if kind == 4:
        return "[" + ", ".join(rng.sample(NAMES, 2)) + "]"
    if kind == 5:
        return f"{rng.randint(1, 300)}, {rng.randint(1, 9)}"
    if kind == 6:
        return f"{rng.randint(1, 900)}.{rng.choice([0, 25, 5, 75])}"
    if kind == 7:
        return "He " + rng.choice(["really", "never", "often"]) + " cried."
    return ", ".join(rng.sample(NAMES, 4))


def example(task: str, i: int) -> dict:
    rng = random.Random(f"bbeh:{task}:{i}")
    body = " ".join(rng.choice(NAMES) for _ in range(30))
    return {"input": f"Synthetic BBEH {task} problem {i}. Read the story: {body}. Give the final answer.",
            "target": target(task, i, rng)}


def build(root: Path) -> dict:
    files = {}
    for t in TASKS:
        doc = {"canary": "synthetic", "examples": [example(t, i) for i in range(N_PER_TASK)]}
        rel = f"bbeh/benchmark_tasks/bbeh_{t}/task.json"
        data = json.dumps(doc).encode()
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        files[rel] = hashlib.sha256(data).hexdigest()
    return {"commit": "e" * 40, "url_template": "https://bbeh.invalid/{commit}/{path}", "files": files}
