"""E8b (retry round 2, preregistered P11 in docs/methods/rrsi/claims-audit.md section 6): claim L19, live.

"Memory of what failed: rejected ideas stay rejected instead of being retried." E8 (a live proposer over
many rounds) was never run. This probe tests the claim at one decision point with a real LLM proposer:

1. HarnessWorld world w (0-15): the offline loop (full RRSI, default mock proposer, T = 7) runs rounds 0-5
   and is stopped after round 5 settles;
2. the run directory is copied twice and resumed at round 6 with (a) full RRSI and (b) full RRSI with
   ``history_conditioning="accepted_only"``; a capturing backend records variant A's round-6 proposer prompt
   (and system prompt) from the real loop and answers with an empty done() so nothing else happens;
3. each captured prompt is sent once to Claude Haiku (fresh cache, seed 0; the default single-shot protocol);
4. redraws = mechanisms whose file the reply adds and that were measured and REJECTED in rounds 0-5.

Test: paired (accepted-only - full) redraws per prompt, bootstrap 95% CI over the 16 worlds.

    python experiments/rrsi/e8b_history_probe.py --cache-dir DIR [--capture-only] [--max-usd 1.5]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from _common import save  # noqa: E402

from rsi.core import CachedLLM, ClaudeCLI, MockLLM, paired_diff_ci, parse_file_blocks  # noqa: E402
from rsi.domains.harnessworld import HarnessWorldMockLLM, make_domain  # noqa: E402
from rsi.rrsi import Config, Killed, RegularizerSwitches, run  # noqa: E402
from rsi.rrsi.propose import parse_done, parse_sections  # noqa: E402

MID = re.compile(r"\[([a-z]{3}_\d{2})\]")
EMPTY_DONE = '```json\n{"action": "done", "summary": "no change", "edits": []}\n```\n'
CONDITIONS = {"full": RegularizerSwitches.full(),
              "accepted_only": RegularizerSwitches.full().but(history_conditioning="accepted_only",
                                                             name="accepted_only_history")}


def _cfg(seed: int) -> Config:
    return Config(T=7, workers=1, seed=seed, record_timestamps=False, shadow_monitor=False, trace=False)


def capture(world: int, scratch: Path) -> dict:
    dom = make_domain(seed=world)
    base = scratch / f"w{world}_base"
    shutil.rmtree(base, ignore_errors=True)

    def stop_after_5(ev, **kw):
        if ev == "settled" and kw.get("t") == 5:
            raise Killed()
    try:
        run(dom, dom.seed_artifact(), llm_propose=HarnessWorldMockLLM(dom.world), config=_cfg(world), out_dir=base,
            hooks={"settled": stop_after_5})
    except Killed:
        pass
    recs = [json.loads(l) for l in (base / "history.jsonl").read_text().splitlines() if l.strip()]
    rejected = sorted({m for r in recs if r.get("outcome") == "REJECTED" for m in MID.findall(str(r.get("hypothesis")))})
    out = {"world": world, "rejected": rejected, "prompts": {}}
    for cond, sw in CONDITIONS.items():
        d = scratch / f"w{world}_{cond}"
        shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(base, d)
        mock = HarnessWorldMockLLM(dom.world)
        got = {}

        def responder(prompt, system, seed, i):
            if (system or "").startswith("You are a harness engineer agent"):
                dirs = parse_sections(prompt)["directives"]
                if dirs.get("t") == 6:
                    if dirs.get("variant") == "A" and "A" not in got:
                        got["A"] = {"prompt": prompt, "system": system}
                    return EMPTY_DONE
            return mock._respond(prompt, system, seed, i)
        run(dom, dom.seed_artifact(), llm_propose=MockLLM(responder), config=_cfg(world), out_dir=d, switches=sw)
        out["prompts"][cond] = got["A"]
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(base, ignore_errors=True)
    return out


def added_mechanisms(reply: str, base_prompt: str) -> list[str]:
    files = parse_file_blocks(reply or "")
    present = set(re.findall(r"^# mechanism: (\S+)", base_prompt.split("--- CURRENT HARNESS SOURCE H_t ---")[-1],
                             re.M))
    ids = []
    for path, text in files.items():
        if text is None or text.strip() == "<<DELETE>>":
            continue
        for mid in re.findall(r"^# mechanism: (\S+)", text, re.M):
            if mid not in present and mid not in ids:
                ids.append(mid)
    return ids


def main():
    ap = argparse.ArgumentParser(description="E8b: does a live proposer redraw rejected mechanisms?")
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--worlds", type=int, default=16)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--max-usd", type=float, default=1.5)
    ap.add_argument("--capture-only", action="store_true")
    a = ap.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="e8b_"))
    t0 = time.time()
    caps = [capture(w, scratch) for w in range(a.worlds)]
    for c in caps:
        p_full, p_acc = c["prompts"]["full"]["prompt"], c["prompts"]["accepted_only"]["prompt"]
        c["shown_rejected"] = {k: sorted(m for m in c["rejected"] if f"[{m}]" in c["prompts"][k]["prompt"])
                               for k in CONDITIONS}
        c["same_outside_history"] = (parse_sections(p_full)["files"] == parse_sections(p_acc)["files"]
                                     and parse_sections(p_full)["sections"].get("analysis_report")
                                     == parse_sections(p_acc)["sections"].get("analysis_report"))
    if a.capture_only:
        print(json.dumps([{k: c[k] for k in ("world", "rejected", "shown_rejected", "same_outside_history")}
                          for c in caps], indent=1))
        return
    llm = CachedLLM(ClaudeCLI(a.model, timeout_s=300), a.cache_dir)
    rows = []
    for c in caps:
        row = {"world": c["world"], "rejected": c["rejected"], "shown_rejected": c["shown_rejected"],
               "same_outside_history": c["same_outside_history"]}
        for cond in CONDITIONS:
            p = c["prompts"][cond]
            resp = llm.complete(p["prompt"], system=p["system"], seed=0, role="proposer")
            added = added_mechanisms(resp.text, p["prompt"])
            done = parse_done(resp.text) or {}
            row[cond] = {"added": added, "redraws": sorted(set(added) & set(c["rejected"])),
                         "n_redraws": len(set(added) & set(c["rejected"])), "n_added": len(added),
                         "n_declared_edits": len(done.get("edits") or []), "error": resp.error,
                         "reply_head": (resp.text or "")[:400]}
        spent = llm.meter.total().cost_usd
        row["usd_so_far"] = round(spent, 4)
        rows.append(row)
        print(c["world"], {k: (row[k]["n_added"], row[k]["redraws"]) for k in CONDITIONS}, f"${spent:.3f}", flush=True)
        if spent > a.max_usd:
            print("[stop] spend cap reached", flush=True)
            break
    full = [r["full"]["n_redraws"] for r in rows]
    acc = [r["accepted_only"]["n_redraws"] for r in rows]
    pd = paired_diff_ci(full, acc)
    if sum(full) + sum(acc) == 0:
        verdict = "NOT TESTABLE HERE (no redraws in either condition)"
    elif pd["lo"] > 0:
        verdict = "PARTIAL (reproduced on CPU analogue)"
    else:
        verdict = "NOT REPRODUCED (CPU analogue)"
    out = {"experiment": "E8b history probe (retry round 2: P11)", "model": a.model, "worlds": len(rows),
           "redraws_full": {"mean": float(np.mean(full)), "total": int(sum(full))},
           "redraws_accepted_only": {"mean": float(np.mean(acc)), "total": int(sum(acc))},
           "added_full": int(sum(r["full"]["n_added"] for r in rows)),
           "added_accepted_only": int(sum(r["accepted_only"]["n_added"] for r in rows)),
           "paired_accepted_only_minus_full": pd, "verdict": verdict, "live_usd": round(llm.meter.total().cost_usd, 4),
           "cache_hits_misses": [llm.hits, llm.misses], "wall_s": round(time.time() - t0, 1), "rows": rows}
    save("e8b_history_probe", out)
    print(out["redraws_full"], out["redraws_accepted_only"], pd, verdict)


if __name__ == "__main__":
    main()
