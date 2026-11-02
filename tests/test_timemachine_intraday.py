import json
import os
from datetime import date, datetime, time

import pytest

import th_market as tm
import timemachine as tmc
from conftest import FIXTURES

BEFORE = date(2026, 11, 2)


def chart(name, sym):
    with open(os.path.join(FIXTURES, "yahoo", name), encoding="utf-8") as fh:
        return tm.parse_chart(json.load(fh), sym)


@pytest.fixture(scope="module")
def ptt():
    return chart("chart_PTT_5m_60d.json", "PTT")


def test_usable_sessions_fill_to_60(ptt):
    sessions = tmc.usable_sessions(ptt, BEFORE)
    assert len(sessions) == 59
    assert all(len(bars) == 60 for bars in sessions.values())


def test_thin_stock_counts_and_refusal():
    singer = chart("chart_SINGER_5m_60d.json", "SINGER")
    assert len(tmc.usable_sessions(singer, BEFORE)) == 55
    keep = [i for i, t in enumerate(singer["t"]) if tm.ts_to_bkk(t).minute % 15 == 0]  # a third of the bars
    thin = dict(singer, t=[singer["t"][i] for i in keep],
                **{k: [singer[k][i] for i in keep] for k in ("o", "h", "l", "c", "v")})
    with pytest.raises(tmc.TMError, match="try --mode swing"):
        tmc.require_sessions(tmc.usable_sessions(thin, BEFORE), "SINGER")


def test_flat_fill_for_a_slot_with_no_trade(ptt):
    d = date(2026, 10, 30)
    drop = datetime(2026, 10, 30, 10, 30)
    keep = [i for i, t in enumerate(ptt["t"]) if tm.ts_to_bkk(t) != drop]
    holed = dict(ptt, t=[ptt["t"][i] for i in keep], **{k: [ptt[k][i] for i in keep] for k in ("o", "h", "l", "c", "v")})
    bars = tmc.usable_sessions(holed, BEFORE)[d]
    prev = bars[5]["c"]
    assert bars[6]["start"] == drop and (bars[6]["o"], bars[6]["h"], bars[6]["l"], bars[6]["c"]) == (prev,) * 4


def test_pool_slot_30_is_relative_to_1225(ptt):
    sessions = tmc.usable_sessions(ptt, BEFORE)
    days = sorted(sessions)
    pool = tmc.build_pool([sessions[d] for d in days])
    last = sessions[days[-1]]
    assert pool[30][-1][3] == pytest.approx(last[30]["c"] / last[29]["c"])
    assert last[29]["start"].time() == time(12, 25) and last[30]["start"].time() == time(14, 0)


def run_sim(pool, **kw):
    args = dict(entry=100.0, stop=99.0, target=102.0, start_slot=5, end_slot=59, floor=70.0, ceiling=130.0,
                risk_per_share=1.0, fee_rate=0.0, replays=200, seed=19)
    args.update(kw)
    return tmc.simulate(pool, **args)


def flat_pool(o, h, l, c):
    return [[]] + [[(o, h, l, c)] for _ in range(59)]


def test_same_seed_same_result(ptt):
    sessions = tmc.usable_sessions(ptt, BEFORE)
    pool = tmc.build_pool(list(sessions.values()))
    a = run_sim(pool, entry=42.25, stop=41.75, target=43.25, floor=29.5, ceiling=54.5)
    b = run_sim(pool, entry=42.25, stop=41.75, target=43.25, floor=29.5, ceiling=54.5)
    assert a["p_target"] == b["p_target"] and a["avg_R"] == b["avg_R"]
    assert a["p_target"] + a["p_stop"] + a["p_neither"] == pytest.approx(1.0)


def test_gap_through_the_stop_exits_at_the_open():
    mc = run_sim(flat_pool(0.95, 0.96, 0.94, 0.95))
    assert mc["p_stop"] == 1.0 and mc["avg_R"] == pytest.approx(-5.0)  # out at 95, not at the 99 stop


def test_clamped_to_the_ceiling():
    mc = run_sim(flat_pool(1.10, 1.10, 1.10, 1.10), target=1000.0, stop=50.0, risk_per_share=50.0)
    assert mc["p95_close"] <= 130.0 and mc["median_close"] == pytest.approx(130.0)


def test_verdict_break_even_at_2r():
    assert tmc.verdict({"p_target": 0.32, "p_stop": 0.64, "p_neither": 0.04, "r_at_target": 2.0,
                        "until": "16:20"}).startswith("Close to break-even")


def test_entry_bar_not_in_data_yet(ptt):
    plan = {"symbol": "PTT", "date": "2026-10-30", "entry_time": "16:25", "entry": "42.00", "stop": "41.75",
            "target": "42.50", "mode": "intraday", "flat_by": "16:20", "or_minutes": 15}
    trimmed = dict(ptt)
    cut = [i for i, t in enumerate(ptt["t"]) if tm.ts_to_bkk(t) < datetime(2026, 10, 30, 11, 0)]
    trimmed = dict(ptt, t=[ptt["t"][i] for i in cut], **{k: [ptt[k][i] for i in cut] for k in ("o", "h", "l", "c", "v")})
    plan["entry_time"] = "11:30"
    with pytest.raises(tmc.TMError, match="not in Yahoo's data yet"):
        tmc.entry_slot(trimmed, plan)
