"""命令行问数。

  uv run python scripts/ask.py "华东区上个月的销售额是多少"
  uv run python scripts/ask.py --no-cache --verbose "各渠道费效比排名"
"""

from __future__ import annotations

import argparse
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

from app.service import AskService

console = Console()


def render(ans, verbose: bool = False) -> None:
    tag = (f"[green]缓存命中[/] 相似度 {ans.cache_score:.3f}" if ans.cache_hit
           else f"[cyan]链路执行[/] {ans.llm_calls} 次模型调用"
                + (f"，纠错 {ans.retry} 轮" if ans.retry else ""))
    console.print(f"\n[bold]{ans.question}[/]   {tag}   [dim]{ans.latency_ms:.0f} ms[/]")
    if ans.rewritten and ans.rewritten != ans.question:
        console.print(f"[dim]改写为：{ans.rewritten}[/]")
    for i, a in enumerate(ans.attempts, 1):
        console.print(f"[yellow]第 {i} 次尝试失败：{a['error']}[/]")
    if ans.error:
        console.print(f"[red]失败（{ans.error_type}）：{ans.error}[/]")
    if ans.sql:
        console.print(Syntax(ans.sql, "sql", theme="ansi_dark", word_wrap=True))
    if ans.rows:
        t = Table(show_header=True, header_style="bold cyan")
        for c in ans.columns:
            t.add_column(str(c))
        for r in ans.rows[:15]:
            t.add_row(*[f"{v:,.2f}" if isinstance(v, float) else str(v) for v in r])
        console.print(t)
        if ans.row_count > 15:
            console.print(f"[dim]共 {ans.row_count} 行，只显示前 15 行[/]")
    if verbose and ans.timings:
        order = sorted(ans.timings.items(), key=lambda x: -x[1])
        console.print("[dim]节点耗时：" +
                      "  ".join(f"{k} {v:.0f}ms" for k, v in order if v > 1) + "[/]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="+")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    a = ap.parse_args()
    svc = AskService(use_cache=not a.no_cache)
    render(svc.ask(" ".join(a.question)), a.verbose)


if __name__ == "__main__":
    main()
