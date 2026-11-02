import json
from datetime import datetime
from decimal import Decimal as D

import check
import size
import th_market as tm

NOW = datetime(2026, 11, 2, 11, 0)


def write_prices(path, price="42.50", prev_close="42.00", at="2026-11-02T10:50:00+07:00"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"created_at": at, "prices": {"PTT": {
        "price": price, "prev_close": prev_close, "time": at, "source": "yahoo",
        "delay_note": "data ~15 min behind"}}}), encoding="utf-8")


def test_read_prices_fresh_and_stale(tmp_path):
    p = tmp_path / "prices.json"
    write_prices(p)  # 10:50, ten minutes before NOW
    got = tm.read_prices(str(p), NOW)
    assert got["PTT"]["price"] == D("42.50") and got["PTT"]["prev_close"] == D("42.00")
    assert got["PTT"]["time"] == datetime(2026, 11, 2, 10, 50)
    assert tm.read_prices(str(p), datetime(2026, 11, 2, 11, 10)) == {}  # twenty minutes old


def test_read_prices_missing_file(tmp_path):
    assert tm.read_prices(str(tmp_path / "nope.json"), NOW) == {}


def plan_file(tmp_path):
    d = tmp_path / "th-trader"
    d.mkdir(exist_ok=True)
    (d / "plan.json").write_text(json.dumps({"symbol": "PTT", "mode": "intraday", "date": "2026-11-02",
                                             "entry": "42.25", "stop": "41.75", "target": "44.25",
                                             "flat_by": "16:20"}), encoding="utf-8")


def test_size_uses_a_fresh_price(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    plan_file(tmp_path)
    write_prices(tmp_path / "th-trader" / "prices.json")
    assert size.main(["--now", "2026-11-02 11:00"]) == 0
    out = capsys.readouterr().out
    assert "Sized at 42.50" in out and "the latest price from prices.json (10:50)" in out


def test_size_ignores_a_stale_price(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    plan_file(tmp_path)
    write_prices(tmp_path / "th-trader" / "prices.json", at="2026-11-02T10:30:00+07:00")
    assert size.main(["--now", "2026-11-02 11:00"]) == 0
    out = capsys.readouterr().out
    assert "prices.json is 30 minutes old: not used" in out
    assert "Sized at 42.25" in out


def test_risk_takes_prev_close_from_prices(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "th-trader"
    d.mkdir()
    write_prices(d / "prices.json", at="2026-11-02T09:00:00+07:00")  # old is fine for a previous close
    (d / "orders.json").write_text(json.dumps({"orders": [{"symbol": "PTT", "side": "buy", "qty": 100,
                                                           "type": "limit", "limit_price": "55.00",
                                                           "stop": "54.00", "target": "57.00"}]}),
                                   encoding="utf-8")
    assert check.main(["--now", "2026-11-02 11:00", "check", "--json", "--no-write"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res[0]["verdict"] == "BLOCK" and any("ceiling" in r for r in res[0]["reasons"])
