"""Shared helpers for the autoresearch experiment scripts."""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from rsi.core import CachedLLM, ClaudeCLI, summarize_runs  # noqa: E402

RESULTS = Path(os.environ.get("RSI_AR_RESULTS", ROOT / "results" / "autoresearch"))
CACHE = Path(os.environ.get("RSI_AR_CACHE", ROOT / ".rsi_cache" / "autoresearch"))  # a fresh dir per live re-run
SCRATCH = Path(os.environ.get("RSI_AR_SCRATCH", "/tmp/rsi_autoresearch_runs"))
CONST_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)(\s*=\s*)([^#\n]+?)(\s*(#.*)?)$", re.M)


def parser(description: str, seeds: int = 3) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--llm", default="sim",
                    help="sim (scripted offline agent) | claude:haiku (live research agent) | scripted (offline "
                         "MockLLM through the same LLM-agent code path as a live run)")
    ap.add_argument("--seeds", type=int, default=seeds)
    ap.add_argument("--quick", action="store_true", help="tiny settings for a smoke check")
    ap.add_argument("--workers", type=int, default=2, help="parallel seeds (processes)")
    return ap


def is_live(spec: str | None) -> bool:
    return spec not in ("sim", "mock", "", None)


def propose_llm(spec: str | None, pool=None, seed: int = 0):
    """None for ``sim``; an offline MockLLM over ``pool`` for ``scripted``; a cached Claude for ``claude:<model>``."""
    if not is_live(spec):
        return None
    if spec == "scripted":
        from rsi.autoresearch import scripted_llm

        if pool is None:
            raise SystemExit("--llm scripted needs the task's edit pool")
        return scripted_llm(pool, seed=seed)
    kind, _, model = spec.partition(":")
    if kind != "claude":
        raise SystemExit(f"unsupported --llm {spec!r}; use sim, scripted or claude:<model>")
    return CachedLLM(ClaudeCLI(model or "haiku", timeout_s=300), CACHE)


def suffix(spec: str, quick: bool) -> str:
    """Result-name suffix: '' (offline), '_live' or '_scripted', plus '_quick'."""
    kind = "" if not is_live(spec) else "_scripted" if spec == "scripted" else "_live"
    return kind + ("_quick" if quick else "")


def slug(spec: str) -> str:
    return spec.replace(":", "_")


def research_agent(spec: str, pool, *, seed: int = 0, **mock_kw):
    """``(agent, llms)``: MockResearchAgent for ``sim``, else an LLMResearchAgent on :func:`propose_llm`."""
    from rsi.autoresearch import LLMResearchAgent, MockResearchAgent
    from rsi.core import RewriteEditor

    llm = propose_llm(spec, pool=pool, seed=seed)
    if llm is None:
        return MockResearchAgent(pool, seed=seed, **mock_kw), []
    return LLMResearchAgent(RewriteEditor(llm)), [llm]


def usage_of(llms) -> dict:
    tot = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for llm in llms:
        t = llm.meter.total()
        for k in tot:
            tot[k] += getattr(t, k)
    return tot


def ci(values) -> dict:
    vals = [float(v) for v in values if v is not None and v == v]
    return summarize_runs(vals) if vals else {"mean": float("nan"), "lo": float("nan"), "hi": float("nan"), "n": 0}


def _default(o):
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def write(name: str, payload: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    p = RESULTS / f"{name}.json"
    payload = {"experiment": name, "written_at": time.strftime("%Y-%m-%d %H:%M:%S"), **payload}
    p.write_text(json.dumps(payload, indent=1, default=_default))
    print(f"wrote {p}")
    return p


def report(name: str, out: dict, shown=None, **dump_kw) -> None:
    """Write the result file, then print ``shown`` (default: the verdict) as JSON."""
    write(name, out)
    print(json.dumps(out["verdict"] if shown is None else shown, indent=1, **dump_kw))


def pool_map(fn, items, workers: int):
    """``fn`` over ``items`` in a forked process pool, or serially."""
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    import multiprocessing as mp

    with mp.get_context("fork").Pool(min(workers, len(items))) as pool:
        return pool.map(fn, items)


def plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as p

    return p


def save_fig(fig, name: str) -> Path:
    fig.tight_layout()
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.png"
    fig.savefig(path, dpi=110)
    return path


def running_best(traj: list[dict]) -> list[float]:
    out, best = [], None
    for r in traj:
        if r["status"] == "keep" and r["metric"] is not None and (best is None or r["metric"] < best):
            best = r["metric"]
        out.append(best)
    return out


def time_to(traj, target: float) -> float:
    """First time of a ``(t, best)`` trajectory whose best reaches ``target`` (lower is better)."""
    return next((t for t, v in traj if v is not None and v <= target + 1e-12), float("nan"))


def setk(text: str, k: str, v) -> str:
    """Set the constant ``k`` of a train.py to ``repr(v)``, keeping its comment."""
    out, n = re.subn(rf"^{k}(\s*=\s*)[^#\n]+?(\s*(#.*)?)$", lambda m: f"{k}{m.group(1)}{v!r}{m.group(2)}", text,
                     count=1, flags=re.M)
    assert n == 1, k
    return out


def constants(src: str) -> dict:
    return {m.group(1): m.group(3).strip() for m in CONST_RE.finditer(src)}


def with_constants(src: str, consts: dict) -> str:
    def sub(m):
        k = m.group(1)
        return f"{k}{m.group(2)}{consts[k]}{m.group(4)}" if k in consts else m.group(0)

    return CONST_RE.sub(sub, src)


def claim(path: Path) -> bool:
    """Atomically create ``path``; False if it exists (lets parallel slots share one seed list)."""
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL))
    except FileExistsError:
        return False
    return True


def cached_json(path: Path, compute):
    """``path`` as JSON, computed and saved first if missing (resumable nights)."""
    if not path.exists():
        out = compute()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out, default=str))
    return json.loads(path.read_text())


def insert_audit_code(code: str) -> str:
    """tinylm's prepare.py with measurement-only ``code`` inserted just before the eval-time line."""
    from rsi.domains.tinylm.task import DOMAIN_DIR

    anchor = '    record["eval_seconds"] = clock() - t0\n'
    src = (DOMAIN_DIR / "prepare.py").read_text()
    assert src.count(anchor) == 1
    return src.replace(anchor, code + anchor)


def hardened_audit(out) -> dict:
    """Hardened semantics for a run scored in audit mode (a flagged record is a crash); returns its audit dict."""
    flags = out.meta.pop("audit_flags", None)
    if flags and out.metric is not None:
        out.crash_reason, out.metric = flags[0], None
    return dict((out.record or {}).get("audit") or {})


def scored_keeps(res, scored: list[dict]) -> tuple[list, list]:
    """Decided ledger nodes, and each keep with the audit record of the run that scored it (None if unmatched)."""
    by_key = {}
    for h in scored:
        if h["metric"] is not None:
            by_key.setdefault((h["artifact_id"], round(h["metric"], 12)), h)
    nodes = [nd for nd in res.ledger.nodes() if nd.status in ("keep", "discard", "crash", "rejected")]
    return nodes, [(nd, by_key.get((nd.artifact_id, round(nd.score, 12)))) for nd in nodes if nd.status == "keep"]
