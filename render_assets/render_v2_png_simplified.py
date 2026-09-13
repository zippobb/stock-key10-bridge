#!/usr/bin/env python3
"""Deterministic V2.0 PNG renderer with crisp simplified-Chinese typography.

The renderer uses the pinned WenQuanYi Micro Hei Chinese outline font, never
falls back to a system CJK font or a glyph atlas, draws the complete page at
4x resolution, and downsamples once with Lanczos to RGB 1024x2048.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from fontTools.ttLib import TTCollection, TTFont


W, H, SCALE = 1024, 2048, 4
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
CJK_FONT_PATH = Path(__file__).with_name("wqy-microhei.ttc")


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return 0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF


def normalize(value: object) -> str:
    return str(value).translate(str.maketrans({
        "：": ":", "｜": "|", "、": ",", "。": ".", "，": ",",
        "（": "(", "）": ")", "“": '"', "”": '"', "‘": "'", "’": "'",
    }))


class SimplifiedFontText:
    """Use a real simplified-Chinese outline font, with no glyph fallback."""

    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise RuntimeError(f"缺少可验证的简体中文字体文件: {path}")
        self.font_path = path
        self.source_ref = "anthonyfok/fonts-wqy-microhei@cd82defe33ec0e86e628329f1b63049ef562c8e5"
        self._cmap: set[int] = set()
        collection = TTCollection(str(path), lazy=False) if path.suffix.lower() == ".ttc" else None
        tt = collection.fonts[0] if collection else TTFont(str(path), lazy=False)
        for table in tt["cmap"].tables:
            self._cmap.update(table.cmap)
        if collection:
            collection.close()
        else:
            tt.close()

    @staticmethod
    def _font_path(bold: bool) -> Path:
        return LATIN_BOLD if bold else LATIN_REG

    def _latin_font(self, size: int, bold: bool) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self._font_path(bold)), max(1, size * SCALE))

    def _cjk_font(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.font_path), max(1, size * SCALE), index=0)

    def preflight(self, texts: Iterable[object]) -> dict[str, object]:
        required = sorted({ch for value in texts for ch in normalize(value) if is_cjk(ch)})
        missing = [ch for ch in required if ord(ch) not in self._cmap]
        representatives = "为买亚环际领闽"
        rep_missing = [ch for ch in representatives if ord(ch) not in self._cmap]
        if missing or rep_missing:
            raise RuntimeError(f"简体中文字体预检失败，缺少: {''.join(missing or rep_missing)}")
        return {
            "required_count": len(required),
            "missing": missing,
            "representative_simplified_chars": representatives,
            "source": "WenQuanYi Micro Hei",
            "source_ref": self.source_ref,
            "font_path": str(self.font_path),
        }

    def textlength(self, value: object, size: int, bold: bool = False) -> float:
        text = normalize(value)
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        latin = self._latin_font(size, bold)
        cjk = self._cjk_font(size)
        total = 0.0
        run = ""
        for ch in text + "\0":
            if ch != "\0" and not is_cjk(ch):
                run += ch
                continue
            if run:
                total += draw.textlength(run, font=latin) / SCALE
                run = ""
            if ch != "\0":
                if ord(ch) not in self._cmap:
                    raise RuntimeError(f"简体中文字体不在预检数据: {ch}")
                total += draw.textlength(ch, font=cjk) / SCALE
        return total

    def draw_text(self, canvas: Image.Image, xy: tuple[float, float], value: object,
                  size: int, color: str, bold: bool = False, anchor: str | None = None) -> None:
        text = normalize(value)
        x, y = float(xy[0]), float(xy[1])
        total = self.textlength(text, size, bold)
        if anchor == "ra":
            x -= total
        elif anchor == "mm":
            x -= total / 2.0
            y -= size / 2.0
        cursor = x * SCALE
        draw = ImageDraw.Draw(canvas)
        latin = self._latin_font(size, bold)
        cjk = self._cjk_font(size)
        run = ""

        def flush_latin() -> None:
            nonlocal cursor, run
            if not run:
                return
            draw.text((round(cursor), round(y * SCALE)), run, font=latin, fill=color,
                      stroke_width=2 if bold else 0, stroke_fill=color, anchor="lt")
            cursor += draw.textlength(run, font=latin)
            run = ""

        for ch in text:
            if not is_cjk(ch):
                run += ch
                continue
            flush_latin()
            if ord(ch) not in self._cmap:
                raise RuntimeError(f"简体中文字体不在预检数据: {ch}")
            stroke_width = 2 if bold else 0
            draw.text((round(cursor), round(y * SCALE)), ch, font=cjk, fill=color,
                      stroke_width=stroke_width, stroke_fill=color, anchor="lt")
            cursor += draw.textlength(ch, font=cjk)
        flush_latin()


def fmt_date(value: str) -> str:
    date = datetime.strptime(str(value)[:8], "%Y%m%d")
    return date.strftime("%Y-%m-%d")


def six_code(value: str) -> str:
    return str(value).split(".", 1)[0]


def score_label(record: dict, field: str) -> str:
    value = record.get(field)
    if not isinstance(value, dict) or value.get("score") is None:
        return "-"
    return str(value["score"])


def draw_page(data: dict, output: Path, next_trade_date: str, glyphs: SimplifiedFontText) -> dict[str, object]:
    if data.get("schema_version") != "2.0.0" or data.get("model_version") != "V2.0":
        raise RuntimeError("输入不是有效的 V2.0 正式结果")
    if data.get("stage") != "final_review":
        raise RuntimeError("输入不是 final_review 正式结果")
    if int(data.get("future_data_rows_used", 0)) != 0:
        raise RuntimeError("future_data_rows_used 不为0，停止制图")
    official = data.get("final_selection", {}).get("official_pool", [])
    external = data.get("final_selection", {}).get("external_pool", [])
    if len(official) > 10 or len(external) > 3:
        raise RuntimeError("池内或池外数量超过 V2.0 上限")

    dynamic_texts = [data.get("market_overview", {}).get("phase", ""), data.get("market_overview", {}).get("posture", "")]
    dynamic_texts += [x.get("name", "") for x in official + external]
    dynamic_texts += [x.get("sector", "") for x in official + external]
    dynamic_texts += [x.get("selection_axes", {}).get("entry_timing_status", "数据不足") for x in official + external]
    dynamic_texts += [x.get("sector", "") for x in data.get("sector_rotation", {}).get("warming_sectors", [])[:3]]
    dynamic_texts += [x.get("sector", "") for x in data.get("sector_rotation", {}).get("cooling_sectors", [])[:3]]
    fixed_texts = [
        "V2.0每日选股", "盘前候选", "数据截止", "市场总览", "板块轮动", "相较上一交易日", "升温板块", "降温板块",
        "池内正式候选", "池外观察", "只（不自动入池）", "市场", "板块", "技术", "逻辑", "弹性", "基本面", "利好",
        "核心", "风险", "研究与决策支持，不构成投资建议", "次日执行纪律", "不追高：高开过多先观察，等待量价与板块同步确认。",
        "回踩确认：低开或回踩承接有效，再观察是否出现标准买点。", "风险不改分：风险标记用于执行约束，不改变本次评分与排序。",
        "池外仅观察：未经人工确认，不自动加入正式股票池。", "时机", "可关注", "待确认", "不追高", "数据不足", "风险待确认",
        "强", "日", "量", "核心逻辑", "市场背景", "上涨", "下跌", "站上", "成交额", "亿元", "中位涨跌"
    ]
    preflight = glyphs.preflight(dynamic_texts + fixed_texts)
    img = Image.new("RGB", (W * SCALE, H * SCALE), BG)
    draw = ImageDraw.Draw(img)

    def box(values: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
        return tuple(round(v * SCALE) for v in values)  # type: ignore[return-value]

    def rounded(values: tuple[float, float, float, float], radius: float, fill: str,
                outline: str | None = None, width: float = 1) -> None:
        draw.rounded_rectangle(box(values), radius=round(radius * SCALE), fill=fill,
                               outline=outline, width=max(1, round(width * SCALE)))

    def put(x: float, y: float, value: object, size: int = 20, color: str = TEXT,
            bold: bool = False, anchor: str | None = None) -> None:
        glyphs.draw_text(img, (x, y), value, size, color, bold, anchor)

    def width(value: object, size: int, bold: bool = False) -> float:
        return glyphs.textlength(value, size, bold)

    def fit(value: object, max_width: float, size: int, color: str = TEXT, bold: bool = False,
            min_size: int = 12, x: float = 0, y: float = 0, anchor: str | None = None) -> int:
        current = size
        while current > min_size and width(value, current, bold) > max_width:
            current -= 1
        put(x, y, value, current, color, bold, anchor)
        return current

    def short_rotation(item: dict, warming: bool) -> str:
        arrow = "↑" if warming else "↓"
        sector = str(item.get("sector") or "")
        prev = item.get("previous_rank")
        curr = item.get("current_rank")
        strength = float(item.get("strength_change") or 0.0)
        ret = float(item.get("return_1d_mean_pct") or 0.0)
        vol = float(item.get("volume_expansion_ratio_pct") or 0.0)
        return f"{arrow} {sector}  {prev}->{curr}  强{strength:+.3f}  日{ret:+.1f}% 量{vol:+.0f}%"

    def official_card(record: dict, x: float, y: float, rank: int) -> None:
        card_w, card_h = 454, 166
        rounded((x, y, x + card_w, y + card_h), 16, CARD, CARD_BORDER, 2)
        draw.ellipse(box((x + 16, y + 16, x + 54, y + 54)), fill=TEAL)
        put(x + 35, y + 35, rank, 18, "#041321", True, "mm")
        title = f"{record.get('name', '')}  {six_code(record.get('ts_code', ''))}"
        fit(title, 300, 23, "#FFFFFF", True, 17, x + 66, y + 16)
        put(x + card_w - 18, y + 19, f"{float(record.get('final_score', 0.0)):.1f}", 27, YELLOW, True, "ra")
        put(x + 18, y + 62,
            f"市场{float(record.get('market_fit_score', 0.0)):.1f}  板块{float(record.get('sector_score', 0.0)):.1f}  技术{float(record.get('technical_score', 0.0)):.1f}",
            17, MUTED)
        put(x + 18, y + 88,
            f"逻辑{score_label(record, 'logic_purity')}  弹性{score_label(record, 'elasticity')}  基本面{score_label(record, 'fundamentals')}  利好{score_label(record, 'major_positive')}",
            17, "#B9E8F2")
        fit(f"核心逻辑：{record.get('sector', '')}", 416, 15, TEXT, False, 12, x + 18, y + 113)
        timing = record.get("selection_axes", {}).get("entry_timing_status") or "数据不足"
        fit(f"时机：{timing}  风险待确认", 416, 14, RED if timing == "不追高" else MUTED, False, 11, x + 18, y + 140)

    def external_card(record: dict, x: float, y: float, rank: int) -> None:
        card_w, card_h = 300, 205
        rounded((x, y, x + card_w, y + card_h), 16, CARD, ORANGE, 2)
        draw.ellipse(box((x + 16, y + 18, x + 58, y + 60)), fill="#F3A277")
        put(x + 37, y + 39, rank, 18, "#241A18", True, "mm")
        fit(f"{record.get('name', '')}  {float(record.get('final_score', 0.0)):.1f}", 222, 21, "#FFF2BD", True, 15, x + 72, y + 20)
        put(x + 18, y + 59, f"{six_code(record.get('ts_code', ''))}|{record.get('sector', '')}", 16, MUTED)
        put(x + 18, y + 89,
            f"技术{float(record.get('technical_score', 0.0)):.1f}  弹性{score_label(record, 'elasticity')}  逻辑{score_label(record, 'logic_purity')}",
            16, TEXT)
        put(x + 18, y + 121, "池外仅观察", 15, TEXT)
        timing = record.get("selection_axes", {}).get("entry_timing_status") or "数据不足"
        fit(f"时机：{timing}", 264, 15, RED if timing == "不追高" else MUTED, False, 11, x + 18, y + 149)
        put(x + 18, y + 177, "风险待确认", 14, RED)

    for gx in range(0, W, 32):
        draw.line((gx * SCALE, 0, gx * SCALE, H * SCALE), fill=GRID, width=1 * SCALE)
    for gy in range(0, H, 32):
        draw.line((0, gy * SCALE, W * SCALE, gy * SCALE), fill=GRID, width=1 * SCALE)

    rounded((38, 34, 168, 82), 22, "#E43D4F")
    put(103, 58, "V2.0", 22, "#FFFFFF", True, "mm")
    put(42, 101, "V2.0每日选股", 46, "#FFFFFF", True)
    cutoff = data.get("evidence_as_of") or data.get("review_as_of") or ""
    cutoff_text = cutoff.replace("T", " ")[:16]
    put(42, 158, f"{fmt_date(next_trade_date)}盘前候选 | 数据截止: {cutoff_text}", 18, MUTED)

    market = data["market_overview"]
    rounded((38, 190, 986, 328), 18, PANEL, "#E44858", 3)
    put(60, 207, "市场总览", 25, "#FF6B78", True)
    fit(f"市场{market.get('phase', '')} | {market.get('posture', '')} | 上涨 {int(market.get('advance_count', 0))} / 下跌 {int(market.get('decline_count', 0))} | 站上MA20 {float(market.get('above_ma20_ratio_pct', 0.0)):.1f}%", 900, 22, "#FFFFFF", True, 15, 60, 246)
    put(60, 286, f"成交额 {float(market.get('market_amount_yi', 0.0)):.0f} 亿元 | 中位涨跌 {float(market.get('market_median_return_1d_pct', 0.0)):.2f}%", 20, "#B7CCE6")

    rotation = data["sector_rotation"]
    rounded((38, 348, 986, 520), 18, PANEL, ORANGE, 2)
    put(60, 365, "板块轮动", 25, YELLOW, True)
    put(260, 370, "相较上一交易日", 15, "#89AECA")
    put(62, 402, "升温板块", 18, GREEN, True)
    put(542, 402, "降温板块", 18, RED, True)
    for index, item in enumerate(rotation.get("warming_sectors", [])[:3]):
        fit(short_rotation(item, True), 454, 15, GREEN, False, 10, 62, 432 + index * 26)
    for index, item in enumerate(rotation.get("cooling_sectors", [])[:3]):
        fit(short_rotation(item, False), 420, 15, RED, False, 10, 542, 432 + index * 26)

    put(42, 542, f"池内正式候选  {len(official)}只", 28, CYAN, True)
    for index, record in enumerate(official):
        official_card(record, 42 + (index % 2) * 480, 580 + (index // 2) * 172, index + 1)

    put(42, 1460, f"池外观察  {len(external)}只（不自动入池）", 27, YELLOW, True)
    for index, record in enumerate(external):
        external_card(record, 42 + index * 318, 1505, index + 1)

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

    output.parent.mkdir(parents=True, exist_ok=True)
    final = img.resize((W, H), Image.Resampling.LANCZOS).convert("RGB")
    final.save(output, format="PNG", optimize=True)
    return {
        "output": str(output), "dimensions": [W, H], "mode": final.mode,
        "trade_date": data["trade_date"], "next_trade_date": next_trade_date,
        "official_count": len(official), "external_count": len(external),
        "render_backend": "wqy_microhei_simplified_font_supersampled_4x",
        "glyph_preflight": preflight,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--next-trade-date", required=True)
    parser.add_argument("--font", "--glyphs", dest="font", default=str(CJK_FONT_PATH))
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = draw_page(data, Path(args.output), args.next_trade_date, SimplifiedFontText(Path(args.font)))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
