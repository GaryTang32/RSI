"""Deterministic offline improvers: every role reads its rendered evidence back and replies in the mission's format."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from ..core.llm import LLM, LLMResponse, Usage, estimate_tokens
from . import context as C

LABELLED = re.compile(r"^\s*[A-Za-z][A-Za-z ]{0,30}:\s*\S")
BRACES = ("{{", "{%", "{#")


@dataclass(frozen=True)
class Lever:
    key: str
    line: str
    why: str            # evidence predicate, see _evidence


INV_LEVERS = (
    Lever("final-line", "End your reply with one final line of the form 'ANSWER: <value>'.", "unlabelled"),
    Lever("check", "Before that final line, check the result once more against the question and fix it if the check "
          "fails.", "unchecked"),
)
DOM_LEVERS = (
    Lever("steps", "Work through it step by step: name the quantities involved, then combine them.", "no-steps"),
    Lever("form", "Give the value in exactly the form the question asks for (unit, case, digits).", "always"),
)


def last_line(text: str) -> str:
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def labelled(text: str) -> bool:
    return bool(LABELLED.match(last_line(text))) and text.strip() != "(nothing produced)"


def _evidence(why: str, dels: dict[str, str], rolls: dict[str, str]) -> Optional[str]:
    base = {k: v for k, v in dels.items() if k.startswith("base")}
    if why == "unlabelled":
        bad = [k for k, v in base.items() if not labelled(v)]
        return f"{bad[0]} ends with {last_line(base[bad[0]])[:80]!r}: no labelled final line" if bad else None
    text = " ".join(rolls.values()).lower()
    if why == "unchecked":
        return None if ("check" in text or "verify" in text) else "no rollout re-checks its result before finishing"
    if why == "no-steps":
        return None if "step" in text else "no rollout names the quantities before combining them"
    return "the request fixes the form of the value; base rollouts do not state it"


def text_files(files: dict[str, str]) -> list[str]:
    return sorted(p for p in files if not p.endswith(".py"))


def target_file(files: dict[str, str]) -> Optional[str]:
    """The text file the code fills per task (one with a placeholder), else the first text file."""
    texts = text_files(files)
    return next((p for p in texts if re.search(r"\{[A-Za-z_]\w*\}", files[p])), texts[0] if texts else None)


def add_line(text: str, line: str) -> str:
    return text.rstrip("\n") + "\n" + line + "\n"


def blocks(files: dict[str, Optional[str]]) -> str:
    return "".join(f"=== FILE: {p} ===\n{'<<DELETE>>' if t is None else t.rstrip(chr(10))}\n"
                   for p, t in files.items())


@dataclass
class MockEditor:
    """INV/DOM editor: one evidence-backed lever per candidate; DOM also copies library lines; may emit invalid trees."""

    invalid: str = "never"          # never | first | always (adds a template brace to test retry and fallback)

    def __call__(self, prompt: str) -> str:
        dim = "inv" if "# YOUR DIMENSION: INV" in prompt else "dom"
        m = re.search(r"You are candidate (\d+) of", prompt)
        i = int(m.group(1)) if m else 1
        ctx = C.parse(prompt.split("# YOUR EVIDENCE", 1)[-1])
        files = C.subtree(ctx, "context/harness")
        rejected = "# YOUR PREVIOUS ATTEMPT WAS REJECTED" in prompt
        target = target_file(files)
        if target is None:
            return "<notes>\nKEEP_BASE -- no text component to edit\n</notes>"
        text = "\n".join(ln for ln in files[target].splitlines() if not any(b in ln for b in BRACES)) + "\n"
        notes = [f"repaired {target}: removed the template braces the validator rejected"] if rejected else []
        dels, rolls = C.subtree(ctx, "context/deliverables"), C.subtree(ctx, "context/rollouts")
        cands = [(lv, ev) for lv in (INV_LEVERS if dim == "inv" else DOM_LEVERS)
                 if lv.line not in text and (ev := _evidence(lv.why, dels, rolls))]
        if cands:
            lv, ev = cands[(i - 1) % len(cands)]
            text = add_line(text, lv.line)
            notes.append(f"{target}: added '{lv.line}' -- {ev}")
        if dim == "dom":
            for path, body in sorted(C.subtree(ctx, "context/library").items()):
                if path.endswith(f"/harness/{target}"):
                    for ln in body.splitlines():
                        if ln.strip() and ln not in text:
                            text = add_line(text, ln)
                            notes.append(f"{target}: copied '{ln}' from library {path.split('/')[1]}; re-checked "
                                         "against this task's base rollouts")
        if self.invalid == "always" or (self.invalid == "first" and not rejected):
            text = add_line(text, "{{ note }}")
        if text.strip() == files[target].strip():
            return "<notes>\nKEEP_BASE -- the base rollouts show nothing on this dimension to fix\n</notes>"
        return "<notes>\n" + "\n".join(notes) + "\n</notes>\n" + blocks({target: text})


@dataclass
class MockComposer:
    """Composer: merges the added lines of every candidate whose rollout delivered; ADOPT when one candidate equals it."""

    def __call__(self, prompt: str) -> str:
        ctx = C.parse(prompt.split("# YOUR EVIDENCE", 1)[-1])
        base = C.subtree(ctx, "context/harness")
        labels = list(dict.fromkeys(k.split("/")[2] for k in ctx if k.startswith("context/pool/")))
        merged, notes, trees = dict(base), [], {}
        for lab in labels:
            tree = trees[lab] = C.subtree(ctx, f"context/pool/{lab}/harness")
            changed = [p for p in tree if tree[p].strip() != base.get(p, "").strip()]
            if not changed:
                continue
            produced = ctx.get(f"context/pool/{lab}/produced.md", "(nothing produced)")
            if produced.strip() in ("", "(nothing produced)"):
                notes.append(f"dropped -> {lab} -> its rollout produced nothing (UNSAFE)")
                continue
            for p in changed:
                have = merged.get(p, "").splitlines()
                new = [ln for ln in tree[p].splitlines() if ln not in have]
                merged[p] = "\n".join(have + new) + "\n" if p in merged else tree[p]
            notes.append(f"kept -> {lab} ({', '.join(changed)}) -> its rollout delivered {last_line(produced)[:60]!r}")
        diff = {p: t for p, t in merged.items() if t.strip() != base.get(p, "").strip()}
        if not diff:
            return "<notes>\nKEEP_BASE -- no candidate edit earned its place\n" + "\n".join(notes) + "\n</notes>"
        same = [lab for lab, t in trees.items() if set(t) == set(merged)
                and all(t[p].strip() == merged[p].strip() for p in t)]
        head = "<notes>\n" + "\n".join(notes) + "\n</notes>\n"
        return head + (f"ADOPT: {same[-1]}\n" if same else blocks(diff))


@dataclass
class MockSelector:
    """Selector: DELIVERY and FINISH first (a labelled final line), then the lowest id."""

    def __call__(self, prompt: str) -> str:
        cands = {p.removesuffix(".md"): t.split("\n", 1)[-1]
                 for p, t in C.subtree(C.parse(prompt), "context/candidates").items()}
        if not cands:
            return "<why>no candidates</why>"
        order = sorted(cands, key=lambda c: (not labelled(cands[c]), len(c), c))
        best = order[0]
        runner = f"; runner-up {order[1]} ends {last_line(cands[order[1]])[:60]!r}" if len(order) > 1 else ""
        why = f"FINISH decided it: {best} ends with a labelled final line {last_line(cands[best])[:60]!r}{runner}"
        return f"<why>\n{why}\n</why>\n```choice\n{best}\n```"


@dataclass
class MockReflector:
    """Reflector: one row per (component, form) with a verdict from deliverable contrasts; NO_ENTRY unless one helped."""

    force: Optional[str] = None     # None | "none" (always NO_ENTRY)

    def __call__(self, prompt: str) -> str:
        ctx = C.parse(prompt)
        base, adopted = C.subtree(ctx, "context/base_harness"), C.subtree(ctx, "context/harness")
        dels = C.subtree(ctx, "context/deliverables")
        base_bad = [k for k, v in dels.items() if k.startswith("base") and not labelled(v)]
        labels = list(dict.fromkeys(k.split("/")[2] for k in ctx if k.startswith("context/pool/")))
        forms: dict[tuple[str, str], list[str]] = {}
        n_changed: dict[str, int] = {}
        for lab in labels:
            tree = C.subtree(ctx, f"context/pool/{lab}/harness")
            changed = sorted(p for p in set(tree) | set(base) if tree.get(p, "").strip() != base.get(p, "").strip())
            n_changed[lab] = len(changed)
            for p in changed:
                forms.setdefault((p, tree.get(p, "").strip()), []).append(lab)
        rows, helped = [], []
        for (p, text), labs in forms.items():
            added = [ln for ln in text.splitlines() if ln not in base.get(p, "").splitlines()]
            gained = [lab for lab in labs if labelled(dels.get(lab, ""))]
            if all(n_changed[lab] > 1 for lab in labs):
                verdict = "confounded"
            elif gained and base_bad:
                verdict = "helped"
            else:
                verdict = "no observed effect"
            obs = (f"{', '.join(gained)} ended with a labelled final line; base rollouts {', '.join(base_bad)} did not"
                   if verdict == "helped" else "no difference in the deliverables' final line")
            rows.append(f"component: {p}\ncandidates: {', '.join(labs)}\nchange: added {added[:3]}\n"
                        f"observed: {obs}\nverdict: {verdict}")
            if verdict == "helped" and adopted.get(p, "").strip() != base.get(p, "").strip() and added:
                helped.append((p, labs, added[0], obs))
        edits = "<edits>\n" + "\n\n".join(rows) + "\n</edits>\n"
        if self.force == "none" or not helped:
            return edits + "NO_ENTRY\n"
        p, labs, line, obs = helped[0]
        return edits + (f"<experience>\ncue: base runs end in free prose although the request wants one final value\n"
                        f"lesson: in {p}, ask for one labelled final line ({line!r}) and keep the rest unchanged\n"
                        f"evidence: row {p} [{', '.join(labs)}]: helped -- {obs}\n"
                        "confidence: medium, one rollout per candidate\n</experience>\n")


ROUTES = (("You are adapting an autonomous agent's harness.", "editor"), ("You are composing", "composer"),
          ("You are choosing which of", "selector"), ("You are reading the complete record", "reflector"))


@dataclass
class MockImprover(LLM):
    """Routes each improver prompt to its role mock by the prompt's opening line."""

    editor: MockEditor = field(default_factory=MockEditor)
    composer: MockComposer = field(default_factory=MockComposer)
    selector: MockSelector = field(default_factory=MockSelector)
    reflector: MockReflector = field(default_factory=MockReflector)
    name: str = "harness2-mock"

    def __post_init__(self) -> None:
        super().__init__()
        self.roles: list[str] = []

    def route(self, prompt: str) -> str:
        return next((role for head, role in ROUTES if prompt.startswith(head)), "unknown")

    def _complete(self, prompt, *, system, max_tokens, seed) -> LLMResponse:
        role = self.route(prompt)
        self.roles.append(role)
        text = getattr(self, role)(prompt) if role != "unknown" else "unknown prompt"
        return LLMResponse(text=text, usage=Usage(1, estimate_tokens((system or "") + prompt), estimate_tokens(text)),
                           model=self.name)


def adoption_of(prompt: str) -> dict:
    """The reflector's ``context/adoption.json`` (for tests)."""
    return json.loads(C.parse(prompt).get("context/adoption.json", "{}"))
