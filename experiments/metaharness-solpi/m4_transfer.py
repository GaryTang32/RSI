"""M4 - The discovered harness transfers to unseen datasets and an unseen model (no re-search).

Claims [paper:MH Tables 5-6]: the selected text-classification harness beats the baselines on 9 unseen
datasets (73.1 vs 68.2-70.2 avg); the selected math harness helps across models not seen during search
(note: the paper's "five held-out models" include its selection model - spec A8.10). Expected here:
positive mean gain on OOD datasets and on MemoLM-B, smaller than the search gain.

Per seed: Meta-Harness (full history) selects a harness with MemoLM-A on the search split; then
``rsi.core.transfer_report`` evaluates it unchanged next to ``fewshot_all`` / ``no_memory`` on the same
datasets' test split and on the OOD datasets, with MemoLM-A (selection model) and MemoLM-B (unseen model).

    python experiments/metaharness-solpi/m4_transfer.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import fmt, fresh_dir, live_llm, parse_args, pool_map, save, summarize, table  # noqa: E402

from rsi.core import transfer_report  # noqa: E402
from rsi.domains.memoclassify import make_domain  # noqa: E402
from rsi.metaharness import Config, run  # noqa: E402

ARGS = None


def job(seed):
    dom = make_domain(seed=seed, scale=0.5 if ARGS.live else 1.0)
    n = 2 if ARGS.live else (4 if ARGS.quick else 8)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              llm_propose=live_llm(ARGS.llm) if ARGS.live else None,
              config=Config(iterations=n, k=2, seed=seed, finalize=False),
              out_dir=fresh_dir("m4", f"s{seed}_{ARGS.llm.replace(':', '_')}"), baselines=dom.baselines())
    arms = {"fewshot_all": dom.seed_artifact("fewshot_all"), "no_memory": dom.seed_artifact("no_memory"),
            "selected": res.best}
    out = {"seed": seed, "selected": res.meta["best_system"]}
    for model in ("A", "B"):
        rep = transfer_report(dom, dom.make_model(model), arms, splits=("evolve", "test", "ood"), workers=1)
        for split, row in rep["splits"].items():
            for arm, v in row.items():
                out[f"{model}_{split}_{arm}"] = v["S"]
            out[f"{model}_{split}_gain"] = row["selected"]["S"] - row["fewshot_all"]["S"]
            if split == "ood":
                out[f"{model}_ood_families_gain"] = {f: row["selected"]["families"][f] - row["fewshot_all"]["families"][f]
                                                     for f in row["selected"]["families"]}
    out["search_gain"] = out["A_evolve_gain"]
    return out


def main():
    global ARGS
    ARGS = parse_args(__doc__.splitlines()[0])
    rows = pool_map(job, list(range(ARGS.seeds)), ARGS.workers)
    keys = ["search_gain", "A_test_gain", "A_ood_gain", "B_evolve_gain", "B_test_gain", "B_ood_gain"]
    summ = {k: summarize([r[k] for r in rows]) for k in keys}
    for arm in ("fewshot_all", "no_memory", "selected"):
        for k in ("A_ood", "B_ood", "A_test", "B_test"):
            summ[f"{k}_{arm}"] = summarize([r[f"{k}_{arm}"] for r in rows])
    pos_ood = summ["A_ood_gain"]["mean"] > 0
    pos_b = summ["B_ood_gain"]["mean"] > 0 and summ["B_test_gain"]["mean"] > 0
    smaller = summ["A_ood_gain"]["mean"] < summ["search_gain"]["mean"]
    verdict = ("REPRODUCED" if pos_ood and pos_b and smaller else "PARTIAL" if pos_ood or pos_b else
               "NOT REPRODUCED") + \
        f": selected - fewshot_all gain = search {fmt(summ['search_gain'])}, unseen datasets (model A) " \
        f"{fmt(summ['A_ood_gain'])}, unseen model B test {fmt(summ['B_test_gain'])}, unseen datasets+model " \
        f"{fmt(summ['B_ood_gain'])}"
    print(table([[k, fmt(summ[k])] for k in keys], ["gain (selected - fewshot_all)", "mean [95% CI]"]))
    print("verdict:", verdict)
    save("m4_transfer" + ("_live" if ARGS.live else ""), {
        "claim": "The selected harness transfers to unseen datasets and unseen models, with a smaller gain than on "
                 "the search set [MH Tables 5-6]",
        "config": {"llm": ARGS.llm, "seeds": ARGS.seeds, "selection_model": "MemoLM-A", "unseen_model": "MemoLM-B"},
        "per_seed": rows, "summary": summ, "verdict": verdict}, ARGS.out)


if __name__ == "__main__":
    main()
