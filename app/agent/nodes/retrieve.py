"""召回：字段、指标、取值三路并行，前两路各自混合检索后 RRF 融合。"""

from __future__ import annotations

from app.agent.nodes._util import timed
from app.agent.state import AgentState
from app.retrieval.fingerprint import extract
from app.retrieval.recall import recall, recall_values

TOP_COLUMN = 18
TOP_METRIC = 4
TOP_TABLE = 5
# 维表相关性下限（相对最高分）。多余维表危害小，只剪明显无关的。
MIN_TABLE_RATIO = 0.15


@timed("recall_column")
def recall_column(state: AgentState) -> dict:
    q = state.get("rewritten") or state["question"]
    hits = recall("column", q, k=TOP_COLUMN, hybrid=state.get("use_hybrid", False))
    return {"column_hits": [{"uid": c.uid, "score": c.score, **c.payload} for c in hits]}


@timed("recall_metric")
def recall_metric(state: AgentState) -> dict:
    """混合检索召回指标，再用字面命中做硬先验重排。

    RRF 只反映排名不反映相关强度，融合后指标间分差被压平到 3% 量级，
    「净销额占比」里的「占比」字面命中促销费用率的别名「费用占比」，
    就足以把促销费用率顶到净销额前面——进而把 fact_promo_cost 拖进上下文。

    指标名和取值一样是有限枚举，字面出现即确定，不需要向量来猜。
    这里复用 fingerprint 的最长匹配（它已处理「新品铺市率 / 铺市率」这类重叠），
    把字面命中的指标提到最前；没有字面命中时才完全交给向量召回。
    """
    q = state.get("rewritten") or state["question"]
    hits = recall("metric", q, k=TOP_METRIC, hybrid=state.get("use_hybrid", False))
    out = [{"uid": c.uid, "score": c.score, **c.payload} for c in hits]

    literal = extract(q).metrics
    if literal:
        rank = {mid: i for i, mid in enumerate(literal)}
        out.sort(key=lambda m: (rank.get(m["id"], len(rank)),))
    return {"metric_hits": out, "literal_metrics": literal}


@timed("recall_value")
def recall_value(state: AgentState) -> dict:
    """维度取值走词典精确匹配。取值是有限枚举，向量在这里只会带来误召回。"""
    q = state.get("rewritten") or state["question"]
    return {"value_hits": recall_values(q)}


@timed("fuse_candidates")
def fuse_candidates(state: AgentState) -> dict:
    """三路汇合后按表打分选表。

    只给 SQL 生成看命中的零散列会导致 JOIN 写错——列必须以「表」为单位整块给出，
    因此这里的产物是表清单而非列清单。
    """
    if not state.get("use_retrieval", True):
        # 消融：完全不做 schema linking，把全部表交给 SQL 生成。
        # 用来回答「检索层该不该存在」——若与主方案无差异，
        # 说明在本规模上召回层可以整个去掉，保留它只为可扩展性。
        from app.retrieval.corpus import column_units
        return {"tables": sorted({u.payload["table"] for u in column_units()})}

    score: dict[str, float] = {}
    for c in state.get("column_hits", []):
        score[c["table"]] = score.get(c["table"], 0.0) + c["score"]
    for m in state.get("metric_hits", [])[:2]:
        for t in m["tables"]:                      # 指标声明的依赖表强制入选
            score[t] = score.get(t, 0.0) + 0.05
    for v in state.get("value_hits", []):
        for c in state.get("column_hits", []):
            if c["column"] == v["column"]:
                score[c["table"]] = score.get(c["table"], 0.0) + 0.02
    if not score:
        return {"tables": []}

    ranked = sorted(score.items(), key=lambda x: -x[1])
    top = ranked[0][1]

    # 多余的事实表是有毒的：fact_promo_cost 与 fact_inventory 是月粒度，
    # 一旦进入上下文就可能被拿去按 date_key 与日粒度的 fact_sales 关联，
    # 得到看似合理实则完全错误的数字。维表多给一两张则无此风险。
    # 因此事实表不按分数取，只认「命中指标声明需要哪几张」——
    # 费效比这类跨事实域指标会声明两张，普通指标只声明一张。
    allowed_facts = {t for m in state.get("metric_hits", [])[:1]
                     for t in m["tables"] if t.startswith("fact_")}
    best_fact = next((t for t, _ in ranked if t.startswith("fact_")), None)
    if best_fact and not allowed_facts:   # 一个指标都没命中时，保底给分数最高的那张
        allowed_facts = {best_fact}

    tables = []
    for t, s in ranked:
        if t.startswith("fact_"):
            if t not in allowed_facts:
                continue
        elif s < top * MIN_TABLE_RATIO:
            continue
        tables.append(t)
        if len(tables) >= TOP_TABLE:
            break
    return {"tables": tables}
