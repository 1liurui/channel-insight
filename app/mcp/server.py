"""MCP Server：把数仓、semantic layer 与归因能力暴露给 Claude Desktop / Cursor。

暴露的不是裸 SQL 通道，而是**带口径的能力**：
list_metrics 让客户端拿到 12 项指标的精确定义与陷阱说明，
query_warehouse 复用问数链路同一套 guardrail，
外部客户端因此不可能写出越权或跨粒度错关联的查询。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from mcp.server.mcpserver import MCPServer

from app.agent.nodes.sql import guardrail
from app.attribution.engine import DIMENSIONS, METRICS, attribute
from app.attribution.report import waterfall
from app.retrieval.corpus import column_units, joins, metric_units
from app.service import AskService
from app.warehouse import duck

mcp = MCPServer(
    "channel-insight",
    instructions="渠道运营数仓。写 SQL 前先调用 list_metrics 获取指标口径，"
                 "避免自行推断导致跨粒度关联等错误；只需业务答案时直接用 ask_data。",
)
_svc: AskService | None = None


def _service() -> AskService:
    global _svc
    if _svc is None:
        _svc = AskService(use_cache=True)
    return _svc


def _plain(o: Any) -> Any:
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    return o


@mcp.tool()
def ask_data(question: str) -> dict:
    """用自然语言查询渠道运营数仓，返回生成的 SQL 与结果。

    适用于「华东区上个月的净销额」「各渠道费效比排名」这类业务问题。
    指标口径由 semantic layer 保证，不需要调用方了解表结构。
    """
    a = _service().ask(question)
    return {"question": question, "sql": a.sql, "columns": a.columns,
            "rows": _plain(a.rows[:200]), "row_count": a.row_count,
            "cache_hit": a.cache_hit, "error": a.error or None,
            "latency_ms": round(a.latency_ms)}


@mcp.tool()
def query_warehouse(sql: str) -> dict:
    """直接执行只读 SQL。

    与问数链路共用同一套 guardrail：非 SELECT、写操作、越权表一律拒绝，
    并在执行前做 EXPLAIN 校验。结果最多返回 200 行。
    """
    g = guardrail({"sql": sql})
    if not g.get("guard_ok"):
        return {"error": g.get("guard_reason", "未通过安全校验"), "blocked": True}
    try:
        r = duck.run(sql, max_rows=200)
    except Exception as exc:                       # noqa: BLE001
        return {"error": str(exc).split("\n")[0][:300], "blocked": False}
    return {"columns": r.columns, "rows": _plain(r.rows), "row_count": r.row_count,
            "truncated": r.truncated, "elapsed_ms": round(r.elapsed_ms, 1)}


@mcp.tool()
def list_metrics() -> list[dict]:
    """列出 12 项业务指标的口径定义，含计算式与易错点说明。

    写 SQL 前先调用本工具，可避免自行推断口径导致的错误——
    例如费效比涉及日粒度与月粒度两张表，直接按 date_key 关联会得到错误结果。
    """
    return [{"id": u.payload["id"], "name": u.payload["name"],
             "aliases": u.payload["aliases"], "unit": u.payload["unit"],
             "description": u.payload["description"], "expr": u.payload["expr"],
             "caveat": u.payload["caveat"], "tables": u.payload["tables"]}
            for u in metric_units()]


@mcp.tool()
def describe_schema(table: str = "") -> dict:
    """返回数仓表结构与业务语义。不传 table 则返回全部表的概览。"""
    tables: dict[str, dict] = {}
    for u in column_units():
        p = u.payload
        if table and p["table"] != table:
            continue
        t = tables.setdefault(p["table"], {
            "name": p["table"], "type": p["table_type"],
            "description": p["table_desc"], "grain": p["grain"], "columns": []})
        t["columns"].append({"name": p["column"], "type": p["type"],
                             "description": p["description"], "enum": p["enum"]})
    if table and not tables:
        return {"error": f"表 {table} 不存在"}
    return {"tables": list(tables.values()), "joins": joins()}


@mcp.tool()
def attribute_metric(metric: str, t0: str, t1: str,
                     filters: dict[str, str] | None = None) -> dict:
    """对指标在两个月之间的变动做定量归因，返回主因与下钻路径。

    metric 取 net_sales / sales_qty / promo_cost（仅支持可加指标）。
    t0、t1 为 YYYY-MM。filters 可选，如 {"region": "华东"}。
    贡献度 = 该取值的变动量 ÷ 总变动量，累计超 80% 判为主因，最多下钻三层。
    """
    if metric not in METRICS:
        return {"error": f"{metric} 不是可加指标，归因仅支持 {'、'.join(METRICS)}"}
    bad = set(filters or {}) - set(DIMENSIONS)
    if bad:
        return {"error": f"未知维度 {'、'.join(bad)}，可用：{'、'.join(DIMENSIONS)}"}
    r = attribute(metric, t0, t1, filters=filters or {})
    if not r.significant:
        return {"significant": False,
                "message": f"{r.metric_label} 变动 {r.delta_pct:+.2f}%，未达显著阈值，无需归因"}
    return {"significant": True, "attribution": _plain(r.to_dict()),
            "waterfall": _plain(waterfall(r))}


if __name__ == "__main__":
    mcp.run()
