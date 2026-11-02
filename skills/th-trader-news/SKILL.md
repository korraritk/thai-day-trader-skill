---
name: th-trader-news
description: >-
  Thai stock news for SET and mai symbols from the ข่าวหุ้น (Kaohoon) and
  อินโฟเควสท์ (InfoQuest) RSS feeds: matched to the user's symbols (watchlist,
  plan, open positions, or --symbols), tagged (earnings, dividend, capital,
  deal, analyst, regulator, macro), newest first in Bangkok time, only what was
  not shown before, with each symbol's move vs the previous close. Can send
  items for held symbols to Telegram. Use when the user says "ข่าวหุ้น", "มีข่าว
  อะไรบ้าง", "ทำไมหุ้นตัวนี้วิ่ง", "สรุปข่าวเช้านี้", "ข่าว PTT", "any news on",
  "why is it moving", "morning news", or before th-trader-entry.
argument-hint: "[--symbols PTT CPALL] [--hours 12] [--market] [--all]"
---

# th-trader-news

The news check reads the two Thai stock news feeds CrabTrade777 uses and shows
what touches the user's symbols. It is a port of `trader-news`
(day-trader-skill, Jake Schincariol, MIT): Yahoo headlines and SEC filings are
replaced by ข่าวหุ้น and อินโฟเควสท์, and symbols are matched with CrabTrade777's
rule.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run with `python`, not `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/news.py"                          # your symbols, last 12 hours
python "${CLAUDE_SKILL_DIR}/news.py" --symbols PTT CPALL --hours 48
python "${CLAUDE_SKILL_DIR}/news.py" --market --hours 6       # every item ("สรุปข่าวเช้านี้")
python "${CLAUDE_SKILL_DIR}/news.py" --all                    # items already shown too
python "${CLAUDE_SKILL_DIR}/news.py" --telegram               # also send items for symbols you hold
```

Other flags: `--no-moves`, `--json`, `--offline DIR` (saved feeds
`kaohoon_p1.xml`, `infoquest_p1.xml`, ...), `--now "2026-11-02 11:10"`.

## What it reads and writes

| file | what |
| --- | --- |
| `th-trader/watchlist.json`, `th-trader/plan.json`, `th-trader/state/trades.json` | the user's symbols: scanner hits, the planned symbol, today's open positions |
| `th-trader/universe.txt` | with `--market`, every SET symbol, to label items |
| `https://www.kaohoon.com/feed`, `https://www.infoquest.co.th/feed` | page by page (`?paged=N`, 0.5 s apart) until an item is older than `--hours`, at most 10 pages each |
| `th-trader/state/news.json` | links already shown, kept 30 days |

## How it matches and tags

- **A symbol matches** when an RSS category equals it, or an uppercase Latin
  word in the title does ("หุ้นPTTขึ้น" matches PTT; "ptt" does not).
- **Common abbreviations are left out**, even when they are also symbols: SET,
  MAI, SET50, SET100, IPO, CEO, CFO, GDP, EPS, ESG, AI, EV, US, USD, THB, BOT,
  FED, ECB, IMF, ETF, REIT, NAV, PE, PBV, Q1-Q4, YOY, QOQ.
- **Tags** come from keywords in the title: earnings (งบ, กำไร, ขาดทุน,
  ไตรมาส), dividend (ปันผล, XD), capital (เพิ่มทุน, หุ้นกู้, warrant, RO, PP),
  deal (ซื้อกิจการ, ควบรวม, เข้าซื้อ, M&A), analyst (เป้าราคา, แนะนำ, บล.,
  upgrade, downgrade), regulator (ก.ล.ต., ตลท., SP, H, ห้ามซื้อขาย, Cash
  Balance), macro (เศรษฐกิจ, ดอกเบี้ย, กนง., Fed, เงินเฟ้อ, ค่าเงิน).
- **Moves:** one line per symbol that has news: the last price vs the previous
  close (Yahoo, delayed).

## Steps

1. Run `news.py` (or with `--symbols`). The first line says how many pages each
   feed gave; a failed feed is named there.
2. Tell the user what is new, grouped by symbol, with the tags and links. Say
   which items might explain a move.
3. **Headlines are leads, not facts: open the story.** Do not summarise beyond
   the headline as if it were the article; give the link.
4. "สรุปข่าวเช้านี้": `news.py --market --hours 6`, then group by tag.

## Rules

- Only the two RSS feeds, at a polite pace. Never get around a site's bot
  protection or scrape article pages.
- News is not a signal by itself. Data and statistics, not a recommendation to
  buy or sell.
