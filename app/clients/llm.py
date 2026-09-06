"""LLM 接入层。

对外只暴露 get_llm() / get_vlm()，上层节点不感知具体厂商。
换模型只改 .env，不改调用点。
"""

from functools import lru_cache

from langchain_deepseek import ChatDeepSeek

from app.config import get_settings


class LLMNotConfigured(RuntimeError):
    """缺少 API Key 时抛出，附带申请指引。"""


@lru_cache
def get_llm(temperature: float = 0.0, max_tokens: int = 2048) -> ChatDeepSeek:
    """主模型。SQL 生成与结论转述均走这里，默认 temperature=0 保证可复现。"""
    s = get_settings()
    if not s.deepseek_api_key:
        raise LLMNotConfigured(
            "未配置 DEEPSEEK_API_KEY。前往 https://platform.deepseek.com 申请后写入 .env"
        )
    return ChatDeepSeek(
        model=s.deepseek_model,
        api_key=s.deepseek_api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=3,          # 429 由 SDK 按指数退避重试，避免并发抖动被记成模型失败
        timeout=60,
    )


def is_llm_ready() -> bool:
    return bool(get_settings().deepseek_api_key)


def is_vlm_ready() -> bool:
    return bool(get_settings().dashscope_api_key)
