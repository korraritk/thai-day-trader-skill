"""Price and clock alerts for th-trader-alerts: rings in Claude and on Telegram. Standard library only.
It never places, changes or cancels an order.

Ported from day-trader-skill's trader-alerts (MIT, Jake Schincariol): the rule kinds, ring-once
state, rearm, rules from the exit plan, and the clock / quote / add / check / list / reset
commands. The NYSE clock is replaced by th_market's SET sessions (10:00-12:30, 14:00-16:30
Bangkok, holidays from set-holidays.json); quotes come from Yahoo's v8 chart for SYMBOL.BK.

    python alerts.py clock                            # is SET open, lunch, closed (holiday name)
    python alerts.py quote PTT CPALL --write          # last price, previous close -> th-trader/prices.json
    python alerts.py add --from-plan                  # stop, target and flat-by from th-trader/plan.json
    python alerts.py add --symbol PTT --below 41.75   # or --above X, --pct 3 [--direction up|down]
    python alerts.py add --at 16:20 --label "flat by" # or --opening-bell
    python alerts.py check                            # rings what hit (exit 0), else exit 1
    python alerts.py list
    python alerts.py reset PTT-below-1

Rules live in th-trader/alerts.json, each with its ring state:
  above / below   ring once, then quiet until reset; with --rearm, re-armed when the price crosses back
  pct_move        at most once a day, vs the previous close
  time            once per trading day at or after HH:MM
  opening_bell    once per trading day while SET is open
  a rule with a date is watched on that date only.
A ring prints, and goes to Telegram unless --no-telegram. A failed send is one line; the rule
still counts as rung.
"""
import argparse
import datetime
import json
import os
import sys
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import th_market as tm
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "..", "..", "shared"))
    import th_market as tm
import notify

RULES = "th-trader/alerts.json"
PRICES = "th-trader/prices.json"
PAUSE = 0.5
KINDS = ("above", "below", "pct_move", "time", "opening_bell")
NO_QUOTE = ("time", "opening_bell")


class AlertError(Exception):
    pass


def hhmm(text):
    try:
        return datetime.datetime.strptime(str(text).strip(), "%H:%M").time()
    except ValueError:
        raise AlertError("%r is not a 24-hour Bangkok time like 16:20" % (text,))


def D(x, what="price"):
    try:
        return Decimal(str(x))
    except (InvalidOperation, ValueError):
        raise AlertError("%s %r is not a number" % (what, x))


def px(x):
    return str(Decimal(x).quantize(Decimal("0.01")))


# ---------------------------------------------------------------- the clock

def next_open(d):
    nd = d + datetime.timedelta(days=1)
    for _ in range(30):
        if tm.is_trading_day(nd):
            return "%s 10:00" % nd.isoformat()
        nd += datetime.timedelta(days=1)
    return None


def clock_status(now):
    """{state, holiday, next, sessions}: state is open, lunch, closed, before open or after close."""
    day = now.date()
    holiday = tm.holiday_name(day)
    sess = tm.sessions(day)
    if not sess:
        return {"state": "closed", "holiday": holiday, "weekend": day.weekday() >= 5,
                "next": "opens %s" % next_open(day), "sessions": []}
    (mo, mc), (ao, ac) = sess
    t = now.time()
    if t < mo:
        state, nxt = "before open", "opens %s" % mo.strftime("%H:%M")
    elif t < mc:
        state, nxt = "open", "morning session closes %s" % mc.strftime("%H:%M")
    elif t < ao:
        state, nxt = "lunch", "afternoon session opens %s" % ao.strftime("%H:%M")
    elif t < ac:
        state, nxt = "open", "closes %s" % ac.strftime("%H:%M")
    else:
        state, nxt = "after close", "opens %s" % next_open(day)
    return {"state": state, "holiday": None, "weekend": False, "next": nxt,
            "sessions": ["%s-%s" % (a.strftime("%H:%M"), b.strftime("%H:%M")) for a, b in sess]}


# ---------------------------------------------------------------- rules

def make_rule(kind, symbol=None, price=None, pct=None, direction=None, at=None, label=None, date=None,
              rearm=False):
    if kind not in KINDS:
        raise AlertError("unknown alert kind %r" % kind)
    r = {"id": None, "kind": kind}
    if kind not in NO_QUOTE:
        if not symbol:
            raise AlertError("a %s alert needs --symbol" % kind)
        r["symbol"] = symbol.upper()
    elif symbol:
        r["symbol"] = symbol.upper()
    if kind in ("above", "below"):
        if price is None:
            raise AlertError("a %s alert needs a price" % kind)
        r["price"] = str(D(price))
        r["rearm"] = bool(rearm)
    if kind == "pct_move":
        if pct is None or D(pct, "pct") <= 0:
            raise AlertError("a pct_move alert needs --pct above 0")
        r["pct"] = str(D(pct, "pct"))
        if direction not in (None, "up", "down"):
            raise AlertError("--direction is up or down")
        r["direction"] = direction
    if kind == "time":
        hhmm(at)
        r["at"] = at
    if label:
        r["label"] = label
    if date:
        datetime.date.fromisoformat(str(date))
        r["date"] = str(date)
    r.update(rang_at=None, rang_on=None)
    r["id"] = "%s-%s-1" % (r.get("symbol") or "SET", kind)
    return r


def with_unique_id(rule, rules):
    taken = {r.get("id") for r in rules}
    base = "%s-%s-" % (rule.get("symbol") or "SET", rule["kind"])
    n = 1
    while base + str(n) in taken:
        n += 1
    rule["id"] = base + str(n)
    return rule


def rules_from_plan(plan):
    """th-trader/plan.json -> below = stop, above = target, and for an intraday plan time = flat_by,
    all for the plan's date."""
    if not isinstance(plan, dict) or not plan.get("symbol"):
        raise AlertError("the plan has no symbol (make one with th-trader-exit)")
    sym, day = str(plan["symbol"]).upper(), plan.get("date")
    for k in ("stop", "target"):
        if not plan.get(k):
            raise AlertError("the plan has no %s (make one with th-trader-exit)" % k)
    out = [make_rule("below", sym, price=plan["stop"], label="stop", date=day),
           make_rule("above", sym, price=plan["target"], label="target", date=day)]
    if plan.get("mode", "intraday") == "intraday" and plan.get("flat_by"):
        out.append(make_rule("time", sym, at=plan["flat_by"], label="flat by", date=day))
    for r in out:
        r["id"] = "%s-plan-%s" % (sym, {"below": "stop", "above": "target", "time": "flat-by"}[r["kind"]])
    return out


def active(rule, today):
    return not rule.get("date") or rule["date"] == today.isoformat()


def delay_note(q, now):
    if not q.get("time"):
        return "data time unknown"
    return "data ~%d min behind" % tm.minutes_behind(q["time"], now)


def evaluate(rules, quotes, now):
    """Pure. -> (rung, rules_after). quotes: {SYM: {price, prev_close, time}} (Decimal, naive Bangkok)."""
    today = now.date()
    trading = tm.is_trading_day(today)
    stamp = tm.iso_bkk(now)
    rung, after = [], []
    for r in rules:
        r = dict(r)
        after.append(r)
        if not active(r, today):
            continue
        kind, label = r["kind"], r.get("label")
        msg = None
        if kind == "opening_bell":
            st = clock_status(now)
            if trading and st["state"] in ("open", "lunch") and r.get("rang_on") != today.isoformat():
                msg = "Opening bell: SET is open (%s Bangkok)." % now.strftime("%H:%M")
        elif kind == "time":
            if trading and now.time() >= hhmm(r["at"]) and r.get("rang_on") != today.isoformat():
                what = (label or "time alert")
                msg = "%s%s: it is %s Bangkok (set for %s)%s." % (
                    what[:1].upper(), what[1:], now.strftime("%H:%M"), r["at"],
                    (". If %s is still open, sell it in your broker's app and log it" % r["symbol"])
                    if r.get("symbol") and label == "flat by" else "")
        else:
            q = quotes.get(r["symbol"])
            if not q:
                continue
            p = q["price"]
            when = q["time"].strftime("%H:%M") if q.get("time") else now.strftime("%H:%M")
            if kind in ("above", "below"):
                level = Decimal(r["price"])
                hit = p >= level if kind == "above" else p <= level
                if hit and not r.get("rang_at"):
                    msg = "%s %s hit the %s %s at %s (%s)" % (r["symbol"], px(p), label or "alert level", px(r["price"]),
                                                              when, delay_note(q, now))
                elif not hit and r.get("rang_at") and r.get("rearm"):
                    r["rang_at"], r["rang_on"] = None, None
            else:
                prev = q.get("prev_close")
                if prev:
                    move = (p / prev - 1) * 100
                    want = r.get("direction")
                    hit = abs(move) >= Decimal(r["pct"]) and (not want or (want == "up") == (move > 0))
                    if hit and r.get("rang_on") != today.isoformat():
                        msg = "%s moved %+.2f%% to %s from the previous close %s at %s (%s)" % (
                            r["symbol"], move, px(p), px(prev), when, delay_note(q, now))
        if msg:
            r["rang_at"], r["rang_on"] = stamp, today.isoformat()
            rung.append({"id": r["id"], "kind": kind, "symbol": r.get("symbol"), "message": msg})
    return rung, after


# ---------------------------------------------------------------- quotes

def quote_from_series(series):
    m = series.get("meta") or {}
    price = m.get("regularMarketPrice")
    if price is None:
        raise tm.FetchError("Yahoo sent no last price")
    prev = m.get("previousClose") or m.get("chartPreviousClose")
    t = m.get("regularMarketTime")
    return {"price": D(price), "prev_close": D(prev) if prev else None,
            "time": tm.ts_to_bkk(t) if t else None}


def get_quotes(symbols, offline=None):
    """-> ({SYM: quote}, [errors]). One request per symbol, PAUSE apart."""
    quotes, errors = {}, []
    for i, sym in enumerate(symbols):
        try:
            if offline:
                p = os.path.join(offline, sym + ".json")
                if not os.path.exists(p):
                    raise tm.FetchError("no offline file %s" % p)
                with open(p, encoding="utf-8") as fh:
                    series = tm.parse_chart(fh.read(), sym)
            else:
                if i:
                    tm.SLEEP(PAUSE)
                series = tm.fetch_chart(sym, "1d", "5m")
            quotes[sym] = quote_from_series(series)
        except tm.MarketError as e:
            errors.append("%s: %s" % (sym, e))
    return quotes, errors


def write_prices(path, quotes, now):
    doc = {"created_at": tm.iso_bkk(now), "prices": {}}
    for sym, q in quotes.items():
        doc["prices"][sym] = {"price": str(q["price"]),
                              "prev_close": str(q["prev_close"]) if q["prev_close"] is not None else None,
                              "time": tm.iso_bkk(q["time"] or now), "source": "yahoo",
                              "delay_note": delay_note(q, now)}
    save(path, doc)


# ---------------------------------------------------------------- files

def save(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise AlertError("%s is not valid JSON: %s" % (path, e))


def load_rules(path):
    doc = load(path, {"rules": []})
    if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list):
        raise AlertError("%s should hold {\"rules\": [...]}" % path)
    return doc["rules"]


# ---------------------------------------------------------------- commands

def cmd_clock(a):
    now = tm.parse_now(a.now)
    st = clock_status(now)
    why = st["holiday"] or ("weekend" if st.get("weekend") else "")
    print("Bangkok %s: SET %s%s; %s." % (now.strftime("%a %Y-%m-%d %H:%M"), st["state"].upper(),
                                         (" (%s)" % why) if why else "", st["next"]))
    if st["sessions"]:
        print("Sessions today: %s." % ", ".join(st["sessions"]))
    return 0 if st["state"] == "open" else 1


def cmd_quote(a):
    syms = [s.upper() for s in a.symbols]
    if a.plan:
        plan = load(a.plan, {})
        if plan.get("symbol"):
            syms.append(str(plan["symbol"]).upper())
    syms = list(dict.fromkeys(syms))
    if not syms:
        raise AlertError("which symbols? alerts.py quote PTT CPALL, or --plan")
    now = tm.parse_now(a.now)
    quotes, errors = get_quotes(syms, a.offline)
    for s in syms:
        q = quotes.get(s)
        if q:
            chg = (" (%+.2f%% vs the previous close %s)" % ((q["price"] / q["prev_close"] - 1) * 100, px(q["prev_close"]))
                   if q["prev_close"] else "")
            print("%-7s %s%s  at %s, %s" % (s, px(q["price"]), chg,
                                            q["time"].strftime("%Y-%m-%d %H:%M") if q["time"] else "?",
                                            delay_note(q, now)))
    for e in errors:
        print("error: " + e)
    if a.write and quotes:
        write_prices(a.write, quotes, now)
        print("Wrote %d price(s) to %s." % (len(quotes), a.write))
    print("Yahoo's .BK quotes are free and delayed: for alerts only. Your broker's price is the one that fills.")
    return 0 if quotes else 1


def cmd_add(a):
    rules = load_rules(a.rules)
    if a.from_plan:
        plan = load(a.plan, None)
        if plan is None:
            raise AlertError("no plan at %s: make one with th-trader-exit" % a.plan)
        new = rules_from_plan(plan)
        ids = {r["id"] for r in new}
        rules = [r for r in rules if r.get("id") not in ids] + new
    else:
        if a.opening_bell:
            rule = make_rule("opening_bell", date=a.date)
        elif a.at:
            rule = make_rule("time", a.symbol, at=a.at, label=a.label or "time alert", date=a.date)
        elif a.above is not None:
            rule = make_rule("above", a.symbol, price=a.above, label=a.label, date=a.date, rearm=a.rearm)
        elif a.below is not None:
            rule = make_rule("below", a.symbol, price=a.below, label=a.label, date=a.date, rearm=a.rearm)
        elif a.pct is not None:
            rule = make_rule("pct_move", a.symbol, pct=a.pct, direction=a.direction, label=a.label, date=a.date)
        else:
            raise AlertError("give --from-plan, or --symbol with --above / --below / --pct, or --at, or --opening-bell")
        new = [with_unique_id(rule, rules)]
        rules = rules + new
    save(a.rules, {"rules": rules})
    for r in new:
        print("Added %s: %s" % (r["id"], describe_rule(r)))
    print("While a trade is open, run alerts.py check every few minutes (/loop 5m). Quotes are delayed; "
          "the stop you set in your broker's app is what actually gets you out.")
    return 0


def describe_rule(r):
    k = r["kind"]
    if k in ("above", "below"):
        s = "%s %s %s" % (r["symbol"], k, px(r["price"])) + (" (%s)" % r["label"] if r.get("label") else "")
        s += ", rearms" if r.get("rearm") else ""
    elif k == "pct_move":
        s = "%s moves %s%%%s" % (r["symbol"], r["pct"], (" " + r["direction"]) if r.get("direction") else "")
    elif k == "time":
        s = "%s at %s" % (r.get("label") or "time alert", r["at"]) + (" for %s" % r["symbol"] if r.get("symbol") else "")
    else:
        s = "opening bell"
    return s + (", on %s only" % r["date"] if r.get("date") else "")


def cmd_check(a):
    rules = load_rules(a.rules)
    if not rules:
        raise AlertError("no rules in %s: add some with alerts.py add --from-plan" % a.rules)
    now = tm.parse_now(a.now)
    st = clock_status(now)
    watched = [r for r in rules if active(r, now.date())]
    syms = list(dict.fromkeys(r["symbol"] for r in watched if r["kind"] not in NO_QUOTE))
    quotes, errors = get_quotes(syms, a.offline) if syms else ({}, [])
    rung, rules = evaluate(rules, quotes, now)
    save(a.rules, {"rules": rules})
    print("SET %s (%s Bangkok)." % (st["state"], now.strftime("%Y-%m-%d %H:%M")))
    for x in rung:
        print("ALERT " + x["message"])
        if not a.no_telegram:
            sent, why = notify.send_telegram(x["message"])
            print(("  Telegram: " + why) if sent else ("  Telegram not sent: " + why))
    if not rung:
        print("Nothing hit. %d rule(s) watched today%s." % (len(watched), "".join(
            ", %s %s (%s)" % (s, px(quotes[s]["price"]), delay_note(quotes[s], now)) for s in syms if s in quotes)))
    for e in errors:
        print("error: " + e)
    if rung:
        print("An alert is information, not an order. Nothing was placed.")
    return 0 if rung else 1


def cmd_list(a):
    rules = load_rules(a.rules)
    if not rules:
        print("No rules yet.")
        return 1
    for r in rules:
        print("%-22s %s%s" % (r["id"], describe_rule(r), ("  (rang %s)" % r["rang_at"]) if r.get("rang_at") else ""))
    return 0


def cmd_reset(a):
    rules = load_rules(a.rules)
    hit = [r for r in rules if r.get("id") == a.id]
    if not hit:
        raise AlertError("no rule %s (alerts.py list shows the ids)" % a.id)
    hit[0]["rang_at"], hit[0]["rang_on"] = None, None
    save(a.rules, {"rules": rules})
    print("Re-armed %s." % a.id)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Price and clock alerts for SET (th-trader-alerts).")
    ap.add_argument("--rules", default=RULES)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("clock", help="is SET open now")
    p.add_argument("--now")
    p = sub.add_parser("quote", help="last price and previous close")
    p.add_argument("symbols", nargs="*")
    p.add_argument("--plan", nargs="?", const="th-trader/plan.json")
    p.add_argument("--write", nargs="?", const=PRICES, metavar="PATH")
    p.add_argument("--offline", metavar="DIR", help="a folder of saved Yahoo charts, SYMBOL.json")
    p.add_argument("--now")
    p = sub.add_parser("add", help="add a rule")
    p.add_argument("--from-plan", action="store_true")
    p.add_argument("--plan", default="th-trader/plan.json")
    p.add_argument("--symbol")
    p.add_argument("--above")
    p.add_argument("--below")
    p.add_argument("--pct")
    p.add_argument("--direction", choices=["up", "down"])
    p.add_argument("--at", help="HH:MM Bangkok")
    p.add_argument("--label")
    p.add_argument("--opening-bell", action="store_true")
    p.add_argument("--date", help="YYYY-MM-DD: watch on this date only")
    p.add_argument("--rearm", action="store_true", help="re-arm when the price crosses back")
    p = sub.add_parser("check", help="ring what hit")
    p.add_argument("--now")
    p.add_argument("--offline", metavar="DIR")
    p.add_argument("--no-telegram", action="store_true")
    sub.add_parser("list", help="the rules")
    p = sub.add_parser("reset", help="re-arm a rule")
    p.add_argument("id")
    a = ap.parse_args(argv)
    tm.utf8_stdout()
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"clock": cmd_clock, "quote": cmd_quote, "add": cmd_add, "check": cmd_check, "list": cmd_list,
                "reset": cmd_reset}[a.cmd](a)
    except (AlertError, tm.MarketError, ValueError) as e:
        print("alerts.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
