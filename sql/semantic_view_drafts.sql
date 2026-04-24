-- semantic_inventory_view
-- 统一日库存和 OMS 库存的常见查询入口。
-- 状态：draft
-- 阶段：logical_scaffold
-- 说明：当前只给出草案，主要用于沉淀统一字段契约与未来落库方向，具体字段口径需要结合真实库字段类型和索引继续收敛。
CREATE OR REPLACE VIEW semantic_inventory_view AS
SELECT
    'daily_inventory' AS source_table,
    di.report_date AS biz_date,
    NULL AS biz_month,
    di.factory_code,
    di.ERP_FACTORY,
    di.ERP_LOCATION,
    di.product_ID,
    pa.application,
    pa.common_categories,
    di.GRADE,
    di.TTL_Qty AS inventory_qty,
    di.HOLD_Qty AS hold_qty,
    NULL AS customer,
    NULL AS sbu_desc,
    NULL AS bu_desc
FROM daily_inventory di
LEFT JOIN product_attributes pa
    ON di.product_ID = pa.product_ID

UNION ALL

SELECT
    'oms_inventory' AS source_table,
    NULL AS biz_date,
    oi.report_month AS biz_month,
    NULL AS factory_code,
    oi.ERP_FACTORY,
    oi.ERP_LOCATION,
    oi.product_ID,
    pa.application,
    pa.common_categories,
    oi.GRADE,
    oi.panel_qty AS inventory_qty,
    NULL AS hold_qty,
    oi.CUSTOMER AS customer,
    oi.SBU_DESC AS sbu_desc,
    oi.BU_DESC AS bu_desc
FROM oms_inventory oi
LEFT JOIN product_attributes pa
    ON oi.product_ID = pa.product_ID;


-- semantic_plan_actual_view
-- 统一计划投入、实际投入、实际产出的分析入口。
-- 状态：draft
-- 阶段：logical_scaffold
-- 说明：当前优先统一指标命名、时间字段与输出字段契约，不把测试阶段的口径差异提前固化。
CREATE OR REPLACE VIEW semantic_plan_actual_view AS
SELECT
    'daily_PLAN' AS source_table,
    dp.PLAN_date AS biz_date,
    NULL AS biz_month,
    NULL AS version_code,
    dp.factory_code AS factory,
    dp.product_ID AS stage_product_id,
    NULL AS fg_product_id,
    dp.target_qty AS plan_input_qty,
    NULL AS approved_input_qty,
    NULL AS actual_input_qty,
    NULL AS actual_output_qty,
    NULL AS defect_qty
FROM daily_PLAN dp

UNION ALL

SELECT
    'monthly_plan_approved' AS source_table,
    mpa.PLAN_date AS biz_date,
    mpa.plan_month AS biz_month,
    NULL AS version_code,
    mpa.factory_code AS factory,
    mpa.product_ID AS stage_product_id,
    NULL AS fg_product_id,
    NULL AS plan_input_qty,
    mpa.target_IN_glass_qty AS approved_input_qty,
    NULL AS actual_input_qty,
    NULL AS actual_output_qty,
    NULL AS defect_qty
FROM monthly_plan_approved mpa

UNION ALL

SELECT
    'production_actuals' AS source_table,
    pa2.work_date AS biz_date,
    NULL AS biz_month,
    NULL AS version_code,
    pa2.FACTORY AS factory,
    pa2.product_ID AS stage_product_id,
    NULL AS fg_product_id,
    NULL AS plan_input_qty,
    NULL AS approved_input_qty,
    CASE WHEN pa2.act_type = '投入' THEN pa2.GLS_qty END AS actual_input_qty,
    CASE WHEN pa2.act_type = '产出' THEN pa2.Panel_qty END AS actual_output_qty,
    pa2.defect_qty
FROM production_actuals pa2;


-- semantic_demand_unpivot_view
-- 将横表需求转换为标准月明细，便于统一需求类查询。
-- 状态：draft
-- 阶段：logical_scaffold
-- 说明：当前主要作为需求域统一月明细的草案定义，后续再根据真实字段补完整展开策略。
CREATE OR REPLACE VIEW semantic_demand_unpivot_view AS
SELECT
    'v_demand' AS source_table,
    vd.PM_VERSION,
    vd.FGCODE,
    vd.SBU_DESC AS sbu_desc,
    NULL AS bu_desc,
    vd.CUSTOMER AS customer,
    vd.MONTH AS demand_month,
    vd.REQUIREMENT_QTY AS demand_qty,
    0 AS month_offset
FROM v_demand vd

UNION ALL

SELECT
    'v_demand' AS source_table,
    vd.PM_VERSION,
    vd.FGCODE,
    vd.SBU_DESC AS sbu_desc,
    NULL AS bu_desc,
    vd.CUSTOMER AS customer,
    DATE_ADD(vd.MONTH, INTERVAL 1 MONTH) AS demand_month,
    vd.NEXT_REQUIREMENT AS demand_qty,
    1 AS month_offset
FROM v_demand vd

UNION ALL

SELECT
    'v_demand' AS source_table,
    vd.PM_VERSION,
    vd.FGCODE,
    vd.SBU_DESC AS sbu_desc,
    NULL AS bu_desc,
    vd.CUSTOMER AS customer,
    DATE_ADD(vd.MONTH, INTERVAL 2 MONTH) AS demand_month,
    vd.LAST_REQUIREMENT AS demand_qty,
    2 AS month_offset
FROM v_demand vd

UNION ALL

SELECT
    'p_demand' AS source_table,
    pd.PM_VERSION,
    pd.FGCODE,
    pd.SBU_DESC AS sbu_desc,
    pd.BU_DESC AS bu_desc,
    pd.CUSTOMER AS customer,
    pd.MONTH AS demand_month,
    pd.REQUIREMENT_QTY AS demand_qty,
    0 AS month_offset
FROM p_demand pd

UNION ALL

SELECT
    'p_demand' AS source_table,
    pd.PM_VERSION,
    pd.FGCODE,
    pd.SBU_DESC AS sbu_desc,
    pd.BU_DESC AS bu_desc,
    pd.CUSTOMER AS customer,
    DATE_ADD(pd.MONTH, INTERVAL 1 MONTH) AS demand_month,
    pd.NEXT_REQUIREMENT AS demand_qty,
    1 AS month_offset
FROM p_demand pd

UNION ALL

SELECT
    'p_demand' AS source_table,
    pd.PM_VERSION,
    pd.FGCODE,
    pd.SBU_DESC AS sbu_desc,
    pd.BU_DESC AS bu_desc,
    pd.CUSTOMER AS customer,
    DATE_ADD(pd.MONTH, INTERVAL 2 MONTH) AS demand_month,
    pd.LAST_REQUIREMENT AS demand_qty,
    2 AS month_offset
FROM p_demand pd;


-- semantic_demand_perf_view
-- 统一需求与销售财务表现比较。
-- 状态：draft
-- 阶段：logical_scaffold
-- 说明：当前优先收敛统一输出字段和 join 方向，后续再根据真实数据验证时间与客户口径对齐。
CREATE OR REPLACE VIEW semantic_demand_perf_view AS
SELECT
    d.source_table AS demand_source,
    d.PM_VERSION,
    d.FGCODE,
    d.sbu_desc,
    COALESCE(d.bu_desc, sfp.BU_DESC) AS bu_desc,
    d.customer,
    d.demand_month AS biz_month,
    d.demand_qty,
    sfp.sales_qty,
    sfp.FINANCIAL_qty AS financial_qty
FROM semantic_demand_unpivot_view d
LEFT JOIN sales_financial_perf sfp
    ON d.FGCODE = sfp.FGCODE
   AND d.customer = sfp.CUSTOMER
   AND d.demand_month = sfp.report_month;
