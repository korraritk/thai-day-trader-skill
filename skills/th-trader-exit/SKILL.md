---
name: th-trader-exit
description: >-
  Thai Exit Planner for SET and mai stocks. Sets the stop and the target
  BEFORE the entry, on SET's tick grid, with the round-trip fees counted:
  from a th-trader-scanner orb hit (intraday, opening-range midpoint stop,
  2R target, flat by 16:20), a daily hit (swing, 5% stop, up to 10 trading
  days), th-trader/entry.json, or numbers the user gives. Refuses a stop at or
  above the entry, a stop under the day's floor or a target over its ceiling,
  and a stop so tight that fees eat half of it. Writes th-trader/plan.json for
  th-trader-timemachine and th-trader-budget. Long only; never places an
  order. Use when the user says "where's my stop", "set my stop and target",
  "plan the exit", "risk reward", "วาง stop", "ตั้งจุดตัดขาดทุน", "เป้ากี่ R",
  "วางแผนออก", or right after a Thai breakout is picked.
argument-hint: "[plan --symbol S | plan --entry X ... | show]"
---

# th-trader-exit

The Exit Planner sets the stop and the target before the entry, so the user
knows when to get out. It is a port of `trader-exit` (day-trader-skill, Jake
Schincariol, MIT) with SET rules from `th_market.py`: prices on the tick grid,
the ±30% daily limits, Bangkok time, and fees.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run with `python`, not `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol ADVANC --write          # its orb hit in th-trader/watchlist.json
python "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol PTT --write             # a daily hit: swing plan
python "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol PTT --entry 42.25 --or-high 42.25 --or-low 41.50 --write
python "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol PTT --stop atr:1.5 --target 1.5R
python "${CLAUDE_SKILL_DIR}/exit.py" show
```

## Inputs, first one found

1. `--entry X` with `--symbol` (and `--or-high`/`--or-low` for an orb stop).
2. The symbol's hit in `th-trader/watchlist.json`: an orb hit makes an intraday plan; a daily hit makes a swing plan.
3. `th-trader/entry.json` (from th-trader-entry), only once it has triggered.

## The rules

- **Stops** round down to a valid SET price:
  - `or-mid` (intraday default), `or-low`
  - `price:X`, `pct:X` (swing default `pct:5`)
  - `atr:K`: K × ATR(14) of the 5-minute bars to the entry, or of daily bars in swing mode
- **Targets** round to the nearest valid price: `2R` (default), `1R`, `1.5R` and so on, or `price:X`.
- **Fees** come from `th-trader/config.json`. The defaults are commission 0.15% + market fees 0.007% + VAT 7% per side, and the user should set `commission_pct` to their broker's rate.
  - Risk a share = stop distance + round-trip fee.
  - Net R = (target distance − round-trip fee) ÷ risk a share.
- **Refused**, with the reason:
  - a stop at or above the entry, or a target at or below it
  - a stop under today's floor, or a target over today's ceiling (intraday, when Yahoo gives the previous close)
  - a round-trip fee of half the stop distance or more. From a quarter it warns.
- Net R under `min_reward_risk` (1.5) warns here; th-trader-risk blocks it.
- **Intraday plans** are flat by `flat_by` (16:20), win or lose. **Swing plans** run at most `--days` trading days.

## Steps

1. Make the plan with `--write` and report it in Thai, as printed:
   - entry, stop and its rule, target and its R
   - the fee share of the stop distance, gross and net R
   - every Warning and Note line, the fees line, and the tick-table fallback line when printed
2. Hand on: th-trader-timemachine replays it; th-trader-budget sizes it.

## What it never does

It never places, sizes or approves an order, never moves a stop further away,
and never says whether to take the trade. A stop is a plan, not a guarantee: a
gap or a fast market can fill past it. Education and tooling, not investment
advice.
