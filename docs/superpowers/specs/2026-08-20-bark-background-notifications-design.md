# Crypto Intelligence Desk Bark 后台通知设计

日期：2026-08-20
状态：已确认，待实施计划

## 1. 目标与范围

为 Crypto Intelligence Desk 增加独立、全天候运行的新闻监控 Worker。Worker 在浏览器关闭后仍持续抓取新闻，使用 Gemini 双模型分析，仅将最终确认为 L4–L5 的重大新闻通过 Bark 推送到 iPhone。

本功能不得改变现有网页监控台的新闻展示、浏览器 AI 设置或公开访问方式。后台 Worker 不开放公网通知接口，Bark Push Key 和 Gemini API Key 只存在于服务器环境中。

本阶段不推送以下事件：

- L1–L3 新闻；
- 普通轮询结果；
- 新闻源故障或恢复；
- Gemini 调用失败；
- Worker 启动、停止或心跳；
- 页面打开、关闭或浏览器内操作。

## 2. 已确认的产品决策

- 仅推送最终评级为 L4–L5 的重大新闻。
- 监控台继续公开，不增加登录或浏览器通知令牌。
- 采用独立后台 Worker，而不是 `proxy.py` 内线程或无头浏览器。
- 后台全天候运行，不依赖浏览器标签页。
- Gemini 初判模型为 `gemini-3.1-flash-lite`。
- Gemini 复核模型为 `gemini-3.5-flash-lite`。
- 只有初判 L4–L5 才调用复核模型。
- 复核仍为 L4–L5 时推送；复核降至 L1–L3 时不推送。
- 复核调用失败时回退初判结果，并发送一次带“初判回退”标识的通知。
- Bark 每个事件默认只尝试发送一次，避免网络超时后的不确定送达造成重复推送。
- 本地开发与验收不读取真实 Key、不调用真实 Gemini、不发送真实 Bark、不部署或重启线上服务。

## 3. 方案比较

### 3.1 独立后台 Worker（采用）

Worker 独立负责新闻抓取、去重、Gemini 分析、Bark 推送和持久化状态。网页服务与通知服务可以分别启动、停止、观察和部署。通知链路故障不会影响现有网页代理。

### 3.2 `proxy.py` 内后台线程（不采用）

文件较少，但网页代理和通知循环共用进程。新闻解析、AI 调用或状态异常会扩大到网页服务，长期运行时也难以独立恢复和定位故障。

### 3.3 服务器无头浏览器（不采用）

可复用浏览器 JavaScript，但需要长期维护浏览器进程和用户数据目录，密钥管理、崩溃恢复和资源占用均不适合作为生产告警链路。

## 4. 架构与模块边界

计划增加以下职责清晰的 Python 模块；实施计划可根据现有平铺目录确定最终包路径，但不得合并成单个巨型文件。

- `worker.py`：后台主循环、调度、单飞控制、优雅停止。
- `sources.py`：新闻源定义、HTTP 抓取、JSON/RSS 解析和标准化。
- `gemini.py`：Gemini 请求、严格 JSON 解析、字段规范化和错误分类。
- `notifications/bark.py`：Bark URL、表单正文、北京时间格式、响应检查和失败隔离。
- `policy.py`：决定是否复核、是否推送，并生成 Bark 标题和正文。
- `state.py`：已见新闻、分析任务、推送结果和重试计划的持久化。
- `tests/`：标准库单元测试、来源 fixture 和模拟 HTTP transport。

模块应通过显式接口协作，HTTP transport、时钟和状态路径均可注入，以便测试不接触真实网络、真实时间或用户环境变量。

现有 `index.html` 继续执行浏览器内新闻展示和可选 AI 分析；浏览器设置只影响网页。后台 Worker 使用独立服务器配置，二者不共享浏览器 `localStorage`。

## 5. 数据流

每轮处理顺序如下：

1. 按配置并发抓取启用的新闻源；单个来源失败不取消其他来源。
2. 将不同来源数据标准化为统一新闻对象。
3. 使用来源原始 ID 和归一化标题指纹识别新新闻，并跨来源去重。
4. 对新新闻调用 `gemini-3.1-flash-lite` 初判。
5. 初判 L1–L3 时记录结果并结束。
6. 初判 L4–L5 时调用 `gemini-3.5-flash-lite` 复核。
7. 复核 L1–L3 时记录“复核降级”并结束。
8. 复核 L4–L5 时以复核结果生成一个 Bark 事件。
9. 复核失败时以初判结果生成一个带“初判回退”标识的 Bark 事件。
10. 发送 Bark，并记录一次最终送达结果；Bark 失败不改变新闻分析结果。

Worker 的轮询必须单飞：上一轮未结束时不启动重叠轮询。

## 6. 新闻源

后台默认启用项与当前网页默认值一致：

- PANews；
- 币安公告；
- Odaily；
- 吴说；
- 深潮；
- 链捕手。

金色财经和 BlockBeats 默认关闭。后台来源集合通过 `CID_WORKER_SOURCES` 覆盖，不读取浏览器设置。

来源适配器必须保持以下边界：

- 每个来源独立解析和报错；
- 只接受公网 HTTPS；
- 限制请求与响应体积；
- 使用固定超时；
- 不把响应正文写入错误日志；
- 不因单一来源格式变化停止 Worker。

## 7. Gemini 分析

### 7.1 配置

- `GEMINI_API_KEY`：服务器 Gemini Key，必需但允许未配置时安全停用分析。
- `GEMINI_PRIMARY_MODEL`：默认 `gemini-3.1-flash-lite`。
- `GEMINI_REVIEW_MODEL`：默认 `gemini-3.5-flash-lite`。

Key 通过 `x-goog-api-key` 请求头发送，不进入查询参数、日志、状态文件或错误消息。

### 7.2 输出契约

初判与复核采用与现有网页一致的结构化字段：

- `direction`：利好、利空或中性；
- `st`：短期影响；
- `lt`：长期影响；
- `level`：1–5；
- `conf`：0–100；
- `coins`：最多 5 个规范化资产代码；
- `cat`：新闻分类；
- `priced_in`：是否可能已反映；
- `why`：具体因果判断；
- `reverse`：解读失效条件。

解析器必须从响应中提取单个 JSON 对象，验证字段类型并规范化边界值。模型返回的自由文本不得直接决定推送。

### 7.3 重试

Gemini 网络错误、HTTP 429、HTTP 5xx 和 JSON 格式错误进入有上限的重试：

- 首次请求失败后最多重试 3 次，即单个分析阶段最多发出 4 次请求；
- 3 次重试前的延迟依次为 30 秒、2 分钟、5 分钟；
- 待重试任务及下次时间保存在状态文件；
- Worker 重启后继续未完成任务；
- 超过上限后标记为分析失败，不发送 Bark，不阻塞其他新闻。

复核最终失败时是已确认的例外：如果初判为 L4–L5，则按初判回退发送一次；正文明确说明复核未完成。

## 8. 推送策略与正文

### 8.1 标题

正常复核通过：

```text
币圈重大情报 · L5 · 负面
```

复核失败回退：

```text
币圈重大情报 · L4 · 初判回退
```

### 8.2 正文

```text
内容：
新闻标题

来源：PANews
分类：监管
影响资产：BTC、ETH
短期：负面
长期：负面
置信度：92%

核心判断：
……

解读失效条件：
……

原文：
https://……

推送时间：2026-08-20 14:30:00
```

正常情况采用复核模型的等级、方向、置信度和分析内容。回退情况采用初判结果，并明确标注复核未完成。推送时间固定使用 `Asia/Shanghai`。

本阶段只发送 `title` 和 `body`，不增加 Bark 声音、分组、自动复制或跳转参数。

## 9. Bark 传输契约

- Push Key 只读取环境变量 `BARK_PUSH_KEY`。
- 未配置或空值时安静跳过，不发出请求。
- 请求地址为 `https://api.day.app/{encoded_push_key}`。
- Python 使用与 `encodeURIComponent()` 等价的 `urllib.parse.quote(push_key, safe="")` 对完整路径段编码。
- 请求方法为 POST。
- `Content-Type` 为 `application/x-www-form-urlencoded; charset=utf-8`。
- 请求体至少包含 `title` 和 `body`。
- 超时固定为 15 秒。
- 同时检查 HTTP 状态和 Bark JSON 业务 `code`；只有 HTTP 成功且业务 `code == 200` 才返回成功。
- JSON 非法、HTTP 失败、业务失败、网络异常和超时均返回失败，不向调用方抛出。
- 不记录原始异常对象、请求 URL、Push Key、请求头或请求正文。
- 每个事件只自动调用一次 Bark；失败状态记为 `delivery_failed`，不自动无限重发。

## 10. 首次启动、去重与状态

### 10.1 冷启动

当状态文件不存在时，首轮抓取只建立当前新闻基线，不分析、不推送，防止首次部署产生历史新闻风暴。

### 10.2 后续启动

状态文件存在时，Worker 加载已见新闻和未完成任务。来源最近列表中尚未处理的新新闻会正常进入分析，因此短时停机后的新闻可以补处理。

### 10.3 去重

- 来源原始 ID 防止同一来源重复处理；
- 归一化标题指纹防止跨来源转载重复处理；
- 已创建 Bark 事件的新闻不得再次创建事件；
- 已发送或发送失败的事件在服务重启后均不得自动重复发送。

### 10.4 状态存储

- 使用 JSON 状态文件，不引入数据库；
- 采用临时文件、刷盘和原子替换，避免进程中断留下半写文件；
- 损坏状态文件时保留损坏副本或报告通用错误，然后以安全冷启动模式恢复，不推送历史新闻；
- 只保留最近 7 天或最多 2,000 条新闻状态；
- 状态中不得写入任何 Key、鉴权头、完整 AI 请求或 Bark 请求 URL。

本地默认状态路径由实现选择在已忽略的运行目录内；线上固定通过 `CID_WORKER_STATE_FILE=/var/lib/crypto-intelligence-desk/worker-state.json` 配置。systemd 使用 `StateDirectory=crypto-intelligence-desk` 创建可写目录，状态不随代码部署被覆盖。

## 11. 环境配置

`.env.example` 应包含以下空 Key 和非敏感默认值：

```env
# Gemini 后台分析
GEMINI_API_KEY=
GEMINI_PRIMARY_MODEL=gemini-3.1-flash-lite
GEMINI_REVIEW_MODEL=gemini-3.5-flash-lite

# Bark iPhone 通知
BARK_PUSH_KEY=

# 后台轮询
CID_WORKER_POLL_SECONDS=30
CID_WORKER_STATE_FILE=
CID_WORKER_SOURCES=panews,binance,odaily,ctcn,techflow,catcher
```

Worker 在首次读取配置前加载项目目录 `.env`，但已有系统环境变量优先，不得被 `.env` 覆盖。`.env` 必须由 `.gitignore` 排除。

环境变量在 Worker 启动时加载；修改后需要重启独立 Worker 才能生效，不宣称支持热更新。

如果 `GEMINI_API_KEY` 或 `BARK_PUSH_KEY` 缺失，Worker 进入安静的未配置等待状态：不请求新闻源、不调用 Gemini、不调用 Bark，也不创建或推进新闻基线。配置补齐并重启后才开始首次冷启动。缺少配置不得导致网页服务或 Worker 进程反复崩溃。

## 12. 安全边界

- Worker 不开放任何 HTTP 端口或公网发送接口。
- 现有公开监控台和 `/ping` 不返回 Bark/Gemini 配置状态。
- 前端源码、状态文件、测试快照和日志不得出现真实 Key。
- 线上 `.env` 权限为 `0600`，不进入代码同步或版本库。
- systemd Worker 使用受限用户、独立状态目录，并启用 `NoNewPrivileges`、`ProtectSystem=strict` 等隔离。
- Bark 直接由 Worker 访问，不经过公开 `/p`，因此不需要将 `api.day.app` 加入网页代理白名单。
- 开发测试只使用显然虚构的测试 Key；测试进程必须清除或覆盖用户机器上可能存在的同名环境变量。

## 13. 错误隔离与可观察性

- 单一新闻源失败只记录来源名和错误类别，不影响其他来源。
- 单条新闻 AI 失败只影响该新闻，不影响队列。
- 复核失败按既定回退策略处理。
- Bark 失败只改变该事件的送达状态，不改变 AI 结果，也不终止 Worker。
- 状态写入失败时不得继续发送无法持久化去重状态的新 Bark，以避免重启后重复推送；Worker 应报告通用错误并暂停通知处理，新闻抓取可继续。
- 日志使用稳定事件类型，例如 `source_failed`、`analysis_retry`、`review_fallback`、`bark_failed`，但不包含秘密或完整上游响应。

## 14. 测试设计

项目保持纯 Python 标准库，使用 `unittest`。HTTP transport、时钟、环境和状态路径均在测试中注入或隔离。

### 14.1 Bark

- `key/with space` 编码为 `key%2Fwith%20space`；
- POST 与正确表单 Content-Type；
- 中文 `title`、`body` 正确编码；
- `2026-07-30T04:34:56Z` 转换为北京时间 `2026-07-30 12:34:56`；
- HTTP 500 返回失败；
- HTTP 200、业务 `code=400` 返回失败；
- 非 JSON 返回失败；
- 网络异常与超时返回失败且不抛出；
- 未配置时不调用 transport；
- 错误日志不含测试 Push Key 或完整 Bark URL。

### 14.2 Gemini

- 初判使用 `gemini-3.1-flash-lite`；
- 复核使用 `gemini-3.5-flash-lite`；
- Key 只进入 `x-goog-api-key` 请求头；
- JSON 提取、字段校验和边界规范化；
- 非 JSON、429、5xx 和超时进入有上限重试；
- 错误日志不含测试 Gemini Key；
- 初判 L1–L3 不调用复核；
- 初判 L4–L5 才调用复核。

### 14.3 策略与状态

- 冷启动建立基线，不推送历史新闻；
- 新增 L3 新闻不推送；
- 初判 L4、复核 L3 不推送；
- 初判 L4、复核 L4 推送一次；
- 初判 L5、复核失败，按初判回退推送一次；
- 同一标题跨来源只分析一次；
- Worker 重启后不重复推送；
- 状态原子写入和清理边界；
- 损坏状态进入安全冷启动；
- 状态无法持久化时不发送可能重复的 Bark；
- 来源、Gemini 或 Bark 失败均不终止后台循环。

### 14.4 发布检查

- 完整 `unittest` 套件；
- Python 语法检查；
- `git diff --check`；
- 常见 API Key、私钥、`.env` 和绝对个人路径扫描；
- 前端、状态接口、日志模板和测试快照的明文秘密扫描；
- 现有网页服务启动、页面和 `/ping` 回归检查。

测试不得请求真实新闻源、Gemini 或 Bark，不得读取或发送用户机器上的真实环境变量。

## 15. 运行与部署设计

本地开发提供独立 Worker 启动入口。网页启动器是否同时启动 Worker，由实施计划根据跨平台行为决定；不得让未配置 Gemini/Bark 时的 Worker 影响网页启动。

线上新增独立 systemd 服务，例如 `crypto-intelligence-desk-worker.service`：

- `WorkingDirectory=/opt/crypto-intelligence-desk`；
- 从受保护的 `.env` 或 systemd 环境加载 Gemini/Bark 配置；
- 使用 `StateDirectory=crypto-intelligence-desk`；
- 与现有 `crypto-intelligence-desk.service` 分开启停；
- 不监听端口；
- 失败自动重启，但有重启退避；
- 部署时先在隔离环境运行完整测试，再同步代码并只重启 Worker；只有涉及共享模块且验证需要时才重启网页服务。

实际部署、真实 Key 注入、服务重启和真实 Bark 测试必须分别获得用户明确授权。

## 16. 实施交付要求

实现完成后报告：

1. 修改和新增的文件；
2. Bark、Gemini、来源、策略和状态模块位置；
3. 实际接入的业务事件；
4. 标题和正文格式；
5. 去重、冷启动和重试行为；
6. 完整测试命令及结果；
7. 是否读取过真实 Key；
8. 是否调用过真实 Gemini；
9. 是否发送过真实 Bark；
10. 是否修改或重启线上环境；
11. 后续需要用户授权的部署和真实验证步骤。

## 17. 本次设计之后的工作边界

下一步只编写详细实施计划。计划获准进入编码后，必须采用测试驱动开发：先写失败测试并确认失败原因，再写最小实现使其通过。

在用户另行授权前，实施仅限本地代码、模拟测试、配置示例和文档，不部署、不重启、不读取真实 Key、不发送真实通知。
