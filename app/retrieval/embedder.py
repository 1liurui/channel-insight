"""向量化。模型进程内单例，避免每次调用重复加载 ONNX 权重。"""

from __future__ import annotations

from functools import lru_cache

from fastembed import TextEmbedding

from app.config import get_settings

DIM = 512


@lru_cache(maxsize=1)
def get_embedder() -> TextEmbedding:
    return TextEmbedding(model_name=get_settings().embed_model)


def embed(texts: list[str]) -> list[list[float]]:
    return [v.tolist() for v in get_embedder().embed(texts)]


def embed_one(text: str) -> list[float]:
    return embed([text])[0]
