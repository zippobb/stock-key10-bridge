# Stock Key10 Project State

> 本文件是自动化选股项目的长期事实源（single source of truth）。
> 对话记忆仅用于线索召回，不得替代本文件与实时系统核验。

## 当前交付契约：文字报告（2026-09-15用户确认，临时生效）
本节优先于本文件及其他提示词中所有旧PNG/制图要求，直至用户明确恢复制图。V2.0版本、评分、证据标准、排序、池内外准入、KDJ、候选数、任务时间和启用状态保持不变。
- 当前责任链：ECS生成量化包 → ChatGPT真实终审 → 正式JSON持久化并回读 → 中文Markdown文字报告持久化并回读 → 日期回执及latest回执写入并回读 → 释放所属锁 → 在任务会话直接展示文字报告。
- 取消PNG生成、字体下载/恢复、渲染契约读取、图像哈希、尺寸和视觉验收。旧制图文件保留为停用资料，PNG缺失或旧制图失败不再阻断文字交付，不自动恢复制图。
- 正式JSON路径仍为review/YYYY/MM/key10_review_<trade_date>.json；文字报告为同目录key10_review_<trade_date>.md。文字必须直接对应已验证正式JSON，不得改分、改排名或补造证据。
- 报告标题“V2.0每日选股”，列交易日、下一交易日、证据截止时间；依次展示市场总览、升温/降温板块、池内正式候选、池外观察、次日执行纪律。板块比较相邻交易日排名、综合板块分、收益动量、上涨占比及量能变化，各最多选3个代表；符合条件不足3个时按实际数量并说明，历史数据缺失写无法判断，不以当日强弱冒充轮动。
- 候选用中文表格列排名、名称/代码、板块、综合分、七项评分、入选理由、主要风险、研究价值与市场背景、时机状态和次日动作；池外明确准入理由及不自动入池。缺项显示数据不足及原因，不填默认分；高综合分不是买入信号。摘要不展示“未来数据使用”，底层JSON继续保留并校验future_data_rows_used=0。
- 回执保留原字段及重试元数据，增加delivery_mode=text、text_report_path、text_delivery_status；兼容字段png_delivery_status固定为not_required_text_mode，png_path=null，不伪造PNG成功。正式JSON及文字报告回读一致后才能使final_delivery_gate=ready；发送前visible_completion只能pending，发送时实际展示完整文字报告，不能仅给文件链接。
- 主任务目标节点18:45正式JSON、18:47文字报告、18:48双回执；收尾及硬截止仍为主18:48/18:49、第一次18:54/18:55、第二次18:57/18:58。到时保存实际进度，失败可见，不将迟到结果冒充准时完成。
- 已有合格正式JSON时仅续作文字交付；若仅旧PNG失败，从text_report_from_validated_json恢复，不重复终审。若旧JSON本身证据不合格，仍从阶段B补证，不能借文字模式绕过质量门槛。旧回执不回写，本次实际恢复需遵守当前日期、租约、次数和硬截止。
- 同日同输入已有完整文字成功结果时，只回读并重新展示现有文字报告，输出“结果未变化”。任一失败仍发送“执行失败”或“数据未就绪”及真实阶段/缺失/恢复点。取消制图不等于证据问题已修复。
- 交付复核严格只读：先PROJECT_STATE，再latest和对应日期回执；当日run_date与状态合法、双回执一致才报告原状态。成功/结果未变化还须正式JSON及文字报告可回读、名单分数理由一致、delivery_mode=text、text_delivery_status已持久化回读且gate=ready。缺失或不一致输出“交付复核：执行失败”；不得补产物、改锁、改仓库或主任务。无论状态均给可见回执，列trade_date、回执记录的finished_at、result_json_path、text_report_path、text_delivery_status与摘要。

## 元数据

- 状态文件版本：1.0
- 最后更新：2026-09-15（用户确认临时文字报告交付）
- GitHub 仓库：`zippobb/stock-key10-bridge`
- 服务器项目目录（2026-09-02实时验证）：`/opt/stock-automation/scanner`
- 当前总体状态：V2.0保持有效；交付临时改为正式JSON及中文文字报告，制图暂停。取证缓存和完整端到端尚未通过，不宣称此修改已解决证据不足。

## 权威性与冲突处理

### 规则的优先级

1. 用户在当前会话中的最新明确决定
2. 本文件“已确认规则”
3. 其他项目文档
4. 旧对话、摘要与自动生成记忆

不得用较低优先级覆盖较高优先级。只有当前V2权威规则之间存在未解决冲突时才停止并请求确认；V1.1、V1.2和影子层均已归档，不得因旧版本记载再次制造冲突或要求单独确认。

### 运行状态的优先级

1. 对服务器、GitHub、定时任务或文件的本次实时读取
2. 本文件中带时间戳的“已验证快照”
3. 历史日志、聊天记录和记忆摘要

禁止根据旧状态宣称服务、timer、数据或任务“当前正常”。

## 每次工作的强制读取顺序

1. 读取本文件。
2. 涉及当日数据时，实时读取 `latest/manifest.json` 和必要的权威日期归档。
3. 涉及服务器运行时，实时检查真实服务器的 service、timer 和日志；若当前环境无法访问服务器，必须明确标为“未验证”。
4. 涉及 ChatGPT 定时任务时，实时读取任务状态与最近运行信息。
5. 回答中区分：
   - 已确认规则
   - 已验证实时状态
   - 历史记录
   - 待验证或冲突
6. 用户确认规则变更或完成关键基础设施修改后，同步更新本文件和变更记录。

## 已确认规则

### 市场与股票范围

- 仅覆盖沪深主板、创业板、科创板。
- 明确排除北交所。
- 池外扫描要求：属于上述市场、市值不低于30亿元、排除退市风险。
- 池内与池外分开输出；池外股票不得自动加入正式股票池，由用户决定是否入池。
- 2026-08-28记录的观察池规模约237只；该数量是日期快照，不是永久固定值。
- V2.0删除“重点池/重点标签”。旧文档中“重点池87只、观察池246只”为历史口径，不得直接当作当前配置。

### 当前V2.0终审评分口径

- V2.0完全替代V1.1风险层和V1.2风向层。
- 影子层已取消，不作为独立层参与评分、排序、入选、淘汰或输出。
- V1.1、V1.2及影子层资料仅作归档；不得据此要求再次确认替代关系，也不得将其识别为可重试或不可重试规则冲突。

- 市场适配：5分
- 板块：15分
- 技术：30分
- 逻辑纯度：15分
- 个股弹性：20分
- 基本面：10分
- 重大利好：5分
- 总分：100分
- 个股弹性由反弹效率、题材弹性、资金承接、预期差、筹码推动效率五项组成，每项0–4分。
- 综合分低于60分不得入选。
- 单项缺失时，可用权重达到70%才允许按可用权重等比例还原至100分；低于70%标为数据不完整，不得正式入选。
- 大盘无硬开关。
- 板块样本数不设固定上限。
- 重大风险只作文字标记，不扣分、不否决、不改变分数或排序。
- 池内正式输出0–10只，不凑数。
- 池外输出0–3只，与池内分开展示。
- 必须记录公开信息截止时间，且 `future_data_rows_used=0`。

### 排序与池外条件

- 池内依次按综合分、技术分、弹性分、板块分、逻辑纯度、股票代码排序。
- 池外仅保留强势板块候选，且该板块不存在任何池内综合分不低于60分的合格个股。
- 池外与池内使用相同排序顺序，不得混排。

### 第1步修正：研究价值与买入时机分离（当前有效）

- `final_score`及V2七项评分继续作为综合质量/入选资格分，不是买入信号。
- 每个候选新增`selection_axes`：`research_value_score`（逻辑纯度+基本面+重大利好，0–30）、`market_context_score`（市场适配+板块+技术+弹性，0–70）及`entry_timing_status`、`entry_timing_basis`、`next_day_action`。
- `entry_timing_status`仅可为“可关注”“待确认”“不追高”“数据不足”；本步`entry_timing_score_effect=0`、`entry_timing_rank_effect=false`，不改变分数、排序、池内/池外准入或PNG版式。
- 研究轴缺项不填默认分，按该轴可用权重达到70%时等比例还原，否则记数据不足并记录缺失证据；重大风险规则保持不变。
- 第2–5步另行处理板块驱动、动量/KDJ、阶段状态和输出数量；历史验证（第6步）暂缓。
- 已同步范围：`V2_REVIEW_WORKFLOW.md`及V2.0主任务、第一次重试、第二次重试的活动提示词；ECS量化脚本、板块算法、KDJ/动量阈值、PNG版式本次未改。服务器运行状态未在本会话实时验证。

### 第2步修正：板块轮动驱动候选来源（审查侧当前有效）

- 使用当前bundle的`sector_summary.all_sectors`与`previous_trade_date`权威bundle按行业对齐，计算板块排名变化和强度变化；缺少前一交易日数据时标记`data_insufficient`，不以当日排名替代。
- 轮动层分为`warming`、`leading`、`neutral`、`cooling`、`data_insufficient`；升温需排名上升/强度改善并至少两项加速度、广度、资金、量能确认。
- 候选增加`rotation_state`、`rotation_source_tier`、板块排名/强度变化和确认项数量。正式池在不降低V2评分和数据完整门槛的前提下，若有A层合格候选，最多10只中至少保留3只A层（按实际合格数折减）；不凑数，D层仅作最后备选。
- 现有40只候选中若没有A层合格候选，记录`rotation_coverage_gap`，不使用旧日期、旧PNG或臆造股票补足。
- 用户已确认V2取消行业/板块限制：所有行业均可进入轮动分层和候选来源，不执行白酒、证券、银行等旧排除清单；行业名称不得作为自动排除条件，仍须满足V2市场范围、数据完整、评分门槛、弹性与风险核验。
- 已同步范围：`V2_REVIEW_WORKFLOW.md`及V2.0主任务、第一次重试、第二次重试的活动提示词；本步为审查侧2A，ECS候选池扩展/轮动专用槽位（2B）仍待服务器连接后实施；KDJ/动量阈值、PNG版式本次未改。板块限制取消已按用户中间决定固化，不再作为待确认项。服务器运行状态未在本会话实时验证。


### 第3步修正：动量与KDJ时机层（3A/3B当前生效）

- 目标：识别早期拐点、趋势确认、扩张位和回调等待，避免把高综合分误写成临近回调的可买信号。
- 3A当前在终审侧生效：时机状态写入`selection_axes`，保留`timing_state`、`kdj_status`、`entry_timing_basis`、`next_day_action`、`timing_guard_triggered`；不改变七项分、`final_score`、排序、60分/数据完整门槛、候选数量、板块全行业范围或PNG版式。
- KDJ契约固定为标准KDJ(9,3,3)，要求候选记录提供K/D/J当前值与前值、3日斜率、金叉/死叉状态及年龄、`kdj_as_of`、完整性和来源；K/D限定为0–100有限数，J按标准公式保留有限原值（可超出0–100）；全部合格时`kdj_status=complete`，缺失、越界或未来日期一律`kdj_status=data_insufficient`，不得由RSI、MACD、收益率、乖离率或量能反推。计算使用RSV9、K/D递推和`J=3*K-2*D`，首个有效K/D为50，窗口不足标记数据不足，`HH9=LL9`时RSV取50。
- 状态优先级为`extended`→`pullback_wait`→`early_turn`→`trend_confirming`→`weak`；早期近2日金叉且K<60/D<60/J<90、斜率向上才标`可关注`，趋势确认标`待确认`，K>=80/J>=100、K>=70或J>=90的死叉或扩张护栏触发标`不追高`，无法判断标`数据不足`。`pullback_wait`中的“显著高于MA20”固定为`distance_ma20_atr>=1.0`。
- 审查侧扩张护栏无论KDJ是否完整都评估：`distance_ma20_atr>=1.8`，或`rsi14>=70`且`return_5d_pct>=8`，或`close_location>=0.90`且`volume_ratio_1d_prior_10d>=2.0`且`return_1d_pct>=5`；触发仅标注`不追高`，不否决、不改分、不改排名。KDJ缺失且未触发护栏时保持`数据不足`。
- 2026-09-06实时读取latest：`trade_date=20260904`，池内30只+池外10只均无KDJ字段；按上述护栏预检查，11/40条会得到“不追高”标注，其余在KDJ补齐前为“数据不足”。这不是正式结果回写。
- 3B已完成并正式生效：2026-09-07首次自然交易日链路验收通过。ECS生成并发布`trade_date=20260907`；官方30只+外部10只，KDJ 14字段覆盖40/40，`kdj_data_complete=40/40`，K/D范围、J有限值、斜率、交叉状态/年龄及公式全部通过，`future_data_rows_used=0`，`excluded_sectors=[]`；评分、排名、池归属、排序及板块摘要保持不变。GitHub archive/latest与本地bundle SHA256一致为`43fb384b3361dfe0d5b98fbbcc95acc786e9ce7099c569b13ccb8fc46ad70cbc`，发布commit为`2cae467022df2f7635ffaaa211f484e48a77ee74`。20260904历史归档未触碰；18:30补偿发布按幂等规则成功跳过。3B不再是生产阻断，后续按日观察运行稳定性。
- 已同步范围：`V2_REVIEW_WORKFLOW.md`及V2.0主任务、第一次重试、第二次重试提示词；历史结果不回写，历史验证（第6步）继续暂缓。

### 输出与职责

- 正式结果包括JSON、七项分数、五项弹性明细、风险标记、信息截止时间、未来数据使用校验及PNG。
- 产物责任矩阵：ECS仅生成量化候选包并发布到GitHub；ChatGPT终审任务完成真实终审并生成正式JSON和PNG；Codex仅执行服务器检查与维护。
- 不得把任务“已设置”当成任务“已成功执行”。
- 成功、结果未变化、数据未就绪、执行失败均应提供用户可见回执；没有可见回执不得视为交付完成。


### V2.0 PNG固定样式

- 样式版本：`v2.0-dashboard-20260904`，适用于主终审、有限重试和交付复核。
- 画布：PNG、RGB、1024×2048；深蓝背景`#071525`及32px低对比度网格。
- 字体：可验证的NotoSansSC/NotoSansCJK SC；粗体字形为空时使用常规中文字体加描边；不得使用拉丁字体、英文音译或空字形。
- 版式：顶部标题/日期；市场总览；左右分列的升温/降温板块轮动；两列池内正式候选卡片；三列池外观察卡片；底部次日执行纪律。
- 内容：真实中文股票名称和板块，池内最多10只、池外最多3只；每张卡片展示排名、代码、综合分、评分摘要、理由和风险；风险只作文字标记。
- 验收：原始尺寸视觉检查通过后才可交付；不得出现文字溢出、卡片覆盖、日期混用、英文音译、空字形或旧候选内容；样式失败只修PNG，不重做JSON和评分。
## 当前已验证快照

### GitHub数据

实时检查时间：2026-09-01 22:31 Asia/Shanghai。

`latest/manifest.json`：

- `status=success`
- `schema_version=2.0.0`
- `trade_date=20260831`
- `archive_path=archive/2026/08/key10_bundle_20260831.json`
- `published_at_utc=2026-08-31T15:42:47Z`
- `bundle_sha256=48631c4f836d5ceefb96ad6ac5f7d417ea4e60992b55f4136d7da76231f0be1b`

截至检查时间，仓库没有2026-09-01发布提交。因此2026-09-01正式数据包未就绪。

### ChatGPT定时任务

实时检查时间：2026-09-04 01:26 Asia/Shanghai。

任务快照：

- `V2.0选股终审主任务`
  - 状态：已启用；2026-09-03重新创建，原同名历史任务已不可更新并保持禁用
  - 配置时间：交易日18:35，Asia/Shanghai
  - 最近运行：新任务尚未运行，下一次按计划执行
  - 配置为只接受当天交易日、V2.0、`stage=21:30_quantitative_review_payload`、未来数据为0的数据包
  - 调度：交易日18:35，`exact_schedule`
  - `next_run_time` 仍不作为调度健康依据，改用运行回执和最终交付复核验收
- `V2.0终审有限重试`
  - 状态：已启用；第一次重试为交易日18:45，Asia/Shanghai，`exact_schedule`
  - 第二次重试：原独立漏跑检查任务已改造为 `V2.0终审第二次重试`，交易日18:55，Asia/Shanghai，`exact_schedule`
  - 主运行后最多重试2次；成功即停止，只重试数据迟到或瞬时故障，并从GitHub检查点恢复
  - 使用180秒租约、60秒心跳、owner_token与generation防止主任务或两次重试重叠；第二次失败后写入 `retry_exhausted=true`，不再自动重试
- 独立漏跑检查已取消；原任务已改造为 `V2.0终审第二次重试`，不再占用额外调度名额。
- `终审交付复核`
  - 配置时间：交易日18:58，Asia/Shanghai，`exact_schedule`
  - 每次都提供可见复核回执；核验当天日期回执、latest回执、正式结果JSON及字段一致性，缺失时明确告警
  - 主任务现须将正式结果写入 `review/YYYY/MM/`，并最后写入 `delivery/YYYY/MM/` 日期回执及 `latest/key10_review_receipt_latest.json`；持久回执回读成功且任务会话已回复才算完成
- `标普周期与轮动`
  - 每周一运行

旧V1.x选股任务、融合重跑任务和一次性补跑任务已暂停或完成，不应被当成当前主任务。

### 服务器实时核验通道

实时验收时间：2026-09-02 00:50前后 Asia/Shanghai。

- Windows SSH别名：`stock-server`
- 服务器主机名：`iZuf6fp4j1j3s3wlgdtzfyZ`
- 服务器用户：`root`
- 项目目录：`/opt/stock-automation/scanner`
- Windows本地Codex项目：`C:\\Users\\ROG\\stock-key10-remote`
- 工作方式：本次由Codex通过ECS Workbench直连执行服务器命令；未使用SSH，且不在服务器上运行Codex。
- 已验证：SSH密钥认证成功；systemd为`running`；`stock-key10-result-mcp.service`为active/running；`stock-key10-daily.timer`和`stock-key10-github-publish.timer`为active/waiting。
- 安全收尾：服务器上的临时 `/root/.codex/auth.json` 副本已删除。


### 第2项调度与2026-09-01数据修复

实时验收时间：2026-09-02 01:24 Asia/Shanghai。

- 根因：`stock-key10-daily.timer`原定17:30，但生成器只在18:15后把当天视为已完成交易日；任务因此在2026-09-01跳过当天并继续导出20260831旧包。发布任务17:30与17:45均成功触发，但只对旧包幂等跳过。
- 分类：日更已触发且未失败；发布已触发且未失败；问题属于当天生成时点早于数据完成门槛，导致GitHub输入仍为前一交易日。
- 修复：`stock-key10-daily.timer`改为交易日18:30，`stock-key10-github-publish.timer`改为交易日18:45；均早于ChatGPT 20:00终审。
- 协议说明：`stage=21:30_quantitative_review_payload`保留为兼容字段名，不再解释为真实墙钟生成时间；生产者、发布器与终审仍共同校验该值。
- 服务器备份：`/root/stock-key10-backups/step2_20260902_012134/`。
- 2026-09-01补跑：交易日历确认开市，待处理日期精确为20260901；日更与GitHub发布均退出0。
- 正式包：`trade_date=20260901`、`schema_version=2.0.0`、`model_version=V2.0`、`future_data_rows_used=0`、SHA256=`d31926dab165b33b2f62d20075386cd5f700e6a674822e9070a5f3de7ec8d4f0`。
- GitHub manifest：`status=success`、`archive_path=archive/2026/09/key10_bundle_20260901.json`、`published_at_utc=2026-09-01T17:23:24Z`。

## 待验证与明确冲突

以下仅保留当前V2仍需验证的事项；已归档版本不得重新引入冲突：

4. 当前Work会话本身仍不是服务器环境；但已通过Windows桌面版Codex本地项目和SSH别名 `stock-server` 建立可重复的服务器实时核验通道。


## 最新桥接实时核验

实时检查时间：2026-09-04 08:55 Asia/Shanghai。

- GitHub `latest/manifest.json)：`status=success`、`trade_date=20260903`、`schema_version=2.0.0`，量化 bundle 已发布。
- bundle：`model_version=V2.0`、`stage=21:30_quantitative_review_payload`、`future_data_rows_used=0`、正式池30只、外部池10只。
- 20260903 正式结果 JSON、日期回执和最终 PNG 已生成并完成回读校验。
- `review/2026/09/key10_review_20260903.json`、`delivery/2026/09/key10_review_receipt_20260903.json`和`latest/key10_review_receipt_latest.json`均指向20260903；PNG已按`v2.0-dashboard-20260904`样式重新生成，SHA256=`2d7cc2af9c429df3b8a12aad3b96b27ff62eb4042042862e20c3dbfb7cd8b8a8`。
- 当前工作会话未能直接访问 ECS，服务器 timer/service 状态待下一次服务器连接验收；本段只确认 GitHub 桥接状态。

## 本次实时调度重排核验

核验时间：2026-09-04 01:26 Asia/Shanghai。核验方式：ECS Workbench直连；未读取 `.env`，未运行选股任务。

- 完整当日行情可靠获取门槛保持为18:15；未修改V2入口中的数据截止逻辑。
- `stock-key10-daily.timer`：交易日18:20 Asia/Shanghai，`enabled / active`。
- `stock-key10-github-publish.timer`：交易日18:30 Asia/Shanghai，`enabled / active`。
- `stock-key10-daily.service`：实际入口仍为V2.0量化脚本，18:20描述已同步；`ExecStartPost`仍负责bundle导出，`OnSuccess`仍连接GitHub发布服务。
- `strategy_v2.json`：`schedule.quantitative_run=18:20`、`schedule.final_review=18:35`；顶层`final_review`评分配置对象未改；`21:30_quantitative_review_payload`仅保留为阶段兼容标签。
- 本次仅执行配置同步与`systemctl daemon-reload`，未启动或重启任何服务。
- ECS配置备份：`/root/stock-key10-backups/step_schedule_metadata_20260903T172641Z/`。
- ChatGPT当前V2.0任务时间为18:35主终审、18:45第一次重试、18:55第二次重试、18:58交付复核；标普任务保持不变，其余非V2任务已停用。

### 2026-09-07 第3B首次自然生产发布验收

- 验收结论：步骤3B正式部署完成，无剩余阻断。
- 18:20日更正常触发，`stock-key10-daily.service`退出码0，生成`trade_date=20260907`。
- 18:20:54由`OnSuccess`触发首次GitHub发布并通过远端校验；18:30补偿发布正常触发并按幂等规则跳过，退出码0。
- 本地两个bundle、GitHub archive和GitHub latest SHA256均为`43fb384b3361dfe0d5b98fbbcc95acc786e9ce7099c569b13ccb8fc46ad70cbc`；发布commit为`2cae467022df2f7635ffaaa211f484e48a77ee74`；GitHub manifest SHA256为`db78e3e169593ef43f6527fe34172746632797dd3c580ae7204d5bb002b1cc83`。
- 官方候选30只、外部候选10只；KDJ 14字段覆盖40/40，`kdj_data_complete=40/40`；交叉状态为`golden=4/death=4/none=32`；`future_data_rows_used=0`；`excluded_sectors=[]`；schema/model/stage均符合V2.0契约。
- 评分、排名、池归属、排序和板块摘要与原输入完全一致；未修改代码、配置、timer、service或历史归档。
- 两个timer保持`enabled/active`；两个service为`inactive/dead`且`Result=success`；下一次触发为2026-09-08 18:20/18:30 CST。

## 禁止事项

- 禁止把记忆摘要中的旧状态写成“当前正常”。
- 禁止用旧交易日数据冒充当天结果。
- 禁止在规则冲突时静默选择其中一套。
- 禁止回退到V1.x或旧融合结果，除非用户明确决定。
- 禁止把历史文件中的观察池数量、timer时间或模型版本当成永久常量。
- 禁止在本文件中保存API Key、Token、Cookie或其他凭证。

## 更新规则

发生以下任一事项时必须更新本文件：

- 用户确认新的模型、评分、股票池或输出规则；
- service/timer时间被修改；
- GitHub发布路径、schema或stage发生变化；
- ChatGPT定时任务被创建、修改、暂停或恢复；
- 故障根因查明或修复完成；
- 发现本文件与实时配置不一致。

每次更新应在下方追加简短记录，不覆盖历史结论。

### 第1项全链路验收

- 验收时间：2026-09-02 01:47 Asia/Shanghai。
- 目标交易日：20260901。
- 输入bundle SHA256：`d31926dab165b33b2f62d20075386cd5f700e6a674822e9070a5f3de7ec8d4f0`。
- 输入链路：服务器补生成记录、GitHub manifest、latest与日期归档均通过校验。
- 正式结果：`review/2026/09/key10_review_20260901.json`，包含池内30只、池外10只完整记录，最终池内10只、池外3只。
- 日期回执：`delivery/2026/09/key10_review_receipt_20260901.json`。
- latest回执：`latest/key10_review_receipt_latest.json`。
- PNG：1024×2048，已生成并按原始尺寸完成视觉检查，随任务最终消息交付。
- 结论：第1项全链路验收完成；服务器生成→GitHub发布→ChatGPT读取→终审JSON→PNG→持久回执→可见交付已贯通。

## 变更记录

- 2026-09-04：发现此前20260903 PNG存在英文音译、部分中文缺失及样式未固化问题；已依据正式终审JSON重新生成中文深蓝金融仪表盘PNG，尺寸1024×2048，视觉检查通过，SHA256=`2d7cc2af9c429df3b8a12aad3b96b27ff62eb4042042862e20c3dbfb7cd8b8a8`，并将样式契约固化为`v2.0-dashboard-20260904`。

- 2026-09-02：在用户确认完全替代后，从数据门槛检查点恢复并完成20260902终审；结果JSON为`review/2026/09/key10_review_20260902.json`，池内10只、池外3只，PNG为1024×2048且视觉检查通过，日期与latest回执已写入。

- 2026-09-02：用户明确确认V2.0完全替代V1.1风险层、V1.2风向层及任何影子层；解除规则冲突，恢复主终审并从20260902阶段A继续。

- 2026-09-02：主终审以primary/attempt_no=0运行；20260902数据门槛及三件套SHA校验通过，但因PROJECT_STATE既存的V1影子层与V2.0替代关系冲突未解决，未进入阶段A，写入不可重试失败回执并暂停主任务。

- 2026-09-02：完成第1项全链路验收；使用20260901权威数据包完成输入一致性校验、40只候选终审、正式JSON、PNG、日期与latest双回执及可见交付。

- 2026-09-01：建立长期事实源；记录V2.0已确认规则、GitHub与定时任务实时快照；保留时间轴和V1.x/V2.0替代关系冲突，等待验证。
- 2026-09-01：已修改启用中的“V2.0 选股终审”任务，强制每次先读取本文件，并在规则或实时状态冲突时停止推断、明确报告。
- 2026-09-02：完成第7项修复；建立Windows桌面版Codex经 `stock-server` 实时核验服务器的通道，并验证主机、目录、systemd、service和timer。
- 2026-09-02：完成第6项结构优化；V2.0终审改为分阶段增量流程，先JSON后PNG、按板块批量取证、复用可验证静态资料、隔离制图失败并强制可见回执，评分与输出规则不变。
- 2026-09-02：完成第5项调度监控修复；主任务重新锚定至交易日20:00，并新增20:20独立漏跑检查。`next_run_time=null` 被确认为不可用的状态字段，不再单独作为健康证明；改以实际 `last_run_time` 与漏跑告警闭环验收。一次性验收任务已实际触发并自动停用。
- 2026-09-02：完成第4项交付保障修复；主任务新增正式结果JSON、日期回执和latest回执的持久交付协议，回读成功且任务会话可见回复后才允许判定完成；新增交易日23:30独立交付复核，正常与异常均发送可见回执。仓库新增 `delivery/README.md` 固化协议。
- 2026-09-02：完成第3项有限重试修复；新增共享工作流 `V2_REVIEW_WORKFLOW.md`，主任务attempt_no=0，交易日21:00和22:00最多两次条件重试。成功结果判重后停止，瞬时错误单次调用最多重试1次，跨时间重试从检查点恢复，并以90分钟运行锁避免并发重复；第二次仍失败则停止并记录人工恢复点。
- 2026-09-02：完成第2项修复；查明17:30早于18:15数据完成门槛，改为日更18:30、GitHub补偿发布18:45，并安全补生成、发布及远端回读验证2026-09-01数据。
- 2026-09-02：按用户最新确认固化V2唯一权威：V2.0完全替代V1.1/V1.2，取消影子层，旧版本仅归档；清除由旧替代关系产生的重试分类冲突，保留20260902既有回执与运行历史。
- 2026-09-04：发现旧版“V2.0 选股终审”和有限重试调度记录已不可更新；已新建并启用 V2.0 主终审（交易日20:00）与有限重试（交易日21:00、22:00），并将终审漏跑监控改为检查新主任务名称。
- 2026-09-04：按用户要求将V2.0主终审调整为17:00，第一次重试17:30，第二次重试18:00，交付复核18:50，全部早于19:00；独立漏跑检查改造为第二次重试；标普任务保持不动；非V2任务全部停用。

- 2026-09-04：根据完整当日行情可靠获取门槛18:15完成最终时间校准：ECS量化18:20、GitHub发布18:30、ChatGPT终审18:35、有限重试18:45/18:55、交付复核18:58，最晚不超过19:00；标普任务未改，其他任务已停用。ECS timer、service描述及V2.0配置调度字段已同步；正式JSON/PNG生产责任仍归ChatGPT终审任务。
- 2026-09-04：完成一次性V2.0端到端验收，目标交易日20260903；输入manifest/latest/权威归档一致且future_data_rows_used=0，完成40只候选终审、正式池10只、外部池3只、正式JSON、1024×2048 PNG、日期回执、latest回执及运行锁回读校验。结果JSON为`review/2026/09/key10_review_20260903.json`，PNG为`review/2026/09/key10_review_20260903.png`；本次不含漏跑检查和重试。验收后已恢复`终审交付复核`。


## 2026-09-06 故障修复（当前有效）
9月4日输入有效，main只持久化input_gate运行锁；本地草稿把缺失财报统一赋基本面3分，并将量价共振计为重大利好，证据校验不通过，不得发布为正式JSON/PNG。已关闭过期锁并写入失败双回执，原中断机制与准确结束时间未知。恢复起点为阶段B一手取证。工作流已改180秒租期、60秒续租、分批持久检查点及写入归属检查；主任务/两次重试截止18:44/18:54/18:58，保持原启动时间。配置修复不等于9月4日终审成功，需端到端验证。


### 大文件校验安全规则（2026-09-06新增）
- 禁止把完整 JSON、PNG 或任何大文件内容插入 shell 命令、python -c 参数、URL、环境变量或工具参数中的命令字符串。
- SHA256 必须对已落盘文件按文件流计算，例如 `sha256sum /path/to/file` 或 Python `hashlib.file_digest(open(path, "rb"), "sha256")`；结果只传递短哈希字符串。
- GitHub 文件先保存到临时文件，再计算原始字节 SHA256；不得用 JSON 解析后重新序列化的内容代替原始字节校验。
- 若本地无法安全落盘或流式读取大文件，立即写“执行失败”、failure_stage=`input_sha256_calculation`，不得进入终审，也不得把参数长度错误归类为数据或权限失败。


### 2026-09-06 修复后历史恢复验收

- 目标交易日：20260904；输入门、终审、JSON、PNG、双回执与锁释放均已回读通过。

- 2026-09-06：用户同意第1–5项修正、暂缓历史验证；完成第1步“研究价值与买入时机分离”。将七项V2分数保留为综合质量/入选资格分，新增selection_axes（研究价值0–30、市场背景0–70）和买入时机状态字段；本步不改分、不改排序、不改池规则，不改ECS量化脚本、板块算法、KDJ/动量阈值或PNG版式。已同步V2_REVIEW_WORKFLOW.md及V2.0主任务、两次有限重试提示词；服务器实时状态未在本会话验证。

- 2026-09-06：完成第2步审查侧修正。终审读取当前/前一交易日板块摘要，按排名变化、强度变化及加速度/广度/资金/量能确认建立warming/leading/neutral/cooling分层；对现有候选增加rotation字段，并为合格warming候选预留最多10只正式池中的至少3个名额（不足按实际合格数折减），不降低V2分数/数据门槛、不凑数。由于当前工作会话无法实时访问ECS，轮动专用上游候选槽位扩展（2B）未部署；本步未修改ECS脚本、KDJ/动量阈值或PNG版式。

- 2026-09-06：用户指出板块限制此前已删除。经当前V2记录核对，确认所有行业均可参与扫描和轮动分层；已删除本文件、V2工作流和3个活动终审任务中“待确认/不恢复旧排除项”的模糊口径，明确白酒、证券、银行等不再自动排除。此前将其列为未确认项属于记忆状态误判，已纠正。

- 2026-09-06：完成第3步修正（3A审查侧）。加入标准KDJ(9,3,3)字段契约、早期拐点/趋势确认/扩张/回调状态表及KDJ缺失时的扩张护栏；护栏只改变买入时机标注，不改V2分数、排序、准入、板块范围或PNG。实时bundle尚无KDJ，3B ECS字段补齐待服务器连接后实施；历史验证仍暂缓。
- 2026-09-06：补齐第3步边界：扩张护栏改为无论KDJ是否完整均评估；高位死叉与“显著高于MA20”阈值改为可计算条件，避免趋势确认状态吞掉明显回调风险。分数、排序、准入和全行业规则不变。
- 2026-09-06：修正KDJ契约的J值范围。K/D限定0–100，J按标准公式保留可超出区间的有限值，并固定RSV9、K/D递推、J=3K-2D、首个值50及零区间处理，避免把标准J极值误判为坏数据。

### 2026-09-06 ECS 第3B代码层执行回执

- 执行状态：部分完成；仅修改当前V2上游源码并通过单元校验，未生成生产bundle，未发布GitHub。
- 连接方式：Alibaba Cloud Workbench CLI；实例`i-uf6fp4j1j3s3wlgdtzfy`，区域`cn-shanghai`；目录`/opt/stock-automation/scanner`。
- 修改文件：`stock_key10_v2_scoring.py`、`stock_key10_v2_daily.py`；配置、历史输出、service、timer均未修改。
- 主备份：`/root/stock-key10-backups/step3b_20260906T141321Z/`；语义修正增量备份：`/root/stock-key10-backups/step3b_refine_20260906T141939Z/`。修改前与当前SHA由桌面Codex回执保存；未读取或提交凭据。
- 校验通过：语法、窗口不足、平窗RSV=50、初始K/D=50、金叉/死叉/斜率/年龄、范围/有限值、未来数据守卫、候选覆盖和单元测试。
- 单元候选提取覆盖：官方30/30、池外10/10，每条14个KDJ字段；这不是生产bundle覆盖。
- GitHub latest仍为`trade_date=20260904`、bundle SHA=`c2d1d2440003d14f41b33cf3bfdf6ebc9773c59eb59f54c533fadb7c370c8b3a`，生产候选KDJ仍为0/40；未发布旧产物。
- 阻断：项目目录无`AGENTS.md`、无`.git`，无法验证Git clean状态；项目也未提供安全dry-run参数。人工恢复起点为确认可写入临时目录的V2 dry-run方式，先验证真实30+10候选KDJ覆盖及评分/排序不变，再调用既有bundle发布流程。


### 2026-09-13 PNG 字形后端重设计（当前有效）

- 用户反馈上一次修复的 PNG 仍然模糊且出现繁体/日文笔画风格；经核验，问题来自 `kanjivg_vector_atlas_v1`，不是 V2.0 JSON 或评分。
- 已退役 KanjiVG 图集路径，改用 `wqy-microhei.ttc` 的真实中文轮廓字体（简体字形），并以 4 倍超采样后一次性缩小生成最终 PNG。
- 已对实际显示字符完成 cmap 预检，并对“为、买、亚、环、际、领、闽”完成简繁差异字预检；正式 JSON、评分、池归属和固定版式不变。
- 20260911 PNG 已按新后端重生成，尺寸 RGB 1024×2048，完成原尺寸视觉检查；日期回执与 latest 回执需指向新 PNG 并保留旧修复历史。


## 制图传输与验收契约（2026-09-13生效）

独立制图验收已通过，范围仅为已验证正式JSON到PNG、仓库写入回读和视觉检查，不代表重新终审或未来准时性保证。机器契约：`render_assets/render_contract_v2.json`；验收报告：`render_assets/acceptance/20260911/20260913T134742Z/acceptance_report.json`。

- 必须从同一main基准commit通过GitHub连接器读取契约、渲染器及字体传输文件，不使用shell Git或凭据。
- 字体使用`render_assets/font_transport/manifest.json`列出的18个小型UTF-8分块；逐块验证长度/SHA256，运行同目录`restore_font.py`还原完整TTC，再验证固定完整SHA256。禁止空内容继续、系统字体回退、KanjiVG或旧本地字体充当验收输入。
- 原始JSON/文本哈希敏感读取：先取得文件Blob SHA，再通过`github_fetch_blob`读取UTF-8原文，保留末尾换行；使用文件写入工具或标准输入安全落盘后流式算哈希。不得直接对`github_fetch_file`可能归一化的字符串宣称原始字节已校验，也不得猜测增删换行来满足哈希。
- 渲染入口改为`render_assets/render_v2_verified.py`，传入`--input`、权威`--input-sha256`、`--output`、`--next-trade-date`。入口强制检查输入/字体/渲染器哈希、RGB 1024×2048和输入未变；任何预检失败立即停止。
- 固定后端`wqy_microhei_simplified_font_supersampled_4x`，4倍绘制后一次Lanczos缩小。仍需原尺寸目视核验简体、清晰、无缺字方框/重叠/截断，逐只核对JSON；程序仅输出`visual_check=pending`，不能自动宣称视觉通过。
- PNG通过连接器二进制base64读取回读，只去除传输换行后比较原始字节。仓库提交按最新基准commit非强制推进，冲突停止。所有已有评分、日期时限、租约、交付闸门、池归属和排序规则继续有效。


## 2026-09-14 终审取证链路修复

- 根因确认：当日主任务在短时窗口内从零检索40只候选，仅完成2只部分证据；失败点是阶段B吞吐和错误的不可重试分类，不是输入、评分、字体或制图故障。
- 当前规则：公司业务与最近一期定期报告建立按股票代码持久证据缓存；日常运行复用可回读且截止时间合格的基线，只增量核验新报告、当日公告、风险及事件。缓存不能替代来源或生成默认分。
- 阶段B每批最多10只持久化；主任务、第一次重试和第二次重试均可从首个未完成批次继续。阶段B证据覆盖不足标记为retryable；不可重试门槛保持不变。
- 运行时间、V2评分、候选数量、排序、风险、KDJ及PNG规则未改变。


## 2026-09-15 输入传输与增量进度修复（当前执行约束）
- 不改变评分、候选数、市场范围、产物责任、任务启用状态和计划时间。主运行收尾18:48/截止18:49；第一次重试收尾18:54/截止18:55；第二次收尾18:57/截止18:58；复核18:59且严格只读。
- 输入门槛通过后立即完成并持久化阶段A。不能因为预计整条链路来不及完成就跳过当前能完成的最小步骤。阶段B每批最多10只（不是必须凑齐10只）；收尾前保存已完成的单只记录与待补项，由下一次从首个未完成代码恢复。不得伪造评分、降低证据标准或越过硬截止。
- time_gate_before_stage_A、B_evidence_coverage、尚未完成阶段的时间窗口耗尽可在剩余重试次数内恢复，阶段A缺失不是禁止重试的理由。输入哈希不一致、鉴权、schema/model、未来数据及真实规则冲突仍不允许重试。第二次实际尝试失败标记retry_exhausted=true；当天耗尽不等于永久禁用每日任务。
- 所有尝试必须先取得并回读唯一owner_token及generation；每次共享写入检查租约与最新main。运行锁和共享结果用同一基准commit原子提交，非强制推进main。没有实际取得租约不得虚构新generation或写“锁已释放”。过期执行者只能停止共享写入并在会话报告，不得覆盖新owner。复核任务只核验，不负责补写。
- started_at、heartbeat_at、finished_at来自实际时钟；禁止预填或倒填固定时间。回执中的input校验若来自前次检查点，明确标记复用及检查点路径/Blob SHA；只有输入版本绑定仍匹配且检查点可读时才复用，不能声称本次重新计算过。复核的finished_at应注明“回执记录的结束时间”，不等同成功完成时间。
- 安全接收工具为delivery/tools/receive_github_blob.py，调用参数只有输出路径、UTF-8字节数、Git Blob SHA、权威SHA256。原文只能来自github_fetch_blob，通过标准输入传送，不插入命令字符串、不修改末尾换行。接收器精确读取指定字节数后退出，不等待EOF；验证Git Blob SHA和文件流SHA256，失败不留下输出，不覆盖已有文件。
- 本环境非TTY执行的stdin会立即关闭。使用exec_command的tty=true，先运行stty raw -echo再启动接收器，取得session_id后将原文通过write_stdin的chars原样传入。禁止使用默认canonical/echo终端传送大文本。单次接收超时30秒，超时停止，不反复猜测字节。UTF-8字节数来自权威size或原文UTF-8长度，非JS字符串字符数。
- 读取PROJECT_STATE与工作流后，按需读当前输入/回执/检查点。工具发现只列相关名称和必要签名，不输出全注册表、不读取旧任务正文；大包只传递给接收器与解析程序，返回短校验摘要。

修复验收：接收器6项单元测试通过；20260915真实GitHub Blob经raw/no-echo stdin接收255487字节，Git Blob SHA与流式SHA256一致。当天仍未完成终审，不改写失败回执；未来完整端到端及准时交付尚待验证。


## 2026-09-15 历史全链路验收未通过
- 验收标识：acceptance-20260915-1789480212449；交易日20260915；这是截止后历史验收，不代表当日准时交付。
- 输入原文传输、SHA256、V2字段、30+10候选、KDJ快照检查与阶段A持久化回读已通过。阶段B首批证据覆盖未通过，正式JSON/PNG未生成。
- 失败阶段：B_evidence_coverage；恢复路径：`delivery/acceptance/20260915/acceptance-20260915-1789480212449/stage_B_batch_1.json`，首个未完成代码600176.SH。
- 额外发现：20260911正式结果的40只逻辑纯度依据均为量化走势，预期差缺证记0，不可直接当作当前合格公司证据缓存；历史结果不改写。固定渲染器对理由/风险显示存在静态契约偏差，需在合格正式JSON之后修复及视觉验收。
- 验收报告：`delivery/acceptance/20260915/acceptance-20260915-1789480212449/acceptance_report.json`。未追加“全链路验收完成”，未改写生产日期/latest回执，未执行交易。

## 2026-09-15 交付方式修订
用户确认暂时取消制图，改为文字报告。同步V2工作流及主运行、两次重试、只读复核提示词；保持评分、证据门槛、计划时间和启用状态。此变更仅为流程配置，不代表新选股结果或端到端验收已完成。历史JSON/PNG及生产回执不回写。


## 公司证据存储方式（2026-09-15用户确认）
- 证据库仅持久化结构化事实摘要和来源元数据：股票代码、资料类型、原文URL、原始发布日期（保留日期精度）、报告期、事实摘要、页码/章节定位（如可得）、最近核验时间及待补项。评分记录引用证据，不能只缓存旧分数。
- 不持久化PDF附件、网页快照、截图、完整HTML或完整原文副本，不为这些内容新建对象存储。必要核验时按链接读取原文；解析所需临时内容不作为归档保存。此限制针对外部证据原件，不影响量化包、正式JSON、文字报告和检查点持久化。
- 首次录入必须真实读取并核验来源，不能只凭链接或搜索摘要认定证据合格。已有合格业务/财报基线允许按有效性规则复用；来源失效、事实冲突、报告更新或需要复核时重新读取，日常仍检查新增报告与公告。不把“无本地PDF/快照”列为缺失或失败条件；无法核验必要事实时记录待补，不填默认分。


## 2026-09-15 候选来源改造（用户已确认，代码待生产接入）
- 用户确认优先改候选生成：先行股、细分题材多股联动、已确认跟随三路召回，以上涨机会为研究目标；纯资金推动可作为来源理由，不伪装为公司基本面或重大利好。
- 实现及接入契约见candidate_assets/README.md；候选模块candidate_assets/candidate_sources_v2.py，8项合成单元测试通过。三路轮询去重后保留原30+10上限，不改变七项权重、60分/完整性门槛、最终排序、池外准入、KDJ和风险规则；交付继续文字模式。
- 模块须在ECS完整扫描宇宙截断前调用，不能仅重排既有40只；新诊断字段不会自动产生买入信号。首版召回参数尚未经真实输入或收益验证。
- 部署状态：未接入ECS，未启用新生产候选生成；当前会话无ECS执行通道，仓库未含当前daily/scoring生产源码。须完成真实源码适配、带日期题材映射、完整行情干跑和文字交付验收后更新状态。现有bundle、正式历史结果、任务计划及启用状态未改；缺新字段不得误判旧V2包无效。

## 2026-09-15 GitHub固定commit同步通道（已验证）

- 桌面端 GitHub 连接器当前可用，账号标识为 `zippobb`；目标私有仓库可读，默认分支为 `main`，连接器返回的仓库权限包含 `pull`。本次未读取或输出任何 token、私钥、Cookie、`.env` 或认证文件。
- 已记录的失败类型彼此独立：桌面端此前 `git clone` 的准确错误为 `Failed to connect to github.com:443 after 21096 ms`（TCP 连接超时）；ECS 通过 SSH Git 的准确错误为 `git@github.com: Permission denied (publickey).`（SSH 公钥认证失败）；ECS 在禁用交互提示后使用 HTTPS Git 的准确错误为 `fatal: could not read Username for 'https://github.com': terminal prompts disabled`（私有仓库的 HTTP 凭据不可用）。DNS 当前可解析，桌面端和 ECS 的 GitHub API HTTPS 探测均返回 HTTP 200，未复现 DNS、TLS 或代理错误；两端未配置需要清理的 Git/环境代理。
- 已从 `main` 解析并固定 commit `f63f805037561ac0984efb1eda1879f643d560a7`，按该 commit 的 blob SHA 保存并校验：`PROJECT_STATE.md`（`fc24de8e75d4c37793b009c7d33fa449c488507f`）、`V2_REVIEW_WORKFLOW.md`（`9c806c4287cf44f8040244af747cc778406dfc98`）、`candidate_assets/README.md`（`5a9527b528def66921536fbb7877b1d90ce1b097`）、`candidate_assets/candidate_sources_v2.py`（`f60f63400b7a80be3e9e36de16e04eade7f09dfd`）和 `candidate_assets/test_candidate_sources_v2.py`（`c03766988d2a67c4188035a24bee601c3dd83bca`）。
- 离线包保留在桌面项目的 `offline/stock-key10-bridge-f63f805037561ac0984efb1eda1879f643d560a7/`，清单记录每个文件的字节数和 SHA256；本次包内五个文件的 SHA256 分别为 `ac79563eb23405bad3dacd74b704d09bb4eab53b5fd99e9c52cc6f0061965e80`、`8b0b5abf4c607a5a0514b130a2940b7261d120b50f82409bc765765ef5fa5a55`、`028cf9e8a8ba215c4c0080fd079e211314f8e5dc82cf6ee5fa950b3b7cb730d7`、`2e7bda17b938fab629ec17d5bcf426eeb42944d87acfa42a02cc079fc90a9038`、`cf1a05919f98ae4a3c012a98742c3314232588edaef8a3965118831d7c4e46e0`。
- 新增标准工具：`delivery/tools/sync_candidate_assets_to_ecs.ps1`、`delivery/tools/verify_candidate_assets_stage.py` 和 `delivery/tools/SYNC_CANDIDATE_ASSETS.md`。工具先校验固定 commit 清单，再通过现有 SSH 别名/SCP 写入 `/opt/stock-automation/staging/stock-key10-candidate-assets/<commit>/`，远端只读 verifier 逐文件校验后结束；不会覆盖 `/opt/stock-automation/scanner`、生产 latest、历史 archive，也不会启动或重启服务。
- 本次实际暂存路径为 `/opt/stock-automation/staging/stock-key10-candidate-assets/f63f805037561ac0984efb1eda1879f643d560a7/`，远端 verifier 在新 SSH 会话中返回 `VERIFY_PASSED`，5/5 文件哈希一致。当前候选来源模块仍未接入 ECS 生产日更；本次只建立并验证源码同步通道，不改变选股策略、任务、调度或发布协议。

## 2026-09-16 V2三路候选生成ECS接入（代码已部署，待自然交易日验收）

- 固定同步资产先按 `f63f805037561ac0984efb1eda1879f643d560a7` 完成审查；审查发现的确定性边界问题已在当前main修正，修正后的候选模块来自 `488e70a17872adf68e7b228c45b27d281e09f5c6`。提交 `aed372b597d960c12d7ecf77cd6b1f21a6307e6a` 仅为通道状态提交，不作为源码版本。
- 已部署文件：`/opt/stock-automation/scanner/stock_key10_v2_daily.py`（部署前SHA256 `5591213118263c0fc6a49e405237b849d601db7c93aea3661940d4d7e0fdcbe2`，部署后 `91859c21d11edf2b408e3c62d34dbada6e962f4ae3699c8ebd6210a3d85aff83`）；新增 `candidate_assets/candidate_sources_v2.py`（`4669988110033de3b5d7cc90524ecee47a085366895e58431ed2a9ac47f0fbdf`）及其测试（`6a1e3a8cce16fb5e13165b6de8bb41d49ef8667d7b69c5d17b5ad49d43ba62dd`）。
- 备份目录：`/root/stock-key10-backups/step_candidate_sources_20260915T164450Z/`；其中 `backup_manifest.txt` 记录部署前SHA、systemd单元SHA及新增文件部署前不存在。生产入口在旧30/10截断前把完整 `scored_official`/`scored_external` 传入 `build_candidate_source_output`，再分别执行池内30和池外10限制；没有仅重排原40只。
- 代码/测试校验：修正后候选模块12/12合成单元测试通过，远端语法检查通过；服务入口未执行。真实隔离干跑使用现有20260915缓存（无下载、无回测、无生产写入）：官方扫描237、资格82，外部扫描439、资格215，官方/外部输出30/10，候选代码去重且跨池不重叠。
- 干跑结果：`candidate_generation_version=v2_candidate_sources_20260916`，14个KDJ字段覆盖40/40且`kdj_data_complete=40/40`，K/D范围、J有限、斜率/交叉/年龄有效；`future_data_rows_used=0`、`schema_version=2.0.0`、`model_version=V2.0`、`stage=21:30_quantitative_review_payload`、`excluded_sectors=[]`。全量扫描输入行深拷贝校验无突变；新输出候选逐代码评分、KDJ及`quant_rank`与原评分帧一致。
- 新旧差异：官方新增13、移出13；外部新增7、移出7。路由计数为先行股118、题材联动0、确认跟随0，题材统计0，`coverage_gap=true`；ECS未发现有可靠、带生效日期的题材成员映射，因此本次不得宣称三路完整覆盖，资金/量价依据仅作候选来源诊断，不转为评分或买入信号。干跑耗时约53.935秒，使用43个基准交易日及`raw_tushare_unadjusted_close_volume`口径。
- 接入后实时复核：生产latest仍为`trade_date=20260915`、SHA256 `35b65e03e527b503ee5130823816419db4b8de15af97c82e2b0669918d49ca05`，未覆盖latest或历史归档，未发布GitHub。daily/publish服务均inactive；两个timer仍enabled/active。daily.service、daily.timer、github-publish.timer文件SHA分别保持为 `b5561741ea9121ace2f863baf026ecc3a7e13de2cd84e7cba7ab772ecf3a7106`、`7339f29ccfca73406dcdba2d9998d4aaf8aaeac034250966c784ac56828527f7`、`1e51f2eb3c89b6c650613964af3a6eaeda00f23137b5dcbad33223d32223d35a`。
- 当前判定：代码已部署、合成测试和真实隔离干跑已通过；端到端生产候选包尚未由自然交易日运行生成，故不得报告为正式三路验收完成。下一自然交易日需只观察日更/发布链路，核验新字段、30+10、分数/排名/池归属不变及题材覆盖缺口；不修改任务时间、服务或历史结果。

## 2026-09-16 候选模块文档校正

- 为避免模块文档仍写“integration pending”造成职责歧义，仅修正 `candidate_assets/candidate_sources_v2.py` 模块文档字符串；执行逻辑、测试语义和候选结果不变。修正后main提交为 `739f87bb794bcaa5e5d55ec6767f6c608b68353c`，ECS最终源码SHA256为 `60f25f3015c15236b766f3c261d127d7d5f21b97a06ac64cc6b9d29908449943`。
- 修正前ECS源码已备份至 `/root/stock-key10-backups/step_candidate_sources_refine_20260915T165549Z/`，备份清单记录修正前SHA256 `4669988110033de3b5d7cc90524ecee47a085366895e58431ed2a9ac47f0fbdf`、修正后SHA256及systemd单元哈希。12/12单元测试、语法检查和使用生产源码的20260915真实隔离干跑再次通过；生产latest仍未改变。
