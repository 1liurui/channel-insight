"""检索语料构建。

把 semantic layer 的两份配置摊平成可检索单元：
  字段单元 —— 表名、列名、业务描述、枚举值，供 schema linking 定位到具体列
  指标单元 —— 指标名、别名、口径描述，命中后其 expr 与 caveat 直接注入 SQL 生成 prompt

语料默认只喂稠密一路。曾同时接 BM25 做混合检索，理由是「向量负责语义、BM25 负责专名」，
但消融把这个假设否掉了：10 张表下 BM25 净效应 −1.0，扩到 100 张表后恶化到 −2.3。
原因是陷阱表 fact_sales_archive 的表名里就含 sales，BM25 精确匹配反而更容易召回它，
而向量能读懂描述里「归档表，当前分析不应使用」的语义。规模越大，BM25 越有害。
代码路径保留，由 use_hybrid 开关控制，供消融复现。
"""

from __future__ import annotations

import os
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
    """WIDE_SCHEMA=1 时并入 90 张干扰表，把数仓从 10 张扩到 100 张。

    这是检索层的 schema 压力测试开关，默认关闭。现有 10 张表的裸 schema
    只有 1837 字符，模型一眼看得完，检索没有信息可增——扩表后才能量出
    schema linking 与混合检索的真实价值。见 scripts/build_wide_schema.py。
    """
    conf = yaml.safe_load((get_settings().conf_dir / "schema.yaml").read_text())
    extra = get_settings().conf_dir / "schema_extra.yaml"
    if os.getenv("WIDE_SCHEMA") == "1" and extra.exists():
        conf["tables"] = conf["tables"] + yaml.safe_load(extra.read_text())["tables"]
    return conf


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
