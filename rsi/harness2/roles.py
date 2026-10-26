"""Improver sessions over text: editor/composer stages (retry once, then inherit), selector and reflector."""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

from ..core.artifact import Artifact, parse_file_blocks
from ..core.llm import LLM, LLMResponse, extract_tag
from . import context as C
from . import prompts as P
from .guard import OpenWorld
from .records import Rollout, Stage, TaskRecord
from .tree import off_surface, validate

SYSTEM = "You improve an autonomous agent's harness from execution evidence. Follow the mission exactly."
END = f"\n{C.END_MARK}\nReply now, in the format the mission gives.\n"
ADOPT = re.compile(r"^[ \t]*(ADOPT|REPRODUCE)[ \t]*:[ \t]*`?([A-Za-z0-9_.-]+)`?[ \t]*$", re.M)
CHOICE = re.compile(r"```choice\s*\n?\s*([A-Za-z0-9_-]+)\s*\n?\s*```", re.S)
TRIVIAL = "trivial pool: single final at k=1; delivered without a selector session"
EXP_KEYS = ("cue", "lesson", "evidence", "confidence")


@dataclass
class Session:
    """What every improver call needs: the improver LLM, the open-world guard and the domain hooks."""

    llm: LLM
    guard: OpenWorld
    domain: object
    editable: Sequence[str]
    interface: str = ""
    retry_invalid: int = 1
    max_text_chars: int = 24000
    seed: int = 0
    calls: int = 0
    transport_attempts: int = 2     # TRANSPORT_ATTEMPTS [code proposer.py]; no back-off sleep here

    def ask(self, prompt: str, *, role: str, where: str, attempts: Optional[int] = None) -> LLMResponse:
        """The single choke point to an improver: every prompt is screened for grader verdicts first.

        A transport failure (``resp.ok`` false) is retried up to ``transport_attempts`` times in total."""
        self.guard.check(SYSTEM + "\n" + prompt, where)
        resp = None
        for n in range(max(1, attempts or self.transport_attempts)):
            self.calls += 1
            resp = self.llm.complete(prompt, system=SYSTEM, seed=_seed(self.seed, where, n or ""), role=role)
            if resp.ok:
                break
        return resp


def _seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def parse_edit(reply: str, start: Artifact, pool: Mapping[str, Artifact], editable: Sequence[str]):
    """``(tree, adopted_from, notes, error)`` from a ``<notes>`` + ADOPT/REPRODUCE + ``=== FILE:`` reply."""
    notes = extract_tag(reply, "notes")
    body = re.sub(r"<notes>.*?</notes>", "", reply or "", flags=re.S)
    head = body.split("=== FILE:")[0]
    adopted, base = None, start
    if m := ADOPT.search(head):
        adopted = m.group(2)
        if adopted not in pool:
            return None, None, notes or reply, f"{m.group(1)} names unknown candidate {adopted!r}; known: {sorted(pool)}"
        base = pool[adopted]
    updates = {k: (None if v.strip() == "<<DELETE>>" else v) for k, v in parse_file_blocks(body).items()}
    if bad := off_surface(list(updates), editable):
        return None, adopted, notes or reply, (f"files off the editable surface were written: {bad[:6]}; editable "
                                               f"paths match {list(editable)}")
    return base.with_files(updates), adopted, notes if notes is not None else (reply or ""), None


def _with_harness(ctx: Mapping[str, str], tree: Artifact) -> dict[str, str]:
    out = {k: v for k, v in ctx.items() if not k.startswith("context/harness/")}
    out.update({f"context/harness/{n}": t for n, t in tree.items()})
    return out


def run_stage(s: Session, *, label: str, role: str, prompt: str, ctx: Mapping[str, str], start: Artifact,
              fallback: Artifact, pool: Mapping[str, Artifact] = {}) -> Stage:
    """Up to ``1 + retry_invalid`` attempts; an invalid or failed proposal inherits ``fallback``."""
    st = Stage(label=label, role=role)
    mission = prompt + "\n" + P.MISSION.format(layout=C.layout(ctx))
    cur_ctx, cur_start, err = dict(ctx), start, None
    for attempt in range(1 + s.retry_invalid):
        text = mission + (P.REJECTED.format(err=err) if err else "") + "\n" + C.render(cur_ctx) + END
        resp = s.ask(text, role=role, where=f"{label} attempt {attempt + 1}")
        st.attempts, st.prompt, st.reply = st.attempts + 1, text, resp.text
        if not resp.ok:
            err = f"transport: {resp.error}"
            break
        tree, adopted, st.notes, err = parse_edit(resp.text, cur_start, pool, s.editable)
        if err is None:
            s.guard.screen(tree.files, f"proposed tree {label}")
            err = " | ".join(validate(tree, start, domain=s.domain, max_text_chars=s.max_text_chars)) or None
        if err is None:
            st.tree, st.adopted_from, st.kept_base = tree, adopted, tree == fallback
            return st
        if tree is not None:
            cur_ctx, cur_start = _with_harness(ctx, tree), tree
    st.tree, st.fell_back, st.kept_base, st.error = fallback, True, True, (err or "unknown")[:400]
    return st


def anon_ids(task_id: str, k: int, mode: str, labels: Sequence[str]) -> dict[str, str]:
    """Stable anonymous ids ``A, B, ..., Z, AA, ...`` -> label [code selection.assign_ids]."""
    order = sorted(labels)
    random.Random(int(hashlib.sha1(f"{task_id}|{k}|{mode}".encode()).hexdigest()[:12], 16)).shuffle(order)
    return {_anon(i): lab for i, lab in enumerate(order)}


def _anon(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def select(s: Session, rec: TaskRecord, finals: Sequence[str], *, task_text: str, source: Mapping[str, str],
           attempts: int, slots: Mapping[str, str], obs_trunc: int,
           mode_token: Optional[str] = None) -> tuple[dict, Optional[Stage]]:
    """``(selection, stage)``: trivial at k = 1, else one anonymised deliverable-only selector session."""
    delivered = [lab for lab in finals if rec.rollouts[lab].delivered]
    sel = {"pool": list(finals), "dropped": [lab for lab in finals if lab not in delivered], "ids": {},
           "evidence": "deliverable", "fallback": False, "error": None}
    if len(finals) == 1:
        ok = bool(delivered)
        return {**sel, "chosen": finals[0], "why": TRIVIAL, "ok": ok,
                "error": None if ok else "no candidate produced an artifact (finals-only pool has no base fallback)"
                }, None
    if not delivered:
        return {**sel, "chosen": sorted(finals)[0], "why": "", "ok": False, "fallback": True,
                "error": "no candidate produced an artifact (finals-only pool has no base fallback)"}, None
    ids = anon_ids(rec.task_id, rec.k, mode_token or rec.mode, delivered)
    cands = {cid: C.produced_md(rec.rollouts[lab], obs_trunc) for cid, lab in ids.items()}
    text = P.select(len(ids), list(ids), slots=slots) + "\n" + C.render(C.selector_context(task_text, cands, source))
    st = Stage(label="select", role="selector", prompt=text)
    resp = None
    for _ in range(attempts):
        st.attempts += 1
        resp = s.ask(text + END, role="selector", where="selector", attempts=1)
        if resp.ok:
            break
    st.reply = resp.text if resp else ""
    m = CHOICE.findall(st.reply) if resp and resp.ok else []
    why = (extract_tag(st.reply, "why") or "")[:2000]
    st.notes = why
    if m and m[-1] in ids:
        return {**sel, "ids": ids, "chosen": ids[m[-1]], "why": why, "ok": True}, st
    st.error = f"selector failed: {'transport: ' + str(resp.error) if resp and not resp.ok else 'no valid choice'}"
    st.fell_back = True
    return {**sel, "ids": ids, "chosen": sorted(delivered)[0], "why": why, "ok": True, "fallback": True,
            "error": st.error}, st


def parse_experience(reply: str) -> dict:
    """The reflector's ``<experience>`` entry; empty unless cue, lesson and evidence are all present."""
    block = extract_tag(reply, "experience")
    if not block:
        return {}
    exp: dict[str, str] = {}
    for line in block.splitlines():
        key, sep, val = line.partition(":")
        if sep and key.strip().lower() in EXP_KEYS:
            exp[key.strip().lower()] = val.strip()
    return exp if all(exp.get(k) for k in EXP_KEYS[:3]) else {}


def reflect(s: Session, rec: TaskRecord, *, task_text: str, rollouts: Sequence[Rollout],
            pool: Sequence[tuple[str, Artifact, Optional[Rollout]]], decisions: Mapping[str, str],
            slots: Mapping[str, str], obs_trunc: int) -> tuple[dict, Stage]:
    sel = rec.selection
    adoption = {"chosen_label": rec.adopted, "candidate_ids": sel.get("ids", {}), "reason": sel.get("why", "")}
    ctx = C.reflector_context(task_text, adopted=rec.adopted_tree, base=rec.base_tree, rollouts=rollouts, pool=pool,
                              decisions=decisions, adoption=adoption, obs_trunc=obs_trunc)
    text = P.experience(rec.adopted, sel.get("why", ""), slots=slots) + "\n" + C.render(ctx) + END
    st = Stage(label="reflect", role="reflector", prompt=text, attempts=1)
    resp = s.ask(text, role="reflector", where="reflector")
    st.reply = resp.text
    if not resp.ok:
        st.error = f"transport: {resp.error}"
        return {"error": st.error, "experience": {}, "edits": ""}, st
    edits, exp = extract_tag(resp.text, "edits") or "", parse_experience(resp.text)
    s.guard.check(edits + "\n" + "\n".join(exp.values()), "reflector experience")
    st.notes = edits
    return {"edits": edits, "experience": exp, "no_entry": not exp}, st
