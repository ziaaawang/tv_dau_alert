"""
TV端 DAU 异动归因自动化 — 全局配置
"""
import os
import pickle
from datetime import date, timedelta
from collections import OrderedDict
from itertools import combinations

import numpy as np

# ═══════════════════════════════════════════
# Berserker Adhoc API（自动跑数）
# ═══════════════════════════════════════════
ADHOC_BASE_URL = "https://berserker.bilibili.co"
ADHOC_USERNAME = "wangjiachun01"
ADHOC_TOKEN = "adhoc_vyF9pF4nE94P73EoJWbqbLVXiINsWB"  # ← 填入你的 token

# 千问API（对话功能）
LLM_BASE_URL = "http://llmapi.bilibili.co/v1"
LLM_TOKEN = "bsk-b065d60f6294c2f9078a70851c840d8d"
LLM_MODEL = "qwen3.5-plus"

# 预估默认参数
FORECAST_WEEKS = 4
FORECAST_MONTHS = 3
FORECAST_HISTORY_WEEKS = 12
FORECAST_HISTORY_MONTHS = 18

DEFAULT_LT_PARAMS = {
    'TCL':  {'a': 0.45, 'b': 0.35},
    '小米': {'a': 0.50, 'b': 0.30},
    '海信': {'a': 0.48, 'b': 0.32},
    '康佳': {'a': 0.40, 'b': 0.38},
    '酷开': {'a': 0.42, 'b': 0.36},
    '长虹': {'a': 0.38, 'b': 0.40},
    '其他': {'a': 0.35, 'b': 0.42},
}

VENDOR_NAMES = ['TCL', '小米', '康佳', '海信', '酷开', '长虹', '其他']

# 执行参数
DEFAULT_ENGINE_TYPE = 15        # PRESTO
DEFAULT_LIMIT_SIZE = 50000      # 归因数据量可能较大
POLL_INTERVAL = 5               # 轮询间隔(秒)
POLL_TIMEOUT = 600              # 最长等待(秒，10分钟)

# ═══════════════════════════════════════════
# 项目路径
# ═══════════════════════════════════════════
DATA_PATH = r"D:\b站\ai提效建设\异动自动化\tv_dau_alert\data"

# 旧脚本(step1~step6)使用的参数，app.py不需要
TARGET_YEAR = 2026
TARGET_WEEK = 18
OUTPUT_DIR = None
THRESHOLD_FILE = os.path.join(DATA_PATH, "thresholds.pkl")
THRESHOLD_EXCEL = os.path.join(DATA_PATH, "DAU异常阈值.xlsx")
MANUAL_OVERRIDES = {}

# ═══════════════════════════════════════════
# 维度体系
# ═══════════════════════════════════════════
CORE_DIMS = OrderedDict([
    ('厂商', {
        'field': 'chid',
        'values': ['TCL', '小米', '康佳', '海信', '酷开', '长虹', '其他'],
    }),
    ('启动类型（新）', {
        'field': 'initiative_type_new',
        'values': ['真主启', '类主启', '外唤'],
    }),
    ('活跃分层', {
        'field': 'active_type2',
        'values': [
            '1)当日新增', '2)超低活', '3)低活',
            '4)中活', '5)高活', '6)极高活', '7)流失回流',
        ],
    }),
])

EXTRA_DIMS = OrderedDict([
    ('启动方式（老）', {'field': 'is_initiative_first', 'values': ['主启', '外唤']}),
    ('单端/双栖', {'field': 'is_dbl_buvid', 'values': ['单端', '双栖']}),
    ('城市等级', {'field': 'city_level', 'values': None}),
    ('年龄', {'field': 'age', 'values': ['17-', '18-24', '25-30', '30+', '其他']}),
    ('性别', {'field': 'sex', 'values': ['男', '女', '未知']}),
    ('内容品类', {'field': 'ogv_type_name', 'values': None}),
])

CORE_DIM_NAMES = list(CORE_DIMS.keys())
CORE_DIM_FIELDS = [d['field'] for d in CORE_DIMS.values()]

EXTRA_DIM_NAMES = list(EXTRA_DIMS.keys())
ALL_DIMS = OrderedDict(list(CORE_DIMS.items()) + list(EXTRA_DIMS.items()))

def get_cross_levels():
    """生成叉乘层级列表，返回 [(层级名, [维度名列表]), ...]"""
    levels = []
    # 单维度
    for name in CORE_DIM_NAMES:
        levels.append((name, [name]))
    # 两两叉乘
    for combo in combinations(CORE_DIM_NAMES, 2):
        label = ' × '.join(combo)
        levels.append((label, list(combo)))
    # 三维叉乘
    if len(CORE_DIM_NAMES) >= 3:
        for combo in combinations(CORE_DIM_NAMES, 3):
            label = ' × '.join(combo)
            levels.append((label, list(combo)))
    return levels

CROSS_LEVELS = get_cross_levels()

# ═══════════════════════════════════════════
# 8个指标定义
# ═══════════════════════════════════════════
ALERT_METRICS = ['WoW%', 'YoY%', 'YoY%周环比(pp)', 'WoW%年同比(pp)']
CONTRIB_METRICS = ['WoW贡献pp', 'YoY贡献pp', 'YoY贡献pp周环比差值', 'WoW贡献pp年同比差值']
ALL_METRICS = ALERT_METRICS + CONTRIB_METRICS

# 信号矩阵（预警性质分类）
SIGNAL_MATRIX = [
    ('复合异常',     lambda t: t['WoW%'] and t['YoY%']),
    ('趋势性恶化',   lambda t: t['YoY%'] and t.get('YoY%周环比(pp)_dir') == 'neg'),
    ('趋势性改善',   lambda t: t['YoY%'] and t.get('YoY%周环比(pp)_dir') == 'pos'),
    ('结构性偏移',   lambda t: t['YoY%'] and not t['WoW%']),
    ('季节性偏差',   lambda t: t['WoW%'] and t['WoW%年同比(pp)']),
    ('短期波动',     lambda t: t['WoW%'] and not t['YoY%']),
    ('局部异常',     None),  # 特殊处理：整体 0~1 触发但单维度多处触发
    ('正常周',       lambda t: not any([t['WoW%'], t['YoY%'], t['YoY%周环比(pp)'], t['WoW%年同比(pp)']])),
]

# ═══════════════════════════════════════════
# 样式常量
# ═══════════════════════════════════════════
FONT_NAME = '微软雅黑'
FONT_SIZE_BODY = 10.5
FONT_SIZE_TABLE = 8.5
COLOR_RED = 'C00000'
COLOR_GREEN = '007B3A'
TABLE_STYLE = 'Light Shading Accent 1'

# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def iso_week_to_dates(year, week):
    """ISO 周号 → (周一日期, 周日日期) 字符串 yyyymmdd"""
    jan4 = date(year, 1, 4)
    start_of_w1 = jan4 - timedelta(days=jan4.isoweekday() - 1)
    monday = start_of_w1 + timedelta(weeks=week - 1)
    sunday = monday + timedelta(days=6)
    return monday.strftime('%Y%m%d'), sunday.strftime('%Y%m%d')


def date_to_iso_week(d):
    """date对象 → (iso_year, iso_week)"""
    return d.isocalendar()[:2]


def get_four_weeks(year, week):
    """返回分析所需的4个周：本周/上周/去年同周/去年前一周
    Returns: dict {label: (year, week, start_date, end_date)}
    """
    cur_start, cur_end = iso_week_to_dates(year, week)
    prev_start, prev_end = iso_week_to_dates(year, week - 1) if week > 1 else iso_week_to_dates(year - 1, 52)

    yoy_year = year - 1
    yoy_start, yoy_end = iso_week_to_dates(yoy_year, week)
    yoy_prev_start, yoy_prev_end = iso_week_to_dates(yoy_year, week - 1) if week > 1 else iso_week_to_dates(yoy_year - 1, 52)

    return {
        'cur':      (year, week, cur_start, cur_end),
        'prev':     (year, week - 1 if week > 1 else 52, prev_start, prev_end),
        'yoy':      (yoy_year, week, yoy_start, yoy_end),
        'yoy_prev': (yoy_year, week - 1 if week > 1 else 52, yoy_prev_start, yoy_prev_end),
    }


def get_week_label(year, week):
    """格式化周标签: '2026-W18'"""
    return f"{year}-W{week:02d}"


def get_output_dir(year=None, week=None):
    """获取输出目录"""
    year = year or TARGET_YEAR
    week = week or TARGET_WEEK
    if OUTPUT_DIR:
        return OUTPUT_DIR
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    d = os.path.join(desktop, f"{year}-W{week:02d} TV端DAU预警归因")
    os.makedirs(d, exist_ok=True)
    return d


def load_data(filepath):
    """加载数据文件（支持 csv/xlsx/xls）"""
    import pandas as pd
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.csv':
        df = pd.read_csv(filepath)
    elif ext in ('.xlsx', '.xls'):
        df = pd.read_excel(filepath)
    else:
        raise ValueError(f"不支持的文件格式: {ext}")
    if 'log_date' in df.columns:
        df['log_date'] = df['log_date'].astype(str).str.replace('-', '')
    return df


def day_to_iso_week_col(df):
    """给 DataFrame 添加 iso_year, iso_week 列（基于 log_date 列）"""
    import pandas as pd
    dates = pd.to_datetime(df['log_date'], format='%Y%m%d')
    df['iso_year'] = dates.dt.isocalendar().year.astype(int)
    df['iso_week'] = dates.dt.isocalendar().week.astype(int)
    return df


def aggregate_weekly(df, dim_cols):
    """日级数据按ISO周 + 维度列聚合DAU
    注意：日级DAU是设备去重的，跨天需要直接SUM（因为SQL已经按天去重）
    """
    import pandas as pd
    df = day_to_iso_week_col(df)
    group_cols = ['iso_year', 'iso_week'] + dim_cols
    weekly = df.groupby(group_cols, as_index=False)['dau'].sum()
    return weekly


def compute_metrics(weekly_df, dim_cols, total_weekly=None):
    """计算8个指标
    weekly_df: 周级聚合后的 DataFrame，含 iso_year, iso_week, dim_cols..., dau
    total_weekly: 整体周级DAU（用于计算贡献pp），若为None则自行聚合
    返回: DataFrame，每行 = 一个周 × 一个维度组合，含8个指标列
    """
    import pandas as pd

    if total_weekly is None:
        total_weekly = weekly_df.groupby(['iso_year', 'iso_week'], as_index=False)['dau'].sum()
        total_weekly.rename(columns={'dau': 'total_dau'}, inplace=True)
    elif 'total_dau' not in total_weekly.columns:
        total_weekly = total_weekly.rename(columns={'dau': 'total_dau'})

    rows = []

    if dim_cols:
        groups = weekly_df.groupby(dim_cols)
    else:
        groups = [('__all__', weekly_df)]

    for keys, grp in groups:
        if isinstance(keys, str):
            keys = (keys,)
        if not isinstance(keys, tuple):
            keys = (keys,)

        grp = grp.sort_values(['iso_year', 'iso_week'])

        for _, row in grp.iterrows():
            y, w = int(row['iso_year']), int(row['iso_week'])
            cur_dau = row['dau']

            # 找上周
            prev_w = w - 1 if w > 1 else 52
            prev_y = y if w > 1 else y - 1
            prev = grp[(grp['iso_year'] == prev_y) & (grp['iso_week'] == prev_w)]

            # 找去年同周
            yoy = grp[(grp['iso_year'] == y - 1) & (grp['iso_week'] == w)]

            # 找去年前一周
            yoy_prev = grp[(grp['iso_year'] == y - 1) & (grp['iso_week'] == prev_w)]

            # 至少需要上周数据才能算 WoW
            if prev.empty:
                continue

            prev_dau = prev.iloc[0]['dau']
            yoy_dau = yoy.iloc[0]['dau'] if not yoy.empty else None
            yoy_prev_dau = yoy_prev.iloc[0]['dau'] if not yoy_prev.empty else None

            # 预警指标
            wow_pct = (cur_dau / prev_dau - 1) * 100 if prev_dau else None
            yoy_pct = (cur_dau / yoy_dau - 1) * 100 if yoy_dau else None

            # 上周的 YoY%
            prev_yoy_pct = (prev_dau / yoy_prev_dau - 1) * 100 if yoy_prev_dau else None
            # YoY%周环比(pp)
            yoy_wow_pp = (yoy_pct - prev_yoy_pct) if (yoy_pct is not None and prev_yoy_pct is not None) else None

            # 去年同周的 WoW%
            yoy_wow_pct = (yoy_dau / yoy_prev_dau - 1) * 100 if yoy_prev_dau else None
            # WoW%年同比(pp)
            wow_yoy_pp = (wow_pct - yoy_wow_pct) if (wow_pct is not None and yoy_wow_pct is not None) else None

            # 归因指标（需要总DAU）
            total_prev = total_weekly[(total_weekly['iso_year'] == prev_y) & (total_weekly['iso_week'] == prev_w)]
            total_yoy = total_weekly[(total_weekly['iso_year'] == y - 1) & (total_weekly['iso_week'] == w)]
            total_yoy_prev = total_weekly[(total_weekly['iso_year'] == y - 1) & (total_weekly['iso_week'] == prev_w)]

            total_prev_dau = total_prev.iloc[0]['total_dau'] if not total_prev.empty else None
            total_yoy_dau = total_yoy.iloc[0]['total_dau'] if not total_yoy.empty else None
            total_yoy_prev_dau = total_yoy_prev.iloc[0]['total_dau'] if not total_yoy_prev.empty else None

            wow_contrib = (cur_dau - prev_dau) / total_prev_dau * 100 if total_prev_dau else None
            yoy_contrib = (cur_dau - yoy_dau) / total_yoy_dau * 100 if (yoy_dau is not None and total_yoy_dau) else None

            # 上周的 YoY贡献pp
            prev_yoy_contrib = (prev_dau - yoy_prev_dau) / total_yoy_prev_dau * 100 if (yoy_prev_dau is not None and total_yoy_prev_dau) else None
            yoy_contrib_wow_diff = (yoy_contrib - prev_yoy_contrib) if (yoy_contrib is not None and prev_yoy_contrib is not None) else None

            # 去年同周的 WoW贡献pp
            yoy_wow_contrib = (yoy_dau - yoy_prev_dau) / total_yoy_prev_dau * 100 if (yoy_dau is not None and yoy_prev_dau is not None and total_yoy_prev_dau) else None
            wow_contrib_yoy_diff = (wow_contrib - yoy_wow_contrib) if (wow_contrib is not None and yoy_wow_contrib is not None) else None

            r = {
                'iso_year': y, 'iso_week': w, 'dau': cur_dau,
                'WoW%': wow_pct, 'YoY%': yoy_pct,
                'YoY%周环比(pp)': yoy_wow_pp, 'WoW%年同比(pp)': wow_yoy_pp,
                'WoW贡献pp': wow_contrib, 'YoY贡献pp': yoy_contrib,
                'YoY贡献pp周环比差值': yoy_contrib_wow_diff,
                'WoW贡献pp年同比差值': wow_contrib_yoy_diff,
            }
            for i, name in enumerate(dim_cols):
                r[name] = keys[i] if keys[0] != '__all__' else 'all'
            rows.append(r)

    return pd.DataFrame(rows)


TUKEY_K = 0.8  # 旧脚本(step1~step6)使用，app.py不需要


def tukey_two_round(series, k=TUKEY_K):
    """两轮迭代 Tukey 箱线图规则
    返回: (lo, hi, trigger_rate)
    """
    arr = series.dropna().values
    if len(arr) < 4:
        return None, None, None

    # Round 1
    q25_1, q50_1, q75_1 = np.percentile(arr, [25, 50, 75])
    iqr_1 = q75_1 - q25_1
    lo_1 = q50_1 - k * iqr_1
    hi_1 = q50_1 + k * iqr_1

    # Round 2: 只保留 Round 1 区间内的数据
    filtered = arr[(arr >= lo_1) & (arr <= hi_1)]
    if len(filtered) < 4:
        lo, hi = lo_1, hi_1
    else:
        q25_2, q50_2, q75_2 = np.percentile(filtered, [25, 50, 75])
        iqr_2 = q75_2 - q25_2
        lo_2 = q50_2 - k * iqr_2
        hi_2 = q50_2 + k * iqr_2
        lo = max(lo_1, lo_2)
        hi = min(hi_1, hi_2)

    trigger_rate = np.mean((arr < lo) | (arr > hi))
    return round(lo, 4), round(hi, 4), round(trigger_rate, 4)


def check_threshold(value, lo, hi):
    """判定是否触发阈值，返回 ('触发上界'|'触发下界'|'正常', 偏离幅度)"""
    if lo is None or hi is None or value is None:
        return '无数据', 0
    if value > hi:
        width = hi - lo if hi != lo else 1
        deviation = (value - hi) / abs(width)
        return '触发上界', round(deviation, 4)
    elif value < lo:
        width = hi - lo if hi != lo else 1
        deviation = (lo - value) / abs(width)
        return '触发下界', round(deviation, 4)
    else:
        return '正常', 0


def fmt_pct(val, decimals=2):
    """格式化百分比"""
    if val is None:
        return '-'
    return f"{val:+.{decimals}f}%"


def fmt_pp(val, decimals=2):
    """格式化pp值"""
    if val is None:
        return '-'
    return f"{val:+.{decimals}f}pp"


def fmt_dau_wan(val):
    """DAU转为万，保留整数"""
    if val is None:
        return '-'
    return f"{val / 10000:.0f}"


def save_pkl(obj, path):
    with open(path, 'wb') as f:
        pickle.dump(obj, f)


def load_pkl(path):
    with open(path, 'rb') as f:
        return pickle.load(f)
