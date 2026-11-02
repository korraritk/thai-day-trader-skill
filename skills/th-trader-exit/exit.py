"""Exit Planner for th-trader-exit: sets the stop and the target BEFORE you enter, so you know
when to get out. SET/mai, long only, fees counted. Standard library only. It never places an order.

Ported from day-trader-skill's trader-exit (MIT, Jake Schincariol). Thai rules (tick sizes, price
limits, Bangkok time, fees) come from th_market.py.

    python exit.py plan --symbol ADVANC                 # its orb hit in th-trader/watchlist.json
    python exit.py plan --symbol PTT --mode swing       # its daily hit: pct:5 stop, 10 days
    python exit.py plan --symbol PTT --entry 42.25 --or-high 42.25 --or-low 41.50 --write
    python exit.py plan --stop atr:1.5 --target 1.5R
    python exit.py show                                 # the current th-trader/plan.json

Stops round DOWN to a valid SET price: or-mid (intraday default), or-low, price:X, pct:X (swing
default pct:5), atr:K. Targets round to the nearest valid price: 2R (default), 1R, 1.5R ..., price:X.
Refused, with the reason: a stop at or above the entry, a target at or below it, a stop under the
day's floor or a target over its ceiling, a round-trip fee of half the stop distance or more.
A stop is a plan, not a guarantee: a gap or a fast market can fill past it.
"""
import argparse
import datetime
import json
import os
import re
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import th_market as tm
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "..", "..", "shared"))
    import th_market as tm

ATR_BARS = 14
FEE_WARN = Decimal("0.25")
FEE_REFUSE = Decimal("0.50")
NOTE = "A stop is a plan, not a guarantee: a gap or a fast market can fill past it."


class PlanError(Exception):
    pass


# ---------------------------------------------------------------- parsing (ported unchanged)

def D(x, what="a number"):
    try:
        if isinstance(x, float):
            return Decimal(repr(x))
        return Decimal(str(x).strip().lstrip("$").replace(",", ""))
    except (InvalidOperation, ValueError):
        raise PlanError("%s is not a number: %r" % (what, x))


def num(x):
    """1, 1.5, 2.25: a Decimal without trailing zeros."""
    s = format(D(x).normalize(), "f")
    return s


def ratio(x):
    """reward:risk for people: 1.0, 1.5, 1.37."""
    s = "%.2f" % float(x)
    return s[:-1] if s.endswith("0") else s


WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def parse_stop(text):
    t = str(text).strip().lower()
    if t in ("or-mid", "or_mid", "mid", "or-midpoint"):
        return "or_mid", None
    if t in ("or-low", "or_low", "low"):
        return "or_low", None
    m = re.match(r"^(price|pct|atr):\s*\$?([0-9][0-9,]*\.?[0-9]*)$", t)
    if not m:
        raise PlanError("--stop is or-mid, or-low, price:X, pct:X or atr:K (got %r)" % text)
    kind, val = m.group(1), D(m.group(2), "--stop")
    if val <= 0:
        raise PlanError("--stop %s needs a number above 0" % kind)
    if kind == "pct" and val >= 100:
        raise PlanError("--stop pct:X needs X under 100")
    return kind, val


def parse_target(text):
    t = str(text).strip()
    m = re.match(r"^([0-9]*\.?[0-9]+)\s*[Rr]$", t)
    if m:
        r = D(m.group(1), "--target")
        if r <= 0:
            raise PlanError("--target needs an R multiple above 0, like 1R or 2R")
        return "r", r
    m = re.match(r"^price:\s*\$?([0-9][0-9,]*\.?[0-9]*)$", t.lower())
    if m:
        return "price", D(m.group(1), "--target")
    raise PlanError("--target is 2R, 1R, 1.5R ... or price:X (got %r)" % text)


def r_label(r):
    n = int(r) if r == r.to_integral_value() else None
    return "%sR (%s times the risk)" % (num(r), WORDS.get(n, num(r)))


# ---------------------------------------------------------------- the plan

def baht(x):
    return "{:,.2f}".format(Decimal(x))


def compute(entry, stop_rule, stop_param, target_rule, target_param, day, cfg, or_high=None, or_low=None,
            atr=None, prev_close=None):
    """The stop, the target, R and fees on SET's tick grid for `day`. Raises PlanError with the reason."""
    e = D(entry, "the entry")
    if e <= 0:
        raise PlanError("the entry price must be above 0")
    if tm.round_tick(e, day) != e:
        raise PlanError("the entry %s is not a valid SET price on %s (tick %s)" % (baht(e), day, tm.tick(e, day)))
    detail = ""
    if stop_rule in ("or_mid", "or_low"):
        if or_high is None or or_low is None:
            raise PlanError("an %s stop needs the opening range (--or-high and --or-low)" % stop_rule.replace("_", "-"))
        oh, ol = D(or_high), D(or_low)
        if oh <= ol:
            raise PlanError("the opening-range high %s must be above its low %s" % (baht(oh), baht(ol)))
        if stop_rule == "or_mid":
            raw, label = (oh + ol) / 2, "opening-range midpoint"
        else:
            raw, label = ol, "opening-range low"
    elif stop_rule == "price":
        raw, label = D(stop_param), "your price"
    elif stop_rule == "pct":
        raw, label = e * (1 - stop_param / 100), "%s%% under the entry" % num(stop_param)
    elif stop_rule == "atr":
        if atr is None:
            raise PlanError("an atr stop needs the bars before the entry")
        raw, label = e - stop_param * D(round(atr, 6)), "%s x ATR(%d) under the entry" % (num(stop_param), ATR_BARS)
    else:
        raise PlanError("unknown stop rule %r" % stop_rule)
    if raw <= 0:
        raise PlanError("the stop works out to %s: pick a stop above 0" % baht(raw))
    stop = tm.round_down_tick(raw, day)
    if stop != raw:
        detail = "%s rounded down to the tick" % num(raw.quantize(Decimal("0.0001")))
    if stop >= e:
        raise PlanError("the stop %s (%s) is at or above the entry %s: a long's stop goes under the entry"
                        % (baht(stop), label, baht(e)))
    if target_rule == "r":
        target = tm.round_tick(e + target_param * (e - stop), day)
        t_label = r_label(target_param)
    else:
        target = tm.round_tick(D(target_param), day)
        t_label = "your price"
    if target <= e:
        raise PlanError("the target %s is at or below the entry %s: a long's target goes above it"
                        % (baht(target), baht(e)))
    if prev_close is not None:
        floor, ceiling = tm.price_limits(D(prev_close), day)
        if stop < floor:
            raise PlanError("the stop %s is under today's floor %s (-30%% of %s): it cannot fill there"
                            % (baht(stop), baht(floor), baht(prev_close)))
        if target > ceiling:
            raise PlanError("the target %s is over today's ceiling %s (+30%% of %s): it cannot fill there"
                            % (baht(target), baht(ceiling), baht(prev_close)))
    n = tm.plan_numbers(e, stop, target, cfg)
    share = n["fee_at_stop"] / (e - stop)
    if share >= FEE_REFUSE:
        raise PlanError("the round-trip fee %s a share is %s%% of the %s stop distance: fees alone would eat "
                        "half the risk. Widen the stop or skip this one."
                        % (num(n["fee_at_stop"].quantize(Decimal("0.0001"))), (share * 100).quantize(Decimal("1")),
                           baht(e - stop)))
    warnings = []
    if share >= FEE_WARN:
        warnings.append("the round-trip fee is %s%% of the stop distance" % (share * 100).quantize(Decimal("1")))
    if n["net_r"] < Decimal(str(cfg["min_reward_risk"])):
        warnings.append("net R %s is under min_reward_risk %s (th-trader-risk will block it)"
                        % (ratio(n["net_r"]), cfg["min_reward_risk"]))
    out = {"entry": e, "stop": stop, "stop_rule": stop_rule, "stop_label": label, "stop_detail": detail,
           "target": target, "target_rule": target_rule, "target_label": t_label,
           "tick_table": tm.tick_table_label(day), "fee_share": share, "warnings": warnings}
    out.update(n)
    return out


# ---------------------------------------------------------------- bars for an ATR stop

def atr_of(bars, n=ATR_BARS):
    """Average true range of the last n bars (all of them if fewer). The first
    bar uses its own high - low. Ported from trader-exit."""
    trs, prev = [], None
    for b in bars:
        tr = b["h"] - b["l"]
        if prev is not None:
            tr = max(tr, abs(b["h"] - prev), abs(b["l"] - prev))
        trs.append(tr)
        prev = b["c"]
    use = trs[-n:]
    return sum(use) / len(use), len(use)


def read_json(path, what):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        raise PlanError("no %s at %s" % (what, path))
    except ValueError as e:
        raise PlanError("%s is not valid JSON: %s" % (path, e))


def load_series(path, symbol):
    return tm.parse_chart(read_json(path, "chart file"), symbol)


# ---------------------------------------------------------------- gathering the entry

def gather(a, now):
    """-> dict with symbol, entry, mode, date, entry_time, or_high, or_low, or_minutes, setups, source."""
    if a.entry is not None:
        if not a.symbol:
            raise PlanError("give --symbol with --entry")
        return {"symbol": a.symbol.upper(), "entry": D(a.entry), "mode": a.mode or "intraday",
                "date": a.date or now.date().isoformat(), "entry_time": a.time,
                "or_high": a.or_high, "or_low": a.or_low, "or_minutes": a.or_minutes, "setups": [],
                "source": "flags"}
    if a.symbol and os.path.exists(a.watchlist):
        doc = read_json(a.watchlist, "watchlist")
        hit = next((h for h in doc.get("hits", []) if h.get("symbol", "").upper() == a.symbol.upper()), None)
        if hit:
            if doc.get("mode") == "orb":
                return {"symbol": hit["symbol"], "entry": D(hit["price"]), "mode": a.mode or "intraday",
                        "date": str(doc.get("as_of") or now.date().isoformat()).split()[0],
                        "entry_time": hit.get("at"), "or_high": hit.get("or_high"), "or_low": hit.get("or_low"),
                        "or_minutes": doc.get("or_minutes", a.or_minutes), "setups": hit.get("setups", []),
                        "source": a.watchlist}
            return {"symbol": hit["symbol"], "entry": D(hit["price"]), "mode": a.mode or "swing",
                    "date": hit.get("close_date") or now.date().isoformat(), "entry_time": None,
                    "or_high": None, "or_low": None, "or_minutes": a.or_minutes, "setups": hit.get("setups", []),
                    "source": a.watchlist}
    if os.path.exists(a.entry_file):
        doc = read_json(a.entry_file, "entry file")
        if not a.symbol or doc.get("symbol", "").upper() == a.symbol.upper():
            if not doc.get("triggered"):
                raise PlanError("%s has not triggered yet: wait for the entry, or give --entry" % a.entry_file)
            return {"symbol": doc["symbol"], "entry": D(doc["price"]), "mode": a.mode or "intraday",
                    "date": doc.get("date") or now.date().isoformat(), "entry_time": doc.get("at"),
                    "or_high": doc.get("or_high"), "or_low": doc.get("or_low"),
                    "or_minutes": doc.get("or_minutes", a.or_minutes), "setups": doc.get("setups", []),
                    "source": a.entry_file}
    raise PlanError("no entry to plan from: give --symbol with a hit in %s, an entry in %s, or --entry"
                    % (a.watchlist, a.entry_file))


# ---------------------------------------------------------------- the plan document

def s2(x):
    return str(Decimal(x).quantize(Decimal("0.01")))


def s4(x):
    return str(Decimal(x).quantize(Decimal("0.0001")))


def market_context(a, g, day, swing, stop_rule, now):
    """-> (prev_close or None, atr or None, notes). Yahoo or saved charts."""
    notes = []
    if a.no_fetch:
        return None, None, ["Yahoo not asked (--no-fetch): today's floor and ceiling were not checked."]
    prev_close = atr = None
    try:
        daily = load_series(a.offline_daily, g["symbol"]) if a.offline_daily else tm.fetch_chart(g["symbol"], "3mo", "1d")
        dbars = [b for b in tm.daily_bars(daily, now) if b["date"] < day]
        prev_close = D(dbars[-1]["c"]) if dbars else None
        if stop_rule == "atr" and swing and dbars:
            atr = atr_of(dbars)[0]
        if stop_rule == "atr" and not swing:
            s5 = load_series(a.offline_5m, g["symbol"]) if a.offline_5m else tm.fetch_chart(g["symbol"], "5d", "5m")
            t = datetime.datetime.strptime(g["entry_time"] or "16:30", "%H:%M").time()
            ibars = tm.intraday_bars(s5, day, datetime.datetime.combine(day, t))
            atr = atr_of(ibars)[0] if ibars else None
    except tm.FetchError as e:
        notes.append("Yahoo gave no chart (%s): today's floor and ceiling were not checked." % e)
    if swing:
        prev_close = None  # a swing plan spans days: one day's limits do not bound it
        notes.append("Swing plan: the daily +/-30% limits apply day by day, not to the whole plan.")
    return prev_close, atr, notes


def build_plan(a, now):
    cfg = tm.load_config(a.config)
    g = gather(a, now)
    day = datetime.date.fromisoformat(g["date"])
    swing = g["mode"] == "swing"
    stop_text = a.stop or ("pct:5" if swing else "or-mid")
    target_text = a.target or "2R"
    stop_rule, stop_param = parse_stop(stop_text)
    target_rule, target_param = parse_target(target_text)
    prev_close, atr, notes = market_context(a, g, day, swing, stop_rule, now)
    c = compute(g["entry"], stop_rule, stop_param, target_rule, target_param, day, cfg,
                or_high=g["or_high"], or_low=g["or_low"], atr=atr, prev_close=prev_close)
    plan = {"created_by": "th-trader-exit", "created_at": now.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
            "symbol": g["symbol"], "mode": g["mode"], "date": g["date"], "entry_time": g["entry_time"],
            "entry": s2(c["entry"]), "stop": s2(c["stop"]), "stop_rule": stop_text.lower(),
            "stop_label": c["stop_label"], "stop_detail": c["stop_detail"],
            "target": s2(c["target"]), "target_rule": target_text.upper() if target_rule == "r" else target_text,
            "target_label": c["target_label"],
            "target_r": float(target_param) if target_rule == "r" else float(round(c["gross_r"], 4)),
            "risk_per_share": s4(c["risk_per_share"]), "reward_per_share": s4(c["reward_per_share"]),
            "fee_round_trip_per_share": s4(c["fee_at_stop"]), "fee_share_of_stop": float(round(c["fee_share"], 4)),
            "gross_r": float(round(c["gross_r"], 4)), "net_r": float(round(c["net_r"], 4)),
            "or_high": g["or_high"], "or_low": g["or_low"], "or_minutes": g["or_minutes"], "setups": g["setups"],
            "prev_close": s2(prev_close) if prev_close is not None else None,
            "tick_table": c["tick_table"], "currency": "THB", "source": g["source"],
            "commission_pct": cfg["commission_pct"]}
    if swing:
        plan["days"] = a.days
    else:
        plan["flat_by"] = a.flat_by or cfg["flat_by"]
    return plan, c["warnings"], notes


def describe(plan):
    day = datetime.date.fromisoformat(plan["date"])
    e = Decimal(plan["entry"])
    kind = "intraday" if plan["mode"] == "intraday" else "swing, up to %d trading days" % plan["days"]
    out = ["Exit plan: %s long, %s, %s" % (plan["symbol"], day.strftime("%a %Y-%m-%d"), kind)]
    when = (" at %s" % plan["entry_time"]) if plan.get("entry_time") else ""
    out.append("  Entry         %s%s" % (baht(plan["entry"]), when))
    out.append("  Stop          %s  %s%s" % (baht(plan["stop"]), plan["stop_label"],
                                             (": " + plan["stop_detail"]) if plan.get("stop_detail") else ""))
    out.append("  Target        %s  %s" % (baht(plan["target"]), plan["target_label"]))
    out.append("  Risk a share  %s with fees (%s%% of the entry)"
               % (plan["risk_per_share"], (Decimal(plan["risk_per_share"]) / e * 100).quantize(Decimal("0.01"))))
    share = Decimal(str(plan["fee_share_of_stop"])) * 100
    out.append("  Fees          %s a share round trip = %s%% of the stop distance%s"
               % (plan["fee_round_trip_per_share"], share.quantize(Decimal("1")), "  (!)" if share >= 25 else ""))
    out.append("  R             %s gross, %s net of fees" % (ratio(plan["gross_r"]), ratio(plan["net_r"])))
    if plan["mode"] == "intraday":
        out.append("  Flat by       %s Bangkok, win or lose: no overnight hold" % plan["flat_by"])
    out.append("Set before the entry. If price hits the stop, you are out; never move it further away.")
    out.append(NOTE)
    return out


# ---------------------------------------------------------------- commands

def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def cmd_plan(a):
    now = tm.parse_now(a.now)
    plan, warnings, notes = build_plan(a, now)
    if a.write:
        write_json(a.write, plan)
    if a.json:
        print(json.dumps(dict(plan, warnings=warnings, notes=notes), indent=2, ensure_ascii=False))
        return 0
    for line in describe(plan):
        print(line)
    for w in warnings:
        print("Warning: " + w)
    for n in notes:
        print("Note: " + n)
    print(tm.fees_line(tm.load_config(a.config)))
    if "fallback" in plan["tick_table"]:
        print(tm.TICK_FALLBACK_NOTE)
    if a.write:
        print("Wrote %s." % a.write)
    print("Next: th-trader-timemachine replays it, then th-trader-budget sizes it. Nothing was placed.")
    return 0


def cmd_show(a):
    if not os.path.exists(a.plan):
        print("No plan at %s yet. Make one: exit.py plan --write" % a.plan)
        return 1
    plan = read_json(a.plan, "plan")
    if a.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0
    for line in describe(plan):
        print(line)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Exit Planner (th-trader-exit): the stop and target before the entry.")
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("plan", help="set the stop and target")
    p.add_argument("--symbol")
    p.add_argument("--entry", help="entry price (instead of a watchlist hit or entry.json)")
    p.add_argument("--or-high")
    p.add_argument("--or-low")
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--mode", choices=["intraday", "swing"])
    p.add_argument("--days", type=int, default=10, help="swing: trading days to hold at most (10)")
    p.add_argument("--date", help="session date YYYY-MM-DD (default: the hit's, or today)")
    p.add_argument("--time", help="entry time HH:MM Bangkok")
    p.add_argument("--stop", help="or-mid (intraday default), or-low, price:X, pct:X (swing default pct:5), atr:K")
    p.add_argument("--target", help="2R (default), 1R, 1.5R, price:X")
    p.add_argument("--flat-by", help="HH:MM Bangkok (default: config flat_by, 16:20)")
    p.add_argument("--watchlist", default="th-trader/watchlist.json")
    p.add_argument("--entry-file", default="th-trader/entry.json")
    p.add_argument("--config", default="th-trader/config.json")
    p.add_argument("--offline-daily", metavar="PATH", help="saved Yahoo daily chart (prev close, swing ATR)")
    p.add_argument("--offline-5m", metavar="PATH", help="saved Yahoo 5-minute chart (intraday ATR)")
    p.add_argument("--no-fetch", action="store_true", help="do not ask Yahoo (no floor/ceiling check, no ATR)")
    p.add_argument("--now", help='pretend it is this Bangkok time, e.g. "2026-10-30 10:25"')
    p.add_argument("--write", nargs="?", const="th-trader/plan.json", metavar="PATH",
                   help="save the plan (default th-trader/plan.json)")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("show", help="print the current plan")
    p.add_argument("--plan", default="th-trader/plan.json")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"plan": cmd_plan, "show": cmd_show}[a.cmd](a)
    except (PlanError, tm.MarketError) as e:
        print("exit.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
