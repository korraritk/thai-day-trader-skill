---
name: th-trader-scanner
description: >-
  Thai Chart Scanner for SET and mai stocks. Builds the list of SET/mai common
  shares from the listed-companies file the user downloaded from set.or.th,
  runs an after-the-close scan of Yahoo daily bars (20-day closing-high
  breakout, bull flag, reclaimed the 20-day average, oversold, oversold
  bounce, closed at the +30% ceiling) filtered by 20-day average traded value
  in baht, and an opening-range breakout scan on 5-minute bars of the morning
  session (10:00 to 10:15 by default, lunch break handled), then writes
  th-trader/watchlist.json. Bangkok time, SET holidays, delayed Yahoo .BK data.
  Use when the user says "scan the Thai market", "scan SET", "Thai stock
  setups", "SET opening range breakouts", "สแกนหุ้นไทย", "หาหุ้นเบรก",
  "หุ้นไหนเบรก opening range", "หุ้นไทยตัวไหนเข้าเงื่อนไข", or wants a
  watchlist of Thai stocks for the day. For US stocks use trader-scanner.
argument-hint: "[universe --file F | daily | orb] [--at HH:MM]"
---

# th-trader-scanner

The Thai Chart Scanner reads free Yahoo charts of SET and mai stocks and lists
the ones that match a rule. A scan is a list of charts that match a rule, not
a list of stocks to buy, and a setup on a chart is not a prediction.

It is a port of `trader-scanner` (day-trader-skill, Jake Schincariol, MIT) with
every US rule replaced by Thai ones in `th_market.py`: Bangkok time, the SET
holiday file, the morning and afternoon sessions, tick sizes and the ±30%
price limits.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).
Run with `python`, not `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/scan.py" universe --file listedCompanies_th_TH.xls  # -> th-trader/universe.txt
python "${CLAUDE_SKILL_DIR}/scan.py" daily                       # after 16:40 or before the open
python "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols PTT,CPALL,ADVANC
python "${CLAUDE_SKILL_DIR}/scan.py" orb                         # from about 10:20 Bangkok
python "${CLAUDE_SKILL_DIR}/scan.py" orb --at 11:00              # cut the clock at 11:00 today
python "${CLAUDE_SKILL_DIR}/scan.py" orb --offline day.json --now "2026-10-30 11:00"
```

Add `--json` for machine-readable output. Exit code 0 when the scan found
something, 1 when it did not or could not run.

## What it reads and writes

| File | What |
|---|---|
| the listed-companies file | read by `universe --file`: the file the user downloads from set.or.th in a browser (HTML saved as `.xls`, `.xlsx` or `.csv`) |
| `th-trader/universe.txt` | one `SYMBOL|SET` or `SYMBOL|mai` a line |
| `th-trader/watchlist.json` | the hits, with `"currency": "THB"`, `as_of`, the filters and `tick_table` |
| `th-trader/config.json` | optional: `no_entries_after` ("15:30") for `orb` |

All paths are relative to the folder Claude runs in. The US set's `trader/`
folder is never read or written.

## The universe

The skill never scrapes set.or.th (it is behind bot protection). The user
downloads the listed-companies file in a browser, then:

```bash
python "${CLAUDE_SKILL_DIR}/scan.py" universe --file "<path to the file>"
```

It keeps SET and mai common shares and drops warrants (`-W`, `-W1`...),
foreign-board lines (`-F`) and derivative warrants. A real old binary `.xls`
cannot be read with the standard library: the tool says to save it as `.xlsx`
or `.csv` from Excel. Without a universe file, `--symbols PTT,CPALL` works on
every command.

## The daily scan (after the close)

Stage 1 fetches about 3 months of daily closes (Yahoo spark, 20 symbols a
request) and tags:

| Tag | Rule |
|---|---|
| `breakout_20d` | last close above the highest close of the 20 days before |
| `bull_flag` | up more than 10% from 15 to 5 days ago, then the last 5 closes within 5% |
| `reclaim_ma20` | yesterday under the 20-day average, today above it |
| `oversold` | RSI(14) under 30 |
| `oversold_bounce` | RSI under 30 yesterday and today closed higher |
| `at_ceiling` | today closed at the +30% ceiling: buying near that price tomorrow may not be possible |

Stage 2 fetches the daily chart of each match (spark has no volumes) for its
20-day average traded value (close × volume) and keeps those at or above
`--min-value` (THB 50,000,000). Price floor `--min-price` 1 baht. Today's bar
counts only after 16:40 Bangkok, when pre-close and ATC are over.

## The opening-range breakout scan (during the session)

The range is the first 15 minutes of the morning session, 10:00 to 10:15
(`--or-minutes`). A breakout is the first completed 5-minute bar that closes
above the range high, confirmed on the real highs and lows (stage 2, the most
traded `--max-confirm` 200 first).

- The lunch break (12:30 to 14:00) is not a gap. A run in the break uses the
  bars to 12:30.
- Yahoo's .BK data runs 15 to 30 minutes behind. The scan cuts at the latest
  bar Yahoo has and says so ("data to 10:35 Bangkok"). When that is more than
  30 minutes behind the clock in a session it prints a warning; repeat it.
- A chart missing a 5-minute bar is skipped and counted, never guessed.
- After `no_entries_after` (15:30) it says the window is over; replay with
  `--at`.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and delayed.
- Partial results are said as partial: "Fetched 812 of 930 charts (118 failed
  or had no data)". Fetching none is a failure, never "No setups".
- SET holidays come from `data/set-holidays.json` (2026 and 2027). A date in a
  later year stops with a line asking for that year's holidays.
- Tick sizes come from `data/tick-tables.json`. SET's new table from
  2026-11-16 has no values yet; until it does, the tools use the 2009 table
  and print "SET's tick table from 2026-11-16 is not in tick-tables.json yet;
  prices were rounded with the 2009 table." Repeat that line to the user
  whenever it is printed.

## Steps

1. When the user has a fresh listed-companies file: `scan.py universe --file <file>`.
2. After 16:40 or before the open: `scan.py daily`. Tell the user the counts
   (fetched, scanned, matched, passed the value filter) and the top few by
   setup, with the date of the closes.
3. From about 10:20 Bangkok: `scan.py orb`. Tell the user the time the data
   reaches, how many usable charts broke their range and when, and any stale
   warning.
4. The user picks a chart. Never pick for them.

Report in Thai. Keep symbols, numbers and setup names as printed.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data in Bangkok time.
- Say "matched a rule" ("เข้าเงื่อนไข"), never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, never scrapes set.or.th, and never promises that a
setup works. It is education and tooling, not investment advice.
