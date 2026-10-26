"""Harness² hyperparameters, defaulting to the reference code (``--mode parallel --k 1``)."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

MODES = ("parallel", "sequential")
ORDERS = ("sorted", "split", "shuffle")
#: prompt-pack slots interpolated into the improver prompts [code spec.BenchPrompts]
SLOTS = ("work", "domain_word", "deliverable_word", "inv_levers", "dom_levers", "yardstick", "domain_guidance",
         "selection_checks", "selection_lead")
#: slot values must describe the work, never its measurement [code spec.BenchPrompts.__post_init__]
FORBIDDEN = re.compile(r"benchmark|evaluation|grader|rubric|score", re.I)


@dataclass
class Config:
    mode: str = "parallel"                  # parallel ("agg") | sequential ("seq")
    k: int = 1                              # budget: 4k rollouts and 3k editor/composer sessions per task
    split: Optional[str] = None             # None -> the sealed evaluation split: holdout, else test, else ood;
                                            # "a+b" streams several splits
    max_tasks: Optional[int] = None         # first N tasks of the ordered stream
    max_usd: Optional[float] = None         # spend cap, checked between tasks (stops early when the next task
                                            # would likely cross it at the mean cost per task so far)
    order: str = "sorted"                   # sorted | split (as listed) | shuffle (seeded, per domain)
    retry_invalid: int = 1                  # extra attempts per stage after an invalid proposal (STAGE_ATTEMPTS = 2)
    selector_attempts: int = 2              # transport attempts of the selector before its fallback
    seed: int = 0
    library: bool = True                    # False = the paper's "w/o library" ablation (neither read nor written)
    library_dir: Optional[str] = None       # None -> <out_dir>/library (or a temp dir), emptied at the start
    editable: Optional[list[str]] = None    # editable globs; None -> the seed's files + domain.components
    strip_targets: bool = True              # rollouts get the task without target and answer-like meta keys
    obs_trunc: int = 50000                  # per-trajectory render cap, with an explicit renderer marker
    max_text_chars: int = 24000             # cap on non-code harness text (the always-on cap of OpenCode)
    prompts: dict = field(default_factory=dict)   # prompt-pack slots (SLOTS); never about measurement
    report: bool = True                     # grade base vs submitted per task after the loop (posthoc_report)
    trace: bool = True                      # <out_dir>/trace.jsonl (write-only audit)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Config":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def validate(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if self.order not in ORDERS:
            raise ValueError(f"order must be one of {ORDERS}")
        if self.k < 1 or self.retry_invalid < 0 or self.selector_attempts < 1 or (self.max_tasks or 0) < 0:
            raise ValueError("need k >= 1, retry_invalid >= 0, selector_attempts >= 1 and max_tasks >= 0")
        if unknown := sorted(set(self.prompts) - set(SLOTS)):
            raise ValueError(f"unknown prompt slots {unknown}; valid: {SLOTS}")
        for name, text in self.prompts.items():
            if FORBIDDEN.search(str(text)):
                raise ValueError(f"prompt slot {name!r} must describe the work, not its measurement "
                                 f"(no benchmark/evaluation/grader/rubric/score): {str(text)[:120]!r}")
