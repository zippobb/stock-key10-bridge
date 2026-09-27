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
ECS导入/七文件部署由用户本地Codex负责，Work不连接ECS。
本地Codex回执：冻结包组合验证通过，生产仍未切换；该状态是用户提供回执，不是Work实时SSH核验。
拟部署观察池228：新增605507.SH国邦医药，删除300199.SZ翰宇药业与688295.SH中复神鹰；分类227，国邦医药待分类。生产回执仍229。移出池不等于外部永久禁选。
schema/model保持2.0.0/V2.0；上游stage=21:30_quantitative_review_payload，终审stage=final_review，新review_contract_version独立标识下游契约。
新下游schema：contracts/key10_review_quant_theme.schema.json
新实际校验器：delivery/tools/validate_quant_theme_review.py
历史成功结果不迁移、不覆盖；新schema验收不是自然运行或收益验收。

## 实时任务核对（本次同步前）
主任务：6a999effa2308191a21925a364d2c0fe，V2.0选股终审主任务。
计划工作日18:35，Asia/Shanghai；is_enabled=false。
唯一重试：6a999f1757b881919023f6af25c7f8b2，V2.0终审重试与交付复核。
计划工作日18:50，Asia/Shanghai；is_enabled=false。
提示词同步不得改动计划/状态，不影响其他任务。保持停用意味着不会自动运行，恢复须另行明确授权。
任务新正文存于contracts/automation_primary.txt与contracts/automation_retry.txt；实际同步和回读结果以contracts/sync_20260927_receipt.json为准，不能以存在草案推定线上已更新。
同步前提示词/任务配置及旧工作流归档于contracts/archive/20260927/，仅供恢复，不作当前执行基线。

## 下一验收
本地Codex部署七文件前重查基线；ECS timer据用户回执为2026-09-28 18:20/18:30，Work未直接核验。
部署后仍需恢复两个终审任务并验收下一自然交易日：新池、新优先级、原始量化保留、无旧门槛、JSON/文字及双回执一致。
具体锁、路径、单次重试和截止规则统一遵循V2_REVIEW_WORKFLOW.md。没有完成某一端不得宣称端到端部署完成。

