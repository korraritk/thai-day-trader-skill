import json
import os
from datetime import date, datetime, time

import pytest

import th_market as tm
from conftest import FIXTURES


def load(name):
    with open(os.path.join(FIXTURES, "yahoo", name), encoding="utf-8") as fh:
        return json.load(fh)


def series(name, sym):
    return tm.parse_spark(load(name), {"PTT.BK": "PTT", "CPALL.BK": "CPALL"})[sym]


def test_to_yahoo():
    assert tm.to_yahoo("ptt") == "PTT.BK" and tm.to_yahoo("PTT.BK") == "PTT.BK"
    assert tm.to_yahoo("BBL-R") == "BBL-R.BK"
    assert tm.yahoo_url_symbol("L&E") == "L%26E.BK"


def test_parse_spark_maps_back_and_reports_missing():
    data = tm.parse_spark(load("spark_PTT_CPALL_1d_3mo.json"),
                          {"PTT.BK": "PTT", "CPALL.BK": "CPALL", "L&E.BK": "L&E"})
    assert set(data) == {"PTT", "CPALL"}


def test_fetch_bars_one_bad_symbol_others_fetched(monkeypatch):
    seen = []

    def fake_get(path):
        seen.append(path)
        return json.dumps(load("spark_PTT_CPALL_1d_3mo.json"))

    monkeypatch.setattr(tm, "yahoo_get", fake_get)
    data, failed = tm.fetch_bars(["PTT", "CPALL", "NOPE"], "3mo", "1d", pause=0)
    assert set(data) == {"PTT", "CPALL"} and failed == {"NOPE": "no data from Yahoo"}
    assert "PTT.BK,CPALL.BK,NOPE.BK" in seen[0]


def test_fetch_bars_encodes_url_characters(monkeypatch):
    seen = []
    monkeypatch.setattr(tm, "yahoo_get", lambda path: seen.append(path) or '{"spark": {"result": []}}')
    data, failed = tm.fetch_bars(["L&E"], "3mo", "1d", pause=0)
    assert "L%26E.BK" in seen[0] and "L&E" in failed


def test_zero_fetched_is_a_failure():
    with pytest.raises(tm.FetchError, match="Fetched 0 of 3"):
        tm.require_fetched(0, 3, {"A": "HTTP 404"})
    tm.require_fetched(1, 3, {"A": "HTTP 404"})


def test_daily_bars_dated_in_bangkok():
    bars = tm.daily_bars(series("spark_PTT_CPALL_1d_3mo.json", "PTT"), datetime(2026, 11, 2, 9, 0))
    assert bars[-1]["date"] == date(2026, 10, 30)  # the US scanner labelled this bar 2026-10-29
    assert bars[-1]["c"] == pytest.approx(42.0)


def test_daily_bars_drop_today_until_after_close():
    s = {"t": [1793588400], "c": [41.75], "o": None, "h": None, "l": None, "v": None, "meta": {}}
    assert tm.daily_bars(s, datetime(2026, 11, 2, 16, 30)) == []
    assert tm.daily_bars(s, datetime(2026, 11, 2, 16, 40))[0]["date"] == date(2026, 11, 2)


def test_intraday_drops_lunch_and_keeps_grid():
    s = tm.parse_chart(load("chart_PTT_5m_5d.json"), "PTT")
    day = date(2026, 10, 30)
    bars = tm.intraday_bars(s, day, datetime(2026, 10, 30, 16, 30))
    starts = [b["start"].time() for b in bars]
    assert time(12, 30) not in starts and time(13, 55) not in starts
    assert starts[0] == time(10, 0) and starts[-1] == time(16, 25)
    assert all(b["start"] in tm.slots(day) for b in bars)
    cut = tm.intraday_bars(s, day, datetime(2026, 10, 30, 13, 0))
    assert cut[-1]["start"].time() == time(12, 25)
