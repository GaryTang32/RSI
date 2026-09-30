"""M10 - OOD transfer of the selected harness to 9 unseen datasets vs zero-shot and every few-shot variant
(retry round 2, P5; claim Q6 [paper:MH Table 5]: "best average accuracy ... and all few-shot baselines", "the
highest performance on 6/9 datasets").

The domain's 4 OOD specs plus 5 more (experiment-local; the domain defaults are unchanged), chosen to vary label
count, cluster size, labels-in-input, hard fraction and confusability as the paper's nine datasets vary.

    python experiments/metaharness-solpi/m10_ood9.py --seeds 10 --workers 2
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import fmt, fresh_dir, paired, parse_args, pool_map, save, summarize, table  # noqa: E402
from mh_common import drop_traces  # noqa: E402

import numpy as np  # noqa: E402

from rsi.core import Evaluator  # noqa: E402
from rsi.domains.memoclassify.data import OOD_SPECS, SEARCH_SPECS, DatasetSpec, make_datasets  # noqa: E402
from rsi.domains.memoclassify.domain import MemoClassifyDomain  # noqa: E402
from rsi.metaharness import Config, run  # noqa: E402

EXTRA_OOD = (
    DatasetSpec("ood_theta", 5, (2, 2), n_train=40, n_val=0, n_test=60, labels_in_input=False, hard_frac=0.25),
    DatasetSpec("ood_iota", 48, (2, 4), n_train=192, n_val=0, n_test=60, labels_in_input=False, hard_frac=0.2),
    DatasetSpec("ood_kappa", 16, (3, 4), n_train=128, n_val=0, n_test=60, labels_in_input=True, hard_frac=0.2,
                confuse_p=0.5),
    DatasetSpec("ood_lambda", 26, (2, 3), n_train=78, n_val=0, n_test=60, labels_in_input=False, hard_frac=0.35),
    DatasetSpec("ood_mu", 10, (2, 3), n_train=150, n_val=0, n_test=60, labels_in_input=False, hard_frac=0.15,
                confuse_p=0.45),
)
OOD9 = tuple(OOD_SPECS) + EXTRA_OOD
N_ITER, K = 20, 2


def job(seed):
    search, ood = make_datasets(seed, search=SEARCH_SPECS, ood=OOD9)
    dom = MemoClassifyDomain(search, ood, seed=seed)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              config=Config(iterations=N_ITER, k=K, seed=seed, shadow_monitor=False, finalize=False),
              out_dir=fresh_dir("m10", f"s{seed}"), baselines=dom.baselines())
    arms = {"selected": res.best, **dom.baselines(), **dom.comparators()}
    ev = Evaluator(dom, dom.make_model("A"), workers=1, allow_sealed=True)
    out = {"seed": seed, "selected_name": res.meta["best_system"]}
    for a, art in arms.items():
        r = ev.evaluate(art, "ood")
        out[a] = {tid.split("/")[0]: v for tid, v in r.task_scores().items()}
    drop_traces(res.loop.store.root)
    return out


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=10)
    rows = pool_map(job, list(range(args.seeds)), min(args.workers, 2))
    comps = ["no_memory", "fewshot_all", "fewshot_4", "fewshot_8", "fewshot_16", "fewshot_32", "fewshot_64"]
    dsets = [s.name for s in OOD9]
    mean = lambda r, a: float(np.mean([r[a][d] for d in dsets]))   # noqa: E731
    sel = [mean(r, "selected") for r in rows]
    best_comp = [max(mean(r, a) for a in comps) for r in rows]
    best_comp_name = [max(comps, key=lambda a: mean(r, a)) for r in rows]
    d_i = paired(best_comp, sel)
    per_ds = {d: {a: float(np.mean([r[a][d] for r in rows])) for a in ["selected"] + comps} for d in dsets}
    wins = [d for d in dsets if per_ds[d]["selected"] > max(per_ds[d][a] for a in comps)]
    ties = [d for d in dsets if per_ds[d]["selected"] == max(per_ds[d][a] for a in comps)]
    crit_i = d_i.get("lo", -1) > 0
    crit_ii = len(wins) >= 6
    verdict = ("PARTIAL (reproduced on CPU analogue: best average and best on >= 6/9; no ACE)" if crit_i and crit_ii
               else "NOT REPRODUCED" if d_i.get("mean_diff", 0) <= 0 else
               "PARTIAL (" + ("best average" if crit_i else "average ahead, CI includes 0") +
               f"; best on {len(wins)}/9 datasets)")
    avg = {a: summarize([mean(r, a) for r in rows]) for a in ["selected"] + comps}
    print(table([[a, fmt(avg[a])] for a in avg], ["system", "mean OOD accuracy (9 datasets)"]))
    print("selected - best comparator:", d_i, "| wins", len(wins), wins, "| ties", ties)
    print("verdict:", verdict)
    save("m10_ood9", {
        "claim": "Q6: the selected harness has the best average accuracy on 9 unseen datasets vs zero-shot and all "
                 "few-shot baselines, and the best accuracy on 6/9 [MH Table 5] (ACE not available)",
        "preregistration": "claims-audit-metaharness.md, Retry round 2: preregistration, P5",
        "config": {"seeds": args.seeds, "iterations": N_ITER, "k": K, "ood_datasets": dsets, "model": "MemoLM-A"},
        "mean_ood": avg, "selected_minus_best_comparator": d_i, "best_comparator_per_seed": best_comp_name,
        "per_dataset_mean": per_ds, "datasets_won": wins, "datasets_tied": ties, "criterion_i": crit_i,
        "criterion_ii": crit_ii, "verdict": verdict, "per_seed": rows})


if __name__ == "__main__":
    main()
