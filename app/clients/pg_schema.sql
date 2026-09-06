-- 应用状态库。与数仓分离：这里是高频小事务的 OLTP 负载，
-- 放 DuckDB 会与分析查询争抢，且 DuckDB 单写者模型不适合并发写。
-- LangGraph 的 checkpointer 表由 PostgresSaver.setup() 自建，不在此声明。

CREATE TABLE IF NOT EXISTS app_session (
    session_id   UUID PRIMARY KEY,
    user_name    TEXT        NOT NULL,
    title        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 每次问数的完整轨迹。既是线上排障依据，也是评测与失败样本分类的数据源。
CREATE TABLE IF NOT EXISTS query_log (
    id            BIGSERIAL PRIMARY KEY,
    session_id    UUID REFERENCES app_session(session_id) ON DELETE CASCADE,
    turn_index    INT         NOT NULL DEFAULT 0,
    question      TEXT        NOT NULL,
    rewritten     TEXT,                    -- 指代消解后的完整问题
    generated_sql TEXT,
    row_count     INT,
    success       BOOLEAN     NOT NULL DEFAULT FALSE,
    error_type    TEXT,                    -- 召回缺失/口径歧义/语法错误/逻辑错误
    error_detail  TEXT,
    retry_count   SMALLINT    NOT NULL DEFAULT 0,
    cache_hit     BOOLEAN     NOT NULL DEFAULT FALSE,
    latency_ms    INT,
    node_timings  JSONB,                   -- 各节点耗时，定位链路瓶颈
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_query_log_session ON query_log(session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_query_log_created ON query_log(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_query_log_error   ON query_log(error_type)
    WHERE error_type IS NOT NULL;
