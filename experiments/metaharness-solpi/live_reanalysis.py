"""Offline re-analysis of the live smoke's cached haiku outputs (no new LLM calls, $0).

The live smoke showed that some haiku-written files failed with ``invalid syntax (line 1)``: the reply
wrapped the file in a Markdown fence followed by prose, which ``rsi.core.parse_file_blocks`` does not
unwrap. After adding ``clean_code_block`` to both proposers, this script re-parses the SAME cached replies
(Meta-Harness proposer transcripts + SoL-Pi lineage code) and evaluates the recovered candidates with the
offline frozen models, so the fix is verified without spending again.

    python experiments/metaharness-solpi/live_reanalysis.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import CACHE, RUNS, parse_args, save  # noqa: E402

from rsi.core import Artifact, Evaluator, extract_json, parse_file_blocks  # noqa: E402
from rsi.domains.agentworld import MockAgentLLM, make_domain as make_world  # noqa: E402
from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import InterfaceValidator  # noqa: E402
from rsi.metaharness.proposer import _collect  # noqa: E402
from rsi.solpi import DualGate, GateSpec, metrics_from_eval  # noqa: E402
from rsi.solpi.registry import with_mechanism  # noqa: E402
from rsi.metaharness.proposer import clean_code_block  # noqa: E402


def main():
    parse_args(__doc__.splitlines()[0], default_seeds=1)
    out = {"metaharness": [], "lineage": []}
    dom = make_domain(seed=0, scale=0.4)
    ev = Evaluator(dom, dom.make_model("A"), workers=1, allow_sealed=True)
    base = {n: ev.evaluate(a, "evolve").score for n, a in dom.baselines().items()}
    out["baselines_search"] = base
    val = InterfaceValidator(timeout_s=30)
    sess = RUNS / "live" / "metaharness" / "store" / "sessions"
    for d in sorted(sess.glob("iter*")):
        text = (d / "response.md").read_text()
        try:
            header = extract_json(text.split("=== FILE:")[0])
        except ValueError:
            header = {}
        raw = parse_file_blocks(text)
        cands = _collect(raw, header if isinstance(header, dict) else {}, 4, dom.baselines(), set(), 0)
        for c in cands:
            ok, msg = val.validate(dom, c.artifact)
            row = {"iteration": d.name, "name": c.name, "base_system": c.base_system, "valid": ok,
                   "reason": None if ok else msg[:300], "fence_wrapped_raw": any(
                       v.lstrip().startswith("```") for k, v in raw.items() if k.startswith(f"agents/{c.name}/"))}
            if ok:
                r = ev.evaluate(c.artifact, "evolve")
                t = ev.evaluate(c.artifact, "test")
                row.update(search=r.score, test=t.score, context=sum(tr.meta.get("context_chars", 0) for trs in
                                                                     r.trials.values() for tr in trs) / len(r.trials))
            out["metaharness"].append(row)
    world = make_world(seed=0, n_train=2, n_accept=1, n_final=1, n_test=0)
    agent = MockAgentLLM("A")
    wev = Evaluator(world, agent, workers=1)
    wbase = world.seed_artifact()
    bm = metrics_from_eval(wev.evaluate(wbase, "evolve"))
    gate = DualGate(GateSpec())
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
        out["lineage"].append(row)
    mh_valid = sum(r["valid"] for r in out["metaharness"])
    best = max([r.get("search", 0) for r in out["metaharness"]] or [0])
    ln_ok = [r for r in out["lineage"] if r.get("gate")]
    out["verdict"] = (f"with fence cleanup: {mh_valid}/{len(out['metaharness'])} cached haiku Meta-Harness candidates "
                      f"validate (best search {best:.3f} vs fewshot_all {base['fewshot_all']:.3f}); "
                      f"{sum(r.get('compiles', False) for r in out['lineage'])}/{len(out['lineage'])} cached haiku "
                      f"mechanism files compile, {len(ln_ok)} pass the dual gate on the training screen")
    print(json.dumps(out["metaharness"], indent=1, default=str)[:3000])
    print(json.dumps(out["lineage"], indent=1, default=str)[:3000])
    print(out["verdict"])
    save("live_reanalysis", out)


if __name__ == "__main__":
    main()
