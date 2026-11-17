import calendar
from datetime import date, datetime

import scan
import th_market as tm

DAY = date(2026, 10, 30)
EARLIER = [date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29)]
BREAK_BAR = datetime(2026, 10, 30, 10, 20)  # the bar that closed above the range, 10:20-10:25


def series(days_volume, close=10.0, high=10.5, low=9.5):
    """Every 5-minute bar of each day, flat prices, the given volume per bar."""
    t, o, h, l, c, v = [], [], [], [], [], []
    for d, vol in days_volume:
        for start in tm.slots(d, 5):
            t.append(calendar.timegm((start - tm.BKK_OFFSET).timetuple()))
            o.append(close), h.append(high), l.append(low), c.append(close), v.append(vol)
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "meta": {}}


def test_relative_volume_compares_today_so_far_with_the_same_window_before():
    s = series([(d, 100) for d in EARLIER] + [(DAY, 300)])
    assert scan.relative_volume(s, DAY, BREAK_BAR) == 3.0


def test_relative_volume_needs_two_earlier_days():
    assert scan.relative_volume(series([(EARLIER[-1], 100), (DAY, 300)]), DAY, BREAK_BAR) is None
    assert scan.relative_volume(series([(DAY, 300)]), DAY, BREAK_BAR) is None


def test_vwap_pct_measures_the_break_price_against_the_day_average():
    bars = tm.intraday_bars(series([(DAY, 100)]), DAY, datetime(2026, 10, 30, 10, 25))
    # typical price (10.5 + 9.5 + 10) / 3 = 10 on every bar, so a break at 10.20 is 2% over
    assert scan.vwap_pct(bars, 10.2) == 2.0
    assert scan.vwap_pct([dict(b, v=0) for b in bars], 10.2) is None


def test_index_change_pct_reads_the_index_at_the_break_bar():
    s = series([(EARLIER[-1], 1)], close=1000.0)
    today = series([(DAY, 1)], close=1010.0)
    for k in ("t", "o", "h", "l", "c", "v"):
        s[k] = s[k] + today[k]
    assert scan.index_change_pct(s, DAY, BREAK_BAR) == 1.0
    assert scan.index_change_pct(None, DAY, BREAK_BAR) is None
    assert scan.index_change_pct(today, DAY, BREAK_BAR) is None  # no earlier close to compare with


def test_relative_volume_uses_the_median_of_ten_sessions_so_quiet_days_do_not_inflate_it():
    # 2026-11-16: RATCH broke at 10:15 after two quiet openings and showed 23.77 times
    earlier, d = [], DAY
    while len(earlier) < 12:
        d = date.fromordinal(d.toordinal() - 1)
        if tm.is_trading_day(d):
            earlier.append(d)
    quiet = earlier[:2]  # the two sessions just before
    s = series([(d, 1 if d in quiet else 100) for d in reversed(earlier)] + [(DAY, 300)])
    assert scan.relative_volume(s, DAY, BREAK_BAR) == 3.0
