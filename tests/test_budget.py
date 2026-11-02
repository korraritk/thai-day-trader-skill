from decimal import Decimal as D

import pytest

import size
import th_market as tm


def cfg(**kw):
    c = tm.load_config("nonexistent.json")
    c.update(kw)
    return c


def plan(entry="42.25", stop="41.75", target="43.50", **kw):  # net R 1.73 (2R gross is 1.34 net, under 1.5)
    p = {"symbol": "PTT", "mode": "intraday", "date": "2026-11-02", "entry": entry, "stop": stop,
         "target": target, "flat_by": "16:20"}
    p.update(kw)
    return p


def test_max_loss_decides():
    s = size.size_trade(plan(), cfg())
    assert s["qty"] == 700 and s["decided_by"] == "max loss"  # 500 / 0.6411 = 779 -> 700
    assert s["limits"] == {"max loss": 700, "position cap": 1100, "cash": 2100}


def test_position_cap_decides():
    s = size.size_trade(plan("339", "330", "357"), cfg(max_loss_per_trade_thb=50000))
    assert s["qty"] == 100 and s["decided_by"] == "position cap"  # 50,000 / 339 = 147 -> 100


def test_cash_decides():
    s = size.size_trade(plan(), cfg(cash_buffer_pct=60, max_loss_per_trade_thb=5000))
    assert s["qty"] == 900 and s["decided_by"] == "cash"  # 40,000 / 42.32 = 945 -> 900


def test_zero_lots_refuses_with_the_loss_of_one_lot():
    with pytest.raises(size.SizeError, match="1,012"):
        size.size_trade(plan("339", "330", "357"), cfg())


def test_lot_of_50():
    s = size.size_trade(plan("339", "330", "357"), cfg(max_loss_per_trade_thb=600), lot=50)
    assert s["qty"] == 50  # 600 / 10.12 = 59 -> 50


def test_price_above_entry():
    with pytest.raises(size.SizeError, match="net R"):
        size.size_trade(plan(target="43.25"), cfg(), price=D("42.50"))  # net R 0.68 at 42.50
    s = size.size_trade(plan(target="44.25"), cfg(), price=D("42.50"))
    assert s["entry_used"] == D("42.50") and s["qty"] == 500  # 500 / 0.8915 = 560 -> 500


def test_ticket_shape():
    p = plan()
    t = size.ticket(size.size_trade(p, cfg()), p)
    assert set(t) == {"symbol", "side", "qty", "type", "limit_price", "stop", "target", "flat_by",
                      "est_fees_round_trip", "max_loss_thb", "net_r", "decided_by", "plan_date"}
    assert t["side"] == "buy" and t["qty"] == 700 and t["limit_price"] == "42.25"


def test_cli_writes_orders(tmp_path, monkeypatch, capsys):
    import json
    monkeypatch.chdir(tmp_path)
    (tmp_path / "th-trader").mkdir()
    (tmp_path / "th-trader" / "plan.json").write_text(json.dumps(plan()), encoding="utf-8")
    assert size.main(["--write"]) == 0
    out = capsys.readouterr().out
    doc = json.loads((tmp_path / "th-trader" / "orders.json").read_text(encoding="utf-8"))
    assert doc["orders"][0]["qty"] == 700 and "Fees:" in out and "Nothing was placed" in out
