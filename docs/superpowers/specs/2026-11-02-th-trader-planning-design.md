# Thai trader skills, part 2: trade planning and simulation

Part 1 (`2026-11-02-th-trader-core-scanner-design.md`) built `th_market.py` and `th-trader-scanner`. This part ports four more `trader-*` skills (day-trader-skill, Jake Schincariol, MIT) to SET/mai:

- `th-trader-exit`: the stop and the target, before entering.
- `th-trader-timemachine`: replays of the plan, intraday (5-minute bars) and swing (daily bars).
- `th-trader-budget`: how many shares fit the budget and the max loss.
- `th-trader-risk`: PASS or BLOCK against the user's rules.

The rules of part 1 hold here too:

- Output is data and statistics, never a recommendation to buy or sell.
- Nothing places an order: the user types orders into their broker's app.
- Python standard library only, `python` not `python3`, and `Decimal` for prices and money.
- Script output is in English; Claude reports in Thai.
- Generated files go to `th-trader/`.

## Decisions made with the user (2026-11-02)

- The Time Machine has both modes: intraday (5-minute bars) and swing (daily bars, held over several days).
- Fees are set in `th-trader/config.json` and counted everywhere: risk per share, R, max loss, and the break-even in the Time Machine verdict.
- Approach A again: port each original script, with Thai rules taken from `th_market.py`.
- The budget writes a broker-neutral order ticket, not Alpaca's order shape. No broker API is used.
- Trades the user actually takes are logged by the user (`check.py log`). The risk check counts them for the daily limits.
- The original budget skill's weights mode is dropped: it is not part of day trading.

## Facts checked on 2026-11-02

- Yahoo v8 `range=5y&interval=1d` for PTT.BK: 1,210 daily bars from 2021-11-02 to 2026-10-30, every bar with a volume.
- Yahoo v8 `range=60d&interval=5m`: 59 sessions. Sessions with all 60 bars: PTT 59, SINGER 9, AU 0, TKS 0. Yahoo leaves out a 5-minute bar when nothing traded in it, so thinly traded stocks rarely have a complete session.
- Fee rates, checked on 2026-11-02 at the user's request to use current Thai market standards:
  - **Market fees: VERIFIED.** SET trading fee 0.005%, TCH clearing fee 0.001% (capped at THB 350 per order, which matters only above about THB 35 million an order, so it is not modelled), and regulatory (SEC) fee 0.001%, with VAT on the fees. Sources: [Kasikorn Securities fee page](https://www.kasikornsecurities.com/en/startinvesting/fee/thai-stocks), [SET TCH clearing services](https://www.set.or.th/en/tch/services/equities-bond/services).
  - **VAT 7%.** The cabinet extended the reduced rate to 30 September 2027 (Royal Decree No. 807). Re-check by then. Source: [Nation Thailand](https://nationthailand.com/news/policy/40069105).
  - **Commission.** Negotiated per broker; there is no market standard. 0.15% is a common online cash-balance rate for up to THB 5 million a day ([DAOL Securities](https://www.daolsecurities.co.th/product-and-services/onshore-securities-trading/onshores-onshores/onshores-commission); Kasikorn's online rates run 0.10–0.20%). The 0.157% first proposed is one broker's rate in a high-value tier, so it is not used.

## Part 1: shared config and fees (additions to `th_market.py`)

`th-trader/config.json` is one file shared by every skill, as in the original. Every key is optional.

| Key | Default | Used by |
|---|---|---|
| `commission_pct` | 0.15 (per side, before VAT: a common online cash-balance rate for up to THB 5 million a day) | all |
| `market_fees_pct` | 0.007 (trading 0.005 + clearing 0.001 + SEC 0.001) | all |
| `vat_pct` | 7 | all |
| `budget_thb` | 100000 | budget, risk |
| `max_loss_per_trade_thb` | 500 | budget, risk |
| `max_position_pct` | 50 | budget, risk |
| `cash_buffer_pct` | 10 | budget, risk |
| `max_daily_loss_thb` | 1500 | risk |
| `max_trades_per_day` | 3 | risk |
| `min_reward_risk` | 1.5 | exit (warns), budget, risk |
| `flat_by` | "16:20" | exit, timemachine, risk |
| `no_entries_after` | "15:30" | scanner, risk |
| `blocked` | [] | risk |

New functions:

- `load_config(path="th-trader/config.json") -> dict`: the file's keys over the defaults. A key with the wrong type stops with a plain message naming it.
- `fee(value_thb, cfg) -> Decimal`: one side's fee: value × (commission_pct + market_fees_pct) / 100 × (1 + vat_pct / 100).
- `round_trip_fee_per_share(entry, exit_price, cfg) -> Decimal`: `fee(entry) + fee(exit_price)` for one share.
- `fees_line(cfg) -> str`: the rates in one line, e.g. "Fees: commission 0.15% + market 0.007% + VAT 7% per side (set commission_pct to your broker's rate)". Every skill in this part prints it.

How fees are counted:

- Risk per share = (entry − stop) + round-trip fee per share at the stop.
- Reward per share = (target − entry) − round-trip fee per share at the target.
- Gross R = (target − entry) / (entry − stop).
- Net R = reward per share / risk per share.

## Part 2: `th-trader-exit`

`exit.py plan` writes `th-trader/plan.json`. `exit.py show` prints the current plan.

### Input, first one found

1. `--symbol PTT`: that symbol's `orb` hit in `th-trader/watchlist.json` (`price` = entry, `at`, `or_high`, `or_low`), or its `daily` hit (`price`, `setups`).
2. `th-trader/entry.json`, written by part 3's `th-trader-entry`. Part 2 reads it if it exists.
3. Numbers given by hand: `--symbol PTT --entry 42.25 --or-high 42.25 --or-low 41.50`.

### Modes

- `intraday` (the default for an orb hit or hand-entered numbers): out by `flat_by` (16:20) the same day.
- `swing` (the default for a daily hit, or `--mode swing`): held up to `--days N` trading days (default 10). The entry is the hit's close.

### Stops

The stop is rounded **down** to a valid SET price for the plan's date:

- `or-mid`: the opening-range midpoint. Default for intraday.
- `or-low`: the opening-range low.
- `price:X`: the user's price.
- `pct:X`: X percent below the entry. Default for swing, `pct:5`.
- `atr:K`: K × the 14-bar ATR below the entry. It uses the session's 5-minute bars up to the entry bar in intraday mode, and daily bars in swing mode.

### Targets

The target is rounded to the nearest valid price, half up:

- `2R` (the default), `1R`, `1.5R` and so on, on gross R: entry + R × (entry − stop).
- `price:X`: the user's price.

### Refusals, each with its reason

- A stop at or above the entry, or a target at or below it.
- A stop below the day's floor, or a target above the day's ceiling. Both come from `price_limits` on the previous close: Yahoo's `chartPreviousClose` or the last daily close. The level is not moved for the user.
- A round-trip fee of 50% of the stop distance or more. From 25% it warns and still plans.
- A short. Long only, as the original v2.
- An `entry.json` that has not triggered, unless `--entry` is given.

Net R below `min_reward_risk` is a warning here. `th-trader-risk` enforces it.

### `plan.json`

Fields:

- `symbol`, `mode`, `date`, `entry_time` (intraday), `entry`, `stop`, `stop_rule`, `target`, `target_rule`
- `risk_per_share`, `reward_per_share`, `fee_round_trip_per_share`, `gross_r`, `net_r`
- `flat_by` (intraday) or `days` (swing)
- `or_high`, `or_low` (orb plans) and `setups` (daily plans)
- `tick_table` (with "(fallback)" when it applies), `currency` "THB", `source`, `created_at`

## Part 3: `th-trader-timemachine`

`timemachine.py run` reads `plan.json` and picks the mode from it. `timemachine.py chart` draws the fan chart. `--write` saves `th-trader/timemachine.json`. 10,000 replays, seed 19, the original's `random.Random(seed)` so results repeat.

### Intraday mode

- **Data.** 60 days of 5-minute bars (v8 chart, `range=60d&interval=5m`), sessions before the plan's date.
- **Sessions.** A slot with no bar is a flat bar at the previous close: nothing traded, so the price did not move. A session is used when at least 75% of its 60 slots (45) have a real bar and its 10:00 bar is real.
- **Too few sessions.** With fewer than 20 usable sessions it refuses: "PTT has 9 usable 5-minute sessions in the last 60 days (needs 20): too thinly traded for an intraday replay; try --mode swing."
- **Pool.** For each of the 60 slots, each past session's bar as ratios to the previous bar's close: (open, high, low, close) / previous close. Slot 30 (14:00) is relative to the 12:25 bar, so the lunch break's move is in the pool.
- **One replay.** It starts at the entry price after the entry bar and walks the slots to `flat_by`. For each slot i it picks slot j = i + randint(−2, 2), kept inside the same session's slots, then a random past bar from `pool[j]`, scaled to the current price. Every simulated price is clamped to the day's floor and ceiling.
- **Order of checks in each bar.** A bar that opens through the stop exits at its open, which can be worse than −1R. Otherwise the stop is checked before the target, so a bar that touches both counts as the stop. A bar that opens through the target exits at the target.
- **History.** The plan's own rule (the same opening-range minutes, stop rule and target R) re-run on every usable past session. The entry is the first close above the range.

### Swing mode

- **Data.** 5 years of daily bars (v8 chart, `range=5y&interval=1d`), before the plan's date.
- **Pool.** Every past day as ratios to the previous close.
- **One replay.** It walks `days` days from the entry close. Each day is a random past day (independent draws), scaled to the price and clamped to ±30% of the previous simulated close. Gaps through the stop and the check order are the same as intraday.
- **Neither.** A replay that reaches neither the stop nor the target by the last day ends at that day's close.
- **History.** When the plan came from a `daily` hit with setup tags, every past day on which the stock matched the same tag (`find_setups` on the closes up to that day, the scanner's own function) is re-run with the plan's stop rule and target R, entering at that day's close. Hand-entered plans skip history, and say so.

### Outcomes and verdict

These are the original's, with net R:

- **Odds:** target first, stop first, neither.
- **Green:** target first, or above the entry at the end.
- **R:** net R at the target; at the stop, (stop or gap-open price − entry) / risk, net of fees; otherwise (end price − entry − fees) / risk.
- **End price:** the median and the 5th–95th percentile of the end prices, and the average net R.
- **Verdict.** Among the replays that hit a side, target / (target + stop) is compared with the break-even 1 / (1 + net R). Less than 5 points apart: "Close to break-even". 5–15 points: "Leans toward the target / the stop". 15 or more: "Strongly toward". Below the verdict: a line when more than 40% hit neither, and the average net R when its sign goes against the lean.
- **Seed.** The seed is printed with every result.

## Part 4: `th-trader-budget` and `th-trader-risk`

### `th-trader-budget` (`size.py`)

From `plan.json` and the config, the shares are the largest multiple of the board lot (100, or `--lot 50`) that fits all of:

| Limit | Shares |
|---|---|
| max loss | `max_loss_per_trade_thb` / risk per share (fees included) |
| position cap | `budget_thb` × `max_position_pct` / 100 / entry |
| cash | (`budget_thb` × (1 − `cash_buffer_pct` / 100)) / (entry + buy-side fee per share) |

- It says which limit decided the size.
- Zero lots is a plain refusal ("the stop is too far for THB 500 max loss: one lot would lose THB 812"), not a smaller order.
- `--price X` sizes at X when X is above the plan's entry, because the price ran up since the plan.
- Net R below `min_reward_risk` at the sizing price is refused.
- `--write` writes `th-trader/orders.json`:

```json
{"created_by": "th-trader-budget", "created_at": "...+07:00", "currency": "THB",
 "orders": [{"symbol": "PTT", "side": "buy", "qty": 300, "type": "limit", "limit_price": "42.25",
             "stop": "41.75", "target": "43.25", "flat_by": "16:20", "est_fees_round_trip": "...",
             "max_loss_thb": "...", "net_r": "...", "decided_by": "max loss", "plan_date": "2026-11-02"}]}
```

- It prints the ticket for the user to type into their broker's app. It never places anything.

### `th-trader-risk` (`check.py`)

- `check.py` checks every order in `orders.json` and prints PASS or BLOCK with the reasons. Orders are checked in file order, as if each PASS had filled. It writes `th-trader/state/risk.json`.
- `check.py log buy|sell SYMBOL QTY PRICE [--stop X]` records a trade the user actually made into `th-trader/state/trades.json`, one list per Bangkok date.
- `check.py today` prints today's trades: the realized result net of fees, the open positions and their risk to the stop.

What blocks a buy:

| Check | Blocks when |
|---|---|
| stop | there is no stop, or the stop is at or above the limit price |
| loss | qty × risk per share (fees included) > `max_loss_per_trade_thb` |
| reward | net R < `min_reward_risk` |
| daily loss | today's realized loss + the open positions' risk to their stops + this order's risk > `max_daily_loss_thb` |
| trades | today's logged buys ≥ `max_trades_per_day` |
| window | the market is closed, it is the lunch break, or it is after `no_entries_after` or `flat_by` |
| price | the limit price is not on the tick grid, or it is outside the day's floor and ceiling |
| lot | qty is not a multiple of the board lot |
| position | qty × price > `budget_thb` × `max_position_pct` / 100 |
| cash | the buy would go into the cash buffer, counting open positions |
| blocked | the symbol is on `blocked` |
| duplicate | the same symbol was already bought today, or appears twice in the batch |

- A sell that only reduces a held position is never blocked. The other rules become warnings for it.
- A sell beyond what is held is blocked (long only).
- The clock is Bangkok time; `--now` pins it.

## Repo changes

```
shared/th_market.py                 + config and fees
skills/th-trader-exit/              SKILL.md, exit.py
skills/th-trader-timemachine/       SKILL.md, timemachine.py
skills/th-trader-budget/            SKILL.md, size.py
skills/th-trader-risk/              SKILL.md, check.py
tests/                              + one test file per script, fixtures for the 60d 5m and 5y 1d charts
```

- `Install-Skills.ps1` already installs every `skills/th-trader-*` folder, so it needs no change.
- The Time Machine imports `find_setups` from the scanner. To keep each installed skill standalone, the install copies `scan.py`'s setup functions in as `setups.py`; in the repo they live in `shared/setups.py`, and `scan.py` imports them from there.
- `README.md` gains the four skills.
- Each `SKILL.md` keeps the original's structure, the Thai trigger phrases, and the "what it never does" section.

## Testing

**Fees and config:**

- `fee` and `round_trip_fee_per_share` against hand-computed values. Example: THB 42.25 × 300 = 12,675; one side = 12,675 × 0.157% × 1.07 = 21.29.
- Config defaults, overrides, and a wrong-type key.

**Exit:**

- Each stop rule's rounding at a band edge.
- 2R rounding.
- Each refusal.
- The 25% warning and the 50% refusal.
- An orb hit read from a watchlist fixture.
- Intraday and swing defaults.

**Time Machine:**

- The same seed gives the same odds.
- A pool built from a fixture: slot 30 relative to 12:25, and a flat fill for a slot with no bar.
- The 75% rule and the 20-session refusal.
- A gap through the stop exits at the open.
- The clamp at ceiling and floor.
- Swing history finds the plan's setup tag on a constructed series.
- The verdict thresholds on fixed counts.

**Budget:**

- Each of the three limits decides in its own case.
- Zero lots refuses.
- `--lot 50`.
- `--price` above the entry.
- The ticket JSON shape.

**Risk:**

- One test per blocking row.
- A sell is never blocked.
- Logging trades and the daily-loss sum.

**Manual end-to-end:**

- Scanner `orb` → exit → timemachine → budget → risk on a replayed day against live Yahoo.
- One swing plan from a `daily` hit.

## Open items

- `commission_pct` is a typical rate, not the user's. The user should set it to their broker's rate. VAT 7% is due for re-check by 2027-09-30.
- The tick table from 2026-11-16 is still missing; the part 1 fallback applies.
- Part 3 (`th-trader-entry`, `th-trader-alerts`, `th-trader-news`) will write `entry.json` and a price file the budget and the risk check can read. Until then, `--price` stands in for a fresh price.
