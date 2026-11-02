from decimal import Decimal as D

import pytest

import th_market as tm


def cfg():
    return tm.load_config("nonexistent.json")


def test_one_side_fee():
    assert tm.fee(D("12675"), cfg()).quantize(D("0.01")) == D("21.29")  # 12,675 x 0.157% x 1.07


def test_round_trip_per_share():
    assert tm.round_trip_fee_per_share(D("42.25"), D("41.75"), cfg()).quantize(D("0.0001")) == D("0.1411")


def test_plan_numbers():
    n = tm.plan_numbers(D("42.25"), D("41.75"), D("43.25"), cfg())
    assert n["gross_r"] == D("2")
    assert n["risk_per_share"].quantize(D("0.0001")) == D("0.6411")
    assert n["net_r"].quantize(D("0.01")) == D("1.34")


def test_config_override_and_bad_type(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"commission_pct": 0.1}', encoding="utf-8")
    assert tm.load_config(str(p))["commission_pct"] == 0.1 and tm.load_config(str(p))["vat_pct"] == 7
    p.write_text('{"budget_thb": "100k"}', encoding="utf-8")
    with pytest.raises(tm.MarketError, match="budget_thb"):
        tm.load_config(str(p))
    p.write_text('{"flat_by": "4pm"}', encoding="utf-8")
    with pytest.raises(tm.MarketError, match="flat_by"):
        tm.load_config(str(p))


def test_fees_line_names_rates():
    assert "0.15%" in tm.fees_line(cfg())


def test_setups_module_is_the_scanner_math():
    import scan
    import setups
    assert scan.find_setups is setups.find_setups and scan.rsi is setups.rsi
