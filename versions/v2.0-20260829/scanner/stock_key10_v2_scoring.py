#!/usr/bin/env python3
"""Pure V2.0 scoring functions.

This module contains the only stock/sector scoring logic used by V2.0.  It has
no veto, risk downgrade, focus-pool bonus, market hard gate, or V1.x fusion.
Risk and execution notes are deliberately outside every score function.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd


QUANT_COLUMNS = ["market_fit_score", "sector_score", "technical_score"]


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def percentile(series: pd.Series) -> pd.Series:
    values = numeric(series)
    result = pd.Series(np.nan, index=series.index, dtype="float64")
    valid = values.notna() & np.isfinite(values)
    if valid.any():
        result.loc[valid] = values.loc[valid].rank(pct=True, method="average") * 100.0
    return result


def grouped_percentile(frame: pd.DataFrame, column: str, group: str) -> pd.Series:
    return frame.groupby(group, dropna=False)[column].rank(pct=True, method="average") * 100.0


def clip01(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return min(1.0, max(0.0, number))


def available_weighted_score(
    values: Iterable[tuple[object, float]],
    maximum: float,
    minimum_completeness: float = 0.70,
) -> tuple[float | None, float]:
    """Return a rescaled score and the available weight fraction.

    Every input value is normalized to [0, 1]. Missing inputs are ignored only
    when at least ``minimum_completeness`` of the weight remains.
    """
    pairs = list(values)
    total_weight = sum(float(weight) for _, weight in pairs)
    available: list[tuple[float, float]] = []
    for value, weight in pairs:
        normalized = clip01(value)
        if normalized is not None:
            available.append((normalized, float(weight)))
    available_weight = sum(weight for _, weight in available)
    completeness = available_weight / total_weight if total_weight else 0.0
    if completeness + 1e-12 < minimum_completeness or available_weight <= 0:
        return None, completeness
    score = sum(value * weight for value, weight in available) / available_weight * float(maximum)
    return round(score, 6), completeness


def _sector_day(light: pd.DataFrame, base: pd.DataFrame, trade_date: str) -> pd.DataFrame:
    day = light.loc[light["trade_date"].astype(str).eq(str(trade_date))].merge(
        base[["ts_code", "industry"]], on="ts_code", how="left", validate="one_to_one"
    )
    day = day.loc[day["industry"].notna()].copy()
    day["close_above_ma20"] = numeric(day["close"]).ge(numeric(day["ma20"]))
    day["bullish_alignment"] = (
        numeric(day["ma5"]).ge(numeric(day["ma10"]))
        & numeric(day["ma10"]).ge(numeric(day["ma20"]))
    )
    day["amount_ratio_1d_20d"] = numeric(day["amount"]) / numeric(day["amount_ma20"]).replace(0, np.nan)
    day["volume_expansion"] = numeric(day["volume_ratio_1d_20d"]).ge(1.2)
    grouped = day.groupby("industry", sort=True)
    sector = grouped.agg(
        stock_count=("ts_code", "size"),
        return_1d_mean_pct=("pct_chg", "mean"),
        return_3d_mean_pct=("return_3d_pct", "mean"),
        return_5d_mean_pct=("return_5d_pct", "mean"),
        return_20d_mean_pct=("return_20d_pct", "mean"),
        up_1d_ratio_pct=("pct_chg", lambda x: float(numeric(x).gt(0).mean() * 100)),
        above_ma20_ratio_pct=("close_above_ma20", lambda x: float(x.mean() * 100)),
        bullish_alignment_ratio_pct=("bullish_alignment", lambda x: float(x.mean() * 100)),
        volume_ratio_median=("volume_ratio_1d_20d", "median"),
        amount_ratio_median=("amount_ratio_1d_20d", "median"),
        volume_expansion_ratio_pct=("volume_expansion", lambda x: float(x.mean() * 100)),
    ).reset_index()
    for days in (1, 3, 5, 20):
        sector[f"return_{days}d_percentile"] = percentile(sector[f"return_{days}d_mean_pct"])
    sector["volume_ratio_percentile"] = percentile(sector["volume_ratio_median"])
    sector["amount_ratio_percentile"] = percentile(sector["amount_ratio_median"])
    sector["price_strength_norm"] = (
        sector["return_1d_percentile"] * 0.20
        + sector["return_3d_percentile"] * 0.20
        + sector["return_5d_percentile"] * 0.25
        + sector["return_20d_percentile"] * 0.35
    ) / 100.0
    sector["breadth_norm"] = (
        sector["up_1d_ratio_pct"]
        + sector["above_ma20_ratio_pct"]
        + sector["bullish_alignment_ratio_pct"]
    ) / 300.0
    sector["capital_norm"] = (
        sector["volume_ratio_percentile"]
        + sector["amount_ratio_percentile"]
        + sector["volume_expansion_ratio_pct"]
    ) / 300.0
    sector["persistence_norm"] = (
        sector["return_3d_percentile"]
        + sector["return_5d_percentile"]
        + sector["return_20d_percentile"]
    ) / 300.0
    sector["trade_date"] = str(trade_date)
    return sector


def build_sector_scores(
    light: pd.DataFrame,
    base: pd.DataFrame,
    previous_date: str,
    target_date: str,
) -> pd.DataFrame:
    """Calculate the confirmed 15-point sector score with no sample gate."""
    previous = _sector_day(light, base, previous_date)
    current = _sector_day(light, base, target_date)
    previous = previous[["industry", "price_strength_norm"]].rename(
        columns={"price_strength_norm": "previous_price_strength_norm"}
    )
    sector = current.merge(previous, on="industry", how="left", validate="one_to_one")
    change = sector["price_strength_norm"] - sector["previous_price_strength_norm"]
    sector["acceleration_norm"] = (0.5 + change).clip(0.0, 1.0).fillna(0.5)
    sector["sector_price_score"] = sector["price_strength_norm"] * 4.0
    sector["sector_breadth_score"] = sector["breadth_norm"].clip(0, 1) * 3.0
    sector["sector_capital_score"] = sector["capital_norm"].clip(0, 1) * 3.0
    sector["sector_persistence_score"] = sector["persistence_norm"].clip(0, 1) * 3.0
    sector["sector_acceleration_score"] = sector["acceleration_norm"] * 2.0
    sector["sector_score"] = sector[
        [
            "sector_price_score",
            "sector_breadth_score",
            "sector_capital_score",
            "sector_persistence_score",
            "sector_acceleration_score",
        ]
    ].sum(axis=1).round(6)
    sector["sector_rank"] = sector["sector_score"].rank(ascending=False, method="min").astype("Int64")
    sector["sector_rank_percentile"] = percentile(sector["sector_score"])
    sector.sort_values(["sector_score", "industry"], ascending=[False, True], kind="stable", inplace=True)
    return sector.reset_index(drop=True)


def build_stock_features(history: pd.DataFrame, target_date: str) -> pd.DataFrame:
    """Build the objective V2 stock feature snapshot from T and earlier data."""
    frame = history.copy()
    required = ["ts_code", "trade_date", "open", "high", "low", "close", "pct_chg", "vol", "amount"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"股票历史缺少字段: {missing}")
    frame["trade_date"] = frame["trade_date"].astype(str).str.replace("-", "", regex=False)
    if frame["trade_date"].gt(str(target_date)).any():
        raise ValueError("股票历史包含目标日后的未来数据")
    for column in required[2:]:
        frame[column] = numeric(frame[column])
    frame.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    rows: list[dict[str, object]] = []
    for ts_code, group in frame.groupby("ts_code", sort=False):
        g = group.sort_values("trade_date", kind="stable").tail(300).copy()
        if g.empty or str(g["trade_date"].iloc[-1]) != str(target_date):
            continue
        close, high, low, volume, amount = g["close"], g["high"], g["low"], g["vol"], g["amount"]
        ma = {window: close.rolling(window, min_periods=window).mean() for window in (5, 10, 20, 60, 120, 250)}
        returns = {days: (close / close.shift(days) - 1.0) * 100.0 for days in (3, 5, 10, 20, 60)}
        prior_close = close.shift(1)
        true_range = pd.concat([(high-low).abs(), (high-prior_close).abs(), (low-prior_close).abs()], axis=1).max(axis=1)
        atr_pct = true_range.rolling(14, min_periods=14).mean() / close * 100.0
        high20, low20 = high.rolling(20, min_periods=20).max(), low.rolling(20, min_periods=20).min()
        high60, low60 = high.rolling(60, min_periods=60).max(), low.rolling(60, min_periods=60).min()
        high120 = high.rolling(120, min_periods=120).max()
        vol_ma5, vol_ma20 = volume.rolling(5, min_periods=5).mean(), volume.rolling(20, min_periods=20).mean()
        amount_ma5, amount_ma20 = amount.rolling(5, min_periods=5).mean(), amount.rolling(20, min_periods=20).mean()
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
        loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi14 = 100.0 - 100.0 / (1.0 + rs)
        rsi14 = rsi14.where(~(loss.eq(0) & gain.gt(0)), 100.0)
        rsi14 = rsi14.where(~(loss.eq(0) & gain.eq(0)), 50.0)
        ma5_slope = (ma[5] / ma[5].shift(3) - 1.0) * 100.0
        prior20low = low.shift(3).rolling(20, min_periods=20).min()
        last3low = low.rolling(3, min_periods=3).min()
        daily_return = numeric(g["pct_chg"])
        close_location = (close-low) / (high-low).replace(0, np.nan)
        upper_shadow_atr = (high-np.maximum(g["open"], close)) / (true_range.rolling(14, min_periods=14).mean()).replace(0, np.nan)
        i = -1
        span20, span60 = high20.iloc[i]-low20.iloc[i], high60.iloc[i]-low60.iloc[i]
        vwap = (
            amount.iloc[i] * 10.0 / volume.iloc[i]
            if pd.notna(volume.iloc[i]) and volume.iloc[i] > 0 and pd.notna(amount.iloc[i])
            else np.nan
        )
        positive_impact = daily_return.clip(lower=0).tail(20).sum()
        amount_base = (amount.tail(20) / amount_ma20.tail(20)).replace([np.inf, -np.inf], np.nan).sum()
        rows.append({
            "ts_code": str(ts_code), "trade_date": str(target_date), "history_days": int(len(g)),
            "open": g["open"].iloc[i], "high": high.iloc[i], "low": low.iloc[i], "close": close.iloc[i],
            "return_1d_pct": daily_return.iloc[i], "return_3d_pct": returns[3].iloc[i],
            "return_5d_pct": returns[5].iloc[i], "return_10d_pct": returns[10].iloc[i],
            "return_20d_pct": returns[20].iloc[i], "return_60d_pct": returns[60].iloc[i],
            "ma5": ma[5].iloc[i], "ma10": ma[10].iloc[i], "ma20": ma[20].iloc[i],
            "ma60": ma[60].iloc[i], "ma120": ma[120].iloc[i], "ma250": ma[250].iloc[i],
            "ma5_slope_3d_pct": ma5_slope.iloc[i],
            "ma5_slope_change_pct": ma5_slope.iloc[i] - ma5_slope.iloc[-2] if len(ma5_slope) >= 2 else np.nan,
            "ma20_slope_5d_pct": (ma[20].iloc[i]/ma[20].iloc[-6]-1)*100 if len(ma[20]) >= 6 and pd.notna(ma[20].iloc[-6]) else np.nan,
            "ma60_slope_20d_pct": (ma[60].iloc[i]/ma[60].iloc[-21]-1)*100 if len(ma[60]) >= 21 and pd.notna(ma[60].iloc[-21]) else np.nan,
            "atr14_pct": atr_pct.iloc[i], "rsi14": rsi14.iloc[i], "roc10": returns[10].iloc[i],
            "position_20d_pct": (close.iloc[i]-low20.iloc[i])/span20*100 if pd.notna(span20) and span20 > 0 else np.nan,
            "position_60d_pct": (close.iloc[i]-low60.iloc[i])/span60*100 if pd.notna(span60) and span60 > 0 else np.nan,
            "drawdown_from_20d_high_pct": (close.iloc[i]/high20.iloc[i]-1)*100 if pd.notna(high20.iloc[i]) else np.nan,
            "drawdown_from_120d_high_pct": (close.iloc[i]/high120.iloc[i]-1)*100 if pd.notna(high120.iloc[i]) else np.nan,
            "distance_ma20_pct": (close.iloc[i]/ma[20].iloc[i]-1)*100 if pd.notna(ma[20].iloc[i]) else np.nan,
            "distance_ma20_atr": (close.iloc[i]-ma[20].iloc[i])/(close.iloc[i]*atr_pct.iloc[i]/100) if pd.notna(ma[20].iloc[i]) and pd.notna(atr_pct.iloc[i]) and atr_pct.iloc[i] > 0 else np.nan,
            "volume_ratio_1d_prior_10d": volume.iloc[i]/volume.shift(1).rolling(10, min_periods=10).mean().iloc[i] if pd.notna(volume.shift(1).rolling(10, min_periods=10).mean().iloc[i]) else np.nan,
            "volume_ratio_5d_20d": vol_ma5.iloc[i]/vol_ma20.iloc[i] if pd.notna(vol_ma20.iloc[i]) and vol_ma20.iloc[i] > 0 else np.nan,
            "amount_ratio_1d_20d": amount.iloc[i]/amount_ma20.iloc[i] if pd.notna(amount_ma20.iloc[i]) and amount_ma20.iloc[i] > 0 else np.nan,
            "amount_ratio_5d_20d": amount_ma5.iloc[i]/amount_ma20.iloc[i] if pd.notna(amount_ma20.iloc[i]) and amount_ma20.iloc[i] > 0 else np.nan,
            "close_location": close_location.iloc[i], "vwap": vwap,
            "close_vs_vwap_pct": (close.iloc[i]/vwap-1)*100 if pd.notna(vwap) and vwap > 0 else np.nan,
            "three_days_no_new_low": float(last3low.iloc[i] >= prior20low.iloc[i]) if pd.notna(prior20low.iloc[i]) else np.nan,
            "breakout_3d": float(close.iloc[i] > high.shift(1).rolling(3, min_periods=3).max().iloc[i]) if pd.notna(high.shift(1).rolling(3, min_periods=3).max().iloc[i]) else np.nan,
            "upper_shadow_atr": upper_shadow_atr.iloc[i],
            "active_day_ratio_60d": float(daily_return.tail(60).abs().ge(3).mean()) if len(daily_return) >= 60 else np.nan,
            "upside_per_amount_20d": positive_impact/amount_base if amount_base > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def _healthy_band(value: object, low: float, high: float, outer_low: float, outer_high: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if low <= number <= high:
        return 1.0
    if outer_low <= number < low or high < number <= outer_high:
        return 0.5
    return 0.0


def score_stocks(
    features: pd.DataFrame,
    metadata: pd.DataFrame,
    sectors: pd.DataFrame,
    minimum_completeness: float = 0.70,
) -> pd.DataFrame:
    """Apply the confirmed 5+15+30 quantitative formula."""
    frame = metadata.merge(features, on="ts_code", how="inner", validate="one_to_one")
    frame = frame.merge(
        sectors[["industry", "sector_score", "sector_rank", "sector_rank_percentile"]],
        on="industry", how="left", validate="many_to_one",
    )
    frame.rename(columns={"industry": "sector"}, inplace=True)
    for days in (1, 5, 20):
        frame[f"segment_median_{days}d"] = frame.groupby("segment")[f"return_{days}d_pct"].transform("median")
        frame[f"relative_market_{days}d_pct"] = frame[f"return_{days}d_pct"] - frame[f"segment_median_{days}d"]
        frame[f"relative_market_{days}d_percentile"] = grouped_percentile(frame, f"relative_market_{days}d_pct", "segment")
    for days in (5, 20):
        frame[f"return_{days}d_percentile"] = grouped_percentile(frame, f"return_{days}d_pct", "segment")
        frame[f"relative_sector_{days}d_pct"] = frame[f"return_{days}d_pct"] - frame.groupby("sector")[f"return_{days}d_pct"].transform("median")
        frame[f"relative_sector_{days}d_percentile"] = grouped_percentile(frame, f"relative_sector_{days}d_pct", "sector")
    frame["volume_ratio_1d_percentile"] = grouped_percentile(frame, "volume_ratio_1d_prior_10d", "segment")
    frame["amount_ratio_1d_percentile"] = grouped_percentile(frame, "amount_ratio_1d_20d", "segment")
    frame["volume_ratio_5d_percentile"] = grouped_percentile(frame, "volume_ratio_5d_20d", "segment")
    frame["atr_percentile"] = grouped_percentile(frame, "atr14_pct", "segment")

    records: list[dict[str, object]] = []
    for _, row in frame.iterrows():
        market, market_c = available_weighted_score([
            (row.get("relative_market_1d_percentile")/100 if pd.notna(row.get("relative_market_1d_percentile")) else None, 1),
            (row.get("relative_market_5d_percentile")/100 if pd.notna(row.get("relative_market_5d_percentile")) else None, 2),
            (row.get("relative_market_20d_percentile")/100 if pd.notna(row.get("relative_market_20d_percentile")) else None, 2),
        ], 5, minimum_completeness)
        trend_components = [
            (float(row["close"] >= row["ma20"]) if pd.notna(row.get("ma20")) else None, 1.5),
            (float(row["ma5"] >= row["ma10"]) if pd.notna(row.get("ma5")) and pd.notna(row.get("ma10")) else None, 1),
            (float(row["ma10"] >= row["ma20"]) if pd.notna(row.get("ma10")) and pd.notna(row.get("ma20")) else None, 1),
            (float(row["ma20"] >= row["ma60"]) if pd.notna(row.get("ma20")) and pd.notna(row.get("ma60")) else None, 1),
            (float(row["ma5_slope_3d_pct"] > 0) if pd.notna(row.get("ma5_slope_3d_pct")) else None, 1),
            (float(row["ma20_slope_5d_pct"] > 0) if pd.notna(row.get("ma20_slope_5d_pct")) else None, 1),
            (float(row["close"] >= row["ma120"]) if pd.notna(row.get("ma120")) else None, 0.75),
            (float(row["ma60_slope_20d_pct"] > 0 or (pd.notna(row.get("ma250")) and row["close"] >= row["ma250"])) if pd.notna(row.get("ma60_slope_20d_pct")) or pd.notna(row.get("ma250")) else None, 0.75),
        ]
        trend, trend_c = available_weighted_score(trend_components, 8, minimum_completeness)
        continuation, continuation_c = available_weighted_score([
            (float(row["close"] >= row["ma20"] and row["ma20_slope_5d_pct"] > 0) if pd.notna(row.get("ma20")) and pd.notna(row.get("ma20_slope_5d_pct")) else None, 2),
            (_healthy_band(row.get("position_20d_pct"), 70, 100, 55, 100), 1.5),
            (_healthy_band(row.get("position_60d_pct"), 60, 100, 45, 100), 1.5),
            (_healthy_band(row.get("drawdown_from_20d_high_pct"), -12, 0, -18, 0), 1.5),
            (_healthy_band(row.get("distance_ma20_atr"), 0, 2.5, -0.5, 3.5), 1.5),
        ], 8, minimum_completeness)
        bottom, bottom_c = available_weighted_score([
            (row.get("three_days_no_new_low"), 2),
            (float(row["close"] >= row["ma5"]) if pd.notna(row.get("ma5")) else None, 1),
            (float(row["close"] >= row["ma10"]) if pd.notna(row.get("ma10")) else None, 1),
            (float(row["ma5_slope_3d_pct"] > 0) if pd.notna(row.get("ma5_slope_3d_pct")) else None, 1),
            (float(row["ma5_slope_change_pct"] > 0) if pd.notna(row.get("ma5_slope_change_pct")) else None, 1),
            (_healthy_band(row.get("position_20d_pct"), 30, 80, 20, 90), 1),
            (row.get("breakout_3d"), 1),
        ], 8, minimum_completeness)
        stage_candidates = [("trend_continuation", continuation, continuation_c), ("bottom_right_side", bottom, bottom_c)]
        stage_type, stage, stage_c = max(stage_candidates, key=lambda item: -1 if item[1] is None else item[1])
        price_volume_confirmation = None
        if pd.notna(row.get("return_1d_pct")) and pd.notna(row.get("volume_ratio_1d_prior_10d")):
            price_volume_confirmation = float(
                (row["return_1d_pct"] > 0 and row["volume_ratio_1d_prior_10d"] >= 1)
                or (row["return_1d_pct"] <= 0 and row["volume_ratio_1d_prior_10d"] < 1)
            )
        volume_price, volume_c = available_weighted_score([
            (row.get("volume_ratio_1d_percentile")/100 if pd.notna(row.get("volume_ratio_1d_percentile")) else None, 1.5),
            (row.get("amount_ratio_1d_percentile")/100 if pd.notna(row.get("amount_ratio_1d_percentile")) else None, 1.5),
            (row.get("volume_ratio_5d_percentile")/100 if pd.notna(row.get("volume_ratio_5d_percentile")) else None, 1),
            (row.get("close_location"), 1),
            (price_volume_confirmation, 1),
        ], 6, minimum_completeness)
        momentum, momentum_c = available_weighted_score([
            (row.get("return_5d_percentile")/100 if pd.notna(row.get("return_5d_percentile")) else None, 1),
            (row.get("return_20d_percentile")/100 if pd.notna(row.get("return_20d_percentile")) else None, 1),
            (row.get("relative_sector_5d_percentile")/100 if pd.notna(row.get("relative_sector_5d_percentile")) else None, 1),
            (row.get("relative_sector_20d_percentile")/100 if pd.notna(row.get("relative_sector_20d_percentile")) else None, 1),
            (_healthy_band(row.get("rsi14"), 50, 75, 40, 82), 1),
        ], 5, minimum_completeness)
        structure, structure_c = available_weighted_score([
            (_healthy_band(row.get("atr_percentile"), 25, 85, 10, 95), 1),
            (_healthy_band(row.get("distance_ma20_atr"), -0.5, 2.5, -1.5, 3.5), 1),
            (_healthy_band(row.get("upper_shadow_atr"), 0, 0.5, 0, 1.0), 1),
        ], 3, minimum_completeness)
        technical, technical_c = available_weighted_score([
            (trend/8 if trend is not None else None, 8),
            (stage/8 if stage is not None else None, 8),
            (volume_price/6 if volume_price is not None else None, 6),
            (momentum/5 if momentum is not None else None, 5),
            (structure/3 if structure is not None else None, 3),
        ], 30, minimum_completeness)
        sector_score = float(row["sector_score"]) if pd.notna(row.get("sector_score")) else None
        quant, quant_c = available_weighted_score([
            (market/5 if market is not None else None, 5),
            (sector_score/15 if sector_score is not None else None, 15),
            (technical/30 if technical is not None else None, 30),
        ], 50, minimum_completeness)
        record = row.to_dict()
        record.update({
            "market_fit_score": market,
            "trend_structure_score": trend,
            "stage_pattern_score": stage,
            "setup_type": stage_type,
            "volume_price_score": volume_price,
            "momentum_relative_strength_score": momentum,
            "structure_health_score": structure,
            "technical_score": None if technical is None else round(technical, 6),
            "quant_score": None if quant is None else round(quant, 6),
            "market_fit_completeness": round(market_c, 6),
            "technical_completeness": round(technical_c, 6),
            "quantitative_completeness": round(quant_c, 6),
            "data_complete": bool(quant is not None and technical is not None),
        })
        records.append(record)
    result = pd.DataFrame(records)
    if result.empty:
        return result
    result["technical_eligible"] = result["data_complete"] & numeric(result["technical_score"]).ge(18.0)
    result.sort_values(
        ["quant_score", "technical_score", "sector_score", "market_fit_score", "ts_code"],
        ascending=[False, False, False, False, True], na_position="last", kind="stable", inplace=True,
    )
    result["quant_rank"] = np.arange(1, len(result)+1)
    return result.reset_index(drop=True)


def strong_sectors(sectors: pd.DataFrame, minimum_score: float = 9.0, top_fraction: float = 0.30) -> set[str]:
    if sectors.empty:
        return set()
    top_count = max(1, math.ceil(len(sectors) * float(top_fraction)))
    eligible = sectors.loc[
        numeric(sectors["sector_score"]).ge(float(minimum_score))
        & numeric(sectors["sector_rank"]).le(top_count),
        "industry",
    ]
    return set(eligible.astype(str))


def risk_annotations_do_not_change_scores(before: pd.DataFrame, after: pd.DataFrame) -> bool:
    keys = ["ts_code", "market_fit_score", "sector_score", "technical_score", "quant_score"]
    left = before[keys].sort_values("ts_code").reset_index(drop=True)
    right = after[keys].sort_values("ts_code").reset_index(drop=True)
    return left.equals(right)
