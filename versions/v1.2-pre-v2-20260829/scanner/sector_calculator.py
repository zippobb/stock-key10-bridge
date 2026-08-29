#!/usr/bin/env python3
"""Aggregate latest stock indicators into objective A-share industry features."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd


INDICATOR_REQUIRED = [
    "ts_code",
    "trade_date",
    "history_days",
    "pct_chg",
    "return_5d_pct",
    "return_20d_pct",
    "return_60d_pct",
    "close_above_ma20",
    "ma5_above_ma10_above_ma20",
    "volume_ratio_1d_20d",
    "position_20d_pct",
    "relative_strength_20d_percentile",
]

DEFAULT_EXCLUDED = ("白酒", "证券", "银行")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="计算A股行业板块强弱与市场宽度指标")
    parser.add_argument(
        "--indicators",
        default="output/indicators_latest.csv",
        help="最新个股指标文件",
    )
    parser.add_argument(
        "--universe",
        default="output/base_universe_latest.csv",
        help="最新基础股票池文件",
    )
    parser.add_argument(
        "--output",
        default="output/sector_indicators_latest.csv",
        help="最新行业指标输出文件",
    )
    parser.add_argument(
        "--snapshot-dir",
        default="data/sector_indicators",
        help="按交易日保存行业指标快照的目录",
    )
    parser.add_argument(
        "--min-primary-stocks",
        type=int,
        default=10,
        help="进入主板块排名所需的最少股票数",
    )
    parser.add_argument(
        "--excluded-industries",
        default=",".join(DEFAULT_EXCLUDED),
        help="不进入机会推荐的行业，使用英文逗号分隔",
    )
    return parser.parse_args()


def require_columns(df: pd.DataFrame, required: list[str], label: str) -> None:
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def mean_true(series: pd.Series) -> float:
    valid = series.dropna()
    if valid.empty:
        return np.nan
    return float(valid.astype(bool).mean() * 100.0)


def percentile_on_mask(series: pd.Series, mask: pd.Series) -> pd.Series:
    result = pd.Series(np.nan, index=series.index, dtype="float64")
    valid = mask & series.notna()
    result.loc[valid] = series.loc[valid].rank(pct=True, method="average") * 100.0
    return result


def sample_status(stock_count: pd.Series, minimum: int) -> pd.Series:
    small_floor = min(5, minimum)
    return pd.Series(
        np.select(
            [stock_count >= minimum, stock_count >= small_floor],
            ["主样本", "小样本"],
            default="样本不足",
        ),
        index=stock_count.index,
    )


def build_sector_indicators(
    indicators: pd.DataFrame,
    universe: pd.DataFrame,
    excluded_industries: set[str],
    min_primary_stocks: int,
) -> pd.DataFrame:
    if min_primary_stocks < 2:
        raise ValueError("min_primary_stocks必须至少为2")

    require_columns(indicators, INDICATOR_REQUIRED, "个股指标文件")
    require_columns(universe, ["ts_code", "industry"], "基础股票池")

    if indicators["ts_code"].duplicated().any():
        count = int(indicators["ts_code"].duplicated().sum())
        raise ValueError(f"个股指标文件存在重复股票代码: {count}")
    if universe["ts_code"].duplicated().any():
        count = int(universe["ts_code"].duplicated().sum())
        raise ValueError(f"基础股票池存在重复股票代码: {count}")
    if indicators["trade_date"].nunique() != 1:
        raise ValueError("个股指标文件必须只包含一个交易日")

    industry = universe["industry"].astype("string").str.strip()
    missing_industry = industry.isna() | industry.eq("") | industry.str.lower().isin(
        ["nan", "none", "null"]
    )
    if missing_industry.any():
        raise ValueError(f"基础股票池存在未分类股票: {int(missing_industry.sum())}")

    metadata = universe[["ts_code"]].copy()
    metadata["industry"] = industry
    merged = indicators.merge(metadata, on="ts_code", how="left", validate="one_to_one")

    missing_codes = merged.loc[merged["industry"].isna(), "ts_code"].tolist()
    universe_only = sorted(set(universe["ts_code"]) - set(indicators["ts_code"]))
    if missing_codes or universe_only:
        raise ValueError(
            "个股指标与基础股票池不一致: "
            f"missing_industry_codes={len(missing_codes)}, universe_only={len(universe_only)}"
        )

    numeric_columns = [column for column in INDICATOR_REQUIRED if column not in {"ts_code", "trade_date"}]
    merged[numeric_columns] = merged[numeric_columns].apply(pd.to_numeric, errors="coerce")

    merged["up_1d"] = merged["pct_chg"] > 0
    for window in (5, 20, 60):
        merged[f"positive_return_{window}d"] = merged[f"return_{window}d_pct"].gt(0).where(
            merged[f"return_{window}d_pct"].notna()
        )
    merged["close_above_ma20_bool"] = merged["close_above_ma20"].eq(1).where(
        merged["close_above_ma20"].notna()
    )
    merged["bullish_alignment_bool"] = merged["ma5_above_ma10_above_ma20"].eq(1).where(
        merged["ma5_above_ma10_above_ma20"].notna()
    )
    merged["strong_20d_bool"] = merged["relative_strength_20d_percentile"].ge(70).where(
        merged["relative_strength_20d_percentile"].notna()
    )
    merged["near_20d_high_bool"] = merged["position_20d_pct"].ge(90).where(
        merged["position_20d_pct"].notna()
    )
    merged["volume_expansion_bool"] = merged["volume_ratio_1d_20d"].ge(1.2).where(
        merged["volume_ratio_1d_20d"].notna()
    )

    rows: list[dict[str, object]] = []
    trade_date = str(merged["trade_date"].iloc[0])
    for industry_name, group in merged.groupby("industry", sort=True):
        row: dict[str, object] = {
            "industry": industry_name,
            "trade_date": trade_date,
            "stock_count": int(len(group)),
            "history_20d_count": int(group["return_20d_pct"].notna().sum()),
            "history_60d_count": int(group["return_60d_pct"].notna().sum()),
            "return_1d_ew_mean_pct": float(group["pct_chg"].mean()),
            "return_5d_ew_mean_pct": float(group["return_5d_pct"].mean()),
            "return_20d_ew_mean_pct": float(group["return_20d_pct"].mean()),
            "return_60d_ew_mean_pct": float(group["return_60d_pct"].mean()),
            "return_5d_median_pct": float(group["return_5d_pct"].median()),
            "return_20d_median_pct": float(group["return_20d_pct"].median()),
            "return_60d_median_pct": float(group["return_60d_pct"].median()),
            "up_1d_ratio_pct": mean_true(group["up_1d"]),
            "positive_5d_ratio_pct": mean_true(group["positive_return_5d"]),
            "positive_20d_ratio_pct": mean_true(group["positive_return_20d"]),
            "positive_60d_ratio_pct": mean_true(group["positive_return_60d"]),
            "above_ma20_ratio_pct": mean_true(group["close_above_ma20_bool"]),
            "bullish_alignment_ratio_pct": mean_true(group["bullish_alignment_bool"]),
            "strong_20d_ratio_pct": mean_true(group["strong_20d_bool"]),
            "near_20d_high_ratio_pct": mean_true(group["near_20d_high_bool"]),
            "volume_expansion_ratio_pct": mean_true(group["volume_expansion_bool"]),
            "volume_ratio_1d_20d_median": float(group["volume_ratio_1d_20d"].median()),
        }
        rows.append(row)

    sector = pd.DataFrame(rows)
    sector["sample_status"] = sample_status(sector["stock_count"], min_primary_stocks)
    sector["sample_eligible"] = (sector["stock_count"] >= min_primary_stocks).astype("int8")
    sector["excluded_by_rule"] = sector["industry"].isin(excluded_industries).astype("int8")
    sector["selection_eligible"] = (
        sector["sample_eligible"].eq(1) & sector["excluded_by_rule"].eq(0)
    ).astype("int8")
    sector["excluded_reason"] = np.select(
        [
            sector["excluded_by_rule"].eq(1),
            sector["sample_eligible"].eq(0),
        ],
        [
            "用户规则排除",
            "样本数不足",
        ],
        default="",
    )

    sample_mask = sector["sample_eligible"].eq(1)
    rank_inputs = {
        "return_5d_strength_percentile": "return_5d_ew_mean_pct",
        "return_20d_strength_percentile": "return_20d_ew_mean_pct",
        "return_60d_strength_percentile": "return_60d_ew_mean_pct",
        "above_ma20_breadth_percentile": "above_ma20_ratio_pct",
        "bullish_alignment_breadth_percentile": "bullish_alignment_ratio_pct",
        "strong_20d_breadth_percentile": "strong_20d_ratio_pct",
    }
    for output_column, source_column in rank_inputs.items():
        sector[output_column] = percentile_on_mask(sector[source_column], sample_mask)

    score_weights = {
        "return_5d_strength_percentile": 0.20,
        "return_20d_strength_percentile": 0.30,
        "return_60d_strength_percentile": 0.15,
        "above_ma20_breadth_percentile": 0.15,
        "bullish_alignment_breadth_percentile": 0.10,
        "strong_20d_breadth_percentile": 0.10,
    }
    score = sum(sector[column] * weight for column, weight in score_weights.items())
    sector["sector_strength_score"] = score.where(sample_mask)
    sector["sector_strength_rank"] = sector["sector_strength_score"].rank(
        ascending=False,
        method="min",
    )
    selection_mask = sector["selection_eligible"].eq(1)
    sector["selection_rank"] = sector.loc[selection_mask, "sector_strength_score"].rank(
        ascending=False,
        method="min",
    )

    preferred = [
        "industry",
        "trade_date",
        "stock_count",
        "history_20d_count",
        "history_60d_count",
        "sample_status",
        "sample_eligible",
        "excluded_by_rule",
        "selection_eligible",
        "excluded_reason",
        "sector_strength_score",
        "sector_strength_rank",
        "selection_rank",
    ]
    ordered = preferred + [column for column in sector.columns if column not in preferred]
    sector = sector[ordered].sort_values(
        ["selection_eligible", "sector_strength_score", "industry"],
        ascending=[False, False, True],
        na_position="last",
        kind="stable",
    )
    return sector.reset_index(drop=True)


def main() -> None:
    args = parse_args()
    indicator_path = Path(args.indicators)
    universe_path = Path(args.universe)
    output_path = Path(args.output)
    snapshot_dir = Path(args.snapshot_dir)

    if not indicator_path.exists():
        raise FileNotFoundError(f"找不到个股指标文件: {indicator_path}")
    if not universe_path.exists():
        raise FileNotFoundError(f"找不到基础股票池: {universe_path}")

    indicators = pd.read_csv(indicator_path, dtype={"ts_code": str, "trade_date": str})
    universe = pd.read_csv(universe_path, dtype={"ts_code": str})
    excluded = {item.strip() for item in args.excluded_industries.split(",") if item.strip()}

    sector = build_sector_indicators(
        indicators=indicators,
        universe=universe,
        excluded_industries=excluded,
        min_primary_stocks=args.min_primary_stocks,
    )

    trade_date = str(sector["trade_date"].iloc[0])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"sector_indicators_{trade_date}.csv"
    sector.to_csv(output_path, index=False, encoding="utf-8-sig")
    sector.to_csv(snapshot_path, index=False, encoding="utf-8-sig")

    top_columns = [
        "selection_rank",
        "industry",
        "stock_count",
        "sector_strength_score",
        "return_5d_ew_mean_pct",
        "return_20d_ew_mean_pct",
        "return_60d_ew_mean_pct",
        "above_ma20_ratio_pct",
        "bullish_alignment_ratio_pct",
    ]
    top = sector.loc[sector["selection_eligible"].eq(1), top_columns].nsmallest(
        10,
        "selection_rank",
    )
    summary = {
        "run_time": datetime.now().astimezone().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "stock_rows": int(len(indicators)),
        "sector_rows": int(len(sector)),
        "total_sector_stocks": int(sector["stock_count"].sum()),
        "primary_sample_sectors": int(sector["sample_eligible"].sum()),
        "small_sample_sectors": int(sector["sample_status"].eq("小样本").sum()),
        "insufficient_sample_sectors": int(sector["sample_status"].eq("样本不足").sum()),
        "excluded_sector_count": int(sector["excluded_by_rule"].sum()),
        "excluded_sector_stocks": int(
            sector.loc[sector["excluded_by_rule"].eq(1), "stock_count"].sum()
        ),
        "selection_eligible_sectors": int(sector["selection_eligible"].sum()),
        "output_file": str(output_path.resolve()),
        "snapshot_file": str(snapshot_path.resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\n当前可选行业强度前10：")
    print(top.to_string(index=False))


if __name__ == "__main__":
    main()
