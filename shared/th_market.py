"""Thai market rules for the th-trader skills: Bangkok time, the SET calendar and sessions,
tick sizes, price limits, board lot, and Yahoo .BK data.

Standard library only. Every datetime here is naive Bangkok time unless its name says utc.
Thailand has no daylight saving, so Bangkok is a fixed UTC+7 and no tz database is needed.
"""
import datetime
import json
import os
import time as time_module
import urllib.error
import urllib.parse
import urllib.request
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


# ---------------------------------------------------------------- Yahoo .BK data
# Ported from day-trader-skill's trader-scanner (MIT, Jake Schincariol): http_get, yahoo_get,
# series_from_result, parse_spark, parse_chart, fetch_spark. Symbols keep their dots and get
# .BK; times are Bangkok, not New York.

UA = "Mozilla/5.0 (compatible; thai-trader-skills/1.0)"
YAHOO_HOSTS = ("https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com")
SPARK_MAX = 20  # Yahoo's spark answers 400 above 20 symbols
PAUSE = 0.3  # seconds between requests: be polite to free endpoints
BACKOFF = 5.0  # seconds to wait before the one retry after a 429
SLEEP = time_module.sleep  # tests swap this out
DAY_DONE = datetime.time(16, 40)  # after the 16:30 close, pre-close and ATC


class FetchError(MarketError):
    """Yahoo gave nothing usable."""


def to_yahoo(symbol):
    s = symbol.strip().upper()
    return s if s.endswith(".BK") else s + ".BK"


def yahoo_url_symbol(symbol):
    return urllib.parse.quote(to_yahoo(symbol), safe=".-")


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def yahoo_get(path):
    """GET a Yahoo Finance path. query2 first, query1 on a 429 or 5xx, and one
    more round after BACKOFF seconds if a 429 was seen. 4xx other than 429 is final."""
    last = "no answer"
    for attempt in (0, 1):
        saw_429 = False
        for host in YAHOO_HOSTS:
            try:
                return http_get(host + path)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                try:
                    body = e.read().decode("utf-8", "replace") if 400 <= e.code < 500 and e.code != 429 else ""
                except Exception:
                    body = ""
                finally:
                    e.close()
                if e.code == 429:
                    saw_429 = True
                    continue
                if e.code >= 500:
                    continue
                if body.strip().startswith("{"):
                    return body  # Yahoo's JSON error ("No data found"), parsed by the caller
                raise FetchError("Yahoo answered %s for %s" % (last, path.split("?")[0]))
            except (urllib.error.URLError, OSError) as e:
                last = str(getattr(e, "reason", e))
                continue
        if attempt == 0 and saw_429:
            SLEEP(BACKOFF)
            continue
        break
    raise FetchError("Yahoo answered %s for %s" % (last, path.split("?")[0]))


def spark_path(symbols, rng, interval):
    return "/v7/finance/spark?symbols=%s&range=%s&interval=%s" % (
        ",".join(yahoo_url_symbol(s) for s in symbols), rng, interval)


def chart_path(symbol, rng, interval):
    return "/v8/finance/chart/%s?range=%s&interval=%s" % (yahoo_url_symbol(symbol), rng, interval)


def series_from_result(res):
    """A Yahoo chart result (v8 or one spark response) -> plain lists."""
    q = ((res.get("indicators") or {}).get("quote") or [{}])[0] or {}
    t = res.get("timestamp") or []
    n = len(t)

    def col(k):
        v = q.get(k)
        return list(v) + [None] * (n - len(v)) if isinstance(v, list) else None
    return {"t": list(t), "c": col("close") or [None] * n, "o": col("open"), "h": col("high"),
            "l": col("low"), "v": col("volume"), "meta": res.get("meta") or {}}


def parse_spark(text_or_doc, wanted=None):
    """Spark JSON -> {SYM: series}. Symbols Yahoo does not know are missing from the answer.
    `wanted` maps Yahoo symbols back to ours (PTT.BK -> PTT)."""
    doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
    sp = (doc or {}).get("spark") if isinstance(doc, dict) else None
    if not isinstance(sp, dict):
        raise FetchError("Yahoo's spark answer is not the shape this tool knows")
    err = sp.get("error")
    out = {}
    for item in sp.get("result") or []:
        resp = item.get("response") or []
        if not resp:
            continue
        ysym = (item.get("symbol") or (resp[0].get("meta") or {}).get("symbol") or "").upper()
        sym = (wanted or {}).get(ysym, ysym[:-3] if ysym.endswith(".BK") else ysym)
        out[sym] = series_from_result(resp[0])
    if err and not out:
        raise FetchError("Yahoo spark: %s" % (err.get("description") or err.get("code") or err))
    return out


def parse_chart(text_or_doc, symbol=""):
    """v8 chart JSON -> series. Raises FetchError for Yahoo's 'no data' answers."""
    try:
        doc = json.loads(text_or_doc) if isinstance(text_or_doc, str) else text_or_doc
        chart = doc["chart"]
    except (ValueError, KeyError, TypeError):
        raise FetchError("Yahoo returned something unexpected for %s" % symbol)
    res = chart.get("result")
    if not res:
        err = chart.get("error") or {}
        raise FetchError("no data for %s: %s" % (symbol, err.get("description", "unknown symbol")))
    return series_from_result(res[0])


def fetch_bars(symbols, rng, interval, pause=PAUSE):
    """Spark in batches of 20 -> ({SYM: series}, {SYM: why it failed})."""
    data, failed = {}, {}
    batches = [symbols[i:i + SPARK_MAX] for i in range(0, len(symbols), SPARK_MAX)]
    for k, batch in enumerate(batches):
        if k and pause:
            SLEEP(pause)
        wanted = {to_yahoo(s): s for s in batch}
        try:
            got = parse_spark(yahoo_get(spark_path(batch, rng, interval)), wanted)
        except (FetchError, ValueError) as e:
            for s in batch:
                failed[s] = str(e)
            continue
        for s in batch:
            if s in got:
                data[s] = got[s]
            else:
                failed[s] = "no data from Yahoo"
    return data, failed


def fetch_chart(symbol, rng, interval):
    return parse_chart(yahoo_get(chart_path(symbol, rng, interval)), symbol)


def require_fetched(n_fetched, n_requested, failed):
    """Fetching nothing is a failed run, never an empty result."""
    if n_requested and not n_fetched:
        why = next(iter(failed.values()), "no answer") if failed else "no answer"
        raise FetchError("Fetched 0 of %d symbols from Yahoo: %s" % (n_requested, why))


def _num(col, i):
    if not col or col[i] is None:
        return None
    return float(col[i])


def daily_bars(series, now):
    """Complete daily bars, oldest first, dated by their Bangkok date. Today's bar counts
    only after 16:40; null closes and bars dated after the clock are dropped."""
    today = now.date()
    today_done = now.time() >= DAY_DONE or not is_trading_day(today)
    out = {}
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        d = ts_to_bkk(ts).date()
        if d > today or (d == today and not today_done):
            continue
        out[d] = {"date": d, "o": _num(series.get("o"), i), "h": _num(series.get("h"), i),
                  "l": _num(series.get("l"), i), "c": float(c),
                  "v": _num(series.get("v"), i)}  # one bar a day: a late duplicate (the live tick) wins
    return [out[d] for d in sorted(out)]


def intraday_bars(series, day, cutoff, interval=5):
    """Bars of `day` on the session grid that are complete at `cutoff`, one per slot.
    Lunch-break nulls and off-grid live ticks are dropped."""
    grid = set(slots(day, interval))
    step = datetime.timedelta(minutes=interval)
    out = {}
    for i, ts in enumerate(series["t"]):
        c = series["c"][i]
        if c is None:
            continue
        start = ts_to_bkk(ts)
        if start not in grid or start + step > cutoff:
            continue
        out[start] = {"start": start, "o": _num(series.get("o"), i), "h": _num(series.get("h"), i),
                      "l": _num(series.get("l"), i), "c": float(c), "v": _num(series.get("v"), i) or 0}
    return [out[k] for k in sorted(out)]
