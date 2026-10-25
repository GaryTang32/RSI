"""Merge specs/*.json into the gallery page: python3 build_gallery.py [out.html]"""
import json, sys
from pathlib import Path
H = Path(__file__).resolve().parent
REPO = H.parents[2]
charts = [c for f in sorted((H / "specs").glob("*.json")) for c in json.loads(f.read_text())["charts"]]
js = json.dumps({"charts": charts}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "demo" / "gallery.html"
out.write_text((H / "template.html").read_text().replace("__SPECS__", js))
print(len(charts), "charts ->", out)
