"""数仓只读执行层。

连接以 read_only 打开——这是防越权的最后一道物理防线：
即使 guardrail 的静态检查被绕过，DuckDB 引擎本身也会拒绝任何写操作。
"""

from __future__ import annotations

import atexit
import threading
import time
from dataclasses import dataclass

import duckdb

from app.config import get_settings

MAX_ROWS = 2000
_root: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()
_local = threading.local()


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    row_count: int
    truncated: bool
    elapsed_ms: float


def _root_connection() -> duckdb.DuckDBPyConnection:
    global _root
    if _root is None:
        with _lock:
            if _root is None:
                c = duckdb.connect(str(get_settings().duckdb_file), read_only=True)
                c.execute("SET memory_limit='1GB'; SET threads=4;")
                atexit.register(_close)
                _root = c
    return _root


def _close() -> None:
    global _root
    if _root is not None:
        _root.close()
        _root = None


def connection() -> duckdb.DuckDBPyConnection:
    """单一底层连接 + 每线程 cursor。

    DuckDB 连接对象本身非线程安全，官方并发读的做法是从同一连接派生 cursor；
    若改成每线程各开一个连接，这些连接会在解释器关闭时以不确定顺序析构，
    在 LangGraph 的线程池场景下会触发 recursive_mutex 崩溃。
    """
    cur = getattr(_local, "cur", None)
    if cur is None:
        cur = _root_connection().cursor()
        _local.cur = cur
    return cur


def run(sql: str, max_rows: int = MAX_ROWS) -> QueryResult:
    t0 = time.perf_counter()
    cur = connection().sql(sql)
    if cur is None:                      # DuckDB 对无结果集的语句返回 None
        raise ValueError("该语句不返回结果集")
    rows = cur.fetchmany(max_rows + 1)
    truncated = len(rows) > max_rows
    return QueryResult(columns=[d[0] for d in cur.description], rows=rows[:max_rows],
                       row_count=min(len(rows), max_rows), truncated=truncated,
                       elapsed_ms=(time.perf_counter() - t0) * 1000)


def explain(sql: str) -> str:
    return connection().sql(f"EXPLAIN {sql}").fetchone()[1]


def table_names() -> set[str]:
    return {r[0] for r in connection().sql("SHOW TABLES").fetchall()}
