# V2.0 选股终审工作流

> 本文件是主运行与重试运行共用的持久工作流。规则冲突时以 PROJECT_STATE.md 和用户最新明确决定为准。

执行“V2.0 选股终审”。这是同一任务会话中的增量工作流，必须按阶段执行，禁止把所有检索、评分、制图混成一次不可恢复的长步骤。

一、事实源与数据门槛
1. 先完整读取私有GitHub仓库 zippobb/stock-key10-bridge main分支的 PROJECT_STATE.md。对话记忆只作线索；PROJECT_STATE.md、用户最新明确决定或实时数据互相冲突时，醒目标记并停止相关推断。
2. 读取 latest/manifest.json、其archive_path权威归档和 latest/key10_bundle_latest.json，核对trade_date、schema、model、stage、SHA256及内容一致性。
3. 只接受当天北京时间交易日、schema_version=2.0.0、model_version=V2.0、stage=21:30_quantitative_review_payload、future_data_rows_used=0的数据。任何一项不满足，立即输出“数据未就绪”及具体缺失项；禁止回退V1.x、旧融合结果或旧交易日。

二、分阶段增量核验
阶段A——建立候选表：从量化包读取池内最多30只、池外最多10只及市场适配、板块、技术三项量化分。先生成候选代码、名称、板块、量化分和bundle哈希的结构化工作表。
阶段B——批量取证：按板块/产业链分组检索，产业与政策证据同组复用，避免对每只股票重复搜索相同事件。公司层面优先交易所公告、公司公告、定期报告等一手资料；通常每家公司最多保留两项决定性来源，只有来源冲突、重大风险或重大利好需要追加。
阶段C——增量复用：对本任务会话最近已核验且公司主体未变的业务逻辑、逻辑纯度依据和基本面基线可以复用，但必须保留原来源与evidence_as_of。市场、板块、技术、当天公告、当天业绩变化、重大事件和风险必须按本交易日重新核验；资料过期、来源缺失或出现新公告时不得复用。
阶段D——先评分后写作：对池内最多30只和池外最多10只逐只形成紧凑JSON记录，不为未入选股写长篇文字。字段包括logic_purity、elasticity五项明细及合计、fundamentals、major_positive、risk_annotations、main_risk、evidence_as_of、sources和data_completeness。
阶段E——计算与校验：按市场适配5、板块15、技术30、逻辑纯度15、弹性20、基本面10、重大利好5计算100分。弹性五项各0–4且合计必须等于elasticity。单项缺失时仅在可用权重达到70%时等比例还原至100分，否则标为数据不完整且不得正式入选。综合分低于60不入选。重大风险只作文字标记，不扣分、不否决、不改变分数或排序，不生成PASS/WARNING/FAIL。
阶段F——排序输出：池内按综合分、技术分、弹性分、板块分、逻辑纯度、ts_code依次排序，输出0–10只，不凑数。池外仅保留强势板块且该板块不存在任何池内综合分不低于60的合格个股时的候选，按相同顺序输出0–3只；不得与池内混排，也不得自动加入正式股票池。

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
1. 每个北京时间交易日最多执行1次主运行和2次跨时间重试。主运行attempt_no=0；21:00重试attempt_no=1；22:00重试attempt_no=2。禁止创建无限循环或超过2次重试。
2. 日期回执增加attempt_no、retryable、error_class、resume_from、retry_exhausted字段。每次尝试均以GitHub现有日期回执和结果JSON为检查点，使用SHA更新，禁止并发盲写。
3. 可重试错误仅包括：当天数据包尚未发布或仍为旧交易日、临时网络超时、限流、GitHub临时读取失败、单个来源临时不可用、PNG生成或视觉检查失败。鉴权/权限失败、规则冲突、schema/model不兼容、future_data_rows_used不为0、哈希不一致等属于不可重试错误，必须停止并明确报告。
4. 同一次尝试内，单个瞬时失败的只读调用最多立即重试1次；已成功的调用和阶段不得重做。跨时间重试必须从resume_from继续：数据门槛失败从阶段A前重新检查数据；阶段B/C失败复用已保存的候选表和有效证据；阶段D/E/F失败复用此前已成功阶段；仅PNG失败时只重试PNG，不得重新检索或评分。
5. 每次重试前先检查当日日期回执、latest回执及结果JSON。若status为“已完成”或“结果未变化”且结果可回读，立即停止且不得重复计算；若同一bundle_sha256已有成功结果，同样停止。
6. attempt_no=2仍未成功时，写入retry_exhausted=true，状态为“数据未就绪”或“执行失败”，记录最后错误、已完成阶段和人工恢复起点；不得继续自动重试。
7. 没有执行重试（因为已经成功或当前attempt_no已处理）时可以静默；一旦实际发起重试，无论结果如何都必须写入持久回执并发送可见状态。


六、并发锁与运行状态
1. 每次实际运行前维护 delivery/runs/YYYY/MM/key10_review_run_YYYYMMDD.json，字段至少包括run_date、state、attempt_no、started_at、heartbeat_at、finished_at和owner。state只允许running、completed、failed。
2. 写入running前先读取现有运行状态及SHA。若发现另一尝试处于running且heartbeat_at距当前不足90分钟，本次不得启动、不得重复检索或评分；记录“已有运行进行中”后静默退出。若超过90分钟，标记前一运行stale后才可接管。
3. 每完成一个阶段更新heartbeat_at和已完成阶段；结束时将state改为completed或failed。所有更新必须基于最新SHA，发生冲突时重新读取一次；仍冲突则停止，禁止并发盲写。
4. 运行锁只防止重叠，不增加重试次数；被“已有运行进行中”拦截的计划点不计入已执行重试。
