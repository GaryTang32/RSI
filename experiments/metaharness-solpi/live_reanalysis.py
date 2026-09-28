"""Offline re-analysis of the live smoke's cached haiku outputs (no new LLM calls, $0).

Meta-Harness part (regenerated after the claim audit, finding N1). The first live smoke ran before stage-A fix
12: haiku wrote every file as ``agents/<name>/src/memory.py``, a dead file, so all four "candidates" were
unchanged copies of ``fewshot_all`` and scored exactly its numbers (search 0.4737, test 0.5595, context 8667.7).
This script replays the SAME two proposer calls through ``CachedLLM(offline=True)`` - the recorded prompts
(``results/metaharness-solpi/live_smoke_original_sessions/iter00N/prompt.md``) and the proposer's system prompt
as it was then (``LEGACY_SKILL_TEXT`` below) reproduce the original cache keys, so a cache miss is an error,
never a new call - then re-parses the replies with the CURRENT ``_collect`` / ``clean_code_block``, validates
the candidates and evaluates them on search and test with the current MemoClassify domain (MemoLM-A, scale
0.4, as in the live smoke).

Caveat: iteration 2's reply was written from a history in which the iteration-1 candidates were dead copies of
``fewshot_all``; with the fix the history (and so haiku's reply) would have differed. The fresh live run in
``live_smoke.json`` is the end-to-end evidence; this is the corrected reading of the old run.

SoL-Pi lineage part: re-parses the cached mechanism files (``--parts lineage``); it is not re-run by default,
and its rows in the existing JSON are kept.

    python experiments/metaharness-solpi/live_reanalysis.py [--parts metaharness,lineage]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import CACHE, RESULTS, parse_args, save  # noqa: E402

from rsi.core import Artifact, CachedLLM, ClaudeCLI, Evaluator, extract_json, parse_file_blocks  # noqa: E402
from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import InterfaceValidator, pareto_frontier  # noqa: E402
from rsi.metaharness.proposer import _collect, clean_code_block  # noqa: E402
from rsi.metaharness.store import context_mean  # noqa: E402

SESSIONS = RESULTS / "live_smoke_original_sessions"

#: the proposer's system prompt when the first live smoke ran (``rsi/metaharness/proposer.py`` before the
#: claim-audit fixes); needed to rebuild the cache keys of the recorded calls
LEGACY_SKILL_TEXT = """\
# Meta-Harness (harness evolution) - run ONE iteration

You improve the HARNESS around a frozen model: the code that decides what information to store, what to
retrieve and how to present it to the model. **You do NOT run benchmarks.** A separate evaluator scores every
candidate you write and stores its code, scores and raw execution traces in the history directory.

## Constraints
- Write exactly {k} new candidate harnesses this iteration. Never write "the frontier is optimal" or stop early.
- Mix exploitation (improve a top system) and exploration (a different mechanism).
- Good candidates change a fundamental MECHANISM (a new retrieval algorithm, a new prompt architecture, a new
  learning strategy, a new memory structure). Parameter sweeps (pool sizes, counts, budgets) almost always tie or
  regress. If your logic is identical to the base except for numbers, rewrite it. Combining systems is valid.
- Anti-overfitting: no dataset-specific hints, never mention dataset or task names, never hard-code answers,
  inputs or ids from the traces. General patterns are fine. Would this help on MANY unfamiliar tasks?
- Candidates must keep the harness interface (see the domain description) and work from a cold start.

## Workflow
1. Read evolution_summary.jsonl and frontier_val.json, then the code of top candidates and, most importantly,
   their raw traces (failed AND successful examples) under candidates/<name>/eval/search/traces/.
2. Formulate {k} falsifiable hypotheses, each targeting a different mechanism.
3. Implement each candidate as COMPLETE files (copy a strong base, then change the mechanism). Self-critique:
   is this a genuinely new mechanism?
"""


def replay_metaharness() -> dict:
    llm = CachedLLM(ClaudeCLI("haiku"), CACHE, offline=True)
    dom = make_domain(seed=0, scale=0.4)
    ev = Evaluator(dom, dom.make_model("A"), workers=1)
    evt = Evaluator(dom, dom.make_model("A"), workers=1, allow_sealed=True)

    def ctx(r):
        out = {}
        for tid, trs in r.trials.items():
            vals = [t.meta.get("context_chars") for t in trs if t.meta.get("context_chars") is not None]
            out[tid] = sum(vals) / len(vals) if vals else 0.0
        return context_mean(out)

    rows, base = [], {}
    for n, a in dom.baselines().items():
        rs, rt = ev.evaluate(a, "evolve"), evt.evaluate(a, "test")
        base[n] = {"search": rs.score, "test": rt.score, "context": ctx(rs)}
    val = InterfaceValidator(timeout_s=30)
    replay = []
    for d in sorted(SESSIONS.glob("iter*")):
        it = int(d.name[4:])
        prompt = (d / "prompt.md").read_text()
        resp = llm.complete(prompt, system=LEGACY_SKILL_TEXT.format(k=2), seed=0 * 1000 + it, role="proposer")
        recorded = (d / "response.md").read_text()
        replay.append({"iteration": it, "cache_hit": resp.ok, "error": resp.error,
                       "reply_matches_recorded_response": resp.text == recorded,
                       "original_usage": (resp.raw or {}).get("usage")})
        if not resp.ok:
            continue
        text = resp.text
        try:
            header = extract_json(text.split("=== FILE:")[0])
        except ValueError:
            header = {}
        raw = parse_file_blocks(text)
        cands = _collect(raw, header if isinstance(header, dict) else {}, 4, dom.baselines(), set(), it)
        for c in cands:
            ok, msg = val.validate(dom, c.artifact)
            base_src = dom.baselines().get(c.base_system, dom.seed_artifact()).get("memory.py")
            row = {"iteration": it, "name": c.name, "base_system": c.base_system, "valid": ok,
                   "reason": None if ok else msg[:300], "files_in_reply": sorted(p for p in raw
                                                                            if p.startswith(f"agents/{c.name}/")),
                   "memory_py_differs_from_base": c.artifact.get("memory.py") != base_src,
                   "base_fallback": bool(c.meta.get("base_fallback"))}
            if ok:
                rs, rt = ev.evaluate(c.artifact, "evolve"), evt.evaluate(c.artifact, "test")
                row.update(search=rs.score, test=rt.score, context=ctx(rs),
                           llm_calls_per_unit=sum(t.meta.get("llm_calls", 0) for trs in rs.trials.values()
                                                  for t in trs) / len(rs.trials))
            rows.append(row)
    pts = [(n, b["search"], b["context"]) for n, b in base.items()] + \
        [(r["name"], r["search"], r["context"]) for r in rows if r.get("valid")]
    front = pareto_frontier(pts)
    return {"baselines": base, "candidates": rows, "replay": replay,
            "frontier_search": [{"system": n, "score": s, "context_cost": c} for n, s, c in front],
            "cache": {"hits": llm.hits, "misses": llm.misses, "spend_usd": llm.meter.total().cost_usd}}


def replay_lineage() -> list:
    """SoL-Pi: re-parse the cached haiku mechanism files and gate them on the training screen."""
    from rsi.domains.agentworld import MockAgentLLM, make_domain as make_world
    from rsi.solpi import DualGate, GateSpec, metrics_from_eval
    from rsi.solpi.registry import with_mechanism
    world = make_world(seed=0, n_train=2, n_accept=1, n_final=1, n_test=0)
    agent = MockAgentLLM("A")
    wev = Evaluator(world, agent, workers=1)
    wbase = world.seed_artifact()
    bm = metrics_from_eval(wev.evaluate(wbase, "evolve"))
    gate = DualGate(GateSpec())
    out = []
    for f in sorted(CACHE.glob("*/*.json")):
        d = json.loads(f.read_text())
        if "=== FILE: extensions/" not in d["text"]:
            continue
        files = parse_file_blocks(d["text"])
        path = next(p for p in files if p.startswith("extensions/"))
        code = clean_code_block(files[path])
        name = path[len("extensions/"):-3]
        row = {"cache_file": f.name[:16], "name": name}
        try:
            compile(code, path, "exec")
            art = Artifact(with_mechanism(wbase.files, name, {}, code))
            err = world.smoke(art, agent)
            row["compiles"] = True
            row["smoke"] = err or "ok"
            if err is None:
                m = metrics_from_eval(wev.evaluate(art, "evolve"))
                g = gate.accept(bm, m)
                row.update(gate=g.accept, reason=g.reason, score=m.agg["score"], tokens=m.agg["tokens"],
                           base_tokens=bm.agg["tokens"], cost=m.agg["cost"], base_cost=bm.agg["cost"])
        except SyntaxError as e:
            row.update(compiles=False, error=str(e))
        out.append(row)
    return out


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=1,
                      extra=lambda ap: ap.add_argument("--parts", default="metaharness"))
    parts = set(args.parts.split(","))
    path = Path(args.out) if args.out else RESULTS / "live_reanalysis.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    out = {k: v for k, v in old.items() if k in ("lineage",)}
    if "metaharness" in parts:
        mh = replay_metaharness()
        out["metaharness"] = mh["candidates"]
        out["metaharness_replay"] = {k: v for k, v in mh.items() if k != "candidates"}
        out["baselines_search"] = {n: b["search"] for n, b in mh["baselines"].items()}
    if "lineage" in parts:
        out["lineage"] = replay_lineage()
    else:
        out["lineage_note"] = "lineage rows kept from the previous run of this script (SoL-Pi; not re-run)"
    mh_rows = out.get("metaharness", [])
    fa = out.get("baselines_search", {}).get("fewshot_all", float("nan"))
    best = max([r.get("search", 0) for r in mh_rows] or [0])
    ln = out.get("lineage", [])
    cache = out.get("metaharness_replay", {}).get("cache", {})
    n_valid = sum(r["valid"] for r in mh_rows)
    n_real = sum(r.get("memory_py_differs_from_base", False) for r in mh_rows)
    beats = "none beats the baseline" if best <= fa else "at least one beats the baseline"
    out["verdict"] = (
        f"cached replies replayed at $0 (cache hits {cache.get('hits')}, misses {cache.get('misses')}); with the "
        f"current parser (fix 12: agents/<name>/src/<path> -> <path>) {n_valid}/{len(mh_rows)} haiku Meta-Harness "
        f"candidates validate and {n_real}/{len(mh_rows)} differ from their base; best search {best:.3f} vs "
        f"fewshot_all {fa:.3f}: {beats} (the old JSON's 0.4737 / 0.5595 / 8667.7 were fewshot_all's own numbers: "
        f"dead-file copies); {sum(r.get('compiles', False) for r in ln)}/{len(ln)} cached haiku SoL-Pi mechanism "
        f"files compile, {sum(1 for r in ln if r.get('gate'))} pass the dual gate on the training screen")
    print(json.dumps(out.get("metaharness"), indent=1, default=str)[:4000])
    print(out["verdict"])
    save("live_reanalysis", out, args.out)


if __name__ == "__main__":
    main()
