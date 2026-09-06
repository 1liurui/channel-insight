"""Qdrant 嵌入式客户端单例。

嵌入式模式对存储目录持有排他文件锁，同一进程内开第二个 client 直接抛
「already accessed by another instance」。这是无服务端方案的固有约束。

必须用锁而非 lru_cache：LangGraph 的并行节点跑在线程池里，
三路召回同时进入缓存未命中分支时会各自构造一个客户端，
lru_cache 只保证结果被缓存，不保证构造过程互斥。
"""

from __future__ import annotations

import atexit
import threading

from qdrant_client import QdrantClient

from app.config import get_settings

_client: QdrantClient | None = None
_lock = threading.Lock()


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        with _lock:
            if _client is None:                    # 双重检查，避免并发重复构造
                s = get_settings()
                s.qdrant_dir.mkdir(parents=True, exist_ok=True)
                c = QdrantClient(path=str(s.qdrant_dir),
                                 force_disable_check_same_thread=True)
                atexit.register(_safe_close, c)
                _client = c
    return _client


def _safe_close(client: QdrantClient) -> None:
    try:
        client.close()
    except Exception:                              # noqa: BLE001,S110  退出期不可靠
        pass
