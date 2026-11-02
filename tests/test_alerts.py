import json
import os
import shutil
from datetime import datetime
from decimal import Decimal as D

import pytest

import alerts
import th_market as tm
from conftest import FIXTURES

PLAN = {"symbol": "PTT", "mode": "intraday", "date": "2026-11-02", "entry": "42.25", "stop": "41.75",
        "target": "43.50", "flat_by": "16:20"}


def q(price, prev="42.00", at=datetime(2026, 11, 2, 10, 50)):
    return {"PTT": {"price": D(price), "prev_close": D(prev), "time": at}}


NOW = datetime(2026, 11, 2, 11, 5)


@pytest.mark.parametrize("hhmm,state", [("09:00", "before open"), ("11:00", "open"), ("13:00", "lunch"),
                                        ("17:00", "after close")])
def test_clock_status(hhmm, state):
    h, m = map(int, hhmm.split(":"))
    st = alerts.clock_status(datetime(2026, 11, 2, h, m))
    assert st["state"] == state and st["holiday"] is None and st["next"]


def test_clock_on_a_holiday():
    st = alerts.clock_status(datetime(2026, 1, 2, 11, 0))
    assert st["state"] == "closed" and st["holiday"] == "วันหยุดพิเศษ" and "2026-01-05" in st["next"]


def test_rules_from_plan():
    rules = alerts.rules_from_plan(PLAN)
    kinds = {(r["kind"], r.get("price") or r.get("at")) for r in rules}
    assert kinds == {("below", "41.75"), ("above", "43.50"), ("time", "16:20")}
    assert all(r["date"] == "2026-11-02" for r in rules)
    assert next(r for r in rules if r["kind"] == "time")["label"] == "flat by"


def test_stop_rings_once():
    rules = alerts.rules_from_plan(PLAN)
    rung, rules = alerts.evaluate(rules, q("41.70"), NOW)
    assert len(rung) == 1 and rung[0]["kind"] == "below"
    msg = rung[0]["message"]
    assert "PTT 41.70 hit the stop 41.75 at 10:50" in msg and "(data ~15 min behind)" in msg
    rung2, _ = alerts.evaluate(rules, q("41.70"), NOW)
    assert rung2 == []


def test_rearm_rings_again_after_crossing_back():
    rules = [alerts.make_rule("below", "PTT", price="41.75", rearm=True)]
    rung, rules = alerts.evaluate(rules, q("41.70"), NOW)
    assert len(rung) == 1
    rung, rules = alerts.evaluate(rules, q("42.00"), NOW)
    assert rung == []
    rung, rules = alerts.evaluate(rules, q("41.70"), NOW)
    assert len(rung) == 1


def test_pct_move_once_a_day():
    rules = [alerts.make_rule("pct_move", "PTT", pct=3)]
    rung, rules = alerts.evaluate(rules, q("43.30"), NOW)  # +3.10%
    assert len(rung) == 1 and "+3.10%" in rung[0]["message"]
    assert alerts.evaluate(rules, q("43.50"), NOW)[0] == []
    rung, _ = alerts.evaluate(rules, q("43.50", at=datetime(2026, 11, 3, 10, 50)), datetime(2026, 11, 3, 11, 5))
    assert len(rung) == 1


def test_pct_move_direction():
    rules = [alerts.make_rule("pct_move", "PTT", pct=3, direction="down")]
    assert alerts.evaluate(rules, q("43.30"), NOW)[0] == []


def test_time_rule_once_per_trading_day():
    rules = [alerts.make_rule("time", at="16:20", label="flat by")]
    assert alerts.evaluate(rules, {}, datetime(2026, 11, 2, 16, 19))[0] == []
    rung, rules = alerts.evaluate(rules, {}, datetime(2026, 11, 2, 16, 21))
    assert len(rung) == 1 and "flat by" in rung[0]["message"].lower()
    assert alerts.evaluate(rules, {}, datetime(2026, 11, 2, 16, 25))[0] == []
    assert alerts.evaluate(rules, {}, datetime(2026, 11, 7, 16, 21))[0] == []  # Saturday


def test_opening_bell():
    rules = [alerts.make_rule("opening_bell")]
    assert alerts.evaluate(rules, {}, datetime(2026, 11, 2, 9, 59))[0] == []
    rung, rules = alerts.evaluate(rules, {}, datetime(2026, 11, 2, 10, 0))
    assert len(rung) == 1
    assert alerts.evaluate(rules, {}, datetime(2026, 11, 2, 10, 5))[0] == []


def test_dated_rule_only_on_its_date():
    rules = [alerts.make_rule("below", "PTT", price="41.75", date="2026-11-03")]
    assert alerts.evaluate(rules, q("41.70"), NOW)[0] == []


def offline_dir(tmp_path):
    d = tmp_path / "quotes"
    d.mkdir()
    shutil.copy(os.path.join(FIXTURES, "yahoo", "chart_PTT_5m_5d.json"), d / "PTT.json")
    return d


def test_check_with_a_failing_telegram_still_marks_the_ring(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(alerts.notify, "send_telegram", lambda text: (False, "Telegram answered HTTP 401"))
    off = offline_dir(tmp_path)  # PTT last 42.00 on 2026-10-30
    assert alerts.main(["add", "--symbol", "PTT", "--above", "41.90"]) == 0
    assert alerts.main(["check", "--offline", str(off), "--now", "2026-10-30 16:45"]) == 0
    out = capsys.readouterr().out
    assert "PTT 42.00 hit the alert level 41.90" in out and "Telegram not sent: Telegram answered HTTP 401" in out
    assert alerts.main(["check", "--offline", str(off), "--now", "2026-10-30 16:50"]) == 1
    doc = json.loads((tmp_path / "th-trader" / "alerts.json").read_text(encoding="utf-8"))
    assert doc["rules"][0]["rang_at"]


def test_quote_write_is_read_by_read_prices(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    off = offline_dir(tmp_path)
    assert alerts.main(["quote", "PTT", "--offline", str(off), "--now", "2026-10-30 16:45", "--write"]) == 0
    got = tm.read_prices("th-trader/prices.json", datetime(2026, 10, 30, 16, 45), max_age_min=None)
    assert got["PTT"]["price"] == D("42.0") and got["PTT"]["prev_close"] == D("42.25")
    doc = json.loads((tmp_path / "th-trader" / "prices.json").read_text(encoding="utf-8"))
    assert doc["prices"]["PTT"]["source"] == "yahoo" and "delay_note" in doc["prices"]["PTT"]


def test_add_from_plan_and_list(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "th-trader").mkdir()
    (tmp_path / "th-trader" / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")
    assert alerts.main(["add", "--from-plan"]) == 0
    assert alerts.main(["add", "--from-plan"]) == 0  # replaces, never doubles
    doc = json.loads((tmp_path / "th-trader" / "alerts.json").read_text(encoding="utf-8"))
    assert len(doc["rules"]) == 3 and len({r["id"] for r in doc["rules"]}) == 3
    assert alerts.main(["list"]) == 0


def test_reset_rearms(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    off = offline_dir(tmp_path)
    alerts.main(["add", "--symbol", "PTT", "--above", "41.90"])
    alerts.main(["check", "--offline", str(off), "--now", "2026-10-30 16:45", "--no-telegram"])
    rid = json.loads((tmp_path / "th-trader" / "alerts.json").read_text(encoding="utf-8"))["rules"][0]["id"]
    assert alerts.main(["reset", rid]) == 0
    assert alerts.main(["check", "--offline", str(off), "--now", "2026-10-30 16:50", "--no-telegram"]) == 0
