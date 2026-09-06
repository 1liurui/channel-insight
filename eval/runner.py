"""评测执行与结果比对。

执行准确率（execution accuracy）而非 SQL 文本匹配：同一个问题有无数种正确写法，
比文本必然低估。比对方式是把两侧结果集各自归一化后作多重集比较——
行序不敏感（除非题目要求排序，此时 gold 自带 ORDER BY 且比对保持顺序），
浮点按有效数字容差，Decimal 与 int 统一为 float。

这是 Spider / BIRD 采用的口径，严格：列数不同即判错，多返回一列也算错。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import yaml

from app.config import ROOT

TESTSET = ROOT / "eval" / "testset.yaml"
SIG = 4          # 数值比对保留的有效数字位数


def load_cases() -> list[dict]:
    return yaml.safe_load(TESTSET.read_text())["cases"]


def _norm_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return "nan"
        if f == 0:
            return 0.0
        # 按有效数字取整，吸收浮点累加顺序造成的末位差异
        return round(f, -math.floor(math.log10(abs(f))) + SIG - 1)
    return str(v).strip()


def normalize(rows: list) -> list[tuple]:
    return [tuple(_norm_value(v) for v in r) for r in rows]


@dataclass
class Comparison:
    match: bool
    reason: str = ""


def compare(gold_rows: list, pred_rows: list, ordered: bool) -> Comparison:
    g, p = normalize(gold_rows), normalize(pred_rows)
    if not g and not p:
        return Comparison(True)
    if len(g) != len(p):
        return Comparison(False, f"行数不同 gold={len(g)} pred={len(p)}")
    if g and p and len(g[0]) != len(p[0]):
        return Comparison(False, f"列数不同 gold={len(g[0])} pred={len(p[0])}")
    if ordered:
        return Comparison(g == p, "" if g == p else "有序比对不一致")
    gs, ps = sorted(g, key=repr), sorted(p, key=repr)
    return Comparison(gs == ps, "" if gs == ps else "多重集比对不一致")


def is_ordered(case: dict) -> bool:
    """题目要求排名或趋势时保持顺序敏感。"""
    sql = case["gold_sql"].upper()
    return "ORDER BY" in sql and "ROW_NUMBER" not in sql
