"""查询指纹回归测试。

重点覆盖三类重叠匹配：维度取值互相重叠、时间模式互相包含、指标术语互相包含。
这三类是语义缓存误命中的主要来源，误命中会直接把错误结果返回给业务方。
"""

import pytest

from app.retrieval.fingerprint import extract, scope_of

BASE = "华东区上个月的销售额是多少"


@pytest.mark.parametrize("q", [
    "上月华东的销额是多少",
    "华东大区上个月销售额",
    "华东区上月销售金额多少",
    "查一下华东上个月的销售金额",
])
def test_paraphrase_same_fingerprint(q):
    assert extract(q).key() == extract(BASE).key()


@pytest.mark.parametrize("q,reason", [
    ("华南区上个月的销售额是多少", "换大区"),
    ("华东区这个月的销售额是多少", "换时间"),
    ("上上个月华东区的销售额", "换时间"),
    ("华东区上个月的销量是多少", "换指标"),
    ("华东区上个月的铺市率是多少", "换指标"),
    ("华东区上个月的新品铺市率是多少", "指标术语包含关系"),
    ("华东区上个月便利店的销售额", "多一个渠道筛选"),
])
def test_different_query_different_fingerprint(q, reason):
    assert extract(q).key() != extract(BASE).key(), reason


def test_region_not_mistaken_as_district():
    """「华东区」中的「东区」不得被识别为区县取值。"""
    fp = extract(BASE)
    assert "region=华东" in fp.values
    assert not any(v.startswith("district=") for v in fp.values)


def test_absolute_month_not_duplicated_as_year():
    """「2026年8月」只产出一个年月标记，不得同时产出裸年份。"""
    assert extract("2026年8月的销售额").times == ["2026-8"]


def test_longer_time_expression_wins():
    assert extract("上上个月的销售额").times == ["MONTH-2"]
    assert extract("上个月的销售额").times == ["MONTH-1"]


def test_longer_metric_term_wins():
    assert extract("新品铺市率").metrics == ["new_product_distribution"]


def test_multi_dimension_extraction():
    fp = extract("2026年8月清源品牌在便利店渠道的费效比")
    assert fp.metrics == ["promo_roi"]
    assert set(fp.values) == {"brand=清源", "channel=便利店"}
    assert fp.times == ["2026-8"]


def test_empty_fingerprint_falls_back_to_isolated_scope():
    """无法抽出任何要素时退化为独立作用域，宁可不命中也不错命中。"""
    a, b = scope_of("帮我随便看看"), scope_of("随便给我点什么")
    assert a != b and a.startswith("nofp:")
