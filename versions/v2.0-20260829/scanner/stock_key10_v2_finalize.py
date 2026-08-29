#!/usr/bin/env python3
"""Validate the 22:00 review and produce the V2.0 final ranking."""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
from pathlib import Path

from stock_key10_v2_common import forbidden_keys, load_json, now_bj, sha256, write_json_pair


ROOT = Path(os.environ.get("STOCK_AUTOMATION_ROOT", "/opt/stock-automation/scanner"))
CONFIG = ROOT / "config/strategy_v2.json"
FORBIDDEN = {
    "is_focus", "focus_rank", "risk_status", "risk_priority", "veto", "pass_warning_fail",
    "market_hard_gate", "hard_gate", "market_veto",
}
ELASTICITY_KEYS = {
    "rebound_efficiency",
    "thematic_elasticity",
    "capital_support",
    "expectation_gap",
    "chip_pushing_efficiency",
}


def numeric_score(value: object, maximum: float, label: str, allow_missing: bool = True) -> float | None:
    if value is None and allow_missing:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}必须为数值或null") from None
    if not math.isfinite(number) or number < 0 or number > maximum:
        raise ValueError(f"{label}必须在0到{maximum}之间")
    return round(number, 6)


def normalize_review(item: dict[str, object]) -> dict[str, object]:
    code = str(item.get("ts_code", ""))
    origin = str(item.get("origin", ""))
    if not code or origin not in {"official_pool", "external_pool"}:
        raise ValueError("终审条目缺少有效ts_code或origin")
    logic = item.get("logic_purity") or {}
    elasticity = item.get("elasticity") or {}
    fundamentals = item.get("fundamentals") or {}
    positive = item.get("major_positive") or {}
    factors = elasticity.get("factors") or {}
    unknown = set(factors) - ELASTICITY_KEYS
    if unknown:
        raise ValueError(f"{code}弹性存在未定义因子: {sorted(unknown)}")
    factor_scores = {
        key: numeric_score(factors.get(key), 4.0, f"{code}.elasticity.{key}")
        for key in sorted(ELASTICITY_KEYS)
    }
    elasticity_score = numeric_score(elasticity.get("score"), 20.0, f"{code}.elasticity.score")
    known_factors = [value for value in factor_scores.values() if value is not None]
    if elasticity_score is not None and len(known_factors) == 5 and abs(sum(known_factors)-elasticity_score) > 1e-6:
        raise ValueError(f"{code}弹性总分不等于五项之和")
    risks = item.get("risk_annotations") or []
    if not isinstance(risks, list):
        raise ValueError(f"{code}.risk_annotations必须为列表")
    return {
        "ts_code": code,
        "origin": origin,
        "logic_purity_score": numeric_score(logic.get("score"), 15.0, f"{code}.logic_purity.score"),
        "logic_purity_reason": str(logic.get("reason", "")),
        "elasticity_score": elasticity_score,
        "elasticity_factors": factor_scores,
        "elasticity_reason": str(elasticity.get("reason", "")),
        "fundamental_score": numeric_score(fundamentals.get("score"), 10.0, f"{code}.fundamentals.score"),
        "fundamental_reason": str(fundamentals.get("reason", "")),
        "major_positive_score": numeric_score(positive.get("score"), 5.0, f"{code}.major_positive.score"),
        "major_positive_reason": str(positive.get("reason", "")),
        "risk_annotations": risks,
        "selection_reason": str(item.get("selection_reason", "")),
        "main_risk": str(item.get("main_risk", "")),
        "evidence_as_of": str(item.get("evidence_as_of", "")),
    }


def calculate_final(candidate: dict[str, object], review: dict[str, object], minimum_completeness: float) -> dict[str, object]:
    dimensions = [
        ("market_fit_score", 5.0), ("sector_score", 15.0), ("technical_score", 30.0),
        ("logic_purity_score", 15.0), ("elasticity_score", 20.0),
        ("fundamental_score", 10.0), ("major_positive_score", 5.0),
    ]
    merged = {**candidate, **review}
    available_max = 0.0
    raw_score = 0.0
    for key, maximum in dimensions:
        value = numeric_score(merged.get(key), maximum, f"{merged['ts_code']}.{key}")
        if value is not None:
            available_max += maximum
            raw_score += value
    completeness = available_max / 100.0
    final_score = raw_score / available_max * 100.0 if available_max and completeness+1e-12 >= minimum_completeness else None
    merged.update({
        "available_weight": round(available_max, 6),
        "score_completeness": round(completeness, 6),
        "final_score": None if final_score is None else round(final_score, 6),
        "data_complete_for_formal": bool(final_score is not None),
        "risk_score_effect": 0,
        "risk_rank_effect": False,
    })
    return merged


def ranking_key(item: dict[str, object]) -> tuple[object, ...]:
    elasticity = -1.0 if item.get("elasticity_score") is None else float(item["elasticity_score"])
    logic = -1.0 if item.get("logic_purity_score") is None else float(item["logic_purity_score"])
    return (
        -float(item["final_score"]),
        -float(item["technical_score"]),
        -elasticity,
        -float(item["sector_score"]),
        -logic,
        str(item["ts_code"]),
    )


def finalize(quant: dict[str, object], review_input: dict[str, object], config: dict[str, object]) -> dict[str, object]:
    found = forbidden_keys(quant, FORBIDDEN) | forbidden_keys(review_input, FORBIDDEN)
    if found:
        raise ValueError(f"V2.0输入含禁用字段: {sorted(found)}")
    if quant.get("model_version") != "V2.0" or str(quant.get("trade_date")) != str(review_input.get("trade_date")):
        raise ValueError("量化包版本或交易日与终审输入不一致")
    if review_input.get("future_information_used") is not False:
        raise ValueError("终审输入必须明确future_information_used=false")
    review_as_of = str(review_input.get("review_as_of", "")).replace("-", "")[:8]
    if review_as_of != str(quant.get("trade_date")):
        raise ValueError("终审review_as_of必须为量化包交易日")
    quantitative = list(quant.get("official_review_candidates", [])) + list(quant.get("external_review_candidates", []))
    candidate_map = {(str(item["origin"]), str(item["ts_code"])): item for item in quantitative}
    review_list = review_input.get("candidates", [])
    if not isinstance(review_list, list):
        raise ValueError("终审输入candidates必须为列表")
    review_map: dict[tuple[str, str], dict[str, object]] = {}
    for raw in review_list:
        normalized = normalize_review(raw)
        key = (normalized["origin"], normalized["ts_code"])
        if key not in candidate_map:
            raise ValueError(f"终审加入了21:30量化包之外的股票: {key}")
        if key in review_map:
            raise ValueError(f"终审股票重复: {key}")
        review_map[key] = normalized
    rows = [
        calculate_final(item, review_map.get(key, normalize_review({"origin": key[0], "ts_code": key[1]})), float(config["quantitative"]["minimum_subscore_completeness"]))
        for key, item in candidate_map.items()
    ]
    minimum = float(config["final_review"]["minimum_final_score"])
    eligible = [item for item in rows if item["final_score"] is not None and float(item["final_score"]) >= minimum]
    official_eligible_all = sorted([item for item in eligible if item["origin"] == "official_pool"], key=ranking_key)
    official = official_eligible_all[:int(config["final_review"]["official_limit"])]
    official_sectors = {str(item["sector"]) for item in official_eligible_all}
    external = sorted(
        [item for item in eligible if item["origin"] == "external_pool" and str(item["sector"]) not in official_sectors],
        key=ranking_key,
    )[:int(config["universe"]["external"]["final_limit"])]
    for rank, item in enumerate(official, start=1):
        item["final_rank"] = rank
        item["list_type"] = "official"
    for rank, item in enumerate(external, start=1):
        item["final_rank"] = rank
        item["list_type"] = "external"
    return {
        "schema_version": "2.0.0",
        "model_version": "V2.0",
        "stage": "22:00_final",
        "trade_date": quant["trade_date"],
        "generated_at": now_bj().isoformat(timespec="seconds"),
        "official_final": official,
        "external_final": external,
        "reviewed_candidates": rows,
        "market_environment": quant.get("market_environment", {}),
        "sector_summary": quant.get("sector_summary", {}),
        "validation": {
            "minimum_final_score": minimum,
            "official_count_at_most_10": len(official) <= 10,
            "external_count_at_most_3": len(external) <= 3,
            "official_and_external_ranked_separately": True,
            "external_sector_without_official_eligible": all(str(item["sector"]) not in official_sectors for item in external),
            "risk_changes_score_or_rank": False,
            "future_data_rows_used": int(quant.get("validation", {}).get("future_data_rows_used", 0)),
        },
        "source_sha256": {"quantitative_payload": quant.get("_source_sha256"), "review_input": review_input.get("_source_sha256")},
    }


def render(final_path: Path, image_path: Path) -> None:
    renderer = ROOT / "render_key10_v2.py"
    spec = importlib.util.spec_from_file_location("render_key10_v2", renderer)
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载V2.0制图模块")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.render(final_path, image_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quant", default=str(ROOT/"output/key10_v2_review_payload_latest.json"))
    parser.add_argument("--review", required=True)
    parser.add_argument("--output", default=str(ROOT/"output/key10_v2_final_latest.json"))
    parser.add_argument("--image", default=str(ROOT/"output/key10_v2_final_latest.png"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    quant_path, review_path = Path(args.quant), Path(args.review)
    quant, review_input, config = load_json(quant_path), load_json(review_path), load_json(CONFIG)
    quant["_source_sha256"], review_input["_source_sha256"] = sha256(quant_path), sha256(review_path)
    final = finalize(quant, review_input, config)
    output = Path(args.output)
    dated = ROOT / f"data/v2/key10_v2_final_{final['trade_date']}.json"
    write_json_pair(output, dated, final)
    image = Path(args.image)
    render(output, image)
    dated_image = ROOT / f"data/v2/key10_v2_final_{final['trade_date']}.png"
    dated_image.parent.mkdir(parents=True, exist_ok=True)
    dated_image.write_bytes(image.read_bytes())
    print(f"V2.0终审完成: official={len(final['official_final'])}, external={len(final['external_final'])}, image={image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
