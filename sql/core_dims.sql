-- TV端DAU异动归因 — 核心3维取数SQL
-- 维度：厂商(chid) × 启动方式(initiative_type_new) × 活跃分层(active_type2)
-- 用法：
--   建阈值时: date_start/date_end 覆盖20+完整周（如 20250901 ~ 20260504）
--   每周分析时: date_start/date_end 覆盖4个周（本周/上周/去年同周/去年前一周）

SELECT
  log_date,
  CASE
    WHEN chid_day_first LIKE 'tcl%' THEN 'TCL'
    WHEN chid_day_first LIKE 'xiaomi%' THEN '小米'
    WHEN chid_day_first LIKE 'konka%' THEN '康佳'
    WHEN chid_day_first LIKE 'haixin%' THEN '海信'
    WHEN chid_day_first LIKE 'kukai%' THEN '酷开'
    WHEN chid_day_first IN ('huanshi11', 'changhong') THEN '长虹'
    ELSE '其他'
  END AS chid,
  CASE
    WHEN is_initiative_first = 1 AND resource != '' THEN '类主启'
    WHEN is_initiative_first = 1 AND resource = '' THEN '真主启'
    ELSE '外唤'
  END AS initiative_type_new,
  CASE
    WHEN is_new = '1' THEN '1)当日新增'
    WHEN active_days_30d BETWEEN 1 AND 3 THEN '2)超低活'
    WHEN active_days_30d BETWEEN 4 AND 10 THEN '3)低活'
    WHEN active_days_30d BETWEEN 11 AND 20 THEN '4)中活'
    WHEN active_days_30d BETWEEN 21 AND 26 THEN '5)高活'
    WHEN active_days_30d > 26 THEN '6)极高活'
    ELSE '7)流失回流'
  END AS active_type2,
  COUNT(DISTINCT buvid) AS dau
FROM
  iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
WHERE
  pid = 73
  AND log_date BETWEEN '{date_start}' AND '{date_end}'
GROUP BY
  1, 2, 3, 4
