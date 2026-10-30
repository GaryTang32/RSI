"""A tiny synthetic BBH: 32 examples per task in the upstream file format, CoT prompts, and a matching manifest."""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from rsi.domains.bbh.data import TASKS

N_PER_TASK = 32
KIND = {"boolean_expressions": "tf", "causal_judgement": "YN", "navigate": "YN", "web_of_lies": "YN",
        "sports_understanding": "yn", "formal_fallacies": "valid", "multistep_arithmetic_two": "num",
        "object_counting": "num", "dyck_languages": "dyck", "word_sorting": "words"}
WORDS = ["oven", "costume", "counterpart", "hypochlorite", "ponderosa", "phone", "credulity", "arson", "aspect",
         "sioux", "fortescue", "purloin", "percept", "helmsman", "gala", "knob", "aqueous", "deregulate"]
PAIRS = {"(": ")", "[": "]", "{": "}", "<": ">"}
DYCK_LEAD = "Complete the rest of the sequence, making sure that the parentheses are closed properly. Input: "


def example(task: str, i: int) -> dict:
    rng = random.Random(f"{task}:{i}")
    kind = KIND.get(task, "choice")
    if kind == "choice":
        letters = "ABCDE"
        opts = [f"({L}) {task.split('_')[0]} option {L.lower()}{i}" for L in letters]
        return {"input": f"Synthetic {task} question {i}: which option fits clue {rng.randint(10**5, 10**6)}?\n"
                         "Options:\n" + "\n".join(opts), "target": f"({rng.choice(letters)})"}
    if kind in ("tf", "YN", "yn", "valid"):
        a, b = {"tf": ("True", "False"), "YN": ("Yes", "No"), "yn": ("yes", "no"), "valid": ("valid", "invalid")}[kind]
        return {"input": f"Synthetic {task} statement {i} with token {rng.randint(10**5, 10**6)}. Is it so?",
                "target": rng.choice([a, b])}
    if kind == "num":
        x, y = rng.randint(-50, 50), rng.randint(-50, 50)
        return {"input": f"(({x} + {y}) * {i + 2} - {i}) = ", "target": str((x + y) * (i + 2) - i)}
    if kind == "dyck":
        opens = [list(PAIRS)[(i >> (2 * j)) & 3] for j in range(5)] + rng.sample(list(PAIRS), rng.randint(0, 2))
        return {"input": DYCK_LEAD + " ".join(opens), "target": " ".join(PAIRS[o] for o in reversed(opens))}
    words = rng.sample(WORDS, rng.randint(3, 6)) + [f"zz{i}x"]
    rng.shuffle(words)
    return {"input": "Sort the following words alphabetically: List: " + " ".join(words),
            "target": " ".join(sorted(words))}


def cot_text(task: str) -> str:
    ex = example(task, 999)
    return (f"SYNTHETIC CANARY {task}\n-----\nSynthetic description of {task}.\n\nQ: {ex['input']}\n"
            f"A: Let's think step by step. So the answer is {ex['target']}.\n")


def build(root: Path) -> dict:
    """Write ``<root>/bbh/*.json`` and ``<root>/cot-prompts/*.txt``; return their manifest."""
    files = {}
    for t in TASKS:
        doc = {"canary": "synthetic", "examples": [example(t, i) for i in range(N_PER_TASK)]}
        for rel, data in ((f"bbh/{t}.json", json.dumps(doc).encode()),
                          (f"cot-prompts/{t}.txt", cot_text(t).encode())):
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            files[rel] = hashlib.sha256(data).hexdigest()
    return {"commit": "f" * 40, "url_template": "https://bbh.invalid/{commit}/{path}", "files": files}
