from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd


SCANNER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCANNER))

from stock_key10_v2_finalize import finalize
from stock_key10_v2_scoring import available_weighted_score, build_sector_scores, build_stock_features, score_stocks, strong_sectors
from render_key10_v2 import render


def config() -> dict[str, object]:
    return json.loads((SCANNER/"config/strategy_v2.json").read_text(encoding="utf-8"))


def history(codes: list[str], dates: pd.DatetimeIndex) -> pd.DataFrame:
    rows = []
    for code_index, code in enumerate(codes):
        for day_index, date in enumerate(dates):
            close = 10 + code_index*0.4 + day_index*0.025*(1+code_index/20)
            prior = close-0.025*(1+code_index/20)
            rows.append({
                "ts_code": code, "trade_date": date.strftime("%Y%m%d"),
                "open": close*0.995, "high": close*1.02, "low": close*0.985,
                "close": close, "pre_close": prior, "pct_chg": (close/prior-1)*100,
                "vol": 100000+day_index*2000+code_index*5000,
                "amount": (100000+day_index*2000+code_index*5000)*close/10,
            })
    return pd.DataFrame(rows)


def test_available_weight_rescale_and_70_percent_floor():
    score, completeness = available_weighted_score([(1, 7), (None, 3)], 10, 0.7)
    assert score == 10
    assert completeness == 0.7
    score, completeness = available_weighted_score([(1, 6.9), (None, 3.1)], 10, 0.7)
    assert score is None
    assert abs(completeness-0.69) < 1e-12


def test_sector_has_no_minimum_sample_gate_and_stock_score_bounds():
    codes = [f"{600000+i:06d}.SH" for i in range(12)]
    dates = pd.bdate_range("2025-01-02", periods=70)
    raw = history(codes, dates)
    light = raw.copy()
    light.sort_values(["ts_code", "trade_date"], inplace=True)
    grouped = light.groupby("ts_code", group_keys=False)
    for window in (5, 10, 20):
        light[f"ma{window}"] = grouped["close"].transform(lambda x, w=window: x.rolling(w, min_periods=w).mean())
    for days in (3, 5, 20):
        light[f"return_{days}d_pct"] = (light["close"]/grouped["close"].shift(days)-1)*100
    light["amount_ma20"] = grouped["amount"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    light["volume_ratio_1d_20d"] = light["vol"]/grouped["vol"].transform(lambda x: x.rolling(20, min_periods=20).mean())
    base = pd.DataFrame({
        "ts_code": codes,
        "industry": ["单股板块"]+["多股板块"]*11,
        "name": [f"测试{i}" for i in range(12)],
        "segment": ["沪市主板"]*12,
    })
    target, previous = dates[-1].strftime("%Y%m%d"), dates[-2].strftime("%Y%m%d")
    sectors = build_sector_scores(light, base, previous, target)
    assert set(sectors["industry"]) == {"单股板块", "多股板块"}
    assert int(sectors.loc[sectors["industry"].eq("单股板块"), "stock_count"].iloc[0]) == 1
    features = build_stock_features(raw, target)
    scored = score_stocks(features, base, sectors)
    assert scored["market_fit_score"].dropna().between(0, 5).all()
    assert scored["sector_score"].dropna().between(0, 15).all()
    assert scored["technical_score"].dropna().between(0, 30).all()
    assert scored["quant_score"].dropna().between(0, 50).all()


def candidate(code: str, origin: str, sector: str, quant: float = 45) -> dict[str, object]:
    return {
        "ts_code": code, "origin": origin, "name": code[:6], "segment": "沪市主板", "sector": sector,
        "market_fit_score": 5, "sector_score": 12, "technical_score": quant-17, "quant_score": quant,
    }


def review(code: str, origin: str, risk: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "ts_code": code, "origin": origin,
        "logic_purity": {"score": 12, "reason": "产业受益明确"},
        "elasticity": {"score": 15, "factors": {
            "rebound_efficiency": 3, "thematic_elasticity": 3, "capital_support": 3,
            "expectation_gap": 3, "chip_pushing_efficiency": 3,
        }, "reason": "五项弹性均衡"},
        "fundamentals": {"score": 8, "reason": "经营稳定"},
        "major_positive": {"score": 2, "reason": "已有公开催化"},
        "risk_annotations": risk or [], "selection_reason": "综合动力较强", "main_risk": "波动",
    }


def quant_payload() -> dict[str, object]:
    return {
        "schema_version": "2.0.0", "model_version": "V2.0", "trade_date": "20260828",
        "official_review_candidates": [candidate("600001.SH", "official_pool", "算力")],
        "external_review_candidates": [
            candidate("600002.SH", "external_pool", "算力"),
            candidate("600003.SH", "external_pool", "机器人"),
        ],
        "market_environment": {"phase": "修复", "posture": "进攻", "advance_count": 3100, "decline_count": 1900, "above_ma20_ratio_pct": 56.2},
        "sector_summary": {}, "validation": {"future_data_rows_used": 0},
    }


def test_risk_only_marks_and_external_activation():
    quant = quant_payload()
    reviews = [
        review("600001.SH", "official_pool"),
        review("600002.SH", "external_pool"),
        review("600003.SH", "external_pool"),
    ]
    base = finalize(quant, {"trade_date": "20260828", "review_as_of": "20260828", "future_information_used": False, "candidates": reviews}, config())
    changed = [dict(item) for item in reviews]
    changed[0]["risk_annotations"] = [{"type": "减持", "description": "股东拟减持", "severity": "major"}]
    with_risk = finalize(quant, {"trade_date": "20260828", "review_as_of": "20260828", "future_information_used": False, "candidates": changed}, config())
    assert [(x["ts_code"], x["final_score"]) for x in base["official_final"]] == [(x["ts_code"], x["final_score"]) for x in with_risk["official_final"]]
    assert [x["ts_code"] for x in base["external_final"]] == ["600003.SH"]
    assert base["validation"]["risk_changes_score_or_rank"] is False


def test_single_png_contains_both_sections(tmp_path: Path, monkeypatch):
    result = finalize(quant_payload(), {"trade_date": "20260828", "review_as_of": "20260828", "future_information_used": False, "candidates": [
        review("600001.SH", "official_pool"), review("600002.SH", "external_pool"), review("600003.SH", "external_pool"),
    ]}, config())
    source, output = tmp_path/"final.json", tmp_path/"final.png"
    source.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    font_root = Path(__file__).resolve().parents[4]/"fonts"
    monkeypatch.setenv("STOCK_V2_FONT", os.environ.get("STOCK_V2_FONT", str(font_root/"NotoSansSC-Regular.ttf")))
    if os.environ.get("STOCK_V2_FONT_BOLD"):
        monkeypatch.setenv("STOCK_V2_FONT_BOLD", os.environ["STOCK_V2_FONT_BOLD"])
    render(source, output)
    from PIL import Image
    with Image.open(output) as image:
        assert image.size == (1024, 2048)
