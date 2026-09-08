"""查询指纹。

用途一：语义缓存的硬闸门。纯向量相似度无法区分「华东/华南」「上月/本月」
「销售额/销量」这类替换——它们改变查询语义却几乎不改变句向量，
实测「换时间」的相似度反而高于所有真同义句。因此先用确定性规则抽出
指标、维度取值、时间三要素构成指纹，指纹不一致直接判定为不同查询，
向量只负责在指纹相同的前提下消化剩余的措辞差异。

用途二：M2 召回阶段的取值召回入口，命中的维度取值直接作为 SQL 的 WHERE 候选。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

import duckdb
import yaml

from app.config import ROOT, get_settings

_DICT_CACHE = ROOT / "data" / "value_dict.json"

# 低基数维度列：取值全量入字典
VALUE_COLUMNS: list[tuple[str, str]] = [
    ("dim_outlet", "channel"), ("dim_outlet", "outlet_grade"), ("dim_outlet", "region"),
    ("dim_outlet", "province"), ("dim_outlet", "city"), ("dim_outlet", "district"),
    ("dim_dealer", "dealer_level"), ("dim_dealer", "dealer_name"),
    ("dim_product", "brand"), ("dim_product", "category"), ("dim_product", "sub_category"),
    ("dim_product", "flavor"), ("dim_product", "package_type"),
    ("fact_promo_cost", "promo_type"), ("dim_date", "season"),
]

# 时间表达归一。顺序即优先级，先匹配到的胜出。
CN_DIGIT = {"一": "1", "二": "2", "三": "3", "四": "4"}

TIME_PATTERNS: list[tuple[str, str]] = [
    (r"(上上个?月|前个?月)", "MONTH-2"),
    (r"(上个?月|上一个?月|前一个?月)", "MONTH-1"),
    (r"(这个?月|本月|当月|今个?月)", "MONTH-0"),
    (r"(下个?月)", "MONTH+1"),
    (r"(上个?季度|上季)", "QUARTER-1"),
    (r"(这个?季度|本季度|当季)", "QUARTER-0"),
    (r"(去年同期|同比)", "YOY"),
    (r"(环比)", "MOM"),
    (r"(去年|上年)", "YEAR-1"),
    (r"(今年|本年|当年)", "YEAR-0"),
    (r"(上周|上个?星期)", "WEEK-1"),
    (r"(本周|这周|这个?星期)", "WEEK-0"),
    (r"(昨天|昨日)", "DAY-1"),
    (r"(今天|今日)", "DAY-0"),
    (r"近(\d+)\s*天", "LAST_{}_DAY"),
    (r"近(\d+)\s*个?月", "LAST_{}_MONTH"),
    (r"(\d{4})\s*年\s*(\d{1,2})\s*月", "{}-{}"),
    (r"(\d{4})\s*年", "{}"),
    (r"第?([一二三四1-4])\s*季度", "Q{}"),
]


@dataclass
class Fingerprint:
    metrics: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)   # "列名=取值"
    times: list[str] = field(default_factory=list)

    def key(self) -> str:
        return "|".join([",".join(sorted(self.metrics)), ",".join(sorted(self.values)),
                         ",".join(sorted(self.times))])

    def is_empty(self) -> bool:
        return not (self.metrics or self.values or self.times)


@lru_cache(maxsize=1)
def _metric_index() -> list[tuple[str, list[str]]]:
    """指标 id 与其全部触发词。长词优先，避免「销量」抢走「新品铺市率」的匹配。"""
    conf = yaml.safe_load((get_settings().conf_dir / "metrics.yaml").read_text())
    out = []
    for m in conf["metrics"]:
        terms = [m["name"], *m.get("aliases", [])]
        out.append((m["id"], sorted({t for t in terms if len(t) >= 2}, key=len, reverse=True)))
    return out


@lru_cache(maxsize=1)
def _value_index() -> list[tuple[str, str]]:
    """维度取值字典，按取值长度降序，保证「华东」不被「华」类子串误伤。"""
    if _DICT_CACHE.exists():
        pairs = json.loads(_DICT_CACHE.read_text())
    else:
        con = duckdb.connect(str(get_settings().duckdb_file), read_only=True)
        pairs = []
        for table, col in VALUE_COLUMNS:
            for (v,) in con.sql(
                f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL"
            ).fetchall():
                if isinstance(v, str) and len(v) >= 2:
                    pairs.append([f"{col}={v}", v])
        con.close()
        _DICT_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _DICT_CACHE.write_text(json.dumps(pairs, ensure_ascii=False))
    return sorted([(a, b) for a, b in pairs], key=lambda x: len(x[1]), reverse=True)


def _greedy_spans(cands: list[tuple[int, int, str]]) -> list[str]:
    """左优先、长优先的最大匹配。解决「华东区」中华东与东区重叠、
    「2026年8月」被年份模式重复吃掉、「新品铺市率」被铺市率抢走这三类误匹配。"""
    out, taken = [], []
    for start, end, tag in sorted(cands, key=lambda c: (c[0], -(c[1] - c[0]))):
        if any(not (end <= a or b <= start) for a, b in taken):
            continue
        taken.append((start, end))
        out.append(tag)
    return out


def extract(question: str) -> Fingerprint:
    q = question.strip()

    mc = [(m.start(), m.end(), mid)
          for mid, terms in _metric_index() for t in terms
          for m in re.finditer(re.escape(t), q)]
    vc = [(m.start(), m.end(), tagged)
          for tagged, raw in _value_index() for m in re.finditer(re.escape(raw), q)]
    tc = []
    for pat, tpl in TIME_PATTERNS:
        for m in re.finditer(pat, q):
            # 过滤掉整体匹配那类不含数字的捕获组（如「上个月」自身），
            # 但中文数字要先归一化——否则「第二季度」的「二」会被当成噪声滤掉，
            # 模板 Q{} 拿不到参数直接 IndexError，整题在指纹阶段就崩掉。
            groups = [CN_DIGIT.get(g, g) for g in m.groups() if g]
            groups = [g for g in groups if not re.fullmatch(r"[^\d]+", g)]
            if "{}" in tpl and len(groups) < tpl.count("{}"):
                continue
            tc.append((m.start(), m.end(), tpl.format(*groups) if "{}" in tpl else tpl))

    return Fingerprint(
        metrics=sorted(set(_greedy_spans(mc))),
        values=sorted(set(_greedy_spans(vc))),
        times=sorted(set(_greedy_spans(tc))),
    )


def scope_of(question: str) -> str:
    """缓存作用域。指纹为空时退化为独立作用域，宁可不命中也不错命中。"""
    fp = extract(question)
    return "nofp:" + question if fp.is_empty() else fp.key()
