"""PostgreSQL 应用库接入。"""

from __future__ import annotations

import atexit
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg_pool import ConnectionPool

from app.config import get_settings

_SCHEMA = Path(__file__).with_name("pg_schema.sql")
_pool: ConnectionPool | None = None


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(get_settings().pg_dsn, min_size=1, max_size=6, open=True)
        atexit.register(close_pool)
    return _pool


@contextmanager
def conn():
    with get_pool().connection() as c:
        yield c


def init_schema() -> None:
    with conn() as c:
        c.execute(_SCHEMA.read_text())
        c.commit()


def new_session(user_name: str, title: str | None = None) -> uuid.UUID:
    sid = uuid.uuid4()
    with conn() as c:
        c.execute("INSERT INTO app_session (session_id, user_name, title) VALUES (%s,%s,%s)",
                  (sid, user_name, title))
        c.commit()
    return sid


def log_query(**kw) -> int:
    cols = ", ".join(kw)
    vals = ", ".join(["%s"] * len(kw))
    with conn() as c:
        row = c.execute(f"INSERT INTO query_log ({cols}) VALUES ({vals}) RETURNING id",
                        list(kw.values())).fetchone()
        c.commit()
    return row[0]


def healthy() -> bool:
    try:
        with conn() as c:
            c.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False
