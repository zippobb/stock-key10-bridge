#!/usr/bin/env python3
"""Market-data access for V2.0; contains no selection logic."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import tushare as ts

from stock_key10_v2_common import atomic_write


DAILY_COLUMNS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
METADATA_COLUMNS = ["ts_code", "name", "industry", "segment", "total_mv_yi", "delisting_risk", "list_date"]


def normalize_date(series: pd.Series) -> pd.Series:
    return series.astype(str).str.replace("-", "", regex=False).str.replace(r"\.0$", "", regex=True)


def require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = set(columns)-set(frame.columns)
    if missing:
        raise ValueError(f"{label}缺少字段: {sorted(missing)}")


def fetch_daily_once(target_date: str, cache_path: Path) -> tuple[pd.DataFrame, str]:
    if cache_path.is_file():
        cached = pd.read_csv(cache_path, dtype={"ts_code": "string", "trade_date": "string"})
        require_columns(cached, DAILY_COLUMNS, "当日日线缓存")
        cached["trade_date"] = normalize_date(cached["trade_date"])
        if len(cached) >= 5000 and set(cached["trade_date"]) == {target_date} and cached["ts_code"].is_unique:
            return cached, "existing_valid_cache"
        raise ValueError(f"已有当日日线缓存不完整，停止且不覆盖: {cache_path}")
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN环境变量不可用")
    daily = ts.pro_api(token).daily(trade_date=target_date)
    if daily is None:
        raise ValueError("数据源没有返回目标日日线")
    daily = daily.copy()
    require_columns(daily, DAILY_COLUMNS, "目标日日线")
    daily["trade_date"] = normalize_date(daily["trade_date"])
    if len(daily) < 5000 or set(daily["trade_date"]) != {target_date} or not daily["ts_code"].is_unique:
        raise ValueError(f"目标日日线不完整: rows={len(daily)}")
    daily.sort_values("ts_code", kind="stable", inplace=True)
    atomic_write(cache_path, daily.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    return daily, "tushare_single_daily_request"


def classify_segment(ts_code: str) -> str:
    code, exchange = str(ts_code).split(".")
    if exchange == "SH" and code.startswith(("600", "601", "603", "605")):
        return "沪市主板"
    if exchange == "SZ" and code.startswith(("000", "001", "002", "003")):
        return "深市主板"
    if exchange == "SZ" and code.startswith(("300", "301")):
        return "创业板"
    if exchange == "SH" and code.startswith(("688", "689")):
        return "科创板"
    return "排除市场"


def fetch_market_metadata(target_date: str, target_daily: pd.DataFrame, cache_path: Path) -> tuple[pd.DataFrame, str]:
    if cache_path.is_file():
        cached = pd.read_csv(cache_path, dtype={"ts_code": "string", "list_date": "string"})
        require_columns(cached, METADATA_COLUMNS, "市场元数据缓存")
        if len(cached) >= 5000 and cached["ts_code"].is_unique:
            cached["delisting_risk"] = cached["delisting_risk"].astype(str).str.lower().isin({"true", "1"})
            return cached, "existing_valid_cache"
        raise ValueError(f"已有市场元数据缓存不完整: {cache_path}")
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN环境变量不可用")
    pro = ts.pro_api(token)
    basic = pro.daily_basic(trade_date=target_date, fields="ts_code,total_mv")
    stocks = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name,industry,list_date")
    if basic is None or basic.empty or stocks is None or stocks.empty:
        raise RuntimeError("市场市值或股票元数据不可用")
    if basic["ts_code"].duplicated().any() or stocks["ts_code"].duplicated().any():
        raise ValueError("市场元数据股票代码重复")
    metadata = stocks.merge(basic, on="ts_code", how="left", validate="one_to_one")
    metadata["segment"] = metadata["ts_code"].map(classify_segment)
    metadata["total_mv_yi"] = pd.to_numeric(metadata["total_mv"], errors="coerce")/10000.0
    metadata["delisting_risk"] = metadata["name"].fillna("").str.contains(r"\*ST|退", case=False, regex=True)
    metadata = metadata[METADATA_COLUMNS].sort_values("ts_code", kind="stable")
    if len(metadata) < 5000 or metadata["ts_code"].duplicated().any():
        raise ValueError(f"市场元数据不完整: rows={len(metadata)}")
    atomic_write(cache_path, metadata.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    return metadata, "tushare_daily_basic_and_stock_basic"


def fetch_major_index_history(target_date: str, cache_path: Path) -> tuple[pd.DataFrame, str]:
    needed = ["ts_code", "index_name", "trade_date", "close", "pct_chg"]
    if cache_path.is_file():
        cached = pd.read_csv(cache_path, dtype={"ts_code": "string", "trade_date": "string"})
        require_columns(cached, needed, "主要指数缓存")
        cached["trade_date"] = normalize_date(cached["trade_date"])
        if not cached.empty and cached["trade_date"].max() == target_date and not cached["trade_date"].gt(target_date).any():
            return cached, "existing_valid_cache"
        raise ValueError(f"主要指数缓存不完整: {cache_path}")
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("TUSHARE_TOKEN环境变量不可用")
    pro = ts.pro_api(token)
    start = (datetime.strptime(target_date, "%Y%m%d")-timedelta(days=60)).strftime("%Y%m%d")
    pieces: list[pd.DataFrame] = []
    for code, name in (("000001.SH", "上证综指"), ("399001.SZ", "深证成指"), ("399006.SZ", "创业板指")):
        frame = pro.index_daily(ts_code=code, start_date=start, end_date=target_date)
        if frame is None or frame.empty:
            raise RuntimeError(f"指数数据不可用: {code}")
        frame = frame.copy()
        frame["trade_date"] = normalize_date(frame["trade_date"])
        frame["index_name"] = name
        pieces.append(frame[needed])
    result = pd.concat(pieces, ignore_index=True).sort_values(["ts_code", "trade_date"], kind="stable")
    if result["trade_date"].gt(target_date).any() or set(result.groupby("ts_code")["trade_date"].max()) != {target_date}:
        raise ValueError("主要指数数据含未来行或目标日不完整")
    atomic_write(cache_path, result.to_csv(index=False, lineterminator="\n").encode("utf-8"))
    return result, "tushare_three_major_indices"


def load_recent_market_history(
    cache_dir: Path,
    target_daily: pd.DataFrame,
    target_date: str,
    base_codes: set[str],
) -> tuple[pd.DataFrame, list[str]]:
    dated = sorted(
        (path.stem.removeprefix("daily_"), path)
        for path in cache_dir.glob("daily_*.csv")
        if path.stem.removeprefix("daily_").isdigit() and path.stem.removeprefix("daily_") < target_date
    )[-35:]
    if len(dated) < 30:
        raise ValueError(f"轻量市场历史不足30个交易日: {len(dated)}")
    pieces: list[pd.DataFrame] = []
    for _, path in dated:
        day = pd.read_csv(path, dtype={"ts_code": "string", "trade_date": "string"}, low_memory=False)
        require_columns(day, DAILY_COLUMNS, str(path))
        day["trade_date"] = normalize_date(day["trade_date"])
        pieces.append(day.loc[day["ts_code"].isin(base_codes), DAILY_COLUMNS].copy())
    pieces.append(target_daily.loc[target_daily["ts_code"].isin(base_codes), DAILY_COLUMNS].copy())
    history = pd.concat(pieces, ignore_index=True)
    history.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    if history.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("轻量市场历史存在重复")
    return history, sorted(history["trade_date"].dropna().unique().tolist())


def add_light_features(history: pd.DataFrame) -> pd.DataFrame:
    frame = history.copy()
    for column in DAILY_COLUMNS[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    group = frame.groupby("ts_code", sort=False, group_keys=False)
    for window in (5, 10, 20):
        frame[f"ma{window}"] = group["close"].transform(lambda values, w=window: values.rolling(w, min_periods=w).mean())
    for days in (3, 5, 20):
        frame[f"return_{days}d_pct"] = (frame["close"]/group["close"].shift(days)-1.0)*100.0
    frame["vol_ma20"] = group["vol"].transform(lambda values: values.rolling(20, min_periods=20).mean())
    frame["amount_ma20"] = group["amount"].transform(lambda values: values.where(values.gt(0)).rolling(20, min_periods=15).mean())
    frame["volume_ratio_1d_20d"] = (frame["vol"]/frame["vol_ma20"].where(frame["vol_ma20"].gt(0))).replace([np.inf, -np.inf], np.nan)
    return frame
