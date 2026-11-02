# Thai entry, alerts and news Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `trader-entry`, `trader-alerts` and `trader-news` to SET/mai as `th-trader-entry`, `th-trader-alerts` and `th-trader-news`, with Telegram delivery for alerts and Thai RSS news.

**Architecture:**
- **New shared module:** `shared/notify.py` sends Telegram using environment variables.
- **Ported scripts:** each skill is one script ported from its original, using `th_market` for Bangkok time, sessions and Yahoo .BK data.
- **`prices.json` link:** alerts writes `prices.json`, and budget and risk read it.

**Tech Stack:** Python 3.11 standard library (`urllib`, `xml.etree`, `email.utils`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-th-trader-intraday-news-design.md`

**Port sources (read-only):**
- `C:\Users\15193\.claude\skills\trader-entry\entry.py`
- `C:\Users\15193\.claude\skills\trader-alerts\alerts.py`
- `C:\Users\15193\.claude\skills\trader-news\news.py`
- each of those folders' `SKILL.md`

**Porting rule:**
- Functions marked "port unchanged" are copied with a tool, never retyped.
- Every test literal below was computed from the fixtures at plan time (2026-11-02). A run that disagrees means the code is wrong, unless the fixture was re-recorded.

## Global Constraints

- Standard library only, `python` not `python3`, `Decimal` for prices, naive Bangkok datetimes, English output, files in `th-trader/`.
- Telegram:
  - The variables are `TH_TRADER_TELEGRAM_TOKEN` and `TH_TRADER_TELEGRAM_CHAT_ID`.
  - The token never appears in output, errors or files.
- The news feeds are `https://www.kaohoon.com/feed` and `https://www.infoquest.co.th/feed`, paged with `?paged=N`: at most 10 pages per feed, 0.5 s apart.
- Every entry and alerts line that uses Yahoo data says how far behind the data is.
- Commit messages are in Thai with a conventional prefix and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. **A feed item whose title has a symbol glued to Thai text ("หุ้นPTTขึ้น") or a lowercase look-alike ("ptt"):** the first matches PTT and the second does not. Test in Task 5.
2. **`alerts.py check` run twice in the same minute after a stop hit:** it rings once, and the second run is silent. Test in Task 4.
3. **A Telegram send that fails with an HTTP error:** the printed message has the status code and no token. Test in Task 1.
4. **`entry.py check` at 13:00 (lunch) on a day with no trigger yet:** it reports `waiting` with "lunch break", never `expired`. Test in Task 2.
5. **`prices.json` older than 15 minutes:** budget ignores it and says so. Test in Task 1.

---

## File Structure

| File | Responsibility |
|---|---|
| `shared/notify.py` | `send_telegram` |
| `shared/th_market.py` | + `read_prices(path, now, max_age_min=15)` |
| `skills/th-trader-entry/entry.py`, `SKILL.md` | the trigger, check / watch / watchlist |
| `skills/th-trader-alerts/alerts.py`, `SKILL.md` | clock, quote, rules, check |
| `skills/th-trader-news/news.py`, `SKILL.md` | feeds, matching, tags, seen state |
| `skills/th-trader-budget/size.py`, `skills/th-trader-risk/check.py` | read `prices.json` |
| `tests/fixtures/rss/` | `kaohoon_p1.xml`, `kaohoon_p2.xml`, `infoquest_p1.xml` |
| `tests/conftest.py` | + three skill folders on `sys.path` |

---

### Task 1: `notify.py`, `read_prices`, budget and risk read `prices.json`

**Files:** Create `shared/notify.py`, `tests/test_notify.py`, `tests/test_prices_link.py`. Modify `shared/th_market.py`, `skills/th-trader-budget/size.py`, `skills/th-trader-risk/check.py`, `tests/conftest.py`.

**Interfaces:**
- `notify.send_telegram(text: str, opener=urllib.request.urlopen) -> tuple[bool, str]`
- `th_market.read_prices(path: str, now: datetime, max_age_min: int = 15) -> dict[str, dict]`: returns fresh entries only, each `{price: Decimal, prev_close: Decimal | None, time: datetime}`. A missing file returns `{}`.
- `size.py --prices PATH` (default `th-trader/prices.json`): a fresh price above the plan's entry is used like `--price`, and the report says "the latest price from prices.json (HH:MM)". A stale one gets one line, "prices.json is N minutes old: not used".
- `check.py`: `prev_closes` falls back to `read_prices(...)`'s `prev_close` when `--prev-close` is not given (any age is fine for a previous close).

- [ ] **Step 1: Write the failing tests.**
  - `send_telegram` with no env vars returns `(False, ...)` and the opener is never called.
  - With env vars and a fake opener that records the request: the URL is `.../bot<token>/sendMessage`, and the body has the `chat_id` and the `text`.
  - A fake opener raising `HTTPError(code=401)`: the returned message contains "401" and not the token (Review Focus 3).
  - `read_prices` on a file 10 minutes old returns PTT; at 20 minutes old it returns `{}`.
  - `size.main` with a fresh `prices.json` at 42.50 sizes at 42.50. With a stale one it prints "not used" and sizes at the plan's entry (Review Focus 5).
  - `check.check` given no `--prev-close` but a `prices.json` with `prev_close` 42.00 blocks a 55.00 limit price on "ceiling".
- [ ] **Step 2: Run:** `python -m pytest tests/test_notify.py tests/test_prices_link.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: notify.py (Telegram ผ่าน env) และ prices.json ให้ budget/risk อ่าน`

---

### Task 2: `entry.py`, the trigger logic

**Files:** Create `skills/th-trader-entry/entry.py`, `tests/test_entry_logic.py`.

**Interfaces:**
- `class EntryError(Exception)`
- `evaluate(series, day, now, setup="orb", level=None, or_minutes=15, min_volume_x=None, no_entries_after=time(15,30)) -> dict` with keys:
  - `status` ("triggered" | "waiting" | "expired" | "no-data") and `why`
  - `price`, `at` ("HH:MM", the bar's close time), `bar_start`
  - `or_high`, `or_low`, `range_volume_avg`, `trigger_volume_x`
  - `data_to` ("HH:MM", the end of the latest complete bar), `behind_minutes`, `trigger_age_minutes`
- Bars come from `tm.intraday_bars(series, day, now)`. Only completed bars on the session grid count.

Rules:
- `orb`: range = the first `or_minutes // 5` bars; the trigger is the first bar after them with close > range high (strict).
- `level`: the first bar with close > `level`.
- With `min_volume_x`, a closing-above bar needs volume ≥ X × the range bars' average volume, or it is skipped and later bars are checked.
- `waiting` reasons:
  - "not open yet" (before 10:00)
  - "range still forming" (before 10:15 + 5 min)
  - "lunch break" (12:30–14:00, Review Focus 4)
  - "no close above yet"
- `expired`: now > `no_entries_after` with no trigger. `no-data`: a closed day, or no bars.

- [ ] **Step 1: Write the failing tests**, with the `chart_PTT_5m_60d.json` fixture, day 2026-10-29. Fixture facts: range 42.00 / 41.50; the 11:00 bar wicks above 42.00 without closing above; the 11:05 bar closes 42.25 at 11:10 with volume 5,984,486 against a range average of 3,063,430 (1.95×).
  - At `now` 11:30: triggered, `at` "11:10", price 42.25, `or_high` 42.0.
  - At `now` 11:05: waiting, "no close above yet". The wick is not a trigger.
  - `min_volume_x=2.0` at 11:30: not triggered at "11:10".
  - `level=42.0` at 11:30: triggered at "11:10".
  - At `now` 10:10: waiting, "range still forming".
  - Day 2026-10-30 (no trigger all day) at 13:00: waiting, "lunch break". At 15:45: expired.
  - Day 2026-10-31: no-data.
  - At `now` 11:30, `trigger_age_minutes` is 20 and `data_to` is "11:30".
- [ ] **Step 2: Run:** `python -m pytest tests/test_entry_logic.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `evaluate`.**
- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-entry หาจังหวะเข้า orb/level พร้อมบอกความช้าของข้อมูล`

---

### Task 3: `entry.py` CLI, `entry.json`, `SKILL.md`

**Files:** Modify `skills/th-trader-entry/entry.py`. Create `skills/th-trader-entry/SKILL.md`, `tests/test_entry_cli.py`.

**Interfaces:**
- `main(argv) -> int` with three commands:
  - `check SYMBOL [--setup] [--level] [--or-minutes] [--min-volume-x] [--now] [--offline FILE] [--write [PATH]] [--json] [--no-telegram]`
  - `watch SYMBOL [--every 60] [--until HH:MM] ...`
  - `watchlist [--watchlist PATH] ...`
- `entry.json`: `symbol`, `triggered`, `price` (string), `at`, `date`, `or_high`, `or_low`, `or_minutes`, `setups` (`["orb"]` or `["level"]`), `data_to`, `behind_minutes`, `trigger_age_minutes`, `created_at`.
- `watch` with `--now`/`--offline` steps a simulated clock 5 minutes at a time from `--now` without sleeping. On a trigger it writes `entry.json`, sends Telegram (unless `--no-telegram`) and exits 0. On expiry or `--until` it exits 1. `--every` below 30 is raised to 30 with a note.

- [ ] **Step 1: Write the failing tests:**
  - `check PTT --offline <60d fixture> --now "2026-10-29 11:30" --write --no-telegram` in `tmp_path` writes `th-trader/entry.json`, and exit's `gather` on it gives entry 42.25.
  - `watch PTT --offline ... --now "2026-10-29 10:20" --no-telegram` prints one line per bar and stops at 11:10 with exit 0.
  - The same from 2026-10-30 10:20 ends expired with exit 1.
  - `watchlist` over a two-hit watchlist prints two status lines.
  - A stale trigger (`now` 11:40) prints "triggered 30 minutes ago".
- [ ] **Step 2: Run:** `python -m pytest tests/test_entry_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, then write `SKILL.md`:
  - the original's structure
  - Thai triggers ("จังหวะเข้า", "เบรกหรือยัง", "เฝ้าหุ้นตัวนี้")
  - the delay rules
  - "it never places an order"
- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-entry คำสั่ง check/watch/watchlist และ SKILL.md`

---

### Task 4: `th-trader-alerts`

**Files:** Create `skills/th-trader-alerts/alerts.py`, `skills/th-trader-alerts/SKILL.md`, `tests/test_alerts.py`.

**Interfaces:**
- `class AlertError(Exception)`
- `clock_status(now) -> dict` with `state` ("open" | "lunch" | "closed" | "before open" | "after close"), `holiday`, `next`
- `make_rule(kind, symbol=None, price=None, pct=None, direction=None, at=None, label=None, date=None, rearm=False) -> dict`
- `rules_from_plan(plan) -> list[dict]`
- `evaluate(rules, quotes: dict[str, dict], now) -> (rung: list[dict], rules_after: list[dict])`, a pure function
- `main(argv)` with `clock`, `quote`, `add`, `check`, `list` and `reset`. Rules are stored in `th-trader/alerts.json`.

Ring rules follow spec Part 2:
- `above` / `below`: once, until `reset` or `rearm` (re-arms when the price crosses back)
- `pct_move`: once per date
- `time` and `opening_bell`: once per trading day, and only on trading days
- rules with `date`: only on that date

Each ring line names the symbol, price, level and time, plus "(data ~N min behind)" from the quote's `regularMarketTime`.

- [ ] **Step 1: Write the failing tests:**
  - `clock_status` at 09:00, 11:00, 13:00 and 17:00 on 2026-11-02, and 11:00 on 2026-01-02 (holiday name in `holiday`).
  - `rules_from_plan` on an intraday plan gives below 41.75, above 43.50 and a time rule at 16:20, all dated with the plan date.
  - `evaluate` with PTT at 41.70 rings the stop. The returned rules fed back with the same quote ring nothing (Review Focus 2).
  - With `rearm`, a price of 42.00 then 41.70 rings again.
  - `pct_move` 3% rings once on a date.
  - A `time` rule at 16:20 rings at 16:21 once and not on a Saturday.
  - The opening bell rings at 10:00 on a trading day.
  - `main(["check", ...])` with a fake `send_telegram` that fails still marks the rule rung and prints the failure line.
  - `quote --write` writes the `prices.json` shape that `th_market.read_prices` reads.
- [ ] **Step 2: Run:** `python -m pytest tests/test_alerts.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Port the rule-state logic shape from the original with a tool where it carries over; replace the NYSE clock with `th_market` sessions. Then write `SKILL.md`:
  - Thai triggers ("แจ้งเตือนเมื่อ", "เตือนถ้าชน stop", "ตลาดเปิดไหม", "ราคาตอนนี้")
  - `/loop 5m alerts check` while a trade is open
  - setting the two variables on Windows with `setx` (in the user's own terminal)
- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-alerts แจ้งเตือนราคา/เวลา ใน Claude และ Telegram`

---

### Task 5: `th-trader-news`

**Files:** Create `skills/th-trader-news/news.py`, `skills/th-trader-news/SKILL.md`, `tests/test_news.py`, and `tests/fixtures/rss/*.xml` (recorded once with a small script added to `record_fixtures.py`).

**Interfaces:**
- `parse_feed(xml_text, publisher) -> list[dict]`: each item has `title`, `link`, `published` (naive Bangkok), `categories`, `publisher`
- `match_symbols(item, symbols: set[str]) -> set[str]`: CrabTrade777's rule and stop list
- `tags(item) -> list[str]`
- `fetch_feed(url, publisher, since: datetime, max_pages=10, get=...) -> (items, pages_read, error)`
- `select(items, symbols, seen, show_all, market) -> list[dict]`
- `main(argv)` with `[--symbols ...] [--hours 12] [--all] [--market] [--no-moves] [--telegram] [--offline DIR] [--now ..] [--json]`

Behaviour per spec Part 3:
- The seen state lives in `th-trader/state/news.json` as `{link: first-seen ISO}`. Entries older than 30 days are pruned on write.

- [ ] **Step 1: Record the RSS fixtures** (`kaohoon` pages 1–2, `infoquest` page 1). Print, with a one-off script, each file's item count, oldest and newest `pubDate` in Bangkok time, and the categories that are SET symbols. Write the tests below against those printed values.
- [ ] **Step 2: Write the failing tests:**
  - `parse_feed` counts and Bangkok times match the printed values. CDATA categories are read.
  - `match_symbols`:
    - a category "PTT" matches
    - the title "หุ้นPTTขึ้นแรง" matches PTT
    - "ptt" does not
    - "SET ปิดบวก" does not match SET when SET is in the symbol set
    - "CEO ลาออก" does not match CEO (Review Focus 1)
  - `tags`: "กำไรไตรมาส 3 เพิ่มขึ้น" → earnings; "ประกาศจ่ายปันผล" → dividend; "กนง. คงดอกเบี้ย" → macro.
  - `fetch_feed` with a fake `get` serving page 1 then page 2 stops when an item is older than `since`, and reports pages read.
  - A `get` that raises on one feed: `main` prints a one-line failure for it and still shows the other. Both failing → exit 1.
  - The seen state: the second run shows nothing new; `--all` shows all.
- [ ] **Step 3: Run:** `python -m pytest tests/test_news.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement**, then write `SKILL.md`:
  - Thai triggers ("ข่าวหุ้น", "มีข่าวอะไรบ้าง", "ทำไมหุ้นตัวนี้วิ่ง", "สรุปข่าวเช้านี้")
  - "headlines are leads, not facts: open the story"
- [ ] **Step 5: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 6: Commit:** `feat: th-trader-news ข่าวหุ้นไทยจาก RSS ข่าวหุ้น/อินโฟเควสท์`

---

### Task 6: README, install, live checks

**Files:** Modify `README.md`, `tests/test_install.py`.

- [ ] **Step 1:** Extend `test_every_skill_is_standalone` to the 8 skills, each with `notify.py` installed. Run: `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 2:** Update `README.md`:
  - the three skills
  - the full chain: scanner → entry → exit → timemachine → budget → risk → (you place it) → `check.py log` → alerts → news
  - the Telegram variables
- [ ] **Step 3: Live checks** from a scratch folder:
  - `news.py --market --hours 6`
  - `news.py --symbols PTT CPALL --hours 48`
  - `alerts.py clock`
  - `alerts.py quote PTT CPALL --write`, then `size.py` reading it
  - `entry.py check PTT` (or `--now` on the last trading day)

  Record the outputs in the commit body. **Do not send a Telegram message**: the user's variables are not set in this session. Say so in the report.
- [ ] **Step 4: Commit:** `docs: README ชุดครบ 8 skill และผลทดสอบจริง`
