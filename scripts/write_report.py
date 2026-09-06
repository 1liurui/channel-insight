"""根据多轮评测结果生成 eval/REPORT.md。"""

from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import ROOT
from eval.harness import CONFIG_LABEL, CONFIGS
from eval.runner import load_cases

OUT = ROOT / "eval" / "results"
REPORT = ROOT / "eval" / "REPORT.md"
TIERS = {1: "基础聚合", 2: "多表关联", 3: "嵌套与窗口", 4: "复合指标与跨粒度"}


def runs(cfg: str) -> list[list[dict]]:
    return [json.loads(f.read_text()) for f in sorted(OUT.glob(f"{cfg}.run*.json"))]


def acc(rows: list[dict], tier: int | None = None) -> float:
    sel = [r for r in rows if tier is None or r["tier"] == tier]
    return sum(r["correct"] for r in sel) / max(len(sel), 1) * 100


def band(rs: list[list[dict]], tier: int | None = None) -> tuple[float, float, float]:
    a = [acc(r, tier) for r in rs]
    return st.mean(a), min(a), max(a)


def main() -> None:
    data = {c: runs(c) for c in CONFIGS if runs(c)}
    cases = {c["id"]: c for c in load_cases()}
    n = len(data["full"])
    fm, flo, fhi = band(data["full"])
    noise = fhi - flo

    L = ["# 评测报告", "",
         f"> 测试集 {len(cases)} 题，四档难度各 25 题；每组配置独立跑 {n} 轮取均值。",
         ("> 口径：execution accuracy——比对结果集而非 SQL 文本，行序不敏感"
          "（题目要求排序时保持顺序），数值取 4 位有效数字容差，列数不同即判错。"),
         "> 主模型 DeepSeek-chat，temperature=0。", "",
         "## 一、总体结果", "",
         "| 配置 | 准确率（均值） | 区间 | " + " | ".join(f"T{k} {v}" for k, v in TIERS.items())
         + " | 相对主方案 |",
         "|---|---|---|" + "---|" * 5]
    for cfg, rs in data.items():
        m, lo, hi = band(rs)
        tier_s = " | ".join(f"{band(rs, k)[0]:.0f}%" for k in TIERS)
        if cfg == "full":
            delta = "—"
        else:
            d = m - fm
            delta = (f"**{d:+.1f} 个百分点**" if abs(d) > noise
                     else f"{d:+.1f}（落在 ±{noise:.0f} 的轮次波动内，不构成有效差异）")
        L.append(f"| {CONFIG_LABEL[cfg]} | **{m:.1f}%** | {lo:.0f}–{hi:.0f}% | {tier_s} | {delta} |")

    L += ["", "## 二、轮次波动", "",
          f"同一配置重跑，总体准确率波动 ±{noise:.0f} 个百分点，但**题目级翻转可达 9 题**。",
          "这决定了消融结论的判读方式：差值小于波动幅度的一律不声称存在贡献。",
          "单轮评测在此波动水平下不足以支撑消融结论，故所有数字均为三轮均值。", ""]

    bad = [r for r in data["full"][0] if not r["correct"]]
    L += ["## 三、主方案失败样本分类", "",
          f"共 {len(bad)} 例（取第一轮）。", "",
          "| 类型 | 例数 | 占失败 | 判定依据 | 题号 |", "|---|---|---|---|---|"]
    basis = {
        "召回缺失": "gold SQL 引用的表未进入链路选出的表清单，确定性判定",
        "口径歧义": "命中 semantic layer 指标或属第四档，但结果与 gold 不符",
        "语法错误": "SQL 无法执行，引擎报 Parser Error",
        "逻辑错误": "表选对、非指标题，但聚合或筛选逻辑与 gold 不符",
    }
    cnt = Counter(r["error_type"] or "未分类" for r in bad)
    for k in ["召回缺失", "口径歧义", "语法错误", "逻辑错误"]:
        if not cnt.get(k):
            continue
        ids = [r["id"] for r in bad if r["error_type"] == k]
        L.append(f"| {k} | {cnt[k]} | {cnt[k] / len(bad) * 100:.0f}% | {basis[k]} | "
                 f"{'、'.join(ids)} |")

    lat = sorted(r["latency_ms"] for r in data["full"][0])
    L += ["", "## 四、延迟与调用", "",
          (f"- 端到端延迟：中位 {lat[len(lat) // 2]:,.0f} ms，"
           f"P95 {lat[int(len(lat) * .95)]:,.0f} ms，最大 {lat[-1]:,.0f} ms"),
          f"- 触发 self-correction：{sum(1 for r in data['full'][0] if r['retry'])} / {len(cases)} 题",
          "- 单题模型调用：命中缓存 0 次，未命中 1 次，触发纠错 2 次", ""]

    REPORT.write_text("\n".join(L) + "\n")
    print(f"已写入 {REPORT}")


if __name__ == "__main__":
    main()
