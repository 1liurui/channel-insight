# 业务数据问数与归因智能体

面向品牌商渠道运营场景的数据智能体：业务人员用自然语言完成自助取数、
指标异动溯源与终端陈列稽核，不需要懂 SQL，也不需要记住十几个指标的口径差异。

```
自然语言问题  →  指代消解 → 混合检索 → 口径注入 → SQL 生成 → 安全校验 → 执行
                                                                  ↘ 失败携错重写
指标为何变动  →  多维贡献度分解 → 主因判定 → 递归下钻 → 定量结果转业务结论
```

## 为什么不是又一个 Text2SQL demo

三点区别，都有实测数字支撑：

**一、口径沉淀在 semantic layer，不靠模型推断。** 12 项渠道运营指标的计算式与
易错点写成配置，命中指标后把计算式与 caveat 注入 SQL 生成。消融实验显示
**整条链路的增益全部来自这一层**：移除后从 81.0% 掉到 68.3%（p=0.001），
而 68.3% 已与裸模型基线的 67.3% 持平。

**二、归因是确定性计算，大模型只做转述。** 贡献度按
`C_i = (V_i,t1 − V_i,t0) / (V_t1 − V_t0)` 精确分解，累计超 80% 判主因，
递归下钻三层。**按哪个维度拆解是算出来的**，不是指定的——引擎对 8 个候选维度
各算一遍解释集中度再选。40 组含随机波动的埋因场景定位准确率 **85%**。

**三、评测用配对检验判定显著性。** 单轮评测的题目级翻转可达 9 题，
n=100 时二项标准误就有 4 个百分点，比较均值判不出小于 8 个百分点的差异。
五组配置跑同一批题目，是配对样本，改用配对符号检验后功效大幅提高：
semantic layer p=0.003，而混合检索 7 胜 6 负、self-correction 6 胜 6 负，
**接近完美对称——不是样本量不够，是真的没有差异**。
所以本项目诚实地报告这两个模块无收益，而不是给三个漂亮的百分点。

## 关键指标

| 项 | 实测 |
|---|---|
| 动销明细行数 | 28,539,582（全量构建 18 秒，库文件 0.23 GB） |
| 语义缓存 | 冷 1189ms → 热 5ms，同义改写命中 11/12 |
| 归因定位准确率 | 85.0%（单因 100% / 同维双因 75% / 跨维双因 62%） |
| 端到端延迟 | 中位 1097ms，P95 2514ms |

问数链路的执行准确率、五组消融与 schema 压力测试见
[eval/REPORT.md](eval/REPORT.md)——含每组配置的配对显著性检验。

## 架构

### 系统组成

```mermaid
flowchart LR
  subgraph 客户端
    UI[React 前端]
    MCPC[Claude Desktop / Cursor]
  end
  subgraph 服务
    API[FastAPI · SSE 流式]
    MCPS[MCP Server · 5 个工具]
    G[LangGraph 问数链路]
    ATT[归因引擎]
  end
  subgraph 语义层
    SL[semantic layer<br/>12 项口径 · 10 表 91 字段 · 16 条关联路径]
  end
  subgraph 存储
    DDB[(DuckDB<br/>分析查询 · 2850 万行)]
    PG[(PostgreSQL<br/>会话 · 查询日志 · checkpointer)]
    RD[(Redis<br/>语义缓存)]
    QD[(Qdrant 嵌入式<br/>向量索引)]
    BM[bm25s<br/>进程内稀疏索引]
  end
  UI --> API
  MCPC --> MCPS
  API --> G
  API --> ATT
  MCPS --> G
  MCPS --> ATT
  G --> SL
  ATT --> DDB
  SL --> QD
  SL --> BM
  G --> RD
  G --> DDB
  G --> PG
```

按负载分离存储：DuckDB 列存承分析查询，PostgreSQL 承高频小事务的应用状态，
Redis 承语义缓存。Qdrant 走嵌入式、稀疏检索用进程内 bm25s——
全栈常驻内存约 400 MB，8 GB 无 Docker 环境可跑。

### 问数链路（12 节点）

```mermaid
flowchart TD
  S([问题]) --> RW[指代消解]
  RW --> KW[关键词抽取]
  KW --> RC[字段召回<br/>向量]
  KW --> RM[指标召回<br/>向量+字面命中先验]
  KW --> RV[取值召回<br/>词典精确匹配]
  RC --> FU[融合选表]
  RM --> FU
  RV --> FU
  FU --> FS[补齐指标依赖表]
  FS --> BC[注入口径与关联路径]
  BC --> GS[SQL 生成]
  GS --> GR{护栏<br/>语句类型·白名单·EXPLAIN}
  GR -->|通过| EX[执行]
  GR -->|写操作等硬违规| END1([终止])
  GR -->|表名列名错误| CS[携错重写]
  EX -->|成功| END2([结果])
  EX -->|执行报错| CS
  CS --> GR
```

三路召回真并行（LangGraph fan-out / fan-in）。指标召回在向量结果之上叠一层
**字面命中先验**——指标名和维度取值一样是有限枚举，字面出现即确定，
不需要向量来猜；早期只靠 RRF 时，「净销额占比」里的「占比」会命中
促销费用率的别名「费用占比」，指标间分差只有 3% 就足以翻盘。
纠错后重走护栏，不绕过安全检查；只读连接是引擎层的最后一道物理防线。

### 归因流程

```mermaid
flowchart TD
  Q([指标 · 基期 · 对比期 · 筛选]) --> SIG{总变动 ≥ 2%?}
  SIG -->|否| NO([不归因<br/>分母趋零时贡献度只是噪声放大])
  SIG -->|是| SC[对 8 个候选维度<br/>各算一遍贡献度]
  SC --> RK[按归一化 HHI 排序<br/>选解释力最强的维度]
  RK --> OF{净额/毛额 < 0.25?}
  OF -->|是| WARN[标注存在内部反向抵消]
  OF -->|否| PR
  WARN --> PR[按贡献度降序<br/>取累计超 80% 为主因]
  PR --> DR{贡献度 ≥ 10%<br/>且深度 < 3?}
  DR -->|是| SC2[在该切片内<br/>换维度继续下钻]
  SC2 --> DR
  DR -->|否| OUT[瀑布图数据 + 大模型转述]
```

## 快速开始

需要 macOS/Linux、Python 3.13（uv 管理）、Node 20+、Homebrew。

```bash
# 1. 依赖与服务
uv sync
brew install postgresql@16 redis && brew services start postgresql@16 && brew services start redis
createdb channel_agent

# 2. 配置 API Key
cp .env.example .env      # 填入 DEEPSEEK_API_KEY，视觉侧另需 DASHSCOPE_API_KEY

# 3. 环境自检（六项）
PYTHONPATH=. uv run python scripts/smoke_test.py

# 4. 构建数仓与索引（约 20 秒）
PYTHONPATH=. uv run python scripts/build_warehouse.py --scale 1.20
PYTHONPATH=. uv run python -c "from app.retrieval.indexer import build; print(build())"
PYTHONPATH=. uv run python scripts/validate_warehouse.py     # 14 条基准 SQL 校验

# 5. 起服务
./scripts/dev.sh          # http://localhost:8000
```

命令行直接用：

```bash
PYTHONPATH=. uv run python scripts/ask.py -v "华东区上个月各渠道的净销额"
PYTHONPATH=. uv run python scripts/attribute.py net_sales 2026-02 2026-06 --narrate
```

重跑评测：

```bash
PYTHONPATH=. uv run python scripts/run_eval.py --runs 3        # 问数，5 组配置
PYTHONPATH=. uv run python scripts/run_attribution_eval.py     # 归因，40 组埋因场景
PYTHONPATH=. uv run python scripts/write_report.py             # 生成 eval/REPORT.md
```

MCP 接入 Claude Desktop / Cursor 见 [docs/MCP接入.md](docs/MCP接入.md)。

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 编排 | LangGraph | 图结构天然支持并行召回与条件回环；checkpointer 落 PostgreSQL 使多轮状态跨进程存活 |
| 模型 | DeepSeek-chat / Qwen3-VL | SQL 能力强、中文原生、成本低；视觉侧另配 |
| 分析存储 | DuckDB | OLAP 列存，2850 万行分组聚合 91ms，进程内无服务端 |
| 应用存储 | PostgreSQL | 高频小事务；DuckDB 单写者模型不适合并发写 |
| 缓存 | Redis + Qdrant | 值存储与相似判定分离 |
| 检索 | Qdrant 嵌入式 + bm25s | 语料仅百余条，起 Elasticsearch 服务端占 1GB 内存纯属浪费 |
| 向量化 | fastembed（ONNX） | 省掉 torch 的 1GB+ 常驻，这是 8GB 机器跑起全栈的前提 |
| 服务 | FastAPI + SSE + MCP | 逐节点流式推送；MCP 让外部客户端复用同一套口径与护栏 |
| 前端 | React + TypeScript | 瀑布图为手写内联 SVG，未引图表库 |

## 目录

```
app/
  agent/        LangGraph 状态、图、12 个节点
  attribution/  归因引擎、埋因场景、瀑布图与转述
  retrieval/    语料、索引、混合召回、查询指纹
  warehouse/    星型 DDL、数据生成器、只读执行层
  clients/      LLM、PostgreSQL、Redis 语义缓存、Qdrant 单例
  api/ mcp/     HTTP 服务与 MCP Server
conf/           metrics.yaml（12 项口径）· schema.yaml（表语义与关联路径）
eval/           100 题测试集、评测执行、REPORT.md
frontend/       React + TypeScript
docs/           开发问题记录、MCP 接入说明
scripts/        建仓、建索引、评测、问数、归因、启动
tests/          38 条回归测试
```

## 已知边界

- **归因只支持可加指标**（销额、销量、费用）。比率型指标不满足可加性，
  引擎直接拒绝而不是给出一个看似合理实则无意义的分解。
- **检索在当前规模上无信息增益**。10 张表 91 个字段的裸 schema 仅 1837 字符，
  模型看得见全部信息。保留检索是为可扩展性，不夸大其当前收益。
- **数据为合成**。按深度分销模型参数化生成，14 条基准 SQL 校验业务合理性，
  但不等同真实业务数据。
- **品牌与品类在本数据中一一对应**，导致这两个维度在归因中不可区分。
  已在评测判定中按切片等价处理，但更彻底的做法是让品牌跨品类。
- **陈列稽核（M5）尚未上线**，货架图上传接口返回 501。

开发过程中踩的坑与解法记录在 [docs/开发问题记录.md](docs/开发问题记录.md)，
按类型分八节，每条含现象、根因、解法与可复用的一般结论。
