"""Chart Scanner for th-trader-scanner: daily setups and opening-range breakouts on SET/mai stocks.

Ported from day-trader-skill's trader-scanner (MIT, Jake Schincariol). Every Thai market rule
comes from th_market.py. Standard library only.

    python scan.py universe --file listedCompanies_th_TH.xls   # -> th-trader/universe.txt
    python scan.py daily                                       # after the close: daily setups -> th-trader/watchlist.json
    python scan.py daily --symbols PTT,CPALL,ADVANC
    python scan.py orb                                         # from about 10:20: opening-range breakouts so far
    python scan.py orb --offline day.json --now "2026-10-30 11:00"

universe  Reads the SET listed-companies file the user downloaded from set.or.th in a browser
          (HTML saved as .xls, .xlsx or .csv) and keeps SET and mai common shares.
daily     Yahoo daily closes (spark), the original five setups, plus at_ceiling. Stage 2
          fetches the daily chart of each match for its 20-day average traded value
          (close x volume), filtered by --min-value (THB 50 million).
orb       Opening-range breakouts on 5-minute bars of the morning session (10:00 to 10:15 by
          default). The lunch break is not a gap.

--offline PATH reads files instead of the network: a folder of <SYMBOL>.json v8 charts or
spark answers, or one file that is a spark answer, a v8 chart, or a bundle {"PTT": <v8 json>}.

A scan is a list of charts that match a rule, not a list of stocks to buy.
Exit code 0 when the scan found something, 1 when it did not or could not run.
"""

import argparse
import datetime
import gzip
import json
import os
import re
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
from setups import find_setups, rsi  # noqa: F401  (rsi is re-exported for callers of scan)
from universe_file import UniverseError, read_listed_companies

NOTE = "A scan is a list of charts that match a rule, not a list of stocks to buy."
DEFAULT_UNIVERSE = "th-trader/universe.txt"
DEFAULT_WATCHLIST = "th-trader/watchlist.json"
MIN_DAILY_BARS = 30
VALUE_DAYS = 20
SETUPS = [
    ("breakout_20d", "20-day closing-high breakout"),
    ("bull_flag", "bull flag"),
    ("reclaim_ma20", "reclaimed the 20-day average"),
    ("oversold", "oversold (RSI under 30)"),
    ("oversold_bounce", "oversold bounce"),
    ("at_ceiling", "closed at the +30% ceiling"),
]
LABEL = dict(SETUPS)


class ScanError(Exception):
    pass


# ---------------------------------------------------------------- offline files

def _read_maybe_gz(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return fh.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _stem(name):
    for ext in (".json.gz", ".json"):
        if name.endswith(ext):
            return name[:-len(ext)]
    return name


def _absorb(doc, name_hint, data, errors):
    """One parsed offline JSON doc -> data / errors."""
    if isinstance(doc, dict) and "spark" in doc:
        data.update(tm.parse_spark(doc))
    elif isinstance(doc, dict) and "chart" in doc:
        sym = name_hint
        if not sym:
            try:
                sym = (doc["chart"].get("result") or [{}])[0]["meta"]["symbol"]
            except (AttributeError, KeyError, TypeError, IndexError):
                raise ScanError("an offline v8 chart without a symbol: name the file <SYMBOL>.json")
        sym = sym.upper()
        sym = sym[:-3] if sym.endswith(".BK") else sym
        try:
            data[sym] = tm.parse_chart(doc, sym)
        except tm.FetchError as e:
            errors[sym] = str(e)
    elif isinstance(doc, dict):
        for k, v in doc.items():
            _absorb(v, k, data, errors)
    else:
        raise ScanError("offline file %s is not a spark answer, a v8 chart or a bundle" % name_hint)


def load_offline(path):
    """A folder of <SYMBOL>.json(.gz) files or one file -> ({SYM: series}, {SYM: error})."""
    data, errors = {}, {}
    if os.path.isdir(path):
        names = sorted(f for f in os.listdir(path) if f.endswith((".json", ".json.gz")))
        if not names:
            raise ScanError("no .json files in %s" % path)
        for f in names:
            try:
                doc = json.loads(_read_maybe_gz(os.path.join(path, f)))
            except ValueError as e:
                raise ScanError("%s is not valid JSON: %s" % (f, e))
            _absorb(doc, "" if "spark" in doc else _stem(f), data, errors)
    elif os.path.isfile(path):
        try:
            doc = json.loads(_read_maybe_gz(path))
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        _absorb(doc, "", data, errors)
    else:
        raise ScanError("no offline file or folder at %s" % path)
    return data, errors


# ---------------------------------------------------------------- daily setups

def at_ceiling(prev_close, close, day):
    _, ceiling = tm.price_limits(Decimal(str(round(prev_close, 2))), day)
    return Decimal(str(round(close, 2))) >= ceiling


def value20(bars):
    """Average close x volume over the last 20 bars that have a volume, or None."""
    vals = [b["c"] * b["v"] for b in bars[-VALUE_DAYS:] if b["v"] is not None]
    return sum(vals) / len(vals) if vals else None


def scan_daily(data, symbols, now, min_price, min_value, min_volume, confirm=None):
    """data = {SYM: series} (spark closes, or v8 charts offline). confirm(sym) -> a series
    with volumes (the v8 daily chart), used for each match whose own series has none.
    Returns (hits, counts, last_date)."""
    hits, dates = [], {}
    counts = {"requested": len(symbols), "fetched": 0, "illiquid": 0, "short": 0, "scanned": 0,
              "matched": 0, "unconfirmed": 0}
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = tm.daily_bars(s, now)
        if not bars or bars[-1]["c"] < min_price:
            counts["illiquid"] += 1
            continue
        if len(bars) < MIN_DAILY_BARS:
            counts["short"] += 1
            continue
        counts["scanned"] += 1
        d_last = bars[-1]["date"]
        dates[d_last] = dates.get(d_last, 0) + 1
        closes = [b["c"] for b in bars]
        tags, nums = find_setups(closes)
        if at_ceiling(closes[-2], closes[-1], d_last):
            tags.append("at_ceiling")
        if not tags:
            continue
        counts["matched"] += 1
        vbars = bars
        if all(b["v"] is None for b in bars[-VALUE_DAYS:]) and confirm is not None:
            try:
                vbars = tm.daily_bars(confirm(sym), now)
            except tm.FetchError:
                counts["unconfirmed"] += 1
                continue
        val = value20(vbars)
        vol = vbars[-1]["v"] if vbars else None
        if (min_value > 0 and (val is None or val < min_value)) or \
                (min_volume > 0 and (vol is None or vol < min_volume)):
            counts["illiquid"] += 1
            continue
        hits.append({"symbol": sym, "setups": tags, "price": round(closes[-1], 2),
                     "close_date": d_last.isoformat(), "rsi": round(nums["rsi"], 1) if nums["rsi"] is not None else None,
                     "volume": int(vol) if vol is not None else None,
                     "value20": round(val) if val is not None else None})
    hits.sort(key=lambda h: (-(h["value20"] or 0), h["symbol"]))  # most baht traded first
    last_date = max(dates, key=lambda d: (dates[d], d)) if dates else None
    counts["hits"] = len(hits)
    return hits, counts, last_date


# ---------------------------------------------------------------- opening-range breakout

BAR_MIN = 5
MAX_CONFIRM = 200  # stage-2 v8 charts per orb scan, most traded first
STALE_AFTER = 30  # minutes behind the clock before a warning


def clock_label(t):
    return t.strftime("%H:%M")


def day_label(d):
    return "%s %s" % (d.strftime("%a"), d.isoformat())


def usable(bars, day, cutoff, n_range):
    """Every session slot that is complete at the cutoff is there, and at least the range.
    The lunch break has no slots, so it is never a gap."""
    step = datetime.timedelta(minutes=BAR_MIN)
    want = [s for s in tm.slots(day, BAR_MIN) if s + step <= cutoff]
    if len(want) < n_range or len(bars) != len(want):
        return False
    return all(b["start"] == s for b, s in zip(bars, want))


def filled_closes(bars, day, cutoff):
    """Stage-1 bars on every slot to the cutoff, a missing slot carrying the close before it.
    Yahoo spark leaves out 5-minute bars whose close did not change. None when the first
    slot of the day is missing (nothing to carry)."""
    step = datetime.timedelta(minutes=BAR_MIN)
    want = [s for s in tm.slots(day, BAR_MIN) if s + step <= cutoff]
    have = {b["start"]: b for b in bars}
    if not want or want[0] not in have:
        return None
    out, last = [], None
    for s in want:
        b = have.get(s)
        last = b["c"] if b else last
        out.append({"start": s, "c": last, "h": None, "l": None, "v": b["v"] if b else 0})
    return out


def orb_candidate(bars, n_range):
    """Stage 1, closes only: some completed bar after the range closed at or above
    the highest close of the range. Necessary for a breakout (range high >= any range close)."""
    top = max(b["c"] for b in bars[:n_range])
    return any(b["c"] >= top for b in bars[n_range:])


def orb_hit(bars, n_range):
    """Stage 2, real highs and lows: the first completed bar to CLOSE above the range high."""
    rng = bars[:n_range]
    if any(b["h"] is None or b["l"] is None for b in rng):
        return None
    oh = max(b["h"] for b in rng)
    ol = min(b["l"] for b in rng)
    for k in range(n_range, len(bars)):
        b = bars[k]
        if b["c"] > oh:
            closes_at = b["start"] + datetime.timedelta(minutes=BAR_MIN)
            return {"price": round(b["c"], 2), "at": clock_label(closes_at),
                    "bar_start": b["start"].strftime("%H:%M"), "bar_index": k,
                    "or_high": round(oh, 2), "or_low": round(ol, 2),
                    "pct_over": round((b["c"] / oh - 1) * 100, 2), "_t": closes_at}
    return None


def orb_window(now, at, or_minutes, until, explicit_day=None):
    """-> (day, cutoff), both Bangkok time, or raise ScanError with the plain message.
    A cutoff in the lunch break becomes 12:30; one after the close becomes 16:30."""
    day = explicit_day or now.date()
    if or_minutes < BAR_MIN or or_minutes % BAR_MIN or or_minutes > 120:
        raise ScanError("--or-minutes wants a multiple of 5 from 5 to 120 (15 is the default)")
    sess = tm.sessions(day)
    if not sess:
        why = tm.holiday_name(day) or day.strftime("%A")
        raise ScanError("No session on %s (%s): the market is closed. Run orb on a trading day, "
                        "or replay a past day with --offline." % (day_label(day), why))
    (m_open, m_close), (a_open, a_close) = sess
    range_end = datetime.datetime.combine(day, m_open) + datetime.timedelta(minutes=or_minutes)
    first_close = range_end + datetime.timedelta(minutes=BAR_MIN)
    if at is not None:
        cutoff = datetime.datetime.combine(day, at)
        if explicit_day is None and cutoff > now + datetime.timedelta(seconds=59):
            raise ScanError("--at %s is later than now (%s Bangkok)." % (at.strftime("%H:%M"), clock_label(now)))
    else:
        cutoff = now.replace(second=0, microsecond=0)
    lunch_start = datetime.datetime.combine(day, m_close)
    lunch_end = datetime.datetime.combine(day, a_open)
    close_dt = datetime.datetime.combine(day, a_close)
    if lunch_start < cutoff < lunch_end:
        cutoff = lunch_start
    if cutoff > close_dt:
        cutoff = close_dt
    if cutoff < first_close:
        raise ScanError("It is %s Bangkok: the %d-minute opening range is not complete until %s, and the first "
                        "bar after it closes at %s. Run orb again after %s."
                        % (clock_label(cutoff), or_minutes, clock_label(range_end), clock_label(first_close),
                           clock_label(first_close)))
    if until is not None and cutoff.time() > until:
        raise ScanError("It is %s Bangkok, after %s, your last time for new entries (no_entries_after): an "
                        "opening-range breakout found now is expired. Run orb tomorrow after %s, or replay "
                        "today with --at %s." % (clock_label(cutoff), until.strftime("%H:%M"),
                                                 clock_label(first_close), clock_label(first_close)))
    return day, cutoff


def latest_bar_end(data, symbols, day, cutoff):
    """The end of the latest complete bar any symbol has at the cutoff, or None. Spark leaves
    out bars whose close did not change, so the last trade time in meta counts too: the bar
    it falls in is still forming, so the latest complete one ends at its 5-minute floor."""
    step = datetime.timedelta(minutes=BAR_MIN)
    grid_ends = {s + step for s in tm.slots(day, BAR_MIN)}
    ends = []
    for sym in symbols:
        s = data.get(sym)
        if not s:
            continue
        bars = tm.intraday_bars(s, day, cutoff, BAR_MIN)
        if bars:
            ends.append(bars[-1]["start"] + step)
        rmt = (s.get("meta") or {}).get("regularMarketTime")
        if rmt:
            t = tm.ts_to_bkk(rmt)
            floor = t.replace(minute=t.minute - t.minute % BAR_MIN, second=0, microsecond=0)
            if t.date() == day and floor <= cutoff and floor in grid_ends:
                ends.append(floor)
    return max(ends) if ends else None


def stale_minutes(latest_end, now):
    if latest_end is None:
        return None
    return int((now - latest_end).total_seconds() // 60)


def in_session(now):
    return any(datetime.datetime.combine(now.date(), o) <= now < datetime.datetime.combine(now.date(), c)
               for o, c in tm.sessions(now.date()))


def scan_orb(stage1, symbols, day, cutoff, or_minutes, min_price=0.0, min_volume=0, confirm=None,
             max_confirm=None):
    """stage1 = {SYM: series} with closes (spark or v8). confirm(sym) -> series with highs/lows
    (None = use the stage-1 series). With confirm and max_confirm, only the max_confirm most
    traded candidates (price x volume so far) are confirmed; the rest are counted as unconfirmed.
    Returns (hits, counts, failed {sym: why})."""
    n_range = or_minutes // BAR_MIN
    counts = {"requested": len(symbols), "fetched": 0, "unusable": 0, "illiquid": 0, "scanned": 0,
              "candidates": 0, "confirmed": 0, "unconfirmed": 0}
    hits, failed, cands = [], {}, []
    for sym in symbols:
        s = stage1.get(sym)
        if not s:
            continue
        counts["fetched"] += 1
        bars = tm.intraday_bars(s, day, cutoff, BAR_MIN)
        if confirm is not None:
            bars = filled_closes(bars, day, cutoff)  # spark gaps; stage 2 checks the real bars
            if bars is None or len(bars) < n_range:
                counts["unusable"] += 1
                continue
        elif not usable(bars, day, cutoff, n_range):
            counts["unusable"] += 1
            continue
        vol = sum(b["v"] for b in bars) if s.get("v") else (s.get("meta") or {}).get("regularMarketVolume") or 0
        if bars[-1]["c"] < min_price or vol < min_volume:
            counts["illiquid"] += 1
            continue
        counts["scanned"] += 1
        if orb_candidate(bars, n_range):
            cands.append((sym, bars, bars[-1]["c"] * vol))
    counts["candidates"] = len(cands)
    if confirm is not None and max_confirm and len(cands) > max_confirm:
        cands.sort(key=lambda x: -x[2])
        counts["unconfirmed"] = len(cands) - max_confirm
        cands = cands[:max_confirm]
    for sym, bars, _ in cands:
        full = bars
        if confirm is not None:
            try:
                s2 = confirm(sym)
            except tm.FetchError as e:
                failed[sym] = str(e)
                continue
            full = tm.intraday_bars(s2, day, cutoff, BAR_MIN)
            if not usable(full, day, cutoff, n_range):
                failed[sym] = "the v8 chart has a missing bar"
                continue
        counts["confirmed"] += 1
        h = orb_hit(full, n_range)
        if h:
            hits.append(dict({"symbol": sym, "setups": ["orb"]}, **h))
    hits.sort(key=lambda x: (x["_t"], x["symbol"]))
    for h in hits:
        del h["_t"]
    counts["hits"] = len(hits)
    return hits, counts, failed


# ---------------------------------------------------------------- symbols, files

def symbols_from_file(path):
    if not os.path.exists(path):
        raise ScanError("no file at %s" % path)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if text.lstrip().startswith(("{", "[")):
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScanError("%s is not valid JSON: %s" % (path, e))
        items = doc.get("hits", []) if isinstance(doc, dict) else doc
        return [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in items if x]
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln.split("|")[0].strip().upper())
    return out


def read_symbols(a, offline_syms=None):
    if a.symbols:
        syms = [s for s in re.split(r"[,\s]+", a.symbols.upper()) if s]
    elif a.watchlist:
        syms = symbols_from_file(a.watchlist)
    elif offline_syms is not None and not a.universe:
        syms = sorted(offline_syms)  # offline: every chart in the file, unless a list was given
    else:
        path = a.universe or DEFAULT_UNIVERSE
        if not os.path.exists(path):
            raise ScanError("no %s yet: run `scan.py universe --file <listed companies file>` first, "
                            "or pass --symbols" % path)
        syms = symbols_from_file(path)
    out = []
    for s in syms:
        s = s[:-3] if s.endswith(".BK") else s
        if s not in out:
            out.append(s)
    if a.limit:
        out = out[:a.limit]
    if not out:
        raise ScanError("no symbols to scan")
    return out


def read_config(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except ValueError as e:
        raise ScanError("%s is not valid JSON: %s" % (path, e))
    return cfg if isinstance(cfg, dict) else {}


def write_json(path, doc):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)


def progress(a, msg):
    if not getattr(a, "json", False) and not getattr(a, "quiet", False):
        print(msg, file=sys.stderr)


def money(x):
    return "{:,.2f}".format(x)


def _n(x):
    x = float(x)
    return str(int(x)) if x == int(x) else "%.2f" % x


def _c(n):
    return "{:,}".format(n)


# ---------------------------------------------------------------- commands

def cmd_universe(a):
    if not a.file:
        raise ScanError("give --file: the listed-companies file downloaded from set.or.th "
                        "(Listed Companies > download), saved as .xls, .xlsx or .csv")
    try:
        companies = read_listed_companies(a.file)
    except UniverseError as e:
        raise ScanError(str(e))
    if not companies:
        raise ScanError("no common shares found in %s" % a.file)
    lines = ["# SET/mai common shares from %s, %s" % (os.path.basename(a.file), tm.now_bkk().strftime("%Y-%m-%d"))]
    lines += ["%s|%s" % (sym, market) for sym, market in companies]
    n_set = sum(1 for _, m in companies if m == "SET")
    if not a.no_write:
        d = os.path.dirname(a.out)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines) + "\n")
    if a.json:
        print(json.dumps({"symbols": len(companies), "set": n_set, "mai": len(companies) - n_set,
                          "out": None if a.no_write else a.out}, indent=2))
        return 0
    print("%s common shares: %s SET, %s mai." % (_c(len(companies)), _c(n_set), _c(len(companies) - n_set)))
    if not a.no_write:
        print("Wrote %s." % a.out)
    return 0


def cmd_daily(a):
    t0 = time.time()
    now = tm.parse_now(a.now)
    offline_syms = None
    confirm = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
    symbols = read_symbols(a, offline_syms)
    if not a.offline:
        progress(a, "Fetching daily closes for %s symbols (%d spark requests)..."
                 % (_c(len(symbols)), (len(symbols) + tm.SPARK_MAX - 1) // tm.SPARK_MAX))
        data, failed = tm.fetch_bars(symbols, "3mo", "1d", a.pause)

        def confirm(sym):
            if a.pause:
                tm.SLEEP(a.pause)
            return tm.fetch_chart(sym, "3mo", "1d")
    fetched = sum(1 for s in symbols if s in data)
    tm.require_fetched(fetched, len(symbols), failed)
    hits, counts, last_date = scan_daily(data, symbols, now, a.min_price, a.min_value, a.min_volume, confirm)
    nfailed = len(symbols) - counts["fetched"]
    as_of = ("%s close" % last_date.isoformat()) if last_date else None
    fallback = bool(last_date) and tm.tick_fallback(last_date)
    doc = {"created_by": "th-trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%S+07:00"),
           "mode": "daily", "as_of": as_of, "currency": "THB", "market": "SET",
           "universe": len(symbols), "scanned": counts["scanned"], "failed": nfailed, "source": "yahoo",
           "tick_table": tm.tick_table_label(last_date) if last_date else None,
           "filters": {"min_price": a.min_price, "min_value": a.min_value, "min_volume": a.min_volume},
           "note": NOTE, "hits": hits}
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/\\"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    print("Daily scan, %s Bangkok, closes through %s (Yahoo, daily bars)." % (
        now.strftime("%a %Y-%m-%d %H:%M"), last_date.isoformat() if last_date else "nothing"))
    print("Fetched %s of %s charts%s; %s scanned (price THB %s+%s); %s matched a setup, %s passed the "
          "liquidity filter (20-day average value THB %s+%s)." % (
              _c(counts["fetched"]), _c(len(symbols)),
              (" (%s failed or had no data)" % _c(nfailed)) if nfailed else "",
              _c(counts["scanned"]), _n(a.min_price),
              (", %d had under %d days" % (counts["short"], MIN_DAILY_BARS)) if counts["short"] else "",
              _c(counts["matched"]), _c(len(hits)), _c(int(a.min_value)),
              (", volume %s+" % _c(a.min_volume)) if a.min_volume else ""))
    if counts["unconfirmed"]:
        print("PARTIAL: %d matches could not be checked for traded value (Yahoo gave no daily chart)."
              % counts["unconfirmed"])
    if fallback:
        print(tm.TICK_FALLBACK_NOTE)
    if hits:
        per = [(k, sum(1 for h in hits if k in h["setups"])) for k, _ in SETUPS]
        print("%d chart%s: %s." % (len(hits), "" if len(hits) == 1 else "s",
                                   ", ".join("%d %s" % (n, LABEL[k]) for k, n in per if n)))
        print("Most traded first (20-day average value):")
    else:
        print("No setups.")
    for h in hits[:a.show]:
        print("  %-8s %10s  RSI %5s  value %7s M  %s" % (
            h["symbol"], money(h["price"]), "%.1f" % h["rsi"] if h["rsi"] is not None else "-",
            "%.1f" % (h["value20"] / 1e6) if h["value20"] is not None else "-",
            ", ".join(LABEL[t] for t in h["setups"])))
    if len(hits) > a.show:
        print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    if not a.no_write:
        print("Wrote %s (mode daily, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE)
    return 0 if hits else 1


def parse_hhmm(text, flag="--at"):
    try:
        return datetime.datetime.strptime(text.strip(), "%H:%M").time()
    except ValueError:
        raise ScanError("%s wants HH:MM (24-hour Bangkok time), not %r" % (flag, text))


def cmd_orb(a):
    t0 = time.time()
    now = tm.parse_now(a.now)
    at = parse_hhmm(a.at) if a.at else None
    cfg = read_config(a.config)
    until = parse_hhmm(a.until or cfg.get("no_entries_after") or "15:30", "--until")
    explicit_day = None
    offline_syms = None
    data = failed = None
    if a.offline:
        data, failed = load_offline(a.offline)
        offline_syms = sorted(set(data) | set(failed))
        if not a.now:
            days = [tm.ts_to_bkk(s["t"][-1]).date() for s in data.values() if s["t"]]
            if days:
                explicit_day = max(days)
                if at is None:
                    raise ScanError('offline replay: give --at HH:MM or --now "YYYY-MM-DD HH:MM"')
    day, cutoff = orb_window(now, at, a.or_minutes, until, explicit_day)
    symbols = read_symbols(a, offline_syms)
    confirm = None
    if not a.offline:
        today = tm.now_bkk().date()
        if (today - day).days > 6:
            raise ScanError("Yahoo's free 5-minute history used here is short: replay %s with --offline"
                            % day.isoformat())
        rng = "1d" if day == today else "5d"
        progress(a, "Stage 1: 5-minute closes for %s symbols (%d spark requests)..."
                 % (_c(len(symbols)), (len(symbols) + tm.SPARK_MAX - 1) // tm.SPARK_MAX))
        data, failed = tm.fetch_bars(symbols, rng, "%dm" % BAR_MIN, a.pause)

        def confirm(sym):
            if a.pause:
                tm.SLEEP(a.pause)
            return tm.fetch_chart(sym, rng, "%dm" % BAR_MIN)
    tm.require_fetched(sum(1 for s in symbols if s in data), len(symbols), failed or {})
    # Yahoo's .BK bars run 15-30 minutes behind: cut at the latest bar it actually has, so a
    # delayed feed is not read as every chart missing its last bars.
    latest = latest_bar_end(data, symbols, day, cutoff)
    if latest is not None and latest < cutoff:
        cutoff = latest
    stale = stale_minutes(latest, now) if day == now.date() else None
    hits, counts, failed2 = scan_orb(data, symbols, day, cutoff, a.or_minutes, a.min_price, a.min_volume,
                                     confirm, a.max_confirm)
    failed = dict(failed or {})
    failed.update(failed2)
    nfailed = len([s for s in symbols if s in failed])
    as_of = "%s %s Bangkok" % (day.isoformat(), clock_label(cutoff))
    range_end = datetime.datetime.combine(day, tm.SESSIONS[0][0]) + datetime.timedelta(minutes=a.or_minutes)
    doc = {"created_by": "th-trader-scanner", "created_at": now.strftime("%Y-%m-%dT%H:%M:%S+07:00"), "mode": "orb",
           "as_of": as_of, "currency": "THB", "market": "SET", "universe": len(symbols),
           "scanned": counts["scanned"], "failed": nfailed, "unconfirmed": counts["unconfirmed"], "source": "yahoo",
           "or_minutes": a.or_minutes, "interval": "%dm" % BAR_MIN, "tick_table": tm.tick_table_label(day),
           "filters": {"min_price": a.min_price, "min_volume": a.min_volume}, "note": NOTE, "hits": hits}
    warn = stale is not None and stale > STALE_AFTER and in_session(now)
    if warn:
        doc["stale_minutes"] = stale
    if a.offline:
        doc["offline"] = os.path.basename(a.offline.rstrip("/\\"))
    if not a.no_write:
        write_json(a.out, doc)
    if a.json:
        print(json.dumps(dict(doc, counts=counts, seconds=round(time.time() - t0, 1)), indent=2, ensure_ascii=False))
        return 0 if hits else 1
    print("Opening-range breakout scan, %s, data to %s Bangkok: %d-minute range (10:00 to %s), 5-minute bars, "
          "Yahoo." % (day_label(day), clock_label(cutoff), a.or_minutes, clock_label(range_end)))
    if warn:
        print("Warning: Yahoo's latest bar ended %s, %d minutes ago; a breakout may already be stale."
              % (clock_label(latest), stale))
    print("Fetched %s of %s charts%s; %s usable%s; %s candidates, %s confirmed with real highs and lows."
          % (_c(counts["fetched"]), _c(len(symbols)),
             (" (%s failed or had no data)" % _c(nfailed)) if nfailed else "",
             _c(counts["scanned"]),
             (" (%s under the price / volume floor)" % _c(counts["illiquid"])) if counts["illiquid"] else "",
             _c(counts["candidates"]), _c(counts["confirmed"])))
    if counts["unusable"]:
        print("%s charts had a missing 5-minute bar and were skipped." % _c(counts["unusable"]))
    if counts["unconfirmed"]:
        print("PARTIAL: %s more candidates were not checked (only the %s most traded are; --max-confirm 0 "
              "checks all)." % (_c(counts["unconfirmed"]), _c(a.max_confirm)))
    if hits:
        print("%d of %s charts closed above their opening-range high by %s:"
              % (len(hits), ("the %s checked" % _c(counts["confirmed"])) if counts["unconfirmed"]
                 else _c(counts["scanned"]), clock_label(cutoff)))
        for h in hits[:a.show]:
            print("  %6s  %-8s %10s  range %s to %s  %+.2f%% over" % (
                h["at"], h["symbol"], money(h["price"]), money(h["or_low"]), money(h["or_high"]), h["pct_over"]))
        if len(hits) > a.show:
            print("  ... and %d more in the watchlist (--show N for more)." % (len(hits) - a.show))
    else:
        print("No chart closed above its opening-range high by %s." % clock_label(cutoff))
    if not a.no_write:
        print("Wrote %s (mode orb, %d hit%s)." % (a.out, len(hits), "" if len(hits) == 1 else "s"))
    print(NOTE)
    return 0 if hits else 1


# ---------------------------------------------------------------- CLI

def build_parser():
    ap = argparse.ArgumentParser(description="Chart Scanner (th-trader-scanner): SET/mai daily setups "
                                             "and opening-range breakouts.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("universe", help="read the SET listed-companies file -> th-trader/universe.txt")
    p.add_argument("--file", help="the listed-companies file from set.or.th (.xls saved as HTML, .xlsx, .csv)")
    p.add_argument("--out", default=DEFAULT_UNIVERSE)
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--json", action="store_true")

    def common(p, min_price):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--symbols", help="comma-separated SET symbols instead of the universe")
        g.add_argument("--watchlist", metavar="FILE", help="a watchlist.json (hits) or a text file of symbols")
        p.add_argument("--universe", help="symbol list, one SYMBOL|market a line (default %s)" % DEFAULT_UNIVERSE)
        p.add_argument("--limit", type=int, help="scan only the first N symbols")
        p.add_argument("--min-price", type=float, default=min_price, help="THB (%s)" % _n(min_price))
        p.add_argument("--min-volume", type=int, default=0, help="shares on the last bar (0 = off)")
        p.add_argument("--offline", metavar="PATH", help="fixture folder or file instead of the network")
        p.add_argument("--now", help='pretend it is this time ("2026-10-30 10:20" Bangkok, or ...Z for UTC)')
        p.add_argument("--out", default=DEFAULT_WATCHLIST)
        p.add_argument("--no-write", action="store_true")
        p.add_argument("--pause", type=float, default=tm.PAUSE, help="seconds between requests (%.1f)" % tm.PAUSE)
        p.add_argument("--show", type=int, default=50, help="hits to print (all are written)")
        p.add_argument("--quiet", action="store_true")
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("daily", help="setups from daily closes, after the close or before the open")
    common(p, 1.0)
    p.add_argument("--min-value", type=float, default=50_000_000,
                   help="20-day average traded value, THB (50,000,000; 0 = off)")
    p = sub.add_parser("orb", help="opening-range breakouts of the morning session so far")
    common(p, 1.0)
    p.add_argument("--or-minutes", type=int, default=15)
    p.add_argument("--max-confirm", type=int, default=MAX_CONFIRM,
                   help="v8 charts to fetch in stage 2, most traded first (%d; 0 = all)" % MAX_CONFIRM)
    p.add_argument("--at", help="cut the clock at this Bangkok time, HH:MM")
    p.add_argument("--until", help="no new entries after HH:MM (default: config no_entries_after, 15:30)")
    p.add_argument("--config", default="th-trader/config.json")
    return ap, sub, common


COMMANDS = {"universe": cmd_universe, "daily": cmd_daily, "orb": cmd_orb}


def main(argv=None):
    ap, _, _ = build_parser()
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return COMMANDS[a.cmd](a)
    except (ScanError, tm.MarketError) as e:
        print("scan.py: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("scan.py: stopped", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
