"""Shared builders for the RRSI tests."""
import json
from pathlib import Path

from rsi.core import LLM, Ledger, LLMResponse, Usage
from rsi.domains.agentqa import AgentQADomain, SimModel, make_suite
from rsi.domains.harnessworld import HarnessWorldMockLLM, make_domain
from rsi.rrsi import Config, RRSIRun, drive, result, run
from rsi.rrsi.mocks import AgentQAMockLLM

SMALL = dict(n_evolve=24, n_holdout=24, n_ood_per_family=6)
ACCEPT = json.dumps({"verdict": "accept", "reasons": [], "risk_notes": []})


def hw_domain(seed: int = 0, **sizes):
    return make_domain(seed=seed, **(sizes or SMALL))


def mechanism(dom, kind: str):
    """The first catalog mechanism of `kind` that the seed harness does not hold."""
    return next(m for m in dom.world.catalog.values() if m.kind == kind and m.id not in dom.world.seed_ids)


def hw_loop(out, *, seed: int = 0, T: int = 5, switches=None, hooks=None, **cfg) -> tuple:
    dom = hw_domain(seed)
    config = Config(T=T, workers=1, seed=seed, record_timestamps=False, **cfg)
    return dom, RRSIRun(dom, dom.seed_artifact(), out_dir=out, llm_propose=HarnessWorldMockLLM(dom.world),
                        config=config, switches=switches, hooks=hooks)


def hw_run(out, **kw) -> tuple:
    dom, r = hw_loop(out, **kw)
    return dom, result(r, drive(r))


def aq_suite():
    return make_suite(n_evolve=8, n_holdout=6, n_ood_per_family=2, seed=0)


def aq_run(out, *, T: int = 3, k: int = 2, workers: int = 2, task=None, propose=None, budget=None, hooks=None,
           **cfg) -> tuple:
    suite = aq_suite()
    dom = AgentQADomain(suite)
    config = Config(T=T, k=k, workers=workers, seed=0, record_timestamps=False, **cfg)
    res = run(dom, AgentQADomain.seed_artifact(), llm_task=task or SimModel(suite),
              llm_propose=propose or AgentQAMockLLM(), config=config, out_dir=out, budget=budget, hooks=hooks)
    return dom, res


def edit(component: str = "prompt", hypothesis: str = "h", **fields) -> dict:
    """A done() edit declaration with every required field."""
    return {"id": "C1", "component": component, "hypothesis": hypothesis, "targets_mode": "m",
            "predicted_affected": ["t1"], "retroactive_check": "(corrective) (preservative) (transfer)", **fields}


def edit_rows(**hypotheses) -> list:
    """History edit rows from component=hypothesis pairs, numbered C1, C2, ..."""
    return [{"id": f"C{i}", "component": c, "hypothesis": h} for i, (c, h) in enumerate(hypotheses.items(), 1)]


def reply(files: dict, edits: list, **head) -> str:
    """A rewrite-protocol done() reply: the JSON block, then whole files."""
    body = "\n".join(f"=== FILE: {p} ===\n{t}" for p, t in files.items())
    return "```json\n" + json.dumps({"action": "done", **head, "edits": edits}) + "\n```\n" + body


def read_json(path):
    return json.loads(Path(path).read_text())


def read_jsonl(path) -> list:
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def snapshot(d: Path) -> dict:
    """Everything a run decided, for comparing runs that must agree."""
    fr = read_json(d / "frontier.json")
    ledger = {n.id: {k: v for k, v in n.to_json().items() if k != "t"} for n in Ledger(d / "ledger.jsonl").nodes()}
    return {"ledger": ledger, "history": (d / "history.jsonl").read_text(),
            "attribution": (d / "attribution.jsonl").read_text(),
            "decisions": {p.parent.name: p.read_text() for p in sorted(d.glob("r*/decisions.json"))},
            "trajectory": fr["trajectory"], "S_star": fr["S_star"]}


def spy_splits(dom) -> list:
    """Record every (split, allow_sealed) read of the domain's task suite."""
    calls, orig = [], dom.tasks.split

    def spy(name, *, allow_sealed=False):
        calls.append((name, allow_sealed))
        return orig(name, allow_sealed=allow_sealed)
    dom.tasks.split = spy
    return calls


class Paid(LLM):
    """Charges a fixed USD price per call of the wrapped LLM, so a USD budget binds offline."""

    def __init__(self, inner: LLM, usd: float) -> None:
        super().__init__()
        self.inner, self.usd, self.name = inner, usd, f"paid-{inner.name}"

    def _complete(self, prompt, *, system, max_tokens, seed):
        r = self.inner._complete(prompt, system=system, max_tokens=max_tokens, seed=seed)
        return LLMResponse(r.text, Usage(1, r.usage.input_tokens, r.usage.output_tokens, self.usd, 0.0), r.model)
