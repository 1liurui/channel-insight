"""跑评测。

  uv run python scripts/run_eval.py                 # 全部五组配置
  uv run python scripts/run_eval.py --only full     # 只跑一组
  uv run python scripts/run_eval.py --report        # 只根据已有结果重新出报告
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collections import Counter

from rich.console import Console
from rich.table import Table

from app.config import ROOT
from eval.harness import (
    CONFIG_LABEL,
    CONFIGS,
    EvalAborted,
    load_cases,
    run_config,
    to_dicts,
)

console = Console()
OUT = ROOT / "eval" / "results"
ERROR_TYPES = ["召回缺失", "口径歧义", "语法错误", "逻辑错误"]


def accuracy(rows: list[dict]) -> float:
    return sum(r["correct"] for r in rows) / max(len(rows), 1) * 100


def load_runs(config: str) -> list[list[dict]]:
    """同一配置的多轮结果。LLM 输出非确定，单轮的题目级波动可达 9%，
    消融差值若小于波动幅度就不能声称存在贡献，因此必须多轮取均值并报告区间。"""
    return [json.loads(f.read_text()) for f in sorted(OUT.glob(f"{config}.run*.json"))]


def stat(runs: list[list[dict]], pred=lambda r: True) -> tuple[float, float, float]:
    accs = [sum(r["correct"] for r in run if pred(r)) / max(sum(1 for r in run if pred(r)), 1) * 100
            for run in runs]
    return sum(accs) / len(accs), min(accs), max(accs)


def report() -> None:
    data = {c: load_runs(c) for c in CONFIGS}
    data = {k: v for k, v in data.items() if v}
    if not data:
        console.print("[red]没有结果文件，先跑评测[/]")
        return

    n_runs = len(data.get("full", []))
    t = Table(title=f"执行准确率（{n_runs} 轮均值，括号为区间）", title_style="bold")
    for c in ["配置", "总体", "T1", "T2", "T3", "T4", "相对主方案"]:
        t.add_column(c, no_wrap=True)
    full_m = stat(data["full"])[0] if "full" in data else None
    for cfg, runs in data.items():
        m, lo, hi = stat(runs)
        tiers = [f"{stat(runs, lambda r, k=k: r['tier'] == k)[0]:.0f}" for k in (1, 2, 3, 4)]
        if cfg == "full" or full_m is None:
            delta = "—"
        else:
            d = m - full_m
            spread = max(hi - lo, stat(data["full"])[2] - stat(data["full"])[1])
            delta = (f"{d:+.1f} 个百分点" if abs(d) > spread
                     else f"{d:+.1f}（落在波动内）")
        t.add_row(CONFIG_LABEL[cfg], f"[bold]{m:.1f}%[/] ({lo:.0f}–{hi:.0f})", *tiers, delta)
    console.print(t)

    if "full" in data:
        bad = [r for r in data["full"][0] if not r["correct"]]
        t2 = Table(title=f"主方案失败样本分类（{len(bad)} 例）", title_style="bold")
        t2.add_column("类型")
        t2.add_column("例数", justify="right")
        t2.add_column("占失败", justify="right")
        t2.add_column("典型题号")
        cnt = Counter(r["error_type"] or "未分类" for r in bad)
        for k in ERROR_TYPES + [x for x in cnt if x not in ERROR_TYPES]:
            if not cnt.get(k):
                continue
            ids = [r["id"] for r in bad if (r["error_type"] or "未分类") == k][:6]
            t2.add_row(k, str(cnt[k]), f"{cnt[k] / len(bad) * 100:.0f}%", "、".join(ids))
        console.print(t2)

        lat = sorted(r["latency_ms"] for r in data["full"][0])
        console.print(f"\n主方案延迟  中位 {lat[len(lat) // 2]:,.0f} ms   "
                      f"P95 {lat[int(len(lat) * .95)]:,.0f} ms   最大 {lat[-1]:,.0f} ms")
        console.print("触发 self-correction 的题目："
                      f"{sum(1 for r in data['full'][0] if r['retry'])} 题")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", choices=list(CONFIGS))
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--runs", type=int, default=3, help="每组配置重复轮次")
    a = ap.parse_args()

    if a.report:
        report()
        return

    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("*.json"):
        if ".run" not in f.name:
            f.unlink()
    cases = load_cases()
    for cfg in (a.only or list(CONFIGS)):
        for k in range(1, a.runs + 1):
            t0 = time.time()
            try:
                rows = to_dicts(run_config(cfg, cases, workers=a.workers))
            except EvalAborted as exc:
                console.print(f"[red]评测中止[/] {exc}")
                return
            (OUT / f"{cfg}.run{k}.json").write_text(
                json.dumps(rows, ensure_ascii=False, indent=1))
            console.print(f"[cyan]{CONFIG_LABEL[cfg]}[/] 第 {k} 轮  "
                          f"准确率 [bold]{accuracy(rows):.0f}%[/]  {time.time() - t0:.0f}s")
    report()


if __name__ == "__main__":
    main()
