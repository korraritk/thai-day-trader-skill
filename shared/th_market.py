"""Thai market rules for the th-trader skills: Bangkok time, the SET calendar and sessions.

Standard library only. Every datetime here is naive Bangkok time unless its name says utc.
Thailand has no daylight saving, so Bangkok is a fixed UTC+7 and no tz database is needed.
"""
import datetime
import json
import os

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
