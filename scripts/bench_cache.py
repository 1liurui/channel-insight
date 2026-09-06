"""语义缓存收益基准。同一批问题冷跑一轮、热跑一轮，对比端到端延迟。"""

from __future__ import annotations

import os
import statistics as st
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table

from app.clients.cache import SemanticCache
from app.service import AskService

console = Console()

QUESTIONS = [
    "华东区上个月的销售额是多少",
    "各渠道的净销额排名",
    "清源品牌在便利店渠道卖了多少支",
    "2026年8月哪个大区的费效比最高",
    "临期库存占比最高的前十个经销商",
    "各大区业务代表的拜访达成率",
    "上个月碳酸饮料品类的动销率是多少",
    "对比华东和华南上个月的单店产出",
    "2026年上半年每个月的净销额趋势",
    "华东区各城市的铺市率排名前十",
    "大卖场渠道哪些单品动销最差",
    "上个月促销费用最高的五个经销商及其费效比",
]
# 热跑用同义改写，验证缓存不是靠字符串精确匹配
PARAPHRASE = {
    "华东区上个月的销售额是多少": "上月华东的销额是多少",
    "各渠道的净销额排名": "按渠道给销售额排个序",
    "2026年8月哪个大区的费效比最高": "2026年8月费效比最高的是哪个大区",
}


def pct(v: list[float], p: float) -> float:
    return sorted(v)[min(len(v) - 1, int(len(v) * p))]


def main() -> None:
    SemanticCache().clear()
    svc = AskService(use_cache=True)

    cold, hot, rows = [], [], []
    for q in QUESTIONS:
        a = svc.ask(q)
        cold.append(a.latency_ms)
        rows.append([q, a.ok, a.latency_ms, a.retry, None, None])

    for i, q in enumerate(QUESTIONS):
        probe = PARAPHRASE.get(q, q)
        b = svc.ask(probe)
        hot.append(b.latency_ms)
        rows[i][4] = b.latency_ms
        rows[i][5] = b.cache_hit

    t = Table(title="语义缓存收益", title_style="bold")
    for c in ["问题", "", "冷（毫秒）", "热（毫秒）", "命中", "提速"]:
        t.add_column(c, no_wrap=(c == "问题"))
    for q, ok, c, retry, h, hit in rows:
        t.add_row(q[:22], "[green]OK[/]" if ok else "[red]FAIL[/]",
                  f"{c:,.0f}" + (f" (+{retry}纠错)" if retry else ""), f"{h:,.0f}",
                  "[green]是[/]" if hit else "[red]否[/]",
                  f"{c / h:.0f}×" if hit and h else "—")
    console.print(t)

    n_hit = sum(1 for r in rows if r[5])
    console.print(f"\n冷启动  中位 {st.median(cold):,.0f} ms   P95 {pct(cold, .95):,.0f} ms   "
                  f"最大 {max(cold):,.0f} ms")
    console.print(f"缓存命中 中位 {st.median(hot):,.0f} ms   P95 {pct(hot, .95):,.0f} ms   "
                  f"最大 {max(hot):,.0f} ms")
    console.print(f"命中率 {n_hit}/{len(rows)}   "
                  f"中位提速 {st.median(cold) / max(st.median(hot), 0.01):,.0f} 倍")


if __name__ == "__main__":
    main()
