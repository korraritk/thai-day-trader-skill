import os
from datetime import date, datetime

import pytest

import entry
import th_market as tm
from conftest import FIXTURES

D29, D30, D31 = date(2026, 10, 29), date(2026, 10, 30), date(2026, 10, 31)


@pytest.fixture(scope="module")
def series():
    with open(os.path.join(FIXTURES, "yahoo", "chart_PTT_5m_60d.json"), encoding="utf-8") as fh:
        return tm.parse_chart(fh.read(), "PTT")


def at(day, hhmm):
    h, m = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def test_orb_triggers_on_the_first_close_above(series):
    r = entry.evaluate(series, D29, at(D29, "11:30"))
    assert r["status"] == "triggered"
    assert r["at"] == "11:10" and r["bar_start"] == "11:05"
    assert float(r["price"]) == 42.25 and r["or_high"] == 42.0 and r["or_low"] == 41.5
    assert round(r["range_volume_avg"]) == 3063430 and round(r["trigger_volume_x"], 2) == 1.95


def test_a_wick_is_not_a_trigger(series):
    r = entry.evaluate(series, D29, at(D29, "11:05"))
    assert r["status"] == "waiting" and "no close above yet" in r["why"]


def test_min_volume_skips_a_thin_bar(series):
    r = entry.evaluate(series, D29, at(D29, "11:30"), min_volume_x=2.0)
    assert not (r["status"] == "triggered" and r["at"] == "11:10")


def test_level_setup(series):
    r = entry.evaluate(series, D29, at(D29, "11:30"), setup="level", level=42.0)
    assert r["status"] == "triggered" and r["at"] == "11:10"


def test_range_still_forming(series):
    r = entry.evaluate(series, D29, at(D29, "10:10"))
    assert r["status"] == "waiting" and "range still forming" in r["why"]


def test_not_open_yet(series):
    r = entry.evaluate(series, D29, at(D29, "09:30"))
    assert r["status"] == "waiting" and "not open yet" in r["why"]


def test_lunch_is_waiting_not_expired(series):
    r = entry.evaluate(series, D30, at(D30, "13:00"))
    assert r["status"] == "waiting" and "lunch break" in r["why"]


def test_expired_after_no_entries_after(series):
    r = entry.evaluate(series, D30, at(D30, "15:45"))
    assert r["status"] == "expired"


def test_closed_day_is_no_data(series):
    assert entry.evaluate(series, D31, at(D31, "11:00"))["status"] == "no-data"


def test_delay_fields(series):
    r = entry.evaluate(series, D29, at(D29, "11:30"))
    assert r["trigger_age_minutes"] == 20 and r["data_to"] == "11:30" and r["behind_minutes"] == 0


def test_level_setup_needs_a_level(series):
    with pytest.raises(entry.EntryError):
        entry.evaluate(series, D29, at(D29, "11:30"), setup="level")


def test_lunch_does_not_count_as_behind(series):
    r = entry.evaluate(series, D30, at(D30, "13:00"))
    assert r["data_to"] == "12:30" and r["behind_minutes"] == 0
