-- 深度分销星型数仓：5 维 4 事实
-- 粒度约定：fact_sales 为「日 × 终端 × SKU」，其余事实表粒度见各表注释

-- ========== 维度 ==========

CREATE TABLE IF NOT EXISTS dim_date (
    date_key        INTEGER PRIMARY KEY,   -- YYYYMMDD
    full_date       DATE      NOT NULL,
    year            SMALLINT  NOT NULL,
    quarter         SMALLINT  NOT NULL,
    month           SMALLINT  NOT NULL,
    year_month      VARCHAR   NOT NULL,    -- 2026-08
    week_of_year    SMALLINT  NOT NULL,
    day_of_week     SMALLINT  NOT NULL,    -- 1=周一
    is_weekend      BOOLEAN   NOT NULL,
    is_holiday      BOOLEAN   NOT NULL,
    season          VARCHAR   NOT NULL     -- 春/夏/秋/冬
);

CREATE TABLE IF NOT EXISTS dim_product (
    sku_id          INTEGER PRIMARY KEY,
    sku_code        VARCHAR   NOT NULL,
    sku_name        VARCHAR   NOT NULL,
    brand           VARCHAR   NOT NULL,    -- 品牌
    category        VARCHAR   NOT NULL,    -- 品类：饮用水/碳酸饮料/果汁/茶饮料/功能饮料/乳饮料
    sub_category    VARCHAR   NOT NULL,
    flavor          VARCHAR   NOT NULL,    -- 口味，陈列稽核按此判定
    package_type    VARCHAR   NOT NULL,    -- 瓶装/罐装/利乐包
    spec_ml         SMALLINT  NOT NULL,
    pack_size       SMALLINT  NOT NULL,    -- 每箱支数
    list_price      DECIMAL(10,2) NOT NULL,
    unit_cost       DECIMAL(10,2) NOT NULL,
    launch_date     DATE      NOT NULL,
    is_new_product  BOOLEAN   NOT NULL,    -- 上市 12 个月内
    is_active       BOOLEAN   NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_dealer (
    dealer_id       INTEGER PRIMARY KEY,
    dealer_code     VARCHAR   NOT NULL,
    dealer_name     VARCHAR   NOT NULL,
    region          VARCHAR   NOT NULL,    -- 大区：华东/华南/华北/华中/西南/西北/东北
    province        VARCHAR   NOT NULL,
    city            VARCHAR   NOT NULL,
    dealer_level    VARCHAR   NOT NULL,    -- A/B/C
    sign_date       DATE      NOT NULL,
    credit_limit    DECIMAL(12,2) NOT NULL,
    is_active       BOOLEAN   NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_outlet (
    outlet_id       INTEGER PRIMARY KEY,
    outlet_code     VARCHAR   NOT NULL,
    outlet_name     VARCHAR   NOT NULL,
    dealer_id       INTEGER   NOT NULL REFERENCES dim_dealer(dealer_id),
    channel         VARCHAR   NOT NULL,    -- 渠道：大卖场/连锁超市/便利店/传统食杂/餐饮/特通
    outlet_grade    VARCHAR   NOT NULL,    -- 门店等级 A/B/C/D，决定单店产出量级
    region          VARCHAR   NOT NULL,
    province        VARCHAR   NOT NULL,
    city            VARCHAR   NOT NULL,
    district        VARCHAR   NOT NULL,
    open_date       DATE      NOT NULL,
    is_active       BOOLEAN   NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_rep (
    rep_id          INTEGER PRIMARY KEY,
    rep_code        VARCHAR   NOT NULL,
    rep_name        VARCHAR   NOT NULL,
    region          VARCHAR   NOT NULL,
    city            VARCHAR   NOT NULL,
    team            VARCHAR   NOT NULL,
    hire_date       DATE      NOT NULL,
    is_active       BOOLEAN   NOT NULL
);

-- ========== 事实 ==========

-- 粒度：日 × 终端 × SKU。主表，行数目标 2800 万
CREATE TABLE IF NOT EXISTS fact_sales (
    date_key        INTEGER   NOT NULL,
    outlet_id       INTEGER   NOT NULL,
    sku_id          INTEGER   NOT NULL,
    dealer_id       INTEGER   NOT NULL,
    qty             INTEGER   NOT NULL,    -- 销量（支）
    gross_amount    DECIMAL(12,2) NOT NULL,
    discount_amount DECIMAL(12,2) NOT NULL,
    net_amount      DECIMAL(12,2) NOT NULL,
    is_promo        BOOLEAN   NOT NULL     -- 当日该 SKU 是否处于促销期
);

-- 粒度：日 × 业务代表 × 终端
CREATE TABLE IF NOT EXISTS fact_visit (
    date_key        INTEGER   NOT NULL,
    rep_id          INTEGER   NOT NULL,
    outlet_id       INTEGER   NOT NULL,
    is_planned      BOOLEAN   NOT NULL,    -- 是否在拜访计划内
    is_executed     BOOLEAN   NOT NULL,    -- 是否实际到店
    duration_min    SMALLINT  NOT NULL,
    has_order       BOOLEAN   NOT NULL,    -- 本次拜访是否产生订单
    has_display_fix BOOLEAN   NOT NULL     -- 是否做了陈列整改
);

-- 粒度：月 × 终端 × SKU × 促销类型
CREATE TABLE IF NOT EXISTS fact_promo_cost (
    date_key        INTEGER   NOT NULL,    -- 归属月首日
    outlet_id       INTEGER   NOT NULL,
    sku_id          INTEGER   NOT NULL,
    dealer_id       INTEGER   NOT NULL,
    promo_type      VARCHAR   NOT NULL,    -- 陈列奖励/买赠/特价/堆头/DM
    cost_amount     DECIMAL(12,2) NOT NULL
);

-- 粒度：月末快照 × 经销商 × SKU
CREATE TABLE IF NOT EXISTS fact_inventory (
    date_key        INTEGER   NOT NULL,
    dealer_id       INTEGER   NOT NULL,
    sku_id          INTEGER   NOT NULL,
    qty_on_hand     INTEGER   NOT NULL,
    qty_in_transit  INTEGER   NOT NULL,
    near_expiry_qty INTEGER   NOT NULL,    -- 剩余保质期 < 1/3 的库存量
    avg_shelf_life_days SMALLINT NOT NULL
);

-- ========== 铺货关系（非事实，供铺市率分母使用）==========
-- 粒度：终端 × SKU，记录该终端是否将该 SKU 列入经营范围
CREATE TABLE IF NOT EXISTS rel_outlet_sku (
    outlet_id       INTEGER   NOT NULL,
    sku_id          INTEGER   NOT NULL,
    listed_date     DATE      NOT NULL,
    delisted_date   DATE,
    base_velocity   DECIMAL(8,3) NOT NULL  -- 基准日均动销支数，生成器用
);
