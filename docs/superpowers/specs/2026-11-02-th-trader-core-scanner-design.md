# Thai trader skills, part 1: Thai market core and scanner

## Goal

The `trader-*` skills (Jake Schincariol, MIT) are built for day trading US stocks: New York time, the NYSE calendar, USD, cent rounding, Nasdaq Trader symbol lists and SEC EDGAR. They do not work on SET/mai stocks. `trader-scanner` turns `PTT.BK` into `PTT-BK`, fetches nothing and prints "No setups", and dates a Bangkok daily bar on the previous US day.

This project builds a separate Thai set, `th-trader-*`, for day trading SET/mai stocks with Claude Code. The original `trader-*` skills stay installed and untouched, so both sets can be used side by side.

All output is data, statistics and backtest results. Nothing is phrased as a recommendation to buy or sell, and no skill places, changes or cancels an order.

## Decisions made with the user (2026-11-02)

- A separate set, not a `--market th` flag in the original scripts, so updating the original set never overwrites the Thai work.
- The main use is day trading: in and out on the same day, the morning opening range by default, flat before the close.
- Approach A: port the original scripts and move every Thai market rule into one shared module, `th_market.py`.
- Source lives in this repo, `D:\Source\ThaiTraderSkills`, and an install script copies it into `~/.claude/skills/`.
- The work is split into sub-projects, each with its own spec, plan and implementation:
  1. **This spec:** the Thai market core (`th_market.py`) and `th-trader-scanner`.
  2. Trade planning and simulation: `th-trader-exit`, `th-trader-budget`, `th-trader-risk`, `th-trader-timemachine` (daily and 5-minute bars).
  3. Intraday and news: `th-trader-entry`, `th-trader-alerts`, `th-trader-news` (ข่าวหุ้น and อินโฟเควสท์ RSS, the same sources CrabTrade777 uses).
- Not built:
  - `trader-analyst`: there is no free source of Thai financial statements that does not mean getting around set.or.th's bot protection (CrabTrade777 reached the same conclusion on 2026-10-15).
  - A Thai `trader-pelosi` (executives' Form 59 trades): `www.sec.or.th` answers 403 "Block Response" and `market.sec.or.th` resets the connection for scripted requests. Parked. It can become sub-project 4 if the user registers at the SEC API portal (api-portal.sec.or.th) and it turns out to carry Form 59 data. That was not verified.
- We never get around a site's bot protection.

## Facts checked on 2026-11-02

Yahoo chart API, `interval=5m&range=60d`, for PTT.BK, CPALL.BK and SINGER.BK:

- 60 sessions back, `exchangeTimezoneName` Asia/Bangkok, 78–79 bars a day.
- The morning bars run 10:00–12:25 with volume. The 10:00 bar can have volume 0 (the opening auction).
- The bars stamped 12:30–13:55 are present with `null` volume and prices: the lunch break.
- The afternoon bars run 14:00–16:25 with volume. One bar stamped 16:30 has volume 0.

SET trading hours (from `thai-investment-analysis` `references/market-and-tax.md`, VERIFIED): pre-open 09:30, morning session to 12:30, afternoon pre-open 13:30, afternoon session to 16:30, then pre-close and off-hour trading to 17:00.

Tick sizes (same reference):

- 2009 to 2026-11-15, VERIFIED: < 2: 0.01; 2–5: 0.02; 5–10: 0.05; 10–25: 0.10; 25–100: 0.25; 100–200: 0.50; 200–400: 1.00; ≥ 400: 2.00.
- From 2026-11-16 a new table gives smaller ticks for prices of THB 5–50. Its values were **not found** (GAP). They must come from the official SET notice.

Board lot: 100 shares, or 50 if the price has been at or above THB 500 for 6 months (VERIFIED). Daily price limit: ±30% of the previous close.

SET holidays for 2026 and 2027: `CrabTrade777/src/CrabTrade.Api/Data/Seed/set-holidays.json` (37 entries, `{date, name}`).

## Part 1: `th_market.py`, the shared module

Python standard library only, like the original set. Prices and money use `Decimal`.

### Data files (`shared/data/`)

- `set-holidays.json`, copied from CrabTrade777's seed. A date after the last year in the file stops the tool with a line asking for the next year's holidays. It never guesses.
- `tick-tables.json`: a list of tables, each with `valid_from`, `valid_to` (or null), `status` and `bands` (`[{from, tick}]`).
  - The 2009 table is `"status": "verified"`, valid to 2026-11-15.
  - The 2026-11-16 table is `"status": "missing"` with no bands until the values are entered from SET's notice.
  - Decided by the user on 2026-11-02: until those values are entered, a date under the missing table uses the 2009 table, and every run that does so prints one warning line: "SET's tick table from 2026-11-16 is not in tick-tables.json yet; prices were rounded with the 2009 table." The JSON outputs carry `"tick_table": "2009 (fallback)"`. The new table is reported to have smaller ticks for THB 5–50, so a price on the 2009 grid is expected to stay a valid price, only coarser. That is unverified until the new values are in.
  - Once the new table's bands are entered and its status becomes `verified`, the fallback and the warning stop by themselves.

### Functions

| Function | What it does |
|---|---|
| `now_bkk()`, `to_bkk(utc)`, `ts_to_bkk(ts)` | Bangkok time as a fixed UTC+7 offset. Thailand has no daylight saving, so no tz database is needed. |
| `is_trading_day(d)` | Not a weekend and not in `set-holidays.json`. |
| `sessions(d)` | The day's sessions as `[(10:00, 12:30), (14:00, 16:30)]`, or `[]` on a closed day. Pre-open, pre-close and off-hour trading are left out. |
| `slots(d, interval)` | The bar start times of both sessions in order, skipping the lunch break. |
| `tick(price, d)` | The tick size for that price on that date, from the table valid on `d`. |
| `round_down_tick(price, d)`, `round_up_tick(price, d)`, `round_tick(price, d)` | Round to a valid price on that date. |
| `price_limits(prev_close, d)` | Ceiling and floor at ±30%, rounded inward to a valid tick. |
| `board_lot(price, lot=None)` | 100 by default. `--lot 50` is a user override: the 6-month rule needs price history the tool cannot be sure of. |
| `to_yahoo(symbol)` | `PTT` → `PTT.BK`. A symbol that already ends in `.BK` is left alone. Any other dot or dash in a SET symbol is kept as it is. |
| `fetch_bars(symbols, interval, range)` | Yahoo spark (≤ 20 symbols a request) and chart JSON, polite pauses and one retry after a 429, as in the original. Daily bars are dated by their Bangkok date. Intraday `null` bars (the lunch break) are dropped. |

### Failure rules

- Fetching 0 of N symbols is a failed run: one plain line naming the cause and exit code 1, never "No setups".
- Fetching some but not all symbols is reported: "Fetched 812 of 930; 118 failed (first 10: …)".
- Every result says how fresh its data is: "data to 2026-10-30 16:25 Bangkok".

## Part 2: `th-trader-scanner`

`scan.py` ported from `trader-scanner`, with every US rule replaced by `th_market.py`.

### `scan.py universe --file <listed companies file>`

- Reads the SET listed-companies file the user downloads from set.or.th in a browser. SET serves it both as a real Excel file and as an HTML table with an `.xls` name, so both are read, and columns are found by header name, as CrabTrade777's `ListedCompaniesParser` does. A real `.xls` (BIFF) file needs a reader outside the standard library. If no such reader is available, the tool says to save the file as `.csv` or `.xlsx` from Excel; `.xlsx` (zip + XML) and `.csv` are read with the standard library.
- Keeps SET and mai common shares. It drops warrants (`-W…`), derivative warrants, and foreign-board (`-F`) lines.
- Writes `th-trader/universe.txt`: one symbol per line, with the market (SET or mai) as a second column.
- `--symbols PTT,CPALL` works on every command without a universe file.

### `scan.py daily` (after the close or before the open)

- The same five setups and formulas as the original: `breakout_20d`, `bull_flag`, `reclaim_ma20`, `oversold`, `oversold_bounce`.
- Filters for Thai prices: 20-day average traded value (close × volume) ≥ THB 50 million (`--min-value`, the same threshold as CrabTrade777's "high liquidity" star) and price ≥ THB 1 (`--min-price`). The original's `--min-volume` is kept, with default 0.
- A new tag, `at_ceiling`: the last close is at the +30% ceiling, so buying near that price the next day may not be possible.
- Uses only complete Bangkok trading days. Today's bar counts only after 16:40 Bangkok.

### `scan.py orb` (from about 10:20)

- The opening range is the first 15 minutes of the morning session, 10:00–10:15 (`--or-minutes`).
- A breakout is the first completed 5-minute bar that closes above the range high, as in the original's two stages (closes first, then highs and lows to confirm).
- Bars follow `slots(d, 5)`, so the lunch break is not a gap. A complete day has 30 morning bars and 30 afternoon bars.
- Run between 12:30 and 14:00, it uses the bars up to 12:30.
- Yahoo's .BK data runs 15–30 minutes behind. Every result shows the time of the latest bar. When that is more than 30 minutes behind the clock in a session, it prints a warning that a breakout may already be stale.
- `--offline <file>` and `--now "<date> <time>"` (Bangkok time) replay a saved day, as in the original.

### Output

`th-trader/watchlist.json` in the original's shape, plus `"currency": "THB"`, `"market": "SET"` and the `at_ceiling` tag. The skills in sub-projects 2 and 3 read it.

## Repo, install and language

```
ThaiTraderSkills/
  README.md
  LICENSE                     MIT; credits the original day-trader-skill (Jake Schincariol, MIT)
  shared/
    th_market.py
    data/set-holidays.json
    data/tick-tables.json
  skills/
    th-trader-scanner/SKILL.md
    th-trader-scanner/scan.py
  tests/                      pytest with saved Yahoo JSON fixtures, no network
  scripts/Install-Skills.ps1
  docs/superpowers/specs/, plans/
```

- Python files use `snake_case` (the language's own convention). Skill names and folders use `kebab-case`. The PowerShell script uses `Verb-Noun`.
- The skills run with the Python standard library only. pytest is a development dependency.
- Commands use `python`, not `python3`: on this Windows machine `python3` is the Microsoft Store stub.
- Generated files go to `th-trader/` in the working directory, apart from the original set's `trader/`.
- `Install-Skills.ps1` copies each `skills/<name>/` to `~/.claude/skills/<name>/` and puts `th_market.py` and `data/` into each one, so every installed skill stands alone. It writes a marker file (`.installed-from-thaitraderskills`) into each folder it installs. It replaces only folders that carry that marker. If a target folder exists without the marker, it stops. It never touches `trader-*`.
- `SKILL.md` is in English like the other skills, and its description also lists Thai trigger phrases ("สแกนหุ้นไทย", "หาหุ้นเบรก", "หุ้นไหนเบรก opening range").
- Scripts print English, which avoids Windows console encoding problems. Claude reports the results in Thai.

## Testing

- `th_market.py`:
  - tick rounding at the band edges (1.99/2.00, 4.98/5.00, 24.90/25.00, 99.75/100.00)
  - a date under the missing 2026-11-16 table rounds with the 2009 table and reports the fallback; with the new bands filled in, it uses them and reports no fallback
  - the ±30% limits
  - `sessions`/`slots` on a normal day, a holiday and a weekend
  - a date past the last holiday year
  - `to_yahoo`
  - lunch-break `null` bars dropped
  - daily bars dated in Bangkok time
- Scanner:
  - each of the five setups and `at_ceiling` on constructed closes
  - the value filter
  - ORB on a saved real PTT 5-minute day, including a run cut at 13:00
  - a stale-data warning
  - 0 of N fetched exits 1
  - the universe reader on an HTML `.xls`, an `.xlsx` and a `.csv`
- One manual end-to-end run against live Yahoo before calling it done.

## Open items

- **The 2026-11-16 tick table.** Its values should be entered into `tick-tables.json` from SET's official notice. Until then every tool, sub-project 2 included, rounds with the 2009 table and warns (see Part 1, data files).
- The holiday file must gain 2028 before 2028.
- Short-selling and other 16 Nov 2026 rule changes matter only if a later sub-project adds the short side. All skills are long only.
