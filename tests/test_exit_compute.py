import importlib
from datetime import date
from decimal import Decimal as D

import pytest

import th_market as tm

exit_ = importlib.import_module("exit")
DAY = date(2026, 11, 2)
CFG = tm.load_config("nonexistent.json")


def test_orb_default_or_mid_2r():
    p = exit_.compute(D("42.25"), "or_mid", None, "r", D("2"), DAY, CFG, or_high=D("42.25"), or_low=D("41.50"))
    assert (p["stop"], p["target"]) == (D("41.75"), D("43.25"))  # mid 41.875 -> down 41.75; 42.25 + 2 x 0.50
    assert p["net_r"].quantize(D("0.01")) == D("1.34") and p["tick_table"] == "2009"


def test_band_edge():
    p = exit_.compute(D("25.00"), "pct", D("1"), "r", D("2"), DAY, CFG)
    assert p["stop"] == D("24.70")  # 24.75 on the 0.10 grid rounds down to 24.70
    assert p["target"] == D("25.50")  # 25 + 2 x 0.30 = 25.60 -> nearest 0.25 = 25.50


def test_refusals():
    with pytest.raises(exit_.PlanError, match="at or above"):
        exit_.compute(D("42.25"), "price", D("42.25"), "r", D("2"), DAY, CFG)
    with pytest.raises(exit_.PlanError, match="floor"):
        exit_.compute(D("42.25"), "price", D("29.00"), "r", D("1"), DAY, CFG, prev_close=D("42.00"))
    with pytest.raises(exit_.PlanError, match="ceiling"):
        exit_.compute(D("54.00"), "price", D("53.00"), "price", D("56.00"), DAY, CFG, prev_close=D("42.00"))
    with pytest.raises(exit_.PlanError, match="fee"):
        exit_.compute(D("1.50"), "price", D("1.49"), "r", D("2"), DAY, CFG)  # round trip 50.2% of 0.01
    with pytest.raises(exit_.PlanError, match="opening range"):
        exit_.compute(D("42.25"), "or_mid", None, "r", D("2"), DAY, CFG)


def test_fee_warning_between_25_and_50():
    p = exit_.compute(D("42.25"), "price", D("41.75"), "r", D("2"), DAY, CFG)  # fees 28% of the 0.50 risk
    assert any("fee" in w for w in p["warnings"])


def test_low_net_r_warns():
    p = exit_.compute(D("42.25"), "price", D("41.75"), "r", D("1"), DAY, CFG)
    assert any("min_reward_risk" in w for w in p["warnings"])


def test_parse_rules_ported():
    assert exit_.parse_stop("or-mid") == ("or_mid", None)
    assert exit_.parse_stop("pct:5") == ("pct", D("5"))
    assert exit_.parse_target("1.5R") == ("r", D("1.5"))
