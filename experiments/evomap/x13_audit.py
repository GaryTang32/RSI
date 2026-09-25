"""X13: auditability - every change traceable, tampering detectable (GEP spec §1, [doc]).

Protocol per seed: run the local loop (GeneWorld, safe mode, 60 cycles) into a LocalStore; export the archive and
import it again (hashes must round-trip identically); reconstruct the lineage (event parent chain == the ledger's
cycle chain; every capsule's gene known). Then apply tampering to fresh imports and check whether
``LocalStore.audit`` / the archive import detects it:

* ``field``      change one leaf (string char, number, list element) of a random gene / capsule / event /
                 validation report, WITHOUT re-stamping (detected by asset_id);
* ``restamped``  the same edit on a random non-final event, then re-stamped so its own asset_id verifies
                 (detected only by the event hash chain ``meta.parent_asset_id``);
* ``delete`` / ``swap``  remove an event or swap two adjacent events (detected by the parent chain);
* ``archive_bytes``  flip one byte inside an archive member (detected by checksum.sha256).
Reported limitation: a re-stamped edit of a gene/capsule, or a rewrite of the whole chain suffix, needs an
external anchor (hub-held asset ids, a published head hash) - measured as ``restamped_gene``.

Run: python experiments/evomap/x13_audit.py [--llm sim|claude:haiku] [--seeds N] [--quick]
"""
from __future__ import annotations

import io
import random
import tarfile
import tempfile
from pathlib import Path

from _common import cached_llm, fmt, live, parse_args, pmap, save, summarize

import numpy as np

from rsi.evomap import Config, LocalStore, run
from rsi.evomap.assets import ASSET_CLASSES

TAMPERS = ("field", "restamped", "restamped_gene", "delete", "swap", "archive_bytes")


def mutate_leaf(d: dict, rng: random.Random) -> bool:
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


def build(seed, args, root):
    if live(args):
        from rsi.domains.katas import KatasDomain, seed_harness
        dom = KatasDomain()
        llm = cached_llm(args.llm)
        return run(dom, seed_harness(), llm_task=llm, llm_propose=llm, config=Config(cycles=6, seed=seed),
                   out_dir=root)
    from rsi.domains.geneworld import GeneWorldModel, GeneWorldProposer, WorldConfig, make_domain
    dom = make_domain(WorldConfig(seed=seed))
    return run(dom, dom.seed_artifact(), llm_task=GeneWorldModel(0.0), llm_propose=GeneWorldProposer(dom.world, 0.6),
               config=Config(cycles=30 if args.quick else 60, seed=seed), out_dir=root)


def one(job):
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
    n = 20 if args.quick else 60
    for _ in range(n):
        for t in TAMPERS:
            s2 = LocalStore.import_archive(arch)
            if t == "archive_bytes":
                with tarfile.open(arch) as tar:
                    mem = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
                name = rng.choice(sorted(k for k in mem if k != "checksum.sha256" and mem[k]))
                b = bytearray(mem[name])
                i = rng.randrange(len(b))
                b[i] = (b[i] + 1) % 256
                mem[name] = bytes(b)
                bad = root / "bad.tgz"
                with tarfile.open(bad, "w:gz") as tar:
                    for k, v in mem.items():
                        info = tarfile.TarInfo(k)
                        info.size = len(v)
                        tar.addfile(info, io.BytesIO(v))
                try:
                    LocalStore.import_archive(bad)
                    detected[t].append(False)
                except Exception:  # noqa: BLE001 - any refusal counts as detection
                    detected[t].append(True)
                continue
            if t == "field":
                pools = [("gene", list(s2.genes)), ("capsule", list(s2.capsules)), ("event", range(len(s2.events))),
                         ("report", range(len(s2.reports)))]
                kind, keys = rng.choice([p for p in pools if len(p[1])])
                key = rng.choice(list(keys))
                obj = {"gene": s2.genes, "capsule": s2.capsules, "event": s2.events, "report": s2.reports}[kind][key]
                d = obj.to_dict()
                mutate_leaf(d, rng)
                new = ASSET_CLASSES[d["type"]].from_dict(d)
                {"gene": s2.genes, "capsule": s2.capsules, "event": s2.events, "report": s2.reports}[kind][key] = new
            elif t == "restamped":
                i = rng.randrange(len(s2.events) - 1)
                d = s2.events[i].to_dict()
                mutate_leaf(d, rng)
                d.pop("asset_id", None)
                s2.events[i] = ASSET_CLASSES["EvolutionEvent"].from_dict(d).stamp()
                if s2.events[i].id != d.get("id"):
                    s2.events[i].id = d["id"]
            elif t == "restamped_gene":
                gid = rng.choice(list(s2.genes))
                d = s2.genes[gid].to_dict()
                mutate_leaf(d, rng)
                d.pop("asset_id", None)
                g = ASSET_CLASSES["Gene"].from_dict(d).stamp()
                s2.genes.pop(gid)
                s2.genes[g.id] = g
            elif t == "delete":
                del s2.events[rng.randrange(len(s2.events) - 1)]
            elif t == "swap":
                i = rng.randrange(len(s2.events) - 1)
                s2.events[i], s2.events[i + 1] = s2.events[i + 1], s2.events[i]
            detected[t].append(not s2.audit().ok)
    return {"seed": seed, "roundtrip_identical": roundtrip, "lineage_exact": chain, "ledger_chain_matches": ledger_chain,
            "capsules_linked": caps_linked, "n_events": len(st.events), "n_genes": len(st.genes),
            **{f"detect_{t}": float(np.mean(v)) for t, v in detected.items()}}


def main():
    args = parse_args(__doc__.split("\n")[0], default_seeds=10)
    rows = pmap(one, [(s, args) for s in range(args.seeds)], args.workers)
    keys = [k for k in rows[0] if k != "seed"]
    summ = {k: summarize([float(r[k]) for r in rows]) for k in keys}
    out = {"config": {"llm": args.llm, "seeds": args.seeds, "tampers": TAMPERS}, "raw": rows, "summary": summ,
           "verdict": {"roundtrip_identical": all(r["roundtrip_identical"] for r in rows),
                       "lineage_exact": all(r["lineage_exact"] and r["ledger_chain_matches"] for r in rows),
                       "byte_and_field_tamper_detection_100pct": all(
                           summ[f"detect_{t}"]["mean"] == 1.0 for t in ("field", "archive_bytes", "delete", "swap")),
                       "restamped_event_detected_by_hash_chain": summ["detect_restamped"]["mean"],
                       "restamped_gene_needs_external_anchor": summ["detect_restamped_gene"]["mean"]}}
    save("x13_audit", out, args.out)
    for k in keys:
        print(f"  {k:26s} {fmt(summ[k])}")
    print("verdict:", out["verdict"])


if __name__ == "__main__":
    main()
