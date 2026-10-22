"""Shared helpers for the SoL-Pi experiment scripts (S1-S7) on AgentWorld."""
from __future__ import annotations

from collections.abc import Callable

import numpy as np

from _common import live_llm, paired, parse_args, summarize
from rsi.core import Evaluator
from rsi.domains.agentworld import MockAgentLLM, make_domain
from rsi.solpi import AGENTWORLD_IDEAS
from rsi.solpi.meter import PRICES

SPLITS = ("evolve", "ood")          # training families + held-out families (report-only measurement)
TINY = {"n_train": 1, "n_final": 1, "n_accept": 1, "n_test": 0, "subtasks": (2, 2), "max_turns": 30}
IDEAS = {i.id: i for i in AGENTWORLD_IDEAS}
KIND = {i.id: i.kind for i in AGENTWORLD_IDEAS}
POWER_KEYS = ("A_nofw_capability", "B_nofw_capability", "A_fw_capability", "B_fw_capability", "A_nofw_eta_saving",
              "B_nofw_eta_saving", "A_fw_eta_saving", "B_fw_eta_saving")


def parse_seeded(desc: str, default_seeds: int, extra: Callable | None = None) -> tuple:
    """``parse_args`` plus ``--seed-offset``; returns (args, seed list)."""
    def add(ap):
        ap.add_argument("--seed-offset", type=int, default=10)
        if extra:
            extra(ap)
    a = parse_args(desc, default_seeds=default_seeds, extra=add)
    return a, list(range(a.seed_offset, a.seed_offset + a.seeds))


def backend(args, profile: str):
    """Offline MockAgent profile, or a live LLM agent (tiny runs only)."""
    return live_llm(args.llm) if args.live else MockAgentLLM(profile)


def backends(args) -> list[str]:
    return ["A"] if args.live else ["A", "B"]


def domain(args, seed: int, **kw):
    return make_domain(seed=seed, **({**kw, **TINY} if args.live else kw))


def trials_of(dom, llm, art, splits=SPLITS, k: int = 1) -> list:
    ev = Evaluator(dom, llm, workers=1, allow_sealed=True)
    return [t for sp in splits if dom.tasks.splits.get(sp)
            for trs in ev.evaluate(art, sp, k=k).trials.values() for t in trs]


def row(t) -> dict:
    m = t.meta or {}
    return {"task": t.task_id, "seed": t.seed, "family": t.family, "score": t.score, "tokens": t.tokens,
            "cost": t.cost_usd, "requests": t.steps, "cache_read": m.get("cache_read", 0),
            "cache_write": m.get("cache_write", 0), "output": m.get("output", 0),
            "compactions": m.get("compactions", 0), "overflow": bool(m.get("overflow")),
            "triggers": m.get("triggers", {}), "oracle": m.get("oracle", {}), "audit": m.get("audit", {}),
            "max_context": m.get("max_context", 0)}


def trial_rows(dom, llm, art, splits=SPLITS) -> list[dict]:
    return [row(t) for t in trials_of(dom, llm, art, splits)]


def agg(rows: list[dict]) -> dict:
    if not rows:
        return {}
    ks = ("score", "tokens", "cost", "requests", "cache_read", "cache_write", "output", "compactions")
    out = {k: float(np.mean([r[k] for r in rows])) for k in ks}
    out["overflow_rate"] = float(np.mean([r["overflow"] for r in rows]))
    out["n"] = len(rows)
    return out


def metrics(dom, llm, art, splits=SPLITS) -> dict:
    return agg(trial_rows(dom, llm, art, splits))


def rel(base: dict, cand: dict, k: str) -> float:
    return (cand[k] - base[k]) / base[k] if base.get(k) else float("nan")


def trigger_mean(rows: list[dict], mechanism: str, key: str) -> float:
    return float(np.mean([r["triggers"].get(mechanism, {}).get(key, 0) for r in rows]))


def replay_read_ceiling(rows: list[dict], prices: str) -> float:
    """Cache-read cost of large outputs replayed after their first sends, as a share of each task's bill."""
    read = PRICES[prices].read / 1e6
    return float(np.mean([r["oracle"].get("replayed_large_outputs", 0) * read / r["cost"]
                          for r in rows if r["cost"] > 0]))


def kind_counts(ids) -> dict:
    return {k: sum(KIND.get(i) == k for i in ids) for k in ("general", "trick", "do_less", "dud")}


def frozen_variants(dom, r: dict) -> dict:
    """Idea id -> (base harness plus the variant its lineage froze, that variant's params)."""
    lineages = {l["idea"]: l for l in r["lineages"]}
    change = dict(n.split(":", 1) for n in r["frozen"])
    out = {}
    for iid in r["frozen_ideas"]:
        it = [x for x in lineages[iid]["iterations"]
              if x.get("outcome") == "frozen" and x.get("change") == change.get(iid)][-1]
        idea = IDEAS[iid]
        params = idea.grid[it["variant"]] if idea.grid else {}
        out[iid] = (dom.harness(idea.mechanism, **{idea.mechanism: params}), params)
    return out


def power_compare(rows: list[dict], seeds: list, protos: tuple) -> tuple[dict, dict, dict, dict]:
    """Rows by protocol and seed, paired second - first CIs, per-protocol summaries, tricks admitted."""
    by = {p: {r["seed"]: r for r in rows if r["protocol"] == p} for p in protos}
    first, second = (by[p] for p in protos)
    cmp = {k: paired([first[s][k] for s in seeds], [second[s][k] for s in seeds]) for k in POWER_KEYS}
    summ = {p: {k: summarize([by[p][s][k] for s in seeds]) for k in POWER_KEYS} for p in protos}
    tricks = {p: summarize([by[p][s]["admitted_train"]["trick"] for s in seeds]) for p in protos}
    return by, cmp, summ, tricks


def pooled_capability(first: dict, second: dict, seeds: list, tag: str) -> tuple[list, list]:
    """Per seed, the held-out capability ratio averaged over backends A and B."""
    def mean_ab(r):
        return (r[f"A_{tag}_capability"] + r[f"B_{tag}_capability"]) / 2
    return [mean_ab(first[s]) for s in seeds], [mean_ab(second[s]) for s in seeds]


def pf(d: dict, p: int = 4) -> str:
    """A paired difference (``mean_diff`` + CI)."""
    return f"{d['mean_diff']:+.{p}f} [{d['lo']:+.{p}f}, {d['hi']:+.{p}f}]"
