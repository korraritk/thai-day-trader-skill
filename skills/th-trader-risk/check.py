"""Pre-trade risk check for th-trader-risk. Standard library only, no network.

Checks every order in th-trader/orders.json (from th-trader-budget) against the rules in
th-trader/config.json and today's trades in th-trader/state/trades.json, which the user logs
(no broker API is used). Each order gets PASS or BLOCK with the reasons. Orders are checked in
file order as if each PASS had filled. Getting out is never blocked: a sell is checked only for
going short; every other rule becomes a warning for it.

Ported from day-trader-skill's trader-risk (MIT, Jake Schincariol): the PASS/BLOCK idea, entries
versus exits, checking a batch as if each PASS filled. The Alpaca format checks are replaced by
SET rules: tick grid, daily floor and ceiling, board lots, sessions with a lunch break, fees.

    python check.py                                   # th-trader/orders.json -> th-trader/state/risk.json
    python check.py --now "2026-11-02 11:00"          # pin the Bangkok clock
    python check.py log buy PTT 700 42.25 --stop 41.75
    python check.py log sell PTT 700 43.00
    python check.py today                             # today's trades, open risk, realized result
"""
import argparse
import datetime
import json
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

TRADES = "th-trader/state/trades.json"
DUPLICATE_SECONDS = 60


class RiskError(Exception):
    pass


def D(x):
    return Decimal(str(x))


def baht(x, places=2):
    return ("{:,.%df}" % places).format(Decimal(x))


def hhmm(text):
    return datetime.datetime.strptime(text, "%H:%M").time()


# ---------------------------------------------------------------- today's trades

def positions(trades, cfg):
    """-> ({symbol: {qty, avg, stop}}, realized THB net of fees, buys today)."""
    held, realized, buys = {}, Decimal(0), 0
    for t in trades:
        sym, qty, price = t["symbol"].upper(), int(t["qty"]), D(t["price"])
        if t["side"] == "buy":
            buys += 1
            p = held.setdefault(sym, {"qty": 0, "avg": Decimal(0), "stop": None})
            p["avg"] = (p["avg"] * p["qty"] + price * qty) / (p["qty"] + qty)
            p["qty"] += qty
            if t.get("stop"):
                p["stop"] = D(t["stop"])
        else:
            p = held.get(sym)
            if not p:
                continue
            q = min(qty, p["qty"])
            realized += (price - p["avg"]) * q - tm.fee(p["avg"] * q, cfg) - tm.fee(price * q, cfg)
            p["qty"] -= q
            if p["qty"] == 0:
                del held[sym]
    return held, realized, buys


def open_risk(held, cfg):
    total = Decimal(0)
    for p in held.values():
        stop = p["stop"] if p["stop"] is not None else p["avg"]
        total += max(Decimal(0), (p["avg"] - stop) * p["qty"]) + tm.fee(p["avg"] * p["qty"], cfg) + tm.fee(stop * p["qty"], cfg)
    return total


def today_summary(trades, cfg):
    held, realized, buys = positions(trades, cfg)
    return {"realized_thb": realized, "buys": buys, "open_risk_thb": open_risk(held, cfg),
            "open": [{"symbol": s, "qty": p["qty"], "avg": p["avg"], "stop": p["stop"]} for s, p in held.items()]}


# ---------------------------------------------------------------- the check

def window_problem(now, cfg):
    """Why a new entry cannot go in now, or None."""
    sess = tm.sessions(now.date())
    if not sess:
        return "the market is closed on %s (%s)" % (now.date(), tm.holiday_name(now.date()) or now.strftime("%A"))
    (m_open, m_close), (a_open, a_close) = sess
    t = now.time()
    if t < m_open:
        return "the market is not open yet (it opens at %s)" % m_open.strftime("%H:%M")
    if m_close <= t < a_open:
        return "it is the lunch break (%s to %s)" % (m_close.strftime("%H:%M"), a_open.strftime("%H:%M"))
    if t >= hhmm(cfg["flat_by"]):
        return "it is after flat_by %s: no new entries, get flat" % cfg["flat_by"]
    if t >= a_close:
        return "the market has closed"
    if t > hhmm(cfg["no_entries_after"]):
        return "it is after no_entries_after %s" % cfg["no_entries_after"]
    return None


def lot_ok(qty, price):
    return qty > 0 and (qty % 100 == 0 or (qty % 50 == 0 and price >= 500))


def check(orders, cfg, trades_today, now, prev_closes=None):
    """One {symbol, side, verdict, reasons, warnings} per order, in order, as if each PASS filled."""
    trades = list(trades_today)
    budget = D(cfg["budget_thb"])
    results = []
    seen_buys = set()
    for o in orders:
        sym = str(o.get("symbol") or "").upper()
        side = str(o.get("side") or "").lower()
        qty = int(o.get("qty") or 0)
        price = D(o.get("limit_price") or "0")
        reasons, warnings = [], []
        held, realized, buys = positions(trades, cfg)
        win = window_problem(now, cfg)
        if side == "sell":
            have = held.get(sym, {}).get("qty", 0)
            if qty > have:
                reasons.append("short: selling %d %s with %d held (long only)" % (qty, sym, have))
            if win:
                warnings.append(win + " (a sell to get out is never blocked)")
            if sym in [b.upper() for b in cfg["blocked"]]:
                warnings.append("%s is on your blocked list" % sym)
        elif side == "buy":
            stop = D(o["stop"]) if o.get("stop") else None
            target = D(o["target"]) if o.get("target") else None
            if stop is None or stop >= price:
                reasons.append("stop: a buy needs a stop under the limit price")
            if price <= 0 or tm.round_tick(price, now.date()) != price:
                reasons.append("price: %s is not on SET's tick grid (tick %s)" % (o.get("limit_price"), tm.tick(price, now.date()) if price > 0 else "?"))
            if prev_closes and sym in prev_closes:
                floor, ceiling = tm.price_limits(prev_closes[sym], now.date())
                if not floor <= price <= ceiling:
                    reasons.append("price: %s is outside today's floor %s and ceiling %s" % (baht(price), baht(floor), baht(ceiling)))
            elif not prev_closes:
                warnings.append("today's floor and ceiling were not checked (no previous close given)")
            if not lot_ok(qty, price):
                reasons.append("lot: %d is not a whole number of board lots (100, or 50 at THB 500+)" % qty)
            risk_thb = Decimal(0)
            if stop is not None and stop < price:
                if target is not None and target > price:
                    n = tm.plan_numbers(price, stop, target, cfg)
                    risk_thb = n["risk_per_share"] * qty
                    if n["net_r"] < D(cfg["min_reward_risk"]):
                        reasons.append("reward: net R %s is under min_reward_risk %s"
                                       % (n["net_r"].quantize(Decimal("0.01")), cfg["min_reward_risk"]))
                else:
                    risk_thb = ((price - stop) + tm.round_trip_fee_per_share(price, stop, cfg)) * qty
                    reasons.append("reward: net R cannot be checked without a target above the price")
                if risk_thb > D(cfg["max_loss_per_trade_thb"]):
                    reasons.append("loss: THB %s at the stop with fees is over max loss THB %s"
                                   % (baht(risk_thb), baht(cfg["max_loss_per_trade_thb"], 0)))
            day_total = -min(realized, Decimal(0)) + open_risk(held, cfg) + risk_thb
            if day_total > D(cfg["max_daily_loss_thb"]):
                reasons.append("daily loss: realized THB %s + open risk THB %s + this THB %s = %s, over %s"
                               % (baht(-min(realized, Decimal(0))), baht(open_risk(held, cfg)), baht(risk_thb),
                                  baht(day_total), baht(cfg["max_daily_loss_thb"], 0)))
            if buys >= int(cfg["max_trades_per_day"]):
                reasons.append("trades: %d buys today already (max_trades_per_day %s)" % (buys, cfg["max_trades_per_day"]))
            if win:
                reasons.append("window: " + win)
            value = price * qty
            if value > budget * D(cfg["max_position_pct"]) / 100:
                reasons.append("position: THB %s is over max_position_pct %s%% of THB %s"
                               % (baht(value), cfg["max_position_pct"], baht(budget, 0)))
            invested = sum((p["avg"] * p["qty"] for p in held.values()), Decimal(0))
            if invested + value + tm.fee(value, cfg) > budget * (1 - D(cfg["cash_buffer_pct"]) / 100):
                reasons.append("cash: THB %s held + THB %s would dip into the %s%% cash buffer of THB %s"
                               % (baht(invested), baht(value), cfg["cash_buffer_pct"], baht(budget, 0)))
            if sym in [b.upper() for b in cfg["blocked"]]:
                reasons.append("blocked: %s is on your blocked list" % sym)
            if any(t["side"] == "buy" and t["symbol"].upper() == sym for t in trades_today):
                reasons.append("duplicate: %s already bought today" % sym)
            elif sym in seen_buys:
                reasons.append("duplicate: %s appears twice in this batch" % sym)
        else:
            reasons.append("side: %r is not buy or sell" % o.get("side"))
        ok = not reasons
        results.append({"symbol": sym, "side": side, "qty": qty, "verdict": "PASS" if ok else "BLOCK",
                        "reasons": reasons, "warnings": warnings})
        if ok:
            if side == "buy":
                seen_buys.add(sym)
            trades.append({"side": side, "symbol": sym, "qty": qty, "price": str(price),
                           "stop": o.get("stop"), "at": now.strftime("%H:%M")})
    return results


# ---------------------------------------------------------------- the trade log

def load_trades(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except ValueError as e:
        raise RiskError("%s is not valid JSON: %s" % (path, e))
    return doc if isinstance(doc, dict) else {}


def save_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False, default=str)


def log_trade(path, side, symbol, qty, price, stop=None, now=None, cfg=None):
    now = now or tm.now_bkk()
    cfg = cfg or tm.load_config("nonexistent.json")
    side, sym, qty, price = side.lower(), symbol.upper(), int(qty), D(price)
    if side not in ("buy", "sell"):
        raise RiskError("log wants buy or sell, not %r" % side)
    if qty <= 0 or price <= 0:
        raise RiskError("log wants a positive quantity and price")
    doc = load_trades(path)
    day = now.date().isoformat()
    today = doc.get(day, [])
    for t in today:
        if (t["side"], t["symbol"], int(t["qty"]), D(t["price"])) == (side, sym, qty, price):
            at = datetime.datetime.combine(now.date(), hhmm(t["at"][:5]))
            if abs((now - at).total_seconds()) <= DUPLICATE_SECONDS:
                raise RiskError("duplicate: the same %s %d %s at %s was logged at %s" % (side, qty, sym, baht(price), t["at"]))
    if side == "sell":
        held = positions(today, cfg)[0].get(sym, {}).get("qty", 0)
        if held <= 0:
            raise RiskError("no open position in %s today to sell (long only)" % sym)
        if qty > held:
            raise RiskError("selling %d %s with %d held (long only)" % (qty, sym, held))
    rec = {"side": side, "symbol": sym, "qty": qty, "price": str(price), "at": now.strftime("%H:%M:%S")}
    if stop is not None:
        rec["stop"] = str(D(stop))
    doc[day] = today + [rec]
    save_json(path, doc)
    return rec


# ---------------------------------------------------------------- commands

def report(results):
    lines = []
    for r in results:
        lines.append("%s  %s %s %d" % (r["verdict"], r["side"].upper(), r["symbol"], r["qty"]))
        for x in r["reasons"]:
            lines.append("    BLOCK " + x)
        for w in r["warnings"]:
            lines.append("    note  " + w)
    return lines


def cmd_check(a):
    now = tm.parse_now(a.now)
    cfg = tm.load_config(a.config)
    if not os.path.exists(a.orders):
        raise RiskError("no orders at %s: size a plan with th-trader-budget (size.py --write)" % a.orders)
    with open(a.orders, encoding="utf-8") as fh:
        orders = json.load(fh).get("orders", [])
    trades = load_trades(a.trades).get(now.date().isoformat(), [])
    prev = {k.upper(): D(v) for k, v in (dict(x.split("=", 1) for x in a.prev_close.split(",")) if a.prev_close else {}).items()}
    if not prev and a.prices:  # any age is fine for a previous close
        prev = {s: p["prev_close"] for s, p in tm.read_prices(a.prices, now, max_age_min=None).items()
                if p["prev_close"] is not None}
        if prev and not a.json:
            print("Previous closes from %s." % a.prices)
    results = check(orders, cfg, trades, now, prev or None)
    if not a.no_write:
        save_json(a.out, {"checked_at": now.strftime("%Y-%m-%dT%H:%M:%S+07:00"), "results": results})
    if a.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("Risk check, %s Bangkok, %d order%s, %d logged trade%s today:" % (
            now.strftime("%a %Y-%m-%d %H:%M"), len(orders), "" if len(orders) == 1 else "s", len(trades),
            "" if len(trades) == 1 else "s"))
        for line in report(results):
            print(line)
        print(tm.fees_line(cfg))
        print("A PASS is a rules check, not a recommendation. Nothing was placed.")
    return 0 if results and all(r["verdict"] == "PASS" for r in results) else 1


def cmd_log(a):
    now = tm.parse_now(a.now)
    rec = log_trade(a.trades, a.side, a.symbol, a.qty, a.price, a.stop, now, tm.load_config(a.config))
    print("Logged %s %s %s at %s%s (%s)." % (rec["side"], rec["qty"], rec["symbol"], rec["price"],
                                             (", stop " + rec["stop"]) if rec.get("stop") else "", rec["at"]))
    return 0


def cmd_today(a):
    now = tm.parse_now(a.now)
    cfg = tm.load_config(a.config)
    trades = load_trades(a.trades).get(now.date().isoformat(), [])
    s = today_summary(trades, cfg)
    print("Today %s: %d trade%s logged, %d buy%s." % (now.date(), len(trades), "" if len(trades) == 1 else "s",
                                                      s["buys"], "" if s["buys"] == 1 else "s"))
    print("Realized: THB %s net of fees." % baht(s["realized_thb"]))
    for p in s["open"]:
        print("Open: %s %d at %s, stop %s" % (p["symbol"], p["qty"], baht(p["avg"]), baht(p["stop"]) if p["stop"] else "none"))
    print("Open risk to the stops: THB %s. Daily loss limit: THB %s." % (baht(s["open_risk_thb"]), baht(cfg["max_daily_loss_thb"], 0)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Risk check (th-trader-risk): PASS or BLOCK, and the trade log.")
    ap.add_argument("--config", default="th-trader/config.json")
    ap.add_argument("--trades", default=TRADES)
    ap.add_argument("--now", help='pretend it is this Bangkok time, e.g. "2026-11-02 11:00"')
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("check", help="check th-trader/orders.json (the default)")
    p.add_argument("--orders", default="th-trader/orders.json")
    p.add_argument("--out", default="th-trader/state/risk.json")
    p.add_argument("--prev-close", help="SYMBOL=price,... for the floor/ceiling check")
    p.add_argument("--prices", default="th-trader/prices.json",
                   help="previous closes from th-trader-alerts (quote --write) when --prev-close is not given")
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("log", help="record a trade you made")
    p.add_argument("side", choices=["buy", "sell"])
    p.add_argument("symbol")
    p.add_argument("qty", type=int)
    p.add_argument("price")
    p.add_argument("--stop")
    sub.add_parser("today", help="today's trades and open risk")
    argv = list(sys.argv[1:] if argv is None else argv)
    if not any(x in ("check", "log", "today") for x in argv):
        argv.append("check")
    a = ap.parse_args(argv)
    try:
        return {"check": cmd_check, "log": cmd_log, "today": cmd_today}[a.cmd](a)
    except (RiskError, tm.MarketError, ValueError) as e:
        print("check.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
