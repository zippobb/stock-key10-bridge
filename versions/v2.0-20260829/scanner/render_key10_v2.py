#!/usr/bin/env python3
"""Render one fixed 1024x2048 deep-navy V2.0 candidate image."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from stock_key10_v2_common import load_json


WIDTH, HEIGHT = 1024, 2048
BACKGROUND = "#071525"
PANEL = "#0B2037"
PANEL_ALT = "#0D2843"
GRID = "#27445E"
TEXT = "#E8F1F8"
MUTED = "#91A9BC"
CYAN = "#58D6E7"
GOLD = "#F2C66D"
RED = "#F08787"


def font_path() -> str:
    candidates = [
        os.environ.get("STOCK_V2_FONT", ""),
        "/opt/stock-automation/scanner/config/NotoSansSC-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise FileNotFoundError("未找到可用字体；请设置STOCK_V2_FONT")


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    regular = font_path()
    if bold:
        bold_candidates = [
            os.environ.get("STOCK_V2_FONT_BOLD", ""),
            "/opt/stock-automation/scanner/config/NotoSansSC-Bold.ttf",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        ]
        for candidate in bold_candidates:
            if candidate and Path(candidate).is_file():
                return ImageFont.truetype(candidate, size=size)
    return ImageFont.truetype(regular, size=size)


def truncate(draw: ImageDraw.ImageDraw, text: object, font: ImageFont.FreeTypeFont, width: int) -> str:
    value = str(text or "").replace("\n", " ").strip()
    if draw.textlength(value, font=font) <= width:
        return value
    suffix = "…"
    while value and draw.textlength(value+suffix, font=font) > width:
        value = value[:-1]
    return value+suffix


def score(value: object) -> str:
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "--"


def risk_text(item: dict[str, object]) -> str:
    annotations = item.get("risk_annotations") or []
    if not annotations:
        return "重大风险：未发现"
    parts: list[str] = []
    for risk in annotations:
        if isinstance(risk, dict):
            parts.append(str(risk.get("description") or risk.get("type") or "风险已标记"))
        else:
            parts.append(str(risk))
    return "重大风险：" + "；".join(parts)


def draw_header(draw: ImageDraw.ImageDraw, data: dict[str, object]) -> int:
    title = get_font(42, True)
    subtitle = get_font(22)
    small = get_font(18)
    draw.text((42, 38), f"KEY10 V2.0  ·  {data.get('trade_date', '')}", font=title, fill=TEXT)
    draw.text((44, 100), "自上而下综合终审  |  综合分 ≥ 60  |  风险仅标记", font=subtitle, fill=CYAN)
    market = data.get("market_environment") or {}
    market_line = (
        f"市场：{market.get('phase', '--')} / {market.get('posture', '--')}  "
        f"上涨 {market.get('advance_count', '--')}  下跌 {market.get('decline_count', '--')}  "
        f"MA20上方 {market.get('above_ma20_ratio_pct', '--')}%"
    )
    draw.text((44, 139), market_line, font=small, fill=MUTED)
    draw.line((42, 178, 982, 178), fill=GRID, width=2)
    return 194


def draw_section(
    draw: ImageDraw.ImageDraw,
    y: int,
    title: str,
    items: list[dict[str, object]],
    limit: int,
    accent: str,
) -> int:
    section_font = get_font(28, True)
    header_font = get_font(17, True)
    body = get_font(18)
    body_small = get_font(16)
    draw.rounded_rectangle((34, y, 990, y+46), radius=8, fill=PANEL_ALT, outline=GRID, width=1)
    draw.text((50, y+8), f"{title}  {len(items)}/{limit}", font=section_font, fill=accent)
    y += 58
    if not items:
        draw.rounded_rectangle((34, y, 990, y+72), radius=8, fill=PANEL, outline=GRID, width=1)
        draw.text((54, y+23), "本次没有达到正式标准的个股", font=body, fill=MUTED)
        return y+88
    for position, item in enumerate(items):
        height = 112
        fill = PANEL if position % 2 == 0 else PANEL_ALT
        draw.rounded_rectangle((34, y, 990, y+height), radius=7, fill=fill, outline=GRID, width=1)
        rank = item.get("final_rank", position+1)
        name = truncate(draw, item.get("name", ""), body, 180)
        sector = truncate(draw, item.get("sector", ""), body_small, 165)
        draw.text((50, y+12), f"{rank:02d}", font=section_font, fill=accent)
        draw.text((102, y+13), f"{item.get('ts_code', '')}  {name}", font=body, fill=TEXT)
        draw.text((486, y+13), sector, font=body_small, fill=MUTED)
        draw.text((790, y+8), score(item.get("final_score")), font=get_font(31, True), fill=GOLD)
        draw.text((50, y+40), (
            f"M {score(item.get('market_fit_score'))}/5  S {score(item.get('sector_score'))}/15  "
            f"T {score(item.get('technical_score'))}/30  L {score(item.get('logic_purity_score'))}/15  "
            f"E {score(item.get('elasticity_score'))}/20  F {score(item.get('fundamental_score'))}/10  "
            f"P {score(item.get('major_positive_score'))}/5"
        ), font=body_small, fill=CYAN)
        reason = item.get("selection_reason") or item.get("logic_purity_reason") or "终审依据见数据包"
        draw.text((50, y+65), truncate(draw, f"入选：{reason}", body_small, 910), font=body_small, fill=TEXT)
        risk = truncate(draw, risk_text(item), body_small, 910)
        draw.text((50, y+88), risk, font=body_small, fill=RED if item.get("risk_annotations") else MUTED)
        y += height+8
    return y+6


def render(input_path: Path | str, output_path: Path | str) -> None:
    data = load_json(Path(input_path))
    if data.get("model_version") != "V2.0":
        raise ValueError("制图输入不是V2.0正式结果")
    official = list(data.get("official_final", []))
    external = list(data.get("external_final", []))
    if len(official) > 10 or len(external) > 3:
        raise ValueError("制图输入候选数量超过已确认上限")
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    y = draw_header(draw, data)
    y = draw_section(draw, y, "池内综合最优", official, 10, CYAN)
    y = draw_section(draw, y, "池外强势板块补充", external, 3, GOLD)
    footer_font = get_font(15)
    footer = "买点属于执行纪律，不计入综合分；池外个股仅由用户人工决定是否加入正式股票池。"
    draw.text((42, min(2018, max(y+4, 1986))), truncate(draw, footer, footer_font, 940), font=footer_font, fill=MUTED)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.png")
    image.save(temporary, format="PNG", optimize=True)
    os.replace(temporary, output)
    with Image.open(output) as check:
        if check.size != (WIDTH, HEIGHT):
            raise RuntimeError("V2.0图片尺寸验证失败")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    render(args.input, args.output)
