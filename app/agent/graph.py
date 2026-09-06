"""LangGraph 链路编排。

12 个节点，覆盖指代消解到 SQL 执行。三路召回并行 fan-out / fan-in，
执行失败进 self-correction 环最多两轮后仍失败则终止。

checkpointer 落 PostgreSQL 而非内存：多轮对话的状态必须跨进程存活，
否则服务重启即丢上下文，这也是 LangGraph 生产部署与 demo 的分界线。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agent.nodes.plan import build_context, filter_schema
from app.agent.nodes.preprocess import extract_keywords, rewrite_question
from app.agent.nodes.retrieve import (
    fuse_candidates,
    recall_column,
    recall_metric,
    recall_value,
)
from app.agent.nodes.sql import MAX_RETRY, correct_sql, execute_sql, generate_sql, guardrail
from app.agent.state import AgentState

NODES = [
    ("rewrite_question", rewrite_question),
    ("extract_keywords", extract_keywords),
    ("recall_column", recall_column),
    ("recall_metric", recall_metric),
    ("recall_value", recall_value),
    ("fuse_candidates", fuse_candidates),
    ("filter_schema", filter_schema),
    ("build_context", build_context),
    ("generate_sql", generate_sql),
    ("guardrail", guardrail),
    ("execute_sql", execute_sql),
    ("correct_sql", correct_sql),
]


def _after_guardrail(state: AgentState) -> str:
    """硬拦截直接终止：写操作不该由自然语言问答产生，重试也只会再产生一次。"""
    if state.get("guard_ok"):
        return "execute_sql"
    if state.get("guard_hard") or not state.get("use_self_correction", True):
        return "execute_sql"
    return "correct_sql" if state.get("retry", 0) < MAX_RETRY else "execute_sql"


def _after_execute(state: AgentState) -> str:
    if not state.get("error"):
        return END
    if not state.get("use_self_correction", True) or state.get("retry", 0) >= MAX_RETRY:
        return END
    return "correct_sql"


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)
    for name, fn in NODES:
        g.add_node(name, fn)

    g.add_edge(START, "rewrite_question")
    g.add_edge("rewrite_question", "extract_keywords")
    for r in ("recall_column", "recall_metric", "recall_value"):
        g.add_edge("extract_keywords", r)        # fan-out：三路召回并行
        g.add_edge(r, "fuse_candidates")         # fan-in：LangGraph 自动等齐
    g.add_edge("fuse_candidates", "filter_schema")
    g.add_edge("filter_schema", "build_context")
    g.add_edge("build_context", "generate_sql")
    g.add_edge("generate_sql", "guardrail")
    g.add_conditional_edges("guardrail", _after_guardrail,
                            {"execute_sql": "execute_sql", "correct_sql": "correct_sql"})
    g.add_conditional_edges("execute_sql", _after_execute,
                            {END: END, "correct_sql": "correct_sql"})
    g.add_edge("correct_sql", "guardrail")       # 纠错后重走护栏，不绕过安全检查
    return g.compile(checkpointer=checkpointer)
