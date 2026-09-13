# V2.0 PNG 简体中文渲染资产

当前启用的渲染器是 `render_v2_png_simplified.py`，对应固定样式
`v2.0-dashboard-20260904`。

## 渲染方法

- 使用可验证的 WenQuanYi Micro Hei 中文轮廓字体（完整 TTC 的中文字体面），
  不使用 KanjiVG、日文字形、繁体字形图集或系统字体回退。
- 页面先按 4 倍分辨率绘制，再一次性用 Lanczos 缩小到 RGB
  1024×2048，避免低分辨率直接栅格化造成模糊。
- 渲染前用字体 cmap 预检所有实际中文字符，并强制检查
  `为 买 亚 环 际 领 闽` 等简繁差异字；缺字立即失败。
- JSON 只作为已完成终审结果输入；渲染器不重算分数、不改变排序、不改变池归属。

## 运行

```bash
python3 render_v2_png_simplified.py \
  --input /path/to/key10_review_YYYYMMDD.json \
  --output /path/to/key10_review_YYYYMMDD.png \
  --next-trade-date YYYYMMDD
```

输出必须为标题“V2.0每日选股”的 RGB 1024×2048 PNG，并完成原尺寸视觉检查。

## 退役后端

`kanjivg_vector_atlas_v1` 及其图集构建脚本已退役，不得作为正式
V2.0输出后端。历史提交保留用于审计，但新的正式结果不得引用它。

## 字体来源

字体文件来自 `anthonyfok/fonts-wqy-microhei` 的提交
`cd82defe33ec0e86e628329f1b63049ef562c8e5`。WenQuanYi Micro Hei
按 Apache 2.0 或 GPLv3（含字体嵌入例外）授权；完整许可文本和版权信息
以来源仓库为准。

