"""V2 upstream candidate recall; integration pending, never a final-score engine.

Input rows are the FULL eligible universe after existing V2 universe checks,
not a previously truncated 30+10 bundle. Bars use a single adjusted-price basis
and exactly the benchmark trading calendar. Theme membership must be dated.
"""
from copy import deepcopy
from datetime import date
from math import isfinite
from statistics import mean

VERSION = "v2_candidate_sources_20260915"


def build_candidates(rows, benchmark, trade_date, *, universe_complete=False):
    if universe_complete is not True:
        raise ValueError("Full eligible universe required; truncated bundle is insufficient")
    day = date.fromisoformat(trade_date)
    dates = [b["date"] for b in benchmark]
    if len(dates) < 26 or dates != sorted(set(dates)) or dates[-1] != trade_date:
        raise ValueError("Need >=26 unique ordered benchmark sessions ending on trade_date")
    if any(date.fromisoformat(d) > day for d in dates):
        raise ValueError("Future data")
    def positive(x):
        return isinstance(x, (int, float)) and not isinstance(x, bool) and isfinite(x) and x > 0
    if not all(positive(b["close"]) for b in benchmark):
        raise ValueError("Invalid benchmark prices")
    codes = [r["ts_code"] for r in rows]
    if len(codes) != len(set(codes)):
        raise ValueError("Duplicate universe codes")
    features, rejected = {}, {}
    for row in rows:
        code = row["ts_code"]
        if row.get("v2_universe_eligible") is not True:
            rejected[code] = "existing_v2_universe_gate"
            continue
        if row.get("origin") not in ("official_pool", "external_pool"):
            raise ValueError("Pool membership must come from existing V2 source")
        bars = row.get("bars", [])
        if any(date.fromisoformat(b["date"]) > day for b in bars):
            raise ValueError("Future stock data")
        if [b["date"] for b in bars] != dates or not all(
            positive(b.get(k)) for b in bars for k in ("close", "high", "volume")
        ) or any(b["high"] < b["close"] for b in bars):
            rejected[code] = "history_incomplete_or_invalid"
            continue
        p = [b["close"] for b in bars]
        ret3 = p[-1] / p[-4] - 1
        relative3 = ret3 - (benchmark[-1]["close"] / benchmark[-4]["close"] - 1)
        volume = bars[-1]["volume"] / mean(b["volume"] for b in bars[-11:-1])
        down = [i for i in range(len(bars)-5, len(bars))
                if benchmark[i]["close"] < benchmark[i-1]["close"]]
        resilience = mean((p[i]/p[i-1]-1) -
                          (benchmark[i]["close"]/benchmark[i-1]["close"]-1)
                          for i in down) if down else None
        # The first breakout within this explicit five-session observation window.
        # This is not a claim about the beginning of an entire market cycle.
        events = [i for i in range(len(bars)-5, len(bars))
                  if p[i] > max(b["high"] for b in bars[i-20:i])
                  and bars[i]["volume"] / mean(b["volume"] for b in bars[i-10:i]) >= 1.2]
        event = events[0] if events else None
        themes = set()
        for membership in row.get("themes", []):
            if membership.get("verified") is not True or not membership.get("source_url"):
                continue
            start = date.fromisoformat(membership["effective_from"])
            published = date.fromisoformat(membership["published_on"])
            end = date.fromisoformat(membership["effective_to"]) if membership.get("effective_to") else None
            if start <= day and published <= day and (end is None or day <= end):
                themes.add(membership["theme_id"])
        features[code] = dict(row=row, relative3=relative3, resilience=resilience,
                              volume=volume, event=event, themes=themes,
                              confirmed=ret3 > 0 and relative3 > 0 and volume >= 1.2)
    theme_stats = {}
    for theme in sorted({t for f in features.values() for t in f["themes"]}):
        members = [c for c, f in features.items() if theme in f["themes"]]
        active = [c for c in members if features[c]["confirmed"]]
        theme_stats[theme] = dict(members=members, confirmed=active,
                                 breadth=len(active)/len(members),
                                 linked=len(active) >= 3 and len(active)/len(members) >= .5
                                 and "history_incomplete_or_invalid" not in rejected.values())
    routes = {k: [] for k in ("early_leader", "theme_linkage", "confirmed_follower")}
    reasons = {c: [] for c in features}
    for code, f in features.items():
        # Standalone funds-driven leaders do not require a company-event narrative.
        if f["relative3"] > 0 and (f["event"] is not None or
                                  (f["resilience"] is not None and f["resilience"] > 0 and f["confirmed"])):
            routes["early_leader"].append(code)
        linked = [t for t in f["themes"] if theme_stats[t]["linked"]]
        if linked and f["confirmed"]:
            routes["theme_linkage"].append(code)
        # Preserve the existing timing guard. Unknown guard never means safe entry.
        if linked and f["confirmed"] and f["event"] is not None and f["row"].get("timing_guard_triggered") is False:
            earlier = [features[c]["event"] for t in linked for c in theme_stats[t]["members"]
                       if features[c]["event"] is not None and features[c]["event"] < f["event"]]
            if earlier:
                routes["confirmed_follower"].append(code)
    for route, codes in routes.items():
        for code in codes:
            reasons[code].append(route)
        # Different route keys avoid counting one momentum number three times.
        if route == "early_leader":
            codes.sort(key=lambda c: (features[c]["event"] if features[c]["event"] is not None else len(dates),
                                      -(features[c]["resilience"] or 0), c))
        elif route == "theme_linkage":
            codes.sort(key=lambda c: (-max(theme_stats[t]["breadth"] for t in features[c]["themes"]
                                          if theme_stats[t]["linked"]), -features[c]["relative3"], c))
        else:
            codes.sort(key=lambda c: (-features[c]["event"], -features[c]["volume"], c))
    output = {}
    for origin, limit in (("official_pool", 30), ("external_pool", 10)):
        queues = [[c for c in codes if features[c]["row"]["origin"] == origin] for codes in routes.values()]
        selected = []
        while any(queues) and len(selected) < limit:
            for queue in queues:
                while queue and queue[0] in selected:
                    queue.pop(0)
                if queue and len(selected) < limit:
                    selected.append(queue.pop(0))
        # Explicit coverage gap, no invented themes. Existing quantitative ordering
        # fills unused slots only; it must be supplied for every eligible row.
        remaining = [c for c, f in features.items() if f["row"]["origin"] == origin and c not in selected]
        if any(not positive(features[c]["row"].get("quant_rank")) for c in remaining):
            raise ValueError("Existing V2 quant_rank required for fallback")
        remaining.sort(key=lambda c: (features[c]["row"]["quant_rank"], c))
        selected.extend(remaining[:limit-len(selected)])
        result = []
        for c in selected:
            f = features[c]
            item = deepcopy(f["row"])
            item.pop("bars", None)
            item["candidate_source"] = dict(version=VERSION, as_of=trade_date,
                routes=reasons[c] or ["legacy_quant_fallback"], theme_ids=sorted(f["themes"]),
                breakout_date_5session_window=dates[f["event"]] if f["event"] is not None else None,
                relative_return_3d=f["relative3"], downside_relative_5d=f["resilience"],
                volume_ratio=f["volume"], final_score_effect=0,
                entry_signal=False)
            result.append(item)
        output[origin] = result
    return dict(candidate_generation_version=VERSION, pools=output, theme_stats=theme_stats,
                rejected=rejected, route_counts={k: len(v) for k, v in routes.items()},
                coverage_gap=not any(t["linked"] for t in theme_stats.values()))
