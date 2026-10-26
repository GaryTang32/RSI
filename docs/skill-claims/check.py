"""Verify the skill claim ledgers: every quote must occur in its source (modulo whitespace and typography).

python docs/skill-claims/check.py [method ...] [--corpus DIR]

code / repo-doc / skill-ref rows are checked against this repo. paper rows need the source texts in
``DIR/<method>/`` (default ``docs/skill-claims/corpus``, not committed; see sources.json and README.md);
without them those rows are reported as skipped.
"""
import argparse, hashlib, json, re, sys, unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
METHODS = ["general", "rrsi", "metaharness", "solpi", "gepa", "autoresearch", "dream", "evomap", "harness2"]
KINDS = {"paper", "code", "repo-doc", "absent", "skill-ref"}
TR = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                    "−": "-", " ": " ", " ": " ", " ": " ", "­": "", "`": "'", "′": "'"})


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s).translate(TR)).strip().lower()


def found(quote, hay):
    pos = 0
    for piece in map(norm, re.split(r"\s*(?:\.\.\.|…|\[\.\.\.\])\s*", quote)):
        if piece:
            i = hay.find(piece, pos)
            if i < 0:
                return False
            pos = i + len(piece)
    return True


def check(m, corpus, cache):
    rows = [json.loads(x) for x in (HERE / "ledger" / f"{m}.jsonl").read_text().splitlines() if x.strip()]
    bad, skipped = [], 0
    for r in rows:
        if r.get("kind") not in KINDS or r.get("verdict") not in ("match", "fixed"):
            bad.append((r.get("id"), "kind/verdict"))
        elif r["kind"] == "absent":
            if not r.get("searched"):
                bad.append((r["id"], "absent without searched terms"))
        else:
            p = corpus / m / r["source"] if r["kind"] == "paper" else REPO / r["source"]
            if r["kind"] == "paper" and not p.is_file():
                skipped += 1
                continue
            if p not in cache:
                cache[p] = norm(p.read_text(errors="replace")) if p.is_file() else None
            if cache[p] is None:
                bad.append((r["id"], f"no source {r['source']}"))
            elif not found(r.get("quote", ""), cache[p]):
                bad.append((r["id"], f"quote not in {r['source']}: {r.get('quote', '')[:80]!r}"))
    return rows, bad, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("methods", nargs="*", default=METHODS)
    ap.add_argument("--corpus", type=Path, default=HERE / "corpus")
    a = ap.parse_args()
    for s in json.loads((HERE / "sources.json").read_text()):
        p = a.corpus / s["file"]
        if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest() != s["sha256"]:
            print(f"warning: {s['file']} differs from the text the ledger was built on")
    cache, total, failed, skipped = {}, 0, 0, 0
    for m in a.methods:
        rows, bad, sk = check(m, a.corpus, cache)
        fixed = sum(r["verdict"] == "fixed" for r in rows)
        total, failed, skipped = total + len(rows), failed + len(bad), skipped + sk
        print(f"{m:13s} rows={len(rows):4d} verified={len(rows) - len(bad) - sk:4d} skipped={sk:4d} corrected={fixed:3d}")
        for b in bad:
            print("   FAIL", *b)
    print(f"TOTAL rows={total} verified={total - failed - skipped} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
