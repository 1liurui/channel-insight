"""Cross-encoder 重排。

向量检索是双塔结构：query 与候选各自独立编码，交互只发生在最后的余弦相似度上，
细粒度匹配信号在编码阶段就丢了。schema 一大就暴露出来——300 张表 2157 个字段下，
「净销额」的向量与总账表的「金额」字段相当接近，真表被干扰表挤出候选，
压力测试里的「召回缺失」正是这么来的。

cross-encoder 把 query 与候选拼在一起过一遍模型，交互发生在每一层，
排序质量明显更高，代价是无法预计算、必须在线逐对打分，所以只用于对
向量召回出的小候选池做重排，不能拿来扫全库。

模型走 fastembed 的 ONNX 运行时，与 embedder 同一套依赖，不引入 torch。
"""
from __future__ import annotations

from functools import lru_cache

from fastembed.rerank.cross_encoder import TextCrossEncoder

from app.config import get_settings


@lru_cache(maxsize=1)
def get_reranker() -> TextCrossEncoder:
    return TextCrossEncoder(model_name=get_settings().rerank_model)


def rerank(query: str, docs: list[str], uids: list[str], k: int) -> list[str]:
    """对候选池重排后取前 k 个 uid。"""
    if not docs:
        return []
    scores = list(get_reranker().rerank(query, docs))
    return [u for _, u in sorted(zip(scores, uids), key=lambda x: -x[0])][:k]
