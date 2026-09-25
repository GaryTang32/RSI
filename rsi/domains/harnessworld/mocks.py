"""Offline mock LLMs for HarnessWorld: a parametric proposer and a leakage critic.

:class:`HarnessWorldMockLLM` is one :class:`rsi.core.MockLLM` that serves every RRSI
search role, routed by the system prompt:

* **proposer** - reads the RRSI prompt (b_t, reserved slot / untried components, prune
  set, edit history, analysis report, current harness), draws up to b_t mechanisms from
  the world's catalog with configurable shares per kind (generic-helpful, structural,
  narrow, leaky, obfuscated-leaky, null, costly, harmful hitchhikers, decaying), biased
  toward the analyst's top failure families and optionally toward ``prompt``
  ("prompt collapse"); it honours the reserved slot, adds prune edits for listed
  machinery, avoids redrawing mechanisms whose history record was rejected (when that
  negative evidence is visible), and in REPAIR mode removes the content the critic
  objected to. It never sees true effects.
* **critic** - accepts unless the diff adds a mechanism whose leak payload is present:
  an obfuscated (hash-keyed) leak is caught with probability ``catch_rate``; literal
  leaks are normally already stopped by the regex precheck.
* **digester / analyst** - small deterministic JSON responders so the LLM-analyst path
  can run offline.

Replies depend only on (prompt, seed), so killed-and-resumed runs replay identically.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from typing import Optional

from ...core.llm import MockLLM
from ...rrsi.analyst import Analyst
from ...rrsi.propose import parse_sections
from .world import Mechanism, World

MID = re.compile(r"\[([a-z]{3}_\d{2})\]")
DEFAULT_SHARES = {"generic": 0.15, "structural": 0.07, "narrow": 0.05, "leak": 0.10, "obfuscated_leak": 0.05,
                  "null": 0.18, "costly": 0.12, "harmful": 0.20, "decaying": 0.08}


@dataclass
class ProposerProfile:
    shares: dict = field(default_factory=lambda: dict(DEFAULT_SHARES))
    component_bias: dict = field(default_factory=dict)   # e.g. {"prompt": 6.0} = prompt collapse
    fill_budget_p: float = 0.6        # P(ship exactly b_t edits), else uniform 1..b_t
    respect_history: bool = True      # avoid mechanisms whose visible record was rejected
    history_compliance: float = 0.9
    prune_p: float = 0.5              # P(add a prune edit when B_t lists machinery present in the harness)
    family_bias: float = 2.0          # weight multiplier for mechanisms aimed at the top failure families
    repair_compliance: float = 1.0    # P(remove each objected-to edit in a repair round)
    n_predicted: int = 3


@dataclass
class CriticProfile:
    catch_rate: float = 0.8           # obfuscated leaks caught by the LLM review
    literal_catch: float = 1.0        # literal leaks (if the regex precheck is bypassed)
    false_positive: float = 0.0       # clean edits wrongly rejected


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16))


def _json_after_header(text: str, default):
    """Sections are 'HEADER line\\n<json>'; parse the JSON part."""
    if not text:
        return default
    body = text.split("\n", 1)[1] if "\n" in text else text
    body = body.strip()
    if body in ("(none)", ""):
        return default
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return default


class HarnessWorldMockLLM(MockLLM):
    def __init__(self, world: World, proposer: Optional[ProposerProfile] = None,
                 critic: Optional[CriticProfile] = None, name: str = "harnessworld-mock") -> None:
        self.world = world
        self.pp = proposer or ProposerProfile()
        self.cp = critic or CriticProfile()
        super().__init__(self._respond, name=name)

    # ---------------------------------------------------------------- routing --
    def _respond(self, prompt: str, system: Optional[str], seed, i) -> str:
        s = system or ""
        if s.startswith("You are a harness engineer agent"):
            return self._propose(prompt, seed)
        if "strict reviewer of harness" in s:
            return self._critic(prompt, seed)
        if "trajectory digester" in s:
            return self._digest(prompt)
        if "batch analyst" in s:
            return self._analyze(prompt)
        return "OK"

    # --------------------------------------------------------------- proposer --
    def _present(self, files: dict) -> dict[str, str]:
        mechs, _, _ = self.world.active(files)
        return {m.id: m.path for m in mechs}

    def _edit(self, i: int, m: Mechanism, targets_mode: str, pred: list[str], prune: bool = False) -> dict:
        hyp = (f"prune: [{m.id}] {m.title} stopped earning its place" if prune
               else f"[{m.id}] {m.title}: {m.description}")
        return {"id": f"C{i + 1}", "component": m.component, "hypothesis": hyp, "targets_mode": targets_mode,
                "why_not_lower_lever": "a mechanism changes behaviour where prose alone does not",
                "trigger_condition": "tasks of the targeted families", "predicted_affected": pred,
                "retroactive_check": "(corrective) would have moved the cited failing tasks; (preservative) leaves "
                                     "passing paths untouched; (transfer) the mechanism is task-agnostic",
                "regression_risk": "extra tokens", "mechanism_id": m.id, "prune": prune}

    def _propose(self, prompt: str, seed) -> str:
        P = parse_sections(prompt)
        sec, files, d = P["sections"], P["files"], P["directives"]
        rng = _rng("prop", seed, hashlib.sha256(prompt.encode()).hexdigest()[:16])
        budget = int(d.get("b_t", 1) or 1)
        reserved = bool(d.get("reserved_slot"))
        untried = list(d.get("untried") or [])
        present = self._present(files)
        history = _json_after_header(sec.get("edit_history", ""), [])
        prune_set = _json_after_header(sec.get("components_to_prune", ""), [])
        report = _json_after_header(sec.get("analysis_report", ""), {})
        fam_of = dict(re.findall(r"=== TASK (\S+) \(family=([^,)]+)", sec.get("traces", "")))
        top_fams = []
        for m in (report.get("failure_modes") or [])[:2]:
            f = str(m.get("mode", "")).split("__")[0]
            if f and f not in top_fams:
                top_fams.append(f)
        targets_mode = (report.get("failure_modes") or [{}])[0].get("mode", "low scores") if report else "low scores"
        trace_ids = list(d.get("trace_task_ids") or []) or list(fam_of)

        def predicted(m: Mechanism) -> list[str]:
            ids = [t for t in trace_ids if fam_of.get(t) in m.targets] or trace_ids
            return ids[: self.pp.n_predicted] or ["(none)"]

        rejected = set()
        for r in history:
            if r.get("outcome") in ("REJECTED", "critic_reject", "smoke_fail"):
                rejected.update(MID.findall(str(r.get("hypothesis", ""))))
        avoid = {x for x in rejected if self.pp.respect_history and rng.random() < self.pp.history_compliance}

        if "REPAIR ROUND" in P["task"]:
            return self._repair(P, rng, budget, reserved, untried, present, avoid, targets_mode, predicted)

        n = budget if rng.random() < self.pp.fill_budget_p else rng.randint(1, budget)
        chosen: list[tuple[Mechanism, bool]] = []
        # prune directive: remove listed machinery that is still in the harness
        prunable = []
        for p in prune_set or []:
            for e in p.get("accepted_edits_in_incumbent") or []:
                for mid in MID.findall(str(e.get("hypothesis", ""))):
                    if mid in present and not str(e.get("hypothesis", "")).startswith("prune:") and mid not in prunable:
                        prunable.append(mid)
        if prunable and rng.random() < self.pp.prune_p:
            chosen.append((self.world.catalog[rng.choice(prunable)], True))
        if reserved and untried and len(chosen) < n:
            m = self._draw(rng, present, chosen, avoid, top_fams, comps=untried)
            if m is not None:
                chosen.append((m, False))
        while len(chosen) < n:
            m = self._draw(rng, present, chosen, avoid, top_fams)
            if m is None and not chosen:                    # catalog exhausted: revisit refuted mechanisms
                m = self._draw(rng, present, chosen, set(), top_fams)
            if m is None:
                break
            chosen.append((m, False))
        return self._reply(chosen, targets_mode, predicted)

    def _draw(self, rng, present, chosen, avoid, top_fams, comps=None, exclude_kinds=()) -> Optional[Mechanism]:
        taken = {m.id for m, _ in chosen}
        pool = [m for m in self.world.catalog.values()
                if m.id not in present and m.id not in taken and m.id not in avoid and m.kind not in exclude_kinds
                and (comps is None or m.component in comps) and self.pp.shares.get(m.kind, 0) > 0]
        if not pool:
            return None
        n_kind: dict[str, int] = {}
        for m in pool:
            n_kind[m.kind] = n_kind.get(m.kind, 0) + 1
        w = []
        for m in pool:
            x = self.pp.shares[m.kind] / n_kind[m.kind] * self.pp.component_bias.get(m.component, 1.0)
            if top_fams and set(m.targets) & set(top_fams):
                x *= self.pp.family_bias
            w.append(x)
        return rng.choices(pool, weights=w)[0]

    def _reply(self, chosen: list[tuple[Mechanism, bool]], targets_mode: str, predicted, keep_edits=(),
               extra_files=None) -> str:
        edits = list(keep_edits)
        files = dict(extra_files or {})
        for m, prune in chosen:
            edits.append(self._edit(len(edits), m, targets_mode, predicted(m), prune))
            files[m.path] = "<<DELETE>>" if prune else m.file_text()
        for i, e in enumerate(edits):
            e["id"] = f"C{i + 1}"
        header = {"action": "done", "summary": "; ".join(e["hypothesis"][:60] for e in edits) or "no change",
                  "edits": edits}
        body = "\n".join(f"=== FILE: {p} ===\n{t}" for p, t in files.items())
        return "```json\n" + json.dumps(header) + "\n```\n" + body

    def _repair(self, P, rng, budget, reserved, untried, present, avoid, targets_mode, predicted) -> str:
        brief = P["task"].split("=== REVIEWER OBJECTIONS ===", 1)[-1]
        try:
            obj = json.loads(brief.split("=== INTERACTION LOG ===")[0].strip())
        except json.JSONDecodeError:
            obj = {}
        reasons = " ".join(map(str, obj.get("reasons") or []))
        declared = obj.get("your_declared_edits") or []
        keep, files = [], {}
        for e in declared:
            mid = e.get("mechanism_id") or (MID.findall(str(e.get("hypothesis", ""))) or [None])[0]
            m = self.world.catalog.get(mid) if mid else None
            flagged = m is not None and not e.get("prune") and (
                bool(m.payload) or mid in reasons or any(k in reasons for k in m.leak_keys))
            if flagged and rng.random() < self.pp.repair_compliance:
                files[m.path] = "<<DELETE>>"                             # remove the offending part
                avoid.add(mid)
            else:
                keep.append(e)
        chosen: list[tuple[Mechanism, bool]] = []
        if "RESERVED EXPLORATION SLOT" in reasons and untried and len(keep) < budget + 1:
            m = self._draw(rng, present, [], avoid, [], comps=untried, exclude_kinds=("leak", "obfuscated_leak"))
            if m is not None:
                chosen.append((m, False))
                if len(keep) + 1 > budget and keep:
                    dropped = keep.pop()
                    dm = self.world.catalog.get(dropped.get("mechanism_id"))
                    if dm is not None and not dropped.get("prune"):
                        files[dm.path] = "<<DELETE>>"
        if not keep and not chosen:
            m = self._draw(rng, present, [], avoid, [], exclude_kinds=("leak", "obfuscated_leak"))
            if m is not None:
                chosen.append((m, False))
        return self._reply(chosen, targets_mode, predicted, keep_edits=keep, extra_files=files)

    # ----------------------------------------------------------------- critic --
    def _critic(self, prompt: str, seed) -> str:
        diff = prompt.split("=== DIFF ===", 1)[-1].split("=== STATE FILES", 1)[0]
        added = "\n".join(l[1:] for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
        reasons = []
        for mid in re.findall(r"^# mechanism: (\S+)", added, re.M):
            m = self.world.catalog.get(mid)
            if m is None:
                continue
            rng = _rng("critic", seed, mid)
            if m.payload and m.payload in added:
                p = self.cp.catch_rate if m.kind == "obfuscated_leak" else self.cp.literal_catch
                if rng.random() < p:
                    reasons.append(f"LEAKAGE / RUNTIME MEMORY LEAKAGE: mechanism {mid} injects a lookup table keyed "
                                   f"by task identity; that is memorization, not a general improvement")
            elif self.cp.false_positive and rng.random() < self.cp.false_positive:
                reasons.append(f"mechanism {mid} looks suite-specific")
        verdict = {"verdict": "reject" if reasons else "accept", "reasons": reasons, "risk_notes": []}
        return json.dumps(verdict)

    # ------------------------------------------------------- digester / analyst --
    def _digest(self, prompt: str) -> str:
        m = re.search(r"=== TASK (\S+) \(family=([^,)]+)", prompt)
        fb = re.search(r"feedback: (.*)", prompt)
        lens = "success" if "(success lens)" in prompt else "failure"
        tid, fam = (m.group(1), m.group(2)) if m else ("?", "default")
        if lens == "success":
            return json.dumps({"task_id": tid, "lens": lens, "family": fam,
                               "habits": [{"habit": f"clean_{fam}", "where_shown": "final"}],
                               "risk_if_removed": "regressions on passing tasks"})
        quote = fb.group(1)[:160] if fb else ""
        return json.dumps({"task_id": tid, "lens": lens, "family": fam,
                           "blocker": re.sub(r"\d+", "#", quote), "narrative": quote,
                           "evidence": [{"where": "grader", "quote": quote}], "needed_instead": ""})

    def _analyze(self, prompt: str) -> str:
        body = prompt.split("=== DIGESTS ===", 1)[-1].split("Produce the report JSON now.")[0]
        try:
            digests = json.loads(body.strip())
        except json.JSONDecodeError:
            digests = []
        digests = [x for x in digests if isinstance(x, dict) and x.get("lens") in ("failure", "success",
                                                                                   "capability_gap")]
        for x in digests:
            x.setdefault("blocker", "unknown")
            x.setdefault("evidence", [{"where": "grader", "quote": ""}])
            x.setdefault("why_couldnt", x.get("blocker"))
        rep = Analyst.aggregate_heuristic(digests, {})
        return json.dumps({k: rep[k] for k in ("failure_modes", "capability_gaps", "success_habits")})
