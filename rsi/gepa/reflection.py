"""Reflection: GEPA's meta-prompts (verbatim, paper App. C), record rendering, fenced parsing and the proposer."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from ..core.artifact import Artifact
from ..core.llm import LLM

DEFAULT_TEMPLATE = """I provided an assistant with the following instructions to perform a task for me:
```
<curr_param>
```

The following are examples of different task inputs provided to the assistant along with the assistant's response for each of them, and some feedback on how the assistant's response could be better:
```
<side_info>
```

Your task is to write a new instruction for the assistant.

Read the inputs carefully and identify the input format and infer detailed task description about the task I wish to solve with the assistant.

Read all the assistant responses and the corresponding feedback. Identify all niche and domain specific factual information about the task and include it in the instruction, as a lot of it may not be available to the assistant in the future. The assistant may have utilized a generalizable strategy to solve the task, if so, include that in the instruction as well.

Provide the new instructions within ``` blocks."""

OPTIMIZE_ANYTHING_TEMPLATE = """I am optimizing a parameter in my system. The current parameter value is:
```
<curr_param>
```

Below is evaluation data showing how this parameter value performed across multiple test cases. The data contains performance metrics, diagnostic information, and other relevant details from the evaluation:
```
<side_info>
```

Your task is to propose a new, improved parameter value that can be used as a drop-in replacement for the current one.

Carefully analyze all the evaluation data provided above. Look for patterns that indicate what works and what doesn't. Pay special attention to:
- Performance metrics and how they correlate with parameter behavior
- Recurring issues, errors, or failure patterns across multiple test cases
- Successful patterns or behaviors that should be preserved or enhanced
- Any domain-specific requirements, constraints, or factual information revealed in the evaluation data
- Specific technical details that are crucial for understanding the parameter's role

Based on your analysis, propose a new parameter value that addresses the identified issues while maintaining or improving upon what works well. Your proposal should be directly informed by the patterns and insights from the evaluation data.

Provide the new parameter value within ``` blocks."""

TEMPLATES = {"default": DEFAULT_TEMPLATE, "optimize_anything": OPTIMIZE_ANYTHING_TEMPLATE}


def validate_template(template: str) -> None:
    missing = [p for p in ("<curr_param>", "<side_info>") if p not in template]
    if missing:
        raise ValueError(f"Missing placeholder(s) in prompt template: {', '.join(missing)}")


def render_value(value: Any, level: int = 3) -> str:
    if not isinstance(value, (dict, list, tuple)):
        return f"{str(value).strip()}\n\n"
    pairs = value.items() if isinstance(value, dict) else ((f"Item {i + 1}", v) for i, v in enumerate(value))
    s = "".join(f"{'#' * level} {k}\n" + render_value(v, min(level + 1, 6)) for k, v in pairs)
    return s if value else s + "\n"


def render_samples(samples: Sequence[Mapping[str, Any]]) -> str:
    """Markdown rendering of reflective records (the reference ``format_samples``)."""
    return "\n\n".join(f"# Example {n}\n" + "".join(f"## {k}\n" + render_value(v, level=3) for k, v in s.items())
                       for n, s in enumerate(samples, 1))


def parse_side_info(side: str) -> list[dict]:
    """Records back from rendered samples (``# Example n`` / ``## key`` sections, values as text)."""
    recs = []
    for block in re.split(r"^# Example \d+\s*$", side, flags=re.M)[1:]:
        secs = (sec.partition("\n") for sec in re.split(r"^## ", block, flags=re.M)[1:])
        recs.append({head.strip(): body.strip() for head, _, body in secs})
    return recs


def build_prompt(template: str, current: str, records: Sequence[Mapping[str, Any]]) -> str:
    validate_template(template)
    return template.replace("<curr_param>", current).replace("<side_info>", render_samples(records))


def _has_fence_pair(out: str) -> bool:
    return (out.find("```") + 3) < out.rfind("```")


def is_known_truncated(out: str, finish_reason: Optional[str] = None, tags: Sequence[str] = ("think",)) -> bool:
    if _has_fence_pair(out):
        return False
    if finish_reason in {"length", "max_tokens"}:
        return True
    stripped = out.lstrip()
    return any(stripped.startswith(f"<{t}>") and out.count(f"<{t}>") > out.count(f"</{t}>") for t in tags)


def response_finish_reason(resp) -> Optional[str]:
    """The provider's stop reason: ``raw["stop_reason"]``, ``raw["finish_reason"]`` or ``resp.finish_reason``."""
    raw = getattr(resp, "raw", None)
    if isinstance(raw, dict):
        for key in ("stop_reason", "finish_reason"):
            v = raw.get(key)
            if isinstance(v, str):
                return v
    v = getattr(resp, "finish_reason", None)
    return v if isinstance(v, str) else None


def parse_fenced(out: str, finish_reason: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """``(text, error)`` with the reference ``ProposalAdapter.parse`` semantics."""
    out = (out or "").strip()
    if is_known_truncated(out, finish_reason):
        detail = f"finish_reason={finish_reason!r}" if finish_reason else "unterminated reasoning block"
        return None, f"reflection output is incomplete ({detail})"
    if _has_fence_pair(out):
        content = out[out.find("```") + 3: out.rfind("```")]
        m = re.match(r"^\S*\n", content)
        return (content[m.end():] if m else content).strip(), None
    value = out
    if value.startswith("```"):
        m = re.match(r"^```\S*\n?", value)
        if m:
            value = value[m.end():].strip()
    elif value.endswith("```"):
        value = value[:-3].strip()
    return value, None


def reflection_seed(run_seed: int, iteration: int, component: str, attempt: int = 0) -> int:
    """Per-call seed, so equal prompts in different iterations are distinct samples (and cache keys)."""
    h = hashlib.sha256(f"{run_seed}|{iteration}|{component}|{attempt}".encode()).hexdigest()
    return int(h[:8], 16)


def rollout_seed(*parts) -> int:
    """Deterministic rollout seed in [1, 2**31 - 2] from ``"|".join(parts)``."""
    return 1 + int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16) % (2 ** 31 - 2)


@dataclass
class ReflectionResult:
    new_texts: dict[str, str] = field(default_factory=dict)
    prompts: dict[str, str] = field(default_factory=dict)
    raw: dict[str, str] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)           # component -> reason
    finish: dict[str, Optional[str]] = field(default_factory=dict)   # component -> provider stop reason
    calls: int = 0


class ReflectionProposer:
    """One reflection-LM call per selected component; ``template`` is a string or ``{component: template}``."""

    def __init__(self, llm: LLM, template: str | Mapping[str, str] | None = None, *, role: str = "reflection",
                 system: Optional[str] = None, max_tokens: Optional[int] = None,
                 keep_trailing_newline: bool = True) -> None:
        self.llm = llm
        if template is None or isinstance(template, str):
            t = TEMPLATES.get(template or "default", template or DEFAULT_TEMPLATE)
            validate_template(t)
            self.templates: dict[str, str] = {}
            self.default = t
        else:
            for t in template.values():
                validate_template(t)
            self.templates = dict(template)
            self.default = DEFAULT_TEMPLATE
        self.role = role
        self.system = system
        self.max_tokens = max_tokens
        self.keep_trailing_newline = keep_trailing_newline

    def template_for(self, component: str) -> str:
        return self.templates.get(component, self.default)

    def propose(self, candidate: Artifact, reflective_dataset: Mapping[str, list], components: Sequence[str], *,
                seed_fn=None) -> ReflectionResult:
        res = ReflectionResult()
        for comp in components:
            records = reflective_dataset.get(comp) or []
            if not records:
                continue
            current = candidate.get(comp, "")
            prompt = build_prompt(self.template_for(comp), current.rstrip("\n") if self.keep_trailing_newline
                                  else current, records)
            resp = self.llm.complete(prompt, system=self.system, max_tokens=self.max_tokens,
                                     seed=seed_fn(comp) if seed_fn else None, role=self.role)
            res.calls += 1
            res.prompts[comp] = prompt
            res.raw[comp] = resp.text
            if not resp.ok:
                res.rejected[comp] = f"llm error: {resp.error}"
                continue
            res.finish[comp] = finish = response_finish_reason(resp)
            text, err = parse_fenced(resp.text, finish)
            if text is None:
                res.rejected[comp] = err or "unparseable"
                continue
            if self.keep_trailing_newline and current.endswith("\n") and not text.endswith("\n"):
                text += "\n"
            res.new_texts[comp] = text
        return res
