import calendar
import json
import os
from datetime import date, datetime, time

import pytest

import scan
import th_market as tm
from conftest import FIXTURES

CHART = os.path.join(FIXTURES, "yahoo", "chart_PTT_5m_5d.json")
DAY = date(2026, 10, 30)


def ptt_series():
    with open(CHART, encoding="utf-8") as fh:
        return tm.parse_chart(json.load(fh), "PTT")


def window(now, at=None, until=None):
    return scan.orb_window(now, at, 15, until, None)


def test_closed_days_say_so():
    with pytest.raises(scan.ScanError, match="market is closed"):
        window(datetime(2026, 10, 31, 11, 0))
    with pytest.raises(scan.ScanError, match="วันหยุดพิเศษ"):
        window(datetime(2026, 1, 2, 11, 0))


def test_range_not_complete_yet():
    with pytest.raises(scan.ScanError, match="not complete until 10:15"):
        window(datetime(2026, 10, 30, 10, 10))


def test_lunch_and_after_close_clamp():
    assert window(datetime(2026, 10, 30, 13, 0)) == (DAY, datetime(2026, 10, 30, 12, 30))
    assert window(datetime(2026, 10, 30, 17, 0)) == (DAY, datetime(2026, 10, 30, 16, 30))


def test_after_until_is_expired():
    with pytest.raises(scan.ScanError, match="15:30"):
        window(datetime(2026, 10, 30, 15, 45), until=time(15, 30))


def test_usable_needs_every_slot_but_not_lunch():
    cutoff = datetime(2026, 10, 30, 14, 30)
    bars = tm.intraday_bars(ptt_series(), DAY, cutoff)
    assert scan.usable(bars, DAY, cutoff, 3)
    assert not scan.usable(bars[:5] + bars[6:], DAY, cutoff, 3)


def test_missing_morning_bar_is_unusable_not_a_hit():
    s = ptt_series()
    keep = [i for i, t in enumerate(s["t"]) if tm.ts_to_bkk(t) != datetime(2026, 10, 30, 10, 30)]
    holed = dict(s, t=[s["t"][i] for i in keep], **{k: [s[k][i] for i in keep] for k in ("o", "h", "l", "c", "v")})
    hits, counts, _ = scan.scan_orb({"PTT": holed}, ["PTT"], DAY, datetime(2026, 10, 30, 11, 0), 15)
    assert hits == [] and counts["unusable"] == 1


def test_spark_gaps_in_stage_1_are_not_unusable():
    """Yahoo spark leaves out 5-minute bars whose close did not change; v8 has them all."""
    full = ptt_series()
    cutoff = datetime(2026, 10, 30, 11, 0)
    drop = {datetime(2026, 10, 30, 10, h) for h in (20, 25, 30, 50)}
    keep = [i for i, t in enumerate(full["t"]) if tm.ts_to_bkk(t) not in drop]
    spark = {"t": [full["t"][i] for i in keep], "c": [full["c"][i] for i in keep],
             "o": None, "h": None, "l": None, "v": None, "meta": {"regularMarketVolume": 1_000_000}}
    asked = []
    hits, counts, failed = scan.scan_orb({"PTT": spark}, ["PTT"], DAY, cutoff, 15,
                                         confirm=lambda sym: asked.append(sym) or full)
    assert counts["unusable"] == 0 and counts["scanned"] == 1
    assert failed == {}


def test_latest_bar_end_uses_the_last_trade_time_when_spark_skips_flat_bars():
    """Spark leaves out the last bars when the close did not change; meta still has the last trade."""
    full = ptt_series()
    keep = [i for i, t in enumerate(full["t"]) if tm.ts_to_bkk(t) <= datetime(2026, 10, 30, 10, 30)]
    last_trade = calendar.timegm(datetime(2026, 10, 30, 3, 47, 22).timetuple())  # 10:47:22 Bangkok
    spark = {"t": [full["t"][i] for i in keep], "c": [full["c"][i] for i in keep], "o": None, "h": None,
             "l": None, "v": None, "meta": {"regularMarketTime": last_trade}}
    end = scan.latest_bar_end({"PTT": spark}, ["PTT"], DAY, datetime(2026, 10, 30, 11, 0))
    assert end == datetime(2026, 10, 30, 10, 45)


def test_stale_minutes():
    assert scan.stale_minutes(datetime(2026, 10, 30, 10, 30), datetime(2026, 10, 30, 11, 15)) == 45


def test_orb_offline_replay_json(capsys):
    rc = scan.main(["orb", "--offline", CHART, "--now", "2026-10-30 11:00", "--no-write", "--json"])
    out = capsys.readouterr()
    assert rc in (0, 1) and "Traceback" not in out.err
    doc = json.loads(out.out)
    assert doc["as_of"] == "2026-10-30 11:00 Bangkok" and doc["currency"] == "THB"


def test_stale_data_warns_and_cuts_at_the_latest_bar(tmp_path, capsys):
    with open(CHART, encoding="utf-8") as fh:
        doc = json.load(fh)
    res = doc["chart"]["result"][0]
    keep = [i for i, t in enumerate(res["timestamp"]) if tm.ts_to_bkk(t) <= datetime(2026, 10, 30, 10, 30)]
    res["timestamp"] = [res["timestamp"][i] for i in keep]
    q = res["indicators"]["quote"][0]
    for k in list(q):
        q[k] = [q[k][i] for i in keep]
    f = tmp_path / "PTT.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    scan.main(["orb", "--offline", str(f), "--now", "2026-10-30 11:15", "--no-write"])
    out = capsys.readouterr().out
    assert "Warning: Yahoo's latest bar ended 10:35, 40 minutes ago" in out
    assert "10:35 Bangkok" in out
