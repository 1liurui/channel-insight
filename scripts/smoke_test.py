"""M0 冒烟测试：五项基础设施逐项验证，并实测向量模型常驻内存。"""

import os
import resource
import sys

from rich.console import Console
from rich.table import Table

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.config import get_settings

console = Console()
S = get_settings()
results: list[tuple[str, bool, str]] = []


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 / 1024


def check(name: str):
    def deco(fn):
        try:
            results.append((name, True, fn()))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn

    return deco


@check("DuckDB 进程内列存")
def _duckdb() -> str:
    import duckdb

    S.duckdb_file.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(S.duckdb_file))
    n = con.sql("select count(*) from range(1000000)").fetchone()[0]
    con.close()
    return f"v{duckdb.__version__}，百万行聚合 {n:,}"


@check("PostgreSQL 应用库")
def _pg() -> str:
    import psycopg

    with psycopg.connect(S.pg_dsn) as con:
        ver = con.execute("select version()").fetchone()[0]
    return ver.split(" on ")[0]


@check("Redis 语义缓存")
def _redis() -> str:
    import redis

    r = redis.from_url(S.redis_url)
    r.set("smoke:ping", "1", ex=10)
    assert r.get("smoke:ping") == b"1"
    return f"v{r.info()['redis_version']}，读写往返正常"


@check("Qdrant 嵌入式（无服务端）")
def _qdrant() -> str:
    from qdrant_client import QdrantClient, models

    S.qdrant_dir.mkdir(parents=True, exist_ok=True)
    cli = QdrantClient(path=str(S.qdrant_dir))
    if cli.collection_exists("smoke"):
        cli.delete_collection("smoke")
    cli.create_collection(
        "smoke", vectors_config=models.VectorParams(size=4, distance=models.Distance.COSINE)
    )
    cli.upsert("smoke", [models.PointStruct(id=1, vector=[0.1, 0.2, 0.3, 0.4], payload={"t": "x"})])
    hit = cli.query_points("smoke", query=[0.1, 0.2, 0.3, 0.4], limit=1).points
    cli.delete_collection("smoke")
    cli.close()
    return f"本地落盘模式可用，检索命中 {len(hit)} 条"


@check("bge-small-zh 向量化")
def _embed() -> str:
    before = rss_mb()
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=S.embed_model)
    vecs = list(model.embed(["本月华东区铺市率是多少", "上季度经销商动销率排名"]))
    dim = len(vecs[0])
    delta = rss_mb() - before
    return f"{dim} 维，加载后 RSS 增量 {delta:.0f} MB（进程峰值 {rss_mb():.0f} MB）"


@check("DeepSeek 主模型")
def _llm() -> str:
    from app.clients.llm import get_llm, is_llm_ready

    if not is_llm_ready():
        raise RuntimeError("未配置 DEEPSEEK_API_KEY，见 .env.example")
    reply = get_llm(max_tokens=32).invoke("只回答两个字：就绪").content
    return f"{S.deepseek_model} 应答：{reply.strip()}"


table = Table(title="M0 环境冒烟测试", title_style="bold")
table.add_column("检查项", style="cyan", no_wrap=True)
table.add_column("")
table.add_column("结果")
for name, ok, msg in results:
    table.add_row(name, "[green]PASS[/]" if ok else "[red]FAIL[/]", msg)
console.print(table)

failed = [n for n, ok, _ in results if not ok]
if failed:
    console.print(f"[red]未通过：{'、'.join(failed)}[/]")
    sys.exit(1)
console.print(f"[green]五项全绿[/]  进程峰值内存 {rss_mb():.0f} MB")
