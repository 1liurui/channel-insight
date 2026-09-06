"""索引构建与加载。稠密走 Qdrant 嵌入式，稀疏走 bm25s 进程内。

不用 Elasticsearch：语料只有百余条，起一个 JVM 服务端占 1GB 内存纯属浪费，
bm25s 进程内检索在这个量级上延迟更低。
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from functools import lru_cache

import bm25s
import jieba
from qdrant_client import models

from app.clients.vector import get_client
from app.config import ROOT
from app.retrieval.corpus import Unit, column_units, metric_units
from app.retrieval.embedder import DIM, embed

INDEX_DIR = ROOT / "data" / "index"
KINDS = {"column": column_units, "metric": metric_units}


_STOP = {"的", "是", "在", "了", "和", "与", "及", "有", "为", "不", "多少", "哪些", "什么"}


@lru_cache(maxsize=1)
def _load_userdict() -> int:
    """把行业术语灌进 jieba。默认词典会把「铺市率」切成「铺」+「市率」、
    把「传统食杂」切碎，导致 BM25 完全召不回——领域词典是中文稀疏检索的必需品，
    不是优化项。"""
    from app.retrieval.fingerprint import _metric_index, _value_index

    words = {"深度分销", "铺市率", "动销率", "动销", "铺货", "铺市", "单店产出", "费效比",
             "排面占比", "排面", "陈列", "临期", "周转", "业代", "业务代表", "终端网点",
             "经销商", "大卖场", "连锁超市", "便利店", "传统食杂", "特通", "堆头", "买赠",
             "净销额", "销额", "成单率", "达成率", "在途", "新品", "字段", "维度", "口径"}
    for _, terms in _metric_index():
        words.update(terms)
    for tagged, raw in _value_index():
        words.add(raw)
        words.add(tagged.split("=", 1)[0])
    for w in words:
        jieba.add_word(w, freq=10_000)
    return len(words)


def tokenize(text: str) -> list[str]:
    """中文检索必须先分词，英文表名列名再按下划线切开一并保留。"""
    _load_userdict()
    toks = [t.strip().lower() for t in jieba.lcut(text) if t.strip()]
    # 仅对 fact_promo_cost 这类复合标识符再切一次，避免普通词被重复计入词频
    extra = [p for t in toks if "_" in t or "." in t
             for p in t.replace(".", "_").split("_")]
    return [t for t in toks + extra if len(t) >= 2 and t not in _STOP]


@dataclass
class SparseIndex:
    retriever: bm25s.BM25
    uids: list[str]

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        idx, scores = self.retriever.retrieve([tokenize(query)], k=min(k, len(self.uids)),
                                              show_progress=False)
        return [(self.uids[int(i)], float(s)) for i, s in zip(idx[0], scores[0], strict=True)]


def build() -> dict[str, int]:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    client = get_client()
    counts = {}
    if True:
        for kind, loader in KINDS.items():
            units: list[Unit] = loader()
            coll = f"sl_{kind}"
            if client.collection_exists(coll):
                client.delete_collection(coll)
            client.create_collection(
                coll, vectors_config=models.VectorParams(size=DIM,
                                                         distance=models.Distance.COSINE))
            vecs = embed([u.text for u in units])
            client.upsert(coll, [
                models.PointStruct(id=i, vector=v, payload={**u.payload, "uid": u.uid,
                                                            "text": u.text})
                for i, (u, v) in enumerate(zip(units, vecs, strict=True))])

            retriever = bm25s.BM25()
            retriever.index([tokenize(u.text) for u in units], show_progress=False)
            with open(INDEX_DIR / f"bm25_{kind}.pkl", "wb") as f:
                pickle.dump({"retriever": retriever, "uids": [u.uid for u in units]}, f)
            counts[kind] = len(units)
    return counts


@lru_cache(maxsize=4)
def get_sparse(kind: str) -> SparseIndex:
    with open(INDEX_DIR / f"bm25_{kind}.pkl", "rb") as f:
        d = pickle.load(f)
    return SparseIndex(retriever=d["retriever"], uids=d["uids"])


@lru_cache(maxsize=4)
def unit_map(kind: str) -> dict[str, Unit]:
    return {u.uid: u for u in KINDS[kind]()}
