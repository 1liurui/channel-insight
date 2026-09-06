"""问数服务层。

缓存查询放在图之外而非做成节点：它不属于「schema linking 至 SQL 执行」这条链路，
命中时整条图根本不该被唤起——这才是缓存能把响应从秒级压到毫秒级的原因。
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver

from app.agent.graph import build_graph
from app.agent.state import new_state
from app.clients import pg
from app.clients.cache import SemanticCache
from app.config import get_settings


@contextmanager
def durable_service(**kw):
    """带 PostgreSQL checkpointer 的服务。

    多轮对话的状态必须跨进程存活——服务重启即丢上下文是 demo 与生产的分界线。
    checkpointer 同时提供断点续跑：链路中途失败可从最后一个成功节点恢复，
    不必从头重跑已经花掉的模型调用。
    """
    with PostgresSaver.from_conn_string(get_settings().pg_dsn) as cp:
        cp.setup()
        yield AskService(checkpointer=cp, **kw)


@dataclass
class Answer:
    question: str
    rewritten: str = ""
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    error: str = ""
    error_type: str = ""
    retry: int = 0
    cache_hit: bool = False
    cache_score: float = 0.0
    latency_ms: float = 0.0
    llm_calls: int = 0
    tables: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    attempts: list[dict[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.sql)


class AskService:
    def __init__(self, use_cache: bool = True, checkpointer=None, **flags) -> None:
        self.graph = build_graph(checkpointer=checkpointer)
        self.cache = SemanticCache() if use_cache else None
        self.flags = flags

    def ask(self, question: str, history: list[dict] | None = None,
            session_id: str = "", log: bool = False) -> Answer:
        t0 = time.perf_counter()

        if self.cache is not None and not history:
            hit = self.cache.get(question)
            if hit is not None:
                p = hit.payload
                return Answer(question=question, sql=p["sql"], columns=p["columns"],
                              rows=p["rows"], row_count=p["row_count"], cache_hit=True,
                              cache_score=hit.score, tables=p.get("tables", []),
                              latency_ms=(time.perf_counter() - t0) * 1000)

        state = new_state(question, history, session_id=session_id, **self.flags)
        cfg = {"configurable": {"thread_id": session_id or "adhoc"}} if session_id else None
        out = self.graph.invoke(state, config=cfg)

        ans = Answer(
            question=question, rewritten=out.get("rewritten", ""), sql=out.get("sql", ""),
            columns=out.get("columns", []), rows=out.get("rows", []),
            row_count=out.get("row_count", 0), error=out.get("error", ""),
            error_type=out.get("error_type", ""), retry=out.get("retry", 0),
            llm_calls=out.get("llm_calls", 0), tables=out.get("tables", []),
            timings=out.get("timings", {}), attempts=out.get("attempts", []),
            latency_ms=(time.perf_counter() - t0) * 1000,
        )
        if ans.ok and self.cache is not None:
            self.cache.set(question, {"sql": ans.sql, "columns": ans.columns,
                                      "rows": ans.rows[:200], "row_count": ans.row_count,
                                      "tables": ans.tables})
        if log:
            pg.log_query(question=question, rewritten=ans.rewritten, generated_sql=ans.sql,
                         row_count=ans.row_count, success=ans.ok,
                         error_type=ans.error_type or None, error_detail=ans.error or None,
                         retry_count=ans.retry, cache_hit=ans.cache_hit,
                         latency_ms=int(ans.latency_ms))
        return ans

    def stream(self, question: str, history: list[dict] | None = None,
               session_id: str = "") -> Iterator[dict]:
        """逐节点流式输出。

        前端需要的不是「转圈等三秒」，而是看到链路走到哪一步——
        schema linking、检索、SQL 生成各自耗时多少，失败在哪个节点。
        LangGraph 的 updates 流模式天然提供这个粒度，不必自己埋点。
        """
        t0 = time.perf_counter()
        if self.cache is not None and not history:
            hit = self.cache.get(question)
            if hit is not None:
                p = hit.payload
                yield {"type": "cache_hit", "score": hit.score,
                       "latency_ms": (time.perf_counter() - t0) * 1000}
                yield {"type": "sql", "sql": p["sql"], "tables": p.get("tables", [])}
                yield {"type": "rows", "columns": p["columns"], "rows": p["rows"],
                       "row_count": p["row_count"]}
                yield {"type": "done", "cache_hit": True,
                       "latency_ms": (time.perf_counter() - t0) * 1000}
                return

        state = new_state(question, history, session_id=session_id, **self.flags)
        cfg = {"configurable": {"thread_id": session_id or "adhoc"}} if session_id else None
        merged: dict[str, Any] = dict(state)
        for chunk in self.graph.stream(state, config=cfg, stream_mode="updates"):
            for node, update in chunk.items():
                if not isinstance(update, dict):
                    continue
                merged.update({k: v for k, v in update.items() if k != "timings"})
                ev = {"type": "node", "node": node,
                      "elapsed_ms": (update.get("timings") or {}).get(node, 0.0)}
                if node == "rewrite_question" and update.get("rewritten") != question:
                    ev["rewritten"] = update.get("rewritten")
                if node == "fuse_candidates":
                    ev["tables"] = update.get("tables", [])
                if node in ("generate_sql", "correct_sql"):
                    ev["sql"] = update.get("sql", "")
                if node == "guardrail" and not update.get("guard_ok", True):
                    ev["guard_reason"] = update.get("guard_reason", "")
                yield ev

        if merged.get("sql"):
            yield {"type": "sql", "sql": merged["sql"], "tables": merged.get("tables", [])}
        if merged.get("error"):
            yield {"type": "error", "error": merged["error"],
                   "error_type": merged.get("error_type", "")}
        else:
            yield {"type": "rows", "columns": merged.get("columns", []),
                   "rows": merged.get("rows", []), "row_count": merged.get("row_count", 0)}
            if self.cache is not None and merged.get("sql"):
                self.cache.set(question, {
                    "sql": merged["sql"], "columns": merged.get("columns", []),
                    "rows": merged.get("rows", [])[:200],
                    "row_count": merged.get("row_count", 0),
                    "tables": merged.get("tables", [])})
        yield {"type": "done", "cache_hit": False,
               "retry": merged.get("retry", 0), "llm_calls": merged.get("llm_calls", 0),
               "rewritten": merged.get("rewritten", ""),
               "latency_ms": (time.perf_counter() - t0) * 1000}
