# Contributing

感谢参与改进 Crypto Intelligence Desk。

## Project boundaries

项目定位是新闻聚合、提醒和 AI 信息研判。提交不应加入：

- 实盘或模拟交易。
- 下单、持仓、资金和交易所账户管理。
- 交易策略收益展示或诱导性买卖信号。
- 在源码中内置共享 API Key、Cookie 或账号。

## Architecture

- `index.html`：单页界面、新闻解析、设置和 AI 服务商适配。
- `proxy.py`：Python 标准库本地服务和受限 HTTPS 转发。
- `start.bat` / `start.sh`：本地启动。
- 无构建步骤，无 npm 和第三方 Python 依赖。

保持现有轻量架构，除非新增依赖能解决明确问题，并在 PR 中说明安全、体积和维护成本。

## Development

```sh
python proxy.py
```

打开 `http://127.0.0.1:8899`。修改后重启代理并强制刷新浏览器。

测试 AI 功能时使用你自己的低权限测试 Key。不得把 Key 写进测试、fixture、终端输出或截图。

## Pull request checklist

- [ ] 新闻聚合在不配置 AI Key 时仍可使用。
- [ ] 没有加入交易功能。
- [ ] 不包含真实凭据、个人绝对路径或本机运行产物。
- [ ] 外部文本进入 HTML 前经过转义或使用 `textContent`。
- [ ] 外部 URL 经过协议限制。
- [ ] 新增代理目标仍受公网 HTTPS 限制。
- [ ] 日间和夜间模式都保持可读。
- [ ] 窄屏布局没有明显破坏。
- [ ] JavaScript 与 Python 语法检查通过。
- [ ] `tools/check-release.ps1` 通过。
- [ ] 用户可见变化已经更新 `使用说明.md`。

## News source changes

第三方接口可能随时变化。修改解析器时请：

1. 保留失败隔离，单个源失败不能阻塞其他源。
2. 限制单次读取数量。
3. 清理 HTML 并验证时间戳。
4. 为新闻生成稳定且安全的内部 ID。
5. 不记录完整响应中的敏感字段。
6. 遵守目标站点条款、robots 规则和适用法律。

## License

提交代码即表示你有权提交该内容，并同意其按本项目的 AGPL-3.0 许可证发布。引入第三方代码时必须记录来源和兼容许可证。
