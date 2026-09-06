"""检索语料构建。

把 semantic layer 的两份配置摊平成可检索单元：
  字段单元 —— 表名、列名、业务描述、枚举值，供 schema linking 定位到具体列
  指标单元 —— 指标名、别名、口径描述，命中后其 expr 与 caveat 直接注入 SQL 生成 prompt

同一份语料同时喂给稠密与稀疏两路：向量负责「上个月卖得怎么样」这类无字面重叠的表达，
BM25 负责「fact_promo_cost」「铺市率」这类必须精确命中的专名。二者失效场景互补，
这正是混合检索在消融实验中能拿到独立增益的原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

import yaml

from app.config import get_settings


@dataclass
class Unit:
    uid: str
    kind: str                 # column | metric
    text: str                 # 用于检索的文本
    payload: dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def _schema_conf() -> dict:
    return yaml.safe_load((get_settings().conf_dir / "schema.yaml").read_text())


@lru_cache(maxsize=1)
def _metrics_conf() -> dict:
    return yaml.safe_load((get_settings().conf_dir / "metrics.yaml").read_text())


@lru_cache(maxsize=1)
def column_units() -> list[Unit]:
    units = []
    for t in _schema_conf()["tables"]:
        for c in t["columns"]:
            enum = "，".join(c.get("enum", []))
            text = f"{t['name']} {t['description']} {c['name']} {c['description']}"
            if enum:
                text += f" 取值范围 {enum}"
            units.append(Unit(
                uid=f"{t['name']}.{c['name']}", kind="column", text=text,
                payload={"table": t["name"], "column": c["name"], "type": c["type"],
                         "description": c["description"], "enum": c.get("enum", []),
                         "table_type": t["type"], "table_desc": t["description"],
                         "grain": t.get("grain", "")},
            ))
    return units


@lru_cache(maxsize=1)
def metric_units() -> list[Unit]:
    return [
        Unit(uid=m["id"], kind="metric",
             text=f"{m['name']} {' '.join(m.get('aliases', []))} {m['description']}",
             payload={"id": m["id"], "name": m["name"], "aliases": m.get("aliases", []),
                      "unit": m.get("unit", ""), "description": m["description"],
                      "expr": m["expr"].strip(), "caveat": m.get("caveat", "").strip(),
                      "tables": m["tables"]})
        for m in _metrics_conf()["metrics"]
    ]


def joins() -> list[str]:
    return _schema_conf()["joins"]


def cross_grain_warning() -> str:
    return _schema_conf().get("cross_grain_warning", "").strip()
