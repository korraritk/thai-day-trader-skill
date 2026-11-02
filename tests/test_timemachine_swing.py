import json
import os
from datetime import date, datetime

import pytest

import setups
import th_market as tm
import timemachine as tmc
from conftest import FIXTURES


@pytest.fixture(scope="module")
def ptt_daily():
    with open(os.path.join(FIXTURES, "yahoo", "chart_PTT_1d_5y.json"), encoding="utf-8") as fh:
        return tm.daily_bars(tm.parse_chart(json.load(fh), "PTT"), datetime(2026, 11, 2, 9, 0))


def test_daily_pool_has_one_ratio_per_day(ptt_daily):
    pool = tmc.daily_pool(ptt_daily)
    assert len(pool) == len(ptt_daily) - 1
    assert pool[-1][3] == pytest.approx(ptt_daily[-1]["c"] / ptt_daily[-2]["c"])


def test_swing_same_seed_repeats(ptt_daily):
    pool = tmc.daily_pool(ptt_daily)
    args = dict(entry=42.0, stop=39.75, target=46.5, days=10, risk_per_share=2.39, fee_rate=0.00168,
                replays=500, seed=19)
    a, b = tmc.simulate_swing(pool, **args), tmc.simulate_swing(pool, **args)
    assert a["p_target"] == b["p_target"] and a["p_target"] + a["p_stop"] + a["p_neither"] == pytest.approx(1.0)


def test_swing_clamps_each_day_to_30_pct():
    mc = tmc.simulate_swing([(1.5, 1.6, 1.4, 1.5)], entry=10.0, stop=1.0, target=1000.0, days=2,
                            risk_per_share=9.0, fee_rate=0.0, replays=10, seed=1)
    assert mc["median_close"] == pytest.approx(16.9)  # 10 -> 13 -> 16.9, never 22.5


def test_swing_history_finds_the_plans_setup_days():
    closes = [10.0] * 30
    for _ in range(3):
        closes += [9.0, 11.0] + [10.5] * 20
    bars = [{"date": date(2020, 1, 1), "o": c, "h": c, "l": c, "c": c, "v": 1} for c in closes]
    fired = [i for i in range(30, len(closes) - 1) if "reclaim_ma20" in setups.find_setups(closes[:i + 1])[0]]
    rows = tmc.history_swing(bars, ["reclaim_ma20"], 0.05, 2.0, 10, 0.0)
    assert len(fired) >= 3 and [r["index"] for r in rows] == fired
