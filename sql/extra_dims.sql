-- ═══════════════════════════════════════════
-- TV端DAU异动归因 — 补充维度取数SQL
-- 补充维度仅做单维度监控，不叉乘
-- ═══════════════════════════════════════════

-- ═══════════════════════════════════════════
-- 0a. 启动方式（老）
-- ═══════════════════════════════════════════
SELECT
  log_date,
  CASE WHEN is_initiative_first = 1 THEN '主启' ELSE '外唤' END AS is_initiative_first,
  COUNT(DISTINCT buvid) AS dau
FROM
  iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
WHERE
  pid = 73
  AND log_date BETWEEN '{date_start}' AND '{date_end}'
GROUP BY
  1, 2;


-- ═══════════════════════════════════════════
-- 0b. 单端/双栖
-- ═══════════════════════════════════════════
SELECT
  log_date,
  is_dbl_buvid,
  COUNT(DISTINCT buvid) AS dau
FROM
  iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
WHERE
  pid = 73
  AND log_date BETWEEN '{date_start}' AND '{date_end}'
GROUP BY
  1, 2;


-- ═══════════════════════════════════════════
-- 1. 城市等级（直接从主表取，不需要join）
-- ═══════════════════════════════════════════
SELECT
  log_date,
  city_level,
  COUNT(DISTINCT buvid) AS dau
FROM
  iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
WHERE
  pid = 73
  AND log_date BETWEEN '{date_start}' AND '{date_end}'
GROUP BY
  1, 2;


-- ═══════════════════════════════════════════
-- 2. 年龄 + 性别（需要join画像表）
-- 注意: ai.user_profile_ott 取最新分区
-- ═══════════════════════════════════════════
SELECT
  t1.log_date,
  CASE
    WHEN t2.predict_age_range = 0 THEN '17-'
    WHEN t2.predict_age_range = 1 THEN '18-24'
    WHEN t2.predict_age_range = 2 THEN '25-30'
    WHEN t2.predict_age_range = 3 THEN '30+'
    ELSE '其他'
  END AS age,
  CASE
    WHEN t2.predict_sex = 1 THEN '男'
    WHEN t2.predict_sex = 2 THEN '女'
    ELSE '未知'
  END AS sex,
  COUNT(DISTINCT t1.buvid) AS dau
FROM
  iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d t1
  LEFT JOIN (
    SELECT buvid, predict_age_range, predict_sex
    FROM ai.user_profile_ott
    WHERE log_date = (
      SELECT MAX(log_date) FROM ai.user_profile_ott WHERE log_date >= '20260101'
    )
    GROUP BY 1, 2, 3
  ) t2 ON t1.buvid = t2.buvid
WHERE
  t1.pid = 73
  AND t1.log_date BETWEEN '{date_start}' AND '{date_end}'
GROUP BY
  1, 2, 3;


-- ═══════════════════════════════════════════
-- 3. 内容品类（从OGV播放表取）
-- 注意: 该表的dau字段是 sum(vt_rate)，不是 count(distinct buvid)
-- ═══════════════════════════════════════════
SELECT
  log_date,
  ogv_type_name,
  SUM(vt_rate) AS dau
FROM
  bili_bi.tidename_review_dau_daily_hr_ott
WHERE
  log_date BETWEEN '{date_start}' AND '{date_end}'
GROUP BY
  1, 2;
