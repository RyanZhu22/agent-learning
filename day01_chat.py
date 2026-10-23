"""第一个模型程序：多轮聊天、会话历史、耗时与 Token 用量。"""

from time import perf_counter

from openai import APIConnectionError, APIStatusError, APITimeoutError

from llm_config import create_client


# 作业 1：修改这里的风格约束，对同一个问题比较输出。
SYSTEM_PROMPT = "你是软件产品的客服助手。请用简洁中文回答；未知事实应明确说明。"


def main() -> int:
    try:
        client, model = create_client()
    except ValueError as error:
        print(error)
        return 1

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print(f"DeepSeek 官方 API · 模型：{model}")
    print("每条消息会调用一次真实 API。/clear 清空历史，/exit 退出。")
    print("本程序尚未连接知识库和工单工具，不能查询真实工单。")

    with client:
        while True:
            try:
                question = input("\n你：").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已退出。")
                return 0
            if question == "/exit":
                return 0
            if question == "/clear":
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                print("会话历史已清空。")
                continue
            if not question:
                continue

            # 先构造候选历史；请求成功后才更新已完成的对话。
            request_messages = [*messages, {"role": "user", "content": question}]
            started = perf_counter()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=request_messages,
                    max_tokens=512,
                    stream=False,
                    extra_body={"thinking": {"type": "disabled"}},
                )
            except APITimeoutError:
                print("请求超时。未自动重试；请稍后再试。")
                continue
            except APIConnectionError:
                print("连接失败，请检查网络连接。")
                continue
            except APIStatusError as error:
                print(f"API 返回 HTTP {error.status_code}，请检查密钥、余额和模型名称。")
                continue

            if not response.choices:
                print("模型返回了空结果，本轮未写入历史。")
                continue
            choice = response.choices[0]
            answer = choice.message.content
            if choice.finish_reason != "stop" or not answer or not answer.strip():
                print(f"回答未完整结束（{choice.finish_reason}），本轮未写入历史。")
                continue
            messages = [*request_messages, {"role": "assistant", "content": answer}]
            print(f"\n助手：{answer}")
            usage = response.usage
            tokens = usage.total_tokens if usage is not None else "未提供"
            print(f"[耗时 {perf_counter() - started:.2f}s | 本次请求 Token {tokens}]")


if __name__ == "__main__":
    raise SystemExit(main())
