"""Stage-B live re-run of autoresearch from scratch with the CURRENT code.

The Stage-A live run (validation/autoresearch/tinylm_live) predates two changes:
the reply-debris sanitizer in LLMResearchAgent (Stage A) and the monitor-budget
fix (Stage B). It also clipped prompts at 6,000 chars in the trace. This re-run
starts again from the untouched tinylm seed train.py, a fresh out_dir and a
FRESH LLM cache, with noise calibration and fresh-seed re-evals, so the trace
holds the full prompts and every step can be re-audited.

    python experiments/autoresearch/validate_rerun.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rsi.autoresearch import Config, run  # noqa: E402
from rsi.core import CachedLLM, ClaudeCLI  # noqa: E402

sys.path.insert(0, str(ROOT / "experiments" / "autoresearch"))
from validate_autoresearch import VAL, _task, summarise_dir  # noqa: E402


def live_b(max_usd: float = 1.3) -> dict:
    out = VAL / "tinylm_live_b"
    cache = VAL / ".cache_tinylm_live_b"
    assert not out.exists() and not cache.exists(), "a from-scratch run needs a fresh out_dir and cache"
    t0 = time.time()
    llm = CachedLLM(ClaudeCLI("haiku", timeout_s=300), cache)
    cfg = Config(max_experiments=10, mode="hardened", keep_rule="strict", noise_runs=3, reeval_seeds=3,
                 max_usd=max_usd, tag="validate-live-b", seed=0)
    res = run(_task(), llm_propose=llm, config=cfg, out_dir=out)
    return summarise_dir("tinylm_live_b", out, {"agent": "LLMResearchAgent(RewriteEditor(claude-cli haiku)) "
                                                         "with reply sanitizer",
                                                "cache_dir": str(cache.relative_to(ROOT)),
                                                "cache_hits": llm.hits, "cache_misses": llm.misses},
                         wall_s=round(time.time() - t0, 1))


if __name__ == "__main__":
    rec = live_b()
    print(json.dumps({k: rec[k] for k in ("run", "stop_reason", "wall_s", "final_val_bpb", "counters",
                                          "usage_total", "consistency", "cache_hits", "cache_misses")},
                     indent=1, default=str))
