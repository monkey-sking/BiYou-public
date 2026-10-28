# 安全说明

本仓库在公开前会做历史重写与人工审核。请勿提交：

- API key、Cookie、密码、Bearer token 或浏览器 profile
- `config/config.yaml`、`config/publishing_automation.yaml`、`config/context.json`
- `data/`、`history/`、`logs/`、`analytics/`、SQLite 数据文件
- Cookie 导出、备份文件或本地账号 ID

推送前使用 `python3 scripts/check_public_history.py main` 检查整个历史；钩子失败会阻止推送。

## 配置与源码隔离

真实运行配置属于机器私有数据，不属于源码。仓库仅保留脱敏模板。
现有安装可将四份运行配置放在仓库外的私有目录，并让以下兼容路径指向它们：

- `config/config.yaml`
- `config/publishing_automation.yaml`
- `config/context.json`
- `config/resource_doc.json`

私有目录权限应为 `0700`，文件权限应为 `0600`。兼容链接同样必须被 Git 忽略，
不得以 `git add -f` 提交。保留现有加载路径，不要求更换账号、API key 或模型服务。
新机器可以从 `config/config.example.yaml` 和
`config/publishing_automation.example.yaml` 建立自己的私有配置；不要从其他机器复制真实凭据。

每个新克隆都需要执行 `git config core.hooksPath .githooks`：

- `pre-commit` 检查 Git 暂存区的全部内容，拦截强制加入的私有文件和已知密钥格式。
- `pre-push` 检查将要推送的完整可达历史，不能用“当前文件已删除”替代历史清理。
- 手动检查：`python3 scripts/check_public_history.py --staged`；
  `python3 scripts/check_public_history.py main`。

检查仅覆盖路径规则和已知密钥格式，不保证识别所有未知凭据。Git 历史清理与服务商凭据撤销分别验收；
API 返回成功只能证明该凭据当时可用，不能证明它从未泄露。
私有备份和包含旧历史的本地分支仍须按敏感数据保管，不能随源码发布。
