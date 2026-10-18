# Stock Key10

> A daily A-share screening pipeline: every trading day it produces a candidate bundle from a fixed
> watchlist, runs a final review pass over it, and ships the result as a signed, archived artifact.
>
> **Research and engineering project. No backtesting, no live trading, no investment advice.**

![Schedule](https://img.shields.io/badge/runs-every%20trading%20day%20(CST)-blue)
![Schema](https://img.shields.io/badge/schema-v2.0.0-orange)
![Data](https://img.shields.io/badge/archive-daily%20JSON%20bundles-green)
![License](https://img.shields.io/badge/license-MIT-green)

SeaStorm · CoastWind · Jay Siu

---

## What it is

Stock Key10 automates one job: turn the day's market data into a small, reviewable shortlist.

The screen is **quantitative plus theme-based**. A fixed watchlist is scored on the quantitative side,
then combined with sector/theme context before the final review pass. Output is capped at a fixed
shortlist size — it is never padded. If the market does not support a selection, the run returns fewer
names, or defers, and says why.

Scope: **SSE main board, SZSE main board, ChiNext, and STAR Market. Beijing Stock Exchange is excluded.**

---

## How a trading day runs

```
  18:20 CST   ECS timer → stock_key10_v2_daily.py
                 │  quantitative screen + theme context
                 ▼
              archive/YYYY/MM/key10_bundle_<trade_date>.json   (~520 KB)
                 │  SHA-256 recorded in latest/manifest.json
                 ▼
  18:35 CST   Final review pass (V2.0 contract)
                 │  validated against contracts/key10_review_quant_theme.schema.json
                 ▼
              review/YYYY/MM/key10_review_<trade_date>.json
                 ▼
  18:50 CST   Conditional retry — recoverable failures only
                 │  completed stages are never re-run
                 ▼
              delivery/YYYY/MM/key10_review_receipt_<date>.json
              latest/key10_review_receipt_latest.json
                 ▼
              render_assets/render_v2_verified.py → PNG delivery
```

Every stage writes a receipt. A missing, stale, or incomplete receipt counts as a delivery failure —
a run is never assumed successful just because it was scheduled or triggered.

---

## Repository layout

| Path | Contents |
| --- | --- |
| `archive/YYYY/MM/` | Daily `key10_bundle_<trade_date>.json`, immutable once written |
| `latest/` | `manifest.json` (pointer + SHA-256), `key10_bundle_latest.json`, `key10_review_receipt_latest.json` |
| `review/YYYY/MM/` | Final review results per trading day |
| `delivery/` | Run receipts, run state, checkpoints, evidence cache, tools |
| `contracts/` | `key10_review_quant_theme.schema.json` (v2.0.0) and automation contracts |
| `candidate_assets/` | Candidate sourcing and theme classification code, with tests |
| `pool_assets/` | Official watchlist and its change log |
| `theme_assets/` | Theme tagging rules and the tag table |
| `render_assets/` | Renderer, render contract, fonts |
| `maintenance/`, `backups/` | Operational history |

Grab the latest pointer:

```bash
curl -s https://raw.githubusercontent.com/zippobb/seastrom-stock/main/latest/manifest.json
```

```json
{
  "trade_date": "20261016",
  "archive_path": "archive/2026/10/key10_bundle_20261016.json",
  "bundle_sha256": "840ad54a61c91e1015cb02ac14a302dd0a9f13a93cd9ea48c3cd4326d7488645",
  "schema_version": "2.0.0",
  "status": "success",
  "published_at_utc": "2026-10-16T10:21:01Z"
}
```

---

## Data contract

Downstream results are validated against `contracts/key10_review_quant_theme.schema.json`
(schema/model version `2.0.0`). The active validator is
`delivery/tools/validate_quant_theme_review.py`.

Historical artifacts are never rewritten or backfilled. A new schema passing validation is not the
same thing as a natural daily run succeeding.

---

## Operating discipline

These rules are enforced by the pipeline, not by convention:

- **No stale trade dates.** A result whose `trade_date` is not the latest trading day is never
  presented as today's output.
- **No silent fallback** to an older bundle unless explicitly authorized.
- **No fabricated evidence.** Missing news or long-form company evidence does not block a delivery;
  it is recorded instead.
- **No backtesting, no live execution.** Thresholds and formulas are experimental parameters and do
  not represent any verified return.
- **Receipts are the source of truth.** If a receipt is missing, stale, incomplete, or inconsistent
  with `latest/`, the delivery is treated as failed.
- **Theme tags are not same-day co-movement evidence.**

---

## Disclaimer

This repository is a personal engineering project published for automation and data-verification
purposes. Nothing here is investment advice, an offer, or a recommendation. No strategy here has been
validated against historical returns. Markets carry risk. Make your own decisions.

---

## License

MIT
