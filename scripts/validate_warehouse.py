"""基准 SQL 校验：10 条业务口径查询，验证生成数据是否落在快消行业合理区间。

每条给出实测值与期望区间。区间来自行业常识，不是拍脑袋——
例如费效比长期低于 3 说明促销在亏本做量，高于 12 则说明费用没投出去，都不真实。
"""

from __future__ import annotations

import os
import sys

import duckdb
from rich.console import Console
from rich.table import Table

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import get_settings

console = Console()

CHECKS: list[tuple[str, str, str, tuple[float, float], str]] = [
    ("数据完整性", "日期覆盖天数", """
        SELECT count(DISTINCT date_key) FROM fact_sales
    """, (720, 731), "天"),

    ("数据完整性", "动销明细行数", """
        SELECT count(*) / 10000.0 FROM fact_sales
    """, (2600, 3000), "万行"),

    ("铺货", "品牌级铺市率", """
        SELECT count(DISTINCT r.outlet_id)::DOUBLE
             / (SELECT count(*) FROM dim_outlet WHERE is_active) * 100
        FROM rel_outlet_sku r JOIN dim_product p USING (sku_id)
        WHERE p.brand = '清源'
    """, (60, 92), "%"),

    ("铺货", "单品级平均铺市率", """
        SELECT avg(rate) FROM (
          SELECT sku_id, count(DISTINCT outlet_id)::DOUBLE
               / (SELECT count(*) FROM dim_outlet WHERE is_active) * 100 AS rate
          FROM rel_outlet_sku GROUP BY sku_id)
    """, (20, 55), "%"),

    ("动销", "单品级月度动销率", """
        WITH pairs AS (SELECT count(*) n FROM rel_outlet_sku),
             m AS (SELECT d.year_month, count(DISTINCT (s.outlet_id, s.sku_id)) n
                   FROM fact_sales s JOIN dim_date d USING (date_key)
                   GROUP BY d.year_month)
        SELECT avg(m.n)::DOUBLE / (SELECT n FROM pairs) * 100 FROM m
    """, (55, 92), "%"),

    ("渠道结构", "大卖场与传统食杂单店产出倍数", """
        WITH t AS (
          SELECT o.channel,
                 sum(s.net_amount) / count(DISTINCT s.outlet_id) AS spo
          FROM fact_sales s JOIN dim_outlet o USING (outlet_id)
          GROUP BY o.channel)
        SELECT (SELECT spo FROM t WHERE channel='大卖场')
             / (SELECT spo FROM t WHERE channel='传统食杂')
    """, (8, 35), "倍"),

    ("季节性", "饮用水 7 月比 1 月销量", """
        WITH t AS (
          SELECT d.month, sum(s.qty) q
          FROM fact_sales s JOIN dim_date d USING (date_key)
          JOIN dim_product p USING (sku_id)
          WHERE p.category = '饮用水' GROUP BY d.month)
        SELECT (SELECT q FROM t WHERE month=7)::DOUBLE / (SELECT q FROM t WHERE month=1)
    """, (1.6, 3.0), "倍"),

    ("长尾", "销额前 20% SKU 占比", """
        WITH t AS (
          SELECT sku_id, sum(net_amount) amt,
                 row_number() OVER (ORDER BY sum(net_amount) DESC) rn,
                 count(*) OVER () tot
          FROM fact_sales GROUP BY sku_id)
        SELECT sum(amt) FILTER (WHERE rn <= tot*0.2) / sum(amt) * 100 FROM t
    """, (35, 70), "%"),

    ("费用", "整体费效比", """
        WITH s AS (SELECT sum(net_amount) amt FROM fact_sales WHERE is_promo),
             c AS (SELECT sum(cost_amount) camt FROM fact_promo_cost)
        SELECT (SELECT amt FROM s) / (SELECT camt FROM c)
    """, (3.5, 12), "倍"),

    ("费用", "促销期日均销量提升", """
        WITH t AS (
          SELECT is_promo, sum(qty)::DOUBLE / count(DISTINCT date_key) v
          FROM fact_sales GROUP BY is_promo)
        SELECT ((SELECT v FROM t WHERE is_promo) / (SELECT v FROM t WHERE NOT is_promo)) * 100
    """, (5, 60), "%"),

    ("库存", "库存周转天数", """
        WITH inv AS (SELECT avg(qty_on_hand) h FROM fact_inventory),
             outflow AS (SELECT sum(qty)::DOUBLE / count(DISTINCT date_key) / 
                            (SELECT count(DISTINCT dealer_id||'-'||sku_id) FROM fact_inventory) d
                     FROM fact_sales)
        SELECT (SELECT h FROM inv) / (SELECT d FROM outflow)
    """, (12, 40), "天"),

    ("库存", "临期库存占比", """
        SELECT sum(near_expiry_qty)::DOUBLE / sum(qty_on_hand) * 100 FROM fact_inventory
    """, (2, 15), "%"),

    ("拜访", "拜访达成率", """
        SELECT count(*) FILTER (WHERE is_executed)::DOUBLE
             / count(*) FILTER (WHERE is_planned) * 100 FROM fact_visit
    """, (85, 99), "%"),

    ("拜访", "拜访成单率", """
        SELECT count(*) FILTER (WHERE has_order)::DOUBLE
             / count(*) FILTER (WHERE is_executed) * 100 FROM fact_visit
    """, (48, 65), "%"),
]


def main() -> int:
    con = duckdb.connect(str(get_settings().duckdb_file), read_only=True)
    t = Table(title="基准 SQL 校验", title_style="bold")
    for c, style in [("类别", "dim"), ("检查项", "cyan"), ("实测", "bold"),
                     ("期望区间", "dim"), ("", "")]:
        t.add_column(c, style=style, no_wrap=(c == "检查项"))
    bad = []
    for cat, name, sql, (lo, hi), unit in CHECKS:
        v = con.sql(sql).fetchone()[0]
        ok = v is not None and lo <= v <= hi
        if not ok:
            bad.append(name)
        t.add_row(cat, name, f"{v:,.2f} {unit}" if v is not None else "NULL",
                  f"{lo:g} – {hi:g}", "[green]OK[/]" if ok else "[red]越界[/]")
    con.close()
    console.print(t)
    if bad:
        console.print(f"[red]{len(bad)} 项越界：{'、'.join(bad)}[/]")
        return 1
    console.print(f"[green]{len(CHECKS)} 项全部落在业务合理区间[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
