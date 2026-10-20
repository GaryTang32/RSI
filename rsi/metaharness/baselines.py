"""Structured optimisers (OpenEvolve, TTT-Discover PUCT, GEPA) as equal-budget comparison arms (paper §4.1)."""
from __future__ import annotations

import json
import math
import random
import re
from collections import Counter
from typing import Optional

from ..core.llm import Usage
from .mock import PARENT_HINT
from .proposer import ProposalBatch, Proposer, excerpt

POLICIES = ("openevolve", "ttt_discover", "gepa", "gepa_minibatch")

# OpenEvolve 0.4.0 ``config.py`` defaults
OE_ISLANDS = 5
OE_EXPLORATION = 0.2
OE_EXPLOITATION = 0.7
OE_ARCHIVE = 100
OE_TOP = 3
OE_DIVERSE = 2
OE_ARTIFACT_BYTES = 20 * 1024
PUCT_C = 1.0
#: GEPA ``reflection_minibatch_size`` default (``api.py``)
GEPA_MINIBATCH = 3
#: re-asks when the inner proposer returns an exact copy of an existing program
MAX_DEDUP_RETRIES = 3


class _Prog:
    def __init__(self, name: str, score: float, meta: dict, per_unit: dict) -> None:
        self.name, self.score, self.meta, self.per_unit = name, score, meta, per_unit
        self.base = meta.get("base_system")
        self.kind = meta.get("kind", "candidate")
        self.order = int(meta.get("order", 0) or 0)


def _cand_names(view: dict[str, str]) -> set[str]:
    return {m.group(1) for p in view for m in [re.match(r"candidates/([^/]+)/", p)] if m}


def programs_in(view: dict[str, str]) -> list[_Prog]:
    """Every evaluated program of a full view, in registration order."""
    out = []
    for n in sorted(_cand_names(view)):
        sc = view.get(f"candidates/{n}/eval/search/scores.json")
        if not sc:
            continue
        try:
            d = json.loads(sc)
            meta = json.loads(view.get(f"candidates/{n}/meta.json", "{}"))
        except ValueError:
            continue
        out.append(_Prog(n, float(d.get("score", 0.0)), meta, d.get("per_unit") or {}))
    return sorted(out, key=lambda p: (p.order, p.name))


def _n_proposed(progs: list[_Prog]) -> int:
    return sum(1 for p in progs if p.kind != "baseline")


class StructuredOptimizerProposer(Proposer):
    """Per slot, apply an optimiser's parent selection and context to the full view; ``inner`` writes one candidate."""

    def __init__(self, inner: Proposer, policy: str, *, seed: int = 0, puct_c: float = PUCT_C) -> None:
        if policy not in POLICIES:
            raise ValueError(f"policy must be one of {POLICIES}")
        self.inner = inner
        self.policy = policy
        self.seed = seed
        self.puct_c = puct_c
        self.role = getattr(inner, "role", "proposer")
        self._inst_cache: dict[tuple[str, int], dict[tuple[str, str], tuple[float, str]]] = {}

    def _openevolve(self, progs: list[_Prog], slot_index: int, rng: random.Random) -> tuple[_Prog, list[_Prog]]:
        # islands; 20% exploration, 70% exploitation (archive), 10% uniform; context = top + diverse island programs
        island = slot_index % OE_ISLANDS
        members = [p for p in progs if p.kind == "baseline" or p.meta.get("island") in (None, "") or
                   int(p.meta.get("island", -1)) == island]
        members = members or progs
        archive = {p.name for p in sorted(progs, key=lambda p: -p.score)[:OE_ARCHIVE]}
        r = rng.random()
        if r < OE_EXPLORATION:
            parent = rng.choice(members)
        elif r < OE_EXPLORATION + OE_EXPLOITATION:
            parent = rng.choice([p for p in members if p.name in archive] or [p for p in progs if p.name in archive])
        else:
            parent = rng.choice(progs)
        top = [p for p in sorted(members, key=lambda p: (-p.score, p.order)) if p.name != parent.name][:OE_TOP]
        rest = [p for p in members if p.name != parent.name and p not in top]
        return parent, top + rng.sample(rest, min(OE_DIVERSE, len(rest)))

    def _puct(self, progs: list[_Prog], extra_visits: dict[str, int]) -> _Prog:
        """argmax ``Q(s) + c * P(s) * sqrt(1 + T) / (1 + n(s))``, Q = best score among s and its descendants."""
        children: dict[str, list[_Prog]] = {}
        for p in progs:
            if p.base:
                children.setdefault(p.base, []).append(p)

        def best_desc(p: _Prog, seen=None) -> float:
            seen = seen or set()
            if p.name in seen:
                return p.score
            seen.add(p.name)
            return max([p.score] + [best_desc(c, seen) for c in children.get(p.name, [])])

        ranked = sorted(progs, key=lambda p: (-p.score, p.order))
        w = {p.name: len(ranked) - i for i, p in enumerate(ranked)}
        tot = sum(w.values())
        t = _n_proposed(progs) + sum(extra_visits.values())

        def score(p: _Prog) -> float:
            visits = len(children.get(p.name, [])) + extra_visits.get(p.name, 0)
            return best_desc(p) + self.puct_c * (w[p.name] / tot) * math.sqrt(1 + t) / (1 + visits)
        return max(progs, key=lambda p: (score(p), -p.order))

    def _gepa(self, progs: list[_Prog], rng: random.Random) -> _Prog:
        # the earlier per-unit variant (not GEPA's per-instance front), kept unchanged for M7's record
        wins: dict[str, int] = {}
        for u in sorted({u for p in progs for u in p.per_unit}):
            best = max(p.per_unit.get(u, -1.0) for p in progs)
            for p in progs:
                if u in p.per_unit and p.per_unit[u] >= best - 1e-12:
                    wins[p.name] = wins.get(p.name, 0) + 1
        front = [p for p in progs if wins.get(p.name)]
        if not front:
            return max(progs, key=lambda p: p.score)
        return rng.choices(front, weights=[wins[p.name] for p in front], k=1)[0]

    def _instances(self, view: dict[str, str], name: str) -> dict[tuple[str, str], tuple[float, str]]:
        """``{(unit, step): (0/1 score, raw eval record line)}`` of program ``name``, from its stored traces."""
        pre = f"candidates/{name}/eval/search/traces/"
        paths = sorted(p for p in view if p.startswith(pre))
        key = (name, sum(len(view[p]) for p in paths))
        if key in self._inst_cache:
            return self._inst_cache[key]
        out: dict[tuple[str, str], tuple[float, str]] = {}
        for p in paths:
            unit = p[len(pre):].rsplit(".", 1)[0]
            for line in view[p].splitlines():
                if '"eval_step"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("type") != "eval_step":
                    continue
                ok = r.get("ok")
                ok = ok if isinstance(ok, bool) else str(ok).lower() == "true"
                out[(unit, str(r.get("step")))] = (1.0 if ok else 0.0, line)
        self._inst_cache[key] = out
        return out

    @staticmethod
    def remove_dominated(fronts: dict, scores: dict[str, float]) -> dict:
        """GEPA ``remove_dominated_programs``: drop programs each of whose instance fronts holds another survivor."""
        progs = sorted({p for f in fronts.values() for p in f}, key=lambda x: scores[x])
        dominated: set[str] = set()
        found = True
        while found:
            found = False
            for y in progs:
                if y in dominated:
                    continue
                others = set(progs) - {y} - dominated
                if all(any(o in others for o in f) for f in fronts.values() if y in f):
                    dominated.add(y)
                    found = True
                    break
        return {k: {p for p in f if p not in dominated} for k, f in fronts.items()}

    def _gepa_minibatch(self, view: dict[str, str], progs: list[_Prog], rng: random.Random) -> _Prog:
        # GEPA select_program_candidate_from_pareto_front over (unit, eval step) instances
        inst = {p.name: self._instances(view, p.name) for p in progs}
        keys = sorted({k for d in inst.values() for k in d})
        if not keys:
            return max(progs, key=lambda p: p.score)
        fronts = {}
        for k in keys:
            vals = {n: d[k][0] for n, d in inst.items() if k in d}
            best = max(vals.values())
            fronts[k] = {n for n, v in vals.items() if v >= best - 1e-12}
        fronts = self.remove_dominated(fronts, {p.name: p.score for p in progs})
        freq = Counter(n for k in keys for n in fronts[k])
        by = {p.name: p for p in progs}
        sampling = [n for n in sorted(freq, key=lambda n: (by[n].order, n)) for _ in range(freq[n])]
        return by[rng.choice(sampling)]

    def _minibatch(self, view: dict[str, str], parent: str, n_proposed: int, seed: int) -> dict[str, str]:
        """GEPA reflective dataset: the parent's records of an epoch-shuffled minibatch of search instances."""
        inst = self._instances(view, parent)
        keys = sorted(inst)
        if not keys:
            return {}
        epoch, pos = divmod(n_proposed, max(1, len(keys) // GEPA_MINIBATCH))
        perm = list(keys)
        random.Random(f"gepa-epoch|{self.seed}|{seed}|{epoch}").shuffle(perm)
        chosen = perm[pos * GEPA_MINIBATCH:(pos + 1) * GEPA_MINIBATCH] or perm[:GEPA_MINIBATCH]
        out: dict[str, list[str]] = {}
        for unit, step in sorted(chosen):
            out.setdefault(unit, []).append(inst[(unit, step)][1])
        return {f"candidates/{parent}/eval/search/traces/{u}.jsonl": json.dumps({"type": "meta", "unit": u,
                "note": f"GEPA reflective minibatch: {len(lines)} of this unit's examples"}) + "\n" + "\n".join(lines)
                for u, lines in out.items()}

    @staticmethod
    def _files(view: dict[str, str], name: str, *, traces: str = "none") -> dict[str, str]:
        """Code, scores and meta of ``name``; ``traces``: none | full | artifacts (excerpted to OE_ARTIFACT_BYTES)."""
        pre = f"candidates/{name}/"
        out = {}
        tr = {p: t for p, t in view.items() if p.startswith(pre + "eval/search/traces/")}
        for p, t in view.items():
            if not p.startswith(pre):
                continue
            if "/src/" in p or p.endswith(("scores.json", "meta.json")):
                out[p] = t
            elif traces == "full" and ("/traces/" in p or "/per_task/" in p):
                out[p] = t
        if traces == "artifacts" and tr:
            share = max(1000, OE_ARTIFACT_BYTES // len(tr))
            for p, t in tr.items():
                out[p] = excerpt(t, share)
        return out

    def select(self, view: dict[str, str], iteration: int, slot: int, seed: int,
               extra_visits: Optional[dict[str, int]] = None) -> tuple[str, dict[str, str]]:
        """``(parent, sub-view)`` for one candidate slot."""
        progs = programs_in(view)
        if not progs:
            return "", {}
        rng = random.Random(f"{self.policy}|{self.seed}|{seed}|{iteration}|{slot}")
        n_prop = _n_proposed(progs) + slot
        sub: dict[str, str] = {}
        if self.policy == "openevolve":
            parent, ctx = self._openevolve(progs, n_prop, rng)
            sub.update(self._files(view, parent.name, traces="artifacts"))
            for p in ctx:
                sub.update(self._files(view, p.name))
        elif self.policy == "ttt_discover":
            parent = self._puct(progs, extra_visits or {})
            sub.update(self._files(view, parent.name))
        elif self.policy == "gepa_minibatch":
            parent = self._gepa_minibatch(view, progs, rng)
            sub.update(self._files(view, parent.name))
            sub.update(self._minibatch(view, parent.name, n_prop, seed))
        else:
            parent = self._gepa(progs, rng)
            sub.update(self._files(view, parent.name, traces="full"))
        sub[PARENT_HINT] = json.dumps({"parent": parent.name, "policy": self.policy})
        return parent.name, sub

    def propose(self, *, iteration, view, k, brief, artifacts, seed=0):
        out = ProposalBatch(meta={"policy": self.policy, "parents": []})
        usage = Usage()
        visits: dict[str, int] = {}
        known = {a.id: n for n, a in artifacts.items()}      # exact programs already in the store
        suffix = self.policy[:2] + ("m" if self.policy == "gepa_minibatch" else "")
        read, scanned, transcripts = [], [], []
        for slot in range(k):
            parent, sub = self.select(view, iteration, slot, seed, visits)
            if not parent:
                out.error = "no evaluated program in the view"
                break
            visits[parent] = visits.get(parent, 0) + 1
            names = _cand_names(sub)
            for attempt in range(1 + MAX_DEDUP_RETRIES):
                b = self.inner.propose(iteration=iteration, view=sub, k=1, brief=brief,
                                       artifacts={n: a for n, a in artifacts.items() if n in names},
                                       seed=seed * 1000 + slot + 7919 * attempt)
                usage = usage + b.usage
                dup = [c for c in b.candidates if c.artifact.id in known]
                if not dup:
                    break
                # an exact copy: show it (source only, unscored) and ask again, as a sampling LLM would not repeat
                out.meta["dedup_retries"] = out.meta.get("dedup_retries", 0) + 1
                for c in dup:
                    for rel, text in c.artifact.files.items():
                        sub[f"candidates/_seen_{known[c.artifact.id]}/src/{rel}"] = text
            for c in b.candidates:
                known.setdefault(c.artifact.id, f"new{slot}")
            for c in b.candidates:
                c.name = f"{c.name}_{suffix}{slot}"
                c.meta = {**(c.meta or {}), "policy": self.policy, "selected_parent": parent}
                if self.policy == "openevolve":
                    c.meta["island"] = (_n_proposed(programs_in(view)) + slot) % OE_ISLANDS
                out.candidates.append(c)
            out.meta["parents"].append(parent)
            read += [p for p in b.files_read if p != PARENT_HINT]
            scanned += list(getattr(b, "files_scanned", []) or [])
            transcripts.append(b.transcript or "")
            out.reports.update(b.reports or {})
            if b.error and not b.candidates:
                out.error = b.error
        out.usage = usage
        out.files_read = sorted(set(read))
        out.files_scanned = sorted(set(scanned) - set(out.files_read))
        out.transcript = "\n".join(transcripts)
        return out
