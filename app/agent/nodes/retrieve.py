"""召回：字段、指标、取值三路并行，前两路各自混合检索后 RRF 融合。"""

from __future__ import annotations

from app.agent.nodes._util import timed
from app.agent.state import AgentState
from app.retrieval.recall import recall, recall_values

TOP_COLUMN = 18
TOP_METRIC = 4


@timed("recall_column")
def recall_column(state: AgentState) -> dict:
    q = state.get("rewritten") or state["question"]
    hits = recall("column", q, k=TOP_COLUMN, hybrid=state.get("use_hybrid", True))
    return {"column_hits": [{"uid": c.uid, "score": c.score, **c.payload} for c in hits]}


@timed("recall_metric")
def recall_metric(state: AgentState) -> dict:
    q = state.get("rewritten") or state["question"]
    hits = recall("metric", q, k=TOP_METRIC, hybrid=state.get("use_hybrid", True))
    return {"metric_hits": [{"uid": c.uid, "score": c.score, **c.payload} for c in hits]}


@timed("recall_value")
def recall_value(state: AgentState) -> dict:
    """维度取值走词典精确匹配。取值是有限枚举，向量在这里只会带来误召回。"""
    q = state.get("rewritten") or state["question"]
    return {"value_hits": recall_values(q)}


@timed("fuse_candidates")
def fuse_candidates(state: AgentState) -> dict:
    """三路汇合后按表打分选表。

    只给 SQL 生成看命中的零散列会导致 JOIN 写错——列必须以「表」为单位整块给出，
    因此这里的产物是表清单而非列清单。
    """
    score: dict[str, float] = {}
    for c in state.get("column_hits", []):
        score[c["table"]] = score.get(c["table"], 0.0) + c["score"]
    for m in state.get("metric_hits", [])[:2]:
        for t in m["tables"]:                      # 指标声明的依赖表强制入选
            score[t] = score.get(t, 0.0) + 0.05
    for v in state.get("value_hits", []):
        for c in state.get("column_hits", []):
            if c["column"] == v["column"]:
                score[c["table"]] = score.get(c["table"], 0.0) + 0.02
    tables = [t for t, _ in sorted(score.items(), key=lambda x: -x[1])[:5]]
    return {"tables": tables}
