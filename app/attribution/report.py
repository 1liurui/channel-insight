"""归因结果的呈现层：瀑布图数据与业务结论转述。

大模型只在 narrate 出现，且只拿到算好的数字，不参与任何计算。
prompt 里明确禁止推测原因、禁止改动数值——它的职责是把
「大区=华东 贡献 -62%」翻译成业务方能读的句子，不是分析师。
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.attribution.engine import Attribution, Node
from app.clients.llm import get_llm

SYSTEM = ("你是渠道运营分析师，只根据给定的定量结果撰写结论。"
          "禁止推测数据之外的业务原因，禁止修改或重新计算任何数值。")

NARRATE_PROMPT = """下面是一次指标异动的定量归因结果，请写一段业务结论。

指标：{metric}（单位 {unit}）
对比：{t0} 为 {v0}，{t1} 为 {v1}，变动 {delta}（{delta_pct}）
{filters}
主拆解维度：{dim}（在所有候选维度中解释集中度最高）

各取值贡献度（贡献度 = 该取值的变动量 ÷ 总变动量）：
{rows}

要求：
1. 三到五句话，先说整体变动，再说主因，最后说下钻发现。
2. 只复述上面的数字，不要自己推算，不要编造促销、竞品、天气等原因。
3. 贡献度超过 100% 说明存在反向抵消，若出现须点明。
4. 用渠道运营的口吻，不要用「数据显示」「综上所述」这类套话。

结论："""


def waterfall(attr: Attribution, max_bars: int = 8) -> dict:
    """瀑布图数据。首柱为期初值，中间为各主因增减，末柱为期末值，
    未进入主因集合的部分合并为「其他」，保证首尾能对上。"""
    steps = [{"label": attr.t0, "type": "total", "value": attr.v0}]
    covered = 0.0
    for n in attr.tree[:max_bars]:
        steps.append({"label": f"{n.dim_label}·{n.value}", "type": "change",
                      "value": n.delta, "contribution": n.contribution})
        covered += n.delta
    residual = attr.delta - covered
    if abs(residual) > 1e-9:
        steps.append({"label": "其他", "type": "change", "value": residual,
                      "contribution": residual / attr.delta if attr.delta else 0.0})
    steps.append({"label": attr.t1, "type": "total", "value": attr.v1})
    return {"metric": attr.metric_label, "unit": attr.unit, "steps": steps,
            "delta": attr.delta, "delta_pct": attr.delta_pct}


def _fmt(v: float, unit: str) -> str:
    if unit == "元":
        return f"{v / 1e4:,.1f} 万元" if abs(v) >= 1e4 else f"{v:,.0f} 元"
    return f"{v:,.0f} {unit}"


def _lines(nodes: list[Node], unit: str, depth: int = 0) -> list[str]:
    out = []
    for n in nodes:
        pad = "  " * depth
        out.append(f"{pad}- {n.dim_label}={n.value}："
                   f"{_fmt(n.v0, unit)} → {_fmt(n.v1, unit)}，"
                   f"变动 {_fmt(n.delta, unit)}，贡献度 {n.contribution:+.1%}")
        out += _lines(n.children, unit, depth + 1)
    return out


def narrate(attr: Attribution) -> str:
    if not attr.significant:
        return (f"{attr.metric_label}从 {attr.t0} 到 {attr.t1} 变动 "
                f"{attr.delta_pct:+.2f}%，未达到显著异动阈值，无需归因。")
    dim_label = attr.scans[0].dim_label if attr.scans else ""
    flt = ("筛选范围：" + "、".join(f"{k}={v}" for k, v in attr.filters.items())
           if attr.filters else "筛选范围：全量")
    prefix = ""
    if attr.offsetting:
        prefix = ("注意：本次变动的净额仅为各取值变动绝对值之和的 "
                  f"{attr.scans[0].offset_ratio:.0%}，存在明显的内部反向抵消。\n")
    msg = get_llm(max_tokens=600).invoke([
        SystemMessage(content=SYSTEM),
        HumanMessage(content=prefix + NARRATE_PROMPT.format(
            metric=attr.metric_label, unit=attr.unit, t0=attr.t0, t1=attr.t1,
            v0=_fmt(attr.v0, attr.unit), v1=_fmt(attr.v1, attr.unit),
            delta=_fmt(attr.delta, attr.unit), delta_pct=f"{attr.delta_pct:+.2f}%",
            filters=flt, dim=dim_label, rows="\n".join(_lines(attr.tree, attr.unit)))),
    ])
    return msg.content.strip()
