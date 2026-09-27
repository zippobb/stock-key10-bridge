# Stock Key10 Project State
更新：2026-09-27。当前权威规则为本文件与V2_REVIEW_WORKFLOW.md（quant_theme_rotation_20260927）。
用户最新明确决定优先；历史资料仅作归档，不再参与当前规则冲突判断。

## 当前已确认规则
V2.0唯一有效，V1及影子层取消；只输出JSON及中文文字。量化＋题材背景，取消旧七项主观终审评分、60分/70%权重门槛及旧final_score回排。
上游技术分>=18与原量化公式、必要风险/数据校验不变。沪深主板/创业板/科创板，排除北交所；不恢复行业排除。
官方/外部分开，正式短名单上限10/3，不凑数。selection_order只读；final_order是最终selected顺序，watch/deferred为null。改序须说明依据。
使用公开收评减少重复扫描；缺新闻/公司长篇证据不阻断整份交付；市场条件可导致少选、观察、暂缓并说明影响。不做历史回测，不执行真实交易。
上涨延伸在截断前全局降优先级；公式与阈值为实验参数，不代表收益验证。跟随保护Unknown，确认跟随未启用；题材标签不等于当日联动证据。

## 部署状态与分工
ECS七文件部署状态：2026-09-27 22:04 CST，本地Codex经SSH stock-server新鲜核验后，按冻结manifest完成七个运行文件替换并逐项回读SHA256通过。实际备份：/root/stock-key10-backups/step_v2_pool_candidate_freeze_20260927T140416Z/。无代码策略参数调整；未改service/timer，未启动日更/发布，未改latest或历史归档。
部署文件及SHA256：
- config/observation_pool.csv：8c55a323ba8f6f005290fc5f2ce1d279425ef618a94f4000b662934e6c8c7a82
- config/stock_theme_tags_229_v2.0.json：8ab1714a1802241a0caef827cec11894ddf0f57fe0e0e487f7ceb9688e09b445
- config/stock_theme_tags_229_v2.0_import.json：cc88cbadec5e176cc842c8afdeb1d72deeaeb8ad54927585a8927a61de79e345
- config/strategy_v2.json：85de2921d16404bf1360c69343e7e77efd110cd779050518943fc81e0e0bd48e
- candidate_assets/theme_classification_v2.py：fee7ecf24936f9a95aa123238c65518036bb98cd0dcad6e4beee92a49c6a8411
- candidate_assets/candidate_sources_v2.py：cf531c85261ef1223ac81dcef7620ebd8af0d345098c5fc3e21234df2c23d1df
- stock_key10_v2_daily.py：5aa9308ba03afbd92277526158b933a3d862d9b2b6312eca9a330d77299c772a
当前观察池已为228只：新增605507.SH国邦医药，删除300199.SZ翰宇药业与688295.SH中复神鹰；分类227条，国邦医药待分类，不因缺标签剔除。移出池不等于外部永久禁选。
部署后两个service均inactive，两个timer均enabled/active；实际下次触发为2026-09-28 18:20与18:30 CST。单元文件哈希未变。
schema/model保持2.0.0/V2.0；上游stage=21:30_quantitative_review_payload，终审stage=final_review，新review_contract_version独立标识下游契约。
新下游schema：contracts/key10_review_quant_theme.schema.json
新实际校验器：delivery/tools/validate_quant_theme_review.py
历史成功结果不迁移、不覆盖；新schema验收不是自然运行或收益验收。

## 实时任务核对（2026-09-27恢复后）
用户明确授权“恢复”，Work已将以下两个任务is_enabled设为true并回读确认：
- 主任务6a999effa2308191a21925a364d2c0fe：工作日18:35，Asia/Shanghai，已启用。
- 唯一重试6a999f1757b881919023f6af25c7f8b2：工作日18:50，Asia/Shanghai，已启用。
原提示词、计划、时区、标题均未改变，其他任务未变。未手动触发执行。
工具next_run_time返回null；依原计划下次应为2026-09-28上述时间，不把计划时间当作运行成功证据。
contracts/sync_20260927_receipt.json记录的是此前提示词同步时停用快照；当前启用状态以本节及实时任务读取为准。
新契约与提示词同步已完成；ECS部署依据本地Codex回执。当前状态：ECS已部署、终审任务已恢复、自然运行待验收。

## 下一验收
当前ECS生产latest两份副本SHA256相同，均为a43a528a7f31ab316f999cdd895bf26def518b99e067904131acc98827f807b4，trade_date=20260924；其候选尚无selection_order，不能作为新终审契约输入。该历史latest未改写，等待自然日更产生新包。
主任务与唯一重试已同步并恢复启用。等待自然运行核验新池、selection_order、评分/KDJ、未来数据守卫、JSON/文字报告及回执一致性；不把隔离缓存测试或启用成功当作自然运行验收。
具体锁、路径、单次重试和截止规则统一遵循V2_REVIEW_WORKFLOW.md。没有完成某一端不得宣称端到端部署完成。

