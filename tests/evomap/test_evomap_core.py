"""rsi.evomap core: content addressing, schemas, assets, LocalStore audit and archives."""
import json
import shutil
import subprocess

import pytest

from rsi.evomap import (Capsule, EvolutionEvent, Gene, JsonSchemaValidator, LocalStore, SCHEMA_VERSION, asset_id,
                        canonicalize, verify_asset_id)
from rsi.evomap.hashing import _js_number, hub_capsule_asset_id

# The reference canonicalize() from @evomap/gep-sdk src/contentHash.js (Apache-2.0), used to cross-check.
JS_REF = r"""
const { createHash } = require('node:crypto');
function canonicalize(obj) {
  if (obj === null || obj === undefined) return 'null';
  if (typeof obj === 'boolean') return obj ? 'true' : 'false';
  if (typeof obj === 'number') { if (!Number.isFinite(obj)) return 'null'; return String(obj); }
  if (typeof obj === 'string') return JSON.stringify(obj);
  if (Array.isArray(obj)) return '[' + obj.map(canonicalize).join(',') + ']';
  if (typeof obj === 'object') {
    const keys = Object.keys(obj).sort();
    return '{' + keys.map(k => JSON.stringify(k) + ':' + canonicalize(obj[k])).join(',') + '}';
  }
  return 'null';
}
function computeAssetId(obj) {
  const clean = {}; for (const k of Object.keys(obj)) { if (k !== 'asset_id') clean[k] = obj[k]; }
  return 'sha256:' + createHash('sha256').update(canonicalize(clean), 'utf8').digest('hex');
}
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(input.map(computeAssetId)));
"""

SAMPLES = [
    {"type": "Gene", "id": "g1", "n": 1.0, "f": 0.1, "big": 1e21, "small": 1e-7, "tiny": 0.000001, "neg": -2.5,
     "s": "café \"q\" \n\t\u0001", "l": [True, False, None, 3, [1, {"b": 2, "a": 1}]], "asset_id": "x"},
    {"z": 123456789012, "a": {"y": 1e16, "x": 12345.678}, "u": "üß中"},
    Gene(id="gene_x", signals_match=["a", "b"], strategy=["s1", "s2"], summary="sum", validation=["python t.py"]).to_dict(),
]


def test_js_number_formatting():
    assert _js_number(1.0) == "1" and _js_number(0.1) == "0.1" and _js_number(100.0) == "100"
    assert _js_number(1e21) == "1e+21" and _js_number(1e-7) == "1e-7" and _js_number(0.000001) == "0.000001"
    assert _js_number(1e16) == "10000000000000000" and _js_number(-2.5) == "-2.5" and _js_number(float("inf")) == "null"
    assert canonicalize({"b": 1, "a": [None, True]}) == '{"a":[null,true],"b":1}'


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_asset_id_matches_js_reference():
    out = subprocess.run(["node", "-e", JS_REF], input=json.dumps(SAMPLES), capture_output=True, text=True, timeout=30)
    ref = json.loads(out.stdout)
    assert ref == [asset_id(s) for s in SAMPLES]


def test_null_changes_hash_and_verify():
    a = {"type": "Gene", "id": "g"}
    b = {"type": "Gene", "id": "g", "scope": None}
    assert asset_id(a) != asset_id(b)
    a["asset_id"] = asset_id(a)
    assert verify_asset_id(a)
    a["id"] = "h"
    assert not verify_asset_id(a)
    c = {"type": "Capsule", "outcome": {"status": "success", "score": 0.9, "notes": "x"}}
    d = {"type": "Capsule", "outcome": {"status": "success", "score": 0.9}}
    assert hub_capsule_asset_id(c) == asset_id(d)


def test_schema_strict_vs_lenient():
    g = Gene(id="gene_ok", signals_match=["x"], strategy=["do"], validation=["python c.py"]).stamp()
    v_strict, v_len = JsonSchemaValidator(strict=True), JsonSchemaValidator(strict=False)
    assert v_strict.validate(g.to_gep()) == []
    g.avoid = ["bad thing"]
    g.epigenetic_marks = [{"context": "env", "boost": 0.1}]
    g.stamp()
    errs = v_strict.validate(g.to_dict())
    assert any("avoid" in e for e in errs) and any("epigenetic_marks" in e for e in errs)   # documented drift
    assert v_len.validate(g.to_dict()) == []
    bad = Gene(id="g", signals_match=[], strategy=[], validation=[]).stamp()
    assert v_len.validate(bad.to_dict())            # minItems violations
    ev = EvolutionEvent(id="evt_1", mutation_id="m", signals=["a"]).stamp()
    assert v_strict.validate(ev.to_dict()) == []
    cap = Capsule(id="c1", gene="g", trigger=["a"], summary="s", confidence=0.5, diff="diff text").stamp()
    assert v_len.validate(cap.to_dict()) == [] and v_strict.validate(cap.to_gep()) == []
    assert g.to_dict()["schema_version"] == SCHEMA_VERSION


def _filled_store(root=None) -> LocalStore:
    st = LocalStore(root, node_id="n1")
    st.upsert_gene(Gene(id="gene_a", signals_match=["x"], strategy=["s"], validation=["python c.py"]))
    for i in range(4):
        e = EvolutionEvent(id=st.new_id("evt"), signals=["x"], genes_used=["gene_a"], mutation_id=f"m{i}",
                           outcome={"status": "success", "score": 0.8}, capsule_id="cap1" if i % 2 else None)
        st.append_event(e)
        if i % 2:
            st.upsert_capsule(Capsule(id="cap1", gene="gene_a", trigger=["x"], summary="ok", confidence=0.8))
    st.memory.record_outcome(signals=["x"], gene_id="gene_a", status="success", score=0.8)
    return st


def test_store_chain_audit_and_tamper(tmp_path):
    st = _filled_store(tmp_path / "s")
    assert st.audit().ok
    assert [e.id for e in st.lineage(st.events[-1].id)] == [e.id for e in st.events]
    assert st.success_streak("cap1") == 2
    # content tamper -> asset_id mismatch
    st.events[1].signals.append("tampered")
    rep = st.audit()
    assert not rep.ok and any("asset_id mismatch" in p for p in rep.problems)
    # deletion -> broken parent chain
    st2 = _filled_store()
    del st2.events[1]
    assert any("parent" in p for p in st2.audit().problems)
    # reload from disk reproduces the same ids
    st3 = LocalStore(tmp_path / "s", node_id="n1")
    assert [e.asset_id for e in st3.events] == [e.asset_id for e in _filled_store().events]


def test_archive_roundtrip_and_checksum(tmp_path):
    st = _filled_store()
    p = st.export_archive(tmp_path / "a.gepx.tgz")
    st2 = LocalStore.import_archive(p)
    assert st2.genes_json() == st.genes_json()
    assert [e.asset_id for e in st2.events] == [e.asset_id for e in st.events]
    assert {c.asset_id for c in st2.capsules.values()} == {c.asset_id for c in st.capsules.values()}
    # byte-level tamper inside the archive is detected
    import io
    import tarfile
    with tarfile.open(p) as tar:
        mem = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    mem["genes/genes.json"] = mem["genes/genes.json"].replace(b'"s"', b'"S"')
    bad = tmp_path / "bad.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        for n, d in mem.items():
            info = tarfile.TarInfo(n)
            info.size = len(d)
            tar.addfile(info, io.BytesIO(d))
    with pytest.raises(ValueError):
        LocalStore.import_archive(bad)


def test_quarantine_zone_staging_and_promotion():
    st = LocalStore(node_id="n2")
    g = Gene(id="gene_ext", signals_match=["x"], strategy=["s"], validation=["python c.py"]).stamp()
    cap = Capsule(id="c", gene="gene_ext", trigger=["x"], summary="s", confidence=0.9).stamp()
    st.stage_external(cap.to_dict(), "hub")
    assert st.external[-1]["asset"]["confidence"] == pytest.approx(0.54)
    rec = st.stage_external(g.to_dict(), "hub")
    assert rec["a2a"]["status"] == "external_candidate" and "gene_ext" not in st.genes
    with pytest.raises(PermissionError):
        st.promote_external(g.asset_id, validated=False)
    got = st.promote_external(g.asset_id, validated=True)
    assert got is not None and got.parent == g.asset_id and got.provenance["kind"] == "external"
    tampered = g.to_dict()
    tampered["strategy"] = ["evil"]
    with pytest.raises(ValueError):
        st.stage_external(tampered, "hub")
    assert any(m["kind"] == "external_candidate" for m in st.memory.events)
