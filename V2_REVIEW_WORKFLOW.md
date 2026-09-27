# V2.0 量化与题材终审工作流
契约版本：quant_theme_rotation_20260927。适用于切换后新运行；历史成功结果只读，不按新schema追溯验证。
本文件替代旧七项主观评分、final_score/60分/可用权重门槛、旧轮动保留席位和旧按分重排规则。上游量化公式与technical_score>=18不变。V1和影子层不参与执行。旧文档仅在git历史及contracts/archive/20260927/供恢复，日常不读取。

## 1. 输入与阶段
仓库 zippobb/stock-key10-bridge main。先读PROJECT_STATE.md和本文，固定本次规则commit。
读取latest/manifest.json、其archive_path和latest/key10_bundle_latest.json。校验原始字节SHA256一致，禁止解析再序列化代替原文哈希。
仅处理当前北京时间自然交易日的完整量化包；非交易日跳过。以交易日历确认日期，不能用本机UTC日期代替。
输入schema_version=2.0.0，model_version=V2.0，stage=21:30_quantitative_review_payload；future_data_rows_used=0（顶层或future_data_guard明确提供，缺失不能推定0）。
池内最多30、池外最多10；代码全局唯一、市场为沪深主板/创业板/科创板，排除北交所。保留上游资格及必要风险校验；不恢复行业排除。各池selection_order必须是数组顺序1..N，technical_score>=18，data_complete=true，origin正确。
无selection_order的新输入为upstream_contract_not_ready，不伪造顺序；不将旧成功结果判成失败。
硬校验不通过时不生成正式入选，按真实失败阶段保存回执。

## 2. 终审选择
各池默认按selection_order审阅。官方selected最多10，外部selected最多3；外部始终是独立观察来源，不自动入池，不受旧池内final_score规则约束。
所有输入候选均保留一条决策记录：selected / watch / deferred。selected表示进入该池最终展示短名单，不代表交易指令；watch是观察，deferred是暂缓。
selected的final_order在各池从1连续编号；watch/deferred的final_order=null。
selection_order及原始量化、KDJ、题材、路线、延伸字段只读保留。禁止计算或要求旧final_score、research_value_score、market_context_score和七项主观终审分。
信号/延伸Unknown不等于安全、不等于False；跟随路线仍未启用，不能用历史终审护栏自行补成已启用。
缺少标签不影响量化资格，不虚构联动。分类以本次bundle为准，不固定229/88；拟部署池228、已分类227，国邦医药待分类。
默认保持上游相对顺序。若有具体市场/题材/风险事实支持改序，为该池selected记录完整调整：上游位置、最终位置、理由、来源及日期。final_order改变不覆盖selection_order。仅因删减导致编号前移不算改序。
低延伸不是入场信号，高RSI/KDJ、单日涨跌或路线身份不能单独决定买卖。

## 3. 市场与证据
有针对性读取截至evidence_cutoff的公开收评、必要公告，不重复全市场扫描，不默认检索40家公司长篇材料。
market_context分开记录reported_facts、analysis_inferences、missing和decision。来源含URL及published_at，不把抓取时间当发布日期；不能使用截止后的事实倒推当时决定。
比较大盘连涨、累计涨幅、偏离和涨跌面；用题材当日表现及公开催化说明优先级。不得只凭标签断言联动，缺前日数据不宣称轮动。
市场背景可减少selected、仅观察或暂缓，decision必须说明具体依据和受影响数量/代码，不虚构市场乘数或收益概率。
新闻、题材或公司资料不足不阻断JSON和中文报告；明确缺失，必要时watch/deferred。日期、哈希、schema、未来数据、来源、池归属及具体风险硬条件不能放宽。
不生成PNG；不保存外部PDF/完整网页快照，只保留事实摘要与来源。

## 4. 正式JSON与验证
新结果必须符合contracts/key10_review_quant_theme.schema.json，review_contract_version=quant_theme_rotation_20260927。
顶层schema_version仍为2.0.0，model_version=V2.0，stage=final_review；input_stage保留量化包stage，避免把终审产物冒充上游包。
official_candidates / external_candidates记录全部输入决策；每条input_snapshot为原候选完整只读副本，另含ts_code、name、origin、selection_order、quant_score、quant_rank、technical_score、selection_status、final_order、reason、risk_notes、next_day_action。
新结果不产生旧七项主观评分或final_score。
写正式产物前必须实际执行：
python delivery/tools/validate_quant_theme_review.py --schema contracts/key10_review_quant_theme.schema.json --bundle INPUT_RAW.json --review REVIEW.json --trade-date YYYYMMDD --write-text REVIEW.md
INPUT_RAW必须是已校验的权威原始包。脚本按原始字节核对input_sha256，验证快照、顺序、资格、分池上限、状态和来源日期，生成确定性中文报告。
正式JSON和MD回读落盘后，再运行同一命令，改用--text REVIEW.md验证一致；此阶段不得使用--write-text覆盖回读内容。
自动校验只证明结构和输入一致，不代替事实核验；报告不得把合成测试描述为真实终审。
MD展示市场事实/推断/缺失、官方名单、外部观察、暂缓及次日动作，按final_order显示selected，其余按selection_order。不展示旧评分体系。

## 5. 持久路径、租约与恢复
正式路径：
review/YYYY/MM/key10_review_<trade_date>.json
review/YYYY/MM/key10_review_<trade_date>.md
delivery/YYYY/MM/key10_review_receipt_<trade_date>.json
latest/key10_review_receipt_latest.json
运行状态/租约沿用delivery/runs/YYYY/MM/key10_review_run_<trade_date>.json；
检查点delivery/checkpoints/YYYY/MM/<trade_date>/；
不可变attempt记录delivery/checkpoints/YYYY/MM/<trade_date>/attempt_<0|1>.json。
创建attempt记录后不得覆盖；恢复操作追加新阶段检查点。覆盖本次未完成共享文件前，先将其原内容保存为检查点中的带Blob标识快照。
owner_token、generation、租约180秒/60秒续租；每次共享写入前检查归属和最新main。同一基准commit原子写关联状态、非强制推进main；冲突重新核对，禁止盲写。有效他人租约仅报告并发，不消耗retry。
primary attempt_no=0，18:48收尾、18:49截止；唯一retry attempt_no=1，18:58收尾、18:59截止。不得永久停用任务，不生成第二次retry。时间为Asia/Shanghai，使用真实时钟。
步骤：输入校验及阶段A检查点 → 终审决策 → schema/语义验证 → JSON持久化回读 → MD持久化回读验证 → 日期回执 → latest回执 → 回读一致 → 释放所属锁 → 会话展示完整文字。
回执沿用状态字段，并记录review_contract_version、attempt_no、输入/输出哈希、真实阶段、retryable、resume_from、finished_at、delivery_mode=text、text_report_path、text_delivery_status；
png_delivery_status=not_required_text_mode、png_path=null。验证完成才final_delivery_gate=ready；发送前visible_completion只能pending，不冒充已展示。
保留primary失败attempt与快照。retry可以更新同日同输入、本次失败运行的未完成正式路径及共享回执；不能覆盖已验收成功产物、其他交易日结果或不可变attempt记录。
同日同输入已有完整成功结果：只读复核并重新展示，结果未变化；旧契约成功结果按当时契约只读复核，不追溯要求新字段。
仅retryable瞬时读取/未完成交付可恢复；每次瞬时读失败最多重试一次。输入本身哈希、未来数据、schema等错误不可绕过。普通研究资料缺失不是整任务阻断。
到截止保存真实进度，失败可见；唯一重试失败则retry_exhausted=true。不得把迟到成功写成准时完成。
工具无写权限或校验执行能力时说明具体阻断，不声称持久化或验证成功。

## 6. 部署边界
ChatGPT负责规则/终审；ECS部署由本地Codex负责。本次契约同步不等于ECS七文件已部署。
任务时间和启用状态不自行更改；2026-09-27读取两个任务均停用，更新提示词不自动恢复运行。

