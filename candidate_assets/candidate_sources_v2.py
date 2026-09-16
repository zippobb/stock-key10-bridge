"""V2 upstream candidate recall adapter, never a final-score engine.

Input rows are the FULL eligible universe after existing V2 universe checks,
not a previously truncated 30+10 bundle. Bars use one explicitly declared
price/volume basis and exactly the benchmark trading calendar. Theme membership
must be dated, verified, and valid at the event date used for an early/follower
relationship. The production daily entry calls this module before its 30+10
output limits; standalone truncated bundles remain invalid inputs.
"""
from copy import deepcopy
from collections import defaultdict
from datetime import date, datetime
from math import isfinite
from statistics import mean

VERSION = "v2_candidate_sources_20260916"
VALID_ORIGINS = ("official_pool", "external_pool")


def _as_date(value: object) -> date:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    if len(text) >= 10:
        text = text[:10]
    return date.fromisoformat(text)


def _date_prefix(value: object) -> date | None:
    if value in (None, ""):
        return None
    try:
        return _as_date(value)
    except (TypeError, ValueError):
        return None


def _positive(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
        and value > 0
    )


def _normalise_memberships(row: dict, trade_day: date) -> list[dict]:
    """Normalize dated facts and same-day cold-start membership snapshots."""
    result = []
    for raw in row.get("themes", []):
        if not isinstance(raw, dict):
            continue
        if raw.get("verified") is not True or not raw.get("source_url"):
            continue
        theme_id = raw.get("theme_id")
        if not theme_id:
            continue

        observed_day = _date_prefix(raw.get("observed_at"))
        effective_from = _date_prefix(raw.get("effective_from"))
        published_value = raw.get("source_published_at")
        if published_value in (None, ""):
            published_value = raw.get("published_on")
        published_on = _date_prefix(published_value)
        effective_to = _date_prefix(raw.get("effective_to"))

        # Legacy dated facts remain valid. A snapshot without a known
        # historical effective date must carry a real observation timestamp.
        if observed_day is None and effective_from is not None:
            observed_day = effective_from
        snapshot_only = effective_from is None
        if snapshot_only and observed_day is None:
            continue
        if effective_to is not None and (
            effective_from is None or effective_to < effective_from
        ):
            continue

        known_dates = (
            observed_day,
            effective_from,
            published_on,
        )
        if any(item is not None and item > trade_day for item in known_dates):
            continue

        item = deepcopy(raw)
        item["theme_id"] = str(theme_id)
        item["_effective_from"] = effective_from
        item["_published_on"] = published_on
        item["_effective_to"] = effective_to
        item["_observed_day"] = observed_day
        item["_snapshot_only"] = snapshot_only
        result.append(item)
    return result


def _theme_ids(memberships: list[dict], as_of: date) -> set[str]:
    result = set()
    for item in memberships:
        if item.get("_snapshot_only"):
            # A cold-start snapshot proves membership only for the day it was
            # observed; it cannot create a historical follower relationship.
            if item.get("_observed_day") == as_of:
                result.add(str(item["theme_id"]))
            continue
        effective_from = item.get("_effective_from")
        published_on = item.get("_published_on")
        if (
            effective_from is not None
            and effective_from <= as_of
            and (published_on is None or published_on <= as_of)
            and (
                item.get("_effective_to") is None
                or as_of <= item["_effective_to"]
            )
        ):
            result.add(str(item["theme_id"]))
    return result

def _benchmark_basis(benchmark: list[dict]) -> str:
    values = {str(item.get("price_basis", "")) for item in benchmark}
    if len(values) != 1 or "" in values:
        raise ValueError("Benchmark price basis missing or inconsistent")
    return values.pop()


def build_candidates(
    rows,
    benchmark,
    trade_date,
    *,
    universe_complete=False,
):
    if universe_complete is not True:
        raise ValueError(
            "Full eligible universe required; truncated bundle is insufficient"
        )

    day = _as_date(trade_date)
    if not isinstance(benchmark, list) or not benchmark:
        raise ValueError("Benchmark history is required")

    dates = [str(item.get("date", "")) for item in benchmark]
    try:
        benchmark_days = [_as_date(item) for item in dates]
    except (TypeError, ValueError):
        raise ValueError("Invalid benchmark calendar") from None
    if (
        len(benchmark_days) < 26
        or benchmark_days != sorted(set(benchmark_days))
        or benchmark_days[-1] != day
    ):
        raise ValueError(
            "Need >=26 unique ordered benchmark sessions ending on trade_date"
        )
    if any(item > day for item in benchmark_days):
        raise ValueError("Future data")
    price_basis = _benchmark_basis(benchmark)
    if not all(_positive(item.get("close")) for item in benchmark):
        raise ValueError("Invalid benchmark prices")

    codes = [str(row.get("ts_code", "")) for row in rows]
    if len(codes) != len(set(codes)) or any(not code for code in codes):
        raise ValueError("Duplicate or missing universe codes")

    memberships_by_code = {}
    current_theme_members = defaultdict(set)
    for row, code in zip(rows, codes):
        if row.get("v2_universe_eligible") is not True:
            memberships_by_code[code] = []
            continue
        if row.get("origin") not in VALID_ORIGINS:
            raise ValueError("Pool membership must come from existing V2 source")
        memberships = _normalise_memberships(row, day)
        memberships_by_code[code] = memberships
        for theme in _theme_ids(memberships, day):
            current_theme_members[theme].add(code)

    features, rejected = {}, {}
    for row, code in zip(rows, codes):
        if row.get("v2_universe_eligible") is not True:
            rejected[code] = "existing_v2_universe_gate"
            continue
        bars = row.get("bars", [])
        bar_dates = [str(item.get("date", "")) for item in bars]
        try:
            bar_days = [_as_date(item) for item in bar_dates]
        except (TypeError, ValueError):
            rejected[code] = "history_incomplete_or_invalid"
            continue
        if any(item > day for item in bar_days):
            raise ValueError("Future stock data")
        if bar_dates != dates or bar_days != benchmark_days:
            rejected[code] = "history_incomplete_or_invalid"
            continue
        bar_bases = {str(item.get("price_basis", "")) for item in bars}
        if bar_bases != {price_basis}:
            rejected[code] = "price_basis_unverified_or_mismatch"
            continue
        if not all(
            _positive(item.get(key))
            for item in bars
            for key in ("close", "high", "volume")
        ):
            rejected[code] = "history_incomplete_or_invalid"
            continue
        if any(
            item["high"] < item["close"]
            or ("low" in item and item["low"] > item["close"])
            for item in bars
        ):
            rejected[code] = "history_incomplete_or_invalid"
            continue

        prices = [item["close"] for item in bars]
        ret3 = prices[-1] / prices[-4] - 1
        benchmark_ret3 = benchmark[-1]["close"] / benchmark[-4]["close"] - 1
        relative3 = ret3 - benchmark_ret3
        prior_volume = mean(item["volume"] for item in bars[-11:-1])
        if not _positive(prior_volume):
            rejected[code] = "volume_history_incomplete"
            continue
        volume = bars[-1]["volume"] / prior_volume

        down = [
            index
            for index in range(len(bars) - 5, len(bars))
            if benchmark[index]["close"] < benchmark[index - 1]["close"]
        ]
        resilience = (
            mean(
                (prices[index] / prices[index - 1] - 1)
                - (
                    benchmark[index]["close"]
                    / benchmark[index - 1]["close"]
                    - 1
                )
                for index in down
            )
            if down
            else None
        )

        # This is only the first event inside the explicit recent five-session
        # observation window, never a claim about the full market cycle start.
        events = [
            index
            for index in range(len(bars) - 5, len(bars))
            if prices[index] > max(item["high"] for item in bars[index - 20:index])
            and bars[index]["volume"]
            / mean(item["volume"] for item in bars[index - 10:index])
            >= 1.2
        ]
        event = events[0] if events else None
        memberships = memberships_by_code[code]
        features[code] = {
            "row": row,
            "relative3": relative3,
            "resilience": resilience,
            "volume": volume,
            "event": event,
            "themes": _theme_ids(memberships, day),
            "memberships": memberships,
            "confirmed": ret3 > 0 and relative3 > 0 and volume >= 1.2,
        }

    theme_stats = {}
    for theme in sorted(current_theme_members):
        members = sorted(current_theme_members[theme])
        active = [
            code
            for code in members
            if code in features and features[code]["confirmed"]
        ]
        invalid_members = [code for code in members if code not in features]
        breadth = len(active) / len(members) if members else 0.0
        theme_stats[theme] = {
            "members": members,
            "confirmed": active,
            "invalid_history_members": invalid_members,
            "breadth": breadth,
            "linked": (
                len(active) >= 3
                and breadth >= 0.5
                and not invalid_members
            ),
        }

    routes = {key: [] for key in (
        "early_leader",
        "theme_linkage",
        "confirmed_follower",
    )}
    reasons = {code: [] for code in features}
    for code, feature in features.items():
        # Standalone funds-driven leaders do not require a company-event narrative.
        if feature["relative3"] > 0 and (
            feature["event"] is not None
            or (
                feature["resilience"] is not None
                and feature["resilience"] > 0
                and feature["confirmed"]
            )
        ):
            routes["early_leader"].append(code)

        linked = [
            theme
            for theme in feature["themes"]
            if theme_stats[theme]["linked"]
        ]
        if linked and feature["confirmed"]:
            routes["theme_linkage"].append(code)

        # A follower must be a member at its own event date, and the earlier
        # member must also have been a member at that earlier event date.
        event = feature["event"]
        if (
            linked
            and event is not None
            and feature["row"].get("timing_guard_triggered") is False
        ):
            event_day = benchmark_days[event]
            linked_at_event = [
                theme
                for theme in linked
                if theme in _theme_ids(feature["memberships"], event_day)
            ]
            earlier = []
            for theme in linked_at_event:
                for other_code in theme_stats[theme]["members"]:
                    other = features.get(other_code)
                    if other is None or other["event"] is None:
                        continue
                    other_day = benchmark_days[other["event"]]
                    if (
                        other["event"] < event
                        and theme
                        in _theme_ids(other["memberships"], other_day)
                    ):
                        earlier.append(other["event"])
            if earlier:
                routes["confirmed_follower"].append(code)

    for route, route_codes in routes.items():
        for code in route_codes:
            reasons[code].append(route)
        if route == "early_leader":
            route_codes.sort(
                key=lambda code: (
                    features[code]["event"]
                    if features[code]["event"] is not None
                    else len(dates),
                    -(features[code]["resilience"] or 0),
                    code,
                )
            )
        elif route == "theme_linkage":
            route_codes.sort(
                key=lambda code: (
                    -max(
                        theme_stats[theme]["breadth"]
                        for theme in features[code]["themes"]
                        if theme_stats[theme]["linked"]
                    ),
                    -features[code]["relative3"],
                    code,
                )
            )
        else:
            route_codes.sort(
                key=lambda code: (
                    -features[code]["event"],
                    -features[code]["volume"],
                    code,
                )
            )

    output = {}
    for origin, limit in (("official_pool", 30), ("external_pool", 10)):
        queues = [
            [
                code
                for code in route_codes
                if features[code]["row"]["origin"] == origin
            ]
            for route_codes in routes.values()
        ]
        selected = []
        while any(queues) and len(selected) < limit:
            for queue in queues:
                while queue and queue[0] in selected:
                    queue.pop(0)
                if queue and len(selected) < limit:
                    selected.append(queue.pop(0))

        # Explicit coverage gap, no invented themes. Existing quantitative
        # ordering fills unused slots only; it must be supplied for every row.
        remaining = [
            code
            for code, feature in features.items()
            if feature["row"]["origin"] == origin and code not in selected
        ]
        if any(
            not _positive(features[code]["row"].get("quant_rank"))
            for code in remaining
        ):
            raise ValueError("Existing V2 quant_rank required for fallback")
        remaining.sort(
            key=lambda code: (
                features[code]["row"]["quant_rank"],
                code,
            )
        )
        selected.extend(remaining[: limit - len(selected)])

        result = []
        for code in selected:
            feature = features[code]
            item = deepcopy(feature["row"])
            item.pop("bars", None)
            event = feature["event"]
            event_themes = (
                sorted(
                    _theme_ids(
                        feature["memberships"],
                        benchmark_days[event],
                    )
                )
                if event is not None
                else []
            )
            item["candidate_source"] = {
                "version": VERSION,
                "as_of": trade_date,
                "routes": reasons[code] or ["legacy_quant_fallback"],
                "theme_ids": sorted(feature["themes"]),
                "event_theme_ids": event_themes,
                "breakout_date_5session_window": (
                    dates[event] if event is not None else None
                ),
                "breakout_window_scope": "recent_5_session_observation_only",
                "relative_return_3d": feature["relative3"],
                "downside_relative_5d": feature["resilience"],
                "volume_ratio": feature["volume"],
                "final_score_effect": 0,
                "entry_signal": False,
            }
            result.append(item)
        output[origin] = result

    return {
        "candidate_generation_version": VERSION,
        "pools": output,
        "theme_stats": theme_stats,
        "rejected": rejected,
        "route_counts": {
            route: len(route_codes)
            for route, route_codes in routes.items()
        },
        "coverage_gap": not any(
            stats["linked"] for stats in theme_stats.values()
        ),
    }

