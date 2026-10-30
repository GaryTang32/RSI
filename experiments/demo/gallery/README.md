# Chart gallery

`demo/gallery.html` draws every spec in `specs/*.json` as an interactive chart: hover values, a data table per chart,
light and dark themes. Each number is copied from the source file the spec names.

```
python experiments/demo/gallery/make_core_specs.py   # core, live-demo and verification specs (needs the data branch restored)
python experiments/demo/gallery/make_harness2_specs.py   # Harness² specs (same)
python experiments/demo/gallery/make_bbeh_specs.py       # BBH/BBEH specs (same)
python experiments/demo/gallery/validate.py experiments/demo/gallery/specs/*.json
python experiments/demo/gallery/build_gallery.py     # writes demo/gallery.html
```

## Spec format (specs/<group>.json = {"charts": [spec, ...]})

spec = {
  "id": "rrsi-overfit",                 # unique, lowercase, [a-z0-9-]
  "method": "RRSI",                     # one of: Core, Live demo, RRSI, Meta-Harness, SoL-Pi, GEPA, autoresearch, Dream-RSI, EvoMap, Harness², BBEH, Verification
  "title": "Short chart title (<= 70 chars)",
  "takeaway": "One or two plain sentences: what the chart shows, with the key numbers. Must be true of the data in this spec.",
  "source": "results/rrsi/e0_overfitting_trap.json",   # a data-branch path, or "repo:<path>" for a repo file
  "type": "line" | "bar" | "interval" | "stacked" | "scatter",
  "x": {"label": "Iteration", "kind": "linear" | "category", "log": false},
  "y": {"label": "Held-out accuracy", "min": 0, "max": 1, "lowerIsBetter": false, "format": ".2f" | ".0%" | ".0f" | ".3f"},
  "series": [ {"name": "Strict keep rule", "points": [ {"x": 0, "y": 0.5, "lo": 0.4, "hi": 0.6, "label": "optional point note"} ]} ],
  "refLines": [ {"axis": "y" | "x", "value": 0.975, "label": "paper's claim"} ],   # optional
  "note": "optional caveat, e.g. 'simulated domain: shows the mechanism, not a benchmark'"
}

Types:
- line: x numeric (kind linear) or category; one line per series; lo/hi draw a band (e.g. 95% CI).
- bar: x categories; one bar per series inside each category (grouped); lo/hi draw an error bar.
- interval: a forest plot; x = category label (shown on the vertical axis), y = estimate with lo/hi; usually one series.
  refLines on axis "y" mark e.g. zero effect or the paper's number.
- stacked: x categories; series stack per category (e.g. verdict counts); y values must be >= 0.
- scatter: x and y numeric; <= 3 series.

Rules: <= 4 series per chart (<= 3 for scatter); every number finite; same x categories across series of a bar/stacked chart;
lo <= y <= hi when given; use real values from the source file (round sensibly), never invented ones.
