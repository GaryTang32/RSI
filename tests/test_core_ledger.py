"""rsi.core.ledger: append-only JSONL tree, resume, update, lineage, exports,
content-addressed ArtifactStore."""
from __future__ import annotations

import csv
import json
import threading

import pytest

from rsi.core import Artifact, ArtifactStore, Ledger, Node, new_id


def _chain(led):
    led.add(Node("root", None, round=0, kind="baseline", status="keep", score=0.5, cost=100.0, change="baseline"))
    led.add(Node("c1", "root", round=1, status="discard", score=0.4, change="try A"))
    led.add(Node("c2", "root", round=1, status="keep", score=0.6, change="try B"))
    led.add(Node("c3", "c2", round=2, status="crash", score=None, change="try C"))
    return led


def test_add_order_seq_and_queries():
    led = _chain(Ledger())
    assert len(led) == 4 and [n.seq for n in led.nodes()] == [0, 1, 2, 3]
    assert [n.id for n in led.nodes(status="keep")] == ["root", "c2"]
    assert [n.id for n in led.nodes(kind="baseline")] == ["root"]
    assert [n.id for n in led.children("root")] == ["c1", "c2"]
    assert [n.id for n in led.children(None)] == ["root"]
    assert led["c2"].score == 0.6
    assert led.best().id == "c2" and led.best(lower_is_better=True).id == "c1"
    assert led.best(status="discard").id == "c1" and Ledger().best() is None


def test_lineage():
    led = _chain(Ledger())
    assert [n.id for n in led.lineage("c3")] == ["root", "c2", "c3"]
    assert [n.id for n in led.lineage("root")] == ["root"]
    with pytest.raises(KeyError):
        led.lineage("nope")


def test_lineage_tolerates_foreign_parents_and_cycles():
    """Bug fix: a parent id from another ledger raised KeyError; a cycle looped forever."""
    led = Ledger()
    led.add(Node("x", "parent-in-another-ledger"))
    led.add(Node("y", "x"))
    assert [n.id for n in led.lineage("y")] == ["x", "y"]
    led.add(Node("a", "b"))
    led.add(Node("b", "a"))
    assert {n.id for n in led.lineage("a")} == {"a", "b"}


def test_update_merges_dicts_and_persists(tmp_path):
    p = tmp_path / "ledger.jsonl"
    led = Ledger(p)
    led.add(Node("n1", None, metrics={"a": 1}, meta={"x": 1}))
    led.update("n1", status="keep", score=0.9, metrics={"b": 2}, meta={"y": 2})
    n = led["n1"]
    assert (n.status, n.score, n.metrics, n.meta) == ("keep", 0.9, {"a": 1, "b": 2}, {"x": 1, "y": 2})
    assert len(p.read_text().splitlines()) == 2               # append-only: a second record
    again = Ledger(p)
    assert again["n1"].metrics == {"a": 1, "b": 2} and again["n1"].status == "keep" and len(again) == 1
    with pytest.raises(KeyError):
        led.update("missing", status="x")


def test_update_unknown_fields_survive_resume(tmp_path):
    """Bug fix: unknown keyword fields were set as attributes and silently lost on resume."""
    p = tmp_path / "l.jsonl"
    led = Ledger(p)
    led.add(Node("n", None))
    led.update("n", verdict_reason="floor")
    assert led["n"].meta["verdict_reason"] == "floor"
    assert Ledger(p)["n"].meta["verdict_reason"] == "floor"


def test_resume_keeps_order_and_continues_seq(tmp_path):
    p = tmp_path / "l.jsonl"
    _chain(Ledger(p))
    led = Ledger(p)
    assert [n.id for n in led.nodes()] == ["root", "c1", "c2", "c3"]
    assert [n.seq for n in led.nodes()] == [0, 1, 2, 3]
    led.add(Node("c4", "c3"))
    assert led["c4"].seq == 4
    assert [n.id for n in Ledger(p).lineage("c4")] == ["root", "c2", "c3", "c4"]


def test_resume_after_torn_write(tmp_path):
    """Bug fix: a crash mid-write left a partial last line that made the ledger unreadable."""
    p = tmp_path / "l.jsonl"
    led = Ledger(p)
    led.add(Node("a", None, score=1.0))
    with p.open("a") as f:
        f.write('{"id": "b", "parent": "a", "sco')             # torn line, no newline
    led2 = Ledger(p)
    assert [n.id for n in led2.nodes()] == ["a"] and led2.n_corrupt_lines == 1
    led2.add(Node("c", "a"))                                  # new records start on a fresh line
    led3 = Ledger(p)
    assert [n.id for n in led3.nodes()] == ["a", "c"] and led3.n_corrupt_lines == 1


def test_readd_existing_id_keeps_creation_order(tmp_path):
    """Bug fix: re-adding a node reset its seq to the new object's default (0)."""
    p = tmp_path / "l.jsonl"
    led = Ledger(p)
    led.add(Node("a", None))
    led.add(Node("b", "a", status="pending"))
    led.add(Node("b", "a", status="keep", score=0.7))
    assert led["b"].seq == 1 and led["b"].status == "keep"
    assert [n.id for n in led.nodes()] == ["a", "b"] and len(led) == 2
    assert Ledger(p)["b"].seq == 1 and Ledger(p)["b"].score == 0.7


def test_node_json_roundtrip_ignores_unknown_keys():
    n = Node("x", "p", round=3, kind="merge", status="keep", score=0.1, cost=2.0, change="c", artifact_id="aid",
             diff="d", metrics={"m": 1}, meta={"k": "v"})
    d = n.to_json()
    assert Node.from_json(d) == n
    d["from_the_future"] = 1
    assert Node.from_json(d) == n
    assert new_id("gene").startswith("gene_") and new_id() != new_id()


def test_non_json_values_are_stringified(tmp_path):
    p = tmp_path / "l.jsonl"
    Ledger(p).add(Node("x", None, meta={"obj": object()}))
    assert "object object" in Ledger(p)["x"].meta["obj"]


def test_render_and_to_json():
    led = _chain(Ledger())
    r = led.render(last=2)
    lines = r.splitlines()
    assert lines[0] == "round | status | score | cost | change" and len(lines) == 3
    assert "0.6000" in lines[1] and "try C" in lines[2] and " - " in lines[2]
    assert [d["id"] for d in led.to_json()] == ["root", "c1", "c2", "c3"]


def test_results_tsv_autoresearch_format(tmp_path):
    led = _chain(Ledger())
    led["c1"].metrics["memory_gb"] = 44.04
    led.add(Node("c5", "c2", status="keep", score=0.7, change="multi\nline\tdescription", artifact_id="abcdef123456",
                 metrics={"memory_gb": None}))
    out = tmp_path / "results.tsv"
    led.to_results_tsv(out)
    lines = out.read_text().splitlines()
    assert lines[0].split("\t") == ["commit", "score", "memory_gb", "status", "description"]
    assert len(lines) == 6                                    # one line per attempt, even multi-line changes
    rows = list(csv.reader(out.open(), delimiter="\t"))
    assert rows[1] == ["root"[:7], "0.500000", "0.0", "keep", "baseline"]
    assert rows[2][2] == "44.0"
    assert rows[4][1] == "0.000000" and rows[4][3] == "crash"   # crashes log 0.000000
    assert rows[5][0] == "abcdef1" and rows[5][4] == "multi line description"


def test_concurrent_adds(tmp_path):
    p = tmp_path / "l.jsonl"
    led = Ledger(p)

    def work(j):
        for i in range(50):
            led.add(Node(f"n{j}_{i}", None, score=float(i)))

    ts = [threading.Thread(target=work, args=(j,)) for j in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(led) == 300 and sorted(n.seq for n in led.nodes()) == list(range(300))
    assert len(Ledger(p)) == 300
    for line in p.read_text().splitlines():
        json.loads(line)


def test_artifact_store(tmp_path):
    st = ArtifactStore(tmp_path / "store")
    a = Artifact({"x.py": "1\n"}, meta={"origin": "seed"})
    aid = st.put(a)
    assert aid == a.id and st.has(aid) and not st.has("0" * 64)
    b = st.get(aid)
    assert b == a and b.meta == {"origin": "seed"}
    assert st.put(a) == aid                                    # idempotent
    assert len(list((tmp_path / "store").rglob("*.json"))) == 1
    assert not list((tmp_path / "store").rglob("*.tmp"))
