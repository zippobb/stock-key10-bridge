# Stock Key10 V2.0 部署包

部署前版本已备份至私有仓库：`zippobb/stock-key10-bridge` 分支 `backup/pre-v2-20260829`。

## 部署

```bash
tar -xzf stock_key10_v2_deployment_20260829.tar.gz
sudo ./stock_key10_v2_deployment_20260829/deploy_stock_key10_v2.sh
```

默认只安装、验证并启用定时器，不在非交易日强制跑实时行情。若需立即执行最近一个待处理交易日：

```bash
sudo ./stock_key10_v2_deployment_20260829/deploy_stock_key10_v2.sh --run-now
```

## 回滚

```bash
sudo ./stock_key10_v2_deployment_20260829/rollback_stock_key10_v2.sh
```

22:00终审输入必须由`build_key10_v2_review_template.py`生成骨架，再由终审任务填写；终审程序拒绝加入21:30数据包之外的股票。
