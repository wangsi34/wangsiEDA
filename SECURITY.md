# 安全说明

## 部署建议

- 服务只监听 `127.0.0.1`，通过 SSH 隧道访问，每个请求都要求 token。token 存放在 `var/token`，权限为 600。
- 模型密钥只通过环境变量或权限为 600 的文件提供，不要写入 `wangsi_config.json`，也不要提交到仓库。
- 平台只运行派生副本，不修改已验证的工具；派生记录保存在 `derived/*.DERIVATION.json`。
- 写操作只发生在平台目录和运行目录下的 `wangsi/` 子目录。
- 对外分发数据前，用 `export_snapshot` 导出脱敏快照，并在 `redact_terms` 中列出需要隐藏的项目代号和主机名。

## 报告漏洞

请通过仓库的 Security Advisory 私下报告，不要公开提 issue。
