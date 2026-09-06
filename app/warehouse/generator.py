"""事实表与铺货关系生成。

设计要点：
1. 长尾——SKU 热度按 Zipf 分布，头部十余个单品贡献过半销量，符合快消真实结构。
2. 渠道差异——大卖场单店产出是传统食杂的十余倍，但终端数不足其九分之一。
3. 季节性——按品类给出 12 个月乘数，水与碳酸夏季峰值，乳饮料春节档走高。
4. 促销弹性——促销以「月 × 大区 × SKU」为campaign 单位，命中者销量抬升并产生费用行，
   使费效比与归因分析有真实可追溯的因果，而非纯噪声。

销量走泊松分布，天然产生「铺货但当日无动销」的零值，零值不落库，
因此 fact_sales 的行数即真实动销记录数。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa

# 品类 × 月份销量乘数（索引 0 为 1 月）
SEASONALITY: dict[str, list[float]] = {
    "饮用水":   [0.72, 0.70, 0.85, 1.00, 1.22, 1.45, 1.62, 1.55, 1.18, 0.95, 0.80, 0.72],
    "碳酸饮料": [0.80, 0.88, 0.92, 1.02, 1.20, 1.38, 1.50, 1.44, 1.12, 0.95, 0.85, 0.82],
    "果汁":     [1.12, 1.18, 0.95, 0.94, 1.00, 1.08, 1.15, 1.10, 0.96, 0.92, 0.95, 1.05],
    "茶饮料":   [0.78, 0.82, 0.94, 1.05, 1.24, 1.40, 1.52, 1.46, 1.14, 0.96, 0.84, 0.78],
    "功能饮料": [0.85, 0.80, 0.95, 1.05, 1.18, 1.30, 1.36, 1.32, 1.10, 1.00, 0.92, 0.88],
    "乳饮料":   [1.28, 1.32, 1.02, 0.95, 0.92, 0.90, 0.88, 0.90, 0.96, 1.00, 1.08, 1.20],
}
CHANNEL_MULT = {"大卖场": 2.6, "连锁超市": 1.7, "便利店": 1.0,
                "传统食杂": 0.72, "餐饮": 0.85, "特通": 0.75}
GRADE_MULT = {"A": 1.9, "B": 1.35, "C": 0.90, "D": 0.62}
# 各渠道对单一品牌的覆盖概率。铺市率的缺口来自这里——
# 竞品专营、渠道不匹配、经销商未开发，现实中没有哪个品牌能进所有终端。
CHANNEL_BRAND_COVERAGE = {"大卖场": 0.97, "连锁超市": 0.90, "便利店": 0.84,
                          "传统食杂": 0.60, "餐饮": 0.52, "特通": 0.68}
UNDEVELOPED_OUTLET_RATE = 0.06   # 有效但尚未铺货的终端占比
# 各渠道铺货 SKU 数区间：大卖场品项齐全，食杂店只进快销单品
LISTED_RANGE = {"大卖场": (75, 110), "连锁超市": (55, 90), "便利店": (35, 65),
                "传统食杂": (15, 40), "餐饮": (12, 30), "特通": (15, 35)}
DOW_MULT = np.array([0.94, 0.92, 0.95, 1.00, 1.14, 1.28, 1.20])  # 周一至周日
PROMO_TYPES = ["陈列奖励", "买赠", "特价", "堆头", "DM"]


@dataclass
class GenConfig:
    start: dt.date = dt.date(2024, 9, 1)
    end: dt.date = dt.date(2026, 8, 31)
    n_dealers: int = 180
    n_outlets: int = 2000
    n_reps: int = 40
    seed: int = 20260904
    velocity_scale: float = 1.0      # 全局销量缩放，用于校准目标行数
    monthly_growth: float = 0.004    # 大盘月度自然增长
    promo_share: float = 0.13        # 每月进入促销的「大区 × SKU」组合占比
    promo_lift: float = 0.62         # 促销期销量抬升幅度
    visits_per_outlet_month: float = 4.0
    months: list[tuple[int, int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.months:
            y, m = self.start.year, self.start.month
            while (y, m) <= (self.end.year, self.end.month):
                self.months.append((y, m))
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _month_days(y: int, m: int, lo: dt.date, hi: dt.date) -> list[dt.date]:
    nxt = dt.date(y + 1, 1, 1) if m == 12 else dt.date(y, m + 1, 1)
    d, out = dt.date(y, m, 1), []
    while d < nxt:
        if lo <= d <= hi:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def build_rel_outlet_sku(rng: np.random.Generator, outlets: pa.Table, products: pa.Table,
                         cfg: GenConfig) -> pa.Table:
    """铺货关系。SKU 被选中的概率服从 Zipf，形成头部集中的长尾结构。"""
    o = outlets.to_pylist()
    p = products.to_pylist()
    n_sku = len(p)
    # Zipf 热度：随机打乱名次后取 1/rank^0.65，头部单品铺货面显著更广
    rank = rng.permutation(n_sku) + 1
    popularity = 1.0 / rank**0.65
    pick_p = popularity / popularity.sum()

    launch_key = np.array([int(x["launch_date"].strftime("%Y%m%d")) for x in p])
    start_key = int(cfg.start.strftime("%Y%m%d"))
    brands = sorted({x["brand"] for x in p})
    sku_brand = np.array([x["brand"] for x in p])

    rows_o, rows_s, rows_listed, rows_vel = [], [], [], []
    for out in o:
        if not out["is_active"] or rng.random() < UNDEVELOPED_OUTLET_RATE:
            continue
        # 先定该终端经营哪些品牌，再在这些品牌内选品，铺市率缺口由此产生
        cov = CHANNEL_BRAND_COVERAGE[out["channel"]]
        carried = [b for b in brands if rng.random() < cov] or [brands[rng.integers(len(brands))]]
        pool = np.nonzero(np.isin(sku_brand, carried))[0]
        lo, hi = LISTED_RANGE[out["channel"]]
        k = min(int(rng.integers(lo, hi + 1)), pool.size)
        sub_p = pick_p[pool] / pick_p[pool].sum()
        idx = rng.choice(pool, size=k, replace=False, p=sub_p)
        cm = CHANNEL_MULT[out["channel"]] * GRADE_MULT[out["outlet_grade"]]
        for i in idx:
            # 新品的铺货生效日不早于上市日
            listed = max(start_key, int(launch_key[i]))
            base = 0.55 * cm * (popularity[i] / popularity.mean()) ** 0.45
            vel = float(base * rng.lognormal(0.0, 0.95) * cfg.velocity_scale)
            rows_o.append(out["outlet_id"])
            rows_s.append(p[i]["sku_id"])
            rows_listed.append(listed)
            rows_vel.append(round(vel, 3))
    return pa.table({
        "outlet_id": pa.array(rows_o, pa.int32()),
        "sku_id": pa.array(rows_s, pa.int32()),
        "listed_key": pa.array(rows_listed, pa.int32()),
        "base_velocity": pa.array(rows_vel, pa.float32()),
    })


def build_promo_plan(rng: np.random.Generator, cfg: GenConfig, regions: list[str],
                     n_sku: int) -> np.ndarray:
    """促销计划：[月, 大区, SKU] 布尔矩阵。同一 campaign 覆盖一个大区的一个单品一整月。"""
    plan = rng.random((len(cfg.months), len(regions), n_sku)) < cfg.promo_share
    return plan


class FactBuilder:
    """按月生成事实数据。分月是为了把峰值内存压在百兆级，8GB 机器可跑。"""

    def __init__(self, rng: np.random.Generator, cfg: GenConfig, outlets: pa.Table,
                 products: pa.Table, rel: pa.Table, dates: pa.Table) -> None:
        self.rng, self.cfg = rng, cfg
        o = outlets.to_pylist()
        p = products.to_pylist()
        self.regions = sorted({x["region"] for x in o})
        reg_ix = {r: i for i, r in enumerate(self.regions)}
        sku_ix = {x["sku_id"]: i for i, x in enumerate(p)}
        out_ix = {x["outlet_id"]: i for i, x in enumerate(o)}
        self.n_sku = len(p)
        self.dealer_ids = np.array(sorted({x["dealer_id"] for x in o}), dtype=np.int32)
        deal_ix = {d: i for i, d in enumerate(self.dealer_ids)}

        # 逐 pair 展平，后续全部向量化运算
        ro = rel.column("outlet_id").to_numpy()
        rs = rel.column("sku_id").to_numpy()
        self.pair_outlet = ro.astype(np.int32)
        self.pair_sku = rs.astype(np.int32)
        self.pair_skuix = np.array([sku_ix[s] for s in rs], dtype=np.int32)
        self.pair_dealer = np.array([o[out_ix[x]]["dealer_id"] for x in ro], dtype=np.int32)
        self.pair_dealerix = np.array([deal_ix[d] for d in self.pair_dealer], dtype=np.int32)
        self.pair_regionix = np.array([reg_ix[o[out_ix[x]]["region"]] for x in ro], dtype=np.int8)
        self.pair_vel = rel.column("base_velocity").to_numpy().astype(np.float64)
        self.pair_listed = rel.column("listed_key").to_numpy().astype(np.int32)
        self.pair_price = np.array([float(p[sku_ix[s]]["list_price"]) for s in rs])
        self.pair_cat = np.array([p[sku_ix[s]]["category"] for s in rs])
        self.seasonal = np.zeros((len(rs), 12))
        for cat, mult in SEASONALITY.items():
            self.seasonal[self.pair_cat == cat] = mult

        dk = dates.column("date_key").to_numpy()
        self.holiday = dict(zip(dk.tolist(), dates.column("is_holiday").to_pylist(), strict=True))
        self.promo_plan = build_promo_plan(rng, cfg, self.regions, self.n_sku)
        # 每个 campaign 固定一种促销形式，费用与销售可对应追溯
        self.promo_type_ix = rng.integers(
            0, len(PROMO_TYPES), size=self.promo_plan.shape).astype(np.int8)

    def month_sales(self, mi: int) -> tuple[pa.Table, np.ndarray, np.ndarray]:
        """返回当月动销明细，以及供费用与库存推导的 pair 级、经销商级月度汇总。"""
        cfg, rng = self.cfg, self.rng
        y, m = cfg.months[mi]
        days = _month_days(y, m, cfg.start, cfg.end)
        day_keys = np.array([int(d.strftime("%Y%m%d")) for d in days], dtype=np.int32)
        dow = np.array([d.isoweekday() - 1 for d in days])
        day_mult = DOW_MULT[dow] * np.where(
            [self.holiday.get(int(k), False) for k in day_keys], 1.18, 1.0)

        active = self.pair_listed <= day_keys[-1]
        promo = self.promo_plan[mi][self.pair_regionix, self.pair_skuix] & active
        trend = (1.0 + cfg.monthly_growth) ** mi
        # 大区级月度自然波动，制造非促销因素的正常起伏
        reg_noise = rng.lognormal(0.0, 0.045, size=len(self.regions))[self.pair_regionix]

        lam_pair = (self.pair_vel * self.seasonal[:, m - 1] * trend * reg_noise
                    * np.where(promo, 1.0 + cfg.promo_lift, 1.0) * active)
        lam = lam_pair[:, None] * day_mult[None, :]
        qty = rng.poisson(lam)
        pi, di = np.nonzero(qty)
        q = qty[pi, di].astype(np.int32)

        gross = q * self.pair_price[pi]
        is_promo = promo[pi]
        rate = np.where(is_promo, rng.uniform(0.12, 0.22, q.size), rng.uniform(0.0, 0.04, q.size))
        disc = np.round(gross * rate, 2)
        net = np.round(gross - disc, 2)

        tbl = pa.table({
            "date_key": pa.array(day_keys[di], pa.int32()),
            "outlet_id": pa.array(self.pair_outlet[pi], pa.int32()),
            "sku_id": pa.array(self.pair_sku[pi], pa.int32()),
            "dealer_id": pa.array(self.pair_dealer[pi], pa.int32()),
            "qty": pa.array(q, pa.int32()),
            "gross_amount": pa.array(np.round(gross, 2)),
            "discount_amount": pa.array(disc),
            "net_amount": pa.array(net),
            "is_promo": pa.array(is_promo),
        })
        pair_net = np.bincount(pi, weights=net, minlength=len(self.pair_vel))
        dealer_sku_qty = np.bincount(
            self.pair_dealerix[pi] * self.n_sku + self.pair_skuix[pi],
            weights=q, minlength=len(self.dealer_ids) * self.n_sku)
        return tbl, pair_net, dealer_sku_qty

    def month_promo_cost(self, mi: int, pair_net: np.ndarray) -> pa.Table:
        """促销费用由当月促销销额除以目标费效比反推，使 ROI 落在业务合理区间。"""
        rng, cfg = self.rng, self.cfg
        y, m = cfg.months[mi]
        active = self.pair_listed <= int(dt.date(y, m, 1).strftime("%Y%m%d"))
        promo = self.promo_plan[mi][self.pair_regionix, self.pair_skuix] & active
        sel = np.nonzero(promo & (pair_net > 0))[0]
        if sel.size == 0:
            return pa.table({k: pa.array([], t) for k, t in
                             [("date_key", pa.int32()), ("outlet_id", pa.int32()),
                              ("sku_id", pa.int32()), ("dealer_id", pa.int32()),
                              ("promo_type", pa.string()), ("cost_amount", pa.float64())]})
        roi = rng.lognormal(np.log(6.0), 0.38, sel.size)
        cost = np.round(pair_net[sel] / roi, 2)
        ptype = self.promo_type_ix[mi][self.pair_regionix[sel], self.pair_skuix[sel]]
        return pa.table({
            "date_key": pa.array(np.full(sel.size, int(dt.date(y, m, 1).strftime("%Y%m%d")),
                                         dtype=np.int32), pa.int32()),
            "outlet_id": pa.array(self.pair_outlet[sel], pa.int32()),
            "sku_id": pa.array(self.pair_sku[sel], pa.int32()),
            "dealer_id": pa.array(self.pair_dealer[sel], pa.int32()),
            "promo_type": pa.array([PROMO_TYPES[i] for i in ptype]),
            "cost_amount": pa.array(cost),
        })

    def month_inventory(self, mi: int, dealer_sku_qty: np.ndarray, n_days: int) -> pa.Table:
        """月末库存快照：按当月出货速度乘以周转天数反推在库量。"""
        rng, cfg = self.rng, self.cfg
        y, m = cfg.months[mi]
        last = _month_days(y, m, cfg.start, cfg.end)[-1]
        grid = dealer_sku_qty.reshape(len(self.dealer_ids), self.n_sku)
        di, si = np.nonzero(grid)
        if di.size == 0:
            di, si = np.array([0]), np.array([0])
        daily_out = grid[di, si] / n_days
        turnover = rng.lognormal(np.log(22.0), 0.42, di.size)
        on_hand = np.maximum(1, np.round(daily_out * turnover)).astype(np.int32)
        shelf = rng.integers(120, 361, di.size)
        # 周转越慢临期比例越高，形成可被归因发现的因果
        near_ratio = np.clip(rng.beta(1.2, 18.0, di.size) * (turnover / 22.0), 0, 0.5)
        return pa.table({
            "date_key": pa.array(np.full(di.size, int(last.strftime("%Y%m%d")), dtype=np.int32),
                                 pa.int32()),
            "dealer_id": pa.array(self.dealer_ids[di], pa.int32()),
            "sku_id": pa.array((si + 1).astype(np.int32), pa.int32()),
            "qty_on_hand": pa.array(on_hand, pa.int32()),
            "qty_in_transit": pa.array(
                np.round(on_hand * rng.uniform(0.0, 0.35, di.size)).astype(np.int32), pa.int32()),
            "near_expiry_qty": pa.array(np.round(on_hand * near_ratio).astype(np.int32),
                                        pa.int32()),
            "avg_shelf_life_days": pa.array(
                np.round(shelf * (1 - near_ratio)).astype(np.int16), pa.int16()),
        })

    def month_visits(self, mi: int, outlets: pa.Table, reps: pa.Table) -> pa.Table:
        """拜访计划与执行。达成率随大区与月份小幅波动，为归因留出可解释的差异。"""
        rng, cfg = self.rng, self.cfg
        y, m = cfg.months[mi]
        days = _month_days(y, m, cfg.start, cfg.end)
        day_keys = np.array([int(d.strftime("%Y%m%d")) for d in days], dtype=np.int32)
        workday = np.array([d.isoweekday() <= 6 for d in days])
        wd_keys = day_keys[workday]

        o = outlets.to_pylist()
        r = [x for x in reps.to_pylist() if x["is_active"]]
        rep_by_region: dict[str, list[int]] = {}
        for x in r:
            rep_by_region.setdefault(x["region"], []).append(x["rep_id"])
        act = [x for x in o if x["is_active"]]
        # 门店等级越高拜访越密
        freq = np.array([{"A": 1.9, "B": 1.4, "C": 1.0, "D": 0.75}[x["outlet_grade"]] for x in act])
        n_visit = rng.poisson(cfg.visits_per_outlet_month * freq)
        total = int(n_visit.sum())
        outlet_ids = np.repeat([x["outlet_id"] for x in act], n_visit)
        regions = np.repeat([x["region"] for x in act], n_visit)
        rep_ids = np.array([rep_by_region[g][rng.integers(len(rep_by_region[g]))]
                            for g in regions], dtype=np.int32)
        planned = rng.random(total) > 0.06
        base_exec = 0.885 + rng.normal(0, 0.03)
        executed = rng.random(total) < np.where(planned, base_exec, 1.0)
        return pa.table({
            "date_key": pa.array(wd_keys[rng.integers(0, wd_keys.size, total)], pa.int32()),
            "rep_id": pa.array(rep_ids, pa.int32()),
            "outlet_id": pa.array(outlet_ids.astype(np.int32), pa.int32()),
            "is_planned": pa.array(planned),
            "is_executed": pa.array(executed),
            "duration_min": pa.array(
                np.where(executed, rng.integers(8, 46, total), 0).astype(np.int16), pa.int16()),
            "has_order": pa.array(executed & (rng.random(total) < 0.56)),
            "has_display_fix": pa.array(executed & (rng.random(total) < 0.31)),
        })
