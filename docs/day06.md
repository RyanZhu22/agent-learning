# 第 6 天：通过网页操作工单，用接口连接 Python

第 5 天已经跑通了“保存草稿 → 核对确认 → 创建工单”。今天为同一套逻辑增加 FastAPI 接口和一个本地网页，并通过日志观察每次请求。

**界面变了，建单规则保持相同。** 草稿仍然只有 `pending`（待确认）和 `submitted`（已提交）两种状态；`confirm` 是提交操作。首次确认成功后才有工单，工单初始状态为“待处理”。对同一草稿重复确认，仍然返回原工单。

本课约 3 小时。先在浏览器亲手完成一次离线流程，再接入真实模型，最后观察接口与日志。今天的页面使用本机学习数据库，未实现账号、权限或公网部署；知识库问答仍使用第 4 天入口。

| 文件 | 职责 |
| --- | --- |
| `day06_api.py` | 启动服务、定义接口、校验请求、返回结果和记录日志 |
| `static/day06.html` | 网页输入、展示草稿、人工确认、查询工单 |
| `day05_agent.py` | 复用模型整理草稿的函数 |
| `day05_store.py` | 复用草稿和工单存储、事务与防重复逻辑 |
| `tests/test_day06_api.py` | 使用临时数据库和模拟模型检查接口行为 |

## 0–15 分钟：启动服务

在 PowerShell 中进入项目目录，使用项目的 Python 3.12 环境。本机已安装新增依赖，可以直接启动；新环境需先运行 `uv sync` 安装依赖。

```powershell
Set-Location D:\codex\agent-learning
.\.venv\Scripts\python.exe day06_api.py
```

服务启动后，终端会一直运行，等待网页发来的请求。保持这个终端打开，在浏览器访问：

[http://127.0.0.1:8000/](http://127.0.0.1:8000/)

`127.0.0.1` 指当前电脑，`8000` 是这次服务监听的端口。需要停止服务时，回到该终端按 `Ctrl+C`。

如果提示端口被占用，换一个端口启动，并让浏览器地址同步改变：

```powershell
.\.venv\Scripts\python.exe day06_api.py --port 8001
```

此时访问 [http://127.0.0.1:8001/](http://127.0.0.1:8001/)。后续示例默认使用 `8000`；更换端口后，接口地址也要同步替换。

默认沿用 `artifacts/day05_tickets.sqlite3`。因此，前一天创建的草稿和工单仍能查到。若想使用独立的练习数据库，可以停止服务后重新启动：

```powershell
.\.venv\Scripts\python.exe day06_api.py --db artifacts/day06_practice.sqlite3
```

换数据库意味着使用另一份数据；旧库的编号不会自动出现在新库里。练习期间保持同一数据库路径。

## 15–45 分钟：先完成网页离线流程

第一轮只使用页面提供的六字段样例，不调用 DeepSeek，也不需要模型密钥。

1. 点击“使用离线示例”，页面会直接把六字段样例保存为新草稿。
2. 逐项查看标题、描述、复现步骤、期望结果、实际结果和发生时间，记下页面显示的实际 `D` 开头编号。此时状态应为 `pending`，尚未创建工单。
3. 在确认区域先点击“取消本次创建”，再在“找回已有记录”输入刚保存的草稿编号并查询。它仍然存在，状态仍为 `pending`。
4. 重新展示该草稿，核对全部内容。勾选已核对，并手动输入页面给出的精确确认短语，例如“确认创建”后加空格和本次草稿编号。
5. 执行确认，记下返回的 `N` 开头工单编号。草稿变为 `submitted`，工单为“待处理”。

**不要把文档里的 `Dxxx` 或 `Nxxx` 当作实际编号输入。** 每次草稿和工单编号都由程序生成，请复制页面刚返回的完整编号。

网页中的取消会收起草稿详情和确认表单，恢复初始提示，并清理本次确认输入和勾选状态。草稿编号会自动填入“找回已有记录”的查询框，方便重新载入。数据库中的草稿仍保留，不创建工单，也没有新增“取消草稿”状态。

此时的数据关系应当是：

```text
草稿 D…（submitted） ──关联──> 工单 N…（待处理）
```

页面上的复选框用于提醒你核对内容；服务端还会校验确认短语。网页显示“已核对”本身不会创建工单，最终写入仍由 Python 和 SQLite 执行。

## 45–70 分钟：重复提交、查询与重开服务

重新加载刚才的已提交草稿，再点击“查看已创建工单”。它会再次调用同一草稿的确认接口来验证防重复，返回原工单。比较两次返回结果：

| 观察项 | 首次确认 | 对同一草稿再次确认 |
| --- | --- | --- |
| `created` | `true` | `false` |
| 工单编号 | 新生成的 `N…` | 同一个 `N…` |
| 工单创建时间 | 首次创建时间 | 保持相同 |

重复确认是重试同一建单操作，不会推进工单的处理状态。“处理、解决、关闭工单”属于后续业务流程，今天没有实现。

使用页面的查询区域，分别按实际草稿编号和工单编号查询。你也可以查询自己第 5 天已经创建的 `N` 开头工单，因为默认使用同一数据库。网页接口只查询持久化工单；第 1–4 天的 `T1001` 等模拟工单不在这套网页接口中。

再按以下步骤验证持久化：

1. 记下工单编号。
2. 终端按 `Ctrl+C` 停止服务，再用原命令、同一数据库路径启动。
3. 刷新页面，输入刚记下的工单编号查询。

应仍能读到同一张工单。网页暂时显示了什么，与数据库实际保存了什么，是两个层次；刷新页面会重新加载界面，查询结果则来自 SQLite。

如果想报告另一次独立故障，再保存一份新草稿。即使内容相同，新草稿也代表新意图，确认后可以创建另一张工单。防重复的依据仍是同一个 `draft_id`。

## 70–95 分钟：在网页上让模型整理草稿

这一轮使用真实 DeepSeek API。沿用第 5 天已配置的本地 `.env`；修改密钥或模型配置后，停止并重新启动服务。密钥留在服务端，不填写到页面，不贴到聊天。

先输入信息不足的描述：

```text
我要报修：邮件通知失败。
```

预期页面展示追问和缺失字段，没有保存完整草稿，也没有创建工单。接口返回的业务状态为 `needs_information`。

当前接口没有保存自然语言对话历史。补充信息时，要重新提供完整描述，而不是只发送“发生在上午十点”。例如：

```text
请整理报修草稿。问题描述：邮件通知功能异常。复现步骤：进入通知设置，点击发送测试邮件。期望结果：测试邮箱收到邮件。实际结果：页面提示连接超时，邮箱未收到邮件。发生时间：2026年10月24日10:00，UTC+8（虚构练习时间）。
```

完整时应保存并展示一份 `pending` 草稿，业务状态为 `ready`。核对模型整理的六个字段，再选择取消或确认。

下面几处的 `status` 描述不同层次的结果：

| 出现位置 | 示例 | 含义 |
| --- | --- | --- |
| 模型整理接口最外层 | `ready` / `needs_information` | 本次整理是否已经得到完整草稿 |
| 保存后的草稿记录 | `pending` / `submitted` | 这份草稿是否已提交为工单 |
| 工单记录 | `待处理` | 工单当前处理进度 |

模型只负责整理字段，没有创建工单的工具。网页拿到完整草稿后，仍需人工核对并通过单独的确认接口创建。若内容有误，修改描述并重新整理；离线练习也可以修改 JSON 样例，通过接口保存为新草稿。当前没有编辑已保存草稿的接口。

## 95–125 分钟：理解接口与 JSON

浏览器页面并不直接操作 SQLite，它向本机服务发送 HTTP 请求：

```text
浏览器页面
  → HTTP 请求（方法、地址、JSON）
  → FastAPI 校验输入并调用 Python 函数
  → 模型整理或 SQLite 操作
  → HTTP 响应（状态码、JSON）
  → 页面展示
```

先访问 [健康检查](http://127.0.0.1:8000/health)。正常返回：

```json
{"status": "ok"}
```

它用于确认 Web 服务能够响应，不会调用模型。再打开 [接口文档](http://127.0.0.1:8000/docs)，查看每个接口的参数和响应模型。展开接口后，使用 `Try it out` 和 `Execute` 可以直接发送请求。

| 方法与路径 | 作用 | 是否调用真实模型 |
| --- | --- | --- |
| `POST /api/drafts` | 接收六字段，校验后保存草稿 | 否 |
| `POST /api/drafts/prepare` | 接收 `question`，整理完整草稿或追问 | 是 |
| `GET /api/drafts/{draft_id}` | 查询已保存的草稿 | 否 |
| `POST /api/drafts/{draft_id}/confirm` | 校验确认短语后创建工单，或返回已有工单 | 否 |
| `GET /api/tickets/{ticket_id}` | 查询本地 `N` 开头工单 | 否 |

`GET` 用来读取数据。这里的 `POST` 承载保存、整理、确认等操作；`POST` 这个方法本身不保证防重复，幂等行为来自第 5 天的草稿编号、事务和数据库约束。

以下命令在**另一个 PowerShell 终端**运行，保持服务终端继续运行。先在命令行查询健康状态：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/health"
```

再读取项目提供的六字段样例，通过接口保存一份新的独立练习草稿：

```powershell
$practiceBody = Get-Content data/day05_draft.json -Raw -Encoding UTF8
$practiceDraft = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/drafts" -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($practiceBody))
$practiceDraft | ConvertTo-Json -Depth 5
$practiceDraftId = $practiceDraft.draft_id
```

`$practiceDraftId` 已经保存实际返回的草稿编号，不需要手动填占位符。查询它：

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/drafts/$practiceDraftId" | ConvertTo-Json -Depth 5
```

核对这份草稿内容后，再发送确认请求：

```powershell
$practiceConfirmation = @{ confirmation_text = "确认创建 $practiceDraftId" } | ConvertTo-Json -Compress
$practiceResult = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/drafts/$practiceDraftId/confirm" -ContentType "application/json; charset=utf-8" -Body ([System.Text.Encoding]::UTF8.GetBytes($practiceConfirmation))
$practiceResult | ConvertTo-Json -Depth 5
```

请求体只包含确认短语，具体草稿由 URL 中的编号定位。服务端重新读取已保存的草稿，不把浏览器临时填写的字段直接当作确认结果。

首次成功响应为 HTTP `201`，JSON 中 `created` 为 `true`。对同一草稿发送同一确认请求，返回 HTTP `200`，`created` 为 `false`，工单编号保持相同。

通过模型整理接口时，请求体是：

```json
{"question": "我要报修：邮件通知失败。"}
```

信息不足时返回类似以下结构；缺失字段和追问由实际模型输出决定：

```json
{
  "status": "needs_information",
  "draft": null,
  "follow_up": "请补充复现步骤、期望结果、实际结果和发生时间。",
  "missing_fields": ["reproduction_steps", "expected_result", "actual_result", "occurred_at"]
}
```

这种情况可以返回 HTTP `200`：请求已成功处理，业务结果是需要补充信息。它不同于接口参数不合法或服务执行失败。

## 125–150 分钟：用错误响应与日志定位请求

先在页面查询一个不存在的草稿编号，观察错误信息和请求编号。也可以在 `/docs` 中对一份尚未提交的练习草稿发送错误确认短语，核对没有创建工单。不要用删除默认数据库的方式制造故障。

API 错误统一为：

```json
{
  "error": {
    "code": "错误代码",
    "message": "可阅读的错误说明",
    "request_id": "本次请求编号"
  }
}
```

| HTTP 状态码 | 含义 | 本课例子 |
| --- | --- | --- |
| `400` | 确认内容不正确 | 精确短语与当前草稿编号不匹配 |
| `404` | 记录不存在 | 查不到草稿或本地工单 |
| `422` | 输入未通过字段校验 | 缺必填字段、空内容或额外字段 |
| `502` | 模型整理失败 | 连接失败、超时或模型输出不符合要求 |
| `503` | 本地存储暂时不可用 | 数据库操作失败 |

服务的请求日志位于 `artifacts/day06_api.log`，每行是一个 JSON 对象。另开 PowerShell 终端查看最近几条：

```powershell
Get-Content artifacts/day06_api.log -Tail 10
```

日志记录 `request_id`、`method`、`path`、`status_code`、`error_code` 和 `elapsed_ms`。`path` 使用 `/api/drafts/{draft_id}` 这样的路由模板，成功请求的 `error_code` 为空。日志不记录请求正文或密钥。报修内容保存在学习数据库中；请求日志用来了解访问了哪个接口、是否成功、耗时多久。

每个响应还带有 `X-Request-ID` 响应头。错误 JSON 中的 `request_id` 与这次请求的日志编号一致，方便从页面报错找到对应日志。可以用错误中显示的实际编号搜索：

```powershell
$failedRequestId = Read-Host "粘贴错误中的 request_id"
Select-String -Path artifacts/day06_api.log -SimpleMatch $failedRequestId
```

先根据状态码判断问题发生在哪一层，再根据请求编号关联日志。不要仅凭页面“出错”就重新保存一份草稿。如果确认请求的结果不确定，先查原草稿或重试同一个草稿编号，沿用第 5 天的防重复规则。

## 150–180 分钟：读代码、运行检查与验收

按网页操作顺序阅读代码，不必一开始理解所有框架写法：

1. 阅读 `static/day06.html`：找到发送请求的位置，区分页面临时输入与接口返回的已保存草稿。
2. 阅读 `day06_api.py`：找到保存草稿、模型整理、查询、确认的路由；查看请求模型怎样约束输入。
3. 追踪确认接口到 `TicketStore.confirm_draft()`：确认第 5 天的事务和唯一约束仍负责最终写入。
4. 找到错误处理和请求日志：确认错误响应、响应头和日志使用同一个请求编号。

再运行自动检查：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试使用临时数据库和模拟模型，不发起真实 DeepSeek API 请求，不修改你在页面上创建的默认学习记录。测试覆盖已有行为；真实模型是否忠实整理输入，仍需用前面的自然语言练习人工核对。

| 验收项 | 需要观察的证据 |
| --- | --- |
| 网页保存与确认分开 | 保存后为 `pending`，尚无工单 |
| 取消不建单 | 取消后仍能查到原草稿 |
| 确认创建 | 草稿转为 `submitted`，返回本地工单 |
| 重复操作可重试 | 相同草稿返回同一工单，`created: false` |
| 持久化沿用 | 重开服务后仍能查到工单 |
| 模型只整理 | 信息不足时追问，完整时只保存草稿 |
| 接口可独立使用 | 能在 `/docs` 或 PowerShell 中查询与确认 |
| 错误可以定位 | 能用 `request_id` 找到对应日志 |

最后，用自己的话解释：浏览器负责什么、FastAPI 负责什么、模型负责什么、SQLite 负责什么？如果能沿着一次确认请求指出这四者的职责，就掌握了今天的主线。

今天的确认接口适用于本机单用户学习。精确短语用于表达本次确认，没有身份认证含义；部署和更完整的评测留到第 7 天。

## 参考

- [FastAPI 请求体与 Pydantic 模型](https://fastapi.tiangolo.com/tutorial/body/)
- [FastAPI 接口测试](https://fastapi.tiangolo.com/tutorial/testing/)
- [FastAPI 生命周期与资源管理](https://fastapi.tiangolo.com/advanced/events/)
