# V2.0 PNG CJK rendering assets

These assets are the deterministic fallback for the fixed
`v2.0-dashboard-20260904` renderer.  They are used only when a complete,
validated CJK font is unavailable.

The path data is derived from KanjiVG and is distributed under the KanjiVG
CC BY-SA 3.0 terms.  Build the atlas in this directory before rendering:

```bash
python3 build_kanjivg_atlas.py \
  --extra glyph_paths_extra.json \
  --extra glyph_paths_extra2.json \
  --extra glyph_paths_extra3.json
python3 render_v2_png_atlas.py \
  --input /path/to/key10_review_YYYYMMDD.json \
  --output /path/to/key10_review_YYYYMMDD.png \
  --next-trade-date YYYYMMDD
```

The renderer rejects missing/empty CJK glyphs, normalizes full-width
punctuation, and requires RGB 1024x2048 output.  ReportLab font fallback and
Latin-font substitution are not permitted.
