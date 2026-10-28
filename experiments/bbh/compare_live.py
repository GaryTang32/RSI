"""BBH / BBEH comparison, one arm per command: pilot, seed, cot3 (BBH only), rrsi, gepa, metaharness, harness2, bestofn.

``--benchmark bbh`` (default) or ``bbeh`` picks the data, grader, families file (experiments/<bench>/families.json),
result directory (results/<bench>) and size defaults (BBEH: evolve 4, holdout 6, ood 6 per family, k = 1,
n_sub = 24, rrsi T = 4, gepa B = 150, metaharness 3 x k = 2 at $0.75 per session, pilot 5 per family with band
[0.15, 0.75]). ``experiments/bbeh/compare_live.py`` is this script with ``--benchmark bbeh``. Below, sizes are BBH's.

Every arm uses the same model as agent and proposer (``--model``, default claude:haiku) and one shared LLM cache
(``--cache-dir``, default results/bbh/live_cache), and writes ``results/bbh/<arm>.json``; ``merge.py`` builds the table.

- pilot        seed harness on the pilot pool (10 reserved examples x pilot_candidates of families.json, k = 1);
               suggests practice/ood families; ``--apply`` writes the suggestion into families.json.
- seed         seed harness (one call, asked for a final ``ANSWER:`` line) on holdout + ood, k = 2.
- cot3         fixed reference: the official 3-shot CoT prompt of each example's family, holdout + ood, k = 2.
- rrsi / gepa / metaharness
               searched on evolve (spend cap = the method's own Budget/cap at ``(1 - eval_reserve) * cap``), then a
               static leak check of the chosen harness (a failure fails the arm), then holdout + ood at k = 2.
               gepa: acceptance = noise_margin with delta from the seed's noise band on evolve (k = 2).
               metaharness: the coding-agent proposer (``claude -p`` with file tools; needs a claude:* model);
               its spend cap is enforced between iterations by a guard around the proposer.
- harness2     Harness² at k = 1 streaming a fixed subsample (``--n-sub`` = 36: 18 holdout + 18 ood, family-
               stratified, order seed 0); labels are used only by the post-hoc report after the loop.
- bestofn      matched-compute control on the same subsample: the seed harness n times per task, the Harness²
               selector (same model, no labels) picks one; n = Harness² calls per task - 1 (from harness2.json if
               present, else 8 = 4 rollouts + 3 sessions + 1 reflector).

The spend cap (``--cap`` USD, fresh calls only; cache replays are free) is passed to each method's own cap and also
checked between phases (and between tasks/families for pilot, bestofn and evaluation splits). A started phase
finishes, so the final figure can exceed the cap by about one phase step. Re-running an arm resumes its search from
results/bbh/runs/<arm> (``--fresh`` starts over); ``--phase eval`` scores a saved search result only.

Run (live; one background command per arm):
    python experiments/bbh/compare_live.py --arm seed --cap 3
    python experiments/bbh/compare_live.py --arm rrsi --cap 8
    python experiments/bbh/compare_live.py --arm seed --smoke          # 2 tasks, smallest config
    python experiments/bbh/compare_live.py --arm all --offline         # dry run of every arm with the mock LLMs
    python experiments/bbh/merge.py
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
import random
import shutil
import sys
import time
import traceback
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rsi.core import Artifact, Budget, CachedLLM, ClaudeCLI, Evaluator, get_llm, transfer_report  # noqa: E402
from rsi.core.stats import noise_from_trials  # noqa: E402
from rsi.domains.bbh import BBHDomain, BBHSimModel, cot3_artifact, leak_check  # noqa: E402
from rsi.domains.bbh import grading as bbh_grading  # noqa: E402
from rsi.domains.bbh.data import BBH, Benchmark  # noqa: E402
from rsi.domains.bbh.suite import load_families, make_pilot_suite, make_suite, suggest_families  # noqa: E402
from rsi.domains.bbeh import BBEHDomain, BBEHSimModel  # noqa: E402
from rsi.domains.bbeh import grading as bbeh_grading  # noqa: E402
from rsi.domains.bbeh.data import BBEH  # noqa: E402

ARMS = ("pilot", "seed", "cot3", "rrsi", "gepa", "metaharness", "harness2", "bestofn")
SEARCHED = ("rrsi", "gepa", "metaharness")


@dataclass(frozen=True)
class Profile:
    """Everything that differs between the BBH and BBEH runs of this script."""

    spec: Benchmark
    domain_cls: type
    sim_cls: type
    is_generic: Any
    arms: tuple
    defaults: dict

    @property
    def results(self) -> Path:
        return ROOT / "results" / self.spec.name

    @property
    def families(self) -> Path:
        return ROOT / "experiments" / self.spec.name / "families.json"


PROFILES = {
    "bbh": Profile(BBH, BBHDomain, BBHSimModel, bbh_grading.is_generic_target, ARMS,
                   dict(evolve=6, holdout=10, ood=10, k=2, n_sub=36, rrsi_T=10, gepa_B=400, mh_iterations=6, mh_k=2,
                        mh_session_usd=1.0)),
    # BBEH answers are long (median ~10k output tokens, ~$0.056 per Haiku call): small evolve split, short searches;
    # no cot3 (no official CoT prompts)
    "bbeh": Profile(BBEH, BBEHDomain, BBEHSimModel, bbeh_grading.is_generic_target,
                    tuple(x for x in ARMS if x != "cot3"),
                    dict(evolve=4, holdout=10, ood=10, k=2, n_sub=36, rrsi_T=4, gepa_B=150, mh_iterations=3, mh_k=2,
                         mh_session_usd=0.75)),
}


class CapReached(RuntimeError):
    pass


# ----- setup --------------------------------------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--benchmark", choices=sorted(PROFILES), default="bbh")
    ap.add_argument("--arm", required=True, choices=ARMS + ("all",))
    ap.add_argument("--cap", type=float, default=None, help="USD spend cap for this arm (fresh calls)")
    ap.add_argument("--smoke", action="store_true", help="2 tasks, smallest config: a cheap live check")
    ap.add_argument("--offline", action="store_true", help="mock LLMs (BBHSimModel + offline proposers); no spend")
    ap.add_argument("--model", default="claude:haiku", help="agent and proposer model (one model for all roles)")
    ap.add_argument("--cache-dir", default=None, help="shared LLM response cache (default results/<bench>/live_cache)")
    ap.add_argument("--data-cache", default=None, help="data cache (default $RSI_BBH_CACHE or ~/.cache/rsi/bbh; "
                                                       "$RSI_BBEH_CACHE or ~/.cache/rsi/bbeh)")
    ap.add_argument("--families", default=None, help="default experiments/<bench>/families.json")
    ap.add_argument("--out-dir", default=None, help="result directory (default results/<bench>[/smoke|/offline])")
    ap.add_argument("--seed", type=int, default=0, help="suite seed")
    ap.add_argument("--evolve", type=int, default=None, help="evolve examples per practice family")
    ap.add_argument("--holdout", type=int, default=None, help="holdout examples per practice family")
    ap.add_argument("--ood", type=int, default=None, help="ood examples per unseen family")
    ap.add_argument("--k", type=int, default=None, help="trials per example in the final evaluation")
    ap.add_argument("--n-sub", type=int, default=None, help="Harness²/best-of-n subsample (half holdout, half ood)")
    ap.add_argument("--order-seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--eval-reserve", type=float, default=0.3, help="share of --cap kept for the final evaluation")
    ap.add_argument("--phase", choices=("all", "search", "eval"), default="all", help="searched arms only")
    ap.add_argument("--llm-timeout", type=float, default=900.0,
                    help="seconds per claude -p call (BBEH answers can run past 30k tokens; a timeout is billed but lost)")
    ap.add_argument("--llm-retries", type=int, default=2)
    ap.add_argument("--fresh", action="store_true", help="delete this arm's run directory first (no resume)")
    ap.add_argument("--apply", action="store_true", help="pilot: write the suggested families into --families")
    ap.add_argument("--rrsi-T", type=int, default=None)
    ap.add_argument("--gepa-B", type=int, default=None, help="GEPA rollout budget (max_metric_calls)")
    ap.add_argument("--mh-iterations", type=int, default=None)
    ap.add_argument("--mh-k", type=int, default=None)
    ap.add_argument("--mh-session-usd", type=float, default=None, help="per agent-session budget of the proposer")
    ap.add_argument("--bon-n", type=int, default=None, help="best-of-n n (default: matched to harness2.json)")
    a = ap.parse_args(argv)
    a.P = PROFILES[a.benchmark]
    for key, val in a.P.defaults.items():       # benchmark defaults for whatever was not given
        if getattr(a, key) is None:
            setattr(a, key, val)
    if a.arm != "all" and a.arm not in a.P.arms:
        raise SystemExit(f"arm {a.arm!r} is unavailable for {a.benchmark}"
                         + (": BBEH publishes no official CoT prompts" if a.arm == "cot3" else ""))
    a.families = a.families or str(a.P.families)
    if a.smoke:
        a.evolve, a.holdout, a.ood, a.k, a.n_sub = 2, 1, 1, 1, 2
        a.rrsi_T, a.gepa_B, a.mh_iterations, a.mh_k = 1, 12, 1, 1
        a.bon_n = a.bon_n or 2
    sub = "offline" if a.offline else "smoke" if a.smoke else ""
    if a.offline and a.smoke:
        sub = "offline_smoke"
    res = a.P.results
    a.out_dir = Path(a.out_dir) if a.out_dir else (res / sub if sub else res)
    a.cache_dir = Path(a.cache_dir) if a.cache_dir else res / "live_cache"
    return a


def build_suite(a):
    fam = load_families(a.families, a.P.spec)
    practice, ood = fam["practice"], fam["ood"]
    if a.smoke:
        practice, ood = practice[:1], ood[:1]
    suite = make_suite(practice, ood, per_family={"evolve": a.evolve, "holdout": a.holdout, "ood": a.ood},
                       seed=a.seed, cache_dir=a.data_cache, benchmark=a.P.spec)
    return fam, suite


def suite_signature(suite, a) -> dict:
    return {"name": suite.name, "seed": a.seed, "summary": suite.summary(),
            "per_family": {"evolve": a.evolve, "holdout": a.holdout, "ood": a.ood}}


def make_llms(a, suite, arm: str):
    """(task LLM, proposer LLM): one cached live model for both, or the offline sim + the arm's mock proposer."""
    if not a.offline:
        os.environ.setdefault("RSI_EXIT_ON_USAGE_LIMIT", "1")   # stop (resumably) instead of scoring empty replies
        llm = get_llm(a.model, cache_dir=str(a.cache_dir),
                      **({"timeout_s": a.llm_timeout, "retries": a.llm_retries} if a.model.startswith("claude:") else {}))
        return llm, llm
    from rsi.gepa import AgentQAReflectionLM
    from rsi.harness2 import MockImprover
    from rsi.rrsi.mocks import AgentQAMockLLM
    prop = {"rrsi": AgentQAMockLLM, "gepa": AgentQAReflectionLM, "harness2": MockImprover,
            "bestofn": MockImprover}.get(arm)
    return a.P.sim_cls(suite), (prop() if prop else None)


def subsample(suite, n_sub: int, order_seed: int) -> list:
    """``n_sub`` tasks, half holdout and half ood, round-robin over families (seeded), then shuffled."""
    picked = []
    for split, n in (("holdout", n_sub // 2), ("ood", n_sub - n_sub // 2)):
        by_fam: dict[str, list] = {}
        for t in sorted(suite.split(split, allow_sealed=True), key=lambda t: t.id):
            by_fam.setdefault(t.family, []).append(t)
        for f in sorted(by_fam):
            random.Random(f"{order_seed}:{split}:{f}").shuffle(by_fam[f])
        queues = [by_fam[f] for f in sorted(by_fam)]
        got: list = []
        while len(got) < n and any(queues):
            for q in queues:
                if q and len(got) < n:
                    got.append(q.pop(0))
        picked += got
    random.Random(order_seed).shuffle(picked)
    return picked


# ----- spend tracking -----------------------------------------------------------------------------------------------

class Ctx:
    """Per-arm spend, phases and caps."""

    def __init__(self, a, arm: str, llms: list) -> None:
        self.a, self.arm, self.cap = a, arm, a.cap
        self.llms = list({id(x): x for x in llms if x is not None}.values())
        self.extra_usd = 0.0                 # spend outside the LLM objects' meters (the agent proposer)
        self.extra_calls = 0
        self.phases: list[dict] = []
        self._open: list[str] = []

    def usd(self) -> float:
        return sum(x.meter.total().cost_usd for x in self.llms) + self.extra_usd

    def calls(self) -> tuple[int, int]:
        fresh = sum(x.meter.total().calls for x in self.llms) + self.extra_calls
        cached = sum(getattr(x, "hits", 0) for x in self.llms)
        return fresh, cached

    def saved_usd(self) -> float:
        return sum(x.saved.total().cost_usd for x in self.llms if isinstance(x, CachedLLM))

    def check(self, before: str) -> None:
        if self.cap is not None and self.usd() >= self.cap:
            raise CapReached(f"spend ${self.usd():.3f} >= cap ${self.cap:.2f} before {before}")

    def search_cap(self) -> Optional[float]:
        """The methods' own caps compare this to their cumulative LLM meters (all phases of this process, and for
        RRSI also earlier processes of the same run directory and the price of cache replays)."""
        return None if self.cap is None else self.cap * (1 - self.a.eval_reserve)

    @contextmanager
    def phase(self, name: str):
        u0, (f0, c0), s0, t0 = self.usd(), self.calls(), self.saved_usd(), time.time()
        row = {"phase": name, **({"within": self._open[-1]} if self._open else {})}
        self.phases.append(row)
        self._open.append(name)
        try:
            yield row
        finally:
            self._open.pop()
            f1, c1 = self.calls()
            row.update(usd=round(self.usd() - u0, 5), fresh_calls=f1 - f0, cached_calls=c1 - c0,
                       usd_saved_by_cache=round(self.saved_usd() - s0, 5), wall_s=round(time.time() - t0, 1))

    def totals(self) -> dict:
        def tot(pred):
            rows = [p for p in self.phases if pred(p["phase"]) and "within" not in p]   # nested rows: detail
            return {"usd": round(sum(p.get("usd", 0) for p in rows), 5),
                    "fresh_calls": sum(p.get("fresh_calls", 0) for p in rows),
                    "cached_calls": sum(p.get("cached_calls", 0) for p in rows),
                    "usd_saved_by_cache": round(sum(p.get("usd_saved_by_cache", 0) for p in rows), 5)}
        return {"search": tot(lambda n: not n.startswith("eval")), "eval": tot(lambda n: n.startswith("eval")),
                "total": tot(lambda n: True)}


# ----- shared phases ------------------------------------------------------------------------------------------------

def final_eval(ctx: Ctx, dom, llm, name: str, art: Artifact) -> dict:
    """``transfer_report`` per split (holdout, then ood) with a cap check between them."""
    out: dict = {}
    for split in ("holdout", "ood"):
        ctx.check(f"eval {split}")
        with ctx.phase(f"eval_{split}"):
            rep = transfer_report(dom, llm, {name: art}, splits=(split,), k=ctx.a.k, workers=ctx.a.workers)
        v = rep["splits"][split][name]
        out[split] = {"S": v["S"], "C": v["C"], "missing": v["missing"], "families": v["families"],
                      "tasks": v["tasks"]}
    return out


def eval_fields(ev: dict, suite) -> dict:
    per_task = {t: s for split in ("holdout", "ood") for t, s in ev.get(split, {}).get("tasks", {}).items()}
    return {"eval": ev, "per_task": per_task, "task_family": {t: suite.get(t).family for t in per_task},
            "accuracy": (sum(per_task.values()) / len(per_task)) if per_task else None}


def leak(a, art: Artifact, suite) -> dict:
    return leak_check(art, suite, split="evolve", is_generic=a.P.is_generic)


# ----- arms ---------------------------------------------------------------------------------------------------------

def arm_pilot(a, ctx, fam, suite, llm, prop, out: dict) -> None:
    cands = fam["pilot_candidates"][:2] if a.smoke else fam["pilot_candidates"]
    n_pilot = 1 if a.smoke else a.P.spec.pilot_per_family
    psuite = make_pilot_suite(cands, n_per_family=n_pilot, cache_dir=a.data_cache, benchmark=a.P.spec)
    if a.offline:
        llm = a.P.sim_cls(psuite)
        ctx.llms = [llm]
    pdom = a.P.domain_cls(psuite)
    ev = Evaluator(pdom, llm, workers=a.workers)
    per_task, acc = {}, {}
    for f in cands:
        ctx.check(f"pilot family {f}")
        tasks = [t for t in psuite.split("pilot") if t.family == f]
        with ctx.phase(f"pilot_{f}"):
            r = ev.evaluate(pdom.seed_artifact(), tasks, k=1, label="pilot")
        per_task.update(r.task_scores())
        acc[f] = r.score
    lo, hi = a.P.spec.pilot_band
    sug = suggest_families(acc, n_practice=len(fam["practice"]), n_ood=len(fam["ood"]), lo=lo, hi=hi)
    out.update(family_accuracy=acc, per_task=per_task, suggestion=sug, n_per_family=n_pilot)
    if a.apply and not a.smoke:
        if len(sug["practice"]) == len(fam["practice"]) and len(sug["ood"]) == len(fam["ood"]):
            d = json.loads(Path(a.families).read_text())
            d.update(practice=sug["practice"], ood=sug["ood"])
            Path(a.families).write_text(json.dumps(d, indent=1) + "\n")
            out["applied"] = True
        else:
            out["applied"] = False
            out["apply_note"] = "too few families off floor/ceiling; families.json left unchanged"


def arm_seed(a, ctx, fam, suite, llm, prop, out: dict) -> None:
    dom = a.P.domain_cls(suite)
    out.update(eval_fields(final_eval(ctx, dom, llm, "seed", dom.seed_artifact()), suite))


def arm_cot3(a, ctx, fam, suite, llm, prop, out: dict) -> None:
    if a.P.spec.cot_path is None:
        raise SystemExit(f"cot3 is unavailable for {a.P.spec.name}: no official CoT prompts")
    dom = BBHDomain(suite, family_hint=True)
    art = cot3_artifact(suite.families(), cache_dir=a.data_cache)
    out["note"] = "fixed reference (no search): official BBH 3-shot CoT prompt of the example's family"
    out.update(eval_fields(final_eval(ctx, dom, llm, "cot3", art), suite))


def _search_rrsi(a, ctx, dom, llm, prop, run_dir: Path):
    from rsi.rrsi import Config, run
    cfg = Config(T=a.rrsi_T, k=2, workers=a.workers, seed=0, shadow_monitor=False)
    res = run(dom, dom.seed_artifact(), llm_task=llm, llm_propose=prop, config=cfg, out_dir=str(run_dir),
              budget=Budget(max_usd=ctx.search_cap(), stop_dir=str(run_dir)))
    return res, {"T": a.rrsi_T, "stop_reason": res.stop_reason, "rounds": len(res.trajectory)}


def _search_gepa(a, ctx, dom, llm, prop, run_dir: Path):
    from rsi.gepa import Config, run
    ctx.check("noise band")
    with ctx.phase("noise_band"):
        r = Evaluator(dom, llm, workers=a.workers).evaluate(dom.seed_artifact(), "evolve", k=2)
        noise = noise_from_trials(r.trial_matrix())
    cfg = Config(max_metric_calls=a.gepa_B, acceptance="noise_margin", noise_margin=float(noise.delta),
                 workers=a.workers, seed=0, shadow_monitor=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = run(dom, dom.seed_artifact(), llm_task=llm, llm_propose=prop, config=cfg, out_dir=str(run_dir),
                  budget=Budget(max_usd=ctx.search_cap()))
    return res, {"B": a.gepa_B, "noise_margin": float(noise.delta), "seed_evolve_S": r.score,
                 "stop_reason": res.stop_reason, "rollouts": res.meta.get("rollouts")}


def _search_metaharness(a, ctx, dom, llm, prop, run_dir: Path):
    from rsi.metaharness import AgentQALibrary, Config, MockProposer, run
    from rsi.metaharness.proposer import AgentProposer, ProposalBatch, Proposer

    if a.offline:
        inner = MockProposer(AgentQALibrary(), seed=0)
    else:
        base = llm.inner if isinstance(llm, CachedLLM) else llm
        if not isinstance(base, ClaudeCLI):
            raise SystemExit("metaharness uses the coding-agent proposer: pass a claude:* --model")
        inner = AgentProposer(base, max_budget_usd=a.mh_session_usd)
    search_cap = ctx.search_cap()

    class CapGuard(Proposer):
        """Delegates to the proposer until the arm's search cap is reached, then proposes nothing."""
        role = getattr(inner, "role", "proposer")

        def propose(self, **kw):
            if search_cap is not None and ctx.usd() >= search_cap:
                return ProposalBatch(error=f"spend cap: ${ctx.usd():.3f} >= search cap ${search_cap:.2f}")
            b = inner.propose(**kw)
            if not a.offline:            # agent sessions bypass the LLM cache and its meter
                ctx.extra_usd += b.usage.cost_usd
                ctx.extra_calls += b.usage.calls
            return b

    cfg = Config(iterations=a.mh_iterations, k=a.mh_k, search_split="evolve", finalize=False, workers=a.workers,
                 seed=0, shadow_monitor=False)
    res = run(dom, dom.seed_artifact(), llm_task=llm, llm_propose=None if a.offline else llm, config=cfg,
              out_dir=str(run_dir), proposer=CapGuard())
    return res, {"iterations": a.mh_iterations, "k": a.mh_k, "best_system": res.meta.get("best_system"),
                 "n_evaluated": res.meta.get("n_evaluated"), "stop_reason": res.stop_reason,
                 "proposer": type(inner).__name__}


def arm_searched(a, ctx, fam, suite, llm, prop, out: dict) -> None:
    dom = a.P.domain_cls(suite)
    run_dir = Path(out["run_dir"])
    best_dir = run_dir / "final_harness"
    if a.phase in ("all", "search"):
        ctx.check("search")
        with ctx.phase("search"):
            res, info = {"rrsi": _search_rrsi, "gepa": _search_gepa,
                         "metaharness": _search_metaharness}[ctx.arm](a, ctx, dom, llm, prop, run_dir)
        res.best.to_dir(best_dir, clean=True)
        out["search"] = {**info, "best_is_seed": res.best == res.baseline, "best_id": res.best.short_id,
                         "usage": res.usage.get("_total") if isinstance(res.usage, dict) else None}
    if a.phase == "search":
        return
    if not best_dir.exists():
        raise SystemExit(f"no saved search result in {best_dir}; run --phase search first")
    best = Artifact.from_dir(best_dir)
    out["final_harness"] = {"dir": str(best_dir), "id": best.short_id, "files": sorted(best)}
    lc = leak(a, best, suite)
    out["leak_check"] = lc
    if not lc["passed"]:
        out["status"] = "failed_leak_check"
        return
    out.update(eval_fields(final_eval(ctx, dom, llm, ctx.arm, best), suite))


def _bon_n(a) -> tuple[int, str]:
    if a.bon_n:
        return a.bon_n, "--bon-n"
    h2 = a.out_dir / "harness2.json"
    if h2.exists():
        d = json.loads(h2.read_text())
        cpt = d.get("calls_per_task")
        if cpt:
            return max(1, round(cpt - 1)), f"harness2.json: {cpt:.2f} calls per task - 1 selector call"
    return 8, "default: 4 rollouts + 3 sessions + 1 reflector of Harness² k = 1 (no harness2.json yet)"


def arm_harness2(a, ctx, fam, suite, llm, prop, out: dict) -> None:
    from rsi.harness2 import Config, run
    dom = a.P.domain_cls(suite)
    tasks = subsample(suite, a.n_sub, a.order_seed)
    ctx.check("harness2 stream")
    with ctx.phase("stream"):
        res = run(dom, dom.seed_artifact(), llm_task=llm, llm_propose=prop,
                  config=Config(k=1, mode="parallel", seed=0, max_usd=a.cap, report=True, trace=True),
                  out_dir=out["run_dir"], tasks=tasks)
    recs, rep = res.meta["records"], res.meta["report"]
    fresh, cached = ctx.calls()
    out.update(subset=[t.id for t in tasks], stop_reason=res.stop_reason, tasks_done=len(recs),
               per_task={t: v["submitted"] for t, v in rep["per_task"].items()},
               base_per_task={t: v["base"] for t, v in rep["per_task"].items()},
               task_family={t: v["family"] for t, v in rep["per_task"].items()},
               report={k: v for k, v in rep.items() if k != "per_task"},
               status_counts={s: sum(r.status == s for r in recs) for s in sorted({r.status for r in recs})},
               library_added=sum(bool(r.library_added) for r in recs),
               calls_per_task=(fresh + cached) / max(1, len(recs)),
               accuracy=rep["submitted"][0])
    if a.offline:   # the sim and the mock improver are separate objects: count both
        from rsi.harness2.api import merged_usage
        out["calls_per_task"] = merged_usage(llm, prop)["_total"]["calls"] / max(1, len(recs))


def arm_bestofn(a, ctx, fam, suite, llm, prop, out: dict) -> None:
    from rsi.harness2.api import posthoc_report
    from rsi.harness2.baselines import best_of_n
    dom = a.P.domain_cls(suite)
    tasks = subsample(suite, a.n_sub, a.order_seed)
    n, why = _bon_n(a)
    recs: list = []
    stop = "stream_end"
    with ctx.phase("stream"):
        for t in tasks:
            try:
                ctx.check(f"task {t.id}")
            except CapReached as e:
                stop = str(e)
                break
            recs += best_of_n(dom, dom.seed_artifact(), llm_task=llm, llm_select=prop, n=n, tasks=[t], seed=0,
                              report=False)["records"]
    rep = posthoc_report(dom, recs, tasks=tasks)
    fresh, cached = ctx.calls()
    out.update(n=n, n_rule=why, subset=[t.id for t in tasks], stop_reason=stop, tasks_done=len(recs),
               per_task={t: v["submitted"] for t, v in rep["per_task"].items()},
               base_per_task={t: v["base"] for t, v in rep["per_task"].items()},
               task_family={t: v["family"] for t, v in rep["per_task"].items()},
               report={k: v for k, v in rep.items() if k != "per_task"},
               calls_per_task=(fresh + cached) / max(1, len(recs)), accuracy=rep["submitted"][0])


ARM_FN = {"pilot": arm_pilot, "seed": arm_seed, "cot3": arm_cot3, "rrsi": arm_searched, "gepa": arm_searched,
          "metaharness": arm_searched, "harness2": arm_harness2, "bestofn": arm_bestofn}


# ----- driver -------------------------------------------------------------------------------------------------------

def run_arm(a, arm: str) -> dict:
    fam, suite = build_suite(a)
    llm, prop = make_llms(a, suite, arm)
    ctx = Ctx(a, arm, [llm, prop])
    run_dir = a.out_dir / "runs" / arm
    if (a.fresh or a.offline) and a.phase != "eval" and run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"arm": arm, "benchmark": a.P.spec.name, "status": "ok", "mode": "offline" if a.offline else "live",
                           "smoke": a.smoke, "model": "bbh-sim + mocks" if a.offline else a.model, "cap": a.cap,
                           "k": a.k, "suite": suite_signature(suite, a), "families": fam, "run_dir": str(run_dir),
                           "cache_dir": None if a.offline else str(a.cache_dir), "started": time.time()}
    t0 = time.time()
    try:
        ARM_FN[arm](a, ctx, fam, suite, llm, prop, out)
    except CapReached as e:
        out["status"] = "cap_reached"
        out["stop"] = str(e)
    except Exception as e:  # noqa: BLE001 - keep a record of what ran before the failure
        out["status"] = "error"
        out["error"] = f"{type(e).__name__}: {e}"
        out["traceback"] = traceback.format_exc(limit=8)
    out["phases"] = ctx.phases
    out["spend"] = ctx.totals()
    out["usd"] = round(ctx.usd(), 5)
    out["wall_s"] = round(time.time() - t0, 1)
    n_eval = len(out.get("per_task") or {})
    out["eval_usd_per_example"] = (out["spend"]["eval"]["usd"] / n_eval) if n_eval and arm not in (
        "harness2", "bestofn", "pilot") else None
    path = a.out_dir / f"{arm}.json"
    a.out_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, indent=1, default=str))
    tmp.replace(path)
    return out


def summary_line(o: dict) -> str:
    acc = o.get("accuracy")
    lc = o.get("leak_check")
    return (f"{o['arm']:12s} status={o['status']:18s} acc={'-' if acc is None else f'{acc:.3f}'} "
            f"n={len(o.get('per_task') or {})} usd={o['usd']:.4f} "
            f"calls={o['spend']['total']['fresh_calls']}+{o['spend']['total']['cached_calls']}cached"
            + ("" if lc is None else f" leak_check={'pass' if lc['passed'] else 'FAIL'}")
            + (f" error={o['error']}" if o.get("error") else ""))


def main(argv=None) -> int:
    a = parse_args(argv)
    arms = a.P.arms if a.arm == "all" else (a.arm,)
    bad = 0
    for arm in arms:
        o = run_arm(a, arm)
        print(summary_line(o), flush=True)
        bad += o["status"] == "error"
    print(f"results in {a.out_dir}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
