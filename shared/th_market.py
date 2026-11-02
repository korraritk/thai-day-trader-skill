"""Thai market rules for the th-trader skills: Bangkok time, the SET calendar and sessions,
tick sizes, price limits, board lot, and Yahoo .BK data.

Standard library only. Every datetime here is naive Bangkok time unless its name says utc.
Thailand has no daylight saving, so Bangkok is a fixed UTC+7 and no tz database is needed.
"""
import datetime
import json
import os
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
BKK_OFFSET = datetime.timedelta(hours=7)
SESSIONS = [(datetime.time(10, 0), datetime.time(12, 30)), (datetime.time(14, 0), datetime.time(16, 30))]

_HOLIDAYS = None


class MarketError(Exception):
    """A Thai market rule cannot be applied: say why in one plain line."""


def data_path(name):
    return os.path.join(DATA_DIR, name)


# ---------------------------------------------------------------- time

def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)


def to_bkk(utc):
    """UTC (naive or aware) -> naive Bangkok time."""
    if utc.tzinfo is not None:
        utc = (utc - utc.utcoffset()).replace(tzinfo=None)
    return utc + BKK_OFFSET


def now_bkk():
    return to_bkk(now_utc())


def ts_to_bkk(ts):
    return datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts)) + BKK_OFFSET


def parse_now(text):
    """None -> now. "2026-10-30 10:20" is Bangkok time; a trailing Z means UTC."""
    if not text:
        return now_bkk()
    t = text.strip()
    utc = t.endswith("Z")
    if utc:
        t = t[:-1]
    try:
        dt = datetime.datetime.fromisoformat(t.replace("T", " "))
    except ValueError:
        raise MarketError('cannot read the time "%s": use "YYYY-MM-DD HH:MM" (Bangkok) or ...Z (UTC)' % text)
    return to_bkk(dt) if utc else dt.replace(tzinfo=None)


# ---------------------------------------------------------------- calendar

def _holidays():
    global _HOLIDAYS
    if _HOLIDAYS is None:
        with open(data_path("set-holidays.json"), encoding="utf-8") as fh:
            _HOLIDAYS = {datetime.date.fromisoformat(h["date"]): h["name"] for h in json.load(fh)}
    return _HOLIDAYS


def _check_year(d):
    last = max(h.year for h in _holidays())
    if d.year > last:
        raise MarketError("SET holidays for %d are not in set-holidays.json yet: add them from SET's calendar."
                          % d.year)


def holiday_name(d):
    _check_year(d)
    return _holidays().get(d)


def is_trading_day(d):
    if d.weekday() >= 5:
        return False
    return holiday_name(d) is None


def sessions(d):
    """[(open, close), ...] for the morning and afternoon sessions, or [] on a closed day."""
    return list(SESSIONS) if is_trading_day(d) else []


def slots(d, interval=5):
    """Bar start times of both sessions in order, skipping the lunch break."""
    out = []
    step = datetime.timedelta(minutes=interval)
    for start, end in sessions(d):
        t = datetime.datetime.combine(d, start)
        stop = datetime.datetime.combine(d, end)
        while t < stop:
            out.append(t)
            t += step
    return out


# ---------------------------------------------------------------- tick sizes, price limits, board lot

_TICK_FILE = data_path("tick-tables.json")
_TICK_TABLES = None
TICK_FALLBACK_NOTE = ("SET's tick table from 2026-11-16 is not in tick-tables.json yet; "
                      "prices were rounded with the 2009 table.")
LIMIT_PCT = Decimal("0.30")
BOARD_LOTS = (50, 100)


def _tick_tables():
    global _TICK_TABLES
    if _TICK_TABLES is None:
        with open(_TICK_FILE, encoding="utf-8") as fh:
            raw = json.load(fh)
        tables = []
        for t in raw:
            tables.append({
                "name": t["name"],
                "from": datetime.date.fromisoformat(t["valid_from"]),
                "to": datetime.date.fromisoformat(t["valid_to"]) if t.get("valid_to") else None,
                "verified": t.get("status") == "verified" and bool(t.get("bands")),
                "bands": sorted(((Decimal(b["from"]), Decimal(b["tick"])) for b in t.get("bands", [])),
                                key=lambda b: b[0]),
            })
        _TICK_TABLES = sorted(tables, key=lambda t: t["from"])
    return _TICK_TABLES


def _table_for(d):
    """(table used, fell back?) for date d."""
    tables = _tick_tables()
    valid = [t for t in tables if t["from"] <= d and (t["to"] is None or d <= t["to"])]
    if not valid:
        raise MarketError("no SET tick table covers %s" % d.isoformat())
    table = valid[-1]
    if table["verified"]:
        return table, False
    older = [t for t in tables if t["verified"] and t["from"] < table["from"]]
    if not older:
        raise MarketError("the SET tick table for %s has no values" % d.isoformat())
    return older[-1], True


def tick_fallback(d):
    return _table_for(d)[1]


def tick_table_label(d):
    table, fell_back = _table_for(d)
    return table["name"] + (" (fallback)" if fell_back else "")


def tick(price, d):
    price = Decimal(price)
    size = None
    for start, t in _table_for(d)[0]["bands"]:
        if price >= start:
            size = t
    if size is None:
        raise MarketError("no tick size for price %s" % price)
    return size


def round_down_tick(price, d):
    price = Decimal(price)
    t = tick(price, d)
    return (price / t).to_integral_value(rounding=ROUND_FLOOR) * t


def round_up_tick(price, d):
    price = Decimal(price)
    t = tick(price, d)
    return (price / t).to_integral_value(rounding=ROUND_CEILING) * t


def round_tick(price, d):
    """Round half up to the nearest valid price."""
    price = Decimal(price)
    t = tick(price, d)
    return (price / t).to_integral_value(rounding=ROUND_HALF_UP) * t


def price_limits(prev_close, d):
    """(floor, ceiling) at -/+30% of the previous close, rounded inward to a valid tick."""
    prev = Decimal(prev_close)
    return round_up_tick(prev * (1 - LIMIT_PCT), d), round_down_tick(prev * (1 + LIMIT_PCT), d)


def board_lot(lot=None):
    """100 shares, or the user's --lot (50 for a stock at or above THB 500 for 6 months)."""
    if lot is None:
        return 100
    if lot not in BOARD_LOTS:
        raise MarketError("a SET board lot is 100 shares, or 50 for a stock priced at or above THB 500 "
                          "for 6 months; %s is not one" % lot)
    return lot
