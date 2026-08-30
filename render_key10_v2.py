#!/usr/bin/env python3
"""Render the user-facing daily candidate image from a V2.0 final result."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from stock_key10_v2_common import load_json


WIDTH = 1024
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
        return ""
    parts: list[str] = []
    for risk in annotations:
        if isinstance(risk, dict):
            parts.append(str(risk.get("description") or risk.get("type") or "风险已标记"))
        else:
            parts.append(str(risk))
    return "重大风险：" + "；".join(parts)


def display_date(value: object) -> str:
    raw = str(value or "")
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def signed(value: object, digits: int = 2) -> str:
    try:
        number = float(value)
        return f"{number:+.{digits}f}%"
    except (TypeError, ValueError):
        return "--"


def draw_header(draw: ImageDraw.ImageDraw, data: dict[str, object]) -> int:
    title = get_font(42, True)
    draw.text((42, 28), f"每日候选 · {display_date(data.get('trade_date'))}", font=title, fill=TEXT)
    draw.line((42, 91, 982, 91), fill=GRID, width=2)
    return 108


def market_comment(market: dict[str, object]) -> str:
    phase = str(market.get("phase") or "--")
    posture = str(market.get("posture") or "--")
    try:
        advance_ratio = float(market.get("advance_ratio_pct", 0))
    except (TypeError, ValueError):
        advance_ratio = 0
    breadth = "个股涨多跌少，赚钱效应偏正" if advance_ratio >= 52 else "个股跌多涨少，赚钱效应偏弱"
    indices = list(market.get("major_indices") or [])
    if len(indices) >= 3:
        returns = [float(item.get("return_1d_pct", 0)) for item in indices[:3]]
        dispersion = "；上证相对抗跌，深市与创业板承压" if returns[0] > returns[1] and returns[0] > returns[2] else "；主要指数表现较为同步"
    else:
        dispersion = ""
    return f"市场处于{phase}阶段，盘面偏{posture}；{breadth}{dispersion}。"


def draw_market(draw: ImageDraw.ImageDraw, y: int, data: dict[str, object]) -> int:
    market = data.get("market_environment") or {}
    title = get_font(27, True)
    body = get_font(18)
    small = get_font(16)
    height = 228
    draw.rounded_rectangle((34, y, 990, y+height), radius=9, fill=PANEL, outline=GRID, width=1)
    draw.text((50, y+14), "市场概览", font=title, fill=CYAN)
    amount = market.get("market_amount_yi")
    try:
        amount_text = f"{float(amount)/10000:.2f}万亿"
    except (TypeError, ValueError):
        amount_text = "--"
    draw.text((50, y+55), (
        f"阶段  {market.get('phase', '--')}    风格  {market.get('posture', '--')}    "
        f"两市成交  {amount_text}    个股中位涨幅  {signed(market.get('market_median_return_1d_pct'))}"
    ), font=body, fill=TEXT)
    draw.text((50, y+88), (
        f"上涨 {market.get('advance_count', '--')}    下跌 {market.get('decline_count', '--')}    "
        f"平盘 {market.get('unchanged_count', '--')}    上涨占比 {score(market.get('advance_ratio_pct'))}%    "
        f"MA20上方 {score(market.get('above_ma20_ratio_pct'))}%"
    ), font=small, fill=MUTED)
    indices = list(market.get("major_indices") or [])[:3]
    for index, item in enumerate(indices):
        x = 50 + index*310
        draw.rounded_rectangle((x, y+121, x+292, y+168), radius=6, fill=PANEL_ALT, outline=GRID, width=1)
        draw.text((x+12, y+130), (
            f"{item.get('name', '--')}  {signed(item.get('return_1d_pct'))}  "
            f"5日 {signed(item.get('return_5d_pct'))}  {item.get('trend', '--')}"
        ), font=small, fill=TEXT)
    draw.text((50, y+188), truncate(draw, market_comment(market), small, 910), font=small, fill=CYAN)
    return y+height+14


def rotation_names(items: list[dict[str, object]]) -> str:
    return "、".join(str(item.get("industry") or "--") for item in items)


def draw_sector_rotation(draw: ImageDraw.ImageDraw, y: int, data: dict[str, object]) -> int:
    title = get_font(27, True)
    body = get_font(17)
    small = get_font(15)
    all_sectors = list((data.get("sector_summary") or {}).get("all_sectors") or [])
    ranked = sorted(all_sectors, key=lambda item: float(item.get("sector_rank", 9999)))
    leaders = ranked[:5]
    leader_names = {str(item.get("industry")) for item in leaders}
    accelerating = sorted(
        [item for item in all_sectors if str(item.get("industry")) not in leader_names and float(item.get("sector_rank", 9999)) <= 33],
        key=lambda item: float(item.get("acceleration_norm", 0)),
        reverse=True,
    )[:5]
    weak = sorted(all_sectors, key=lambda item: float(item.get("sector_rank", 0)), reverse=True)[:5]
    height = 246
    draw.rounded_rectangle((34, y, 990, y+height), radius=9, fill=PANEL, outline=GRID, width=1)
    draw.text((50, y+14), "板块轮动", font=title, fill=GOLD)
    for index, item in enumerate(leaders):
        x = 50 + index*184
        draw.rounded_rectangle((x, y+54, x+170, y+112), radius=6, fill=PANEL_ALT, outline=GRID, width=1)
        draw.text((x+10, y+62), truncate(draw, item.get("industry", "--"), body, 150), font=body, fill=TEXT)
        draw.text((x+10, y+88), f"当日 {signed(item.get('return_1d_mean_pct'), 1)}  5日 {signed(item.get('return_5d_mean_pct'), 1)}", font=small, fill=CYAN)
    draw.text((50, y+132), "加速方向", font=body, fill=GOLD)
    draw.text((154, y+132), rotation_names(accelerating), font=body, fill=TEXT)
    draw.text((50, y+165), "相对弱势", font=body, fill=RED)
    draw.text((154, y+165), rotation_names(weak), font=body, fill=MUTED)
    if leaders:
        lead_text = rotation_names(leaders[:4])
        comment = f"轮动观察：领涨集中在{lead_text}，资金偏向农业链与周期方向。"
        draw.text((50, y+204), truncate(draw, comment, small, 910), font=small, fill=CYAN)
    return y+height+14


def card_height(item: dict[str, object]) -> int:
    return 114 if item.get("risk_annotations") else 88


def section_height(items: list[dict[str, object]]) -> int:
    if not items:
        return 58+88
    return 58 + sum(card_height(item)+8 for item in items) + 6


def draw_section(
    draw: ImageDraw.ImageDraw,
    y: int,
    title: str,
    items: list[dict[str, object]],
    accent: str,
) -> int:
    section_font = get_font(28, True)
    body = get_font(18)
    body_small = get_font(16)
    draw.rounded_rectangle((34, y, 990, y+46), radius=8, fill=PANEL_ALT, outline=GRID, width=1)
    draw.text((50, y+8), f"{title} · {len(items)}只", font=section_font, fill=accent)
    y += 58
    if not items:
        draw.rounded_rectangle((34, y, 990, y+72), radius=8, fill=PANEL, outline=GRID, width=1)
        draw.text((54, y+23), "本次没有达到正式标准的个股", font=body, fill=MUTED)
        return y+88
    for position, item in enumerate(items):
        height = card_height(item)
        fill = PANEL if position % 2 == 0 else PANEL_ALT
        draw.rounded_rectangle((34, y, 990, y+height), radius=7, fill=fill, outline=GRID, width=1)
        rank = item.get("final_rank", position+1)
        name = truncate(draw, item.get("name", ""), body, 180)
        sector = truncate(draw, item.get("sector", ""), body_small, 165)
        draw.text((50, y+12), f"{rank:02d}", font=section_font, fill=accent)
        draw.text((102, y+13), f"{item.get('ts_code', '')}  {name}", font=body, fill=TEXT)
        draw.text((510, y+13), sector, font=body_small, fill=MUTED)
        draw.text((785, y+10), "综合", font=body_small, fill=MUTED)
        draw.text((842, y+5), score(item.get("final_score")), font=get_font(31, True), fill=GOLD)
        reason = item.get("selection_reason") or item.get("logic_purity_reason") or "终审依据见数据包"
        draw.text((50, y+49), truncate(draw, f"关注理由：{reason}", body_small, 910), font=body_small, fill=TEXT)
        risk = truncate(draw, risk_text(item), body_small, 910)
        if risk:
            draw.text((50, y+78), risk, font=body_small, fill=RED)
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
    height = (
        108 + 228+14 + 246+14
        + section_height(official) + section_height(external) + 26
    )
    image = Image.new("RGB", (WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    y = draw_header(draw, data)
    y = draw_market(draw, y, data)
    y = draw_sector_rotation(draw, y, data)
    y = draw_section(draw, y, "池内候选", official, CYAN)
    y = draw_section(draw, y, "池外补充", external, GOLD)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.png")
    image.save(temporary, format="PNG", optimize=True)
    os.replace(temporary, output)
    with Image.open(output) as check:
        if check.size != (WIDTH, height):
            raise RuntimeError("图片尺寸验证失败")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("output")
    args = parser.parse_args()
    render(args.input, args.output)
