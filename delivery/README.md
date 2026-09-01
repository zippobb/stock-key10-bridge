# V2.0 终审交付回执

此目录保存 ChatGPT “V2.0 选股终审”的持久交付标记。

## 路径

- 日期回执：`delivery/YYYY/MM/key10_review_receipt_YYYYMMDD.json`
- 最新回执：`latest/key10_review_receipt_latest.json`
- 正式结果：`review/YYYY/MM/key10_review_YYYYMMDD.json`

## 完成协议

1. 先生成并校验正式结果 JSON。
2. 写入并回读正式结果文件。
3. 写入日期回执。
4. 最后更新 latest 回执。
5. 日期回执回读成功后，仍须在任务会话发送可见最终消息。

允许状态仅为：`已完成`、`结果未变化`、`数据未就绪`、`执行失败`。

回执至少包含：

- `run_date`
- `started_at`
- `finished_at`
- `status`
- `trade_date`
- `bundle_sha256`
- `completed_stage`
- `failure_stage`
- `missing_items`
- `result_json_path`
- `png_delivery_status`
- `summary`

日期回执是运行完成标记；缺失、过期、字段不全或与 latest 不一致，均视为交付异常。
