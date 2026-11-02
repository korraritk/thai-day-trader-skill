import importlib
import json
import os
import shutil

import entry
from conftest import FIXTURES

exit_ = importlib.import_module("exit")
CHART = os.path.join(FIXTURES, "yahoo", "chart_PTT_5m_60d.json")


def run(capsys, *args):
    rc = entry.main(list(args))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_check_writes_entry_json_that_exit_reads(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, out, err = run(capsys, "check", "PTT", "--offline", CHART, "--now", "2026-10-29 11:30", "--write",
                       "--no-telegram")
    assert rc == 0, err
    assert "TRIGGERED" in out and "data to 11:30 Bangkok (0 min behind)" in out
    doc = json.loads((tmp_path / "th-trader" / "entry.json").read_text(encoding="utf-8"))
    assert doc["triggered"] is True and doc["price"] == "42.25" and doc["at"] == "11:10"
    assert doc["setups"] == ["orb"] and doc["or_minutes"] == 15 and doc["date"] == "2026-10-29"
    for k in ("data_to", "behind_minutes", "trigger_age_minutes", "created_at", "or_high", "or_low", "symbol"):
        assert k in doc, k
    a = exit_.main.__globals__["argparse"].Namespace(entry=None, symbol="PTT", watchlist="nope.json",
                                                     entry_file="th-trader/entry.json", mode=None, or_minutes=15)
    got = exit_.gather(a, entry.tm.parse_now("2026-10-29 11:30"))
    assert str(got["entry"]) == "42.25" and got["entry_time"] == "11:10" and got["mode"] == "intraday"


def test_watch_replays_to_the_trigger(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, out, err = run(capsys, "watch", "PTT", "--offline", CHART, "--now", "2026-10-29 10:20", "--no-telegram")
    assert rc == 0, err
    lines = out.splitlines()
    assert any(l.startswith("10:15 bar") for l in lines) and any(l.startswith("11:05 bar") for l in lines)
    assert not any(l.startswith("11:10 bar") for l in lines)
    assert "TRIGGERED" in out and (tmp_path / "th-trader" / "entry.json").exists()


def test_watch_without_a_trigger_expires(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, out, _ = run(capsys, "watch", "PTT", "--offline", CHART, "--now", "2026-10-30 10:20", "--no-telegram")
    assert rc == 1 and "EXPIRED" in out
    assert not (tmp_path / "th-trader" / "entry.json").exists()


def test_watchlist_prints_a_line_per_hit(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    off = tmp_path / "charts"
    off.mkdir()
    shutil.copy(CHART, off / "PTT.json")
    shutil.copy(os.path.join(FIXTURES, "yahoo", "chart_SINGER_5m_60d.json"), off / "SINGER.json")
    wl = tmp_path / "wl.json"
    wl.write_text(json.dumps({"hits": [{"symbol": "PTT"}, {"symbol": "SINGER"}]}), encoding="utf-8")
    rc, out, err = run(capsys, "watchlist", "--watchlist", str(wl), "--offline", str(off),
                       "--now", "2026-10-29 11:30", "--no-telegram")
    rows = [l for l in out.splitlines() if l.startswith(("PTT ", "SINGER "))]
    assert len(rows) == 2, out + err
    assert rc == 0 and "triggered" in rows[0]


def test_a_stale_trigger_says_how_old(capsys):
    rc, out, _ = run(capsys, "check", "PTT", "--offline", CHART, "--now", "2026-10-29 11:40", "--no-telegram")
    assert rc == 0 and "triggered 30 minutes ago" in out


def test_watch_every_has_a_floor(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, out, _ = run(capsys, "watch", "PTT", "--offline", CHART, "--now", "2026-10-29 10:20", "--every", "10",
                    "--no-telegram")
    assert "--every 10 is under 30 seconds: using 30" in out


def test_check_sends_telegram_once_per_trigger(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sent = []
    monkeypatch.setattr(entry.notify, "send_telegram", lambda text: (sent.append(text) or (True, "sent")))
    for _ in range(2):
        assert run(capsys, "check", "PTT", "--offline", CHART, "--now", "2026-10-29 11:30", "--write")[0] == 0
    assert len(sent) == 1 and "PTT" in sent[0] and "42.25" in sent[0]


def test_watch_survives_a_failed_read(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    real, calls = entry.load_series, []

    def flaky(sym, day, offline=None):
        calls.append(1)
        if len(calls) in (2, 3):
            raise entry.tm.FetchError("Yahoo answered HTTP 503")
        return real(sym, day, offline)
    monkeypatch.setattr(entry, "load_series", flaky)
    rc, out, _ = run(capsys, "watch", "PTT", "--offline", CHART, "--now", "2026-10-29 10:20", "--no-telegram")
    assert rc == 0 and "could not read bars: Yahoo answered HTTP 503" in out


def test_watch_stops_after_five_failed_reads(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def down(sym, day, offline=None):
        raise entry.tm.FetchError("Yahoo answered HTTP 503")
    monkeypatch.setattr(entry, "load_series", down)
    rc, out, _ = run(capsys, "watch", "PTT", "--offline", CHART, "--now", "2026-10-29 10:20", "--no-telegram")
    assert rc == 1 and "Stopping after 5 failed reads in a row" in out
