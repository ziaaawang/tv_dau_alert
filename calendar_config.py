"""
TV端 DAU 预估 — 日历配置 + 日期对齐
定义节假日/调休/寒暑假，提供26→25日期对齐函数
"""
from datetime import date, timedelta, datetime

# ═══════════════════════════════════════════
# 节假日（放假区间，含调休连休）
# ═══════════════════════════════════════════
HOLIDAYS = {
    2024: {
        '元旦':     ('20240101', '20240101'),
        '春节':     ('20240209', '20240217'),
        '清明':     ('20240404', '20240406'),
        '劳动节':   ('20240501', '20240505'),
        '端午':     ('20240608', '20240610'),
        '中秋':     ('20240915', '20240917'),
        '国庆':     ('20241001', '20241007'),
    },
    2025: {
        '元旦':     ('20250101', '20250101'),
        '春节':     ('20250128', '20250204'),
        '清明':     ('20250404', '20250406'),
        '劳动节':   ('20250501', '20250505'),
        '端午':     ('20250531', '20250602'),
        '中秋国庆': ('20251001', '20251008'),
    },
    2026: {
        '元旦':     ('20260101', '20260103'),
        '春节':     ('20260215', '20260223'),
        '清明':     ('20260404', '20260406'),
        '劳动节':   ('20260501', '20260505'),
        '端午':     ('20260619', '20260621'),
        '中秋':     ('20260925', '20260927'),
        '国庆':     ('20261001', '20261007'),
    },
}

# ═══════════════════════════════════════════
# 调休上班日（原本是休息日，调为工作日）
# ═══════════════════════════════════════════
TRANSFER_WORK = {
    2024: {'20240204', '20240218', '20240407', '20240428', '20240511', '20240914', '20240929', '20241012'},
    2025: {'20250126', '20250208', '20250427', '20250928', '20251011'},
    2026: {'20260104', '20260214', '20260228', '20260509', '20260920', '20261010'},
}

# ═══════════════════════════════════════════
# 寒暑假
# ═══════════════════════════════════════════
WINTER_BREAK = {
    2024: ('20240120', '20240225'),
    2025: ('20250111', '20250216'),
    2026: ('20260117', '20260307'),
}

SUMMER_BREAK = {
    2024: ('20240701', '20240831'),
    2025: ('20250701', '20250831'),
    2026: ('20260701', '20260831'),
}

# ═══════════════════════════════════════════
# 春节农历标注
# ═══════════════════════════════════════════
SPRING_FESTIVAL_LUNAR = {
    2024: {
        '20240125': '腊月十四',
        '20240126': '腊月十五',
        '20240127': '腊月十六',
        '20240128': '腊月十七',
        '20240129': '腊月十八',
        '20240130': '腊月十九',
        '20240131': '腊月二十',
        '20240201': '腊月廿一',
        '20240202': '腊月廿二',
        '20240203': '腊月廿三',
        '20240204': '腊月廿四',
        '20240205': '腊月廿五',
        '20240206': '腊月廿六',
        '20240207': '腊月廿七',
        '20240208': '腊月廿八',
        '20240209': '除夕',
        '20240210': '初一',
        '20240211': '初二',
        '20240212': '初三',
        '20240213': '初四',
        '20240214': '初五',
        '20240215': '初六',
        '20240216': '初七',
        '20240217': '初八',
        '20240218': '初九',
        '20240219': '初十',
        '20240220': '十一',
        '20240221': '十二',
        '20240222': '十三',
        '20240223': '十四',
        '20240224': '十五',
    },
    2025: {
        # 除夕前15天（腊月十四~廿九）
        '20250113': '腊月十四',
        '20250114': '腊月十五',
        '20250115': '腊月十六',
        '20250116': '腊月十七',
        '20250117': '腊月十八',
        '20250118': '腊月十九',
        '20250119': '腊月二十',
        '20250120': '腊月廿一',
        '20250121': '腊月廿二',
        '20250122': '腊月廿三',
        '20250123': '腊月廿四',
        '20250124': '腊月廿五',
        '20250125': '腊月廿六',
        '20250126': '腊月廿七',
        '20250127': '腊月廿八',
        '20250128': '除夕',
        # 除夕后15天（正月初一~十五）
        '20250129': '初一',
        '20250130': '初二',
        '20250131': '初三',
        '20250201': '初四',
        '20250202': '初五',
        '20250203': '初六',
        '20250204': '初七',
        '20250205': '初八',
        '20250206': '初九',
        '20250207': '初十',
        '20250208': '十一',
        '20250209': '十二',
        '20250210': '十三',
        '20250211': '十四',
        '20250212': '十五',
    },
    2026: {
        # 除夕前15天（腊月十四~廿九）
        '20260201': '腊月十四',
        '20260202': '腊月十五',
        '20260203': '腊月十六',
        '20260204': '腊月十七',
        '20260205': '腊月十八',
        '20260206': '腊月十九',
        '20260207': '腊月二十',
        '20260208': '腊月廿一',
        '20260209': '腊月廿二',
        '20260210': '腊月廿三',
        '20260211': '腊月廿四',
        '20260212': '腊月廿五',
        '20260213': '腊月廿六',
        '20260214': '腊月廿七',
        '20260215': '腊月廿八',
        '20260216': '除夕',
        # 除夕后15天（正月初一~十五）
        '20260217': '初一',
        '20260218': '初二',
        '20260219': '初三',
        '20260220': '初四',
        '20260221': '初五',
        '20260222': '初六',
        '20260223': '初七',
        '20260224': '初八',
        '20260225': '初九',
        '20260226': '初十',
        '20260227': '十一',
        '20260228': '十二',
        '20260301': '十三',
        '20260302': '十四',
        '20260303': '十五',
    },
}


def _get_lunar_label(d, year):
    """返回春节期间的农历标注，非春节返回 None"""
    labels = SPRING_FESTIVAL_LUNAR.get(year, {})
    return labels.get(d.strftime('%Y%m%d'))

# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def _parse(s):
    return datetime.strptime(s, '%Y%m%d').date()

def _fmt(d):
    return d.strftime('%Y%m%d')

def _in_range(d, rng):
    return _parse(rng[0]) <= d <= _parse(rng[1])

def _is_weekend(d):
    return d.weekday() >= 5


def _get_holiday_info(d, year):
    """如果 d 在 year 的某个节假日内，返回 (节假日名, 偏移天数)，否则 None"""
    holidays = HOLIDAYS.get(year, {})
    for name, (s, e) in holidays.items():
        start, end = _parse(s), _parse(e)
        if start <= d <= end:
            return name, (d - start).days
    return None


def _is_transfer_work(d, year):
    return _fmt(d) in TRANSFER_WORK.get(year, set())


def _is_actual_workday(d, year):
    """考虑调休后的实际工作日判断"""
    if _get_holiday_info(d, year):
        return False
    if _is_transfer_work(d, year):
        return True
    return d.weekday() < 5


def _in_break(d, year):
    """返回 'winter'/'summer'/None"""
    wb = WINTER_BREAK.get(year)
    if wb and _in_range(d, wb):
        return 'winter'
    sb = SUMMER_BREAK.get(year)
    if sb and _in_range(d, sb):
        return 'summer'
    return None


# ═══════════════════════════════════════════
# 核心对齐：26年日期 → 25年参考日期
# ═══════════════════════════════════════════

def _is_actual_restday(d, year):
    """实际休息日：节假日 或 (周末且非调休)"""
    if _get_holiday_info(d, year):
        return True
    if _is_transfer_work(d, year):
        return False
    return d.weekday() >= 5


def _work_stretch_position(d, year):
    """计算工作日在其 work stretch（连续工作日段）中的位置。
    返回 (pos_from_start, pos_from_end)；休息日返回 None。
    """
    if not _is_actual_workday(d, year):
        return None
    pos_from_start = 0
    check = d - timedelta(days=1)
    while _is_actual_workday(check, year) and pos_from_start < 15:
        pos_from_start += 1
        check -= timedelta(days=1)
    pos_from_end = 0
    check = d + timedelta(days=1)
    while _is_actual_workday(check, year) and pos_from_end < 15:
        pos_from_end += 1
        check += timedelta(days=1)
    return (pos_from_start, pos_from_end)


def _rest_stretch_position(d, year):
    """计算休息日在其 rest stretch 中的位置。
    返回 (pos_from_start, pos_from_end)；工作日返回 None。
    """
    if _is_actual_workday(d, year):
        return None
    pos_from_start = 0
    check = d - timedelta(days=1)
    while not _is_actual_workday(check, year) and pos_from_start < 30:
        pos_from_start += 1
        check -= timedelta(days=1)
    pos_from_end = 0
    check = d + timedelta(days=1)
    while not _is_actual_workday(check, year) and pos_from_end < 30:
        pos_from_end += 1
        check += timedelta(days=1)
    return (pos_from_start, pos_from_end)


def _positions_match(pos26, pos25):
    """判断两个 work stretch position 是否匹配。
    匹配规则：pos_from_start 相同 且 is_last 状态相同。
    """
    if pos26 is None or pos25 is None:
        return False
    s26, e26 = pos26
    s25, e25 = pos25
    return s26 == s25 and (e26 == 0) == (e25 == 0)


def _find_position_match(candidate, pos26, year_25, is_workday, max_search=7):
    """在 candidate 附近 ±max_search 天内找 stretch position 匹配的日期。
    先精确匹配(pos_from_start + is_last)，再放宽(仅 is_last 或仅 pos_from_start)。
    """
    # 精确匹配
    for delta in range(0, max_search + 1):
        for sign in ([0] if delta == 0 else [-1, 1]):
            alt = candidate + timedelta(days=delta * sign)
            if is_workday:
                pos25 = _work_stretch_position(alt, year_25)
                if _positions_match(pos26, pos25):
                    return alt
            else:
                pos25 = _rest_stretch_position(alt, year_25)
                if pos25 is not None and pos26[0] == pos25[0]:
                    return alt

    # 放宽：仅匹配 is_last（工作日）
    if is_workday and pos26[1] == 0:
        for delta in range(1, max_search + 1):
            for sign in [-1, 1]:
                alt = candidate + timedelta(days=delta * sign)
                pos25 = _work_stretch_position(alt, year_25)
                if pos25 is not None and pos25[1] == 0:
                    return alt

    # 放宽：仅匹配 pos_from_start（工作日）
    if is_workday:
        for delta in range(1, max_search + 1):
            for sign in [-1, 1]:
                alt = candidate + timedelta(days=delta * sign)
                pos25 = _work_stretch_position(alt, year_25)
                if pos25 is not None and pos25[0] == pos26[0]:
                    return alt

    return candidate


def _is_pre_holiday(d, year):
    """d是否为假期前最后一个工作日（d到假期之间仅隔普通休息日）"""
    if not _is_actual_workday(d, year):
        return False
    check = d + timedelta(days=1)
    while not _is_actual_workday(check, year) and (check - d).days <= 10:
        if _get_holiday_info(check, year):
            return True
        check += timedelta(days=1)
    return False


def _is_holiday_affected(d, year):
    """d是否受节假日影响（假日内/调休/假期前最后工作日）"""
    return bool(_get_holiday_info(d, year)) or _is_transfer_work(d, year) or _is_pre_holiday(d, year)


def _find_nearest_normal_same_dow(d, year, max_weeks=4):
    """找最近的同星期几、不受节假日影响的日期（优先往前找）"""
    for w in range(1, max_weeks + 1):
        for sign in [-1, 1]:
            alt = d + timedelta(weeks=w * sign)
            if not _is_holiday_affected(alt, year):
                return alt
    return d


def align_date(d):
    """将某年日期对齐到前一年的同性质日期。自动检测年份。
    优先级：农历对齐 > 节假日 > 寒暑假 > work stretch position 匹配
    """
    year = d.year
    ref_year = year - 1

    # P0: 春节前后农历对齐（除夕±15天，按农历标注匹配）
    lunar_label = _get_lunar_label(d, year)
    if lunar_label:
        labels_ref = SPRING_FESTIVAL_LUNAR.get(ref_year, {})
        for ds, lbl in labels_ref.items():
            if lbl == lunar_label:
                return _parse(ds)

    # P1: 节假日对齐（同名节假日内按 offset）
    hol_info = _get_holiday_info(d, year)
    if hol_info:
        name, offset = hol_info
        ref_holidays = HOLIDAYS.get(ref_year, {})
        ref_name = None
        if name in ref_holidays:
            ref_name = name
        else:
            candidates = []
            for rn in ref_holidays:
                if name in rn or rn in name:
                    candidates.append(rn)
            if len(candidates) == 1:
                ref_name = candidates[0]
            elif len(candidates) > 1:
                ref_name = min(candidates, key=lambda rn: abs(_parse(ref_holidays[rn][0]).month - d.month))
        if ref_name:
            ref_start = _parse(ref_holidays[ref_name][0])
            ref_end = _parse(ref_holidays[ref_name][1])
            candidate = ref_start + timedelta(days=offset)
            if candidate > ref_end:
                return ref_end
            if candidate < ref_start:
                return ref_start
            return candidate
        return _iso_week_align(d)

    # P2: 寒暑假对齐
    break_type = _in_break(d, year)
    if break_type:
        return _break_align(d, break_type, year, ref_year)

    # P3: 按 work stretch position 对齐
    iso_candidate = _iso_week_align(d)
    candidate = iso_candidate
    is_workday_cur = _is_actual_workday(d, year)

    if is_workday_cur:
        pos_cur = _work_stretch_position(d, year)
        pos_ref = _work_stretch_position(candidate, ref_year)
        if not _positions_match(pos_cur, pos_ref):
            candidate = _find_position_match(candidate, pos_cur, ref_year, is_workday=True)
    else:
        pos_cur = _rest_stretch_position(d, year)
        pos_ref = _rest_stretch_position(candidate, ref_year)
        if pos_ref is None or pos_cur[0] != pos_ref[0]:
            candidate = _find_position_match(candidate, pos_cur, ref_year, is_workday=False)

    # P4: 当年普通日不应对齐到参考年节假日影响日
    if _is_holiday_affected(candidate, ref_year) and not _is_holiday_affected(d, year):
        candidate = _find_nearest_normal_same_dow(iso_candidate, ref_year)

    return candidate


def _iso_week_align(d):
    """用ISO周+星期几找前一年对应日期"""
    iso = d.isocalendar()
    iso_year, iso_week, iso_day = iso[0], iso[1], iso[2]
    try:
        return date.fromisocalendar(iso_year - 1, iso_week, iso_day)
    except ValueError:
        return d - timedelta(days=364)



def _break_align(d, break_type, year=None, ref_year=None):
    """寒暑假对齐：将假期拆为 节前/节中/节后 三段分别按比例对齐，
    避免普通寒假日对齐到春节假期。"""
    if year is None:
        year = d.year
    if ref_year is None:
        ref_year = year - 1

    breaks = WINTER_BREAK if break_type == 'winter' else SUMMER_BREAK
    if year not in breaks or ref_year not in breaks:
        return _iso_week_align(d)

    b_cur_start = _parse(breaks[year][0])
    b_cur_end = _parse(breaks[year][1])
    b_ref_start = _parse(breaks[ref_year][0])
    b_ref_end = _parse(breaks[ref_year][1])

    hol_cur_start, hol_cur_end = None, None
    for name, (s, e) in HOLIDAYS.get(year, {}).items():
        hs, he = _parse(s), _parse(e)
        if hs >= b_cur_start and he <= b_cur_end:
            hol_cur_start, hol_cur_end = hs, he
            break
    hol_ref_start, hol_ref_end = None, None
    for name, (s, e) in HOLIDAYS.get(ref_year, {}).items():
        hs, he = _parse(s), _parse(e)
        if hs >= b_ref_start and he <= b_ref_end:
            hol_ref_start, hol_ref_end = hs, he
            break

    has_both_holidays = (hol_cur_start and hol_ref_start)

    if has_both_holidays:
        if hol_cur_start <= d <= hol_cur_end:
            offset = (d - hol_cur_start).days
            hol_ref_len = (hol_ref_end - hol_ref_start).days
            clamped_offset = min(offset, hol_ref_len)
            candidate = hol_ref_start + timedelta(days=clamped_offset)
        elif d < hol_cur_start:
            pre_cur_len = (hol_cur_start - b_cur_start).days
            pre_ref_len = (hol_ref_start - b_ref_start).days
            offset = (d - b_cur_start).days
            if pre_cur_len > 0:
                ratio = offset / pre_cur_len
                mapped_offset = round(ratio * pre_ref_len)
            else:
                mapped_offset = offset
            candidate = b_ref_start + timedelta(days=mapped_offset)
            if candidate >= hol_ref_start:
                candidate = hol_ref_start - timedelta(days=1)
        else:
            post_cur_len = (b_cur_end - hol_cur_end).days
            post_ref_len = (b_ref_end - hol_ref_end).days
            offset = (d - hol_cur_end).days
            if post_cur_len > 0:
                ratio = offset / post_cur_len
                mapped_offset = round(ratio * post_ref_len)
            else:
                mapped_offset = offset
            candidate = hol_ref_end + timedelta(days=mapped_offset)
            if candidate <= hol_ref_end:
                candidate = hol_ref_end + timedelta(days=1)
    else:
        offset = (d - b_cur_start).days
        candidate = b_ref_start + timedelta(days=offset)

    if candidate > b_ref_end:
        candidate = b_ref_end
    if candidate < b_ref_start:
        candidate = b_ref_start

    d_weekend = _is_weekend(d)
    if _is_weekend(candidate) == d_weekend:
        return candidate

    for delta in range(1, 4):
        for sign in [1, -1]:
            alt = candidate + timedelta(days=delta * sign)
            if b_ref_start <= alt <= b_ref_end and _is_weekend(alt) == d_weekend:
                if has_both_holidays and d < hol_cur_start and alt >= hol_ref_start:
                    continue
                if has_both_holidays and d > hol_cur_end and alt <= hol_ref_end:
                    continue
                return alt

    return candidate


def build_align_map(dates):
    """批量对齐：[date] → {date: aligned_date}"""
    return {d: align_date(d) for d in dates}


def get_25_date_range(align_map):
    """从对齐映射中提取需要查询的25年日期范围"""
    ref_dates = list(align_map.values())
    if not ref_dates:
        return None, None
    return min(ref_dates), max(ref_dates)
