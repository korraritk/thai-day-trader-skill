---
name: th-trader-entry
description: >-
  Thai Entry Timer for SET and mai stocks. Watches a setup bar by bar on Yahoo's
  delayed .BK 5-minute bars and says when it triggered: the opening-range
  breakout (the first 15 minutes, 10:00-10:15 Bangkok) or a close above a level
  the user gives, with an optional volume check. Only a bar that CLOSES above
  counts. Says waiting (not open yet, range forming, lunch break, no close above
  yet), triggered with the time and price, or expired, and always how far behind
  the data is. Writes th-trader/entry.json for th-trader-exit and can send the
  trigger to Telegram. Long only; never places an order. Use when the user says
  "จังหวะเข้า", "เบรกหรือยัง", "เฝ้าหุ้นตัวนี้", "ทะลุกรอบเช้าหรือยัง",
  "has PTT broken out", "watch this stock", "time my entry", "check my
  watchlist", or after th-trader-scanner, before th-trader-exit.
argument-hint: "[check SYMBOL | watch SYMBOL | watchlist] [--setup orb|level]"
---

# th-trader-entry

The Entry Timer turns a setup into one moment: the bar that confirms it. A
price that pokes above the high in the middle of a bar is not an entry. A bar
that CLOSES above it is. Then it says the time and the price, writes them down
for the Exit Planner, and stops. It never buys anything.

It is a port of `trader-entry` (day-trader-skill, Jake Schincariol, MIT) with
SET sessions from `th_market.py`: 10:00-12:30 and 14:00-16:30 Bangkok, holidays
from `set-holidays.json`, and Yahoo's delay said out loud.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run with `python`, not `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/entry.py" check PTT                   # 15-minute opening range
python "${CLAUDE_SKILL_DIR}/entry.py" check PTT --write           # also th-trader/entry.json (+ Telegram once)
python "${CLAUDE_SKILL_DIR}/entry.py" check PTT --setup level --level 42.50
python "${CLAUDE_SKILL_DIR}/entry.py" check PTT --min-volume-x 1.5
python "${CLAUDE_SKILL_DIR}/entry.py" watch PTT --until 15:30     # one line per closed bar
python "${CLAUDE_SKILL_DIR}/entry.py" watchlist                   # every hit in th-trader/watchlist.json
```

Other flags: `--or-minutes N` (default 15), `--json`, `--no-telegram`,
`--offline FILE` (a saved Yahoo chart JSON, or a folder of `SYMBOL.json`),
`--now "2026-10-29 11:30"` (Bangkok) to replay a day. `watch` with `--now` or
`--offline` replays bar by bar without waiting.

## What it reads and writes

| file | what |
| --- | --- |
| `th-trader/watchlist.json` | the scanner's hits, for `watchlist` |
| `th-trader/config.json` | `no_entries_after` (15:30). Optional |
| Yahoo chart API | `PTT.BK`, 5-minute bars: free, unofficial, 15-30 minutes behind |
| `th-trader/entry.json` | `check --write`, or `watch` on a trigger: symbol, triggered, price, at, date, or_high, or_low, or_minutes, setups, data_to, behind_minutes, trigger_age_minutes |

## The rule

- **Opening range (orb).** The high and low of the 10:00, 10:05 and 10:10
  bars. The level to beat is the range high.
- **Level.** A price the user gives, like a 20-day high from the scanner.
- **The trigger** is the first completed bar after the range that CLOSES above
  the level. A wick is not a trigger; a close exactly at the level is not above.
- **The time** is that bar's close time (the 11:05 bar closes at 11:10). The
  price is its close.
- **Volume, if wanted.** `--min-volume-x 1.5` also needs 1.5 times the range
  bars' average volume. A thin bar is skipped and later bars are checked.
- **The lunch break** (12:30-14:00) is waiting, never expired.
- **Expired** after `no_entries_after` (15:30) with no trigger.

## The delay

Yahoo's .BK bars run 15 to 30 minutes behind. Every status line ends with
"data to HH:MM Bangkok (N min behind)" (the lunch break adds no delay). A
trigger more than 15 minutes old adds "This triggered N minutes ago in Yahoo's
delayed data; the price now may be well away from it". Tell the user both: on
delayed data the trigger is a lead to check in the broker's app, not a fill.

## Steps

1. Get the symbol from the user, or run `entry.py watchlist` after
   `th-trader-scanner`. Check `th-trader-news` for it first.
2. Run `entry.py check SYMBOL`. Tell the user the status in one line, with the
   delay.
3. To wait for it, run `entry.py watch SYMBOL --until 15:30` in the background,
   or `entry.py check SYMBOL --write` on `/loop 5m`. `watch` polls every
   `--every` seconds (60, at least 30), prints one line per new bar, waits
   through lunch, and exits 0 on the trigger (it writes `th-trader/entry.json`
   and sends Telegram) or 1 on expiry, the close or `--until`. `check --write`
   sends Telegram once per trigger, however often the loop runs it.
4. When it triggers, say the time, the price, how old it is, and that nothing
   was placed.
5. Hand off to `th-trader-exit` to set the stop and the target BEFORE anything
   is bought.

## Telegram

Set `TH_TRADER_TELEGRAM_TOKEN` and `TH_TRADER_TELEGRAM_CHAT_ID` in the user's
own terminal (see th-trader-alerts). Without them the trigger only prints, with
one line saying Telegram is not set up. Never ask for the token in the chat.

## Rules

- Bars only, on free delayed Yahoo data. Say so when a minute matters.
- Long only. It never places, changes or cancels an order.
- Data and statistics, not a recommendation to buy.
