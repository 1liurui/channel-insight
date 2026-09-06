"""归因定位准确率评测：40 组埋因场景。"""

from __future__ import annotations

import json
import os
import sys
import time
import warnings
from collections import Counter

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.table import Table

from app.attribution.scenarios import ScenarioLab, build_scenarios
from app.config import ROOT

console = Console()
OUT = ROOT / "eval" / "results" / "attribution.json"
KIND_LABEL = {"single": "单因", "multi_same_dim": "同维双因", "multi_cross_dim": "跨维双因"}


def main() -> None:
    t0 = time.time()
    lab = ScenarioLab()
    scenarios = build_scenarios(lab)
    verdicts = [lab.evaluate(s) for s in scenarios]

    t = Table(title="埋因场景归因验证", title_style="bold")
    for c in ["场景", "类型", "埋入成因", "", "命中 / 漏判", "主拆解维度"]:
        t.add_column(c, no_wrap=(c == "场景"))
    for s, v in zip(scenarios, verdicts, strict=True):
        causes = "；".join(f"{c.label()} {c.impact:+.0%}" for c in s.causes)
        detail = "、".join(v.found) or "—"
        if v.missed:
            detail += f"   [red]漏 {'、'.join(v.missed)}[/]"
        t.add_row(s.sid, KIND_LABEL[s.kind], causes[:38],
                  "[green]中[/]" if v.hit else "[red]漏[/]", detail[:44],
                  v.note.split("主拆解维度 ")[-1] if "主拆解维度" in v.note else "—")
    console.print(t)

    hit = sum(v.hit for v in verdicts)
    console.print(f"\n[bold]定位准确率 {hit}/{len(verdicts)} = {hit / len(verdicts) * 100:.1f}%[/]"
                  f"   用时 {time.time() - t0:.0f}s")
    by_kind = Counter(v.kind for v in verdicts)
    hit_kind = Counter(v.kind for v in verdicts if v.hit)
    for k, n in by_kind.items():
        console.print(f"  {KIND_LABEL[k]:<10} {hit_kind[k]}/{n} = {hit_kind[k] / n * 100:.0f}%")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps([{
        "sid": v.sid, "kind": v.kind, "hit": v.hit, "found": v.found, "missed": v.missed,
        "best_dim": v.best_dim, "note": v.note,
        "causes": [{"dimension": c.dimension, "value": c.value, "factor": c.factor,
                    "share": c.share, "impact": c.impact} for c in s.causes],
    } for s, v in zip(scenarios, verdicts, strict=True)], ensure_ascii=False, indent=1))
    lab.close()


if __name__ == "__main__":
    main()
