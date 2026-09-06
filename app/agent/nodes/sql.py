"""SQL 生成、护栏、执行与自纠错。"""

from __future__ import annotations

import re

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.nodes._util import timed
from app.agent.state import AgentState
from app.clients.llm import get_llm
from app.warehouse import duck

MAX_RETRY = 2
ALLOWED_TABLES = {"dim_date", "dim_product", "dim_dealer", "dim_outlet", "dim_rep",
                  "fact_sales", "fact_visit", "fact_promo_cost", "fact_inventory",
                  "rel_outlet_sku"}
# 写操作与系统操作。命中即硬拦截，不进纠错环——这类语句不该由自然语言问答产生。
FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE|GRANT|REVOKE|"
    r"ATTACH|DETACH|COPY|EXPORT|IMPORT|INSTALL|LOAD|PRAGMA|SET|CALL)\b", re.IGNORECASE)
TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

SYSTEM = "你是资深数据分析工程师，只输出可直接执行的 DuckDB SQL，不输出任何解释。"

GEN_PROMPT = """根据下面的表结构与指标口径，为用户问题写一条 DuckDB SQL。

{context}

硬性要求：
1. 只写一条 SELECT 语句，禁止任何写操作，禁止分号后追加语句。
2. 严格使用给出的关联路径，不要自行推断外键。
3. 若上文给出了指标计算式，必须原样采用，不得改写口径。
4. 时间筛选一律通过 JOIN dim_date 后用 full_date 或 year_month，不要对 date_key 做日期函数。
5. 结果超过 200 行时自行加 LIMIT，排名类问题默认取前 10。
6. 给聚合列起中文别名，便于业务阅读。
7. **只返回回答问题所必需的列**，不要附带用于计算的中间列。
   问「哪些 X」只返回 X 的标识列；问「多少 / 比例 / 增长率」只返回该数值列；
   问「各 X 的 Y」返回 X 与 Y 两列。标识对象优先用名称列而非 id 列。
8. 表别名不得使用 SQL 保留字。禁用 do、in、is、as、to、by、or、and、all、any、end、
   for、from、left、right、full、order、group、select 作别名；
   dim_outlet 用 o、dim_dealer 用 dl、dim_date 用 d、dim_product 用 p、
   fact_sales 用 s、fact_promo_cost 用 pc。

用户问题：{question}

SQL："""

FIX_PROMPT = """你上一次生成的 SQL 执行失败了，请修正。

{context}

失败的 SQL：
{sql}

错误信息：
{error}

请仔细检查表名列名是否存在、关联路径是否正确、聚合与分组是否匹配。
只输出修正后的完整 SQL，不要解释。

用户问题：{question}

SQL："""


FENCE = re.compile(r"```(?:sql|SQL)?\s*\n(.*?)\n?```", re.DOTALL)
SQL_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE | re.MULTILINE)
# 中文标点或「说明/注」开头的行，是解释文字不是 SQL
PROSE = re.compile(r"^\s*(说明|注|解释|备注|以上|该查询|本查询)[:：]|[。；！？]\s*$")


def _clean(text: str) -> str:
    """从模型输出里取出可执行 SQL。

    模型偶尔会在 SQL 前后附解释文字，或把 SQL 包在代码围栏里再加一段说明。
    只剥围栏是不够的：无围栏而有前后文时，整段会被当成 SQL 送进执行器，
    报出的却是「syntax error near ...」这类看不出根因的错误。
    """
    t = text.strip()
    if (m := FENCE.search(t)):
        t = m.group(1).strip()
    elif (m := SQL_START.search(t)):
        t = t[m.start():].strip()          # 丢掉 SELECT/WITH 之前的开场白
    lines = t.splitlines()
    while lines and PROSE.search(lines[-1]):   # 丢掉结尾的说明段
        lines.pop()
    return "\n".join(lines).strip().rstrip(";").strip()


@timed("generate_sql")
def generate_sql(state: AgentState) -> dict:
    msg = get_llm().invoke([
        SystemMessage(content=SYSTEM),
        HumanMessage(content=GEN_PROMPT.format(
            context=state["context"], question=state.get("rewritten") or state["question"])),
    ])
    return {"sql": _clean(msg.content), "llm_calls": 1}


@timed("guardrail")
def guardrail(state: AgentState) -> dict:
    """执行前的静态校验。三层：语句类型、表白名单、语法与列存在性。

    硬拦截（写操作）直接终止，软拦截（表名列名错误）交给 self-correction。
    """
    sql = state.get("sql", "")
    if not sql:
        return {"guard_ok": False, "guard_hard": True, "guard_reason": "未生成 SQL"}
    if ";" in sql.strip().rstrip(";"):
        return {"guard_ok": False, "guard_hard": True, "guard_reason": "检测到多语句拼接"}
    body = re.sub(r"'[^']*'", "''", sql)          # 屏蔽字符串字面量再做关键字匹配
    if not re.match(r"^\s*(SELECT|WITH)\b", body, re.IGNORECASE):
        return {"guard_ok": False, "guard_hard": True, "guard_reason": "非 SELECT 语句"}
    if (m := FORBIDDEN.search(body)):
        return {"guard_ok": False, "guard_hard": True,
                "guard_reason": f"检测到禁止的操作：{m.group(1).upper()}"}
    refs = {t.lower() for t in TABLE_REF.findall(body)}
    cte = {c.lower() for c in re.findall(r"(?:WITH|,)\s+([A-Za-z_]\w*)\s+AS\s*\(", body, re.IGNORECASE)}
    if (bad := refs - ALLOWED_TABLES - cte):
        return {"guard_ok": False, "guard_hard": False,
                "guard_reason": f"引用了不存在或未授权的表：{'、'.join(sorted(bad))}"}
    try:
        duck.explain(sql)
    except Exception as exc:                       # noqa: BLE001
        return {"guard_ok": False, "guard_hard": False,
                "guard_reason": str(exc).split("\n")[0][:300]}
    return {"guard_ok": True, "guard_hard": False, "guard_reason": ""}


def classify_error(msg: str) -> str:
    m = msg.lower()
    if "parser error" in m or "syntax error" in m:
        return "语法错误"
    if "catalog error" in m or "does not have a column" in m or "not found" in m:
        return "召回缺失"
    if "binder error" in m or "referenced column" in m or "group by" in m:
        return "逻辑错误"
    if "conversion" in m or "cast" in m or "type mismatch" in m:
        return "口径歧义"
    return "逻辑错误"


@timed("execute_sql")
def execute_sql(state: AgentState) -> dict:
    if not state.get("guard_ok"):
        return {"error": state.get("guard_reason", ""),
                "error_type": classify_error(state.get("guard_reason", ""))}
    try:
        r = duck.run(state["sql"])
    except Exception as exc:                       # noqa: BLE001
        detail = str(exc).split("\n")[0][:300]
        return {"error": detail, "error_type": classify_error(detail)}
    return {"columns": r.columns, "rows": [list(x) for x in r.rows],
            "row_count": r.row_count, "truncated": r.truncated, "error": "", "error_type": ""}


@timed("correct_sql")
def correct_sql(state: AgentState) -> dict:
    """携错重写。把失败的 SQL 与引擎原始错误一并回灌，比让模型盲猜有效得多。"""
    msg = get_llm().invoke([
        SystemMessage(content=SYSTEM),
        HumanMessage(content=FIX_PROMPT.format(
            context=state["context"], sql=state.get("sql", ""),
            error=state.get("error") or state.get("guard_reason", ""),
            question=state.get("rewritten") or state["question"])),
    ])
    return {"sql": _clean(msg.content), "retry": state.get("retry", 0) + 1, "llm_calls": 1,
            "attempts": [{"sql": state.get("sql", ""),
                          "error": state.get("error") or state.get("guard_reason", "")}]}
