"""构建星型数仓。

用法：
  uv run python scripts/build_warehouse.py --calibrate     # 只跑一个月，估算全量行数
  uv run python scripts/build_warehouse.py                 # 全量构建
  uv run python scripts/build_warehouse.py --scale 1.15    # 指定销量缩放
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import duckdb
import numpy as np
from rich.console import Console

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import ROOT, get_settings
from app.warehouse import dimensions as D
from app.warehouse.generator import FactBuilder, GenConfig, build_rel_outlet_sku

console = Console()
DDL = ROOT / "app" / "warehouse" / "ddl" / "star_schema.sql"


def append(con: duckdb.DuckDBPyConnection, table: str, tbl) -> None:
    if tbl.num_rows == 0:
        return
    con.register("_stage", tbl)
    con.execute(f"INSERT INTO {table} SELECT * FROM _stage")
    con.unregister("_stage")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true", help="只跑首月并外推全量行数")
    ap.add_argument("--scale", type=float, default=1.0, help="销量缩放系数")
    ap.add_argument("--outlets", type=int, default=2000)
    args = ap.parse_args()

    s = get_settings()
    cfg = GenConfig(velocity_scale=args.scale, n_outlets=args.outlets)
    rng = np.random.default_rng(cfg.seed)
    t0 = time.time()

    console.print("[cyan]生成维度[/]")
    dates = D.build_dim_date(cfg.start, cfg.end)
    products = D.build_dim_product(rng, cfg.start, cfg.end)
    dealers = D.build_dim_dealer(rng, cfg.n_dealers, cfg.start)
    outlets = D.build_dim_outlet(rng, cfg.n_outlets, dealers, cfg.start)
    reps = D.build_dim_rep(rng, cfg.n_reps, cfg.start)
    rel = build_rel_outlet_sku(rng, outlets, products, cfg)
    console.print(f"  维度就绪：{products.num_rows} SKU、{dealers.num_rows} 经销商、"
                  f"{outlets.num_rows} 终端、{reps.num_rows} 业代、"
                  f"{rel.num_rows:,} 条铺货关系")

    fb = FactBuilder(rng, cfg, outlets, products, rel, dates)

    if args.calibrate:
        n_rows = 0
        for mi in (0, 6, 11):          # 取淡季、旺季、平季各一月
            tbl, _, _ = fb.month_sales(mi)
            y, m = cfg.months[mi]
            console.print(f"  {y}-{m:02d}  {tbl.num_rows:,} 行")
            n_rows += tbl.num_rows
        est = n_rows / 3 * len(cfg.months)
        console.print(f"[bold]外推全量 fact_sales ≈ {est/1e4:,.0f} 万行"
                      f"（目标 2800 万），行数缺口 {2800/(est/1e4)-1:+.1%}[/]")
        return

    if s.duckdb_file.exists():
        s.duckdb_file.unlink()
    con = duckdb.connect(str(s.duckdb_file))
    con.execute("SET memory_limit='2GB'; SET threads=4;")
    con.execute(DDL.read_text())

    for name, tbl in [("dim_date", dates), ("dim_product", products), ("dim_dealer", dealers),
                      ("dim_outlet", outlets), ("dim_rep", reps)]:
        append(con, name, tbl)
    con.register("_rel", rel)
    con.execute("""INSERT INTO rel_outlet_sku
        SELECT outlet_id, sku_id, strptime(listed_key::VARCHAR,'%Y%m%d')::DATE,
               NULL, base_velocity FROM _rel""")
    con.unregister("_rel")

    total = 0
    for mi, (y, m) in enumerate(cfg.months):
        sales, pair_net, ds_qty = fb.month_sales(mi)
        n_days = len(set(sales.column("date_key").to_pylist())) or 30
        append(con, "fact_sales", sales)
        append(con, "fact_promo_cost", fb.month_promo_cost(mi, pair_net))
        append(con, "fact_inventory", fb.month_inventory(mi, ds_qty, n_days))
        append(con, "fact_visit", fb.month_visits(mi, outlets, reps))
        total += sales.num_rows
        console.print(f"  {y}-{m:02d}  动销 {sales.num_rows:>9,}  累计 {total:>11,}")

    con.execute("CHECKPOINT")
    stats = con.sql("""
        SELECT 'fact_sales' t, count(*) n FROM fact_sales
        UNION ALL SELECT 'fact_visit', count(*) FROM fact_visit
        UNION ALL SELECT 'fact_promo_cost', count(*) FROM fact_promo_cost
        UNION ALL SELECT 'fact_inventory', count(*) FROM fact_inventory
        UNION ALL SELECT 'rel_outlet_sku', count(*) FROM rel_outlet_sku
    """).fetchall()
    con.close()
    size = s.duckdb_file.stat().st_size / 1024**3
    console.print(f"\n[bold green]构建完成[/]  用时 {(time.time()-t0)/60:.1f} 分钟  "
                  f"库文件 {size:.2f} GB")
    for t, n in stats:
        console.print(f"  {t:<18} {n:>12,}")


if __name__ == "__main__":
    main()
