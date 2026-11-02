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

    python entry.py check PTT                         # orb, 15-minute range, 5-minute bars
    python entry.py check PTT --write                 # also th-trader/entry.json (+ Telegram once)
    python entry.py check PTT --setup level --level 42.50
    python entry.py watch PTT --until 15:30           # one line per closed bar, stops on the trigger
    python entry.py watchlist                         # every hit in th-trader/watchlist.json
    python entry.py check PTT --offline chart.json --now "2026-10-29 11:30"   # replay a day

Exit codes: check 0 = triggered, 1 = anything else. watch 0 on a trigger, 1 otherwise.
watchlist 0 when at least one symbol triggered.
"""
import argparse
import datetime
import json
import os
import sys
import time
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import th_market as tm
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "..", "..", "shared"))
    import th_market as tm
import notify

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


def market_clock(day, now):
    """now, or the last session close before it: the lunch break and the evening add no delay."""
    last = None
    for start, end in tm.sessions(day):
        s, e = datetime.datetime.combine(day, start), datetime.datetime.combine(day, end)
        if s <= now < e:
            return now
        if now >= e:
            last = e
    return last or now


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
        res["behind_minutes"] = max(0, int((market_clock(day, now) - data_to).total_seconds() // 60))
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


# ---------------------------------------------------------------- data

PAUSE = 0.5
MIN_EVERY = 30


def chart_range(day, today):
    back = (today - day).days
    if back <= 0:
        return "1d"
    if back <= 4:
        return "5d"
    if back <= 28:
        return "1mo"
    if back <= 58:
        return "60d"
    raise EntryError("Yahoo keeps 5-minute bars for about 60 days: %s is too far back" % day)


def offline_path(path, sym):
    if os.path.isdir(path):
        p = os.path.join(path, sym + ".json")
        if not os.path.exists(p):
            raise EntryError("no offline file for %s in %s (want %s.json)" % (sym, path, sym))
        return p
    if not os.path.exists(path):
        raise EntryError("no offline file %s" % path)
    return path


def load_series(sym, day, offline=None):
    if offline:
        with open(offline_path(offline, sym), encoding="utf-8") as fh:
            return tm.parse_chart(fh.read(), sym)
    return tm.fetch_chart(sym, chart_range(day, tm.now_bkk().date()), "5m")


def file_time(offline, sym):
    """An offline file without --now is read as of its own last trade."""
    rmt = (load_series(sym, None, offline).get("meta") or {}).get("regularMarketTime")
    if not rmt:
        raise EntryError("the offline file has no time: give --now")
    return tm.ts_to_bkk(rmt)


# ---------------------------------------------------------------- words

def delay_text(r):
    if not r.get("data_to"):
        return "no bars yet"
    return "data to %s Bangkok (%d min behind)" % (r["data_to"], r["behind_minutes"] or 0)


def setup_name(r):
    if r["setup"] == "orb":
        return "opening-range breakout (%d-minute range)" % r["or_minutes"]
    return "close above %.2f" % r["level"]


def describe(sym, r, now):
    out = ["%s  %s Bangkok  %s on 5-minute bars (Yahoo, delayed)" % (
        sym, now.strftime("%a %Y-%m-%d %H:%M"), setup_name(r))]
    if r.get("or_high") is not None:
        out.append("Opening range: high %.2f, low %.2f." % (r["or_high"], r["or_low"]))
    if r["status"] == "triggered":
        out.append("TRIGGERED %s at %s: %s. %s." % (r["at"], r["price"], r["why"], delay_text(r)))
        if r["trigger_age_minutes"] > STALE_TRIGGER_MIN:
            out.append("This triggered %d minutes ago in Yahoo's delayed data; the price now may be well away "
                       "from it. A late entry is a different trade." % r["trigger_age_minutes"])
        out.append("Nothing was placed. Next: set the stop and target BEFORE you buy with th-trader-exit "
                   "(exit.py plan).")
    else:
        out.append("%s  %s. %s." % (r["status"].upper().replace("-", " "), r["why"], delay_text(r)))
        if r["status"] == "expired":
            out.append("No entry on this setup today. Nothing was placed.")
    return out


def entry_doc(sym, r, now):
    return {"created_by": "th-trader-entry", "symbol": sym, "triggered": r["status"] == "triggered",
            "status": r["status"], "price": str(r["price"]) if r["price"] is not None else None, "at": r["at"],
            "bar_start": r["bar_start"], "date": r["date"], "or_high": r["or_high"], "or_low": r["or_low"],
            "or_minutes": r["or_minutes"], "setups": [r["setup"]], "level": r["level"],
            "data_to": r["data_to"], "behind_minutes": r["behind_minutes"],
            "trigger_age_minutes": r["trigger_age_minutes"], "why": r["why"], "source": "yahoo",
            "created_at": tm.iso_bkk(now)}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)


def same_trigger(path, doc):
    """True when path already holds this trigger: a check on a /loop sends Telegram once."""
    try:
        with open(path, encoding="utf-8") as fh:
            old = json.load(fh)
    except (OSError, ValueError):
        return False
    return bool(old.get("triggered")) and all(old.get(k) == doc[k] for k in ("symbol", "date", "at", "setups"))


def telegram_text(sym, r):
    return "%s %s: triggered %s at %s (%s). %s. Nothing was placed." % (
        sym, setup_name(r), r["at"], r["price"], r["why"], delay_text(r))


def ring(sym, r, a):
    if a.no_telegram:
        return
    sent, why = notify.send_telegram(telegram_text(sym, r))
    print(("Telegram: " + why) if sent else ("Telegram not sent: " + why))


# ---------------------------------------------------------------- commands

def _opts(a, cfg):
    if a.setup == "orb" and a.level is not None:
        raise EntryError("--level is for --setup level (an orb's level is its range high)")
    if a.min_volume_x is not None and a.min_volume_x <= 0:
        raise EntryError("--min-volume-x must be above 0")
    return {"setup": a.setup, "level": a.level, "or_minutes": a.or_minutes, "min_volume_x": a.min_volume_x,
            "no_entries_after": datetime.datetime.strptime(cfg["no_entries_after"], "%H:%M").time()}


def _now(a, sym):
    if a.now:
        return tm.parse_now(a.now)
    if a.offline:
        return file_time(a.offline, sym)
    return tm.now_bkk()


def cmd_check(a, cfg):
    sym = a.symbol.upper()
    opts = _opts(a, cfg)
    now = _now(a, sym)
    r = evaluate(load_series(sym, now.date(), a.offline), now.date(), now, **opts)
    doc = entry_doc(sym, r, now)
    if a.json:
        print(json.dumps(doc, indent=2, ensure_ascii=False))
    else:
        for line in describe(sym, r, now):
            print(line)
    if a.write:
        repeat = same_trigger(a.write, doc)
        write_json(a.write, doc)
        if not a.json:
            print("Wrote %s (status %s)." % (a.write, r["status"]))
        if r["status"] == "triggered" and not repeat:
            ring(sym, r, a)
    return 0 if r["status"] == "triggered" else 1


def bar_line(b, r):
    lvl = r["or_high"] if r["setup"] == "orb" else r["level"]
    note = ""
    if lvl is not None:
        note = "  above %.2f" % lvl if b["c"] > lvl else "  %.2f under %.2f" % (lvl - b["c"], lvl)
    return "%s bar  closed %.2f%s" % (hm(b["start"]), b["c"], note)


def cmd_watch(a, cfg):
    sym = a.symbol.upper()
    opts = _opts(a, cfg)
    every = a.every
    if every < MIN_EVERY:
        print("--every %g is under %d seconds: using %d." % (every, MIN_EVERY, MIN_EVERY))
        every = MIN_EVERY
    simulated = bool(a.now or a.offline)
    now = _now(a, sym)
    day = now.date()
    sess = tm.sessions(day)
    if not sess:
        print("%s: the market is closed (%s). Nothing to watch." % (day, tm.holiday_name(day) or day.strftime("%A")))
        return 1
    close_dt = datetime.datetime.combine(day, sess[-1][1])
    until = datetime.datetime.combine(day, datetime.datetime.strptime(a.until, "%H:%M").time()) if a.until else None
    print("Watching %s from %s Bangkok, one line per closed 5-minute bar (Yahoo, delayed)."
          % (sym, now.strftime("%H:%M")))
    printed = set()
    while True:
        series = load_series(sym, day, a.offline)
        r = evaluate(series, day, now, **opts)
        for b in tm.intraday_bars(series, day, now):
            if b["start"] in printed:
                continue
            printed.add(b["start"])
            print(bar_line(b, r))
            if r["status"] == "triggered" and hm(b["start"]) == r["bar_start"]:
                break
        if r["status"] == "triggered":
            for line in describe(sym, r, now)[1:]:
                print(line)
            write_json(a.out, entry_doc(sym, r, now))
            print("Wrote %s." % a.out)
            ring(sym, r, a)
            return 0
        if r["status"] in ("expired", "no-data") or now >= close_dt:
            print("EXPIRED  %s." % (r["why"] or "the market closed without a trigger"))
            return 1
        if until and now >= until:
            print("Stopped at --until %s without a trigger: %s." % (a.until, r["why"]))
            return 1
        if simulated:
            now += STEP
        else:
            sys.stdout.flush()
            time.sleep(every)
            now = tm.now_bkk()


def cmd_watchlist(a, cfg):
    opts = _opts(a, cfg)
    if not os.path.exists(a.watchlist):
        raise EntryError("no watchlist at %s: run th-trader-scanner first" % a.watchlist)
    try:
        with open(a.watchlist, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as e:
        raise EntryError("%s is not valid JSON: %s" % (a.watchlist, e))
    hits = [h for h in (doc.get("hits") or []) if isinstance(h, dict) and h.get("symbol")]
    if not hits:
        raise EntryError("no hits in %s: the scanner found nothing to watch" % a.watchlist)
    rows = []
    for i, h in enumerate(hits):
        sym = str(h["symbol"]).upper()
        o = dict(opts)
        if a.setup == "level" and o["level"] is None:
            o["level"] = h.get("level")
        try:
            if i and not a.offline:
                tm.SLEEP(PAUSE)
            now = _now(a, sym)
            rows.append((sym, evaluate(load_series(sym, now.date(), a.offline), now.date(), now, **o), None))
        except (EntryError, tm.MarketError) as e:
            rows.append((sym, None, str(e)))
    print("%-7s %-10s %-6s %-8s %-8s %s" % ("SYMBOL", "STATUS", "AT", "PRICE", "LEVEL", "DATA"))
    for sym, r, e in rows:
        if e:
            print("%-7s error: %s" % (sym, e))
            continue
        lvl = r["or_high"] if r["setup"] == "orb" else r["level"]
        print("%-7s %-10s %-6s %-8s %-8s %s" % (sym, r["status"], r["at"] or "-", r["price"] or "-",
                                                 ("%.2f" % lvl) if lvl is not None else "-", delay_text(r)))
    n = sum(1 for _, r, _ in rows if r and r["status"] == "triggered")
    print("%d of %d triggered. Pick one, then: entry.py check SYMBOL --write, and th-trader-exit." % (n, len(rows)))
    print("Yahoo's .BK bars are delayed. Nothing was placed.")
    return 0 if n else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Entry timer (th-trader-entry): watches a SET setup bar by bar.")
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("--setup", choices=["orb", "level"], default="orb")
        p.add_argument("--level", type=float, help="the price to close above (--setup level)")
        p.add_argument("--or-minutes", type=int, default=15, help="opening range length (default 15)")
        p.add_argument("--min-volume-x", type=float, help="trigger bar volume vs the range bars' average")
        p.add_argument("--now", help='pretend it is this Bangkok time, e.g. "2026-10-29 11:30"')
        p.add_argument("--offline", metavar="PATH", help="saved Yahoo 5-minute chart JSON, or a folder of SYMBOL.json")
        p.add_argument("--config", default="th-trader/config.json")
        p.add_argument("--no-telegram", action="store_true", help="do not send the trigger to Telegram")

    p = sub.add_parser("check", help="the setup's status now")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--write", nargs="?", const="th-trader/entry.json", metavar="PATH")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("watch", help="poll bar by bar until it triggers or expires")
    p.add_argument("symbol")
    common(p)
    p.add_argument("--every", type=float, default=60, help="seconds between polls (at least 30)")
    p.add_argument("--until", help="stop at this Bangkok time (HH:MM)")
    p.add_argument("--out", default="th-trader/entry.json")
    p = sub.add_parser("watchlist", help="check every hit in th-trader/watchlist.json")
    common(p)
    p.add_argument("--watchlist", default="th-trader/watchlist.json")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        cfg = tm.load_config(a.config)
        return {"check": cmd_check, "watch": cmd_watch, "watchlist": cmd_watchlist}[a.cmd](a, cfg)
    except (EntryError, tm.MarketError, ValueError) as e:
        print("entry.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("entry.py: stopped.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
