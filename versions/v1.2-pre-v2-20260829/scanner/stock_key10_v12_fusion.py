#!/usr/bin/env python3
"""Calculate V1.2 wind direction and publish formal V1.0/V1.1/V1.2 fusion results."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import os
import re
import resource
import struct
import tempfile
import time
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


ROOT_DEFAULT = Path("/opt/stock-automation/scanner")
DATE_RE = re.compile(r"^[0-9]{8}$")
TS_CODE_RE = re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$")


def now_bj() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, tuple):
        return [clean(item) for item in value]
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if pd.isna(value) if not isinstance(value, (str, bytes)) else False:
        return None
    return value


def json_bytes(value: object) -> bytes:
    return json.dumps(clean(value), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"


def csv_bytes(rows: list[dict[str, object]], fields: list[str]) -> bytes:
    import io
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: clean(row.get(key)) for key in fields})
    return stream.getvalue().encode("utf-8-sig")


def atomic_outputs(payloads: dict[Path, bytes], refuse_different_dated: bool = True) -> None:
    staged: dict[Path, Path] = {}
    dated_pattern = re.compile(r"_[0-9]{8}\.(?:json|csv|png)$")
    for path, payload in payloads.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise RuntimeError(f"拒绝覆盖符号链接: {path}")
        if path.is_file():
            current = path.read_bytes()
            if current == payload:
                continue
            if refuse_different_dated and dated_pattern.search(path.name):
                raise RuntimeError(f"日期归档已存在且内容不同，拒绝覆盖: {path}")
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        staged[path] = Path(temp_name)
    try:
        for path, temp_path in staged.items():
            os.replace(temp_path, path)
            os.chmod(path, 0o600)
    finally:
        for temp_path in staged.values():
            if temp_path.exists():
                temp_path.unlink()


def percentile(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").rank(pct=True, method="average") * 100.0


def load_light_market(cache_dir: Path, trade_date: str, base: pd.DataFrame, days: int = 25) -> tuple[pd.DataFrame, list[Path]]:
    dated: list[tuple[str, Path]] = []
    for path in cache_dir.glob("daily_*.csv"):
        date = path.stem.replace("daily_", "")
        if DATE_RE.fullmatch(date) and date <= trade_date:
            dated.append((date, path))
    dated.sort()
    selected = dated[-days:]
    if len(selected) < 21 or selected[-1][0] != trade_date:
        raise RuntimeError("V1.2轻量历史不足或缺少目标交易日")
    needed = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
    pieces: list[pd.DataFrame] = []
    base_codes = set(base["ts_code"].astype(str))
    for date, path in selected:
        frame = pd.read_csv(path, usecols=needed, dtype={"ts_code": "string", "trade_date": "string"}, low_memory=False)
        frame["trade_date"] = frame["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
        if set(frame["trade_date"]) != {date} or frame["ts_code"].duplicated().any():
            raise RuntimeError(f"全市场单日缓存日期或唯一性异常: {path.name}")
        pieces.append(frame.loc[frame["ts_code"].isin(base_codes)].copy())
    history = pd.concat(pieces, ignore_index=True)
    history.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    if history.duplicated(["ts_code", "trade_date"]).any() or history["trade_date"].gt(trade_date).any():
        raise RuntimeError("V1.2轻量历史重复或含未来数据")
    return history, [path for _, path in selected]


def stock_wind_table(history: pd.DataFrame, base: pd.DataFrame, trade_date: str, config: dict[str, object]) -> pd.DataFrame:
    frame = history.copy()
    for column in ["open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    group = frame.groupby("ts_code", sort=False, group_keys=False)
    for window in [5, 10, 20]:
        frame[f"ma{window}"] = group["close"].transform(lambda values, w=window: values.rolling(w, min_periods=w).mean())
    for days in [3, 5, 20]:
        frame[f"return_{days}d_pct"] = (frame["close"] / group["close"].shift(days) - 1.0) * 100.0
    frame["ma5_slope_3d_pct"] = (frame["ma5"] / group["ma5"].shift(3) - 1.0) * 100.0
    frame["vol_avg_prior_10d"] = group["vol"].transform(lambda values: values.shift(1).rolling(10, min_periods=10).mean())
    frame["volume_ratio_1d_prior_10d"] = frame["vol"] / frame["vol_avg_prior_10d"]
    day = frame.loc[frame["trade_date"].eq(trade_date)].copy()
    day = day.merge(base[["ts_code", "name", "industry", "segment", "list_date"]], on="ts_code", how="left", validate="one_to_one")
    day.rename(columns={"pct_chg": "return_1d_pct"}, inplace=True)
    for days in [1, 3, 5, 20]:
        source = "return_1d_pct" if days == 1 else f"return_{days}d_pct"
        day[f"industry_return_{days}d_percentile"] = day.groupby("industry", dropna=False)[source].rank(pct=True, method="average") * 100.0
    day["volume_ratio_industry_percentile"] = day.groupby("industry", dropna=False)["volume_ratio_1d_prior_10d"].rank(pct=True, method="average") * 100.0
    day["relative_sector_strength_score"] = day[["industry_return_1d_percentile", "industry_return_3d_percentile"]].mean(axis=1)
    price_confirmation = np.clip(50 + day["return_1d_pct"] * 5, 0, 100)
    day["volume_price_confirmation_score"] = day["volume_ratio_industry_percentile"] * 0.60 + price_confirmation * 0.40
    day["leader_persistence_score"] = (
        day["industry_return_3d_percentile"] * 0.40
        + day["industry_return_5d_percentile"] * 0.35
        + day["industry_return_20d_percentile"] * 0.25
    )
    quality = (
        day["close"].ge(day["ma20"]).astype(float)
        + day["ma5"].ge(day["ma10"]).astype(float)
        + day["ma10"].ge(day["ma20"]).astype(float)
        + day["ma5_slope_3d_pct"].gt(0).astype(float)
    ) * 25.0
    day["trend_quality_score"] = quality.where(day[["ma5", "ma10", "ma20", "ma5_slope_3d_pct"]].notna().all(axis=1))
    weights = config["stock_wind"]["weights"]
    day["stock_wind_score"] = (
        day["relative_sector_strength_score"] * float(weights["relative_sector_strength"])
        + day["volume_price_confirmation_score"] * float(weights["volume_price_confirmation"])
        + day["leader_persistence_score"] * float(weights["leader_persistence"])
        + day["trend_quality_score"] * float(weights["trend_quality"])
    ) / 100.0
    day["stock_wind_score"] = day["stock_wind_score"].round(2)
    day["stock_wind_market_rank"] = day["stock_wind_score"].rank(ascending=False, method="min").astype("Int64")
    return day


def sector_wind_table(sector_json: dict[str, object], config: dict[str, object]) -> pd.DataFrame:
    rows = sector_json.get("all_sectors")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("板块轮动缺少all_sectors")
    frame = pd.DataFrame(rows)
    required = ["industry", "strength_percentile_1d", "strength_percentile_3d", "rank_improvement", "up_1d_ratio_pct", "volume_ratio_percentile"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise RuntimeError(f"板块风向字段缺失: {missing}")
    for column in required[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["sector_strength_1d_3d_score"] = frame[["strength_percentile_1d", "strength_percentile_3d"]].mean(axis=1)
    frame["sector_rank_acceleration_score"] = np.clip(50 + frame["rank_improvement"] * 2.5, 0, 100)
    frame["sector_advance_breadth_score"] = np.clip(frame["up_1d_ratio_pct"], 0, 100)
    frame["sector_volume_expansion_score"] = np.clip(frame["volume_ratio_percentile"], 0, 100)
    weights = config["sector_wind"]["weights"]
    frame["sector_wind_score"] = (
        frame["sector_strength_1d_3d_score"] * float(weights["strength_1d_3d"])
        + frame["sector_rank_acceleration_score"] * float(weights["rank_acceleration"])
        + frame["sector_advance_breadth_score"] * float(weights["advance_breadth"])
        + frame["sector_volume_expansion_score"] * float(weights["volume_expansion"])
    ) / 100.0
    frame["sector_wind_score"] = frame["sector_wind_score"].round(2)
    frame["sector_wind_rank"] = frame["sector_wind_score"].rank(ascending=False, method="min").astype("Int64")
    frame.sort_values(["sector_wind_rank", "industry"], kind="stable", inplace=True)
    return frame


def selected_top_sectors(sectors: pd.DataFrame, config: dict[str, object]) -> set[str]:
    cfg = config["expansion"]
    eligible = sectors.loc[sectors["sector_wind_score"].ge(float(cfg["minimum_sector_wind_score"]))].copy()
    if eligible.empty:
        return set()
    count = int(cfg["top_sector_count"])
    cutoff = int(eligible.iloc[min(count, len(eligible)) - 1]["sector_wind_rank"])
    selected = eligible.loc[eligible["sector_wind_rank"].le(cutoff) if cfg.get("include_ties") else eligible.index.isin(eligible.head(count).index)]
    return set(selected["industry"].astype(str))


def load_deep_history(cache_dir: Path, trade_date: str, codes: set[str], days: int) -> tuple[pd.DataFrame, list[Path]]:
    dated = sorted((path.stem.replace("daily_", ""), path) for path in cache_dir.glob("daily_*.csv") if DATE_RE.fullmatch(path.stem.replace("daily_", "")) and path.stem.replace("daily_", "") <= trade_date)
    selected = dated[-days:]
    if len(selected) < min(250, days) or selected[-1][0] != trade_date:
        raise RuntimeError(f"扩池历史不足: {len(selected)}")
    needed = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]
    pieces = []
    for date, path in selected:
        frame = pd.read_csv(path, usecols=needed, dtype={"ts_code": "string", "trade_date": "string"}, low_memory=False)
        frame["trade_date"] = frame["trade_date"].astype(str).str.replace(r"\.0$", "", regex=True)
        subset = frame.loc[frame["ts_code"].isin(codes)].copy()
        pieces.append(subset)
    result = pd.concat(pieces, ignore_index=True)
    result.sort_values(["ts_code", "trade_date"], kind="stable", inplace=True)
    if result.duplicated(["ts_code", "trade_date"]).any() or result["trade_date"].gt(trade_date).any():
        raise RuntimeError("扩池历史重复或包含未来数据")
    return result, [path for _, path in selected]


FONT = {
    " ":["000","000","000","000","000","000","000"],".":["000","000","000","000","000","010","010"],"-":["000","000","000","111","000","000","000"],
    ":":["000","010","010","000","010","010","000"],"/":["001","001","010","010","100","100","000"],
    "0":["111","101","101","101","101","101","111"],"1":["010","110","010","010","010","010","111"],"2":["111","001","001","111","100","100","111"],"3":["111","001","001","111","001","001","111"],"4":["101","101","101","111","001","001","001"],"5":["111","100","100","111","001","001","111"],"6":["111","100","100","111","101","101","111"],"7":["111","001","001","010","010","100","100"],"8":["111","101","101","111","101","101","111"],"9":["111","101","101","111","001","001","111"],
    "A":["010","101","101","111","101","101","101"],"B":["110","101","101","110","101","101","110"],"C":["111","100","100","100","100","100","111"],"D":["110","101","101","101","101","101","110"],"E":["111","100","100","110","100","100","111"],"F":["111","100","100","110","100","100","100"],"G":["111","100","100","101","101","101","111"],"H":["101","101","101","111","101","101","101"],"I":["111","010","010","010","010","010","111"],"J":["001","001","001","001","101","101","111"],"K":["101","101","110","100","110","101","101"],"L":["100","100","100","100","100","100","111"],"M":["10001","11011","10101","10101","10001","10001","10001"],"N":["1001","1101","1101","1011","1011","1001","1001"],"O":["111","101","101","101","101","101","111"],"P":["110","101","101","110","100","100","100"],"Q":["1110","1001","1001","1001","1011","1110","0001"],"R":["110","101","101","110","101","101","101"],"S":["111","100","100","111","001","001","111"],"T":["111","010","010","010","010","010","010"],"U":["101","101","101","101","101","101","111"],"V":["101","101","101","101","101","101","010"],"W":["10001","10001","10001","10101","10101","11011","10001"],"X":["101","101","101","010","101","101","101"],"Y":["101","101","101","010","010","010","010"],"Z":["111","001","001","010","100","100","111"]
}


def png_table(rows: list[dict[str, object]], trade_date: str) -> bytes:
    lines = [f"KEY10 V1.2 FUSION {trade_date}", "RK CODE      FINAL V10 V12 RISK SOURCE"]
    for row in rows[:20]:
        lines.append(f"{int(row['fusion_rank']):02d} {str(row['ts_code']):9s} {float(row['final_score']):5.1f} {float(row['v1_0_raw_score']):3.0f} {float(row['v1_2_wind_score']):3.0f} {str(row['v1_1_risk_status']):7s} {str(row['source_layer']).upper()[:6]}")
    scale, margin, line_h = 2, 12, 18
    width = max(760, margin * 2 + max(len(line) for line in lines) * 12)
    height = margin * 2 + len(lines) * line_h
    pixels = bytearray([255] * width * height * 3)
    for y in range(0, line_h * 2 + margin, 1):
        for x in range(width):
            off = (y * width + x) * 3
            pixels[off:off+3] = bytes((232, 240, 248))
    def put_text(x0: int, y0: int, text: str, color: tuple[int, int, int]) -> None:
        x = x0
        for char in text.upper():
            glyph = FONT.get(char, FONT[" "])
            glyph_w = len(glyph[0])
            for gy, pattern in enumerate(glyph):
                for gx, bit in enumerate(pattern):
                    if bit == "1":
                        for dy in range(scale):
                            for dx in range(scale):
                                px, py = x + gx * scale + dx, y0 + gy * scale + dy
                                if 0 <= px < width and 0 <= py < height:
                                    off = (py * width + px) * 3
                                    pixels[off:off+3] = bytes(color)
            x += (glyph_w + 1) * scale
    for idx, line in enumerate(lines):
        put_text(margin, margin + idx * line_h, line, (16, 32, 48))
    raw = b"".join(b"\x00" + bytes(pixels[y*width*3:(y+1)*width*3]) for y in range(height))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build formal V1.2 fusion recommendations")
    parser.add_argument("--root", default=str(ROOT_DEFAULT))
    parser.add_argument("--config")
    parser.add_argument("--output-dir")
    parser.add_argument("--data-dir")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started = time.perf_counter()
    peak_start = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    root = Path(args.root).resolve(strict=True)
    output = Path(args.output_dir) if args.output_dir else root / "output"
    data_dir = Path(args.data_dir) if args.data_dir else root / "data/fusion"
    source_output = root / "output"
    config_path = Path(args.config) if args.config else root / "config/strategy_v1_2.json"
    paths = {
        "v1_0_formal": source_output / "key10_technical_shortlist_latest.csv",
        "v1_0_near": source_output / "key10_near_signal_watch_latest.csv",
        "v1_1_risk": source_output / "key10_v11_risk_latest.json",
        "market_environment": source_output / "key10_market_environment_latest.json",
        "sector_rotation": source_output / "sector_rotation_latest.json",
        "daily_diagnostics": source_output / "key10_daily_diagnostics_latest.json",
        "base_universe": source_output / "base_universe_latest.csv",
        "observation_pool": root / "config/observation_pool.csv",
        "focus_pool": root / "config/focus_pool.csv",
        "v1_0_strategy": root / "strategy_v1.json",
        "v1_1_strategy": root / "config/strategy_v1_1_risk.json",
        "v1_2_strategy": config_path,
        "v1_0_engine": root / "stock_key10_engine_v1.py",
        "v1_0_signal_module": root / "branch1_selector.py",
        "v1_1_module": root / "stock_key10_v11_risk.py",
    }
    for path in paths.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"固定输入不存在或为符号链接: {path}")
    helper = load_module(root / "stock_key10_v11_shadow.py", "v12_legacy_helpers")
    engine = load_module(paths["v1_0_engine"], "v12_v1_engine")
    risk_module = load_module(paths["v1_1_module"], "v12_v11_risk")
    config = helper.load_json(config_path)
    v1_config = helper.load_json(paths["v1_0_strategy"])
    v11_config = helper.load_json(paths["v1_1_strategy"])
    if config.get("layer_status") != "formal_fusion" or sum(config["sector_wind"]["weights"].values()) != 100 or sum(config["stock_wind"]["weights"].values()) != 100:
        raise RuntimeError("V1.2配置状态或分项权重无效")
    if int(config["wind_fusion"]["sector_weight"]) + int(config["wind_fusion"]["stock_weight"]) != 100 or int(config["formal_fusion"]["v1_0_weight"]) + int(config["formal_fusion"]["v1_2_weight"]) != 100:
        raise RuntimeError("V1.2或正式融合权重和不等于100")

    _, formal = helper.load_csv_records(paths["v1_0_formal"])
    _, near = helper.load_csv_records(paths["v1_0_near"])
    v11 = helper.load_json(paths["v1_1_risk"])
    market = helper.load_json(paths["market_environment"])
    sectors_json = helper.load_json(paths["sector_rotation"])
    diagnostics = helper.load_json(paths["daily_diagnostics"])
    observation = pd.read_csv(paths["observation_pool"], dtype={"ts_code": "string", "code": "string"})
    focus = pd.read_csv(paths["focus_pool"], dtype={"ts_code": "string", "code": "string"})
    base = pd.read_csv(paths["base_universe"], dtype={"ts_code": "string", "trade_date": "string", "list_date": "string"}, low_memory=False)
    if len(observation) != int(config["expected_observation_pool_count"]) or observation["ts_code"].duplicated().any():
        raise RuntimeError("观察池数量或唯一性异常")
    if len(focus) != int(config["expected_focus_pool_count"]) or focus["ts_code"].duplicated().any() or not set(focus["ts_code"]).issubset(set(observation["ts_code"])):
        raise RuntimeError("重点池数量、唯一性或从属关系异常")
    if base["ts_code"].duplicated().any():
        raise RuntimeError("基础股票列表ts_code重复")

    trade_dates = {str(row.get("trade_date", "")) for row in formal + near}
    if len(trade_dates) != 1 or not DATE_RE.fullmatch(next(iter(trade_dates))):
        raise RuntimeError(f"V1.0候选交易日异常: {sorted(trade_dates)}")
    trade_date = next(iter(trade_dates))
    for label, value in {"v1_1_risk": v11.get("trade_date"), "market": market.get("trade_date"), "sector": sectors_json.get("trade_date"), "diagnostics": diagnostics.get("target_trade_date")}.items():
        if str(value) != trade_date:
            raise RuntimeError(f"{label}交易日不一致: {value}")
    if int(v11.get("future_data_rows", -1)) != 0:
        raise RuntimeError("V1.1风险层含未来数据")

    history_light, light_paths = load_light_market(root / "data/history_daily", trade_date, base)
    stock_wind = stock_wind_table(history_light, base, trade_date, config)
    sector_wind = sector_wind_table(sectors_json, config)
    sector_map = {str(row["industry"]): row.to_dict() for _, row in sector_wind.iterrows()}
    stock_wind = stock_wind.merge(sector_wind[["industry", "sector_wind_score", "sector_wind_rank"]], on="industry", how="left", validate="many_to_one")
    stock_wind["v1_2_wind_score"] = (
        stock_wind["sector_wind_score"] * float(config["wind_fusion"]["sector_weight"])
        + stock_wind["stock_wind_score"] * float(config["wind_fusion"]["stock_weight"])
    ) / 100.0
    stock_wind["v1_2_wind_score"] = stock_wind["v1_2_wind_score"].round(2)
    stock_wind["v1_2_wind_market_rank"] = stock_wind["v1_2_wind_score"].rank(ascending=False, method="min").astype("Int64")
    wind_by_code = {str(row["ts_code"]): row.to_dict() for _, row in stock_wind.iterrows()}

    v11_by_code = {str(row.get("ts_code")): row for row in v11.get("candidates", []) if isinstance(row, dict)}
    source_codes = {str(row.get("ts_code")) for row in formal + near}
    if set(v11_by_code) != source_codes:
        raise RuntimeError("V1.1风险层与V1.0候选集合不一致")

    top_sectors = selected_top_sectors(sector_wind, config)
    listing_cutoff = (pd.Timestamp(trade_date) - pd.Timedelta(days=int(config["expansion"]["minimum_listing_calendar_days"]))).strftime("%Y%m%d")
    expansion_pool = stock_wind.loc[
        stock_wind["industry"].isin(top_sectors)
        & stock_wind["stock_wind_score"].ge(float(config["expansion"]["minimum_stock_wind_score"]))
        & stock_wind["list_date"].astype(str).le(listing_cutoff)
        & stock_wind["ts_code"].astype(str).str.endswith((".SH", ".SZ"))
    ].copy()
    expansion_pool.sort_values(["industry", "v1_2_wind_score", "stock_wind_score", "ts_code"], ascending=[True, False, False, True], kind="stable", inplace=True)
    expansion_pool = expansion_pool.groupby("industry", group_keys=False).head(int(config["expansion"]["max_candidates_per_sector_before_fusion"]))
    expansion_codes = set(expansion_pool["ts_code"].astype(str)) - source_codes

    expansion_scored = pd.DataFrame()
    deep_paths: list[Path] = []
    if expansion_codes:
        deep_history, deep_paths = load_deep_history(root / "data/history_daily", trade_date, expansion_codes, int(config["expansion"]["history_days"]))
        features = engine.observation_features(deep_history, trade_date)
        expansion_observation = pd.DataFrame({"ts_code": sorted(expansion_codes)})
        expansion_observation["is_focus"] = expansion_observation["ts_code"].isin(set(focus["ts_code"])).astype(int)
        sector_current = pd.DataFrame(sectors_json["all_sectors"])
        engine.TARGET_DATE = trade_date
        branch1 = engine.load_branch1(paths["v1_0_signal_module"])
        expansion_scored = engine.score_observation(features, expansion_observation, base, sector_current, market, v1_config, branch1)
        if set(expansion_scored["ts_code"].astype(str)) != expansion_codes:
            raise RuntimeError("扩池V1.0特征或评分缺失")

    combined: list[dict[str, object]] = []
    for layer, rows in (("formal", formal), ("near_signal", near)):
        for original in rows:
            code = str(original["ts_code"])
            wind = wind_by_code.get(code)
            risk = v11_by_code[code]
            if wind is None:
                raise RuntimeError(f"V1.0候选缺少V1.2风向数据: {code}")
            raw_score = finite(original.get("total_score" if layer == "formal" else "existing_total_score"))
            if raw_score is None:
                raise RuntimeError(f"V1.0候选分数缺失: {code}")
            original_rank = int(original.get("rank") if layer == "formal" else 1000 + int(original.get("watch_rank", 0)))
            combined.append({"source_layer": layer, "v1_0": original, "v1_0_raw_score": raw_score, "v1_0_original_rank": original_rank, "v1_1": risk, "wind": wind})

    for _, scored in expansion_scored.iterrows():
        code = str(scored["ts_code"])
        wind = wind_by_code[code]
        sector_name = str(scored["sector"])
        sector_row = sector_map.get(sector_name)
        if sector_row is None:
            raise RuntimeError(f"扩池候选板块缺失: {code}")
        metrics = scored.to_dict()
        metrics["industry"] = sector_name
        stage = helper.sector_stage(sector_row, v11_config)
        setup = helper.setup_type(metrics, stage, v11_config)
        profit = helper.profit_taking_risk(metrics, setup, stage, v11_config)
        downgrade = helper.sector_downgrade(metrics, stage, sector_row, v11_config)
        original = {"risk_status": scored.get("risk_status"), "risk_reasons": scored.get("risk_reasons"), "direct_excluded": bool(scored.get("direct_excluded"))}
        status, reasons = risk_module.risk_status(original, profit, downgrade, v11_config)
        risk = {"ts_code": code, "setup_type": setup, "sector_stage": stage, "profit_taking_risk": profit, "sector_downgrade": downgrade, "v1_1_risk_status": status, "v1_1_risk_reasons": reasons, "hard_risk_can_be_offset": False}
        combined.append({"source_layer": "v1_2_expansion", "v1_0": clean(scored.to_dict()), "v1_0_raw_score": float(scored["total_score"]), "v1_0_original_rank": 1000000, "v1_1": risk, "wind": wind})

    fused: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    for item in combined:
        original = item["v1_0"]
        risk = item["v1_1"]
        wind = item["wind"]
        code = str(original.get("ts_code"))
        raw_score = float(item["v1_0_raw_score"])
        normalized = float(np.clip(raw_score, 0, 100))
        v12_score = float(wind["v1_2_wind_score"])
        final_score = round(normalized * float(config["formal_fusion"]["v1_0_weight"]) / 100.0 + v12_score * float(config["formal_fusion"]["v1_2_weight"]) / 100.0, 2)
        status = str(risk.get("v1_1_risk_status"))
        row = clean({
            "trade_date": trade_date,
            "ts_code": code,
            "name": original.get("name") or wind.get("name"),
            "is_focus": bool(original.get("is_focus", wind.get("is_focus", False))),
            "industry_sector": original.get("sector") or wind.get("industry"),
            "source_layer": item["source_layer"],
            "v1_0_raw_score": round(raw_score, 2),
            "v1_0_normalized_score": round(normalized, 2),
            "v1_0_original_rank": int(item["v1_0_original_rank"]),
            "v1_0_original_risk_status": original.get("risk_status"),
            "v1_1_risk_status": status,
            "v1_1_risk_reasons": risk.get("v1_1_risk_reasons"),
            "v1_1_setup_type": risk.get("setup_type"),
            "v1_1_sector_stage": risk.get("sector_stage"),
            "v1_1_profit_taking_risk": risk.get("profit_taking_risk"),
            "v1_1_sector_downgrade": risk.get("sector_downgrade"),
            "hard_risk_can_be_offset": False,
            "sector_wind_score": wind.get("sector_wind_score"),
            "sector_wind_rank": wind.get("sector_wind_rank"),
            "sector_strength_1d_3d_score": sector_map.get(str(wind.get("industry")), {}).get("sector_strength_1d_3d_score"),
            "sector_rank_acceleration_score": sector_map.get(str(wind.get("industry")), {}).get("sector_rank_acceleration_score"),
            "sector_advance_breadth_score": sector_map.get(str(wind.get("industry")), {}).get("sector_advance_breadth_score"),
            "sector_volume_expansion_score": sector_map.get(str(wind.get("industry")), {}).get("sector_volume_expansion_score"),
            "stock_wind_score": wind.get("stock_wind_score"),
            "stock_wind_market_rank": wind.get("stock_wind_market_rank"),
            "relative_sector_strength_score": wind.get("relative_sector_strength_score"),
            "volume_price_confirmation_score": wind.get("volume_price_confirmation_score"),
            "leader_persistence_score": wind.get("leader_persistence_score"),
            "trend_quality_score": wind.get("trend_quality_score"),
            "v1_2_wind_score": round(v12_score, 2),
            "v1_2_wind_market_rank": wind.get("v1_2_wind_market_rank"),
            "concept_bonus": 0,
            "concept_bonus_applied": False,
            "final_score": final_score,
            "v1_0_attribution": original,
        })
        if status == "FAIL" or final_score < float(config["formal_fusion"]["minimum_final_score"]):
            excluded.append({**row, "exclusion_reason": "V1.1硬风险FAIL" if status == "FAIL" else "final_score低于60"})
        else:
            fused.append(row)

    risk_order = {"FAIL": 2, "WARNING": 1, "PASS": 0}
    fused.sort(key=lambda row: (risk_order.get(str(row["v1_1_risk_status"]), 9), -float(row["final_score"]), -float(row["relative_sector_strength_score"] or -1), -float(row["leader_persistence_score"] or -1), int(row["v1_0_original_rank"]), str(row["ts_code"])))
    for index, row in enumerate(fused, 1):
        row["all_eligible_rank"] = index
    formal_output_max = int(config["formal_fusion"]["formal_output_max"])
    if formal_output_max != 10:
        raise RuntimeError("正式输出上限必须为10")
    recommendations = [row for row in fused if row["v1_1_risk_status"] == "PASS"][:min(int(config["formal_fusion"]["recommendation_size"]), formal_output_max)]
    recommendation_codes = {str(row["ts_code"]) for row in recommendations}
    remaining_slots = formal_output_max - len(recommendations)
    alternate_pool = [row for row in fused if str(row["ts_code"]) not in recommendation_codes]
    alternate_pool.sort(key=lambda row: (-float(row["final_score"]), -float(row["relative_sector_strength_score"] or -1), -float(row["leader_persistence_score"] or -1), int(row["v1_0_original_rank"]), str(row["ts_code"])))
    alternates = alternate_pool[:min(int(config["formal_fusion"]["alternate_size"]), remaining_slots)]
    published = recommendations + alternates
    published.sort(key=lambda row: (0 if str(row["ts_code"]) in recommendation_codes else 1, row["all_eligible_rank"]))
    for index, row in enumerate(published, 1):
        row["fusion_rank"] = index
        row["recommendation_type"] = "推荐" if str(row["ts_code"]) in recommendation_codes else "备选"
    recommendations = [row for row in published if row["recommendation_type"] == "推荐"]
    alternates = [row for row in published if row["recommendation_type"] == "备选"]
    published_codes = {str(row["ts_code"]) for row in published}
    audit_only = []
    for row in fused:
        if str(row["ts_code"]) in published_codes:
            continue
        audit_row = dict(row)
        audit_row["audit_only"] = True
        audit_row["exclusion_reason"] = "正式输出上限10只；推荐超额转audit_only" if row["v1_1_risk_status"] == "PASS" else "正式输出上限10只；备选名额已满"
        audit_only.append(audit_row)
    if len(published) > formal_output_max:
        raise RuntimeError("推荐与备选合计超过正式输出上限")
    if any(row["v1_1_risk_status"] == "FAIL" for row in published):
        raise RuntimeError("正式融合输出含FAIL")
    if len({row["ts_code"] for row in published}) != len(published) or len({row["fusion_rank"] for row in published}) != len(published):
        raise RuntimeError("正式融合代码或排名不唯一")

    original_formal = [str(row.get("ts_code")) for row in formal]
    rec_codes = [str(row["ts_code"]) for row in recommendations]
    alt_codes = [str(row["ts_code"]) for row in alternates]
    fields = ["fusion_rank", "recommendation_type", "trade_date", "ts_code", "name", "is_focus", "industry_sector", "source_layer", "v1_0_raw_score", "v1_0_normalized_score", "v1_0_original_rank", "v1_1_risk_status", "v1_1_risk_reasons", "sector_wind_score", "sector_wind_rank", "stock_wind_score", "relative_sector_strength_score", "volume_price_confirmation_score", "leader_persistence_score", "trend_quality_score", "v1_2_wind_score", "v1_2_wind_market_rank", "final_score", "concept_bonus_applied"]
    source_hashes = {name: sha256_file(path) for name, path in paths.items()}
    source_hashes["light_daily_first"] = sha256_file(light_paths[0])
    source_hashes["light_daily_last"] = sha256_file(light_paths[-1])
    if deep_paths:
        source_hashes["deep_daily_first"] = sha256_file(deep_paths[0])
        source_hashes["deep_daily_last"] = sha256_file(deep_paths[-1])
    dated_json = data_dir / f"key10_v12_fusion_{trade_date}.json"
    dated_audit = data_dir / f"key10_v12_fusion_audit_{trade_date}.json"
    if dated_json.is_file() and not dated_json.is_symlink():
        existing = json.loads(dated_json.read_text(encoding="utf-8"))
        if (
            existing.get("schema_version") == config.get("schema_version")
            and existing.get("layer_status") == "formal_fusion"
            and existing.get("trade_date") == trade_date
            and existing.get("source_file_sha256") == source_hashes
            and int(existing.get("future_data_rows", -1)) == 0
        ):
            existing_rec = existing.get("recommendations", [])
            existing_alt = existing.get("alternates", [])
            if not isinstance(existing_rec, list) or not isinstance(existing_alt, list):
                raise RuntimeError("已有融合归档候选结构异常")
            if len(existing_rec) + len(existing_alt) > formal_output_max:
                raise RuntimeError("已有融合归档超过正式输出上限")
            if not dated_audit.is_file() or dated_audit.is_symlink():
                raise RuntimeError("已有融合归档缺少独立audit记录")
            payload_json = dated_json.read_bytes()
            payload_audit = dated_audit.read_bytes()
            payload_rec = csv_bytes(existing_rec, fields)
            payload_alt = csv_bytes(existing_alt, fields)
            payload_png = png_table(existing_rec + existing_alt, trade_date)
            payloads = {
                output / "key10_v12_fusion_latest.json": payload_json,
                dated_json: payload_json,
                output / "key10_v12_fusion_audit_latest.json": payload_audit,
                dated_audit: payload_audit,
                output / "key10_fusion_recommendations_latest.csv": payload_rec,
                data_dir / f"key10_fusion_recommendations_{trade_date}.csv": payload_rec,
                output / "key10_fusion_alternates_latest.csv": payload_alt,
                data_dir / f"key10_fusion_alternates_{trade_date}.csv": payload_alt,
                output / "key10_fusion_candidates_latest.png": payload_png,
                data_dir / f"key10_fusion_candidates_{trade_date}.png": payload_png,
            }
            atomic_outputs(payloads)
            print(json.dumps({"status": "success_idempotent", "trade_date": trade_date, "recommendations": len(existing_rec), "alternates": len(existing_alt), "output_fail_count": 0, "future_data_rows": 0, "sha256": {str(path): hashlib.sha256(payload).hexdigest() for path, payload in payloads.items()}}, ensure_ascii=False, indent=2))
            return 0
    result = clean({
        "schema_version": config["schema_version"],
        "layer_status": "formal_fusion",
        "generated_at": now_bj().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "observation_pool_count": len(observation),
        "focus_pool_count": len(focus),
        "source_file_sha256": source_hashes,
        "formula": {"sector_wind": config["sector_wind"], "stock_wind": config["stock_wind"], "wind_fusion": config["wind_fusion"], "formal_fusion": config["formal_fusion"]},
        "top_v1_2_sectors": sector_wind.loc[sector_wind["industry"].isin(top_sectors)].to_dict(orient="records"),
        "candidate_source_counts": {"v1_0_formal": len(formal), "v1_0_near": len(near), "v1_2_expansion_scored": len(expansion_scored)},
        "formal_output_count": len(published),
        "formal_output_max": formal_output_max,
        "recommendations": recommendations,
        "alternates": alternates,
        "diagnostics": {
            "eligible_before_limit": len(fused),
            "recommendation_count": len(recommendations),
            "alternate_count": len(alternates),
            "audit_only_count": len(audit_only),
            "output_fail_count": sum(row["v1_1_risk_status"] == "FAIL" for row in published),
            "excluded_fail_count": sum(row["v1_1_risk_status"] == "FAIL" for row in excluded),
            "excluded_below_60_count": sum(row["exclusion_reason"] == "final_score低于60" for row in excluded),
            "rank_unique": len({row["fusion_rank"] for row in published}) == len(published),
            "ts_code_unique": len({row["ts_code"] for row in published}) == len(published),
            "future_data_rows": int(history_light["trade_date"].gt(trade_date).sum()),
            "three_layers_traceable": all("v1_0_attribution" in row and "v1_1_risk_status" in row and "v1_2_wind_score" in row for row in published),
            "concept_bonus_applied_count": sum(bool(row["concept_bonus_applied"]) for row in published),
            "completion_or_maturity_used": False,
        },
        "changes_vs_v1_0_formal": {
            "added_to_recommendations": [code for code in rec_codes if code not in original_formal],
            "removed_from_recommendations": [code for code in original_formal if code not in rec_codes],
            "upgraded_from_near_or_expansion": [code for code in rec_codes if code not in original_formal],
            "downgraded_to_alternate": [code for code in original_formal if code in alt_codes],
            "excluded": [{"ts_code": row["ts_code"], "reason": row["exclusion_reason"], "v1_1_risk_status": row["v1_1_risk_status"], "final_score": row["final_score"]} for row in excluded],
        },
        "market_context": {
            "market_state": market.get("market_state"),
            "market_score": market.get("market_score_3d_smoothed"),
            "market_bottom_status": market.get("market_bottom_status"),
            "market_repair_status": market.get("market_repair_status"),
            "turnover_display_definition": "沪深全部A股成交额，含ST，排除北交所",
        },
        "future_data_rows": int(history_light["trade_date"].gt(trade_date).sum()),
        "pool_changed": False,
        "v1_0_formula_changed": False,
        "hard_risk_can_be_offset": False,
        "performance": {
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 3),
            "peak_rss_increase_mb": round(max(0, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - peak_start) / 1024, 3),
        },
    })
    if result["future_data_rows"] != 0:
        raise RuntimeError("future_data_rows非零，拒绝输出")

    payload_json = json_bytes(result)
    audit_result = clean({
        "schema_version": "1.2-fusion-audit-1",
        "layer_status": "audit_only",
        "generated_at": result["generated_at"],
        "trade_date": trade_date,
        "formal_output_count": len(published),
        "formal_output_max": formal_output_max,
        "audit_only_count": len(audit_only),
        "candidates": audit_only,
        "source_file_sha256": source_hashes,
        "future_data_rows": result["future_data_rows"],
    })
    payload_audit = json_bytes(audit_result)
    payload_rec = csv_bytes(recommendations, fields)
    payload_alt = csv_bytes(alternates, fields)
    payload_png = png_table(published, trade_date)
    payloads = {
        output / "key10_v12_fusion_latest.json": payload_json,
        data_dir / f"key10_v12_fusion_{trade_date}.json": payload_json,
        output / "key10_v12_fusion_audit_latest.json": payload_audit,
        data_dir / f"key10_v12_fusion_audit_{trade_date}.json": payload_audit,
        output / "key10_fusion_recommendations_latest.csv": payload_rec,
        data_dir / f"key10_fusion_recommendations_{trade_date}.csv": payload_rec,
        output / "key10_fusion_alternates_latest.csv": payload_alt,
        data_dir / f"key10_fusion_alternates_{trade_date}.csv": payload_alt,
        output / "key10_fusion_candidates_latest.png": payload_png,
        data_dir / f"key10_fusion_candidates_{trade_date}.png": payload_png,
    }
    atomic_outputs(payloads)
    print(json.dumps({"status": "success", "trade_date": trade_date, "recommendations": len(recommendations), "alternates": len(alternates), "output_fail_count": 0, "future_data_rows": 0, "elapsed_seconds": result["performance"]["elapsed_seconds"], "peak_rss_mb": result["performance"]["peak_rss_mb"], "sha256": {str(path): hashlib.sha256(payload).hexdigest() for path, payload in payloads.items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
