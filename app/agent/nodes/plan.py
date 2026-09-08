"""规划：把召回结果组装成注入 SQL 生成的上下文。"""

from __future__ import annotations

import datetime as dt

from app.agent.nodes._util import timed
from app.agent.state import AgentState
from app.retrieval.corpus import column_units as all_columns
from app.retrieval.corpus import cross_grain_warning, joins

DATA_START = dt.date(2024, 9, 1)
DATA_END = dt.date(2026, 8, 31)
TODAY = dt.date(2026, 9, 4)


def _join_tables(join: str) -> set[str]:
    """从 "fact_sales.date_key = dim_date.date_key" 取出两侧表名。"""
    return {side.strip().split(".")[0] for side in join.split("=")}


def _table_block(table: str, with_semantics: bool) -> str:
    cols = [u for u in all_columns() if u.payload["table"] == table]
    if not cols:
        return ""
    head = cols[0].payload
    lines = [f"表 {table}" + (f"（{head['table_desc']}）" if with_semantics else "")]
    if with_semantics and head.get("grain"):
        lines.append(f"  粒度：{head['grain']}")
    for u in cols:
        p = u.payload
        line = f"  {p['column']} {p['type']}"
        if with_semantics:
            line += f" —— {p['description']}"
            if p.get("enum"):
                line += f"（取值：{'、'.join(p['enum'])}）"
        lines.append(line)
    return "\n".join(lines)


@timed("filter_schema")
def filter_schema(state: AgentState) -> dict:
    """确定最终进上下文的表。命中指标声明的依赖表必须补齐，
    否则会出现「选中了费效比却没带上费用表」这类必错场景。"""
    tables = list(state.get("tables", []))
    for m in state.get("metric_hits", [])[:1]:
        for t in m["tables"]:
            if t not in tables:
                tables.append(t)
    # 事实表在场时补上其常用维表，避免 GROUP BY 无维度可用
    if any(t.startswith("fact_") for t in tables) and "dim_date" not in tables:
        tables.append("dim_date")
    return {"tables": tables}


# 口径全量注入的上限。指标数不超过这个值时直接全塞，超过才走检索。
#
# 检索注入只给 top-2，而复合指标题往往要两个以上口径（费效比要销额与费用、
# 库存周转要库存与销量），漏一个就必错——实测全量注入 82.3% 对检索注入 80.8%，
# T4 复合指标档 78.7% 对 64.7%，差 14 个百分点。
#
# 但不能无条件全塞：口径带 caveat 后每项约 300 字符，指标上百时会挤爆上下文，
# 且无关口径本身就是干扰。所以按规模分流，而不是二选一。
METRIC_INJECT_ALL_MAX = 20


def _metric_context(state: AgentState) -> list[dict]:
    from app.retrieval.corpus import metric_units
    if state.get("force_metric_retrieval"):
        return state.get("metric_hits", [])[:2]
    units = metric_units()
    if len(units) <= METRIC_INJECT_ALL_MAX:
        return [u.payload for u in units]
    return state.get("metric_hits", [])[:2]


@timed("build_context")
def build_context(state: AgentState) -> dict:
    """组装 semantic layer 上下文。

    use_semantic_layer=False 时退化为裸 schema（仅表名列名类型），
    这正是消融实验里「移除 semantic layer」的对照组。
    """
    sl = state.get("use_semantic_layer", True)
    parts = [(f"数据库方言：DuckDB。今天是 {TODAY:%Y-%m-%d}，"
              f"数据覆盖 {DATA_START:%Y-%m-%d} 至 {DATA_END:%Y-%m-%d}。"), "", "可用表结构："]
    parts += [b for t in state.get("tables", []) if (b := _table_block(t, sl))]

    if sl:
        sel = set(state.get("tables", []))
        rel = [j for j in joins() if _join_tables(j) <= sel]
        parts += ["", "关联路径（只允许使用以下等值关联）："] + [f"  {j}" for j in rel or joins()]
        hits = _metric_context(state)
        if hits:
            parts += ["", "相关指标口径（必须严格按此计算，不得自行发挥）："]
            for m in hits:
                parts.append(f"  【{m['name']}】{m['description']}")
                parts.append(f"    计算式：{m['expr']}")
                if m.get("caveat"):
                    parts.append(f"    注意：{m['caveat']}")
        if cross_grain_warning():
            parts += ["", "跨粒度告警：" + cross_grain_warning()]
    if state.get("value_hits"):
        parts += ["", "问题中识别到的维度取值（可直接用于 WHERE）："]
        parts += [f"  {v['column']} = '{v['value']}'" for v in state["value_hits"]]
    return {"context": "\n".join(parts)}
