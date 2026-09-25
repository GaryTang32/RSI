"""Stage-B check of WHAT THE LIVE PROPOSER SAW (tinylm_live).

The live trace clipped prompts at 6,000 chars, and the LLM cache stores replies
but not prompts. The cache key is sha256([backend, system, prompt, max_tokens,
seed]), so a prompt rebuilt from the recorded state (incumbent artifact from the
store, results.tsv / git log from the analysis event, the rendered program.md
from run_start, the log tail from logs/*.log) that hits an existing cache file
proves byte-for-byte what was sent. For every call we then check that the prompt
holds the history (results.tsv rows) and no sealed-split number (monitor
values), and that the reply's parsed file equals the traced diff.

    python experiments/autoresearch/validate_prompts.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rsi.autoresearch.agent import FIX, PROPOSE, SYSTEM  # noqa: E402
from rsi.core import ArtifactStore, CachedLLM, ClaudeCLI  # noqa: E402
from rsi.core.editors import RewriteEditor, parse_proposal  # noqa: E402

RUN = ROOT / "validation" / "autoresearch" / "tinylm_live"
CACHE = ROOT / "validation" / "autoresearch" / ".cache_tinylm_live"


def main() -> dict:
    ev = [json.loads(l) for l in (RUN / "trace.jsonl").read_text().splitlines()]
    start = next(e["data"] for e in ev if e["kind"] == "run_start")
    program = start["program_md"]
    store = ArtifactStore(RUN / "artifacts")
    llm = CachedLLM(ClaudeCLI("haiku"), CACHE, offline=True)
    ed = RewriteEditor(llm)
    from rsi.domains.tinylm import TinyLMTask

    brief = TinyLMTask(budget_s=8.0, data_root=ROOT / "validation" / "autoresearch" / ".cache_tinylm_data").describe()
    sealed = [v["S"] for e in ev if e["kind"] == "monitor" for v in e["data"]["sealed"].values()]
    out = []
    for e in [e for e in ev if e["kind"] == "analysis"]:
        r = e["round"]
        txt = e["data"]["text"]
        best = float(txt.split("best val_bpb so far: ", 1)[1].split("\n", 1)[0])
        tsv = txt.split("--- results.tsv (tail) ---\n", 1)[1].split("\n--- git log (kept chain) ---\n", 1)[0]
        glog = txt.split("\n--- git log (kept chain) ---\n", 1)[1]
        props = [x["data"] for x in ev if x["kind"] == "proposal" and x["round"] == r]
        inc = next(store.get(p.name.split(".")[0]) for p in (RUN / "artifacts").glob("*/*.json")
                   if p.name.startswith(props[0]["parent"]))
        instr = PROPOSE.format(program=program, brief=brief, metric="val_bpb", best=f"{best:.6f}", n=r)
        prompt = ed.build_prompt(inc, instr, {"results.tsv": tsv, "git_log.txt": glog}, None)
        seed = 0 * 100003 + r
        hit = llm._key(prompt, SYSTEM, None, seed).exists()
        resp = llm.complete(prompt, system=SYSTEM, seed=seed)
        cand = parse_proposal(inc, resp.text).artifact if resp.ok else None
        diff_ok = cand is not None and inc.diff(cand) == props[0]["diff"]
        rec = {"round": r, "call": "propose", "cache_hit": hit, "prompt_chars": len(prompt),
               "tsv_rows_in_prompt": tsv.count("\n") - 1, "tsv_in_prompt": tsv in prompt,
               "sealed_numbers_in_prompt": [s for s in sealed if f"{s:.4f}" in prompt or f"{s:.6f}" in prompt],
               "sealed_words_in_prompt": [w for w in ("test_iid", "test_shift") if w in prompt],
               "reply_diff_equals_trace": diff_ok}
        out.append(rec)
        print(rec, flush=True)
        # fixes
        cur = cand
        for p in props[1:]:
            if cur is None:
                break
            k = p["attempt"]
            log = (RUN / "logs" / (f"exp{r:04d}" + ("" if k == 1 else f"_fix{k - 1}") + ".log")).read_text()
            tail = "\n".join(log.strip().splitlines()[-50:])
            desc = props[0]["change"]
            finstr = FIX.format(program=program, desc=desc, tail=tail[-4000:])
            fprompt = ed.build_prompt(cur, finstr, None, None)
            fseed = 0 * 100003 + r + 50000
            fhit = llm._key(fprompt, SYSTEM, None, fseed).exists()
            fresp = llm.complete(fprompt, system=SYSTEM, seed=fseed)
            nxt = parse_proposal(cur, fresp.text).artifact if fresp.ok else None
            out.append({"round": r, "call": f"fix{k}", "cache_hit": fhit, "prompt_chars": len(fprompt),
                        "sealed_numbers_in_prompt": [s for s in sealed if f"{s:.4f}" in fprompt],
                        "reply_diff_equals_trace": nxt is not None and cur.diff(nxt) == p["diff"],
                        "reply_tail": fresp.text.rstrip()[-160:] if k == 1 else None})
            cur = nxt
    res = {"calls": out, "all_hit": all(o["cache_hit"] for o in out), "hits": llm.hits, "misses": llm.misses}
    print(json.dumps(res, indent=1, default=str))
    (RUN / "prompt_audit.json").write_text(json.dumps(res, indent=1, default=str))
    return res


if __name__ == "__main__":
    main()
