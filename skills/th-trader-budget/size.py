"""Position sizing for th-trader-budget. Standard library only, no network.

Sizes ONE SET/mai trade from th-trader/plan.json (written by th-trader-exit) to the budget and the
max loss per trade in th-trader/config.json, with fees counted, in whole board lots. Writes a
broker-neutral order ticket to th-trader/orders.json for th-trader-risk and for the user to type
into their broker's app. It never places anything.

Ported from day-trader-skill's trader-budget (MIT, Jake Schincariol): the smallest-of-the-limits
rule and the report. The Alpaca bracket shape and the weights mode are not ported.

    python size.py                       # th-trader/plan.json + th-trader/config.json
    python size.py --write               # also write th-trader/orders.json
    python size.py --price 42.50         # the price ran up since the plan: size at 42.50
    python size.py --lot 50              # a stock at or above THB 500 for 6 months trades in lots of 50

Shares = the largest multiple of the board lot that fits all of
  max loss       max_loss_per_trade_thb / (stop distance + round-trip fee a share)
  position cap   budget_thb x max_position_pct / entry
  cash           budget_thb x (1 - cash_buffer_pct) / (entry + buy-side fee a share)
Zero lots is a plain refusal, not a smaller order. So is net R under min_reward_risk.
"""
import argparse
import json
import os
import sys
from decimal import ROUND_FLOOR, Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import th_market as tm
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "..", "..", "shared"))
    import th_market as tm


class SizeError(Exception):
    pass


def D(x, what="a number"):
    try:
        return Decimal(str(x))
    except Exception:
        raise SizeError("%s is not a number: %r" % (what, x))


def baht(x, places=2):
    return ("{:,.%df}" % places).format(Decimal(x))


def lots(shares, lot):
    return int((Decimal(shares) / lot).to_integral_value(rounding=ROUND_FLOOR)) * lot


def size_trade(plan, cfg, price=None, lot=None):
    """One plan + the rules -> the sizing. Raises SizeError for zero lots or a low net R."""
    sym = str(plan.get("symbol") or "").upper()
    if not sym:
        raise SizeError("the plan has no symbol (make one with th-trader-exit)")
    entry_plan, stop, target = D(plan.get("entry"), "entry"), D(plan.get("stop"), "stop"), D(plan.get("target"), "target")
    entry = entry_plan
    entry_from = "the plan's entry"
    if price is not None and D(price) > entry_plan:
        entry, entry_from = D(price), "your price, above the plan's entry"
    if stop >= entry:
        raise SizeError("the stop %s is at or above the price %s: no long trade to size" % (baht(stop), baht(entry)))
    lot = tm.board_lot(lot)
    n = tm.plan_numbers(entry, stop, target, cfg)
    min_rr = Decimal(str(cfg["min_reward_risk"]))
    if n["net_r"] < min_rr:
        raise SizeError("net R is %s at %s (the target %s minus fees, over the risk %s a share): under "
                        "min_reward_risk %s. Re-plan with th-trader-exit."
                        % (n["net_r"].quantize(Decimal("0.01")), baht(entry), baht(target),
                           n["risk_per_share"].quantize(Decimal("0.0001")), cfg["min_reward_risk"]))
    budget = Decimal(str(cfg["budget_thb"]))
    max_loss = Decimal(str(cfg["max_loss_per_trade_thb"]))
    buy_fee = tm.fee(entry, cfg)
    limits = {
        "max loss": lots(max_loss / n["risk_per_share"], lot),
        "position cap": lots(budget * Decimal(str(cfg["max_position_pct"])) / 100 / entry, lot),
        "cash": lots(budget * (1 - Decimal(str(cfg["cash_buffer_pct"])) / 100) / (entry + buy_fee), lot),
    }
    decided_by = min(limits, key=lambda k: limits[k])
    qty = limits[decided_by]
    if qty <= 0:
        one = n["risk_per_share"] * lot
        raise SizeError("%s: one lot of %d would lose THB %s at the stop with fees, over the %s limit "
                        "(max loss THB %s). Widen nothing; skip it, or set a bigger limit yourself."
                        % (decided_by, lot, baht(one, 0), decided_by, baht(max_loss, 0))
                        if decided_by == "max loss" else
                        "%s: not even one lot of %d fits (%s a share)." % (decided_by, lot, baht(entry)))
    return {"symbol": sym, "qty": qty, "lot": lot, "decided_by": decided_by, "limits": limits,
            "entry_plan": entry_plan, "entry_used": entry, "entry_from": entry_from, "stop": stop, "target": target,
            "risk_per_share": n["risk_per_share"], "max_loss_thb": n["risk_per_share"] * qty,
            "est_fees_round_trip": n["fee_at_stop"] * qty, "cost": entry * qty + buy_fee * qty,
            "net_r": n["net_r"], "gross_r": n["gross_r"]}


def ticket(s, plan):
    q2 = Decimal("0.01")
    return {"symbol": s["symbol"], "side": "buy", "qty": s["qty"], "type": "limit",
            "limit_price": str(s["entry_used"].quantize(q2)), "stop": str(s["stop"].quantize(q2)),
            "target": str(s["target"].quantize(q2)), "flat_by": plan.get("flat_by"),
            "est_fees_round_trip": str(s["est_fees_round_trip"].quantize(q2)),
            "max_loss_thb": str(s["max_loss_thb"].quantize(q2)), "net_r": float(round(s["net_r"], 4)),
            "decided_by": s["decided_by"], "plan_date": plan.get("date")}


def report(s, plan, cfg):
    hows = {"max loss": "THB %s / %s risk a share with fees" % (baht(cfg["max_loss_per_trade_thb"], 0),
                                                                 s["risk_per_share"].quantize(Decimal("0.0001"))),
            "position cap": "THB %s x %s%% / %s" % (baht(cfg["budget_thb"], 0), cfg["max_position_pct"],
                                                     baht(s["entry_used"])),
            "cash": "THB %s less %s%% buffer / (%s + buy fee)" % (baht(cfg["budget_thb"], 0), cfg["cash_buffer_pct"],
                                                                  baht(s["entry_used"]))}
    lines = ["%s buy, %s plan for %s: entry %s, stop %s, target %s." % (
        s["symbol"], plan.get("mode", "intraday"), plan.get("date"), baht(s["entry_plan"]), baht(s["stop"]),
        baht(s["target"])),
        "Sized at %s (%s). Risk %s a share with fees; net R %s." % (
            baht(s["entry_used"]), s["entry_from"], s["risk_per_share"].quantize(Decimal("0.0001")),
            s["net_r"].quantize(Decimal("0.01"))),
        "",
        "Limits (lots of %d; the smallest one wins):" % s["lot"]]
    for name, sh in s["limits"].items():
        lines.append("  %-13s %-52s %6s shares%s" % (name, hows[name], "{:,}".format(sh),
                                                     "  <- decides" if name == s["decided_by"] else ""))
    lines += ["",
              "Size: %s shares of %s = THB %s with the buy fee. Max loss at the stop: THB %s with fees."
              % ("{:,}".format(s["qty"]), s["symbol"], baht(s["cost"]), baht(s["max_loss_thb"])),
              "",
              "Ticket to type into your broker's app (nothing was placed):",
              "  BUY %s %s  limit %s" % (s["symbol"], "{:,}".format(s["qty"]), baht(s["entry_used"])),
              "  stop %s   target %s%s" % (baht(s["stop"]), baht(s["target"]),
                                           ("   flat by %s" % plan["flat_by"]) if plan.get("flat_by") else ""),
              "A stop is a plan, not a promise: in a fast market it can fill below %s." % baht(s["stop"]),
              tm.fees_line(cfg),
              "Next: th-trader-risk checks it. Nothing was placed. Education and tooling, not investment advice."]
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Budget (th-trader-budget): size a SET trade plan in board lots.")
    ap.add_argument("--plan", default="th-trader/plan.json")
    ap.add_argument("--config", default="th-trader/config.json")
    ap.add_argument("--price", help="the latest price, when it ran above the plan's entry")
    ap.add_argument("--lot", type=int, help="board lot: 100 (default) or 50")
    ap.add_argument("--write", nargs="?", const="th-trader/orders.json", metavar="PATH")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if not os.path.exists(a.plan):
            raise SizeError("no plan at %s: make one with th-trader-exit (exit.py plan --write)" % a.plan)
        with open(a.plan, encoding="utf-8") as fh:
            plan = json.load(fh)
        cfg = tm.load_config(a.config)
        s = size_trade(plan, cfg, price=D(a.price, "--price") if a.price else None, lot=a.lot)
    except (SizeError, tm.MarketError, ValueError) as e:
        print("size.py: %s" % e, file=sys.stderr)
        return 1
    t = ticket(s, plan)
    if a.write:
        d = os.path.dirname(a.write)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.write, "w", encoding="utf-8") as fh:
            json.dump({"created_by": "th-trader-budget", "created_at": tm.now_bkk().strftime("%Y-%m-%dT%H:%M:%S+07:00"),
                       "currency": "THB", "orders": [t]}, fh, indent=2, ensure_ascii=False)
    if a.json:
        print(json.dumps(t, indent=2, ensure_ascii=False))
        return 0
    for line in report(s, plan, cfg):
        print(line)
    if a.write:
        print("Wrote %s." % a.write)
    return 0


if __name__ == "__main__":
    sys.exit(main())
