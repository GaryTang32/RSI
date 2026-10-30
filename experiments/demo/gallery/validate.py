"""Validate chart specs: python3 validate.py specs/*.json

Sources are repo files (repo:...) or data-branch files restored at the same paths (see the top-level README).
A missing data-branch source is a note; a missing repo source fails."""
import json, math, re, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WARN = set()
METHODS = {"Core", "Live demo", "RRSI", "Meta-Harness", "SoL-Pi", "GEPA", "autoresearch", "Dream-RSI", "EvoMap", "Harness²", "Verification"}
TYPES = {"line", "bar", "interval", "stacked", "scatter"}
def num(v): return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
def check(c):
    e = []
    if not re.fullmatch(r"[a-z0-9-]+", str(c.get("id", ""))): e.append("id")
    if c.get("method") not in METHODS: e.append(f"method {c.get('method')!r}")
    if c.get("type") not in TYPES: e.append("type")
    for k in ("title", "takeaway", "source"):
        if not isinstance(c.get(k), str) or not c[k].strip(): e.append(k)
    if len(c.get("title", "")) > 70: e.append("title > 70 chars")
    src = c.get("source", "").split(" ")[0]
    if src and not (REPO / src.removeprefix("repo:")).exists():
        if src.startswith("repo:"): e.append(f"source missing: {src}")
        else: WARN.add(src)
    S = c.get("series") or []
    if not S: e.append("no series")
    cap = 3 if c.get("type") == "scatter" else 4
    if len(S) > cap: e.append(f"{len(S)} series > {cap}")
    xs = None
    for s in S:
        if not s.get("name"): e.append("series name")
        P = s.get("points") or []
        if not P: e.append(f"series {s.get('name')} empty")
        for p in P:
            if not num(p.get("y")): e.append(f"y not finite in {s.get('name')}")
            if c.get("x", {}).get("kind") == "linear" and not num(p.get("x")): e.append(f"x not numeric in {s.get('name')}")
            if ("lo" in p) != ("hi" in p): e.append("lo/hi pair")
            if "lo" in p and num(p.get("y")) and not (num(p["lo"]) and num(p["hi"]) and p["lo"] - 1e-9 <= p["y"] <= p["hi"] + 1e-9): e.append(f"lo<=y<=hi in {s.get('name')} at x={p.get('x')}")
            if c.get("type") == "stacked" and num(p.get("y")) and p["y"] < 0: e.append("negative stack")
        if c.get("type") in ("bar", "stacked"):
            k = [p.get("x") for p in P]
            if xs is None: xs = k
            elif k != xs: e.append("categories differ across series")
    if c.get("x", {}).get("log") and any(num(p.get("x")) and p["x"] <= 0 for s in S for p in s.get("points", [])): e.append("log x with x<=0")
    return e
bad = 0; ids = set()
for f in sys.argv[1:]:
    charts = json.loads(Path(f).read_text())["charts"]
    for c in charts:
        e = check(c)
        if c.get("id") in ids: e.append("duplicate id")
        ids.add(c.get("id"))
        if e: bad += 1; print(f"FAIL {f}:{c.get('id')}: {'; '.join(e)}")
    print(f"{f}: {len(charts)} charts")
if WARN: print(f"note: {len(WARN)} data-branch sources not restored here")
print("OK" if not bad else f"{bad} failing"); sys.exit(1 if bad else 0)
