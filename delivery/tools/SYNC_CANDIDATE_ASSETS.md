# 固定 commit 候选资产同步通道

本工具把已由桌面 GitHub 连接器按固定 commit 读取的候选资产，复制到 ECS 的独立暂存目录并在远端逐文件校验。它不覆盖生产代码、latest 或历史归档，不调用 systemd，也不读取凭据。

## 标准流程

1. 用已认证的 GitHub 连接器读取私有仓库 `zippobb/stock-key10-bridge` 的 `main`，解析出 commit SHA；再用该 SHA 对应的 blob SHA 读取下列文件的原始 UTF-8 内容：`PROJECT_STATE.md`、`V2_REVIEW_WORKFLOW.md`、`candidate_assets/README.md`、`candidate_assets/candidate_sources_v2.py`、`candidate_assets/test_candidate_sources_v2.py`。
2. 将原始内容放入离线包，并在包根目录写入 `manifest.json`。清单必须包含仓库、`ref=main`、固定 commit、每个文件的 blob SHA、字节数和 SHA256。离线包应保留作故障备用。
3. 在桌面端执行（每次使用唯一的 commit 暂存目录）：

   `pwsh -NoProfile -File delivery/tools/sync_candidate_assets_to_ecs.ps1 -CommitSha <40位commit> -PackageRoot <离线包路径>`

脚本先在本地校验清单和全部文件，再通过 SSH/SCP 写入：

`/opt/stock-automation/staging/stock-key10-candidate-assets/<commit>/`

随后由远端 `verify_candidate_assets_stage.py` 只读核对路径、字节数和 SHA256。暂存成功不代表已接入生产；生产接入仍需单独备份、审查、干跑和自然交易日验收。

## 安全边界

- ECS 不新增 GitHub 源码下载认证；现有发布器继续使用其既有链路。
- 不把 token、私钥、Cookie、`.env` 或带凭据的 URL 放入命令、清单或脚本。
- 不使用未固定的 `main` 内容作为生产输入；任何校验失败都停止并保留离线包。
- 不用该工具覆盖 `/opt/stock-automation/scanner`、`latest`、`archive` 或重启/启动服务。
- 新终端可重复执行远端 verifier，确认暂存内容未被当前进程缓存替代。
