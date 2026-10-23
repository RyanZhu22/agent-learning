# Python Agent 学习项目

以 [Agent-Learning-Hub](https://github.com/datawhalechina/Agent-Learning-Hub) 为能力清单，以 [Hello-Agents](https://github.com/datawhalechina/hello-agents) 为主教材。目标是逐步完成一个知识库与工单助手。

每天约 3 小时。目前已提供第 1–4 天的练习，其余功能随学习推进。

| 日期 | 实践目标 |
| --- | --- |
| 1 | Python 必要语法、模型调用、多轮聊天、结构化分类 |
| 2 | 手写工具调用循环，注册工单查询工具 |
| 3 | 文档切分、检索、带来源的回答 |
| 4 | 串起知识库、工单查询与报修草稿 |
| 5 | 确认后建单、持久化、失败处理、防重复 |
| 6 | FastAPI 接口、简单页面、日志 |
| 7 | 固定评测、部署说明、演示和失败分析 |

## 从这里开始

在当前项目目录打开终端：

```sh
cd "/Users/ryanzhu/Desktop/vibe coding/agent-learning"
uv sync
uv run day01_basics.py
```

上面的练习完全离线，使用模拟工单数据。

继续阅读 [第 1 天讲义](docs/day01.md)，然后配置真实模型：

```sh
cp -n .env.example .env
```

在本地 `.env` 中填入 `DEEPSEEK_API_KEY`。本项目直连 DeepSeek 官方服务，默认模型为 `deepseek-flash`，地址为 `https://api.deepseek.com`。

```sh
uv run day01_chat.py
uv run day01_classify.py "查询工单 T1001 的状态"
uv run day01_classify.py --eval
```

聊天每条消息调用一次真实 API。评测对每个案例调用一次 API，报告保存到 `artifacts/day01_report.json`。聊天与分类目前均未接入业务工具，不会查询真实工单，也不会创建工单。

## 文件阅读顺序

1. `day01_basics.py`：字典、函数、异常和模拟工单。
2. `llm_config.py`：加载环境变量，创建客户端。
3. `day01_chat.py`：发送消息，保留历史，观察耗时和 Token。
4. `day01_classify.py`：要求 JSON 输出，再使用 Pydantic 校验业务字段。
5. `data/day01_cases.jsonl`：5 个初始案例，逐步扩展到至少 20 个。

`.env`、虚拟环境和评测报告已列入 `.gitignore`。密钥只放本地文件，不贴到聊天或提交到仓库。

## 第 2 天：让模型查询工单

阅读 [第 2 天讲义](docs/day02.md)，运行：

```sh
uv run day02_agent.py "查询工单 T1003 的状态"
```

`day02_agent.py` 复用你在 `day01_basics.py` 中编写的 `get_ticket()` 和模拟数据。
观察模型请求的工具名、参数、Python 执行结果和最终回复。一次查询通常需要两轮模型请求；也可能直接追问或需要更多轮次。

```sh
uv run day02_agent.py "查询工单 T9999 的状态"
uv run day02_agent.py "帮我查一下工单"
```

本课是单个问题的工具调用循环，历史只保存在本次进程中。每轮模型请求有 30 秒超时、默认最多 4 轮，工具请求最多处理 6 次；预算耗尽会明确停止。工单仍然来自本地模拟数据。

## 第 3 天：检索知识库，再根据资料回答

阅读 [第 3 天讲义](docs/day03.md)。`knowledge/` 包含 10 篇虚构的演示产品文档。
依赖已加入本项目；从新环境运行时先执行 `uv sync`。

先构建索引，再单独观察检索结果：

```sh
uv run day03_rag.py --index
uv run day03_rag.py --search-only "邮件通知失败怎么办？"
```

本地中文模型 `BAAI/bge-small-zh-v1.5` 负责生成 512 维向量，Chroma 保存和检索文档片段。
首次建索引会下载公开模型，不需要新增 API Key。模型缓存完成后，单独检索只使用本地文件。

确认找到了正确资料，再让 DeepSeek 基于片段回答：

```sh
uv run day03_rag.py "邮件通知失败怎么办？"
```

这一步会发起一次真实 DeepSeek API 请求。程序验证来源编号来自本次检索，但仍需要人工核对引用是否支持回答。
相似度和检索命中率都不是生成答案正确率；最近的片段也可能没有答案。

```sh
uv run day03_rag.py --eval
```

评测包含 8 条资料内检索题和 2 条资料外观察题，不调用 DeepSeek；报告位于 `artifacts/day03_retrieval_report.json`。
修改、新增或删除知识文档后，重新运行 `--index`。重建只替换本课程专用 collection，避免旧片段累积。

`day03_retrieval.py` 实现本地切分和检索；`day03_rag.py` 提供命令入口、答案字段与来源校验。
本课先实现固定的 RAG 流程，第 4 天再将检索接入 Agent 的工具选择循环。

## 第 4 天第一部分：同一个 Agent 选择工具

阅读 [第 4 天讲义](docs/day04.md)。保留前面所有文件，使用新的入口连接已完成的工单查询与知识检索：

```sh
uv run day04_agent.py "查询工单 T1004 的状态"
uv run day04_agent.py "邮件通知首次发送失败后，最多重试多少次？"
uv run day04_agent.py "查询工单 T1004 的状态，并告诉我邮件通知失败如何排查"
```

模型选择 get_ticket 或 search_docs，Python 按各自的参数模型执行工具，随后由同一个模型循环生成回复。
检索工具只返回本地片段，不在内部额外调用生成模型。来源编号在本次运行内保持一致。
每轮模型请求会调用真实 DeepSeek API。

## 第 4 天第二部分：整理报修草稿

同一个入口已增加 prepare_ticket_draft 工具。先测试信息不足时是否追问：

```sh
uv run day04_agent.py "我要报修：邮件通知失败，请帮我整理工单草稿。"
```

再按第 4 天讲义输入完整的描述、复现步骤、期望与实际结果、发生时间。
day04_draft.py 定义六个草稿字段及整理函数；标题由模型概括，其余字段来自用户。
草稿仅展示，不保存或实际建单；跨命令没有历史，补充信息时要重新提供完整描述。

## 参考资料

- [Hello-Agents 第四章](https://github.com/datawhalechina/hello-agents/blob/main/docs/chapter4/第四章%20智能体经典范式构建.md)
- [DeepSeek 首次调用](https://api-docs.deepseek.com/)
- [DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)
- [FastEmbed 入门](https://qdrant.github.io/fastembed/Getting%20Started/)
- [FastEmbed 支持模型](https://qdrant.github.io/fastembed/examples/Supported_Models/)
- [Chroma 集合配置](https://docs.trychroma.com/docs/collections/configure)

第 1 天显式关闭 thinking，先学习请求、消息和结果的完整流程。后续工具调用阶段再按官方文档学习相应参数。
