# 第 1 天：用 Python 接通模型，完成意图分类

今天用约 3 小时，为“企业知识库与工单助手”建立可运行的基础：普通对话、结构化输出、类型校验和小规模评测。适合已有 Java/Go 后端经验、刚开始用 Python 的你。

本课对应 [Agent Learning Hub](https://github.com/datawhalechina/Agent-Learning-Hub) 的 Stage 0、Stage 1 前半部分，并提前引入 Stage 7 的评测习惯。工具调用循环留到第 2 天。

## 0–20 分钟：准备环境与配置

使用 Python 3.12、uv、openai SDK、Pydantic 和 python-dotenv。若还没有 uv，先按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/) 安装，再打开终端。

进入项目并准备解释器和依赖：

```bash
cd "/Users/ryanzhu/Desktop/vibe coding/agent-learning"
uv python install 3.12
uv sync
```

首次配置时复制模板；已有 `.env` 时直接编辑，避免覆盖原配置：

```bash
cp -n .env.example .env
```

在本地编辑 `.env`，填入自己的 DeepSeek 官方 API Key：

```dotenv
DEEPSEEK_API_KEY=在这里填入你的DeepSeek官方API密钥
DEEPSEEK_MODEL=deepseek-flash
```

密钥只保存在本地 `.env`，不要贴到聊天、截图或提交到 Git。OpenCode 中配置过 DeepSeek，并不会让这个 Python 项目自动获得配置。

理解三个参数：

| 参数 | 本项目使用什么 | 相当于什么 |
| --- | --- | --- |
| API Key | `.env` 中的 `DEEPSEEK_API_KEY` | 调用服务时的身份凭证 |
| Base URL | 代码固定为 `https://api.deepseek.com` | HTTP 服务地址 |
| model | `.env` 中的 `deepseek-flash` | 请求哪个模型 |

`openai` 是这里使用的客户端库，实际请求发给 DeepSeek 官方服务。配置依据见 [DeepSeek 首次调用 API](https://api-docs.deepseek.com/zh-cn/)。

## 20–60 分钟：用已有经验理解 Python

先运行离线练习，无需 API Key，也不会调用模型：

```bash
uv run day01_basics.py
```

首次安装依赖仍可能需要联网。运行后打开 `day01_basics.py`，对照下面的概念阅读代码：

| Java / Go 中的概念 | Python 中需要掌握的写法 |
| --- | --- |
| 代码块、花括号 | 冒号与一致缩进，通常每层 4 个空格 |
| `List` / slice | `list`，如 `messages = []`、`messages.append(item)` |
| `Map` / map | `dict`，如 `{"role": "user", "content": "你好"}` |
| `null` / `nil` | `None`；判断时使用 `value is None` |
| 方法 / 函数 | `def classify(text: str) -> str:` |
| 类型声明 | `name: str`；普通类型注解不会自动检查运行时输入 |
| DTO / struct 校验 | Pydantic 的 `BaseModel`，显式执行运行时校验 |
| `try/catch` / `error` | `try/except` 与异常；先学会看报错的最后几行 |
| 包引用 | `import`、`from ... import ...` |
| 程序入口 | `if __name__ == "__main__":` |

读懂这段类型声明即可，暂时不需要另建文件：

```python
from typing import Literal
from pydantic import BaseModel

class Classification(BaseModel):
    intent: Literal["knowledge_qa", "ticket_query", "incident_report"]
    summary: str
```

`Literal` 限定可选值；`summary: str` 声明字段类型。调用 Pydantic 的校验方法后，缺字段或非法意图会产生异常。字段合法也不代表模型理解正确，仍需要评测。

今天先掌握变量、集合、函数、模块、异常和基础类型。装饰器、元类、异步并发、复杂继承可以后续按需学习。

## 60–100 分钟：运行多轮对话

```bash
uv run day01_chat.py
```

逐条输入以下内容，观察模型是否利用当前会话中的前文：

```text
我负责一个订单服务，技术栈是 Java 和 MySQL。
我刚才提到的数据库是什么？
/clear
我刚才提到的数据库是什么？
/exit
```

`/clear` 清除本程序的会话历史；`/exit` 退出程序。清除后观察模型有没有说明缺少上下文，并记录实际回答。

打开 `day01_chat.py`，找到系统提示与消息列表：`system` 定义助手的行为要求，`user` 表示用户输入，`assistant` 表示模型回答。多轮效果来自程序继续发送历史消息。

这一步会实际调用 DeepSeek API 并按官方规则计费。本文没有替你执行真实调用，是否成功以你本地运行结果为准。

## 100–140 分钟：结构化输出与首次评测

运行意图分类：

```bash
uv run day01_classify.py "查询工单 T1001 的状态"
```

程序要求模型输出 JSON，再使用 Pydantic 校验 `intent` 和 `summary`。一种合法输出形态如下；摘要措辞可能不同：

```json
{"intent": "ticket_query", "summary": "查询工单 T1001 的状态"}
```

| 意图 | 今天的判断约定 | 示例 |
| --- | --- | --- |
| `knowledge_qa` | 询问知识、规范或操作方法 | 如何重置公司 VPN 密码？ |
| `ticket_query` | 查询已有工单 | 查询 T1001 的状态 |
| `incident_report` | 报告故障或请求处理问题 | 订单服务一直返回 500，帮我记录故障 |

**分类不会查询工单、创建工单或检索知识库。** 输出 `ticket_query` 只说明模型判断用户想查工单；没有数据库结果，就不能声称知道 T1001 的实际状态。

理解两层检查：[DeepSeek JSON Output](https://api-docs.deepseek.com/zh-cn/guides/json_mode/) 用于要求 JSON 格式；Pydantic 检查字段结构与允许值。JSON 格式正确、字段校验通过、业务判断正确，是三个不同条件。

阅读 `day01_classify.py`，找到 JSON 模式参数、包含 JSON 示例的系统提示、Pydantic 校验，以及错误处理。官方说明也提示 JSON 输出可能为空，应把这种情况视为失败，不能直接当作分类成功。

运行随项目提供的 5 条初始案例：

```bash
uv run day01_classify.py --eval
```

程序读取 `data/day01_cases.jsonl`，调用模型并生成 `artifacts/day01_report.json`。打开报告，对照预期意图、实际结果和错误；5 条样例只能帮助发现明显问题。

## 140–180 分钟：三个动手作业

1. **修改系统提示并比较。** 先保存初始评测结果，再明确补充一条业务边界，例如“查询已存在的工单归为 ticket_query”。用同一批案例重跑，记录哪些结果改变、哪些没有改变。每次只改一个因素。
2. **增加带预期结果的案例。** 在 `data/day01_cases.jsonl` 中参照现有字段格式增加 3 条，每行一个 JSON 对象。至少包含一条知识问答、一条工单查询、一条故障报告；先人工标注预期，再运行评测。
3. **解释 workflow 与 Agent。** 写下固定规则路由如何实现同样的三个类别，再解释第 2 天的 Agent 如何选择工具、接收工具结果并决定下一步。指出今天的模型分类器还缺少哪一段工具执行循环。

阅读控制在 15 分钟内：看 [HelloAgents 第 4 章](https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter4/%E7%AC%AC%E5%9B%9B%E7%AB%A0%20%E6%99%BA%E8%83%BD%E4%BD%93%E7%BB%8F%E5%85%B8%E8%8C%83%E5%BC%8F%E6%9E%84%E5%BB%BA.md) 的 4.1 和 4.2.1，重点理解客户端配置与“行动后读取观察结果”。今天无需照抄章节全部代码。

## 验收清单

- [ ] 离线练习可运行，能解释 `list`、`dict`、函数和 `BaseModel`。
- [ ] 能区分 API Key、Base URL、model，并在本地完成配置。
- [ ] 已亲自验证对话、`/clear` 和 `/exit`，记录真实结果。
- [ ] 分类输出经过 Pydantic 校验，能解释三个意图类别。
- [ ] 能说明为什么分类成功不代表实际查询或创建了工单。
- [ ] 已运行 5 条初始案例，查看报告，并新增 3 条标注案例。
- [ ] 能区分格式问题、校验问题和分类错误，记录至少一次提示修改比较。

若尚未成功调用 API，先记录具体阻塞；不要把未执行项标为完成。遇到鉴权错误检查本地密钥，额度错误查看 DeepSeek 控制台，模型错误核对模型名；分享报错时去掉密钥。

## 学习记录模板

```text
日期 / 实际用时：
离线练习结果：
对话及清除历史的实际结果：
评测案例数 / 通过数 / 错误数：
修改的提示与前后差异：
新增案例及预期意图：
一次失败的输入、错误类型和处理方式：
今天分类器实际完成了什么：
第 2 天需要增加的工具及执行循环：
仍未解决的阻塞：
```
