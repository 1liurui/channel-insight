"""生成 90 张干扰表，把数仓从 10 张扩到 100 张，用于检索层的 schema 压力测试。

为什么需要它：现有 10 张表的裸 schema 只有 1837 字符（约 612 token），
不到 DeepSeek 上下文的 1%。模型一眼看得完全部表和字段，检索没有信息可增——
这正是混合检索、RRF、schema linking 在消融中全都测不出贡献的原因。

而且这 10 张表是人工挑好的，真实数仓里它们混在几百张表中间。
换言之现有评测等于替检索先把活干了，再说检索没用，对比本身不公平。

干扰表分两类：
  其他业务域（财务、HR、供应链、生产、营销、CRM、电商、系统）—— 语义上远离
  相似命名的陷阱表（fact_sales_archive、dim_outlet_history 等）—— 语义上极近

后者是重点。向量检索对「fact_sales」与「fact_sales_archive」这种高度相似的
命名最弱，而 BM25 靠精确词命中恰好能区分。混合检索的价值若存在，应在这里显现。

干扰表只建空表：gold SQL 不涉及它们，模型若误用会返回空结果而被判错，
这正是要测的失败模式。但表必须真实存在——否则误用只会报「表不存在」，
被 self-correction 轻易修好，实验就失真了。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb
import yaml

from app.config import ROOT, get_settings

# 每个业务域：(前缀, 域名, [(表名, 描述, [(列, 类型, 列描述)])])
KEY = ("INTEGER", "主键")
FK = ("INTEGER", "外键")
AMT = ("DECIMAL", "金额")
DT = ("DATE", "日期")
TS = ("TIMESTAMP", "时间戳")
S = ("VARCHAR", "名称")
N = ("INTEGER", "数量")

DOMAINS: dict[str, tuple[str, list]] = {
    "fin": ("财务", [
        ("fin_gl_entry", "总账分录明细", ["entry_id", "voucher_no", "account_code", "debit_amt", "credit_amt", "post_date", "cost_center"]),
        ("fin_account", "会计科目主数据", ["account_code", "account_name", "account_level", "parent_code", "is_leaf"]),
        ("fin_ar_invoice", "应收发票", ["invoice_id", "customer_code", "invoice_amt", "due_date", "settled_amt", "status"]),
        ("fin_ap_invoice", "应付发票", ["invoice_id", "vendor_code", "invoice_amt", "due_date", "paid_amt", "status"]),
        ("fin_budget", "预算编制表", ["budget_id", "dept_code", "budget_year", "budget_amt", "used_amt", "category"]),
        ("fin_cost_center", "成本中心主数据", ["cost_center", "cc_name", "dept_code", "owner", "is_active"]),
        ("fin_exchange_rate", "汇率表", ["rate_date", "currency_from", "currency_to", "rate"]),
        ("fin_settlement", "结算单", ["settle_id", "dealer_code", "settle_amt", "settle_date", "channel_type"]),
        ("fin_rebate", "返利计提", ["rebate_id", "dealer_code", "period", "rebate_amt", "policy_code", "is_paid"]),
        ("fin_tax_detail", "税务明细", ["tax_id", "invoice_id", "tax_rate", "tax_amt", "tax_type"]),
        ("fin_cash_flow", "现金流水", ["flow_id", "account_code", "flow_amt", "flow_date", "direction"]),
        ("fin_period_close", "期末结账记录", ["period", "closed_by", "close_time", "status"]),
    ]),
    "hr": ("人力资源", [
        ("hr_employee", "员工主数据", ["emp_id", "emp_name", "dept_code", "position", "hire_date", "status"]),
        ("hr_department", "部门主数据", ["dept_code", "dept_name", "parent_dept", "manager_id", "level"]),
        ("hr_attendance", "考勤记录", ["record_id", "emp_id", "work_date", "check_in", "check_out", "status"]),
        ("hr_payroll", "薪资发放", ["payroll_id", "emp_id", "period", "gross_pay", "net_pay", "tax_amt"]),
        ("hr_leave", "请假申请", ["leave_id", "emp_id", "leave_type", "start_date", "end_date", "days"]),
        ("hr_position", "岗位字典", ["position_code", "position_name", "job_family", "grade"]),
        ("hr_training", "培训记录", ["training_id", "emp_id", "course_code", "finish_date", "score"]),
        ("hr_performance", "绩效考核", ["review_id", "emp_id", "period", "rating", "reviewer_id"]),
        ("hr_recruit", "招聘需求", ["req_id", "dept_code", "position_code", "headcount", "open_date", "status"]),
        ("hr_contract", "劳动合同", ["contract_id", "emp_id", "start_date", "end_date", "contract_type"]),
    ]),
    "scm": ("供应链与物流", [
        ("scm_purchase_order", "采购订单", ["po_id", "vendor_code", "order_date", "total_amt", "status"]),
        ("scm_purchase_item", "采购订单行", ["po_id", "line_no", "material_code", "qty", "unit_price"]),
        ("scm_vendor", "供应商主数据", ["vendor_code", "vendor_name", "vendor_type", "region", "is_active"]),
        ("scm_material", "物料主数据", ["material_code", "material_name", "material_group", "unit", "spec"]),
        ("scm_warehouse", "仓库主数据", ["wh_code", "wh_name", "wh_type", "city", "capacity"]),
        ("scm_stock_move", "库存移动", ["move_id", "wh_code", "material_code", "move_qty", "move_date", "move_type"]),
        ("scm_shipment", "发运单", ["shipment_id", "wh_code", "dest_city", "ship_date", "carrier_code", "weight_kg"]),
        ("scm_carrier", "承运商主数据", ["carrier_code", "carrier_name", "transport_mode", "is_active"]),
        ("scm_delivery_note", "送货单", ["dn_id", "shipment_id", "receiver", "sign_date", "status"]),
        ("scm_transport_cost", "运输费用", ["cost_id", "shipment_id", "freight_amt", "fuel_surcharge", "settle_month"]),
        ("scm_safety_stock", "安全库存设置", ["wh_code", "material_code", "min_qty", "max_qty", "lead_days"]),
        ("scm_return_order", "退货单", ["return_id", "wh_code", "material_code", "return_qty", "return_date", "reason"]),
    ]),
    "mfg": ("生产制造", [
        ("mfg_work_order", "生产工单", ["wo_id", "plant_code", "material_code", "plan_qty", "actual_qty", "start_date"]),
        ("mfg_plant", "工厂主数据", ["plant_code", "plant_name", "city", "capacity_per_day"]),
        ("mfg_line", "产线主数据", ["line_code", "plant_code", "line_name", "designed_speed"]),
        ("mfg_bom", "物料清单", ["bom_id", "material_code", "component_code", "component_qty"]),
        ("mfg_quality_check", "质检记录", ["check_id", "wo_id", "check_date", "pass_qty", "defect_qty", "inspector"]),
        ("mfg_defect_type", "缺陷类型字典", ["defect_code", "defect_name", "severity", "category"]),
        ("mfg_downtime", "停机记录", ["downtime_id", "line_code", "start_time", "end_time", "reason_code"]),
        ("mfg_shift", "班次定义", ["shift_code", "shift_name", "start_time", "end_time"]),
        ("mfg_yield_daily", "日产量汇总", ["stat_date", "line_code", "output_qty", "scrap_qty", "yield_rate"]),
        ("mfg_equipment", "设备台账", ["equip_code", "equip_name", "line_code", "install_date", "status"]),
    ]),
    "mkt": ("市场营销", [
        ("mkt_campaign", "营销活动", ["campaign_id", "campaign_name", "start_date", "end_date", "budget_amt", "channel"]),
        ("mkt_ad_spend", "广告投放花费", ["spend_id", "campaign_id", "media_code", "spend_date", "spend_amt", "impressions"]),
        ("mkt_media", "媒体渠道字典", ["media_code", "media_name", "media_type", "settle_mode"]),
        ("mkt_coupon", "优惠券主数据", ["coupon_id", "coupon_name", "discount_type", "face_value", "valid_days"]),
        ("mkt_coupon_usage", "优惠券核销", ["usage_id", "coupon_id", "member_id", "use_time", "order_no"]),
        ("mkt_member", "会员主数据", ["member_id", "member_name", "level", "register_date", "points"]),
        ("mkt_member_point", "会员积分流水", ["flow_id", "member_id", "point_change", "flow_time", "source"]),
        ("mkt_survey", "问卷调研", ["survey_id", "survey_name", "publish_date", "sample_size"]),
        ("mkt_survey_answer", "问卷答案明细", ["answer_id", "survey_id", "member_id", "question_no", "answer_text"]),
        ("mkt_brand_index", "品牌健康度指数", ["stat_month", "brand_name", "awareness", "preference", "nps"]),
    ]),
    "crm": ("客户服务", [
        ("crm_ticket", "客服工单", ["ticket_id", "member_id", "channel", "create_time", "close_time", "status"]),
        ("crm_ticket_category", "工单分类字典", ["category_code", "category_name", "parent_code", "sla_hours"]),
        ("crm_complaint", "投诉记录", ["complaint_id", "ticket_id", "complaint_type", "severity", "handled_by"]),
        ("crm_agent", "客服坐席", ["agent_id", "agent_name", "team_code", "online_status"]),
        ("crm_call_log", "通话记录", ["call_id", "agent_id", "member_id", "call_time", "duration_sec"]),
        ("crm_satisfaction", "满意度评价", ["eval_id", "ticket_id", "score", "eval_time", "comment_text"]),
        ("crm_knowledge", "知识库条目", ["doc_id", "title", "category_code", "update_time", "view_count"]),
        ("crm_sla_stat", "SLA 达成统计", ["stat_date", "team_code", "total_ticket", "in_sla_ticket", "sla_rate"]),
    ]),
    "ec": ("电商", [
        ("ec_order", "电商订单", ["order_no", "member_id", "order_time", "pay_amt", "platform", "status"]),
        ("ec_order_item", "电商订单行", ["order_no", "line_no", "item_code", "qty", "item_price"]),
        ("ec_item", "电商商品", ["item_code", "item_title", "shop_code", "list_price", "is_online"]),
        ("ec_shop", "店铺主数据", ["shop_code", "shop_name", "platform", "open_date"]),
        ("ec_traffic", "店铺流量", ["stat_date", "shop_code", "uv", "pv", "conversion_rate"]),
    ]),
    "sys": ("系统与主数据", [
        ("sys_user", "系统用户", ["user_id", "login_name", "emp_id", "role_code", "last_login"]),
        ("sys_role", "角色定义", ["role_code", "role_name", "scope", "is_active"]),
        ("sys_audit_log", "操作审计日志", ["log_id", "user_id", "action", "target", "action_time"]),
        ("sys_dict", "通用字典", ["dict_code", "dict_type", "dict_label", "sort_no"]),
        ("sys_job_run", "调度任务运行记录", ["run_id", "job_name", "start_time", "end_time", "status"]),
        ("sys_data_quality", "数据质量检查结果", ["check_id", "table_name", "rule_name", "check_date", "fail_count"]),
        ("sys_region", "行政区划字典", ["region_code", "region_name", "parent_code", "level"]),
        ("sys_calendar", "系统日历", ["cal_date", "is_workday", "holiday_name", "week_of_year"]),
    ]),
}

# 陷阱表：与现有 10 张表命名高度相似。向量检索对这类相似命名最弱，
# BM25 靠精确词命中能区分——混合检索的价值若存在，应在这里显现。
TRAPS = [
    ("fact_sales_archive", "销售明细归档表，存放 2023 年及以前的历史数据，当前分析不应使用",
     ["date_key", "outlet_id", "sku_id", "dealer_id", "qty", "gross_amount", "net_amount", "is_promo"]),
    ("fact_sales_daily_snapshot", "销售日快照，按天全量重算，含中间计算列，非事实明细",
     ["snapshot_date", "outlet_id", "sku_id", "qty", "net_amount", "calc_version"]),
    ("fact_sales_2024", "2024 年销售分区表，已并入主表，保留用于回溯核对",
     ["date_key", "outlet_id", "sku_id", "qty", "net_amount"]),
    ("fact_sales_draft", "销售暂存表，ETL 中间态，未经清洗",
     ["date_key", "outlet_id", "sku_id", "qty", "net_amount", "load_batch"]),
    ("fact_sales_adjusted", "销售调整表，仅存放冲销与调账记录",
     ["adjust_id", "date_key", "outlet_id", "adjust_amount", "adjust_reason"]),
    ("dim_outlet_history", "终端维度历史拉链表，含生效与失效时间，查当前状态不应使用",
     ["outlet_id", "outlet_name", "channel", "region", "valid_from", "valid_to", "is_current"]),
    ("dim_outlet_bak", "终端维度备份表，某次迁移前的快照",
     ["outlet_id", "outlet_code", "outlet_name", "channel", "region", "backup_time"]),
    ("dim_product_draft", "商品维度草稿表，新品上市前的暂存数据",
     ["sku_id", "sku_name", "brand", "category", "draft_status"]),
    ("dim_product_v2", "商品维度新版本，尚未切换，字段口径与主表不同",
     ["sku_id", "sku_name", "brand_new", "category_new", "spec_ml", "effective_date"]),
    ("dim_dealer_history", "经销商维度历史拉链表",
     ["dealer_id", "dealer_name", "dealer_level", "valid_from", "valid_to", "is_current"]),
    ("dim_rep_history", "业务代表维度历史拉链表",
     ["rep_id", "rep_name", "region", "valid_from", "valid_to", "is_current"]),
    ("dim_date_fiscal", "财年日历维度，财年起止与自然年不同，销售分析不应使用",
     ["date_key", "fiscal_year", "fiscal_quarter", "fiscal_month", "fiscal_week"]),
    ("rel_outlet_sku_snapshot", "终端商品铺货关系月度快照",
     ["snapshot_month", "outlet_id", "sku_id", "is_listed"]),
    ("fact_visit_plan", "拜访计划表，存放计划而非实际执行的拜访",
     ["plan_id", "date_key", "rep_id", "outlet_id", "plan_type"]),
    ("fact_inventory_daily", "库存日快照，主表为月末快照，两者粒度不同不可混用",
     ["stat_date", "dealer_id", "sku_id", "on_hand_qty", "in_transit_qty"]),
]

TYPE_HINT = [
    (("_id", "_no", "_key", "qty", "count", "_days", "days", "headcount", "uv", "pv",
      "sort_no", "line_no", "level", "score", "points", "duration_sec", "impressions",
      "question_no", "sample_size", "view_count", "week_of_year", "fail_count",
      "capacity", "min_qty", "max_qty", "lead_days", "grade", "severity"), "INTEGER"),
    (("_amt", "_amount", "price", "rate", "value", "weight_kg", "speed", "_pay"), "DECIMAL"),
    (("_date", "_from", "_to", "cal_date"), "DATE"),
    (("_time", "check_in", "check_out"), "TIMESTAMP"),
    (("is_", "has_"), "BOOLEAN"),
]


def col_type(name: str) -> str:
    for keys, t in TYPE_HINT:
        if any(name.endswith(k) or name.startswith(k) for k in keys):
            return t
    return "VARCHAR"


# 数仓分层变体。真实数仓的表数量主要不是被业务实体撑起来的，而是被分层撑起来的：
# 同一个实体在 ODS 有贴源表、DWD 有清洗后的明细、DWS 有按不同粒度的汇总、
# ADS 有面向报表的应用表。这也是「几千张表」的由来。
LAYERS = [
    ("ods_", "ODS 贴源层", "贴源同步表，字段与源系统一致未做清洗", ["src_system", "load_time", "load_batch", "is_deleted"]),
    ("dwd_", "DWD 明细层", "清洗后的明细表，已做标准化与去重", ["etl_time", "data_version"]),
    ("dws_", "DWS 汇总层", "按主题与粒度预聚合的汇总表", ["stat_period", "grain", "agg_value", "record_cnt"]),
    ("ads_", "ADS 应用层", "面向固定报表的应用表，口径随报表定制", ["report_code", "stat_date", "dim_key", "metric_value"]),
]


def layered(base_name: str, domain: str, base_desc: str, cols: list[str], want: int) -> list[dict]:
    """给一个业务实体生成分层变体，直到凑够 want 张。"""
    out = []
    for prefix, layer, layer_desc, extra in LAYERS[:want]:
        out.append({
            "name": prefix + base_name, "type": "distractor",
            "description": f"[{domain}域·{layer}] {base_desc}的{layer_desc}。与渠道销售分析无关。",
            "columns": [{"name": c, "type": col_type(c), "description": c}
                        for c in cols[:5] + extra],
        })
    return out


def build_tables(target: int = 90) -> list[dict]:
    out = []
    for prefix, (domain, tables) in DOMAINS.items():
        for name, desc, cols in tables:
            out.append({
                "name": name, "type": "distractor",
                "description": f"[{domain}域] {desc}。与渠道销售分析无关。",
                "columns": [{"name": c, "type": col_type(c), "description": c} for c in cols],
            })
    for name, desc, cols in TRAPS:
        out.append({
            "name": name, "type": "distractor",
            "description": desc,
            "columns": [{"name": c, "type": col_type(c), "description": c} for c in cols],
        })

    # 不够就按数仓分层展开，一轮一层，保证各业务域均匀铺开而不是堆在某个域
    for depth in range(1, len(LAYERS) + 1):
        for prefix, (domain, tables) in DOMAINS.items():
            for name, desc, cols in tables:
                if len(out) >= target:
                    return out
                out.extend(layered(name, domain, desc, cols, depth)[depth - 1:depth])
    return out


def main() -> None:
    target = int(sys.argv[1]) - 10 if len(sys.argv) > 1 else 90
    tables = build_tables(target)
    conf_dir = get_settings().conf_dir
    (conf_dir / "schema_extra.yaml").write_text(
        "# 干扰表，仅用于 schema 压力测试（WIDE_SCHEMA=1 时并入检索语料）\n"
        "# 由 scripts/build_wide_schema.py 生成，请勿手工编辑\n"
        + yaml.safe_dump({"tables": tables}, allow_unicode=True, sort_keys=False, width=200))

    con = duckdb.connect(str(get_settings().duckdb_file))
    for t in tables:
        cols = ", ".join(f'"{c["name"]}" {c["type"]}' for c in t["columns"])
        con.execute(f'CREATE TABLE IF NOT EXISTS {t["name"]} ({cols})')
    n = con.sql("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='main'").fetchone()[0]
    con.close()

    ncol = sum(len(t["columns"]) for t in tables)
    layers = sum(1 for t in tables if t["name"][:4] in ("ods_", "dwd_", "dws_", "ads_"))
    print(f"生成干扰表 {len(tables)} 张（陷阱表 {len(TRAPS)} 张，分层变体 {layers} 张），{ncol} 字段")
    print(f"数仓现有表总数：{n}")
    print(f"写入 {conf_dir / 'schema_extra.yaml'}")


if __name__ == "__main__":
    main()
