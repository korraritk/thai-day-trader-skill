"""Save raw Yahoo JSON for the tests. Run once by hand; the tests never touch the network."""
import os
import urllib.request

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "yahoo")
UA = "Mozilla/5.0"
BASE = "https://query1.finance.yahoo.com"
FILES = {
    "chart_PTT_5m_5d.json": "/v8/finance/chart/PTT.BK?range=5d&interval=5m",
    "spark_PTT_CPALL_1d_3mo.json": "/v7/finance/spark?symbols=PTT.BK,CPALL.BK&range=3mo&interval=1d",
    "spark_PTT_CPALL_5m_1d.json": "/v7/finance/spark?symbols=PTT.BK,CPALL.BK&range=1d&interval=5m",
    "chart_PTT_5m_60d.json": "/v8/finance/chart/PTT.BK?range=60d&interval=5m",
    "chart_SINGER_5m_60d.json": "/v8/finance/chart/SINGER.BK?range=60d&interval=5m",
    "chart_PTT_1d_5y.json": "/v8/finance/chart/PTT.BK?range=5y&interval=1d",
}

RSS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rss")
RSS = {  # Thai stock news feeds, as CrabTrade777 reads them
    "kaohoon_p1.xml": "https://www.kaohoon.com/feed",
    "kaohoon_p2.xml": "https://www.kaohoon.com/feed?paged=2",
    "infoquest_p1.xml": "https://www.infoquest.co.th/feed",
}

import sys
import time
wanted = sys.argv[1:] or list(FILES) + list(RSS)
for name, url in RSS.items():
    if name not in wanted:
        continue
    os.makedirs(RSS_DIR, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, */*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    with open(os.path.join(RSS_DIR, name), "wb") as fh:
        fh.write(body)
    print(name, len(body))
    time.sleep(0.5)
for name, path in FILES.items():
    if name not in wanted:
        continue
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read()
    with open(os.path.join(HERE, name), "wb") as fh:
        fh.write(body)
    print(name, len(body))
