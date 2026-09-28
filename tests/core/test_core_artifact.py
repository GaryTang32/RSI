"""rsi.core.artifact: identity, edits, diffs, file blocks, directory round trips."""
from __future__ import annotations

import copy
import pickle

import pytest

from rsi.core import Artifact, parse_file_blocks
from rsi.core.artifact import is_safe_relpath
from rsi.core.critic import added_lines


# ------------------------------------------------------------------- identity
def test_identity_is_content_addressed():
    a = Artifact({"b.py": "2", "a.py": "1"})
    b = Artifact({"a.py": "1", "b.py": "2"}, meta={"note": "different meta"})
    assert a == b and hash(a) == hash(b) and a.id == b.id
    assert len(a.id) == 64 and a.short_id == a.id[:10]
    assert list(a) == ["a.py", "b.py"]                # sorted
    assert a != Artifact({"a.py": "1", "b.py": "3"})
    assert a != {"a.py": "1", "b.py": "2"}              # only equal to artifacts
    assert {a: 1}[b] == 1
    assert "a.py" in a and a.get("zzz") is None and len(a) == 2


def test_values_and_keys_are_stringified_and_files_is_a_copy():
    a = Artifact({1: 2})
    assert a["1"] == "2"
    f = a.files
    f["new"] = "x"
    assert "new" not in a


def test_sizes():
    a = Artifact({"a": "abcd", "b": "abcde"})
    assert a.size_chars() == 9
    assert a.size_tokens() == 1 + 2


def test_with_files_updates_deletes_and_meta():
    a = Artifact({"a.py": "1", "b.py": "2"}, meta={"k": 1})
    b = a.with_files({"a.py": "10", "c.py": "3", "b.py": None, "missing.py": None}, origin="edit")
    assert dict(b) == {"a.py": "10", "c.py": "3"}
    assert b.meta == {"k": 1, "origin": "edit"} and a.meta == {"k": 1}
    assert dict(a) == {"a.py": "1", "b.py": "2"}          # immutable
    assert a.changed_files(b) == ["a.py", "b.py", "c.py"]
    assert a.changed_files(a) == []


def test_pickle_deepcopy_and_json_roundtrip():
    a = Artifact({"x/y.md": "hello\n"}, meta={"m": [1]})
    for b in (pickle.loads(pickle.dumps(a)), copy.deepcopy(a), Artifact.from_json(a.to_json())):
        assert b == a and b.id == a.id and b.meta == {"m": [1]}
    assert a.to_json()["id"] == a.id


# ------------------------------------------------------------------------ diffs
def test_diff_and_diff_size_basic():
    a = Artifact({"f.py": "a\nb\nc\n", "gone.py": "x\n"})
    b = Artifact({"f.py": "a\nB\nc\nd\n", "new.py": "n\n"})
    d = a.diff(b)
    assert "--- a/f.py\n+++ b/f.py\n" in d
    assert "-b\n+B\n" in d and "+d\n" in d
    assert "--- a/gone.py" in d and "-x\n" in d
    assert "+++ b/new.py" in d and "+n\n" in d
    # f.py: -b +B +d ; gone.py: -x ; new.py: +n
    assert a.diff_size(b) == 5
    assert a.diff(a) == "" and a.diff_size(a) == 0


def test_diff_without_trailing_newline_keeps_lines_apart():
    """Bug fix: '-old' and '+new' used to be glued into one line when a file had no
    trailing newline, hiding the added line from critics and miscounting diff_size."""
    a = Artifact({"f.py": "x = 1", "g.py": "p"})
    b = Artifact({"f.py": "x = 'SECRET'", "g.py": "q"})
    d = a.diff(b)
    assert all(line.startswith(("---", "+++", "@@", "-", "+", " ", "\\")) for line in d.splitlines())
    assert "\\ No newline at end of file" in d
    assert d.endswith("\n")
    assert added_lines(d).splitlines() == ["x = 'SECRET'", "q"]
    assert a.diff_size(b) == 4
    # only the trailing newline changes: one removed + one added line
    assert Artifact({"f": "z"}).diff_size(Artifact({"f": "z\n"})) == 2


def test_diff_size_counts_content_that_looks_like_headers():
    """Bug fix: removed/added lines whose text begins with '--' / '++' are content."""
    a = Artifact({"q.sql": "-- old comment\nSELECT 1;\n"})
    b = Artifact({"q.sql": "++counter\nSELECT 1;\n"})
    assert a.diff_size(b) == 2
    assert added_lines(a.diff(b)) == "++counter"


def test_diff_context_parameter():
    a = Artifact({"f": "".join(f"{i}\n" for i in range(20))})
    b = a.with_files({"f": a["f"].replace("10\n", "ten\n")})
    assert len(a.diff(b, context=0).splitlines()) < len(a.diff(b, context=3).splitlines())


# ------------------------------------------------------------------- file blocks
def test_render_and_parse_file_blocks_roundtrip():
    a = Artifact({"harness.py": "def solve():\n    return 1\n", "prompts/task.md": "Q: {question}\n"})
    parsed = parse_file_blocks(a.render())
    assert parsed == dict(a)
    assert Artifact(parsed) == a


def test_parse_file_blocks_fences_prose_and_crlf():
    text = ("Here is my edit.\n"
            "=== FILE: a.py ===\n```python\nprint(1)\n```\n\n"
            "=== FILE: notes/b.md ===   \nplain text\n  indented\n\n\n"
            "=== FILE: c.txt ===\r\nwindows\n")
    out = parse_file_blocks(text)
    assert out == {"a.py": "print(1)\n", "notes/b.md": "plain text\n  indented\n", "c.txt": "windows\n"}
    assert parse_file_blocks("") == {} and parse_file_blocks(None) == {}
    assert parse_file_blocks("no blocks at all") == {}
    assert parse_file_blocks("=== FILE: d.py ===\n<<DELETE>>\n") == {"d.py": "<<DELETE>>\n"}


def test_render_truncates_long_files():
    a = Artifact({"big": "x" * 100})
    r = a.render(max_chars_per_file=10)
    assert "=== FILE: big ===" in r and "...[truncated]" in r and "x" * 11 not in r


# ------------------------------------------------------------------ directories
def test_to_dir_from_dir_roundtrip(tmp_path):
    a = Artifact({"harness.py": "code\n", "prompts/sys.md": "sys\n", "deep/er/x.txt": "x"})
    root = a.to_dir(tmp_path / "art")
    assert (root / "deep/er/x.txt").read_text() == "x"
    b = Artifact.from_dir(root)
    assert b == a


def test_from_dir_skips_binary_caches_and_excludes(tmp_path):
    (tmp_path / "keep.py").write_text("k")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__/keep.cpython-311.pyc").write_bytes(b"\x00\xff")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("git")
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00\x81")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/run.log").write_text("log")
    (tmp_path / "data.csv").write_text("1,2")
    assert set(Artifact.from_dir(tmp_path)) == {"keep.py", "logs/run.log", "data.csv"}
    assert set(Artifact.from_dir(tmp_path, exclude=("logs",))) == {"keep.py", "data.csv"}
    assert set(Artifact.from_dir(tmp_path, exclude=("*.csv",))) == {"keep.py", "logs/run.log"}
    assert set(Artifact.from_dir(tmp_path, patterns=("*.py",))) == {"keep.py"}


def test_to_dir_clean(tmp_path):
    d = tmp_path / "out"
    Artifact({"old.txt": "o"}).to_dir(d)
    Artifact({"new.txt": "n"}).to_dir(d)
    assert (d / "old.txt").exists()
    Artifact({"new.txt": "n"}).to_dir(d, clean=True)
    assert not (d / "old.txt").exists() and (d / "new.txt").exists()


@pytest.mark.parametrize("name", ["../escape.txt", "/abs/path.txt", "a/../../b", "C:\\x.txt", "..\\win.txt", ""])
def test_to_dir_refuses_paths_outside_root(tmp_path, name):
    """Bug fix: a proposal naming '../x' or '/abs' used to be written outside the directory."""
    art = Artifact({name: "evil", "ok.txt": "fine"})
    with pytest.raises(ValueError):
        art.to_dir(tmp_path / "root" / "inner")
    assert not (tmp_path / "root" / "escape.txt").exists()
    assert not (tmp_path / "root" / "inner" / "ok.txt").exists()  # nothing written at all


def test_is_safe_relpath():
    for ok in ("a.py", "dir/sub/x.md", ".hidden", "a..b.txt", "notes:1.md"):
        assert is_safe_relpath(ok), ok
    for bad in ("", "/etc/passwd", "../x", "a/../../x", "\\\\server\\x", "C:/x", "a/\x00"):
        assert not is_safe_relpath(bad), bad
