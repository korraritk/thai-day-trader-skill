import calendar
import json
import os
from datetime import date, datetime, timedelta

import pytest

import scan
import th_market as tm
from conftest import FIXTURES


def trading_days(end, n):
    out, d = [], end
    while len(out) < n:
        if tm.is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def make_series(closes, volumes=None, end=date(2026, 10, 30)):
    days = trading_days(end, len(closes))
    ts = [calendar.timegm(datetime(d.year, d.month, d.day, 3, 0).timetuple()) for d in days]  # 10:00 Bangkok
    return {"t": ts, "c": list(closes), "o": None, "h": None, "l": None,
            "v": list(volumes) if volumes else None, "meta": {}}


def tags(closes):
    return scan.find_setups(closes)[0]


def test_each_original_setup():
    assert "breakout_20d" in tags([10.0] * 29 + [11.0])
    assert "bull_flag" in tags([10.0] * 14 + [10.0 + 0.2 * i for i in range(11)] + [12.0, 12.1, 12.0, 12.1, 12.0])
    assert "reclaim_ma20" in tags([10.0] * 28 + [9.0, 11.0])
    falling = [20.0 - 0.3 * i for i in range(30)]
    assert "oversold" in tags(falling)
    assert "oversold_bounce" in tags(falling[:-1] + [falling[-2] + 0.5])


def test_at_ceiling():
    assert scan.at_ceiling(42.0, 54.5, date(2026, 10, 30))
    assert not scan.at_ceiling(42.0, 54.25, date(2026, 10, 30))


def test_value_filter_drops_thin_stocks():
    closes = [10.0] * 29 + [11.0]
    data = {"BIG": make_series(closes, [10_000_000] * 30), "THIN": make_series(closes, [1_000_000] * 30)}
    hits, counts, last = scan.scan_daily(data, ["BIG", "THIN"], datetime(2026, 11, 2, 9, 0), 1.0, 50_000_000, 0)
    assert [h["symbol"] for h in hits] == ["BIG"]
    assert hits[0]["value20"] == pytest.approx(100_500_000, rel=0.01)
    assert counts["illiquid"] == 1 and last == date(2026, 10, 30)


def test_ceiling_close_is_tagged():
    closes = [42.0] * 29 + [54.5]
    hits, _, _ = scan.scan_daily({"X": make_series(closes, [5_000_000] * 30)}, ["X"],
                                 datetime(2026, 11, 2, 9, 0), 1.0, 0, 0)
    assert "at_ceiling" in hits[0]["setups"]


def test_matches_without_volume_are_confirmed_with_the_daily_chart():
    closes = [10.0] * 29 + [11.0]
    asked = []

    def confirm(sym):
        asked.append(sym)
        return make_series(closes, [10_000_000] * 30)

    hits, _, _ = scan.scan_daily({"A": make_series(closes)}, ["A"], datetime(2026, 11, 2, 9, 0),
                                 1.0, 50_000_000, 0, confirm)
    assert asked == ["A"] and hits[0]["symbol"] == "A"


def test_daily_offline_json(capsys):
    f = os.path.join(FIXTURES, "yahoo", "spark_PTT_CPALL_1d_3mo.json")
    rc = scan.main(["daily", "--offline", f, "--now", "2026-11-02 09:00", "--no-write", "--json", "--min-value", "0"])
    doc = json.loads(capsys.readouterr().out)
    assert rc in (0, 1)
    assert doc["currency"] == "THB" and doc["as_of"] == "2026-10-30 close" and doc["tick_table"] == "2009"


def test_zero_fetched_fails_plainly(monkeypatch, capsys):
    monkeypatch.setattr(tm, "fetch_bars", lambda *a, **k: ({}, {"NOPE": "no data from Yahoo"}))
    assert scan.main(["daily", "--symbols", "NOPE", "--no-write"]) == 1
    err = capsys.readouterr().err
    assert "Fetched 0 of 1" in err and "Traceback" not in err


def test_universe_writes_symbols(tmp_path):
    out = tmp_path / "universe.txt"
    f = os.path.join(FIXTURES, "universe", "listed.html.xls")
    assert scan.main(["universe", "--file", f, "--out", str(out)]) == 0
    lines = [ln for ln in out.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]
    assert lines == ["PTT|SET", "AU|mai", "L&E|SET"]
