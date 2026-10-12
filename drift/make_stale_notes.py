"""
drift/make_stale_notes.py — build study_materials/stale_notes from sample_notes.

    python drift/make_stale_notes.py

The "retrieval drift" scenario points NOTES_PATH at this copy. The files the
Python goals rely on are truncated to their headings or replaced with an
unrelated topic; the file *names* are unchanged, so list_study_files() and
search_notes() still look healthy to anyone watching only system signals.
The output directory is committed so the lab is reproducible without rerunning
this script.
"""

from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "study_materials" / "sample_notes"
DST = ROOT / "study_materials" / "stale_notes"

UNRELATED = """# Decorators

> This page was migrated from the old wiki. Content pending review.

## Holiday Decorations Checklist

- String lights: check every bulb before hanging; replace the fuse if a strand is dark.
- Wreaths: fresh fir holds its needles about three weeks indoors.
- Garland: measure the banister twice; most railings need 1.5x their length.
- Storage: wrap lights around cardboard to prevent tangles next season.

## Table Settings

Layer a charger, then the dinner plate, then the salad plate. Glasses go to
the upper right; forks on the left, knives and spoons on the right.
"""


def truncate_to_headings(text: str, keep_lines: int = 6) -> str:
    """Keep the first `keep_lines` lines and every heading; drop the body."""
    lines = text.splitlines()
    head = lines[:keep_lines]
    headings = [ln for ln in lines[keep_lines:] if ln.startswith("#")]
    return "\n".join(head + [""] + headings + ["", "_(section content archived)_", ""])


def build() -> Path:
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)
    for src in sorted(SRC.glob("*.md")):
        text = src.read_text(encoding="utf-8")
        if src.name == "decorators.md":
            out = UNRELATED                      # replaced by an unrelated topic
        elif src.name in ("closures.md", "python_basics.md"):
            out = truncate_to_headings(text)     # truncated: headings survive, bodies do not
        else:
            out = text                           # unrelated files untouched
        (DST / src.name).write_text(out, encoding="utf-8")
    return DST


if __name__ == "__main__":
    out = build()
    for f in sorted(out.glob("*.md")):
        print(f"{f.name:<28} {f.stat().st_size:>6} bytes")
    print(f"→ {out}")
