# V2.0 选股终审工作流

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


> 本文件是主运行与重试运行共用的持久工作流。V2.0完全替代V1.1风险层和V1.2风向层；影子层已取消。V1.1、V1.2及影子层仅为归档资料，不得参与运行或触发规则冲突。当前V2规则冲突时以PROJECT_STATE.md和用户最新明确决定为准。

执行“V2.0 选股终审”。这是同一任务会话中的增量工作流，必须按阶段执行，禁止把所有检索、评分、制图混成一次不可恢复的长步骤。

一、事实源与数据门槛
1. 先完整读取私有GitHub仓库 zippobb/stock-key10-bridge main分支的 PROJECT_STATE.md。对话记忆只作线索；仅当PROJECT_STATE.md、用户最新明确决定或实时数据中的当前V2信息互相冲突时，醒目标记并停止相关推断。V1.1、V1.2及影子层记载一律视为归档，不得要求单独确认替代关系。
2. 读取 latest/manifest.json、其archive_path权威归档和 latest/key10_bundle_latest.json，核对trade_date、schema、model、stage、SHA256及内容一致性。
3. 只接受当天北京时间交易日、schema_version=2.0.0、model_version=V2.0、stage=21:30_quantitative_review_payload、future_data_rows_used=0的数据。任何一项不满足，立即输出“数据未就绪”及具体缺失项；禁止回退V1.x、旧融合结果或旧交易日。

二、分阶段增量核验
阶段A——建立候选表：从量化包读取池内最多30只、池外最多10只及市场适配、板块、技术三项量化分。先生成候选代码、名称、板块、量化分和bundle哈希的结构化工作表。
阶段B——批量取证：按板块/产业链分组检索，产业与政策证据同组复用，避免对每只股票重复搜索相同事件。公司层面优先交易所公告、公司公告、定期报告等一手资料；通常每家公司最多保留两项决定性来源，只有来源冲突、重大风险或重大利好需要追加。
阶段C——增量复用：对本任务会话最近已核验且公司主体未变的业务逻辑、逻辑纯度依据和基本面基线可以复用，但必须保留原来源与evidence_as_of。市场、板块、技术、当天公告、当天业绩变化、重大事件和风险必须按本交易日重新核验；资料过期、来源缺失或出现新公告时不得复用。
阶段D——先评分后写作：对池内最多30只和池外最多10只逐只形成紧凑JSON记录，不为未入选股写长篇文字。字段包括logic_purity、elasticity五项明细及合计、fundamentals、major_positive、risk_annotations、main_risk、evidence_as_of、sources和data_completeness。
阶段E——计算与校验：按市场适配5、板块15、技术30、逻辑纯度15、弹性20、基本面10、重大利好5计算100分。弹性五项各0–4且合计必须等于elasticity。单项缺失时仅在可用权重达到70%时等比例还原至100分，否则标为数据不完整且不得正式入选。综合分低于60不入选。重大风险只作文字标记，不扣分、不否决、不改变分数或排序，不生成PASS/WARNING/FAIL。
阶段F——排序输出：池内按综合分、技术分、弹性分、板块分、逻辑纯度、ts_code依次排序，输出0–10只，不凑数。池外仅保留强势板块且该板块不存在任何池内综合分不低于60的合格个股时的候选，按相同顺序输出0–3只；不得与池内混排，也不得自动加入正式股票池。

### 第1步修正：研究价值与买入时机分离（当前生效）

本步只做“研究价值”和“买入时机”的职责分离，不提前改动板块算法、KDJ阈值、动量阈值、候选数量或PNG版式。

1. 现有七项评分及`final_score`继续作为V2.0的综合质量/入选资格分：市场适配5、板块15、技术30、逻辑纯度15、弹性20、基本面10、重大利好5。它不是买入信号，也不得把高综合分直接表述为“次日可买”。
2. 每个池内、池外候选记录增加`selection_axes`，至少包含：
   - `research_value_score`（0–30）：逻辑纯度15 + 基本面10 + 重大利好5。缺项不填默认分；按该轴可用权重达到70%时等比例还原，否则记为数据不足并记录缺失证据。
   - `market_context_score`（0–70）：市场适配5 + 板块15 + 技术30 + 弹性20。它是当前综合分的市场/交易背景拆分，不是新的加权分；缺项沿用V2可用权重规则。
   - `entry_timing_status`：只能取`可关注`、`待确认`、`不追高`、`数据不足`之一；同时写`entry_timing_basis`和`next_day_action`。判断必须引用当日量化快照、市场/板块证据和执行纪律，不得仅由`final_score`或单一指标推断。
   - `entry_timing_score_effect=0`、`entry_timing_rank_effect=false`：本步时机状态只作独立标注，暂不改分、不改排序、不改变池内/池外准入。
3. 正式JSON必须同时保留七项原始分、`final_score`和上述`selection_axes`。正式池仍按现行V2顺序排序，PNG仍按现行固定样式生成；卡片可继续展示现有字段，时机状态先以JSON和最终文字回执为准。
4. `entry_timing_status`不得与重大风险混用：重大风险仍只作文字标记，不扣分、不否决、不改变排序。后续第2–5步将分别处理板块驱动、动量/KDJ、阶段状态和输出数量；在对应步骤获确认前，不得把这些规则提前写入本步。

### 第2步修正：板块轮动驱动候选来源（审查侧当前生效）

本步先在终审侧使用现有量化包实现板块轮动分层；不修改七项评分、排序字段、KDJ/动量阈值、候选数量或PNG版式。由于当前bundle只提供池内30只和池外10只，未提供额外“轮动专用候选槽位”，本步不得凭空新增股票；上游候选扩展另行处理。

1. 轮动数据：读取当前bundle的`sector_summary.all_sectors`，并读取`previous_trade_date`对应的权威bundle，按`industry`对齐。计算`sector_rank_delta=previous_rank-current_rank`（正数代表排名上升）和`sector_strength_delta=price_strength_norm-previous_price_strength_norm`。前一交易日板块缺失时，相关字段记为null，`rotation_state=data_insufficient`，不得用当日排名替代跨日变化。
2. 分层口径（优先级从上到下，避免同一板块多层归类）：
   - `warming`：`sector_rank_delta>=10`或`sector_strength_delta>=0.10`，且加速度、广度、资金、量能扩张四项中至少两项达到确认；无足够确认不得标为升温。
   - `leading`：未归入warming，且当前板块排名<=30、强度变化>-0.05、持续性>=0.60。
   - `cooling`：未归入前两层，且`sector_rank_delta<=-10`或`sector_strength_delta<=-0.10`。
   - 其余为`neutral`；历史行缺失为`data_insufficient`。
   其中确认项必须来自bundle的`acceleration_norm`、`breadth_norm`、`capital_norm`和`volume_expansion_ratio_pct`；对应阈值分别为0.60、0.60、0.60和50。
3. 每个候选增加`rotation_state`、`rotation_source_tier`（A=warming、B=leading、C=neutral、D=cooling、U=data_insufficient）、`sector_rank_delta`、`sector_strength_delta`和`rotation_confirmation_count`，并保留板块证据日期。
4. 正式池候选选择：先对现有候选完成V2终审和60分/数据完整门槛；若A层存在合格候选，正式池最多10只中至少保留`min(3,A层合格数)`只A层候选；A层不足时不凑数，可按B层、C层补足剩余名额。D层只能在没有合格A/B/C候选时作为最后备选。保留位不改变单只综合分，也不允许低于60分或数据不完整的候选入选。
5. 保留位确定后，池内最终展示顺序仍按综合分、技术分、弹性分、板块分、逻辑纯度、ts_code排序；池外仍执行原V2准入条件且独立展示。若当前40只候选中没有A层合格候选，记录`rotation_coverage_gap`，不得用旧日期、旧PNG或臆造股票填补。
6. 用户已确认V2取消行业/板块限制：所有行业均可进入轮动分层和候选来源，不执行白酒、证券、银行等旧排除清单。行业名称不得作为自动排除条件；候选仍须满足V2市场范围、数据完整、综合分门槛、弹性与风险核验等现行规则。
### 第3步修正：动量与KDJ时机层（3A当前生效，3B待ECS字段）

本步针对“高综合分/技术分但入选时点已接近回调”的滞后问题。时机层仅作独立标注，不修改V2七项评分、`final_score`、最终排序、60分/数据完整门槛、板块全行业范围、候选数量或PNG版式；历史结果不回写，历史验证（第6步）仍暂缓。

1. 3A审查侧输入与字段契约（当前生效）
   - 优先读取候选记录中的标准KDJ(9,3,3)快照；字段名固定为`kdj_k`、`kdj_d`、`kdj_j`、`kdj_k_prev`、`kdj_d_prev`、`kdj_j_prev`、`kdj_k_slope_3d`、`kdj_d_slope_3d`、`kdj_j_slope_3d`、`kdj_cross_state`（`golden_cross`/`death_cross`/`none`）、`kdj_cross_age_days`、`kdj_as_of`、`kdj_data_complete`和`kdj_source`。
   - K、D必须为0–100的有限数；J按标准公式保留为有限数，可低于0或高于100。斜率为最近3个交易日的点数变化，`kdj_cross_age_days`为非负整数，`kdj_as_of`不得晚于`trade_date`；全部字段合格时`kdj_status=complete`，任一必需字段缺失、越界或日期不合格时为`kdj_status=data_insufficient`。
   - 计算口径固定为：`RSV=(close-LL9)/(HH9-LL9)*100`；`K=2/3*K_prev+1/3*RSV`，`D=2/3*D_prev+1/3*K`，`J=3*K-2*D`。首个有效值`K=D=50`；9日窗口不足时标记数据不足，`HH9=LL9`时`RSV=50`；禁止引入未来数据。
   - 不得由RSI、MACD、收益率、乖离率或量能反推KDJ；缺失值不得填0、填50或沿用上一交易日。

2. 时机状态（字段写入`selection_axes`，不改分/不改排名）
   - `early_turn`：`kdj_cross_state=golden_cross`且`kdj_cross_age_days<=2`、K<60、D<60、J<90，K/J斜率>0且D斜率>=0；对应`entry_timing_status=可关注`，次日只观察回踩承接，不在高开长阳时追入。
   - `trend_confirming`：K>D、K<80、J<100，K/D斜率非负且未触发扩张护栏；对应`entry_timing_status=待确认`，等待板块与量价确认。
   - `extended`：K>=80或J>=100，或`kdj_cross_state=death_cross`且(K>=70或J>=90)，或触发扩张护栏；对应`entry_timing_status=不追高`，等待回撤/重新钝化。
   - `pullback_wait`：`kdj_cross_state=death_cross`（未达到`extended`条件），或K/J斜率均<0且`distance_ma20_atr>=1.0`；对应`entry_timing_status=待确认`，等待K/D止跌再评估。
   - `weak`：K<D且K/J斜率均非正，且未满足前述状态；对应`entry_timing_status=待确认`，暂不行动。
   - 以上条件按`extended`→`pullback_wait`→`early_turn`→`trend_confirming`→`weak`优先级判定；无法满足或数据不完整时为`data_insufficient`，对应`entry_timing_status=数据不足`。每条记录必须写`timing_state`、`kdj_status`、`entry_timing_basis`和`next_day_action`。

3. 审查侧扩张护栏（当前可执行、仅标注）
   - 无论KDJ是否完整都评估以下任一条件：`distance_ma20_atr>=1.8`；或`rsi14>=70`且`return_5d_pct>=8`；或`close_location>=0.90`且`volume_ratio_1d_prior_10d>=2.0`且`return_1d_pct>=5`。任一触发即写`timing_guard_triggered=true`及依据，并按状态优先级归入`extended`、标注`entry_timing_status=不追高`。
   - 护栏只影响`entry_timing_status`、`entry_timing_basis`和`next_day_action`，不改变任何V2分数、排序、准入、淘汰或风险文字；不得把护栏等同于否决。KDJ缺失且未触发护栏时保持`kdj_status=data_insufficient`和`entry_timing_status=数据不足`，不作推断。
   - 本护栏用于避免明显扩张位被误写成“可买”，不是历史验证或回测，也不改变第6步暂缓状态。

4. 3B ECS上游部署要求（已正式部署并通过首次自然交易日验收）

   - ECS量化包必须在正式池30只和池外10只的每条候选中输出上述KDJ字段，保持`future_data_rows_used=0`和现有stage/schema/model不变；同时保留第1步`selection_axes`和第2步`rotation_*`字段。
   - 2026-09-07首次自然交易日验收通过：18:20日更成功生成`trade_date=20260907`，18:20:54由`OnSuccess`发布到GitHub，18:30补偿发布按幂等规则成功跳过。
   - 官方30只+外部10只，KDJ 14字段覆盖40/40，`kdj_data_complete=40/40`；K/D范围、J有限值、斜率、交叉状态/年龄及公式全部通过；`future_data_rows_used=0`，`excluded_sectors=[]`；评分、排名、池归属、排序及板块摘要与原输入一致。
   - 本地bundle、GitHub archive和GitHub latest SHA256均为`43fb384b3361dfe0d5b98fbbcc95acc786e9ce7099c569b13ccb8fc46ad70cbc`，发布commit为`2cae467022df2f7635ffaaa211f484e48a77ee74`。20260904历史归档未触碰。
   - 后续运行必须重新读取当日bundle；3B不再是生产阻断。不得回写旧交易日结果；历史验证（第6步）仍暂缓。

5. 输出与验收
   - 正式JSON保留七项原始分、`final_score`、`selection_axes`、`rotation_*`和时机字段；`entry_timing_score_effect=0`、`entry_timing_rank_effect=false`继续固定为0/false。
   - PNG版式不变；时机状态先在JSON和最终文字回执中呈现。只有后续用户明确确认并完成单独部署/验收，才可讨论把时机状态转为分数或排序规则。

三、交付隔离
1. 必须先完成并自检正式结果JSON，再开始制图。JSON包含七项分数、五项弹性明细、风险标记、来源、公开信息截止时间、bundle哈希和future_data_rows_used=0校验。
2. 只为最终池内0–10只和池外0–3只撰写入选依据与主要风险；非入选股只保留紧凑评分记录和未入选原因。
3. JSON通过后生成一张1024×2048深蓝风格PNG，在同一张图分别展示池内与池外；移除V1.0/V1.1/V1.2、影子层、重点池、PASS/WARNING/FAIL字段。买点只作执行纪律说明，不计分。
4. PNG生成或视觉检查失败时，禁止重新执行阶段A至F；保留已完成JSON，单独重试制图最多一次。仍失败则输出“执行失败”、JSON结果和具体制图错误。
5. 任一阶段失败都必须输出可见回执，说明失败阶段、已完成阶段和可恢复起点。最终状态只允许“已完成”“结果未变化”“数据未就绪”“执行失败”。不得静默结束。
6. 同一trade_date和bundle哈希已有成功结果时，直接输出“结果未变化”并重新提供该日结果，不重复检索和评分。


四、持久交付与完成定义
1. 每次运行无论状态如何，都必须把机器可读回执写入私有GitHub仓库 zippobb/stock-key10-bridge。日期归档路径固定为 delivery/YYYY/MM/key10_review_receipt_YYYYMMDD.json，最新指针固定为 latest/key10_review_receipt_latest.json；若同路径已存在，先读取当前SHA再更新，禁止盲目覆盖。
2. 回执字段至少包含：run_date、started_at、finished_at、status、trade_date、bundle_sha256、completed_stage、failure_stage、missing_items、result_json_path、png_delivery_status、summary。status只允许“已完成”“结果未变化”“数据未就绪”“执行失败”。
3. “已完成”时，先将完整正式结果JSON写入 review/YYYY/MM/key10_review_YYYYMMDD.json，再写日期回执，最后更新latest回执；回执中的result_json_path必须指向该结果文件。只有结果JSON已写入且可重新读取校验，才允许把status写为“已完成”。
4. “结果未变化”时，回执必须指向既有且已核验的结果JSON；“数据未就绪”必须记录具体missing_items；“执行失败”必须记录failure_stage、已完成阶段和可恢复起点。
5. GitHub日期回执是本次运行的持久完成标记，必须最后写入。若回执写入或回读校验失败，最终状态必须改为“执行失败”，并在任务会话明确说明“持久回执写入失败”；不得宣称已完成。
6. 写入并回读日期回执后，仍必须在本任务会话发送可见最终消息。最终消息第一行必须是四种状态之一，随后给出trade_date、结果JSON路径、PNG状态或具体缺失/失败原因。禁止SKIP_COMPLETION、禁止只有内部结果、禁止仅写GitHub而不回复用户。

五、有限重试协议
1. 每个北京时间交易日最多执行1次主运行和2次跨时间重试。主运行attempt_no=0；18:50重试attempt_no=1；18:56重试attempt_no=2。禁止创建无限循环或超过2次重试。
2. 日期回执增加attempt_no、retryable、error_class、resume_from、retry_exhausted字段。每次尝试均以GitHub现有日期回执和结果JSON为检查点，使用SHA更新，禁止并发盲写。
3. 可重试错误仅包括：当天数据包尚未发布或仍为旧交易日、临时网络超时、限流、GitHub临时读取失败、单个来源临时不可用、PNG生成或视觉检查失败。鉴权/权限失败、当前V2权威规则之间尚未解决的冲突、schema/model不兼容、future_data_rows_used不为0、哈希不一致等属于不可重试错误，必须停止并明确报告。V1.1、V1.2或影子层的归档记载不构成规则冲突，不得据此失败、重试或暂停primary。
4. 同一次尝试内，单个瞬时失败的只读调用最多立即重试1次；已成功的调用和阶段不得重做。跨时间重试必须从resume_from继续：数据门槛失败从阶段A前重新检查数据；阶段B/C失败复用已保存的候选表和有效证据；阶段D/E/F失败复用此前已成功阶段；仅PNG失败时只重试PNG，不得重新检索或评分。
5. 每次重试前先检查当日日期回执、latest回执及结果JSON。若status为“已完成”或“结果未变化”且结果可回读，立即停止且不得重复计算；若同一bundle_sha256已有成功结果，同样停止。
6. attempt_no=2仍未成功时，写入retry_exhausted=true，状态为“数据未就绪”或“执行失败”，记录最后错误、已完成阶段和人工恢复起点；不得继续自动重试。
7. 没有执行重试时也必须输出可见状态，已有成功结果重新附PNG；一旦实际发起重试，无论结果如何都必须写入持久回执并发送可见状态。


六、并发锁与运行状态（2026-09-06修复）
1. 运行锁路径保持不变。使用唯一owner_token、generation、lease_expires_at、heartbeat_at、hard_deadline和completed_stage。租期180秒，每60秒及每个阶段持久化后续租，租期不得超过本次hard_deadline。主运行截止18:49，第一次重试截止18:55，第二次重试截止18:58，交付复核18:59；目标始终早于19:00。
2. 必须用现有文件SHA进行条件更新取得锁并回读owner_token。锁有效时只报告并发拦截，不覆盖原运行或日期回执，不把锁记录当作进程存活证明。失去租期的旧执行者必须停止写入；每次检查点或正式输出写入前重新核对owner_token、generation和未到期租期。
3. 过期锁可基于最新SHA接管，generation递增，保留previous_run摘要与接管原因。旧版无lease_expires_at锁按heartbeat_at+180秒识别过期。任何SHA冲突都停止当前写入并重新核验归属，禁止盲写。共享结果与锁的更新必须在同一基准commit上提交并非强制推进main，拒绝过期执行者覆盖新owner。
4. 阶段A及每批取证后立即将候选表、来源URL、原始发布时间、证据摘要及完成阶段写入delivery/checkpoints/YYYY/MM/<trade_date>/，回读后才更新completed_stage。正式JSON验证通过立即持久化，再制图。不能仅保存在临时目录或未挂到main的Git blob。
5. 主任务18:48、第一次重试18:54、第二次18:57开始收尾；到本次截止时停止新检索、保存已验证检查点，写失败回执并释放锁。运行中断后的过期锁由下一次重试从持久检查点接管。截止后只允许记录失败，不生成迟到的成功回执冒充准时完成。
6. 成功需JSON、PNG与双回执逐项回读，原尺寸视觉检查通过，并在最终消息实际附PNG。不能提前记录visible_completion=true；发送前只能记pending。
7. 证据质量门槛：格式非空不等于真实终审。禁止因无财报证据统一填基本面3分，禁止把量价共振计为重大利好，禁止仅凭价格乖离赋予预期差。公司业务、财务和事件评分须对应截止前一手来源和推理；缺失项标null及原因，按原V2可用权重>=70%规则处理，禁止补默认分。缺少必要证据且不满足权重门槛时不得发布正式名单。
8. 内容校验失败从取证阶段恢复，不能仅重传JSON/PNG。鉴权、哈希、未来数据及当前规则不兼容仍不可自动重试。活跃锁拦截须可见报告但不计次数；同日已有完整成功结果重新交付PNG，禁止静默。


### 大文件校验安全规则（2026-09-06新增）
- 禁止把完整 JSON、PNG 或任何大文件内容插入 shell 命令、python -c 参数、URL、环境变量或工具参数中的命令字符串。
- SHA256 必须对已落盘文件按文件流计算，例如 `sha256sum /path/to/file` 或 Python `hashlib.file_digest(open(path, "rb"), "sha256")`；结果只传递短哈希字符串。
- GitHub 文件先保存到临时文件，再计算原始字节 SHA256；不得用 JSON 解析后重新序列化的内容代替原始字节校验。
- 若本地无法安全落盘或流式读取大文件，立即写“执行失败”、failure_stage=`input_sha256_calculation`，不得进入终审，也不得把参数长度错误归类为数据或权限失败。


## 终审取证时限与跨日证据缓存修复（2026-09-14生效）

1. 阶段B不得默认从零检索40只候选。建立并优先读取 `delivery/evidence_cache/<ts_code>.json` 及最近成功正式JSON中的公司级证据；仅当股票主体、报告期或来源URL失效时重建基线。
2. 公司业务逻辑与最近一期定期报告属于可复用基线。每次运行必须保留原URL、原始发布时间、报告期、摘要和上次核验日期，并核对截止时间前是否出现新定期报告、业绩预告、重大公告或风险事件。市场、板块、技术、KDJ、当日事件和执行时机仍按当日bundle重新计算或核验。
3. 缓存证据不是默认分。只有来源仍可回读、发布时间不晚于本次证据截止时间、主体和报告期匹配时才能复用；否则字段为null并写明原因。禁止因缓存存在而跳过新增公告检查。
4. 阶段B按批次持久化，每批最多10只，文件名为 `stage_B_batch_<n>.json`；每批记录已完成代码、来源、发布时间、摘要、评分依据和待补项。中断后从首个未完成代码继续。
5. 主任务首先读取上一交易日成功结果和证据缓存，对重合候选执行增量刷新；新候选优先补业务与财务基线，再补事件与风险。不得重复检索已验证且仍有效的同一来源。
6. `B_evidence_coverage` 属于可从检查点继续的 retryable 阶段性失败，但只有已持久化批次可复用。第一次和第二次有限重试可以继续阶段B补证、评分和交付；不得把它误分类为“只能重传文件”。鉴权、输入哈希、future data、schema/model及规则冲突仍不可重试。
7. 时间闸门继续有效：各运行到收尾点立即保存批次和回执并释放锁。不得为赶时限编造证据、默认评分或发布不完整名单。
8. 成功标准、V2七项权重、60分门槛、正式池/池外规则、风险文字规则、KDJ时机层和PNG契约均不变。


## 2026-09-15 输入传输与增量进度修复（当前执行约束）
- 不改变评分、候选数、市场范围、产物责任、任务启用状态和计划时间。主运行收尾18:48/截止18:49；第一次重试收尾18:54/截止18:55；第二次收尾18:57/截止18:58；复核18:59且严格只读。
- 输入门槛通过后立即完成并持久化阶段A。不能因为预计整条链路来不及完成就跳过当前能完成的最小步骤。阶段B每批最多10只（不是必须凑齐10只）；收尾前保存已完成的单只记录与待补项，由下一次从首个未完成代码恢复。不得伪造评分、降低证据标准或越过硬截止。
- time_gate_before_stage_A、B_evidence_coverage、尚未完成阶段的时间窗口耗尽可在剩余重试次数内恢复，阶段A缺失不是禁止重试的理由。输入哈希不一致、鉴权、schema/model、未来数据及真实规则冲突仍不允许重试。第二次实际尝试失败标记retry_exhausted=true；当天耗尽不等于永久禁用每日任务。
- 所有尝试必须先取得并回读唯一owner_token及generation；每次共享写入检查租约与最新main。运行锁和共享结果用同一基准commit原子提交，非强制推进main。没有实际取得租约不得虚构新generation或写“锁已释放”。过期执行者只能停止共享写入并在会话报告，不得覆盖新owner。复核任务只核验，不负责补写。
- started_at、heartbeat_at、finished_at来自实际时钟；禁止预填或倒填固定时间。回执中的input校验若来自前次检查点，明确标记复用及检查点路径/Blob SHA；只有输入版本绑定仍匹配且检查点可读时才复用，不能声称本次重新计算过。复核的finished_at应注明“回执记录的结束时间”，不等同成功完成时间。
- 安全接收工具为delivery/tools/receive_github_blob.py，调用参数只有输出路径、UTF-8字节数、Git Blob SHA、权威SHA256。原文只能来自github_fetch_blob，通过标准输入传送，不插入命令字符串、不修改末尾换行。接收器精确读取指定字节数后退出，不等待EOF；验证Git Blob SHA和文件流SHA256，失败不留下输出，不覆盖已有文件。
- 本环境非TTY执行的stdin会立即关闭。使用exec_command的tty=true，先运行stty raw -echo再启动接收器，取得session_id后将原文通过write_stdin的chars原样传入。禁止使用默认canonical/echo终端传送大文本。单次接收超时30秒，超时停止，不反复猜测字节。UTF-8字节数来自权威size或原文UTF-8长度，非JS字符串字符数。
- 读取PROJECT_STATE与工作流后，按需读当前输入/回执/检查点。工具发现只列相关名称和必要签名，不输出全注册表、不读取旧任务正文；大包只传递给接收器与解析程序，返回短校验摘要。


## 公司证据存储方式（2026-09-15用户确认）
- 证据库仅持久化结构化事实摘要和来源元数据：股票代码、资料类型、原文URL、原始发布日期（保留日期精度）、报告期、事实摘要、页码/章节定位（如可得）、最近核验时间及待补项。评分记录引用证据，不能只缓存旧分数。
- 不持久化PDF附件、网页快照、截图、完整HTML或完整原文副本，不为这些内容新建对象存储。必要核验时按链接读取原文；解析所需临时内容不作为归档保存。此限制针对外部证据原件，不影响量化包、正式JSON、文字报告和检查点持久化。
- 首次录入必须真实读取并核验来源，不能只凭链接或搜索摘要认定证据合格。已有合格业务/财报基线允许按有效性规则复用；来源失效、事实冲突、报告更新或需要复核时重新读取，日常仍检查新增报告与公告。不把“无本地PDF/快照”列为缺失或失败条件；无法核验必要事实时记录待补，不填默认分。


## 2026-09-15 候选来源改造（用户已确认，代码待生产接入）
- 用户确认优先改候选生成：先行股、细分题材多股联动、已确认跟随三路召回，以上涨机会为研究目标；纯资金推动可作为来源理由，不伪装为公司基本面或重大利好。
- 实现及接入契约见candidate_assets/README.md；候选模块candidate_assets/candidate_sources_v2.py，8项合成单元测试通过。三路轮询去重后保留原30+10上限，不改变七项权重、60分/完整性门槛、最终排序、池外准入、KDJ和风险规则；交付继续文字模式。
- 模块须在ECS完整扫描宇宙截断前调用，不能仅重排既有40只；新诊断字段不会自动产生买入信号。首版召回参数尚未经真实输入或收益验证。
- 部署状态：未接入ECS，未启用新生产候选生成；当前会话无ECS执行通道，仓库未含当前daily/scoring生产源码。须完成真实源码适配、带日期题材映射、完整行情干跑和文字交付验收后更新状态。现有bundle、正式历史结果、任务计划及启用状态未改；缺新字段不得误判旧V2包无效。

## 2026-09-16 三路候选生成上游接入（代码已部署，待自然交易日验收）

- 候选来源模块修正后版本为 `v2_candidate_sources_20260916`，源码固定于main提交 `488e70a17872adf68e7b228c45b27d281e09f5c6`，并已安装至ECS `candidate_assets/candidate_sources_v2.py`。初始暂存提交 `f63f805037561ac0984efb1eda1879f643d560a7` 已完成审查；`aed372b597d960c12d7ecf77cd6b1f21a6307e6a` 仅为通道状态，不是源码。
- `stock_key10_v2_daily.py` 在评分后的官方/外部完整合格扫描集合截断前调用模块，保留原V2七项权重、60分/完整性门槛、排序、池内外规则、KDJ和风险文字规则；新诊断字段不参与评分、排名或买入信号。生产接入备份见 `/root/stock-key10-backups/step_candidate_sources_20260915T164450Z/`。
- 修正项包括：无关股票缺历史不阻断其他题材、题材分母不因缺行情缩小、题材成员按事件公开/生效日期核验、近期突破仅表述为观察窗口内首次、交易日/价格口径/未来数据守卫、多题材及跨池去重。12/12合成测试和远端语法检查通过。
- 20260915真实缓存隔离干跑：官方扫描237/资格82，外部扫描439/资格215，输出30/10，代码唯一且跨池不重叠；14个KDJ字段覆盖40/40，`kdj_data_complete=40/40`，评分、KDJ、量化排名及池归属逐代码保持不变，`future_data_rows_used=0`，`schema/model/stage`及`excluded_sectors=[]`保持不变，耗时约53.935秒。
- 由于ECS没有可靠的带生效日期题材成员映射，真实路由为先行股118、题材联动0、确认跟随0，题材统计0、`coverage_gap=true`；因此本次仅完成代码部署、合成测试和真实干跑，不宣称三路完整真实覆盖。后续不得硬编码示例题材/股票或恢复行业排除。
- 生产latest、历史归档、GitHub发布、service和timer未被本次接入触碰；下一步为自然交易日只读观察日更调用新模块并核验新bundle与发布链路，不能手动补发或把干跑产物当准时生产结果。
