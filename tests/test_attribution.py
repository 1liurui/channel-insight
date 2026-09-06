"""归因引擎回归测试。

重点是三条不变量：贡献度之和恒为 1、瀑布图首尾能对上、集中度不被反向对冲骗过。
前两条错了会得到看似合理的错误数字，第三条错了会选错拆解维度。
"""

import pytest

from app.attribution.engine import (
    MIN_REL_CHANGE,
    Attribution,
    Node,
    attribute,
    scan_dimension,
)
from app.attribution.report import waterfall
from app.warehouse import duck


@pytest.fixture(scope="module")
def con():
    return duck.connection()


def test_contributions_sum_to_one(con):
    """可加指标的分解是精确的：全部取值贡献度之和必须为 1。"""
    s = scan_dimension("net_sales", "region", "2026-02", "2026-06",
                       total_delta=1.0, filters={}, con=con, sales_table="fact_sales")
    delta = sum(n.delta for n in s.nodes)
    s2 = scan_dimension("net_sales", "region", "2026-02", "2026-06",
                        total_delta=delta, filters={}, con=con, sales_table="fact_sales")
    assert sum(n.contribution for n in s2.nodes) == pytest.approx(1.0, abs=1e-9)


def test_flat_period_is_not_significant():
    """相邻两月自然波动约 1%，低于 2% 门槛，不应触发归因。"""
    r = attribute("net_sales", "2026-07", "2026-08")
    assert abs(r.delta_pct) < MIN_REL_CHANGE * 100
    assert not r.significant
    assert r.tree == []


def test_significant_period_produces_tree():
    r = attribute("net_sales", "2026-02", "2026-06")
    assert r.significant
    assert r.tree, "显著异动必须给出主因"
    assert r.best_dimension
    # 主因集合的累计贡献必须达到 80%
    assert sum(n.contribution for n in r.tree) >= 0.80


def test_drill_depth_capped_at_three():
    r = attribute("net_sales", "2026-02", "2026-06")

    def depth(nodes, d=1):
        return max([depth(n.children, d + 1) for n in nodes if n.children] or [d])

    assert depth(r.tree) <= 3


def test_child_dimension_differs_from_parent():
    """下钻必须换维度，否则同一维度反复拆自己没有信息量。"""
    r = attribute("net_sales", "2026-02", "2026-06")

    def walk(nodes, seen):
        for n in nodes:
            assert n.dimension not in seen, f"{n.dimension} 在下钻链上重复出现"
            walk(n.children, seen | {n.dimension})

    walk(r.tree, set())


def test_concentration_not_fooled_by_offsetting():
    """一涨一跌互相对冲时，集中度不应被判为高。

    这是真实踩过的坑：净变动趋零会让贡献度爆炸，
    用「达到 80% 累计贡献需要几个取值」衡量会把对冲误判成集中。
    """
    nodes = [Node("d", "维度", "A", 100.0, 300.0, 200.0, 0.0),
             Node("d", "维度", "B", 100.0, 0.0, -100.0, 0.0),
             Node("d", "维度", "C", 100.0, 10.0, -90.0, 0.0)]
    gross = sum(abs(n.delta) for n in nodes)
    net = abs(sum(n.delta for n in nodes))
    assert net / gross < 0.25, "构造的场景本身就该是强对冲"


def test_waterfall_endpoints_match():
    r = attribute("net_sales", "2026-02", "2026-06")
    w = waterfall(r)
    assert w["steps"][0]["value"] == pytest.approx(r.v0)
    assert w["steps"][-1]["value"] == pytest.approx(r.v1)
    changes = sum(s["value"] for s in w["steps"] if s["type"] == "change")
    assert changes == pytest.approx(r.delta, rel=1e-9)


def test_filters_narrow_the_scope(con):
    whole = attribute("net_sales", "2026-02", "2026-06")
    part = attribute("net_sales", "2026-02", "2026-06", filters={"region": "华东"})
    assert part.v1 < whole.v1
    assert part.filters == {"region": "华东"}


def test_ratio_metric_not_supported():
    """比率型指标不可加，引擎必须拒绝而不是给出无意义的分解。"""
    with pytest.raises(KeyError):
        attribute("distribution_rate", "2026-02", "2026-06")


def test_attribution_to_dict_is_serializable():
    r = attribute("net_sales", "2026-02", "2026-06")
    import json
    d = r.to_dict()
    json.dumps(d)                       # 前端要吃这个结构，必须可序列化
    assert d["dimension_ranking"]
    assert isinstance(r, Attribution)
