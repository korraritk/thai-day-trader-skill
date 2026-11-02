"""Thai stock news for th-trader-news: the ข่าวหุ้น (Kaohoon) and อินโฟเควสท์ (InfoQuest) RSS feeds,
matched to the user's SET symbols, tagged, newest first, with what was already shown remembered.
Standard library only.

Ported from day-trader-skill's trader-news (MIT, Jake Schincariol): the seen state, the window,
tags from keywords, the price line per symbol and the leads-not-facts rule. Yahoo headline RSS and
SEC EDGAR are replaced by the two Thai feeds CrabTrade777 reads, and the symbol matching is
CrabTrade777's (News/NewsStockMatcher.cs).

    python news.py                         # watchlist + plan + today's open positions, last 12 hours
    python news.py --symbols PTT CPALL --hours 48
    python news.py --market --hours 6      # every item, not only your symbols
    python news.py --all                   # items already shown too
    python news.py --telegram              # also send items for symbols you hold
    python news.py --offline DIR --now "2026-11-02 11:10"   # saved feeds: kaohoon_p1.xml, ...

Paging: each feed is read page by page (?paged=N, 0.5 s apart) until an item is older than
--hours, at most 10 pages. A feed that fails is one line; both failing is a failed run (exit 1).
"""
import argparse
import datetime
import email.utils
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import th_market as tm
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "..", "..", "shared"))
    import th_market as tm
import notify

FEEDS = [("Kaohoon", "https://www.kaohoon.com/feed"), ("InfoQuest", "https://www.infoquest.co.th/feed")]
OFFLINE_NAMES = {"kaohoon.com": "kaohoon", "infoquest.co.th": "infoquest"}
MAX_PAGES = 10
PAUSE = 0.5
KEEP_DAYS = 30
STATE = "th-trader/state/news.json"
UA = "Mozilla/5.0 (compatible; thai-trader-skills/1.0)"

# CrabTrade777's NewsStockMatcher: lookarounds, not \b, because Thai letters are word characters.
TITLE_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9&]{1,9}(?![A-Za-z0-9])")
STOP_LIST = {"SET", "MAI", "SET50", "SET100", "IPO", "CEO", "CFO", "GDP", "EPS", "ESG", "AI", "EV", "US", "USD",
             "THB", "BOT", "FED", "ECB", "IMF", "ETF", "REIT", "NAV", "PE", "PBV", "Q1", "Q2", "Q3", "Q4", "YOY",
             "QOQ"}


def _en(*words, ci=False):
    """English keywords as whole tokens (lookarounds again: a Thai letter next to them is fine)."""
    return re.compile(r"(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])" % "|".join(re.escape(w) for w in words),
                      re.I if ci else 0)


TAGS = [  # (tag, Thai substrings, English pattern)
    ("earnings", ("งบ", "กำไร", "ขาดทุน", "ไตรมาส"), _en("earnings", ci=True)),
    ("dividend", ("ปันผล",), _en("XD")),
    ("capital", ("เพิ่มทุน", "หุ้นกู้"), _en("RO", "PP", "Warrant", "warrant")),
    ("deal", ("ซื้อกิจการ", "ควบรวม", "เข้าซื้อ"), _en("M&A")),
    ("analyst", ("เป้าราคา", "แนะนำ", "บล."), _en("upgrade", "downgrade", ci=True)),
    ("regulator", ("ก.ล.ต.", "ตลท.", "ห้ามซื้อขาย"), _en("SET", "SP", "H", "Cash Balance")),
    ("macro", ("เศรษฐกิจ", "ดอกเบี้ย", "กนง.", "เงินเฟ้อ", "ค่าเงิน"), _en("Fed", "FED")),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- feeds

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_time(value):
    """RFC 822 pubDate -> naive Bangkok time, or None."""
    try:
        d = email.utils.parsedate_to_datetime((value or "").strip())
    except (TypeError, ValueError, IndexError):
        return None
    if d is None:
        return None
    return tm.to_bkk(d) if d.tzinfo else d


def parse_feed(xml_text, publisher):
    """RSS 2.0 -> [{title, link, published, categories, publisher}]. CDATA categories read as text."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise NewsError("%s's feed is not valid XML: %s" % (publisher, e))
    out = []
    for it in root.iter("item"):
        fields = {"category": []}
        for c in it:
            name = _local(c.tag)
            if name == "category":
                fields["category"].append((c.text or "").strip())
            elif name in ("title", "link", "pubDate") and name not in fields:
                fields[name] = (c.text or "").strip()
        if not fields.get("title") or not fields.get("link"):
            continue
        out.append({"title": fields["title"], "link": fields["link"], "published": parse_time(fields.get("pubDate")),
                    "categories": [c for c in fields["category"] if c], "publisher": publisher})
    return out


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, */*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read()


def page_url(url, n):
    return url if n == 1 else "%s?paged=%d" % (url, n)


def fetch_feed(url, publisher, since, max_pages=MAX_PAGES, get=http_get, pause=PAUSE):
    """-> (items no older than since, pages read, error or None). Pages until an item is older than
    since. A failure on page 1 is the feed's error; a later page that fails ends the paging."""
    items, pages = [], 0
    for n in range(1, max_pages + 1):
        if n > 1 and pause:
            tm.SLEEP(pause)
        try:
            page = parse_feed(get(page_url(url, n)), publisher)
        except (OSError, ValueError, NewsError) as e:
            if n == 1:
                return [], 0, str(getattr(e, "reason", None) or e)
            break
        pages = n
        items += [i for i in page if i["published"] is None or i["published"] >= since]
        if not page or any(i["published"] is not None and i["published"] < since for i in page):
            break
    return items, pages, None


def offline_get(folder):
    def get(url):
        host = next((v for k, v in OFFLINE_NAMES.items() if k in url), None)
        n = int(url.split("paged=")[1]) if "paged=" in url else 1
        path = os.path.join(folder, "%s_p%d.xml" % (host, n))
        if not host or not os.path.exists(path):
            raise OSError("no offline file %s" % path)
        with open(path, "rb") as fh:
            return fh.read()
    return get


# ---------------------------------------------------------------- matching and tags

def match_symbols(item, symbols):
    """A category equal to a symbol, or an uppercase Latin title token equal to one, minus the stop list
    (applied to categories too: a "SET" or "IPO" category is a topic, not a stock)."""
    found = set()
    for c in item.get("categories") or []:
        s = c.strip().upper()
        if s in symbols and s not in STOP_LIST:
            found.add(s)
    for m in TITLE_TOKEN.finditer(item.get("title") or ""):
        if m.group(0) in symbols and m.group(0) not in STOP_LIST:
            found.add(m.group(0))
    return found


def tags(item):
    title = item.get("title") or ""
    return [name for name, thai, en in TAGS if any(w in title for w in thai) or en.search(title)]


def select(items, symbols, seen, show_all, market):
    """Matching items (all with market), unseen unless show_all, one per link, newest first."""
    out, links = [], set()
    for it in items:
        if it["link"] in links:
            continue
        links.add(it["link"])
        syms = sorted(match_symbols(it, symbols))
        if not market and not syms:
            continue
        if it["link"] in seen and not show_all:
            continue
        out.append(dict(it, symbols=syms, tags=tags(it), new=it["link"] not in seen))
    out.sort(key=lambda i: i["published"] or datetime.datetime.min, reverse=True)
    return out


# ---------------------------------------------------------------- the user's symbols

def _read(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as e:
        raise NewsError("%s is not valid JSON: %s" % (path, e))


def user_symbols(now, watchlist="th-trader/watchlist.json", plan="th-trader/plan.json",
                 trades="th-trader/state/trades.json"):
    """-> (watched symbols, held symbols): watchlist hits, the plan's symbol, today's open positions."""
    watched, held = set(), set()
    wl = _read(watchlist) or {}
    for h in wl.get("hits") or []:
        if isinstance(h, dict) and h.get("symbol"):
            watched.add(str(h["symbol"]).upper())
    p = _read(plan) or {}
    if p.get("symbol"):
        watched.add(str(p["symbol"]).upper())
    qty = {}
    for t in (_read(trades) or {}).get(now.date().isoformat(), []):
        s = str(t.get("symbol", "")).upper()
        qty[s] = qty.get(s, 0) + (int(t.get("qty", 0)) if t.get("side") == "buy" else -int(t.get("qty", 0)))
    held = {s for s, q in qty.items() if q > 0}
    return watched | held, held


def universe(path="th-trader/universe.txt"):
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        return {line.split("|")[0].strip().upper() for line in fh if line.strip()}


# ---------------------------------------------------------------- seen state

def load_seen(path):
    doc = _read(path)
    return doc if isinstance(doc, dict) else {}


def save_seen(path, seen, shown, now):
    stamp = tm.iso_bkk(now)
    for it in shown:
        seen.setdefault(it["link"], stamp)
    cutoff = now - datetime.timedelta(days=KEEP_DAYS)
    keep = {}
    for link, when in seen.items():
        try:
            if tm.parse_iso_bkk(when) >= cutoff:
                keep[link] = when
        except (TypeError, ValueError):
            continue
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(keep, fh, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------- moves

def moves(symbols, offline=None):
    out, errors = [], []
    for i, sym in enumerate(sorted(symbols)):
        try:
            if offline:
                p = os.path.join(offline, sym + ".json")
                if not os.path.exists(p):
                    continue
                with open(p, encoding="utf-8") as fh:
                    series = tm.parse_chart(fh.read(), sym)
            else:
                if i:
                    tm.SLEEP(PAUSE)
                series = tm.fetch_chart(sym, "1d", "5m")
        except tm.MarketError as e:
            errors.append("price for %s: %s" % (sym, e))
            continue
        m = series.get("meta") or {}
        price, prev = m.get("regularMarketPrice"), m.get("previousClose") or m.get("chartPreviousClose")
        if not price or not prev:
            continue
        t = m.get("regularMarketTime")
        out.append({"symbol": sym, "price": float(price), "prev_close": float(prev),
                    "pct": (float(price) / float(prev) - 1) * 100, "time": tm.ts_to_bkk(t) if t else None})
    return out, errors


# ---------------------------------------------------------------- output

def item_lines(it):
    when = it["published"].strftime("%Y-%m-%d %H:%M") if it["published"] else "time unknown"
    who = ",".join(it["symbols"]) or "-"
    extra = ("[%s]  " % ", ".join(it["tags"])) if it["tags"] else ""
    return ["%s  %-9s %-14s %s" % (when, it["publisher"], who, it["title"]), "      %s%s" % (extra, it["link"])]


def telegram_text(items):
    lines = ["Thai stock news for what you hold:"]
    for it in items[:10]:
        lines.append("%s %s: %s %s" % (it["published"].strftime("%H:%M") if it["published"] else "",
                                       ",".join(it["symbols"]), it["title"], it["link"]))
    return "\n".join(lines)


def main(argv=None, get=None):
    ap = argparse.ArgumentParser(description="Thai stock news (th-trader-news) from Kaohoon and InfoQuest RSS.")
    ap.add_argument("--symbols", nargs="*", help="default: watchlist + plan + today's open positions")
    ap.add_argument("--hours", type=float, default=12)
    ap.add_argument("--all", action="store_true", help="show items already seen too")
    ap.add_argument("--market", action="store_true", help="every item, not only your symbols")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--telegram", action="store_true", help="send items for symbols you hold")
    ap.add_argument("--offline", metavar="DIR", help="saved feeds (kaohoon_p1.xml, infoquest_p1.xml, ...)")
    ap.add_argument("--now", help='pretend it is this Bangkok time, e.g. "2026-11-02 11:10"')
    ap.add_argument("--state", default=STATE)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    tm.utf8_stdout()
    try:
        now = tm.parse_now(a.now)
        watched, held = user_symbols(now)
        symbols = {s.upper() for s in a.symbols} if a.symbols else watched
        if a.market:
            symbols = symbols | universe()
        elif not symbols:
            raise NewsError("no symbols: give --symbols PTT CPALL, or --market, or run th-trader-scanner first")
        get = get or (offline_get(a.offline) if a.offline else http_get)
        since = now - datetime.timedelta(hours=a.hours)
        items, report, failed = [], [], 0
        for publisher, url in FEEDS:
            got, pages, err = fetch_feed(url, publisher, since, get=get, pause=0 if a.offline else PAUSE)
            if err:
                failed += 1
                report.append("%s: failed (%s)" % (publisher, err))
            else:
                report.append("%s: %d page%s" % (publisher, pages, "" if pages == 1 else "s"))
            items += [i for i in got if i["published"] is None or i["published"] <= now + datetime.timedelta(minutes=10)]
        if failed == len(FEEDS):
            raise NewsError("both feeds failed: %s" % "; ".join(report))
        seen = load_seen(a.state)
        shown = select(items, symbols, seen, a.all, a.market)
        mv, mv_errors = ([], []) if a.no_moves else moves({s for it in shown for s in it["symbols"]}, a.offline)
    except (NewsError, tm.MarketError) as e:
        print("news.py: %s" % e, file=sys.stderr)
        return 1
    n_new = sum(1 for i in shown if i["new"])
    if a.json:
        print(json.dumps({"checked": tm.iso_bkk(now), "feeds": report, "moves": [
            dict(m, time=tm.iso_bkk(m["time"]) if m["time"] else None) for m in mv],
            "items": [dict(i, published=tm.iso_bkk(i["published"]) if i["published"] else None) for i in shown]},
            indent=2, ensure_ascii=False))
    else:
        print("Thai stock news, %s Bangkok, last %g hours (%s); %s." % (
            now.strftime("%a %Y-%m-%d %H:%M"), a.hours, "; ".join(report),
            "every item" if a.market else "for " + ", ".join(sorted(symbols))))
        for m in mv:
            print("%-7s %.2f  %+.2f%% vs the previous close %.2f%s (Yahoo, delayed)" % (
                m["symbol"], m["price"], m["pct"], m["prev_close"],
                (" at %s" % m["time"].strftime("%H:%M")) if m["time"] else ""))
        if mv:
            print("")
        for it in shown:
            for line in item_lines(it):
                print(line)
        print("%d new." % n_new if n_new else "Nothing new%s." % (" (--all shows what was already shown)" if not a.all else ""))
        for e in mv_errors:
            print("note: " + e)
        print("Headlines are leads, not facts: open the story before acting. Not investment advice.")
    if a.telegram:
        mine = [i for i in shown if i["new"] and set(i["symbols"]) & held]
        if mine:
            sent, why = notify.send_telegram(telegram_text(mine))
            print(("Telegram: " + why) if sent else ("Telegram not sent: " + why))
    save_seen(a.state, seen, shown, now)
    return 0


if __name__ == "__main__":
    sys.exit(main())
