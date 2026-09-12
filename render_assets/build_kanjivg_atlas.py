#!/usr/bin/env python3
"""Build a deterministic CJK glyph atlas from the checked-in KanjiVG paths.

The production PNG renderer uses this atlas only when a validated CJK font is
not available.  It is intentionally text-data based, so it cannot silently
fall back to a Latin font or a PDF font with missing Chinese glyphs.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import subprocess
from pathlib import Path


CELL = 128
VIEW = 109
COLS = 16


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="glyph_paths.json")
    parser.add_argument("--extra", action="append", default=[])
    parser.add_argument("--svg", default="v2_cjk_atlas.svg")
    parser.add_argument("--png", default="v2_cjk_atlas.png")
    parser.add_argument("--index", default="v2_cjk_atlas_index.json")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    glyphs = dict(payload.get("chars", {}))
    for extra_path in args.extra:
        extra = json.loads(Path(extra_path).read_text(encoding="utf-8"))
        glyphs.update(extra.get("chars", {}))
    chars = list(glyphs)
    if not chars:
        raise SystemExit("glyph_paths.json 没有字形")
    rows = math.ceil(len(chars) / COLS)
    width = COLS * CELL
    height = rows * CELL
    chunks = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<g fill="none" stroke="#000" stroke-width="3.6" stroke-linecap="round" stroke-linejoin="round">',
    ]
    positions = {}
    for index, ch in enumerate(chars):
        col, row = index % COLS, index // COLS
        x, y = col * CELL, row * CELL
        positions[ch] = {"index": index, "x": x, "y": y, "width": CELL, "height": CELL}
        chunks.append(f'<g transform="translate({x},{y}) scale({CELL / VIEW:.9f})">')
        for item in glyphs[ch]:
            d = html.escape(str(item["d"]), quote=True)
            transform = item.get("transform")
            if transform:
                chunks.append(f'<g transform="{html.escape(transform, quote=True)}"><path d="{d}"/></g>')
            else:
                chunks.append(f'<path d="{d}"/>')
        chunks.append("</g>")
    chunks.extend(["</g>", "</svg>"])
    Path(args.svg).write_text("\n".join(chunks), encoding="utf-8")
    subprocess.run(
        [
            "inkscape",
            str(args.svg),
            "--export-filename",
            str(args.png),
            "--export-width",
            str(width),
            "--export-height",
            str(height),
            "--export-background-opacity",
            "0",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    Path(args.index).write_text(
        json.dumps(
            {
                "schema": "v2-cjk-atlas-v1",
                "source_schema": payload.get("schema"),
                "license": payload.get("license"),
                "cell": CELL,
                "cols": COLS,
                "rows": rows,
                "chars": chars,
                "positions": positions,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"chars": len(chars), "size": [width, height], "png": str(args.png)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
