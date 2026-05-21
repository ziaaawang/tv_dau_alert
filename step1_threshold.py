"""
Step 1: 阈值计算
输入: 长周期核心维度数据 (CSV/Excel)
输出: thresholds.pkl
"""
import os
import sys
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from config import (
    CORE_DIMS, CORE_DIM_FIELDS, CORE_DIM_NAMES, EXTRA_DIMS,
    CROSS_LEVELS, ALL_METRICS, TUKEY_K, THRESHOLD_FILE, DATA_PATH,
    load_data, aggregate_weekly, compute_metrics, tukey_two_round, save_pkl,
)


def compute_thresholds_for_group(metrics_df, dim_cols, group_keys=None):
    """对一个维度组合计算8个指标的阈值
    metrics_df: 含8个指标列的 DataFrame
    dim_cols: 维度字段列表（用于筛选 group_keys 对应行）
    group_keys: 维度取值元组，None 表示整体
    """
    if group_keys is not None and dim_cols:
        mask = pd.Series(True, index=metrics_df.index)
        for col, val in zip(dim_cols, group_keys):
            mask &= (metrics_df[col] == val)
        sub = metrics_df[mask]
    else:
        sub = metrics_df

    result = {}
    for metric in ALL_METRICS:
        if metric not in sub.columns:
            result[metric] = {'lo': None, 'hi': None, 'trigger_rate': None}
            continue
        lo, hi, tr = tukey_two_round(sub[metric], TUKEY_K)
        result[metric] = {'lo': lo, 'hi': hi, 'trigger_rate': tr}
    return result


def run(core_data_path, extra_data_paths=None):
    """
    core_data_path: 核心3维数据文件路径
    extra_data_paths: dict, {'城市等级': path, '年龄性别': path, '内容品类': path}
    """
    print("=" * 60)
    print("Step 1: 阈值计算")
    print("=" * 60)

    # ── 加载核心数据 ──
    print(f"\n加载核心数据: {core_data_path}")
    df = load_data(core_data_path)
    print(f"  行数: {len(df):,}，日期范围: {df['log_date'].min()} ~ {df['log_date'].max()}")

    # ── 周级聚合 ──
    print("\n周级聚合...")
    weekly_all = aggregate_weekly(df, CORE_DIM_FIELDS)
    total_weekly = weekly_all.groupby(['iso_year', 'iso_week'], as_index=False)['dau'].sum()
    total_weekly.rename(columns={'dau': 'total_dau'}, inplace=True)

    weeks = total_weekly[['iso_year', 'iso_week']].drop_duplicates()
    print(f"  共 {len(weeks)} 个周")

    # ── 计算指标（整体 + 全维度交叉） ──
    print("\n计算8指标（全维度交叉）...")
    metrics_full = compute_metrics(weekly_all, CORE_DIM_FIELDS, total_weekly)
    print(f"  指标行数: {len(metrics_full):,}")

    if len(metrics_full) == 0:
        # 检查是否缺少 YoY 数据
        years = total_weekly['iso_year'].unique()
        print(f"\n  警告：指标行数为0！")
        print(f"  数据覆盖年份: {sorted(years)}")
        if len(years) < 2:
            print(f"  原因：数据只有 {years[0]} 年，缺少去年同周对照。")
            print(f"  解决：SQL的 date_start 需扩大到 {int(min(years))-1} 年，至少覆盖2个完整年度。")
            print(f"  例如：date_start = '{int(min(years))-1}0901'")
        else:
            print(f"  原因：虽然有多年数据，但可能缺少连续周数据。请检查数据完整性。")
        print(f"\n  当前仍会尝试只用 WoW 指标计算阈值...")

    # ── 计算阈值 ──
    thresholds = {}

    # 1. 整体
    print("\n[1/5] 整体阈值...")
    weekly_total = weekly_all.groupby(['iso_year', 'iso_week'], as_index=False)['dau'].sum()
    overall_metrics_df = compute_metrics(weekly_total, [], total_weekly)
    if overall_metrics_df.empty:
        print("  整体指标为空，请检查数据覆盖时间范围！")
        thresholds['整体'] = {m: {'lo': None, 'hi': None, 'trigger_rate': None} for m in ALL_METRICS}
    else:
        thresholds['整体'] = compute_thresholds_for_group(overall_metrics_df, [], None)
        _print_threshold('整体', thresholds['整体'])

    # 2~4. 单维度、两两叉乘、三维叉乘
    level_labels = {1: '单维度', 2: '两两叉乘', 3: '三维叉乘'}
    for level_num in [1, 2, 3]:
        levels = [(name, dims) for name, dims in CROSS_LEVELS if len(dims) == level_num]
        label = level_labels[level_num]
        print(f"\n[{level_num + 1}/5] {label}阈值（{len(levels)}组）...")

        for level_name, dim_names in levels:
            dim_fields = [CORE_DIMS[d]['field'] for d in dim_names]
            # 对这些维度聚合
            weekly_level = weekly_all.groupby(
                ['iso_year', 'iso_week'] + dim_fields, as_index=False
            )['dau'].sum()
            metrics_level = compute_metrics(weekly_level, dim_fields, total_weekly)

            # 对每个维度组合计算阈值
            if not metrics_level.empty:
                groups = metrics_level.groupby(dim_fields).groups
                level_thresholds = {}
                for group_keys in groups:
                    if not isinstance(group_keys, tuple):
                        group_keys = (group_keys,)
                    t = compute_thresholds_for_group(metrics_level, dim_fields, group_keys)
                    level_thresholds[group_keys] = t

                thresholds[level_name] = level_thresholds
                n_groups = len(level_thresholds)
                print(f"  {level_name}: {n_groups} 个组合")

    # 5. 补充维度（单维度）
    print(f"\n[5/5] 补充维度阈值...")
    if extra_data_paths:
        for dim_name, dim_info in EXTRA_DIMS.items():
            field = dim_info['field']
            path_key = dim_name
            if path_key not in extra_data_paths:
                print(f"  跳过 {dim_name}：无数据文件")
                continue

            edf = load_data(extra_data_paths[path_key])
            weekly_extra = aggregate_weekly(edf, [field])
            total_extra = weekly_extra.groupby(['iso_year', 'iso_week'], as_index=False)['dau'].sum()
            total_extra.rename(columns={'dau': 'total_dau'}, inplace=True)
            metrics_extra = compute_metrics(weekly_extra, [field], total_extra)

            if not metrics_extra.empty:
                groups = metrics_extra.groupby(field).groups
                extra_thresholds = {}
                for val in groups:
                    gk = (val,) if not isinstance(val, tuple) else val
                    t = compute_thresholds_for_group(metrics_extra, [field], gk)
                    extra_thresholds[gk] = t
                thresholds[f"补充_{dim_name}"] = extra_thresholds
                print(f"  {dim_name}: {len(extra_thresholds)} 个取值")
    else:
        print("  未提供补充维度数据，跳过")

    # ── 保存 ──
    os.makedirs(os.path.dirname(THRESHOLD_FILE), exist_ok=True)
    save_pkl(thresholds, THRESHOLD_FILE)
    print(f"\n阈值已保存: {THRESHOLD_FILE}")
    print(f"共 {sum(len(v) if isinstance(v, dict) and not isinstance(list(v.values())[0] if v else {}, dict) else (sum(len(vv) for vv in v.values()) if isinstance(v, dict) else 1) for v in thresholds.values())} 组阈值")

    return thresholds


def _print_threshold(name, t):
    """打印单组阈值"""
    print(f"  {name}:")
    for m in ALL_METRICS:
        if m in t:
            info = t[m]
            lo = f"{info['lo']:.2f}" if info['lo'] is not None else '-'
            hi = f"{info['hi']:.2f}" if info['hi'] is not None else '-'
            tr = f"{info['trigger_rate']*100:.0f}%" if info['trigger_rate'] is not None else '-'
            print(f"    {m:20s} [{lo:>8s}, {hi:>8s}]  触发率 {tr}")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='TV端DAU阈值计算')
    parser.add_argument('core_data', help='核心3维数据文件路径 (CSV/Excel)')
    parser.add_argument('--extra-city', help='补充维度-城市等级数据文件')
    parser.add_argument('--extra-age-sex', help='补充维度-年龄性别数据文件')
    parser.add_argument('--extra-content', help='补充维度-内容品类数据文件')
    args = parser.parse_args()

    extra = {}
    if args.extra_city:
        extra['城市等级'] = args.extra_city
    if args.extra_age_sex:
        # 年龄和性别在同一个文件里，分别处理
        extra['年龄'] = args.extra_age_sex
        extra['性别'] = args.extra_age_sex
    if args.extra_content:
        extra['内容品类'] = args.extra_content

    run(args.core_data, extra if extra else None)
