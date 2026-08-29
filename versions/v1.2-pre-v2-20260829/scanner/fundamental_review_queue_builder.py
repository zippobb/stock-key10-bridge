#!/usr/bin/env python3
"""Build a deterministic GPT fundamental-review queue without doing any review."""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import time
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


DEFAULT_TECHNICAL = "output/technical_candidates_latest.csv"
DEFAULT_MARKET = "output/market_environment_latest.json"
DEFAULT_CONFIG = "strategy_v1.json"
DEFAULT_OUTPUT = "output/fundamental_review_queue_latest.csv"
DEFAULT_SNAPSHOT_DIR = "data/review"
DEFAULT_MARKDOWN = "output/fundamental_review_queue_latest.md"
DEFAULT_DIAGNOSTICS = "output/fundamental_review_queue_diagnostics_latest.json"

FAMILY_ORDER = ["horizontal", "bottom_right_side", "contraction"]
SIGNAL_FAMILIES = {
    "horizontal_breakout_strong": "horizontal",
    "horizontal_breakout_watch": "horizontal",
    "bottom_right_side": "bottom_right_side",
    "bottom_right_side_leader_relaxed": "bottom_right_side",
    "right_side_contraction_strong": "contraction",
    "right_side_contraction_watch": "contraction",
}

FUNDAMENTAL_FIELDS = [
    "fundamental_rating",
    "industry_position",
    "fundamental_advantage",
    "fundamental_disadvantage",
    "latest_material_risk",
]

OUTPUT_COLUMNS = [
    "review_rank",
    "trade_date",
    "ts_code",
    "name",
    "board",
    "industry",
    "signal_family",
    "primary_signal",
    "matched_signals",
    "market_state",
    "market_score",
    "market_bottom_status",
    "sector_rank",
    "sector_rank_percentile",
    "original_total_score",
    "risk_adjusted_score",
    "risk_adjusted_grade",
    "risk_action",
    "overheat",
    "wave_risk_label",
    "technical_reason",
    "risk_reason",
    "manual_review_note",
    *FUNDAMENTAL_FIELDS,
    "fundamental_review_status",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成基本面复核优先队列")
    parser.add_argument("--technical", default=DEFAULT_TECHNICAL)
    parser.add_argument("--market", default=DEFAULT_MARKET)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--snapshot-dir", default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--markdown", default=DEFAULT_MARKDOWN)
    parser.add_argument("--diagnostics", default=DEFAULT_DIAGNOSTICS)
    return parser.parse_args()


def require_columns(frame: pd.DataFrame, columns: list[str], label: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{label}缺少字段: {missing}")


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def csv_bytes(frame: pd.DataFrame) -> bytes:
    buffer = io.StringIO(newline="")
    frame.to_csv(buffer, index=False, lineterminator="\n")
    return buffer.getvalue().encode("utf-8-sig")


def markdown_cell(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def score_distribution(series: pd.Series) -> dict[str, float | None]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if values.empty:
        return {"min": None, "median": None, "max": None}
    return {
        "min": round(float(values.min()), 2),
        "median": round(float(values.median()), 2),
        "max": round(float(values.max()), 2),
    }


def base_sort(frame: pd.DataFrame, prioritize_pass: bool) -> pd.DataFrame:
    ordered = frame.copy()
    ordered["_pass_priority"] = (
        ordered["risk_action"].eq("PASS").astype(int) if prioritize_pass else 0
    )
    ordered.sort_values(
        [
            "risk_adjusted_score",
            "_pass_priority",
            "sector_rank_percentile",
            "return_5d_industry_percentile",
            "ts_code",
        ],
        ascending=[False, False, False, False, True],
        na_position="last",
        kind="stable",
        inplace=True,
    )
    return ordered


def build_markdown(queue: pd.DataFrame, market: dict[str, object]) -> str:
    family_counts = {
        family: int(queue["signal_family"].eq(family).sum()) for family in FAMILY_ORDER
    }
    lines = [
        "# 基本面复核队列",
        "",
        f"- 交易日期：{queue['trade_date'].iloc[0] if not queue.empty else market.get('trade_date')}",
        f"- 市场状态：{market.get('market_state')}",
        f"- 市场评分：{market.get('market_score')}",
        f"- 市场底部状态：{market.get('market_bottom_status')}",
        f"- 入选数量：{len(queue)}",
        (
            "- 信号数量："
            f"horizontal {family_counts['horizontal']}，"
            f"bottom_right_side {family_counts['bottom_right_side']}，"
            f"contraction {family_counts['contraction']}"
        ),
        "",
        "| 排名 | 代码 | 名称 | 行业 | 主信号 | 风险调整分 | 风险等级 | 风险动作 | 技术入选理由 | 风险说明 |",
        "|---:|---|---|---|---|---:|---|---|---|---|",
    ]
    for _, row in queue.iterrows():
        values = [
            row["review_rank"],
            row["ts_code"],
            row["name"],
            row["industry"],
            row["primary_signal"],
            f"{float(row['risk_adjusted_score']):.2f}",
            row["risk_adjusted_grade"],
            row["risk_action"],
            row["technical_reason"],
            row["risk_reason"],
        ]
        lines.append("| " + " | ".join(markdown_cell(value) for value in values) + " |")
    lines.extend(
        [
            "",
            "请对以上股票进行基本面辅助复核。每只股票给出：",
            "- 基本面评分：+2、+1、0、-1或-2",
            "- 行业地位",
            "- 一句话主要优势",
            "- 一句话主要劣势",
            "- 最新重大风险",
            "基本面只作为辅助加减分，不作硬性过滤。最终仍由用户人工确认。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    started = time.perf_counter()
    args = parse_args()
    technical_path = Path(args.technical)
    market_path = Path(args.market)
    config_path = Path(args.config)
    for label, path in [
        ("技术候选", technical_path),
        ("市场环境", market_path),
        ("策略配置", config_path),
    ]:
        if not path.is_file():
            raise FileNotFoundError(f"{label}不存在: {path}")

    technical = pd.read_csv(
        technical_path, dtype={"ts_code": "string", "trade_date": "string"}
    )
    market = json.loads(market_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    queue_config = config.get("review_queue")
    expected_config = {
        "target_size": 20,
        "per_signal_seed": 5,
        "max_per_industry": 4,
        "exclude_risk_adjusted_grades": ["watch_risk"],
        "prioritize_pass": True,
    }
    if queue_config != expected_config:
        raise ValueError(f"review_queue配置与STEP 5F不一致: {queue_config}")

    required = [
        "trade_date", "ts_code", "name", "board", "industry", "primary_signal",
        "matched_signals", "market_state", "market_score", "market_bottom_status",
        "sector_rank", "sector_rank_percentile", "total_score", "risk_adjusted_score",
        "risk_adjusted_grade", "risk_action", "overheat", "wave_risk_label",
        "technical_reason", "risk_reason", "manual_review_note",
        "return_5d_industry_percentile",
    ]
    require_columns(technical, required, "技术候选")
    if technical["ts_code"].duplicated().any():
        raise ValueError("技术候选存在重复ts_code")
    technical_dates = set(technical["trade_date"].dropna().astype(str))
    market_date = str(market.get("trade_date"))
    if len(technical_dates) != 1 or technical_dates != {market_date}:
        raise ValueError(
            f"技术候选和市场环境交易日不一致: {technical_dates}, {market_date}"
        )
    trade_date = market_date

    actual_signals = set(technical["primary_signal"].dropna().astype(str))
    unknown_signals = sorted(actual_signals - set(SIGNAL_FAMILIES))
    missing_signal_rows = int(technical["primary_signal"].isna().sum())
    if unknown_signals or missing_signal_rows:
        raise ValueError(
            f"发现未知或缺失primary_signal: unknown={unknown_signals}, missing={missing_signal_rows}"
        )
    technical = technical.copy()
    technical["signal_family"] = technical["primary_signal"].map(SIGNAL_FAMILIES)
    for column in [
        "risk_adjusted_score", "sector_rank_percentile",
        "return_5d_industry_percentile", "total_score",
    ]:
        technical[column] = pd.to_numeric(technical[column], errors="coerce")

    excluded_grades = set(queue_config["exclude_risk_adjusted_grades"])
    excluded_mask = technical["risk_adjusted_grade"].isin(excluded_grades)
    eligible = technical.loc[~excluded_mask & ~technical["risk_action"].eq("VETO")].copy()
    ordered = base_sort(eligible, bool(queue_config["prioritize_pass"]))
    target_size = int(queue_config["target_size"])
    per_signal_seed = int(queue_config["per_signal_seed"])
    max_per_industry = int(queue_config["max_per_industry"])

    selected_codes: set[str] = set()
    selected_rows: list[pd.Series] = []
    industry_counts: Counter[str] = Counter()
    selection_phase: dict[str, str] = {}
    first_round_counts: Counter[str] = Counter()

    def add_row(row: pd.Series, phase: str) -> None:
        code = str(row["ts_code"])
        industry = str(row["industry"])
        selected_codes.add(code)
        selected_rows.append(row)
        industry_counts[industry] += 1
        selection_phase[code] = phase

    for family in FAMILY_ORDER:
        family_pool = ordered.loc[ordered["signal_family"].eq(family)]
        for _, row in family_pool.iterrows():
            if first_round_counts[family] >= per_signal_seed:
                break
            code = str(row["ts_code"])
            industry = str(row["industry"])
            if code in selected_codes or industry_counts[industry] >= max_per_industry:
                continue
            add_row(row, f"seed_{family}")
            first_round_counts[family] += 1

    for _, row in ordered.iterrows():
        if len(selected_rows) >= target_size:
            break
        code = str(row["ts_code"])
        industry = str(row["industry"])
        if code in selected_codes or industry_counts[industry] >= max_per_industry:
            continue
        add_row(row, "fill_with_industry_cap")

    relaxed_stocks: list[dict[str, object]] = []
    if len(selected_rows) < target_size:
        for _, row in ordered.iterrows():
            if len(selected_rows) >= target_size:
                break
            code = str(row["ts_code"])
            if code in selected_codes:
                continue
            industry = str(row["industry"])
            if industry_counts[industry] >= max_per_industry:
                relaxed_stocks.append(
                    {"ts_code": code, "name": str(row["name"]), "industry": industry}
                )
            add_row(row, "fill_relaxed_industry_cap")

    if selected_rows:
        selected = pd.DataFrame(selected_rows).drop(columns=["_pass_priority"], errors="ignore")
        selected = base_sort(selected, bool(queue_config["prioritize_pass"]))
        selected.drop(columns=["_pass_priority"], inplace=True)
    else:
        selected = eligible.head(0).copy()
    selected.reset_index(drop=True, inplace=True)
    selected["review_rank"] = np.arange(1, len(selected) + 1)
    selected["original_total_score"] = selected["total_score"]
    for field in FUNDAMENTAL_FIELDS:
        selected[field] = ""
    selected["fundamental_review_status"] = "pending_gpt_review"
    queue = selected[OUTPUT_COLUMNS].copy()

    duplicate_count = int(queue["ts_code"].duplicated().sum())
    all_from_technical = set(queue["ts_code"]).issubset(set(technical["ts_code"]))
    veto_count = int(queue["risk_action"].eq("VETO").sum())
    watch_risk_count = int(queue["risk_adjusted_grade"].eq("watch_risk").sum())
    if duplicate_count or not all_from_technical or veto_count or watch_risk_count:
        raise RuntimeError("队列基本约束校验失败")
    if len(queue) == target_size:
        if sorted(queue["review_rank"].tolist()) != list(range(1, target_size + 1)):
            raise RuntimeError("review_rank不连续")
    if not all(queue[field].eq("").all() for field in FUNDAMENTAL_FIELDS):
        raise RuntimeError("基本面字段未保持空白")
    if not queue["fundamental_review_status"].eq("pending_gpt_review").all():
        raise RuntimeError("复核状态错误")

    family_counts = {
        family: int(queue["signal_family"].eq(family).sum()) for family in FAMILY_ORDER
    }
    action_counts = {
        action: int(queue["risk_action"].eq(action).sum())
        for action in ["PASS", "DOWNGRADE"]
    }
    grade_counts = {
        grade: int(queue["risk_adjusted_grade"].eq(grade).sum())
        for grade in ["strong", "normal", "watch"]
    }
    industry_result = {
        str(key): int(value)
        for key, value in queue["industry"].value_counts().sort_index().items()
    }
    seed_shortfalls = {
        family: max(0, per_signal_seed - int(first_round_counts[family]))
        for family in FAMILY_ORDER
    }
    diagnostics = {
        "trade_date": trade_date,
        "technical_candidate_count": int(len(technical)),
        "excluded_watch_risk_count": int(excluded_mask.sum()),
        "eligible_candidate_count": int(len(eligible)),
        "target_queue_size": target_size,
        "final_queue_size": int(len(queue)),
        "signal_family_counts": family_counts,
        "first_round_seed_counts": {
            family: int(first_round_counts[family]) for family in FAMILY_ORDER
        },
        "first_round_seed_shortfalls": seed_shortfalls,
        "pass_downgrade_counts": action_counts,
        "risk_adjusted_grade_counts": grade_counts,
        "industry_counts": industry_result,
        "industry_limit": max_per_industry,
        "industry_limit_relaxed": bool(relaxed_stocks),
        "industry_relaxation_count": int(len(relaxed_stocks)),
        "industry_relaxation_industries": sorted(
            {str(item["industry"]) for item in relaxed_stocks}
        ),
        "industry_relaxation_stocks": relaxed_stocks,
        "risk_adjusted_score_distribution": score_distribution(
            queue["risk_adjusted_score"]
        ),
        "duplicate_ts_code_count": duplicate_count,
        "all_from_technical_candidates": all_from_technical,
        "unexpected_veto_count": veto_count,
        "unexpected_watch_risk_count": watch_risk_count,
        "unknown_signals": unknown_signals,
        "missing_primary_signal_count": missing_signal_rows,
        "selection_phases": {
            code: selection_phase[code] for code in queue["ts_code"].astype(str)
        },
        "queue_order": queue[
            ["review_rank", "ts_code", "name", "signal_family"]
        ].to_dict(orient="records"),
        "full_technical_pool_modified": False,
        "queue_does_not_invalidate_unselected_candidates": True,
        "fundamental_analysis_performed": False,
        "network_search_performed": False,
        "elapsed_seconds_before_write": round(time.perf_counter() - started, 3),
        "warnings": (
            []
            if len(queue) == target_size
            else [f"队列仅生成{len(queue)}只，少于目标{target_size}只。"]
        ),
    }

    output_path = Path(args.output)
    snapshot_path = Path(args.snapshot_dir) / f"fundamental_review_queue_{trade_date}.csv"
    payload = csv_bytes(queue)
    atomic_write(snapshot_path, payload)
    atomic_write(output_path, payload)
    markdown = build_markdown(queue, market)
    atomic_write(Path(args.markdown), markdown.encode("utf-8"))
    diagnostic_payload = (
        json.dumps(diagnostics, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    atomic_write(Path(args.diagnostics), diagnostic_payload)

    print(json.dumps(diagnostics, ensure_ascii=False, indent=2, allow_nan=False))
    print("\nFINAL_QUEUE")
    print(
        queue[
            [
                "review_rank", "ts_code", "name", "industry", "signal_family",
                "primary_signal", "risk_adjusted_score", "risk_adjusted_grade",
                "risk_action",
            ]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()
