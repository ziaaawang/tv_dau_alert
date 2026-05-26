"""
TV端 DAU 归因分析 — Streamlit 应用
启动: streamlit run app.py
"""
import os
import sys
import time
import requests
import pandas as pd
import numpy as np
import streamlit as st
from datetime import date, datetime, timedelta
from itertools import combinations
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(__file__))

# ═══════════════════════════════════════════
# 配置（从 config.py 读取 API 信息）
# ═══════════════════════════════════════════
from config import (
    ADHOC_BASE_URL, ADHOC_USERNAME, ADHOC_TOKEN,
    DEFAULT_ENGINE_TYPE, DEFAULT_LIMIT_SIZE,
    POLL_INTERVAL, POLL_TIMEOUT,
    CORE_DIMS, CORE_DIM_NAMES, CORE_DIM_FIELDS,
    EXTRA_DIMS, EXTRA_DIM_NAMES, ALL_DIMS,
    LLM_BASE_URL, LLM_TOKEN, LLM_MODEL,
    FORECAST_WEEKS, FORECAST_MONTHS,
    FORECAST_HISTORY_WEEKS, FORECAST_HISTORY_MONTHS,
    DEFAULT_LT_PARAMS, VENDOR_NAMES,
)
from openai import OpenAI
from calendar_config import align_date, build_align_map, get_25_date_range, _get_holiday_info, _is_transfer_work, _iso_week_align, _get_lunar_label

# ═══════════════════════════════════════════
# 页面配置
# ═══════════════════════════════════════════
st.set_page_config(page_title="TV端DAU归因分析", page_icon="📺", layout="wide")
st.markdown("""
<style>
    .styled-table-wrap table {
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
    }
    .styled-table-wrap th {
        background: #f0f2f6;
        font-weight: 600;
        padding: 8px 12px;
        text-align: center !important;
        border-bottom: 2px solid #ddd;
    }
    .styled-table-wrap td {
        padding: 6px 12px;
        text-align: center !important;
        border-bottom: 1px solid #eee;
    }
    .styled-table-wrap tr:hover td {
        background-color: #f8f9fb;
    }
    /* 展示指标筛选器样式 */
    div[data-testid="stMultiSelect"] label p {
        font-weight: 700 !important;
        font-size: 15px !important;
    }
    /* multiselect 标签可拖拽 */
    div[data-testid="stMultiSelect"] span[data-baseweb="tag"] {
        cursor: grab;
    }
    div[data-testid="stMultiSelect"] span[data-baseweb="tag"]:active {
        cursor: grabbing;
    }
    /* 导出按钮缩小 */
    div[data-testid="stDownloadButton"] button {
        padding: 4px 10px;
        font-size: 13px;
        min-height: 0;
        line-height: 1.4;
    }
    /* 隐藏右上角 Deploy/Settings 菜单 */
    #MainMenu {visibility: hidden;}
    header[data-testid="stHeader"] .stAppDeployButton {display: none;}
    /* Running 状态提示改中文 */
    div[data-testid="stStatusWidget"] {visibility: hidden;}
    /* 顶部模式切换样式 */
    div[data-testid="stHorizontalBlock"]:first-child .stRadio > div {
        gap: 0;
    }
</style>
""", unsafe_allow_html=True)

# ═══════════════════════════════════════════
# 顶部模式切换
# ═══════════════════════════════════════════
app_mode = st.radio("", ["📊 归因分析", "📈 DAU预估"], horizontal=True,
                    label_visibility="collapsed", key="app_mode")

# ═══════════════════════════════════════════
# API 执行层
# ═══════════════════════════════════════════

def _headers():
    return {
        "Adhoc-Username": ADHOC_USERNAME,
        "Adhoc-Token": ADHOC_TOKEN,
        "Content-Type": "application/json",
    }


def execute_sql(sql, status_placeholder=None):
    """提交SQL → 轮询 → 取结果，返回 DataFrame"""
    # 提交
    url = f"{ADHOC_BASE_URL}/api/adhoc/outer/v2/sql/execute"
    resp = requests.post(url, json={
        "sqlCommand": sql,
        "engineType": DEFAULT_ENGINE_TYPE,
        "limitSize": DEFAULT_LIMIT_SIZE,
    }, headers=_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"提交SQL失败: {data.get('msg')}")
    query_id = data["data"]["queryId"]

    if status_placeholder:
        status_placeholder.info(f"SQL已提交，queryId: {query_id}，等待执行...")

    # 轮询
    status_url = f"{ADHOC_BASE_URL}/api/adhoc/outer/v2/sql/status/{query_id}"
    start = time.time()
    while True:
        resp = requests.get(status_url, headers=_headers(), timeout=30)
        status = resp.json().get("data")
        if status == 1:  # 成功
            break
        elif status == 2:  # 失败
            result_url = f"{ADHOC_BASE_URL}/api/adhoc/outer/v2/sql/result/{query_id}"
            err_resp = requests.get(result_url, headers=_headers(), timeout=30)
            raise RuntimeError(f"SQL执行失败: {err_resp.json().get('msg', '未知错误')}")
        if time.time() - start > POLL_TIMEOUT:
            raise TimeoutError(f"SQL执行超时（{POLL_TIMEOUT}s）")
        elapsed = int(time.time() - start)
        if status_placeholder:
            status_placeholder.info(f"SQL执行中... 已等待 {elapsed}s")
        time.sleep(POLL_INTERVAL)

    # 取结果（全量数据，不用 preview 模式）
    result_url = f"{ADHOC_BASE_URL}/api/adhoc/outer/v2/sql/result/{query_id}"
    resp = requests.get(result_url, headers=_headers(),
                        params={"returnDataMode": "all"}, timeout=60)
    resp.raise_for_status()
    rd = resp.json()["data"]
    columns = rd.get("columns", [])
    rows = rd.get("result", [])

    if not columns or not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=columns)
    return df


# ═══════════════════════════════════════════
# LLM 对话层
# ═══════════════════════════════════════════

@st.cache_resource
def get_llm_client():
    return OpenAI(api_key=LLM_TOKEN, base_url=LLM_BASE_URL)


OTT_KNOWLEDGE = """## OTT DAU业务知识

### DAU构成
DAU = 主启(分厂商) + 外唤(分厂商) + 音箱及其他
- 主启 = 当月新增 + 12月内新增用户留存 + 老用户留存
- 外唤占比趋势：23年36% → 24年18% → 25年15%（预算+政策收紧）
- 登录率极低(~25%)，家庭场景一设备多人使用

### 厂商行为模式（异动核心来源）
- **强渠道控制**：DAU受厂商预算和目标强影响，商务合作变动冲击大
- **月末冲量/压量**：厂商在月底调整力度以达月度目标（康佳曾精确做到100%完成率）
- **合同谈判波动**：小米25年4月合同未谈拢，新增周环比-37%，DAU周环比-9%
- **预算年度节奏**：Q4可能消耗预算(海信/TCL外唤涨)或收量(长虹12月目标完成后收量)
- **厂商增长类型(26Q1实测)**：海信=纯主启(93.4%)；小米=主启为主(91.2%)；康佳=外唤拉动(53.9%)；中小厂商=外唤拉动(65.7%)

### 季节性规律
- **暑假(7-8月)**：18-24岁学生群体贡献显著，偏好游戏(射击/SLG)和小剧场；25年7月YoY从23.4%提升到30.3%
- **暑期留存(9月)**：25年暑期留存贡献427万/446万增量(96%)，主启18-24贡献52%
- **寒假/春节**：家庭场景增加，26年寒假+13天带动DAU+20万(+0.9pp)
- **S赛(10-11月)**：电竞商务推广拉动，25年S赛贡献WoW+4.3pp
- **月末效应**：厂商冲量或压量
- **考试周(6月/1月)**：学生活跃度可能受影响

### 历史异动典型模式
1. 厂商预算/策略变动 → 外唤DAU大幅波动（康佳25年8月恢复合作YoY+204%）
2. 合同谈判 → 新增和DAU同步下降（小米25年4月）
3. 月末调量 → 各厂商月末冲/压量
4. 季节性学生群体 → 暑假/寒假低中活18-24用户贡献突出
5. 内容事件 → S赛/阅兵等大事件拉动（25年9.3阅兵DAU YoY+37%）
6. 政策变动 → 广电23年12月禁止launcher推APK，渠道拉量下降

### 超预期因素参考(26Q1)
- 预算增长+15.6%：六大厂商外唤超预估+10.0pp(+53万,+2.4pp)
- 算法贡献：长反+0.32% DAU(日均+7.4万)
- 产研贡献：+0.50% DAU(日均+11.6万)
- 内容：游戏/影视/小剧场贡献75.9%

### 归因分析思路
1. 先看整体YoY%变化方向和幅度
2. 单维度拆解：厂商→启动方式→活跃分层，找最大贡献者
3. 交叉下钻：厂商×启动方式判断增长驱动类型
4. 结合贡献度/占比判断是否超预期
5. 结合时间节点和厂商策略信息解释原因
6. 关注结构性变化(占比变化pp) vs 规模效应"""


def _format_results_for_llm(results):
    """将归因结果格式化为LLM可读的文本摘要"""
    import re
    lines = []

    overall = results['整体'].iloc[0]
    lines.append(f"### 整体")
    lines.append(f"观测期日均DAU {overall['观测期DAU']/1e4:.1f}万, 对比期日均DAU {overall['对比期DAU']/1e4:.1f}万, YoY {overall['YoY%']:+.1f}%")
    lines.append("")

    for level_name, df in results.items():
        if level_name in ('整体', '__extra_separator__') or df is None:
            continue
        dim_count = level_name.count('×') + 1
        if dim_count > 2:
            continue

        dim_names = level_name.split(' × ')
        dim_fields = [ALL_DIMS[d]['field'] for d in dim_names if d in ALL_DIMS]
        if not dim_fields:
            continue

        lines.append(f"### {level_name}")
        top_rows = df.loc[df['贡献pp'].abs().nlargest(min(7, len(df))).index]
        for _, row in top_rows.iterrows():
            label = ' × '.join(re.sub(r'^\d+\)', '', str(row[f])) for f in dim_fields)
            parts = [f"贡献pp={row['贡献pp']:+.1f}"]
            if pd.notna(row.get('DAU占比%')):
                parts.append(f"占比={row['DAU占比%']:.1f}%")
            if pd.notna(row.get('占比变化pp')) and row['占比变化pp'] != 0:
                parts.append(f"占比变化={row['占比变化pp']:+.1f}pp")
            if pd.notna(row.get('YoY%')):
                parts.append(f"YoY={row['YoY%']:+.1f}%")
            obs_dau = row.get('观测期DAU', 0)
            parts.append(f"DAU={obs_dau/1e4:.1f}万")
            lines.append(f"- {label}: {', '.join(parts)}")
        lines.append("")

    return '\n'.join(lines)


def build_system_prompt(results, conclusion_text, obs_label, cmp_label):
    """构建对话 system prompt"""
    data_summary = _format_results_for_llm(results)

    return f"""你是B站TV端（OTT）DAU数据分析专家，具备丰富的OTT行业经验和B站TV端业务知识。
用户正在使用DAU异动归因工具分析数据，你需要基于下方的归因数据和业务知识来回答用户的问题。

## 当前分析概况
- 观测期: {obs_label}
- 对比期: {cmp_label}

## 归因数据摘要
{data_summary}

## 自动生成的结论
{conclusion_text}

{OTT_KNOWLEDGE}

## 回答要求
1. 必须结合上方的实际数据来分析，引用具体数字（如"TCL贡献+2.3pp，DAU占比28%"）
2. 结合业务知识给出可能的原因假设，说明判断依据
3. 如果当前数据无法确认原因，明确指出需要补充哪些数据/维度来验证
4. 给出可执行的调查方向和建议
5. 简洁有力，用要点形式组织回答，避免空泛
6. 如果用户问的内容超出当前数据范围，坦诚告知并建议获取方式"""


def chat_stream(client, user_msg, system_prompt, history):
    """流式调用千问API，yield文本chunk"""
    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_msg})

    response = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0.3,
        stream=True,
        extra_body={"enable_thinking": False},
    )
    for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content


# ═══════════════════════════════════════════
# SQL 生成
# ═══════════════════════════════════════════

def build_core_sql(obs_start, obs_end, cmp_start, cmp_end):
    """生成核心3维SQL — 在SQL内直接算好每个时间段的日均DAU
    返回结果：period × chid × initiative_type_new × active_type2 × avg_dau
    每个period最多 7×3×7=147 行，两个period共 ~294 行，不会被API截断。
    """
    return f"""
SELECT
  period,
  chid,
  initiative_type_new,
  active_type2,
  SUM(daily_dau) / COUNT(DISTINCT log_date) AS dau
FROM (
  SELECT
    log_date,
    CASE
      WHEN log_date BETWEEN '{obs_start}' AND '{obs_end}' THEN 'obs'
      ELSE 'cmp'
    END AS period,
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
    COUNT(DISTINCT buvid) AS daily_dau
  FROM
    iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
  WHERE
    pid = 73
    AND (
      log_date BETWEEN '{obs_start}' AND '{obs_end}'
      OR log_date BETWEEN '{cmp_start}' AND '{cmp_end}'
    )
  GROUP BY
    1, 2, 3, 4, 5
) t
GROUP BY
  1, 2, 3, 4
"""


def build_initiative_old_sql(obs_start, obs_end, cmp_start, cmp_end):
    return f"""
SELECT
  period, is_initiative_first,
  SUM(daily_dau) / COUNT(DISTINCT log_date) AS dau
FROM (
  SELECT log_date,
    CASE WHEN log_date BETWEEN '{obs_start}' AND '{obs_end}' THEN 'obs' ELSE 'cmp' END AS period,
    CASE WHEN is_initiative_first = 1 THEN '主启' ELSE '外唤' END AS is_initiative_first,
    COUNT(DISTINCT buvid) AS daily_dau
  FROM iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
  WHERE pid = 73
    AND (log_date BETWEEN '{obs_start}' AND '{obs_end}' OR log_date BETWEEN '{cmp_start}' AND '{cmp_end}')
  GROUP BY 1, 2, 3
) t
GROUP BY 1, 2
"""


def build_dbl_buvid_sql(obs_start, obs_end, cmp_start, cmp_end):
    return f"""
SELECT
  period, is_dbl_buvid,
  SUM(daily_dau) / COUNT(DISTINCT log_date) AS dau
FROM (
  SELECT log_date,
    CASE WHEN log_date BETWEEN '{obs_start}' AND '{obs_end}' THEN 'obs' ELSE 'cmp' END AS period,
    CASE WHEN is_dbl_buvid = 1 THEN '双栖' ELSE '单端' END AS is_dbl_buvid,
    COUNT(DISTINCT buvid) AS daily_dau
  FROM iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
  WHERE pid = 73
    AND (log_date BETWEEN '{obs_start}' AND '{obs_end}' OR log_date BETWEEN '{cmp_start}' AND '{cmp_end}')
  GROUP BY 1, 2, 3
) t
GROUP BY 1, 2
"""


def build_city_level_sql(obs_start, obs_end, cmp_start, cmp_end):
    return f"""
SELECT
  period, city_level,
  SUM(daily_dau) / COUNT(DISTINCT log_date) AS dau
FROM (
  SELECT log_date,
    CASE WHEN log_date BETWEEN '{obs_start}' AND '{obs_end}' THEN 'obs' ELSE 'cmp' END AS period,
    city_level,
    COUNT(DISTINCT buvid) AS daily_dau
  FROM iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
  WHERE pid = 73
    AND (log_date BETWEEN '{obs_start}' AND '{obs_end}' OR log_date BETWEEN '{cmp_start}' AND '{cmp_end}')
  GROUP BY 1, 2, 3
) t
GROUP BY 1, 2
"""


def build_age_sex_sql(obs_start, obs_end, cmp_start, cmp_end, dims=None):
    if dims is None:
        dims = ['age', 'sex']
    select_cols = []
    group_idx = [1, 2]  # period, log_date
    idx = 3
    if 'age' in dims:
        select_cols.append("""CASE
      WHEN t2.predict_age_range = 0 THEN '17-'
      WHEN t2.predict_age_range = 1 THEN '18-24'
      WHEN t2.predict_age_range = 2 THEN '25-30'
      WHEN t2.predict_age_range = 3 THEN '30+'
      ELSE '其他'
    END AS age""")
        group_idx.append(idx)
        idx += 1
    if 'sex' in dims:
        select_cols.append("""CASE
      WHEN t2.predict_sex = 1 THEN '男'
      WHEN t2.predict_sex = 2 THEN '女'
      ELSE '未知'
    END AS sex""")
        group_idx.append(idx)
        idx += 1

    inner_select = ',\n    '.join(select_cols)
    inner_group = ', '.join(str(i) for i in group_idx)

    outer_fields = ', '.join(d for d in ['age', 'sex'] if d in dims)
    outer_group_idx = list(range(1, len([d for d in dims if d in ('age', 'sex')]) + 2))
    outer_group = ', '.join(str(i) for i in outer_group_idx)

    return f"""
SELECT
  period, {outer_fields},
  SUM(daily_dau) / COUNT(DISTINCT log_date) AS dau
FROM (
  SELECT t1.log_date,
    CASE WHEN t1.log_date BETWEEN '{obs_start}' AND '{obs_end}' THEN 'obs' ELSE 'cmp' END AS period,
    {inner_select},
    COUNT(DISTINCT t1.buvid) AS daily_dau
  FROM iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d t1
  LEFT JOIN (
    SELECT buvid, predict_age_range, predict_sex
    FROM ai.user_profile_ott
    WHERE log_date = (SELECT MAX(log_date) FROM ai.user_profile_ott WHERE log_date >= '20260101')
    GROUP BY 1, 2, 3
  ) t2 ON t1.buvid = t2.buvid
  WHERE t1.pid = 73
    AND (t1.log_date BETWEEN '{obs_start}' AND '{obs_end}' OR t1.log_date BETWEEN '{cmp_start}' AND '{cmp_end}')
  GROUP BY {inner_group}
) t
GROUP BY {outer_group}
"""


def build_content_type_sql(obs_start, obs_end, cmp_start, cmp_end):
    return f"""
SELECT
  period, ogv_type_name,
  SUM(daily_dau) / COUNT(DISTINCT log_date) AS dau
FROM (
  SELECT log_date,
    CASE WHEN log_date BETWEEN '{obs_start}' AND '{obs_end}' THEN 'obs' ELSE 'cmp' END AS period,
    ogv_type_name,
    SUM(vt_rate) AS daily_dau
  FROM bili_bi.tidename_review_dau_daily_hr_ott
  WHERE log_date BETWEEN '{obs_start}' AND '{obs_end}' OR log_date BETWEEN '{cmp_start}' AND '{cmp_end}'
  GROUP BY 1, 2, 3
) t
GROUP BY 1, 2
"""


# 维度→SQL CASE表达式映射（基于主表 iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d）
# 注意：年龄、性别需要JOIN画像表
DIM_SQL_EXPR = {
    '厂商': """CASE
      WHEN chid_day_first LIKE 'tcl%' THEN 'TCL'
      WHEN chid_day_first LIKE 'xiaomi%' THEN '小米'
      WHEN chid_day_first LIKE 'konka%' THEN '康佳'
      WHEN chid_day_first LIKE 'haixin%' THEN '海信'
      WHEN chid_day_first LIKE 'kukai%' THEN '酷开'
      WHEN chid_day_first IN ('huanshi11', 'changhong') THEN '长虹'
      ELSE '其他'
    END AS chid""",
    '启动类型（新）': """CASE
      WHEN is_initiative_first = 1 AND resource != '' THEN '类主启'
      WHEN is_initiative_first = 1 AND resource = '' THEN '真主启'
      ELSE '外唤'
    END AS initiative_type_new""",
    '活跃分层': """CASE
      WHEN is_new = '1' THEN '1)当日新增'
      WHEN active_days_30d BETWEEN 1 AND 3 THEN '2)超低活'
      WHEN active_days_30d BETWEEN 4 AND 10 THEN '3)低活'
      WHEN active_days_30d BETWEEN 11 AND 20 THEN '4)中活'
      WHEN active_days_30d BETWEEN 21 AND 26 THEN '5)高活'
      WHEN active_days_30d > 26 THEN '6)极高活'
      ELSE '7)流失回流'
    END AS active_type2""",
    '启动方式（老）': "CASE WHEN is_initiative_first = 1 THEN '主启' ELSE '外唤' END AS is_initiative_first",
    '单端/双栖': "CASE WHEN is_dbl_buvid = 1 THEN '双栖' ELSE '单端' END AS is_dbl_buvid",
    '城市等级': "city_level",
    '年龄': """CASE
      WHEN t2.predict_age_range = 0 THEN '17-'
      WHEN t2.predict_age_range = 1 THEN '18-24'
      WHEN t2.predict_age_range = 2 THEN '25-30'
      WHEN t2.predict_age_range = 3 THEN '30+'
      ELSE '其他'
    END AS age""",
    '性别': """CASE
      WHEN t2.predict_sex = 1 THEN '男'
      WHEN t2.predict_sex = 2 THEN '女'
      ELSE '未知'
    END AS sex""",
}

# 维度→输出列名（用于外层SELECT和GROUP BY）
DIM_OUTPUT_FIELD = {
    '厂商': 'chid',
    '启动类型（新）': 'initiative_type_new',
    '活跃分层': 'active_type2',
    '启动方式（老）': 'is_initiative_first',
    '单端/双栖': 'is_dbl_buvid',
    '城市等级': 'city_level',
    '年龄': 'age',
    '性别': 'sex',
}


def build_combined_sql(dim_names, obs_start, obs_end, cmp_start, cmp_end):
    """根据需要的维度组合动态生成主表SQL（不含内容品类）。
    年龄/性别需要JOIN画像表，其他维度直接从主表取。
    """
    needs_join = '年龄' in dim_names or '性别' in dim_names
    table_alias = 't1.' if needs_join else ''
    buvid_col = f"{table_alias}buvid"
    log_date_col = f"{table_alias}log_date"

    select_cols = []
    for d in dim_names:
        expr = DIM_SQL_EXPR[d]
        if not needs_join and d not in ('年龄', '性别'):
            # 移除可能的 t1. 前缀（如果存在）
            expr = expr.replace('t1.', '')
        select_cols.append(expr)

    inner_select = ',\n    '.join(select_cols)
    output_fields = [DIM_OUTPUT_FIELD[d] for d in dim_names]
    output_fields_str = ', '.join(output_fields)

    inner_group_idx = ', '.join(str(i) for i in range(1, 3 + len(dim_names)))
    outer_group_idx = ', '.join(str(i) for i in range(1, 2 + len(dim_names)))

    if needs_join:
        from_clause = f"""iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d t1
  LEFT JOIN (
    SELECT buvid, predict_age_range, predict_sex
    FROM ai.user_profile_ott
    WHERE log_date = (SELECT MAX(log_date) FROM ai.user_profile_ott WHERE log_date >= '20260101')
    GROUP BY 1, 2, 3
  ) t2 ON t1.buvid = t2.buvid"""
    else:
        from_clause = "iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d"

    return f"""
SELECT
  period, {output_fields_str},
  SUM(daily_dau) / COUNT(DISTINCT log_date) AS dau
FROM (
  SELECT {log_date_col} AS log_date,
    CASE WHEN {log_date_col} BETWEEN '{obs_start}' AND '{obs_end}' THEN 'obs' ELSE 'cmp' END AS period,
    {inner_select},
    COUNT(DISTINCT {buvid_col}) AS daily_dau
  FROM {from_clause}
  WHERE {table_alias}pid = 73
    AND ({log_date_col} BETWEEN '{obs_start}' AND '{obs_end}' OR {log_date_col} BETWEEN '{cmp_start}' AND '{cmp_end}')
  GROUP BY {inner_group_idx}
) t
GROUP BY {outer_group_idx}
"""


# ═══════════════════════════════════════════
# 归因计算
# ═══════════════════════════════════════════

def aggregate_period(df, period, dim_cols):
    """获取某个period的日均DAU（SQL已算好日均）"""
    sub = df[df['period'] == period]
    if dim_cols:
        return sub.groupby(dim_cols, as_index=False)['dau'].sum()
    else:
        return pd.DataFrame([{'dau': sub['dau'].sum()}])


def compute_attribution(df, obs_start, obs_end, cmp_start, cmp_end):
    """
    计算归因分析
    df: SQL返回结果，含 period='obs'/'cmp', chid, initiative_type_new, active_type2, dau（日均）
    返回: dict {层级名: DataFrame}
    """
    results = OrderedDict()

    # 整体（日均，SQL已聚合）
    obs_total = df[df['period'] == 'obs']['dau'].sum()
    cmp_total = df[df['period'] == 'cmp']['dau'].sum()

    change_pct = (obs_total / cmp_total - 1) * 100 if cmp_total else 0

    results['整体'] = pd.DataFrame([{
        '观测期DAU': obs_total,
        '对比期DAU': cmp_total,
        'YoY%': round(change_pct, 1),
    }])

    # 各层级归因
    cross_levels = []
    # 单维度
    for name in CORE_DIM_NAMES:
        cross_levels.append((name, [name]))
    # 两两叉乘
    for combo in combinations(CORE_DIM_NAMES, 2):
        cross_levels.append((' × '.join(combo), list(combo)))
    # 三维叉乘
    if len(CORE_DIM_NAMES) >= 3:
        cross_levels.append((' × '.join(CORE_DIM_NAMES), list(CORE_DIM_NAMES)))

    for level_name, dim_names in cross_levels:
        dim_fields = [CORE_DIMS[d]['field'] for d in dim_names]

        obs_df = aggregate_period(df, 'obs', dim_fields)
        cmp_df = aggregate_period(df, 'cmp', dim_fields)

        merged = obs_df.merge(cmp_df, on=dim_fields, how='outer', suffixes=('_obs', '_cmp'))
        merged = merged.fillna(0)

        merged['YoY%'] = merged.apply(
            lambda r: round((r['dau_obs'] / r['dau_cmp'] - 1) * 100, 1) if r['dau_cmp'] > 0 else None,
            axis=1
        )
        merged['贡献pp'] = merged.apply(
            lambda r: round((r['dau_obs'] - r['dau_cmp']) / cmp_total * 100, 1) if cmp_total > 0 else 0,
            axis=1
        )

        # 结构-规模分解
        if obs_total > 0 and cmp_total > 0:
            share_obs = merged['dau_obs'] / obs_total
            share_cmp = merged['dau_cmp'] / cmp_total
            merged['占比变化pp'] = ((share_obs - share_cmp) * 100).round(1)
            merged['结构贡献pp'] = ((share_obs - share_cmp) * obs_total / cmp_total * 100).round(1)
            merged['规模贡献pp'] = (share_cmp * (obs_total - cmp_total) / cmp_total * 100).round(1)
        else:
            merged['占比变化pp'] = 0.0
            merged['结构贡献pp'] = 0.0
            merged['规模贡献pp'] = 0.0

        # 增量 & 贡献度 & 占比
        merged['YoY增量'] = merged['dau_obs'] - merged['dau_cmp']
        if len(dim_fields) <= 1:
            overall_pp = round((obs_total / cmp_total - 1) * 100, 1) if cmp_total > 0 else 0
            merged['贡献度%'] = merged['贡献pp'].apply(
                lambda pp: round(pp / overall_pp * 100, 1) if overall_pp != 0 else None
            )
        else:
            parent_fields = dim_fields[:-1]
            parent_pp = merged.groupby(parent_fields)['贡献pp'].transform('sum')
            merged['贡献度%'] = (merged['贡献pp'] / parent_pp * 100).round(1)
            merged.loc[parent_pp == 0, '贡献度%'] = None
        if len(dim_fields) <= 1:
            merged['DAU占比%'] = (merged['dau_obs'] / obs_total * 100).round(1) if obs_total > 0 else 0.0
        else:
            parent_fields = dim_fields[:-1]
            parent_dau = merged.groupby(parent_fields)['dau_obs'].transform('sum')
            merged['DAU占比%'] = (merged['dau_obs'] / parent_dau * 100).round(1)
            merged.loc[parent_dau == 0, 'DAU占比%'] = None
        merged['贡献度/占比'] = merged.apply(
            lambda r: round(r['贡献度%'] / r['DAU占比%'], 2) if pd.notna(r.get('贡献度%')) and r.get('DAU占比%', 0) > 0 else None,
            axis=1
        )

        # 重命名列
        rename_map = {'dau_obs': '观测期DAU', 'dau_cmp': '对比期DAU'}
        merged.rename(columns=rename_map, inplace=True)

        # 排序
        merged = merged.sort_values('贡献pp', ascending=False)

        results[level_name] = merged

    return results


def compute_extra_attribution(df, dim_names, core_cmp_total=None):
    """补充维度归因计算。
    dim_names: 维度名列表，如 ['城市等级'] 或 ['年龄', '性别']，可包含核心+补充维度
    core_cmp_total: 用于计算贡献pp的对比期总DAU。若为None则用自身总DAU。
    """
    results = OrderedDict()
    dim_fields = [ALL_DIMS[d]['field'] for d in dim_names]

    obs_total = df[df['period'] == 'obs']['dau'].sum()
    cmp_total_self = df[df['period'] == 'cmp']['dau'].sum()
    cmp_total = core_cmp_total if core_cmp_total is not None else cmp_total_self

    # 仅生成最完整的层级（用户精确选择的组合）
    fields = dim_fields
    level_name = ' × '.join(dim_names)
    obs_df = aggregate_period(df, 'obs', fields)
    cmp_df = aggregate_period(df, 'cmp', fields)

    merged = obs_df.merge(cmp_df, on=fields, how='outer', suffixes=('_obs', '_cmp'))
    merged = merged.fillna(0)

    merged['YoY%'] = merged.apply(
        lambda r: round((r['dau_obs'] / r['dau_cmp'] - 1) * 100, 1) if r['dau_cmp'] > 0 else None,
        axis=1
    )
    merged['贡献pp'] = merged.apply(
        lambda r: round((r['dau_obs'] - r['dau_cmp']) / cmp_total * 100, 1) if cmp_total > 0 else 0,
        axis=1
    )

    # 结构-规模分解
    if obs_total > 0 and cmp_total > 0:
        share_obs = merged['dau_obs'] / obs_total
        share_cmp = merged['dau_cmp'] / cmp_total
        merged['占比变化pp'] = ((share_obs - share_cmp) * 100).round(1)
        merged['结构贡献pp'] = ((share_obs - share_cmp) * obs_total / cmp_total * 100).round(1)
        merged['规模贡献pp'] = (share_cmp * (obs_total - cmp_total) / cmp_total * 100).round(1)
    else:
        merged['占比变化pp'] = 0.0
        merged['结构贡献pp'] = 0.0
        merged['规模贡献pp'] = 0.0

    # 增量 & 贡献度 & 占比
    merged['YoY增量'] = merged['dau_obs'] - merged['dau_cmp']
    if len(fields) <= 1:
        overall_pp = round((obs_total / cmp_total - 1) * 100, 1) if cmp_total > 0 else 0
        merged['贡献度%'] = merged['贡献pp'].apply(
            lambda pp: round(pp / overall_pp * 100, 1) if overall_pp != 0 else None
        )
    else:
        parent_fields = fields[:-1]
        parent_pp = merged.groupby(parent_fields)['贡献pp'].transform('sum')
        merged['贡献度%'] = (merged['贡献pp'] / parent_pp * 100).round(1)
        merged.loc[parent_pp == 0, '贡献度%'] = None
    if len(fields) <= 1:
        merged['DAU占比%'] = (merged['dau_obs'] / obs_total * 100).round(1) if obs_total > 0 else 0.0
    else:
        parent_fields = fields[:-1]
        parent_dau = merged.groupby(parent_fields)['dau_obs'].transform('sum')
        merged['DAU占比%'] = (merged['dau_obs'] / parent_dau * 100).round(1)
        merged.loc[parent_dau == 0, 'DAU占比%'] = None
    merged['贡献度/占比'] = merged.apply(
        lambda r: round(r['贡献度%'] / r['DAU占比%'], 2) if pd.notna(r.get('贡献度%')) and r.get('DAU占比%', 0) > 0 else None,
        axis=1
    )

    merged.rename(columns={'dau_obs': '观测期DAU', 'dau_cmp': '对比期DAU'}, inplace=True)
    merged = merged.sort_values('贡献pp', ascending=False)
    results[level_name] = merged

    return results


# ═══════════════════════════════════════════
# 智能归因分析
# ═══════════════════════════════════════════

def generate_insights(results, obs_start_s, obs_end_s, cmp_start_s, cmp_end_s):
    """基于归因数据和OTT业务知识，自动生成异动分析洞察"""
    import re

    analysis = []
    suggestions = []

    overall = results['整体'].iloc[0]
    yoy_pct = overall['YoY%']
    obs_total = overall['观测期DAU']
    cmp_total = overall['对比期DAU']
    direction = "增长" if yoy_pct > 0 else "下降"

    vendor_field = CORE_DIMS['厂商']['field']
    launch_field = CORE_DIMS['启动类型（新）']['field']
    active_field = CORE_DIMS['活跃分层']['field']

    # ── 1. 厂商维度分析 ──
    if '厂商' in results:
        vdf = results['厂商']
        outsized = []
        underperform = []
        big_share_shift = []

        for _, row in vdf.iterrows():
            name = row[vendor_field]
            ratio = row.get('贡献度/占比')
            share_pct = row.get('DAU占比%', 0)
            contrib_pct = row.get('贡献度%')
            share_change = row.get('占比变化pp', 0)
            pp = row['贡献pp']

            if pd.notna(ratio) and pd.notna(contrib_pct) and share_pct > 3:
                if ratio > 1.5:
                    outsized.append((name, contrib_pct, share_pct, pp))
                elif ratio < 0.5 and yoy_pct != 0:
                    underperform.append((name, contrib_pct, share_pct, pp))

            if abs(share_change) > 0.5 and share_pct > 3:
                dir_text = "+" if share_change > 0 else ""
                big_share_shift.append(f"{name}({dir_text}{share_change:.1f}pp)")

        if outsized:
            parts = [f"{n}(贡献度{c:.0f}% vs 占比{s:.0f}%, {p:+.1f}pp)" for n, c, s, p in outsized]
            analysis.append(f"超预期{direction}厂商：{'、'.join(parts)}")
            suggestions.append("确认上述超预期厂商的商务合作/预算/预装策略是否有变动")

        if underperform:
            parts = [f"{n}(贡献度{c:.0f}% vs 占比{s:.0f}%)" for n, c, s, p in underperform]
            analysis.append(f"{direction}乏力厂商：{'、'.join(parts)}")

        if big_share_shift:
            analysis.append(f"厂商份额变化：{'、'.join(big_share_shift)}")

    # ── 2. 启动方式分析 ──
    if '启动类型（新）' in results:
        ldf = results['启动类型（新）']
        for _, row in ldf.iterrows():
            name = row[launch_field]
            share_change = row.get('占比变化pp', 0)
            struct_pp = row.get('结构贡献pp', 0)

            if name == '外唤' and abs(share_change) > 0.3:
                if share_change > 0:
                    analysis.append(f"外唤占比扩张{share_change:+.1f}pp（结构贡献{struct_pp:+.1f}pp），可能与厂商拉活预算增加有关")
                else:
                    analysis.append(f"外唤占比收缩{share_change:+.1f}pp（结构贡献{struct_pp:+.1f}pp），可能与厂商预算缩减或渠道策略调整有关")
                suggestions.append("确认各厂商的外唤投放策略和预算执行情况")

            if '主启' in name and abs(struct_pp) > 0.5:
                verb = "增长" if struct_pp > 0 else "下降"
                analysis.append(f"{name}结构性{verb}（结构贡献{struct_pp:+.1f}pp），可能反映产品/算法迭代效果")

    # ── 3. 活跃分层结构变化 ──
    if '活跃分层' in results:
        adf = results['活跃分层']
        tier_shifts = []

        for _, row in adf.iterrows():
            tier = row[active_field]
            clean_tier = re.sub(r'^\d+\)', '', str(tier))
            share_change = row.get('占比变化pp', 0)
            pp = row['贡献pp']
            struct_pp = row.get('结构贡献pp', 0)

            if abs(share_change) > 0.3:
                dir_icon = "+" if share_change > 0 else ""
                tier_shifts.append(f"{clean_tier}({dir_icon}{share_change:.1f}pp)")

            if '新增' in clean_tier and abs(pp) > 0.3:
                verb = "增长" if pp > 0 else "下降"
                analysis.append(f"新增用户{verb}（贡献{pp:+.1f}pp），可能与厂商获客策略变动有关")
                suggestions.append("关注各厂商的新增获客渠道和投放力度")

            if '流失回流' in clean_tier and abs(struct_pp) > 0.3:
                verb = "增多" if struct_pp > 0 else "减少"
                analysis.append(f"流失回流用户{verb}（结构贡献{struct_pp:+.1f}pp）")

        if tier_shifts:
            analysis.append(f"用户结构变化：{'、'.join(tier_shifts)}")

    # ── 4. 交叉维度：厂商×启动方式 ──
    cross_key = '厂商 × 启动类型（新）'
    if cross_key in results and '厂商' in results:
        cdf = results[cross_key]
        vdf = results['厂商']
        top_idx = vdf['贡献pp'].abs().nlargest(3).index
        top_vendors = vdf.loc[top_idx, vendor_field].tolist()

        vendor_drivers = []
        for vendor in top_vendors:
            vendor_cross = cdf[cdf[vendor_field] == vendor].copy()
            if vendor_cross.empty:
                continue
            total_pp = vendor_cross['贡献pp'].sum()
            if abs(total_pp) < 0.1:
                continue

            top_row = vendor_cross.loc[vendor_cross['贡献pp'].abs().idxmax()]
            launch_type = top_row[launch_field]
            type_pp = top_row['贡献pp']
            pct = abs(type_pp / total_pp * 100) if total_pp != 0 else 0

            if pct > 60:
                vendor_drivers.append(f"{vendor}→{launch_type}({type_pp:+.1f}pp, 占{pct:.0f}%)")

        if vendor_drivers:
            analysis.append(f"厂商增长驱动：{'；'.join(vendor_drivers)}")

    # ── 5. 季节性/事件提示 ──
    try:
        obs_s = datetime.strptime(obs_start_s, '%Y%m%d')
        obs_e = datetime.strptime(obs_end_s, '%Y%m%d')
        cmp_s = datetime.strptime(cmp_start_s, '%Y%m%d')
        month = obs_s.month

        event_hints = []

        if month in (7, 8):
            event_hints.append("暑假期间：18-24岁学生群体贡献通常显著，偏好游戏和小剧场内容")
        if month == 1 or (month == 2 and obs_s.day < 25):
            event_hints.append("寒假/春节期间：家庭场景使用增加")
        if month == 9:
            event_hints.append("开学季：暑期留存用户贡献值得关注")
        if month in (10, 11):
            event_hints.append("S赛期间：电竞商务推广可能拉动DAU")
        if obs_e.day >= 25:
            event_hints.append("月末时段：厂商可能为达成月度目标冲量或压量")
        if cmp_s.month == 12 and cmp_s.year == 2023:
            event_hints.append("对比期(23年12月)基数较低：广电政策限制+预算缩减")
        if cmp_s.month in (7, 8):
            event_hints.append("对比期处于暑假，同比基数较高")

        if event_hints:
            for hint in event_hints:
                analysis.append(f"[时间] {hint}")
    except ValueError:
        pass

    # ── 6. 建议 ──
    if abs(yoy_pct) > 5 and not suggestions:
        suggestions.append("确认主要厂商的商务合作和预算执行情况")

    if '活跃分层' in results:
        adf = results['活跃分层']
        for _, row in adf.iterrows():
            tier = row[active_field]
            clean_tier = re.sub(r'^\d+\)', '', str(tier))
            if clean_tier in ('低活', '中活', '超低活') and abs(row['贡献pp']) > 1:
                suggestions.append(f"{clean_tier}用户贡献显著，建议补充年龄维度查看是否与学生群体相关")
                break

    if '厂商' in results:
        vdf = results['厂商']
        top_vendor = vdf.loc[vdf['贡献pp'].abs().idxmax(), vendor_field]
        suggestions.append(f"可补充{top_vendor}的厂商×启动方式×活跃分层交叉归因，进一步定位变化来源")

    return analysis, suggestions


# ═══════════════════════════════════════════
# 展示
# ═══════════════════════════════════════════

def style_contrib_bar(df, col='贡献pp'):
    """给贡献pp列添加数据条 + 格式化数值列"""
    fmt = {}
    for c in df.columns:
        if c == 'YoY增量(万)':
            fmt[c] = '{:+.1f}'
        elif 'DAU(万)' in c:
            fmt[c] = '{:.0f}'
        elif c in ('YoY%', '贡献pp', '占比变化pp', '结构贡献pp', '规模贡献pp'):
            fmt[c] = '{:+.1f}'
        elif c in ('贡献度%', 'DAU占比%'):
            fmt[c] = '{:.1f}'
        elif c == '贡献度/占比':
            fmt[c] = '{:.2f}'

    if col is None or col not in df.columns:
        return df.style.format(fmt, na_rep='-').hide(axis='index')

    abs_max = df[col].abs().max() if not df[col].isna().all() else 1
    if abs_max == 0:
        abs_max = 1

    def bar_css(val):
        if pd.isna(val) or val == 0:
            return ''
        half_pct = min(abs(val) / abs_max * 50, 50)
        if val > 0:
            return (
                f'background: linear-gradient(to right, transparent 50%, rgba(220,38,38,0.3) 50%, rgba(220,38,38,0.3) {50 + half_pct:.0f}%, transparent {50 + half_pct:.0f}%);'
                f'color: #dc2626; font-weight: 600'
            )
        else:
            left = 50 - half_pct
            return (
                f'background: linear-gradient(to right, transparent {left:.0f}%, rgba(22,163,74,0.3) {left:.0f}%, rgba(22,163,74,0.3) 50%, transparent 50%);'
                f'color: #16a34a; font-weight: 600'
            )

    styled = df.style.map(bar_css, subset=[col]).format(fmt, na_rep='-').hide(axis='index')
    return styled


def render_styled_df(styled):
    """将 pandas Styler 渲染为 HTML 并展示"""
    html = styled.to_html()
    st.markdown(f'<div class="styled-table-wrap">{html}</div>', unsafe_allow_html=True)


def render_merged_df(df, dim_cols, bar_col='贡献pp'):
    """渲染带有维度列合并单元格(rowspan)的HTML表格"""
    def _fmt_dau(v):
        return f'{v:.0f}' if pd.notna(v) else '-'

    def _fmt_signed(v):
        return f'{v:+.1f}' if pd.notna(v) else '-'

    def _fmt_pct(v):
        return f'{v:.1f}' if pd.notna(v) else '-'

    def _fmt_ratio(v):
        return f'{v:.2f}' if pd.notna(v) else '-'

    fmt_funcs = {}
    for c in df.columns:
        if c == 'YoY增量(万)':
            fmt_funcs[c] = _fmt_signed
        elif 'DAU(万)' in c:
            fmt_funcs[c] = _fmt_dau
        elif c in ('YoY%', '贡献pp', '占比变化pp', '结构贡献pp', '规模贡献pp'):
            fmt_funcs[c] = _fmt_signed
        elif c in ('贡献度%', 'DAU占比%'):
            fmt_funcs[c] = _fmt_pct
        elif c == '贡献度/占比':
            fmt_funcs[c] = _fmt_ratio

    abs_max = 1
    if bar_col and bar_col in df.columns:
        abs_max = df[bar_col].abs().max() if not df[bar_col].isna().all() else 1
        if abs_max == 0:
            abs_max = 1

    def _bar_style(val):
        if pd.isna(val) or val == 0:
            return ''
        half_pct = min(abs(val) / abs_max * 50, 50)
        if val > 0:
            return (
                f'background: linear-gradient(to right, transparent 50%, rgba(220,38,38,0.3) 50%, rgba(220,38,38,0.3) {50 + half_pct:.0f}%, transparent {50 + half_pct:.0f}%);'
                f'color: #dc2626; font-weight: 600'
            )
        else:
            left = 50 - half_pct
            return (
                f'background: linear-gradient(to right, transparent {left:.0f}%, rgba(22,163,74,0.3) {left:.0f}%, rgba(22,163,74,0.3) 50%, transparent 50%);'
                f'color: #16a34a; font-weight: 600'
            )

    # 计算每个维度列的 rowspan（层级式合并）
    n = len(df)
    rowspans = {c: [1] * n for c in dim_cols}

    for col_idx, col in enumerate(dim_cols):
        # 确定每行所属的父级分组边界
        if col_idx == 0:
            # 第一列：整个表为一个组
            group_bounds = [(0, n)]
        else:
            # 后续列：以上一列的合并块为分组
            prev_col = dim_cols[col_idx - 1]
            group_bounds = []
            i = 0
            while i < n:
                span = rowspans[prev_col][i]
                group_bounds.append((i, i + span))
                i += span

        # 在每个组内合并连续相同值
        for start, end in group_bounds:
            i = start
            while i < end:
                j = i + 1
                while j < end and df.iloc[j][col] == df.iloc[i][col]:
                    j += 1
                rowspans[col][i] = j - i
                for k in range(i + 1, j):
                    rowspans[col][k] = 0  # 被合并
                i = j

    # 生成 HTML
    header = '<tr>' + ''.join(f'<th>{c}</th>' for c in df.columns) + '</tr>'
    rows_html = []
    for i in range(n):
        cells = []
        for c in df.columns:
            if c in dim_cols:
                span = rowspans[c][i]
                if span == 0:
                    continue  # 被合并
                val = str(df.iloc[i][c])
                if span > 1:
                    cells.append(f'<td rowspan="{span}" style="vertical-align:middle; font-weight:500;">{val}</td>')
                else:
                    cells.append(f'<td>{val}</td>')
            else:
                raw = df.iloc[i][c]
                fmt_fn = fmt_funcs.get(c)
                text = fmt_fn(raw) if fmt_fn else (str(raw) if pd.notna(raw) else '-')
                style = ''
                if c == bar_col:
                    style = _bar_style(raw)
                if style:
                    cells.append(f'<td style="{style}">{text}</td>')
                else:
                    cells.append(f'<td>{text}</td>')
        rows_html.append('<tr>' + ''.join(cells) + '</tr>')

    html = f'<table>{header}{"".join(rows_html)}</table>'
    st.markdown(f'<div class="styled-table-wrap">{html}</div>', unsafe_allow_html=True)


def _render_card(level_name, df, obs_col_name, cmp_col_name, metric_cols, default_metric_cols, is_extra):
    """渲染单个维度归因卡片"""
    dim_count = level_name.count('×') + 1
    icon = "📌" if dim_count == 1 else "🔗" if dim_count == 2 else "🧩"

    dim_names = level_name.split(' × ')
    dim_fields = [ALL_DIMS[d]['field'] for d in dim_names if d in ALL_DIMS]
    field_to_name = {ALL_DIMS[d]['field']: d for d in dim_names if d in ALL_DIMS}

    display_df = df.copy()
    display_df.rename(columns=field_to_name, inplace=True)
    dim_display_names = [field_to_name[f] for f in dim_fields]

    display_df[obs_col_name] = (display_df['观测期DAU'] / 10000).round(1)
    display_df[cmp_col_name] = (display_df['对比期DAU'] / 10000).round(1)
    display_df['YoY增量(万)'] = (display_df['YoY增量'] / 10000).round(1)

    all_show_cols = dim_display_names + metric_cols
    export_df = display_df.sort_values(dim_display_names)[all_show_cols]
    csv_data = export_df.reset_index(drop=True).to_csv(index=False).encode('utf-8-sig')

    header_col, btn_col = st.columns([8, 1])
    with header_col:
        st.subheader(f"{icon} {level_name}")
    with btn_col:
        st.download_button("📥", data=csv_data, file_name=f"{level_name}.csv",
                           mime="text/csv", key=f"download_{level_name}")

    selected_metrics = st.multiselect(
        "**展示指标**", metric_cols, default=default_metric_cols,
        key=f"metrics_{level_name}")
    if not selected_metrics:
        selected_metrics = default_metric_cols

    show_cols = dim_display_names + selected_metrics
    show_df = display_df[show_cols].copy()
    bar_col = '贡献pp' if '贡献pp' in selected_metrics else None

    use_tabs = is_extra or (dim_count >= 2 and len(show_df) > 20)

    if use_tabs:
        drag = display_df.nsmallest(10, '贡献pp')[show_cols]
        pull = display_df.nlargest(10, '贡献pp')[show_cols]
        all_df = display_df.sort_values(dim_display_names)[show_cols]
        t1, t2, t3 = st.tabs(["拉动Top10", "拖累Top10", "全部数据"])
        with t1:
            render_styled_df(style_contrib_bar(pull.reset_index(drop=True), col=bar_col))
        with t2:
            render_styled_df(style_contrib_bar(drag.reset_index(drop=True), col=bar_col))
        with t3:
            if dim_count >= 2:
                render_merged_df(all_df.reset_index(drop=True), dim_display_names, bar_col=bar_col)
            else:
                render_styled_df(style_contrib_bar(all_df.reset_index(drop=True), col=bar_col))
    else:
        render_styled_df(style_contrib_bar(show_df.reset_index(drop=True), col=bar_col))

    if not is_extra and dim_count == 1 and not display_df.empty:
        top_drag = display_df.nsmallest(3, '贡献pp')
        top_pull = display_df.nlargest(3, '贡献pp')
        drag_text = " / ".join(f"{row[dim_display_names[0]]} {row['贡献pp']:+.1f}pp" for _, row in top_drag.iterrows())
        pull_text = " / ".join(f"{row[dim_display_names[0]]} {row['贡献pp']:+.1f}pp" for _, row in top_pull.iterrows())
        st.markdown(f"**拉动Top3**: {pull_text}")
        st.markdown(f"**拖累Top3**: {drag_text}")


def _render_overview(results, obs_label, cmp_label, date_strs):
    """核心结论Tab"""
    import re
    overall = results['整体'].iloc[0]
    obs_dau, cmp_dau, change = overall['观测期DAU'], overall['对比期DAU'], overall['YoY%']

    col1, col2, col3 = st.columns(3)
    col1.metric("观测期 日均DAU", f"{obs_dau/10000:.0f}万")
    col2.metric("对比期 日均DAU", f"{cmp_dau/10000:.0f}万")
    col3.metric("YoY%", f"{change:+.1f}%", delta=f"{change:+.1f}%", delta_color="inverse")

    st.markdown(f"**观测期**: {obs_label}　｜　**对比期**: {cmp_label}")

    diff_dau = obs_dau - cmp_dau
    diff_sign = "+" if diff_dau >= 0 else ""
    conclusion = (
        f"{obs_label} 日均DAU {obs_dau/10000:.0f}万，{cmp_label} 日均DAU {cmp_dau/10000:.0f}万，"
        f"YoY {change:+.1f}%（YoY增量{diff_sign}{diff_dau/10000:.1f}万）。\n\n"
    )

    def _clean(val):
        return re.sub(r'^\d+\)', '', str(val))

    def _top_text(level_df, dim_fields, n):
        top = level_df.nlargest(n, '贡献pp')
        return '、'.join(f"{'×'.join(_clean(row[f]) for f in dim_fields)}（{row['贡献pp']:+.1f}pp）" for _, row in top.iterrows())

    contrib_lines = []
    top_n_map = {CORE_DIM_NAMES[0]: 3, CORE_DIM_NAMES[1]: 1, CORE_DIM_NAMES[2]: 3}
    for k, v in results.items():
        if k in ('整体', '__extra_separator__') or v is None:
            continue
        dims = k.split(' × ')
        if len(dims) > 1 or not all(d in ALL_DIMS for d in dims):
            continue
        fields = [ALL_DIMS[d]['field'] for d in dims]
        contrib_lines.append(f"  • {k}：{_top_text(v, fields, top_n_map.get(k, 3))}")

    conclusion += "主要贡献维度：\n" + '\n'.join(contrib_lines)

    analysis_lines, suggestion_lines = [], []
    if date_strs:
        try:
            analysis_lines, suggestion_lines = generate_insights(
                results, date_strs[0], date_strs[1], date_strs[2], date_strs[3])
            if analysis_lines:
                conclusion += "\n\n异动分析：\n" + '\n'.join(f"  • {l}" for l in analysis_lines)
            if suggestion_lines:
                conclusion += "\n\n建议关注：\n" + '\n'.join(f"  • {l}" for l in suggestion_lines)
        except Exception:
            pass

    st.session_state['conclusion_text'] = conclusion

    total = len(contrib_lines) + len(analysis_lines) + len(suggestion_lines)
    st.text_area("可编辑结论（支持复制）", value=conclusion,
                 height=min(max(300, total * 28 + 120), 700), key="conclusion_editor")


def _render_cards_section(card_items, obs_col_name, cmp_col_name, metric_cols, default_metric_cols, is_extra):
    """渲染一组维度归因卡片"""
    if not card_items:
        st.info("无对应维度数据")
        return
    for level_name, df in card_items.items():
        st.markdown("---")
        _render_card(level_name, df, obs_col_name, cmp_col_name, metric_cols, default_metric_cols, is_extra)


def _render_chat(results, obs_label, cmp_label):
    """异动探讨对话Tab"""
    st.caption("基于当前归因数据和OTT业务知识，继续追问异动原因、探讨业务假设")

    if 'chat_history' not in st.session_state:
        st.session_state['chat_history'] = []

    for msg in st.session_state['chat_history']:
        with st.chat_message(msg['role'], avatar="🧑‍💻" if msg['role'] == 'user' else "📊"):
            st.markdown(msg['content'])

    if prompt := st.chat_input("输入问题，如：TCL增长的原因可能是什么？"):
        st.session_state['chat_history'].append({"role": "user", "content": prompt})
        with st.chat_message("user", avatar="🧑‍💻"):
            st.markdown(prompt)

        conclusion = st.session_state.get('conclusion_text', '')
        sys_prompt = build_system_prompt(results, conclusion, obs_label, cmp_label)
        chat_hist = [{"role": m["role"], "content": m["content"]} for m in st.session_state['chat_history'][:-1]]

        with st.chat_message("assistant", avatar="📊"):
            try:
                client = get_llm_client()
                response_text = st.write_stream(chat_stream(client, prompt, sys_prompt, chat_hist))
                st.session_state['chat_history'].append({"role": "assistant", "content": response_text})
            except Exception as e:
                error_msg = f"对话服务暂时不可用: {e}"
                st.error(error_msg)
                st.session_state['chat_history'].append({"role": "assistant", "content": error_msg})


def build_daily_dau_sql(start, end):
    """整体每日DAU SQL"""
    return f"""
SELECT log_date, COUNT(DISTINCT buvid) AS dau
FROM iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
WHERE pid = 73
  AND log_date BETWEEN '{start}' AND '{end}'
GROUP BY 1
"""


def compute_daily_forecast(df_26, df_25, align_map, forecast_dates, ref_days=14, yoy_overrides=None,
                           manual_weekday_yoy=None, manual_weekend_yoy=None,
                           weekday_slope=0.0, weekend_slope=0.0,
                           src_year=2026):
    """天级DAU预估，src_year控制列名动态生成"""
    import io
    from calendar_config import _is_weekend

    sy = src_year % 100
    ry = (src_year - 1) % 100

    dau_25 = {}
    for _, row in df_25.iterrows():
        d = row['log_date']
        if isinstance(d, str):
            d = datetime.strptime(d, '%Y%m%d').date()
        dau_25[d] = float(row['dau'])

    dau_26 = {}
    for _, row in df_26.iterrows():
        d = row['log_date']
        if isinstance(d, str):
            d = datetime.strptime(d, '%Y%m%d').date()
        dau_26[d] = float(row['dau'])

    last_hist_date = max(dau_26.keys()) if dau_26 else (forecast_dates[0] if forecast_dates else date.today())

    # 计算历史日期的YoY
    hist_dates = sorted(dau_26.keys())
    hist_records = []
    for d26 in hist_dates:
        d25 = align_map.get(d26)
        dau_actual = dau_26[d26]
        dau_ref = dau_25.get(d25, 0) if d25 else 0
        yoy = (dau_actual / dau_ref - 1) * 100 if dau_ref > 0 else None
        hist_records.append({
            '日期': d26, f'{sy}年DAU': dau_actual, '数据类型': '实际',
            f'{ry}年参考日期': d25, f'{ry}年参考DAU': dau_ref, 'YoY%': yoy,
            'is_weekend': _is_weekend(d26) or bool(_get_holiday_info(d26, d26.year)),
        })

    # 计算近N天的 weekday/weekend 平均YoY
    recent = [r for r in hist_records[-ref_days:] if r['YoY%'] is not None]
    weekday_yoys = [r['YoY%'] for r in recent if not r['is_weekend']]
    weekend_yoys = [r['YoY%'] for r in recent if r['is_weekend']]

    weekday_avg_yoy = sum(weekday_yoys) / len(weekday_yoys) if weekday_yoys else 0
    weekend_avg_yoy = sum(weekend_yoys) / len(weekend_yoys) if weekend_yoys else 0

    # 手动YoY覆盖
    if manual_weekday_yoy is not None:
        weekday_avg_yoy = manual_weekday_yoy
    if manual_weekend_yoy is not None:
        weekend_avg_yoy = manual_weekend_yoy

    # 预测
    forecast_records = []
    for d26 in sorted(forecast_dates):
        d25 = align_map.get(d26)
        dau_ref = dau_25.get(d25, 0) if d25 else 0
        is_wknd = _is_weekend(d26) or bool(_get_holiday_info(d26, d26.year))

        # 检查是否有手动覆盖
        override_hit = None
        if yoy_overrides:
            for ov in yoy_overrides:
                if ov['start'] <= d26 <= ov['end']:
                    override_hit = ov
                    break

        if override_hit:
            yoy_rate = override_hit['yoy']
            data_type = '预估(调整)'
        else:
            base_yoy = weekend_avg_yoy if is_wknd else weekday_avg_yoy
            slope = weekend_slope if is_wknd else weekday_slope
            weeks_ahead = max(0, (d26 - last_hist_date).days) / 7
            yoy_rate = base_yoy + slope * weeks_ahead
            data_type = '预估'

        predicted = round(dau_ref * (1 + yoy_rate / 100)) if dau_ref > 0 else 0
        forecast_records.append({
            '日期': d26, f'{sy}年DAU': predicted, '数据类型': data_type,
            f'{ry}年参考日期': d25, f'{ry}年参考DAU': dau_ref, 'YoY%': round(yoy_rate, 2),
            'is_weekend': is_wknd,
        })

    all_records = hist_records + forecast_records
    result_df = pd.DataFrame(all_records)
    return result_df, weekday_avg_yoy, weekend_avg_yoy


def compute_weekly_yoy_stats(df_26, df_25, align_map, ref_weeks=4):
    """计算每周平均YoY的趋势斜率（分weekday/weekend），返回 (wd_slope, we_slope) 单位pp/周
    ref_weeks: 使用最近N周数据计算斜率
    """
    from calendar_config import _is_weekend
    from collections import defaultdict

    dau_25 = {}
    for _, row in df_25.iterrows():
        d = row['log_date']
        if isinstance(d, str):
            d = datetime.strptime(d, '%Y%m%d').date()
        dau_25[d] = float(row['dau'])

    dau_26 = {}
    for _, row in df_26.iterrows():
        d = row['log_date']
        if isinstance(d, str):
            d = datetime.strptime(d, '%Y%m%d').date()
        dau_26[d] = float(row['dau'])

    weekly_wd = defaultdict(list)
    weekly_we = defaultdict(list)

    for d26 in sorted(dau_26.keys()):
        d25 = align_map.get(d26)
        if not d25 or d25 not in dau_25 or dau_25[d25] == 0:
            continue
        yoy = (dau_26[d26] / dau_25[d25] - 1) * 100
        key = (d26.isocalendar()[0], d26.isocalendar()[1])
        if _is_weekend(d26):
            weekly_we[key].append(yoy)
        else:
            weekly_wd[key].append(yoy)

    sorted_weeks = sorted(set(list(weekly_wd.keys()) + list(weekly_we.keys())))
    if ref_weeks and len(sorted_weeks) > ref_weeks:
        sorted_weeks = sorted_weeks[-ref_weeks:]

    def _build_series(weekly_dict):
        series = []
        for i, key in enumerate(sorted_weeks):
            vals = weekly_dict.get(key)
            if vals:
                series.append((i, sum(vals) / len(vals)))
        return series

    def _slope(series):
        if len(series) < 3:
            return 0.0
        x = np.array([s[0] for s in series])
        y = np.array([s[1] for s in series])
        coeffs = np.polyfit(x, y, 1)
        return round(coeffs[0], 2)

    return _slope(_build_series(weekly_wd)), _slope(_build_series(weekly_we))


def _render_short_term_forecast():
    """短期DAU预估 — 天级，整体，日期性质对齐"""
    import io
    import plotly.graph_objects as go

    st.markdown("#### 参数设置")

    today = date.today()

    # 从session_state恢复上次参数（防止模式切换后widget重置）
    saved = st.session_state.get('fc_saved_params', {})
    col1, col2 = st.columns(2)
    with col1:
        hist_start = st.date_input("历史数据起始日",
            value=saved.get('hist_start', date(2026, 1, 1)), key="fc_hist_start")
        fc_start = st.date_input("预测起始日",
            value=saved.get('fc_start', today), key="fc_start")
    with col2:
        hist_end = st.date_input("历史数据截止日",
            value=saved.get('hist_end', today - timedelta(days=1)), key="fc_hist_end")
        fc_end = st.date_input("预测截止日",
            value=saved.get('fc_end', today + timedelta(days=30)), key="fc_end")

    ref_days = st.slider("YoY参考天数（取最近N天均值）", 7, 30,
        value=saved.get('ref_days', 14), key="fc_ref_days")

    src_year = hist_start.year
    ref_year = src_year - 1
    sy = src_year % 100
    ry = ref_year % 100
    col_src_dau = f'{sy}年DAU'
    col_ref_date = f'{ry}年参考日期'
    col_ref_dau = f'{ry}年参考DAU'
    col_ref_week_date = f'{ry}年周对齐日期'
    col_ref_week_dau = f'{ry}年周对齐DAU'

    # ── YoY手动调整 ──
    if 'fc_yoy_overrides' not in st.session_state:
        st.session_state['fc_yoy_overrides'] = []

    with st.expander("📝 YoY手动调整（商务策略/春秋假等）", expanded=False):
        ov_col1, ov_col2, ov_col3, ov_col4 = st.columns([2, 2, 1.5, 2])
        with ov_col1:
            ov_start = st.date_input("起始日", value=fc_start, key="ov_start")
        with ov_col2:
            ov_end = st.date_input("截止日", value=fc_start, key="ov_end")
        with ov_col3:
            ov_yoy = st.number_input("YoY%", value=30.0, step=1.0, format="%.1f", key="ov_yoy")
        with ov_col4:
            ov_reason = st.text_input("原因", value="", placeholder="如：春假/商务策略", key="ov_reason")

        if st.button("➕ 添加调整", key="btn_add_override"):
            if ov_start <= ov_end:
                st.session_state['fc_yoy_overrides'].append({
                    'start': ov_start, 'end': ov_end,
                    'yoy': ov_yoy, 'reason': ov_reason or '手动调整',
                })
                st.rerun()
            else:
                st.warning("起始日不能晚于截止日")

        overrides = st.session_state['fc_yoy_overrides']
        if overrides:
            st.markdown("**已添加的调整：**")
            for i, ov in enumerate(overrides):
                ov_text = f"`{ov['start']}` ~ `{ov['end']}` → **YoY {ov['yoy']:+.1f}%**　{ov['reason']}"
                col_t, col_d = st.columns([8, 1])
                with col_t:
                    st.markdown(ov_text)
                with col_d:
                    if st.button("🗑", key=f"del_ov_{i}"):
                        st.session_state['fc_yoy_overrides'].pop(i)
                        st.rerun()

    # 检查是否有已缓存的数据可直接展示
    has_data = 'fc_data_26' in st.session_state and 'fc_data_25' in st.session_state

    # 判断当前参数是否与缓存数据匹配
    current_params = (hist_start, hist_end, fc_start, fc_end, ref_days)
    cached_params = st.session_state.get('fc_cached_params')
    params_changed = cached_params != current_params

    if has_data and not params_changed:
        st.success("✅ 使用已缓存数据")
    elif has_data and params_changed:
        st.info("参数已变更，点击「运行预估」重新获取数据")

    # 计算需要的所有源年日期和对齐
    all_26_dates = [hist_start + timedelta(days=i) for i in range((fc_end - hist_start).days + 1)]
    align_map = build_align_map(all_26_dates)
    week_align_map = {d: _iso_week_align(d) for d in all_26_dates}
    all_ref_dates = list(align_map.values()) + list(week_align_map.values())
    ref_min = min(all_ref_dates) if all_ref_dates else None
    ref_max = max(all_ref_dates) if all_ref_dates else None

    if not ref_min:
        st.warning("无法计算日期对齐")
        return

    hist_start_s = hist_start.strftime('%Y%m%d')
    hist_end_s = hist_end.strftime('%Y%m%d')
    ref_min_s = ref_min.strftime('%Y%m%d')
    ref_max_s = ref_max.strftime('%Y%m%d')

    if not has_data or params_changed:
        if st.button("🚀 运行预估", type="primary", key="btn_fc_daily"):
            # 立即保存参数，防止中途切换后丢失
            st.session_state['fc_saved_params'] = {
                'hist_start': hist_start, 'hist_end': hist_end,
                'fc_start': fc_start, 'fc_end': fc_end, 'ref_days': ref_days,
            }
            st.session_state.pop('fc_wd_slope', None)
            st.session_state.pop('fc_we_slope', None)
            status = st.empty()
            sql_26 = build_daily_dau_sql(hist_start_s, hist_end_s)
            sql_25 = build_daily_dau_sql(ref_min_s, ref_max_s)
            with st.expander(f"查看SQL - {sy}年历史", expanded=False):
                st.code(sql_26, language="sql")
            with st.expander(f"查看SQL - {ry}年参考", expanded=False):
                st.code(sql_25, language="sql")
            try:
                status.warning("⏳ SQL执行中，请勿切换页面，否则需重新运行...")
                df_26 = execute_sql(sql_26, status)
                df_26['dau'] = pd.to_numeric(df_26['dau'], errors='coerce').fillna(0)

                status.warning("⏳ SQL执行中（2/2），请勿切换页面...")
                df_25 = execute_sql(sql_25, status)
                df_25['dau'] = pd.to_numeric(df_25['dau'], errors='coerce').fillna(0)

                st.session_state['fc_data_26'] = df_26
                st.session_state['fc_data_25'] = df_25
                st.session_state['fc_cached_params'] = current_params

                status.success("✅ 数据获取完成")
                has_data = True
            except Exception as e:
                st.error(f"查询失败: {e}")
                return
        else:
            if not has_data:
                st.info("设置好日期后，点击「运行预估」获取数据并生成预测")
            return

    if not has_data:
        return

    df_26 = st.session_state['fc_data_26']
    df_25 = st.session_state['fc_data_25']

    # 预测日期列表
    forecast_dates = [fc_start + timedelta(days=i) for i in range((fc_end - fc_start).days + 1)]

    # 计算YoY周环比趋势
    with st.expander("📈 YoY周环比趋势调整", expanded=False):
        enable_slope = st.checkbox("启用趋势调整（逐周递增/递减预测YoY）",
                                   value=False, key="fc_enable_slope")
        if enable_slope:
            slope_ref_weeks = st.slider("趋势参考周数", 3, 12, value=4, key="fc_slope_ref_weeks",
                                        help="用最近N周的YoY趋势计算斜率")
            wd_slope_auto, we_slope_auto = compute_weekly_yoy_stats(
                df_26, df_25, align_map, ref_weeks=slope_ref_weeks)
            st.caption(f"自动计算（最近{slope_ref_weeks}周）: 周中 {wd_slope_auto:+.2f}pp/周, 周末 {we_slope_auto:+.2f}pp/周")
            sc1, sc2 = st.columns(2)
            with sc1:
                wd_slope = st.number_input("周中斜率(pp/周)", value=round(wd_slope_auto, 2),
                                           step=0.1, format="%.2f", key="fc_wd_slope")
            with sc2:
                we_slope = st.number_input("周末斜率(pp/周)", value=round(we_slope_auto, 2),
                                           step=0.1, format="%.2f", key="fc_we_slope")
        else:
            wd_slope = 0.0
            we_slope = 0.0

    # 计算
    result_df, weekday_yoy, weekend_yoy = compute_daily_forecast(
        df_26, df_25, align_map, forecast_dates, ref_days,
        yoy_overrides=st.session_state.get('fc_yoy_overrides'),
        weekday_slope=wd_slope, weekend_slope=we_slope,
        src_year=src_year)

    # 添加周对齐DAU
    dau_25_lookup = {}
    for _, row in df_25.iterrows():
        d = row['log_date']
        if isinstance(d, str):
            d = datetime.strptime(d, '%Y%m%d').date()
        dau_25_lookup[d] = float(row['dau'])
    result_df[col_ref_week_date] = result_df['日期'].map(week_align_map)
    result_df[col_ref_week_dau] = result_df[col_ref_week_date].map(
        lambda d: dau_25_lookup.get(d, 0) if d else 0)

    # ── AI预估结果 ──
    st.markdown("---")
    st.markdown("#### AI预估结果")

    m1, m2 = st.columns(2)
    wd_delta = f"{wd_slope:+.2f}pp/周" if wd_slope != 0 else None
    we_delta = f"{we_slope:+.2f}pp/周" if we_slope != 0 else None
    m1.metric("周中平均YoY", f"{weekday_yoy:+.1f}%", delta=wd_delta)
    m2.metric("周末平均YoY", f"{weekend_yoy:+.1f}%", delta=we_delta)

    # 趋势图
    hist_part = result_df[result_df['数据类型'] == '实际'].copy()
    fc_part = result_df[result_df['数据类型'] == '预估'].copy()
    fc_adj_part = result_df[result_df['数据类型'] == '预估(调整)'].copy()

    dau_hover = '%{y:.0f}万'
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=hist_part['日期'], y=(hist_part[col_src_dau] / 1e4).round(0),
        mode='lines', name=f'{sy}年实际', line=dict(width=2, color='#1f77b4'),
        hovertemplate='%{x}<br>' + f'{sy}年实际: ' + dau_hover + '<extra></extra>'))
    fc_combined = pd.concat([fc_part, fc_adj_part]).sort_values('日期')
    if not fc_combined.empty:
        fig.add_trace(go.Scatter(
            x=fc_combined['日期'], y=(fc_combined[col_src_dau] / 1e4).round(0),
            mode='lines', name=f'{sy}年预估', line=dict(width=2, dash='dash', color='red'),
            hovertemplate='%{x}<br>' + f'{sy}年预估: ' + dau_hover + '<extra></extra>'))
    if not fc_adj_part.empty:
        fig.add_trace(go.Scatter(
            x=fc_adj_part['日期'], y=(fc_adj_part[col_src_dau] / 1e4).round(0),
            mode='markers', name='YoY调整点',
            marker=dict(size=8, symbol='diamond', color='orange'),
            hovertemplate='%{x}<br>预估(调整): ' + dau_hover + '<extra></extra>'))
    fig.add_trace(go.Scatter(
        x=result_df['日期'], y=(result_df[col_ref_dau] / 1e4).round(0),
        mode='lines', name=f'{ry}年参考日期', line=dict(width=1, dash='dot', color='gray'),
        hovertemplate='%{x}<br>' + f'{ry}年参考日期: ' + dau_hover + '<extra></extra>'))
    fig.add_trace(go.Scatter(
        x=result_df['日期'], y=(result_df[col_ref_week_dau] / 1e4).round(0),
        mode='lines', name=f'{ry}年周对齐', line=dict(width=1, dash='dashdot', color='#ff7f0e'),
        hovertemplate='%{x}<br>' + f'{ry}年周对齐: ' + dau_hover + '<extra></extra>'))
    fig.update_layout(title="每日DAU趋势与预估（AI）", xaxis_title="日期", yaxis_title="DAU(万)",
                      height=450, legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fig, use_container_width=True)

    # 预测明细表
    with st.expander("AI预测明细表", expanded=False):
        show_df = result_df[['日期', col_src_dau, '数据类型', col_ref_date, col_ref_dau, 'YoY%']].copy()
        WEEKDAY_NAMES = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        def _weekday_label(d):
            if not d or pd.isna(d):
                return ''
            label = WEEKDAY_NAMES[d.weekday()]
            lunar = _get_lunar_label(d, d.year)
            if lunar:
                label += f'（{lunar}）'
            elif _get_holiday_info(d, d.year):
                label += f'（{_get_holiday_info(d, d.year)[0]}）'
            elif _is_transfer_work(d, d.year):
                label += '（调休上班）'
            return label
        col_src_week = f'{sy}年周'
        col_ref_week = f'{ry}年周'
        show_df.insert(1, col_src_week, show_df['日期'].apply(_weekday_label))
        show_df.insert(show_df.columns.get_loc(col_ref_date) + 1, col_ref_week,
                       show_df[col_ref_date].apply(_weekday_label))
        show_df[col_src_dau] = (show_df[col_src_dau] / 1e4).round(1)
        show_df[col_ref_dau] = (show_df[col_ref_dau] / 1e4).round(1)
        show_df['YoY%'] = show_df['YoY%'].round(1)
        disp_cols = [f'{sy}年日期', '周', f'{sy}年DAU(万)', '类型', f'{ry}年参考日期', f'周({ry})', f'{ry}年DAU(万)', 'YoY%']
        show_df.columns = disp_cols

        all_cols = list(show_df.columns)
        selected_cols = st.multiselect("展示列", all_cols, default=disp_cols, key="fc_detail_cols")
        if not selected_cols:
            selected_cols = disp_cols
        st.dataframe(show_df[selected_cols], use_container_width=True, height=400)

    # Excel导出
    export_df = result_df[['日期', col_src_dau, col_ref_date, col_ref_dau, 'YoY%']].copy()
    export_df.columns = [f'{sy}年日期', f'{sy}年DAU', f'同比{ry}年参考日期', f'{ry}年参考DAU', 'YoY%']
    export_df[f'{sy}年日期'] = export_df[f'{sy}年日期'].apply(lambda d: d.strftime('%Y-%m-%d') if d else '')
    export_df[f'同比{ry}年参考日期'] = export_df[f'同比{ry}年参考日期'].apply(lambda d: d.strftime('%Y-%m-%d') if d else '')
    export_df['YoY%'] = export_df['YoY%'].round(2)

    buffer = io.BytesIO()
    export_df.to_excel(buffer, index=False, engine='openpyxl')
    st.download_button("📥 导出Excel（AI预估）", data=buffer.getvalue(),
                       file_name="DAU预估_AI.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key="btn_export_forecast")

    # ── 人工预估结果 ──
    st.markdown("---")
    st.markdown("#### 人工预估结果")

    mc1, mc2 = st.columns(2)
    with mc1:
        manual_wd_yoy = st.number_input("周中YoY%", value=round(weekday_yoy, 1), step=0.5, format="%.1f", key="manual_wd_yoy")
    with mc2:
        manual_we_yoy = st.number_input("周末YoY%", value=round(weekend_yoy, 1), step=0.5, format="%.1f", key="manual_we_yoy")

    manual_result_df, _, _ = compute_daily_forecast(
        df_26, df_25, align_map, forecast_dates, ref_days,
        yoy_overrides=st.session_state.get('fc_yoy_overrides'),
        manual_weekday_yoy=manual_wd_yoy, manual_weekend_yoy=manual_we_yoy,
        weekday_slope=wd_slope, weekend_slope=we_slope,
        src_year=src_year)
    manual_result_df[col_ref_week_date] = manual_result_df['日期'].map(week_align_map)
    manual_result_df[col_ref_week_dau] = manual_result_df[col_ref_week_date].map(
        lambda d: dau_25_lookup.get(d, 0) if d else 0)

    m_hist = manual_result_df[manual_result_df['数据类型'] == '实际'].copy()
    m_fc = manual_result_df[manual_result_df['数据类型'].isin(['预估', '预估(调整)'])].copy().sort_values('日期')
    m_fc_adj = manual_result_df[manual_result_df['数据类型'] == '预估(调整)'].copy()

    fig_m = go.Figure()
    fig_m.add_trace(go.Scatter(
        x=m_hist['日期'], y=(m_hist[col_src_dau] / 1e4).round(0),
        mode='lines', name=f'{sy}年实际', line=dict(width=2, color='#1f77b4'),
        hovertemplate='%{x}<br>' + f'{sy}年实际: ' + dau_hover + '<extra></extra>'))
    if not m_fc.empty:
        fig_m.add_trace(go.Scatter(
            x=m_fc['日期'], y=(m_fc[col_src_dau] / 1e4).round(0),
            mode='lines', name=f'{sy}年预估(人工)', line=dict(width=2, dash='dash', color='#2ca02c'),
            hovertemplate='%{x}<br>' + f'{sy}年预估(人工): ' + dau_hover + '<extra></extra>'))
    if not m_fc_adj.empty:
        fig_m.add_trace(go.Scatter(
            x=m_fc_adj['日期'], y=(m_fc_adj[col_src_dau] / 1e4).round(0),
            mode='markers', name='YoY调整点',
            marker=dict(size=8, symbol='diamond', color='orange'),
            hovertemplate='%{x}<br>预估(调整): ' + dau_hover + '<extra></extra>'))
    fig_m.add_trace(go.Scatter(
        x=manual_result_df['日期'], y=(manual_result_df[col_ref_dau] / 1e4).round(0),
        mode='lines', name=f'{ry}年参考日期', line=dict(width=1, dash='dot', color='gray'),
        hovertemplate='%{x}<br>' + f'{ry}年参考日期: ' + dau_hover + '<extra></extra>'))
    fig_m.add_trace(go.Scatter(
        x=manual_result_df['日期'], y=(manual_result_df[col_ref_week_dau] / 1e4).round(0),
        mode='lines', name=f'{ry}年周对齐', line=dict(width=1, dash='dashdot', color='#ff7f0e'),
        hovertemplate='%{x}<br>' + f'{ry}年周对齐: ' + dau_hover + '<extra></extra>'))
    fig_m.update_layout(title="每日DAU趋势与预估（人工）", xaxis_title="日期", yaxis_title="DAU(万)",
                        height=450, legend=dict(orientation="h", y=-0.12))
    st.plotly_chart(fig_m, use_container_width=True)

    # 人工预测明细表
    with st.expander("人工预测明细表", expanded=False):
        m_show = manual_result_df[['日期', col_src_dau, '数据类型', col_ref_date, col_ref_dau, 'YoY%']].copy()
        m_show.insert(1, col_src_week, m_show['日期'].apply(_weekday_label))
        m_show.insert(m_show.columns.get_loc(col_ref_date) + 1, col_ref_week,
                      m_show[col_ref_date].apply(_weekday_label))
        m_show[col_src_dau] = (m_show[col_src_dau] / 1e4).round(1)
        m_show[col_ref_dau] = (m_show[col_ref_dau] / 1e4).round(1)
        m_show['YoY%'] = m_show['YoY%'].round(1)
        m_show.columns = disp_cols
        st.dataframe(m_show, use_container_width=True, height=400)

    # 人工预估导出
    m_export = manual_result_df[['日期', col_src_dau, col_ref_date, col_ref_dau, 'YoY%']].copy()
    m_export.columns = [f'{sy}年日期', f'{sy}年DAU', f'同比{ry}年参考日期', f'{ry}年参考DAU', 'YoY%']
    m_export[f'{sy}年日期'] = m_export[f'{sy}年日期'].apply(lambda d: d.strftime('%Y-%m-%d') if d else '')
    m_export[f'同比{ry}年参考日期'] = m_export[f'同比{ry}年参考日期'].apply(lambda d: d.strftime('%Y-%m-%d') if d else '')
    m_export['YoY%'] = m_export['YoY%'].round(2)

    m_buffer = io.BytesIO()
    m_export.to_excel(m_buffer, index=False, engine='openpyxl')
    st.download_button("📥 导出Excel（人工预估）", data=m_buffer.getvalue(),
                       file_name="DAU预估_人工.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       key="btn_export_manual")


def build_monthly_new_users_sql(n_months=18):
    """月度新增用户SQL（分厂商×启动类型）"""
    from datetime import datetime, timedelta
    today = date.today()
    start = (today.replace(day=1) - timedelta(days=n_months * 30)).strftime('%Y%m01')
    end = today.strftime('%Y%m%d')
    return f"""
SELECT month, chid, initiative_type,
  SUM(daily_new) / COUNT(DISTINCT log_date) AS avg_daily_new
FROM (
  SELECT log_date, SUBSTR(log_date, 1, 6) AS month,
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
      WHEN is_initiative_first = 1 THEN '主启'
      ELSE '外唤'
    END AS initiative_type,
    COUNT(DISTINCT buvid) AS daily_new
  FROM iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
  WHERE pid = 73 AND is_new = '1'
    AND log_date BETWEEN '{start}' AND '{end}'
  GROUP BY 1, 2, 3, 4
) t
GROUP BY 1, 2, 3
"""


def build_monthly_dau_sql(n_months=18):
    """月度日均DAU SQL（分厂商×启动类型）"""
    today = date.today()
    start = (today.replace(day=1) - timedelta(days=n_months * 30)).strftime('%Y%m01')
    end = today.strftime('%Y%m%d')
    return f"""
SELECT month, chid, initiative_type,
  SUM(daily_dau) / COUNT(DISTINCT log_date) AS avg_dau
FROM (
  SELECT log_date, SUBSTR(log_date, 1, 6) AS month,
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
      WHEN is_initiative_first = 1 THEN '主启'
      ELSE '外唤'
    END AS initiative_type,
    COUNT(DISTINCT buvid) AS daily_dau
  FROM iceberg_ug.dws_flow_visit_ott_growth_buvid_dau_analysis_i_1d_d
  WHERE pid = 73
    AND log_date BETWEEN '{start}' AND '{end}'
  GROUP BY 1, 2, 3, 4
) t
GROUP BY 1, 2, 3
"""


def lt_curve(n, a, b):
    """幂律留存曲线: LT(n) = a × (n+1)^(-b)"""
    return a * (n + 1) ** (-b)


def compute_long_term_forecast(df_new, df_dau, lt_params, n_months=3):
    """长期留存模型预测
    df_new: 月度新增（month, chid, initiative_type, avg_daily_new）
    df_dau: 月度DAU（month, chid, initiative_type, avg_dau）
    lt_params: {vendor: {a, b}}
    """
    df_new = df_new.copy()
    df_dau = df_dau.copy()
    for d in [df_new, df_dau]:
        d['month'] = d['month'].astype(str)

    months_sorted = sorted(df_dau['month'].unique())
    vendors = sorted(df_new['chid'].unique())

    # 历史月度汇总
    hist_dau = df_dau.groupby(['month', 'chid'])['avg_dau'].sum().reset_index()
    hist_total = df_dau.groupby('month')['avg_dau'].sum().reset_index()
    hist_total['chid'] = '整体'
    hist_dau = pd.concat([hist_dau, hist_total], ignore_index=True)

    # 主启新增月均
    new_zhuqi = df_new[df_new['initiative_type'] == '主启'].groupby(['month', 'chid'])['avg_daily_new'].sum().reset_index()

    # 外唤DAU月均
    waihuan_dau = df_dau[df_dau['initiative_type'] == '外唤'].groupby(['month', 'chid'])['avg_dau'].sum().reset_index()

    # 预测未来月份
    last_month = months_sorted[-1]
    last_year = int(last_month[:4])
    last_m = int(last_month[4:6])

    future_months = []
    for i in range(1, n_months + 1):
        fm = last_m + i
        fy = last_year + (fm - 1) // 12
        fm = (fm - 1) % 12 + 1
        future_months.append(f"{fy}{fm:02d}")

    # 近3月平均新增和外唤作为预测基线
    recent_months = months_sorted[-3:]
    recent_new = new_zhuqi[new_zhuqi['month'].isin(recent_months)]
    avg_new_by_vendor = recent_new.groupby('chid')['avg_daily_new'].mean().to_dict()

    recent_wh = waihuan_dau[waihuan_dau['month'].isin(recent_months)]
    avg_wh_by_vendor = recent_wh.groupby('chid')['avg_dau'].mean().to_dict()

    # 留存模型预测
    forecasts = []
    for fm in future_months:
        for vendor in vendors:
            params = lt_params.get(vendor, lt_params.get('其他', {'a': 0.35, 'b': 0.42}))
            a, b = params['a'], params['b']
            new_vol = avg_new_by_vendor.get(vendor, 0)

            # 主启DAU = 当月新增×LT(0) + 上月新增×LT(1) + ... + 11月前新增×LT(11)
            zhuqi_dau = sum(new_vol * lt_curve(i, a, b) for i in range(12))

            wh_dau = avg_wh_by_vendor.get(vendor, 0)
            total_dau = round(zhuqi_dau + wh_dau)

            forecasts.append({
                '月份': fm, '厂商': vendor,
                '主启DAU': round(zhuqi_dau), '外唤DAU': round(wh_dau),
                '预测DAU': total_dau,
            })

    forecast_df = pd.DataFrame(forecasts)

    # 汇总整体
    total_fc = forecast_df.groupby('月份')[['主启DAU', '外唤DAU', '预测DAU']].sum().reset_index()
    total_fc['厂商'] = '整体'
    forecast_df = pd.concat([forecast_df, total_fc], ignore_index=True)

    return hist_dau, forecast_df, avg_new_by_vendor, avg_wh_by_vendor


def _render_long_term_forecast():
    """长期留存模型预估Tab"""
    st.markdown("#### 参数设置")
    lt_saved = st.session_state.get('lt_saved_params', {})
    n_months_pred = st.slider("预测月数", 1, 6,
        value=lt_saved.get('n_months', FORECAST_MONTHS), key="lt_pred_months")
    st.session_state.setdefault('lt_saved_params', {})['n_months'] = n_months_pred

    # 数据加载
    has_data = 'lt_data_new' in st.session_state and 'lt_data_dau' in st.session_state

    if not has_data:
        if st.button("🚀 获取历史月度数据", type="primary", key="btn_lt_fetch"):
            status = st.empty()
            sql_new = build_monthly_new_users_sql(FORECAST_HISTORY_MONTHS)
            sql_dau = build_monthly_dau_sql(FORECAST_HISTORY_MONTHS)
            with st.expander("查看SQL — 月度新增", expanded=False):
                st.code(sql_new, language="sql")
            with st.expander("查看SQL — 月度DAU", expanded=False):
                st.code(sql_dau, language="sql")
            try:
                status.info("正在查询月度新增数据...")
                df_new = execute_sql(sql_new, status)
                df_new['avg_daily_new'] = pd.to_numeric(df_new['avg_daily_new'], errors='coerce').fillna(0)
                st.session_state['lt_data_new'] = df_new

                status.info("正在查询月度DAU数据...")
                df_dau = execute_sql(sql_dau, status)
                df_dau['avg_dau'] = pd.to_numeric(df_dau['avg_dau'], errors='coerce').fillna(0)
                st.session_state['lt_data_dau'] = df_dau

                status.success("✅ 月度数据获取完成")
                has_data = True
            except Exception as e:
                st.error(f"查询失败: {e}")
                return
        else:
            st.info("点击上方按钮获取历史月度数据（新增+DAU），用于留存模型预测")
            return

    if not has_data:
        return

    df_new = st.session_state['lt_data_new']
    df_dau = st.session_state['lt_data_dau']

    # LT参数调整
    st.markdown("---")
    st.markdown("#### LT留存曲线参数（幂律: LT(n) = a × (n+1)^(-b)）")
    st.caption("a=次月留存率近似, b=衰减速度。可按厂商调整")

    lt_params = {}
    vendors = VENDOR_NAMES
    cols = st.columns(min(len(vendors), 4))
    for i, v in enumerate(vendors):
        with cols[i % len(cols)]:
            defaults = DEFAULT_LT_PARAMS.get(v, {'a': 0.4, 'b': 0.35})
            st.markdown(f"**{v}**")
            a = st.number_input(f"a", value=defaults['a'], step=0.05, format="%.2f", key=f"lt_a_{v}")
            b = st.number_input(f"b", value=defaults['b'], step=0.05, format="%.2f", key=f"lt_b_{v}")
            lt_params[v] = {'a': a, 'b': b}

    # 计算预测
    hist_dau, forecast_df, avg_new, avg_wh = compute_long_term_forecast(
        df_new, df_dau, lt_params, n_months_pred)

    # ── 展示 ──
    st.markdown("---")
    st.markdown("#### 预测结果")

    import plotly.graph_objects as go

    # 整体月度DAU趋势图
    hist_total = hist_dau[hist_dau['chid'] == '整体'].sort_values('month')
    fc_total = forecast_df[forecast_df['厂商'] == '整体'].sort_values('月份')

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hist_total['month'], y=hist_total['avg_dau'] / 1e4,
                             mode='lines+markers', name='历史日均DAU'))
    fig.add_trace(go.Scatter(x=fc_total['月份'], y=fc_total['预测DAU'] / 1e4,
                             mode='lines+markers', name='预测DAU', line=dict(dash='dash', color='red')))
    fig.update_layout(title="整体月度日均DAU — 历史与预测", xaxis_title="月份", yaxis_title="日均DAU(万)",
                      height=400, legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, use_container_width=True)

    # 预测明细表
    st.markdown("##### 预测明细（日均DAU，万）")
    fc_display = forecast_df[forecast_df['厂商'] != '整体'].copy()
    pivot = fc_display.pivot_table(index='厂商', columns='月份', values='预测DAU', aggfunc='first')
    pivot = (pivot / 1e4).round(1)
    st.dataframe(pivot, use_container_width=True)

    # 主启/外唤拆分
    st.markdown("##### 主启 vs 外唤拆分（日均DAU，万）")
    fc_total_detail = forecast_df[forecast_df['厂商'] == '整体'][['月份', '主启DAU', '外唤DAU', '预测DAU']].copy()
    for c in ['主启DAU', '外唤DAU', '预测DAU']:
        fc_total_detail[c] = (fc_total_detail[c] / 1e4).round(1)
    st.dataframe(fc_total_detail.reset_index(drop=True), use_container_width=True)

    # LT曲线可视化
    with st.expander("LT留存曲线可视化", expanded=False):
        fig_lt = go.Figure()
        months_x = list(range(0, 13))
        for v in vendors:
            p = lt_params[v]
            y = [lt_curve(n, p['a'], p['b']) * 100 for n in months_x]
            fig_lt.add_trace(go.Scatter(x=months_x, y=y, mode='lines+markers', name=v))
        fig_lt.update_layout(title="分厂商LT留存曲线", xaxis_title="月份(0=当月)", yaxis_title="留存率(%)",
                             height=350)
        st.plotly_chart(fig_lt, use_container_width=True)


def display_results(results, obs_label, cmp_label, date_strs=None):
    """展示归因结果 — 全部section顺序渲染，sidebar目录锚点跳转"""
    obs_col_name = f"{obs_label} 日均DAU(万)"
    cmp_col_name = f"{cmp_label} 日均DAU(万)"
    default_metric_cols = [obs_col_name, cmp_col_name, 'YoY%', '贡献pp']
    decompose_cols = ['占比变化pp', '结构贡献pp', '规模贡献pp']
    extra_metric_cols = ['YoY增量(万)', '贡献度%', 'DAU占比%', '贡献度/占比']
    metric_cols = default_metric_cols + decompose_cols + extra_metric_cols

    core_1d, core_2d, core_3d, extra = OrderedDict(), OrderedDict(), OrderedDict(), OrderedDict()
    is_extra_zone = False
    for k, v in results.items():
        if k == '整体':
            continue
        if k == '__extra_separator__':
            is_extra_zone = True
            continue
        if v is None:
            continue
        if is_extra_zone:
            extra[k] = v
        else:
            dc = k.count('×') + 1
            if dc == 1:
                core_1d[k] = v
            elif dc == 2:
                core_2d[k] = v
            else:
                core_3d[k] = v

    # ── 核心结论 ──
    st.markdown('<div id="sec-overview"></div>', unsafe_allow_html=True)
    _render_overview(results, obs_label, cmp_label, date_strs)

    # ── 异动探讨 ──
    st.markdown('<div id="sec-chat"></div>', unsafe_allow_html=True)
    st.markdown("---")
    st.subheader("💬 异动探讨")
    _render_chat(results, obs_label, cmp_label)

    # ── 单维度归因 ──
    st.markdown('<div id="sec-1d"></div>', unsafe_allow_html=True)
    st.markdown("---")
    st.subheader("📌 单维度归因")
    _render_cards_section(core_1d, obs_col_name, cmp_col_name, metric_cols, default_metric_cols, is_extra=False)

    # ── 双维度归因 ──
    st.markdown('<div id="sec-2d"></div>', unsafe_allow_html=True)
    st.markdown("---")
    st.subheader("🔗 双维度归因")
    _render_cards_section(core_2d, obs_col_name, cmp_col_name, metric_cols, default_metric_cols, is_extra=False)

    # ── 三维度归因 ──
    st.markdown('<div id="sec-3d"></div>', unsafe_allow_html=True)
    st.markdown("---")
    st.subheader("🧩 三维度归因")
    _render_cards_section(core_3d, obs_col_name, cmp_col_name, metric_cols, default_metric_cols, is_extra=False)

    # ── 补充维度 ──
    if extra:
        st.markdown('<div id="sec-extra"></div>', unsafe_allow_html=True)
        st.markdown("---")
        st.subheader("📋 补充维度归因")
        _render_cards_section(extra, obs_col_name, cmp_col_name, metric_cols, default_metric_cols, is_extra=True)


# ═══════════════════════════════════════════
# 模式A：归因分析
# ═══════════════════════════════════════════
if app_mode == "📊 归因分析":
    st.title("📺 TV端 DAU 归因分析")

    # 侧边栏 — 目录（锚点跳转）
    st.sidebar.header("📑 目录")
    st.sidebar.markdown("""
<style>
    .toc-link { display:block; padding:3px 0; color:#31333f;
                text-decoration:none !important; font-size:14px; }
    .toc-link:hover { color:#1f77b4; }
    section[data-testid="stSidebar"] hr { margin:0.3rem 0; }
</style>
<a class="toc-link" href="#sec-overview">📊 核心结论</a>
<a class="toc-link" href="#sec-chat">💬 异动探讨</a>
<a class="toc-link" href="#sec-1d">📌 单维度归因</a>
<a class="toc-link" href="#sec-2d">🔗 双维度归因</a>
<a class="toc-link" href="#sec-3d">🧩 三维度归因</a>
<a class="toc-link" href="#sec-extra">📋 补充维度</a>
""", unsafe_allow_html=True)

    st.sidebar.markdown("---")
    st.sidebar.header("参数设置")

    if not ADHOC_TOKEN:
        st.sidebar.warning("请先在 config.py 中填入 ADHOC_TOKEN")

    date_mode = st.sidebar.radio("对比模式", ["周环比（WoW）", "年同比（YoY）", "自定义日期"])

    today = date.today()
    default_obs_end = today - timedelta(days=today.weekday() + 1)
    default_obs_start = default_obs_end - timedelta(days=6)

    if date_mode == "周环比（WoW）":
        obs_start = st.sidebar.date_input("观测期 起始", value=default_obs_start)
        obs_end = st.sidebar.date_input("观测期 截止", value=default_obs_end)
        cmp_start = obs_start - timedelta(days=7)
        cmp_end = obs_end - timedelta(days=7)
        st.sidebar.info(f"对比期: {cmp_start} ~ {cmp_end}")
    elif date_mode == "年同比（YoY）":
        obs_start = st.sidebar.date_input("观测期 起始", value=default_obs_start)
        obs_end = st.sidebar.date_input("观测期 截止", value=default_obs_end)
        cmp_start = obs_start.replace(year=obs_start.year - 1)
        cmp_end = obs_end.replace(year=obs_end.year - 1)
        st.sidebar.info(f"对比期: {cmp_start} ~ {cmp_end}")
    else:
        obs_start = st.sidebar.date_input("观测期 起始", value=default_obs_start)
        obs_end = st.sidebar.date_input("观测期 截止", value=default_obs_end)
        cmp_start = st.sidebar.date_input("对比期 起始", value=default_obs_start - timedelta(days=7))
        cmp_end = st.sidebar.date_input("对比期 截止", value=default_obs_end - timedelta(days=7))

    obs_start_s = obs_start.strftime('%Y%m%d')
    obs_end_s = obs_end.strftime('%Y%m%d')
    cmp_start_s = cmp_start.strftime('%Y%m%d')
    cmp_end_s = cmp_end.strftime('%Y%m%d')

    st.sidebar.markdown("---")
    st.sidebar.subheader("补充维度")
    ALL_DIM_NAMES = CORE_DIM_NAMES + EXTRA_DIM_NAMES
    extra_1d_list = st.sidebar.multiselect("单维度归因", EXTRA_DIM_NAMES)
    _core_set = set(CORE_DIM_NAMES)
    all_2d_combos = [f"{a} × {b}" for a, b in combinations(ALL_DIM_NAMES, 2)
                     if '内容品类' not in (a, b) and not {a, b} <= _core_set]
    extra_2d_list = st.sidebar.multiselect("二维交叉", all_2d_combos)
    all_3d_combos = [f"{a} × {b} × {c}" for a, b, c in combinations(ALL_DIM_NAMES, 3)
                     if '内容品类' not in (a, b, c) and not {a, b, c} <= _core_set]
    extra_3d_list = st.sidebar.multiselect("三维交叉", all_3d_combos)

    # ── 执行分析 ──
    if st.sidebar.button("🚀 开始分析", type="primary", use_container_width=True):
        if not ADHOC_TOKEN:
            st.error("请先在 config.py 中填入 ADHOC_TOKEN")
            st.stop()

        status = st.empty()
        current_dates = (obs_start_s, obs_end_s, cmp_start_s, cmp_end_s)

        if st.session_state.get('cache_dates') != current_dates:
            st.session_state.pop('cache_core_df', None)
            st.session_state.pop('cache_extra_dims', None)
            st.session_state.pop('cache_extra_df', None)
            st.session_state.pop('cache_content_df', None)
            st.session_state.pop('chat_history', None)
            st.session_state['cache_dates'] = current_dates

        if 'cache_core_df' in st.session_state:
            df = st.session_state['cache_core_df']
            obs_dau_total = df[df['period'] == 'obs']['dau'].sum()
            cmp_dau_total = df[df['period'] == 'cmp']['dau'].sum()
            status.success(f"✅ 核心数据使用缓存 | 观测期日均DAU {obs_dau_total/10000:.0f}万 | 对比期日均DAU {cmp_dau_total/10000:.0f}万")
        else:
            sql = build_core_sql(obs_start_s, obs_end_s, cmp_start_s, cmp_end_s)
            with st.expander("查看SQL - 核心维度", expanded=False):
                st.code(sql, language="sql")
            try:
                status.info("正在执行核心维度SQL取数...")
                df = execute_sql(sql, status)
            except Exception as e:
                st.error(f"SQL执行失败: {e}")
                st.stop()
            if df.empty:
                st.warning("查询结果为空，请检查日期范围")
                st.stop()
            df['dau'] = pd.to_numeric(df['dau'], errors='coerce').fillna(0)
            st.session_state['cache_core_df'] = df
            obs_dau_total = df[df['period'] == 'obs']['dau'].sum()
            cmp_dau_total = df[df['period'] == 'cmp']['dau'].sum()
            status.success(f"取数完成！共 {len(df):,} 行 | 观测期日均DAU {obs_dau_total/10000:.0f}万 | 对比期日均DAU {cmp_dau_total/10000:.0f}万")

        results = compute_attribution(df, obs_start_s, obs_end_s, cmp_start_s, cmp_end_s)
        core_cmp_total = df[df['period'] == 'cmp']['dau'].sum()

        selected_combos = []
        for d in extra_1d_list:
            selected_combos.append([d])
        for label in extra_2d_list:
            selected_combos.append(label.split(' × '))
        for label in extra_3d_list:
            selected_combos.append(label.split(' × '))

        extra_results = OrderedDict()
        need_content = False
        all_dims_for_sql = set()
        for combo in selected_combos:
            if combo == ['内容品类']:
                need_content = True
            else:
                for d in combo:
                    all_dims_for_sql.add(d)

        has_non_core = any(d not in CORE_DIM_NAMES for d in all_dims_for_sql)
        ALL_DIM_NAMES_local = CORE_DIM_NAMES + EXTRA_DIM_NAMES

        df_extra_main = None
        if has_non_core and all_dims_for_sql:
            cached_extra_dims = st.session_state.get('cache_extra_dims', set())
            if all_dims_for_sql <= cached_extra_dims:
                df_extra_main = st.session_state['cache_extra_df']
                status.success("✅ 补充维度使用缓存")
            else:
                dims_to_fetch = cached_extra_dims | all_dims_for_sql
                all_dims_list = [d for d in ALL_DIM_NAMES_local if d in dims_to_fetch]
                status.info(f"正在执行补充维度SQL（{', '.join(all_dims_list)}）...")
                sql_extra = build_combined_sql(all_dims_list, obs_start_s, obs_end_s, cmp_start_s, cmp_end_s)
                with st.expander("查看SQL - 补充维度", expanded=False):
                    st.code(sql_extra, language="sql")
                try:
                    df_extra_main = execute_sql(sql_extra, status)
                    df_extra_main['dau'] = pd.to_numeric(df_extra_main['dau'], errors='coerce').fillna(0)
                    st.session_state['cache_extra_dims'] = dims_to_fetch
                    st.session_state['cache_extra_df'] = df_extra_main
                except Exception as e:
                    st.error(f"补充维度SQL执行失败: {e}")
                    df_extra_main = st.session_state.get('cache_extra_df')

        df_content = None
        if need_content:
            if 'cache_content_df' in st.session_state:
                df_content = st.session_state['cache_content_df']
            else:
                status.info("正在执行SQL - 内容品类...")
                sql_ct = build_content_type_sql(obs_start_s, obs_end_s, cmp_start_s, cmp_end_s)
                with st.expander("查看SQL - 内容品类", expanded=False):
                    st.code(sql_ct, language="sql")
                try:
                    df_content = execute_sql(sql_ct, status)
                    df_content['dau'] = pd.to_numeric(df_content['dau'], errors='coerce').fillna(0)
                    st.session_state['cache_content_df'] = df_content
                except Exception as e:
                    st.error(f"内容品类SQL执行失败: {e}")

        if selected_combos:
            status.success("✅ 取数完成，正在计算归因...")

        for combo in selected_combos:
            level_name = ' × '.join(combo)
            if all(d in CORE_DIM_NAMES for d in combo) and level_name in results:
                continue
            if combo == ['内容品类']:
                if df_content is not None:
                    r = compute_extra_attribution(df_content, combo, core_cmp_total=None)
                    if level_name in r:
                        extra_results[level_name] = r[level_name]
                continue
            if df_extra_main is None:
                continue
            r = compute_extra_attribution(df_extra_main, combo, core_cmp_total=core_cmp_total)
            if level_name in r:
                extra_results[level_name] = r[level_name]

        all_results = OrderedDict()
        all_results.update(results)
        if extra_results:
            all_results['__extra_separator__'] = None
            all_results.update(extra_results)

        obs_label = f"{obs_start} ~ {obs_end}"
        cmp_label = f"{cmp_start} ~ {cmp_end}"
        date_strs = (obs_start_s, obs_end_s, cmp_start_s, cmp_end_s)
        display_results(all_results, obs_label, cmp_label, date_strs=date_strs)

        st.session_state['last_df'] = df
        st.session_state['last_results'] = all_results
        st.session_state['last_labels'] = (obs_label, cmp_label, date_strs)

    elif 'last_results' in st.session_state and 'last_labels' in st.session_state:
        obs_label, cmp_label, date_strs = st.session_state['last_labels']
        display_results(st.session_state['last_results'], obs_label, cmp_label, date_strs=date_strs)

# ═══════════════════════════════════════════
# 模式B：DAU预估
# ═══════════════════════════════════════════
elif app_mode == "📈 DAU预估":
    st.title("📺 TV端 DAU 预估")

    st.sidebar.markdown("### 📑 目录")
    forecast_pages = ["短期预估", "长期预估"]
    fc_page = st.sidebar.radio("", forecast_pages, label_visibility="collapsed", key="fc_page")

    if fc_page == "短期预估":
        _render_short_term_forecast()
    elif fc_page == "长期预估":
        _render_long_term_forecast()
