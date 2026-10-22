"""X22 (retry round 2, P10): Gene vs Skill token length, measured on the vendor's own artifacts.
Run: python experiments/evomap/x22_token_lengths.py --src <scratchpad>/src --retry <scratchpad>/retry2/evomap
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from _common import save

from rsi.core.llm import estimate_tokens
from rsi.evomap import Gene, render_gene

SCRATCH = Path("/tmp/claude-0/-home-user-RSI/ebd00391-ba98-5b98-9125-83abd1dce979/scratchpad")


def to_gene(d: dict) -> Gene:
    return Gene(id=d.get("id", "gene_x"), signals_match=list(d.get("signals_match", [])),
                summary=str(d.get("summary", "")), strategy=[str(s) for s in d.get("strategy", [])],
                avoid=[str(a) if not isinstance(a, dict) else json.dumps(a) for a in
                       (d.get("avoid") or d.get("anti_patterns_text") or [])])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SCRATCH / "src"))
    ap.add_argument("--retry", default=str(SCRATCH / "retry2" / "evomap"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    src, rt = Path(a.src), Path(a.retry)
    genes = json.loads((src / "EvoMap__evolver/assets/gep/genes.seed.json").read_text())["genes"]
    ex = json.loads((rt / "skill2gep/examples/gene.example.json").read_text())
    genes.extend(ex if isinstance(ex, list) else [ex])
    g_rows = [{"id": g.get("id"), "tokens": estimate_tokens(render_gene(to_gene(g)))} for g in genes]
    skills = {"evolver": src / "EvoMap__evolver/SKILL.md", "skill2gep": rt / "skill2gep/SKILL.md",
              "pdf2gep": SCRATCH / "papers/evomap_secondary/pdf2gep_SKILL.md"}
    s_rows = [{"repo": k, "path": str(p), "tokens": estimate_tokens(p.read_text())} for k, p in skills.items()]
    gm = statistics.median(r["tokens"] for r in g_rows)
    sm = statistics.median(r["tokens"] for r in s_rows)
    v = {"gene_median": gm, "skill_median": sm, "ratio": sm / gm, "paper": {"gene": 230, "skill": 2500, "ratio": 10.9},
         "gene_in_band": 115 <= gm <= 345, "skill_in_band": 1250 <= sm <= 3750}
    v["pass"] = bool(v["gene_in_band"] and v["skill_in_band"])
    out = {"config": {"counter": "rsi.core.llm.estimate_tokens", "n_genes": len(g_rows), "n_skills": len(s_rows),
                      "preregistration": "docs/methods/evomap/claims-audit.md#retry-round-2-preregistration"},
           "genes": g_rows, "skills": s_rows, "verdict": v}
    save("x22_token_lengths", out, a.out)
    print(json.dumps(v, indent=1))


if __name__ == "__main__":
    main()
