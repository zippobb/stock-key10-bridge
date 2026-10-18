# Daily A-share Picks

> An automated pipeline that runs after every trading day's close: it scores a fixed watchlist,
> selects that day's 5 candidates, and publishes them as a public dashboard.
>
> **This is a record-and-verification project. Not investment advice.**

![Dashboard](https://img.shields.io/badge/dashboard-live-brightgreen)
![Schedule](https://img.shields.io/badge/updated-every%20trading%20day%2018%3A40%20CST-blue)
![Data](https://img.shields.io/badge/data-daily%20JSON%20snapshots-orange)
![License](https://img.shields.io/badge/license-MIT-green)

🔗 **Live dashboard**: https://zippobb.github.io/seastrom-stock/
📅 **Schedule**: 18:40 (UTC+8) on every trading day; non-trading days are skipped entirely
📦 **Data**: every run is archived as a JSON snapshot, fully back-testable and verifiable

---

## What it does

```
   ┌──────────────────────────────────────┐
   │  After close: quotes / themes /       │
   │  fundamentals                         │
   └──────────────────┬───────────────────┘
                      ▼
   ┌──────────────────────────────────────┐
   │  Multi-factor scoring over a fixed    │   ← screening engine,
   │  watchlist (200 names)                │     runs on a private server
   └──────────────────┬───────────────────┘
                      ▼
   ┌──────────────────────────────────────┐
   │  Rank → Top 5 + rejected list +       │
   │  reasons                              │
   └──────────────────┬───────────────────┘
                      ▼
   ┌──────────────────────────────────────┐
   │  Push daily/<date>/ to this repo      │
   │  render_dashboard.py → index.html     │   ← this repo (open source)
   │  Publish via GitHub Pages             │
   └──────────────────────────────────────┘
```

**Scope**: this repository is the **publishing side** — daily snapshots, the dashboard renderer,
and the release scripts. The screening engine runs on a private ECS instance and is not open sourced.

---

## Getting the data

One directory per trading day: `daily/<YYYYMMDD>/`

| File | Contents |
| --- | --- |
| `picks.json` | The day's 5 candidates with selection rationale |
| `scored.json` | Full scoring breakdown for every watchlist name |
| `market.json` | Market regime / timing state for the day |
| `narrative.json` | Dashboard copy |
| `dropped.json` | Rejected names and rejection reasons |
| `verify.json` | Pre-publish verification record |
| `comment.json` | Commentary |
| `_meta.json` | Run metadata |

`latest.json` always points at the most recent run:

```json
{ "trade_date": "20261016", "generated_at": "2026-10-17T19:21:33+08:00", "path": "daily/20261016" }
```

```bash
# What did it pick today?
curl -s https://raw.githubusercontent.com/zippobb/seastrom-stock/main/latest.json

# Full result for one session
curl -s https://raw.githubusercontent.com/zippobb/seastrom-stock/main/daily/20261016/picks.json
```

---

## Repository layout

```
.
├── daily/                    # Daily result snapshots (one dir per session)
│   └── latest.json           # → most recent run
├── render/
│   ├── render_dashboard.py   # Dashboard renderer
│   └── template.css
├── theme/                    # Theme / concept data
│   ├── theme_market_full.csv
│   ├── theme_override.json   # Manual theme override (outranks the algorithm)
│   └── biz_draft.json
├── pipeline.py               # Pipeline orchestration
├── publish_pages.py          # GitHub Pages publishing
├── aliyun_api.py             # Cloud provider API wrapper
├── market_calendar.json      # Trading calendar (non-trading days are skipped)
├── config.example.json       # Config template
├── run_daily.sh              # Daily entry point
└── index.html                # Dashboard (GitHub Pages entry)
```

---

## Run it yourself

```bash
git clone https://github.com/zippobb/seastrom-stock.git
cd seastrom-stock
cp config.example.json config.json   # fill in your own data sources and push targets
./run_daily.sh
```

---

## Design rules

- **Verify before publish.** Results go through an independent verification pass. If `trade_date`
  does not match the latest trading day, publishing aborts — stale data is never presented as today's.
- **Hard exclusion rules.** ST / \*ST names and newly listed stocks are removed before ranking.
- **Manual override wins.** `theme/theme_override.json` outranks algorithmic output.
- **No empty runs.** Non-trading days are skipped entirely.

---

## Disclaimer

This repository is a personal automation project published for engineering and data-verification
purposes. Nothing here constitutes investment advice, an offer, or a recommendation.
Markets carry risk. Make your own decisions.

---

## License

MIT
