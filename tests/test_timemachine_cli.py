import json
import os

import pytest

import timemachine as tmc
from conftest import FIXTURES

INTRADAY = {"symbol": "PTT", "mode": "intraday", "date": "2026-10-30", "entry_time": "10:30", "entry": "42.00",
            "stop": "41.50", "target": "43.00", "stop_rule": "or-mid", "target_rule": "2R", "target_r": 2.0,
            "risk_per_share": "0.6411", "or_minutes": 15, "flat_by": "16:20", "setups": ["orb"]}
SWING = {"symbol": "PTT", "mode": "swing", "date": "2026-10-30", "entry_time": None, "entry": "42.00",
         "stop": "39.75", "target": "46.50", "stop_rule": "pct:5", "target_rule": "2R", "target_r": 2.0,
         "risk_per_share": "2.3911", "days": 10, "setups": ["reclaim_ma20"]}


def write_plan(tmp_path, plan):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan), encoding="utf-8")
    return str(p)


@pytest.mark.parametrize("plan,chart", [(INTRADAY, "chart_PTT_5m_60d.json"), (SWING, "chart_PTT_1d_5y.json")])
def test_run_json(tmp_path, capsys, plan, chart):
    rc = tmc.main(["run", "--plan", write_plan(tmp_path, plan), "--offline", os.path.join(FIXTURES, "yahoo", chart),
                   "--replays", "300", "--json", "--now", "2026-11-02 09:00"])
    doc = json.loads(capsys.readouterr().out)
    m = doc["mc"]
    assert rc == 0 and m["p_target"] + m["p_stop"] + m["p_neither"] == pytest.approx(1.0, abs=0.001)
    assert doc["mode"] == plan["mode"] and "history" in doc and doc["verdict"]


def test_run_text_and_chart(tmp_path, capsys):
    out_path = str(tmp_path / "tm.json")
    tmc.main(["run", "--plan", write_plan(tmp_path, INTRADAY),
              "--offline", os.path.join(FIXTURES, "yahoo", "chart_PTT_5m_60d.json"), "--replays", "300",
              "--write", out_path, "--now", "2026-11-02 09:00"])
    text = capsys.readouterr().out
    assert "seed 19" in text and "net" in text and "Fees:" in text
    assert tmc.main(["chart", "--from", out_path]) == 0
    chart = capsys.readouterr().out
    assert "target" in chart and "stop" in chart and "Traceback" not in chart
