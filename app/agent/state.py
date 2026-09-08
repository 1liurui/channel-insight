"""链路状态。

LangGraph 的状态即链路的唯一真相来源。每个节点只写自己的字段，
并行节点写不同键，避免竞态——这是三路召回能并行的前提。
消融开关也放在状态里，评测时构一次图跑多组配置。
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class AgentState(TypedDict, total=False):
    # 输入
    question: str
    history: list[dict[str, str]]
    session_id: str

    # 预处理
    rewritten: str
    keywords: list[str]

    # 召回（三路并行，各写各的键）
    metric_hits: list[dict[str, Any]]
    column_hits: list[dict[str, Any]]
    value_hits: list[dict[str, Any]]
    literal_metrics: list[str]   # 问题中字面出现的指标 id，用作召回重排的硬先验

    # 规划
    tables: list[str]
    context: str

    # SQL
    sql: str
    guard_ok: bool
    guard_reason: str
    guard_hard: bool

    # 执行
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool

    # 纠错
    error: str
    error_type: str
    retry: int
    attempts: Annotated[list[dict[str, str]], operator.add]

    # 观测与消融
    timings: Annotated[dict[str, float], operator.or_]
    llm_calls: Annotated[int, operator.add]
    use_hybrid: bool
    use_rerank: bool
    use_retrieval: bool
    use_semantic_layer: bool
    force_metric_retrieval: bool
    use_self_correction: bool
    n_samples: int


def new_state(question: str, history: list[dict] | None = None, *,
              use_hybrid: bool = False, use_rerank: bool = False,
              use_retrieval: bool = True,
              use_semantic_layer: bool = True, force_metric_retrieval: bool = False,
              use_self_correction: bool = True, n_samples: int = 1,
              session_id: str = "") -> AgentState:
    return AgentState(
        question=question, history=history or [], session_id=session_id,
        rewritten=question, retry=0, timings={}, llm_calls=0, attempts=[],
        use_hybrid=use_hybrid, use_rerank=use_rerank, use_retrieval=use_retrieval,
        use_semantic_layer=use_semantic_layer,
        force_metric_retrieval=force_metric_retrieval,
        use_self_correction=use_self_correction, n_samples=n_samples,
    )
