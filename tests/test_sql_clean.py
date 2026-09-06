"""模型输出到可执行 SQL 的清洗。

模型偶尔会附解释文字。只剥代码围栏不够——无围栏而有前后文时整段会被送进执行器，
报出「syntax error near ...」这类看不出根因的错误，排查成本远高于在此拦一道。
"""

import pytest

from app.agent.nodes.sql import _clean

SQL = "SELECT region, SUM(net_amount) AS 净销额\nFROM fact_sales\nGROUP BY region"


@pytest.mark.parametrize("raw", [
    SQL,
    f"```sql\n{SQL}\n```",
    f"```\n{SQL}\n```",
    f"{SQL};",
    f"根据表结构，可以这样写：\n\n{SQL}",
    f"```sql\n{SQL}\n```\n\n说明：本查询按大区汇总净销额。",
    f"{SQL}\n\n注：结果按大区分组。",
    f"好的，下面是 SQL。\n\n```sql\n{SQL};\n```\n以上查询满足需求。",
])
def test_extracts_pure_sql(raw):
    assert _clean(raw) == SQL


def test_keeps_chinese_alias_inside_sql():
    """SQL 内部的中文别名不能被当成解释文字误删。"""
    out = _clean(f"```sql\n{SQL}\n```")
    assert "净销额" in out and out.startswith("SELECT")


def test_cte_is_recognised_as_sql_start():
    cte = "WITH t AS (SELECT 1 AS x)\nSELECT * FROM t"
    assert _clean(f"先建个 CTE：\n\n{cte}") == cte


def test_non_sql_output_becomes_empty():
    """模型答非所问时应清成空串，由护栏以「未生成 SQL」硬拦，
    而不是把一段中文送进执行器换来一个看不懂的语法错误。"""
    assert _clean("") == ""
    assert _clean("我无法回答这个问题。") == ""

    from app.agent.nodes.sql import guardrail
    g = guardrail({"sql": ""})
    assert not g["guard_ok"] and g["guard_hard"]
