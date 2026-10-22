"""M10 - OOD transfer of the selected harness to 9 unseen datasets vs zero-shot and every few-shot variant
[MH Table 5; claim Q6, preregistration P5].

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

# the domain's 4 OOD specs plus 5 experiment-local ones that vary label count, cluster size, labels-in-input,
# hard fraction and confusability, as the paper's nine datasets vary
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
DATASETS = [s.name for s in OOD9]
COMPARATORS = ["no_memory", "fewshot_all", "fewshot_4", "fewshot_8", "fewshot_16", "fewshot_32", "fewshot_64"]
N_ITER, K = 20, 2


def job(seed: int) -> dict:
    search, ood = make_datasets(seed, search=SEARCH_SPECS, ood=OOD9)
    dom = MemoClassifyDomain(search, ood, seed=seed)
    res = run(dom, dom.seed_artifact("fewshot_all"), llm_task=dom.make_model("A"),
              config=Config(iterations=N_ITER, k=K, seed=seed, shadow_monitor=False, finalize=False),
              out_dir=fresh_dir("m10", f"s{seed}"), baselines=dom.baselines())
    ev = Evaluator(dom, dom.make_model("A"), workers=1, allow_sealed=True)
    out = {"seed": seed, "selected_name": res.meta["best_system"]}
    for a, art in {"selected": res.best, **dom.baselines(), **dom.comparators()}.items():
        out[a] = {tid.split("/")[0]: v for tid, v in ev.evaluate(art, "ood").task_scores().items()}
    drop_traces(res.loop.store.root)
    return out


def ood_mean(row: dict, arm: str) -> float:
    return float(np.mean([row[arm][d] for d in DATASETS]))


def main():
    args = parse_args(__doc__.splitlines()[0], default_seeds=10)
    rows = pool_map(job, list(range(args.seeds)), min(args.workers, 2))
    sel = [ood_mean(r, "selected") for r in rows]
    best_comp = [max(ood_mean(r, a) for a in COMPARATORS) for r in rows]
    best_comp_name = [max(COMPARATORS, key=lambda a: ood_mean(r, a)) for r in rows]
    d_i = paired(best_comp, sel)
    per_ds = {d: {a: float(np.mean([r[a][d] for r in rows])) for a in ["selected"] + COMPARATORS} for d in DATASETS}
    wins = [d for d in DATASETS if per_ds[d]["selected"] > max(per_ds[d][a] for a in COMPARATORS)]
    ties = [d for d in DATASETS if per_ds[d]["selected"] == max(per_ds[d][a] for a in COMPARATORS)]
    crit_i = d_i.get("lo", -1) > 0
    crit_ii = len(wins) >= 6
    verdict = ("PARTIAL (reproduced on CPU analogue: best average and best on >= 6/9; no ACE)" if crit_i and crit_ii
               else "NOT REPRODUCED" if d_i.get("mean_diff", 0) <= 0 else
               "PARTIAL (" + ("best average" if crit_i else "average ahead, CI includes 0") +
               f"; best on {len(wins)}/9 datasets)")
    avg = {a: summarize([ood_mean(r, a) for r in rows]) for a in ["selected"] + COMPARATORS}
    print(table([[a, fmt(avg[a])] for a in avg], ["system", "mean OOD accuracy (9 datasets)"]))
    print("selected - best comparator:", d_i, "| wins", len(wins), wins, "| ties", ties)
    print("verdict:", verdict)
    save("m10_ood9", {
        "claim": "Q6: the selected harness has the best average accuracy on 9 unseen datasets vs zero-shot and all "
                 "few-shot baselines, and the best accuracy on 6/9 [MH Table 5] (ACE not available)",
        "preregistration": "claims-audit-metaharness.md, Retry round 2: preregistration, P5",
        "config": {"seeds": args.seeds, "iterations": N_ITER, "k": K, "ood_datasets": DATASETS, "model": "MemoLM-A"},
        "mean_ood": avg, "selected_minus_best_comparator": d_i, "best_comparator_per_seed": best_comp_name,
        "per_dataset_mean": per_ds, "datasets_won": wins, "datasets_tied": ties, "criterion_i": crit_i,
        "criterion_ii": crit_ii, "verdict": verdict, "per_seed": rows})


if __name__ == "__main__":
    main()
