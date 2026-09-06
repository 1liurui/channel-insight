"""指标异动归因引擎。

核心是确定性计算，大模型不参与任何数值环节，只在最后把算好的结果转述成业务语言。

三个关键设计：

1. 贡献度定义为 C_i = (V_i,t1 − V_i,t0) / (V_t1 − V_t0)。
   对可加指标（销额、销量、费用）这是精确分解，全部取值的贡献度之和恒为 1。
   比率型指标（铺市率、费效比）不满足可加性，不在本引擎处理范围内，
   强行套用会得到看似合理实则无意义的数字。

2. **按哪个维度拆解是算出来的，不是指定的。** 同一笔下滑，按大区拆可能七个大区
   均摊（没有信息量），按单品拆可能一个单品吃掉八成（这才是原因）。
   引擎对每个候选维度算一遍，用「解释集中度」排序——达到 80% 累计贡献所需的
   取值个数越少，该维度越有解释力。

3. 递归下钻带剪枝。只对贡献度绝对值超阈值的取值继续下钻，且下一层换用
   在该切片内解释力最强的维度，最多三层。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

from app.warehouse import duck

# 可加指标：分解才有意义
METRICS: dict[str, tuple[str, str, str, str]] = {
    # id: (事实表, 度量表达式, 中文名, 单位)
    "net_sales":  ("fact_sales", "SUM({t}.net_amount)", "净销额", "元"),
    "sales_qty":  ("fact_sales", "SUM({t}.qty)", "销量", "支"),
    "promo_cost": ("fact_promo_cost", "SUM({t}.cost_amount)", "促销费用", "元"),
}

# 候选拆解维度：(维表, 列, 中文名, 与事实表的关联键)
DIMENSIONS: dict[str, tuple[str, str, str, str]] = {
    "region":   ("dim_outlet", "region", "大区", "outlet_id"),
    "channel":  ("dim_outlet", "channel", "渠道", "outlet_id"),
    "province": ("dim_outlet", "province", "省份", "outlet_id"),
    "grade":    ("dim_outlet", "outlet_grade", "门店等级", "outlet_id"),
    "dealer":   ("dim_dealer", "dealer_name", "经销商", "dealer_id"),
    "category": ("dim_product", "category", "品类", "sku_id"),
    "brand":    ("dim_product", "brand", "品牌", "sku_id"),
    "sku":      ("dim_product", "sku_name", "单品", "sku_id"),
}
JOIN_KEY = {"dim_outlet": "outlet_id", "dim_dealer": "dealer_id", "dim_product": "sku_id"}

PRIMARY_CUM = 0.80      # 累计贡献超此比例即认定为主因集合
DRILL_MIN = 0.10        # 贡献度绝对值低于此值不再下钻
MAX_DEPTH = 3
MIN_REL_CHANGE = 0.02   # 总变动幅度低于此比例视为无显著异动，避免分母趋零放大噪声
MIN_OFFSET_RATIO = 0.25 # 净额/毛额低于此值说明变动主要在内部对冲，归因结论不可靠


@dataclass
class Node:
    dimension: str
    dim_label: str
    value: str
    v0: float
    v1: float
    delta: float
    contribution: float          # 对上一层变动的贡献度
    children: list[Node] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"dimension": self.dimension, "dim_label": self.dim_label, "value": self.value,
                "v0": self.v0, "v1": self.v1, "delta": self.delta,
                "contribution": self.contribution,
                "children": [c.to_dict() for c in self.children]}


@dataclass
class DimensionScan:
    dimension: str
    dim_label: str
    nodes: list[Node]
    n_for_80: int                # 达到 80% 累计贡献所需取值数
    top_contribution: float      # 头名贡献度
    concentration: float         # 归一化 HHI，跨基数可比，越大越集中
    offset_ratio: float          # |Σdelta| / Σ|delta|，越低说明取值间互相对冲越严重
    n_values: int = 0


@dataclass
class Attribution:
    metric: str
    metric_label: str
    unit: str
    t0: str
    t1: str
    v0: float
    v1: float
    delta: float
    delta_pct: float
    significant: bool
    offsetting: bool = False     # 变动主要由内部对冲构成，归因结论需谨慎
    scans: list[DimensionScan] = field(default_factory=list)
    tree: list[Node] = field(default_factory=list)
    filters: dict[str, str] = field(default_factory=dict)

    @property
    def best_dimension(self) -> str:
        return self.scans[0].dimension if self.scans else ""

    def to_dict(self) -> dict:
        return {"metric": self.metric, "metric_label": self.metric_label, "unit": self.unit,
                "t0": self.t0, "t1": self.t1, "v0": self.v0, "v1": self.v1,
                "delta": self.delta, "delta_pct": self.delta_pct,
                "significant": self.significant, "offsetting": self.offsetting,
                "filters": self.filters,
                "dimension_ranking": [
                    {"dimension": s.dimension, "label": s.dim_label,
                     "n_values": s.n_values, "n_for_80": s.n_for_80,
                     "top_contribution": s.top_contribution,
                     "concentration": s.concentration,
                     "offset_ratio": s.offset_ratio} for s in self.scans],
                "tree": [n.to_dict() for n in self.tree]}


def _sql(metric: str, dim: str, t0: str, t1: str, filters: dict[str, str],
         sales_table: str) -> str:
    fact, measure, _, _ = METRICS[metric]
    tbl = sales_table if fact == "fact_sales" else fact
    dim_table, dim_col, _, _ = DIMENSIONS[dim]

    needed = {dim_table} | {DIMENSIONS[k][0] for k in filters}
    joins = ["JOIN dim_date d ON f.date_key = d.date_key"]
    for t in sorted(needed):
        key = JOIN_KEY[t]
        joins.append(f"JOIN {t} ON f.{key} = {t}.{key}")

    where = [f"d.year_month IN ('{t0}', '{t1}')"]
    for k, v in filters.items():
        ft, fc, _, _ = DIMENSIONS[k]
        where.append(f"{ft}.{fc} = '{v}'")

    m0 = measure.format(t="f")
    return f"""
        SELECT {dim_table}.{dim_col} AS v,
               COALESCE({m0} FILTER (WHERE d.year_month = '{t0}'), 0) AS v0,
               COALESCE({m0} FILTER (WHERE d.year_month = '{t1}'), 0) AS v1
        FROM {tbl} f
        {' '.join(joins)}
        WHERE {' AND '.join(where)}
        GROUP BY 1
    """


def scan_dimension(metric: str, dim: str, t0: str, t1: str, total_delta: float,
                   filters: dict[str, str], con, sales_table: str) -> DimensionScan:
    rows = con.sql(_sql(metric, dim, t0, t1, filters, sales_table)).fetchall()
    _, _, label, _ = DIMENSIONS[dim]
    nodes = [
        Node(dimension=dim, dim_label=label, value=str(v), v0=float(a), v1=float(b),
             delta=float(b) - float(a),
             contribution=(float(b) - float(a)) / total_delta if total_delta else 0.0)
        for v, a, b in rows
    ]
    nodes.sort(key=lambda n: -abs(n.contribution))

    cum, n80 = 0.0, len(nodes)
    for i, n in enumerate(nodes, start=1):
        cum += n.contribution
        if cum >= PRIMARY_CUM:
            n80 = i
            break

    # 集中度用「移动量份额」的归一化 HHI，而不是「达到 80% 需要几个取值」。
    # 后者会被反向对冲骗过：某大区涨 246%、其余合计跌 146% 时，
    # 累计贡献一个取值就超 80%，看起来极度集中，实际是内部对冲的假象。
    gross = sum(abs(n.delta) for n in nodes)
    net = abs(sum(n.delta for n in nodes))
    k = len(nodes)
    if gross > 0 and k > 1:
        hhi = sum((abs(n.delta) / gross) ** 2 for n in nodes)
        conc = (hhi - 1.0 / k) / (1.0 - 1.0 / k)      # 归一化后跨维度基数可比
    else:
        conc = 1.0
    return DimensionScan(dimension=dim, dim_label=label, nodes=nodes, n_for_80=n80,
                         top_contribution=nodes[0].contribution if nodes else 0.0,
                         concentration=max(0.0, conc),
                         offset_ratio=(net / gross) if gross else 0.0,
                         n_values=k)


def _primary(nodes: list[Node]) -> list[Node]:
    """按贡献度降序取到累计超 80%。同向贡献才计入累计，反向贡献单独保留。"""
    out, cum = [], 0.0
    for n in nodes:
        out.append(n)
        cum += n.contribution
        if cum >= PRIMARY_CUM:
            break
    return out


def _drill(metric: str, node: Node, t0: str, t1: str, filters: dict[str, str],
           used: set[str], depth: int, con, sales_table: str) -> None:
    if depth >= MAX_DEPTH or abs(node.contribution) < DRILL_MIN or node.delta == 0:
        return
    sub_filters = {**filters, node.dimension: node.value}
    candidates = [d for d in DIMENSIONS if d not in used]
    scans = []
    for d in candidates:
        try:
            scans.append(scan_dimension(metric, d, t0, t1, node.delta, sub_filters,
                                        con, sales_table))
        except duckdb.Error:
            continue
    scans = [s for s in scans if len(s.nodes) > 1]
    if not scans:
        return
    best = max(scans, key=lambda s: s.concentration)
    node.children = _primary(best.nodes)
    for child in node.children:
        _drill(metric, child, t0, t1, sub_filters, used | {best.dimension}, depth + 1,
               con, sales_table)


def attribute(metric: str, t0: str, t1: str, filters: dict[str, str] | None = None,
              con=None, sales_table: str = "fact_sales",
              dimensions: list[str] | None = None) -> Attribution:
    """对指标在 t0 → t1 之间的变动做归因。t0、t1 为 year_month 字符串。"""
    filters = filters or {}
    con = con or duck.connection()
    _, _, label, unit = METRICS[metric]

    dims = dimensions or list(DIMENSIONS)
    first = scan_dimension(metric, dims[0], t0, t1, 1.0, filters, con, sales_table)
    v0 = sum(n.v0 for n in first.nodes)
    v1 = sum(n.v1 for n in first.nodes)
    delta = v1 - v0
    rel = abs(delta) / v0 if v0 else 0.0

    res = Attribution(metric=metric, metric_label=label, unit=unit, t0=t0, t1=t1,
                      v0=v0, v1=v1, delta=delta,
                      delta_pct=(delta / v0 * 100) if v0 else 0.0,
                      significant=rel >= MIN_REL_CHANGE, filters=filters)
    if not res.significant or delta == 0:
        return res

    res.scans = sorted(
        (scan_dimension(metric, d, t0, t1, delta, filters, con, sales_table) for d in dims),
        key=lambda s: -s.concentration)
    best = res.scans[0]
    res.offsetting = best.offset_ratio < MIN_OFFSET_RATIO
    res.tree = _primary(best.nodes)
    for node in res.tree:
        _drill(metric, node, t0, t1, filters, {best.dimension}, 1, con, sales_table)
    return res
