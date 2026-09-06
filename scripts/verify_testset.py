"""校验评测集：全部 gold SQL 必须可执行且结果合理。gold 错了整个评测就是废的。"""

from __future__ import annotations

import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import Counter

from rich.console import Console

from app.warehouse import duck
from eval.runner import load_cases

console = Console()


def main() -> int:
    cases = load_cases()
    bad, empty, nulls = [], [], []
    for c in cases:
        try:
            r = duck.run(c["gold_sql"])
        except Exception as exc:                       # noqa: BLE001
            bad.append((c["id"], str(exc).split("\n")[0][:130]))
            continue
        if r.row_count == 0:
            empty.append(c["id"])
        elif all(v is None for v in r.rows[0]):
            nulls.append(c["id"])
    console.print(f"共 {len(cases)} 题   "
                  f"难度分布 {dict(sorted(Counter(c['tier'] for c in cases).items()))}")
    for cid, msg in bad:
        console.print(f"[red]执行失败[/] {cid}  {msg}")
    if empty:
        console.print(f"[yellow]返回空结果[/] {'、'.join(empty)}")
    if nulls:
        console.print(f"[yellow]首行全为 NULL[/] {'、'.join(nulls)}")
    if not bad and not empty and not nulls:
        console.print("[green]全部 gold SQL 执行通过且结果非空[/]")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
