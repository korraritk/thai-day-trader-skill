import importlib
import json
import os

from conftest import FIXTURES

exit_ = importlib.import_module("exit")


def run(capsys, *args):
    rc = exit_.main(list(args))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_orb_hit_from_watchlist(capsys):
    rc, out, _ = run(capsys, "plan", "--symbol", "ADVANC", "--watchlist", os.path.join(FIXTURES, "watchlist_orb.json"),
                     "--no-fetch", "--json")
    plan = json.loads(out)
    assert rc == 0
    assert plan["mode"] == "intraday" and plan["stop_rule"] == "or-mid" and plan["flat_by"] == "16:20"
    assert (plan["entry"], plan["stop"], plan["target"]) == ("339.00", "336.00", "345.00")  # mid 336; risk 3; 2R
    assert plan["date"] == "2026-10-30" and plan["entry_time"] == "10:20"


def test_daily_hit_is_swing(capsys):
    rc, out, _ = run(capsys, "plan", "--symbol", "PTT", "--watchlist", os.path.join(FIXTURES, "watchlist_daily.json"),
                     "--no-fetch", "--json")
    plan = json.loads(out)
    assert rc == 0 and plan["mode"] == "swing" and plan["days"] == 10 and plan["stop_rule"] == "pct:5"
    assert plan["setups"] == ["reclaim_ma20"] and plan["stop"] == "39.75"  # 42 x 0.95 = 39.90 -> down 39.75


def test_flags_and_write(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, out, _ = run(capsys, "plan", "--symbol", "X", "--entry", "42.25", "--or-high", "42.25", "--or-low", "41.5",
                     "--now", "2026-11-02 10:30", "--no-fetch", "--write")
    assert rc == 0 and "41.75" in out and "Fees:" in out
    plan = json.loads((tmp_path / "th-trader" / "plan.json").read_text(encoding="utf-8"))
    assert plan["symbol"] == "X" and plan["target"] == "43.25" and plan["currency"] == "THB"
    rc, out, _ = run(capsys, "show")
    assert rc == 0 and "43.25" in out


def test_refusal_is_one_line(capsys):
    rc, _, err = run(capsys, "plan", "--symbol", "X", "--entry", "42.25", "--stop", "price:42.50", "--no-fetch")
    assert rc == 1 and err.startswith("exit.py: ") and "Traceback" not in err


def test_no_fetch_says_limits_were_not_checked(capsys):
    _, out, _ = run(capsys, "plan", "--symbol", "X", "--entry", "42.25", "--or-high", "42.25", "--or-low", "41.5",
                    "--no-fetch")
    assert "not checked" in out
