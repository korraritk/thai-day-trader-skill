import json
from datetime import datetime
from decimal import Decimal as D

import pytest

import check
import th_market as tm

NOW = datetime(2026, 11, 2, 11, 0)


def cfg(**kw):
    c = tm.load_config("nonexistent.json")
    c.update(kw)
    return c


def order(**kw):
    o = {"symbol": "PTT", "side": "buy", "qty": 700, "type": "limit", "limit_price": "42.25", "stop": "41.75",
         "target": "43.50"}
    o.update(kw)
    return o


def verdict(orders, trades=(), now=NOW, prev=None, **c):
    return check.check(orders if isinstance(orders, list) else [orders], cfg(**c), list(trades), now, prev)


def reasons(res, i=0):
    return " | ".join(res[i]["reasons"])


def test_a_good_order_passes():
    res = verdict(order())
    assert res[0]["verdict"] == "PASS", res


@pytest.mark.parametrize("o,cfgkw,word", [
    (order(stop=None), {}, "stop"),
    (order(stop="42.50"), {}, "stop"),
    (order(qty=800), {}, "max loss"),
    (order(target="43.25"), {}, "net R"),
    (order(limit_price="42.30"), {}, "tick"),
    (order(qty=750), {}, "lot"),
    (order(qty=1200, stop="42.00", target="43.25"), {"max_loss_per_trade_thb": 5000}, "position"),
    (order(), {"blocked": ["PTT"]}, "blocked"),
    (order(), {"budget_thb": 20000, "max_position_pct": 100}, "cash"),
])
def test_each_order_rule_blocks(o, cfgkw, word):
    res = verdict(o, **cfgkw)
    assert res[0]["verdict"] == "BLOCK" and word in reasons(res)


def test_price_outside_the_day_limits():
    res = verdict(order(limit_price="55.00", stop="54.00", target="57.00", qty=100), prev={"PTT": D("42.00")})
    assert res[0]["verdict"] == "BLOCK" and "ceiling" in reasons(res)


@pytest.mark.parametrize("hhmm,word", [("13:00", "lunch break"), ("15:45", "no_entries_after"),
                                       ("09:40", "not open"), ("16:25", "flat_by")])
def test_entry_window(hhmm, word):
    res = verdict(order(), now=datetime(2026, 11, 2, int(hhmm[:2]), int(hhmm[3:])))
    assert res[0]["verdict"] == "BLOCK" and word in reasons(res)


def test_closed_day_blocks():
    res = verdict(order(), now=datetime(2026, 10, 31, 11, 0))
    assert res[0]["verdict"] == "BLOCK" and "closed" in reasons(res)


def buy(sym="CPALL", qty=500, price="50.00", stop="49.00", at="10:30"):
    return {"side": "buy", "symbol": sym, "qty": qty, "price": price, "stop": stop, "at": at}


def test_trades_per_day_and_duplicate():
    trades = [buy("A"), buy("B"), buy("C")]
    assert "max_trades_per_day" in reasons(verdict(order(), trades, max_daily_loss_thb=100000))
    assert "already bought" in reasons(verdict(order(), [buy("PTT")], max_daily_loss_thb=100000))
    res = verdict([order(), order()])
    assert res[0]["verdict"] == "PASS" and "twice" in reasons(res, 1)


def test_daily_loss_counts_realized_open_and_new():
    trades = [buy("CPALL", 500, "50.00", "49.00"),
              {"side": "sell", "symbol": "CPALL", "qty": 500, "price": "49.00", "at": "10:50"},  # about -584 with fees
              buy("AOT", 300, "60.00", "59.00")]  # open risk about 300 + fees
    res = verdict(order(), trades)  # + about 449 for the new order: over 1,500? 584 + 339 + 449 = 1,372 -> pass
    assert res[0]["verdict"] == "PASS"
    res = verdict(order(), trades, max_daily_loss_thb=1200)
    assert res[0]["verdict"] == "BLOCK" and "daily loss" in reasons(res)


def test_a_sell_is_never_blocked_by_entry_rules():
    trades = [buy("PTT", 700, "42.25", "41.75")]
    res = verdict(order(side="sell", qty=700, limit_price="41.00", stop=None, target=None), trades,
                  now=datetime(2026, 11, 2, 13, 0), blocked=["PTT"])
    assert res[0]["verdict"] == "PASS" and res[0]["warnings"]


def test_a_sell_beyond_the_position_is_blocked():
    res = verdict(order(side="sell", qty=800, stop=None, target=None), [buy("PTT", 700, "42.25", "41.75")])
    assert res[0]["verdict"] == "BLOCK" and "short" in reasons(res)


def test_log_refuses_duplicates_and_naked_sells(tmp_path):
    path = str(tmp_path / "trades.json")
    check.log_trade(path, "buy", "PTT", 700, "42.25", stop="41.75", now=NOW)
    with pytest.raises(check.RiskError, match="duplicate"):
        check.log_trade(path, "buy", "PTT", 700, "42.25", stop="41.75", now=NOW)
    with pytest.raises(check.RiskError, match="no open position"):
        check.log_trade(path, "sell", "AOT", 100, "60.00", now=NOW)
    check.log_trade(path, "sell", "PTT", 700, "43.00", now=datetime(2026, 11, 2, 11, 30))
    doc = json.loads(open(path, encoding="utf-8").read())
    assert len(doc["2026-11-02"]) == 2


def test_today_summary():
    s = check.today_summary([buy("CPALL", 500, "50.00", "49.00"),
                             {"side": "sell", "symbol": "CPALL", "qty": 500, "price": "49.00", "at": "10:50"},
                             buy("AOT", 300, "60.00", "59.00")], cfg())
    assert s["realized_thb"] < -500 and [p["symbol"] for p in s["open"]] == ["AOT"]
