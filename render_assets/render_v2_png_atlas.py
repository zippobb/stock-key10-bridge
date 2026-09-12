#!/usr/bin/env python3
"""Render the fixed V2.0 daily stock-selection PNG.

This renderer is deliberately data-driven: it reads only the validated formal
review JSON and never recalculates scores or changes selection order.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont


W, H = 1024, 2048
BG = "#071525"
GRID = "#102947"
PANEL = "#0B2037"
CARD = "#0D2843"
CARD_BORDER = "#1E5B88"
TEXT = "#E8F1F8"
MUTED = "#9FBBD7"
CYAN = "#56D6FF"
TEAL = "#38C3D7"
YELLOW = "#FFD66B"
RED = "#FF818B"
GREEN = "#61E1B8"
ORANGE = "#B98B35"


LATIN_REG = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
LATIN_BOLD = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
ATLAS_PATH = Path(os.environ.get("STOCK_V2_CJK_ATLAS", Path(__file__).with_name("v2_cjk_atlas.png")))
ATLAS_INDEX_PATH = Path(os.environ.get("STOCK_V2_CJK_INDEX", Path(__file__).with_name("v2_cjk_atlas_index.json")))


class AtlasText:
    """Draw Latin text with DejaVu and Chinese glyphs from a checked atlas."""

    def __init__(self, atlas_path: Path, index_path: Path) -> None:
        if not atlas_path.is_file() or not index_path.is_file():
            raise RuntimeError("缺少持久化 CJK 字形图集；禁止回退到拉丁字体或 ReportLab。")
        self.atlas = Image.open(atlas_path).convert("RGBA")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        self.cell = int(index["cell"])
        self.positions = index["positions"]
        self.reg = ImageFont.truetype(str(LATIN_REG), 16)
        self.bold = ImageFont.truetype(str(LATIN_BOLD), 16)
        bad = []
        for ch, pos in self.positions.items():
            x, y = int(pos["x"]), int(pos["y"])
            alpha = self.atlas.crop((x, y, x + self.cell, y + self.cell)).getchannel("A")
            if alpha.getbbox() is None:
                bad.append(ch)
        if bad:
            raise RuntimeError(f"CJK 字形预检失败，空字形: {''.join(bad[:8])}")

    @staticmethod
    def is_cjk(ch: str) -> bool:
        code = ord(ch)
        return 0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF

    def has_glyph(self, ch: str) -> bool:
        return ch in self.positions

    @staticmethod
    def normalize(value: object) -> str:
        # DejaVu intentionally handles only the Latin/ASCII side of the
        # renderer. Normalize full-width punctuation before drawing so it
        # cannot become a tofu box when mixed with atlas glyphs.
        return str(value).translate(str.maketrans({
            "：": ":", "｜": "|", "、": ",", "。": ".",
            "（": "(", "）": ")", "“": '"', "”": '"',
        }))

    def _font(self, size: int, bold: bool) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(LATIN_BOLD if bold else LATIN_REG), size)

    def _mask(self, ch: str, size: int, bold: bool) -> Image.Image:
        pos = self.positions[ch]
        x, y = int(pos["x"]), int(pos["y"])
        alpha = self.atlas.crop((x, y, x + self.cell, y + self.cell)).getchannel("A")
        bbox = alpha.getbbox()
        if bbox is None:
            raise RuntimeError(f"CJK 字形为空: {ch}")
        alpha = alpha.crop(bbox)
        target = max(1, int(size * 0.88))
        alpha.thumbnail((target, target), Image.Resampling.LANCZOS)
        canvas = Image.new("L", (size, size), 0)
        left = (size - alpha.width) // 2
        top = (size - alpha.height) // 2
        canvas.paste(alpha, (left, top))
        if bold and size >= 16:
            canvas = canvas.filter(ImageFilter.MaxFilter(3))
        return canvas

    def textlength(self, value: object, size: int, bold: bool = False) -> float:
        text = self.normalize(value)
        total = 0.0
        run = ""
        font = self._font(size, bold)
        for ch in text + "\0":
            if ch != "\0" and self.is_cjk(ch):
                if ch not in self.positions:
                    raise RuntimeError(f"CJK 字形不在预检图集: {ch}")
                if run:
                    total += ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(run, font=font)
                    run = ""
                total += size
            else:
                if ch != "\0":
                    run += ch
        if run:
            total += ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(run, font=font)
        return total

    def draw_text(self, canvas: Image.Image, xy: tuple[float, float], value: object, size: int, color: str, bold: bool, anchor: str | None) -> None:
        text = self.normalize(value)
        x, y = float(xy[0]), float(xy[1])
        total = self.textlength(text, size, bold)
        if anchor == "ra":
            x -= total
        elif anchor == "mm":
            x -= total / 2
            y -= size / 2
        target = ImageDraw.Draw(canvas)
        cursor = x
        run = ""
        font = self._font(size, bold)

        def flush() -> None:
            nonlocal cursor, run
            if not run:
                return
            target.text((cursor, y), run, font=font, fill=color, stroke_width=1 if bold else 0, stroke_fill=color)
            cursor += target.textlength(run, font=font)
            run = ""

        for ch in text:
            if self.is_cjk(ch):
                if ch not in self.positions:
                    raise RuntimeError(f"CJK 字形不在预检图集: {ch}")
                flush()
                mask = self._mask(ch, size, bold)
                ink = Image.new("RGB", mask.size, color)
                canvas.paste(ink, (round(cursor), round(y)), mask)
                cursor += size
            else:
                run += ch
        flush()


ATLAS_TEXT = AtlasText(ATLAS_PATH, ATLAS_INDEX_PATH)
FONT_REG = ATLAS_PATH
FONT_BOLD = ATLAS_PATH


def F(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    # Kept as a compatibility shim for callers outside this renderer.
    return ImageFont.truetype(str(LATIN_BOLD if bold else LATIN_REG), size)


img = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)


def rounded(box: tuple[int, int, int, int], radius: int, fill: str, outline: str | None = None, width: int = 1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def put(x: int, y: int, value: object, size: int = 20, color: str = TEXT, bold: bool = False, anchor: str | None = None) -> None:
    ATLAS_TEXT.draw_text(img, (x, y), value, size, color, bold, anchor)


def width(value: object, size: int, bold: bool = False) -> float:
    return ATLAS_TEXT.textlength(value, size, bold)


def fit(value: object, max_width: int, size: int, color: str = TEXT, bold: bool = False, min_size: int = 12, x: int = 0, y: int = 0, anchor: str | None = None) -> int:
    current = size
    while current > min_size and width(value, current, bold) > max_width:
        current -= 1
    put(x, y, value, current, color, bold, anchor)
    return current


def wrap_pixels(value: object, max_width: int, size: int, bold: bool = False, max_lines: int = 2) -> list[str]:
    text = str(value)
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and width(candidate, size, bold) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current or not lines:
        lines.append(current)
    return lines[:max_lines]


def fmt_date(value: str) -> str:
    date = datetime.strptime(value[:8], "%Y%m%d")
    return f"{date.month}月{date.day}日"


def fmt_cutoff(value: str) -> str:
    if "T" in value:
        return value.split("T", 1)[0] + " " + value.split("T", 1)[1][:5]
    return value


def six_code(value: str) -> str:
    return str(value).split(".", 1)[0]


def short_risk(record: dict) -> str:
    raw = str(record.get("main_risk") or "板块持续性及次日承接待确认")
    if "偏离MA20" in raw:
        return "偏离MA20较远，防追涨"
    if "RSI偏高" in raw and "历史波动率" in raw:
        return "RSI偏高，防冲高回落"
    if "RSI偏高" in raw:
        return "RSI偏高，防冲高回落"
    if "量能未明显放大" in raw:
        return "量能未明显放大"
    if "VWAP" in raw:
        return "低于VWAP，次日确认承接"
    if "20日涨幅" in raw:
        return "20日涨幅偏大，防获利回吐"
    if "未见突出量化风险" in raw:
        return "板块持续性及次日承接待确认"
    return raw


def score_label(record: dict, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, dict) or value.get("score") is None:
        return "未核验"
    return str(value["score"])


def core_text(record: dict) -> str:
    sector = str(record.get("sector") or "")
    reason = str(record.get("selection_reason") or "")
    if "量价" in reason and "趋势" in reason:
        return f"{sector}：量价与趋势结构共振"
    return f"{sector}：量价结构与逻辑匹配"


def display_rotation(item: dict, warming: bool) -> str:
    arrow = "↑" if warming else "↓"
    sector = str(item.get("sector") or "")
    prev = item.get("previous_rank")
    curr = item.get("current_rank")
    delta = float(item.get("strength_change") or 0.0)
    ret = float(item.get("return_1d_mean_pct") or 0.0)
    vol = float(item.get("volume_expansion_ratio_pct") or 0.0)
    # Keep one compact row per sector so the two columns never overwrite each
    # other. "强度" is the adjacent-day normalized sector-strength change.
    return f"{arrow} {sector}  {prev}→{curr}  强{delta:+.3f}  日{ret:+.1f}% 量{vol:+.0f}%"


def render_official(record: dict, x: int, y: int, rank: int) -> None:
    card_w, card_h = 454, 166
    rounded((x, y, x + card_w, y + card_h), 16, CARD, CARD_BORDER, 2)
    draw.ellipse((x + 16, y + 16, x + 54, y + 54), fill=TEAL)
    put(x + 35, y + 35, rank, 18, "#041321", True, "mm")
    title = f"{record.get('name', '')}  {six_code(record.get('ts_code', ''))}"
    fit(title, 300, 23, "#FFFFFF", True, 17, x + 66, y + 16)
    put(x + card_w - 18, y + 19, f"{float(record.get('final_score', 0.0)):.1f}", 27, YELLOW, True, "ra")

    put(
        x + 18,
        y + 62,
        f"市场{float(record.get('market_fit_score', 0.0)):.1f}  板块{float(record.get('sector_score', 0.0)):.1f}  技术{float(record.get('technical_score', 0.0)):.1f}",
        17,
        MUTED,
    )
    logic = score_label(record, "logic_purity")
    elasticity = score_label(record, "elasticity")
    fundamentals = score_label(record, "fundamentals")
    positive = score_label(record, "major_positive")
    put(x + 18, y + 88, f"逻辑{logic}  弹性{elasticity}  基本面{fundamentals}  利好{positive}", 17, "#B9E8F2")
    fit("核心：" + core_text(record), 416, 16, TEXT, False, 12, x + 18, y + 118)
    fit("风险：" + short_risk(record), 416, 15, RED, False, 11, x + 18, y + 143)


def render_external(record: dict, x: int, y: int, rank: int) -> None:
    card_w, card_h = 300, 205
    rounded((x, y, x + card_w, y + card_h), 16, CARD, ORANGE, 2)
    draw.ellipse((x + 16, y + 18, x + 58, y + 60), fill="#F3A277")
    put(x + 37, y + 39, rank, 18, "#241A18", True, "mm")
    fit(f"{record.get('name', '')}  {float(record.get('final_score', 0.0)):.1f}", 222, 21, "#FFF2BD", True, 15, x + 72, y + 20)
    put(x + 18, y + 59, f"{six_code(record.get('ts_code', ''))}｜{record.get('sector', '')}", 16, MUTED)
    put(
        x + 18,
        y + 89,
        f"技术{float(record.get('technical_score', 0.0)):.1f}  弹性{int(record.get('elasticity', {}).get('score', 0))}  逻辑{int(record.get('logic_purity', {}).get('score', 0))}",
        16,
        TEXT,
    )
    reason = f"{record.get('sector', '')}强势且池内无同板块合格股，仅作观察"
    for index, line in enumerate(wrap_pixels(reason, 264, 15, False, 2)):
        put(x + 18, y + 121 + index * 22, line, 15, TEXT)
    fit("风险：" + short_risk(record), 264, 14, RED, False, 11, x + 18, y + 174)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--next-trade-date", required=True)
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if data.get("schema_version") != "2.0.0" or data.get("model_version") != "V2.0":
        raise SystemExit("输入不是有效的 V2.0 正式结果")
    if data.get("stage") != "final_review":
        raise SystemExit("输入不是 final_review 正式结果")
    if int(data.get("validation", {}).get("future_data_rows_used", 0)) != 0:
        raise SystemExit("future_data_rows_used 不为0，停止制图")

    official = data["final_selection"]["official_pool"]
    external = data["final_selection"]["external_pool"]
    if len(official) > 10 or len(external) > 3:
        raise SystemExit("池内或池外数量超过 V2.0 上限")

    for gx in range(0, W, 32):
        draw.line((gx, 0, gx, H), fill=GRID, width=1)
    for gy in range(0, H, 32):
        draw.line((0, gy, W, gy), fill=GRID, width=1)

    rounded((38, 34, 168, 82), 22, "#E43D4F")
    put(103, 58, "V2.0", 22, "#FFFFFF", True, "mm")
    put(42, 101, "V2.0每日选股", 46, "#FFFFFF", True)
    cutoff = data.get("evidence_as_of") or data.get("review_as_of") or ""
    put(42, 158, f"{fmt_date(args.next_trade_date)}盘前候选 ｜ 数据截止：{fmt_cutoff(cutoff)}", 18, MUTED)

    market = data["market_overview"]
    rounded((38, 190, 986, 328), 18, PANEL, "#E44858", 3)
    put(60, 207, "市场总览", 25, "#FF6B78", True)
    fit(
        f"{market.get('phase', '')}{market.get('posture', '')}｜上涨 {int(market.get('advance_count', 0))} / 下跌 {int(market.get('decline_count', 0))}｜站上MA20 {float(market.get('above_ma20_ratio_pct', 0.0)):.1f}%",
        900,
        22,
        "#FFFFFF",
        True,
        16,
        60,
        246,
    )
    indices = market.get("major_indices", [])
    growth = float(indices[2].get("return_1d_pct", 0.0)) if len(indices) >= 3 else 0.0
    fit(
        f"成交额 {float(market.get('market_amount_yi', 0.0)):.0f} 亿元｜中位涨跌 {float(market.get('market_median_return_1d_pct', 0.0)):.2f}%｜创业板 {growth:+.2f}%",
        900,
        20,
        "#B7CCE6",
        False,
        15,
        60,
        286,
    )

    rotation = data["sector_rotation"]
    rounded((38, 348, 986, 520), 18, PANEL, ORANGE, 2)
    put(60, 365, "板块轮动", 25, YELLOW, True)
    put(210, 370, "相较上一交易日：排名 + 板块强度 + 收益动量 + 量能", 15, "#89AECA")
    put(62, 402, "升温板块", 18, GREEN, True)
    put(542, 402, "降温板块", 18, RED, True)
    for index, item in enumerate(rotation.get("warming_sectors", [])[:3]):
        fit(display_rotation(item, True), 454, 15, GREEN, False, 10, 62, 432 + index * 26)
    for index, item in enumerate(rotation.get("cooling_sectors", [])[:3]):
        fit(display_rotation(item, False), 420, 15, RED, False, 10, 542, 432 + index * 26)

    put(42, 542, f"池内正式候选  {len(official)}只", 28, CYAN, True)
    for index, record in enumerate(official):
        render_official(record, 42 + (index % 2) * 480, 580 + (index // 2) * 172, index + 1)

    put(42, 1460, f"池外观察  {len(external)}只（不自动入池）", 27, YELLOW, True)
    for index, record in enumerate(external):
        render_external(record, 42 + index * 318, 1505, index + 1)

    rounded((38, 1740, 986, 1995), 18, PANEL, "#2B567C", 2)
    put(60, 1762, "次日执行纪律", 24, CYAN, True)
    rules = [
        "不追高：高开过多先观察，等待量价与板块同步确认。",
        "回踩确认：低开或回踩承接有效，再观察是否出现标准买点。",
        "风险不改分：风险标记用于执行约束，不改变本次评分与排序。",
        "池外仅观察：未经人工确认，不自动加入正式股票池。",
    ]
    for index, rule in enumerate(rules):
        put(64, 1805 + index * 34, "• " + rule, 18, TEXT)
    put(512, 1970, "研究与决策支持，不构成投资建议", 17, "#738EAA", False, "mm")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    img.save(output, format="PNG", optimize=True)
    print(json.dumps({
        "output": str(output),
        "dimensions": [W, H],
        "font_regular": str(FONT_REG),
        "font_bold": str(FONT_BOLD),
        "trade_date": data["trade_date"],
        "next_trade_date": args.next_trade_date,
        "official_count": len(official),
        "external_count": len(external),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
