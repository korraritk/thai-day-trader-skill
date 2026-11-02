from datetime import date, datetime, time

import pytest

import th_market as tm


def test_bangkok_is_utc_plus_7():
    assert tm.ts_to_bkk(1793588400) == datetime(2026, 11, 2, 10, 0)


def test_parse_now_bangkok_and_utc():
    assert tm.parse_now("2026-10-30 10:20") == datetime(2026, 10, 30, 10, 20)
    assert tm.parse_now("2026-10-30T03:20:00Z") == datetime(2026, 10, 30, 10, 20)


def test_trading_day_weekend_and_holiday():
    assert tm.is_trading_day(date(2026, 10, 30))
    assert not tm.is_trading_day(date(2026, 10, 31))  # Saturday
    assert not tm.is_trading_day(date(2026, 1, 2))  # วันหยุดพิเศษ in the seed
    assert tm.holiday_name(date(2026, 1, 1)) == "วันขึ้นปีใหม่"


def test_past_last_holiday_year_stops():
    with pytest.raises(tm.MarketError, match="2028"):
        tm.is_trading_day(date(2028, 1, 4))


def test_sessions_and_slots():
    d = date(2026, 10, 30)
    assert tm.sessions(d) == [(time(10, 0), time(12, 30)), (time(14, 0), time(16, 30))]
    s = tm.slots(d, 5)
    assert len(s) == 60
    assert s[0] == datetime(2026, 10, 30, 10, 0)
    assert s[29] == datetime(2026, 10, 30, 12, 25)
    assert s[30] == datetime(2026, 10, 30, 14, 0)
    assert tm.sessions(date(2026, 10, 31)) == [] and tm.slots(date(2026, 10, 31)) == []
