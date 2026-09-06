"""语义缓存。

不做字符串精确匹配——业务人员问同一件事的措辞每次都不同，
「华东区上月销售额」和「上个月华东的销额是多少」应当命中同一条缓存。
实现是问题向量入 Qdrant，余弦相似度超阈值即视为同一问题，
再用其携带的 key 去 Redis 取结果。Redis 负责带 TTL 的值存储，Qdrant 负责相似判定。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

import redis
from qdrant_client import models

from app.clients.vector import get_client
from app.config import get_settings
from app.retrieval.embedder import embed_one
from app.retrieval.fingerprint import scope_of

COLLECTION = "semantic_cache"
DEFAULT_THRESHOLD = 0.88  # 指纹已做硬隔离，此处只消化措辞差异，可放宽
DEFAULT_TTL = 3600


@dataclass
class CacheHit:
    key: str
    score: float
    payload: dict
    elapsed_ms: float


class SemanticCache:
    def __init__(self, threshold: float = DEFAULT_THRESHOLD, ttl: int = DEFAULT_TTL) -> None:
        s = get_settings()
        self.threshold, self.ttl = threshold, ttl
        self.redis = redis.from_url(s.redis_url, decode_responses=True)
        self.qdrant = get_client()
        self._ensure()

    def _ensure(self) -> None:
        if not self.qdrant.collection_exists(COLLECTION):
            self.qdrant.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(size=512, distance=models.Distance.COSINE),
            )

    @staticmethod
    def _key(question: str, scope: str) -> str:
        return "qc:" + hashlib.sha1(f"{scope}|{question}".encode()).hexdigest()[:20]

    def get(self, question: str, scope: str | None = None) -> CacheHit | None:
        t0 = time.perf_counter()
        scope = scope or scope_of(question)
        vec = embed_one(question)
        hits = self.qdrant.query_points(
            COLLECTION, query=vec, limit=1, score_threshold=self.threshold,
            query_filter=models.Filter(must=[models.FieldCondition(
                key="scope", match=models.MatchValue(value=scope))]),
        ).points
        if not hits:
            return None
        key = hits[0].payload["key"]
        raw = self.redis.get(key)
        if raw is None:          # Redis 已过期而向量还在，清掉向量保持两侧一致
            self.qdrant.delete(COLLECTION, points_selector=[hits[0].id])
            return None
        return CacheHit(key=key, score=hits[0].score, payload=json.loads(raw),
                        elapsed_ms=(time.perf_counter() - t0) * 1000)

    def set(self, question: str, payload: dict, scope: str | None = None) -> str:
        scope = scope or scope_of(question)
        key = self._key(question, scope)
        self.redis.setex(key, self.ttl, json.dumps(payload, ensure_ascii=False, default=str))
        self.qdrant.upsert(COLLECTION, [models.PointStruct(
            id=int(hashlib.sha1(key.encode()).hexdigest()[:12], 16),
            vector=embed_one(question),
            payload={"key": key, "scope": scope, "question": question},
        )])
        return key

    def clear(self, scope: str | None = None) -> None:
        if self.qdrant.collection_exists(COLLECTION):
            self.qdrant.delete_collection(COLLECTION)
        self._ensure()
        for k in self.redis.scan_iter("qc:*"):
            self.redis.delete(k)
