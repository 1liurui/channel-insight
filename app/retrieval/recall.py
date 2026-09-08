"""三路召回与 RRF 融合。

字段与指标各走稠密、稀疏两路后 RRF 融合；维度取值走词典精确匹配。
RRF 相比加权求和的好处是不需要归一化两侧分数——余弦相似度与 BM25 分数
量纲完全不同，加权融合必须先标定量纲，而 RRF 只用名次，无超参可调错。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.clients.vector import get_client
from app.retrieval.embedder import embed_one
from app.retrieval.fingerprint import extract
from app.retrieval.indexer import get_sparse, unit_map

RRF_K = 60


@dataclass
class Candidate:
    uid: str
    kind: str
    score: float
    payload: dict
    dense_rank: int | None = None
    sparse_rank: int | None = None


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion：只看名次不看分数，规避两路分数量纲不可比的问题。"""
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, uid in enumerate(ranking, start=1):
            fused[uid] = fused.get(uid, 0.0) + 1.0 / (k + rank)
    return fused


def dense(kind: str, query: str, k: int) -> list[str]:
    hits = get_client().query_points(f"sl_{kind}", query=embed_one(query), limit=k).points
    return [h.payload["uid"] for h in hits]


def sparse(kind: str, query: str, k: int) -> list[str]:
    return [uid for uid, score in get_sparse(kind).search(query, k) if score > 0]


def recall(kind: str, query: str, k: int = 15, hybrid: bool = True,
           rerank: bool = False) -> list[Candidate]:
    if rerank:
        # 向量召回一个大候选池，再由 cross-encoder 精排到 k 个。
        # 双塔的粗排负责覆盖率，交互式的精排负责准确率。
        from app.config import get_settings
        from app.retrieval.reranker import rerank as cross_rerank
        umap0 = unit_map(kind)
        pool = dense(kind, query, get_settings().rerank_pool)
        picked = cross_rerank(query, [umap0[u].text for u in pool], pool, k)
        return [Candidate(uid=u, kind=kind, score=1.0 / (i + 1), payload=umap0[u].payload,
                          dense_rank=pool.index(u) + 1)
                for i, u in enumerate(picked)]

    d = dense(kind, query, k * 2)
    s = sparse(kind, query, k * 2) if hybrid else []
    fused = rrf([d, s] if hybrid else [d])
    umap = unit_map(kind)
    out = [
        Candidate(uid=uid, kind=kind, score=score, payload=umap[uid].payload,
                  dense_rank=d.index(uid) + 1 if uid in d else None,
                  sparse_rank=s.index(uid) + 1 if uid in s else None)
        for uid, score in sorted(fused.items(), key=lambda x: -x[1])[:k]
    ]
    return out


def recall_values(question: str) -> list[dict]:
    """维度取值召回。取值是有限枚举，精确匹配比向量可靠，且能直接给出 WHERE 条件。"""
    fp = extract(question)
    out = []
    for tagged in fp.values:
        col, val = tagged.split("=", 1)
        out.append({"column": col, "value": val})
    return out
