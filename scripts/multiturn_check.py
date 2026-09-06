"""多轮追问验证：指代消解 + PostgreSQL checkpointer 持久化。"""

from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console

from app.service import durable_service

console = Console()
TURNS = [
    "华东区上个月的销售额是多少",
    "那华南呢",
    "这两个大区哪个单店产出更高",
    "只看便利店渠道再算一次",
]


def main() -> None:
    with durable_service(use_cache=False) as svc:
        history: list[dict] = []
        for i, q in enumerate(TURNS, 1):
            a = svc.ask(q, history=history, session_id="mt-demo")
            console.print(f"\n[bold]第 {i} 轮[/]  {q}   [dim]{a.latency_ms:.0f} ms[/]")
            if a.rewritten != q:
                console.print(f"  [yellow]改写 →[/] {a.rewritten}")
            if a.error:
                console.print(f"  [red]{a.error_type}：{a.error}[/]")
            else:
                console.print(f"  [green]结果[/] {a.columns} {a.rows[:3]}")
            # 存改写后的完整问句而非原始问句：否则「这两个大区哪个单店产出更高」
            # 这类本身不含时间的句子进了上文，几轮之后时间范围就丢了
            history += [{"role": "用户", "content": a.rewritten or q},
                        {"role": "助手", "content": f"查询结果：{a.rows[:2]}"}]


if __name__ == "__main__":
    main()
