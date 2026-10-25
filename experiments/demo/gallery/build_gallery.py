"""Merge specs/*.json into the gallery page: python build_gallery.py [out.html] [--fragment]

--fragment leaves out the <!doctype>/<head> wrapper (for hosts that add their own)."""
import json, sys
from pathlib import Path
H = Path(__file__).resolve().parent
REPO = H.parents[2]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
charts = [c for f in sorted((H / "specs").glob("*.json")) for c in json.loads(f.read_text())["charts"]]
js = json.dumps({"charts": charts}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
page = (H / "template.html").read_text().replace("__SPECS__", js)
if "--fragment" not in sys.argv:
    page = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n</head>\n<body>\n' + page + "\n</body>\n</html>\n")
out = Path(args[0]) if args else REPO / "demo" / "gallery.html"
out.write_text(page)
print(len(charts), "charts ->", out)
