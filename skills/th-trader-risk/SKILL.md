---
name: th-trader-risk
description: >-
  Thai Risk Manager for SET and mai stocks. Checks every order ticket in
  th-trader/orders.json against the user's rules in th-trader/config.json and
  today's trades the user logged, and prints PASS or BLOCK with the reasons:
  a stop is required, the loss at the stop with fees must fit the max loss per
  trade, net R must meet min_reward_risk, today's realized loss plus open risk
  plus this trade must fit the daily loss limit, trades per day, no entries
  when the market is closed, in the lunch break or after no_entries_after /
  flat_by, prices on SET's tick grid and inside the day's floor and ceiling,
  whole board lots, position cap, cash buffer, blocked symbols, duplicates.
  Getting out is never blocked. Also logs trades the user made ("log buy PTT
  700 42.25 --stop 41.75") and sums the day. Use when the user says "check my
  order", "is this trade within my rules", "risk check", "I bought", "I sold",
  "how much have I lost today", "เช็กความเสี่ยง", "ไม้นี้ผ่านกฎไหม", "บันทึกว่าซื้อแล้ว",
  "วันนี้ขาดทุนไปเท่าไหร่", or after th-trader-budget writes a ticket.
argument-hint: "[check | log buy|sell SYMBOL QTY PRICE [--stop X] | today]"
---

# th-trader-risk

The Risk Manager checks a trade against the user's own rules before the user
types it into their broker's app. It is a port of `trader-risk` (day-trader-skill,
Jake Schincariol, MIT) with SET rules from `th_market.py`. No broker API is
used, so the user logs the trades they actually make.

`${CLAUDE_SKILL_DIR}` is this skill's folder. Run with `python`, not `python3`.

```bash
python "${CLAUDE_SKILL_DIR}/check.py"                                  # th-trader/orders.json
python "${CLAUDE_SKILL_DIR}/check.py" check --prev-close PTT=42.00     # also the floor/ceiling check
python "${CLAUDE_SKILL_DIR}/check.py" log buy PTT 700 42.25 --stop 41.75
python "${CLAUDE_SKILL_DIR}/check.py" log sell PTT 700 43.00
python "${CLAUDE_SKILL_DIR}/check.py" today
```

## What blocks a buy

| Check | Blocks when |
|---|---|
| stop | no stop, or a stop at or above the limit price |
| loss | quantity × (stop distance + round-trip fee) > `max_loss_per_trade_thb` |
| reward | net R < `min_reward_risk` |
| daily loss | today's realized loss + the open positions' risk to their stops + this trade > `max_daily_loss_thb` |
| trades | today's logged buys ≥ `max_trades_per_day` |
| window | market closed, before 10:00, the lunch break, after `no_entries_after` or `flat_by` |
| price | off the tick grid, or outside today's floor and ceiling (with `--prev-close`) |
| lot | not whole board lots (100, or 50 at THB 500+) |
| position | over `max_position_pct` of `budget_thb` |
| cash | would dip into the `cash_buffer_pct` buffer, counting open positions |
| blocked | on the `blocked` list |
| duplicate | already bought today, or twice in the batch |

- **Sells:** a sell that only reduces a held position always passes. The other rules become notes for it. A sell beyond what is held is blocked: long only.
- **The batch:** orders are checked in file order, as if each PASS had filled.
- **Saved results:** `th-trader/state/risk.json`.

## The trade log

`log buy|sell SYMBOL QTY PRICE [--stop X]` records a trade into `th-trader/state/trades.json` under today's Bangkok date. It refuses:
- the same record twice within a minute
- a sell with no open position

`today` prints:
- the realized result net of fees
- the open positions
- their risk to the stops against the daily loss limit

Remind the user to log each fill: the daily limits only know what was logged.

## Steps

1. `check.py`. Report each PASS or BLOCK in Thai with every reason and note, as printed.
2. On a BLOCK, say which rule and why. Never suggest loosening the rule to make it pass.
3. After the user says they bought or sold, log it with `check.py log`.

## What it never does

It never places, changes or cancels an order and never raises a limit. A PASS is
a rules check, not a recommendation. Education and tooling, not investment advice.
