"""HarnessWorld: a synthetic harness-evolution world with analytic ground truth.

A *harness* is a set of *mechanisms* (one file each). Every candidate mechanism in the
world's catalog has

* a component (RRSI's K: prompt, control_flow, config, output_plumbing, context_mgmt,
  client_tool, skill, memory, subagent) and a file path derived from it;
* a true **generic effect** per task family (logit units; families shared by the
  evolve/holdout splits and shifted OOD families);
* a **suite-specific (trick) effect** that only helps listed evolve tasks - only while
  the mechanism's leak payload (a lookup table keyed by evolve task ids / answers, or an
  obfuscated hash table) is present in its file;
* a multiplicative **token cost**;
* possibly **decaying usefulness**: its generic effect vanishes once a superseding
  mechanism is present (so it keeps costing tokens after it stopped helping).

The frozen policy solves task x with per-criterion success probability
``p = sigmoid(a_pi - d_x + sum_m e_m(x))``; a trial's reward is the fraction of
``n_criteria`` rubric criteria passed (Binomial), its tokens are
``base * prod(cost_m) * lognormal`` (mean-preserving). Ground truth ``E[S]`` and
``E[C]`` are analytic (:meth:`World.expected`).

Mechanism kinds in the catalog (the mock proposer draws from them with set shares):
``generic``, ``structural`` (K_str components, larger effects), ``narrow`` (practice
families only: transfers to holdout, not OOD), ``leak`` (literal evolve ids/answers in
the text: the regex denylist catches it), ``obfuscated_leak`` (hash-keyed: only an LLM
critic can catch it), ``null``, ``costly`` (small gain, large cost), ``harmful``
(plausible-looking, negative effect: hitchhikers), ``decaying`` (+ its superseders).
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field, replace
from typing import Optional

import numpy as np

K = ["prompt", "control_flow", "config", "output_plumbing", "context_mgmt", "client_tool", "skill", "memory",
     "subagent"]
STRUCTURAL = ("client_tool", "skill", "memory", "subagent")
NON_STRUCTURAL = ("prompt", "control_flow", "config", "output_plumbing", "context_mgmt")
COMPONENT_PATH = {"prompt": "prompts/{id}.md", "control_flow": "control/{id}.py", "config": "config/{id}.py",
                  "output_plumbing": "output/{id}.py", "context_mgmt": "context/{id}.py",
                  "client_tool": "tools/{id}.py", "skill": "skills/{id}/SKILL.md", "memory": "memory/{id}.py",
                  "subagent": "agents/{id}.py"}
KINDS = ("generic", "structural", "narrow", "leak", "obfuscated_leak", "null", "costly", "harmful", "decaying")

_TITLES = {
    "prompt": ["Restate the goal before acting", "Explicit output contract", "Checklist of constraints",
               "Ask for units and formats", "Short worked example of the procedure", "Concise reasoning directive",
               "Role and scope statement", "Error-prone step warnings"],
    "control_flow": ["Bounded self-verification pass", "Plan-then-act split", "Retry once on malformed output",
                     "Early exit when the answer is certain", "Two-phase draft and finalize", "Stop-condition guard"],
    "config": ["Raise the per-step output cap", "Lower sampling temperature for final answers",
               "Longer tool timeout", "Tighter step limit"],
    "output_plumbing": ["Head/tail truncation of long tool output", "Structured result parsing",
                        "Collapse repeated log lines", "Answer-line extractor"],
    "context_mgmt": ["Summarize old turns", "Keep the last N tool results verbatim", "Pin the task statement",
                     "Drop stale observations"],
    "client_tool": ["Deterministic calculator tool", "Unit-conversion tool", "Structured search tool",
                    "Code runner tool", "Date arithmetic tool"],
    "skill": ["Procedure card: decomposition", "Procedure card: verification", "Procedure card: parsing",
              "Procedure card: estimation"],
    "memory": ["Episodic notes of general lessons", "Scratchpad for intermediate results",
               "Semantic memory of general procedures"],
    "subagent": ["Bounded checker sub-call", "Planner sub-call", "Critic sub-call on final answer"],
}


@dataclass
class Mechanism:
    id: str
    kind: str
    component: str
    title: str
    description: str
    effect: dict                      # family -> logit effect (generic part)
    cost: float = 1.0                 # multiplicative token factor
    trick: float = 0.0                # logit effect on leak_keys (evolve tasks) while the payload is present
    leak_keys: tuple = ()             # evolve task ids helped by the trick
    payload: str = ""                 # the leak line (literal ids/answers or hash table)
    superseded_by: tuple = ()         # generic effect -> decay_to * effect once any of these is present
    decay_to: float = 0.0
    tool_boost: float = 1.0           # multiplier on tool-surface tasks
    targets: tuple = ()               # families the description says it helps

    @property
    def path(self) -> str:
        return COMPONENT_PATH[self.component].format(id=self.id)

    @property
    def enable_line(self) -> str:
        return f'enable("{self.id}")'

    def file_text(self) -> str:
        lines = [f"# mechanism: {self.id}", f"# component: {self.component}", f"# title: {self.title}",
                 f'"""{self.description}"""']
        if self.payload:
            lines.append(self.payload)
        lines.append(self.enable_line)
        return "\n".join(lines) + "\n"

    def catalog_row(self) -> dict:
        return {"id": self.id, "component": self.component, "title": self.title, "description": self.description,
                "path": self.path}


@dataclass
class WorldConfig:
    seed: int = 0
    practice_families: tuple = ("extract", "arith", "plan", "format")
    ood_families: tuple = ("units", "code", "search", "dates")
    n_evolve: int = 100
    n_holdout: int = 100
    n_ood_per_family: int = 25
    n_criteria: int = 16
    difficulty_mean: float = 0.3
    difficulty_sd: float = 1.0
    ood_shift: float = 0.4
    tool_surface_p_practice: float = 0.2
    tool_surface_p_ood: float = 0.6
    base_tokens: float = 2000.0
    token_noise_sd: float = 0.25
    # catalog composition (number of mechanisms of each kind)
    n_generic: int = 20
    n_structural: int = 12
    n_narrow: int = 8
    n_leak: int = 12
    n_obfuscated: int = 6
    n_null: int = 16
    n_costly: int = 10
    n_harmful: int = 20
    n_decaying: int = 6
    # effect scales (logit units): (mean, sd) of a mechanism's base effect on a covered family
    generic_effect: tuple = (0.12, 0.04)
    structural_effect: tuple = (0.20, 0.05)
    narrow_effect: tuple = (0.12, 0.03)
    decaying_effect: tuple = (0.16, 0.04)
    costly_effect: tuple = (0.05, 0.02)
    harmful_effect: tuple = (-0.15, 0.05)
    null_effect_sd: float = 0.01
    trick_effect: tuple = (1.5, 0.25)
    leak_fraction: tuple = (0.12, 0.25)       # share of evolve tasks a leak helps
    family_coverage: float = 0.6              # P(a generic mechanism helps a given family)
    structural_coverage: float = 0.7
    # extra token cost ranges U(lo, hi): a mechanism multiplies tokens by 1 + U(lo, hi)
    generic_cost: tuple = (0.0, 0.06)
    structural_cost: tuple = (0.03, 0.12)
    narrow_cost: tuple = (0.0, 0.04)
    leak_cost: tuple = (0.01, 0.05)
    null_cost: tuple = (0.0, 0.02)
    costly_cost: tuple = (0.3, 0.8)
    harmful_cost: tuple = (0.0, 0.05)
    decaying_cost: tuple = (0.04, 0.10)
    leak_offtarget: tuple = (-0.06, 0.03)     # generic effect of a leak (a lookup table is distracting context)
    narrow_offtarget: float = -0.04           # practice-tuned instructions confuse never-seen families
    context_penalty: float = 0.15             # logit penalty per unit log(cost factor): a bigger harness dilutes
                                              # the policy's context (set 0 to make complexity cost tokens only)
    decaying_components: tuple = ("prompt", "prompt", "context_mgmt", "output_plumbing")
    seed_mechanisms: int = 2                  # generic mechanisms already in H_0
    # overrides by kind (e.g. {"prompt": 0.0} to exhaust non-structural gains in E9)
    nonstructural_effect_scale: float = 1.0


@dataclass
class Policy:
    """The frozen policy: strength a_pi and per-component sensitivity to mechanisms."""

    name: str = "strong"
    strength: float = 0.0
    component_scale: dict = field(default_factory=dict)

    def scale(self, component: str) -> float:
        return self.component_scale.get(component, 1.0)


STRONG = Policy("strong", 0.0, {})
#: a weaker policy never used in the search: lower base skill, gains more from tools,
#: less from textual skills, hurt by sub-calls ("sub-calls tend to HURT on small policies").
WEAK = Policy("weak", -1.0, {"subagent": -0.5, "skill": 0.5, "client_tool": 1.3, "memory": 0.8, "prompt": 0.9,
                             "control_flow": 0.9, "config": 0.8, "output_plumbing": 1.0, "context_mgmt": 1.0})


@dataclass
class HWTask:
    id: str
    split: str
    family: str
    surface: str
    difficulty: float
    target: str
    text: str


def _h(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


class World:
    """Tasks + mechanism catalog of one HarnessWorld instance (deterministic in ``cfg.seed``)."""

    def __init__(self, cfg: Optional[WorldConfig] = None) -> None:
        self.cfg = cfg or WorldConfig()
        self.rng = random.Random(f"hw-{self.cfg.seed}")
        self.families = tuple(self.cfg.practice_families) + tuple(self.cfg.ood_families)
        self.tasks: list[HWTask] = []
        self._make_tasks()
        self.catalog: dict[str, Mechanism] = {}
        self._make_catalog()

    # ---------------------------------------------------------------- tasks --
    def _make_tasks(self) -> None:
        c, rng = self.cfg, self.rng
        pf, of = c.practice_families, c.ood_families
        for split, n, fams, shift, ptool, prefix in (("evolve", c.n_evolve, pf, 0.0, c.tool_surface_p_practice, "e"),
                                                     ("holdout", c.n_holdout, pf, 0.0, c.tool_surface_p_practice, "h"),
                                                     ("ood", c.n_ood_per_family * len(of), of, c.ood_shift,
                                                      c.tool_surface_p_ood, "o")):
            for i in range(n):
                fam = fams[i % len(fams)]
                surface = "tool" if rng.random() < ptool else "text"
                d = rng.gauss(c.difficulty_mean + shift, c.difficulty_sd)
                tid = f"hw-{prefix}-{i:03d}"
                target = "ans-" + hashlib.sha1(f"{c.seed}|{tid}".encode()).hexdigest()[:6]
                text = (f"[{fam}/{surface}] Task {tid}: produce a deliverable for a {fam} problem "
                        f"(instance {hashlib.md5(tid.encode()).hexdigest()[:5]}).")
                self.tasks.append(HWTask(tid, split, fam, surface, d, target, text))
        self.by_id = {t.id: t for t in self.tasks}

    def split_ids(self, split: str) -> list[str]:
        return [t.id for t in self.tasks if t.split == split]

    # -------------------------------------------------------------- catalog --
    def _effects(self, base: float, coverage: float, fams: tuple) -> dict:
        rng = self.rng
        eff = {}
        for f in self.families:
            if f in fams and rng.random() < coverage:
                eff[f] = base * rng.uniform(0.5, 1.5)
            else:
                eff[f] = rng.gauss(0.0, 0.01)
        if all(abs(eff[f]) < 0.02 for f in fams):                   # guarantee at least one target family
            f = rng.choice(list(fams))
            eff[f] = base
        return eff

    def _add(self, m: Mechanism) -> None:
        self.catalog[m.id] = m

    def _title(self, comp: str) -> str:
        return self.rng.choice(_TITLES[comp])

    def _make_catalog(self) -> None:
        c, rng = self.cfg, self.rng
        ev_ids = self.split_ids("evolve")
        nsx = c.nonstructural_effect_scale

        def comp_ns():
            return rng.choices(NON_STRUCTURAL, weights=[0.4, 0.2, 0.1, 0.15, 0.15])[0]

        def mk(kind, i, comp, base, coverage, fams, cost, **kw):
            eff = self._effects(base, coverage, fams)
            targets = tuple(f for f in fams if abs(eff[f]) >= 0.02)
            title = self._title(comp)
            # the text a proposer reads names practice families only: which mechanisms help the sealed OOD
            # families is ground truth about a split the loop must never see
            shown = [f for f in targets if f in c.practice_families]
            desc = kw.pop("desc", None) or (f"{title}; aimed at {', '.join(shown) or 'general'} tasks.")
            m = Mechanism(f"{kind[:3]}_{i:02d}" if kind not in ("obfuscated_leak",) else f"obf_{i:02d}", kind, comp,
                          title, desc, eff, cost=cost, targets=targets, **kw)
            self._add(m)
            return m

        allf = self.families
        for i in range(c.n_generic):
            mk("generic", i, comp_ns(), nsx * rng.gauss(*c.generic_effect), c.family_coverage, allf,
               1.0 + rng.uniform(*c.generic_cost))
        for i in range(c.n_structural):
            comp = rng.choice(STRUCTURAL)
            mk("structural", i, comp, rng.gauss(*c.structural_effect), c.structural_coverage, allf,
               1.0 + rng.uniform(*c.structural_cost), tool_boost=1.5 if comp == "client_tool" else 1.0)
        for i in range(c.n_narrow):
            m = mk("narrow", i, comp_ns(), nsx * rng.gauss(*c.narrow_effect), 0.8, tuple(c.practice_families),
                   1.0 + rng.uniform(*c.narrow_cost))
            for f in c.ood_families:
                m.effect[f] = c.narrow_offtarget + rng.gauss(0.0, 0.01)
        for i in range(c.n_leak + c.n_obfuscated):
            obf = i >= c.n_leak
            comp = rng.choice(["memory", "skill", "prompt", "control_flow"])
            frac = rng.uniform(*c.leak_fraction)
            keys = tuple(sorted(rng.sample(ev_ids, max(1, int(round(frac * len(ev_ids)))))))
            if obf:
                table = ", ".join(f'"{hashlib.sha1(self.by_id[k].text.encode()).hexdigest()[:8]}": '
                                  f'{_h(k) % 97}' for k in keys)
                payload = "SHORTCUTS = {" + table + "}"
                desc = "Reuse precomputed shortcuts for recurring problem shapes."
                title = "Shortcut table"
            else:
                table = ", ".join(f'"{k}": "{self.by_id[k].target}"' for k in keys)
                payload = "ANSWERS = {" + table + "}"
                desc = "Return remembered answers for tasks seen during practice."
                title = "Practice answer cache"
            eff = {f: rng.gauss(*c.leak_offtarget) for f in allf}
            m = Mechanism(f"obf_{i - c.n_leak:02d}" if obf else f"lea_{i:02d}", "obfuscated_leak" if obf else "leak",
                          comp, title, desc, eff, cost=1.0 + rng.uniform(*c.leak_cost),
                          trick=rng.gauss(*c.trick_effect), leak_keys=keys, payload=payload,
                          targets=tuple(c.practice_families))
            self._add(m)
        for i in range(c.n_null):
            comp = comp_ns()
            title = self._title(comp)
            self._add(Mechanism(f"nul_{i:02d}", "null", comp, title, f"{title} (rewording of existing guidance).",
                                {f: rng.gauss(0.0, c.null_effect_sd) for f in allf}, cost=1.0 + rng.uniform(*c.null_cost),
                                targets=tuple(rng.sample(list(allf), 2))))
        for i in range(c.n_costly):
            comp = rng.choice(["control_flow", "subagent", "prompt", "context_mgmt"])
            mk("costly", i, comp, rng.gauss(*c.costly_effect), 0.6, allf, 1.0 + rng.uniform(*c.costly_cost),
               desc=f"{self._title(comp)}; runs extra model passes (self-consistency / long reasoning).")
        for i in range(c.n_harmful):
            comp = rng.choice(K)
            mk("harmful", i, comp, rng.gauss(*c.harmful_effect), 0.7, allf, 1.0 + rng.uniform(*c.harmful_cost))
        # decaying mechanisms: useful until a superseding (generic / structural) mechanism is present
        sup_pool = [m for m in self.catalog.values() if m.kind in ("generic", "structural")]
        for i in range(c.n_decaying):
            comp = rng.choice(list(c.decaying_components))
            m = mk("decaying", i, comp, nsx * rng.gauss(*c.decaying_effect), 0.8, allf, 1.0 + rng.uniform(*c.decaying_cost))
            overlap = [s for s in sup_pool if set(s.targets) & set(m.targets)] or sup_pool
            sups = tuple(sorted(s.id for s in rng.sample(overlap, min(2, len(overlap)))))
            self.catalog[m.id] = replace(m, superseded_by=sups, description=m.description +
                                         " (a stop-gap until a dedicated mechanism exists)")
        # H_0: a few generic mechanisms are already in the seed harness
        self.seed_ids = tuple(sorted(rng.sample([m.id for m in self.catalog.values() if m.kind == "generic"],
                                                min(c.seed_mechanisms, c.n_generic))))

    # -------------------------------------------------------------- scoring --
    def active(self, files: dict) -> tuple[list[Mechanism], list[str], float]:
        """(active catalog mechanisms, mechanisms whose leak payload is present, free-text extra cost)."""
        mechs, payload, extra = [], [], 0.0
        for path, text in files.items():
            if path == "harness.md":
                continue
            mid = None
            for line in text.splitlines()[:3]:
                if line.startswith("# mechanism:"):
                    mid = line.split(":", 1)[1].strip()
                    break
            m = self.catalog.get(mid) if mid else None
            if m is not None and m.enable_line in text and m not in mechs:
                mechs.append(m)
                if m.payload and m.payload in text:
                    payload.append(m.id)
            else:
                extra += len(text) / 20000.0                          # free text: no effect, costs context tokens
        return mechs, payload, extra

    # vectorised ground truth: per-mechanism effect vectors over all tasks (cached)
    def _vectors(self, m: Mechanism) -> tuple:
        cache = self.__dict__.setdefault("_vec_cache", {})
        v = cache.get(m.id)
        if v is None:
            eff = np.array([m.effect.get(t.family, 0.0) * (m.tool_boost if t.surface == "tool" else 1.0)
                            for t in self.tasks])
            trick = np.array([m.trick if t.id in m.leak_keys else 0.0 for t in self.tasks])
            v = cache[m.id] = (eff, trick)
        return v

    def logits(self, files: dict, policy: Policy = STRONG) -> tuple[dict, float, list[Mechanism]]:
        """Per-task logit and the harness cost factor."""
        z, cost, mechs = self.logit_array(files, policy)
        return dict(zip(self._ids, z.tolist())), cost, mechs

    def logit_array(self, files: dict, policy: Policy = STRONG):
        mechs, payload, extra = self.active(files)
        if not hasattr(self, "_ids"):
            self._ids = [t.id for t in self.tasks]
            self._neg_d = np.array([-t.difficulty for t in self.tasks])
            self._split_idx = {s: np.array([i for i, t in enumerate(self.tasks) if t.split == s], dtype=int)
                               for s in ("evolve", "holdout", "ood")}
        ids = {m.id for m in mechs}
        cost = math.prod(m.cost for m in mechs) * (1.0 + extra)
        dilution = self.cfg.context_penalty * math.log(cost) if cost > 0 else 0.0
        z = policy.strength + self._neg_d - dilution
        for m in mechs:
            eff, trick = self._vectors(m)
            f = policy.scale(m.component)
            if m.superseded_by and ids.intersection(m.superseded_by):
                f *= m.decay_to
            if f:
                z = z + eff * f
            if m.id in payload:
                z = z + trick
        return z, cost, mechs

    def expected(self, files: dict, split: str, policy: Policy = STRONG) -> dict:
        """Analytic E[S] (mean criterion pass probability) and E[C] (tokens per trial)."""
        z, cost, mechs = self.logit_array(files, policy)
        idx = self._split_idx.get(split)
        if idx is None:
            idx = np.array([i for i, t in enumerate(self.tasks) if t.split == split], dtype=int)
        p = 1.0 / (1.0 + np.exp(-z[idx])) if len(idx) else np.array([])
        return {"S": float(p.mean()) if len(p) else 0.0, "C": self.cfg.base_tokens * cost, "n_mechanisms": len(mechs)}

    def seed_files(self) -> dict:
        files = {"harness.md": ("# HarnessWorld harness\n\nA harness is the set of mechanism files below. Each file "
                                "declares `# mechanism: <id>` and is enabled by its `enable(\"<id>\")` line.\n")}
        for mid in self.seed_ids:
            m = self.catalog[mid]
            files[m.path] = m.file_text()
        return files

    def kind_of(self, mid: str) -> Optional[str]:
        m = self.catalog.get(mid)
        return m.kind if m else None
