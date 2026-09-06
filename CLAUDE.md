# 项目约定

业务数据问数与归因智能体。定位、架构与评测结果见 [README.md](README.md)，
里程碑进度见 [任务清单.md](任务清单.md)，踩过的坑见
[docs/开发问题记录.md](docs/开发问题记录.md)。

## 运行

**所有 Python 命令必须带 `PYTHONPATH=.`**——项目未安装为包（`[tool.uv] package = false`），
不带前缀会 `ModuleNotFoundError: No module named 'app'`。

```bash
PYTHONPATH=. uv run python scripts/smoke_test.py   # 环境自检六项
PYTHONPATH=. uv run pytest -q                       # 38 条回归测试
uv run ruff check app/ eval/ scripts/ tests/        # lint，行宽 100
./scripts/dev.sh                                    # 全栈，http://localhost:8000
```

数仓与索引是可再生成产物，不入库。首次 clone 后需依 README 建仓再跑测试，
否则依赖数据的测试会失败。

## 硬约束

**M2 / 8GB 内存 / 无 Docker。** 这条决定了全部选型，改动前务必确认不会破坏：

- 向量化走 **fastembed（ONNX）** 而非 sentence-transformers——不能引入 torch，
  它的 1GB+ 常驻会让全栈跑不动。M5 的 YOLO 训练放 Colab，本地只做推理。
- 稀疏检索走 **进程内 bm25s** 而非 Elasticsearch。语料仅百余条，
  为此起一个 JVM 服务端占 1GB 内存不划算。
- Qdrant 走**嵌入式**，无服务端。代价是对存储目录持排他文件锁——
  **全进程只能有一个客户端**，必须通过 `app.clients.vector.get_client()`
  取用（带双重检查锁）。直接 `QdrantClient(path=...)` 会在并行节点下崩。
- DuckDB 以 **read_only 打开**，且用**单一连接 + 每线程 cursor**
  （见 `app/warehouse/duck.py`）。改成每线程一个连接会在解释器退出时
  触发 recursive_mutex 崩溃。

全栈常驻内存约 400 MB，`scripts/smoke_test.py` 会打印实测值。

## 代码约定

- 注释与文档一律中文。注释解释**为什么**这么写，不复述代码在做什么；
  凡是「踩过坑才这么写」的地方必须写清坑在哪。
- ruff 行宽 100，`target-version = py313`。提交前跑 lint。
- `scripts/*.py` 允许 E402（需先改 sys.path 才能 import app），已在 pyproject 配好。
- 新增业务指标改 `conf/metrics.yaml`，新增表或字段语义改 `conf/schema.yaml`，
  **不要在代码里硬编码口径**。改完需重建索引：
  `PYTHONPATH=. uv run python -c "from app.retrieval.indexer import build; build()"`。

## 容易再犯的坑

- **DuckDB 保留字**：`do`、`cost`、`days` 不能作别名。SQL 生成的 prompt 里
  已给别名规范，手写 SQL（含评测 gold）时也要避开。
- **跨粒度关联**：`fact_sales` 是日粒度，`fact_promo_cost.date_key` 是归属月首日，
  `fact_inventory.date_key` 是当月末日。三者**严禁按 date_key 互相关联**，
  必须各自聚合到 `year_month` 再关联。口径 caveat 里给了可照抄的范式。
- **多轮 history 存改写后的问句**，不存原句。原句可能不含时间与筛选条件，
  几轮后上下文会衰减，结果看不出错但量级已偏。
- **评测中 API 故障不得计入模型失败**。`eval/harness.py` 单组 API 故障率
  超 2% 会中止评测。并发上限 4（DeepSeek 按余额分配并发额度）。
- **归因只接受可加指标**。比率型指标传进去会 KeyError，这是刻意的。

## 简历口径

本项目服务于求职，简历正文与所有数字的口径记录在 [简历部分.md](简历部分.md)。
**任何评测数字变化都要同步更新该文件**——那里的数字是要写进简历、
并在面试中被追问三层的。不确定的数字宁可不写。
