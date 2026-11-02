# Thai trader skills, part 3: entry, alerts and news

Parts 1 and 2 built `th_market.py`, the scanner, exit, Time Machine, budget and risk. This part ports the last three `trader-*` skills (day-trader-skill, Jake Schincariol, MIT) to SET/mai:

- `th-trader-entry`: when an opening-range or level breakout triggers.
- `th-trader-alerts`: price, stop, target and clock alerts, in Claude and on Telegram.
- `th-trader-news`: Thai stock news for the user's symbols.

The rules of parts 1 and 2 hold:

- Data and statistics only; nothing places an order.
- Python standard library only, `python` not `python3`, `Decimal` for prices.
- English script output.
- Generated files in `th-trader/`.
- We never get around a site's bot protection.

## Decisions made with the user (2026-11-02)

- Alerts print in Claude, as in the original, **and** go to Telegram through the user's own bot. The token and the chat id come from environment variables, never from a file or the chat.
- News uses the same two RSS feeds as CrabTrade777: ข่าวหุ้น and อินโฟเควสท์.
- Entry and alerts say plainly how far behind Yahoo's .BK data is.

## Facts checked on 2026-11-02

**News feeds:**
- `https://www.kaohoon.com/feed` and `https://www.infoquest.co.th/feed` answer 200 with 10 items each.
- Both feeds page back with `?paged=N`; kaohoon's page 5 reached the previous day.
- Categories are CDATA, mostly topics (บริษัทจดทะเบียน, หุ้นพลังงาน...), sometimes a symbol.

**Yahoo:**
- The headline RSS for `PTT.BK` answers, but with 3 items, the newest from August 2026. Not used.
- Yahoo .BK 5-minute bars run 15 to 30 minutes behind (part 1). The v8 chart's `meta.regularMarketTime` is the last trade time, and `meta.chartPreviousClose` / `regularMarketPrice` give the move.

**CrabTrade777's matching** (`News/NewsStockMatcher.cs`): a category equal to a symbol, or an uppercase Latin token in the title (lookarounds, not `\b`, because Thai letters are word characters), minus a stop list.

## Shared additions

### `shared/notify.py`

`send_telegram(text) -> (sent: bool, why: str)`:

- Reads `TH_TRADER_TELEGRAM_TOKEN` and `TH_TRADER_TELEGRAM_CHAT_ID` from the environment.
- Posts to `https://api.telegram.org/bot<token>/sendMessage` with `urllib`, 10-second timeout.
- Without the variables it returns `(False, "Telegram not set up: TH_TRADER_TELEGRAM_TOKEN / _CHAT_ID")`.
- The token never appears in any output, error message or file. Errors are reported by HTTP status only.

### `th-trader/prices.json` (written by `alerts.py quote --write`)

`{"created_at", "prices": {"PTT": {"price", "prev_close", "time", "source": "yahoo", "delay_note"}}}`

- **Read by budget:** `size.py` uses a fresh price (under 15 minutes old, by its `time`) the way `--price` does, when it is above the plan's entry.
- **Read by risk:** `check.py` takes `prev_close` from it for the floor/ceiling row when `--prev-close` is not given.
- Both say which source they used.

## Part 1: `th-trader-entry` (`entry.py`)

Commands, as in the original:

- `check SYMBOL [--setup orb|level] [--level X] [--or-minutes 15] [--min-volume-x X] [--now ..] [--offline FILE] [--write]`
- `watch SYMBOL [--every 60] [--until HH:MM]`
- `watchlist`, which checks every hit in `th-trader/watchlist.json`

The setups:

- **`orb`:** the range is the high and low of the first `--or-minutes` of the morning session (10:00–10:15). The trigger is the first **completed** 5-minute bar after the range that **closes** above the range high.
- **`level`:** the first completed bar that closes above `--level`.
- **`--min-volume-x X`:** the trigger bar needs X times the average volume of the range bars.
- **Price and time:** a bar counts only once closed. The trigger time is the bar's close time, and the price is its close.

Statuses:

| Status | When |
|---|---|
| `triggered` | a bar closed above the range high or the level |
| `waiting` | not open yet, the range still forming, the lunch break (12:30–14:00, never an expiry), or no close above yet |
| `expired` | no trigger by `no_entries_after` (15:30) |
| `no-data` | the market is closed, or Yahoo sent no bars |

The delay, which the original does not have:

- Every status line ends with "data to HH:MM Bangkok (N min behind)".
- A trigger older than 15 minutes by the clock adds: "triggered N minutes ago in Yahoo's delayed data; the price now may be well away from it".
- `entry.json` records `data_to`, `behind_minutes` and `trigger_age_minutes`.

`entry.json` holds the fields `th-trader-exit` already reads: `symbol`, `triggered`, `price`, `at`, `date`, `or_high`, `or_low`, `or_minutes`, `setups`.

`watch`:

- It polls every `--every` seconds (default 60, at least 30) and prints one line per newly closed bar.
- It waits through the lunch break.
- It stops on a trigger (exit 0, writes `entry.json`, sends Telegram), on expiry, at the close, or at `--until` (exit 1).
- With `--now`/`--offline` it replays without sleeping.

## Part 2: `th-trader-alerts` (`alerts.py`)

Commands:

| Command | What it does |
|---|---|
| `clock` | Whether SET is open, in the lunch break, or closed (holiday name), with the next open or close |
| `quote SYMBOL... [--plan] [--write]` | Yahoo v8 chart (`range=1d&interval=5m`): `regularMarketPrice`, `chartPreviousClose`, `regularMarketTime` → `prices.json` |
| `add --from-plan` | Three rules for the plan's date: `below` = stop, `above` = target, and, for an intraday plan, `time` = `flat_by` labelled "flat by" |
| `add --symbol S --below X` / `--above X` / `--pct N [--direction up\|down]` / `--at HH:MM --label T` / `--opening-bell` | One rule |
| `check [--now ..] [--offline DIR] [--no-telegram]` | Every active rule; rings what hit |
| `list` | The rules |
| `reset ID` | Re-arms a rule |

Rules live in `th-trader/alerts.json`. Each has an `id` (`<symbol>-<kind>-<n>`), its fields, an optional `date` (a plan's rules are for that date only), `rearm` and its ring state.

When a rule rings:
- **`above` / `below`:** once, then quiet until reset. With `rearm`, it re-arms when the price crosses back.
- **`pct_move`:** at most once a day.
- **`time` and `opening_bell` (10:00):** once per trading day.

A ring:
- prints a line such as "PTT 41.75 hit the stop 41.75 at 11:05 (data ~15 min behind)"
- sends the same text by Telegram unless `--no-telegram`
- reports a failed send in one line, while the rule still counts as rung

`check` fetches only the symbols of today's active rules, one request each, 0.5 seconds apart. Its exit code is 0 when something rang and 1 when nothing did.

The SKILL.md tells Claude to run `check` every few minutes with `/loop 5m` while a trade is open, and how to set the two environment variables (Windows: `setx`).

## Part 3: `th-trader-news` (`news.py`)

Commands:

- `news.py`: the user's symbols are every hit in `th-trader/watchlist.json`, the symbol in `th-trader/plan.json`, and the open positions in today's `th-trader/state/trades.json`.
- `--symbols PTT CPALL`
- `--hours N` (default 12)
- `--all`: show items already seen too
- `--market`: every item, not only the user's symbols
- `--no-moves`
- `--telegram`: send items for held symbols only

How it works:

- **Paging.** Each feed is read page by page (`?paged=N`, 0.5 seconds apart) until an item is older than `--hours`, at most 10 pages per feed.
- **Matching** is CrabTrade777's rule, ported:
  - An RSS category, trimmed and upper-cased, equal to a symbol.
  - Or a title token matching `(?<![A-Za-z0-9])[A-Z][A-Z0-9&]{1,9}(?![A-Za-z0-9])` equal to a symbol.
  - Tokens on the stop list are left out: SET, MAI, SET50, SET100, IPO, CEO, CFO, GDP, EPS, ESG, AI, EV, US, USD, THB, BOT, FED, ECB, IMF, ETF, REIT, NAV, PE, PBV, Q1–Q4, YOY, QOQ.
  - The symbol set is the user's symbols. With `--market`, the universe file is used when it exists.
- **Tags** come from Thai and English keywords. One item can get several:
  - earnings (งบ, กำไร, ขาดทุน, ไตรมาส, earnings)
  - dividend (ปันผล, XD)
  - capital (เพิ่มทุน, หุ้นกู้, warrant, RO, PP)
  - deal (ซื้อกิจการ, ควบรวม, M&A, เข้าซื้อ)
  - analyst (เป้าราคา, แนะนำ, upgrade, downgrade, บล.)
  - regulator (ก.ล.ต., ตลท., SET, SP, H, ห้ามซื้อขาย, Cash Balance)
  - macro (เศรษฐกิจ, ดอกเบี้ย, กนง., Fed, เงินเฟ้อ, ค่าเงิน)
- **Seen items.** Only items not seen before are shown, newest first, in Bangkok time, each with publisher, tags, symbols and link. Seen links are kept in `th-trader/state/news.json` for 30 days.
- **Moves.** With moves on, one line per symbol: last price vs the previous close (Yahoo chart, delayed). They are fetched only for symbols that have news.
- **Failures.** A feed that fails is reported in one line; the other still runs. Both failing is a failed run (exit 1).

## Repo changes

```
shared/notify.py
skills/th-trader-entry/       SKILL.md, entry.py
skills/th-trader-alerts/      SKILL.md, alerts.py
skills/th-trader-news/        SKILL.md, news.py
skills/th-trader-budget/size.py     + reads prices.json
skills/th-trader-risk/check.py      + reads prev_close from prices.json
tests/ + fixtures: two saved RSS pages per feed, a 5-minute chart for a replayed trigger
README.md                     + three skills, the Telegram variables
```

## Testing

- **notify:**
  - no variables → `(False, ...)` without a network call
  - a fake opener (no network) gets the right URL and body
  - an HTTP error message never contains the token
- **entry:**
  - orb on the saved PTT day: the trigger bar and time
  - a wick above the high is not a trigger
  - level
  - `--min-volume-x`
  - waiting at 10:10
  - waiting (not expired) at 13:00
  - expired at 15:45
  - no-data on a Saturday
  - the delay line and the stale-trigger warning
  - `entry.json` fields read back by `exit.py` (exit's `gather` on the written file)
  - `watch` replayed with `--now`/`--offline` stops at the trigger
- **alerts:**
  - `clock` at 09:00 / 11:00 / 13:00 / 17:00 / a holiday
  - `add --from-plan` makes three rules
  - `check` rings once, not twice; rearm
  - `pct_move` once a day
  - `time` once per trading day
  - opening bell
  - Telegram failure still counts the ring
  - `quote --write` shape
  - budget and risk reading `prices.json`
- **news:**
  - parsing both saved feeds (CDATA categories, `pubDate` to Bangkok)
  - matching (category, title token, stop list, a Thai word glued to a symbol "หุ้นPTTขึ้น")
  - tags
  - seen state
  - paging stops at `--hours`
  - one feed failing
- **Manual, live:** `news.py --market --hours 6`; `alerts.py quote PTT`; `entry.py check` on a symbol during a session; one Telegram message to the user's bot after they set the variables.

## Open items

- The two feeds' page size (10) and paging could change; a run reports how many pages it read.
- Telegram needs the user's bot token and chat id in environment variables, set by the user.
