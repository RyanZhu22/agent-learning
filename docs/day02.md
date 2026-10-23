# 第 2 天：让模型提出工具请求，由 Python 执行查询

你已完成第 1 天的真实聊天和 5 条分类评测。今天把 `day01_basics.py` 中的 `get_ticket` 接入模型，完成 [Agent Learning Hub Stage 1](https://github.com/datawhalechina/Agent-Learning-Hub#stage-1-build-a-minimal-agent-loop) 的工具调用循环。

目标只有一个：给出工单编号，让模型选择 `get_ticket`，由 Python 查询，再让模型根据查询结果回答。这里使用本地 mock 工单，不连接企业真实系统。

## 先运行，再对照每一步

沿用第 1 天的 `.env`。代码请求 DeepSeek 官方 API，使用非思考模式；密钥继续只保存在本地。

```bash
cd "/Users/ryanzhu/Desktop/vibe coding/agent-learning"
uv run day02_agent.py "查询工单 T1003 的状态"
```

建议用约 2–3 小时完成：先运行观察 20 分钟，阅读工具定义 30 分钟，追踪消息循环 40 分钟，完成练习和记录 40 分钟。

当前 `day01_basics.py` 的模拟工单如下；以你本地文件的实际内容为准：

| 编号 | 标题 | 状态 |
| --- | --- | --- |
| T1001 | 无法登录 | 处理中 |
| T1002 | 导出失败 | 已解决 |
| T1003 | 支付失败 | 待处理 |

运行时依次观察：每轮模型请求、模型选择的工具和 JSON 参数、Python 执行返回的结构化结果、最终回答。以下内容均为流程示意，不是替你完成的真实 API 测试。

## 三件事分别由谁完成

| 步骤 | 谁负责 | T1003 示例 |
| --- | --- | --- |
| 决定是否查询以及查询哪个编号 | 模型 | 请求 `get_ticket`，参数为 `{"ticket_id":"T1003"}` |
| 验证参数并查询数据 | Python | 调用你写的 `get_ticket("T1003")` |
| 根据结果组织回答 | 模型 | 根据返回数据说明标题“支付失败”、状态“待处理” |

模型收到的工具说明不是 Python 函数代码。发送给 API 的是名称、用途和参数结构；模型不会因此自动读取 `TICKETS`，也不会在你的电脑上执行函数。

打开 `day02_agent.py`，找到 `TicketQuery`、`TOOLS` 和 `TOOL_FUNCTIONS`：

```python
class TicketQuery(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    ticket_id: str = Field(pattern=r"^T[0-9]{4}$")

TOOL_FUNCTIONS = {"get_ticket": get_ticket}
```

`TicketQuery` 要求参数是字符串、编号符合 `T` 加 4 位数字，而且不能多传字段。`strict=True` 是本地 Pydantic 校验配置，不代表开启了 DeepSeek 的服务端 Beta strict 模式。

`TOOLS` 包含工具的 `name`、`description`，并用 `TicketQuery.model_json_schema()` 生成 `parameters`。这是给模型看的接口契约。`TOOL_FUNCTIONS` 是 Python 的允许列表，只有其中注册的函数才可以执行。

回想 Java/Go：Schema 类似接口文档，Pydantic 类似请求参数校验，允许列表类似明确注册的处理器；它们都不等于函数的执行结果。

## 第一次 API 调用：请求还没有查询结果

输入仍然是“查询工单 T1003 的状态”。第一次请求传入 `messages` 和 `tools=TOOLS`。消息示意如下，系统提示为简写：

```python
messages = [
    {"role": "system", "content": "按需查询工单，不编造查询结果。"},
    {"role": "user", "content": "查询工单 T1003 的状态"},
]
```

模型可能返回一条带 `tool_calls` 的 assistant 消息。注意 `arguments` 是包含 JSON 文本的字符串：

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [{
    "id": "call_example_1",
    "type": "function",
    "function": {"name": "get_ticket", "arguments": "{\"ticket_id\":\"T1003\"}"}
  }]
}
```

这表示“模型请求程序查询 T1003”，还不表示查询已经完成。Python 接下来检查工具名、解析并校验 JSON 参数，然后调用允许列表中的 `get_ticket`。

成功执行后，程序包装的返回形态示意如下：

```json
{"ok": true, "data": {"ticket_id": "T1003", "title": "支付失败", "status": "待处理"}}
```

## 第二次 API 调用：把执行结果交还模型

第二次请求的 `messages` 包含原消息、模型刚提出的请求，以及 Python 的执行结果。下面是相同内容的紧凑示意；实际调用 ID 由 API 产生：

```python
messages = [
    {"role": "system", "content": "按需查询工单，不编造查询结果。"},
    {"role": "user", "content": "查询工单 T1003 的状态"},
    {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call_example_1", "type": "function", "function": {
            "name": "get_ticket", "arguments": '{"ticket_id":"T1003"}'
        }
    }]},
    {"role": "tool", "tool_call_id": "call_example_1", "content": json.dumps({
        "ok": True, "data": {"ticket_id": "T1003", "title": "支付失败", "status": "待处理"}
    }, ensure_ascii=False)},
]
```

先保留 assistant 的 `tool_calls`，再追加每个 `role="tool"` 结果；`tool_call_id` 必须对应原请求的 `id`。`content` 是序列化后的字符串，不是直接放入 Python 字典。

模型据此决定回复或继续请求工具。对于这个简单查询，常见路径是两次模型调用：第一次选工具，第二次组织答案；回答措辞可能不同，关键是事实应与工具结果一致。

调用次数并非固定为两次。缺少编号时模型可以追问；无需查询时可以直接回答。没有待执行的工具请求且产生了正常回复时，本次运行就结束，不强制调用工具。

## 看懂失败与停止条件

默认最多 4 轮模型请求，每次运行最多执行 6 次工具调用。可用 `--max-steps` 调整模型轮数，允许范围为 1–8：

```bash
uv run day02_agent.py "查询工单 T1003 的状态" --max-steps 1
```

如果第一轮请求工具，这个预算通常不足以完成后续模型答复。观察程序如何明确停止，区分“已取得工具结果”和“已完成最终回答”。一次模型请求可能包含多个工具调用，所以两个预算分别计数。

未知工具、非法参数和查无工单都返回结构化错误，由模型读取后决定下一步；它们不应变成“查询成功”。如果模型输出因长度限制而截断（`length`），程序不会执行其中的工具请求。预算耗尽时明确停止，避免无限循环。

每次命令只处理一个问题，不保存跨命令会话。模型追问编号后，再次运行时请提供完整问题，例如“查询工单 T1003 的状态”；新进程不会自动继承上次追问。

## 三个动手练习

1. **查询不存在的编号。** 运行 `uv run day02_agent.py "查询工单 T9999 的状态"`。核对 Python 的查无工单错误与模型答复，检查是否虚构了状态。
2. **故意缺少编号。** 运行 `uv run day02_agent.py "帮我查一下工单状态"`。观察模型是否要求补充信息；若擅自猜编号或无效调用，记录为失败案例。
3. **增加数据再查询。** 在 `day01_basics.py` 的 `TICKETS` 中新增 T1004，自定标题与状态，再运行 `uv run day02_agent.py "查询工单 T1004 的状态"`。核对调用参数、返回数据和最终回答。

这三个练习先不修改模型调用协议。第 3 个练习也不需要把 T1004 写进提示词；新增数据应通过函数结果进入模型上下文。

## 验收与记录

- [ ] 能分别指出模型请求、Python 执行和模型答复发生在哪里。
- [ ] 能解释 `TOOLS` 与 `TOOL_FUNCTIONS` 的区别，以及 Pydantic 的校验职责。
- [ ] 能在记录中对应 assistant 调用 ID 与 tool 结果的 `tool_call_id`。
- [ ] 已实际验证 T1003、T9999、缺少编号和自加的 T1004，记录事实是否一致。
- [ ] 理解模型轮数、工具执行次数和停止条件，知道本程序不保留跨命令历史。

记录模板：输入｜模型轮数｜工具名与参数｜工具执行结果｜最终回答是否符合结果｜失败原因或下一步。不要把示意输出算作实际完成。

参考 [DeepSeek 官方 Tool Calls](https://api-docs.deepseek.com/zh-cn/guides/tool_calls/) 的非思考模式示例，重点对照“模型本身不执行函数”以及请求与执行结果如何配对。
