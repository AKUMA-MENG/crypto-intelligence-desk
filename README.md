# Crypto Intelligence Desk / 币圈新闻监控台

一个本地运行、新闻优先、可选 AI 解读的加密行业情报终端。

它聚合多个公开新闻源，自动去重、按时间展示，并可使用用户自己的 AI API Key 生成影响方向、短期/长期判断、影响等级、置信度和重大新闻提醒。项目只处理新闻与信息研判，不包含行情交易、策略回测、下单、持仓或交易所账户功能。

## 核心特点

- 多信源新闻聚合与自动去重。
- 三栏桌面情报布局，重大新闻自动置顶。
- 新闻正文与 AI 分析分区显示。
- 新消息弹入动效，同时尊重系统“减少动态效果”设置。
- 日间/夜间模式，可在浏览器中持久保存。
- 支持 DeepSeek、Claude、OpenAI、Gemini、xAI 和自定义 OpenAI 兼容接口。
- 输入 API Key 后自动读取可用模型并匹配模型名。
- AI 未返回 JSON 或 JSON 无效时自动重试 3 次。
- 仅监听 `127.0.0.1`，无需数据库和第三方 Python 包。
- Windows 全新电脑可一键准备隔离的便携 Python 运行环境。

## 30 秒开始使用

### Windows 10 / 11

1. 下载发布包并**完整解压**到一个普通文件夹。
2. 双击 `启动监控台.bat`，也可以双击 `start.bat`。
3. 浏览器会打开 `http://127.0.0.1:8899`。
4. 首次没有 Python 时，启动器会从 Python 官方下载约 11 MB 的便携运行环境，并在校验 SHA-256 后放入本目录的 `.runtime` 文件夹。
5. 保持启动窗口开启。关闭窗口或按 `Ctrl+C` 即可停止。

首次启动需要联网。便携运行环境不会安装到系统、不写注册表、不要求管理员权限。支持常见的 x64、ARM64 和 32 位 Windows 架构。

### macOS / Linux

系统需要 Python 3.8 或更高版本：

```sh
chmod +x start.sh
./start.sh
```

也可直接运行：

```sh
python3 proxy.py
```

然后打开 `http://127.0.0.1:8899`。

## AI 配置

新闻聚合不需要任何 Key。只有 AI 自动分析需要用户自行准备对应服务商的 API Key。

1. 打开右上角“设置”。
2. 选择 AI 服务商。
3. 粘贴自己的 API Key。
4. 程序会读取该账号可用的模型列表并自动选择模型；也可以手动覆盖。
5. 保存设置。

发布包不包含任何 API Key、账号、Cookie 或个人路径。Key 只保存在当前浏览器、当前站点的本地存储中，并通过本机代理发送给用户选择的 AI 服务商。请勿在公共电脑上选择长期保存；离开前可在设置底部点击“清除全部本地数据”。

## 目录结构

```text
crypto-intelligence-desk/
├─ index.html                 网页界面和主要业务逻辑
├─ proxy.py                  本地静态服务与受限 HTTPS 转发代理
├─ start.bat                 Windows 主启动器
├─ 启动监控台.bat             Windows 中文入口
├─ start.sh                  macOS / Linux 启动器
├─ tools/
│  ├─ bootstrap-python.ps1   Windows 首次运行的便携环境引导器
│  ├─ start.ps1              Windows 可靠启动逻辑
│  └─ check-release.ps1      发布前脱敏与完整性检查
├─ 使用说明.md                完整用户手册与排障说明
├─ SECURITY.md               安全模型与漏洞报告方式
├─ CONTRIBUTING.md           贡献指南
├─ THIRD_PARTY_NOTICES.md    第三方说明
├─ CHANGELOG.md              版本变更记录
├─ LICENSE                   AGPL-3.0 许可证
└─ VERSION                   当前版本
```

`.runtime` 是 Windows 首次运行时生成的本机运行环境，已被 `.gitignore` 排除，不应提交到 Git 仓库或打进发布压缩包。

## 隐私与安全摘要

- 服务只绑定本机回环地址，不向局域网或互联网开放端口。
- 本地代理只接受来自监控台页面的请求。
- 转发目标必须是可公开解析的公网 HTTPS 地址；本机、内网和非 HTTPS 目标会被拒绝。
- 页面响应包含 CSP、禁止嵌入和内容类型保护等安全头。
- 外部新闻数据会转义，外链只允许 HTTP/HTTPS，外部 ID 不会直接进入 DOM。
- 代理日志只显示目标主机名，不记录请求正文、API Key 或完整 URL。
- Gemini Key 通过请求头传递，不放入 URL 查询参数。
- 发布前可运行 `tools/check-release.ps1` 检查常见密钥格式、私钥块、个人绝对路径和不应发布的运行时目录。

更完整的安全边界见 [SECURITY.md](SECURITY.md)，操作细节见 [使用说明.md](使用说明.md)。

## 开发与验证

本项目没有构建步骤，也没有第三方 Python 依赖。修改 `index.html` 或 `proxy.py` 后重启本地服务并刷新页面即可。

发布前在 PowerShell 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\check-release.ps1
```

## 开源许可

本项目采用 [GNU Affero General Public License v3.0](LICENSE)。`proxy.py` 中的阿里云 WAF 兼容算法参考了 AGPL-3.0 项目 RSSHub，因此发布版保留同类许可证与来源说明。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 免责声明

本项目只做公开新闻聚合、提醒和 AI 辅助解读，不构成投资、法律、税务或交易建议。新闻源可能变更、延迟、限流或停止服务；AI 也可能误判、遗漏上下文或生成不准确内容。重大信息请打开原文并从多个独立来源交叉核实。
