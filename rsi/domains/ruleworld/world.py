"""RuleWorld: a synthetic compound system whose accuracy depends on which general
rules its prompts contain (GEPA spec section 9.2, Tier 1).

The system is an order-fulfilment assistant with several *modules* (default
``triage`` then ``reply``), each driven by one prompt file ``prompts/<module>.md``.
Every example is a ticket from a customer *family* (retail, wholesale, ...) whose
order has 2-4 *aspects* (fragile, perishable, ...). Each aspect is handled by one
module and has a correct *handling protocol* (a rare code word such as ``cobalt``).

The simulated task model follows its module prompt literally:

* a **general rule** line ("When an order is fragile, use the cobalt protocol.")
  applies to every family; a **conditioned rule** line that names families
  ("For wholesale orders that are fragile, ...") applies only to them and
  overrides general rules for those families;
* several different applicable codes = a contradiction: the model picks one at
  random;
* no applicable rule: the model imitates matching **demos** (``Example: ...``
  lines) with probability ``p_demo`` per demo, else it uses the (always wrong)
  standard protocol;
* a line naming a ticket id is a **memorised fact** about that ticket only
  (the GEPA meta-prompt's "include niche facts" overfitting lure);
* prompts longer than ``capacity`` lines dilute adherence by ``dilution`` per extra
  line; each module slips (garbled output) with probability ``slip`` per rollout;
* optional **interference** (``n_interfering``): the *general* rule of an interfering
  aspect also gets applied to a victim aspect of the same module on tickets outside
  the interfering aspect's home family (the victim then shows a foreign protocol even
  though its own rule is present). Rules conditioned on families never bleed, and a
  conditioned rule for the victim overrides the bleed. This models prompt rules with
  hidden side effects - commitments a greedy search cannot easily undo.

*Conflicting* aspects need different codes for the two halves of the families:
a general rule helps half the instances and cannot help the rest, so per-instance
trade-offs (Pareto frontiers) exist until a conditioned rule is found.

Everything is analytic: :meth:`RuleWorld.expected` gives the exact expected score of
any prompt set, which experiments use as ground truth (never the optimizer).
"""
from __future__ import annotations

import hashlib
import random
import re
import threading
from dataclasses import dataclass, field, asdict
from typing import Optional, Sequence

ASPECTS = ["fragile", "perishable", "oversized", "hazardous", "international", "insured", "prepaid", "backordered",
           "refurbished", "bulk", "gift", "subscription", "rural", "express", "holiday", "warranty", "recalled",
           "customized", "lithium", "frozen", "liquid", "luxury", "returned", "embargoed"]
CODES = ["cobalt", "basalt", "obsidian", "saffron", "juniper", "cypress", "garnet", "jasper", "ochre", "umber",
         "sienna", "vermilion", "cerulean", "chartreuse", "magenta", "turquoise", "aquamarine", "periwinkle",
         "mahogany", "walnut", "sycamore", "hawthorn", "alder", "hemlock", "tamarack", "larch", "sequoia", "acacia",
         "banyan", "baobab", "ginkgo", "magnolia", "wisteria", "camellia", "gardenia", "hibiscus", "jasmine",
         "lavender", "marigold", "peony", "zinnia", "dahlia", "begonia", "azalea", "freesia", "primrose", "bluebell",
         "foxglove", "thistle", "fennel", "anise", "cardamom", "cumin", "nutmeg", "paprika", "tarragon", "oregano",
         "chervil", "sorrel", "quartz", "feldspar", "beryl", "zircon", "topaz", "onyx", "agate", "malachite",
         "lapis", "citrine", "peridot", "tourmaline", "spinel", "tanzanite", "amethyst", "carnelian", "hematite",
         "pyrite", "gypsum", "dolomite", "granite", "marble", "shale", "gneiss", "schist", "pumice", "tungsten",
         "nickel", "bismuth", "iridium", "osmium", "rhodium", "palladium", "titanium", "vanadium", "chromium",
         "gallium", "indium", "hafnium", "niobium", "yttrium"]
FAMILIES = ["retail", "wholesale", "government", "nonprofit", "education", "medical"]
STANDARD = "standard"
GARBLED = "unclear"
TICKET_RE = re.compile(r"\bT-\d{4}\b")

SEED_TEXT = ("You are the {module} module of an order-fulfilment assistant. For each order property you handle, "
             "output '<property>: <protocol>'.\nUse the standard protocol when no rule applies.\n")


@dataclass
class WorldConfig:
    """World generator settings. Defaults give a 2-module world with 16 aspects,
    2 of them conflicting, 4 customer families and noisy rollouts."""

    modules: tuple[str, ...] = ("triage", "reply")
    n_aspects: int = 16
    n_families: int = 4
    n_conflict: int = 2              # aspects whose correct code differs between family halves
    n_variants: int = 3              # candidate protocols per aspect (1-2 correct, rest decoys)
    aspects_per_example: tuple[int, int] = (2, 4)
    family_bias: float = 0.75        # P(an aspect slot is drawn from the family's home pool)
    p_conflict: float = 0.5          # P(an example includes each conflict aspect)
    slip: float = 0.05               # per-module garbling probability per rollout
    capacity: int = 16               # prompt lines per module before dilution
    dilution: float = 0.02           # adherence loss per line beyond capacity
    p_demo: float = 0.35             # P(imitating one matching demo)
    n_interfering: int = 0           # aspects whose GENERAL rule bleeds into a victim aspect (hidden side effect)
    scoring: str = "partial"         # partial: fraction of aspects right | binary: all right
    n_train: int = 30
    n_val: int = 30
    n_test: int = 300
    seed: int = 0

    def to_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Aspect:
    name: str
    module: str
    home: Optional[int]                     # home family index (None for conflict aspects)
    codes: tuple[str, ...]                  # the lexicon variants (decoys included)
    conflict: bool = False

    def correct(self, family_idx: int, n_families: int) -> str:
        if not self.conflict:
            return self.codes[0]
        return self.codes[0] if family_idx < n_families // 2 else self.codes[1]


@dataclass
class Example:
    id: str
    ticket: str
    family: str
    aspects: tuple[str, ...]
    target: dict                             # aspect -> correct code
    text: str

    @property
    def target_str(self) -> str:
        return "; ".join(f"{a}: {c}" for a, c in self.target.items())


@dataclass
class ModuleView:
    """What the simulated model 'understands' from one module prompt."""

    rules: dict = field(default_factory=dict)    # aspect -> list[(code, frozenset(families))]
    facts: dict = field(default_factory=dict)    # ticket -> {aspect: code}
    demos: list = field(default_factory=list)    # list[(family or None, {aspect: code})]
    n_lines: int = 0


def _h(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:16], 16))


class RuleWorld:
    """Lexicon + tasks + simulated task model + analytic ground truth."""

    def __init__(self, cfg: Optional[WorldConfig] = None) -> None:
        self.cfg = cfg = cfg or WorldConfig()
        if cfg.n_aspects > len(ASPECTS) or cfg.n_families > len(FAMILIES):
            raise ValueError("too many aspects or families for the lexicon")
        if cfg.n_aspects * cfg.n_variants > len(CODES):
            raise ValueError("lexicon has too few protocol codes")
        rng = random.Random(f"ruleworld-{cfg.seed}")
        names = list(ASPECTS)
        rng.shuffle(names)
        names = names[: cfg.n_aspects]
        codes = list(CODES)
        rng.shuffle(codes)
        self.families = FAMILIES[: cfg.n_families]
        self.aspects: dict[str, Aspect] = {}
        for i, name in enumerate(names):
            conflict = i < cfg.n_conflict
            home = None if conflict else (i - cfg.n_conflict) % cfg.n_families
            self.aspects[name] = Aspect(name, cfg.modules[i % len(cfg.modules)], home,
                                        tuple(codes[i * cfg.n_variants:(i + 1) * cfg.n_variants]), conflict)
        self.code_owner = {c: a.name for a in self.aspects.values() for c in a.codes}
        # interference pairs: interfering aspect -> victim (same module, different home family)
        self.bleeds: dict[str, str] = {}
        normal = [a for a in self.aspects.values() if not a.conflict]
        for a in normal[: cfg.n_interfering]:
            victims = [b.name for b in normal if b.module == a.module and b.home != a.home
                       and b.name not in self.bleeds and b.name not in self.bleeds.values()]
            if victims:
                self.bleeds[a.name] = rng.choice(victims)
        self.victim_of = {v: a for a, v in self.bleeds.items()}
        self._code_re = re.compile(r"\b(" + "|".join(sorted(self.code_owner, key=len, reverse=True)) + r")\b")
        self._fam_re = re.compile(r"\b(" + "|".join(self.families) + r")\b")
        self._aspect_re = re.compile("(" + "|".join(sorted(self.aspects, key=len, reverse=True)) + ")")
        self.examples: dict[str, Example] = {}
        self.splits: dict[str, list[str]] = {}
        used_tickets: set[str] = set()
        for split, n in (("evolve", cfg.n_train), ("val", cfg.n_val), ("test", cfg.n_test)):
            srng = random.Random(f"ruleworld-{cfg.seed}-{split}")
            self.splits[split] = []
            for j in range(n):
                ex = self._make_example(f"rw-{split}-{j:03d}", srng, used_tickets)
                self.examples[ex.id] = ex
                self.splits[split].append(ex.id)
        self._parse_cache: dict[str, ModuleView] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ lexicon --
    def module_path(self, module: str) -> str:
        return f"prompts/{module}.md"

    def rule_text(self, aspect: str, code: str, families: Sequence[str] = ()) -> str:
        if families:
            fams = " and ".join(families)
            return f"For {fams} orders that are {aspect}, use the {code} protocol."
        return f"When an order is {aspect}, use the {code} protocol."

    def aspects_of(self, module: str) -> list[str]:
        return [a for a, x in self.aspects.items() if x.module == module]

    def family_index(self, family: str) -> int:
        return self.families.index(family)

    # ----------------------------------------------------------------- examples --
    def _make_example(self, eid: str, rng: random.Random, used: set) -> Example:
        cfg = self.cfg
        fam_i = rng.randrange(cfg.n_families)
        family = self.families[fam_i]
        normal = [a for a in self.aspects.values() if not a.conflict]
        home = [a.name for a in normal if a.home == fam_i]
        chosen: list[str] = []
        k = rng.randint(*cfg.aspects_per_example)
        tries = 0
        while len(chosen) < k and tries < 50:
            tries += 1
            pool = home if (home and rng.random() < cfg.family_bias) else [a.name for a in normal]
            a = rng.choice(pool)
            if a not in chosen:
                chosen.append(a)
        for a in self.aspects.values():
            if a.conflict and rng.random() < cfg.p_conflict:
                chosen.append(a.name)
        order = list(self.aspects)
        chosen.sort(key=lambda a: (cfg.modules.index(self.aspects[a].module), order.index(a)))
        while True:
            ticket = f"T-{rng.randrange(1000, 10000)}"
            if ticket not in used:
                used.add(ticket)
                break
        target = {a: self.aspects[a].correct(fam_i, cfg.n_families) for a in chosen}
        props = ", ".join(chosen[:-1]) + (" and " if len(chosen) > 1 else "") + chosen[-1]
        text = (f"Ticket {ticket} ({family} customer): the order is {props}. "
                f"Choose the handling protocol for each order property.")
        return Example(eid, ticket, family, tuple(chosen), target, text)

    # ------------------------------------------------------------------ parsing --
    def parse_module(self, text: str) -> ModuleView:
        with self._lock:
            v = self._parse_cache.get(text)
        if v is not None:
            return v
        view = ModuleView()
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            view.n_lines += 1
            low = line.lower()
            codes = self._code_re.findall(low)
            tickets = TICKET_RE.findall(line)
            if tickets:
                pairs = {self.code_owner[c]: c for c in codes}
                for t in tickets:
                    view.facts.setdefault(t, {}).update(pairs)
                if low.startswith("example") and pairs:
                    fams = self._fam_re.findall(low)
                    view.demos.append((fams[0] if fams else None, pairs))
                continue
            if not codes:
                continue
            fams = frozenset(self._fam_re.findall(low))
            present = set(self._aspect_re.findall(low))
            for c in codes:
                a = self.code_owner[c]
                if a in present:
                    view.rules.setdefault(a, []).append((c, fams))
        with self._lock:
            if len(self._parse_cache) > 50000:
                self._parse_cache.clear()
            self._parse_cache[text] = view
        return view

    # ------------------------------------------------------------ task model ----
    def outcome_dist(self, view: ModuleView, ex: Example, aspect: str) -> tuple[list[tuple[str, float]], str]:
        """Distribution over the code the model outputs for ``aspect`` (before
        dilution and slip), plus a short reason string for the trace."""
        fact = view.facts.get(ex.ticket, {}).get(aspect)
        if fact is not None:
            return [(fact, 1.0)], "recalled ticket fact"
        rules = view.rules.get(aspect, [])
        specific = sorted({c for c, fams in rules if ex.family in fams})
        general = sorted({c for c, fams in rules if not fams})
        src = self.victim_of.get(aspect)
        if src is not None and not specific and ex.family != self.families[self.aspects[src].home]:
            bleed = sorted({c for c, fams in view.rules.get(src, []) if not fams})
            if bleed:
                return [(bleed[0], 1.0)], f"applied the {src} rule by mistake"
        codes = specific or general
        if codes:
            why = ("conditioned rule" if specific else "general rule") + (" (contradictory rules)" if len(codes) > 1
                                                                            else "")
            return [(c, 1.0 / len(codes)) for c in codes], why
        demo_codes = [pairs[aspect] for fam, pairs in view.demos if aspect in pairs]
        if demo_codes:
            p_imitate = 1.0 - (1.0 - self.cfg.p_demo) ** len(demo_codes)
            dist = [(c, p_imitate / len(demo_codes)) for c in demo_codes]
            return dist + [(STANDARD, 1.0 - p_imitate)], "imitating demos"
        return [(STANDARD, 1.0)], "no applicable rule"

    def dilution_factor(self, view: ModuleView) -> float:
        extra = max(0, view.n_lines - self.cfg.capacity)
        return (1.0 - self.cfg.dilution) ** extra

    def views(self, files: dict) -> dict[str, ModuleView]:
        return {m: self.parse_module(files.get(self.module_path(m), "")) for m in self.cfg.modules}

    def p_correct(self, views: dict, ex: Example) -> dict[str, float]:
        """Exact P(aspect correct) for every aspect of ``ex``."""
        out = {}
        for a in ex.aspects:
            m = self.aspects[a].module
            dist, _ = self.outcome_dist(views[m], ex, a)
            p = sum(pr for c, pr in dist if c == ex.target[a])
            out[a] = (1.0 - self.cfg.slip) * self.dilution_factor(views[m]) * p
        return out

    def expected_example(self, views: dict, ex: Example) -> float:
        pc = self.p_correct(views, ex)
        if self.cfg.scoring == "partial":
            return float(sum(pc.values()) / len(pc))
        prob = 1.0
        for m in self.cfg.modules:
            asp = [a for a in ex.aspects if self.aspects[a].module == m]
            if not asp:
                continue
            q = 1.0
            for a in asp:
                q *= pc[a] / (1.0 - self.cfg.slip) if self.cfg.slip < 1 else 0.0
            prob *= (1.0 - self.cfg.slip) * q
        return float(prob)

    def expected(self, files: dict, ids: Sequence[str]) -> float:
        """Exact expected score of prompt set ``files`` on examples ``ids``."""
        vs = self.views(files)
        vals = [self.expected_example(vs, self.examples[i]) for i in ids]
        return float(sum(vals) / len(vals)) if vals else 0.0

    def simulate(self, files: dict, ex: Example, seed: int, salt: str = "") -> tuple[dict, str]:
        """One noisy rollout: returns ({aspect: code}, trace)."""
        vs = self.views(files)
        rng = _h("rw", seed, ex.id, salt)
        out: dict[str, str] = {}
        lines = []
        for m in self.cfg.modules:
            asp = [a for a in ex.aspects if self.aspects[a].module == m]
            slipped = rng.random() < self.cfg.slip
            dil = self.dilution_factor(vs[m])
            decisions = []
            for a in asp:
                dist, why = self.outcome_dist(vs[m], ex, a)
                u = rng.random()
                acc, code = 0.0, dist[-1][0]
                for c, pr in dist:
                    acc += pr
                    if u < acc:
                        code = c
                        break
                if rng.random() >= dil:
                    code, why = STANDARD, "rule ignored (prompt too long)"
                if slipped:
                    code, why = GARBLED, "output garbled"
                out[a] = code
                decisions.append(f"{a} -> {code} ({why})")
            upstream = "" if m == self.cfg.modules[0] else " | upstream notes: " + "; ".join(
                f"{a}: {out[a]}" for a in ex.aspects if self.aspects[a].module != m and a in out)
            lines.append(f"[{m}] prompt lines={vs[m].n_lines}{upstream}\n[{m}] decisions: " +
                         ("; ".join(decisions) if decisions else "(nothing to handle)"))
        return out, "\n".join(lines)

    # ---------------------------------------------------------------- helpers ---
    def seed_files(self) -> dict[str, str]:
        return {self.module_path(m): SEED_TEXT.format(module=m) for m in self.cfg.modules}

    def oracle_files(self, conditioned: bool = True) -> dict[str, str]:
        """Prompt set with every correct rule (conflicts resolved by conditioned rules)."""
        files = self.seed_files()
        half = self.cfg.n_families // 2
        for m in self.cfg.modules:
            lines = [files[self.module_path(m)].rstrip("\n")]
            for a in self.aspects_of(m):
                asp = self.aspects[a]
                if a in self.bleeds and conditioned:        # scoped so it cannot bleed
                    lines.append(self.rule_text(a, asp.codes[0], self.families))
                elif not asp.conflict:
                    lines.append(self.rule_text(a, asp.codes[0]))
                elif conditioned:
                    lines.append(self.rule_text(a, asp.codes[0], self.families[:half]))
                    lines.append(self.rule_text(a, asp.codes[1], self.families[half:]))
                else:
                    lines.append(self.rule_text(a, asp.codes[0]))
            files[self.module_path(m)] = "\n".join(lines) + "\n"
        return files

    def vocabulary(self, module: str) -> list[str]:
        """Every rule line a policy could write for ``module`` (general rules for all
        variants + conditioned variants of conflict aspects for each family half)."""
        half = self.cfg.n_families // 2
        out = []
        for a in self.aspects_of(module):
            asp = self.aspects[a]
            for c in asp.codes:
                out.append(self.rule_text(a, c))
            if asp.conflict:
                for fams in (self.families[:half], self.families[half:]):
                    for c in asp.codes:
                        out.append(self.rule_text(a, c, fams))
        return out

    def count_facts(self, files: dict) -> int:
        """Number of ticket-specific lines (memorised facts / demos) in a prompt set."""
        return sum(len(TICKET_RE.findall(t)) for t in files.values())
