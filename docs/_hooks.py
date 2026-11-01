"""MkDocs hook: publish the self-contained chart gallery next to the site (it is a single HTML file kept in demo/)."""

import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def on_post_build(config, **kwargs):
    gallery = REPO / "demo" / "gallery.html"
    if gallery.is_file():
        shutil.copy(gallery, Path(config["site_dir"]) / "gallery.html")
