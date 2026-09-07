"""评测执行。五组配置：主方案、裸模型基线、三组消融。"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.clients.llm import get_llm
from app.retrieval.corpus import column_units
from app.service import AskService
from app.warehouse import duck
from eval.runner import compare, is_ordered, load_cases

# 五组配置。baseline 不走图，直连裸 schema 单次生成，是"不做任何工程"的下界。
CONFIGS: dict[str, dict[str, Any]] = {
    "full":               {},
    "baseline":           {"_bare": True},
    "no_semantic_layer":  {"use_semantic_layer": False},
    "hybrid_bm25":        {"use_hybrid": True},
    "no_self_correction": {"use_self_correction": False},
    "no_retrieval":       {"use_retrieval": False},
}
CONFIG_LABEL = {
    "full": "主方案（全链路）",
    "baseline": "基线：裸模型直连 schema",
    "no_semantic_layer": "消融：移除 semantic layer",
    "hybrid_bm25": "消融：加回 BM25 混合检索",
    "no_self_correction": "消融：移除 self-correction",
    "no_retrieval": "消融：不做检索，全量 schema + semantic layer",
}

BARE_SYSTEM = "你是资深数据分析工程师，只输出可直接执行的 DuckDB SQL，不输出任何解释。"
BARE_PROMPT = """下面是数据库的全部表结构，请为用户问题写一条 DuckDB SQL。

{schema}

只返回回答问题所必需的列，不要附带用于计算的中间列。
问「哪些 X」只返回 X 的标识列；问「多少 / 比例 / 增长率」只返回该数值列；
问「各 X 的 Y」返回 X 与 Y 两列。标识对象优先用名称列而非 id 列。

用户问题：{question}

SQL："""

TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
# API 侧故障绝不能记成模型失败——那会安静地压低准确率，产出一个看似正常的假数字
API_ERROR = re.compile(r"Error code: (4\d\d|5\d\d)|Insufficient Balance|Too many requests"
                       r"|rate.?limit|timed? ?out|Connection", re.IGNORECASE)
MAX_API_ERROR_RATE = 0.02


class EvalAborted(RuntimeError):
    """API 故障率过高时中止评测，而不是把故障算作模型错误。"""
KNOWN_TABLES = {u.payload["table"] for u in column_units()}


def bare_schema() -> str:
    """裸 schema：只有表名、列名、类型，没有业务描述、没有关联路径、没有指标口径。"""
    by_table: dict[str, list[str]] = {}
    for u in column_units():
        p = u.payload
        by_table.setdefault(p["table"], []).append(f"{p['column']} {p['type']}")
    return "\n".join(f"{t}({', '.join(cols)})" for t, cols in by_table.items())


def gold_tables(sql: str) -> set[str]:
    return {t.lower() for t in TABLE_REF.findall(sql)} & KNOWN_TABLES


@dataclass
class CaseResult:
    id: str
    tier: int
    question: str
    config: str
    correct: bool = False
    executed: bool = False
    error_type: str = ""
    error_detail: str = ""
    api_error: bool = False
    reason: str = ""
    sql: str = ""
    retry: int = 0
    latency_ms: float = 0.0
    tables: list[str] = field(default_factory=list)


def classify(case: dict, ans_tables: list[str], executed: bool, err: str,
             err_type: str) -> str:
    """失败四分类。

    召回缺失的判定是确定性的：gold SQL 引用的表若未进入链路选出的表清单，
    则无论后面写得多好都不可能对——这比事后猜测可靠。
    """
    if not executed:
        return err_type or "语法错误"
    need = gold_tables(case["gold_sql"])
    if need and not need <= {t.lower() for t in ans_tables}:
        return "召回缺失"
    tags = set(case.get("tags", []))
    metric_tags = {"费效比", "铺市率", "动销率", "单店产出", "拜访达成率", "成单率",
                   "促销费用率", "库存周转", "临期占比", "新品铺市率"}
    if case["tier"] == 4 or tags & metric_tags:
        return "口径歧义"
    return "逻辑错误"


def _run_bare(case: dict) -> tuple[str, float, int]:
    t0 = time.perf_counter()
    msg = get_llm().invoke([
        SystemMessage(content=BARE_SYSTEM),
        HumanMessage(content=BARE_PROMPT.format(schema=bare_schema(),
                                                question=case["question"])),
    ])
    sql = msg.content.strip()
    if sql.startswith("```"):
        sql = re.sub(r"^```[a-zA-Z]*\n?", "", sql)
        sql = re.sub(r"\n?```$", "", sql.strip())
    return sql.strip().rstrip(";"), (time.perf_counter() - t0) * 1000, 1


def run_case(case: dict, config: str, svc: AskService | None) -> CaseResult:
    r = CaseResult(id=case["id"], tier=case["tier"], question=case["question"], config=config)
    try:
        if config == "baseline":
            sql, ms, _ = _run_bare(case)
            r.sql, r.latency_ms, r.tables = sql, ms, list(KNOWN_TABLES)
        else:
            a = svc.ask(case["question"])
            r.sql, r.latency_ms, r.retry, r.tables = a.sql, a.latency_ms, a.retry, a.tables
            if a.error:
                r.error_type, r.error_detail = a.error_type, a.error[:200]
    except Exception as exc:                            # noqa: BLE001
        detail = str(exc)
        r.error_detail = detail[:200]
        if API_ERROR.search(detail):
            r.api_error, r.error_type = True, "API 故障"
        else:
            r.error_type = "语法错误"
        return r

    if not r.sql:
        r.error_type = r.error_type or "语法错误"
        return r
    try:
        pred = duck.run(r.sql)
        r.executed = True
    except Exception as exc:                            # noqa: BLE001
        from app.agent.nodes.sql import classify_error
        r.error_detail = str(exc).split("\n")[0][:200]
        r.error_type = classify_error(r.error_detail)
        return r

    gold = duck.run(case["gold_sql"])
    cmp = compare(gold.rows, pred.rows, is_ordered(case))
    r.correct, r.reason = cmp.match, cmp.reason
    if not r.correct:
        r.error_type = classify(case, r.tables, True, "", "")
    return r


def run_config(config: str, cases: list[dict], workers: int = 4) -> list[CaseResult]:
    """workers 默认 4：DeepSeek 按余额分配并发额度，超过即 429，
    而 429 若被当成模型失败会直接污染准确率。"""
    flags = {k: v for k, v in CONFIGS[config].items() if not k.startswith("_")}
    svc = None if config == "baseline" else AskService(use_cache=False, **flags)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda c: run_case(c, config, svc), cases))
    n_api = sum(r.api_error for r in results)
    if n_api > len(cases) * MAX_API_ERROR_RATE:
        sample = next(r.error_detail for r in results if r.api_error)
        raise EvalAborted(
            f"{config}：{n_api}/{len(cases)} 题因 API 故障失败，结果不可用。首个错误：{sample}")
    return results


def to_dicts(results: list[CaseResult]) -> list[dict]:
    return [asdict(r) for r in results]


__all__ = ["CONFIGS", "CONFIG_LABEL", "EvalAborted", "load_cases", "run_config",
           "to_dicts"]
