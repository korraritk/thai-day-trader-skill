import json
import os
import shutil
from datetime import datetime

import pytest

import news
from conftest import FIXTURES

RSS = os.path.join(FIXTURES, "rss")


def read(name):
    with open(os.path.join(RSS, name), "rb") as fh:
        return fh.read()


def item(title, cats=()):
    return {"title": title, "link": "https://x/" + title, "published": datetime(2026, 11, 2, 10, 0),
            "categories": list(cats), "publisher": "Kaohoon"}


@pytest.mark.parametrize("name,n,oldest,newest", [
    ("kaohoon_p1.xml", 10, datetime(2026, 11, 2, 10, 49, 3), datetime(2026, 11, 2, 11, 4, 17)),
    ("kaohoon_p2.xml", 10, datetime(2026, 11, 2, 9, 57, 25), datetime(2026, 11, 2, 10, 48, 36)),
    ("infoquest_p1.xml", 10, datetime(2026, 11, 2, 10, 14, 47), datetime(2026, 11, 2, 11, 2, 9)),
])
def test_parse_feed_counts_and_bangkok_times(name, n, oldest, newest):
    items = news.parse_feed(read(name), "X")
    assert len(items) == n
    assert min(i["published"] for i in items) == oldest and max(i["published"] for i in items) == newest
    assert all(i["link"].startswith("http") and i["title"] for i in items)


def test_cdata_categories_are_read():
    items = news.parse_feed(read("kaohoon_p1.xml"), "Kaohoon")
    aira = next(i for i in items if i["title"].startswith("AIRA"))
    assert "AIRA" in aira["categories"] and "ข่าวประชาสัมพันธ์" in aira["categories"]


def test_match_by_category():
    assert news.match_symbols(item("ข่าวบริษัท", ["PTT"]), {"PTT"}) == {"PTT"}
    assert news.match_symbols(item("ข่าวบริษัท", [" ptt "]), {"PTT"}) == {"PTT"}


def test_match_a_symbol_glued_to_thai():
    assert news.match_symbols(item("หุ้นPTTขึ้นแรง"), {"PTT"}) == {"PTT"}


def test_lowercase_is_not_a_symbol():
    assert news.match_symbols(item("ptt ขึ้นแรง"), {"PTT"}) == set()


def test_stop_list():
    assert news.match_symbols(item("SET ปิดบวก"), {"SET", "PTT"}) == set()
    assert news.match_symbols(item("CEO ลาออก"), {"CEO"}) == set()


def test_match_on_a_real_headline():
    items = news.parse_feed(read("kaohoon_p2.xml"), "Kaohoon")
    it = next(i for i in items if i["title"].startswith("“พบชัย”"))
    assert news.match_symbols(it, {"AAV", "THAI", "GPSC", "BGRIM", "SET", "PTT"}) == {"AAV", "THAI", "GPSC", "BGRIM"}


@pytest.mark.parametrize("title,tag", [("กำไรไตรมาส 3 เพิ่มขึ้น", "earnings"), ("ประกาศจ่ายปันผล", "dividend"),
                                       ("กนง. คงดอกเบี้ย", "macro"), ("บล.บัวหลวง ปรับเป้าราคา", "analyst"),
                                       ("ก.ล.ต. กล่าวโทษผู้บริหาร", "regulator"), ("ออกหุ้นกู้ชุดใหม่", "capital"),
                                       ("ซื้อกิจการโรงไฟฟ้า", "deal")])
def test_tags(title, tag):
    assert tag in news.tags(item(title))


def test_no_tag_for_plain_news():
    assert news.tags(item("ญี่ปุ่นจี้สหรัฐฯ เข้มงวดวินัยทหาร")) == []


class FakeGet:
    def __init__(self, pages, fail=False):
        self.pages, self.fail, self.urls = pages, fail, []

    def __call__(self, url):
        self.urls.append(url)
        if self.fail:
            raise OSError("connection refused")
        page = int(url.split("paged=")[1]) if "paged=" in url else 1
        if page not in self.pages:
            raise OSError("HTTP 404")
        return self.pages[page]


def test_fetch_feed_pages_until_older_than_since(monkeypatch):
    monkeypatch.setattr(news.tm, "SLEEP", lambda s: None)
    get = FakeGet({1: read("kaohoon_p1.xml"), 2: read("kaohoon_p2.xml")})
    items, pages, err = news.fetch_feed(news.FEEDS[0][1], "Kaohoon", datetime(2026, 11, 2, 10, 30), get=get)
    assert err is None and pages == 2
    assert get.urls == ["https://www.kaohoon.com/feed", "https://www.kaohoon.com/feed?paged=2"]
    assert all(i["published"] >= datetime(2026, 11, 2, 10, 30) for i in items)
    assert len(items) == 10 + sum(1 for i in news.parse_feed(read("kaohoon_p2.xml"), "K")
                                  if i["published"] >= datetime(2026, 11, 2, 10, 30))


def test_fetch_feed_stops_on_page_one(monkeypatch):
    monkeypatch.setattr(news.tm, "SLEEP", lambda s: None)
    get = FakeGet({1: read("kaohoon_p1.xml"), 2: read("kaohoon_p2.xml")})
    items, pages, err = news.fetch_feed(news.FEEDS[0][1], "Kaohoon", datetime(2026, 11, 2, 10, 55), get=get)
    assert pages == 1 and len(get.urls) == 1


def test_fetch_feed_respects_max_pages(monkeypatch):
    monkeypatch.setattr(news.tm, "SLEEP", lambda s: None)
    get = FakeGet({n: read("kaohoon_p1.xml") for n in range(1, 20)})
    _, pages, _ = news.fetch_feed(news.FEEDS[0][1], "Kaohoon", datetime(2026, 11, 1), max_pages=3, get=get)
    assert pages == 3


def offline(tmp_path):
    d = tmp_path / "rss"
    shutil.copytree(RSS, d)
    return d


NOW = "2026-11-02 11:10"


def test_one_feed_failing_still_shows_the_other(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(news.tm, "SLEEP", lambda s: None)
    good = FakeGet({1: read("infoquest_p1.xml")})

    def get(url):
        if "kaohoon" in url:
            raise OSError("connection refused")
        return good(url)
    rc = news.main(["--market", "--now", NOW, "--no-moves", "--hours", "2"], get=get)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Kaohoon: failed (connection refused)" in out and "InfoQuest" in out


def test_both_failing_is_a_failed_run(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(news.tm, "SLEEP", lambda s: None)
    assert news.main(["--market", "--now", NOW, "--no-moves"], get=FakeGet({}, fail=True)) == 1


def test_seen_state(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    off = offline(tmp_path)
    args = ["--symbols", "AIRA", "BAY", "--now", NOW, "--no-moves", "--hours", "2", "--offline", str(off)]
    assert news.main(args) == 0
    first = capsys.readouterr().out
    assert "AIRA" in first and "BAY" in first and "2 new" in first
    state = json.loads((tmp_path / "th-trader" / "state" / "news.json").read_text(encoding="utf-8"))
    assert len(state) >= 2
    assert news.main(args) == 0
    assert "Nothing new" in capsys.readouterr().out
    assert news.main(args + ["--all"]) == 0
    assert "AIRA" in capsys.readouterr().out


def test_seen_state_prunes_old_links(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "th-trader" / "state").mkdir(parents=True)
    (tmp_path / "th-trader" / "state" / "news.json").write_text(
        json.dumps({"https://old": "2026-09-01T10:00:00+07:00"}), encoding="utf-8")
    news.main(["--market", "--now", NOW, "--no-moves", "--hours", "2", "--offline", str(offline(tmp_path))])
    state = json.loads((tmp_path / "th-trader" / "state" / "news.json").read_text(encoding="utf-8"))
    assert "https://old" not in state


def test_default_symbols_from_files(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / "th-trader"
    (d / "state").mkdir(parents=True)
    (d / "watchlist.json").write_text(json.dumps({"hits": [{"symbol": "AIRA"}]}), encoding="utf-8")
    (d / "plan.json").write_text(json.dumps({"symbol": "BAY"}), encoding="utf-8")
    (d / "state" / "trades.json").write_text(json.dumps({"2026-11-02": [
        {"side": "buy", "symbol": "EURO", "qty": 100, "price": "5", "at": "10:30:00"}]}), encoding="utf-8")
    assert news.user_symbols(datetime(2026, 11, 2, 11, 0)) == ({"AIRA", "BAY", "EURO"}, {"EURO"})


def test_select_newest_first_and_only_matches():
    items = news.parse_feed(read("kaohoon_p2.xml"), "Kaohoon")
    got = news.select(items, {"EURO", "TPLAS"}, {}, show_all=False, market=False)
    assert [g["symbols"] for g in got] == [["EURO"], ["TPLAS"]] or [g["symbols"] for g in got] == [["TPLAS"], ["EURO"]]
    assert got[0]["published"] >= got[1]["published"]


def test_telegram_sends_held_symbols_only(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "th-trader" / "state").mkdir(parents=True)
    (tmp_path / "th-trader" / "state" / "trades.json").write_text(json.dumps({"2026-11-02": [
        {"side": "buy", "symbol": "EURO", "qty": 100, "price": "5", "at": "10:30:00"}]}), encoding="utf-8")
    sent = []
    monkeypatch.setattr(news.notify, "send_telegram", lambda text: (sent.append(text) or (True, "sent")))
    news.main(["--symbols", "AIRA", "EURO", "--now", NOW, "--no-moves", "--hours", "2", "--offline",
               str(offline(tmp_path)), "--telegram"])
    assert len(sent) == 1 and "EURO" in sent[0] and "AIRA" not in sent[0]
