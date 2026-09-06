"""埋因场景生成与归因验证。

做法：在只读数仓之上挂一个内存库，把对比期两个月的动销明细物化一次，
再为每个场景建一个带乘数的视图——注入是视图层的 CASE 表达式，不改动原始数据。

注入强度不是拍一个百分比，而是**按目标影响量反推**：
先算该取值在大盘中的份额 s，要制造 I 的总盘影响，系数即 1 + I/s。
否则给一个只占 2% 份额的单品打 7 折，总盘只动 0.6%，落在自然波动里，
测的就不是归因算法而是"这个变化大不大"。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import duckdb
import numpy as np
import pyarrow as pa

from app.attribution.engine import DIMENSIONS, attribute
from app.config import get_settings

BASE_T0, BASE_T1 = "2026-07", "2026-08"
DIM_TABLE_ALIAS = {"dim_outlet": "o", "dim_product": "p", "dim_dealer": "dl"}


@dataclass
class Cause:
    dimension: str
    value: str
    factor: float
    share: float                 # 该取值在 t1 大盘中的份额
    impact: float                # 对总盘的影响比例

    def label(self) -> str:
        return f"{DIMENSIONS[self.dimension][2]}={self.value}"


@dataclass
class Scenario:
    sid: str
    kind: str                    # single | multi_same_dim | multi_cross_dim
    metric: str
    causes: list[Cause] = field(default_factory=list)
    noise_seed: int = 0


@dataclass
class Verdict:
    sid: str
    kind: str
    hit: bool
    found: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)
    best_dim: str = ""
    note: str = ""


class ScenarioLab:
    def __init__(self, t0: str = BASE_T0, t1: str = BASE_T1) -> None:
        self.t0, self.t1 = t0, t1
        self.con = duckdb.connect(":memory:")
        self.con.execute("SET memory_limit='2GB'; SET threads=4;")
        self.con.execute(
            f"ATTACH '{get_settings().duckdb_file}' AS wh (READ_ONLY)")
        for t in ["dim_date", "dim_outlet", "dim_product", "dim_dealer", "dim_rep",
                  "rel_outlet_sku", "fact_promo_cost", "fact_inventory"]:
            self.con.execute(f"CREATE VIEW {t} AS SELECT * FROM wh.{t}")
        # 只物化对比期两个月，约 290 万行
        self.con.execute(f"""
            CREATE TABLE base AS
            SELECT f.* FROM wh.fact_sales f
            JOIN wh.dim_date d ON f.date_key = d.date_key
            WHERE d.year_month IN ('{t0}', '{t1}')""")
        self.n_base = self.con.sql("SELECT count(*) FROM base").fetchone()[0]
        self._share_cache: dict[str, dict[str, float]] = {}
        self._slice_cache: dict[tuple[str, str], tuple[str, frozenset]] = {}

    def close(self) -> None:
        self.con.close()

    def shares(self, dim: str) -> dict[str, float]:
        """各取值在 t1 大盘中的销额份额，用于反推注入系数。"""
        if dim not in self._share_cache:
            tbl, col, _, key = DIMENSIONS[dim]
            rows = self.con.sql(f"""
                SELECT {tbl}.{col}, SUM(b.net_amount) FROM base b
                JOIN dim_date d ON b.date_key = d.date_key
                JOIN {tbl} ON b.{key} = {tbl}.{key}
                WHERE d.year_month = '{self.t1}' GROUP BY 1""").fetchall()
            tot = sum(float(x[1]) for x in rows) or 1.0
            self._share_cache[dim] = {str(v): float(a) / tot for v, a in rows}
        return self._share_cache[dim]

    def slice_of(self, dim: str, value: str) -> tuple[str, frozenset]:
        """取值所覆盖的实体集合，归一到「终端轴」或「单品轴」。

        判定是否命中要看切片是否等价，而不是标签是否相同：
        本数据中每个品牌只归属一个品类，「品牌=鲜果道」与「品类=果汁」
        选中的是完全相同的行，引擎报哪个都同样正确，
        按字面标签判定会把正确答案判成漏判。
        """
        ck = (dim, value)
        if ck not in self._slice_cache:
            tbl, col, _, key = DIMENSIONS[dim]
            if tbl == "dim_dealer":
                rows = self.con.sql(
                    f"SELECT o.outlet_id FROM dim_outlet o JOIN dim_dealer dl "
                    f"ON o.dealer_id = dl.dealer_id WHERE dl.{col} = ?", params=[value]).fetchall()
                self._slice_cache[ck] = ("outlet", frozenset(r[0] for r in rows))
            else:
                rows = self.con.sql(
                    f"SELECT {key} FROM {tbl} WHERE {col} = ?", params=[value]).fetchall()
                axis = "sku" if tbl == "dim_product" else "outlet"
                self._slice_cache[ck] = (axis, frozenset(r[0] for r in rows))
        return self._slice_cache[ck]

    def make_cause(self, dim: str, value: str, impact: float) -> Cause | None:
        """impact 为对总盘的目标影响比例，负数表示下滑。系数越界则放弃该取值。"""
        s = self.shares(dim).get(value, 0.0)
        if s <= 0:
            return None
        factor = 1.0 + impact / s
        if not (0.30 <= factor <= 3.0):
            return None
        return Cause(dimension=dim, value=value, factor=round(factor, 4), share=s,
                     impact=impact)

    def _build_noise(self, seed: int, outlet_sigma: float = 0.10,
                     sku_sigma: float = 0.07) -> None:
        """终端级与单品级的月度随机波动。

        真实数据里每家店、每个单品的月度销额本就会自然浮动，归因算法要做的是
        从这层噪声里把真正的成因分离出来。没有这层噪声，埋因场景的信噪比高到
        不真实，测出来的准确率虚高。噪声按终端与单品两个轴施加，不偏袒任何维度。
        """
        rng = np.random.default_rng(seed)
        for tbl, key in (("noise_outlet", "outlet_id"), ("noise_sku", "sku_id")):
            ids = [r[0] for r in self.con.sql(
                f"SELECT DISTINCT {key} FROM base").fetchall()]
            sigma = outlet_sigma if key == "outlet_id" else sku_sigma
            arr = pa.table({key: pa.array(ids, pa.int32()),
                            "f": pa.array(rng.lognormal(0.0, sigma, len(ids)))})
            self.con.register("_noise_stage", arr)
            self.con.execute(f"CREATE OR REPLACE TABLE {tbl} AS SELECT * FROM _noise_stage")
            self.con.unregister("_noise_stage")

    def build_view(self, causes: list[Cause], noise_seed: int = 0) -> None:
        needed = {DIMENSIONS[c.dimension][0] for c in causes}
        joins = ["JOIN dim_date d ON b.date_key = d.date_key"]
        for t in sorted(needed):
            key = {"dim_outlet": "outlet_id", "dim_product": "sku_id",
                   "dim_dealer": "dealer_id"}[t]
            joins.append(f"JOIN {t} {DIM_TABLE_ALIAS[t]} ON b.{key} = {DIM_TABLE_ALIAS[t]}.{key}")
        terms = []
        for c in causes:
            tbl, col, _, _ = DIMENSIONS[c.dimension]
            a = DIM_TABLE_ALIAS[tbl]
            terms.append(f"(CASE WHEN d.year_month = '{self.t1}' AND {a}.{col} = "
                         f"'{c.value}' THEN {c.factor} ELSE 1 END)")
        mult = " * ".join(terms) if terms else "1"
        if noise_seed:
            self._build_noise(noise_seed)
            joins.append("JOIN noise_outlet no ON b.outlet_id = no.outlet_id")
            joins.append("JOIN noise_sku ns ON b.sku_id = ns.sku_id")
            mult += (f" * (CASE WHEN d.year_month = '{self.t1}' "
                     f"THEN no.f * ns.f ELSE 1 END)")
        self.con.execute(f"""
            CREATE OR REPLACE VIEW scn AS
            SELECT b.date_key, b.outlet_id, b.sku_id, b.dealer_id, b.qty,
                   b.gross_amount, b.discount_amount,
                   b.net_amount * {mult} AS net_amount, b.is_promo
            FROM base b {' '.join(joins)}""")

    def evaluate(self, scn: Scenario) -> Verdict:
        self.build_view(scn.causes, scn.noise_seed)
        res = attribute(scn.metric, self.t0, self.t1, con=self.con, sales_table="scn")
        if not res.significant:
            return Verdict(sid=scn.sid, kind=scn.kind, hit=False,
                           missed=[c.label() for c in scn.causes],
                           note=f"总变动 {res.delta_pct:+.1f}% 未达显著阈值")

        found: list[tuple[str, str]] = []

        def walk(nodes) -> None:
            for n in nodes:
                found.append((n.dimension, n.value))
                walk(n.children)

        walk(res.tree)
        found_slices = [(d, v, self.slice_of(d, v)) for d, v in found]

        hits, miss = [], []
        for c in scn.causes:
            target = self.slice_of(c.dimension, c.value)
            same = next(((d, v) for d, v, sl in found_slices if sl == target), None)
            if same is None:
                miss.append(c.label())
            elif (same[0], same[1]) == (c.dimension, c.value):
                hits.append(c.label())
            else:
                hits.append(f"{c.label()}（引擎报为等价切片 "
                            f"{DIMENSIONS[same[0]][2]}={same[1]}）")
        return Verdict(
            sid=scn.sid, kind=scn.kind, hit=not miss,
            found=hits, missed=miss,
            best_dim=res.best_dimension,
            note=f"总变动 {res.delta_pct:+.1f}%，主拆解维度 {DIMENSIONS[res.best_dimension][2]}"
                 if res.best_dimension else "")


# 场景配方：维度、候选取值来源、目标影响区间
SINGLE_DIMS = ["region", "channel", "category", "brand", "grade", "province",
               "dealer", "sku"]


def build_scenarios(lab: ScenarioLab, seed: int = 20260906) -> list[Scenario]:
    """40 组：20 单因、12 同维双因、8 跨维双因。

    三类的**总注入量拉齐在 5%～9%**，使难度差异只来自归因本身（单个成因 vs
    两个成因，同维 vs 跨维），而不是信号强弱。否则单因给 2%、双因给两个 6%，
    测出来的"单因更难"其实只是信号更弱。
    """
    rng = random.Random(seed)
    out: list[Scenario] = []

    def pick(dim: str, k: int = 1, top: int = 12) -> list[str]:
        vals = sorted(lab.shares(dim).items(), key=lambda x: -x[1])[:top]
        return [v for v, _ in rng.sample(vals, min(k, len(vals)))]

    n = 0
    while len([s for s in out if s.kind == "single"]) < 20:
        dim = SINGLE_DIMS[n % len(SINGLE_DIMS)]
        n += 1
        val = pick(dim)[0]
        impact = rng.choice([-1, 1]) * rng.uniform(0.05, 0.09)
        c = lab.make_cause(dim, val, impact)
        if c:
            out.append(Scenario(sid=f"S{len(out) + 1:02d}", kind="single",
                                metric="net_sales", causes=[c],
                                noise_seed=seed + len(out)))

    while len([s for s in out if s.kind == "multi_same_dim"]) < 12:
        dim = SINGLE_DIMS[n % len(SINGLE_DIMS)]
        n += 1
        vals = pick(dim, k=2)
        if len(vals) < 2:
            continue
        sign = rng.choice([-1, 1])
        cs = [lab.make_cause(dim, v, sign * rng.uniform(0.025, 0.045)) for v in vals]
        cs = [c for c in cs if c]
        if len(cs) == 2:
            out.append(Scenario(sid=f"S{len(out) + 1:02d}", kind="multi_same_dim",
                                metric="net_sales", causes=cs,
                                noise_seed=seed + len(out)))

    while len([s for s in out if s.kind == "multi_cross_dim"]) < 8:
        d1, d2 = rng.sample(SINGLE_DIMS, 2)
        n += 1
        sign = rng.choice([-1, 1])
        cs = [lab.make_cause(d1, pick(d1)[0], sign * rng.uniform(0.025, 0.045)),
              lab.make_cause(d2, pick(d2)[0], sign * rng.uniform(0.025, 0.045))]
        cs = [c for c in cs if c]
        if len(cs) == 2 and cs[0].dimension != cs[1].dimension:
            out.append(Scenario(sid=f"S{len(out) + 1:02d}", kind="multi_cross_dim",
                                metric="net_sales", causes=cs,
                                noise_seed=seed + len(out)))
    return out
