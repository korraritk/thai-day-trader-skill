"""Entry timer for th-trader-entry: watches a SET/mai setup bar by bar and says when it triggered.
Standard library only. Long side only. It never places an order.

Ported from day-trader-skill's trader-entry (MIT, Jake Schincariol): the orb and level setups,
the closed-bar rule, the statuses and the check / watch / watchlist commands. The NYSE clock is
replaced by th_market's SET sessions (10:00-12:30, 14:00-16:30, Bangkok), and every line says
how far behind Yahoo's .BK data is.

Setups:
  orb    the range is the high and low of the first --or-minutes (default 15: the 10:00, 10:05
         and 10:10 bars). It triggers on the first COMPLETED bar after the range that CLOSES
         above the range high. A wick above the high is not a trigger.
  level  the first completed bar that closes above --level.
  --min-volume-x X  the trigger bar also needs X times the range bars' average volume.

A trigger's time is the bar's close time (the 11:05 bar closes at 11:10) and its price is that
close. Statuses: triggered; waiting (not open yet, range still forming, lunch break, no close
above yet); expired (no trigger by no_entries_after, 15:30); no-data (a closed day, no bars).
"""
import datetime
import os
import sys
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import th_market as tm
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "..", "..", "shared"))
    import th_market as tm

STEP = datetime.timedelta(minutes=5)
STALE_TRIGGER_MIN = 15


class EntryError(Exception):
    pass


def hm(dt):
    return dt.strftime("%H:%M")


def _data_to(series, bars, day, now):
    """End of the latest complete bar, or Yahoo's last trade time when it is later (spark-style
    answers leave out bars where nothing changed)."""
    last = bars[-1]["start"] + STEP if bars else None
    rmt = (series.get("meta") or {}).get("regularMarketTime")
    if rmt:
        t = tm.ts_to_bkk(rmt).replace(second=0)
        if t.date() == day and t <= now and (last is None or t > last):
            last = t
    return last


def evaluate(series, day, now, setup="orb", level=None, or_minutes=15, min_volume_x=None,
             no_entries_after=datetime.time(15, 30)):
    """Pure: the setup's status at `now` from one day's 5-minute bars."""
    if setup not in ("orb", "level"):
        raise EntryError("--setup is orb or level")
    if setup == "level" and level is None:
        raise EntryError("--setup level needs --level PRICE")
    if or_minutes <= 0 or or_minutes % 5:
        raise EntryError("--or-minutes must be a whole number of 5-minute bars")
    res = {"status": "waiting", "why": "", "price": None, "at": None, "bar_start": None,
           "or_high": None, "or_low": None, "range_volume_avg": None, "trigger_volume_x": None,
           "data_to": None, "behind_minutes": None, "trigger_age_minutes": None,
           "setup": setup, "level": float(level) if level is not None else None, "or_minutes": or_minutes,
           "date": day.isoformat(), "last_close": None}
    sess = tm.sessions(day)
    if not sess:
        res.update(status="no-data", why="the market is closed on %s (%s)" % (
            day.isoformat(), tm.holiday_name(day) or day.strftime("%A")))
        return res
    (m_open, m_close), (a_open, _a_close) = sess
    open_dt = datetime.datetime.combine(day, m_open)
    or_end = open_dt + datetime.timedelta(minutes=or_minutes)
    cutoff = datetime.datetime.combine(day, no_entries_after)
    bars = tm.intraday_bars(series, day, now)
    data_to = _data_to(series, bars, day, now)
    if data_to:
        res["data_to"] = hm(data_to)
        res["behind_minutes"] = max(0, int((now - data_to).total_seconds() // 60))
    if bars:
        res["last_close"] = bars[-1]["c"]
    if now < open_dt:
        res["why"] = "not open yet (SET opens at %s)" % m_open.strftime("%H:%M")
        return res

    or_bars = [b for b in bars if b["start"] < or_end]
    avg = sum(b["v"] for b in or_bars) / len(or_bars) if or_bars else None
    res["range_volume_avg"] = avg
    if or_bars:
        res["or_high"], res["or_low"] = max(b["h"] for b in or_bars), min(b["l"] for b in or_bars)
    if setup == "orb":
        if now < or_end + STEP:
            res["why"] = "range still forming (it is set when the %s bar closes at %s)" % (
                hm(or_end - STEP), hm(or_end))
            return res
        if not or_bars:
            res.update(status="no-data", why="Yahoo sent no bars for the opening range")
            return res
        lvl = res["or_high"]
    else:
        lvl = float(level)

    trig = None
    for b in bars:
        if setup == "orb" and b["start"] < or_end:
            continue
        if b["start"] + STEP > cutoff:
            break
        if b["c"] > lvl:
            vx = b["v"] / avg if avg else None
            if min_volume_x and (vx is None or vx < min_volume_x):
                continue
            trig = b
            break
    if trig:
        end = trig["start"] + STEP
        res.update(status="triggered", price=Decimal(str(trig["c"])), at=hm(end), bar_start=hm(trig["start"]),
                   trigger_volume_x=(trig["v"] / avg) if avg else None,
                   trigger_age_minutes=int((now - end).total_seconds() // 60),
                   why="the %s bar closed above %s %.2f" % (
                       hm(trig["start"]), "the range high" if setup == "orb" else "the level", lvl))
        return res
    if not bars:
        res.update(status="no-data", why="Yahoo sent no bars for %s yet" % day.isoformat())
        return res
    what = "the range high %.2f" % lvl if setup == "orb" else "%.2f" % lvl
    if now > cutoff:
        res.update(status="expired", why="no close above %s by %s, the last time for a new entry" % (
            what, no_entries_after.strftime("%H:%M")))
        return res
    lunch_from = datetime.datetime.combine(day, m_close)
    lunch_to = datetime.datetime.combine(day, a_open)
    if lunch_from <= now < lunch_to:
        res["why"] = "lunch break until %s; no close above yet over %s" % (a_open.strftime("%H:%M"), what)
        return res
    res["why"] = "no close above yet over %s (last close %.2f)" % (what, bars[-1]["c"])
    return res
