# Thai trade planning and simulation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `trader-exit`, `trader-timemachine`, `trader-budget` and `trader-risk` to SET/mai as `th-trader-exit`, `th-trader-timemachine`, `th-trader-budget` and `th-trader-risk`, with fees counted everywhere.

**Architecture:**
- **Shared code:** `shared/th_market.py` gains config and fee functions. `shared/setups.py` takes the scanner's setup math, so the Time Machine's swing history uses the same rules.
- **Skills:** each one is a single script ported from its original. US clock, calendar, cents and the Alpaca order shape are replaced by `th_market`.
- **Install:** `Install-Skills.ps1` copies every `shared/*.py` and `shared/data/` into each skill.

**Tech Stack:** Python 3.11 standard library (skills), pytest (development), PowerShell 5.1 (install).

**Spec:** `docs/superpowers/specs/2026-11-02-th-trader-planning-design.md` (part 1's spec, `2026-11-02-th-trader-core-scanner-design.md`, still holds).

**Port sources (read-only, never edit):**
- `C:\Users\15193\.claude\skills\trader-exit\exit.py`
- `C:\Users\15193\.claude\skills\trader-timemachine\timemachine.py`
- `C:\Users\15193\.claude\skills\trader-budget\size.py`
- `C:\Users\15193\.claude\skills\trader-risk\check.py`
- each of those folders' `SKILL.md`

**Porting rule** (from observation 0010):
- A function the plan marks "port unchanged" is copied from the source with a tool (`sed -n 'A,Bp'`), never retyped.
- Each such function gets one test pinning a known output computed by the original script.

## Global Constraints

- Standard library only in skills. `python`, never `python3`. `Decimal` for prices, fees and money. Naive Bangkok datetimes.
- Config defaults (all optional keys):
  - fees: `commission_pct` 0.15, `market_fees_pct` 0.007, `vat_pct` 7
  - money: `budget_thb` 100000, `max_loss_per_trade_thb` 500, `max_position_pct` 50, `cash_buffer_pct` 10, `max_daily_loss_thb` 1500
  - limits: `max_trades_per_day` 3, `min_reward_risk` 1.5, `blocked` []
  - times: `flat_by` "16:20", `no_entries_after` "15:30"
- One side's fee is value × (commission_pct + market_fees_pct) / 100 × (1 + vat_pct / 100).
- Risk per share = (entry − stop) + round-trip fee per share at the stop. Reward per share = (target − entry) − round-trip fee per share at the target. Gross R = (target − entry) / (entry − stop). Net R = reward per share / risk per share.
- Every skill in this part prints `fees_line(cfg)`.
- Stops round **down** to a valid tick. Targets round to the nearest valid tick, half up.
- Generated files:
  - `th-trader/plan.json` (exit), `th-trader/timemachine.json` (timemachine), `th-trader/orders.json` (budget)
  - `th-trader/state/risk.json` and `th-trader/state/trades.json` (risk)
- Output is data, never advice. No skill places an order. Script output is English.
- Commit messages are in Thai with a conventional prefix and end with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.

## Review Focus

1. **A plan for a stock priced exactly on a tick band edge:** e.g. entry 25.00, where ticks are 0.10 below and 0.25 above. The stop rounds down on the lower band's tick, and the target rounds on the higher band's tick. Test in Task 2.
2. **A replayed price walking past the day's ceiling or floor many bars in a row:** it stays clamped and never compounds beyond the limit. Test in Task 4.
3. **`check.py log` run twice for the same trade, or a sell logged with no prior buy:** the duplicate is refused with a message, and the sell without a position is refused (long only). Test in Task 7.
4. **A config file with a string where a number belongs** (e.g. `"budget_thb": "100k"`): one plain error naming the key, no traceback. Test in Task 1.
5. **A Time Machine run on a plan dated today whose entry bar is not in the data yet** (Yahoo delay): a plain refusal saying the entry bar is not in Yahoo's data yet, never a replay from a wrong bar. Test in Task 4.

---

## File Structure

| File | Responsibility |
|---|---|
| `shared/th_market.py` | + `DEFAULT_CONFIG`, `load_config`, `fee`, `round_trip_fee_per_share`, `fees_line`, `plan_numbers` |
| `shared/setups.py` | `rsi`, `find_setups` (moved from `scan.py`) |
| `skills/th-trader-scanner/scan.py` | imports `rsi` and `find_setups` from `setups` |
| `skills/th-trader-exit/exit.py`, `SKILL.md` | the plan |
| `skills/th-trader-timemachine/timemachine.py`, `SKILL.md` | replays and history |
| `skills/th-trader-budget/size.py`, `SKILL.md` | sizing and the ticket |
| `skills/th-trader-risk/check.py`, `SKILL.md` | PASS / BLOCK, the trade log |
| `scripts/Install-Skills.ps1` | copies `shared/*.py` instead of only `th_market.py` |
| `tests/conftest.py` | adds each new skill folder to `sys.path` |
| `tests/fixtures/yahoo/chart_PTT_5m_60d.json`, `chart_SINGER_5m_60d.json`, `chart_PTT_1d_5y.json` | recorded once |

Every new script finds `th_market` and `setups` the way `scan.py` does: its own folder first, then `<repo>/shared`.

---

### Task 1: Config, fees, shared setups

**Files:**
- Modify: `shared/th_market.py`, `skills/th-trader-scanner/scan.py`, `scripts/Install-Skills.ps1`, `tests/conftest.py`, `tests/test_install.py`
- Create: `shared/setups.py`
- Test: `tests/test_th_market_fees.py`

**Interfaces:**
- Produces (`th_market`):
  - `DEFAULT_CONFIG: dict` (the Global Constraints values; times as "HH:MM" strings)
  - `load_config(path: str = "th-trader/config.json") -> dict`
  - `fee(value_thb: Decimal, cfg: dict) -> Decimal`, unrounded
  - `round_trip_fee_per_share(entry: Decimal, exit_price: Decimal, cfg: dict) -> Decimal`
  - `fees_line(cfg: dict) -> str`
  - `plan_numbers(entry, stop, target, cfg) -> dict`, with keys `risk_per_share`, `reward_per_share`, `fee_at_stop`, `fee_at_target`, `gross_r`, `net_r` (all `Decimal`)
- Produces (`setups`): `rsi(closes, n=14) -> float | None`, `find_setups(closes) -> (list[str], dict)`, both moved verbatim from `scan.py`.

- [ ] **Step 1: Write the failing tests** in `tests/test_th_market_fees.py`:

```python
def test_one_side_fee():
    cfg = tm.load_config("nonexistent.json")
    assert tm.fee(D("12675"), cfg).quantize(D("0.01")) == D("21.29")   # 12,675 x 0.157% x 1.07

def test_round_trip_per_share():
    cfg = tm.load_config("nonexistent.json")
    assert tm.round_trip_fee_per_share(D("42.25"), D("41.75"), cfg).quantize(D("0.0001")) == D("0.1411")

def test_plan_numbers():
    n = tm.plan_numbers(D("42.25"), D("41.75"), D("43.25"), tm.load_config("nonexistent.json"))
    assert n["gross_r"] == D("2")
    assert n["risk_per_share"].quantize(D("0.0001")) == D("0.6411")
    assert n["net_r"].quantize(D("0.01")) == D("1.34")

def test_config_override_and_bad_type(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"commission_pct": 0.1}', encoding="utf-8")
    assert tm.load_config(str(p))["commission_pct"] == 0.1 and tm.load_config(str(p))["vat_pct"] == 7
    p.write_text('{"budget_thb": "100k"}', encoding="utf-8")
    with pytest.raises(tm.MarketError, match="budget_thb"):
        tm.load_config(str(p))

def test_fees_line_names_rates():
    assert "0.15%" in tm.fees_line(tm.load_config("nonexistent.json"))

def test_setups_module_is_the_scanner_math():
    import setups, scan
    assert scan.find_setups is setups.find_setups
```

Before writing the asserted values, recompute them with `python -c`:
- 42.25 → 0.164% × 1.07 = 0.07415 per side
- 41.75 → 0.07327
- total 0.1474 to 4 places
- 43.25 → 0.0759

**Ruling to make on run:** if the printed value differs from the test's literal, the computed value wins. Fix the literal and ledger it.

- [ ] **Step 2: Run:** `python -m pytest tests/test_th_market_fees.py -v`. Expected: FAIL.

- [ ] **Step 3: Implement.**
  - Move `rsi` and `find_setups` from `scan.py` into `shared/setups.py` with a tool (cut and paste, not retyped). `scan.py` does `from setups import find_setups, rsi`.
  - Change `Install-Skills.ps1` to copy `shared\*.py`. Extend `test_install.py`: the installed scanner has `setups.py`.
  - In `tests/conftest.py`, add `skills/th-trader-exit`, `-timemachine`, `-budget` and `-risk` to `sys.path`.
  - `load_config` checks:
    - numbers are `int` or `float` (not `bool`)
    - `flat_by` and `no_entries_after` match `HH:MM`
    - `blocked` is a list of strings
    - an unknown key is kept, untouched

- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.

- [ ] **Step 5: Commit:** `feat: config กลาง ค่าธรรมเนียม และย้ายสูตร setup ไป shared/setups.py`

---

### Task 2: `exit.py`, the pure plan computation

**Files:**
- Create: `skills/th-trader-exit/exit.py`
- Test: `tests/test_exit_compute.py`

**Interfaces:**
- Consumes: `round_down_tick`, `round_tick`, `price_limits`, `tick_table_label`, `plan_numbers`, `load_config`
- Produces:
  - `class PlanError(Exception)`
  - `parse_stop(text) -> (rule, param)` and `parse_target(text) -> (rule, param)`, ported from the original. Stop rules: `or-mid`, `or-low`, `price:X`, `pct:X`, `atr:K`. Target rules: `NR`, `price:X`.
  - `compute(entry, stop_rule, stop_param, target_rule, target_param, day, cfg, or_high=None, or_low=None, atr=None, prev_close=None) -> dict`, returning `entry, stop, target, stop_rule, target_rule`, the `plan_numbers` fields, `tick_table` and `warnings: list[str]`

Rules (spec Part 2):
- The stop is rounded down to a tick; a `NR` target is entry + N × (entry − stop) rounded half up to a tick.
- Raise `PlanError` for each of:
  - a stop at or above the entry, or a target at or below it
  - a stop below the floor, or a target above the ceiling (only when `prev_close` is given)
  - a round-trip fee at the stop of 50% or more of (entry − stop)
  - an `or-*` rule without `or_high`/`or_low`, or `atr` without an ATR value
- Warn from 25% fee share, and when net R < `min_reward_risk`.

- [ ] **Step 1: Write the failing tests:**

```python
DAY = date(2026, 11, 2); CFG = tm.load_config("nonexistent.json")

def test_orb_default_or_mid_2r():
    p = exit_.compute(D("42.25"), "or-mid", None, "R", D("2"), DAY, CFG, or_high=D("42.25"), or_low=D("41.50"))
    assert (p["stop"], p["target"]) == (D("41.75"), D("43.25"))     # mid 41.875 -> down 41.75; 42.25 + 2 x 0.50

def test_band_edge():                                              # Review Focus 1
    p = exit_.compute(D("25.00"), "pct", D("1"), "R", D("2"), DAY, CFG)
    assert p["stop"] == D("24.70")                                 # 24.75 is on the 0.10 grid? 24.75 -> down 24.70
    assert p["target"] == D("25.50")                               # 25 + 2 x 0.30 = 25.60 -> nearest 0.25 = 25.50

def test_refusals():
    with pytest.raises(exit_.PlanError, match="at or above"):
        exit_.compute(D("42.25"), "price", D("42.25"), "R", D("2"), DAY, CFG)
    with pytest.raises(exit_.PlanError, match="floor"):
        exit_.compute(D("42.25"), "price", D("29.00"), "R", D("1"), DAY, CFG, prev_close=D("42.00"))
    with pytest.raises(exit_.PlanError, match="fee"):
        exit_.compute(D("1.50"), "price", D("1.49"), "R", D("2"), DAY, CFG)   # round-trip fee 0.0050 = 50.2% of the 0.01 risk

def test_fee_warning_between_25_and_50():
    p = exit_.compute(D("42.25"), "price", D("41.75"), "R", D("2"), DAY, CFG)   # fees 28% of the 0.50 risk
    assert any("fee" in w for w in p["warnings"])
```

Check every expected value with `python -c` against `th_market` before running, especially the band-edge case:
- 25.00 × 0.99 = 24.75, which is on the 0.10 grid as 24.70 or 24.80, so it rounds down to 24.70
- risk 0.30, 2R target 25.60, nearest tick at 0.25 = 25.50

Fix any literal the computation contradicts, and ledger it.

- [ ] **Step 2: Run:** `python -m pytest tests/test_exit_compute.py -v`. Expected: FAIL (`No module named 'exit'`). Import the module as `import importlib; exit_ = importlib.import_module("exit")`, because `exit` shadows a builtin name.
- [ ] **Step 3: Implement `compute`.** Port `parse_stop`, `parse_target` and `r_label` from the original with a tool; write `compute` on `th_market`.
- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-exit คำนวณ stop/เป้า ตามช่วงราคา SET พร้อมค่าธรรมเนียม`

---

### Task 3: `exit.py` CLI, `plan.json`, `SKILL.md`

**Files:**
- Modify: `skills/th-trader-exit/exit.py`
- Create: `skills/th-trader-exit/SKILL.md`, `tests/fixtures/watchlist_orb.json`, `tests/fixtures/watchlist_daily.json`
- Test: `tests/test_exit_cli.py`

**Interfaces:**
- Consumes: `compute` (Task 2); `fetch_chart`, `intraday_bars`, `daily_bars`, `parse_now`, `load_config`, `fees_line`
- Produces:
  - `main(argv) -> int`
  - `atr_of(bars, n=14) -> Decimal | None`, ported unchanged
  - the `plan.json` fields listed in spec Part 2, including `mode`, `date`, `entry_time`, `flat_by` / `days`, `setups`, `currency`, `source` and `created_at`

Behaviour:
- `plan [--symbol S] [--entry X --or-high H --or-low L] [--mode intraday|swing] [--days N] [--stop RULE] [--target RULE] [--watchlist PATH] [--entry-file PATH] [--offline FILE] [--now ..] [--config ..] [--write] [--json]`
- Input order: watchlist hit for `--symbol` → `th-trader/entry.json` (`triggered` must be true unless `--entry`) → flags.
- Mode: an orb hit or flags → intraday; a daily hit → swing.
- Default stop: `or-mid` for intraday, `pct:5` for swing. `--days` defaults to 10.
- `prev_close`: `meta.chartPreviousClose` of the 5-minute chart (intraday) or the daily close before the plan date (swing). It is fetched only when `--stop atr:` is used or `--check-limits` is given (default on when online; `--offline FILE` supplies the chart).
- `show` prints `th-trader/plan.json`.
- The text output gives:
  - the plan line from the spec: entry, stop (rule), target (R), fee share with a ⚠ when ≥ 25%, net R
  - the fees line
  - the tick-table fallback line when it applies
  - the original's NOTE ("A stop is a plan, not a guarantee: a gap or a fast market can fill past it.")

- [ ] **Step 1: Write the failing tests:**
  - `plan --symbol ADVANC --watchlist watchlist_orb.json --no-fetch --json` gives intraday, `stop_rule` "or-mid", the right numbers, and `flat_by` "16:20".
  - `plan --symbol PTT --watchlist watchlist_daily.json --no-fetch --json` gives swing, `days` 10, and `stop_rule` "pct:5".
  - `plan --symbol X --entry 42.25 --or-high 42.25 --or-low 41.5 --write` writes `th-trader/plan.json` under `tmp_path` (`monkeypatch.chdir`).
  - Each `PlanError` exits 1 with `exit.py: <reason>` and no traceback.
  - `--no-fetch` (also a real flag) skips Yahoo, so no `prev_close` and no limit check, and says so in one line.
  - The watchlist fixtures are small hand-written JSON in the scanner's shape (ADVANC orb hit 339.00 / or 336.00–338.00 / at 10:20; PTT daily hit `reclaim_ma20` at 42.00).
- [ ] **Step 2: Run:** `python -m pytest tests/test_exit_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement the CLI.** Port `atr_of`, `describe` and `cmd_show` with a tool where they carry over; write `gather` and `build_plan` on the Thai inputs. Then write `SKILL.md`:
  - the original's structure, `name: th-trader-exit`
  - Thai triggers ("วาง stop", "ตั้งจุดตัดขาดทุน", "เป้ากี่ R")
  - `python "${CLAUDE_SKILL_DIR}/exit.py"`
  - the never-places-orders section
- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-exit คำสั่ง plan/show และ SKILL.md`

---

### Task 4: Time Machine, intraday mode

**Files:**
- Create: `skills/th-trader-timemachine/timemachine.py`, `tests/fixtures/yahoo/chart_PTT_5m_60d.json`, `tests/fixtures/yahoo/chart_SINGER_5m_60d.json`
- Modify: `tests/fixtures/record_fixtures.py` (add the two 60d charts and the 5y daily chart)
- Test: `tests/test_timemachine_intraday.py`

**Interfaces:**
- Consumes: `intraday_bars`, `slots`, `price_limits`, `plan_numbers`, `parse_chart`, `ts_to_bkk`
- Produces:
  - `class TMError(Exception)`
  - `usable_sessions(series, before: date, min_share=0.75) -> dict[date, list[dict]]`: 60 bars per day, flat-filled
  - `build_pool(sessions) -> list[list[tuple]]`: 60 slots of (o, h, l, c) ratios
  - `simulate(pool, entry, stop, target, start_slot, end_slot, floor, ceiling, replays=10000, seed=19, window=2) -> dict` with `target`, `stop`, `neither`, `green` (shares), `end_prices` and `r_values` (gross, per replay)
  - `history_intraday(sessions, or_minutes, stop_rule, stop_param, target_r, end_slot) -> list[dict]`
  - `verdict(share_target, share_stop, net_r) -> str` and `verdict_notes(...) -> list[str]`, ported with net R in place of R

Rules (spec Part 3, intraday):
- **Flat fill and usable days:** a slot without a bar is (c_prev, c_prev, c_prev, c_prev). A day is usable when it has ≥ 45 real bars and a real 10:00 bar.
- **Refusals:**
  - fewer than 20 usable days: `TMError` with the spec's exact message shape, ending in "try --mode swing"
  - an entry bar not yet in the data on the plan's date: `TMError("The entry bar at HH:MM on <date> is not in Yahoo's data yet ...")` (Review Focus 5)
- **Slot picking:** slot j stays inside the same session's slots (0–29 or 30–59).
- **Clamp:** clamp o, h, l, c of each simulated bar to [floor, ceiling] for the plan day. Every bar is clamped, so the price cannot compound past a limit (Review Focus 2).
- **Exits:**
  - an open at or below the stop exits at the open
  - otherwise the low touching the stop exits at the stop
  - otherwise an open at or above the target exits at the target
  - otherwise the high touching the target exits at the target
- **Net R per replay:** (exit − entry − round-trip fee at exit) / risk_per_share from `plan_numbers`.

- [ ] **Step 1: Record fixtures.** Add the PTT and SINGER `range=60d&interval=5m` charts and the PTT `range=5y&interval=1d` chart to `record_fixtures.py`, then run it. Then print, with a one-off `python -c`, each fixture's usable-day count under the 75% rule. The tests below assert those printed numbers, not guesses.
- [ ] **Step 2: Write the failing tests:**
  - `usable_sessions` on PTT gives the printed count (≥ 20), and every day has 60 bars.
  - On SINGER: a plan raises `TMError` matching `"try --mode swing"` when the printed count is under 20. If it is 20 or more, assert the count instead and ledger it.
  - `build_pool`: slot 30's close ratio for a known day equals that day's 14:00 close / its 12:25 close, computed in the test from the fixture's bars.
  - `simulate` with the same seed twice gives identical results.
  - A pool whose every bar opens 5% below the previous close exits at the open, not at the stop: the replay's exit equals entry × 0.95 when the stop is at entry × 0.99.
  - A pool of +10% bars, run for 60 slots, never exceeds the ceiling.
  - The entry bar missing on the plan date raises the Review Focus 5 message.
  - `verdict(0.32, 0.68, D("2"))` returns "Close to break-even", because the break-even at 2R is 33.3%.
- [ ] **Step 3: Run:** `python -m pytest tests/test_timemachine_intraday.py -v`. Expected: FAIL.
- [ ] **Step 4: Implement.** Port `pct`, `simulate`'s loop shape, `lean`, `verdict` and `verdict_notes` from the original with a tool, then change the slot range, the clamp and the gap exit.
- [ ] **Step 5: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 6: Commit:** `feat: th-trader-timemachine จำลองแท่ง 5 นาที (60 ช่อง, เติมแท่งไม่มีซื้อขาย, ceiling/floor)`

---

### Task 5: Time Machine, swing mode, CLI, chart, `SKILL.md`

**Files:**
- Modify: `skills/th-trader-timemachine/timemachine.py`
- Create: `skills/th-trader-timemachine/SKILL.md`
- Test: `tests/test_timemachine_swing.py`, `tests/test_timemachine_cli.py`

**Interfaces:**
- Consumes: Task 4; `daily_bars`, `setups.find_setups`
- Produces:
  - `daily_pool(bars) -> list[tuple]`
  - `simulate_swing(pool, entry, stop, target, days, replays=10000, seed=19) -> dict`, the same keys as `simulate`
  - `history_swing(bars, setup_tags, stop_rule, stop_param, target_r, days) -> list[dict]`
  - `main(argv)` with `run [--plan PATH] [--replays N] [--seed N] [--until HH:MM] [--offline FILE] [--now ..] [--write] [--json]` and `chart [--from PATH]`

Rules:
- **Swing replay:** each day is a random past day scaled to the previous simulated close, clamped to ±30% of it. The gap and check rules are the same as intraday. Neither by day N means the end price is day N's close.
- **Swing history:** for each past index i ≥ 30, `find_setups(closes[:i+1])`. When the plan's tags intersect the result, enter at closes[i] and walk days i+1…i+N with the plan's stop rule (`pct` on that entry) and target R.
- **Output:** the original's report layout. It says the mode, the number of usable sessions or days, the seed, the fees line and the verdict lines; "R" is labelled "net R".
- **`chart`:** port `render_chart` unchanged.

- [ ] **Step 1: Write the failing tests:**
  - `daily_pool` on the 5y PTT fixture has len(bars) − 1 entries.
  - `simulate_swing` with the same seed repeats.
  - A constructed series where `reclaim_ma20` fires 3 times gives 3 history rows.
  - CLI `run --plan <intraday plan fixture> --offline chart_PTT_5m_60d.json --json` exits 0, and the odds sum to 1.
  - The same with a swing plan and `chart_PTT_1d_5y.json`.
  - `chart --from` a saved run prints a block of lines with no traceback.
- [ ] **Step 2: Run:** `python -m pytest tests/test_timemachine_swing.py tests/test_timemachine_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement**, then write `SKILL.md`:
  - the original's structure, including the "How it works" section rewritten for the 60 Thai slots and the swing mode
  - Thai triggers ("จำลองไม้นี้", "โอกาสถึงเป้า", "ย้อนดูไม้นี้")
  - "statistics, not a prediction"
- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-timemachine โหมด swing, คำสั่ง run/chart และ SKILL.md`

---

### Task 6: `th-trader-budget`

**Files:**
- Create: `skills/th-trader-budget/size.py`, `skills/th-trader-budget/SKILL.md`
- Test: `tests/test_budget.py`

**Interfaces:**
- Consumes: `plan_numbers`, `fee`, `board_lot`, `load_config`, `fees_line`, `round_tick`
- Produces:
  - `class SizeError(Exception)`
  - `size_trade(plan: dict, cfg: dict, price: Decimal | None = None, lot: int | None = None) -> dict`, with keys `qty`, `decided_by` ("max loss" | "position cap" | "cash"), `limits` {name: shares}, `entry_used`, `max_loss_thb`, `est_fees_round_trip`, `net_r`
  - `ticket(sized: dict, plan: dict) -> dict`, the spec's order shape
  - `main(argv)` with `[--plan PATH] [--price X] [--lot 50] [--config ..] [--write] [--json]`

Rules (spec Part 4): each limit's shares are floored to the lot. Zero lots → `SizeError` naming the THB loss of one lot. Net R at the sizing price below `min_reward_risk` → `SizeError`.

- [ ] **Step 1: Write the failing tests**, one case each:
  - max loss decides: entry 42.25, stop 41.75, defaults → 700 shares (500 / 0.6411 = 779 → 700)
  - position cap decides: a tight stop on a high-priced stock (cap 50,000 / 339 = 147 → 100)
  - cash decides: `cash_buffer_pct` 60 with a tiny stop
  - zero lots refuses, and the message has the one-lot loss in THB
  - `--lot 50`
  - `--price` above the entry sizes at it and recomputes net R
  - the ticket JSON keys equal the spec's list

  Recompute each expected quantity with `python -c` before writing it.
- [ ] **Step 2: Run:** `python -m pytest tests/test_budget.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Port the report layout of `plan_report` with a tool and drop the weights code. Write `SKILL.md`:
  - Thai triggers ("ซื้อกี่หุ้น", "คำนวณขนาดไม้", "มีเงิน 100,000")
  - set the rules once in `th-trader/config.json`
  - "it never places anything; type the ticket into your broker's app"
- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-budget คำนวณจำนวนหุ้นเป็น lot และใบสั่ง`

---

### Task 7: `th-trader-risk`

**Files:**
- Create: `skills/th-trader-risk/check.py`, `skills/th-trader-risk/SKILL.md`
- Test: `tests/test_risk.py`

**Interfaces:**
- Consumes: `load_config`, `plan_numbers`, `tick`, `round_tick`, `price_limits`, `sessions`, `board_lot`, `parse_now`
- Produces:
  - `class RiskError(Exception)`
  - `check(orders: list[dict], cfg, trades_today: list[dict], now: datetime, prev_closes: dict[str, Decimal] | None = None) -> list[dict]`: one `{symbol, verdict: "PASS"|"BLOCK", reasons: [..], warnings: [..]}` per order
  - `log_trade(path, side, symbol, qty, price, stop=None, now=None) -> dict`
  - `today_summary(trades_today, cfg) -> dict` with `realized_thb`, `open: [..]`, `open_risk_thb`
  - `main(argv)` with `[check] [--orders PATH] [--now ..] [--no-write] [--json]`, `log buy|sell SYMBOL QTY PRICE [--stop X]` and `today`

Rules: the spec Part 4 blocking table, row by row, plus:
- a sell is never blocked by those rows
- a sell beyond the held quantity is blocked
- `log` refuses a sell with no open position (long only)
- `log` refuses an identical record (same side, symbol, qty, price) within 60 seconds, as a duplicate (Review Focus 3)

`prev_closes` feeds the price-limit row. Without them, that row is skipped with a warning.

- [ ] **Step 1: Write the failing tests:**
  - one test per blocking row (12)
  - a sell passes while every entry rule would block it
  - a sell beyond the position is blocked
  - the daily-loss sum counts a logged realized loss + an open position's risk + the new order
  - the `log` duplicate and the sell-without-position refusals
  - `--now` at 13:00 blocks with "lunch break"
- [ ] **Step 2: Run:** `python -m pytest tests/test_risk.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Port `report`'s layout and `window_check`'s shape with a tool, and replace the Alpaca format checks with the Thai ticket fields. Write `SKILL.md`:
  - Thai triggers ("เช็กความเสี่ยง", "ไม้นี้ผ่านกฎไหม", "บันทึกว่าซื้อแล้ว")
  - the log commands
  - "a PASS is a rules check, not a recommendation"
- [ ] **Step 4: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 5: Commit:** `feat: th-trader-risk ตรวจ PASS/BLOCK และบันทึกไม้ที่เข้าจริง`

---

### Task 8: README, install check, live end-to-end

**Files:**
- Modify: `README.md`, `tests/test_install.py`

- [ ] **Step 1: Extend `test_install.py`.** Every installed `th-trader-*` folder has `th_market.py`, `setups.py`, `data/` and the marker. `exit.py plan --entry 42.25 --or-high 42.25 --or-low 41.5 --symbol X --no-fetch --json` runs from the installed copy.
- [ ] **Step 2: Run:** `python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 3: Update `README.md`.** List the five skills, and the config file with its keys and the UNVERIFIED fee note.
- [ ] **Step 4: Live end-to-end** in a scratch folder, Bangkok time, against Yahoo. Replay the last full trading day:
  - `scan.py orb --symbols PTT,CPALL,ADVANC,KBANK,AOT,DELTA,SCB,GULF --now "<day> 11:00"`
  - `exit.py plan --symbol <a hit> --write`
  - `timemachine.py run`
  - `size.py --write`
  - `check.py --now "<day> 11:00"`

  Then one swing plan: `scan.py daily` → `exit.py plan --symbol <daily hit> --write` → `timemachine.py run`. Record the outputs in the commit body.
- [ ] **Step 5: Commit:** `docs: README ชุดวางแผนไม้ และผลทดสอบจริง`
