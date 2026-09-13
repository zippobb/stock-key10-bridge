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



## 制图传输与验收契约（2026-09-13生效）

独立制图验收已通过，范围仅为已验证正式JSON到PNG、仓库写入回读和视觉检查，不代表重新终审或未来准时性保证。机器契约：`render_assets/render_contract_v2.json`；验收报告：`render_assets/acceptance/20260911/20260913T134742Z/acceptance_report.json`。

- 必须从同一main基准commit通过GitHub连接器读取契约、渲染器及字体传输文件，不使用shell Git或凭据。
- 字体使用`render_assets/font_transport/manifest.json`列出的18个小型UTF-8分块；逐块验证长度/SHA256，运行同目录`restore_font.py`还原完整TTC，再验证固定完整SHA256。禁止空内容继续、系统字体回退、KanjiVG或旧本地字体充当验收输入。
- 原始JSON/文本哈希敏感读取：先取得文件Blob SHA，再通过`github_fetch_blob`读取UTF-8原文，保留末尾换行；使用文件写入工具或标准输入安全落盘后流式算哈希。不得直接对`github_fetch_file`可能归一化的字符串宣称原始字节已校验，也不得猜测增删换行来满足哈希。
- 渲染入口改为`render_assets/render_v2_verified.py`，传入`--input`、权威`--input-sha256`、`--output`、`--next-trade-date`。入口强制检查输入/字体/渲染器哈希、RGB 1024×2048和输入未变；任何预检失败立即停止。
- 固定后端`wqy_microhei_simplified_font_supersampled_4x`，4倍绘制后一次Lanczos缩小。仍需原尺寸目视核验简体、清晰、无缺字方框/重叠/截断，逐只核对JSON；程序仅输出`visual_check=pending`，不能自动宣称视觉通过。
- PNG通过连接器二进制base64读取回读，只去除传输换行后比较原始字节。仓库提交按最新基准commit非强制推进，冲突停止。所有已有评分、日期时限、租约、交付闸门、池归属和排序规则继续有效。
