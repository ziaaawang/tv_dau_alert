"""
TV端 DAU 预估 — 日历配置 + 日期对齐
定义节假日/调休/寒暑假，提供26→25日期对齐函数
"""
from datetime import date, timedelta, datetime

# ═══════════════════════════════════════════
# 节假日（放假区间，含调休连休）
# ═══════════════════════════════════════════
HOLIDAYS = {
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
    2025: {'20250126', '20250208', '20250427', '20250928', '20251011'},
    2026: {'20260104', '20260214', '20260228', '20260509', '20260920', '20261010'},
}

# ═══════════════════════════════════════════
# 寒暑假
# ═══════════════════════════════════════════
WINTER_BREAK = {
    2025: ('20250111', '20250216'),
    2026: ('20260117', '20260307'),
}

SUMMER_BREAK = {
    2025: ('20250701', '20250831'),
    2026: ('20260701', '20260831'),
}

# ═══════════════════════════════════════════
# 春节农历标注
# ═══════════════════════════════════════════
SPRING_FESTIVAL_LUNAR = {
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


def align_date(d26):
    """将2026年的日期对齐到2025年的同性质日期。
    优先级：农历对齐 > 节假日 > 寒暑假 > work stretch position 匹配
    """
    # P0: 春节前后农历对齐（除夕±15天，按农历标注匹配）
    lunar_label = _get_lunar_label(d26, 2026)
    if lunar_label:
        labels_25 = SPRING_FESTIVAL_LUNAR.get(2025, {})
        for ds, lbl in labels_25.items():
            if lbl == lunar_label:
                return _parse(ds)

    # P1: 节假日对齐（同名节假日内按 offset）
    hol_info = _get_holiday_info(d26, 2026)
    if hol_info:
        name, offset = hol_info
        ref_holidays = HOLIDAYS.get(2025, {})
        # 名称匹配：精确 → 子串模糊
        ref_name = None
        if name in ref_holidays:
            ref_name = name
        else:
            for rn in ref_holidays:
                if name in rn or rn in name:
                    ref_name = rn
                    break
        if ref_name:
            ref_start = _parse(ref_holidays[ref_name][0])
            ref_end = _parse(ref_holidays[ref_name][1])
            # 国庆单独拆分时，从参考假期末尾倒推对齐
            if name == '国庆' and name != ref_name:
                ref_len = (ref_end - ref_start).days
                hol_len = (_parse(HOLIDAYS[2026][name][1]) - _parse(HOLIDAYS[2026][name][0])).days
                candidate = ref_end - timedelta(days=hol_len - offset)
            else:
                candidate = ref_start + timedelta(days=offset)
            if candidate > ref_end:
                return ref_end
            if candidate < ref_start:
                return ref_start
            return candidate
        return _iso_week_align(d26)

    # P2: 寒暑假对齐
    break_type = _in_break(d26, 2026)
    if break_type:
        return _break_align(d26, break_type)

    # P3: 按 work stretch position 对齐
    iso_candidate = _iso_week_align(d26)
    candidate = iso_candidate
    is_workday_26 = _is_actual_workday(d26, 2026)

    if is_workday_26:
        pos26 = _work_stretch_position(d26, 2026)
        pos25 = _work_stretch_position(candidate, 2025)
        if not _positions_match(pos26, pos25):
            candidate = _find_position_match(candidate, pos26, 2025, is_workday=True)
    else:
        pos26 = _rest_stretch_position(d26, 2026)
        pos25 = _rest_stretch_position(candidate, 2025)
        if pos25 is None or pos26[0] != pos25[0]:
            candidate = _find_position_match(candidate, pos26, 2025, is_workday=False)

    # P4: 26年普通日不应对齐到25年节假日影响日（假日/调休/假期前最后工作日）
    if _is_holiday_affected(candidate, 2025) and not _is_holiday_affected(d26, 2026):
        candidate = _find_nearest_normal_same_dow(iso_candidate, 2025)

    return candidate


def _iso_week_align(d26):
    """用ISO周+星期几找25年对应日期"""
    iso = d26.isocalendar()
    iso_year, iso_week, iso_day = iso[0], iso[1], iso[2]
    try:
        return date.fromisocalendar(iso_year - 1, iso_week, iso_day)
    except ValueError:
        return d26 - timedelta(days=364)



def _break_align(d26, break_type):
    """寒暑假对齐：将假期拆为 节前/节中/节后 三段分别按比例对齐，
    避免普通寒假日对齐到春节假期。"""
    breaks = WINTER_BREAK if break_type == 'winter' else SUMMER_BREAK
    b26_start = _parse(breaks[2026][0])
    b26_end = _parse(breaks[2026][1])
    b25_start = _parse(breaks[2025][0])
    b25_end = _parse(breaks[2025][1])

    # 找26年和25年寒/暑假内的节假日段
    hol26_start, hol26_end = None, None
    for name, (s, e) in HOLIDAYS.get(2026, {}).items():
        hs, he = _parse(s), _parse(e)
        if hs >= b26_start and he <= b26_end:
            hol26_start, hol26_end = hs, he
            break
    hol25_start, hol25_end = None, None
    for name, (s, e) in HOLIDAYS.get(2025, {}).items():
        hs, he = _parse(s), _parse(e)
        if hs >= b25_start and he <= b25_end:
            hol25_start, hol25_end = hs, he
            break

    has_both_holidays = (hol26_start and hol25_start)

    if has_both_holidays:
        # 判断d26落在哪个段
        if hol26_start <= d26 <= hol26_end:
            # 节假日段：按 align_date 的 P1 逻辑已处理，这里做 offset 对齐
            offset = (d26 - hol26_start).days
            hol25_len = (hol25_end - hol25_start).days
            clamped_offset = min(offset, hol25_len)
            candidate = hol25_start + timedelta(days=clamped_offset)
        elif d26 < hol26_start:
            # 节前段：按比例对齐到25年节前段
            pre26_len = (hol26_start - b26_start).days
            pre25_len = (hol25_start - b25_start).days
            offset = (d26 - b26_start).days
            if pre26_len > 0:
                ratio = offset / pre26_len
                mapped_offset = round(ratio * pre25_len)
            else:
                mapped_offset = offset
            candidate = b25_start + timedelta(days=mapped_offset)
            if candidate >= hol25_start:
                candidate = hol25_start - timedelta(days=1)
        else:
            # 节后段：按比例对齐到25年节后段
            post26_len = (b26_end - hol26_end).days
            post25_len = (b25_end - hol25_end).days
            offset = (d26 - hol26_end).days
            if post26_len > 0:
                ratio = offset / post26_len
                mapped_offset = round(ratio * post25_len)
            else:
                mapped_offset = offset
            candidate = hol25_end + timedelta(days=mapped_offset)
            if candidate <= hol25_end:
                candidate = hol25_end + timedelta(days=1)
    else:
        # 无法识别假期段，退回简单偏移
        offset = (d26 - b26_start).days
        candidate = b25_start + timedelta(days=offset)

    # clamp 到25年假期范围
    if candidate > b25_end:
        candidate = b25_end
    if candidate < b25_start:
        candidate = b25_start

    # 确保周中/周末性质一致
    d26_weekend = _is_weekend(d26)
    if _is_weekend(candidate) == d26_weekend:
        return candidate

    for delta in range(1, 4):
        for sign in [1, -1]:
            alt = candidate + timedelta(days=delta * sign)
            if b25_start <= alt <= b25_end and _is_weekend(alt) == d26_weekend:
                # 节前段不能越过节假日
                if has_both_holidays and d26 < hol26_start and alt >= hol25_start:
                    continue
                # 节后段不能越过节假日
                if has_both_holidays and d26 > hol26_end and alt <= hol25_end:
                    continue
                return alt

    return candidate


def build_align_map(dates_26):
    """批量对齐：[date] → {date_26: date_25}"""
    return {d: align_date(d) for d in dates_26}


def get_25_date_range(align_map):
    """从对齐映射中提取需要查询的25年日期范围"""
    ref_dates = list(align_map.values())
    if not ref_dates:
        return None, None
    return min(ref_dates), max(ref_dates)
