"""X13: auditability - every change traceable, tampering detectable (GEP spec §1, [doc]).
Run: python experiments/evomap/x13_audit.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import io
import random
import tarfile
import tempfile
from pathlib import Path

from _common import cached_llm, live, parse_args, pmap, print_stats, save, summarize_columns

import numpy as np

from rsi.evomap import Config, LocalStore, run
from rsi.evomap.assets import ASSET_CLASSES

TAMPERS = ("field", "restamped", "restamped_gene", "delete", "swap", "archive_bytes")


def mutate_leaf(d: dict, rng: random.Random) -> bool:
    """Change one random string, number or list leaf of an asset dict (never its asset_id or type)."""
    paths = []

    def walk(x, path):
        if isinstance(x, dict):
            for k, v in x.items():
                if k not in ("asset_id", "type"):
                    walk(v, path + [k])
        elif isinstance(x, list):
            for i, v in enumerate(x):
                walk(v, path + [i])
        elif isinstance(x, (str, int, float)) and not isinstance(x, bool):
            paths.append(path)
    walk(d, [])
    if not paths:
        return False
    path = rng.choice(paths)
    cur = d
    for p in path[:-1]:
        cur = cur[p]
    v = cur[path[-1]]
    if isinstance(v, str):
        cur[path[-1]] = (v[:-1] + ("X" if not v.endswith("X") else "Y")) if v else "X"
    elif isinstance(v, int):
        cur[path[-1]] = v + 1
    else:
        cur[path[-1]] = v * 1.01 + 0.001
    return True


def build(seed: int, args, root: Path):
    if live(args):
        from rsi.domains.katas import KatasDomain, seed_harness
        dom = KatasDomain()
        llm = cached_llm(args.llm)
        return run(dom, seed_harness(), llm_task=llm, llm_propose=llm, config=Config(cycles=6, seed=seed), out_dir=root)
    from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, WorldConfig, make_domain
    dom = make_domain(WorldConfig(seed=seed))
    return run(dom, dom.seed_artifact(), llm_task=GeneWorldModel(0.0), llm_propose=GeneWorldProposer(dom.world, 0.6),
               config=Config(cycles=30 if args.quick else 60, seed=seed), out_dir=root)


def archive_tamper_detected(arch: Path, bad: Path, rng: random.Random) -> bool:
    """Flip one byte of one archive member and re-pack; any import refusal counts as detection."""
    with tarfile.open(arch) as tar:
        mem = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    name = rng.choice(sorted(k for k in mem if k != "checksum.sha256" and mem[k]))
    b = bytearray(mem[name])
    i = rng.randrange(len(b))
    b[i] = (b[i] + 1) % 256
    mem[name] = bytes(b)
    with tarfile.open(bad, "w:gz") as tar:
        for k, v in mem.items():
            info = tarfile.TarInfo(k)
            info.size = len(v)
            tar.addfile(info, io.BytesIO(v))
    try:
        LocalStore.import_archive(bad)
        return False
    except Exception:  # noqa: BLE001
        return True


def tamper(st: LocalStore, kind: str, rng: random.Random) -> None:
    if kind == "field":                 # edit without re-stamping: caught by asset_id
        containers = {"gene": st.genes, "capsule": st.capsules, "event": st.events, "report": st.reports}
        c = containers[rng.choice([k for k, v in containers.items() if len(v)])]
        key = rng.choice(list(c) if isinstance(c, dict) else list(range(len(c))))
        d = c[key].to_dict()
        mutate_leaf(d, rng)
        c[key] = ASSET_CLASSES[d["type"]].from_dict(d)
    elif kind == "restamped":           # a re-stamped non-final event: caught only by the parent hash chain
        i = rng.randrange(len(st.events) - 1)
        d = st.events[i].to_dict()
        mutate_leaf(d, rng)
        d.pop("asset_id", None)
        st.events[i] = ASSET_CLASSES["EvolutionEvent"].from_dict(d).stamp()
        if st.events[i].id != d.get("id"):
            st.events[i].id = d["id"]
    elif kind == "restamped_gene":      # needs an external anchor (hub-held ids, a published head hash)
        gid = rng.choice(list(st.genes))
        d = st.genes[gid].to_dict()
        mutate_leaf(d, rng)
        d.pop("asset_id", None)
        g = ASSET_CLASSES["Gene"].from_dict(d).stamp()
        st.genes.pop(gid)
        st.genes[g.id] = g
    elif kind == "delete":
        del st.events[rng.randrange(len(st.events) - 1)]
    elif kind == "swap":
        i = rng.randrange(len(st.events) - 1)
        st.events[i], st.events[i + 1] = st.events[i + 1], st.events[i]


def one(job) -> dict:
    seed, args = job
    rng = random.Random(f"x13-{seed}")
    root = Path(tempfile.mkdtemp(prefix="x13_"))
    res = build(seed, args, root / "run")
    arch = root / "run" / "store.gepx.tgz"
    st = LocalStore.import_archive(arch)
    orig = res.meta["agent"].store
    roundtrip = (st.genes_json() == orig.genes_json() and [e.asset_id for e in st.events] ==
                 [e.asset_id for e in orig.events] and {c.asset_id for c in st.capsules.values()} ==
                 {c.asset_id for c in orig.capsules.values()})
    chain = [e.id for e in st.lineage(st.events[-1].id)] == [e.id for e in st.events]
    nodes = res.ledger.nodes(kind="cycle")
    cyc = [n for n in nodes if n.status != "skipped"]     # evolution events = the trunk of the ledger tree
    ledger_chain = [n.id for n in cyc] == [e.id for e in orig.events] and all(
        n.parent == (cyc[i - 1].id if i else None) for i, n in enumerate(cyc)) and all(
        n.parent in {None} | {c.id for c in cyc} for n in nodes if n.status == "skipped")
    caps_linked = all(c.gene in st.genes or any(c.id == e.capsule_id for e in st.events) for c in st.capsules.values())
    detected = {t: [] for t in TAMPERS}
    for _ in range(20 if args.quick else 60):
        for t in TAMPERS:
            if t == "archive_bytes":
                detected[t].append(archive_tamper_detected(arch, root / "bad.tgz", rng))
                continue
            s2 = LocalStore.import_archive(arch)
            tamper(s2, t, rng)
            detected[t].append(not s2.audit().ok)
    return {"seed": seed, "roundtrip_identical": roundtrip, "lineage_exact": chain,
            "ledger_chain_matches": ledger_chain, "capsules_linked": caps_linked, "n_events": len(st.events),
            "n_genes": len(st.genes),
            **{f"detect_{t}": float(np.mean(v)) for t, v in detected.items()}}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=10)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)
    summ = summarize_columns(rows)
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "tampers": TAMPERS}, "raw": rows, "summary": summ,
           "verdict": {"roundtrip_identical": all(r["roundtrip_identical"] for r in rows),
                       "lineage_exact": all(r["lineage_exact"] and r["ledger_chain_matches"] for r in rows),
                       "byte_and_field_tamper_detection_100pct": all(
                           summ[f"detect_{t}"]["mean"] == 1.0 for t in ("field", "archive_bytes", "delete", "swap")),
                       "restamped_event_detected_by_hash_chain": summ["detect_restamped"]["mean"],
                       "restamped_gene_needs_external_anchor": summ["detect_restamped_gene"]["mean"]}}
    save("x13_audit", out, args.out)
    print_stats(summ, 26)
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
