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

def rsi(closes, n=14):
    """Wilder's RSI on closes. Unchanged from trader-scanner."""
    if len(closes) < n + 1:
        return None
    g = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    lo = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, len(closes))]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n
    for i in range(n, len(g)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
    return 100 - 100 / (1 + ag / al) if al else 100.0


def find_setups(closes):
    """closes (oldest first, >= 30) -> (setup keys, numbers). Unchanged from trader-scanner."""
    c = closes
    last = c[-1]
    hi20 = max(c[-21:-1])
    ma20 = sum(c[-20:]) / 20.0
    ma20_prev = sum(c[-21:-1]) / 20.0
    run = c[-6] / c[-16] - 1
    rng5 = (max(c[-5:]) - min(c[-5:])) / last
    r = rsi(c)
    r_prev = rsi(c[:-1])
    tags = []
    if last > hi20:
        tags.append("breakout_20d")
    if run > 0.10 and rng5 < 0.05:
        tags.append("bull_flag")
    if c[-2] < ma20_prev and last > ma20:
        tags.append("reclaim_ma20")
    if r is not None and r < 30:
        tags.append("oversold")
    if r_prev is not None and r_prev < 30 and last > c[-2]:
        tags.append("oversold_bounce")
    return tags, {"rsi": r, "rsi_prev": r_prev, "high20": hi20, "ma20": ma20,
                  "run_pct": run * 100, "range5_pct": rng5 * 100}


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
    return ap, sub, common


COMMANDS = {"universe": cmd_universe, "daily": cmd_daily}


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
