"""维度表生成。

所有随机性统一走传入的 numpy Generator，固定种子即可完整复现。
地理与渠道分布参照快消行业常见结构：传统食杂占终端数近半但单店产出最低，
大卖场数量少而产出高，这一反差是后续归因分析的基础。
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa

REGIONS: dict[str, list[tuple[str, list[str]]]] = {
    "华东": [("江苏", ["南京", "苏州", "无锡", "常州"]), ("浙江", ["杭州", "宁波", "温州"]),
             ("上海", ["上海"]), ("安徽", ["合肥", "芜湖"])],
    "华南": [("广东", ["广州", "深圳", "东莞", "佛山"]), ("福建", ["福州", "厦门"]),
             ("广西", ["南宁", "柳州"])],
    "华北": [("北京", ["北京"]), ("天津", ["天津"]), ("河北", ["石家庄", "唐山"]),
             ("山东", ["济南", "青岛", "烟台"])],
    "华中": [("湖北", ["武汉", "宜昌"]), ("湖南", ["长沙", "株洲"]), ("河南", ["郑州", "洛阳"])],
    "西南": [("四川", ["成都", "绵阳"]), ("重庆", ["重庆"]), ("云南", ["昆明"]),
             ("贵州", ["贵阳"])],
    "西北": [("陕西", ["西安", "咸阳"]), ("甘肃", ["兰州"])],
    "东北": [("辽宁", ["沈阳", "大连"]), ("吉林", ["长春"]), ("黑龙江", ["哈尔滨"])],
}

# 大区终端配额权重：华东华南为核心市场
REGION_WEIGHT = {"华东": 0.26, "华南": 0.21, "华北": 0.17, "华中": 0.14,
                 "西南": 0.12, "东北": 0.06, "西北": 0.04}

CHANNELS = ["大卖场", "连锁超市", "便利店", "传统食杂", "餐饮", "特通"]
CHANNEL_WEIGHT = np.array([0.05, 0.14, 0.22, 0.42, 0.13, 0.04])
# 各渠道的门店等级分布（A/B/C/D），大卖场几乎全是 A/B，食杂店集中在 C/D
CHANNEL_GRADE_DIST = {
    "大卖场":   [0.70, 0.25, 0.05, 0.00],
    "连锁超市": [0.30, 0.45, 0.20, 0.05],
    "便利店":   [0.08, 0.30, 0.42, 0.20],
    "传统食杂": [0.01, 0.09, 0.35, 0.55],
    "餐饮":     [0.05, 0.20, 0.40, 0.35],
    "特通":     [0.10, 0.30, 0.40, 0.20],
}
GRADES = ["A", "B", "C", "D"]

PRODUCT_LINES = [
    # (品牌, 品类, [(子品类, [口味...])], 规格候选, 基准价区间)
    ("清源", "饮用水",
     [("天然矿泉水", ["原味"]), ("饮用纯净水", ["原味"]), ("苏打水", ["原味", "青柠", "白桃"])],
     [350, 550, 1500], (1.5, 4.0)),
    ("气立方", "碳酸饮料",
     [("可乐型", ["经典", "无糖", "樱桃"]), ("果味汽水", ["白桃", "青提", "柠檬", "荔枝", "橙"])],
     [330, 500, 1250], (3.0, 5.5)),
    ("鲜果道", "果汁",
     [("低浓度果汁", ["橙", "苹果", "葡萄", "桃", "芒果"]), ("NFC果汁", ["橙", "苹果", "西柚"])],
     [300, 450, 1000], (4.5, 12.0)),
    ("茶小叙", "茶饮料",
     [("无糖茶", ["乌龙", "茉莉花茶", "普洱", "绿茶"]), ("调味茶", ["柠檬红茶", "蜜桃乌龙", "青柑普洱"])],
     [500, 900, 1500], (3.5, 6.0)),
    ("燃力", "功能饮料",
     [("能量饮料", ["原味", "荔枝", "青柠"]), ("电解质水", ["白桃", "柑橘", "西柚"])],
     [330, 500, 600], (5.0, 9.0)),
    ("牧诚", "乳饮料",
     [("含乳饮料", ["原味", "草莓", "香蕉", "巧克力"]), ("植物蛋白", ["核桃", "杏仁", "燕麦"])],
     [250, 450], (3.5, 8.0)),
]

SURNAMES = list("王李张刘陈杨黄赵吴周徐孙马朱胡林郭何高罗郑梁谢宋唐许韩冯邓曹彭曾")
GIVEN = ["伟", "芳", "娜", "敏", "静", "磊", "强", "军", "洋", "勇", "艳", "杰", "涛",
         "明", "超", "秀英", "霞", "平", "刚", "桂英", "建国", "志强", "小雨", "晨"]
OUTLET_PREFIX = ["兴隆", "福万家", "永旺", "百惠", "民乐", "顺发", "康泰", "新一佳", "好邻居",
                 "四季鲜", "家家悦", "汇丰", "旺角", "利群", "美宜佳", "天天", "惠民", "常青"]
OUTLET_SUFFIX = {"大卖场": ["购物广场", "生活广场"], "连锁超市": ["超市", "生鲜超市"],
                 "便利店": ["便利店", "便利", "24小时店"], "传统食杂": ["食杂店", "小卖部", "商行"],
                 "餐饮": ["餐厅", "快餐店", "面馆"], "特通": ["加油站店", "校园店", "健身房店"]}


def _pick(rng: np.random.Generator, seq, n: int, p=None):
    return [seq[i] for i in rng.choice(len(seq), size=n, p=p)]


def build_dim_date(start: dt.date, end: dt.date) -> pa.Table:
    days = (end - start).days + 1
    dates = [start + dt.timedelta(days=i) for i in range(days)]
    # 主要法定节假日（简化为春节、劳动节、国庆三段）
    holidays = set()
    for y in range(start.year, end.year + 1):
        for m, d, span in ((2, 10, 8), (5, 1, 5), (10, 1, 7)):
            for k in range(span):
                holidays.add(dt.date(y, m, d) + dt.timedelta(days=k))
    seasons = {12: "冬", 1: "冬", 2: "冬", 3: "春", 4: "春", 5: "春",
               6: "夏", 7: "夏", 8: "夏", 9: "秋", 10: "秋", 11: "秋"}
    return pa.table({
        "date_key": pa.array([int(d.strftime("%Y%m%d")) for d in dates], pa.int32()),
        "full_date": pa.array(dates, pa.date32()),
        "year": pa.array([d.year for d in dates], pa.int16()),
        "quarter": pa.array([(d.month - 1) // 3 + 1 for d in dates], pa.int16()),
        "month": pa.array([d.month for d in dates], pa.int16()),
        "year_month": pa.array([d.strftime("%Y-%m") for d in dates]),
        "week_of_year": pa.array([d.isocalendar()[1] for d in dates], pa.int16()),
        "day_of_week": pa.array([d.isoweekday() for d in dates], pa.int16()),
        "is_weekend": pa.array([d.isoweekday() >= 6 for d in dates]),
        "is_holiday": pa.array([d in holidays for d in dates]),
        "season": pa.array([seasons[d.month] for d in dates]),
    })


def build_dim_product(rng: np.random.Generator, start: dt.date, end: dt.date) -> pa.Table:
    rows = []
    sku_id = 1
    for brand, category, subs, specs, (lo, hi) in PRODUCT_LINES:
        for sub, flavors in subs:
            for flavor in flavors:
                for spec in specs:
                    # 价格随规格线性上浮，同时留出品类内的价格带
                    ratio = (spec - min(specs)) / max(1, (max(specs) - min(specs)))
                    price = round(lo + (hi - lo) * ratio + rng.normal(0, 0.15), 1)
                    # 约 12% 的 SKU 为近一年上市的新品
                    if rng.random() < 0.12:
                        launch = end - dt.timedelta(days=int(rng.integers(30, 360)))
                    else:
                        launch = start - dt.timedelta(days=int(rng.integers(200, 2000)))
                    rows.append({
                        "sku_id": sku_id,
                        "sku_code": f"SKU{sku_id:05d}",
                        "sku_name": f"{brand}{sub}{flavor}{spec}ml",
                        "brand": brand, "category": category, "sub_category": sub,
                        "flavor": flavor,
                        "package_type": "罐装" if spec <= 350 and category in ("碳酸饮料", "功能饮料")
                                        else ("利乐包" if category == "乳饮料" and spec <= 250 else "瓶装"),
                        "spec_ml": spec,
                        "pack_size": 24 if spec <= 500 else (12 if spec <= 900 else 6),
                        "list_price": max(1.0, price),
                        "unit_cost": round(max(1.0, price) * rng.uniform(0.42, 0.58), 2),
                        "launch_date": launch,
                        "is_new_product": (end - launch).days <= 365,
                        "is_active": True,
                    })
                    sku_id += 1
    return pa.Table.from_pylist(rows)


def build_dim_dealer(rng: np.random.Generator, n: int, start: dt.date) -> pa.Table:
    regions = _pick(rng, list(REGION_WEIGHT), n, p=list(REGION_WEIGHT.values()))
    rows = []
    for i, region in enumerate(regions, start=1):
        prov, cities = REGIONS[region][rng.integers(len(REGIONS[region]))]
        city = cities[rng.integers(len(cities))]
        level = _pick(rng, ["A", "B", "C"], 1, p=[0.2, 0.45, 0.35])[0]
        rows.append({
            "dealer_id": i, "dealer_code": f"DL{i:04d}",
            "dealer_name": f"{city}{rng.choice(['盛通', '联合', '汇达', '恒信', '嘉和', '万隆', '源泰'])}"
                           f"商贸有限公司",
            "region": region, "province": prov, "city": city, "dealer_level": level,
            "sign_date": start - dt.timedelta(days=int(rng.integers(300, 3000))),
            "credit_limit": float({"A": 3_000_000, "B": 1_200_000, "C": 400_000}[level]
                                  * rng.uniform(0.7, 1.4)),
            "is_active": True,
        })
    return pa.Table.from_pylist(rows)


def build_dim_outlet(rng: np.random.Generator, n: int, dealers: pa.Table,
                     start: dt.date) -> pa.Table:
    d = dealers.to_pylist()
    # 终端按大区权重分配，再落到该大区的经销商上
    by_region: dict[str, list[dict]] = {}
    for row in d:
        by_region.setdefault(row["region"], []).append(row)
    regions = _pick(rng, list(REGION_WEIGHT), n, p=list(REGION_WEIGHT.values()))
    rows = []
    for i, region in enumerate(regions, start=1):
        dealer = by_region[region][rng.integers(len(by_region[region]))]
        channel = CHANNELS[rng.choice(len(CHANNELS), p=CHANNEL_WEIGHT)]
        grade = GRADES[rng.choice(4, p=CHANNEL_GRADE_DIST[channel])]
        rows.append({
            "outlet_id": i, "outlet_code": f"OT{i:06d}",
            "outlet_name": f"{dealer['city']}{rng.choice(OUTLET_PREFIX)}"
                           f"{rng.choice(OUTLET_SUFFIX[channel])}",
            "dealer_id": dealer["dealer_id"], "channel": channel, "outlet_grade": grade,
            "region": region, "province": dealer["province"], "city": dealer["city"],
            "district": f"{rng.choice(list('城东西南北新高开'))}区",
            "open_date": start - dt.timedelta(days=int(rng.integers(100, 4000))),
            "is_active": bool(rng.random() > 0.03),
        })
    return pa.Table.from_pylist(rows)


def build_dim_rep(rng: np.random.Generator, n: int, start: dt.date) -> pa.Table:
    regions = _pick(rng, list(REGION_WEIGHT), n, p=list(REGION_WEIGHT.values()))
    rows = []
    for i, region in enumerate(regions, start=1):
        _, cities = REGIONS[region][rng.integers(len(REGIONS[region]))]
        rows.append({
            "rep_id": i, "rep_code": f"RP{i:04d}",
            "rep_name": f"{rng.choice(SURNAMES)}{rng.choice(GIVEN)}",
            "region": region, "city": cities[rng.integers(len(cities))],
            "team": f"{region}{rng.choice(['一', '二', '三'])}组",
            "hire_date": start - dt.timedelta(days=int(rng.integers(60, 2500))),
            "is_active": bool(rng.random() > 0.05),
        })
    return pa.Table.from_pylist(rows)
