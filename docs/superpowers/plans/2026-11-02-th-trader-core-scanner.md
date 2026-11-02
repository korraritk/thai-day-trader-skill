# Thai market core and th-trader-scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A shared Thai market module (`th_market.py`) and a SET/mai scanner skill (`th-trader-scanner`) ported from `trader-scanner`, installable into `~/.claude/skills/`.

**Architecture:** `shared/th_market.py` holds every Thai market rule (Bangkok time, SET calendar and sessions, tick tables, price limits, board lot, Yahoo .BK fetching). `skills/th-trader-scanner/scan.py` is the original `scan.py` with its US parts deleted and replaced by calls into `th_market`. `scripts/Install-Skills.ps1` copies each skill plus `th_market.py` and `data/` into `~/.claude/skills/<name>/`.

**Tech Stack:** Python 3.11 standard library only (skills); pytest (development only); PowerShell 5.1 (install script).

**Spec:** `docs/superpowers/specs/2026-11-02-th-trader-core-scanner-design.md`

**Port source (read-only, never edit):** `C:\Users\15193\.claude\skills\trader-scanner\scan.py` and `SKILL.md` (MIT, Jake Schincariol).

## Global Constraints

- Skills use the Python standard library only. pytest is the only development dependency.
- Run everything with `python`, never `python3`.
- Prices and money in `th_market` use `Decimal`. The scanner's setup math may stay `float` as in the original.
- Bangkok time is a fixed UTC+7 offset. Every datetime inside `th_market` and `scan.py` is **naive Bangkok time** unless the name says `utc`.
- Sessions: morning 10:00–12:30, afternoon 14:00–16:30. Pre-open, pre-close and off-hour are excluded.
- Tick table 2009 (to 2026-11-15): < 2: 0.01; 2–5: 0.02; 5–10: 0.05; 10–25: 0.10; 25–100: 0.25; 100–200: 0.50; 200–400: 1.00; ≥ 400: 2.00.
- From 2026-11-16 the table is `"status": "missing"`. Dates under it use the 2009 table and print exactly: `SET's tick table from 2026-11-16 is not in tick-tables.json yet; prices were rounded with the 2009 table.` JSON carries `"tick_table": "2009 (fallback)"`.
- Price limits ±30% of the previous close, rounded inward to a valid tick.
- Board lot 100; `--lot 50` is a user override.
- 0 of N fetched is a failed run: one plain line, exit code 1, never "No setups".
- Generated files go to `th-trader/` in the working directory.
- Script output is English. Every result says the time of its latest data in Bangkok time.
- Python files `snake_case`; skill folders `kebab-case`; PowerShell `Verb-Noun.ps1`.
- Commit messages in Thai, conventional prefix (`feat:`, `test:`, `docs:`), ending with the line `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. **Symbols with URL characters** (`L&E`, `S&J`): the Yahoo URL must percent-encode them (`L%26E.BK`) and map the answer back to `L&E`. Test in Task 3.
2. **One bad symbol in a spark batch** (Yahoo omits it or returns an error object): the other 19 still count as fetched; the bad one goes to `failed`. Test in Task 3.
3. **A thinly traded stock with a missing 5-minute bar outside the lunch break**: `orb` counts it as unusable and moves on, never crashes or calls it a breakout. Test in Task 6.
4. **`orb` run on a holiday, a weekend, before 10:15, or after 16:30**: one plain line saying the market is closed / the range is not complete yet / which day it replayed, exit 1, no traceback. Test in Task 6.
5. **A CSV universe file saved by Thai Excel** (cp874, or UTF-8 with BOM, Thai company names): read without a UnicodeDecodeError. Test in Task 4.

---

## File Structure

| File | Responsibility |
|---|---|
| `shared/th_market.py` | Thai market rules and Yahoo .BK fetching. No CLI. |
| `shared/data/set-holidays.json` | `[{date, name}]`, copied from `D:\Source\CrabTrade777\src\CrabTrade.Api\Data\Seed\set-holidays.json`. |
| `shared/data/tick-tables.json` | Effective-dated tick tables. |
| `skills/th-trader-scanner/scan.py` | CLI: `universe`, `daily`, `orb`. |
| `skills/th-trader-scanner/universe_file.py` | Reads the SET listed-companies file (HTML `.xls`, `.xlsx`, `.csv`). |
| `skills/th-trader-scanner/SKILL.md` | Skill instructions. |
| `tests/conftest.py` | Puts `shared/` and `skills/th-trader-scanner/` on `sys.path`. |
| `tests/fixtures/` | Saved Yahoo JSON and sample universe files. |
| `tests/test_th_market_*.py`, `tests/test_scan_*.py`, `tests/test_universe_file.py`, `tests/test_install.py` | Tests. |
| `scripts/Install-Skills.ps1` | Installs skills into `~/.claude/skills/`. |
| `README.md`, `LICENSE`, `.gitignore`, `.gitattributes` | Repo basics. |

How `scan.py` finds `th_market`: installed, `th_market.py` sits beside `scan.py`. In the repo, `scan.py` falls back to `<repo>/shared`:

```python
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import th_market as tm
except ImportError:
    sys.path.insert(0, os.path.join(HERE, "..", "..", "shared"))
    import th_market as tm
```

`th_market` finds its data in `os.path.join(os.path.dirname(__file__), "data")`.

---

### Task 1: Repo scaffold, calendar and sessions

**Files:**
- Create: `README.md`, `LICENSE`, `.gitignore`, `.gitattributes`, `tests/conftest.py`
- Create: `shared/data/set-holidays.json`, `shared/th_market.py`
- Test: `tests/test_th_market_calendar.py`

**Interfaces:**
- Produces:
  - `class MarketError(Exception)`
  - `now_bkk() -> datetime`
  - `to_bkk(utc: datetime) -> datetime` (naive or aware UTC in, naive Bangkok out)
  - `ts_to_bkk(ts: int) -> datetime`
  - `parse_now(text: str | None) -> datetime` (`"2026-10-30 10:20"` = Bangkok; a trailing `Z` = UTC; None = now)
  - `is_trading_day(d: date) -> bool`, raising `MarketError` when `d.year` is past the last year in the holiday file
  - `holiday_name(d: date) -> str | None`
  - `sessions(d: date) -> list[tuple[time, time]]`
  - `slots(d: date, interval: int = 5) -> list[datetime]`

- [ ] **Step 1: Scaffold the repo**
  - `pip`: `python -m pip install --user pytest`
  - `.gitignore`: `__pycache__/`, `.pytest_cache/`, `th-trader/`
  - `.gitattributes`: `* text=auto eol=lf`, plus `*.ps1 text eol=crlf`
  - `LICENSE`: MIT, copyright 2026 korraritk. Add a section "Portions derived from day-trader-skill, Copyright (c) 2026 Jake Schincariol, MIT License" with that license's full text, copied from `C:\Users\15193\.claude\skills\trader-scanner\LICENSE.day-trader-skill`.
  - `README.md`: what the repo is, the install command, the test command and the credit line.
  - `tests/conftest.py`: inserts `<repo>/shared` and `<repo>/skills/th-trader-scanner` at the front of `sys.path`.
  - Copy the holiday JSON byte for byte.

- [ ] **Step 2: Write the failing tests**

```python
from datetime import date, datetime, time
import pytest, th_market as tm

def test_bangkok_is_utc_plus_7():
    assert tm.ts_to_bkk(1793588400) == datetime(2026, 10, 30, 10, 0)

def test_parse_now_bangkok_and_utc():
    assert tm.parse_now("2026-10-30 10:20") == datetime(2026, 10, 30, 10, 20)
    assert tm.parse_now("2026-10-30T03:20:00Z") == datetime(2026, 10, 30, 10, 20)

def test_trading_day_weekend_and_holiday():
    assert tm.is_trading_day(date(2026, 10, 30))
    assert not tm.is_trading_day(date(2026, 10, 31))          # Saturday
    assert not tm.is_trading_day(date(2026, 1, 2))            # วันหยุดพิเศษ in the seed
    assert tm.holiday_name(date(2026, 1, 1)) == "วันขึ้นปีใหม่"

def test_past_last_holiday_year_stops():
    with pytest.raises(tm.MarketError, match="2028"):
        tm.is_trading_day(date(2028, 1, 4))

def test_sessions_and_slots():
    d = date(2026, 10, 30)
    assert tm.sessions(d) == [(time(10, 0), time(12, 30)), (time(14, 0), time(16, 30))]
    s = tm.slots(d, 5)
    assert len(s) == 60
    assert s[0] == datetime(2026, 10, 30, 10, 0)
    assert s[29] == datetime(2026, 10, 30, 12, 25)
    assert s[30] == datetime(2026, 10, 30, 14, 0)
    assert tm.sessions(date(2026, 10, 31)) == [] and tm.slots(date(2026, 10, 31)) == []
```

- [ ] **Step 3: Run:** `python -m pytest tests/test_th_market_calendar.py -v`. Expected: FAIL (`No module named 'th_market'`).

- [ ] **Step 4: Implement the Interfaces above in `shared/th_market.py`.** Load the holiday file once (module-level cache). The `MarketError` message for a year past the file names the year: `"SET holidays for 2028 are not in set-holidays.json yet: add them from SET's calendar."`

- [ ] **Step 5: Run:** `python -m pytest tests/test_th_market_calendar.py -v`. Expected: 5 passed.

- [ ] **Step 6: Commit:** `feat: โครง repo และปฏิทิน SET / รอบซื้อขายใน th_market`

---

### Task 2: Tick tables, rounding, price limits, board lot

**Files:**
- Create: `shared/data/tick-tables.json`
- Modify: `shared/th_market.py`
- Test: `tests/test_th_market_prices.py`

**Interfaces:**
- Consumes: `MarketError` (Task 1)
- Produces:
  - `TICK_FALLBACK_NOTE: str`, equal to the exact sentence in Global Constraints
  - `tick_table_label(d: date) -> str`, which returns `"2009"`, `"2009 (fallback)"`, or the new table's name once it is verified
  - `tick_fallback(d: date) -> bool`
  - `tick(price: Decimal, d: date) -> Decimal`
  - `round_down_tick(price, d) -> Decimal`, `round_up_tick(price, d) -> Decimal`, `round_tick(price, d) -> Decimal` (half up)
  - `price_limits(prev_close: Decimal, d: date) -> tuple[Decimal, Decimal]` returning `(floor, ceiling)`
  - `board_lot(lot: int | None = None) -> int`

`tick-tables.json` shape:

```json
[
  {"name": "2009", "valid_from": "2009-01-01", "valid_to": "2026-11-15", "status": "verified",
   "bands": [{"from": "0", "tick": "0.01"}, {"from": "2", "tick": "0.02"}, {"from": "5", "tick": "0.05"},
             {"from": "10", "tick": "0.10"}, {"from": "25", "tick": "0.25"}, {"from": "100", "tick": "0.50"},
             {"from": "200", "tick": "1.00"}, {"from": "400", "tick": "2.00"}]},
  {"name": "2026-11-16", "valid_from": "2026-11-16", "valid_to": null, "status": "missing", "bands": [],
   "note": "Smaller ticks for THB 5-50. Enter the bands from SET's official notice and set status to verified."}
]
```

A table applies when `valid_from <= d` and (`valid_to` is null or `d <= valid_to`). A `missing` table falls back to the latest `verified` table before it. A price's band is the last band whose `from` is ≤ the price, so a price of exactly 2.00 uses tick 0.02. Rounding down and rounding up use the tick of the price being rounded.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import date
from decimal import Decimal as D
import json, pytest, th_market as tm

OLD, NEW = date(2026, 11, 13), date(2026, 11, 16)

@pytest.mark.parametrize("price,tick", [("1.99", "0.01"), ("2.00", "0.02"), ("4.98", "0.02"), ("5.00", "0.05"),
    ("24.90", "0.10"), ("25.00", "0.25"), ("99.75", "0.25"), ("100.00", "0.50"), ("400", "2.00")])
def test_tick_bands(price, tick):
    assert tm.tick(D(price), OLD) == D(tick)

def test_rounding():
    assert tm.round_down_tick(D("24.97"), OLD) == D("24.90")
    assert tm.round_up_tick(D("24.91"), OLD) == D("25.00")
    assert tm.round_tick(D("42.13"), OLD) == D("42.25")
    assert tm.round_tick(D("42.12"), OLD) == D("42.00")

def test_price_limits_round_inward():
    assert tm.price_limits(D("42.00"), OLD) == (D("29.50"), D("54.50"))   # 29.4 -> up 29.50; 54.6 -> down 54.50

def test_missing_new_table_falls_back_and_says_so():
    assert tm.tick(D("12.00"), NEW) == D("0.10")
    assert tm.tick_fallback(NEW) and not tm.tick_fallback(OLD)
    assert tm.tick_table_label(NEW) == "2009 (fallback)" and tm.tick_table_label(OLD) == "2009"

def test_verified_new_table_is_used(tmp_path, monkeypatch):
    tables = json.load(open(tm.data_path("tick-tables.json"), encoding="utf-8"))
    tables[1].update(status="verified", bands=[{"from": "0", "tick": "0.01"}, {"from": "5", "tick": "0.01"}])
    p = tmp_path / "tick-tables.json"; p.write_text(json.dumps(tables), encoding="utf-8")
    monkeypatch.setattr(tm, "_TICK_TABLES", None); monkeypatch.setattr(tm, "_TICK_FILE", str(p))
    assert tm.tick(D("12.00"), NEW) == D("0.01") and not tm.tick_fallback(NEW)
    assert tm.tick_table_label(NEW) == "2026-11-16"

def test_board_lot():
    assert tm.board_lot() == 100 and tm.board_lot(50) == 50
    with pytest.raises(tm.MarketError):
        tm.board_lot(30)
```

(The verified-table test fixes two module names that the implementer must use: `_TICK_FILE`, the path the tables are loaded from, and `_TICK_TABLES`, the cache. It also fixes `data_path(name) -> str`.)

- [ ] **Step 2: Run:** `python -m pytest tests/test_th_market_prices.py -v`. Expected: FAIL (`AttributeError`).
- [ ] **Step 3: Implement the Interfaces, `data_path`, `_TICK_FILE` and `_TICK_TABLES` in `shared/th_market.py`, and write `tick-tables.json`.**
- [ ] **Step 4: Run:** `python -m pytest tests -v`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: ตารางช่วงราคาแบบมีวันเริ่มใช้, ceiling/floor และ board lot`

---

### Task 3: Yahoo .BK fetching and bar shaping

**Files:**
- Modify: `shared/th_market.py`
- Create: `tests/fixtures/record_fixtures.py`, `tests/fixtures/yahoo/*.json`
- Test: `tests/test_th_market_yahoo.py`

**Interfaces:**
- Consumes: `ts_to_bkk`, `slots`, `is_trading_day`, `MarketError` (Task 1)
- Produces:
  - `class FetchError(MarketError)`
  - `to_yahoo(symbol: str) -> str`
  - `yahoo_url_symbol(symbol: str) -> str` (percent-encoded)
  - `yahoo_get(path: str) -> str`: ported from the original, raising `FetchError`. It keeps query2 then query1, one retry after `BACKOFF` on a 429, and the module-level `SLEEP` that tests patch.
  - `parse_spark(doc, wanted: dict[str, str]) -> dict[str, Series]`
  - `parse_chart(doc, symbol) -> Series`
  - `fetch_bars(symbols: list[str], rng: str, interval: str, pause: float = 0.3) -> tuple[dict[str, Series], dict[str, str]]`, which fetches spark in batches of 20 and returns `(data, failed)`
  - `fetch_chart(symbol, rng, interval) -> Series`
  - `Series` is the original's dict: `{"t", "o", "h", "l", "c", "v", "meta"}`
  - `daily_bars(series, now: datetime) -> list[dict]`: each dict is `{"date": date, "o", "h", "l", "c", "v"}`, dated by the Bangkok date. A bar dated today is dropped before 16:40. Bars with a `null` close are dropped.
  - `intraday_bars(series, day: date, cutoff: datetime, interval: int = 5) -> list[dict]`: each dict is `{"start": datetime, "o", "h", "l", "c", "v"}`, keeping only starts in `slots(day, interval)` whose end is ≤ `cutoff`. Null bars (the lunch break) and off-grid live ticks are dropped. `v` None becomes 0.
  - `require_fetched(n_fetched: int, n_requested: int, failed: dict[str, str]) -> None`: raises `FetchError("Fetched 0 of N symbols from Yahoo: <first failure reason>")` when `n_fetched == 0 < n_requested`.

- [ ] **Step 1: Record fixtures.** `record_fixtures.py` saves raw Yahoo JSON with `urllib` (no project code) to `tests/fixtures/yahoo/`:
  - `chart_PTT_5m_5d.json` (v8 chart)
  - `spark_PTT_CPALL_1d_3mo.json`
  - `spark_PTT_CPALL_5m_1d.json`
  Run it once: `python tests/fixtures/record_fixtures.py`. Commit the JSON files. The tests never touch the network.

- [ ] **Step 2: Write the failing tests**

```python
def test_to_yahoo():
    assert tm.to_yahoo("ptt") == "PTT.BK" and tm.to_yahoo("PTT.BK") == "PTT.BK"
    assert tm.to_yahoo("BBL-R") == "BBL-R.BK"
    assert tm.yahoo_url_symbol("L&E") == "L%26E.BK"

def test_parse_spark_maps_back_and_reports_missing():   # Review Focus 1 and 2
    doc = load("spark_PTT_CPALL_1d_3mo.json")
    data = tm.parse_spark(doc, {"PTT.BK": "PTT", "CPALL.BK": "CPALL", "L&E.BK": "L&E"})
    assert set(data) == {"PTT", "CPALL"}

def test_fetch_bars_one_bad_symbol_others_fetched(monkeypatch):
    monkeypatch.setattr(tm, "yahoo_get", lambda path: json.dumps(load("spark_PTT_CPALL_1d_3mo.json")))
    data, failed = tm.fetch_bars(["PTT", "CPALL", "NOPE"], "3mo", "1d", pause=0)
    assert set(data) == {"PTT", "CPALL"} and failed == {"NOPE": "no data from Yahoo"}

def test_zero_fetched_is_a_failure():
    with pytest.raises(tm.FetchError, match="Fetched 0 of 3"):
        tm.require_fetched(0, 3, {"A": "HTTP 404"})

def test_daily_bars_dated_in_bangkok():
    bars = tm.daily_bars(series("spark_PTT_CPALL_1d_3mo.json", "PTT"), datetime(2026, 11, 2, 9, 0))
    assert bars[-1]["date"] == date(2026, 10, 30)          # was mislabelled 2026-10-29 by the US scanner

def test_intraday_drops_lunch_and_keeps_grid():
    s = tm.parse_chart(load("chart_PTT_5m_5d.json"), "PTT")
    day = date(2026, 10, 30)
    bars = tm.intraday_bars(s, day, datetime(2026, 10, 30, 16, 30))
    starts = [b["start"].time() for b in bars]
    assert time(12, 30) not in starts and time(13, 55) not in starts
    assert starts[0] == time(10, 0) and starts[-1] == time(16, 25)
    assert all(b["start"] in tm.slots(day) for b in bars)
    cut = tm.intraday_bars(s, day, datetime(2026, 10, 30, 13, 0))
    assert cut[-1]["start"].time() == time(12, 25)
```

`load(name)` and `series(name, sym)` are small helpers at the top of the test file. If the recorded days differ from 2026-10-30, change the dates in the tests to a full trading day that is in the fixture.

- [ ] **Step 3: Run:** `python -m pytest tests/test_th_market_yahoo.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement the Interfaces.** Port `http_get`, `yahoo_get`, `series_from_result`, `parse_spark`, `parse_chart` and `fetch_spark` (renamed `fetch_bars`) from the original. Then change three things:
  - `to_yahoo` no longer turns `.` into `-`; it appends `.BK`.
  - Every URL uses `yahoo_url_symbol`.
  - Time handling goes through `ts_to_bkk` instead of Eastern time.
- [ ] **Step 5: Run:** `python -m pytest tests -v`. Expected: all pass.
- [ ] **Step 6: Commit:** `feat: ดึงข้อมูล Yahoo .BK และจัดแท่งรายวัน/5 นาทีตามเวลาไทย`

---

### Task 4: Universe file reader

**Files:**
- Create: `skills/th-trader-scanner/universe_file.py`
- Create: `tests/fixtures/universe/listed.html.xls`, `listed.xlsx`, `listed_cp874.csv`, `listed_utf8bom.csv`, `listed_biff.xls`
- Test: `tests/test_universe_file.py`

**Interfaces:**
- Produces:
  - `class UniverseError(Exception)`
  - `read_listed_companies(path: str) -> list[tuple[str, str]]`: returns `(symbol, market)` with the symbol upper-cased and the market `"SET"` or `"mai"`, de-duplicated in file order
  - `is_common_share(symbol: str) -> bool`

Rules, from CrabTrade777's `ListedCompaniesParser`:
- The header row is the first row with cells starting (case-insensitive) with `symbol` and `company`. Columns are found by header name; `market` is optional and defaults to SET. A `market` cell of `mai`, in any case, means mai.
- Format by content, not by extension. Bytes starting with `PK` are `.xlsx`: read with `zipfile` + `xml.etree`, using `xl/sharedStrings.xml` and `xl/worksheets/sheet1.xml`. Content that has `<tr` in its first 4 KB is HTML: rows by regex `<tr[^>]*>(.*?)</tr>`, cells by `<t[dh][^>]*>(.*?)</t[dh]>`, tags stripped, `html.unescape`. Bytes starting with `D0 CF 11 E0` are a real BIFF `.xls`: raise `UniverseError("This is an old binary .xls. Open it in Excel and save it as .xlsx or .csv, then run universe again.")`. Anything else is CSV, decoded as `utf-8-sig` first and `cp874` on a `UnicodeDecodeError`.
- `is_common_share` returns False for:
  - warrants: `-W`, then optional digits, at the end
  - foreign board: `-F` at the end
  - derivative warrants: `^[A-Z0-9]+\d{2}[CP]\d{4}[A-Z]$`

- [ ] **Step 1: Build the fixtures.** Each has 3–4 rows, including `PTT` (SET), `AU` (mai), `L&E` (SET, `&amp;` in HTML), `PTT-W1` (dropped) and one Thai company name. Build `listed.xlsx` with a short script using `zipfile`. `listed_biff.xls` only needs the 8-byte BIFF signature followed by zeros.
- [ ] **Step 2: Write the failing tests.** One test per format asserts the result `[("PTT", "SET"), ("AU", "mai"), ("L&E", "SET")]`. Further tests:
  - `listed_biff.xls` raises `UniverseError` matching `"save it as .xlsx or .csv"`
  - `is_common_share` on `PTT`, `PTT-W1`, `TRUE-F`, `BANP19C2303A`
- [ ] **Step 3: Run:** `python -m pytest tests/test_universe_file.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement the Interfaces.**
- [ ] **Step 5: Run:** `python -m pytest tests -v`. Expected: all pass.
- [ ] **Step 6: Commit:** `feat: อ่านไฟล์รายชื่อบริษัทจดทะเบียน SET (HTML .xls, .xlsx, .csv)`

---

### Task 5: `scan.py` port: `universe` and `daily`

**Files:**
- Create: `skills/th-trader-scanner/scan.py`
- Test: `tests/test_scan_daily.py`

**Interfaces:**
- Consumes:
  - `th_market` (Tasks 1–3): `parse_now`, `daily_bars`, `fetch_bars`, `require_fetched`, `price_limits`, `tick_table_label`, `FetchError`, `MarketError`
  - `read_listed_companies`, `is_common_share` (Task 4)
- Produces:
  - `main(argv: list[str] | None = None) -> int`
  - `find_setups(closes: list[float]) -> tuple[list[str], dict]`, unchanged from the original
  - `scan_daily(data, symbols, now, min_price, min_value, min_volume) -> tuple[list[dict], dict, date | None]`
  - `SETUPS`, the original's list plus `("at_ceiling", "closed at the +30% ceiling")`
  - The watchlist JSON shape: the original's `daily` document plus `"currency": "THB"`, `"market": "SET"`, `"created_by": "th-trader-scanner"`, `"filters": {"min_price", "min_value", "min_volume"}`. Each hit has `symbol, setups, price, close_date, rsi, volume, value20`.

Port from the original `scan.py`:
- **Keep:** `find_setups`, `rsi`, `read_symbols`, `symbols_from_file`, `write_json`, `read_config`, the argparse layout and `--json`, `--show`, `--quiet`, `--offline`, `--now`, `--limit`, `--no-write`, `--pause`.
- **Delete:** every NYSE, Eastern, Nasdaq Trader and `classify` piece. They now come from `th_market` or `universe_file`.
- **Change these values:**
  - `universe` takes `--file PATH` (required unless `--offline` is given). It writes `th-trader/universe.txt` as `SYMBOL|SET` lines, only for `is_common_share` symbols.
  - `DEFAULT_UNIVERSE = "th-trader/universe.txt"`; `--out` defaults to `th-trader/watchlist.json`.
  - `daily`: `--min-price 1`, `--min-value 50000000` (the 20-day average of close × volume, in THB), `--min-volume 0`. Fetch `"3mo"` daily.
  - `at_ceiling`: the last close equals `price_limits(prev_close, last_date)[1]`, compared as `Decimal(str(round(close, 2)))`.
  - Call `require_fetched` right after fetching, so 0 of N exits 1 with its message.
  - The text header reads `Daily scan, <Bangkok time> Bangkok, closes through <date> (Yahoo, daily bars).`
  - Print `TICK_FALLBACK_NOTE` when `tick_fallback(last_date)` is true.

- [ ] **Step 1: Write the failing tests**
  - `find_setups` on constructed closes hits each of the five original setups (the same constructions the original's formulas define).
  - `at_ceiling` is tagged when the last close = 42.00 × 1.3 rounded down to tick (54.50).
  - With `value20` under 50,000,000, the stock is counted `illiquid` and gets no hit.
  - `main(["daily", "--offline", "<fixture spark file>", "--now", "2026-11-02 09:00", "--no-write", "--json"])` prints JSON with `currency == "THB"` and `as_of` naming the fixture's last trading day.
  - `main(["daily", "--symbols", "NOPE"])` with `fetch_bars` patched to return `({}, {"NOPE": "no data from Yahoo"})` returns 1 and prints to stderr a line containing `Fetched 0 of 1`.
  - `main(["universe", "--file", "<listed.html.xls>", "--out", tmp])` writes the lines `PTT|SET`, `AU|mai` and `L&E|SET`.
- [ ] **Step 2: Run:** `python -m pytest tests/test_scan_daily.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement the port.**
- [ ] **Step 4: Run:** `python -m pytest tests -v`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-scanner คำสั่ง universe และ daily`

---

### Task 6: `scan.py` port: `orb`

**Files:**
- Modify: `skills/th-trader-scanner/scan.py`
- Test: `tests/test_scan_orb.py`

**Interfaces:**
- Consumes:
  - `th_market` (Tasks 1–3): `intraday_bars`, `slots`, `sessions`, `is_trading_day`, `holiday_name`, `fetch_bars`, `fetch_chart`, `require_fetched`
- Produces:
  - `orb_window(now: datetime, at: time | None, or_minutes: int, until: time, explicit_day: date | None) -> tuple[date, datetime]` returning `(day, cutoff)`
  - `usable(bars, day, cutoff, n_range) -> bool`
  - `scan_orb(...)`, the original's signature
  - `stale_minutes(latest_end: datetime, now: datetime) -> int | None`

Behaviour (the spec plus the original):
- The range is the first `or_minutes` of the morning session, starting 10:00. Port `orb_candidate` and `orb_hit` unchanged.
- `usable` compares the bars with `[s for s in slots(day) if s + 5 min <= cutoff]`. Every one of those must be present, and there must be at least `n_range`. So the lunch break is never a gap, and a missing morning bar makes the stock unusable.
- `orb_window`:
  - On a closed day (weekend or holiday), raise `ScanError("No session on <date> (<holiday name or weekday>): the market is closed. Run orb on a trading day, or replay a past day with --offline.")`.
  - Before the range is complete, raise `ScanError("The <n>-minute opening range is not complete until <10:15>. Run orb again after <10:20>.")`.
  - A cutoff between 12:30 and 14:00 becomes 12:30.
  - A cutoff after 16:30 becomes 16:30.
  - Past `until`, say so, as the original does.
- `--until` defaults to config `no_entries_after` or `"15:30"`. `--config` defaults to `th-trader/config.json`. `--at` is in Bangkok time.
- Live fetch: `"1d"` for today, `"5d"` within 6 days, otherwise the original's message telling the user to replay with `--offline`.
- **Stale warning:** `stale_minutes` is now minus the end of the latest bar any symbol had. When it is over 30 and `now` is inside a session, print `Warning: Yahoo's latest bar ended <HH:MM>, <n> minutes ago; a breakout may already be stale.` and add `"stale_minutes": n` to the JSON.
- The text header says `Bangkok` and the range `10:00 to <10:15>`.
- `orb --min-price` defaults to 1; `--min-value` is not applied to orb.

- [ ] **Step 1: Write the failing tests.** Use the `chart_PTT_5m_5d.json` fixture through `--offline`.
  - `orb_window` on 2026-10-31 (Saturday) raises, and the message contains `market is closed`.
  - `orb_window` on 2026-01-02 raises, and the message contains `วันหยุดพิเศษ`.
  - `orb_window` at 10:10 raises, and the message contains `not complete until 10:15`.
  - `orb_window` at 13:00 gives cutoff 12:30; at 17:00 it gives 16:30.
  - `usable` with one morning bar removed returns False. The scan counts that symbol `unusable` and gives it no hit (Review Focus 3).
  - `usable` on a full 13:00 cutoff returns True (no lunch gap).
  - `main(["orb", "--offline", f, "--now", "<fixture day> 11:00", "--no-write", "--json"])` exits 0 or 1 without a traceback, and `as_of` ends in `Bangkok`.
  - `stale_minutes(datetime(.., 10, 30), datetime(.., 11, 15)) == 45` and the warning line appears in text mode.
- [ ] **Step 2: Run:** `python -m pytest tests/test_scan_orb.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement the port.**
- [ ] **Step 4: Run:** `python -m pytest tests -v`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-scanner คำสั่ง orb (opening range รอบเช้า ข้ามพักเที่ยง)`

---

### Task 7: SKILL.md, install script, live check

**Files:**
- Create: `skills/th-trader-scanner/SKILL.md`, `scripts/Install-Skills.ps1`
- Test: `tests/test_install.py`

**Interfaces:**
- Consumes: the finished skill (Tasks 1–6)
- Produces: `Install-Skills.ps1 [-Target <dir>]`, where `-Target` defaults to `$HOME\.claude\skills`

**`SKILL.md`:**
- Port the structure of the original `trader-scanner/SKILL.md`: frontmatter `name`, `description`, then Steps, Rules and "What it never does".
- `name: th-trader-scanner`.
- The description says it is for SET/mai stocks. Include the English triggers ("scan the Thai market", "Thai stock setups", "SET opening range breakouts") and the Thai triggers "สแกนหุ้นไทย", "หาหุ้นเบรก", "หุ้นไหนเบรก opening range".
- Commands use `python "${CLAUDE_SKILL_DIR}/scan.py"`.
- State these rules:
  - Data is Yahoo .BK, delayed 15–30 minutes.
  - The universe comes from the listed-companies file the user downloads from set.or.th in a browser. The skill never scrapes set.or.th.
  - Results are data, not a recommendation to buy or sell.
  - Report the tick-table fallback line whenever it is printed.

**`Install-Skills.ps1`:**
- For each `skills\<name>\`:
  - If `<Target>\<name>` exists without `.installed-from-thaitraderskills`, stop with an error naming the folder.
  - Otherwise remove the old copy and copy `skills\<name>\*`, `shared\th_market.py` and `shared\data\`.
  - Write the marker file containing the repo's `git rev-parse --short HEAD`.
- Never touch a folder whose name does not start with `th-trader-`.

- [ ] **Step 1: Write the failing test** `tests/test_install.py`. It skips when `powershell` is not on PATH, then runs `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/Install-Skills.ps1 -Target <tmp>` and checks:
  - `<tmp>/th-trader-scanner/` has `scan.py`, `th_market.py`, `data/tick-tables.json` and the marker.
  - `python <tmp>/th-trader-scanner/scan.py daily --offline <fixture> --now "..." --no-write --json` exits 0 or 1 with JSON on stdout. This proves the installed copy imports `th_market` from its own folder.
  - A pre-existing `<tmp>/th-trader-scanner/` without the marker makes the script exit non-zero and leaves that folder untouched.
- [ ] **Step 2: Run:** `python -m pytest tests/test_install.py -v`. Expected: FAIL.
- [ ] **Step 3: Write `SKILL.md` and `Install-Skills.ps1`.**
- [ ] **Step 4: Run:** `python -m pytest tests -v`. Expected: all pass.
- [ ] **Step 5: Live check (manual, network)** from a scratch folder:
  - `python <repo>/skills/th-trader-scanner/scan.py daily --symbols PTT,CPALL,ADVANC,KBANK,AOT --no-write`: it fetches 5 of 5, and the "closes through" date is the last SET trading day.
  - During a session, `scan.py orb --symbols ...`, or outside one, `orb --now "<last trading day> 11:00"`, which uses the 5d fetch: there is no traceback, the time is Bangkok, and the stale warning shows if Yahoo is behind.
  - Record the output in the commit message body.
- [ ] **Step 6: Commit:** `feat: SKILL.md และสคริปต์ติดตั้ง th-trader-scanner`
- [ ] **Step 7: Hand over.** Tell the user to run `powershell -ExecutionPolicy Bypass -File scripts\Install-Skills.ps1` themselves; installing into `~/.claude/skills` is their step.
