"""LocalStore: an agent's asset store (spec §5.10, §3.8, §9.1).

Layout under ``root`` (all optional - ``root=None`` keeps everything in memory
for population simulations)::

    genes.json                {version, genes[]}           (rewritten on upsert)
    capsules.jsonl            append-only upserts (last line per id wins)
    events.jsonl              append-only EvolutionEvents + ValidationReports, parent chain
    memory_graph.jsonl        MemoryGraphEvents
    external_candidates.jsonl QUARANTINE zone (fetched / ingested assets, never executed directly)
    failed_capsules.json      failed attempts with non-empty diffs
    distiller_log.jsonl       distillation audit log

Integrity: every gene / capsule / event carries a content ``asset_id``;
:meth:`LocalStore.audit` recomputes all ids and checks the event parent chain
(no gaps, no forks, every capsule's gene known). :meth:`export_archive` writes a
``.gepx``-like tar.gz with ``manifest.json`` and ``checksum.sha256``;
:meth:`import_archive` verifies both before loading.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .assets import Capsule, Clock, EvolutionEvent, Gene, ValidationReport
from .hashing import SCHEMA_VERSION, sha256_text, verify_asset_id
from .memory import MemoryGraph

EXTERNAL_CONFIDENCE_FACTOR = 0.6


@dataclass
class AuditReport:
    ok: bool
    problems: list[str] = field(default_factory=list)
    n_checked: int = 0

    def to_dict(self) -> dict:
        return {"ok": self.ok, "problems": self.problems, "n_checked": self.n_checked}


class LocalStore:
    def __init__(self, root: Optional[str | Path] = None, *, node_id: str = "node_local",
                 clock: Optional[Clock] = None) -> None:
        self.root = Path(root) if root else None
        self.node_id = node_id
        self.clock = clock or Clock()
        self.genes: dict[str, Gene] = {}
        self.capsules: dict[str, Capsule] = {}
        self.events: list[EvolutionEvent] = []
        self.reports: list[ValidationReport] = []
        self.failed_capsules: list[dict] = []
        self.external: list[dict] = []
        self.distiller_log: list[dict] = []
        self.solidify_count = 0
        self._counter = 0
        self.memory = MemoryGraph(self.root / "memory_graph.jsonl" if self.root else None, clock=self.clock)
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)
            self._load()

    # ------------------------------------------------------------------ ids
    def new_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self.node_id}_{self._counter:06d}"

    # ------------------------------------------------------------------ persistence
    def _append(self, name: str, obj: dict) -> None:
        if self.root:
            with (self.root / name).open("a") as f:
                f.write(json.dumps(obj, default=str) + "\n")

    def _write_genes(self) -> None:
        if self.root:
            (self.root / "genes.json").write_text(self.genes_json())

    def _load(self) -> None:
        r = self.root
        if (r / "genes.json").exists():
            for d in json.loads((r / "genes.json").read_text()).get("genes", []):
                g = Gene.from_dict(d)
                self.genes[g.id] = g
        for name, handler in (("capsules.jsonl", self._load_capsule), ("events.jsonl", self._load_event),
                              ("external_candidates.jsonl", self.external.append),
                              ("distiller_log.jsonl", self.distiller_log.append)):
            p = r / name
            if p.exists():
                for line in p.read_text().splitlines():
                    if line.strip():
                        handler(json.loads(line))
        if (r / "failed_capsules.json").exists():
            self.failed_capsules = json.loads((r / "failed_capsules.json").read_text())
        state = r / "state.json"
        if state.exists():
            s = json.loads(state.read_text())
            self._counter, self.solidify_count = s.get("counter", 0), s.get("solidify_count", 0)

    def _load_capsule(self, d: dict) -> None:
        c = Capsule.from_dict(d)
        self.capsules[c.id] = c

    def _load_event(self, d: dict) -> None:
        if d.get("type") == "EvolutionEvent":
            self.events.append(EvolutionEvent.from_dict(d))
        elif d.get("type") == "ValidationReport":
            self.reports.append(ValidationReport.from_dict(d))

    def _save_state(self) -> None:
        if self.root:
            (self.root / "state.json").write_text(json.dumps({"counter": self._counter,
                                                              "solidify_count": self.solidify_count}))

    # ------------------------------------------------------------------ genes / capsules
    def has_gene(self, gid: str) -> bool:
        return gid in self.genes

    def upsert_gene(self, g: Gene) -> Gene:
        g.stamp()
        self.genes[g.id] = g
        self._write_genes()
        return g

    def remove_gene(self, gid: str) -> None:
        self.genes.pop(gid, None)
        self._write_genes()

    def upsert_capsule(self, c: Capsule) -> Capsule:
        c.stamp()
        self.capsules[c.id] = c
        self._append("capsules.jsonl", c.to_dict())
        return c

    def append_failed_capsule(self, fc: dict) -> None:
        self.failed_capsules.append(fc)
        if self.root:
            (self.root / "failed_capsules.json").write_text(json.dumps(self.failed_capsules[-500:], default=str))

    def genes_json(self) -> str:
        return json.dumps({"version": SCHEMA_VERSION, "genes": [self.genes[k].to_dict() for k in sorted(self.genes)]},
                          sort_keys=True)

    def gene_library_version(self) -> str:
        return "glib_" + sha256_text(self.genes_json())[:16]

    # ------------------------------------------------------------------ events
    def last_event_id(self) -> Optional[str]:
        return self.events[-1].id if self.events else None

    def append_event(self, e: EvolutionEvent) -> EvolutionEvent:
        """Append with the ``parent`` id chain (GEP) plus a hash chain: ``meta.parent_asset_id`` pins the
        parent's content, so re-stamping a modified earlier event breaks its successor."""
        if e.parent is None and self.events:
            e.parent = self.events[-1].id
        if self.events:
            e.meta = {**(e.meta or {}), "parent_asset_id": self.events[-1].asset_id}
        e.stamp()
        self.events.append(e)
        self._append("events.jsonl", e.to_dict())
        self.solidify_count += 1
        self._save_state()
        return e

    def append_report(self, vr: ValidationReport) -> None:
        vr.stamp()
        self.reports.append(vr)
        self._append("events.jsonl", vr.to_dict())

    def recent_events(self, n: int = 80) -> list[EvolutionEvent]:
        return self.events[-n:]

    def success_streak(self, capsule_id: str) -> int:
        """Consecutive trailing successes among the events that reference ``capsule_id``
        (failed events carry no capsule id, so they do not break the streak)."""
        n = 0
        for e in reversed(self.events):
            if e.capsule_id != capsule_id:
                continue
            if e.outcome.get("status") != "success":
                break
            n += 1
        return max(1, n)

    def lineage(self, event_id: str) -> list[EvolutionEvent]:
        by = {e.id: e for e in self.events}
        out, cur = [], event_id
        while cur is not None and cur in by:
            out.append(by[cur])
            cur = by[cur].parent
        return list(reversed(out))

    # ------------------------------------------------------------------ quarantine (external candidates)
    def stage_external(self, asset: dict, source: str, *, hub_report_id: Optional[str] = None) -> dict:
        """Stage a fetched/ingested asset. Rejects an asset_id mismatch; lowers Capsule
        confidence by 0.6; records provenance and a memory-graph event. Never executes it."""
        if "asset_id" in asset and not verify_asset_id(asset):
            raise ValueError("asset_id integrity check failed")
        staged = copy.deepcopy(asset)
        if staged.get("type") == "Capsule" and isinstance(staged.get("confidence"), (int, float)):
            staged["confidence"] = round(staged["confidence"] * EXTERNAL_CONFIDENCE_FACTOR, 4)
        rec = {"asset": staged, "a2a": {"status": "external_candidate", "source": source,
                                        "received_at": self.clock.iso(),
                                        "confidence_factor": EXTERNAL_CONFIDENCE_FACTOR},
               "original_asset_id": asset.get("asset_id"), "hub_report_id": hub_report_id}
        self.external.append(rec)
        self._append("external_candidates.jsonl", rec)
        self.memory.record("external_candidate", asset_id=asset.get("asset_id"), source=source,
                           asset_type=asset.get("type"), hub_report_id=hub_report_id)
        return rec

    def resolve_external(self, original_asset_id: str, status: str, reason: str = "") -> None:
        for rec in self.external:
            if rec.get("original_asset_id") == original_asset_id and rec["a2a"]["status"] == "external_candidate":
                rec["a2a"]["status"] = status
                rec["a2a"]["reason"] = reason
                self._append("external_candidates.jsonl", {**rec, "_update": True})

    def promote_external(self, original_asset_id: str, *, validated: bool) -> Optional[Gene]:
        """a2a_promote: refuses without local validation; a Gene never overwrites a local
        gene with the same id; stamps provenance and re-computes asset_id."""
        if not validated:
            raise PermissionError("Refusing to promote without --validated (local verification must be done first)")
        for rec in reversed(self.external):
            a = rec["asset"]
            if rec.get("original_asset_id") == original_asset_id and a.get("type") == "Gene":
                g = Gene.from_dict(a)
                if g.id in self.genes:
                    g.id = g.id + "_ext"
                g.provenance = {**(g.provenance or {}), "kind": "external", "source": rec["a2a"]["source"],
                                "parent_asset_id": original_asset_id}
                g.parent = original_asset_id
                self.upsert_gene(g)
                self.resolve_external(original_asset_id, "promoted")
                return g
        return None

    # ------------------------------------------------------------------ audit
    def audit(self) -> AuditReport:
        problems: list[str] = []
        n = 0
        for g in self.genes.values():
            n += 1
            if not g.verify():
                problems.append(f"gene {g.id}: asset_id mismatch")
        for c in self.capsules.values():
            n += 1
            if not c.verify():
                problems.append(f"capsule {c.id}: asset_id mismatch")
        ids = set()
        prev, prev_aid = None, None
        for e in self.events:
            n += 1
            if not e.verify():
                problems.append(f"event {e.id}: asset_id mismatch")
            if e.id in ids:
                problems.append(f"event {e.id}: duplicate id")
            if e.parent != prev:
                problems.append(f"event {e.id}: parent {e.parent!r} != previous event {prev!r} (gap, fork or reorder)")
            pa = (e.meta or {}).get("parent_asset_id")
            if prev is not None and pa is not None and pa != prev_aid:
                problems.append(f"event {e.id}: hash chain broken (parent content changed)")
            ids.add(e.id)
            prev, prev_aid = e.id, e.asset_id
        for vr in self.reports:
            n += 1
            if not vr.verify():
                problems.append(f"validation report {vr.id}: asset_id mismatch")
        caps_in_events = {e.capsule_id for e in self.events if e.capsule_id}
        for c in self.capsules.values():
            if c.id not in caps_in_events:
                problems.append(f"capsule {c.id}: no event references it")
        return AuditReport(not problems, problems, n)

    # ------------------------------------------------------------------ archive
    def _archive_members(self) -> dict[str, bytes]:
        mem: dict[str, bytes] = {
            "genes/genes.json": self.genes_json().encode(),
            "capsules/capsules.jsonl": "".join(json.dumps(c.to_dict(), sort_keys=True) + "\n"
                                               for c in self.capsules.values()).encode(),
            "events/events.jsonl": "".join(json.dumps(e.to_dict(), sort_keys=True) + "\n"
                                           for e in [*self.events]).encode(),
            "events/validation_reports.jsonl": "".join(json.dumps(v.to_dict(), sort_keys=True) + "\n"
                                                       for v in self.reports).encode(),
            "memory/memory_graph.jsonl": "".join(json.dumps(m, sort_keys=True, default=str) + "\n"
                                                 for m in self.memory.events).encode(),
        }
        n_ev = len(self.events)
        succ = sum(1 for e in self.events if e.outcome.get("status") == "success")
        manifest = {"gep_version": "1.0.0", "schema_version": SCHEMA_VERSION, "created_at": self.clock.iso(),
                    "head_event_asset_id": self.events[-1].asset_id if self.events else None,
                    "gene_library_version": self.gene_library_version(),
                    "agent_id": self.node_id, "agent_name": self.node_id,
                    "statistics": {"total_events": n_ev, "total_genes": len(self.genes),
                                   "total_capsules": len(self.capsules),
                                   "success_rate": succ / n_ev if n_ev else 0.0,
                                   "memory_graph_entries": len(self.memory.events)},
                    "source": {"platform": "rsi.evomap", "version": "1"}}
        mem["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=1).encode()
        mem["checksum.sha256"] = "".join(f"{hashlib.sha256(mem[k]).hexdigest()}  {k}\n" for k in sorted(mem)).encode()
        return mem

    def export_archive(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, "w:gz") as tar:
            for name, data in sorted(self._archive_members().items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mtime = 0
                tar.addfile(info, io.BytesIO(data))
        return path

    @classmethod
    def import_archive(cls, path: str | Path, root: Optional[str | Path] = None, *, verify: bool = True) -> "LocalStore":
        with tarfile.open(path, "r:gz") as tar:
            mem = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
        if verify:
            lines = mem.get("checksum.sha256", b"").decode().splitlines()
            listed = {}
            for ln in lines:
                h, _, name = ln.partition("  ")
                listed[name] = h
            for name, data in mem.items():
                if name == "checksum.sha256":
                    continue
                if listed.get(name) != hashlib.sha256(data).hexdigest():
                    raise ValueError(f"archive checksum mismatch: {name}")
        manifest = json.loads(mem["manifest.json"])
        st = cls(root, node_id=manifest.get("agent_id", "imported"))
        for d in json.loads(mem["genes/genes.json"]).get("genes", []):
            g = Gene.from_dict(d)
            st.genes[g.id] = g
        for line in mem["capsules/capsules.jsonl"].decode().splitlines():
            if line.strip():
                st._load_capsule(json.loads(line))
        for line in mem["events/events.jsonl"].decode().splitlines():
            if line.strip():
                st.events.append(EvolutionEvent.from_dict(json.loads(line)))
        for line in mem.get("events/validation_reports.jsonl", b"").decode().splitlines():
            if line.strip():
                st.reports.append(ValidationReport.from_dict(json.loads(line)))
        for line in mem.get("memory/memory_graph.jsonl", b"").decode().splitlines():
            if line.strip():
                st.memory.events.append(json.loads(line))
        if verify:
            rep = st.audit()
            if not rep.ok:
                raise ValueError("archive audit failed: " + "; ".join(rep.problems[:5]))
        if st.root:
            st._write_genes()
            for c in st.capsules.values():
                st._append("capsules.jsonl", c.to_dict())
            for e in st.events:
                st._append("events.jsonl", e.to_dict())
        return st
