"""HTTP 服务。

问数接口走 SSE 逐节点推送：业务人员等三秒看到的不该是一个转圈，
而是链路走到了哪一步、每步花了多久、失败在哪个节点。
这个粒度不需要额外埋点，LangGraph 的 updates 流模式天然提供。
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import iterate_in_threadpool

from app.attribution.engine import DIMENSIONS, METRICS, attribute
from app.attribution.report import narrate, waterfall
from app.clients import pg
from app.config import ROOT
from app.retrieval.corpus import column_units, metric_units
from app.service import AskService

app = FastAPI(title="业务数据问数与归因智能体", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"])

_svc: AskService | None = None


def service() -> AskService:
    global _svc
    if _svc is None:
        _svc = AskService(use_cache=True)
    return _svc


def jsonable(o: Any) -> Any:
    """DuckDB 返回 Decimal 与 date，标准 json 不认，统一在出口转换。"""
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    if isinstance(o, dict):
        return {k: jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    return o


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    history: list[dict[str, str]] = Field(default_factory=list)
    session_id: str = ""


class AttributeRequest(BaseModel):
    metric: str = "net_sales"
    t0: str
    t1: str
    filters: dict[str, str] = Field(default_factory=dict)
    narrate: bool = False


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "postgres": pg.healthy(),
            "metrics": len(metric_units()), "columns": len(column_units())}


@app.get("/api/metrics")
def list_metrics() -> list[dict]:
    return [{"id": u.payload["id"], "name": u.payload["name"],
             "aliases": u.payload["aliases"], "unit": u.payload["unit"],
             "description": u.payload["description"], "expr": u.payload["expr"],
             "caveat": u.payload["caveat"], "tables": u.payload["tables"]}
            for u in metric_units()]


@app.get("/api/schema")
def list_schema() -> list[dict]:
    tables: dict[str, dict] = {}
    for u in column_units():
        p = u.payload
        t = tables.setdefault(p["table"], {"name": p["table"], "type": p["table_type"],
                                           "description": p["table_desc"],
                                           "grain": p["grain"], "columns": []})
        t["columns"].append({"name": p["column"], "type": p["type"],
                             "description": p["description"], "enum": p["enum"]})
    return list(tables.values())


@app.post("/api/ask")
async def ask(req: AskRequest) -> EventSourceResponse:
    def gen():
        try:
            for ev in service().stream(req.question, req.history, req.session_id):
                yield {"event": ev["type"],
                       "data": json.dumps(jsonable(ev), ensure_ascii=False)}
        except Exception as exc:                            # noqa: BLE001
            yield {"event": "error",
                   "data": json.dumps({"type": "error", "error": str(exc)[:300],
                                       "error_type": "服务异常"}, ensure_ascii=False)}

    return EventSourceResponse(iterate_in_threadpool(gen()))


@app.get("/api/attribute/options")
def attribute_options() -> dict:
    return {"metrics": [{"id": k, "label": v[2], "unit": v[3]} for k, v in METRICS.items()],
            "dimensions": [{"id": k, "label": v[2]} for k, v in DIMENSIONS.items()]}


@app.post("/api/attribute")
def do_attribute(req: AttributeRequest) -> dict:
    if req.metric not in METRICS:
        raise HTTPException(400, f"指标 {req.metric} 不可加，归因只支持可加指标")
    bad = set(req.filters) - set(DIMENSIONS)
    if bad:
        raise HTTPException(400, f"未知维度：{'、'.join(bad)}")
    r = attribute(req.metric, req.t0, req.t1, filters=req.filters)
    out = {"attribution": jsonable(r.to_dict()), "waterfall": jsonable(waterfall(r))}
    if req.narrate:
        out["narrative"] = narrate(r)
    return out


@app.post("/api/shelf/audit")
def shelf_audit() -> dict:
    raise HTTPException(501, "陈列稽核模块尚未上线（M5）")


# 构建产物存在时由后端一并托管，演示只需起一个进程；
# 开发期走 Vite dev server，由其 proxy 转发 /api，故此处不影响热更新。
_DIST = ROOT / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="frontend")
