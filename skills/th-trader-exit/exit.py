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
