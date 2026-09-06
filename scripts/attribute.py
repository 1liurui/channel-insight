"""命令行归因。

  uv run python scripts/attribute.py net_sales 2026-07 2026-08
  uv run python scripts/attribute.py net_sales 2026-07 2026-08 --filter region=华东 --narrate
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

from app.attribution.engine import METRICS, attribute
from app.attribution.report import _fmt, narrate, waterfall

console = Console()


def render_tree(nodes, unit, parent) -> None:
    for n in nodes:
        color = "red" if n.delta < 0 else "green"
        branch = parent.add(
            f"[{color}]{n.dim_label}={n.value}[/]  贡献 [bold]{n.contribution:+.1%}[/]"
            f"   {_fmt(n.v0, unit)} → {_fmt(n.v1, unit)}  ({_fmt(n.delta, unit)})")
        render_tree(n.children, unit, branch)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("metric", choices=list(METRICS))
    ap.add_argument("t0")
    ap.add_argument("t1")
    ap.add_argument("--filter", action="append", default=[], help="维度=取值")
    ap.add_argument("--narrate", action="store_true", help="调用大模型转述为业务结论")
    a = ap.parse_args()
    filters = dict(f.split("=", 1) for f in a.filter)

    r = attribute(a.metric, a.t0, a.t1, filters=filters)
    head = (f"{r.metric_label}  {r.t0} → {r.t1}   "
            f"{_fmt(r.v0, r.unit)} → {_fmt(r.v1, r.unit)}   "
            f"变动 [bold]{_fmt(r.delta, r.unit)}（{r.delta_pct:+.2f}%）[/]")
    console.print(f"\n{head}")
    if not r.significant:
        console.print("[yellow]未达显著异动阈值，不做归因[/]")
        return
    if r.offsetting:
        console.print(f"[yellow]净额仅为毛额的 {r.scans[0].offset_ratio:.0%}，"
                      f"存在明显内部对冲，结论需谨慎[/]")

    t = Table(title="候选维度解释力", title_style="bold")
    for c in ["维度", "取值数", "集中度", "净额/毛额", "头名贡献", "达 80% 需"]:
        t.add_column(c)
    for s in r.scans:
        t.add_row(s.dim_label, str(s.n_values), f"{s.concentration:.3f}",
                  f"{s.offset_ratio:.2f}", f"{s.top_contribution:+.1%}", str(s.n_for_80))
    console.print(t)

    tree = Tree(f"[bold]按{r.scans[0].dim_label}拆解，累计贡献超 80% 判为主因，"
                f"逐层下钻最多三层[/]")
    render_tree(r.tree, r.unit, tree)
    console.print(tree)

    w = waterfall(r)
    console.print(f"[dim]瀑布图 {len(w['steps'])} 段："
                  + " → ".join(f"{s['label']}" for s in w["steps"]) + "[/]")
    if a.narrate:
        console.print(f"\n[bold cyan]业务结论[/]\n{narrate(r)}")


if __name__ == "__main__":
    main()
