import json
from datetime import date
from decimal import Decimal as D

import pytest

import th_market as tm

OLD, NEW = date(2026, 11, 13), date(2026, 11, 16)


@pytest.mark.parametrize("price,tick", [("1.99", "0.01"), ("2.00", "0.02"), ("4.98", "0.02"), ("5.00", "0.05"),
                                        ("24.90", "0.10"), ("25.00", "0.25"), ("99.75", "0.25"),
                                        ("100.00", "0.50"), ("400", "2.00")])
def test_tick_bands(price, tick):
    assert tm.tick(D(price), OLD) == D(tick)


def test_rounding():
    assert tm.round_down_tick(D("24.97"), OLD) == D("24.90")
    assert tm.round_up_tick(D("24.91"), OLD) == D("25.00")
    assert tm.round_tick(D("42.13"), OLD) == D("42.25")
    assert tm.round_tick(D("42.12"), OLD) == D("42.00")


def test_price_limits_round_inward():
    assert tm.price_limits(D("42.00"), OLD) == (D("29.50"), D("54.50"))  # 29.4 -> up 29.50; 54.6 -> down 54.50


def test_missing_new_table_falls_back_and_says_so():
    assert tm.tick(D("12.00"), NEW) == D("0.10")
    assert tm.tick_fallback(NEW) and not tm.tick_fallback(OLD)
    assert tm.tick_table_label(NEW) == "2009 (fallback)" and tm.tick_table_label(OLD) == "2009"
    assert tm.TICK_FALLBACK_NOTE == ("SET's tick table from 2026-11-16 is not in tick-tables.json yet; "
                                     "prices were rounded with the 2009 table.")


def test_verified_new_table_is_used(tmp_path, monkeypatch):
    with open(tm.data_path("tick-tables.json"), encoding="utf-8") as fh:
        tables = json.load(fh)
    tables[1].update(status="verified", bands=[{"from": "0", "tick": "0.01"}, {"from": "5", "tick": "0.01"}])
    p = tmp_path / "tick-tables.json"
    p.write_text(json.dumps(tables), encoding="utf-8")
    monkeypatch.setattr(tm, "_TICK_TABLES", None)
    monkeypatch.setattr(tm, "_TICK_FILE", str(p))
    assert tm.tick(D("12.00"), NEW) == D("0.01") and not tm.tick_fallback(NEW)
    assert tm.tick_table_label(NEW) == "2026-11-16"


def test_board_lot():
    assert tm.board_lot() == 100 and tm.board_lot(50) == 50
    with pytest.raises(tm.MarketError):
        tm.board_lot(30)
