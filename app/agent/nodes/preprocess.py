"""预处理：多轮指代消解与检索词构造。"""

from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.nodes._util import timed
from app.agent.state import AgentState
from app.clients.llm import get_llm
from app.retrieval.fingerprint import extract
from app.retrieval.indexer import tokenize

REWRITE_PROMPT = """你是数据分析助手的问题改写模块。用户在多轮对话中提问，\
后续问句常省略主语、时间或筛选条件。请结合上文，把用户最新的问题改写成一个\
不依赖上文即可独立理解的完整问句。

规则：
1. **必须继承上文已确立的时间范围与筛选条件**，除非最新问题明确改变了它们。
   例：上文在问「上个月华东区的销售额」，最新问题是「那单店产出呢」，
   须改写为「上个月华东区的单店产出是多少」——时间与大区都要带上。
2. 不要凭空添加上文从未出现过的条件，也不要做业务推理。
3. **输出必须是一个独立问句**，不得保留上文原句、不得用问号把两个问题拼接。
   反例：「华东区上个月的净销额是多少？那华南呢？」——这是两句，错。
   正例：「华南区上个月的净销额是多少」——把省略成分补进最新问题本身。
4. 若最新问题本身已完整，原样返回。
5. 只输出改写后的问句，不要任何解释。

上文：
{history}

最新问题：{question}
改写结果："""


@timed("rewrite_question")
def rewrite_question(state: AgentState) -> dict:
    """指代消解放在链路最前端，后续所有节点都不必关心多轮。

    这是架构选择而非实现细节：若把多轮上下文一路透传到 SQL 生成，
    每个节点都要处理省略语义，出错面成倍扩大。

    调用方须把改写后的问句而非原始问句写回 history。原始问句本身可能不含时间与
    筛选条件，几轮之后上下文会逐轮衰减，导致时间范围悄悄丢失且结果看不出错。
    """
    history = state.get("history") or []
    if not history:
        return {"rewritten": state["question"]}
    text = "\n".join(f"{h['role']}：{h['content']}" for h in history[-6:])
    msg = get_llm(max_tokens=200).invoke([
        SystemMessage(content="你是严谨的问题改写模块，只输出改写结果。"),
        HumanMessage(content=REWRITE_PROMPT.format(history=text, question=state["question"])),
    ])
    return {"rewritten": msg.content.strip(), "llm_calls": 1}


@timed("extract_keywords")
def extract_keywords(state: AgentState) -> dict:
    """构造检索词。在原句基础上补入指纹识别出的指标名与维度取值，
    让稀疏检索不受口语措辞影响。"""
    q = state.get("rewritten") or state["question"]
    fp = extract(q)
    terms = tokenize(q) + [v.split("=", 1)[1] for v in fp.values] + fp.metrics
    return {"keywords": list(dict.fromkeys(terms))}
