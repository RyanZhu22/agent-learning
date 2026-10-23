"""第 1 天只共用配置；模型请求保留在各练习中，便于阅读。"""

import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


def create_client() -> tuple[OpenAI, str]:
    # 明确加载本项目的 .env，已有环境变量优先。
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip()
    if not api_key:
        raise ValueError(
            "尚未配置 DEEPSEEK_API_KEY。请复制 .env.example 为 .env，"
            "在本地填入 DeepSeek 官方密钥。"
        )
    if not model:
        raise ValueError("DEEPSEEK_MODEL 不能为空。")

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com",
        timeout=30.0,
        max_retries=0,
    )
    return client, model
